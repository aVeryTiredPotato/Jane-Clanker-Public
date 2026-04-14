import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Any

import discord
from discord import ui

import config
from features.staff.bgflags import service as flagService
from features.staff.orbat import sheets as orbatSheets
from runtime import interaction as interactionRuntime
from runtime import taskBudgeter
from features.staff.sessions import (
    bgBuckets,
    bgCheckViews,
    bgInfoFlow,
    bgRouting,
    bgOutfitsFlow,
    bgQueueViews,
    bgQueueMessaging,
    bgScanPipeline,
    postActions,
    roblox,
    service,
    retryFlow,
    sessionControls,
)
from features.staff.sessions.bgText import (
    buildBgFinalSummaryText as _buildBgFinalSummaryText,
    normalizeForumPostTitle as _normalizeForumPostTitle,
)
from features.staff.sessions.outfitEmbeds import (
    buildOutfitPageEmbeds as _buildOutfitPageEmbeds,
)
from features.staff.sessions.outfitViews import OutfitPageView
from features.staff.sessions.rendering import (
    badgeReviewIcon,
    buildSessionEmbed,
    buildGradingEmbed,
    buildBgQueueEmbed,
    bgReviewIcon,
    flaggedReviewIcon,
    inventoryReviewIcon,
)
from features.staff.sessions.viewHelpers import (
    bgCandidates as _bgCandidates,
    isBgQueueComplete as _isBgQueueComplete,
    loadJsonList as _loadJsonList,
    normalizeIntSet as _normalizeGroupIds,
    parseSessionId,
)
from features.staff.sessions.viewPolicy import (
    canClockIn as _canClockIn,
    clockInDeniedMessage as _clockInDeniedMessage,
    hasModPerm as _hasModPerm,
    resolveBgQueuePingRoleId as _resolveBgQueuePingRoleId,
    robloxGroupUrl as _robloxGroupUrl,
    sessionGuild as _sessionGuild,
)

log = logging.getLogger(__name__)
_modOnlyMessage = "This action is restricted to moderation staff."

_flagRulesCache: Optional[
    tuple[
        set[int],
        list[str],
        list[str],
        list[str],
        set[int],
        set[int],
        set[int],
        dict[int, str],
        int,
    ]
] = None
_flagRulesCacheAt: Optional[datetime] = None
_flagRulesCacheTtl = timedelta(seconds=60)
RobloxJoinRetryView = retryFlow.RobloxJoinRetryView
InventoryRetryView = retryFlow.InventoryRetryView


async def _sendRobloxJoinRequestDm(bot: discord.Client, sessionId: int, userId: int) -> None:
    await retryFlow.sendRobloxJoinRequestDm(bot, sessionId, userId)


async def _sendInventoryPrivateDm(bot: discord.Client, sessionId: int, userId: int) -> None:
    await retryFlow.sendInventoryPrivateDm(bot, sessionId, userId)


async def handleRobloxRetryInteraction(interaction: discord.Interaction) -> bool:
    return await retryFlow.handleRobloxRetryInteraction(interaction)


async def handleInventoryRetryInteraction(interaction: discord.Interaction) -> bool:
    return await retryFlow.handleInventoryRetryInteraction(interaction)

_handledComponentInteractionIds: dict[int, datetime] = {}
_handledComponentInteractionTtl = timedelta(minutes=5)
_bgQueueUpdateTasks: dict[int, asyncio.Task] = {}
_bgQueueUpdateDirty: set[int] = set()
_sessionMessageUpdateTasks: dict[int, asyncio.Task] = {}
_sessionMessageUpdateDirty: set[int] = set()
_bgQueueRepostTasks: dict[int, asyncio.Task] = {}
_bgQueueClaims: dict[tuple[int, int], int] = {}
_cachedUsersById: dict[int, tuple[datetime, Optional[discord.abc.User]]] = {}
_cachedChannelsById: dict[int, tuple[datetime, Optional[object]]] = {}
_bgFinalSummaryPosted: set[int] = set()


def _activeTaskCount(tasksBySessionId: dict[int, asyncio.Task]) -> int:
    return sum(1 for task in tasksBySessionId.values() if task and not task.done())


def getRuntimeQueueTelemetry() -> dict[str, int]:
    return {
        "bgQueueUpdateDirty": len(_bgQueueUpdateDirty),
        "bgQueueUpdateActiveTasks": _activeTaskCount(_bgQueueUpdateTasks),
        "sessionUpdateDirty": len(_sessionMessageUpdateDirty),
        "sessionUpdateActiveTasks": _activeTaskCount(_sessionMessageUpdateTasks),
        "bgQueueRepostActiveTasks": _activeTaskCount(_bgQueueRepostTasks),
        "bgClaimsActive": len(_bgQueueClaims),
    }

def _claimComponentInteraction(interactionId: int) -> bool:
    now = datetime.now()
    expiredIds = [
        key
        for key, seenAt in _handledComponentInteractionIds.items()
        if now - seenAt > _handledComponentInteractionTtl
    ]
    for key in expiredIds:
        _handledComponentInteractionIds.pop(key, None)

    if interactionId in _handledComponentInteractionIds:
        return False
    _handledComponentInteractionIds[interactionId] = now
    return True


def _bgClaimKey(sessionId: int, userId: int) -> tuple[int, int]:
    return int(sessionId), int(userId)


def _cacheTtlSec() -> int:
    return max(30, int(getattr(config, "discordEntityCacheTtlSec", 300) or 300))


