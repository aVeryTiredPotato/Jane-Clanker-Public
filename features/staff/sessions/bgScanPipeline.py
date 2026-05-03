from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable, Optional

import config
from features.staff.bgflags import service as flagService
from features.staff.sessions import bgBuckets, service
from features.staff.sessions.Roblox import (
    robloxBadges,
    robloxGroups,
    robloxInventory,
    robloxOutfits,
    robloxProfiles,
    robloxUsers,
)

FlagRules = tuple[
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


@dataclass
class RobloxIdentity:
    robloxUserId: Optional[int]
    robloxUsername: Optional[str]
    lookupError: Optional[str] = None


def _isRecentIsoScan(scanAt: Optional[str], cacheDays: int) -> bool:
    if not scanAt:
        return False
    try:
        scanTime = datetime.fromisoformat(scanAt)
    except ValueError:
        return False
    return (datetime.now() - scanTime).days < int(cacheDays)


async def resolveRobloxIdentity(attendee: dict) -> RobloxIdentity:
    robloxUserId = attendee.get("robloxUserId")
    robloxUsername = attendee.get("robloxUsername")
    if robloxUserId:
        return RobloxIdentity(robloxUserId, robloxUsername)

    lookup = await robloxUsers.fetchRobloxUser(attendee["userId"])
    return RobloxIdentity(
        lookup.robloxId,
        lookup.robloxUsername or robloxUsername,
        lookup.error,
    )


async def scanRobloxGroupsForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    flagIds: set[int],
    flagUsernames: list[str],
    groupKeywords: list[str],
    accountAgeDays: int,
) -> bool:
    if not (flagIds or flagUsernames or groupKeywords or accountAgeDays > 0):
        return False
    scanAt = attendee.get("robloxGroupScanAt")
    if scanAt and attendee.get("robloxGroupScanStatus") in {"OK", "NO_ROVER", "ERROR"}:
        cacheDays = int(getattr(config, "robloxGroupScanCacheDays", 7))
        if _isRecentIsoScan(scanAt, cacheDays):
            return False

    robloxUserId = identity.robloxUserId
    robloxUsername = identity.robloxUsername
    lookupError = identity.lookupError

    if not robloxUserId:
        await service.setRobloxGroupScan(
            sessionId,
            attendee["userId"],
            groups=[],
            flaggedGroups=[],
            flagMatches=[],
            status="NO_ROVER",
            error=lookupError,
            robloxUserId=None,
            robloxUsername=robloxUsername,
        )
        return True

    matches: list[dict] = []

    if accountAgeDays > 0:
        profile = await robloxProfiles.fetchRobloxUserProfile(robloxUserId)
        if profile.created:
            createdRaw = profile.created.replace("Z", "+00:00")
            try:
                createdAt = datetime.fromisoformat(createdRaw)
                ageDays = (datetime.now(createdAt.tzinfo) - createdAt).days
                if ageDays < accountAgeDays:
                    matches.append(
                        {
                            "type": "accountAge",
                            "value": f"{ageDays} days",
                            "created": profile.created,
                            "thresholdDays": accountAgeDays,
                        }
                    )
            except ValueError:
                pass

    groups: list[dict] = []
    flaggedGroups: list[dict] = []
    if flagIds or flagUsernames or groupKeywords:
        result = await robloxGroups.fetchRobloxGroups(robloxUserId)
        if result.error:
            await service.setRobloxGroupScan(
                sessionId,
                attendee["userId"],
                groups=[],
                flaggedGroups=[],
                flagMatches=matches,
                status="ERROR",
                error=result.error,
                robloxUserId=robloxUserId,
                robloxUsername=robloxUsername,
            )
            return True

        groups = result.groups
        flaggedGroups = [g for g in groups if g.get("id") in flagIds]
        if groupKeywords:
            for group in groups:
                groupName = str(group.get("name") or "").lower()
                if not groupName:
                    continue
                if any(keyword and keyword in groupName for keyword in groupKeywords):
                    flaggedGroups.append(group)

        dedupedFlagged: list[dict] = []
        seenGroupKeys: set[tuple[Optional[int], str]] = set()
        for group in flaggedGroups:
            groupId = group.get("id")
            groupName = str(group.get("name") or "")
            key = (groupId if isinstance(groupId, int) else None, groupName.lower())
            if key in seenGroupKeys:
                continue
            seenGroupKeys.add(key)
            dedupedFlagged.append(group)
        flaggedGroups = dedupedFlagged

    if robloxUsername:
        username = robloxUsername.lower()
        if username in flagUsernames:
            matches.append({"type": "username", "value": robloxUsername})
        for keyword in groupKeywords:
            if keyword and keyword in username:
                matches.append({"type": "keyword", "value": keyword, "context": "username"})

    if groupKeywords:
        for group in groups:
            name = str(group.get("name") or "").lower()
            if not name:
                continue
            for keyword in groupKeywords:
                if keyword and keyword in name:
                    matches.append(
                        {
                            "type": "keyword",
                            "value": keyword,
                            "context": "group",
                            "groupId": group.get("id"),
                            "groupName": group.get("name"),
                        }
                    )

    await service.setRobloxGroupScan(
        sessionId,
        attendee["userId"],
        groups=groups,
        flaggedGroups=flaggedGroups,
        flagMatches=matches,
        status="OK",
        error=None,
        robloxUserId=robloxUserId,
        robloxUsername=robloxUsername,
    )
    return True


