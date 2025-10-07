import discord
from discord.ext import commands
from datetime import datetime
import os
import logging

logger = logging.getLogger(__name__)


class MemberEvents(commands.Cog):
    """Handles member join/leave events and role assignments."""

    def __init__(self, bot):
        self.bot = bot
        self._load_config()

    def _load_config(self):
        """Load configuration from environment variables."""
        try:
            self.guild_id = int(os.getenv("GUILD_ID"))
            self.applicant_role_id = int(os.getenv("APPLICANT_ROLE_ID"))
            self.admin_role_id = int(os.getenv("ADMIN_ROLE_ID"))
            self.join_leave_channel_id = int(os.getenv("JOIN_LEAVE_CHANNEL_ID"))
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid configuration values: {e}")
            raise

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Automatically assign applicant role when a member joins and notifies admins"""
        # Only process for the configured guild
        if member.guild.id != self.guild_id:
            return

        guild = self.bot.get_guild(self.guild_id)
        try:
            # Assign applicant role
            role = member.guild.get_role(self.applicant_role_id)

            if not role:
                logger.error(f"Could not find applicant role with ID {self.applicant_role_id}")
            else:
                await member.add_roles(role, reason="Auto-assigned applicant role on join")
                logger.info(f"Assigned applicant role to {member.display_name} ({member.id})")

            # Send notification to join/leave channel
            join_leave_channel = guild.get_channel(self.join_leave_channel_id)
            if join_leave_channel:
                admin_role = guild.get_role(self.admin_role_id)
                if admin_role:
                    welcome_notification = f"{admin_role.mention}, {member.mention} has joined! Go say hi!"
                    await join_leave_channel.send(welcome_notification)
                    logger.info(f"Sent join notification for {member.display_name}")
                else:
                    logger.warning(f"Could not find admin role with ID {self.admin_role_id}")
            else:
                logger.warning(f"Could not find join/leave channel with ID {self.join_leave_channel_id}")

        except discord.Forbidden:
            logger.error(f"Missing permissions to assign role to {member.display_name}")
        except discord.HTTPException as e:
            logger.error(f"Failed to assign applicant role to {member.display_name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error assigning applicant role to {member.display_name}: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Log when a member leaves the server."""
        # Only process for the configured guild
        if member.guild.id != self.guild_id:
            return

        guild = self.bot.get_guild(self.guild_id)
        logger.info(f"Member left: {member.display_name} ({member.id})")

        # Send leave notification to channel
        try:
            join_leave_channel = guild.get_channel(self.join_leave_channel_id)
            if join_leave_channel:
                now = datetime.now()
                unix_ts = int(now.timestamp())
                leave_message = f"{member.mention} ({member.display_name}) has left the server. <t:{unix_ts}:f>"
                await join_leave_channel.send(leave_message)
                logger.info(f"Sent leave notification for {member.display_name}")
            else:
                logger.warning(f"Could not find join/leave channel with ID {self.join_leave_channel_id}")
        except discord.HTTPException as e:
            logger.error(f"Failed to send leave notification for {member.display_name}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending leave notification: {e}")


async def setup(bot):
    """Setup function for the cog."""
    await bot.add_cog(MemberEvents(bot))
