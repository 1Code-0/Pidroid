# pyright: reportMissingImports=false

import aiohttp
import asyncio
import os
import signal
import sys
import logging

from argparse import ArgumentParser
from discord.ext import commands
from discord.ext.commands import Context

# Allows us to not set the Python path
sys.path.append(os.getcwd())

from pidroid.client import Pidroid, __VERSION__ # noqa: E402
from pidroid.constants import DATA_FILE_PATH, TEMPORARY_FILE_PATH # noqa: E402

# Use uvloop if possible
try:
    import uvloop # If my calculations are correct, when this baby hits eighty-eight miles per hour you're gonna see some serious shit
    uvloop.install() # pyright: ignore[reportUnknownMemberType]
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except ImportError:
    pass
    

# Use selector event loop since proactor has some issues
if sys.platform == 'win32':
    from asyncio import WindowsSelectorEventLoopPolicy
    asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())

# Setup Pidroid level logging
logger = logging.getLogger("Pidroid")
#logger.setLevel(logging.WARNING)
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
formatter = logging.Formatter('[%(asctime)s %(name)s:%(levelname)s]: %(message)s', "%Y-%m-%d %H:%M:%S")
ch.setFormatter(formatter)
logger.addHandler(ch)


def load_env_from_file(path: str) -> None:
    """Load environment values from the specified file."""
    logger.info(f"Loading environment from {path} file")
    with open(path) as f:
        for line in f.readlines():
            key, value = line.split("=", 1)
            os.environ[key] = value.strip()

def get_postgres_dsn() -> str:
    postgres_dsn = os.environ.get("POSTGRES_DSN", None)
    if postgres_dsn == '':
        postgres_dsn = None
    if postgres_dsn is None:
        logger.debug("POSTGRES_DSN variable was not found, attempting to resolve from postgres variables")
        
        user = os.environ.get("DB_USER", None)
        password = os.environ.get("DB_PASSWORD", None)
        host = os.environ.get("DB_HOST", "127.0.0.1")
        
        if user is None or password is None:
            logger.critical((
                "Unable to create a postgres DSN string. "
                "DB_USER or DB_PASSWORD environment variable is missing."
            ))
            exit()
        postgres_dsn = "postgresql+asyncpg://{}:{}@{}".format(user, password, host)
    return postgres_dsn

def config_from_env() -> dict[str, list[str] | str | bool | None]:

    if os.environ.get("TOKEN", None) is None:
        exit("No bot token was specified. Please specify it using the TOKEN environment variable.")
    
    postgres_dsn = get_postgres_dsn()

    prefix_string = os.environ.get("PREFIXES", "P, p, TT")
    prefixes = [p.strip() for p in prefix_string.split(",")]
    
    debugging = False
    if os.environ.get("DEBUGGING", "0").lower() in ['1', 'true']:
        debugging = True
        logger.info("Debugging mode is enabled")
        logger.setLevel(logging.DEBUG)

    return {
        "debugging": debugging,
        
        "token": os.environ["TOKEN"],
        "prefixes": prefixes,

        "postgres_dsn": postgres_dsn,

        "tt_api_key": os.environ.get("TT_API_KEY"),
        "deepl_api_key": os.environ.get("DEEPL_API_KEY"),
        "tenor_api_key": os.environ.get("TENOR_API_KEY"),
        "unbelievaboat_api_key": os.environ.get("UNBELIEVABOAT_API_KEY")
    }

async def main():  # noqa: C901

    # Create directories in case they don't exist
    if not os.path.exists(DATA_FILE_PATH):
        os.mkdir(DATA_FILE_PATH)
    if not os.path.exists(TEMPORARY_FILE_PATH):
        os.mkdir(TEMPORARY_FILE_PATH)

    arg_parser = ArgumentParser()
    arg_parser.add_argument("-e", "--envfile", help="specifies .env file to load environment from")

    args = arg_parser.parse_args()
    if args.envfile:
        load_env_from_file(args.envfile)

    # Load configuration from environment
    bot = Pidroid(config_from_env())

    @bot.command(hidden=True)
    @commands.is_owner()
    @commands.bot_has_permissions(send_messages=True)
    async def reload(ctx: Context):
        try:
            await bot.handle_reload()
            await ctx.reply('The entirety of the bot has been reloaded!')
        except Exception as e:
            await ctx.reply(f'The following exception occurred while trying to reload the bot:\n```{e}```')

    # Handle docker's sigterm signal gracefully
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(
        signal.SIGTERM,
        lambda: loop.create_task(bot.close())
    ) 

    async with aiohttp.ClientSession() as session:
        async with bot:
            bot.session = session
            await bot.start(bot.token)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
