import discord
from discord.ext import commands, tasks
from datetime import datetime
import os
import logging
import asyncio
from typing import Dict, Any
from .google_forms_service import GoogleFormsService
from .database import Database

logger = logging.getLogger(__name__)


class ApplicationButtons(discord.ui.View):
    def __init__(self, cog, response_id: str):
        super().__init__(timeout=None)  # Persistent view
        self.cog = cog
        self.response_id = response_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.green)
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Simulate adding a thumbs up reaction
        await self.cog._handle_button_vote(interaction, "👍", self.response_id)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red)
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Simulate adding a thumbs down reaction
        await self.cog._handle_button_vote(interaction, "👎", self.response_id)


class UndoButton(discord.ui.View):
    def __init__(self, user_id: int, original_reaction: str, timeout: float = 10.0):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.original_reaction = original_reaction
        self.cancelled = False

    @discord.ui.button(label="Cancel Vote", style=discord.ButtonStyle.danger)
    async def cancel_vote(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This button is not for you!", ephemeral=True)
            return

        self.cancelled = True
        self.stop()

        # Delete the message with the button
        try:
            await interaction.message.delete()
        except:
            pass  # Message might already be deleted

    async def on_timeout(self):
        # Disable the button after timeout
        for item in self.children:
            item.disabled = True


class ApplicationHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.google_service = GoogleFormsService()

        # Configuration
        self.channel_id = int(os.getenv('APPLICATION_CHANNEL_ID'))
        self.form_id = os.getenv('GOOGLE_FORM_ID')
        self.acceptance_threshold = int(os.getenv('ACCEPTANCE_THRESHOLD'))
        self.denial_threshold = int(os.getenv('DENIAL_THRESHOLD', '1'))  # Default to 1 if not set
        self.accepted_role_id = int(os.getenv('MEMBER_ROLE'))
        self.discord_id_entry = os.getenv('DISCORD_ID_ENTRY', 'entry.1141798550')

        # Question mapping - will be built dynamically from form
        self.question_map = {}

        # Track pending undo operations
        self.pending_undos = {}

        # Track votes per application (response_id -> {user_id: vote_type})
        self.application_votes = {}

        # Start the polling task
        self.check_new_responses.start()

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.check_new_responses.cancel()

    # TODO: tone this the fuck down in prod
    @tasks.loop(seconds=30)
    async def check_new_responses(self):
        """Check for new Google Form responses every 30 seconds"""
        try:
            # Build question map if not already done
            if not self.question_map:
                await self._build_question_map()

            responses = await self.google_service.get_form_responses(self.form_id)

            for response in responses:
                response_id = response['responseId']

                # Check if we've already processed this response
                if not self.db.is_response_processed(response_id):
                    await self._process_new_response(response)
                    self.db.mark_response_processed(response_id)

        except Exception as e:
            logger.error(f"Error checking for new responses: {e}")

    @check_new_responses.before_loop
    async def before_check_responses(self):
        """Wait until bot is ready before starting the loop"""
        await self.bot.wait_until_ready()

    async def _build_question_map(self):
        """Build question map from Google Form metadata"""
        try:
            form_info = await self.google_service.get_form_info(self.form_id)
            self.question_map = self.google_service.build_question_map(form_info)
            logger.info(f"Built question map with {len(self.question_map)} questions")
        except Exception as e:
            logger.error(f"Error building question map: {e}")
            # Fallback to basic mapping if form info fails
            self.question_map = {}

    async def _process_new_response(self, response: Dict[str, Any]):
        """Process a new form response"""
        try:
            channel = self.bot.get_channel(self.channel_id)
            if not channel:
                logger.error(f"Could not find channel with ID {self.channel_id}")
                return

            embed = await self._create_application_embed(response)

            # Create buttons for voting
            view = ApplicationButtons(self, response['responseId'])
            message = await channel.send(embed=embed, view=view)

            # Initialize vote tracking for this application
            self.application_votes[response['responseId']] = {}

            # Store message info in database
            self.db.store_application_message(
                response['responseId'],
                message.id,
                channel.id
            )

            logger.info(f"Processed new application: {response['responseId']}")

        except Exception as e:
            logger.error(f"Error processing response {response['responseId']}: {e}")

    def _get_discord_id_from_answers(self, answers: Dict[str, Any]) -> tuple:
        """Extract Discord ID from form answers"""
        try:
            # First, let's log what question IDs we actually have
            logger.info(f"Available question IDs: {list(answers.keys())}")

            # Try different possible formats for the Discord ID question
            possible_ids = [
                "440e7696",  # Just the number
                str(440e7696),  # Ensure it's a string
                "entry.1141798550",  # Full entry format
            ]

            discord_id = None

            # Try each possible ID format
            for question_id in possible_ids:
                if question_id in answers:
                    answer_data = answers[question_id]
                    if 'textAnswers' in answer_data and answer_data['textAnswers']['answers']:
                        discord_id = answer_data['textAnswers']['answers'][0]['value'].strip()
                        logger.info(f"Found Discord ID using question_id: {question_id}")
                        found_question_id = question_id
                        break

            # If direct lookup fails, search through all answers for something that looks like a Discord ID
            if not discord_id:
                logger.info("Direct lookup failed, searching all answers for Discord ID pattern")
                for question_id, answer_data in answers.items():
                    if 'textAnswers' in answer_data and answer_data['textAnswers']['answers']:
                        value = answer_data['textAnswers']['answers'][0]['value'].strip()
                        # Check if this looks like a Discord ID (17-19 digit number)
                        if value.isdigit() and 17 <= len(value) <= 19:
                            discord_id = value
                            logger.info(f"Found Discord ID pattern in question_id: {question_id}, value: {value}")
                            found_question_id = question_id
                            break

            if discord_id:
                # Validate it's a proper Discord ID
                try:
                    int(discord_id)
                    return discord_id, found_question_id
                except ValueError:
                    logger.warning(f"Invalid Discord ID format: {discord_id}")
                    return None
            else:
                logger.warning("No Discord ID found in any answer")
                return None

        except Exception as e:
            logger.error(f"Error extracting Discord ID: {e}")
            return None

    async def _get_discord_user_by_id(self, discord_id: str) -> discord.Member:
        """Get Discord user by ID from the current guild"""
        try:
            discord_id_int = int(discord_id)
            print(f"Looking for Discord ID: {discord_id_int}")

            guild = self.bot.guilds[0] if self.bot.guilds else None
            if not guild:
                print("No guild found")
                return None

            print(f"Searching in guild: {guild.name}")

            # Try get_member first (cached members only)
            member = guild.get_member(discord_id_int)
            if member:
                print(f"Found member via get_member: {member.display_name}")
                return member

            # If not in cache, try fetching from Discord API
            try:
                member = await guild.fetch_member(discord_id_int)
                print(f"Found member via fetch_member: {member.display_name}")
                return member
            except discord.NotFound:
                print(f"Member {discord_id_int} not found in guild")
                return None
            except discord.HTTPException as e:
                print(f"HTTP error fetching member: {e}")
                return None

        except ValueError:
            print(f"Invalid Discord ID format: {discord_id}")
            return None
        except Exception as e:
            print(f"Error getting user by ID {discord_id}: {e}")
            return None

    def _update_embed_with_vote_counts(self, embed: discord.Embed, response_id: str) -> discord.Embed:
        """Update embed with current vote counts"""
        if response_id not in self.application_votes:
            return embed

        votes = self.application_votes[response_id]
        thumbs_up = sum(1 for vote in votes.values() if vote == '👍')
        thumbs_down = sum(1 for vote in votes.values() if vote == '👎')

        # Get voter names for display
        approvers = [f"<@{uid}>" for uid, vote in votes.items() if vote == '👍']
        deniers = [f"<@{uid}>" for uid, vote in votes.items() if vote == '👎']

        # Add or update vote count field
        vote_info = f"**Approvals ({thumbs_up}):** {', '.join(approvers) if approvers else 'None'}\n"
        vote_info += f"**Denials ({thumbs_down}):** {', '.join(deniers) if deniers else 'None'}"

        # Remove existing vote field if it exists
        for i, field in enumerate(embed.fields):
            if field.name == "Votes":
                embed.remove_field(i)
                break

        # Add vote field at the end
        embed.add_field(name="Votes", value=vote_info, inline=False)

        return embed

    async def _create_application_embed(self, response: Dict[str, Any]) -> discord.Embed:
        # Parse timestamp
        iso_timestamp = response['createTime']
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        unix_ts = int(dt.timestamp())

        answers = response.get('answers', {})
        discord_id, discord_question_id = self._get_discord_id_from_answers(answers)

        # Try to get the Discord user object (now awaited)
        member = None
        if discord_id:
            member = await self._get_discord_user_by_id(discord_id)  # Use a simpler function
            print(member)

        # Build the embed
        if member:
            title = f"Application from {member.display_name}"
            thumbnail_url = member.display_avatar.url if hasattr(member, 'display_avatar') else None
        else:
            title = "Application from Unknown User"
            thumbnail_url = None

        embed = discord.Embed(
            title=title,
            description=f"**Submitted:** <t:{unix_ts}:f>",
            color=discord.Color.blue()
        )

        # Set thumbnail if we have a profile picture
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        # Add form answers as fields
        for question_id, question_title in self.question_map.items():
            # Skip the Discord ID since we display it in the description
            if question_id == discord_question_id:
                continue

            if question_id in answers:
                question_title = self.question_map.get(question_id, f"Question {question_id}")
                answer_data = answers[question_id]

                # Handle different answer types
                if 'textAnswers' in answer_data:
                    answer_text = ", ".join(a['value'] for a in answer_data['textAnswers']['answers'])
                else:
                    answer_text = str(answer_data)

                embed.add_field(
                    name=question_title,
                    value=answer_text or "*No answer provided*",
                    inline=False
                )

        # Add initial empty vote field
        embed.add_field(name="Votes", value="**Approvals (0):** None\n**Denials (0):** None", inline=False)

        return embed

    async def _handle_button_vote(self, interaction: discord.Interaction, vote_type: str, response_id: str):
        """Handle button-based voting on applications"""
        try:
            user = interaction.user

            # Check if user already voted
            if response_id in self.application_votes:
                if user.id in self.application_votes[response_id]:
                    # User already voted - remove their previous vote or update it
                    previous_vote = self.application_votes[response_id][user.id]
                    if previous_vote == vote_type:
                        # Same vote - remove it (toggle off)
                        del self.application_votes[response_id][user.id]

                        # Update the embed and respond
                        embed = interaction.message.embeds[0]
                        updated_embed = self._update_embed_with_vote_counts(embed, response_id)
                        await interaction.response.edit_message(embed=updated_embed)

                        # Recheck vote counts after removal
                        await self._recheck_button_vote_counts(interaction.message, response_id)
                        return
                    else:
                        # Different vote - update it
                        self.application_votes[response_id][user.id] = vote_type

                        # Update the embed and respond
                        embed = interaction.message.embeds[0]
                        updated_embed = self._update_embed_with_vote_counts(embed, response_id)
                        await interaction.response.edit_message(embed=updated_embed)
                        await interaction.followup.send(f"Changed your vote to {vote_type}.", ephemeral=True)
                else:
                    # New vote
                    self.application_votes[response_id][user.id] = vote_type

                    # Update the embed and respond
                    embed = interaction.message.embeds[0]
                    updated_embed = self._update_embed_with_vote_counts(embed, response_id)
                    await interaction.response.edit_message(embed=updated_embed)
            else:
                # Initialize vote tracking for this response
                self.application_votes[response_id] = {user.id: vote_type}

                # Update the embed and respond
                embed = interaction.message.embeds[0]
                updated_embed = self._update_embed_with_vote_counts(embed, response_id)
                await interaction.response.edit_message(embed=updated_embed)

            # Count current votes
            thumbs_up = sum(1 for vote in self.application_votes[response_id].values() if vote == '👍')
            thumbs_down = sum(1 for vote in self.application_votes[response_id].values() if vote == '👎')

            logger.info(f"Application {response_id}: {thumbs_up} 👍, {thumbs_down} 👎")

            # Check if this vote is decisive (reaches threshold)
            is_decisive = False
            if thumbs_up >= self.acceptance_threshold and vote_type == '👍':
                is_decisive = True
                decision_type = "accept"
            elif thumbs_down >= self.denial_threshold and vote_type == '👎':
                is_decisive = True
                decision_type = "deny"

            # If this is a decisive vote, offer undo option
            if is_decisive:
                await self._offer_vote_undo(interaction.message, user, vote_type, response_id, decision_type)

            # Only process application if this isn't a decisive vote (which needs undo window)
            # If it's a decisive vote, the processing will happen after the undo timeout
            if not is_decisive:
                # Check if acceptance threshold is met
                if thumbs_up >= self.acceptance_threshold:
                    await self._accept_application(interaction.message, response_id)
                elif thumbs_down >= self.denial_threshold:
                    await self._deny_application(interaction.message, response_id)

        except Exception as e:
            logger.error(f"Error handling button vote for application {response_id}: {e}")
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("An error occurred while processing your vote.",
                                                            ephemeral=True)
                else:
                    await interaction.followup.send("An error occurred while processing your vote.", ephemeral=True)
            except:
                pass

    async def _offer_vote_undo(self, message: discord.Message, user: discord.User, reaction_emoji: str,
                               response_id: str, decision_type: str):
        """Offer the user a chance to undo their decisive vote"""
        try:
            # Create undo button view
            view = UndoButton(user.id, reaction_emoji)

            # Send message in channel with undo button (only the specific user can use it)
            undo_msg = await message.channel.send(
                f"{user.mention}, your vote was decisive and will **{decision_type}** this application. You have 10 seconds to cancel your vote if this was a mistake.",
                view=view,
                delete_after=15  # Delete message after timeout + buffer
            )

            # Track this pending undo
            self.pending_undos[response_id] = {
                'user_id': user.id,
                'reaction': reaction_emoji,
                'message': message,
                'decision_type': decision_type
            }

            # Wait for either button click or timeout
            await view.wait()

            # Clean up pending undo
            if response_id in self.pending_undos:
                del self.pending_undos[response_id]

            if view.cancelled:
                # User cancelled their vote - remove their vote from tracking
                try:
                    if response_id in self.application_votes and user.id in self.application_votes[response_id]:
                        del self.application_votes[response_id][user.id]
                        logger.info(
                            f"Removed {reaction_emoji} vote from {user.display_name} on application {response_id}")

                    # Update the embed with new vote counts
                    embed = message.embeds[0]
                    updated_embed = self._update_embed_with_vote_counts(embed, response_id)
                    await message.edit(embed=updated_embed)

                    # Recheck vote counts after removal
                    await asyncio.sleep(0.5)  # Small delay
                    await self._recheck_button_vote_counts(message, response_id)

                except Exception as e:
                    logger.error(f"Error removing vote: {e}")
            else:
                # Timeout occurred - proceed with original decision
                logger.info(f"Vote undo timeout for application {response_id}, proceeding with {decision_type}")
                if decision_type == "accept":
                    await self._accept_application(message, response_id)
                else:
                    await self._deny_application(message, response_id)

        except Exception as e:
            logger.error(f"Error in vote undo process: {e}")
            # Clean up pending undo on error
            if response_id in self.pending_undos:
                del self.pending_undos[response_id]

    async def _recheck_button_vote_counts(self, message: discord.Message, response_id: str):
        """Recheck vote counts after a vote is removed"""
        try:
            # Count current votes
            if response_id not in self.application_votes:
                return

            thumbs_up = sum(1 for vote in self.application_votes[response_id].values() if vote == '👍')
            thumbs_down = sum(1 for vote in self.application_votes[response_id].values() if vote == '👎')

            logger.info(f"Rechecked application {response_id}: {thumbs_up} 👍, {thumbs_down} 👎")

            # Check if threshold is still met
            if thumbs_up >= self.acceptance_threshold:
                await self._accept_application(message, response_id)
            elif thumbs_down >= self.denial_threshold:
                await self._deny_application(message, response_id)

        except Exception as e:
            logger.error(f"Error rechecking vote counts: {e}")

    async def _get_member_and_role(self, response_id: str, guild: discord.Guild):
        """Get the Discord member and role for application processing"""
        responses = await self.google_service.get_form_responses(self.form_id)
        target_response = next((r for r in responses if r['responseId'] == response_id), None)

        if not target_response:
            logger.error(f"Could not find response with ID {response_id}")
            return None, None

        answers = target_response.get('answers', {})
        discord_id, _ = self._get_discord_id_from_answers(answers)

        if not discord_id:
            logger.warning(f"Could not extract Discord ID from response {response_id}")
            return None, None

        member = await self._get_discord_user_by_id(discord_id)
        if not member:
            logger.warning(f"Could not find member with Discord ID {discord_id}")
            return None, None

        role = guild.get_role(self.accepted_role_id)
        if not role:
            logger.error(f"Could not find role with ID {self.accepted_role_id}")
            return member, None

        return member, role

    async def _notify_user(self, member: discord.Member, accepted: bool, guild_name: str):
        """Send DM notification to user about application result"""
        try:
            status = "accepted" if accepted else "denied"
            if accepted:
                message = f"Your application to {guild_name} has been **accepted**! You now have access to the server."
            else:
                message = f"Your application to {guild_name} has been **denied**."

            await member.send(message)
            logger.info(f"Successfully notified {member.display_name} about {status}")
        except discord.Forbidden:
            logger.warning(f"Could not DM {member.display_name} about application result")

    async def _accept_application(self, message: discord.Message, response_id: str):
        """Accept an application"""
        # Check if this application is already processed
        app_data = self.db.get_application_by_message_id(message.id)
        if app_data and app_data.get('status') in ['accepted', 'denied']:
            return  # Already processed

        # Update embed
        embed = message.embeds[0]
        now = datetime.now()
        unix_ts = int(now.timestamp())
        embed.description += f"\n**Approved:** <t:{unix_ts}:f>"
        embed.remove_footer()
        embed.colour = discord.Color.green()

        # Disable all buttons
        view = discord.ui.View()
        for item in view.children:
            item.disabled = True

        await message.edit(embed=embed, view=view)

        # Mark as processed in database
        self.db.set_application_status(response_id, 'accepted')

        # Clean up vote tracking
        if response_id in self.application_votes:
            del self.application_votes[response_id]

        # Get member and role
        member, role = await self._get_member_and_role(response_id, message.guild)

        if member and role:
            try:
                await member.add_roles(role, reason=f"Application {response_id} accepted")
                logger.info(f"Added role {role.name} to {member.display_name}")
                await self._notify_user(member, True, message.guild.name)
            except Exception as e:
                logger.error(f"Error adding role to {member.display_name}: {e}")

        logger.info(f"Application {response_id} accepted")

    async def _deny_application(self, message: discord.Message, response_id: str):
        """Deny an application"""
        # Check if this application is already processed
        app_data = self.db.get_application_by_message_id(message.id)
        if app_data and app_data.get('status') in ['accepted', 'denied']:
            return  # Already processed

        # Update embed
        embed = message.embeds[0]
        now = datetime.now()
        unix_ts = int(now.timestamp())
        embed.description += f"\n**Denied:** <t:{unix_ts}:f>"
        embed.remove_footer()
        embed.colour = discord.Color.red()

        # Disable all buttons
        view = discord.ui.View()
        for item in view.children:
            item.disabled = True

        await message.edit(embed=embed, view=view)

        # Mark as processed in database
        self.db.set_application_status(response_id, 'denied')

        # Clean up vote tracking
        if response_id in self.application_votes:
            del self.application_votes[response_id]

        # Get member and kick them
        member, _ = await self._get_member_and_role(response_id, message.guild)

        if member:
            try:
                await member.kick(reason=f"Application {response_id} denied")
                logger.info(f"Kicked {member.display_name} from the server")
            except Exception as e:
                logger.error(f"Error kicking {member.display_name}: {e}")

        logger.info(f"Application {response_id} denied")


async def setup(bot):
    await bot.add_cog(ApplicationHandler(bot))