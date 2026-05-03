from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from features.staff.bgItemReview import spreadsheetSync as itemReviewSpreadsheetSync
from features.staff.bgItemReview import workflow as itemReviewWorkflow
from runtime import cogGuards as runtimeCogGuards

PLUGIN_MANIFEST = {
    "displayName": "BG Item Review",
    "category": "staff",
    "description": "Persistent review queue for suspicious BGC inventory items.",
}


class BgItemReviewCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await itemReviewWorkflow.restorePersistentViews(self.bot)

    async def _requireReviewer(self, interaction: discord.Interaction) -> discord.Member | None:
        member = await self._requireGuildMember(interaction)
        if member is None:
            return None
        if itemReviewWorkflow.canReviewMember(member):
            return member
        await self._safeReply(interaction, "BG reviewer required.")
        return None

    @app_commands.command(
        name="bg-item-review",
        description="Post a fresh BG item review queue summary in the configured queue channel.",
    )
    async def bgItemReview(self, interaction: discord.Interaction) -> None:
        member = await self._requireReviewer(interaction)
        if member is None:
            return
        await interaction.response.defer(ephemeral=True)
        sentMessage = await itemReviewWorkflow.postQueueSummaryMessage(
            self.bot,
            guildId=int(interaction.guild_id or 0),
        )
        if sentMessage is None:
            await interaction.followup.send(
                "Jane could not post the queue summary. Check the configured queue channel and webhook permissions.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Posted BG item review summary in <#{int(getattr(sentMessage.channel, 'id', 0) or 0)}>.",
            ephemeral=True,
        )

    @app_commands.command(
        name="bg-item-review-status",
        description="Show BG item review queue counts and recent items.",
    )
    async def bgItemReviewStatus(self, interaction: discord.Interaction) -> None:
        member = await self._requireReviewer(interaction)
        if member is None:
            return
        embed = await itemReviewWorkflow.buildQueueSummaryEmbed(
            guildId=int(interaction.guild_id or 0),
        )
        await self._safeReply(interaction, embed=embed)

    @app_commands.command(
        name="bg-item-review-sync",
        description="Sync denied rows from recent BGC spreadsheets into the item review queue.",
    )
    async def bgItemReviewSync(self, interaction: discord.Interaction) -> None:
        member = await self._requireReviewer(interaction)
        if member is None:
            return
        await interaction.response.defer(ephemeral=True)
        lookbackDays = itemReviewSpreadsheetSync._startupLookbackDays(int(interaction.guild_id or 0))
        result = await itemReviewSpreadsheetSync.syncDeniedSpreadsheetRows(
            self.bot,
            guildId=int(interaction.guild_id or 0),
            lookbackDays=lookbackDays,
        )
        if not bool(result.get("enabled", True)):
            await interaction.followup.send("Spreadsheet sync is disabled.", ephemeral=True)
            return
        if str(result.get("reason") or "").strip():
            await interaction.followup.send(
                (
                    f"Spreadsheet sync finished with a configuration issue: {str(result.get('reason') or '').strip()}\n"
                    f"lookbackDays=`{int(result.get('lookbackDays') or lookbackDays)}` "
                    f"files=`{int(result.get('files') or 0)}` rows=`{int(result.get('rows') or 0)}` errors=`{int(result.get('errors') or 0)}`"
                ),
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            (
                "Spreadsheet sync complete.\n"
                f"lookbackDays=`{int(result.get('lookbackDays') or lookbackDays)}` files=`{int(result.get('files') or 0)}` "
                f"rows=`{int(result.get('rows') or 0)}` denied=`{int(result.get('denied') or 0)}`\n"
                f"created=`{int(result.get('created') or 0)}` existing=`{int(result.get('existing') or 0)}` "
                f"known=`{int(result.get('known') or 0)}` errors=`{int(result.get('errors') or 0)}`"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BgItemReviewCog(bot))
