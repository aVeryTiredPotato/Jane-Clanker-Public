from __future__ import annotations

from typing import Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands

from cogs.staff.honorGuardViews import HonorGuardPointAwardReviewView
import config
from features.staff.honorGuard import buildScaffoldStatus
from features.staff.honorGuard import rendering as honorGuardRendering
from features.staff.honorGuard import service as honorGuardService
from runtime import cogGuards as runtimeCogGuards
from runtime import normalization
from runtime import permissions as runtimePermissions

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

def _reviewerMention() -> str:
    roleId = int(
        getattr(
            config,
            "honorGuardReviewerPingRoleId",
            getattr(config, "honorGuardReviewerRoleId", 0),
        )
        or 0
    )
    if roleId > 0:
        return f"<@&{roleId}>"
    return ""

def _hasRole(member: discord.Member, roleId: Optional[int]) -> bool:
    return runtimePermissions.hasAnyRole(member, [roleId])

def _normalizeRoleIdList(rawValues) -> set[int]:
    return normalization.normalizeIntSet(rawValues)

class HonorGuardCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _canAwardPoints(self, member: discord.Member) -> bool:
        honorGuardReviewerRoleId = int(getattr(config, "honorGuardReviewerRoleId", 0) or 0)
        if honorGuardReviewerRoleId <= 0:
            return True
        return _hasRole(member, honorGuardReviewerRoleId)

    def _honorGuardCommandGuildIds(self) -> set[int]:
        return _normalizeRoleIdList(getattr(config, "honorGuardCommandGuildIds", []))

    async def _ensureHonorGuardCommandGuild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return False
        allowedGuildIds = self._honorGuardCommandGuildIds()
        if int(interaction.guild.id) in allowedGuildIds:
            return True
        await interaction.response.send_message(
            "Honor Guard commands can only be used in the CE server or configured test servers.",
            ephemeral=True,
        )
        return False

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

    @app_commands.command(
        name="honorguard-award-points",
        description="Award points to a member of the Honor Guard."
    )
    @app_commands.describe(
        awarded_user="User you want to award",
        quota_points="Quota Points you want to award",
        event_points="Event Points you want to award",
        reason="The Reason for the Award"
    )
    @app_commands.rename(awarded_user="awarded-user")
    @app_commands.rename(quota_points="quota-points")
    @app_commands.rename(event_points="event-points")
    async def honorGuardAwardPoints(self, interaction: discord.Interaction, awardedUser: discord.Member, reason: str, eventPoints: int, quotaPoints: int = 0,) -> None:
        if not await self._ensureHonorGuardCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return
        if not self._canAwardPoints(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to award Honor Guard Points.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        submissionId = await honorGuardService.createPointAwardSubmission(
            guildId=interaction.guild.id,
            channelId=interaction.channel.id,
            submitterId=interaction.user.id,
            awardedUserId=awardedUser.id,
            quotaPoints=quotaPoints,
            eventPoints=eventPoints,
            reason=reason,
            awardedUserDisplayName=self._memberDisplayName(awardedUser),
        )
        submission = await honorGuardService.getPointAwardSubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create point award submission.",
                ephemeral=True,
            )
            return

        embed = honorGuardRendering.buildPointAwardEmbed(submission)
        view = HonorGuardPointAwardReviewView(self, "honorGuard", submissionId)
        reviewMessage = await self._postHonorGuardForReview(
            guild=interaction.guild,
            fallbackChannel=interaction.channel,
            embed=embed,
            view=view,
            reviewChannelId=int(getattr(config, "honorGuardReviewChannelId", 0) or 0),
        )
        if not reviewMessage:
            await interaction.followup.send(
                "Submission saved, but I could not post it for review.",
                ephemeral=True,
            )
            return

        await honorGuardService.setPointAwardMessageId(submissionId, reviewMessage.id)
        await interaction.followup.send(
            "Submitted point award log.",
            ephemeral=True,
        )
        

    @app_commands.command(
        name="honorguard-solo-sentry",
        description="Manually log a sentry event for an Honor Guard member."
    )
    async def honorGuardSoloSentry(self, interaction: discord.Interaction, member: discord.Member, eventDescription: str) -> None:
        # Implementation for logging a sentry event
        pass

    @app_commands.command(
        name="honorguard-event-log",
        description="Create an Clockin for an Honor Guard Event."
    )
    async def honorGuardEventLog(self, interaction: discord.Interaction, member: discord.Member, eventDescription: str) -> None:
        # Implementation for logging an event
        pass

    @app_commands.command(
        name="honorguard-schedule-event",
        description="Schedule an event for Honor Guard ."
    )
    async def honorGuardScheduleEvent(self, interaction: discord.Interaction, member: discord.Member, eventDescription: str) -> None:
        # Implementation for scheduling an event
        pass

    @app_commands.command(
        name="honorguard-quota-cycle",
        description="Cycle the quota for a Honor Guard."
    )
    async def honorGuardQuotaCycle(self, interaction: discord.Interaction, member: discord.Member) -> None:
        # Implementation for cycling the quota
        pass
    

    async def _postHonorGuardForReview(
        self,
        *,
        guild: discord.Guild,
        fallbackChannel: Optional[discord.abc.Messageable],
        embed: discord.Embed,
        view: discord.ui.View,
        reviewChannelId: Optional[int] = None,
    ) -> Optional[discord.Message]:
        channel = await self._resolveReviewChannel(
            guild,
            fallbackChannel,
            channelId=reviewChannelId,
        )
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        mention = _reviewerMention()
        contentParts: list[str] = []
        if mention:
            contentParts.append(mention)
        content = "\n".join(part for part in contentParts if part)
        if not content:
            content = None
        allowedMentions = discord.AllowedMentions(roles=True, users=True)
        try:
            return await channel.send(
                content=content,
                embed=embed,
                view=view,
                allowed_mentions=allowedMentions,
            )
        except (discord.Forbidden, discord.HTTPException):
            return None



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HonorGuardCog(bot))
