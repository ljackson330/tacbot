import discord
from discord.ext import commands, tasks
from datetime import datetime
import os
import logging
from typing import Dict, Any
from .google_forms_service import GoogleFormsService
from .database import Database

logger = logging.getLogger(__name__)


class ApplicationHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.google_service = GoogleFormsService()

        # Configuration
        self.channel_id = int(os.getenv('APPLICATION_CHANNEL_ID'))
        self.form_id = os.getenv('GOOGLE_FORM_ID')
        self.acceptance_threshold = int(os.getenv('ACCEPTANCE_THRESHOLD'))

        # Question mapping - will be built dynamically from form
        self.question_map = {}

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

            embed = self._create_application_embed(response)
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

    def _create_application_embed(self, response: Dict[str, Any]) -> discord.Embed:
        """Create a Discord embed for the application"""
        iso_timestamp = response['createTime']
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        unix_ts = int(dt.timestamp())
        answers = response.get('answers', {})


        # TODO: unfuck this
        username = None
        if answers and not username:
            first_question_id = list(answers.keys())[0]
            first_answer_data = answers[first_question_id]
            if 'textAnswers' in first_answer_data:
                username = first_answer_data['textAnswers']['answers'][0]['value']

        embed = discord.Embed(
            title=username,
            description=f"**Submitted:** <t:{unix_ts}:f>",
            color=discord.Color.blue()
        )

        # Add form answers as fields
        for question_id, question_title in self.question_map.items():
            # TODO: also this
            if question_id == first_question_id:
                continue
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

        embed.set_footer(text="React with 👍 to approve or 👎 to deny")
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

        await self._handle_application_vote(reaction, app_data)

    async def _handle_application_vote(self, reaction, app_data):
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

            # Check if acceptance threshold is met
            if thumbs_up >= self.acceptance_threshold:
                await self._accept_application(message, response_id)
            elif thumbs_down >= self.acceptance_threshold:
                await self._deny_application(message, response_id)

        except Exception as e:
            logger.error(f"Error handling vote for application {app_data['response_id']}: {e}")

    async def _accept_application(self, message: discord.Message, response_id: str):
        """Accept an application"""
        embed = message.embeds[0]
        now = datetime.now()
        unix_ts = int(now.timestamp())
        embed.description=f"**ACCEPTED:** <t:{unix_ts}:f>"
        embed.remove_footer()
        embed.colour = discord.Color.green()

        await message.edit(embed=embed)
        await message.clear_reactions()

        # Mark as processed in database
        self.db.set_application_status(response_id, 'accepted')

        # TODO: email sending logic here?

        logger.info(f"Application {response_id} accepted")

    async def _deny_application(self, message: discord.Message, response_id: str):
        """Deny an application"""
        embed = message.embeds[0]
        now = datetime.now()
        unix_ts = int(now.timestamp())
        embed.description = f"**DENIED:** <t:{unix_ts}:f>"
        embed.remove_footer()
        embed.colour = discord.Color.red()

        await message.edit(embed=embed)
        await message.clear_reactions()

        # Mark as processed in database
        self.db.set_application_status(response_id, 'denied')

        logger.info(f"Application {response_id} denied")

    async def cog_load(self):
        """Sync slash commands when cog is loaded"""
        try:
            synced = await self.bot.tree.sync()
            logger.info(f"Synchronized {len(synced)} slash commands")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")


async def setup(bot):
    await bot.add_cog(ApplicationHandler(bot))