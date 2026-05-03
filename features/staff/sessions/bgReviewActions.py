from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import discord
from features.staff.sessions import bgBuckets

_deps: dict[str, Any] = {}


def configure(**deps: Any) -> None:
    _deps.update(deps)


def _dep(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"bgReviewActions dependency not configured: {name}")
    return value


@dataclass(slots=True)
class BgDecisionResult:
    statusChanged: bool
    statusText: str


def _statusText(newStatus: str) -> str:
    normalizedStatus = str(newStatus).strip().upper()
    if normalizedStatus == "APPROVED":
        return "Approved"
    if normalizedStatus == "REJECTED":
        return "Rejected"
    return normalizedStatus.title()


async def applyDecision(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
    newStatus: str,
    *,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
    defer: bool = False,
    refreshBgCheckMessage: bool = False,
) -> BgDecisionResult:
    normalizedStatus = str(newStatus).strip().upper()
    normalizedBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)

    if defer:
        await _dep("safeInteractionDefer")(interaction, ephemeral=True)

    statusChanged = await _dep("service").setBgStatusWithReviewer(
        sessionId,
        targetUserId,
        normalizedStatus,
        int(interaction.user.id),
    )
    _dep("clearBgClaim")(sessionId, targetUserId)

    session = await _dep("service").getSession(sessionId)
    sessionType = (session or {}).get("sessionType")
    sessionGuild = _dep("sessionGuild")(interaction.client, session, interaction.guild)

    if normalizedStatus == "REJECTED" and statusChanged:
        asyncio.create_task(
            _dep("postBgFailureForumEntry")(
                interaction.client,
                sessionGuild,
                targetUserId,
                int(interaction.user.id),
            )
        )

    if sessionType in {"orientation", "bg-check"}:
        await _dep("setPendingBgRole")(sessionGuild, targetUserId, False)

    if normalizedStatus == "APPROVED":
        if sessionType == "orientation":
            await _dep("service").awardHostPointIfEligible(sessionId, targetUserId)

        asyncio.create_task(
            _dep("maybeAutoAcceptRoblox")(
                interaction.client,
                sessionGuild,
                sessionId,
                targetUserId,
            )
        )
        asyncio.create_task(
            _dep("sendRobloxJoinRequestDm")(interaction.client, sessionId, targetUserId)
        )
        if sessionType == "orientation":
            asyncio.create_task(
                _dep("applyRecruitmentOrientationBonus")(
                    interaction.client,
                    targetUserId,
                )
            )

    await _dep("updateSessionMessage")(interaction.client, sessionId)
    if refreshBgCheckMessage:
        await _dep("updateBgCheckMessage")(interaction, sessionId, targetUserId, normalizedBucket)
    await _dep("requestBgQueueMessageUpdate")(interaction.client, sessionId)
    await _dep("maybeNotifyBgComplete")(interaction, sessionId)

    return BgDecisionResult(
        statusChanged=statusChanged,
        statusText=_statusText(normalizedStatus),
    )
