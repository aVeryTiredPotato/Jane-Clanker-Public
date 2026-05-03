from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any, Optional

import discord
from features.staff.sessions import bgBuckets
from runtime import interaction as interactionRuntime
from runtime import orgProfiles

_deps: dict[str, Any] = {}


def configure(**deps: Any) -> None:
    _deps.update(deps)


def _dep(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"bgQueueMessaging dependency not configured: {name}")
    return value


def _buildQueueEmbedAndView(
    sessionId: int,
    session: dict[str, Any],
    attendees: list[dict[str, Any]],
    *,
    reviewBucket: str,
) -> tuple[discord.Embed, discord.ui.View]:
    embed = _dep("buildBgQueueEmbed")(
        session,
        attendees,
        reviewBucket=reviewBucket,
        claimsByUserId=_dep("getBgClaimsForSession")(sessionId),
    )
    view = _dep("buildBgQueueMainView")(sessionId, attendees)
    return embed, view


def _syncQueueRepostState(bot: discord.Client, sessionId: int, attendees: list[dict[str, Any]]) -> None:
    if _dep("isBgQueueComplete")(attendees):
        _dep("stopBgQueueRepostTask")(sessionId)
        return
    _dep("ensureBgQueueRepostTask")(bot, sessionId)


async def _sendQueueStartupAlert(
    channel: discord.TextChannel | discord.Thread,
    *,
    reviewRoleId: int,
    attendeeCount: int,
) -> None:
    if int(reviewRoleId) <= 0:
        return
    await interactionRuntime.safeChannelSend(
        channel,
        content=(
            f"<@&{int(reviewRoleId)}>\n"
            f"Background-check queue startup: `{int(attendeeCount)}` attendee(s) waiting."
        ),
        allowed_mentions=discord.AllowedMentions(roles=True),
    )


async def _recoverMissingQueueMessage(
    bot: discord.Client,
    sessionId: int,
    *,
    previousMessageId: int,
    attendees: list[dict[str, Any]],
    reviewBucket: str,
) -> None:
    latestSession = await _dep("service").getSession(sessionId)
    latestMessageId = _messageIdForBucket(latestSession or {}, reviewBucket)
    if latestMessageId > 0 and latestMessageId != int(previousMessageId):
        await _dep("requestBgQueueMessageUpdate")(bot, sessionId, delaySec=0)
        return
    if attendees and not _dep("isBgQueueComplete")(attendees):
        await repostBgQueueMessage(bot, sessionId, reviewBucket=reviewBucket)


def bgQueueChannelCandidateIds(session: dict[str, Any], reviewBucket: str = bgBuckets.adultBgReviewBucket) -> list[int]:
    candidateIds: list[int] = []
    normalizedBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)
    try:
        sessionGuildId = int(session.get("guildId") or 0)
    except (TypeError, ValueError):
        sessionGuildId = 0

    try:
        configuredChannelId = int(
            orgProfiles.getOrganizationValue(
                _dep("configModule"),
                "bgCheckChannelId",
                guildId=sessionGuildId,
                default=0,
            )
            or 0
        )
    except (TypeError, ValueError):
        configuredChannelId = 0
    try:
        configuredAdultChannelId = int(
            orgProfiles.getOrganizationValue(
                _dep("configModule"),
                "bgCheckAdultReviewChannelId",
                guildId=sessionGuildId,
                default=0,
            )
            or 0
        )
    except (TypeError, ValueError):
        configuredAdultChannelId = 0
    try:
        configuredMinorChannelId = int(
            orgProfiles.getOrganizationValue(
                _dep("configModule"),
                "bgCheckMinorReviewChannelId",
                guildId=sessionGuildId,
                default=0,
            )
            or 0
        )
    except (TypeError, ValueError):
        configuredMinorChannelId = 0

    # BG review should stay in the dedicated review channels. Falling back to the
    # original session channel can leak the split review flow back into the source
    # server, which is exactly what the adult/minor separation is trying to avoid.
    if normalizedBucket == bgBuckets.minorBgReviewBucket:
        preferredIds = [configuredMinorChannelId]
    else:
        effectiveConfiguredChannelId = configuredAdultChannelId or configuredChannelId
        preferredIds = [effectiveConfiguredChannelId]
    for channelId in preferredIds:
        if channelId > 0 and channelId not in candidateIds:
            candidateIds.append(channelId)
    return candidateIds


