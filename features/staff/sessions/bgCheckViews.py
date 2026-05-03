from __future__ import annotations

import logging
from typing import Any

import discord
from discord import ui
from features.staff.sessions import bgBuckets

log = logging.getLogger(__name__)

_deps: dict[str, Any] = {}


def configure(**deps: Any) -> None:
    _deps.update(deps)


def _dep(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"bgCheckViews dependency not configured: {name}")
    return value


async def _getBgCandidateByIndex(
    sessionId: int,
    index: int,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
) -> dict[str, Any] | None:
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId), reviewBucket)
    if index < 1 or index > len(attendees):
        return None
    return attendees[index - 1]


class BgCheckView(ui.View):
    def __init__(self, sessionId: int, targetUserId: int, *, reviewBucket: str = bgBuckets.adultBgReviewBucket):
        super().__init__(timeout=None)
        self.sessionId = int(sessionId)
        self.targetUserId = int(targetUserId)
        self.reviewBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)

        self.approveBtn.custom_id = f"bg:approve:{sessionId}:{targetUserId}"
        self.rejectBtn.custom_id = f"bg:reject:{sessionId}:{targetUserId}"
        self.infoBtn.custom_id = f"bg:info:{sessionId}:{targetUserId}"
        self.outfitsBtn.custom_id = f"bg:outfits:{sessionId}:{targetUserId}"
        if self.reviewBucket == bgBuckets.minorBgReviewBucket:
            self.remove_item(self.outfitsBtn)

    async def _applyDecision(self, interaction: discord.Interaction, newStatus: str) -> None:
        if not await _dep("requireModPermission")(interaction):
            return

        result = await _dep("applyBgDecision")(
            interaction,
            self.sessionId,
            self.targetUserId,
            newStatus,
            reviewBucket=self.reviewBucket,
            refreshBgCheckMessage=True,
        )
        await _dep("safeInteractionReply")(
            interaction,
            f"{result.statusText} <@{self.targetUserId}>.",
            ephemeral=True,
        )

    @ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approveBtn(self, interaction: discord.Interaction, _: ui.Button):
        await self._applyDecision(interaction, "APPROVED")

    @ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def rejectBtn(self, interaction: discord.Interaction, _: ui.Button):
        await self._applyDecision(interaction, "REJECTED")

    @ui.button(label="Get Info", style=discord.ButtonStyle.secondary, row=1)
    async def infoBtn(self, interaction: discord.Interaction, _: ui.Button):
        if not await _dep("requireModPermission")(interaction):
            return
        try:
            await _dep("sendBgInfoForTarget")(interaction, self.sessionId, self.targetUserId, self.reviewBucket)
        except Exception:
            log.exception(
                "Get Info failed for session %s attendee %s.",
                self.sessionId,
                self.targetUserId,
            )
            await _dep("safeInteractionReply")(
                interaction,
                content="Get Info failed due to an internal error. Please try again.",
                ephemeral=True,
            )

    @ui.button(label="Outfits", style=discord.ButtonStyle.secondary, row=1)
    async def outfitsBtn(self, interaction: discord.Interaction, _: ui.Button):
        if not await _dep("requireModPermission")(interaction):
            return
        await _dep("sendBgOutfitsForTarget")(interaction, self.sessionId, self.targetUserId)


class BgInfoModal(ui.Modal, title="Get Attendee Info"):
    number = ui.TextInput(
        label="Attendee Number",
        placeholder="Number from the BG queue list",
        required=True,
    )

    def __init__(self, sessionId: int, reviewBucket: str = bgBuckets.adultBgReviewBucket):
        super().__init__()
        self.sessionId = int(sessionId)
        self.reviewBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _dep("requireModPermission")(interaction):
            return

        try:
            index = int(str(self.number.value).strip())
        except ValueError:
            await _dep("safeInteractionReply")(
                interaction,
                "Please enter a valid attendee number.",
                ephemeral=True,
            )
            return

        attendee = await _getBgCandidateByIndex(self.sessionId, index, self.reviewBucket)
        if attendee is None:
            await _dep("safeInteractionReply")(
                interaction,
                "The attendee number you entered is outside the current queue range.",
                ephemeral=True,
            )
            return
        try:
            await _dep("sendBgInfoForTarget")(interaction, self.sessionId, attendee["userId"], self.reviewBucket)
        except Exception:
            log.exception(
                "Get Info modal failed for session %s attendee %s.",
                self.sessionId,
                attendee["userId"],
            )
            await _dep("safeInteractionReply")(
                interaction,
                content="Get Info failed due to an internal error. Please try again.",
                ephemeral=True,
            )