def _pruneCacheBySize(cache: dict, maxSize: int = 2048) -> None:
    if len(cache) <= maxSize:
        return
    ordered = sorted(cache.items(), key=lambda item: item[1][0])
    removeCount = len(cache) - maxSize
    for key, _ in ordered[:removeCount]:
        cache.pop(key, None)


def _cacheIsFresh(cachedAt: datetime) -> bool:
    return (datetime.utcnow() - cachedAt).total_seconds() <= _cacheTtlSec()


async def _getCachedUser(bot: discord.Client, userId: int) -> Optional[discord.abc.User]:
    key = int(userId)
    cached = _cachedUsersById.get(key)
    if cached and _cacheIsFresh(cached[0]):
        return cached[1]

    user = bot.get_user(key)
    if user is None:
        try:
            user = await taskBudgeter.runDiscord(lambda: bot.fetch_user(key))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            user = None

    _cachedUsersById[key] = (datetime.utcnow(), user)
    _pruneCacheBySize(_cachedUsersById)
    return user


async def _getCachedChannel(
    bot: discord.Client,
    channelId: int,
) -> Optional[object]:
    key = int(channelId)
    cached = _cachedChannelsById.get(key)
    if cached and _cacheIsFresh(cached[0]):
        return cached[1]

    channel = bot.get_channel(key)
    if channel is None:
        try:
            channel = await taskBudgeter.runDiscord(lambda: bot.fetch_channel(key))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            channel = None

    _cachedChannelsById[key] = (datetime.utcnow(), channel)
    _pruneCacheBySize(_cachedChannelsById)
    return channel


async def _resolveOrbatAgeGroupForUser(userId: int) -> str:
    if orbatSheets is None or not hasattr(orbatSheets, "getOrbatEntry"):
        return ""
    try:
        entry = await taskBudgeter.runSheetsThread(orbatSheets.getOrbatEntry, int(userId))
    except Exception:
        log.exception("Failed to resolve ORBAT age group for user %s during BGC routing.", userId)
        return ""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("ageGroup") or "").strip()


async def ensureBgReviewBuckets(
    bot: discord.Client,
    sessionId: int,
    sourceGuild: Optional[discord.Guild],
) -> dict[str, int]:
    attendees = _bgCandidates(await service.getAttendees(sessionId))
    if not attendees:
        return {
            bgBuckets.adultBgReviewBucket: 0,
            bgBuckets.minorBgReviewBucket: 0,
            "updated": 0,
        }

    reviewBucketsByUserId: dict[int, str] = {}
    bucketCounts = {
        bgBuckets.adultBgReviewBucket: 0,
        bgBuckets.minorBgReviewBucket: 0,
    }

    for attendee in attendees:
        userId = int(attendee["userId"])
        storedBucket = str(attendee.get("bgReviewBucket") or "").strip()
        if storedBucket:
            bucket = bgBuckets.normalizeBgReviewBucket(storedBucket)
        else:
            member = None
            if sourceGuild is not None:
                member = sourceGuild.get_member(userId)
            bucket, _ = await bgRouting.classifyBgReviewBucketForMember(
                member,
                configModule=config,
                resolveOrbatAgeGroup=_resolveOrbatAgeGroupForUser,
                userId=userId,
                guildId=int(getattr(sourceGuild, "id", 0) or 0),
            )
            reviewBucketsByUserId[userId] = bucket
        bucketCounts[bucket] = bucketCounts.get(bucket, 0) + 1

    if reviewBucketsByUserId:
        await service.setBgReviewBucketsBulk(sessionId, reviewBucketsByUserId)

    bucketCounts["updated"] = len(reviewBucketsByUserId)
    return bucketCounts


def getBgClaimOwnerId(sessionId: int, userId: int) -> Optional[int]:
    return _bgQueueClaims.get(_bgClaimKey(sessionId, userId))


def setBgClaimOwnerId(sessionId: int, userId: int, ownerId: int) -> None:
    _bgQueueClaims[_bgClaimKey(sessionId, userId)] = int(ownerId)


def clearBgClaim(sessionId: int, userId: int) -> None:
    _bgQueueClaims.pop(_bgClaimKey(sessionId, userId), None)


def clearBgClaimsForSession(sessionId: int) -> None:
    targetSessionId = int(sessionId)
    staleKeys = [key for key in _bgQueueClaims.keys() if key[0] == targetSessionId]
    for key in staleKeys:
        _bgQueueClaims.pop(key, None)


def getBgClaimsForSession(sessionId: int) -> dict[int, int]:
    out: dict[int, int] = {}
    targetSessionId = int(sessionId)
    for (claimSessionId, userId), ownerId in _bgQueueClaims.items():
        if claimSessionId != targetSessionId:
            continue
        out[int(userId)] = int(ownerId)
    return out


async def requestBgQueueMessageUpdate(
    bot: discord.Client,
    sessionId: int,
    *,
    delaySec: Optional[float] = None,
) -> None:
    debounceDelaySec = float(
        delaySec
        if delaySec is not None
        else getattr(config, "bgQueueUpdateDebounceSec", 1.0)
    )
    debounceDelaySec = max(0.0, debounceDelaySec)

    _bgQueueUpdateDirty.add(sessionId)
    existing = _bgQueueUpdateTasks.get(sessionId)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            while True:
                _bgQueueUpdateDirty.discard(sessionId)
                if debounceDelaySec > 0:
                    await asyncio.sleep(debounceDelaySec)
                await updateBgQueueMessage(bot, sessionId)
                if sessionId not in _bgQueueUpdateDirty:
                    break
        except Exception:
            log.exception("Debounced BG queue update failed for session %s.", sessionId)
        finally:
            current = _bgQueueUpdateTasks.get(sessionId)
            if current is asyncio.current_task():
                _bgQueueUpdateTasks.pop(sessionId, None)
            _bgQueueUpdateDirty.discard(sessionId)

    _bgQueueUpdateTasks[sessionId] = asyncio.create_task(_runner())


