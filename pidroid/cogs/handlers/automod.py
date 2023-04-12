from discord import Member
from discord.ext import commands # type: ignore

from pidroid.client import Pidroid
from pidroid.models.guild_configuration import GuildConfiguration
from pidroid.utils.logger import SuspiciousUserLog

class AutomodTask(commands.Cog): # type: ignore
    """This class implements a cog for handling automatic moderation of the TheoTown guild."""

    def __init__(self, client: Pidroid) -> None:
        self.client = client

    async def handle_suspicious_member(self, config: GuildConfiguration, member: Member) -> bool:
        """Handles the detection of members which are deemed suspicious."""
        for trigger_word in config.suspicious_usernames:
            if trigger_word in member.name.lower():
                await self.client.dispatch_log(member.guild, SuspiciousUserLog(member, trigger_word))
                return True
        return False

    @commands.Cog.listener() # type: ignore
    async def on_member_join(self, member: Member) -> None:
        """Checks whether new member is suspicious."""
        if member.bot:
            return

        await self.client.wait_until_guild_configurations_loaded()
        config = await self.client.fetch_guild_configuration(member.guild.id)

        await self.handle_suspicious_member(config, member)

async def setup(client: Pidroid) -> None:
    await client.add_cog(AutomodTask(client))