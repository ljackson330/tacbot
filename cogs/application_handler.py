import discord
from discord.ext import commands, tasks
from datetime import datetime
import os
import logging
import time
from typing import Dict, Any, Optional, Tuple
from .google_forms_service import GoogleFormsService
from .database import Database
import threading

logger = logging.getLogger(__name__)


class ApplicationButtons(discord.ui.View):
    """Persistent view for application voting buttons."""

    def __init__(self, cog, response_id: str):
        super().__init__(timeout=None)  # Persistent view
        self.cog = cog
        self.response_id = response_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green, custom_id="approve")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_vote(interaction, "approve", self.response_id)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="deny")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_vote(interaction, "deny", self.response_id)


class UndoButton(discord.ui.View):
    """Temporary view for vote cancellation."""

    def __init__(self, user_id: int, vote_type: str, timeout: float = 10.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.vote_type = vote_type
        self.cancelled = False

    @discord.ui.button(label="Cancel Vote", style=discord.ButtonStyle.danger)
    async def cancel_vote(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return

        self.cancelled = True
        self.stop()

        await interaction.response.defer()  # Just acknowledge the button press

    async def on_timeout(self):
        """Clean up after timeout."""
        for item in self.children:
            item.disabled = True


class ApplicationHandler(commands.Cog):
    """Handles Google Forms application processing with Discord voting."""

    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.google_service = GoogleFormsService()

        # Thread lock for vote operations
        self._vote_lock = threading.Lock()

        # Better rate limiting implementation
        self._api_call_times = []
        self._max_calls_per_minute = 30  # More conservative limit
        self._rate_limit_window = 60  # 1-minute window

        # Add backoff when rate limited
        self._last_rate_limit_time = 0
        self._rate_limit_backoff = 60  # Wait 1 minute when rate limited

        # Add request deduplication
        self._recent_responses = {}  # response_id -> timestamp
        self._response_cache_ttl = 300  # 5 minutes

        # Load configuration
        self._load_config()

        # Initialize database tables
        self.db.initialize_applications_table()
        self.db.initialize_votes_table()

        # Question mapping - will be built dynamically from form
        self.question_map = {}

        # Track processing states to prevent race conditions
        self._processing_applications = set()

        # Start the polling task
        self.check_new_responses.start()

    def _load_config(self):
        """Load configuration from environment variables."""
        config_map = {
            "guild_id": ("GUILD_ID", int),
            "channel_id": ("APPLICATION_CHANNEL_ID", int),
            "form_id": ("GOOGLE_FORM_ID", str),
            "acceptance_threshold": ("ACCEPTANCE_THRESHOLD", int),
            "denial_threshold": ("DENIAL_THRESHOLD", int),
            "member_role_id": ("MEMBER_ROLE_ID", int),
            "applicant_role_id": ("APPLICANT_ROLE_ID", int),
            "applicant_channel_id": ("APPLICANT_CHANNEL_ID", int),
            "recruit_role_id": ("RECRUIT_ROLE_ID", int),
            "general_channel_id": ("GENERAL_CHANNEL_ID", int),
            "discord_id_question": ("DISCORD_ID_QUESTION_ID", str),
            "poll_interval": ("APPLICATION_POLL_INTERVAL", int),
        }

        for attr, (env_var, cast) in config_map.items():
            raw_value = os.getenv(env_var)
            try:
                value = cast(raw_value) if raw_value is not None else None
            except (ValueError, TypeError) as e:
                logger.error(f"Invalid value for {env_var}: {raw_value!r} ({e})")
                raise
            setattr(self, attr, value)

    def _is_rate_limited(self) -> bool:
        """Better rate limiting check with backoff"""
        now = time.time()

        # Check if we're still in backoff period from previous rate limit
        if now - self._last_rate_limit_time < self._rate_limit_backoff:
            return True

        # Clean old timestamps
        cutoff = now - self._rate_limit_window
        self._api_call_times = [t for t in self._api_call_times if t > cutoff]

        # Check if we're approaching limits
        is_limited = len(self._api_call_times) >= self._max_calls_per_minute

        if is_limited:
            self._last_rate_limit_time = now
            logger.warning(f"Rate limit reached: {len(self._api_call_times)}/{self._max_calls_per_minute} calls in last {self._rate_limit_window}s")

        return is_limited

    def _record_api_call(self):
        """Record an API call timestamp"""
        self._api_call_times.append(time.time())

    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        self.check_new_responses.cancel()
        logger.info("ApplicationHandler cog unloaded")

    async def cog_load(self):
        """Initialize when cog is loaded."""
        await self._cleanup_stale_data()
        logger.info("ApplicationHandler cog loaded")

    async def _cleanup_stale_data(self):
        """Clean up any stale processing states on startup."""
        self._processing_applications.clear()
        # Reset rate limiting on startup
        self._api_call_times.clear()
        self._last_rate_limit_time = 0
        logger.info("Cleaned up stale application data")

    @tasks.loop(seconds=30)  # Default 30 seconds, configurable
    async def check_new_responses(self):
        """Check for new Google Form responses."""
        try:
            # Check rate limiting with better logging
            if self._is_rate_limited():
                # Don't log this every time - only occasionally
                if len(self._api_call_times) % 10 == 0 or not hasattr(self, "_last_rate_limit_log"):
                    logger.info("Rate limit active, skipping check (this message appears occasionally)")
                    self._last_rate_limit_log = time.time()
                return

            # Build question map if not already done
            if not self.question_map:
                await self._build_question_map()
                # If building question map failed due to rate limit, skip this iteration
                if not self.question_map:
                    return

            # Record API call BEFORE making it
            self._record_api_call()

            try:
                responses = await self.google_service.get_form_responses(self.form_id)
            except Exception as e:
                # Don't record API call if it failed
                if self._api_call_times:
                    self._api_call_times.pop()
                raise e

            for response in responses:
                response_id = response["responseId"]

                # Check if we've already processed this response
                if not self.db.is_response_processed(response_id):
                    await self._process_new_response(response)
                    self.db.mark_response_processed(response_id)

        except Exception as e:
            logger.error(f"Error checking for new responses: {e}")

    @check_new_responses.before_loop
    async def before_check_responses(self):
        """Wait until bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        # Update loop interval if configured
        if hasattr(self, "poll_interval") and self.poll_interval:
            self.check_new_responses.change_interval(seconds=self.poll_interval)

    async def _build_question_map(self):
        """Build question map from Google Form metadata."""
        try:
            if self._is_rate_limited():
                logger.debug("Rate limit reached, cannot build question map")
                return

            # Record API call BEFORE making it
            self._record_api_call()

            try:
                form_info = await self.google_service.get_form_info(self.form_id)
                self.question_map = self.google_service.build_question_map(form_info)
                logger.info(f"Built question map with {len(self.question_map)} questions")
            except Exception as e:
                # Remove the recorded API call if it failed
                if self._api_call_times:
                    self._api_call_times.pop()
                raise e

        except Exception as e:
            logger.error(f"Error building question map: {e}")
            # Fallback to basic mapping if form info fails
            self.question_map = {}

    async def _process_new_response(self, response: Dict[str, Any]):
        """Process a new form response."""
        response_id = response["responseId"]

        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                logger.error(f"Could not find guild with ID {self.guild_id}")
                return

            channel = guild.get_channel(self.channel_id)
            if not channel:
                logger.error(f"Could not find channel with ID {self.channel_id}")
                return

            embed = await self._create_application_embed(response, guild)

            # Create buttons for voting
            view = ApplicationButtons(self, response_id)
            message = await channel.send(embed=embed, view=view)

            # Store message info in database
            self.db.store_application_message(response_id, message.id, channel.id)

            # Send confirmation message to applicant
            await self._send_application_confirmation(response, guild)

            logger.info(f"Processed new application: {response_id}")

        except Exception as e:
            logger.error(f"Error processing response {response_id}: {e}")

    async def _send_application_confirmation(self, response: Dict[str, Any], guild: discord.Guild):
        """Send confirmation message to applicant via DM, with channel fallback."""
        try:
            # Extract Discord ID from response
            answers = response.get("answers", {})
            discord_data = self._extract_discord_id(answers)

            if not discord_data:
                logger.warning("Could not extract Discord ID for confirmation message")
                return

            discord_id, _ = discord_data
            member = await self._get_discord_member(discord_id, guild)

            if not member:
                logger.warning(f"Could not find member {discord_id} for confirmation message")
                return

            confirmation_message = "Thank you for submitting your application! We have received it and will review it shortly."

            # Try to send DM first
            try:
                await member.send(confirmation_message)
                logger.info(f"Sent application confirmation DM to {member.display_name}")
            except discord.Forbidden:
                # DMs are disabled or blocked - fall back to channel message
                logger.warning(f"Could not DM {member.display_name}, falling back to channel message")

                applicant_channel = guild.get_channel(self.applicant_channel_id)
                if not applicant_channel:
                    logger.error(f"Could not find applicant channel with ID {self.applicant_channel_id}")
                    return

                # Send in channel with mention as fallback
                await applicant_channel.send(f"{member.mention}, {confirmation_message}")
                logger.info(f"Sent application confirmation in channel to {member.display_name}")

        except Exception as e:
            logger.error(f"Error sending application confirmation: {e}")

    def _extract_discord_id(self, answers: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """Extract Discord ID from form answers."""
        try:
            logger.debug(f"Available question IDs: {list(answers.keys())}")

            # Try configured question ID first
            possible_ids = [
                self.discord_id_question,
                f"entry.{self.discord_id_question}",
                str(self.discord_id_question),
            ]

            # Try each possible ID format
            for question_id in possible_ids:
                if question_id in answers:
                    answer_data = answers[question_id]
                    if "textAnswers" in answer_data and answer_data["textAnswers"]["answers"]:
                        discord_id = answer_data["textAnswers"]["answers"][0]["value"].strip()
                        if self._validate_discord_id(discord_id):
                            logger.debug(f"Found Discord ID using question_id: {question_id}")
                            return discord_id, question_id

            # If direct lookup fails, search through all answers
            logger.debug("Direct lookup failed, searching all answers for Discord ID pattern")
            for question_id, answer_data in answers.items():
                if "textAnswers" in answer_data and answer_data["textAnswers"]["answers"]:
                    value = answer_data["textAnswers"]["answers"][0]["value"].strip()
                    if self._validate_discord_id(value):
                        logger.debug(f"Found Discord ID pattern in question_id: {question_id}")
                        return value, question_id

            logger.warning("No Discord ID found in any answer")
            return None

        except Exception as e:
            logger.error(f"Error extracting Discord ID: {e}")
            return None

    def _validate_discord_id(self, discord_id: str) -> bool:
        """Validate Discord ID format with proper sanitization"""
        if not isinstance(discord_id, str):
            return False

        # Remove any whitespace and non-digit characters except for the ID
        # itself
        cleaned_id = "".join(c for c in discord_id if c.isdigit())

        # Discord IDs are exactly 17-20 digits (snowflakes)
        if not (17 <= len(cleaned_id) <= 20):
            return False

        # Check if it's a reasonable snowflake (after Discord's epoch)
        try:
            snowflake = int(cleaned_id)
            # Discord epoch: 2015-01-01, minimum reasonable ID
            return snowflake > 4194304  # First possible Discord ID
        except (ValueError, OverflowError):
            return False

    async def _get_discord_member(self, discord_id: str, guild: discord.Guild) -> Optional[discord.Member]:
        """Get Discord member by ID from the specified guild."""
        try:
            discord_id_int = int(discord_id)
            logger.debug(f"Looking for Discord ID: {discord_id_int} in guild: {guild.name}")

            # Try get_member first (cached members only)
            member = guild.get_member(discord_id_int)
            if member:
                logger.debug(f"Found member via get_member: {member.display_name}")
                return member

            # If not in cache, try fetching from Discord API
            try:
                member = await guild.fetch_member(discord_id_int)
                logger.debug(f"Found member via fetch_member: {member.display_name}")
                return member
            except discord.NotFound:
                logger.warning(f"Member {discord_id_int} not found in guild {guild.name}")
                return None
            except discord.HTTPException as e:
                logger.error(f"HTTP error fetching member {discord_id_int}: {e}")
                return None

        except ValueError:
            logger.error(f"Invalid Discord ID format: {discord_id}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting member {discord_id}: {e}")
            return None

    async def _create_application_embed(self, response: Dict[str, Any], guild: discord.Guild) -> discord.Embed:
        """Create embed for application display."""
        # Parse timestamp
        iso_timestamp = response["createTime"]
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        unix_ts = int(dt.timestamp())

        answers = response.get("answers", {})
        discord_data = self._extract_discord_id(answers)

        discord_id = None
        discord_question_id = None
        if discord_data:
            discord_id, discord_question_id = discord_data

        # Try to get the Discord member
        member = None
        if discord_id:
            member = await self._get_discord_member(discord_id, guild)

        # Build the embed
        if member:
            title = f"Application from {member.display_name}"
            thumbnail_url = member.display_avatar.url if hasattr(member, "display_avatar") else None
        else:
            title = "Application from Unknown User"
            if discord_id:
                title += f" (ID: {discord_id})"
            thumbnail_url = None

        embed = discord.Embed(title=title, description=f"**Submitted:** <t:{unix_ts}:f>", color=discord.Color.blue())

        # Set thumbnail if available
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        # Add form answers as fields (sanitize input)
        for question_id, question_title in self.question_map.items():
            # Skip the Discord ID since we display it in the title/description
            if question_id == discord_question_id:
                continue

            if question_id in answers:
                answer_data = answers[question_id]

                # Handle different answer types and sanitize
                if "textAnswers" in answer_data:
                    raw_answers = [a["value"] for a in answer_data["textAnswers"]["answers"]]
                    answer_text = ", ".join(self._sanitize_text(ans) for ans in raw_answers)
                else:
                    answer_text = self._sanitize_text(str(answer_data))

                # Let Discord handle embed length limits naturally

                embed.add_field(
                    name=self._sanitize_text(question_title),
                    value=answer_text or "*No answer provided*",
                    inline=False,
                )

        # Add initial vote status
        embed.add_field(name="Votes", value="**Approvals (0):** None\n**Denials (0):** None", inline=False)

        return embed

    def _sanitize_text(self, text: str) -> str:
        """Sanitize text for embed display."""
        if not isinstance(text, str):
            text = str(text)

        # Remove or escape potential markdown/mentions
        # Zero-width space to break mentions
        text = text.replace("@", "@\u200b")
        text = text.replace("`", "`\u200b")  # Break code blocks

        # Additional sanitization for potential abuse
        text = text.replace("http://", "http[://]")  # Break HTTP links
        text = text.replace("https://", "https[://]")  # Break HTTPS links
        text = text.replace("discord.gg/", "discord[.]gg/")  # Break Discord invites

        return text.strip()

    async def handle_vote(self, interaction: discord.Interaction, vote_type: str, response_id: str):
        """Handle voting on applications with proper locking."""
        user_id = interaction.user.id

        # Check if application is being processed
        if response_id in self._processing_applications:
            await interaction.response.send_message("This application is currently being processed. Please wait.", ephemeral=True)
            return

        try:
            with self._vote_lock:
                # Get current vote status
                current_vote = self.db.get_user_vote(response_id, user_id)

                if current_vote == vote_type:
                    # Same vote - remove it (toggle off)
                    self.db.remove_vote(response_id, user_id)
                    await interaction.response.defer()
                elif current_vote:
                    # Different vote - update it
                    self.db.update_vote(response_id, user_id, vote_type)
                    await interaction.response.defer()
                else:
                    # New vote
                    self.db.add_vote(response_id, user_id, vote_type)
                    await interaction.response.defer()

            # Update embed with new vote counts
            await self._update_application_embed(interaction.message, response_id)

            # Check if this vote is decisive
            vote_counts = self.db.get_vote_counts(response_id)

            if self._is_decisive_vote(vote_counts, vote_type):
                await self._handle_decisive_vote(interaction, response_id, vote_type, vote_counts)
            else:
                # Check if threshold reached without being decisive
                await self._check_auto_process(interaction.message, response_id, vote_counts)

        except Exception as e:
            logger.error(f"Error handling vote for application {response_id}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message("An error occurred while processing your vote.", ephemeral=True)

    def _is_decisive_vote(self, vote_counts: Dict[str, int], vote_type: str) -> bool:
        """Check if this vote reaches the threshold and is the deciding vote."""
        approvals = vote_counts.get("approve", 0)
        denials = vote_counts.get("deny", 0)

        if vote_type == "approve" and approvals >= self.acceptance_threshold:
            # Check if this was the decisive vote (one less before this vote)
            return (approvals - 1) < self.acceptance_threshold
        elif vote_type == "deny" and denials >= self.denial_threshold:
            # Check if this was the decisive vote
            return (denials - 1) < self.denial_threshold

        return False

    async def _handle_decisive_vote(
        self,
        interaction: discord.Interaction,
        response_id: str,
        vote_type: str,
        vote_counts: Dict[str, int],
    ):
        """Handle a decisive vote with undo option."""
        decision = "accept" if vote_type == "approve" else "deny"

        # Create undo button
        view = UndoButton(interaction.user.id, vote_type)

        try:
            undo_msg = await interaction.followup.send(
                f"{interaction.user.mention}, your vote was decisive and will **{decision}** this application. "
                f"You have 10 seconds to cancel if this was a mistake.",
                view=view,
            )

            # Wait for either button click or timeout
            await view.wait()

            # Delete the undo message after completion (success or timeout)
            try:
                await undo_msg.delete()
            except discord.NotFound:
                pass  # Message already deleted
            except discord.HTTPException:
                pass  # Failed to delete, but continue

            if view.cancelled:
                # User cancelled - remove their vote
                with self._vote_lock:
                    self.db.remove_vote(response_id, interaction.user.id)

                await self._update_application_embed(interaction.message, response_id)

                # Recheck thresholds after cancellation
                new_counts = self.db.get_vote_counts(response_id)
                await self._check_auto_process(interaction.message, response_id, new_counts)

            else:
                # Timeout - proceed with decision
                await self._process_application(interaction.message, response_id, decision)

        except Exception as e:
            logger.error(f"Error in decisive vote handling: {e}")

    async def _check_auto_process(self, message: discord.Message, response_id: str, vote_counts: Dict[str, int]):
        """Check if application should be auto-processed based on vote counts."""
        approvals = vote_counts.get("approve", 0)
        denials = vote_counts.get("deny", 0)

        if approvals >= self.acceptance_threshold:
            await self._process_application(message, response_id, "accept")
        elif denials >= self.denial_threshold:
            await self._process_application(message, response_id, "deny")

    async def _update_application_embed(self, message: discord.Message, response_id: str):
        """Update application embed with current vote counts."""
        try:
            # Get vote data from database
            votes = self.db.get_votes(response_id)
            vote_counts = self.db.get_vote_counts(response_id)

            embed = message.embeds[0]

            # Build vote display
            approvers = [f"<@{vote['user_id']}>" for vote in votes if vote["vote_type"] == "approve"]
            deniers = [f"<@{vote['user_id']}>" for vote in votes if vote["vote_type"] == "deny"]

            approvals_count = vote_counts.get("approve", 0)
            denials_count = vote_counts.get("deny", 0)

            vote_info = f"**Approvals ({approvals_count}):** {', '.join(approvers) if approvers else 'None'}\n"
            vote_info += f"**Denials ({denials_count}):** {', '.join(deniers) if deniers else 'None'}"

            # Update or add vote field
            for i, field in enumerate(embed.fields):
                if field.name == "Votes":
                    embed.set_field_at(i, name="Votes", value=vote_info, inline=False)
                    break
            else:
                # Vote field not found, add it
                embed.add_field(name="Votes", value=vote_info, inline=False)

            await message.edit(embed=embed)

        except Exception as e:
            logger.error(f"Error updating embed for {response_id}: {e}")

    async def _process_application(self, message: discord.Message, response_id: str, decision: str):
        """Process application acceptance or denial."""
        if response_id in self._processing_applications:
            return  # Already being processed

        self._processing_applications.add(response_id)

        try:
            # Check if already processed
            app_data = self.db.get_application_by_message_id(message.id)
            if app_data and app_data.get("status") in ["accepted", "denied"]:
                return

            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                logger.error(f"Could not find guild {self.guild_id}")
                return

            # Update embed
            embed = message.embeds[0]
            now = datetime.now()
            unix_ts = int(now.timestamp())

            if decision == "accept":
                embed.description += f"\n**Accepted:** <t:{unix_ts}:f>"
                embed.colour = discord.Color.green()
                await self._handle_acceptance(guild, response_id)
            else:
                embed.description += f"\n**Denied:** <t:{unix_ts}:f>"
                embed.colour = discord.Color.red()
                await self._handle_denial(guild, response_id)

            # Remove all buttons by passing None as the view
            await message.edit(embed=embed, view=None)

            # Mark as processed in database
            self.db.set_application_status(response_id, decision)

            logger.info(f"Application {response_id} {decision}ed")

        except Exception as e:
            logger.error(f"Error processing application {response_id}: {e}")
        finally:
            self._processing_applications.discard(response_id)

    async def _handle_acceptance(self, guild: discord.Guild, response_id: str):
        """Handle application acceptance."""
        member, role = await self._get_member_and_role(guild, response_id)
        applicant_role = member.guild.get_role(self.applicant_role_id)
        recruit_role = member.guild.get_role(self.recruit_role_id)

        if not member:
            logger.warning(f"Could not find member for accepted application {response_id}")
            return

        if role:
            try:
                await member.add_roles(role, recruit_role, reason=f"Application {response_id} accepted")
                logger.info(f"Added role {role.name} to {member.display_name}")
                await member.remove_roles(applicant_role, reason=f"Application {response_id} accepted")
            except discord.HTTPException as e:
                logger.error(f"Failed to update roles for {member.display_name}: {e}")

        # Send notifications
        await self._send_notifications(member, True, guild.name)

    async def _handle_denial(self, guild: discord.Guild, response_id: str):
        """Handle application denial."""
        member, _ = await self._get_member_and_role(guild, response_id)

        if member:
            try:
                await member.kick(reason=f"Application {response_id} denied")
                logger.info(f"Kicked {member.display_name} from server")
            except discord.HTTPException as e:
                logger.error(f"Failed to kick {member.display_name}: {e}")

    async def _get_member_and_role(self, guild: discord.Guild, response_id: str) -> Tuple[Optional[discord.Member], Optional[discord.Role]]:
        """Get the Discord member and role for application processing."""
        try:
            if self._is_rate_limited():
                logger.warning("Rate limit reached, cannot fetch form responses")
                return None, None

            # Record API call BEFORE making it
            self._record_api_call()

            try:
                responses = await self.google_service.get_form_responses(self.form_id)
            except Exception as e:
                # Remove the recorded API call if it failed
                if self._api_call_times:
                    self._api_call_times.pop()
                raise e

            target_response = next((r for r in responses if r["responseId"] == response_id), None)

            if not target_response:
                logger.error(f"Could not find response with ID {response_id}")
                return None, None

            answers = target_response.get("answers", {})
            discord_data = self._extract_discord_id(answers)

            if not discord_data:
                logger.warning(f"Could not extract Discord ID from response {response_id}")
                return None, None

            discord_id, _ = discord_data
            member = await self._get_discord_member(discord_id, guild)

            if not member:
                logger.warning(f"Could not find member with Discord ID {discord_id}")
                return None, None

            role = guild.get_role(self.member_role_id)
            if not role:
                logger.error(f"Could not find role with ID {self.member_role_id}")
                return member, None

            return member, role

        except Exception as e:
            logger.error(f"Error getting member and role for {response_id}: {e}")
            return None, None

    async def _send_notifications(self, member: discord.Member, accepted: bool, guild_name: str):
        """Send notifications for application result."""
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return

        # Try to DM the user
        if accepted:
            try:
                message = f"Your application to **{guild_name}** has been **accepted**! You now have access to the server."
                await member.send(message)
                logger.info(f"Successfully notified {member.display_name} of acceptance")
            except discord.Forbidden:
                logger.warning(f"Could not DM {member.display_name} about acceptance")

            # Post welcome message in general
            general_channel = guild.get_channel(self.general_channel_id)
            if general_channel:
                try:
                    welcome_message = f"{member.mention} has been accepted to {guild_name}! Please give them a warm welcome"
                    await general_channel.send(welcome_message)
                    logger.info(f"Sent welcome message for {member.display_name}")
                except discord.HTTPException as e:
                    logger.error(f"Failed to send welcome message: {e}")

    @commands.command(name="app_stats")
    @commands.has_permissions(administrator=True)
    async def application_stats(self, ctx):
        """Show application statistics (admin only)."""
        try:
            stats = self.db.get_application_stats()

            embed = discord.Embed(title="Application Statistics", color=discord.Color.blue())

            embed.add_field(name="Total Applications", value=stats.get("total", 0), inline=True)
            embed.add_field(name="Accepted", value=stats.get("accepted", 0), inline=True)
            embed.add_field(name="Denied", value=stats.get("denied", 0), inline=True)
            embed.add_field(name="Pending", value=stats.get("pending", 0), inline=True)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error getting application stats: {e}")
            await ctx.send("Error retrieving application statistics.")

    @commands.command(name="reset_rate_limit")
    @commands.has_permissions(administrator=True)
    async def reset_rate_limit(self, ctx):
        """Reset rate limiting counters (admin only)."""
        try:
            self._api_call_times.clear()
            self._last_rate_limit_time = 0
            await ctx.send("Rate limiting counters have been reset.")
            logger.info("Rate limiting counters reset by admin command")
        except Exception as e:
            logger.error(f"Error resetting rate limit: {e}")
            await ctx.send("Error resetting rate limiting counters.")

    @commands.command(name="rate_limit_status")
    @commands.has_permissions(administrator=True)
    async def rate_limit_status(self, ctx):
        """Show current rate limiting status (admin only)."""
        try:
            now = time.time()
            cutoff = now - self._rate_limit_window
            recent_calls = [t for t in self._api_call_times if t > cutoff]

            is_limited = len(recent_calls) >= self._max_calls_per_minute
            backoff_remaining = max(0, self._rate_limit_backoff - (now - self._last_rate_limit_time))

            embed = discord.Embed(title="Rate Limiting Status", color=discord.Color.red() if is_limited else discord.Color.green())

            embed.add_field(name="Current Status", value="Rate Limited" if is_limited else "Normal", inline=True)
            embed.add_field(name="API Calls (Last 60s)", value=f"{len(recent_calls)}/{self._max_calls_per_minute}", inline=True)

            if backoff_remaining > 0:
                embed.add_field(name="Backoff Remaining", value=f"{backoff_remaining:.1f} seconds", inline=True)

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"Error getting rate limit status: {e}")
            await ctx.send("Error retrieving rate limit status.")


async def setup(bot):
    """Setup function for the cog."""
    await bot.add_cog(ApplicationHandler(bot))