def _sessionMessageUpdateDebounceSec() -> float:
    return max(0.0, float(getattr(config, "sessionMessageUpdateDebounceSec", 0.75) or 0.75))


async def requestSessionMessageUpdate(
    bot: discord.Client,
    sessionId: int,
    *,
    delaySec: Optional[float] = None,
) -> None:
    debounceDelaySec = float(
        delaySec
        if delaySec is not None
        else _sessionMessageUpdateDebounceSec()
    )
    debounceDelaySec = max(0.0, debounceDelaySec)

    _sessionMessageUpdateDirty.add(sessionId)
    existing = _sessionMessageUpdateTasks.get(sessionId)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            while True:
                _sessionMessageUpdateDirty.discard(sessionId)
                if debounceDelaySec > 0:
                    await asyncio.sleep(debounceDelaySec)
                await updateSessionMessage(bot, sessionId)
                if sessionId not in _sessionMessageUpdateDirty:
                    break
        except Exception:
            log.exception("Debounced session message update failed for session %s.", sessionId)
        finally:
            current = _sessionMessageUpdateTasks.get(sessionId)
            if current is asyncio.current_task():
                _sessionMessageUpdateTasks.pop(sessionId, None)
            _sessionMessageUpdateDirty.discard(sessionId)

    _sessionMessageUpdateTasks[sessionId] = asyncio.create_task(_runner())


def _queueRepostIntervalSec() -> int:
    return max(60, int(getattr(config, "bgQueueRepostIntervalSec", 300) or 300))


def _ensureBgQueueRepostTask(bot: discord.Client, sessionId: int) -> None:
    existing = _bgQueueRepostTasks.get(sessionId)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            await bot.wait_until_ready()
            intervalSec = _queueRepostIntervalSec()
            while not bot.is_closed():
                await asyncio.sleep(intervalSec)
                shouldContinue = await _repostBgQueueMessage(bot, sessionId)
                if not shouldContinue:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("BG queue repost loop failed for session %s.", sessionId)
        finally:
            current = _bgQueueRepostTasks.get(sessionId)
            if current is asyncio.current_task():
                _bgQueueRepostTasks.pop(sessionId, None)

    _bgQueueRepostTasks[sessionId] = asyncio.create_task(_runner())


def _stopBgQueueRepostTask(sessionId: int) -> None:
    task = _bgQueueRepostTasks.pop(sessionId, None)
    if task and not task.done():
        task.cancel()


