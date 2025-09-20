import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
import asyncio
import signal

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s",
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()],
)


def validate_environment():
    """Validate required environment variables are set"""
    required_vars = [
        "DISCORD_TOKEN",
        "GUILD_ID",
        "APPLICATION_CHANNEL_ID",
        "GOOGLE_FORM_ID",
        "GOOGLE_CREDENTIALS_FILE",
        "GOOGLE_TOKEN_FILE",
        "MEMBER_ROLE_ID",
    ]

    missing_vars = []
    invalid_vars = []

    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        elif var.endswith("_ID") and var != "GOOGLE_FORM_ID":
            try:
                int(value)
            except ValueError:
                invalid_vars.append(f"{var} (not a valid integer)")

    if missing_vars or invalid_vars:
        error_msg = "Environment validation failed:\n"
        if missing_vars:
            error_msg += f"Missing variables: {', '.join(missing_vars)}\n"
        if invalid_vars:
            error_msg += f"Invalid variables: {', '.join(invalid_vars)}\n"
        raise ValueError(error_msg)


class TacBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.reactions = True

        super().__init__(command_prefix="!", intents=intents, description="Havoc Tactical Bot")

    async def setup_hook(self):
        """Called when the bot is starting up"""
        cogs_to_load = ["cogs.application_handler", "cogs.chat_commands", "cogs.event_handler"]

        for cog in cogs_to_load:
            try:
                await self.load_extension(cog)
                logging.info(f"Loaded cog: {cog}")
            except Exception as e:
                logging.error(f"Failed to load cog {cog}: {e}")

    async def on_ready(self):
        logging.info(f"TacBot has connected to Discord as {self.user}")
        logging.info(f"Bot ID: {self.user.id}")

        # Sync slash commands
        try:
            synced = await self.tree.sync()
            logging.info(f"Synced {len(synced)} command(s)")
        except Exception as e:
            logging.error(f"Failed to sync commands: {e}")


async def main():
    """Main bot entry point with proper error handling and cleanup"""
    bot = None
    try:
        # Validate environment before starting
        validate_environment()

        bot = TacBot()

        # Set up signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            logging.info(f"Received signal {signum}, initiating graceful shutdown...")
            if bot:
                asyncio.create_task(bot.close())

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        async with bot:
            await bot.start(os.getenv("DISCORD_TOKEN"))

    except discord.LoginFailure:
        logging.error("Invalid Discord token provided")
    except discord.HTTPException as e:
        logging.error(f"Discord HTTP error: {e}")
    except KeyboardInterrupt:
        logging.info("Bot shutdown requested by user")
    except ValueError as e:
        logging.error(f"Configuration error: {e}")
    except Exception as e:
        logging.error(f"TacBot encountered an unexpected error: {e}")
    finally:
        if bot and not bot.is_closed():
            await bot.close()
        logging.info("Bot shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
