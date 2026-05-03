from __future__ import annotations

from datetime import date
from typing import Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands

import config
from cogs.staff.honorGuardViews import HonorGuardPointAwardReviewView, HonorGuardSentryReviewView
from features.staff.honorGuard import buildScaffoldStatus
from features.staff.honorGuard import rendering as honorGuardRendering
from features.staff.honorGuard import service as honorGuardService
from runtime import cogGuards as runtimeCogGuards
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions

PLUGIN_MANIFEST = {
    "displayName": "Honor Guard",
    "category": "staff",
    "description": "Honor Guard ORBAT integration status and review-flow backend.",
}


def _displayChannel(channelId: int) -> str:
    return f"<#{channelId}>" if int(channelId or 0) > 0 else "`not set`"


def _displayText(value: str) -> str:
    text = str(value or "").strip()
    return f"`{text}`" if text else "`not set`"


def _isImageAttachment(attachment: discord.Attachment) -> bool:
    contentType = (attachment.content_type or "").lower()
    if contentType.startswith("image/"):
        return True
    filename = (attachment.filename or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))


def _evidenceLinks(attachments: Sequence[discord.Attachment]) -> list[str]:
    return [attachment.url for attachment in attachments if _isImageAttachment(attachment)]


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


def _normalizeRoleIdList(rawValues: object) -> set[int]:
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
        configuredGuildIds = _normalizeRoleIdList(getattr(config, "honorGuardCommandGuildIds", []))
        if configuredGuildIds:
            return configuredGuildIds
        fallbackGuildIds = [
            getattr(config, "serverId", 0),
            getattr(config, "serverIdTesting", 0),
            *(getattr(config, "testGuildIds", []) or []),
        ]
        return _normalizeRoleIdList(fallbackGuildIds)

    async def _ensureHonorGuardCommandGuild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return False
        allowedGuildIds = self._honorGuardCommandGuildIds()
        if not allowedGuildIds or int(interaction.guild.id) in allowedGuildIds:
            return True
        await self._safeReply(
            interaction,
            "Honor Guard commands can only be used in the CE server or configured test servers.",
        )
        return False

    @staticmethod
    def _memberDisplayName(member: discord.Member) -> str:
        return str(
            getattr(member, "display_name", None)
            or getattr(member, "global_name", None)
            or getattr(member, "name", None)
            or member.id
        ).strip()

    async def _resolveReviewChannel(
        self,
        guild: discord.Guild,
        fallback: Optional[discord.abc.Messageable],
        *,
        channelId: Optional[int] = None,
    ) -> Optional[discord.abc.Messageable]:
        targetChannelId = int(channelId or 0)
        if targetChannelId > 0:
            channel = self.bot.get_channel(targetChannelId)
            if channel is None:
                channel = guild.get_channel(targetChannelId)
            if channel is None:
                channel = await interactionRuntime.safeFetchChannel(self.bot, targetChannelId)
            if channel is not None:
                return channel
        return fallback

    @app_commands.command(
        name="honor-guard-status",
        description="Show the current Honor Guard ORBAT integration wiring.",
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
            f"Event hosts sheet: {_displayText(status.config.eventHostsSheetName)}",
        ]
        embed = discord.Embed(
            title="Honor Guard Integration",
            description="Backend tables, point rules, and sheet adapter are wired. Review commands are still separate.",
            color=discord.Color.gold(),
        )
        embed.add_field(name="Current Wiring", value="\n".join(summary), inline=False)
        embed.add_field(
            name="DB Tables",
            value="\n".join(f"`{name}`" for name in status.plannedDbTables),
            inline=False,
        )
        if status.sheetProblems:
            embed.add_field(
                name="Sheet Adapter Warnings",
                value="\n".join(f"- {problem}" for problem in status.sheetProblems),
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
        description="Award points to a member of the Honor Guard.",
    )
    @app_commands.describe(
        awarded_user="User you want to award",
        quota_points="Quota Points you want to award",
        awarded_points="Awarded promotion points you want to award",
        reason="The reason for the award",
    )
    @app_commands.rename(awarded_user="awarded-user")
    @app_commands.rename(quota_points="quota-points")
    @app_commands.rename(awarded_points="awarded-points")
    async def honorGuardAwardPoints(
        self,
        interaction: discord.Interaction,
        awarded_user: discord.Member,
        reason: str,
        awarded_points: float,
        quota_points: float = 0.0,
    ) -> None:
        if not await self._ensureHonorGuardCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return
        if not self._canAwardPoints(interaction.user):
            await self._safeReply(
                interaction,
                "You do not have permission to award Honor Guard points.",
            )
            return
        if float(awarded_points or 0) < 0 or float(quota_points or 0) < 0:
            await self._safeReply(
                interaction,
                "Honor Guard point awards cannot be negative.",
            )
            return
        if float(awarded_points or 0) <= 0 and float(quota_points or 0) <= 0:
            await self._safeReply(
                interaction,
                "Set at least one positive point value before submitting the award.",
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        submissionId = await honorGuardService.createPointAwardSubmission(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
            submitterId=int(interaction.user.id),
            awardedUserId=int(awarded_user.id),
            quotaPoints=float(quota_points or 0),
            awardedPoints=float(awarded_points or 0),
            reason=str(reason or "").strip(),
            awardedUserDisplayName=self._memberDisplayName(awarded_user),
        )
        submission = await honorGuardService.getPointAwardSubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create point award submission.",
                ephemeral=True,
            )
            return

        embed = honorGuardRendering.buildPointAwardEmbed(submission)
        view = HonorGuardPointAwardReviewView(self, submissionId)
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
        description="Submit a solo sentry log for Honor Guard review.",
    )
    @app_commands.describe(
        duty_date="Duty date in YYYY-MM-DD format.",
        roblox_username="Your Roblox username as it appears on the HG ORBAT.",
        image="Primary sentry screenshot.",
        extra_image="Second sentry screenshot.",
    )
    @app_commands.rename(duty_date="duty-date")
    @app_commands.rename(roblox_username="roblox-username")
    @app_commands.rename(extra_image="extra-image")
    async def honorGuardSoloSentry(
        self,
        interaction: discord.Interaction,
        duty_date: str,
        roblox_username: str,
        image: discord.Attachment,
        extra_image: discord.Attachment,
    ) -> None:
        if not await self._ensureHonorGuardCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeReply(
                interaction,
                "This command can only be used in a server channel.",
            )
            return

        try:
            normalizedDutyDate = date.fromisoformat(str(duty_date or "").strip()).isoformat()
        except ValueError:
            await self._safeReply(
                interaction,
                "Duty date must use the `YYYY-MM-DD` format.",
            )
            return

        attachments = [image, extra_image]
        imageUrls = _evidenceLinks(attachments)
        if len(imageUrls) < 2:
            await self._safeReply(
                interaction,
                "Two valid image attachments are required for solo sentry logs.",
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        imageFiles: list[discord.File] = []
        for attachment in attachments:
            if not _isImageAttachment(attachment):
                continue
            try:
                imageFiles.append(await attachment.to_file())
            except (discord.HTTPException, OSError):
                continue
        if len(imageFiles) < 2:
            await interaction.followup.send(
                "I could not copy both sentry screenshots for review. Please try again.",
                ephemeral=True,
            )
            return

        try:
            submissionId = await honorGuardService.createSentrySubmission(
                guildId=int(interaction.guild.id),
                channelId=int(interaction.channel.id),
                submitterId=int(interaction.user.id),
                targetUserId=int(interaction.user.id),
                targetRobloxUsername=str(roblox_username or "").strip(),
                targetDisplayName=self._memberDisplayName(interaction.user),
                dutyDate=normalizedDutyDate,
                imageUrls=imageUrls,
            )
        except ValueError as exc:
            await interaction.followup.send(
                str(exc),
                ephemeral=True,
            )
            return

        submission = await honorGuardService.getSentrySubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create solo sentry submission.",
                ephemeral=True,
            )
            return

        embed = honorGuardRendering.buildSentrySubmissionEmbed(submission)
        view = HonorGuardSentryReviewView(self, submissionId)
        reviewMessage = await self._postHonorGuardForReview(
            guild=interaction.guild,
            fallbackChannel=interaction.channel,
            embed=embed,
            view=view,
            files=imageFiles,
            reviewChannelId=int(getattr(config, "honorGuardReviewChannelId", 0) or 0),
        )
        if not reviewMessage:
            await interaction.followup.send(
                "Submission saved, but I could not post it for review.",
                ephemeral=True,
            )
            return

        await honorGuardService.setSubmissionMessageId(submissionId, reviewMessage.id)
        await interaction.followup.send(
            "Submitted solo sentry log.",
            ephemeral=True,
        )

    @app_commands.command(
        name="honorguard-event-log",
        description="Create a clock-in for an Honor Guard event.",
    )
    async def honorGuardEventLog(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        eventDescription: str,
    ) -> None:
        _ = member
        _ = eventDescription
        await self._safeReply(
            interaction,
            "Honor Guard event logging is not wired yet.",
        )

    @app_commands.command(
        name="honorguard-schedule-event",
        description="Schedule an event for Honor Guard.",
    )
    async def honorGuardScheduleEvent(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        eventDescription: str,
    ) -> None:
        _ = member
        _ = eventDescription
        await self._safeReply(
            interaction,
            "Honor Guard event scheduling is not wired yet.",
        )

    @app_commands.command(
        name="honorguard-quota-cycle",
        description="Cycle the quota for Honor Guard.",
    )
    async def honorGuardQuotaCycle(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        _ = member
        await self._safeReply(
            interaction,
            "Honor Guard quota cycling is not wired yet.",
        )

    async def _postHonorGuardForReview(
        self,
        *,
        guild: discord.Guild,
        fallbackChannel: Optional[discord.abc.Messageable],
        embed: discord.Embed,
        view: discord.ui.View,
        files: Optional[list[discord.File]] = None,
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
        content = mention or None
        allowedMentions = discord.AllowedMentions(roles=True, users=True)
        return await interactionRuntime.safeChannelSend(
            channel,
            content=content,
            embed=embed,
            view=view,
            files=files or [],
            allowed_mentions=allowedMentions,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HonorGuardCog(bot))