async def restorePersistentViews(bot: discord.Client) -> dict[str, int]:
    restoredSessionViews = 0
    restoredBgQueueViews = 0
    restoredBgCheckViews = 0

    activeSessions = await service.getSessionsByStatus(["OPEN", "GRADING", "FINISHED"])
    maxRestoreAgeHours = max(1, int(getattr(config, "sessionExpiryHours", 48) or 48))
    restoreCutoff = datetime.utcnow() - timedelta(hours=maxRestoreAgeHours)

    def _isSessionStale(row: dict) -> bool:
        createdRaw = str(row.get("createdAt") or "").strip()
        if not createdRaw:
            return False
        try:
            createdAt = datetime.fromisoformat(createdRaw)
        except ValueError:
            try:
                createdAt = datetime.strptime(createdRaw, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return False
        return createdAt < restoreCutoff

    for session in activeSessions:
        if _isSessionStale(session):
            continue
        sessionId = int(session["sessionId"])
        sessionType = str(session.get("sessionType") or "").lower()
        sessionStatus = str(session.get("status") or "").upper()
        messageId = session.get("messageId")
        # Only restore the main orientation/session control panel for truly active sessions.
        # Finished sessions should not regain control buttons after restart.
        if messageId and sessionType != "bg-check" and sessionStatus in {"OPEN", "GRADING"}:
            try:
                bot.add_view(SessionView(sessionId), message_id=int(messageId))
                restoredSessionViews += 1
            except Exception:
                log.exception("Failed to restore SessionView for session %s.", sessionId)

        bgQueueMessageIds = [
            int(session.get("bgQueueMessageId") or 0),
            int(session.get("bgQueueMinorMessageId") or 0),
        ]
        if any(messageId > 0 for messageId in bgQueueMessageIds):
            try:
                attendees = _bgCandidates(await service.getAttendees(sessionId))
                # Restore BG queue controls only while queue work is still pending.
                if attendees and not _isBgQueueComplete(attendees):
                    adultQueueMessageId = int(session.get("bgQueueMessageId") or 0)
                    minorQueueMessageId = int(session.get("bgQueueMinorMessageId") or 0)
                    if adultQueueMessageId > 0:
                        bot.add_view(
                            BgQueueView(sessionId, reviewBucket=bgBuckets.adultBgReviewBucket),
                            message_id=adultQueueMessageId,
                        )
                        restoredBgQueueViews += 1
                    if minorQueueMessageId > 0:
                        bot.add_view(
                            BgQueueView(sessionId, reviewBucket=bgBuckets.minorBgReviewBucket),
                            message_id=minorQueueMessageId,
                        )
                        restoredBgQueueViews += 1
                    _ensureBgQueueRepostTask(bot, sessionId)
                    await requestBgQueueMessageUpdate(bot, sessionId, delaySec=0)
            except Exception:
                log.exception("Failed to restore BgQueueView for session %s.", sessionId)

        # Per-attendee BG messages are now ephemeral only.

    return {
        "sessions": restoredSessionViews,
        "bgQueues": restoredBgQueueViews,
        "bgChecks": restoredBgCheckViews,
    }


async def _safeInteractionReply(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    *,
    embed: Optional[discord.Embed] = None,
    embeds: Optional[list[discord.Embed]] = None,
    view: Optional[ui.View] = None,
    ephemeral: bool = True,
) -> None:
    ok = await interactionRuntime.safeInteractionReply(
        interaction,
        content=content,
        embed=embed,
        embeds=embeds,
        view=view,
        ephemeral=ephemeral,
    )
    if not ok:
        return


async def _safeInteractionDefer(
    interaction: discord.Interaction,
    *,
    ephemeral: bool = True,
) -> None:
    await interactionRuntime.safeInteractionDefer(interaction, ephemeral=ephemeral)


async def _safeInteractionEditMessage(
    self,
    interaction: discord.Interaction,
    clearEmbed: bool,
    *,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[ui.View] = None,
) -> bool:
    kwargs: dict[str, Any] = {}
    if content is not None:
        kwargs["content"] = content
    if clearEmbed is True:
        kwargs["embed"] = None
    if embed is not None and clearEmbed is not True:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view

    try:
        await interaction.response.edit_message(**kwargs)
        return True
    except discord.InteractionResponded:
        await interaction.edit_original_response(**kwargs)
        return True
    except (discord.NotFound, discord.HTTPException) as exc:
        if not interactionRuntime.isUnknownInteractionError(exc):
            return False
    except TypeError:
        # Ignore incompatible kwargs and fall through to message edit.
        pass

    if interaction.message is not None:
        return await interactionRuntime.safeMessageEdit(interaction.message, **kwargs)

    if not hasattr(self, "_original_response") or self._original_response is None: # works only if the interaction reffers to message creation, not if the interaction is for example button click
        try:
            self._original_response = await interaction.original_response()
        except:
            self._original_response = None
            pass

    try:
        await self._original_response.edit(**kwargs)
        return True
    except (discord.NotFound, discord.HTTPException):
        return False


async def _sendEphemeralReply(
    interaction: discord.Interaction,
    content: str,
) -> None:
    await _safeInteractionReply(
        interaction,
        content=content,
        ephemeral=True,
    )


async def _sendInvalidComponentReply(
    interaction: discord.Interaction,
    content: str,
) -> None:
    if interaction.response.is_done():
        await _sendEphemeralReply(interaction, content)
        return
    await _safeInteractionReply(interaction, content, ephemeral=True)


async def _requireModPermission(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if isinstance(member, discord.Member) and _hasModPerm(member):
        return True
    await _safeInteractionReply(
        interaction,
        content=_modOnlyMessage,
        ephemeral=True,
    )
    return False

async def _setPendingBgRole(
    guild: Optional[discord.Guild],
    userId: int,
    applyRole: bool,
) -> None:
    roleId = getattr(config, "pendingBgRoleId", None)
    if not guild or not roleId:
        return
    role = guild.get_role(roleId)
    if role is None:
        return
    member = guild.get_member(userId)
    if member is None:
        try:
            member = await guild.fetch_member(userId)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    try:
        if applyRole:
            if role not in member.roles:
                await member.add_roles(role, reason="Orientation exam passed; pending BG check.")
        else:
            if role in member.roles:
                await member.remove_roles(role, reason="BG check resolved.")
    except (discord.Forbidden, discord.HTTPException):
        return


async def _dmUserWithView(bot: discord.Client, userId: int, content: str, view: ui.View) -> bool:
    try:
        user = await _getCachedUser(bot, userId)
        if not user:
            return False
        await user.send(content, view=view)
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def _loadFlagRules() -> tuple[
    set[int], list[str], list[str], list[str], set[int], set[int], set[int], dict[int, str], int
]:
    global _flagRulesCache, _flagRulesCacheAt
    if _flagRulesCache and _flagRulesCacheAt:
        if datetime.now() - _flagRulesCacheAt < _flagRulesCacheTtl:
            return _flagRulesCache

    groupIds = _normalizeGroupIds(getattr(config, "robloxFlagGroupIds", []) or [])
    badgeIds = _normalizeGroupIds(getattr(config, "robloxFlagBadgeIds", []) or [])
    badgeNotes: dict[int, str] = {}
    accountAgeDays = int(getattr(config, "robloxAccountAgeFlagDays", 0))
    itemIds: set[int] = set()
    creatorIds: set[int] = set()
    usernames: list[str] = []
    groupKeywords: list[str] = []
    itemKeywords: list[str] = []

    rules = await flagService.listRules()
    for rule in rules:
        ruleType = str(rule.get("ruleType", "")).lower()
        value = str(rule.get("ruleValue", "")).strip()
        if not value:
            continue
        if ruleType == "group":
            try:
                groupIds.add(int(value))
            except ValueError:
                continue
        elif ruleType == "username":
            usernames.append(value.lower())
        elif ruleType == "keyword":
            keywordValue = value.lower()
            groupKeywords.append(keywordValue)
            itemKeywords.append(keywordValue)
        elif ruleType in {"group_keyword", "group-keyword"}:
            groupKeywords.append(value.lower())
        elif ruleType in {"item_keyword", "item-keyword"}:
            itemKeywords.append(value.lower())
        elif ruleType == "item":
            try:
                itemIds.add(int(value))
            except ValueError:
                continue
        elif ruleType == "creator":
            try:
                creatorIds.add(int(value))
            except ValueError:
                continue
        elif ruleType == "badge":
            try:
                badgeId = int(value)
            except ValueError:
                continue
            badgeIds.add(badgeId)
            note = str(rule.get("note") or "").strip()
            if note:
                badgeNotes[badgeId] = note

    _flagRulesCache = (
        groupIds,
        usernames,
        groupKeywords,
        itemKeywords,
        itemIds,
        creatorIds,
        badgeIds,
        badgeNotes,
        accountAgeDays,
    )
    _flagRulesCacheAt = datetime.now()
    return _flagRulesCache

RobloxIdentity = bgScanPipeline.RobloxIdentity


async def _resolveRobloxIdentity(attendee: dict) -> RobloxIdentity:
    return await bgScanPipeline.resolveRobloxIdentity(attendee)

JoinPasswordModal = sessionControls.JoinPasswordModal
SessionView = sessionControls.SessionView
GradingView = sessionControls.GradingView


def _buildBgAttendeeReviewEmbed(
    attendee: dict,
    *,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
    claimOwnerId: Optional[int] = None,
    includeClaimField: bool = True,
) -> discord.Embed:
    return bgQueueViews.buildBgAttendeeReviewEmbed(
        attendee,
        reviewBucket=reviewBucket,
        claimOwnerId=claimOwnerId,
        includeClaimField=includeClaimField,
    )


async def _openBgAttendeePanel(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
) -> None:
    await bgQueueViews.openBgAttendeePanel(
        interaction,
        sessionId,
        targetUserId,
        reviewBucket=reviewBucket,
    )


BgAttendeeReviewView = bgQueueViews.BgAttendeeReviewView
BgOpenAttendeeModal = bgQueueViews.BgOpenAttendeeModal
BgQueueView = bgQueueViews.BgQueueView
BgQueueForceCloseConfirmView = bgQueueViews.BgQueueForceCloseConfirmView


BgCheckView = bgCheckViews.BgCheckView
BgInfoModal = bgCheckViews.BgInfoModal


async def _getBgCandidateByIndex(sessionId: int, index: int) -> Optional[dict]:
    attendees = _bgCandidates(await service.getAttendees(sessionId))
    if index < 1 or index > len(attendees):
        return None
    return attendees[index - 1]


async def _sendBgInfoForTarget(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
) -> None:
    await bgInfoFlow.sendBgInfoForTarget(
        interaction,
        sessionId,
        targetUserId,
        reviewBucket=reviewBucket,
        configModule=config,
        serviceModule=service,
        safeInteractionDefer=_safeInteractionDefer,
        safeInteractionReply=_safeInteractionReply,
        bgCandidates=_bgCandidates,
        loadFlagRules=_loadFlagRules,
        resolveRobloxIdentity=_resolveRobloxIdentity,
        scanRobloxGroupsForAttendee=_scanRobloxGroupsForAttendee,
        scanRobloxInventoryForAttendee=_scanRobloxInventoryForAttendee,
        scanRobloxBadgesForAttendee=_scanRobloxBadgesForAttendee,
        sendInventoryPrivateDm=_sendInventoryPrivateDm,
        loadJsonList=_loadJsonList,
        buildActionsView=lambda sid, uid, viewerId, robloxUid, robloxName, bucket: bgInfoFlow.BgInfoActionsView(
            sid,
            uid,
            viewerId,
            robloxUid,
            robloxName,
            reviewBucket=bucket,
            configModule=config,
            serviceModule=service,
            robloxModule=roblox,
            safeInteractionReply=_safeInteractionReply,
            safeInteractionDefer=_safeInteractionDefer,
            requireModPermission=_requireModPermission,
        ),
    )


class BgOutfitModal(ui.Modal, title="View Roblox Outfits"):
    number = ui.TextInput(
        label="Attendee Number",
        placeholder="Number from the BG queue list",
        required=True,
    )

    def __init__(self, sessionId: int):
        super().__init__()
        self.sessionId = sessionId

    async def on_submit(self, interaction: discord.Interaction):
        if not await _requireModPermission(interaction):
            return

        if not getattr(config, "robloxOutfitScanEnabled", True):
            return await _safeInteractionReply(interaction,
                "The outfit viewer is currently disabled in configuration.",
                ephemeral=True,
            )

        try:
            index = int(str(self.number.value).strip())
        except ValueError:
            return await _safeInteractionReply(interaction,
                "Please enter a valid attendee number.",
                ephemeral=True,
            )

        attendee = await _getBgCandidateByIndex(self.sessionId, index)
        if attendee is None:
            return await _safeInteractionReply(interaction,
                "The attendee number you entered is outside the current queue range.",
                ephemeral=True,
            )
        await _sendBgOutfitsForTarget(interaction, self.sessionId, attendee["userId"])


async def _sendBgOutfitsForTarget(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
) -> None:
    await bgOutfitsFlow.sendBgOutfitsForTarget(interaction, sessionId, targetUserId)


async def _updateBgCheckMessage(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
) -> None:
    attendee = await service.getAttendee(sessionId, targetUserId)
    if not attendee or not interaction.message:
        return

    embed = _buildBgAttendeeReviewEmbed(
        attendee,
        reviewBucket=reviewBucket,
        includeClaimField=False,
    )

    if isinstance(interaction.message, discord.Message):
        newView = BgCheckView(sessionId, targetUserId, reviewBucket=reviewBucket)
        for child in newView.children:
            child.disabled = True
        await interaction.message.edit(embed=embed, view=newView)


async def _maybeNotifyBgComplete(interaction: discord.Interaction, sessionId: int) -> None:
    attendees = _bgCandidates(await service.getAttendees(sessionId))
    if not attendees:
        return
    if any(a["bgStatus"] == "PENDING" for a in attendees):
        return
    await _postBgFinalSummary(interaction.client, sessionId)
    await _safeInteractionReply(interaction, "All attendees processed.", ephemeral=False)


def _bgQueueChannelCandidateIds(session: dict) -> list[int]:
    return bgQueueMessaging.bgQueueChannelCandidateIds(session)


async def _resolveBgQueueChannelForSession(
    bot: discord.Client,
    session: dict,
) -> Optional[discord.abc.Messageable]:
    return await bgQueueMessaging.resolveBgQueueChannelForSession(bot, session)


async def _fetchBgQueueMessageForSession(
    bot: discord.Client,
    session: dict,
    messageId: int,
) -> tuple[Optional[discord.abc.Messageable], Optional[discord.Message]]:
    return await bgQueueMessaging.fetchBgQueueMessageForSession(bot, session, messageId)


async def _postBgFinalSummary(bot: discord.Client, sessionId: int) -> None:
    await bgQueueMessaging.postBgFinalSummary(bot, sessionId)

async def _dmUser(bot: discord.Client, userId: int, message: str) -> bool:
    try:
        user = await _getCachedUser(bot, userId)
        if user is None:
            return False
        await user.send(message)
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def _notifyMods(bot: discord.Client, message: str) -> None:
    if not config.bgCheckChannelId:
        return
    channel = await _getCachedChannel(bot, int(config.bgCheckChannelId))
    if channel:
        await channel.send(message)


async def _postBgFailureForumEntry(
    bot: discord.Client,
    guild: Optional[discord.Guild],
    targetUserId: int,
    reviewerId: int,
) -> None:
    forumChannelId = int(getattr(config, "bgFailureForumChannelId", 0) or 0)
    if forumChannelId <= 0:
        return

    channel = await _getCachedChannel(bot, forumChannelId)
    if not isinstance(channel, discord.ForumChannel):
        return

    failedUsername: Optional[str] = None
    if guild is not None:
        member = guild.get_member(int(targetUserId))
        if member is not None:
            # Prefer guild nickname for forum titles; fall back gracefully.
            failedUsername = str(member.nick or member.display_name or member.name).strip()
    if not failedUsername:
        user = await _getCachedUser(bot, int(targetUserId))
        if user is not None:
            failedUsername = user.name

    postTitle = _normalizeForumPostTitle(
        failedUsername or "",
        fallback=f"user-{int(targetUserId)}",
    )
    try:
        await channel.create_thread(
            name=postTitle,
            content=f"<@{int(reviewerId)}>",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except (discord.Forbidden, discord.HTTPException):
        return


async def _updateRecruitmentSubmissionMessage(
    bot: discord.Client,
    submission: dict,
) -> None:
    await postActions.updateRecruitmentSubmissionMessage(
        bot,
        submission,
        getChannel=_getCachedChannel,
    )


async def _dmRecruiterBonus(
    bot: discord.Client,
    recruiterId: int,
    recruitId: int,
    bonusPoints: int,
) -> None:
    await postActions.dmRecruiterBonus(
        bot,
        recruiterId,
        recruitId,
        bonusPoints,
        dmUser=_dmUser,
    )


async def _applyRecruitmentOrientationBonus(
    bot: discord.Client,
    recruitUserId: int,
) -> None:
    await postActions.applyRecruitmentOrientationBonus(
        bot,
        recruitUserId,
        getChannel=_getCachedChannel,
        dmUser=_dmUser,
    )


async def _reconcileRecruitmentOrientationBonusesForSession(
    bot: discord.Client,
    sessionId: int,
) -> None:
    await postActions.reconcileRecruitmentOrientationBonusesForSession(
        bot,
        sessionId,
        getChannel=_getCachedChannel,
        dmUser=_dmUser,
    )


async def _reconcileRecruitmentOrientationBonusesForSessionSafe(
    bot: discord.Client,
    sessionId: int,
) -> None:
    try:
        await _reconcileRecruitmentOrientationBonusesForSession(bot, sessionId)
    except Exception:
        log.exception(
            "Failed recruitment orientation bonus reconciliation for session %s",
            sessionId,
        )


async def _scanRobloxGroupsForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    flagIds: set[int],
    flagUsernames: list[str],
    groupKeywords: list[str],
    accountAgeDays: int,
) -> bool:
    return await bgScanPipeline.scanRobloxGroupsForAttendee(
        sessionId,
        attendee,
        identity,
        flagIds,
        flagUsernames,
        groupKeywords,
        accountAgeDays,
    )


async def _scanRobloxInventoryForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    itemKeywords: list[str],
    flagItemIds: set[int],
    flagCreatorIds: set[int],
    forceRescan: bool = False,
) -> bool:
    updated = await bgScanPipeline.scanRobloxInventoryForAttendee(
        sessionId,
        attendee,
        identity,
        itemKeywords,
        flagItemIds,
        flagCreatorIds,
        forceRescan=forceRescan,
    )
    if updated and attendee.get("userId") is not None:
        refreshed = await service.getAttendee(sessionId, int(attendee["userId"]))
        if refreshed and str(refreshed.get("robloxInventoryScanStatus") or "").upper() == "OK":
            retryFlow.clearInventoryPrivateDmSent(sessionId, int(attendee["userId"]))
    return updated

async def _scanRobloxBadgesForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    flagBadgeIds: set[int],
    badgeNotes: dict[int, str],
) -> bool:
    return await bgScanPipeline.scanRobloxBadgesForAttendee(
        sessionId,
        attendee,
        identity,
        flagBadgeIds,
        badgeNotes,
    )

