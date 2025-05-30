import os
import discord
from discord.ext import commands, tasks
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from .google_forms_service import GoogleFormsService
from .database import Database

logger = logging.getLogger(__name__)


class ApplicationHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()
        self.google_service = GoogleFormsService()

        # Configuration - these should come from environment or config
        self.channel_id = int(os.getenv('APPLICATION_CHANNEL_ID'))
        self.form_id = os.getenv('GOOGLE_FORM_ID')
        self.acceptance_threshold = int(os.getenv('ACCEPTANCE_THRESHOLD', '2'))

        # Question mapping - consider moving to config file
        self.question_map = {
            '758b061c': 'Multiple Choice Question',
            '4608a6e7': 'Short Answer Question'
            # Add more mappings as needed
        }

        # Start the polling task
        self.check_new_responses.start()

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.check_new_responses.cancel()

    @tasks.loop(seconds=30)
    async def check_new_responses(self):
        """
        TODO: this will need to be toned down in prod
        Check for new Google Form responses every 30 seconds
        """
        try:
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
        response_id = response['responseId']
        timestamp = response['createTime']
        answers = response.get('answers', {})

        embed = discord.Embed(
            title="New Application Submission",
            description=f"**Application ID:** `{response_id}`\n**Submitted:** `{timestamp}`",
            color=discord.Color.blue()
        )

        # Add form answers as fields
        for question_id, answer_data in answers.items():
            question_title = self.question_map.get(question_id, f"Question {question_id}")

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

        await self._handle_application_vote(reaction, user, app_data)

    async def _handle_application_vote(self, reaction, user, app_data):
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
        # Update embed color to green
        embed = message.embeds[0]
        embed.color = discord.Color.green()
        embed.description += "\n\n**ACCEPTED**"

        await message.edit(embed=embed)

        # Send acceptance message
        await message.channel.send(f"Application `{response_id}` has been **ACCEPTED**!")

        # Mark as processed in database
        self.db.set_application_status(response_id, 'accepted')

        # TODO: Add email sending logic here
        # await self._send_acceptance_email(response_id)

        logger.info(f"Application {response_id} accepted")

    async def _deny_application(self, message: discord.Message, response_id: str):
        """Deny an application"""
        # Update embed color to red
        embed = message.embeds[0]
        embed.color = discord.Color.red()
        embed.description += "\n\n**DENIED**"

        await message.edit(embed=embed)

        # Send denial message
        await message.channel.send(f"Application `{response_id}` has been **DENIED**.")

        # Mark as processed in database
        self.db.set_application_status(response_id, 'denied')

        logger.info(f"Application {response_id} denied")

    @commands.command(name='appstatus')
    @commands.has_permissions(manage_messages=True)
    async def application_status(self, ctx, response_id: str = None):
        """Check the status of an application"""
        if not response_id:
            await ctx.send("Please provide an application ID.")
            return

        status = self.db.get_application_status(response_id)
        if status:
            await ctx.send(f"Application `{response_id}` status: **{status['status'].upper()}**")
        else:
            await ctx.send(f"Application `{response_id}` not found.")

    @commands.command(name='recheck')
    @commands.has_permissions(administrator=True)
    async def force_recheck(self, ctx):
        """Manually trigger a check for new responses"""
        await ctx.send("Checking for new responses...")
        await self.check_new_responses()
        await ctx.send("Check complete")


async def setup(bot):
    await bot.add_cog(ApplicationHandler(bot))