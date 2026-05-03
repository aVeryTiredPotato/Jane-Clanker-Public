
import asyncio
import logging
from typing import Optional, Sequence

import discord
from discord import app_commands
from discord.ext import commands

from cogs.staff.recruitmentViews import (
    GroupPatrolFinishModal,
    GroupPatrolManageModal,
    GroupPatrolView,
    RecruitmentReviewView,
)
from features.staff.clockins import ClockinEngine, resolveAttendeeUserIdFromToken
from features.staff.clockins.recruitmentPatrolAdapter import RecruitmentPatrolAdapter
import config
from features.staff.recruitment import rendering as recruitmentRendering
from features.staff.recruitment import service as recruitmentService
from runtime import commandScopes as runtimeCommandScopes
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions
from runtime import taskBudgeter


log = logging.getLogger(__name__)


def _isImageAttachment(attachment: discord.Attachment) -> bool:
    contentType = (attachment.content_type or "").lower()
    if contentType.startswith("image/"):
        return True
    filename = (attachment.filename or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"))


def _patrolPoints(durationMinutes: int) -> int:
    pointsPer15 = int(getattr(config, "recruitmentPointsPer15Minutes", 1) or 1)
    if durationMinutes <= 0 or pointsPer15 <= 0:
        return 0
    return max(0, durationMinutes // 15) * pointsPer15


def _groupPatrolPoints() -> int:
    return max(0, int(getattr(config, "recruitmentGroupPatrolPoints", 6) or 0))


def _maxPatrolDurationMinutes() -> int:
    return max(0, int(getattr(config, "recruitmentPatrolMaxDurationMinutes", 240) or 0))


def _hasRole(member: discord.Member, roleId: Optional[int]) -> bool:
    return runtimePermissions.hasAnyRole(member, [roleId])


def _normalizeRoleIdList(rawValues) -> set[int]:
    return normalization.normalizeIntSet(rawValues)


def _positiveInt(value: object) -> int:
    return normalization.toPositiveInt(value)


def _parseUserIdInput(value: object) -> int:
    return normalization.parseDiscordUserId(value)


def _reviewerMention() -> str:
    roleId = int(
        getattr(
            config,
            "recruitmentReviewerPingRoleId",
            getattr(config, "recruitmentReviewerRoleId", 0),
        )
        or 0
    )
    if roleId > 0:
        return f"<@&{roleId}>"
    return ""


def _evidenceLinks(attachments: Sequence[discord.Attachment]) -> list[str]:
    return [attachment.url for attachment in attachments if _isImageAttachment(attachment)]


class RecruitmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._patrolLocks: dict[int, asyncio.Lock] = {}
        self._groupPatrolAdapter = RecruitmentPatrolAdapter()
        self._groupPatrolEngine = ClockinEngine(bot, self._groupPatrolAdapter)

    async def cog_load(self) -> None:
        await self._restoreReviewViews()
        await self._restoreOpenPatrolViews()

    async def _restoreReviewViews(self) -> None:
        recruitRows = await recruitmentService.listRecruitmentPendingStatuses()
        for row in recruitRows:
            messageId = int(row.get("messageId") or 0)
            if messageId <= 0:
                continue
            self.bot.add_view(
                RecruitmentReviewView(self, "recruitment", int(row["submissionId"])),
                message_id=messageId,
            )

        timeRows = await recruitmentService.listRecruitmentTimePendingStatuses()
        for row in timeRows:
            messageId = int(row.get("messageId") or 0)
            if messageId <= 0:
                continue
            self.bot.add_view(
                RecruitmentReviewView(self, "time", int(row["submissionId"])),
                message_id=messageId,
            )

    async def _restoreOpenPatrolViews(self) -> None:
        await self._groupPatrolEngine.restoreOpenViews(
            lambda patrolId: GroupPatrolView(self, patrolId),
        )

    def _canSubmitRecruitment(self, member: discord.Member) -> bool:
        recruiterRoleId = int(getattr(config, "recruiterRoleId", 0) or 0)
        if recruiterRoleId <= 0:
            return True
        return _hasRole(member, recruiterRoleId)

    def _canHostGroupPatrol(self, member: discord.Member) -> bool:
        configuredRoleIds = _normalizeRoleIdList(
            getattr(config, "recruitmentPatrolGroupHostRoleIds", []),
        )
        if configuredRoleIds:
            return any(role.id in configuredRoleIds for role in member.roles)
        return self._canSubmitRecruitment(member)

    def _validatePatrolDuration(self, durationMinutes: int) -> Optional[str]:
        if int(durationMinutes or 0) <= 0:
            return "Duration must be greater than 0 minutes."
        maxDuration = _maxPatrolDurationMinutes()
        if maxDuration > 0 and int(durationMinutes) > maxDuration:
            return f"Duration cannot exceed {maxDuration} minutes."
        return None

    def _recruitmentCommandGuildIds(self) -> set[int]:
        return _normalizeRoleIdList(getattr(config, "recruitmentCommandGuildIds", []))

    async def _ensureRecruitmentCommandGuild(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None:
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return False
        allowedGuildIds = self._recruitmentCommandGuildIds()
        if int(interaction.guild.id) in allowedGuildIds:
            return True
        await interaction.response.send_message(
            "Recruitment commands can only be used in the CE server or configured test servers.",
            ephemeral=True,
        )
        return False

    async def _fetchMainAnroMember(self, userId: int) -> Optional[discord.Member]:
        sourceGuildId = _positiveInt(getattr(config, "recruitmentSourceGuildId", getattr(config, "serverId", 0)))
        if sourceGuildId <= 0 or int(userId or 0) <= 0:
            return None
        guild = self.bot.get_guild(sourceGuildId)
        if guild is None:
            try:
                guild = await taskBudgeter.runDiscord(lambda: self.bot.fetch_guild(sourceGuildId))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        member = guild.get_member(int(userId))
        if member is not None:
            return member
        try:
            return await taskBudgeter.runDiscord(lambda: guild.fetch_member(int(userId)))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return None

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
        targetChannelId = int(channelId or getattr(config, "recruitmentChannelId", 0) or 0)
        if targetChannelId > 0:
            # Use client-level channel resolution so review channels can live
            # outside the invoking guild (cross-server review setup).
            channel = self.bot.get_channel(targetChannelId)
            if channel is None:
                channel = guild.get_channel(targetChannelId)
            if channel is None:
                channel = await interactionRuntime.safeFetchChannel(self.bot, targetChannelId)
            if channel is not None:
                return channel
        return fallback

    async def _postRecruitmentForReview(
        self,
        *,
        guild: discord.Guild,
        fallbackChannel: Optional[discord.abc.Messageable],
        embed: discord.Embed,
        view: discord.ui.View,
        extraContent: Optional[str] = None,
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
        contentParts: list[str] = []
        if mention:
            contentParts.append(mention)
        if extraContent:
            contentParts.append(str(extraContent).strip())
        content = "\n".join(part for part in contentParts if part)
        if not content:
            content = None
        allowedMentions = discord.AllowedMentions(roles=True, users=True)
        return await interactionRuntime.safeChannelSend(
            channel,
            content=content,
            embed=embed,
            view=view,
            files=files or [],
            allowed_mentions=allowedMentions,
        )

    async def _resolveConfiguredMessageChannel(
        self,
        guild: discord.Guild,
        channelId: int,
    ) -> Optional[discord.abc.Messageable]:
        if int(channelId or 0) <= 0:
            return None
        channel = self.bot.get_channel(int(channelId))
        if channel is None:
            channel = guild.get_channel(int(channelId))
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, int(channelId))
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def _collectTwoImageEvidenceMessage(
        self,
        *,
        channel: discord.abc.Messageable,
        userId: int,
        timeoutSec: float = 180.0,
    ) -> Optional[discord.Message]:
        channelId = getattr(channel, "id", None)
        if channelId is None:
            return None

        def check(message: discord.Message) -> bool:
            # We only accept the submitter's next message in this channel with
            # at least two image attachments.
            if message.author.id != userId:
                return False
            if message.channel.id != channelId:
                return False
            images = [att for att in message.attachments if _isImageAttachment(att)]
            return len(images) >= 2

        try:
            message = await self.bot.wait_for("message", check=check, timeout=timeoutSec)
        except asyncio.TimeoutError:
            return None
        return message

    async def _updatePatrolMessage(
        self,
        patrolId: int,
        *,
        message: Optional[discord.Message] = None,
    ) -> None:
        await self._groupPatrolEngine.updateClockinMessage(
            int(patrolId),
            viewFactory=lambda sessionId: GroupPatrolView(self, sessionId),
            message=message,
        )

    async def _deletePatrolClockinMessage(
        self,
        patrol: dict,
        *,
        message: Optional[discord.Message] = None,
    ) -> None:
        await self._groupPatrolEngine.deleteClockinMessage(
            patrol,
            message=message,
        )

    async def _refreshPatrolMessageFromInteraction(
        self,
        patrolId: int,
        interaction: discord.Interaction,
    ) -> None:
        if isinstance(interaction.message, discord.Message):
            await self._updatePatrolMessage(patrolId, message=interaction.message)
            return
        await self._updatePatrolMessage(patrolId)

    async def _isHost(self, interaction: discord.Interaction, patrol: dict) -> bool:
        return interaction.user.id == int(patrol.get("hostId") or 0)

    async def openGroupPatrolManage(self, interaction: discord.Interaction, patrolId: int) -> None:
        patrol = await self._groupPatrolEngine.getSession(int(patrolId))
        if not patrol:
            await interaction.response.send_message("This patrol session no longer exists.", ephemeral=True)
            return
        if not await self._isHost(interaction, patrol):
            await interaction.response.send_message(
                "Only the patrol host can manage attendees.",
                ephemeral=True,
            )
            return
        if str(patrol.get("status") or "").upper() != "OPEN":
            await interaction.response.send_message("This patrol is no longer open.", ephemeral=True)
            return
        attendees = await self._groupPatrolEngine.listAttendees(int(patrolId))
        if not attendees:
            await interaction.response.send_message("No attendees to remove.", ephemeral=True)
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            GroupPatrolManageModal(self, patrolId),
        )

    async def handleGroupPatrolManage(
        self,
        interaction: discord.Interaction,
        patrolId: int,
        token: str,
    ) -> None:
        patrol = await self._groupPatrolEngine.getSession(int(patrolId))
        if not patrol:
            await interaction.response.send_message("This patrol session no longer exists.", ephemeral=True)
            return
        if not await self._isHost(interaction, patrol):
            await interaction.response.send_message(
                "Only the patrol host can manage attendees.",
                ephemeral=True,
            )
            return
        attendees = await self._groupPatrolEngine.listAttendees(int(patrolId))
        if not attendees:
            await interaction.response.send_message("No attendees to remove.", ephemeral=True)
            return

        targetUserId = resolveAttendeeUserIdFromToken(token, attendees)
        if not targetUserId:
            await interaction.response.send_message(
                "Could not match that attendee in this patrol.",
                ephemeral=True,
            )
            return

        await self._groupPatrolEngine.removeAttendee(int(patrolId), int(targetUserId))
        await interaction.response.send_message(
            f"Removed <@{targetUserId}> from this patrol.",
            ephemeral=True,
        )
        await self._refreshPatrolMessageFromInteraction(patrolId, interaction)

    async def handleGroupPatrolJoin(self, interaction: discord.Interaction, patrolId: int) -> None:
        if interaction.user.bot:
            await interaction.response.send_message("Bots cannot join patrols.", ephemeral=True)
            return
        patrol = await self._groupPatrolEngine.getSession(int(patrolId))
        if not patrol:
            await interaction.response.send_message("This patrol session no longer exists.", ephemeral=True)
            return
        if str(patrol.get("status") or "").upper() != "OPEN":
            await interaction.response.send_message("This patrol is no longer open.", ephemeral=True)
            return
        if interaction.user.id == int(patrol.get("hostId") or 0):
            await interaction.response.send_message(
                "You are the host of this patrol and cannot clock in as an attendee.",
                ephemeral=True,
            )
            return
        await self._groupPatrolEngine.addAttendee(int(patrolId), int(interaction.user.id))
        await interaction.response.send_message("You have been added to this patrol.", ephemeral=True)
        await self._refreshPatrolMessageFromInteraction(patrolId, interaction)

    async def handleGroupPatrolDelete(self, interaction: discord.Interaction, patrolId: int) -> None:
        patrol = await self._groupPatrolEngine.getSession(int(patrolId))
        if not patrol:
            await interaction.response.send_message("This patrol session no longer exists.", ephemeral=True)
            return
        if not await self._isHost(interaction, patrol):
            await interaction.response.send_message("Only the patrol host can delete this patrol.", ephemeral=True)
            return
        await self._groupPatrolEngine.updateSessionStatus(int(patrolId), "CANCELED")
        await interaction.response.send_message("Patrol deleted.", ephemeral=True)
        await self._refreshPatrolMessageFromInteraction(patrolId, interaction)

    async def openGroupPatrolFinish(self, interaction: discord.Interaction, patrolId: int) -> None:
        patrol = await self._groupPatrolEngine.getSession(int(patrolId))
        if not patrol:
            await interaction.response.send_message("This patrol session no longer exists.", ephemeral=True)
            return
        if not await self._isHost(interaction, patrol):
            await interaction.response.send_message("Only the patrol host can finish this patrol.", ephemeral=True)
            return
        if str(patrol.get("status") or "").upper() != "OPEN":
            await interaction.response.send_message("This patrol is no longer open.", ephemeral=True)
            return
        attendees = await self._groupPatrolEngine.listAttendees(int(patrolId))
        if not attendees:
            await interaction.response.send_message(
                "This patrol has no attendees yet.",
                ephemeral=True,
            )
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            GroupPatrolFinishModal(self, patrolId),
        )

    async def handleGroupPatrolFinish(
        self,
        interaction: discord.Interaction,
        patrolId: int,
        durationMinutes: int,
    ) -> None:
        durationError = self._validatePatrolDuration(durationMinutes)
        if durationError:
            await interaction.response.send_message(durationError, ephemeral=True)
            return

        lock = self._patrolLocks.setdefault(patrolId, asyncio.Lock())
        async with lock:
            # Guard against double-finalize clicks while one reviewer is still
            # uploading evidence / posting the review message.
            patrol = await self._groupPatrolEngine.getSession(int(patrolId))
            if not patrol:
                await interaction.response.send_message("This patrol session no longer exists.", ephemeral=True)
                return
            if not await self._isHost(interaction, patrol):
                await interaction.response.send_message(
                    "Only the patrol host can finish this patrol.",
                    ephemeral=True,
                )
                return
            if str(patrol.get("status") or "").upper() != "OPEN":
                await interaction.response.send_message("This patrol is no longer open.", ephemeral=True)
                return

            attendees = await self._groupPatrolEngine.listAttendees(int(patrolId))
            participantIds = [int(row["userId"]) for row in attendees]
            if not participantIds:
                await interaction.response.send_message(
                    "Cannot finish this patrol with no attendees.",
                    ephemeral=True,
                )
                return

            channel = interaction.channel
            if channel is None:
                await interaction.response.send_message(
                    "Could not resolve the channel for screenshot upload.",
                    ephemeral=True,
                )
                return
            evidenceChannelId = int(
                getattr(
                    config,
                    "recruitmentPatrolEvidenceChannelId",
                    getattr(config, "recruitmentChannelId", 0),
                )
                or 0
            )
            evidenceChannel = await self._resolveConfiguredMessageChannel(
                interaction.guild,
                evidenceChannelId,
            )
            if evidenceChannel is None:
                await interaction.response.send_message(
                    "Could not resolve the configured patrol screenshot channel.",
                    ephemeral=True,
                )
                return

            await interaction.response.send_message(
                f"Upload two patrol screenshots in <#{evidenceChannelId}> within 3 minutes.",
                ephemeral=True,
            )
            # We reuse the evidence collector so solo/group flows behave the same.
            evidenceMessage = await self._collectTwoImageEvidenceMessage(
                channel=evidenceChannel,
                userId=interaction.user.id,
            )
            if evidenceMessage is None:
                await interaction.followup.send(
                    "Timed out waiting for two image screenshots. Patrol is still open.",
                    ephemeral=True,
                )
                return

            imageUrls = _evidenceLinks(evidenceMessage.attachments)
            points = _groupPatrolPoints()
            submissionId = await recruitmentService.createRecruitmentTimeSubmission(
                guildId=int(patrol["guildId"]),
                channelId=int(patrol["channelId"]),
                submitterId=int(patrol["hostId"]),
                durationMinutes=int(durationMinutes),
                imageUrls=imageUrls,
                points=points,
                patrolType="group",
                participantUserIds=participantIds,
                evidenceMessageUrl=evidenceMessage.jump_url,
            )
            submission = await recruitmentService.getRecruitmentTimeSubmission(submissionId)
            if not submission:
                await interaction.followup.send(
                    "Failed to create patrol submission.",
                    ephemeral=True,
                )
                return

            embed = recruitmentRendering.buildRecruitmentTimeEmbed(submission)
            embed.add_field(
                name="Evidence Message",
                value=f"[Open message]({evidenceMessage.jump_url})",
                inline=False,
            )
            reviewView = RecruitmentReviewView(self, "time", submissionId)
            reviewMessage = await self._postRecruitmentForReview(
                guild=interaction.guild,
                fallbackChannel=interaction.channel,
                embed=embed,
                view=reviewView,
                reviewChannelId=int(getattr(config, "recruitmentPatrolReviewChannelId", 0) or 0),
            )
            if not reviewMessage:
                await interaction.followup.send(
                    "Could not post this submission for review. Patrol remains open.",
                    ephemeral=True,
                )
                return

            await recruitmentService.setRecruitmentTimeMessageId(
                submissionId,
                reviewMessage.id,
                getattr(reviewMessage.channel, "id", None),
            )
            await self._groupPatrolEngine.updateSessionStatus(int(patrolId), "FINISHED")
            if isinstance(interaction.message, discord.Message):
                await self._deletePatrolClockinMessage(patrol, message=interaction.message)
            else:
                await self._deletePatrolClockinMessage(patrol)

            await interaction.followup.send(
                "Patrol finished and submitted for review.",
                ephemeral=True,
            )

    @app_commands.command(name="recruitment", description="Submit a recruitment log.")
    @app_commands.describe(
        user_id="Discord user ID of the user you recruited.",
        image="Primary screenshot proof.",
        extra_image="Second screenshot proof.",
    )
    @app_commands.rename(user_id="user-id")
    @app_commands.rename(extra_image="extra-image")
    async def recruitment(
        self,
        interaction: discord.Interaction,
        user_id: str,
        image: discord.Attachment,
        extra_image: discord.Attachment,
    ) -> None:
        if not await self._ensureRecruitmentCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return
        if not self._canSubmitRecruitment(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to submit recruitment logs.",
                ephemeral=True,
            )
            return

        recruitUserId = _parseUserIdInput(user_id)
        if recruitUserId <= 0:
            await interaction.response.send_message(
                "Please provide a valid Discord user ID for the recruited user.",
                ephemeral=True,
            )
            return

        attachments = [image, extra_image]
        imageUrls = _evidenceLinks(attachments)
        # Two screenshots are required to reduce ambiguity during review.
        if len(imageUrls) < 2:
            await interaction.response.send_message(
                "Two valid image attachments are required.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        recruitMember = await self._fetchMainAnroMember(recruitUserId)
        if recruitMember is None:
            await interaction.followup.send(
                "That user ID is incorrect, or that user is not in the ANRO server.",
                ephemeral=True,
            )
            return

        basePoints = int(getattr(config, "recruitmentPointsBase", 2) or 2)
        submissionId = await recruitmentService.createRecruitmentSubmission(
            guildId=interaction.guild.id,
            channelId=interaction.channel.id,
            submitterId=interaction.user.id,
            recruitUserId=recruitUserId,
            passedOrientation=False,
            imageUrls=imageUrls,
            points=basePoints,
            recruitDisplayName=self._memberDisplayName(recruitMember),
        )
        submission = await recruitmentService.getRecruitmentSubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create recruitment submission.",
                ephemeral=True,
            )
            return

        embed = recruitmentRendering.buildRecruitmentEmbed(submission)
        imageFiles: list[discord.File] = []
        for attachment in attachments:
            if not _isImageAttachment(attachment):
                continue
            try:
                imageFiles.append(await attachment.to_file())
            except (discord.HTTPException, OSError):
                continue

        view = RecruitmentReviewView(self, "recruitment", submissionId)
        reviewMessage = await self._postRecruitmentForReview(
            guild=interaction.guild,
            fallbackChannel=interaction.channel,
            embed=embed,
            view=view,
            extraContent=None if imageFiles else "\n".join(imageUrls),
            files=imageFiles,
            reviewChannelId=int(getattr(config, "recruitmentChannelId", 0) or 0),
        )
        if not reviewMessage:
            await interaction.followup.send(
                "Submission saved, but I could not post it for review.",
                ephemeral=True,
            )
            return

        await recruitmentService.setRecruitmentMessageId(
            submissionId,
            reviewMessage.id,
            getattr(reviewMessage.channel, "id", None),
        )
        await interaction.followup.send(
            "Submitted recruitment log.",
            ephemeral=True,
        )

    async def _submitSoloPatrolForReview(
        self,
        interaction: discord.Interaction,
        *,
        durationMinutes: int,
        imageUrls: list[str],
        imageFiles: Optional[list[discord.File]] = None,
        evidenceMessageUrl: Optional[str] = None,
        reviewChannelId: Optional[int] = None,
    ) -> None:
        points = _patrolPoints(int(durationMinutes))
        submissionId = await recruitmentService.createRecruitmentTimeSubmission(
            guildId=interaction.guild.id,
            channelId=interaction.channel.id,
            submitterId=interaction.user.id,
            durationMinutes=int(durationMinutes),
            imageUrls=imageUrls,
            points=points,
            patrolType="solo",
            evidenceMessageUrl=evidenceMessageUrl,
        )
        submission = await recruitmentService.getRecruitmentTimeSubmission(submissionId)
        if not submission:
            await interaction.followup.send(
                "Failed to create patrol submission.",
                ephemeral=True,
            )
            return

        embed = recruitmentRendering.buildRecruitmentTimeEmbed(submission)
        if evidenceMessageUrl:
            # Prefer the source message link so reviewers can inspect original
            # attachments/context directly.
            embed.add_field(
                name="Evidence Message",
                value=f"[Open message]({evidenceMessageUrl})",
                inline=False,
            )
        elif imageFiles:
            embed.add_field(
                name="Evidence",
                value="Attached screenshots.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Evidence",
                value="\n".join(f"[Image {index + 1}]({url})" for index, url in enumerate(imageUrls)),
                inline=False,
            )
        view = RecruitmentReviewView(self, "time", submissionId)
        reviewMessage = await self._postRecruitmentForReview(
            guild=interaction.guild,
            fallbackChannel=interaction.channel,
            embed=embed,
            view=view,
            files=imageFiles or [],
            reviewChannelId=reviewChannelId or int(getattr(config, "recruitmentTimeLogReviewChannelId", 0) or 0),
        )
        if not reviewMessage:
            await interaction.followup.send(
                "Submission saved, but I could not post it for review.",
                ephemeral=True,
            )
            return
        await recruitmentService.setRecruitmentTimeMessageId(
            submissionId,
            reviewMessage.id,
            getattr(reviewMessage.channel, "id", None),
        )
        await interaction.followup.send(
            "Submitted solo patrol log.",
            ephemeral=True,
        )

    async def _startGroupPatrolClockin(self, interaction: discord.Interaction) -> None:
        patrolId = await self._groupPatrolEngine.createSession(
            guildId=interaction.guild.id,
            channelId=interaction.channel.id,
            hostId=interaction.user.id,
            maxAttendeeLimit=int(getattr(config, "recruitmentPatrolMaxAttendeeLimit", 30) or 30),
        )
        patrol = await self._groupPatrolEngine.getSession(int(patrolId))
        if not patrol:
            await interaction.followup.send(
                "Could not create patrol clock-in.",
                ephemeral=True,
            )
            return
        embed = self._groupPatrolAdapter.buildEmbed(patrol, [])
        view = GroupPatrolView(self, patrolId)
        message = await interactionRuntime.safeChannelSend(interaction.channel, embed=embed, view=view)
        if message is None:
            await interaction.followup.send(
                "Could not create the patrol clock-in message in this channel.",
                ephemeral=True,
            )
            return
        await self._groupPatrolEngine.setSessionMessageId(int(patrolId), int(message.id))
        await interaction.followup.send(
            "Group patrol clock-in created.",
            ephemeral=True,
        )

    async def handleSoloPatrolDetails(
        self,
        interaction: discord.Interaction,
        durationMinutes: int,
    ) -> None:
        if not await self._ensureRecruitmentCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return
        if not self._canSubmitRecruitment(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to submit patrol logs.",
                ephemeral=True,
            )
            return

        durationError = self._validatePatrolDuration(durationMinutes)
        if durationError:
            await interaction.response.send_message(durationError, ephemeral=True)
            return

        await interaction.response.send_message(
            "Upload two patrol screenshots in your next message in this channel within 3 minutes.",
            ephemeral=True,
        )
        evidenceMessage = await self._collectTwoImageEvidenceMessage(
            channel=interaction.channel,
            userId=interaction.user.id,
        )
        if evidenceMessage is None:
            await interaction.followup.send(
                "Timed out waiting for two image screenshots. Submit the command again when ready.",
                ephemeral=True,
            )
            return

        imageUrls = _evidenceLinks(evidenceMessage.attachments)
        if len(imageUrls) < 2:
            await interaction.followup.send(
                "Two image attachments are required for patrol logs.",
                ephemeral=True,
            )
            return
        await self._submitSoloPatrolForReview(
            interaction,
            durationMinutes=durationMinutes,
            imageUrls=imageUrls,
            evidenceMessageUrl=evidenceMessage.jump_url,
        )

    @app_commands.command(name="recruitment-time-log", description="Submit a solo recruitment time log.")
    @app_commands.describe(
        duration_minutes="Patrol duration in minutes.",
        image="Primary patrol screenshot.",
        extra_image="Second patrol screenshot.",
    )
    @app_commands.rename(duration_minutes="duration-minutes")
    @app_commands.rename(extra_image="extra-image")
    async def recruitmentTimeLog(
        self,
        interaction: discord.Interaction,
        duration_minutes: int,
        image: discord.Attachment,
        extra_image: discord.Attachment,
    ) -> None:
        if not await self._ensureRecruitmentCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return
        if not self._canSubmitRecruitment(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to submit patrol logs.",
                ephemeral=True,
            )
            return

        durationError = self._validatePatrolDuration(int(duration_minutes or 0))
        if durationError:
            await interaction.response.send_message(
                durationError,
                ephemeral=True,
            )
            return

        attachments = [image, extra_image]
        imageUrls = _evidenceLinks(attachments)
        if len(imageUrls) < 2:
            await interaction.response.send_message(
                "Two valid image attachments are required.",
                ephemeral=True,
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
                "I could not copy both screenshot attachments for review. Please try again.",
                ephemeral=True,
            )
            return

        await self._submitSoloPatrolForReview(
            interaction,
            durationMinutes=int(duration_minutes),
            imageUrls=imageUrls,
            imageFiles=imageFiles,
            reviewChannelId=int(getattr(config, "recruitmentTimeLogReviewChannelId", 0) or 0),
        )

    @app_commands.command(name="recruitment-patrol", description="Create a group recruitment patrol clock-in.")
    async def recruitmentPatrol(
        self,
        interaction: discord.Interaction,
    ) -> None:
        if not await self._ensureRecruitmentCommandGuild(interaction):
            return
        if not interaction.channel or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "This command can only be used in a server channel.",
                ephemeral=True,
            )
            return
        if not self._canHostGroupPatrol(interaction.user):
            await interaction.response.send_message(
                "You do not have permission to host group patrol clock-ins.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._startGroupPatrolClockin(interaction)


async def setup(bot: commands.Bot):
    await bot.add_cog(
        RecruitmentCog(bot),
        guilds=runtimeCommandScopes.getGuildAndTestGuildObjects(1475705573575098461),
    )

