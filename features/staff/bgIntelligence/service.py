from __future__ import annotations

import asyncio
import difflib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import discord

import characters
import config
from db.sqlite import execute, executeReturnId, fetchAll, fetchOne
from features.staff.bgIntelligence import externalSources, scoring
from features.staff.bgflags import service as flagService
from features.staff.orbat import sheets as orbatSheets
from features.staff.sessions import (
    bgRouting,
    bgScanPipeline,
)
from features.staff.sessions.Roblox import (
    robloxBadges,
    robloxGamepasses,
    robloxGames,
    robloxGroups,
    robloxInventory,
    robloxOutfits,
    robloxProfiles,
    robloxUsers,
)
from features.staff.sessions.bgBuckets import adultBgReviewBucket, normalizeBgReviewBucket
from runtime import taskBudgeter

ProgressCallback = Callable[[str], Awaitable[Any]]
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlagRules:
    groupIds: set[int]
    usernames: list[str]
    usernameNotes: dict[str, str]
    usernameSeverities: dict[str, int]
    robloxUserIds: set[int]
    robloxUserNotes: dict[int, str]
    robloxUserSeverities: dict[int, int]
    watchlistUserIds: set[int]
    watchlistNotes: dict[int, str]
    watchlistSeverities: dict[int, int]
    bannedUserIds: set[int]
    bannedUserNotes: dict[int, str]
    bannedUserSeverities: dict[int, int]
    groupKeywords: list[str]
    itemKeywords: list[str]
    itemIds: set[int]
    creatorIds: set[int]
    gameIds: set[int]
    gameKeywords: list[str]
    badgeIds: set[int]
    badgeNotes: dict[int, str]
    accountAgeDays: int


def _emptyFlagRules(*, configModule: Any = config) -> FlagRules:
    try:
        accountAgeDays = int(getattr(configModule, "robloxAccountAgeFlagDays", 0) or 0)
    except (TypeError, ValueError):
        accountAgeDays = 0
    return FlagRules(
        groupIds=set(),
        usernames=[],
        usernameNotes={},
        usernameSeverities={},
        robloxUserIds=set(),
        robloxUserNotes={},
        robloxUserSeverities={},
        watchlistUserIds=set(),
        watchlistNotes={},
        watchlistSeverities={},
        bannedUserIds=set(),
        bannedUserNotes={},
        bannedUserSeverities={},
        groupKeywords=[],
        itemKeywords=[],
        itemIds=set(),
        creatorIds=set(),
        gameIds=set(),
        gameKeywords=[],
        badgeIds=set(),
        badgeNotes={},
        accountAgeDays=max(0, accountAgeDays),
    )


@dataclass
class BgIntelligenceReport:
    discordUserId: int
    discordDisplayName: str
    discordUsername: str
    reviewBucket: str
    reviewBucketSource: str
    identitySource: str = "rover"
    robloxUserId: Optional[int] = None
    robloxUsername: Optional[str] = None
    roverError: Optional[str] = None
    robloxCreated: Optional[str] = None
    robloxAgeDays: Optional[int] = None
    usernameHistoryScanStatus: str = "SKIPPED"
    usernameHistoryScanError: Optional[str] = None
    previousRobloxUsernames: list[str] | None = None
    altScanStatus: str = "SKIPPED"
    altScanError: Optional[str] = None
    altMatches: list[dict[str, Any]] | None = None
    groupSummary: dict[str, Any] | None = None
    groupScanStatus: str = "SKIPPED"
    groupScanError: Optional[str] = None
    connectionScanStatus: str = "SKIPPED"
    connectionScanError: Optional[str] = None
    connectionSummary: dict[str, Any] | None = None
    friendIdsScanStatus: str = "SKIPPED"
    friendIdsScanError: Optional[str] = None
    friendUserIds: list[int] | None = None
    groups: list[dict[str, Any]] | None = None
    flaggedGroups: list[dict[str, Any]] | None = None
    flagMatches: list[dict[str, Any]] | None = None
    directMatches: list[dict[str, Any]] | None = None
    inventoryScanStatus: str = "SKIPPED"
    inventoryScanError: Optional[str] = None
    inventorySummary: dict[str, Any] | None = None
    flaggedItems: list[dict[str, Any]] | None = None
    gamepassScanStatus: str = "SKIPPED"
    gamepassScanError: Optional[str] = None
    gamepassSummary: dict[str, Any] | None = None
    ownedGamepasses: list[dict[str, Any]] | None = None
    favoriteGameScanStatus: str = "SKIPPED"
    favoriteGameScanError: Optional[str] = None
    favoriteGames: list[dict[str, Any]] | None = None
    flaggedFavoriteGames: list[dict[str, Any]] | None = None
    outfitScanStatus: str = "SKIPPED"
    outfitScanError: Optional[str] = None
    outfits: list[dict[str, Any]] | None = None
    badgeHistoryScanStatus: str = "SKIPPED"
    badgeHistoryScanError: Optional[str] = None
    badgeHistorySample: list[dict[str, Any]] | None = None
    badgeTimelineSummary: dict[str, Any] | None = None
    badgeScanStatus: str = "SKIPPED"
    badgeScanError: Optional[str] = None
    flaggedBadges: list[dict[str, Any]] | None = None
    externalSourceStatus: str = "SKIPPED"
    externalSourceError: Optional[str] = None
    externalSourceMatches: list[dict[str, Any]] | None = None
    externalSourceDetails: list[dict[str, Any]] | None = None
    priorReportSummary: dict[str, Any] | None = None
    privateInventoryDmSent: Optional[bool] = None


def _normalizeIntSet(values: Any) -> set[int]:
    normalized: set[int] = set()
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            normalized.add(parsed)
    return normalized


def _ageDaysFromCreated(created: Optional[str]) -> Optional[int]:
    if not created:
        return None
    try:
        createdAt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    if createdAt.tzinfo is None:
        createdAt = createdAt.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(createdAt.tzinfo) - createdAt).days)


def _parseRobloxDate(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _ruleSeverity(rule: dict[str, Any]) -> int:
    try:
        severity = int(rule.get("severity") or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, severity))


def _directMinimum(ruleType: str, severity: int = 0) -> int:
    normalizedType = str(ruleType or "").strip().lower()
    defaults = {
        "banned_user": 95,
        "watchlist": 88,
        "roblox_user": 82,
        "username": 82,
    }
    defaultMinimum = defaults.get(normalizedType, 0)
    configured = max(0, min(100, int(severity or 0)))
    if normalizedType == "banned_user":
        return max(defaultMinimum, configured)
    if configured > 0:
        return max(20, configured)
    return defaultMinimum


async def _resolveOrbatAgeGroupForUser(userId: int) -> str:
    if orbatSheets is None or not hasattr(orbatSheets, "getOrbatEntry"):
        return ""
    try:
        entry = await taskBudgeter.runSheetsThread(orbatSheets.getOrbatEntry, int(userId))
    except Exception:
        return ""
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("ageGroup") or "").strip()


async def resolveReviewBucket(
    member: discord.Member,
    *,
    guildId: int,
    reviewBucketOverride: str = "auto",
    configModule: Any = config,
) -> tuple[str, str]:
    normalizedOverride = str(reviewBucketOverride or "auto").strip().lower()
    if normalizedOverride and normalizedOverride != "auto":
        return normalizeBgReviewBucket(normalizedOverride), "manual"
    return await bgRouting.classifyBgReviewBucketForMember(
        member,
        configModule=configModule,
        resolveOrbatAgeGroup=_resolveOrbatAgeGroupForUser,
        userId=int(member.id),
        guildId=int(guildId),
    )


