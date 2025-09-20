import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta, time
import pytz
import os
import logging
from typing import Optional
from .database import Database

logger = logging.getLogger(__name__)


class EventHandler(commands.Cog):
    """Handles automatic Discord event creation and management."""

    def __init__(self, bot):
        self.bot = bot
        self.db = Database()

        # Load and validate configuration
        self._load_config()
        self._validate_config()

        # Initialize database table
        self.db.initialize_events_table()

        # Track last execution times to prevent duplicate operations
        self._last_create_check = None
        self._last_delete_check = None

        # Start the scheduled tasks
        self.check_event_schedule.start()

    def _load_config(self):
        """Load configuration from environment variables."""
        try:
            self.guild_id = int(os.getenv("GUILD_ID"))
            self.event_voice_channel_id = int(os.getenv("EVENT_VOICE_CHANNEL_ID"))
            self.event_notification_channel_id = int(os.getenv("EVENT_NOTIFICATION_CHANNEL_ID"))
            self.event_notification_role_id = int(os.getenv("EVENT_NOTIFICATION_ROLE_ID"))

            # Event timing configuration
            self.event_time_hour = int(os.getenv("EVENT_TIME_HOUR", "17"))  # 5 PM
            self.event_time_minute = int(os.getenv("EVENT_TIME_MINUTE", "0"))  # On the hour
            self.create_day = int(os.getenv("EVENT_CREATE_DAY", "0"))  # Monday (0=Monday)
            self.create_hour = int(os.getenv("EVENT_CREATE_HOUR", "20"))  # 8 PM
            self.delete_day = int(os.getenv("EVENT_DELETE_DAY", "6"))  # Sunday
            self.delete_hour = int(os.getenv("EVENT_DELETE_HOUR", "23"))  # 11 PM

            # Timezone configuration
            timezone_str = os.getenv("TIMEZONE", "US/Eastern")
            self.timezone = pytz.timezone(timezone_str)

            # Task interval configuration
            self.check_interval = int(os.getenv("EVENT_CHECK_INTERVAL", "5"))  # 5 minutes

        except (ValueError, TypeError) as e:
            logger.error(f"Invalid configuration value: {e}")
            raise
        except pytz.UnknownTimeZoneError as e:
            logger.error(f"Invalid timezone: {e}")
            raise

    def _validate_config(self):
        """Validate required configuration is present and reasonable."""
        required_vars = {
            "GUILD_ID": self.guild_id,
            "EVENT_VOICE_CHANNEL_ID": self.event_voice_channel_id,
            "EVENT_NOTIFICATION_CHANNEL_ID": self.event_notification_channel_id,
            "EVENT_NOTIFICATION_ROLE_ID": self.event_notification_role_id,
        }

        missing = [name for name, value in required_vars.items() if value is None]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {
                    ', '.join(missing)}"
            )

        # Validate time values
        if not (0 <= self.event_time_hour <= 23):
            raise ValueError("EVENT_TIME_HOUR must be between 0-23")
        if not (0 <= self.event_time_minute <= 59):
            raise ValueError("EVENT_TIME_MINUTE must be between 0-59")
        if not (0 <= self.create_day <= 6):
            raise ValueError("EVENT_CREATE_DAY must be between 0-6 (Monday-Sunday)")
        if not (0 <= self.delete_day <= 6):
            raise ValueError("EVENT_DELETE_DAY must be between 0-6 (Monday-Sunday)")
        if not (0 <= self.create_hour <= 23):
            raise ValueError("EVENT_CREATE_HOUR must be between 0-23")
        if not (0 <= self.delete_hour <= 23):
            raise ValueError("EVENT_DELETE_HOUR must be between 0-23")

        logger.info(
            f"EventHandler configured: Create on {
                self._day_name(
                    self.create_day)} at {
                self.create_hour}:00, "
            f"Delete on {
                self._day_name(
                    self.delete_day)} at {
                        self.delete_hour}:00 ({
                            self.timezone})"
        )

    def _day_name(self, day_num: int) -> str:
        """Convert day number to name."""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return days[day_num] if 0 <= day_num <= 6 else "Unknown"

    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        self.check_event_schedule.cancel()
        logger.info("EventHandler cog unloaded")

    async def cog_load(self):
        """Initialize when cog is loaded."""
        await self._cleanup_stale_events()
        logger.info("EventHandler cog loaded")

    async def _cleanup_stale_events(self):
        """Clean up any stale events on startup."""
        try:
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                logger.warning(
                    f"Guild {
                        self.guild_id} not found during cleanup"
                )
                return

            # Get all active events from database
            active_events = self.db.get_all_active_events()

            for event_data in active_events:
                event_id = event_data["event_id"]
                try:
                    # Check if event still exists on Discord
                    await guild.fetch_scheduled_event(event_id)
                    logger.debug(f"Event {event_id} still exists on Discord")
                except discord.NotFound:
                    # Event doesn't exist on Discord, mark as deleted in
                    # database
                    logger.info(f"Marking stale event {event_id} as deleted")
                    self.db.mark_event_deleted(event_id)
                except discord.HTTPException as e:
                    logger.warning(f"Could not check event {event_id}: {e}")

            logger.info("Completed stale event cleanup")

        except Exception as e:
            logger.error(f"Error during stale event cleanup: {e}")

    @tasks.loop(minutes=5)  # Default 5 minutes, configurable
    async def check_event_schedule(self):
        """Check if we need to create or delete events."""
        try:
            now = datetime.now(self.timezone)
            current_time_key = now.strftime("%Y-%m-%d-%H")

            # Check if we need to create a new event
            if self._should_create_event(now, current_time_key):
                await self._create_weekly_event()
                self._last_create_check = current_time_key

            # Check if we need to delete the old event
            if self._should_delete_event(now, current_time_key):
                await self._delete_old_event()
                self._last_delete_check = current_time_key

        except Exception as e:
            logger.error(f"Error in event schedule check: {e}")

    def _should_create_event(self, now: datetime, current_time_key: str) -> bool:
        """Check if we should create an event now."""
        return now.weekday() == self.create_day and now.hour == self.create_hour and self._last_create_check != current_time_key

    def _should_delete_event(self, now: datetime, current_time_key: str) -> bool:
        """Check if we should delete an event now."""
        return now.weekday() == self.delete_day and now.hour == self.delete_hour and self._last_delete_check != current_time_key

    @check_event_schedule.before_loop
    async def before_check_schedule(self):
        """Wait until bot is ready before starting the loop."""
        await self.bot.wait_until_ready()
        # Update loop interval if configured
        self.check_event_schedule.change_interval(minutes=self.check_interval)
        logger.info(
            f"Event schedule check started (interval: {
                self.check_interval} minutes)"
        )

    async def _create_weekly_event(self):
        """Create the weekly Sunday Op event."""
        try:
            # Check if there's already an active event this week
            if self.db.has_active_event():
                logger.info("Active event already exists, skipping creation")
                return

            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                logger.error(
                    f"Guild {
                        self.guild_id} not found for event creation"
                )
                return

            # Get the voice channel for the event
            voice_channel = guild.get_channel(self.event_voice_channel_id)
            if not voice_channel:
                logger.error(
                    f"Voice channel {
                        self.event_voice_channel_id} not found"
                )
                return

            # Calculate next Sunday at specified time
            event_datetime = self._calculate_next_sunday()
            if not event_datetime:
                logger.error("Could not calculate next Sunday for event")
                return

            # Format the event title with date
            event_title = f"HTG Sunday Op - {event_datetime.strftime('%B %d')}"

            # Create the event description
            description = self._create_event_description()

            # Create the Discord event
            event = await guild.create_scheduled_event(
                name=event_title,
                description=description,
                start_time=event_datetime,
                entity_type=discord.EntityType.voice,
                channel=voice_channel,
                privacy_level=discord.PrivacyLevel.guild_only,
            )

            # Store in database
            self.db.store_event(event.id, event_datetime.date())

            logger.info(
                f"Created weekly event: {event_title} (ID: {
                    event.id})"
            )

            # Send notification
            await self._send_event_notification(guild, event)

        except Exception as e:
            logger.error(f"Error creating weekly event: {e}")

    def _calculate_next_sunday(self) -> Optional[datetime]:
        """Calculate the next Sunday at the specified time."""
        try:
            now = datetime.now(self.timezone)

            # Calculate days until next Sunday
            days_until_sunday = (6 - now.weekday()) % 7
            if days_until_sunday == 0:  # If today is Sunday
                # Check if it's already past the event time
                event_time_today = now.replace(
                    hour=self.event_time_hour,
                    minute=self.event_time_minute,
                    second=0,
                    microsecond=0,
                )
                if now >= event_time_today:
                    days_until_sunday = 7  # Get next Sunday
                # else: use today (days_until_sunday remains 0)

            # Create the event datetime
            event_date = now.date() + timedelta(days=days_until_sunday)
            event_time = time(hour=self.event_time_hour, minute=self.event_time_minute)
            event_datetime = datetime.combine(event_date, event_time)
            event_datetime = self.timezone.localize(event_datetime)

            return event_datetime

        except Exception as e:
            logger.error(f"Error calculating next Sunday: {e}")
            return None

    def _create_event_description(self) -> str:
        """Create the event description text."""
        description = '**Please select "Interested" if you intend to participate**\n\n' "Please show up 15 minutes early for briefing and setup."
        return description

    async def _send_event_notification(self, guild: discord.Guild, event: discord.ScheduledEvent):
        """Send notification about the new event."""
        try:
            notification_channel = guild.get_channel(self.event_notification_channel_id)
            if not notification_channel:
                logger.warning(
                    f"Notification channel {
                        self.event_notification_channel_id} not found"
                )
                return

            role = guild.get_role(self.event_notification_role_id)
            if not role:
                logger.warning(
                    f"Notification role {
                        self.event_notification_role_id} not found"
                )
                role_mention = "@everyone"
            else:
                role_mention = role.mention

            await notification_channel.send(
                content=f"{role_mention} [New Event Posted]({event.url})",
            )

            logger.info(
                f"Sent event notification to {
                    notification_channel.name}"
            )

        except discord.HTTPException as e:
            logger.error(f"Error sending event notification: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending event notification: {e}")

    async def _delete_old_event(self):
        """Delete the previous week's event and update database with participant info."""
        try:
            # Get the active event from database
            active_event = self.db.get_active_event()
            if not active_event:
                logger.info("No active event to delete")
                return

            event_id = active_event["event_id"]
            guild = self.bot.get_guild(self.guild_id)
            if not guild:
                logger.error(
                    f"Guild {
                        self.guild_id} not found for event deletion"
                )
                return

            try:
                event = await guild.fetch_scheduled_event(event_id)

                # Get interested users before deleting
                interested_users = []
                try:
                    async for user in event.users():
                        interested_users.append({"id": user.id, "name": user.display_name})
                except Exception as e:
                    logger.warning(f"Error fetching event users: {e}")

                # Update database with final participant info
                self.db.update_event_participants(event_id, len(interested_users), [user["name"] for user in interested_users])

                # Delete the Discord event
                await event.delete()

                # Mark as deleted in database
                self.db.mark_event_deleted(event_id)

                logger.info(
                    f"Deleted event {event_id} with {
                        len(interested_users)} interested users"
                )

            except discord.NotFound:
                logger.warning(f"Event {event_id} not found on Discord, marking as deleted in DB")
                self.db.mark_event_deleted(event_id)
            except discord.HTTPException as e:
                logger.error(f"HTTP error deleting Discord event {event_id}: {e}")
                # Still mark as deleted in database if it's a 404-like error
                if e.status == 404:
                    self.db.mark_event_deleted(event_id)

        except Exception as e:
            logger.error(f"Error in delete old event: {e}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the cog is ready."""
        logger.info(f"{self.__class__.__name__} cog ready")
        logger.info(
            f"Event schedule: Create on {
                self._day_name(
                    self.create_day)} at {
                self.create_hour}:00, "
            f"Delete on {
                self._day_name(
                    self.delete_day)} at {
                        self.delete_hour}:00 ({
                            self.timezone})"
        )


async def setup(bot):
    """Function called when loading this cog."""
    await bot.add_cog(EventHandler(bot))
