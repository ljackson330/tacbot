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

        await interaction.response.send_message("Vote cancelled! Your reaction has been removed.", ephemeral=True)

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
            message = await channel.send(embed=embed)

            # Add initial reactions
            await message.add_reaction('👍')
            await message.add_reaction('👎')

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

        return embed

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Handle reaction additions to application messages"""
        # Ignore bot reactions
        if user.bot:
            return

        # Check if this is an application message
        app_data = self.db.get_application_by_message_id(reaction.message.id)
        if not app_data:
            return

        # Only handle thumbs up/down reactions
        if str(reaction.emoji) not in ['👍', '👎']:
            return

        await self._handle_application_vote(reaction, app_data, user)

    async def _handle_application_vote(self, reaction, app_data, user):
        """Handle voting on applications"""
        try:
            message = reaction.message
            response_id = app_data['response_id']

            # Count current reactions (excluding bot)
            thumbs_up = 0
            thumbs_down = 0

            for msg_reaction in message.reactions:
                if str(msg_reaction.emoji) == '👍':
                    thumbs_up = msg_reaction.count - 1  # Subtract bot reaction
                elif str(msg_reaction.emoji) == '👎':
                    thumbs_down = msg_reaction.count - 1  # Subtract bot reaction

            logger.info(f"Application {response_id}: {thumbs_up} 👍, {thumbs_down} 👎")

            # Check if this vote is decisive (reaches threshold)
            is_decisive = False
            if thumbs_up >= self.acceptance_threshold and str(reaction.emoji) == '👍':
                is_decisive = True
                decision_type = "accept"
            elif thumbs_down >= self.denial_threshold and str(reaction.emoji) == '👎':
                is_decisive = True
                decision_type = "deny"

            # If this is a decisive vote, offer undo option
            if is_decisive:
                await self._offer_vote_undo(message, user, str(reaction.emoji), response_id, decision_type)

            # Only process application if this isn't a decisive vote (which needs undo window)
            # If it's a decisive vote, the processing will happen after the undo timeout
            if not is_decisive:
                # Check if acceptance threshold is met
                if thumbs_up >= self.acceptance_threshold:
                    await self._accept_application(message, response_id)
                elif thumbs_down >= self.denial_threshold:
                    await self._deny_application(message, response_id)

        except Exception as e:
            logger.error(f"Error handling vote for application {app_data['response_id']}: {e}")

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
                # User cancelled their vote - remove ONLY their reaction
                try:
                    await message.remove_reaction(reaction_emoji, user)
                    logger.info(
                        f"Removed {reaction_emoji} reaction from {user.display_name} on application {response_id}")

                    # Recheck vote counts after removal
                    await asyncio.sleep(0.5)  # Small delay to ensure reaction is removed
                    await self._recheck_vote_counts(message, response_id)

                except Exception as e:
                    logger.error(f"Error removing reaction: {e}")
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

    async def _recheck_vote_counts(self, message: discord.Message, response_id: str):
        """Recheck vote counts after a reaction is removed"""
        try:
            # Refresh message to get current reactions
            message = await message.channel.fetch_message(message.id)

            # Count current reactions (excluding bot)
            thumbs_up = 0
            thumbs_down = 0

            for msg_reaction in message.reactions:
                if str(msg_reaction.emoji) == '👍':
                    thumbs_up = msg_reaction.count - 1  # Subtract bot reaction
                elif str(msg_reaction.emoji) == '👎':
                    thumbs_down = msg_reaction.count - 1  # Subtract bot reaction

            logger.info(f"Rechecked application {response_id}: {thumbs_up} 👍, {thumbs_down} 👎")

            # Check if threshold is still met
            if thumbs_up >= self.acceptance_threshold:
                await self._accept_application(message, response_id)
            elif thumbs_down >= self.denial_threshold:
                await self._deny_application(message, response_id)

        except Exception as e:
            logger.error(f"Error rechecking vote counts: {e}")

    async def _accept_application(self, message: discord.Message, response_id: str):
        """Accept an application"""
        # Check if this application is already processed
        app_data = self.db.get_application_by_message_id(message.id)
        if app_data and app_data.get('status') in ['accepted', 'denied']:
            return  # Already processed

        embed = message.embeds[0]
        now = datetime.now()
        unix_ts = int(now.timestamp())
        embed.description += f"\n**Approved:** <t:{unix_ts}:f>"
        embed.remove_footer()
        embed.colour = discord.Color.green()

        await message.edit(embed=embed)
        # Remove all reactions when application is processed
        await message.clear_reactions()

        # Mark as processed in database
        self.db.set_application_status(response_id, 'accepted')

        # Get the full response and extract Discord ID
        responses = await self.google_service.get_form_responses(self.form_id)
        target_response = next((r for r in responses if r['responseId'] == response_id), None)

        if target_response:
            answers = target_response.get('answers', {})
            discord_id, _ = self._get_discord_id_from_answers(answers)

            if discord_id:
                member = await self._get_discord_user_by_id(discord_id)
                if member:
                    try:
                        role = message.guild.get_role(self.accepted_role_id)
                        if role:
                            await member.add_roles(role, reason=f"Application {response_id} accepted")
                            logger.info(f"Added role {role.name} to {member.display_name}")

                            # Try to DM the user about acceptance
                            try:
                                await member.send(
                                    f"Your application to {message.guild.name} has been **accepted**! You now have access to the server.")
                            except discord.Forbidden:
                                logger.warning(f"Could not DM {member.display_name} about acceptance")
                        else:
                            logger.error(f"Could not find role with ID {self.accepted_role_id}")
                    except Exception as e:
                        logger.error(f"Error adding role to {member.display_name}: {e}")
                else:
                    logger.warning(f"Could not find member with Discord ID {discord_id}")
            else:
                logger.warning(f"Could not extract Discord ID from response {response_id}")
        else:
            logger.error(f"Could not find response with ID {response_id}")

        logger.info(f"Application {response_id} accepted")

    async def _deny_application(self, message: discord.Message, response_id: str):
        """Deny an application"""
        # Check if this application is already processed
        app_data = self.db.get_application_by_message_id(message.id)
        if app_data and app_data.get('status') in ['accepted', 'denied']:
            return  # Already processed

        embed = message.embeds[0]
        now = datetime.now()
        unix_ts = int(now.timestamp())
        embed.description += f"\n**Denied:** <t:{unix_ts}:f>"
        embed.remove_footer()
        embed.colour = discord.Color.red()

        await message.edit(embed=embed)
        # Remove all reactions when application is processed
        await message.clear_reactions()

        # Mark as processed in database
        self.db.set_application_status(response_id, 'denied')

        # Get the full response and extract Discord ID
        responses = await self.google_service.get_form_responses(self.form_id)
        target_response = next((r for r in responses if r['responseId'] == response_id), None)

        if target_response:
            answers = target_response.get('answers', {})
            discord_id, _ = self._get_discord_id_from_answers(answers)

            if discord_id:
                member = await self._get_discord_user_by_id(discord_id)
                if member:
                    try:
                        # Kick the user
                        await member.kick(reason=f"Application {response_id} denied")
                        logger.info(f"Kicked {member.display_name} from the server")

                    except Exception as e:
                        logger.error(f"Error kicking {member.display_name}: {e}")
                else:
                    logger.warning(f"Could not find member with Discord ID {discord_id}")
            else:
                logger.warning(f"Could not extract Discord ID from response {response_id}")
        else:
            logger.error(f"Could not find response with ID {response_id}")

        logger.info(f"Application {response_id} denied")


async def setup(bot):
    await bot.add_cog(ApplicationHandler(bot))