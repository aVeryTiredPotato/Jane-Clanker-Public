from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.staff.honorGuard import buildScaffoldStatus
from runtime import cogGuards as runtimeCogGuards

PLUGIN_MANIFEST = {
    "displayName": "Honor Guard Scaffold",
    "category": "staff",
    "description": "Initial Honor Guard scaffolding for the shared branch.",
}


def _displayChannel(channelId: int) -> str:
    return f"<#{channelId}>" if int(channelId or 0) > 0 else "`not set`"


def _displayText(value: str) -> str:
    text = str(value or "").strip()
    return f"`{text}`" if text else "`not set`"


class HonorGuardCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="honor-guard-status",
        description="Show the current Honor Guard scaffold wiring.",
    )
    async def honorGuardStatus(self, interaction: discord.Interaction) -> None:
        member = await self._requireAdminOrManageGuild(interaction)
        if member is None:
            return
        status = buildScaffoldStatus(configModule=config)
        summary = [
            f"Enabled: `{status.config.enabled}`",
            f"Review channel: {_displayChannel(status.config.reviewChannelId)}",
            f"Log channel: {_displayChannel(status.config.logChannelId)}",
            f"Archive channel: {_displayChannel(status.config.archiveChannelId)}",
            f"Spreadsheet: {_displayText(status.config.spreadsheetId)}",
            f"Member sheet: {_displayText(status.config.memberSheetName)}",
            f"Schedule sheet: {_displayText(status.config.scheduleSheetName)}",
            f"Archive sheet: {_displayText(status.config.archiveSheetName)}",
        ]
        embed = discord.Embed(
            title="Honor Guard Scaffold",
            description="The branch scaffolding is in place. No logging workflow is live yet.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Current Wiring", value="\n".join(summary), inline=False)
        embed.add_field(
            name="Planned DB Tables",
            value="\n".join(f"`{name}`" for name in status.plannedDbTables),
            inline=False,
        )
        embed.add_field(
            name="Next Milestones",
            value="\n".join(status.nextMilestones),
            inline=False,
        )
        await self._safeReply(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HonorGuardCog(bot))