async def _scanRobloxOutfitsForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    forceRescan: bool = False,
) -> bool:
    return await bgScanPipeline.scanRobloxOutfitsForAttendee(
        sessionId,
        attendee,
        identity,
        forceRescan=forceRescan,
    )


async def _scanRobloxGroupsForAttendees(
    sessionId: int,
    attendees: list[dict],
    bot: Optional[discord.Client] = None,
) -> bool:
    flagRules = await _loadFlagRules()

    async def _onInventoryBecamePrivate(userId: int) -> None:
        if bot is None:
            return
        await _sendInventoryPrivateDm(bot, sessionId, userId)

    callback = _onInventoryBecamePrivate if bot is not None else None
    return await bgScanPipeline.scanRobloxFlagsForAttendees(
        sessionId,
        attendees,
        flagRules=flagRules,
        onInventoryBecamePrivate=callback,
    )


async def _attemptRobloxAutoAccept(
    bot: discord.Client,
    guild: Optional[discord.Guild],
    sessionId: int,
    targetUserId: int,
) -> str:
    return await postActions.attemptRobloxAutoAccept(
        bot,
        guild,
        sessionId,
        targetUserId,
        dmUser=_dmUser,
        notifyMods=_notifyMods,
        groupUrlProvider=_robloxGroupUrl,
    )