async def resolveBgQueueChannelForSession(
    bot: discord.Client,
    session: dict[str, Any],
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
) -> Optional[discord.abc.Messageable]:
    for channelId in bgQueueChannelCandidateIds(session, reviewBucket):
        channel = await _dep("getCachedChannel")(bot, channelId)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
    return None


async def fetchBgQueueMessageForSession(
    bot: discord.Client,
    session: dict[str, Any],
    messageId: int,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
) -> tuple[Optional[discord.abc.Messageable], Optional[discord.Message]]:
    targetMessageId = int(messageId or 0)
    if targetMessageId <= 0:
        return None, None

    for channelId in bgQueueChannelCandidateIds(session, reviewBucket):
        channel = await _dep("getCachedChannel")(bot, channelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue
        message = await interactionRuntime.safeFetchMessage(channel, targetMessageId)
        if message is not None:
            return channel, message
        else:
            continue
    return None, None


def _messageIdForBucket(session: dict[str, Any], reviewBucket: str) -> int:
    normalizedBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)
    if normalizedBucket == bgBuckets.minorBgReviewBucket:
        return int(session.get("bgQueueMinorMessageId") or 0)
    return int(session.get("bgQueueMessageId") or 0)


def _activeQueueBuckets(attendees: list[dict[str, Any]]) -> list[str]:
    buckets: list[str] = []
    if _dep("bgCandidates")(attendees, bgBuckets.adultBgReviewBucket):
        buckets.append(bgBuckets.adultBgReviewBucket)
    if _dep("bgCandidates")(attendees, bgBuckets.minorBgReviewBucket):
        buckets.append(bgBuckets.minorBgReviewBucket)
    return buckets


def _pendingQueueBuckets(attendees: list[dict[str, Any]]) -> list[str]:
    buckets: list[str] = []
    for reviewBucket in _activeQueueBuckets(attendees):
        bucketAttendees = _dep("bgCandidates")(attendees, reviewBucket)
        if bucketAttendees and not _dep("isBgQueueComplete")(bucketAttendees):
            buckets.append(reviewBucket)
    return buckets


