from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional, Sequence

import discord

import config

from runtime import permissions as runtimePermissions
from runtime import interaction as interactionRuntime
from features.staff.honorGuard import outputs as honorGuardOutputs
from features.staff.honorGuard import rendering as honorGuardRendering
from features.staff.honorGuard import service as honorGuardService

log = logging.getLogger(__name__)
joinButtonEmoji = "\N{WHITE HEAVY CHECK MARK}"

def _hasRole(member: discord.Member, roleId: Optional[int]) -> bool:
    return runtimePermissions.hasAnyRole(member, [roleId])

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

def _setAllButtonsDisabled(view: discord.ui.View, disabled: bool) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = disabled

class HonorGuardPointAwardReviewView(discord.ui.View):
    def __init__(self, cog: "HonorGuardCog",  submissionId: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.submissionId = int(submissionId)
        self._lock = asyncio.Lock()

    async def _getSubmission(self) -> Optional[dict]:
        return await honorGuardService.getPointAwardSubmission(self.submissionId)

    def _canReview(self, member: discord.Member) -> bool:
        reviewerRoleId = int(getattr(config, "honorGuardReviewerRoleId", 0) or 0)
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
        await honorGuardService.updatePointAwardStatus(
            self.submissionId,
            status,
            reviewerId=reviewerId,
            note=note,
            threadId=threadId,
        )

    async def _queueApprovalPoints(self, submission: dict) -> None:
        quotaPoints = int(submission.get("quotaPoints") or 0)
        eventPoints = int(submission.get("eventPoints") or 0)
        if quotaPoints <= 0 and eventPoints <= 0:
            return
        
        submitterId = int(submission["submitterId"])
        await self._syncHonorGuardSheet(
            [submitterId],
            quotaPoints,
            eventPoints,
        )

    async def _logHonorGuardSheetChange(
        self,
        *,
        reviewerId: int,
        change: str,
        details: str,
    ) -> None:
        await honorGuardOutputs.sendHonorGuardSheetChangeLog(
            self.cog.bot,
            reviewerId=reviewerId,
            change=change,
            details=details,
        )

    async def _syncHonorGuardSheet(
        self,
        discordUserIds: Sequence[int],
        quotaDelta: int,
        pointsDelta: int,
    ) -> None:
        await honorGuardOutputs.syncApprovedLogsToSheet(
            discordUserIds,
            quotaDelta,
            pointsDelta,
            organizeAfter=True,
        )


    async def _buildSubmissionEmbed(self) -> Optional[discord.Embed]:
        submission = await self._getSubmission()
        if not submission:
            return None
        return honorGuardRendering.buildPointAwardEmbed(submission)


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
                "You are not authorized to review this Point Award.",
                ephemeral=True,
            )
            return

        async with self._lock:
            submission = await self._getSubmission()
            if not submission:
                await _safeInteractionReply(interaction, "Point Award not found.", ephemeral=True)
                return

            if submission.get("status") in {"APPROVED", "REJECTED"}:
                await _safeInteractionReply(
                    interaction,
                    "This Submission has already been finalized.",
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
                        eventPoints = int(submission.get("eventPoints") or 0)
                        quotaPoints = int(submission.get("quotaPoints") or 0)
                        await self._logHonorGuardSheetChange(
                            reviewerId=interaction.user.id,
                            change="Edited Honor Guard points for an approved Point Award.",
                            details=(
                                f"User: <@{submission.get('awardedUserId')}> | "
                                f"Points +{eventPoints} EP, +{quotaPoints} QP | "
                                f"Reason: {submission.get('reason')}"
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
                    await _safeInteractionReply(interaction, "Point Award approved.", ephemeral=True)
                elif status == "REJECTED":
                    await _safeInteractionReply(interaction, "Point Award rejected.", ephemeral=True)
                else:
                    await _safeInteractionReply(
                        interaction,
                        "Point Award status updated.",
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
                log.exception("Failed to process Point Award review decision.")
                await _safeInteractionReply(
                    interaction,
                    f"Could not process this action: {exc}",
                    ephemeral=True,
                )

    @discord.ui.button(
        label="Approve",
        style=discord.ButtonStyle.success,
        custom_id="honorGuard_review:approve",
    )
    async def approveBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finishDecision(interaction, status="APPROVED", note=None)

    @discord.ui.button(
        label="Reject",
        style=discord.ButtonStyle.danger,
        custom_id="honorGuard_review:reject",
    )
    async def rejectBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._finishDecision(interaction, status="REJECTED", note=None)
