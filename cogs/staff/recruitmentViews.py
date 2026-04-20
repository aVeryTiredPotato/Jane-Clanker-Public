from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Sequence

import discord

import config
from features.staff.recruitment import outputs as recruitmentOutputs
from features.staff.recruitment import rendering as recruitmentRendering
from features.staff.recruitment import service as recruitmentService
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions


log = logging.getLogger(__name__)
joinButtonEmoji = "\N{WHITE HEAVY CHECK MARK}"


def _hasRole(member: discord.Member, roleId: Optional[int]) -> bool:
    return runtimePermissions.hasAnyRole(member, [roleId])


def _parseParticipantUserIds(raw: Optional[str]) -> list[int]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return normalization.normalizeIntList(data)


def _setAllButtonsDisabled(view: discord.ui.View, disabled: bool) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = disabled


async def _safeInteractionReply(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    await interactionRuntime.safeInteractionReply(
        interaction,
        content=message,
        ephemeral=ephemeral,
    )


async def _safeInteractionDefer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = True,
    thinking: bool = False,
) -> None:
    await interactionRuntime.safeInteractionDefer(
        interaction,
        ephemeral=ephemeral,
        thinking=thinking,
    )


def _parseImageUrls(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for value in data:
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


class RecruitmentReviewView(discord.ui.View):
    def __init__(self, cog: "RecruitmentCog", submissionType: str, submissionId: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.submissionType = submissionType
        self.submissionId = int(submissionId)
        self._lock = asyncio.Lock()

    async def _getSubmission(self) -> Optional[dict]:
        if self.submissionType == "recruitment":
            return await recruitmentService.getRecruitmentSubmission(self.submissionId)
        return await recruitmentService.getRecruitmentTimeSubmission(self.submissionId)

    def _canReview(self, member: discord.Member) -> bool:
        reviewerRoleId = int(getattr(config, "recruitmentReviewerRoleId", 0) or 0)
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return True
        return _hasRole(member, reviewerRoleId)

    async def _updateSubmissionStatus(
        self,
        *,
        status: str,
        reviewerId: int,
        note: Optional[str],
        threadId: Optional[int],
    ) -> None:
        if self.submissionType == "recruitment":
            await recruitmentService.updateRecruitmentStatus(
                self.submissionId,
                status,
                reviewerId=reviewerId,
                note=note,
                threadId=threadId,
            )
            return
        await recruitmentService.updateRecruitmentTimeStatus(
            self.submissionId,
            status,
            reviewerId=reviewerId,
            note=note,
            threadId=threadId,
        )

    async def _queueApprovalPoints(self, submission: dict) -> None:
        points = int(submission.get("points") or 0)
        if points <= 0:
            return

        if self.submissionType == "recruitment":
            submitterId = int(submission["submitterId"])
            await recruitmentService.queuePointsBatch(
                [(submitterId, points, "recruitment", self.submissionId)]
            )
            await self._syncRecruitmentSheet(
                [submitterId],
                points,
                patrolDelta=0,
                hostedPatrolDelta=0,
            )
            return

        patrolType = str(submission.get("patrolType") or "solo").strip().lower()
        if patrolType == "group":
            participantIds = _parseParticipantUserIds(submission.get("participantUserIds"))
            if participantIds:
                await recruitmentService.queuePointsBatch(
                    [
                        (int(userId), int(points), "recruitment-patrol-group", self.submissionId)
                        for userId in participantIds
                    ]
                )
                # Non-RM+ users count attended patrols; RM+ users count hosted patrols.
                # We always write the attended delta here and let sheet rank logic decide
                # whether to use attended or hosted.
                await self._syncRecruitmentSheet(
                    participantIds,
                    points,
                    patrolDelta=1,
                    hostedPatrolDelta=0,
                )

                # Host gets a hosted patrol increment even when not in attendee list.
                submitterId = int(submission["submitterId"])
                await self._syncRecruitmentSheet(
                    [submitterId],
                    pointsDelta=0,
                    patrolDelta=0,
                    hostedPatrolDelta=1,
                )
                return

        submitterId = int(submission["submitterId"])
        await recruitmentService.queuePointsBatch(
            [(submitterId, points, "recruitment-patrol-solo", self.submissionId)]
        )
        await self._syncRecruitmentSheet(
            [submitterId],
            points,
            patrolDelta=0,
            hostedPatrolDelta=0,
        )

    async def _logRecruitmentSheetChange(
        self,
        *,
        reviewerId: int,
        change: str,
        details: str,
    ) -> None:
        await recruitmentOutputs.sendRecruitmentSheetChangeLog(
            self.cog.bot,
            reviewerId=reviewerId,
            change=change,
            details=details,
        )

    async def _syncRecruitmentSheet(
        self,
        discordUserIds: Sequence[int],
        pointsDelta: int,
        patrolDelta: int,
        hostedPatrolDelta: int = 0,
    ) -> None:
        await recruitmentOutputs.syncApprovedLogsToSheet(
            discordUserIds,
            pointsDelta,
            patrolDelta,
            hostedPatrolDelta,
            organizeAfter=True,
        )

    async def _buildSubmissionEmbed(self) -> Optional[discord.Embed]:
        submission = await self._getSubmission()
        if not submission:
            return None
        if self.submissionType == "recruitment":
            return recruitmentRendering.buildRecruitmentEmbed(submission)

        embed = recruitmentRendering.buildRecruitmentTimeEmbed(submission)
        evidenceMessageUrl = submission.get("evidenceMessageUrl")
        if isinstance(evidenceMessageUrl, str) and evidenceMessageUrl.strip():
            embed.add_field(
                name="Evidence Message",
                value=f"[Open message]({evidenceMessageUrl.strip()})",
                inline=False,
            )
        else:
            imageUrls = _parseImageUrls(submission.get("imageUrls"))
            if imageUrls:
                embed.add_field(
                    name="Evidence",
                    value="\n".join(f"[Image {index + 1}]({url})" for index, url in enumerate(imageUrls)),
                    inline=False,
                )
        return embed

    async def _finishDecision(
        self,
        interaction: discord.Interaction,
        *,
        status: str,
        note: Optional[str],
    ) -> None:
        if not isinstance(interaction.user, discord.Member):
            await _safeInteractionReply(
                interaction,
                "This action can only be used inside a server.",
                ephemeral=True,
            )
            return
        if not self._canReview(interaction.user):
            await _safeInteractionReply(
                interaction,
                "You are not authorized to review this submission.",
                ephemeral=True,
            )
            return

        async with self._lock:
            submission = await self._getSubmission()
            if not submission:
                await _safeInteractionReply(interaction, "Submission not found.", ephemeral=True)
                return

            if submission.get("status") in {"APPROVED", "REJECTED"}:
                await _safeInteractionReply(
                    interaction,
                    "This submission has already been finalized.",
                    ephemeral=True,
                )
                return

            await _safeInteractionDefer(interaction, ephemeral=True, thinking=True)

            previousState = [child.disabled for child in self.children]
            _setAllButtonsDisabled(self, True)
            if isinstance(interaction.message, discord.Message):
                try:
                    await interaction.message.edit(view=self)
                except (discord.Forbidden, discord.HTTPException):
                    pass

            try:
                await self._updateSubmissionStatus(
                    status=status,
                    reviewerId=interaction.user.id,
                    note=note,
                    threadId=None,
                )

                if status == "APPROVED":
                    submission = await self._getSubmission()
                    if submission:
                        await self._queueApprovalPoints(submission)
                        points = int(submission.get("points") or 0)
                        patrolType = str(submission.get("patrolType") or "solo").strip().lower()
                        if self.submissionType == "recruitment":
                            await self._logRecruitmentSheetChange(
                                reviewerId=interaction.user.id,
                                change="Edited recruitment points for an approved recruitment log.",
                                details=(
                                    f"User: <@{int(submission.get('submitterId') or 0)}> | "
                                    f"Points +{points}"
                                ),
                            )
                        elif patrolType == "group":
                            participantIds = _parseParticipantUserIds(submission.get("participantUserIds"))
                            await self._logRecruitmentSheetChange(
                                reviewerId=interaction.user.id,
                                change="Edited recruitment patrol events/points for a group patrol approval.",
                                details=(
                                    f"Participants: {len(participantIds)} | "
                                    f"Points per participant +{points} | "
                                    f"Patrols +1 | Host hosted patrols +1"
                                ),
                            )
                        else:
                            await self._logRecruitmentSheetChange(
                                reviewerId=interaction.user.id,
                                change="Edited recruitment patrol events/points for a solo patrol approval.",
                                details=(
                                    f"User: <@{int(submission.get('submitterId') or 0)}> | "
                                    f"Points +{points} | Patrols +1 | Hosted patrols +1"
                                ),
                            )

                updated = await self._getSubmission()
                if updated and updated.get("status") not in {"APPROVED", "REJECTED"}:
                    _setAllButtonsDisabled(self, False)
                else:
                    _setAllButtonsDisabled(self, True)

                if isinstance(interaction.message, discord.Message):
                    embed = await self._buildSubmissionEmbed()
                    try:
                        if embed:
                            await interaction.message.edit(embed=embed, view=self)
                        else:
                            await interaction.message.edit(view=self)
                    except (discord.Forbidden, discord.HTTPException):
                        pass

                if status == "APPROVED":
                    await _safeInteractionReply(interaction, "Submission approved.", ephemeral=True)
                elif status == "REJECTED":
                    await _safeInteractionReply(interaction, "Submission rejected.", ephemeral=True)
                else:
                    await _safeInteractionReply(
                        interaction,
                        "Submission status updated.",
                        ephemeral=True,
                    )
            except Exception as exc:
                for idx, child in enumerate(self.children):
                    child.disabled = previousState[idx] if idx < len(previousState) else False
                if isinstance(interaction.message, discord.Message):
                    try:
                        await interaction.message.edit(view=self)
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                log.exception("Failed to process recruitment review decision.")
                await _safeInteractionReply(
                    interaction,
                    f"Could not process this action: {exc}",
                    ephemeral=True,
                )

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        custom_id="recruitment_review:approve",
    )
    async def approveBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finishDecision(interaction, status="APPROVED", note=None)

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        custom_id="recruitment_review:reject",
    )
    async def rejectBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finishDecision(interaction, status="REJECTED", note=None)