async def _maybeAutoAcceptRoblox(
    bot: discord.Client,
    guild: Optional[discord.Guild],
    sessionId: int,
    targetUserId: int,
) -> None:
    try:
        await _attemptRobloxAutoAccept(bot, guild, sessionId, targetUserId)
    except Exception:
        log.exception("Roblox auto-accept failed for session %s user %s", sessionId, targetUserId)


async def _deleteSessionMessage(bot: discord.Client, sessionId: int) -> None:
    await postActions.deleteSessionMessage(
        bot,
        sessionId,
        getChannel=_getCachedChannel,
    )


async def _postOrientationResults(bot: discord.Client, sessionId: int) -> None:
    await postActions.postOrientationResults(
        bot,
        sessionId,
        getChannel=_getCachedChannel,
    )


def _buildBgQueueMainView(sessionId: int, attendees: list[dict]) -> BgQueueView:
    reviewBucket = bgBuckets.adultBgReviewBucket
    if attendees:
        reviewBucket = bgBuckets.normalizeBgReviewBucket(
            attendees[0].get("bgReviewBucket"),
            default=bgBuckets.adultBgReviewBucket,
        )
    view = BgQueueView(sessionId, reviewBucket=reviewBucket)
    if _isBgQueueComplete(attendees):
        for child in view.children:
            child.disabled = True
    return view