def isPrivateInventoryStatus(status: int, error: Optional[str]) -> bool:
    if status in {401, 403}:
        return True
    text = (error or "").strip().lower()
    if not text:
        return False
    privateMarkers = (
        "private",
        "hidden",
        "forbidden",
        "not authorized",
        "unauthorized",
        "insufficient permission",
        "insufficient permissions",
    )
    return any(marker in text for marker in privateMarkers)


async def scanRobloxInventoryForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    itemKeywords: list[str],
    flagItemIds: set[int],
    flagCreatorIds: set[int],
    forceRescan: bool = False,
) -> bool:
    if not getattr(config, "robloxInventoryScanEnabled", False):
        return False

    cacheDays = int(getattr(config, "robloxInventoryScanCacheDays", 7))
    scanAt = attendee.get("robloxInventoryScanAt")
    if (
        not forceRescan
        and scanAt
        and attendee.get("robloxInventoryScanStatus") in {"OK", "NO_ROVER", "ERROR", "PRIVATE"}
    ):
        if _isRecentIsoScan(scanAt, cacheDays):
            return False

    robloxUserId = identity.robloxUserId
    robloxUsername = identity.robloxUsername
    if not robloxUserId:
        await service.setRobloxInventoryScan(
            sessionId,
            attendee["userId"],
            items=[],
            flaggedItems=[],
            status="NO_ROVER",
            error=identity.lookupError,
            robloxUserId=None,
            robloxUsername=robloxUsername,
        )
        return True

    maxPages = int(getattr(config, "robloxInventoryScanMaxPages", 5))
    visualReferenceHashes = await flagService.getValidatedItemVisualHashes(
        ensureSynced=True,
    )
    result = await robloxInventory.fetchRobloxInventory(
        robloxUserId,
        flagItemIds,
        targetCreatorIds=flagCreatorIds,
        targetKeywords=itemKeywords,
        visualReferenceHashes=visualReferenceHashes,
        maxPages=maxPages,
    )
    if result.error:
        isPrivate = isPrivateInventoryStatus(result.status, result.error)
        status = "PRIVATE" if isPrivate else "ERROR"
        error = "Inventory is private or hidden." if isPrivate else result.error
        await service.setRobloxInventoryScan(
            sessionId,
            attendee["userId"],
            items=[],
            flaggedItems=[],
            status=status,
            error=error,
            robloxUserId=robloxUserId,
            robloxUsername=robloxUsername,
        )
        return True

    flaggedItems = result.items
    await service.setRobloxInventoryScan(
        sessionId,
        attendee["userId"],
        items=[],
        flaggedItems=flaggedItems,
        status="OK",
        error=None,
        robloxUserId=robloxUserId,
        robloxUsername=robloxUsername,
    )
    return True


async def scanRobloxBadgesForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    flagBadgeIds: set[int],
    badgeNotes: dict[int, str],
) -> bool:
    if not getattr(config, "robloxBadgeScanEnabled", True):
        return False
    if not flagBadgeIds:
        return False

    cacheDays = int(getattr(config, "robloxBadgeScanCacheDays", 7))
    scanAt = attendee.get("robloxBadgeScanAt")
    if scanAt and attendee.get("robloxBadgeScanStatus") in {"OK", "NO_ROVER", "ERROR"}:
        if _isRecentIsoScan(scanAt, cacheDays):
            return False

    robloxUserId = identity.robloxUserId
    robloxUsername = identity.robloxUsername
    if not robloxUserId:
        await service.setRobloxBadgeScan(
            sessionId,
            attendee["userId"],
            flaggedBadges=[],
            status="NO_ROVER",
            error=identity.lookupError,
            robloxUserId=None,
            robloxUsername=robloxUsername,
        )
        return True

    batchSize = int(getattr(config, "robloxBadgeScanBatchSize", 50))
    result = await robloxBadges.fetchRobloxBadgeAwards(robloxUserId, flagBadgeIds, batchSize=batchSize)
    if result.error:
        await service.setRobloxBadgeScan(
            sessionId,
            attendee["userId"],
            flaggedBadges=[],
            status="ERROR",
            error=result.error,
            robloxUserId=robloxUserId,
            robloxUsername=robloxUsername,
        )
        return True

    flaggedBadges = []
    for badge in result.badges:
        badgeId = badge.get("badgeId")
        if badgeId is None:
            continue
        entry = {
            "badgeId": badgeId,
            "awardedDate": badge.get("awardedDate"),
        }
        note = badgeNotes.get(int(badgeId))
        if note:
            entry["note"] = note
        flaggedBadges.append(entry)

    await service.setRobloxBadgeScan(
        sessionId,
        attendee["userId"],
        flaggedBadges=flaggedBadges,
        status="OK",
        error=None,
        robloxUserId=robloxUserId,
        robloxUsername=robloxUsername,
    )
    return True


