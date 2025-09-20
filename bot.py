import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
import asyncio

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)


class TacBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True

        super().__init__(
            command_prefix="!",
            intents=intents,
            description='Havoc Tactical Bot'
        )

    async def setup_hook(self):
        """Called when the bot is starting up"""
        cogs_to_load = [
            'cogs.application_handler',
            'cogs.chat_commands',
            'cogs.event_handler'
        ]

        for cog in cogs_to_load:
            try:
                await self.load_extension(cog)
                logging.info(f"Loaded cog: {cog}")
            except Exception as e:
                logging.error(f"Failed to load cog {cog}: {e}")

    async def on_ready(self):
        logging.info(f'TacBot has connected to Discord as {self.user}')
        logging.info(f'Bot ID: {self.user.id}')

        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logging.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")


async def main():
    bot = TacBot()

    try:
        async with bot:
            await bot.start(os.getenv('DISCORD_TOKEN'))
    except Exception as e:
        logging.error(f"TacBot encountered an error: {e}")


if __name__ == "__main__":
    asyncio.run(main())