class GroupPatrolManageModal(discord.ui.Modal, title="Manage Group Patrol"):
    targetInput = discord.ui.TextInput(
        label="Remove attendee (number, user ID, or mention)",
        style=discord.TextStyle.short,
        max_length=40,
        required=True,
        placeholder="Example: 2 or 123456789012345678",
    )

    def __init__(self, cog: "RecruitmentCog", patrolId: int):
        super().__init__()
        self.cog = cog
        self.patrolId = int(patrolId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        token = str(self.targetInput.value or "").strip()
        await self.cog.handleGroupPatrolManage(interaction, self.patrolId, token)


class GroupPatrolFinishModal(discord.ui.Modal, title="Finish Group Patrol"):
    durationMinutesInput = discord.ui.TextInput(
        label="Patrol duration (minutes)",
        style=discord.TextStyle.short,
        required=True,
        max_length=5,
        placeholder="Example: 45",
    )

    def __init__(self, cog: "RecruitmentCog", patrolId: int):
        super().__init__()
        self.cog = cog
        self.patrolId = int(patrolId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.durationMinutesInput.value or "").strip()
        try:
            durationMinutes = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Duration must be a whole number of minutes.",
                ephemeral=True,
            )
            return
        if durationMinutes <= 0:
            await interaction.response.send_message(
                "Duration must be greater than 0 minutes.",
                ephemeral=True,
            )
            return
        await self.cog.handleGroupPatrolFinish(interaction, self.patrolId, durationMinutes)