async def _closeBgQueueControls(
    bot: discord.Client,
    sessionId: int,
    *,
    reviewBucket: str | None = None,
    clearMessageReference: bool,
) -> None:
    await bgQueueMessaging.closeBgQueueControls(
        bot,
        sessionId,
        reviewBucket=reviewBucket,
        clearMessageReference=clearMessageReference,
    )


async def _repostBgQueueMessage(bot: discord.Client, sessionId: int) -> bool:
    return await bgQueueMessaging.repostBgQueueMessage(bot, sessionId)


async def updateBgQueueMessage(bot: discord.Client, sessionId: int) -> None:
    await bgQueueMessaging.updateBgQueueMessage(bot, sessionId)

async def updateSessionMessage(bot: discord.Client, sessionId: int):
    session = await service.getSession(sessionId)
    if not session:
        return

    try:
        channel = await _getCachedChannel(bot, int(session["channelId"]))
        if channel is None:
            return
        msg = await channel.fetch_message(session["messageId"])
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return

    guild = msg.guild
    host = None
    if guild:
        host = guild.get_member(session["hostId"])
        if host is None:
            try:
                host = await guild.fetch_member(session["hostId"])
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                host = None
    attendees = await service.getAttendees(sessionId)

    showBg = False
    hostMention = host.mention if host else f"<@{session['hostId']}>"
    embed = buildSessionEmbed(session, hostMention, attendees, showBg=showBg)

    view = SessionView(sessionId)
    await view.disableIfLocked()
    await msg.edit(embed=embed, view=view)

async def postBgQueue(bot: discord.Client, sessionId: int, guild: discord.Guild):
    await ensureBgReviewBuckets(bot, sessionId, guild)
    await bgQueueMessaging.postBgQueue(bot, sessionId, guild)


def _configureSessionControlsModule() -> None:
    sessionControls.configure(
        serviceModule=service,
        canClockIn=_canClockIn,
        clockInDeniedMessage=_clockInDeniedMessage,
        parseSessionId=parseSessionId,
        safeInteractionReply=_safeInteractionReply,
        safeInteractionDefer=_safeInteractionDefer,
        safeInteractionEditMessage=_safeInteractionEditMessage,
        safeInteractionSendModal=interactionRuntime.safeInteractionSendModal,
        requestSessionMessageUpdate=requestSessionMessageUpdate,
        updateSessionMessage=updateSessionMessage,
        buildGradingEmbed=buildGradingEmbed,
        setPendingBgRole=_setPendingBgRole,
        postOrientationResults=_postOrientationResults,
        deleteSessionMessage=_deleteSessionMessage,
        postBgQueue=postBgQueue,
    )