async def postBgFinalSummary(bot: discord.Client, sessionId: int) -> None:
    if int(sessionId) in _dep("bgFinalSummaryPosted"):
        return

    session = await _dep("service").getSession(sessionId)
    if not session:
        return

    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))
    if not attendees:
        return

    approved = [int(row["userId"]) for row in attendees if str(row.get("bgStatus") or "").upper() == "APPROVED"]
    rejected = [int(row["userId"]) for row in attendees if str(row.get("bgStatus") or "").upper() == "REJECTED"]
    pending = [int(row["userId"]) for row in attendees if str(row.get("bgStatus") or "").upper() == "PENDING"]
    sessionReviewStats = await _dep("service").getBgReviewSessionStats(int(sessionId))

    moderatorStatsLines: list[str] = []
    for row in sessionReviewStats:
        try:
            reviewerId = int(row.get("reviewerId") or 0)
        except (TypeError, ValueError):
            reviewerId = 0
        if reviewerId <= 0:
            continue
        user = await _dep("getCachedUser")(bot, reviewerId)
        username = str(user.name) if user is not None else f"user-{reviewerId}"
        approvals = int(row.get("approvals") or 0)
        rejections = int(row.get("rejections") or 0)
        total = int(row.get("total") or 0)
        moderatorStatsLines.append(f"{username}: {approvals} approved, {rejections} denied, {total} total.")

    if not approved and not rejected and not pending:
        return

    summary = _dep("buildBgFinalSummaryText")(
        sessionId=int(sessionId),
        approvedUserIds=approved,
        rejectedUserIds=rejected,
        pendingUserIds=pending,
        moderatorStatsLines=moderatorStatsLines,
    )

    sentChannelIds: set[int] = set()
    for reviewBucket in _activeQueueBuckets(attendees) or [bgBuckets.adultBgReviewBucket]:
        targetChannel = await resolveBgQueueChannelForSession(bot, session, reviewBucket)
        if not isinstance(targetChannel, (discord.TextChannel, discord.Thread)):
            continue
        channelId = int(getattr(targetChannel, "id", 0) or 0)
        if channelId <= 0 or channelId in sentChannelIds:
            continue
        try:
            if len(summary) <= 1900:
                await interactionRuntime.safeChannelSend(
                    targetChannel,
                    content=summary,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
            else:
                tempPath = ""
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".txt", delete=False) as handle:
                        handle.write(summary)
                        tempPath = handle.name
                    await interactionRuntime.safeChannelSend(
                        targetChannel,
                        content=(
                            "### BG Check Final Results\n"
                            f"Session `{int(sessionId)}` summary is attached."
                        ),
                        file=discord.File(tempPath, filename=f"bg-final-results-session-{int(sessionId)}.txt"),
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                finally:
                    if tempPath:
                        try:
                            os.remove(tempPath)
                        except OSError:
                            pass
            sentChannelIds.add(channelId)
        except (discord.Forbidden, discord.HTTPException):
            continue

    _dep("bgFinalSummaryPosted").add(int(sessionId))


async def closeBgQueueControls(
    bot: discord.Client,
    sessionId: int,
    *,
    reviewBucket: str | None,
    clearMessageReference: bool,
) -> None:
    session = await _dep("service").getSession(sessionId)
    if not session:
        return
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))

    targetBuckets = (
        [bgBuckets.normalizeBgReviewBucket(reviewBucket)]
        if reviewBucket is not None
        else [bgBuckets.adultBgReviewBucket, bgBuckets.minorBgReviewBucket]
    )
    for bucket in targetBuckets:
        messageId = _messageIdForBucket(session, bucket)
        if messageId <= 0:
            continue
        _, queueMessage = await fetchBgQueueMessageForSession(bot, session, messageId, bucket)
        if queueMessage is not None:
            view = _dep("bgQueueViewClass")(sessionId, reviewBucket=bucket)
            for child in view.children:
                child.disabled = True
            await interactionRuntime.safeMessageEdit(queueMessage, view=view)

    if reviewBucket is None:
        _dep("clearBgClaimsForSession")(sessionId)
    else:
        for attendee in _dep("bgCandidates")(attendees, reviewBucket):
            try:
                targetUserId = int(attendee.get("userId") or 0)
            except (TypeError, ValueError):
                targetUserId = 0
            if targetUserId > 0:
                _dep("clearBgClaim")(sessionId, targetUserId)
    if clearMessageReference:
        for bucket in targetBuckets:
            await _dep("service").setBgQueueMessage(sessionId, 0, reviewBucket=bucket)

    refreshedSession = await _dep("service").getSession(sessionId)
    remainingQueueMessageIds = [
        _messageIdForBucket(refreshedSession or {}, bgBuckets.adultBgReviewBucket),
        _messageIdForBucket(refreshedSession or {}, bgBuckets.minorBgReviewBucket),
    ]
    if _dep("isBgQueueComplete")(attendees) or not any(messageId > 0 for messageId in remainingQueueMessageIds):
        _dep("stopBgQueueRepostTask")(sessionId)
    asyncio.create_task(_dep("reconcileRecruitmentOrientationBonusesForSessionSafe")(bot, sessionId))