async def scanRobloxOutfitsForAttendee(
    sessionId: int,
    attendee: dict,
    identity: RobloxIdentity,
    forceRescan: bool = False,
) -> bool:
    if not getattr(config, "robloxOutfitScanEnabled", True):
        return False

    cacheDays = int(getattr(config, "robloxOutfitScanCacheDays", 7))
    scanAt = attendee.get("robloxOutfitScanAt")
    if (
        not forceRescan
        and scanAt
        and attendee.get("robloxOutfitScanStatus") in {"OK", "NO_ROVER", "ERROR"}
    ):
        if _isRecentIsoScan(scanAt, cacheDays):
            return False

    robloxUserId = identity.robloxUserId
    robloxUsername = identity.robloxUsername
    if not robloxUserId:
        await service.setRobloxOutfitScan(
            sessionId,
            attendee["userId"],
            outfits=[],
            status="NO_ROVER",
            error=identity.lookupError,
            robloxUserId=None,
            robloxUsername=robloxUsername,
        )
        return True

    maxOutfits = int(getattr(config, "robloxOutfitMax", 0) or 0)
    maxPages = int(getattr(config, "robloxOutfitMaxPages", 20) or 20)
    result = await robloxOutfits.fetchRobloxUserOutfits(
        robloxUserId,
        maxOutfits=maxOutfits,
        editableOnly=False,
        maxPages=maxPages,
    )
    if result.error:
        await service.setRobloxOutfitScan(
            sessionId,
            attendee["userId"],
            outfits=[],
            status="ERROR",
            error=result.error,
            robloxUserId=robloxUserId,
            robloxUsername=robloxUsername,
        )
        return True

    await service.setRobloxOutfitScan(
        sessionId,
        attendee["userId"],
        outfits=result.outfits,
        status="OK",
        error=None,
        robloxUserId=robloxUserId,
        robloxUsername=robloxUsername,
    )
    return True


async def scanRobloxFlagsForAttendees(
    sessionId: int,
    attendees: list[dict],
    *,
    flagRules: FlagRules,
    onInventoryBecamePrivate: Optional[Callable[[int], Awaitable[None]]] = None,
) -> bool:
    (
        flagIds,
        flagUsernames,
        groupKeywords,
        itemKeywords,
        flagItemIds,
        flagCreatorIds,
        flagBadgeIds,
        badgeNotes,
        accountAgeDays,
    ) = flagRules
    scanGroups = bool(flagIds or flagUsernames or groupKeywords or accountAgeDays > 0)
    scanInventory = bool(getattr(config, "robloxInventoryScanEnabled", False)) and bool(
        flagItemIds or flagCreatorIds or itemKeywords
    )
    scanBadges = bool(flagBadgeIds)
    if not (scanGroups or scanInventory or scanBadges):
        return False
    updated = False
    for attendee in attendees:
        if attendee.get("examGrade") != "PASS":
            continue
        reviewBucket = bgBuckets.normalizeBgReviewBucket(
            attendee.get("bgReviewBucket"),
            default=bgBuckets.adultBgReviewBucket,
        )
        identity: Optional[RobloxIdentity] = None

        async def getIdentity() -> RobloxIdentity:
            nonlocal identity
            if identity is None:
                identity = await resolveRobloxIdentity(attendee)
            return identity

        if reviewBucket == bgBuckets.adultBgReviewBucket and scanGroups:
            if await scanRobloxGroupsForAttendee(
                sessionId,
                attendee,
                await getIdentity(),
                flagIds,
                flagUsernames,
                groupKeywords,
                accountAgeDays,
            ):
                updated = True
        if reviewBucket == bgBuckets.adultBgReviewBucket and scanInventory:
            previousInventoryStatus = attendee.get("robloxInventoryScanStatus")
            if await scanRobloxInventoryForAttendee(
                sessionId,
                attendee,
                await getIdentity(),
                itemKeywords,
                flagItemIds,
                flagCreatorIds,
            ):
                updated = True
                if onInventoryBecamePrivate is not None:
                    refreshedAttendee = await service.getAttendee(sessionId, attendee["userId"])
                    if (
                        refreshedAttendee
                        and refreshedAttendee.get("robloxInventoryScanStatus") == "PRIVATE"
                        and previousInventoryStatus != "PRIVATE"
                    ):
                        await onInventoryBecamePrivate(int(attendee["userId"]))
        if scanBadges:
            if await scanRobloxBadgesForAttendee(
                sessionId,
                attendee,
                await getIdentity(),
                flagBadgeIds,
                badgeNotes,
            ):
                updated = True
    return updated

