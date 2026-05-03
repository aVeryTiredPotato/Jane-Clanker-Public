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
    bgReviewActions,
    bgScanPipeline,
    bgSpreadsheetRouting,
    sessionNotifications,
    postActions,
    service,
    retryFlow,
    sessionControls,
    viewRuntime,
)
from features.staff.sessions.Roblox import robloxGames, robloxOutfits, robloxUsers
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


getRuntimeQueueTelemetry = viewRuntime.getRuntimeQueueTelemetry
getBgClaimOwnerId = viewRuntime.getBgClaimOwnerId
setBgClaimOwnerId = viewRuntime.setBgClaimOwnerId
clearBgClaim = viewRuntime.clearBgClaim
clearBgClaimsForSession = viewRuntime.clearBgClaimsForSession
getBgClaimsForSession = viewRuntime.getBgClaimsForSession
requestBgQueueMessageUpdate = viewRuntime.requestBgQueueMessageUpdate
requestSessionMessageUpdate = viewRuntime.requestSessionMessageUpdate
_claimComponentInteraction = viewRuntime.claimComponentInteraction
_getCachedUser = viewRuntime.getCachedUser
_getCachedChannel = viewRuntime.getCachedChannel
_ensureBgQueueRepostTask = viewRuntime.ensureBgQueueRepostTask
_stopBgQueueRepostTask = viewRuntime.stopBgQueueRepostTask
_bgFinalSummaryPosted = viewRuntime.getBgFinalSummaryPostedSet()
_dmUser = sessionNotifications.dmUser
_notifyMods = sessionNotifications.notifyMods
_postBgFailureForumEntry = sessionNotifications.postBgFailureForumEntry
_postBgFinalSummary = sessionNotifications.postBgFinalSummary
_maybeNotifyBgComplete = sessionNotifications.maybeNotifyBgComplete


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
async def restorePersistentViews(bot: discord.Client) -> dict[str, int]:
    restoredSessionViews = 0
    restoredBgQueueViews = 0
    restoredBgCheckViews = 0

    activeSessions = await service.getSessionsByStatus(["OPEN", "FULL", "GRADING", "FINISHED"])
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
        if messageId and sessionType != "bg-check" and sessionStatus in {"OPEN", "FULL", "GRADING"}:
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
            member = await taskBudgeter.runDiscord(lambda: guild.fetch_member(userId))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return
    try:
        if applyRole:
            if role not in member.roles:
                await taskBudgeter.runDiscord(
                    lambda: member.add_roles(role, reason="Orientation exam passed; pending BG check.")
                )
        else:
            if role in member.roles:
                await taskBudgeter.runDiscord(
                    lambda: member.remove_roles(role, reason="BG check resolved.")
                )
    except (discord.Forbidden, discord.HTTPException):
        return


async def _dmUserWithView(bot: discord.Client, userId: int, content: str, view: ui.View) -> bool:
    try:
        user = await _getCachedUser(bot, userId)
        if not user:
            return False
        await taskBudgeter.runDiscord(lambda: user.send(content, view=view))
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
            robloxModule=robloxUsers,
            robloxGamesModule=robloxGames,
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
        await interactionRuntime.safeMessageEdit(interaction.message, embed=embed, view=newView)


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

    channel = await _getCachedChannel(bot, int(session["channelId"]))
    if channel is None:
        return
    msg = await interactionRuntime.safeFetchMessage(channel, session["messageId"])
    if msg is None:
        return

    guild = msg.guild
    host = None
    if guild:
        host = guild.get_member(session["hostId"])
        if host is None:
            try:
                host = await taskBudgeter.runDiscord(lambda: guild.fetch_member(session["hostId"]))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                host = None
    attendees = await service.getAttendees(sessionId)

    showBg = False
    hostMention = host.mention if host else f"<@{session['hostId']}>"
    embed = buildSessionEmbed(session, hostMention, attendees, showBg=showBg)

    view = SessionView(sessionId)
    await view.disableIfLocked()
    await interactionRuntime.safeMessageEdit(msg, embed=embed, view=view)


