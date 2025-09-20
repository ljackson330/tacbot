import discord
from discord.ext import commands
from discord import app_commands
import os
import logging
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


class ChatCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Get the form ID from environment variables
        self.form_id = os.getenv('GOOGLE_FORM_ID')

        if not self.form_id:
            logger.warning("GOOGLE_FORM_ID environment variable not set")

        # Build form URL from ID
        if self.form_id:
            self.form_url = f"https://docs.google.com/forms/d/{self.form_id}/viewform"
        else:
            self.form_url = None
            logger.warning("GOOGLE_FORM_ID environment variable not set")

        logger.info(f"ChatCommands initialized with form_id: {self.form_id}")

    @app_commands.command(name="apply", description="Get the application form link")
    async def apply(self, interaction: discord.Interaction):
        """
        Provides a Google Form application link with the user's Discord ID pre-filled
        """
        try:
            if not self.form_url:
                await interaction.response.send_message(
                    "Application form is not configured",
                    ephemeral=True
                )
                return

            # Get the user's Discord ID
            user_id = str(interaction.user.id)

            # Create the pre-filled form URL
            discord_id_entry = os.getenv('DISCORD_ID_ENTRY')

            # Build the pre-filled URL parameters
            prefill_params = {
                discord_id_entry: user_id
            }

            # Create the complete URL with pre-filled data
            prefilled_url = f"{self.form_url}?{urlencode(prefill_params)}"

            # Create an embed for a more polished response
            embed = discord.Embed(
                title="HTG Application Form",
                description=f"[Click here to apply]({prefilled_url})",
                color=discord.Color.blue()
            )

            # Send the response (ephemeral so only the user sees it)
            await interaction.response.send_message(embed=embed, ephemeral=True)

            logger.info(f"Application form requested by {interaction.user} ({user_id})")

        except Exception as e:
            logger.error(f"Error in apply command: {e}")
            await interaction.response.send_message(
                "An error occurred while generating your application link. Please ping @kanuk.",
                ephemeral=True
            )

    @commands.Cog.listener()
    async def on_ready(self):
        """Called when the cog is loaded"""
        logger.info(f"{self.__class__.__name__} cog loaded")


async def setup(bot):
    """Function called when loading this cog"""
    await bot.add_cog(ChatCommands(bot))