def _configureRetryFlowModule() -> None:
    retryFlow.configure(
        serviceModule=service,
        claimComponentInteraction=_claimComponentInteraction,
        safeInteractionDefer=_safeInteractionDefer,
        sendEphemeralReply=_sendEphemeralReply,
        sendInvalidComponentReply=_sendInvalidComponentReply,
        safeInteractionReply=_safeInteractionReply,
        requestBgQueueMessageUpdate=requestBgQueueMessageUpdate,
        loadFlagRules=_loadFlagRules,
        resolveRobloxIdentity=_resolveRobloxIdentity,
        scanRobloxInventoryForAttendee=_scanRobloxInventoryForAttendee,
        attemptRobloxAutoAccept=_attemptRobloxAutoAccept,
        dmUserWithView=_dmUserWithView,
        robloxGroupUrlProvider=_robloxGroupUrl,
    )

def _configureBgOutfitsFlowModule() -> None:
    bgOutfitsFlow.configure(
        safeInteractionDefer=_safeInteractionDefer,
        safeInteractionReply=_safeInteractionReply,
        bgCandidates=_bgCandidates,
        service=service,
        resolveRobloxIdentity=_resolveRobloxIdentity,
        scanRobloxOutfitsForAttendee=_scanRobloxOutfitsForAttendee,
        loadJsonList=_loadJsonList,
        robloxModule=roblox,
        outfitPageViewClass=OutfitPageView,
        buildOutfitPageEmbeds=_buildOutfitPageEmbeds,
    )


def _configureBgQueueMessagingModule() -> None:
    bgQueueMessaging.configure(
        configModule=config,
        service=service,
        bgFinalSummaryPosted=_bgFinalSummaryPosted,
        bgCandidates=_bgCandidates,
        isBgQueueComplete=_isBgQueueComplete,
        getCachedChannel=_getCachedChannel,
        getCachedUser=_getCachedUser,
        buildBgFinalSummaryText=_buildBgFinalSummaryText,
        getBgClaimsForSession=getBgClaimsForSession,
        buildBgQueueMainView=_buildBgQueueMainView,
        buildBgQueueEmbed=buildBgQueueEmbed,
        stopBgQueueRepostTask=_stopBgQueueRepostTask,
        ensureBgQueueRepostTask=_ensureBgQueueRepostTask,
        clearBgClaimsForSession=clearBgClaimsForSession,
        clearBgClaim=clearBgClaim,
        ensureBgReviewBuckets=ensureBgReviewBuckets,
        reconcileRecruitmentOrientationBonusesForSessionSafe=_reconcileRecruitmentOrientationBonusesForSessionSafe,
        scanRobloxGroupsForAttendees=_scanRobloxGroupsForAttendees,
        requestBgQueueMessageUpdate=requestBgQueueMessageUpdate,
        bgQueueViewClass=BgQueueView,
        resolveBgQueuePingRoleId=_resolveBgQueuePingRoleId,
    )


def _configureBgQueueViewsModule() -> None:
    bgQueueViews.configure(
        service=service,
        bgCandidates=_bgCandidates,
        safeInteractionReply=_safeInteractionReply,
        safeInteractionDefer=_safeInteractionDefer,
        safeInteractionSendModal=interactionRuntime.safeInteractionSendModal,
        requireModPermission=_requireModPermission,
        getBgClaimOwnerId=getBgClaimOwnerId,
        setBgClaimOwnerId=setBgClaimOwnerId,
        clearBgClaim=clearBgClaim,
        requestBgQueueMessageUpdate=requestBgQueueMessageUpdate,
        updateSessionMessage=updateSessionMessage,
        maybeNotifyBgComplete=_maybeNotifyBgComplete,
        sessionGuild=_sessionGuild,
        setPendingBgRole=_setPendingBgRole,
        postBgFailureForumEntry=_postBgFailureForumEntry,
        maybeAutoAcceptRoblox=_maybeAutoAcceptRoblox,
        sendRobloxJoinRequestDm=_sendRobloxJoinRequestDm,
        applyRecruitmentOrientationBonus=_applyRecruitmentOrientationBonus,
        sendBgInfoForTarget=_sendBgInfoForTarget,
        sendBgOutfitsForTarget=_sendBgOutfitsForTarget,
        isBgQueueComplete=_isBgQueueComplete,
        closeBgQueueControls=_closeBgQueueControls,
        inventoryReviewIcon=inventoryReviewIcon,
        badgeReviewIcon=badgeReviewIcon,
        bgReviewIcon=bgReviewIcon,
        flaggedReviewIcon=flaggedReviewIcon,
    )


_configureSessionControlsModule()
_configureRetryFlowModule()
_configureBgOutfitsFlowModule()
_configureBgQueueMessagingModule()
_configureBgQueueViewsModule()


def _configureBgCheckViewsModule() -> None:
    bgCheckViews.configure(
        service=service,
        bgCandidates=_bgCandidates,
        requireModPermission=_requireModPermission,
        clearBgClaim=clearBgClaim,
        safeInteractionReply=_safeInteractionReply,
        sessionGuild=_sessionGuild,
        setPendingBgRole=_setPendingBgRole,
        updateSessionMessage=updateSessionMessage,
        updateBgCheckMessage=_updateBgCheckMessage,
        requestBgQueueMessageUpdate=requestBgQueueMessageUpdate,
        maybeNotifyBgComplete=_maybeNotifyBgComplete,
        maybeAutoAcceptRoblox=_maybeAutoAcceptRoblox,
        sendRobloxJoinRequestDm=_sendRobloxJoinRequestDm,
        applyRecruitmentOrientationBonus=_applyRecruitmentOrientationBonus,
        postBgFailureForumEntry=_postBgFailureForumEntry,
        sendBgInfoForTarget=_sendBgInfoForTarget,
        sendBgOutfitsForTarget=_sendBgOutfitsForTarget,
    )


_configureBgCheckViewsModule()

