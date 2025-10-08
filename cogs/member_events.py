import discord
from discord.ext import commands
from datetime import datetime
import os
import logging

logger = logging.getLogger(__name__)


class WelcomeButtons(discord.ui.View):
    """Persistent view for welcome message buttons."""

    def __init__(self, cog):
        super().__init__(timeout=None)  # Persistent view
        self.cog = cog

    @discord.ui.button(label="How to Apply", style=discord.ButtonStyle.primary, custom_id="how_to_apply")
    async def how_to_apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="How to Apply",
            color=discord.Color.blue(),
            description=(
                f"1. Use the `/apply` command for the link to the application form\n"
                f"2. Complete the Google Form **(don't change the pre-filled Discord ID textbox!)**\n"
                f"3. We will review and process your application shortly\n"
                f"4. You'll be notified of the decision via DM or in this channel\n\n"
            )
        )
        file = discord.File("assets/icons/HAVOC_UNITPATCH_400_GREY.png", filename="unit_icon.png")
        embed.set_thumbnail(url="attachment://unit_icon.png")
        embed.set_footer(text="Please ping an admin if you have any questions!")
        await interaction.response.send_message(file=file, embed=embed, ephemeral=True)
        logger.info(f"User {interaction.user.display_name} requested application info")

    @discord.ui.button(label="Server Info", style=discord.ButtonStyle.primary, custom_id="server_info")
    async def server_info_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Discord Server Info",
            color=discord.Color.blue(),
            description=(
                f"* Our community rules can be found in <#{self.cog.rules_channel_id}>\n"
                f"* Community announcements are posted in <#{self.cog.announcements_channel_id}>\n"
                f"* Gameplay information specific to the way we run operations can be found in <#{self.cog.gameplay_info_channel_id}>\n"
                f"* Mission briefings are posted as forum threads in <#{self.cog.a3_briefings_channel_id}>\n"
                f"* Screenshots and videos from prior missions are posted in <#{self.cog.aar_channel_id}>\n"
                f"* Our modpack can be found in <#{self.cog.mods_channel_id}>\n\n"
            ),
        )
        embed.add_field(
            name="",
            value=f"These channels are open to viewing from the public as a means to provide context and an idea of how our community runs.\n\n"
                  f"More channels, and the ability to post in these channels, are unlocked upon an application being accepted."
        )
        file = discord.File("assets/icons/HAVOC_UNITPATCH_400_GREY.png", filename="unit_icon.png")
        embed.set_thumbnail(url="attachment://unit_icon.png")
        embed.set_footer(text="Please ping an admin if you have any questions!")
        await interaction.response.send_message(file=file, embed=embed, ephemeral=True)
        logger.info(f"User {interaction.user.display_name} requested group info")


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
            self.applicant_channel_id = int(os.getenv("APPLICANT_CHANNEL_ID"))
            self.rules_channel_id = int(os.getenv("RULES_CHANNEL_ID"))
            self.announcements_channel_id = int(os.getenv("ANNOUNCEMENTS_CHANNEL_ID"))
            self.gameplay_info_channel_id = int(os.getenv("GAMEPLAY_INFO_CHANNEL_ID"))
            self.mods_channel_id = int(os.getenv("MODS_CHANNEL_ID"))
            self.aar_channel_id = int(os.getenv("AAR_CHANNEL_ID"))
            self.a3_briefings_channel_id = int(os.getenv("A3_BRIEFINGS_CHANNEL_ID"))
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

            # Send welcome message with buttons in applicant channel
            applicant_channel = guild.get_channel(self.applicant_channel_id)
            if applicant_channel:
                welcome_message = f"Welcome, {member.mention}!"
                view = WelcomeButtons(self)  # Pass the cog instance
                await applicant_channel.send(welcome_message, view=view)
                logger.info(f"Sent welcome message with buttons for {member.display_name}")
            else:
                logger.warning(f"Could not find applicant channel with ID {self.applicant_channel_id}")

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