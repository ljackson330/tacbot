import discord
from discord.ext import commands
from discord import app_commands
import os
import logging
from urllib.parse import urlencode
import pytz

logger = logging.getLogger(__name__)


class ChatCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Load configuration
        self._load_config()

    def _load_config(self):
        """Load configuration from environment variables."""
        # Form configuration
        self.form_id = os.getenv("GOOGLE_FORM_ID")
        self.discord_id_entry = os.getenv("DISCORD_ID_ENTRY")

        # Admin role configuration
        admin_role_env = os.getenv("ADMIN_ROLE_ID")
        self.admin_role_id = int(admin_role_env) if admin_role_env else None

        # Event configuration (for admin commands)
        guild_id_env = os.getenv("GUILD_ID")
        self.guild_id = int(guild_id_env) if guild_id_env else None

        timezone_str = os.getenv("TIMEZONE", "US/Eastern")
        try:
            self.timezone = pytz.timezone(timezone_str)
        except pytz.UnknownTimeZoneError:
            logger.warning(f"Unknown timezone {timezone_str}, defaulting to US/Eastern")
            self.timezone = pytz.timezone("US/Eastern")

        self.create_day = int(os.getenv("EVENT_CREATE_DAY", "0"))  # Monday
        self.create_hour = int(os.getenv("EVENT_CREATE_HOUR", "20"))  # 8 PM
        self.delete_day = int(os.getenv("EVENT_DELETE_DAY", "6"))  # Sunday
        self.delete_hour = int(os.getenv("EVENT_DELETE_HOUR", "11"))  # Midnight

        # Build form URL from ID
        if self.form_id:
            self.form_url = f"https://docs.google.com/forms/d/{
                self.form_id}/viewform"
        else:
            self.form_url = None
            logger.warning("GOOGLE_FORM_ID environment variable not set")

        logger.info(f"ChatCommands initialized with form_id: {self.form_id}")

    def _is_admin(self, member: discord.Member) -> bool:
        """Check if member has admin role or administrator permissions."""
        if member.guild_permissions.administrator:
            return True

        if self.admin_role_id:
            return any(role.id == self.admin_role_id for role in member.roles)

        return False

    @app_commands.command(name="apply", description="Get the application form link")
    async def apply(self, interaction: discord.Interaction):
        """
        Provides a Google Form application link with the user's Discord ID pre-filled
        """
        try:
            if not self.form_url:
                await interaction.response.send_message("Application form is not configured. Please ping @kanuk", ephemeral=True)
                return

            if not self.discord_id_entry:
                await interaction.response.send_message("Discord ID entry field is not configured. Please ping @kanuk", ephemeral=True)
                return

            # Get the user's Discord ID
            user_id = str(interaction.user.id)

            # Build the pre-filled URL parameters
            prefill_params = {self.discord_id_entry: user_id}

            # Create the complete URL with pre-filled data
            prefilled_url = f"{self.form_url}?{urlencode(prefill_params)}"

            # Create an embed for a more polished response
            embed = discord.Embed(
                title="HTG Application Form",
                description=f"[Click here to apply]({prefilled_url})",
                color=discord.Color.blue(),
            )

            # Send the response (ephemeral so only the user sees it)
            await interaction.response.send_message(embed=embed, ephemeral=True)

            logger.info(
                f"Application form requested by {
                    interaction.user} ({user_id})"
            )

        except Exception as e:
            logger.error(f"Error in apply command: {e}")
            await interaction.response.send_message(
                "An error occurred while generating your application link. Please contact an admin.",
                ephemeral=True,
            )

    # ===== ADMIN EVENT MANAGEMENT COMMANDS =====

    @app_commands.command(name="event_create", description="Manually create a weekly event (admin only)")
    async def event_create(self, interaction: discord.Interaction):
        """Manually create a weekly event (admin only)."""
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            # Get the event handler from the bot
            event_handler = self.bot.get_cog("EventHandler")
            if not event_handler:
                await interaction.response.send_message("Event system is not loaded.", ephemeral=True)
                return

            await interaction.response.defer()
            await event_handler._create_weekly_event()
            await interaction.followup.send("Event creation initiated.")

        except Exception as e:
            logger.error(f"Error in manual event creation: {e}")
            await interaction.followup.send("Error creating event. Check logs for details.")

    @app_commands.command(name="event_delete", description="Manually delete the active event (admin only)")
    async def event_delete(self, interaction: discord.Interaction):
        """Manually delete the active event (admin only)."""
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            # Get the event handler from the bot
            event_handler = self.bot.get_cog("EventHandler")
            if not event_handler:
                await interaction.response.send_message("Event system is not loaded.", ephemeral=True)
                return

            await interaction.response.defer()
            await event_handler._delete_old_event()
            await interaction.followup.send("Event deletion initiated.")

        except Exception as e:
            logger.error(f"Error in manual event deletion: {e}")
            await interaction.followup.send("Error deleting event. Check logs for details.")

    @app_commands.command(name="event_stats", description="Show event statistics (admin only)")
    async def event_stats(self, interaction: discord.Interaction):
        """Show event statistics (admin only)."""
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            await interaction.response.defer()

            # Get database from event handler
            event_handler = self.bot.get_cog("EventHandler")
            if not event_handler:
                await interaction.followup.send("Event system is not loaded.")
                return

            db = event_handler.db
            stats = db.get_event_stats()

            embed = discord.Embed(title="Event Statistics", color=discord.Color.blue())

            embed.add_field(name="Total Events Created", value=stats.get("total_events", 0), inline=True)
            embed.add_field(name="Active Events", value=stats.get("active_events", 0), inline=True)
            embed.add_field(name="Completed Events", value=stats.get("completed_events", 0), inline=True)

            if stats.get("avg_participants"):
                embed.add_field(
                    name="Avg. Participants",
                    value=f"{
                        stats['avg_participants']:.1f}",
                    inline=True,
                )

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error getting event stats: {e}")
            await interaction.followup.send("Error retrieving event statistics.")

    @app_commands.command(name="app_stats", description="Show application statistics (admin only)")
    async def app_stats(self, interaction: discord.Interaction):
        """Show application statistics (admin only)."""
        if not self._is_admin(interaction.user):
            await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        try:
            await interaction.response.defer()

            # Get database from application handler
            app_handler = self.bot.get_cog("ApplicationHandler")
            if not app_handler:
                await interaction.followup.send("Application system is not loaded.")
                return

            db = app_handler.db
            stats = db.get_application_stats()

            embed = discord.Embed(title="Application Statistics", color=discord.Color.blue())

            embed.add_field(name="Total Applications", value=stats.get("total", 0), inline=True)
            embed.add_field(name="Accepted", value=stats.get("accepted", 0), inline=True)
            embed.add_field(name="Denied", value=stats.get("denied", 0), inline=True)
            embed.add_field(name="Pending", value=stats.get("pending", 0), inline=True)

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.error(f"Error getting application stats: {e}")
            await interaction.followup.send("Error retrieving application statistics.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the cog is loaded"""
        logger.info(f"{self.__class__.__name__} cog loaded")


async def setup(bot):
    """Function called when loading this cog"""
    await bot.add_cog(ChatCommands(bot))