def _configureViewRuntimeModule() -> None:
    viewRuntime.configure(
        configModule=config,
        taskBudgeterModule=taskBudgeter,
        updateBgQueueMessage=updateBgQueueMessage,
        updateSessionMessage=updateSessionMessage,
        repostBgQueueMessage=_repostBgQueueMessage,
    )


def _configureSessionNotificationsModule() -> None:
    sessionNotifications.configure(
        configModule=config,
        service=service,
        bgCandidates=_bgCandidates,
        safeInteractionReply=_safeInteractionReply,
        getCachedUser=_getCachedUser,
        getCachedChannel=_getCachedChannel,
        normalizeForumPostTitle=_normalizeForumPostTitle,
        postBgFinalSummaryFn=bgQueueMessaging.postBgFinalSummary,
    )


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
        routeBgcSpreadsheet=bgSpreadsheetRouting.routeBgcSpreadsheet,
    )


def _configureBgSpreadsheetRoutingModule() -> None:
    bgSpreadsheetRouting.configure(
        service=service,
        bgCandidates=_bgCandidates,
        ensureBgReviewBuckets=ensureBgReviewBuckets,
        getCachedChannel=_getCachedChannel,
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
        robloxModule=robloxOutfits,
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


def _configureBgReviewActionsModule() -> None:
    bgReviewActions.configure(
        service=service,
        safeInteractionDefer=_safeInteractionDefer,
        clearBgClaim=clearBgClaim,
        sessionGuild=_sessionGuild,
        setPendingBgRole=_setPendingBgRole,
        updateSessionMessage=updateSessionMessage,
        updateBgCheckMessage=_updateBgCheckMessage,
        requestBgQueueMessageUpdate=requestBgQueueMessageUpdate,
        maybeNotifyBgComplete=_maybeNotifyBgComplete,
        postBgFailureForumEntry=_postBgFailureForumEntry,
        maybeAutoAcceptRoblox=_maybeAutoAcceptRoblox,
        sendRobloxJoinRequestDm=_sendRobloxJoinRequestDm,
        applyRecruitmentOrientationBonus=_applyRecruitmentOrientationBonus,
    )


def _configureBgQueueViewsModule() -> None:
    bgQueueViews.configure(
        service=service,
        bgCandidates=_bgCandidates,
        safeInteractionReply=_safeInteractionReply,
        safeInteractionSendModal=interactionRuntime.safeInteractionSendModal,
        requireModPermission=_requireModPermission,
        getBgClaimOwnerId=getBgClaimOwnerId,
        setBgClaimOwnerId=setBgClaimOwnerId,
        requestBgQueueMessageUpdate=requestBgQueueMessageUpdate,
        applyBgDecision=bgReviewActions.applyDecision,
        sendBgInfoForTarget=_sendBgInfoForTarget,
        sendBgOutfitsForTarget=_sendBgOutfitsForTarget,
        isBgQueueComplete=_isBgQueueComplete,
        closeBgQueueControls=_closeBgQueueControls,
        inventoryReviewIcon=inventoryReviewIcon,
        badgeReviewIcon=badgeReviewIcon,
        bgReviewIcon=bgReviewIcon,
        flaggedReviewIcon=flaggedReviewIcon,
    )


_configureViewRuntimeModule()
_configureSessionNotificationsModule()
_configureBgSpreadsheetRoutingModule()
_configureSessionControlsModule()
_configureRetryFlowModule()
_configureBgOutfitsFlowModule()
_configureBgQueueMessagingModule()
_configureBgReviewActionsModule()
_configureBgQueueViewsModule()


def _configureBgCheckViewsModule() -> None:
    bgCheckViews.configure(
        service=service,
        bgCandidates=_bgCandidates,
        requireModPermission=_requireModPermission,
        safeInteractionReply=_safeInteractionReply,
        applyBgDecision=bgReviewActions.applyDecision,
        sendBgInfoForTarget=_sendBgInfoForTarget,
        sendBgOutfitsForTarget=_sendBgOutfitsForTarget,
    )


_configureBgCheckViewsModule()