class SoloPatrolDetailsModal(discord.ui.Modal, title="Solo Patrol Details"):
    durationMinutesInput = discord.ui.TextInput(
        label="Patrol duration (minutes)",
        style=discord.TextStyle.short,
        required=True,
        max_length=5,
        placeholder="Example: 45",
    )

    def __init__(self, cog: "RecruitmentCog"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.durationMinutesInput.value or "").strip()
        try:
            durationMinutes = int(raw)
        except ValueError:
            await interaction.response.send_message(
                "Duration must be a whole number of minutes.",
                ephemeral=True,
            )
            return
        if durationMinutes <= 0:
            await interaction.response.send_message(
                "Duration must be greater than 0 minutes.",
                ephemeral=True,
            )
            return
        await self.cog.handleSoloPatrolDetails(interaction, durationMinutes)


class GroupPatrolView(discord.ui.View):
    def __init__(self, cog: "RecruitmentCog", patrolId: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.patrolId = int(patrolId)

    @discord.ui.button(
        label="Delete",
        style=discord.ButtonStyle.danger,
        row=0,
        custom_id="recruitment_patrol:delete",
    )
    async def deleteBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleGroupPatrolDelete(interaction, self.patrolId)

    @discord.ui.button(
        label="Manage",
        style=discord.ButtonStyle.secondary,
        row=0,
        custom_id="recruitment_patrol:manage",
    )
    async def manageBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.openGroupPatrolManage(interaction, self.patrolId)

    @discord.ui.button(
        label="Finish",
        style=discord.ButtonStyle.primary,
        row=0,
        custom_id="recruitment_patrol:finish",
    )
    async def finishBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.openGroupPatrolFinish(interaction, self.patrolId)

    @discord.ui.button(
        style=discord.ButtonStyle.success,
        emoji=joinButtonEmoji,
        row=1,
        custom_id="recruitment_patrol:join",
    )
    async def joinBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleGroupPatrolJoin(interaction, self.patrolId)