async def repostBgQueueMessage(
    bot: discord.Client,
    sessionId: int,
    *,
    reviewBucket: str | None = None,
) -> bool:
    session = await _dep("service").getSession(sessionId)
    if not session:
        return False
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))
    if not attendees or _dep("isBgQueueComplete")(attendees):
        _dep("stopBgQueueRepostTask")(sessionId)
        return False

    anySucceeded = False
    targetBuckets = (
        [bgBuckets.normalizeBgReviewBucket(reviewBucket)]
        if reviewBucket is not None
        else _pendingQueueBuckets(attendees)
    )
    for bucket in targetBuckets:
        bucketAttendees = _dep("bgCandidates")(attendees, bucket)
        if not bucketAttendees or _dep("isBgQueueComplete")(bucketAttendees):
            continue
        oldMessageId = _messageIdForBucket(session, bucket)
        if oldMessageId <= 0:
            continue
        channel = await resolveBgQueueChannelForSession(bot, session, bucket)
        if channel is None or not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue

        embed, view = _buildQueueEmbedAndView(
            sessionId,
            session,
            bucketAttendees,
            reviewBucket=bucket,
        )

        newMessage = await interactionRuntime.safeChannelSend(channel, embed=embed, view=view)
        if newMessage is None:
            continue

        await _dep("service").setBgQueueMessage(sessionId, int(newMessage.id), reviewBucket=bucket)
        _, oldMessage = await fetchBgQueueMessageForSession(bot, session, int(oldMessageId), bucket)
        if oldMessage is not None:
            await interactionRuntime.safeMessageDelete(oldMessage)
        anySucceeded = True
    return anySucceeded


async def updateBgQueueMessage(bot: discord.Client, sessionId: int) -> None:
    session = await _dep("service").getSession(sessionId)
    if not session:
        return

    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))
    await _dep("ensureBgReviewBuckets")(bot, sessionId, bot.get_guild(int(session.get("guildId") or 0)))
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))
    updated = await _dep("scanRobloxGroupsForAttendees")(sessionId, attendees, bot=bot)
    if updated:
        attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))

    _syncQueueRepostState(bot, sessionId, attendees)
    for reviewBucket in _activeQueueBuckets(attendees):
        bucketAttendees = _dep("bgCandidates")(attendees, reviewBucket)
        messageId = _messageIdForBucket(session, reviewBucket)
        if messageId <= 0:
            continue
        _, msg = await fetchBgQueueMessageForSession(bot, session, messageId, reviewBucket)
        if msg is None:
            await _recoverMissingQueueMessage(
                bot,
                sessionId,
                previousMessageId=messageId,
                attendees=bucketAttendees,
                reviewBucket=reviewBucket,
            )
            continue
        embed, view = _buildQueueEmbedAndView(
            sessionId,
            session,
            bucketAttendees,
            reviewBucket=reviewBucket,
        )
        if not await interactionRuntime.safeMessageEdit(msg, embed=embed, view=view):
            await _recoverMissingQueueMessage(
                bot,
                sessionId,
                previousMessageId=messageId,
                attendees=bucketAttendees,
                reviewBucket=reviewBucket,
            )


async def postBgQueue(bot: discord.Client, sessionId: int, guild: discord.Guild) -> None:
    session = await _dep("service").getSession(sessionId)
    if not session:
        return
    await _dep("ensureBgReviewBuckets")(bot, sessionId, guild)
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))

    queueBuckets = _activeQueueBuckets(attendees)
    if not queueBuckets:
        return

    _dep("clearBgClaimsForSession")(sessionId)
    for reviewBucket in queueBuckets:
        bucketAttendees = _dep("bgCandidates")(attendees, reviewBucket)
        channel = await resolveBgQueueChannelForSession(bot, session, reviewBucket)
        if channel is None:
            continue

        embed, view = _buildQueueEmbedAndView(
            sessionId,
            session,
            bucketAttendees,
            reviewBucket=reviewBucket,
        )
        reviewRoleIdInt = _dep("resolveBgQueuePingRoleId")(channel)
        try:
            await _sendQueueStartupAlert(
                channel,
                reviewRoleId=reviewRoleIdInt,
                attendeeCount=len(bucketAttendees),
            )
            msg = await interactionRuntime.safeChannelSend(channel, embed=embed, view=view)
            if msg is None:
                continue
        except (discord.Forbidden, discord.HTTPException):
            continue

        await _dep("service").setBgQueueMessage(sessionId, msg.id, reviewBucket=reviewBucket)
    if attendees and not _dep("isBgQueueComplete")(attendees):
        _syncQueueRepostState(bot, sessionId, attendees)
        await _dep("requestBgQueueMessageUpdate")(bot, sessionId, delaySec=0)
