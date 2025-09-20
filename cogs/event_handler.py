import asyncio

import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import os
import logging
from .database import Database

logger = logging.getLogger(__name__)


class EventHandler(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = Database()

        # Configuration
        #self.briefing_channel_id = int(os.getenv('BRIEFING_CHANNEL_ID'))
        self.event_voice_channel_id = int(os.getenv('EVENT_VOICE_CHANNEL_ID'))
        self.timezone = pytz.timezone('US/Eastern')
        self.event_notification_channel_id = int(os.getenv('EVENT_NOTIFICATION_CHANNEL_ID'))
        self.event_notification_role_id = int(os.getenv('EVENT_NOTIFICATION_ROLE_ID'))

        # Initialize database table
        self.db.initialize_events_table()

        # Start the scheduled tasks
        self.check_event_schedule.start()

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.check_event_schedule.cancel()

    @tasks.loop(minutes=5)  # Check every 5 minutes
    async def check_event_schedule(self):
        """Check if we need to create or delete events"""
        try:
            now = datetime.now(self.timezone)

            # Check if we need to create a new event (Monday 8pm EST)
            if now.weekday() == 0 and now.hour == 20 and now.minute < 5:  # Monday 8pm
                await self._create_weekly_event()

            # Check if we need to delete the old event (Sunday midnight EST)
            if now.weekday() == 6 and now.hour == 0 and now.minute < 5:  # Sunday midnight
                await self._delete_old_event()

        except Exception as e:
            logger.error(f"Error in event schedule check: {e}")

    @check_event_schedule.before_loop
    async def before_check_schedule(self):
        """Wait until bot is ready before starting the loop"""
        await self.bot.wait_until_ready()

    async def _create_weekly_event(self):
        """Create the weekly Sunday Op event"""
        try:
            # Check if there's already an active event this week
            if self.db.has_active_event():
                logger.info("Active event already exists, skipping creation")
                return

            # Calculate next Sunday 5pm EST
            now = datetime.now(self.timezone)
            days_until_sunday = (6 - now.weekday()) % 7
            if days_until_sunday == 0:  # If today is Sunday, get next Sunday
                days_until_sunday = 7

            event_date = now.date() + timedelta(days=days_until_sunday)
            event_datetime = datetime.combine(event_date, datetime.min.time().replace(hour=17))
            event_datetime = self.timezone.localize(event_datetime)

            # Get the guild
            guild = self.bot.guilds[0] if self.bot.guilds else None
            if not guild:
                logger.error("No guild found for event creation")
                return

            # Get the voice channel for the event
            voice_channel = guild.get_channel(self.event_voice_channel_id)
            if not voice_channel:
                logger.error(f"Voice channel {self.event_voice_channel_id} not found")
                return

            # Format the event title with date
            event_title = f"HTG Sunday Op - {event_date.strftime('%B %d')}"

            # Create timestamp for the description
            timestamp = f"<t:{int(event_datetime.timestamp())}:F>"

            # Create the event description
            description = (
                f"**Please select \"Interested\" if you intend to participate**\n\n"
                #f"Check <#{self.briefing_channel_id}> for briefing.\n\n"
                f"Please show up 15 minutes early"
            )

            # Create the Discord event
            event = await guild.create_scheduled_event(
                name=event_title,
                description=description,
                start_time=event_datetime,
                entity_type=discord.EntityType.voice,
                channel=voice_channel,
                privacy_level=discord.PrivacyLevel.guild_only
            )

            # Store in database
            self.db.store_event(event.id, event_date)

            logger.info(f"Created weekly event: {event_title} (ID: {event.id})")

            notification_channel = guild.get_channel(self.event_notification_channel_id)
            role = guild.get_role(self.event_notification_role_id)

            if notification_channel and role:
                await notification_channel.send(
                    content=f"{role.mention} [New Event Posted]({event.url})",
                )

        except Exception as e:
            logger.error(f"Error creating weekly event: {e}")

    async def _delete_old_event(self):
        """Delete the previous week's event and update database with participant info"""
        try:
            # Get the active event from database
            active_event = self.db.get_active_event()
            if not active_event:
                logger.info("No active event to delete")
                return

            event_id = active_event['event_id']

            # Get the guild and event
            guild = self.bot.guilds[0] if self.bot.guilds else None
            if not guild:
                logger.error("No guild found for event deletion")
                return

            try:
                event = await guild.fetch_scheduled_event(event_id)

                # Get interested users before deleting
                interested_users = []
                async for user in event.users():
                    interested_users.append(user.display_name)

                # Update database with final participant info
                self.db.update_event_participants(
                    event_id,
                    len(interested_users),
                    interested_users
                )

                # Delete the Discord event
                await event.delete()

                # Mark as deleted in database
                self.db.mark_event_deleted(event_id)

                logger.info(f"Deleted event {event_id} with {len(interested_users)} interested users")

            except discord.NotFound:
                logger.warning(f"Event {event_id} not found on Discord, marking as deleted in DB")
                self.db.mark_event_deleted(event_id)
            except Exception as e:
                logger.error(f"Error deleting Discord event {event_id}: {e}")

        except Exception as e:
            logger.error(f"Error in delete old event: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the cog is loaded"""
        logger.info(f"{self.__class__.__name__} cog loaded")
        logger.info(f"Event schedule: Create events on Monday 8pm, Delete on Sunday midnight (US/Eastern)")

        # TEST
        await self._test_event_functions()

    async def _test_event_functions(self):
        """Temporary function to test event creation and deletion"""
        logger.info("TESTING: Creating test event")
        await self._create_weekly_event()

        # Wait a bit so you can see the event was created
        await asyncio.sleep(30)

        logger.info("TESTING: Deleting test event")
        await self._delete_old_event()

async def setup(bot):
    """Function called when loading this cog"""
    await bot.add_cog(EventHandler(bot))