def _positiveInt(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _roverSourceName(guildId: int, *, configModule: Any = config) -> str:
    parsedGuildId = _positiveInt(guildId)
    mainGuildId = _positiveInt(getattr(configModule, "serverId", 0))
    if parsedGuildId > 0 and mainGuildId > 0 and parsedGuildId == mainGuildId:
        return "main server"
    if parsedGuildId > 0:
        return f"guild {parsedGuildId}"
    return "configured guild"


def _roverIdentitySource(
    *,
    roverGuildId: int,
    scanGuildId: int,
    configModule: Any = config,
) -> str:
    parsedRoverGuildId = _positiveInt(roverGuildId)
    mainGuildId = _positiveInt(getattr(configModule, "serverId", 0))
    if parsedRoverGuildId > 0 and mainGuildId > 0 and parsedRoverGuildId == mainGuildId:
        if _positiveInt(scanGuildId) != parsedRoverGuildId:
            return "rover_main_server"
    return "rover"


async def _lookupRoverForGuild(
    discordUserId: int,
    *,
    guildId: int,
    configModule: Any = config,
) -> tuple[robloxUsers.RoverLookupResult, int]:
    resolvedGuildId = _positiveInt(guildId) or _positiveInt(getattr(configModule, "serverId", 0))
    result = await robloxUsers.fetchRobloxUser(int(discordUserId), guildId=resolvedGuildId or None)
    return result, resolvedGuildId


async def loadFlagRules(*, configModule: Any = config) -> FlagRules:
    groupIds = _normalizeIntSet(getattr(configModule, "robloxFlagGroupIds", []) or [])
    badgeIds = _normalizeIntSet(getattr(configModule, "robloxFlagBadgeIds", []) or [])
    badgeNotes: dict[int, str] = {}
    itemIds: set[int] = set()
    creatorIds: set[int] = set()
    gameIds: set[int] = set()
    gameKeywords: list[str] = []
    usernames: list[str] = []
    usernameNotes: dict[str, str] = {}
    usernameSeverities: dict[str, int] = {}
    robloxUserIds: set[int] = set()
    robloxUserNotes: dict[int, str] = {}
    robloxUserSeverities: dict[int, int] = {}
    watchlistUserIds: set[int] = set()
    watchlistNotes: dict[int, str] = {}
    watchlistSeverities: dict[int, int] = {}
    bannedUserIds: set[int] = set()
    bannedUserNotes: dict[int, str] = {}
    bannedUserSeverities: dict[int, int] = {}
    groupKeywords: list[str] = []
    itemKeywords: list[str] = []
    try:
        accountAgeDays = int(getattr(configModule, "robloxAccountAgeFlagDays", 0) or 0)
    except (TypeError, ValueError):
        accountAgeDays = 0

    rules = await flagService.listRules()
    for rule in rules:
        ruleType = str(rule.get("ruleType", "")).strip().lower()
        value = str(rule.get("ruleValue", "")).strip()
        if not value:
            continue
        if ruleType == "group":
            try:
                groupIds.add(int(value))
            except ValueError:
                continue
        elif ruleType == "username":
            lowered = value.lower()
            usernames.append(lowered)
            note = str(rule.get("note") or "").strip()
            if note:
                usernameNotes[lowered] = note
            severity = _ruleSeverity(rule)
            if severity > 0:
                usernameSeverities[lowered] = severity
        elif ruleType == "roblox_user":
            try:
                robloxUserId = int(value)
            except ValueError:
                continue
            robloxUserIds.add(robloxUserId)
            note = str(rule.get("note") or "").strip()
            if note:
                robloxUserNotes[robloxUserId] = note
            severity = _ruleSeverity(rule)
            if severity > 0:
                robloxUserSeverities[robloxUserId] = severity
        elif ruleType == "watchlist":
            try:
                robloxUserId = int(value)
            except ValueError:
                continue
            watchlistUserIds.add(robloxUserId)
            note = str(rule.get("note") or "").strip()
            if note:
                watchlistNotes[robloxUserId] = note
            severity = _ruleSeverity(rule)
            if severity > 0:
                watchlistSeverities[robloxUserId] = severity
        elif ruleType == "banned_user":
            try:
                robloxUserId = int(value)
            except ValueError:
                continue
            bannedUserIds.add(robloxUserId)
            note = str(rule.get("note") or "").strip()
            if note:
                bannedUserNotes[robloxUserId] = note
            severity = _ruleSeverity(rule)
            if severity > 0:
                bannedUserSeverities[robloxUserId] = severity
        elif ruleType == "keyword":
            lowered = value.lower()
            groupKeywords.append(lowered)
            itemKeywords.append(lowered)
            gameKeywords.append(lowered)
        elif ruleType in {"group_keyword", "group-keyword"}:
            groupKeywords.append(value.lower())
        elif ruleType in {"item_keyword", "item-keyword"}:
            itemKeywords.append(value.lower())
        elif ruleType in {"game_keyword", "game-keyword"}:
            gameKeywords.append(value.lower())
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
        elif ruleType == "game":
            try:
                gameIds.add(int(value))
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

    return FlagRules(
        groupIds=groupIds,
        usernames=usernames,
        usernameNotes=usernameNotes,
        usernameSeverities=usernameSeverities,
        robloxUserIds=robloxUserIds,
        robloxUserNotes=robloxUserNotes,
        robloxUserSeverities=robloxUserSeverities,
        watchlistUserIds=watchlistUserIds,
        watchlistNotes=watchlistNotes,
        watchlistSeverities=watchlistSeverities,
        bannedUserIds=bannedUserIds,
        bannedUserNotes=bannedUserNotes,
        bannedUserSeverities=bannedUserSeverities,
        groupKeywords=groupKeywords,
        itemKeywords=itemKeywords,
        itemIds=itemIds,
        creatorIds=creatorIds,
        gameIds=gameIds,
        gameKeywords=gameKeywords,
        badgeIds=badgeIds,
        badgeNotes=badgeNotes,
        accountAgeDays=max(0, accountAgeDays),
    )


def _groupKey(group: dict[str, Any]) -> tuple[Optional[int], str]:
    rawId = group.get("id")
    try:
        groupId = int(rawId) if rawId is not None else None
    except (TypeError, ValueError):
        groupId = None
    return groupId, str(group.get("name") or "").strip().lower()


def _buildGroupSummary(groups: list[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    baseRank = 0
    elevatedRank = 0
    ownerRank = 0
    namedRole = 0
    verifiedGroups = 0
    publicEntryGroups = 0
    lockedGroups = 0
    knownMemberCountGroups = 0
    unknownMemberCountGroups = 0
    smallGroups = 0
    midSizeGroups = 0
    largeGroups = 0
    veryLargeGroups = 0
    ranks: list[int] = []
    memberCounts: list[int] = []
    roleNames: set[str] = set()

    def _safeInt(value: Any) -> Optional[int]:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            return None
        return None

    for group in list(groups or []):
        if not isinstance(group, dict):
            continue
        total += 1
        roleName = str(group.get("role") or "").strip().lower()
        rank = _safeInt(group.get("rank")) or 0
        if rank <= 0 and roleName == "owner":
            rank = 255
        ranks.append(rank)
        if roleName:
            roleNames.add(roleName)
        if rank <= 5:
            baseRank += 1
        if rank >= 100:
            elevatedRank += 1
        if rank >= 255 or roleName == "owner":
            ownerRank += 1
        if roleName and roleName not in {"member", "guest", "rank 1"}:
            namedRole += 1
        if group.get("hasVerifiedBadge") is True:
            verifiedGroups += 1
        if group.get("publicEntryAllowed") is True:
            publicEntryGroups += 1
        if group.get("isLocked") is True:
            lockedGroups += 1
        memberCount = _safeInt(group.get("memberCount"))
        if memberCount is None or memberCount < 0:
            unknownMemberCountGroups += 1
            continue
        knownMemberCountGroups += 1
        memberCounts.append(memberCount)
        if memberCount >= 100_000:
            veryLargeGroups += 1
            largeGroups += 1
        elif memberCount >= 10_000:
            largeGroups += 1
        elif memberCount < 100:
            smallGroups += 1
        else:
            midSizeGroups += 1

    sortedRanks = sorted(ranks)
    sortedMemberCounts = sorted(memberCounts)
    medianRank = sortedRanks[len(sortedRanks) // 2] if sortedRanks else 0
    averageRank = round(sum(ranks) / len(ranks), 1) if ranks else 0.0
    averageMemberCount = int(sum(memberCounts) / len(memberCounts)) if memberCounts else 0
    baseRatio = (baseRank / total) if total else 0.0
    elevatedRatio = (elevatedRank / total) if total else 0.0
    smallRatio = (smallGroups / knownMemberCountGroups) if knownMemberCountGroups else 0.0
    largeRatio = (largeGroups / knownMemberCountGroups) if knownMemberCountGroups else 0.0
    verifiedRatio = (verifiedGroups / total) if total else 0.0
    return {
        "totalGroups": total,
        "baseRankGroups": baseRank,
        "elevatedRankGroups": elevatedRank,
        "ownerRankGroups": ownerRank,
        "namedRoleGroups": namedRole,
        "baseRankRatio": round(baseRatio, 3),
        "elevatedRankRatio": round(elevatedRatio, 3),
        "knownMemberCountGroups": knownMemberCountGroups,
        "unknownMemberCountGroups": unknownMemberCountGroups,
        "smallGroups": smallGroups,
        "midSizeGroups": midSizeGroups,
        "largeGroups": largeGroups,
        "veryLargeGroups": veryLargeGroups,
        "smallGroupRatio": round(smallRatio, 3),
        "largeGroupRatio": round(largeRatio, 3),
        "verifiedGroups": verifiedGroups,
        "verifiedGroupRatio": round(verifiedRatio, 3),
        "publicEntryGroups": publicEntryGroups,
        "lockedGroups": lockedGroups,
        "distinctRoleNames": len(roleNames),
        "highestRank": max(ranks) if ranks else 0,
        "medianRank": medianRank,
        "averageRank": averageRank,
        "smallestKnownGroupMemberCount": sortedMemberCounts[0] if sortedMemberCounts else 0,
        "largestGroupMemberCount": sortedMemberCounts[-1] if sortedMemberCounts else 0,
        "averageKnownMemberCount": averageMemberCount,
    }


def _badgeIdFromSample(badge: dict[str, Any]) -> Optional[int]:
    for key in ("id", "badgeId", "badge_id"):
        try:
            value = badge.get(key)
            if value is not None:
                parsed = int(value)
                if parsed > 0:
                    return parsed
        except (TypeError, ValueError):
            continue
    return None


def _mergeBadgeAwardDates(
    badges: list[dict[str, Any]],
    awardRows: list[dict[str, Any]],
) -> None:
    awardDates: dict[int, str] = {}
    awardSources: dict[int, str] = {}
    for row in list(awardRows or []):
        if not isinstance(row, dict):
            continue
        badgeId = _badgeIdFromSample(row)
        awardedDate = row.get("awardedDate") or row.get("awarded_date")
        if badgeId is not None and isinstance(awardedDate, str) and awardedDate.strip():
            awardDates[badgeId] = awardedDate.strip()
            source = row.get("awardedDateSource") or row.get("awarded_date_source")
            if isinstance(source, str) and source.strip():
                awardSources[badgeId] = source.strip()
    if not awardDates:
        return
    for badge in list(badges or []):
        if not isinstance(badge, dict):
            continue
        badgeId = _badgeIdFromSample(badge)
        if badgeId is not None and badgeId in awardDates:
            badge["awardedDate"] = awardDates[badgeId]
            badge["awardedDateSource"] = awardSources.get(badgeId) or "awarded_dates_endpoint"


def _hasDatedBadges(badges: list[dict[str, Any]]) -> bool:
    for badge in list(badges or []):
        if isinstance(badge, dict) and _parseRobloxDate(badge.get("awardedDate")) is not None:
            return True
    return False


def _buildBadgeTimelineSummary(
    badges: list[dict[str, Any]],
    *,
    awardDateStatus: str = "SKIPPED",
    awardDateError: Optional[str] = None,
    historyComplete: bool | None = None,
    historyNextCursor: Optional[str] = None,
) -> dict[str, Any]:
    sampleSize = 0
    awardedDates: list[datetime] = []
    awardDateSources: dict[str, int] = {}
    for badge in list(badges or []):
        if not isinstance(badge, dict):
            continue
        sampleSize += 1
        awardedAt = _parseRobloxDate(badge.get("awardedDate"))
        if awardedAt is not None:
            awardedDates.append(awardedAt)
            source = str(badge.get("awardedDateSource") or "unknown").strip() or "unknown"
            awardDateSources[source] = awardDateSources.get(source, 0) + 1

    datedBadges = len(awardedDates)
    coverage = (datedBadges / sampleSize) if sampleSize else 0.0
    summary: dict[str, Any] = {
        "sampleSize": sampleSize,
        "datedBadges": datedBadges,
        "awardDateStatus": str(awardDateStatus or "SKIPPED").upper(),
        "awardDateCoverage": round(coverage, 3),
        "awardDateSources": awardDateSources,
        "quality": "unknown",
    }
    if historyComplete is not None:
        summary["historyComplete"] = bool(historyComplete)
    if historyNextCursor:
        summary["historyNextCursor"] = str(historyNextCursor)
    if awardDateError:
        summary["awardDateError"] = str(awardDateError)
    if not awardedDates:
        summary.update(
            {
                "oldestAwardedAt": None,
                "newestAwardedAt": None,
                "spanDays": 0,
                "distinctAwardYears": 0,
                "recent7Days": 0,
                "recent30Days": 0,
                "maxSameDayAwards": 0,
                "maxSameDayRatio": 0.0,
            }
        )
        if sampleSize <= 0:
            summary["quality"] = "none"
        elif summary["awardDateStatus"] == "OK":
            summary["quality"] = "undated"
        return summary

    awardedDates.sort()
    now = datetime.now(timezone.utc)
    oldest = awardedDates[0]
    newest = awardedDates[-1]
    spanDays = max(0, (newest - oldest).days)
    distinctYears = len({date.year for date in awardedDates})
    recent7Days = sum(1 for date in awardedDates if 0 <= (now - date).days <= 7)
    recent30Days = sum(1 for date in awardedDates if 0 <= (now - date).days <= 30)
    dayCounts: dict[str, int] = {}
    for date in awardedDates:
        dayKey = date.date().isoformat()
        dayCounts[dayKey] = dayCounts.get(dayKey, 0) + 1
    maxSameDayAwards = max(dayCounts.values()) if dayCounts else 0
    maxSameDayRatio = (maxSameDayAwards / datedBadges) if datedBadges else 0.0

    if datedBadges >= 75 and spanDays >= 1095 and distinctYears >= 3:
        quality = "multi_year_deep"
    elif datedBadges >= 30 and spanDays >= 365 and distinctYears >= 2:
        quality = "established"
    elif datedBadges >= 20 and spanDays <= 14 and maxSameDayRatio >= 0.6:
        quality = "burst_heavy"
    elif datedBadges <= 3:
        quality = "thin"
    else:
        quality = "normal"

    summary.update(
        {
            "quality": quality,
            "oldestAwardedAt": oldest.isoformat(),
            "newestAwardedAt": newest.isoformat(),
            "spanDays": spanDays,
            "distinctAwardYears": distinctYears,
            "recent7Days": recent7Days,
            "recent30Days": recent30Days,
            "maxSameDayAwards": maxSameDayAwards,
            "maxSameDayRatio": round(maxSameDayRatio, 3),
        }
    )
    return summary


def _directMatchesForReport(report: BgIntelligenceReport, rules: FlagRules) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    if report.robloxUserId:
        try:
            robloxUserId = int(report.robloxUserId)
        except (TypeError, ValueError):
            robloxUserId = 0
        if robloxUserId > 0:
            if robloxUserId in rules.bannedUserIds:
                minimumScore = _directMinimum("banned_user", rules.bannedUserSeverities.get(robloxUserId, 0))
                matches.append(
                    {
                        "type": "banned_user",
                        "value": robloxUserId,
                        "minimumScore": minimumScore,
                        "severity": rules.bannedUserSeverities.get(robloxUserId, 0),
                        "note": rules.bannedUserNotes.get(robloxUserId),
                    }
                )
            if robloxUserId in rules.watchlistUserIds:
                minimumScore = _directMinimum("watchlist", rules.watchlistSeverities.get(robloxUserId, 0))
                matches.append(
                    {
                        "type": "watchlist",
                        "value": robloxUserId,
                        "minimumScore": minimumScore,
                        "severity": rules.watchlistSeverities.get(robloxUserId, 0),
                        "note": rules.watchlistNotes.get(robloxUserId),
                    }
                )
            if robloxUserId in rules.robloxUserIds:
                minimumScore = _directMinimum("roblox_user", rules.robloxUserSeverities.get(robloxUserId, 0))
                matches.append(
                    {
                        "type": "roblox_user",
                        "value": robloxUserId,
                        "minimumScore": minimumScore,
                        "severity": rules.robloxUserSeverities.get(robloxUserId, 0),
                        "note": rules.robloxUserNotes.get(robloxUserId),
                    }
                )

    if report.robloxUsername:
        username = str(report.robloxUsername).strip().lower()
        if username in rules.usernames:
            minimumScore = _directMinimum("username", rules.usernameSeverities.get(username, 0))
            matches.append(
                {
                    "type": "username",
                    "value": report.robloxUsername,
                    "minimumScore": minimumScore,
                    "severity": rules.usernameSeverities.get(username, 0),
                    "note": rules.usernameNotes.get(username),
                }
            )
    previousUsernames = [
        str(value).strip()
        for value in list(report.previousRobloxUsernames or [])
        if str(value).strip()
    ]
    for previousUsername in previousUsernames:
        username = previousUsername.lower()
        if username not in rules.usernames:
            continue
        configuredSeverity = rules.usernameSeverities.get(username, 0)
        minimumScore = max(40, min(75, configuredSeverity or 60))
        matches.append(
            {
                "type": "previous_username",
                "value": previousUsername,
                "minimumScore": minimumScore,
                "severity": configuredSeverity,
                "note": rules.usernameNotes.get(username),
            }
        )
    return matches


def _configStringList(value: Any, fallback: list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, str):
        rawValues: list[Any] = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        rawValues = list(value)
    else:
        rawValues = list(fallback)
    cleaned: list[str] = []
    seen: set[str] = set()
    for rawValue in rawValues:
        text = str(rawValue or "").strip()
        key = characters.normalized_username_key(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    return cleaned


def _altDetectorWords(configModule: Any = config) -> list[str]:
    return _configStringList(
        getattr(configModule, "bgIntelligenceKnownMemberAltWords", None),
        characters.ALT_ACCOUNT_WORDS,
    )


def _safeIntValue(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safeFloatValue(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _positiveConfigInt(value: Any, default: int) -> int:
    parsed = _safeIntValue(value)
    return parsed if parsed > 0 else int(default)


def _sqlPlaceholders(values: list[Any] | tuple[Any, ...] | set[Any]) -> str:
    return ",".join("?" for _ in values)


def _altStrengthRank(strength: str) -> int:
    normalized = str(strength or "").strip().lower()
    return {
        "cleared": 0,
        "data": 1,
        "weak": 2,
        "moderate": 3,
        "strong": 4,
        "confirmed": 5,
    }.get(normalized, 2)


def _addAltEvidence(
    matches: list[dict[str, Any]],
    evidence: dict[str, Any],
    *,
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> bool:
    evidenceType = str(evidence.get("evidenceType") or "alt_signal").strip()
    candidate = characters.normalized_username_key(str(evidence.get("candidateUsername") or ""))
    known = characters.normalized_username_key(str(evidence.get("knownRobloxUsername") or ""))
    knownDiscordId = _safeIntValue(evidence.get("knownDiscordUserId"))
    knownRobloxId = _safeIntValue(evidence.get("knownRobloxUserId"))
    key = (evidenceType, candidate, known, knownDiscordId, knownRobloxId)
    if key in seen:
        return len(matches) >= limit
    seen.add(key)
    evidence["strength"] = str(evidence.get("strength") or "weak").strip().lower()
    matches.append(evidence)
    matches.sort(
        key=lambda item: (
            _altStrengthRank(str(item.get("strength") or "")),
            _safeIntValue(item.get("sharedGroupCount")),
        ),
        reverse=True,
    )
    if len(matches) > limit:
        del matches[limit:]
    return len(matches) >= limit


def _altEndpointKey(discordUserId: Any, robloxUserId: Any) -> tuple[int, int]:
    return _safeIntValue(discordUserId), _safeIntValue(robloxUserId)


def _knownMemberLabel(row: dict[str, Any]) -> str:
    pieces = [
        str(row.get("rank") or "").strip(),
        str(row.get("department") or "").strip(),
        str(row.get("sectionLabel") or "").strip(),
    ]
    return " / ".join(piece for piece in pieces if piece)


async def _loadKnownMemberAltCandidates(*, limit: int) -> list[dict[str, Any]]:
    safeLimit = max(1, int(limit or 5000))
    rows: list[dict[str, Any]] = []

    orbatRows = await fetchAll(
        """
        SELECT discordUserId, robloxUserId, robloxUsername,
               sheetKey, rank, department, sectionLabel,
               'orbat_member_mirror' AS source
        FROM orbat_member_mirror
        WHERE active = 1
          AND robloxUsername IS NOT NULL
          AND trim(robloxUsername) <> ''
        ORDER BY datetime(lastSyncedAt) DESC, sheetKey, rowNumber
        LIMIT ?
        """,
        (safeLimit,),
    )
    rows.extend(dict(row) for row in orbatRows)

    remaining = max(0, safeLimit - len(rows))
    if remaining > 0:
        approvedRows = await fetchAll(
            """
            SELECT userId AS discordUserId, robloxUserId, robloxUsername,
                   '' AS sheetKey, '' AS rank, '' AS department, '' AS sectionLabel,
                   'approved_bg_queue' AS source
            FROM attendees
            WHERE UPPER(bgStatus) = 'APPROVED'
              AND robloxUsername IS NOT NULL
              AND trim(robloxUsername) <> ''
            ORDER BY rowid DESC
            LIMIT ?
            """,
            (remaining,),
        )
        rows.extend(dict(row) for row in approvedRows)

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for row in rows:
        username = str(row.get("robloxUsername") or "").strip()
        usernameKey = characters.normalized_username_key(username)
        if not usernameKey:
            continue
        key = (
            _safeIntValue(row.get("discordUserId")),
            _safeIntValue(row.get("robloxUserId")),
            usernameKey,
        )
        if key in seen:
            continue
        seen.add(key)
        row["robloxUsername"] = username
        row["knownMemberLabel"] = _knownMemberLabel(row)
        deduped.append(row)
    return deduped


def _altCandidateUsernames(report: BgIntelligenceReport) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    current = str(report.robloxUsername or "").strip()
    if current:
        candidates.append(("current_username", current))
    seen = {characters.normalized_username_key(current)} if current else set()
    for value in list(report.previousRobloxUsernames or []):
        username = str(value or "").strip()
        usernameKey = characters.normalized_username_key(username)
        if not username or not usernameKey or usernameKey in seen:
            continue
        seen.add(usernameKey)
        candidates.append(("previous_username", username))
    return candidates


async def _loadAltLinkRows(report: BgIntelligenceReport, *, guildId: int) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    discordUserId = _safeIntValue(report.discordUserId)
    robloxUserId = _safeIntValue(report.robloxUserId)
    if discordUserId > 0:
        clauses.append("(sourceDiscordUserId = ? OR targetDiscordUserId = ?)")
        params.extend([discordUserId, discordUserId])
    if robloxUserId > 0:
        clauses.append("(sourceRobloxUserId = ? OR targetRobloxUserId = ?)")
        params.extend([robloxUserId, robloxUserId])
    if not clauses:
        return []
    guildParams = [0]
    if int(guildId or 0) > 0:
        guildParams.append(int(guildId))
    return await fetchAll(
        f"""
        SELECT *
        FROM bg_alt_links
        WHERE guildId IN ({_sqlPlaceholders(guildParams)})
          AND ({" OR ".join(clauses)})
        ORDER BY datetime(updatedAt) DESC, linkId DESC
        LIMIT 25
        """,
        tuple(guildParams + params),
    )


def _clearedAltEndpoints(report: BgIntelligenceReport, rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    cleared: set[tuple[int, int]] = set()
    currentDiscordId = _safeIntValue(report.discordUserId)
    currentRobloxId = _safeIntValue(report.robloxUserId)
    for row in rows:
        status = str(row.get("status") or "").strip().upper()
        if status not in {"CLEARED", "NOT_ALT", "NOT AN ALT"}:
            continue
        source = _altEndpointKey(row.get("sourceDiscordUserId"), row.get("sourceRobloxUserId"))
        target = _altEndpointKey(row.get("targetDiscordUserId"), row.get("targetRobloxUserId"))
        sourceMatches = (
            (currentDiscordId > 0 and source[0] == currentDiscordId)
            or (currentRobloxId > 0 and source[1] == currentRobloxId)
        )
        targetMatches = (
            (currentDiscordId > 0 and target[0] == currentDiscordId)
            or (currentRobloxId > 0 and target[1] == currentRobloxId)
        )
        if sourceMatches:
            cleared.add(target)
        if targetMatches:
            cleared.add(source)
    return cleared


def _pairWasCleared(known: dict[str, Any], cleared: set[tuple[int, int]]) -> bool:
    knownKey = _altEndpointKey(known.get("discordUserId"), known.get("robloxUserId"))
    if knownKey in cleared:
        return True
    knownDiscordId, knownRobloxId = knownKey
    return any(
        (knownDiscordId > 0 and key[0] == knownDiscordId)
        or (knownRobloxId > 0 and key[1] == knownRobloxId)
        for key in cleared
    )


def _addAltLinkEvidence(
    report: BgIntelligenceReport,
    *,
    rows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> None:
    currentDiscordId = _safeIntValue(report.discordUserId)
    currentRobloxId = _safeIntValue(report.robloxUserId)
    for row in rows:
        status = str(row.get("status") or "").strip().upper()
        if status in {"", "CLEARED", "NOT_ALT", "NOT AN ALT"}:
            if status in {"CLEARED", "NOT_ALT", "NOT AN ALT"}:
                source = _altEndpointKey(row.get("sourceDiscordUserId"), row.get("sourceRobloxUserId"))
                target = _altEndpointKey(row.get("targetDiscordUserId"), row.get("targetRobloxUserId"))
                other = target if (
                    (currentDiscordId > 0 and source[0] == currentDiscordId)
                    or (currentRobloxId > 0 and source[1] == currentRobloxId)
                ) else source
                _addAltEvidence(
                    matches,
                    {
                        "evidenceType": "staff_alt_link",
                        "strength": "cleared",
                        "knownDiscordUserId": other[0] or None,
                        "knownRobloxUserId": other[1] or None,
                        "knownRobloxUsername": row.get("targetRobloxUsername") or row.get("sourceRobloxUsername") or None,
                        "source": "bg_alt_links",
                        "reason": "Staff previously marked this relationship as not an alt.",
                        "note": row.get("note") or None,
                    },
                    seen=seen,
                    limit=limit,
                )
            continue
        source = _altEndpointKey(row.get("sourceDiscordUserId"), row.get("sourceRobloxUserId"))
        target = _altEndpointKey(row.get("targetDiscordUserId"), row.get("targetRobloxUserId"))
        sourceMatches = (
            (currentDiscordId > 0 and source[0] == currentDiscordId)
            or (currentRobloxId > 0 and source[1] == currentRobloxId)
        )
        other = target if sourceMatches else source
        otherUsername = row.get("targetRobloxUsername") if sourceMatches else row.get("sourceRobloxUsername")
        label = "confirmed" if status == "CONFIRMED" else "strong"
        _addAltEvidence(
            matches,
            {
                "evidenceType": "staff_alt_link",
                "strength": label,
                "knownDiscordUserId": other[0] or None,
                "knownRobloxUserId": other[1] or None,
                "knownRobloxUsername": otherUsername or None,
                "source": "bg_alt_links",
                "reason": f"Staff alt-link registry status: {status}.",
                "note": row.get("note") or None,
            },
            seen=seen,
            limit=limit,
        )


async def _addIdentityReuseEvidence(
    report: BgIntelligenceReport,
    *,
    knownRows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> None:
    currentDiscordId = _safeIntValue(report.discordUserId)
    currentRobloxId = _safeIntValue(report.robloxUserId)
    if currentRobloxId > 0:
        for known in knownRows:
            knownRobloxId = _safeIntValue(known.get("robloxUserId"))
            knownDiscordId = _safeIntValue(known.get("discordUserId"))
            if knownRobloxId != currentRobloxId or knownDiscordId <= 0 or knownDiscordId == currentDiscordId:
                continue
            _addAltEvidence(
                matches,
                {
                    "evidenceType": "same_roblox_id",
                    "strength": "strong",
                    "knownDiscordUserId": knownDiscordId,
                    "knownRobloxUserId": knownRobloxId,
                    "knownRobloxUsername": known.get("robloxUsername"),
                    "source": known.get("source") or "known_member",
                    "reason": "The same Roblox account is already tied to a different known Discord member.",
                    "knownMemberLabel": known.get("knownMemberLabel") or None,
                },
                seen=seen,
                limit=limit,
            )

        identityLinkRows = await fetchAll(
            """
            SELECT discordUserId, robloxUserId, robloxUsername, source, updatedAt AS lastSeenAt
            FROM roblox_identity_links
            WHERE robloxUserId = ?
              AND discordUserId > 0
              AND discordUserId <> ?
            ORDER BY datetime(updatedAt) DESC
            LIMIT 8
            """,
            (currentRobloxId, currentDiscordId),
        )
        for row in identityLinkRows:
            _addAltEvidence(
                matches,
                {
                    "evidenceType": "same_roblox_id",
                    "strength": "strong",
                    "knownDiscordUserId": row.get("discordUserId"),
                    "knownRobloxUserId": row.get("robloxUserId"),
                    "knownRobloxUsername": row.get("robloxUsername"),
                    "source": row.get("source") or "identity_links",
                    "reason": "Stored identity links tie this Roblox account to another Discord user.",
                    "lastSeenAt": row.get("lastSeenAt"),
                },
                seen=seen,
                limit=limit,
            )

        historyRows = await fetchAll(
            """
            SELECT discordUserId, robloxUserId, robloxUsername, source, MAX(createdAt) AS lastSeenAt
            FROM bg_identity_history
            WHERE robloxUserId = ?
              AND discordUserId > 0
              AND discordUserId <> ?
            GROUP BY discordUserId, robloxUserId, robloxUsername, source
            ORDER BY datetime(lastSeenAt) DESC
            LIMIT 8
            """,
            (currentRobloxId, currentDiscordId),
        )
        for row in historyRows:
            _addAltEvidence(
                matches,
                {
                    "evidenceType": "same_roblox_id_history",
                    "strength": "strong",
                    "knownDiscordUserId": row.get("discordUserId"),
                    "knownRobloxUserId": row.get("robloxUserId"),
                    "knownRobloxUsername": row.get("robloxUsername"),
                    "source": row.get("source") or "identity_history",
                    "reason": "Local history saw this Roblox account attached to another Discord user.",
                    "lastSeenAt": row.get("lastSeenAt"),
                },
                seen=seen,
                limit=limit,
            )

    if currentDiscordId <= 0 or currentRobloxId <= 0:
        return
    rows = await fetchAll(
        """
        SELECT robloxUserId, robloxUsername, source, MAX(createdAt) AS lastSeenAt
        FROM bg_identity_history
        WHERE discordUserId = ?
          AND robloxUserId IS NOT NULL
          AND robloxUserId > 0
          AND robloxUserId <> ?
        GROUP BY robloxUserId, robloxUsername, source
        ORDER BY datetime(lastSeenAt) DESC
        LIMIT 6
        """,
        (currentDiscordId, currentRobloxId),
    )
    for row in rows:
        _addAltEvidence(
            matches,
            {
                "evidenceType": "discord_identity_cycle",
                "strength": "moderate",
                "knownDiscordUserId": currentDiscordId,
                "knownRobloxUserId": row.get("robloxUserId"),
                "knownRobloxUsername": row.get("robloxUsername"),
                "source": row.get("source") or "identity_history",
                "reason": "This Discord user has previously been seen with another Roblox account.",
                "lastSeenAt": row.get("lastSeenAt"),
            },
            seen=seen,
            limit=limit,
        )


def _nameSimilarityReason(
    candidateUsername: str,
    knownUsername: str,
    *,
    altWords: list[str],
    fuzzyEnabled: bool,
    fuzzyMinSimilarity: float,
    fuzzyMinLength: int,
) -> tuple[str | None, str, float | None]:
    candidateKey = characters.normalized_username_key(candidateUsername)
    knownKey = characters.normalized_username_key(knownUsername)
    if not candidateKey or not knownKey:
        return None, "weak", None
    if candidateKey == knownKey and str(candidateUsername or "").strip().lower() != str(knownUsername or "").strip().lower():
        return "known member username with separator or case changes", "weak", 1.0
    reason = characters.username_alt_match_reason(candidateUsername, knownUsername, altWords=altWords)
    if reason:
        strength = "moderate" if "marker" in reason or "alternate" in reason else "weak"
        return reason, strength, None
    if not fuzzyEnabled:
        return None, "weak", None
    minLength = max(3, int(fuzzyMinLength or 5))
    if len(candidateKey) < minLength or len(knownKey) < minLength:
        return None, "weak", None
    if abs(len(candidateKey) - len(knownKey)) > 3:
        return None, "weak", None
    ratio = difflib.SequenceMatcher(None, candidateKey, knownKey).ratio()
    if ratio < fuzzyMinSimilarity:
        return None, "weak", ratio
    strength = "moderate" if ratio >= 0.95 else "weak"
    return f"known member username is {ratio:.0%} similar after normalization", strength, ratio


def _addNameVariantEvidence(
    report: BgIntelligenceReport,
    *,
    knownRows: list[dict[str, Any]],
    clearedPairs: set[tuple[int, int]],
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
    configModule: Any = config,
) -> None:
    altWords = _altDetectorWords(configModule)
    fuzzyEnabled = bool(getattr(configModule, "bgIntelligenceKnownMemberAltFuzzyEnabled", True))
    fuzzyMinSimilarity = max(
        0.75,
        min(0.99, _safeFloatValue(getattr(configModule, "bgIntelligenceKnownMemberAltFuzzyMinSimilarity", 0.9), 0.9)),
    )
    fuzzyMinLength = _positiveConfigInt(getattr(configModule, "bgIntelligenceKnownMemberAltFuzzyMinLength", 5), 5)
    currentDiscordId = _safeIntValue(report.discordUserId)
    currentRobloxId = _safeIntValue(report.robloxUserId)
    for candidateKind, candidateUsername in _altCandidateUsernames(report):
        candidateKey = characters.normalized_username_key(candidateUsername)
        if not candidateKey:
            continue
        for known in knownRows:
            if _pairWasCleared(known, clearedPairs):
                continue
            knownUsername = str(known.get("robloxUsername") or "").strip()
            knownKey = characters.normalized_username_key(knownUsername)
            if not knownKey:
                continue
            knownDiscordId = _safeIntValue(known.get("discordUserId"))
            knownRobloxId = _safeIntValue(known.get("robloxUserId"))
            if currentRobloxId > 0 and knownRobloxId == currentRobloxId:
                continue
            if currentDiscordId > 0 and knownDiscordId == currentDiscordId:
                continue
            if candidateKind == "previous_username" and candidateKey == knownKey:
                reason = "prior Roblox username exactly matches a known member username"
                strength = "moderate"
                ratio = 1.0
            else:
                reason, strength, ratio = _nameSimilarityReason(
                    candidateUsername,
                    knownUsername,
                    altWords=altWords,
                    fuzzyEnabled=fuzzyEnabled,
                    fuzzyMinSimilarity=fuzzyMinSimilarity,
                    fuzzyMinLength=fuzzyMinLength,
                )
            if not reason:
                continue
            _addAltEvidence(
                matches,
                {
                    "evidenceType": "name_variant",
                    "strength": strength,
                    "candidateUsername": candidateUsername,
                    "candidateKind": candidateKind,
                    "knownDiscordUserId": knownDiscordId or None,
                    "knownRobloxUserId": knownRobloxId or None,
                    "knownRobloxUsername": knownUsername,
                    "source": known.get("source") or "known_member",
                    "reason": reason,
                    "similarity": round(float(ratio), 3) if ratio is not None else None,
                    "knownMemberLabel": known.get("knownMemberLabel") or None,
                },
                seen=seen,
                limit=limit,
            )


async def _addPreviousUsernameIndexEvidence(
    report: BgIntelligenceReport,
    *,
    knownRows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> None:
    currentRobloxId = _safeIntValue(report.robloxUserId)
    knownByRobloxId = {
        _safeIntValue(row.get("robloxUserId")): row
        for row in knownRows
        if _safeIntValue(row.get("robloxUserId")) > 0
    }
    if not knownByRobloxId:
        return
    candidateKeys = []
    candidateByKey: dict[str, tuple[str, str]] = {}
    for kind, username in _altCandidateUsernames(report):
        key = characters.normalized_username_key(username)
        if not key or key in candidateByKey:
            continue
        candidateKeys.append(key)
        candidateByKey[key] = (kind, username)
    if not candidateKeys:
        return
    rows = await fetchAll(
        f"""
        SELECT robloxUserId, robloxUsername, robloxUsernameKey, usernameKind, source, lastSeenAt
        FROM bg_roblox_username_index
        WHERE robloxUsernameKey IN ({_sqlPlaceholders(candidateKeys)})
          AND robloxUserId <> ?
        ORDER BY datetime(lastSeenAt) DESC
        LIMIT 20
        """,
        tuple(candidateKeys + [currentRobloxId]),
    )
    for row in rows:
        knownRobloxId = _safeIntValue(row.get("robloxUserId"))
        known = knownByRobloxId.get(knownRobloxId)
        if not known:
            continue
        key = str(row.get("robloxUsernameKey") or "")
        candidateKind, candidateUsername = candidateByKey.get(key, ("username", key))
        _addAltEvidence(
            matches,
            {
                "evidenceType": "previous_username_index",
                "strength": "moderate",
                "candidateUsername": candidateUsername,
                "candidateKind": candidateKind,
                "knownDiscordUserId": known.get("discordUserId") or None,
                "knownRobloxUserId": knownRobloxId,
                "knownRobloxUsername": known.get("robloxUsername") or row.get("robloxUsername"),
                "source": row.get("source") or "username_index",
                "reason": f"Username matches a stored {row.get('usernameKind') or 'historical'} username for a known member.",
                "lastSeenAt": row.get("lastSeenAt"),
                "knownMemberLabel": known.get("knownMemberLabel") or None,
            },
            seen=seen,
            limit=limit,
        )


async def _addGroupOverlapEvidence(
    report: BgIntelligenceReport,
    *,
    knownRows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
    configModule: Any = config,
) -> None:
    currentRobloxId = _safeIntValue(report.robloxUserId)
    if currentRobloxId <= 0:
        return
    maxMemberCount = _positiveConfigInt(
        getattr(configModule, "bgIntelligenceKnownMemberAltGroupOverlapMaxMemberCount", 50000),
        50000,
    )
    minShared = max(1, _positiveConfigInt(getattr(configModule, "bgIntelligenceKnownMemberAltGroupOverlapMin", 2), 2))
    currentGroups: dict[int, dict[str, Any]] = {}
    for group in list(report.groups or []):
        if not isinstance(group, dict):
            continue
        groupId = _safeIntValue(group.get("id"))
        if groupId <= 0:
            continue
        memberCount = _safeIntValue(group.get("memberCount"))
        if memberCount > maxMemberCount:
            continue
        currentGroups[groupId] = group
    if len(currentGroups) < minShared:
        return
    knownByRobloxId = {
        _safeIntValue(row.get("robloxUserId")): row
        for row in knownRows
        if _safeIntValue(row.get("robloxUserId")) > 0
    }
    if not knownByRobloxId:
        return
    groupIds = list(currentGroups.keys())[:150]
    rows = await fetchAll(
        f"""
        SELECT robloxUserId, robloxUsername, groupId, groupName, role, rank, memberCount, lastSeenAt
        FROM bg_roblox_group_index
        WHERE groupId IN ({_sqlPlaceholders(groupIds)})
          AND robloxUserId <> ?
        ORDER BY datetime(lastSeenAt) DESC
        LIMIT 250
        """,
        tuple(groupIds + [currentRobloxId]),
    )
    rowsByRobloxId: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        knownRobloxId = _safeIntValue(row.get("robloxUserId"))
        if knownRobloxId not in knownByRobloxId:
            continue
        rowsByRobloxId.setdefault(knownRobloxId, []).append(row)
    for knownRobloxId, sharedRows in rowsByRobloxId.items():
        if len(sharedRows) < minShared:
            continue
        known = knownByRobloxId[knownRobloxId]
        sharedGroups = []
        for row in sharedRows[:8]:
            sharedGroups.append(
                {
                    "groupId": row.get("groupId"),
                    "groupName": row.get("groupName") or currentGroups.get(_safeIntValue(row.get("groupId")), {}).get("name"),
                    "memberCount": row.get("memberCount"),
                }
            )
        strength = "strong" if len(sharedRows) >= max(5, minShared + 3) else "moderate"
        _addAltEvidence(
            matches,
            {
                "evidenceType": "group_overlap",
                "strength": strength,
                "knownDiscordUserId": known.get("discordUserId") or None,
                "knownRobloxUserId": knownRobloxId,
                "knownRobloxUsername": known.get("robloxUsername"),
                "source": "bg_roblox_group_index",
                "reason": f"Shares {len(sharedRows)} lower-noise Roblox group(s) with a known member profile.",
                "sharedGroupCount": len(sharedRows),
                "sharedGroups": sharedGroups,
                "knownMemberLabel": known.get("knownMemberLabel") or None,
            },
            seen=seen,
            limit=limit,
        )


def _addFriendOverlapEvidence(
    report: BgIntelligenceReport,
    *,
    knownRows: list[dict[str, Any]],
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> None:
    friendIds = {_safeIntValue(value) for value in list(report.friendUserIds or [])}
    friendIds.discard(0)
    if not friendIds:
        return
    currentRobloxId = _safeIntValue(report.robloxUserId)
    currentDiscordId = _safeIntValue(report.discordUserId)
    for known in knownRows:
        knownRobloxId = _safeIntValue(known.get("robloxUserId"))
        knownDiscordId = _safeIntValue(known.get("discordUserId"))
        if knownRobloxId <= 0 or knownRobloxId not in friendIds:
            continue
        if knownRobloxId == currentRobloxId or (currentDiscordId > 0 and knownDiscordId == currentDiscordId):
            continue
        _addAltEvidence(
            matches,
            {
                "evidenceType": "known_member_friend",
                "strength": "weak",
                "knownDiscordUserId": knownDiscordId or None,
                "knownRobloxUserId": knownRobloxId,
                "knownRobloxUsername": known.get("robloxUsername"),
                "source": "Roblox friends",
                "reason": "The scanned account is friends with a known member Roblox account.",
                "knownMemberLabel": known.get("knownMemberLabel") or None,
            },
            seen=seen,
            limit=limit,
        )


async def _addRejectionEvasionEvidence(
    *,
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> None:
    linkedDiscordIds = sorted({
        _safeIntValue(match.get("knownDiscordUserId"))
        for match in matches
        if _altStrengthRank(str(match.get("strength") or "")) >= _altStrengthRank("moderate")
    } - {0})
    linkedRobloxIds = sorted({
        _safeIntValue(match.get("knownRobloxUserId"))
        for match in matches
        if _altStrengthRank(str(match.get("strength") or "")) >= _altStrengthRank("moderate")
    } - {0})
    clauses: list[str] = []
    params: list[Any] = []
    if linkedDiscordIds:
        clauses.append(f"userId IN ({_sqlPlaceholders(linkedDiscordIds)})")
        params.extend(linkedDiscordIds)
    if linkedRobloxIds:
        clauses.append(f"robloxUserId IN ({_sqlPlaceholders(linkedRobloxIds)})")
        params.extend(linkedRobloxIds)
    if not clauses:
        return
    rows = await fetchAll(
        f"""
        SELECT userId, robloxUserId, robloxUsername, COUNT(*) AS total
        FROM attendees
        WHERE UPPER(bgStatus) = 'REJECTED'
          AND ({" OR ".join(clauses)})
        GROUP BY userId, robloxUserId, robloxUsername
        ORDER BY total DESC
        LIMIT 8
        """,
        tuple(params),
    )
    for row in rows:
        _addAltEvidence(
            matches,
            {
                "evidenceType": "rejection_evasion",
                "strength": "strong",
                "knownDiscordUserId": row.get("userId") or None,
                "knownRobloxUserId": row.get("robloxUserId") or None,
                "knownRobloxUsername": row.get("robloxUsername") or None,
                "source": "Jane BG queue",
                "reason": f"Alt-linked identity has prior BG queue rejection(s): {int(row.get('total') or 0)}.",
            },
            seen=seen,
            limit=limit,
        )


def _addNewAccountClusterEvidence(
    report: BgIntelligenceReport,
    *,
    matches: list[dict[str, Any]],
    seen: set[tuple[str, str, str, int, int]],
    limit: int,
) -> None:
    ageDays = report.robloxAgeDays
    try:
        ageDaysInt = int(ageDays) if ageDays is not None else None
    except (TypeError, ValueError):
        ageDaysInt = None
    if ageDaysInt is None or ageDaysInt > 30:
        return
    support = [
        match
        for match in matches
        if str(match.get("strength") or "").lower() not in {"cleared", "data", "weak"}
        and str(match.get("evidenceType") or "") != "new_account_cluster"
    ]
    weakSupport = [
        match
        for match in matches
        if str(match.get("strength") or "").lower() == "weak"
        and str(match.get("evidenceType") or "") != "new_account_cluster"
    ]
    if not support and len(weakSupport) < 2:
        return
    strength = "strong" if ageDaysInt <= 7 and len(support) >= 2 else "moderate"
    _addAltEvidence(
        matches,
        {
            "evidenceType": "new_account_cluster",
            "strength": strength,
            "source": "Jane correlation",
            "reason": f"New Roblox account ({ageDaysInt} day(s)) also has alt-correlation evidence.",
        },
        seen=seen,
        limit=limit,
    )


async def _detectKnownMemberAltMatches(
    report: BgIntelligenceReport,
    *,
    guildId: int = 0,
    configModule: Any = config,
) -> None:
    report.altMatches = []
    if not bool(getattr(configModule, "bgIntelligenceKnownMemberAltDetectionEnabled", True)):
        report.altScanStatus = "SKIPPED"
        return

    if not _altCandidateUsernames(report) and not report.robloxUserId and not report.discordUserId:
        report.altScanStatus = "SKIPPED"
        return

    matchLimit = max(1, _positiveConfigInt(getattr(configModule, "bgIntelligenceKnownMemberAltMatchLimit", 10), 10))
    candidateLimit = max(
        matchLimit,
        _positiveConfigInt(getattr(configModule, "bgIntelligenceKnownMemberAltCandidateLimit", 5000), 5000),
    )
    knownRows = await _loadKnownMemberAltCandidates(limit=candidateLimit)
    matches: list[dict[str, Any]] = []
    seenMatches: set[tuple[str, str, str, int, int]] = set()
    altLinkRows = await _loadAltLinkRows(report, guildId=int(guildId or 0))
    clearedPairs = _clearedAltEndpoints(report, altLinkRows)

    _addAltLinkEvidence(report, rows=altLinkRows, matches=matches, seen=seenMatches, limit=matchLimit)
    await _addIdentityReuseEvidence(report, knownRows=knownRows, matches=matches, seen=seenMatches, limit=matchLimit)
    _addNameVariantEvidence(
        report,
        knownRows=knownRows,
        clearedPairs=clearedPairs,
        matches=matches,
        seen=seenMatches,
        limit=matchLimit,
        configModule=configModule,
    )
    await _addPreviousUsernameIndexEvidence(
        report,
        knownRows=knownRows,
        matches=matches,
        seen=seenMatches,
        limit=matchLimit,
    )
    _addFriendOverlapEvidence(report, knownRows=knownRows, matches=matches, seen=seenMatches, limit=matchLimit)
    await _addGroupOverlapEvidence(
        report,
        knownRows=knownRows,
        matches=matches,
        seen=seenMatches,
        limit=matchLimit,
        configModule=configModule,
    )
    await _addRejectionEvasionEvidence(matches=matches, seen=seenMatches, limit=matchLimit)
    _addNewAccountClusterEvidence(report, matches=matches, seen=seenMatches, limit=matchLimit)

    report.altScanStatus = "OK"
    report.altMatches = matches


async def _safeDetectKnownMemberAltMatches(
    report: BgIntelligenceReport,
    *,
    guildId: int = 0,
    configModule: Any = config,
) -> None:
    try:
        await _detectKnownMemberAltMatches(report, guildId=int(guildId or 0), configModule=configModule)
    except Exception as exc:
        log.exception("BG intelligence known-member alt scan failed unexpectedly.")
        report.altScanStatus = "ERROR"
        report.altScanError = _unexpectedScanError("Known-member alt scan", exc)
        report.altMatches = []


def _analyzeGroups(
    *,
    groups: list[dict[str, Any]],
    robloxUsername: Optional[str],
    ageDays: Optional[int],
    created: Optional[str],
    rules: FlagRules,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    flaggedGroups: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []

    if ageDays is not None and rules.accountAgeDays > 0 and ageDays < rules.accountAgeDays:
        matches.append(
            {
                "type": "accountAge",
                "value": f"{ageDays} days",
                "created": created,
                "thresholdDays": rules.accountAgeDays,
            }
        )

    for group in groups:
        groupId, groupNameLower = _groupKey(group)
        matchedGroup = False
        if groupId is not None and groupId in rules.groupIds:
            matchedGroup = True
        for keyword in rules.groupKeywords:
            if keyword and groupNameLower and keyword in groupNameLower:
                matchedGroup = True
                matches.append(
                    {
                        "type": "keyword",
                        "value": keyword,
                        "context": "group",
                        "groupId": groupId,
                        "groupName": group.get("name"),
                    }
                )
        if matchedGroup:
            flaggedGroups.append(group)

    if robloxUsername:
        username = robloxUsername.lower()
        for keyword in rules.groupKeywords:
            if keyword and keyword in username:
                matches.append({"type": "keyword", "value": keyword, "context": "username"})

    dedupedGroups: list[dict[str, Any]] = []
    seenGroups: set[tuple[Optional[int], str]] = set()
    for group in flaggedGroups:
        key = _groupKey(group)
        if key in seenGroups:
            continue
        seenGroups.add(key)
        dedupedGroups.append(group)
    return dedupedGroups, matches


def _analyzeFavoriteGames(
    *,
    games: list[dict[str, Any]],
    rules: FlagRules,
) -> list[dict[str, Any]]:
    flaggedGames: list[dict[str, Any]] = []
    seen: set[tuple[Optional[int], Optional[int], str]] = set()
    keywords = [
        str(keyword).strip().lower()
        for keyword in list(rules.gameKeywords or [])
        if str(keyword).strip()
    ]
    for game in games:
        if not isinstance(game, dict):
            continue
        rawUniverseId = game.get("universeId")
        rawPlaceId = game.get("placeId")
        try:
            universeId = int(rawUniverseId) if rawUniverseId is not None else None
        except (TypeError, ValueError):
            universeId = None
        try:
            placeId = int(rawPlaceId) if rawPlaceId is not None else None
        except (TypeError, ValueError):
            placeId = None
        name = str(game.get("name") or "").strip()
        nameLower = name.lower()
        matchType = ""
        matchedKeyword = None
        if universeId is not None and universeId in rules.gameIds:
            matchType = "game"
        elif placeId is not None and placeId in rules.gameIds:
            matchType = "game"
        elif nameLower and keywords:
            for keyword in keywords:
                if keyword in nameLower:
                    matchType = "keyword"
                    matchedKeyword = keyword
                    break
        if not matchType:
            continue
        key = (universeId, placeId, nameLower)
        if key in seen:
            continue
        seen.add(key)
        flaggedGames.append(
            {
                "name": name or None,
                "universeId": universeId,
                "placeId": placeId,
                "matchType": matchType,
                "keyword": matchedKeyword,
            }
        )
    return flaggedGames


async def sendPrivateInventoryNotice(
    member: discord.Member,
    *,
    reviewer: discord.User | discord.Member | None = None,
) -> bool:
    reviewerText = f" by {reviewer.mention}" if reviewer is not None else ""
    content = (
        f"Jane tried to run a Roblox background review{reviewerText}, but your inventory "
        "appears to be private or hidden.\n\n"
        "Please set your Roblox inventory to public, then ask staff to run `/bg-intel` again. "
        "If you already changed it, the next scan should pick it up."
    )
    try:
        await member.send(content)
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def _scanExternalSources(
    report: BgIntelligenceReport,
    *,
    configModule: Any = config,
) -> None:
    externalResult = await externalSources.scanExternalSources(
        discordUserId=int(report.discordUserId or 0),
        robloxUserId=int(report.robloxUserId) if report.robloxUserId else None,
        configModule=configModule,
    )
    report.externalSourceStatus = externalResult.status
    report.externalSourceError = externalResult.error
    report.externalSourceMatches = externalResult.matches
    report.externalSourceDetails = externalResult.details


async def _emitProgress(progressCallback: ProgressCallback | None, status: str) -> None:
    if progressCallback is None:
        return
    try:
        await progressCallback(status)
    except Exception:
        log.debug("BG intelligence progress update failed.", exc_info=True)


def _unexpectedScanError(label: str, exc: Exception) -> str:
    text = str(exc).strip()
    prefix = f"{label} failed unexpectedly"
    if text:
        return f"{prefix}: {text[:260]}"
    return f"{prefix}."


async def _safeScanExternalSources(
    report: BgIntelligenceReport,
    *,
    configModule: Any = config,
) -> None:
    try:
        await _scanExternalSources(report, configModule=configModule)
    except Exception as exc:
        log.exception("BG intelligence external source scan failed unexpectedly.")
        report.externalSourceStatus = "ERROR"
        report.externalSourceError = _unexpectedScanError("External source scan", exc)
        report.externalSourceMatches = []
        report.externalSourceDetails = []


async def _rememberReportRobloxIdentity(report: BgIntelligenceReport, *, guildId: int) -> None:
    discordUserId = int(report.discordUserId or 0)
    robloxUsername = str(report.robloxUsername or "").strip()
    if discordUserId <= 0 or not robloxUsername:
        return
    await robloxUsers.rememberKnownRobloxIdentity(
        discordUserId,
        robloxUsername,
        robloxId=int(report.robloxUserId) if report.robloxUserId else None,
        source=f"bg-intelligence:{str(report.identitySource or 'unknown').strip()[:48]}",
        guildId=int(guildId or 0),
        confidence=95 if report.robloxUserId else 70,
    )


async def _completeReportScan(
    report: BgIntelligenceReport,
    *,
    guildId: int,
    rules: FlagRules,
    member: discord.Member | None = None,
    notifyPrivateInventory: bool = False,
    reviewer: discord.User | discord.Member | None = None,
    configModule: Any = config,
    progressCallback: ProgressCallback | None = None,
) -> BgIntelligenceReport:
    if not report.robloxUserId:
        await _emitProgress(progressCallback, "No Roblox account found; checking external safety records only...")
        report.groupScanStatus = "NO_ROVER"
        report.connectionScanStatus = "NO_ROVER"
        report.friendIdsScanStatus = "NO_ROVER"
        report.inventoryScanStatus = "NO_ROVER"
        report.gamepassScanStatus = "NO_ROVER"
        report.favoriteGameScanStatus = "NO_ROVER"
        report.outfitScanStatus = "NO_ROVER"
        report.badgeHistoryScanStatus = "NO_ROVER"
        report.badgeScanStatus = "NO_ROVER"
        await _safeScanExternalSources(report, configModule=configModule)
        if report.robloxUsername:
            await _emitProgress(progressCallback, "Checking known-member username variants...")
            await _safeDetectKnownMemberAltMatches(report, guildId=int(guildId), configModule=configModule)
        try:
            report.priorReportSummary = await _loadPriorReportSummary(
                guildId=int(guildId),
                targetUserId=int(report.discordUserId or 0),
                robloxUserId=None,
                limit=5,
            )
        except Exception as exc:
            log.exception("BG intelligence prior context load failed unexpectedly.")
            report.priorReportSummary = {"error": _unexpectedScanError("Prior Jane context", exc)}
        try:
            await _rememberReportRobloxIdentity(report, guildId=int(guildId))
        except Exception:
            log.debug("BG intelligence identity memory failed.", exc_info=True)
        return report

    await _emitProgress(progressCallback, "Pulling Roblox profile and safety records...")
    profileTask = asyncio.create_task(robloxProfiles.fetchRobloxUserProfile(int(report.robloxUserId)))
    externalTask = asyncio.create_task(_safeScanExternalSources(report, configModule=configModule))

    try:
        profile = await profileTask
        if not profile.error:
            if not report.robloxUsername and profile.username:
                report.robloxUsername = profile.username
            report.robloxCreated = profile.created
            report.robloxAgeDays = _ageDaysFromCreated(profile.created)
        elif profile.error and not report.roverError:
            report.roverError = profile.error
    except Exception as exc:
        log.exception("BG intelligence profile lookup failed unexpectedly.")
        if not report.roverError:
            report.roverError = _unexpectedScanError("Roblox profile lookup", exc)
    if bool(getattr(configModule, "bgIntelligenceFetchUsernameHistoryEnabled", True)):
        await _emitProgress(progressCallback, "Checking Roblox username history...")
        try:
            usernameHistory = await robloxProfiles.fetchRobloxUsernameHistory(
                int(report.robloxUserId),
                maxNames=int(getattr(configModule, "bgIntelligenceUsernameHistoryMax", 50) or 50),
            )
            if usernameHistory.error:
                report.usernameHistoryScanStatus = "ERROR"
                report.usernameHistoryScanError = usernameHistory.error
            else:
                report.usernameHistoryScanStatus = "OK"
                report.previousRobloxUsernames = usernameHistory.usernames
        except Exception as exc:
            log.exception("BG intelligence username history scan failed unexpectedly.")
            report.usernameHistoryScanStatus = "ERROR"
            report.usernameHistoryScanError = _unexpectedScanError("Username history scan", exc)
    else:
        report.usernameHistoryScanStatus = "SKIPPED"
    report.directMatches = _directMatchesForReport(report, rules)
    await externalTask

    if bool(getattr(configModule, "bgIntelligenceFetchConnectionsEnabled", True)):
        await _emitProgress(progressCallback, "Checking Roblox connection counts...")
        try:
            connectionResult = await robloxProfiles.fetchRobloxConnectionCounts(int(report.robloxUserId))
            report.connectionSummary = {
                "friends": connectionResult.friends,
                "followers": connectionResult.followers,
                "following": connectionResult.following,
            }
            if connectionResult.error and all(
                value is None
                for value in (connectionResult.friends, connectionResult.followers, connectionResult.following)
            ):
                report.connectionScanStatus = "ERROR"
                report.connectionScanError = connectionResult.error
            else:
                report.connectionScanStatus = "OK" if not connectionResult.error else "PARTIAL"
                report.connectionScanError = connectionResult.error
        except Exception as exc:
            log.exception("BG intelligence connection scan failed unexpectedly.")
            report.connectionScanStatus = "ERROR"
            report.connectionScanError = _unexpectedScanError("Connection scan", exc)
    else:
        report.connectionScanStatus = "SKIPPED"

    if bool(getattr(configModule, "bgIntelligenceFetchFriendIdsEnabled", True)):
        await _emitProgress(progressCallback, "Sampling Roblox friends for known-member overlap...")
        try:
            friendResult = await robloxProfiles.fetchRobloxFriendIds(
                int(report.robloxUserId),
                maxFriends=int(getattr(configModule, "bgIntelligenceKnownMemberAltFriendLimit", 200) or 200),
            )
            if friendResult.error:
                report.friendIdsScanStatus = "ERROR"
                report.friendIdsScanError = friendResult.error
                report.friendUserIds = friendResult.friendIds
            else:
                report.friendIdsScanStatus = "OK"
                report.friendUserIds = friendResult.friendIds
        except Exception as exc:
            log.exception("BG intelligence friend-ID scan failed unexpectedly.")
            report.friendIdsScanStatus = "ERROR"
            report.friendIdsScanError = _unexpectedScanError("Friend-ID scan", exc)
            report.friendUserIds = []
    else:
        report.friendIdsScanStatus = "SKIPPED"

    isAdultRoute = report.reviewBucket == adultBgReviewBucket
    if isAdultRoute and bool(getattr(configModule, "bgIntelligenceFetchGroupsEnabled", True)):
        await _emitProgress(progressCallback, "Reading Roblox group membership...")
        try:
            groupResult = await robloxGroups.fetchRobloxGroups(int(report.robloxUserId))
            if groupResult.error:
                report.groupScanStatus = "ERROR"
                report.groupScanError = groupResult.error
            else:
                report.groupScanStatus = "OK"
                report.groups = groupResult.groups
                report.groupSummary = _buildGroupSummary(groupResult.groups)
                flaggedGroups, flagMatches = _analyzeGroups(
                    groups=groupResult.groups,
                    robloxUsername=report.robloxUsername,
                    ageDays=report.robloxAgeDays,
                    created=report.robloxCreated,
                    rules=rules,
                )
                report.flaggedGroups = flaggedGroups
                report.flagMatches = flagMatches
        except Exception as exc:
            log.exception("BG intelligence group scan failed unexpectedly.")
            report.groupScanStatus = "ERROR"
            report.groupScanError = _unexpectedScanError("Group scan", exc)
    elif isAdultRoute:
        report.groupScanStatus = "SKIPPED"

    inventoryEnabled = bool(getattr(configModule, "robloxInventoryScanEnabled", True)) and bool(
        getattr(configModule, "bgIntelligenceFetchInventoryEnabled", True)
    )
    if isAdultRoute and inventoryEnabled:
        await _emitProgress(progressCallback, "Reviewing inventory and item values...")
        try:
            visualReferenceHashes = await flagService.getValidatedItemVisualHashes(
                ensureSynced=True,
            )
        except Exception:
            log.exception("BG intelligence visual reference sync failed unexpectedly.")
            visualReferenceHashes = {}
        try:
            inventoryMaxPages = int(
                getattr(
                    configModule,
                    "bgIntelligenceInventoryMaxPages",
                    getattr(configModule, "robloxInventoryScanMaxPages", 5),
                )
            )
        except (TypeError, ValueError):
            inventoryMaxPages = 5
        try:
            inventoryResult = await robloxInventory.fetchRobloxInventory(
                int(report.robloxUserId),
                rules.itemIds,
                targetCreatorIds=rules.creatorIds,
                targetKeywords=rules.itemKeywords,
                visualReferenceHashes=visualReferenceHashes,
                maxPages=inventoryMaxPages,
                includeValue=True,
            )
            report.inventorySummary = inventoryResult.summary or {}
            if inventoryResult.error:
                isPrivate = bgScanPipeline.isPrivateInventoryStatus(
                    inventoryResult.status,
                    inventoryResult.error,
                )
                report.inventoryScanStatus = "PRIVATE" if isPrivate else "ERROR"
                report.inventoryScanError = "Inventory is private or hidden." if isPrivate else inventoryResult.error
                if (
                    member is not None
                    and isPrivate
                    and notifyPrivateInventory
                    and bool(getattr(configModule, "bgIntelligencePrivateInventoryDmEnabled", True))
                ):
                    report.privateInventoryDmSent = await sendPrivateInventoryNotice(member, reviewer=reviewer)
            else:
                report.inventoryScanStatus = "OK"
                report.flaggedItems = inventoryResult.items
        except Exception as exc:
            log.exception("BG intelligence inventory scan failed unexpectedly.")
            report.inventoryScanStatus = "ERROR"
            report.inventoryScanError = _unexpectedScanError("Inventory scan", exc)
    elif isAdultRoute:
        report.inventoryScanStatus = "SKIPPED"

    if isAdultRoute and bool(getattr(configModule, "bgIntelligenceFetchGamepassesEnabled", True)):
        await _emitProgress(progressCallback, "Pricing owned gamepasses...")
        try:
            try:
                gamepassMaxPages = int(getattr(configModule, "bgIntelligenceGamepassMaxPages", 0))
            except (TypeError, ValueError):
                gamepassMaxPages = 0
            inventoryGamepassIds = []
            if isinstance(report.inventorySummary, dict):
                inventoryGamepassIds = [
                    int(value)
                    for value in list(report.inventorySummary.get("ownedGamepassIds") or [])
                    if _positiveInt(value) > 0
                ]
            if inventoryGamepassIds:
                gamepassResult = await robloxGamepasses.fetchRobloxGamepassesByIds(
                    inventoryGamepassIds,
                    ownerRobloxUserId=int(report.robloxUserId),
                )
            else:
                gamepassResult = await robloxGamepasses.fetchRobloxUserGamepasses(
                    int(report.robloxUserId),
                    maxPages=gamepassMaxPages,
                )
            report.gamepassSummary = gamepassResult.summary or {}
            report.ownedGamepasses = gamepassResult.gamepasses
            if gamepassResult.error:
                isPrivate = bgScanPipeline.isPrivateInventoryStatus(
                    gamepassResult.status,
                    gamepassResult.error,
                )
                report.gamepassScanStatus = "PRIVATE" if isPrivate else "ERROR"
                report.gamepassScanError = "Gamepass inventory is private or hidden." if isPrivate else gamepassResult.error
            else:
                report.gamepassScanStatus = "OK"
        except Exception as exc:
            log.exception("BG intelligence gamepass scan failed unexpectedly.")
            report.gamepassScanStatus = "ERROR"
            report.gamepassScanError = _unexpectedScanError("Gamepass scan", exc)
    elif isAdultRoute:
        report.gamepassScanStatus = "SKIPPED"

    if isAdultRoute and bool(getattr(configModule, "bgIntelligenceFetchFavoriteGamesEnabled", True)):
        await _emitProgress(progressCallback, "Checking favorite games...")
        try:
            gameResult = await robloxGames.fetchRobloxFavoriteGames(
                int(report.robloxUserId),
                maxGames=int(getattr(configModule, "bgIntelligenceFavoriteGameMax", 25) or 25),
            )
            if gameResult.error:
                report.favoriteGameScanStatus = "ERROR"
                report.favoriteGameScanError = gameResult.error
            else:
                report.favoriteGameScanStatus = "OK"
                report.favoriteGames = gameResult.games
                report.flaggedFavoriteGames = _analyzeFavoriteGames(games=gameResult.games, rules=rules)
        except Exception as exc:
            log.exception("BG intelligence favorite-game scan failed unexpectedly.")
            report.favoriteGameScanStatus = "ERROR"
            report.favoriteGameScanError = _unexpectedScanError("Favorite-game scan", exc)
    elif isAdultRoute:
        report.favoriteGameScanStatus = "SKIPPED"

    outfitEnabled = bool(getattr(configModule, "robloxOutfitScanEnabled", True)) and bool(
        getattr(configModule, "bgIntelligenceFetchOutfitsEnabled", True)
    )
    if isAdultRoute and outfitEnabled:
        await _emitProgress(progressCallback, "Checking saved outfits...")
        try:
            outfitResult = await robloxOutfits.fetchRobloxUserOutfits(
                int(report.robloxUserId),
                maxOutfits=int(getattr(configModule, "bgIntelligenceOutfitMax", 25) or 25),
                maxPages=int(getattr(configModule, "robloxOutfitMaxPages", 20) or 20),
            )
            if outfitResult.error:
                report.outfitScanStatus = "ERROR"
                report.outfitScanError = outfitResult.error
            else:
                report.outfitScanStatus = "OK"
                report.outfits = outfitResult.outfits
        except Exception as exc:
            log.exception("BG intelligence outfit scan failed unexpectedly.")
            report.outfitScanStatus = "ERROR"
            report.outfitScanError = _unexpectedScanError("Outfit scan", exc)
    elif isAdultRoute:
        report.outfitScanStatus = "SKIPPED"

    if bool(getattr(configModule, "bgIntelligenceFetchBadgeHistoryEnabled", True)):
        await _emitProgress(progressCallback, "Collecting the full badge timeline...")
        try:
            try:
                badgeHistoryMaxPages = int(getattr(configModule, "bgIntelligenceBadgeHistoryMaxPages", 0))
            except (TypeError, ValueError):
                badgeHistoryMaxPages = 0
            badgeHistoryResult = await robloxBadges.fetchRobloxUserBadges(
                int(report.robloxUserId),
                limit=int(getattr(configModule, "bgIntelligenceBadgeHistoryPageSize", 100) or 100),
                maxPages=badgeHistoryMaxPages,
            )
            badgeHistoryComplete = not bool(badgeHistoryResult.nextCursor)
            if badgeHistoryResult.error:
                report.badgeHistoryScanStatus = "ERROR"
                report.badgeHistoryScanError = badgeHistoryResult.error
                if badgeHistoryResult.badges:
                    report.badgeHistorySample = badgeHistoryResult.badges
                    report.badgeTimelineSummary = _buildBadgeTimelineSummary(
                        badgeHistoryResult.badges,
                        awardDateStatus="PARTIAL" if _hasDatedBadges(badgeHistoryResult.badges) else "SKIPPED",
                        awardDateError=badgeHistoryResult.error,
                        historyComplete=badgeHistoryComplete,
                        historyNextCursor=badgeHistoryResult.nextCursor,
                    )
            else:
                report.badgeHistoryScanStatus = "OK"
                report.badgeHistorySample = badgeHistoryResult.badges
                badgeIds = {
                    badgeId
                    for badgeId in (_badgeIdFromSample(badge) for badge in badgeHistoryResult.badges)
                    if badgeId is not None
                }
                if badgeIds:
                    awardResult = await robloxBadges.fetchRobloxBadgeAwards(
                        int(report.robloxUserId),
                        badgeIds,
                        batchSize=int(getattr(configModule, "robloxBadgeScanBatchSize", 50) or 50),
                    )
                    if awardResult.error:
                        if awardResult.badges:
                            _mergeBadgeAwardDates(badgeHistoryResult.badges, awardResult.badges)
                        hasTimelineDates = bool(awardResult.badges) or _hasDatedBadges(badgeHistoryResult.badges)
                        report.badgeTimelineSummary = _buildBadgeTimelineSummary(
                            badgeHistoryResult.badges,
                            awardDateStatus="PARTIAL" if hasTimelineDates else "ERROR",
                            awardDateError=awardResult.error,
                            historyComplete=badgeHistoryComplete,
                            historyNextCursor=badgeHistoryResult.nextCursor,
                        )
                    else:
                        _mergeBadgeAwardDates(badgeHistoryResult.badges, awardResult.badges)
                        report.badgeTimelineSummary = _buildBadgeTimelineSummary(
                            badgeHistoryResult.badges,
                            awardDateStatus="OK",
                            historyComplete=badgeHistoryComplete,
                            historyNextCursor=badgeHistoryResult.nextCursor,
                        )
                else:
                    report.badgeTimelineSummary = _buildBadgeTimelineSummary(
                        badgeHistoryResult.badges,
                        awardDateStatus="OK",
                        historyComplete=badgeHistoryComplete,
                        historyNextCursor=badgeHistoryResult.nextCursor,
                    )
        except Exception as exc:
            log.exception("BG intelligence badge history scan failed unexpectedly.")
            report.badgeHistoryScanStatus = "ERROR"
            report.badgeHistoryScanError = _unexpectedScanError("Badge history scan", exc)
            report.badgeTimelineSummary = _buildBadgeTimelineSummary(
                [],
                awardDateStatus="ERROR",
                awardDateError=report.badgeHistoryScanError,
                historyComplete=False,
            )
    else:
        report.badgeHistoryScanStatus = "SKIPPED"
        report.badgeTimelineSummary = _buildBadgeTimelineSummary([], awardDateStatus="SKIPPED")

    badgeEnabled = bool(getattr(configModule, "robloxBadgeScanEnabled", True)) and bool(
        getattr(configModule, "bgIntelligenceFetchBadgesEnabled", True)
    )
    if rules.badgeIds and badgeEnabled:
        try:
            historyComplete = bool((report.badgeTimelineSummary or {}).get("historyComplete"))
            if report.badgeHistoryScanStatus == "OK" and historyComplete and report.badgeHistorySample:
                await _emitProgress(progressCallback, "Checking configured badge records...")
                report.badgeScanStatus = "OK"
                flaggedBadges: list[dict[str, Any]] = []
                for badge in list(report.badgeHistorySample or []):
                    if not isinstance(badge, dict):
                        continue
                    badgeId = _badgeIdFromSample(badge)
                    if badgeId is None or int(badgeId) not in rules.badgeIds:
                        continue
                    entry = {
                        "badgeId": badgeId,
                        "awardedDate": badge.get("awardedDate"),
                    }
                    note = rules.badgeNotes.get(int(badgeId))
                    if note:
                        entry["note"] = note
                    flaggedBadges.append(entry)
                report.flaggedBadges = flaggedBadges
            else:
                await _emitProgress(progressCallback, "Checking configured badge records...")
                badgeResult = await robloxBadges.fetchRobloxBadgeAwards(
                    int(report.robloxUserId),
                    rules.badgeIds,
                    batchSize=int(getattr(configModule, "robloxBadgeScanBatchSize", 50) or 50),
                )
                if badgeResult.error:
                    report.badgeScanStatus = "ERROR"
                    report.badgeScanError = badgeResult.error
                else:
                    report.badgeScanStatus = "OK"
                    flaggedBadges = []
                    for badge in badgeResult.badges:
                        badgeId = badge.get("badgeId")
                        if badgeId is None:
                            continue
                        entry = {
                            "badgeId": badgeId,
                            "awardedDate": badge.get("awardedDate"),
                        }
                        note = rules.badgeNotes.get(int(badgeId))
                        if note:
                            entry["note"] = note
                        flaggedBadges.append(entry)
                    report.flaggedBadges = flaggedBadges
        except Exception as exc:
            log.exception("BG intelligence configured badge scan failed unexpectedly.")
            report.badgeScanStatus = "ERROR"
            report.badgeScanError = _unexpectedScanError("Configured badge scan", exc)
    else:
        report.badgeScanStatus = "SKIPPED"

    await _emitProgress(progressCallback, "Correlating known-member alt evidence...")
    await _safeDetectKnownMemberAltMatches(report, guildId=int(guildId), configModule=configModule)

    await _emitProgress(progressCallback, "Loading recent local BG context...")
    try:
        report.priorReportSummary = await _loadPriorReportSummary(
            guildId=int(guildId),
            targetUserId=int(report.discordUserId or 0),
            robloxUserId=int(report.robloxUserId) if report.robloxUserId else None,
            limit=5,
        )
    except Exception as exc:
        log.exception("BG intelligence prior context load failed unexpectedly.")
        report.priorReportSummary = {"error": _unexpectedScanError("Prior Jane context", exc)}

    try:
        await _rememberReportRobloxIdentity(report, guildId=int(guildId))
    except Exception:
        log.debug("BG intelligence identity memory failed.", exc_info=True)
    return report


async def buildReport(
    member: discord.Member,
    *,
    guild: discord.Guild,
    reviewBucketOverride: str = "auto",
    robloxUserIdOverride: int | None = None,
    robloxUsernameOverride: str | None = None,
    roverGuildId: int | None = None,
    notifyPrivateInventory: bool = False,
    reviewer: discord.User | discord.Member | None = None,
    configModule: Any = config,
    progressCallback: ProgressCallback | None = None,
) -> BgIntelligenceReport:
    await _emitProgress(progressCallback, "Loading scan rules...")
    reviewBucket, reviewBucketSource = await resolveReviewBucket(
        member,
        guildId=int(guild.id),
        reviewBucketOverride=reviewBucketOverride,
        configModule=configModule,
    )
    try:
        rules = await loadFlagRules(configModule=configModule)
    except Exception:
        log.exception("BG intelligence rule load failed unexpectedly.")
        rules = _emptyFlagRules(configModule=configModule)
    report = BgIntelligenceReport(
        discordUserId=int(member.id),
        discordDisplayName=str(member.display_name),
        discordUsername=str(member),
        reviewBucket=reviewBucket,
        reviewBucketSource=reviewBucketSource,
        groups=[],
        flaggedGroups=[],
        flagMatches=[],
        directMatches=[],
        flaggedItems=[],
        ownedGamepasses=[],
        favoriteGames=[],
        flaggedFavoriteGames=[],
        outfits=[],
        flaggedBadges=[],
    )

    await _emitProgress(progressCallback, "Checking RoVer for the linked Roblox account...")
    roverResult, roverLookupGuildId = await _lookupRoverForGuild(
        int(member.id),
        guildId=_positiveInt(roverGuildId) or int(guild.id),
        configModule=configModule,
    )
    roverSource = _roverSourceName(roverLookupGuildId, configModule=configModule)
    manualRobloxUserId = 0
    try:
        manualRobloxUserId = int(robloxUserIdOverride or 0)
    except (TypeError, ValueError):
        manualRobloxUserId = 0
    manualRobloxUsername = str(robloxUsernameOverride or "").strip()
    if manualRobloxUserId > 0:
        report.identitySource = "manual"
        report.robloxUserId = manualRobloxUserId
        if roverResult.robloxId and int(roverResult.robloxId) == manualRobloxUserId:
            report.robloxUsername = roverResult.robloxUsername
        elif roverResult.robloxId:
            report.roverError = f"RoVer on {roverSource} linked to {roverResult.robloxId}; manual override {manualRobloxUserId} used."
        elif roverResult.error:
            report.roverError = f"Manual override used. RoVer on {roverSource} note: {roverResult.error}"
    elif manualRobloxUsername:
        report.identitySource = "manual_username"
        await _emitProgress(progressCallback, "Resolving the Roblox username override...")
        usernameResult = await robloxUsers.fetchRobloxUserByUsername(manualRobloxUsername)
        report.robloxUserId = usernameResult.robloxId
        report.robloxUsername = usernameResult.robloxUsername or manualRobloxUsername
        if roverResult.robloxId:
            report.roverError = f"RoVer on {roverSource} linked to {roverResult.robloxId}; username override {manualRobloxUsername} used."
        elif usernameResult.error:
            report.roverError = usernameResult.error
        elif roverResult.error:
            report.roverError = f"Username override used. RoVer on {roverSource} note: {roverResult.error}"
    else:
        report.identitySource = _roverIdentitySource(
            roverGuildId=roverLookupGuildId,
            scanGuildId=int(guild.id),
            configModule=configModule,
        )
        report.robloxUserId = roverResult.robloxId
        report.robloxUsername = roverResult.robloxUsername
        report.roverError = roverResult.error

    return await _completeReportScan(
        report,
        guildId=int(guild.id),
        rules=rules,
        member=member,
        notifyPrivateInventory=notifyPrivateInventory,
        reviewer=reviewer,
        configModule=configModule,
        progressCallback=progressCallback,
    )


async def buildReportForDiscordId(
    *,
    guild: discord.Guild,
    discordUserId: int,
    displayMember: discord.Member | None = None,
    roverGuildId: int | None = None,
    robloxUsernameOverride: str | None = None,
    reviewBucketOverride: str = "auto",
    configModule: Any = config,
    progressCallback: ProgressCallback | None = None,
) -> BgIntelligenceReport:
    await _emitProgress(progressCallback, "Loading scan rules...")
    normalizedOverride = str(reviewBucketOverride or "auto").strip().lower()
    if normalizedOverride and normalizedOverride != "auto":
        reviewBucket = normalizeBgReviewBucket(normalizedOverride)
        reviewBucketSource = "manual"
    else:
        reviewBucket = adultBgReviewBucket
        reviewBucketSource = "discord_identity_default"

    try:
        rules = await loadFlagRules(configModule=configModule)
    except Exception:
        log.exception("BG intelligence rule load failed unexpectedly.")
        rules = _emptyFlagRules(configModule=configModule)
    cleanDiscordUserId = int(discordUserId or 0)
    displayName = str(getattr(displayMember, "display_name", "") or "").strip()
    displayUsername = str(displayMember) if displayMember is not None else ""
    report = BgIntelligenceReport(
        discordUserId=cleanDiscordUserId,
        discordDisplayName=displayName or (f"Discord ID {cleanDiscordUserId}" if cleanDiscordUserId > 0 else "Discord User"),
        discordUsername=displayUsername,
        reviewBucket=reviewBucket,
        reviewBucketSource=reviewBucketSource,
        groups=[],
        flaggedGroups=[],
        flagMatches=[],
        directMatches=[],
        flaggedItems=[],
        ownedGamepasses=[],
        favoriteGames=[],
        flaggedFavoriteGames=[],
        outfits=[],
        flaggedBadges=[],
    )

    roverResult = None
    roverLookupGuildId = 0
    roverSource = "configured guild"
    if cleanDiscordUserId > 0:
        await _emitProgress(progressCallback, "Checking RoVer for the linked Roblox account...")
        roverResult, roverLookupGuildId = await _lookupRoverForGuild(
            cleanDiscordUserId,
            guildId=_positiveInt(roverGuildId) or int(guild.id),
            configModule=configModule,
        )
        roverSource = _roverSourceName(roverLookupGuildId, configModule=configModule)
    manualRobloxUsername = str(robloxUsernameOverride or "").strip()
    if manualRobloxUsername:
        report.identitySource = "manual_username"
        await _emitProgress(progressCallback, "Resolving the Roblox username override...")
        usernameResult = await robloxUsers.fetchRobloxUserByUsername(manualRobloxUsername)
        report.robloxUserId = usernameResult.robloxId
        report.robloxUsername = usernameResult.robloxUsername or manualRobloxUsername
        if roverResult is not None and roverResult.robloxId:
            report.roverError = f"RoVer on {roverSource} linked to {roverResult.robloxId}; username override {manualRobloxUsername} used."
        elif usernameResult.error:
            report.roverError = usernameResult.error
        elif roverResult is not None and roverResult.error:
            report.roverError = f"Username override used. RoVer on {roverSource} note: {roverResult.error}"
    else:
        report.identitySource = _roverIdentitySource(
            roverGuildId=roverLookupGuildId,
            scanGuildId=int(guild.id),
            configModule=configModule,
        )
        if roverResult is None:
            report.roverError = "No Discord ID supplied."
        else:
            report.robloxUserId = roverResult.robloxId
            report.robloxUsername = roverResult.robloxUsername
            report.roverError = roverResult.error

    return await _completeReportScan(
        report,
        guildId=int(guild.id),
        rules=rules,
        member=None,
        notifyPrivateInventory=False,
        reviewer=None,
        configModule=configModule,
        progressCallback=progressCallback,
    )


async def buildReportForRobloxIdentity(
    *,
    guild: discord.Guild,
    robloxUserId: int | None = None,
    robloxUsername: str | None = None,
    reviewBucketOverride: str = "auto",
    configModule: Any = config,
    progressCallback: ProgressCallback | None = None,
) -> BgIntelligenceReport:
    await _emitProgress(progressCallback, "Loading scan rules...")
    normalizedOverride = str(reviewBucketOverride or "auto").strip().lower()
    if normalizedOverride and normalizedOverride != "auto":
        reviewBucket = normalizeBgReviewBucket(normalizedOverride)
        reviewBucketSource = "manual"
    else:
        reviewBucket = adultBgReviewBucket
        reviewBucketSource = "roblox_identity_default"

    try:
        rules = await loadFlagRules(configModule=configModule)
    except Exception:
        log.exception("BG intelligence rule load failed unexpectedly.")
        rules = _emptyFlagRules(configModule=configModule)
    report = BgIntelligenceReport(
        discordUserId=0,
        discordDisplayName=str(robloxUsername or robloxUserId or "Roblox User"),
        discordUsername="",
        reviewBucket=reviewBucket,
        reviewBucketSource=reviewBucketSource,
        identitySource="manual",
        groups=[],
        flaggedGroups=[],
        flagMatches=[],
        directMatches=[],
        flaggedItems=[],
        ownedGamepasses=[],
        favoriteGames=[],
        flaggedFavoriteGames=[],
        outfits=[],
        flaggedBadges=[],
    )

    manualRobloxUserId = 0
    try:
        manualRobloxUserId = int(robloxUserId or 0)
    except (TypeError, ValueError):
        manualRobloxUserId = 0
    manualRobloxUsername = str(robloxUsername or "").strip()
    if manualRobloxUserId > 0:
        report.robloxUserId = manualRobloxUserId
        report.robloxUsername = manualRobloxUsername or None
    elif manualRobloxUsername:
        report.identitySource = "manual_username"
        await _emitProgress(progressCallback, "Resolving the Roblox username...")
        usernameResult = await robloxUsers.fetchRobloxUserByUsername(manualRobloxUsername)
        report.robloxUserId = usernameResult.robloxId
        report.robloxUsername = usernameResult.robloxUsername or manualRobloxUsername
        report.roverError = usernameResult.error
    else:
        report.roverError = "No Discord member, Discord ID, or Roblox username supplied."

    return await _completeReportScan(
        report,
        guildId=int(guild.id),
        rules=rules,
        member=None,
        notifyPrivateInventory=False,
        reviewer=None,
        configModule=configModule,
        progressCallback=progressCallback,
    )


def _safeJson(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, default=str)
    except Exception:
        return "{}"


async def _loadPriorReportSummary(
    *,
    guildId: int,
    targetUserId: int,
    robloxUserId: int | None,
    limit: int = 5,
) -> dict[str, Any]:
    clauses = ["guildId = ?"]
    params: list[Any] = [int(guildId)]
    targetClauses: list[str] = []
    if int(targetUserId or 0) > 0:
        targetClauses.append("targetUserId = ?")
        params.append(int(targetUserId))
    if robloxUserId is not None and int(robloxUserId or 0) > 0:
        targetClauses.append("robloxUserId = ?")
        params.append(int(robloxUserId))
    if not targetClauses:
        return {
            "totalRecent": 0,
            "rows": [],
            "queueApprovals": 0,
            "queueRejections": 0,
        }

    params.append(max(1, min(int(limit or 5), 10)))
    rows = await fetchAll(
        f"""
        SELECT reportId, targetUserId, robloxUserId, score, band, confidence,
               scored, outcome, hardMinimum, createdAt
        FROM bg_intelligence_report_index
        WHERE {" AND ".join(clauses)} AND ({" OR ".join(targetClauses)})
        ORDER BY datetime(createdAt) DESC, reportId DESC
        LIMIT ?
        """,
        tuple(params),
    )

    queueApprovals = 0
    queueRejections = 0
    queueClauses: list[str] = []
    queueParams: list[Any] = []
    if int(targetUserId or 0) > 0:
        queueClauses.append("userId = ?")
        queueParams.append(int(targetUserId))
    if robloxUserId is not None and int(robloxUserId or 0) > 0:
        queueClauses.append("robloxUserId = ?")
        queueParams.append(int(robloxUserId))
    if queueClauses:
        queueRows = await fetchAll(
            f"""
            SELECT UPPER(bgStatus) AS status, COUNT(*) AS total
            FROM attendees
            WHERE {" OR ".join(queueClauses)}
            GROUP BY UPPER(bgStatus)
            """,
            tuple(queueParams),
        )
        for row in queueRows:
            status = str(row.get("status") or "").upper()
            total = int(row.get("total") or 0)
            if status == "APPROVED":
                queueApprovals += total
            elif status == "REJECTED":
                queueRejections += total

    scoredRows = [row for row in rows if int(row.get("scored", 1) or 0) == 1]
    noScoreRows = [row for row in rows if int(row.get("scored", 1) or 0) == 0]
    highRiskRows = [
        row
        for row in scoredRows
        if int(row.get("score") or 0) >= 60 or str(row.get("band") or "").lower() in {"high risk", "escalate"}
    ]
    escalateRows = [
        row
        for row in scoredRows
        if int(row.get("score") or 0) >= 80 or str(row.get("band") or "").lower() == "escalate"
    ]
    lastRow = rows[0] if rows else {}
    return {
        "totalRecent": len(rows),
        "scoredRecent": len(scoredRows),
        "noScoreRecent": len(noScoreRows),
        "highRiskRecent": len(highRiskRows),
        "escalateRecent": len(escalateRows),
        "lastScore": lastRow.get("score") if lastRow else None,
        "lastBand": lastRow.get("band") if lastRow else None,
        "lastOutcome": lastRow.get("outcome") if lastRow else None,
        "lastCreatedAt": lastRow.get("createdAt") if lastRow else None,
        "queueApprovals": queueApprovals,
        "queueRejections": queueRejections,
        "rows": rows,
    }


def reportToDict(report: BgIntelligenceReport) -> dict[str, Any]:
    return {
        "discordUserId": int(report.discordUserId),
        "discordDisplayName": report.discordDisplayName,
        "discordUsername": report.discordUsername,
        "reviewBucket": report.reviewBucket,
        "reviewBucketSource": report.reviewBucketSource,
        "identitySource": report.identitySource,
        "robloxUserId": report.robloxUserId,
        "robloxUsername": report.robloxUsername,
        "roverError": report.roverError,
        "robloxCreated": report.robloxCreated,
        "robloxAgeDays": report.robloxAgeDays,
        "usernameHistoryScanStatus": report.usernameHistoryScanStatus,
        "usernameHistoryScanError": report.usernameHistoryScanError,
        "previousRobloxUsernames": report.previousRobloxUsernames or [],
        "altScanStatus": report.altScanStatus,
        "altScanError": report.altScanError,
        "altMatches": report.altMatches or [],
        "groupSummary": report.groupSummary or {},
        "groupScanStatus": report.groupScanStatus,
        "groupScanError": report.groupScanError,
        "connectionScanStatus": report.connectionScanStatus,
        "connectionScanError": report.connectionScanError,
        "connectionSummary": report.connectionSummary or {},
        "friendIdsScanStatus": report.friendIdsScanStatus,
        "friendIdsScanError": report.friendIdsScanError,
        "friendUserIds": report.friendUserIds or [],
        "groups": report.groups or [],
        "flaggedGroups": report.flaggedGroups or [],
        "flagMatches": report.flagMatches or [],
        "directMatches": report.directMatches or [],
        "inventoryScanStatus": report.inventoryScanStatus,
        "inventoryScanError": report.inventoryScanError,
        "inventorySummary": report.inventorySummary or {},
        "flaggedItems": report.flaggedItems or [],
        "gamepassScanStatus": report.gamepassScanStatus,
        "gamepassScanError": report.gamepassScanError,
        "gamepassSummary": report.gamepassSummary or {},
        "ownedGamepasses": report.ownedGamepasses or [],
        "favoriteGameScanStatus": report.favoriteGameScanStatus,
        "favoriteGameScanError": report.favoriteGameScanError,
        "favoriteGames": report.favoriteGames or [],
        "flaggedFavoriteGames": report.flaggedFavoriteGames or [],
        "outfitScanStatus": report.outfitScanStatus,
        "outfitScanError": report.outfitScanError,
        "outfits": report.outfits or [],
        "badgeHistoryScanStatus": report.badgeHistoryScanStatus,
        "badgeHistoryScanError": report.badgeHistoryScanError,
        "badgeHistorySample": report.badgeHistorySample or [],
        "badgeTimelineSummary": report.badgeTimelineSummary or {},
        "badgeScanStatus": report.badgeScanStatus,
        "badgeScanError": report.badgeScanError,
        "flaggedBadges": report.flaggedBadges or [],
        "externalSourceStatus": report.externalSourceStatus,
        "externalSourceError": report.externalSourceError,
        "externalSourceMatches": report.externalSourceMatches or [],
        "externalSourceDetails": report.externalSourceDetails or [],
        "priorReportSummary": report.priorReportSummary or {},
        "privateInventoryDmSent": report.privateInventoryDmSent,
    }


def _normalizedAltLinkStatus(status: str) -> str:
    normalized = str(status or "").strip().upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "CONFIRM": "CONFIRMED",
        "CONFIRMED_ALT": "CONFIRMED",
        "RELATED_BUT_ALLOWED": "RELATED",
        "RELATED_ALLOWED": "RELATED",
        "CLEAR": "CLEARED",
        "CLEARED_ALT": "CLEARED",
        "NOT_ALT": "CLEARED",
        "NOT_AN_ALT": "CLEARED",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"CONFIRMED", "RELATED", "CLEARED"}:
        return "CONFIRMED"
    return normalized


async def recordAltLink(
    *,
    guildId: int,
    createdBy: int,
    status: str,
    sourceDiscordUserId: int = 0,
    sourceRobloxUserId: int | None = None,
    sourceRobloxUsername: str = "",
    targetDiscordUserId: int = 0,
    targetRobloxUserId: int | None = None,
    targetRobloxUsername: str = "",
    note: str = "",
) -> int:
    normalizedStatus = _normalizedAltLinkStatus(status)
    return await executeReturnId(
        """
        INSERT INTO bg_alt_links (
            guildId, status,
            sourceDiscordUserId, sourceRobloxUserId, sourceRobloxUsername,
            targetDiscordUserId, targetRobloxUserId, targetRobloxUsername,
            note, createdBy, updatedAt
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            int(guildId or 0),
            normalizedStatus,
            int(sourceDiscordUserId or 0),
            int(sourceRobloxUserId) if sourceRobloxUserId else None,
            str(sourceRobloxUsername or "").strip()[:80],
            int(targetDiscordUserId or 0),
            int(targetRobloxUserId) if targetRobloxUserId else None,
            str(targetRobloxUsername or "").strip()[:80],
            str(note or "").strip()[:500],
            int(createdBy or 0),
        ),
    )


async def _recordIdentityGraphSnapshot(
    report: BgIntelligenceReport,
    *,
    guildId: int,
    reportId: int,
) -> None:
    discordUserId = _safeIntValue(report.discordUserId)
    robloxUserId = _safeIntValue(report.robloxUserId)
    robloxUsername = str(report.robloxUsername or "").strip()
    robloxUsernameKey = characters.normalized_username_key(robloxUsername)
    if discordUserId > 0 or robloxUserId > 0 or robloxUsernameKey:
        await execute(
            """
            INSERT INTO bg_identity_history (
                guildId, reportId, discordUserId, robloxUserId,
                robloxUsername, robloxUsernameKey, source, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(guildId or 0),
                int(reportId or 0),
                discordUserId,
                robloxUserId or None,
                robloxUsername,
                robloxUsernameKey,
                str(report.identitySource or "bg-intelligence")[:80],
                95 if robloxUserId > 0 else 70,
            ),
        )

    if robloxUserId > 0:
        usernameRows: list[tuple[str, str]] = []
        if robloxUsernameKey:
            usernameRows.append(("current", robloxUsername))
        for previousUsername in list(report.previousRobloxUsernames or []):
            previousText = str(previousUsername or "").strip()
            previousKey = characters.normalized_username_key(previousText)
            if previousKey and previousKey != robloxUsernameKey:
                usernameRows.append(("previous", previousText))
        seenUsernames: set[str] = set()
        for usernameKind, username in usernameRows:
            key = characters.normalized_username_key(username)
            if not key or key in seenUsernames:
                continue
            seenUsernames.add(key)
            await execute(
                """
                INSERT INTO bg_roblox_username_index (
                    robloxUserId, robloxUsernameKey, robloxUsername,
                    usernameKind, source, reportId, lastSeenAt
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(robloxUserId, robloxUsernameKey) DO UPDATE SET
                    robloxUsername = excluded.robloxUsername,
                    usernameKind = CASE
                        WHEN bg_roblox_username_index.usernameKind = 'current' THEN 'current'
                        ELSE excluded.usernameKind
                    END,
                    source = excluded.source,
                    reportId = excluded.reportId,
                    lastSeenAt = datetime('now'),
                    seenCount = bg_roblox_username_index.seenCount + 1
                """,
                (
                    robloxUserId,
                    key,
                    str(username or "").strip()[:80],
                    usernameKind,
                    "bg-intelligence",
                    int(reportId or 0),
                ),
            )

        for group in list(report.groups or []):
            if not isinstance(group, dict):
                continue
            groupId = _safeIntValue(group.get("id"))
            if groupId <= 0:
                continue
            memberCountRaw = group.get("memberCount")
            memberCount = _safeIntValue(memberCountRaw) if memberCountRaw is not None else None
            await execute(
                """
                INSERT INTO bg_roblox_group_index (
                    robloxUserId, groupId, robloxUsername, groupName,
                    role, rank, memberCount, source, reportId, lastSeenAt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(robloxUserId, groupId) DO UPDATE SET
                    robloxUsername = excluded.robloxUsername,
                    groupName = excluded.groupName,
                    role = excluded.role,
                    rank = excluded.rank,
                    memberCount = excluded.memberCount,
                    source = excluded.source,
                    reportId = excluded.reportId,
                    lastSeenAt = datetime('now'),
                    seenCount = bg_roblox_group_index.seenCount + 1
                """,
                (
                    robloxUserId,
                    groupId,
                    robloxUsername[:80],
                    str(group.get("name") or "").strip()[:160],
                    str(group.get("role") or "").strip()[:80],
                    _safeIntValue(group.get("rank")),
                    memberCount,
                    "bg-intelligence",
                    int(reportId or 0),
                ),
            )


async def recordReport(
    *,
    guildId: int,
    channelId: int,
    reviewerId: int,
    report: BgIntelligenceReport,
    riskScore: scoring.RiskScore,
) -> int:
    signalRows = [
        {
            "label": signal.label,
            "points": int(signal.points),
            "kind": signal.kind,
        }
        for signal in list(riskScore.signals or [])
    ]
    reportId = await executeReturnId(
        """
        INSERT INTO bg_intelligence_reports (
            guildId, channelId, reviewerId, targetUserId,
            robloxUserId, robloxUsername, reviewBucket,
            score, band, confidence, scored, outcome, hardMinimum, signalJson, reportJson
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guildId),
            int(channelId),
            int(reviewerId),
            int(report.discordUserId),
            report.robloxUserId,
            report.robloxUsername,
            str(report.reviewBucket or ""),
            int(riskScore.score),
            str(riskScore.band),
            int(riskScore.confidence),
            1 if bool(riskScore.scored) else 0,
            str(riskScore.outcome or "scored"),
            int(riskScore.hardMinimum or 0),
            _safeJson(signalRows),
            _safeJson(reportToDict(report)),
        ),
    )
    try:
        await execute(
            """
            INSERT OR REPLACE INTO bg_intelligence_report_index (
                reportId, guildId, channelId, reviewerId, targetUserId,
                robloxUserId, robloxUsername, reviewBucket,
                score, band, confidence, scored, outcome, hardMinimum, createdAt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                int(reportId),
                int(guildId),
                int(channelId),
                int(reviewerId),
                int(report.discordUserId),
                report.robloxUserId,
                report.robloxUsername,
                str(report.reviewBucket or ""),
                int(riskScore.score),
                str(riskScore.band),
                int(riskScore.confidence),
                1 if bool(riskScore.scored) else 0,
                str(riskScore.outcome or "scored"),
                int(riskScore.hardMinimum or 0),
            ),
        )
    except Exception:
        log.exception("BG intelligence minimal index insert failed.")
    try:
        await _recordIdentityGraphSnapshot(
            report,
            guildId=int(guildId),
            reportId=int(reportId),
        )
    except Exception:
        log.exception("BG intelligence identity graph snapshot failed.")
    return int(reportId)


async def pruneExpiredReports(
    *,
    keepHours: int = 24,
    keepIndexDays: int = 90,
    keepIdentityGraphDays: int = 365,
) -> int:
    normalizedHours = max(1, int(keepHours or 24))
    cutoffModifier = f"-{normalizedHours} hours"
    row = await fetchOne(
        """
        SELECT COUNT(*) AS total
        FROM bg_intelligence_reports
        WHERE datetime(createdAt) < datetime('now', ?)
        """,
        (cutoffModifier,),
    )
    total = int((row or {}).get("total") or 0)
    if total > 0:
        await execute(
            """
            DELETE FROM bg_intelligence_reports
            WHERE datetime(createdAt) < datetime('now', ?)
            """,
            (cutoffModifier,),
        )
    normalizedIndexDays = max(1, int(keepIndexDays or 90))
    indexCutoffModifier = f"-{normalizedIndexDays} days"
    await execute(
        """
        DELETE FROM bg_intelligence_report_index
        WHERE datetime(createdAt) < datetime('now', ?)
        """,
        (indexCutoffModifier,),
    )
    normalizedGraphDays = max(1, int(keepIdentityGraphDays or 365))
    graphCutoffModifier = f"-{normalizedGraphDays} days"
    for tableName, columnName in (
        ("bg_identity_history", "createdAt"),
        ("bg_roblox_username_index", "lastSeenAt"),
        ("bg_roblox_group_index", "lastSeenAt"),
    ):
        await execute(
            f"""
            DELETE FROM {tableName}
            WHERE datetime({columnName}) < datetime('now', ?)
            """,
            (graphCutoffModifier,),
        )
    return total


async def listRecentReports(
    *,
    guildId: int,
    targetUserId: int | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    normalizedLimit = max(1, min(int(limit or 5), 20))
    if targetUserId is not None and int(targetUserId) > 0:
        return await fetchAll(
            """
            SELECT *
            FROM bg_intelligence_reports
            WHERE guildId = ? AND targetUserId = ?
            ORDER BY datetime(createdAt) DESC, reportId DESC
            LIMIT ?
            """,
            (int(guildId), int(targetUserId), normalizedLimit),
        )
    return await fetchAll(
        """
        SELECT *
        FROM bg_intelligence_reports
        WHERE guildId = ?
        ORDER BY datetime(createdAt) DESC, reportId DESC
        LIMIT ?
        """,
        (int(guildId), normalizedLimit),
    )
