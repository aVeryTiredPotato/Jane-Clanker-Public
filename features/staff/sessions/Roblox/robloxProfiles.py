from __future__ import annotations

import asyncio
from typing import Optional

import config
from features.staff.sessions.Roblox import robloxPayloads, robloxTransport
from features.staff.sessions.Roblox.robloxModels import (
    RobloxConnectionCountsResult,
    RobloxFriendIdsResult,
    RobloxUserProfileResult,
    RobloxUsernameHistoryResult,
)

_cacheGet = robloxTransport.cacheGet
_cacheSet = robloxTransport.cacheSet
_requestJson = robloxTransport.requestJson
_optionalInt = robloxPayloads.optionalInt


async def _fetchRobloxCount(url: str) -> tuple[Optional[int], int, Optional[str]]:
    try:
        status, data = await _requestJson(
            "GET",
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": str(
                    getattr(
                        config,
                        "robloxPublicApiUserAgent",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jane-Clanker/1.0",
                    )
                ),
            },
            timeoutSec=10,
        )
    except Exception as exc:
        return None, 0, str(exc)
    if status != 200 or not isinstance(data, dict):
        return None, int(status or 0), f"Count lookup failed ({status})."
    count = _optionalInt(data.get("count"))
    if count is None:
        return None, int(status or 0), "Count lookup returned invalid data."
    return int(count), int(status or 200), None


async def fetchRobloxConnectionCounts(robloxUserId: int) -> RobloxConnectionCountsResult:
    try:
        normalizedUserId = int(robloxUserId)
    except (TypeError, ValueError):
        return RobloxConnectionCountsResult(None, None, None, 0, error="Connection lookup failed (invalid Roblox user ID).")
    if normalizedUserId <= 0:
        return RobloxConnectionCountsResult(None, None, None, 0, error="Connection lookup failed (invalid Roblox user ID).")
    cached = _cacheGet(
        "connection_counts",
        normalizedUserId,
        ttlName="robloxConnectionCacheTtlSec",
        defaultTtlSec=3600,
    )
    if isinstance(cached, RobloxConnectionCountsResult):
        return cached

    baseUrl = f"https://friends.roblox.com/v1/users/{normalizedUserId}"
    results = await asyncio.gather(
        _fetchRobloxCount(f"{baseUrl}/friends/count"),
        _fetchRobloxCount(f"{baseUrl}/followers/count"),
        _fetchRobloxCount(f"{baseUrl}/followings/count"),
    )
    friends, friendStatus, friendError = results[0]
    followers, followerStatus, followerError = results[1]
    following, followingStatus, followingError = results[2]
    errors = [error for error in (friendError, followerError, followingError) if error]
    status = max(friendStatus, followerStatus, followingStatus)
    if errors and friends is None and followers is None and following is None:
        return RobloxConnectionCountsResult(friends, followers, following, status, error="; ".join(errors[:3]))
    result = RobloxConnectionCountsResult(
        friends,
        followers,
        following,
        status or 200,
        error="; ".join(errors[:3]) if errors else None,
    )
    _cacheSet(
        "connection_counts",
        normalizedUserId,
        result,
        ttlName="robloxConnectionCacheTtlSec",
        defaultTtlSec=3600,
    )
    return result


async def fetchRobloxFriendIds(
    robloxUserId: int,
    *,
    maxFriends: int = 200,
) -> RobloxFriendIdsResult:
    try:
        normalizedUserId = int(robloxUserId)
    except (TypeError, ValueError):
        return RobloxFriendIdsResult([], 0, error="Friend list lookup failed (invalid Roblox user ID).")
    if normalizedUserId <= 0:
        return RobloxFriendIdsResult([], 0, error="Friend list lookup failed (invalid Roblox user ID).")
    try:
        normalizedMax = max(1, min(int(maxFriends or 200), 200))
    except (TypeError, ValueError):
        normalizedMax = 200
    cacheKey = (normalizedUserId, normalizedMax)
    cached = _cacheGet(
        "friend_ids",
        cacheKey,
        ttlName="robloxFriendListCacheTtlSec",
        defaultTtlSec=3600,
    )
    if isinstance(cached, RobloxFriendIdsResult):
        return cached

    url = f"https://friends.roblox.com/v1/users/{normalizedUserId}/friends"
    try:
        status, data = await _requestJson(
            "GET",
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": str(
                    getattr(
                        config,
                        "robloxPublicApiUserAgent",
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jane-Clanker/1.0",
                    )
                ),
            },
            timeoutSec=10,
        )
    except Exception as exc:
        return RobloxFriendIdsResult([], 0, error=str(exc))
    if status != 200 or not isinstance(data, dict):
        return RobloxFriendIdsResult([], int(status or 0), error=f"Friend list lookup failed ({status}).")
    rows = data.get("data")
    if not isinstance(rows, list):
        return RobloxFriendIdsResult([], int(status or 0), error="Friend list lookup returned invalid data.")
    friendIds: list[int] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        friendId = _optionalInt(row.get("id"))
        if friendId is None or friendId <= 0 or friendId in seen:
            continue
        seen.add(friendId)
        friendIds.append(int(friendId))
        if len(friendIds) >= normalizedMax:
            break
    result = RobloxFriendIdsResult(friendIds, int(status or 200))
    _cacheSet(
        "friend_ids",
        cacheKey,
        result,
        ttlName="robloxFriendListCacheTtlSec",
        defaultTtlSec=3600,
    )
    return result


async def fetchRobloxUserProfile(robloxUserId: int) -> RobloxUserProfileResult:
    cacheKey = int(robloxUserId or 0)
    cached = _cacheGet(
        "profiles",
        cacheKey,
        ttlName="robloxProfileCacheTtlSec",
        defaultTtlSec=86400,
    )
    if isinstance(cached, RobloxUserProfileResult):
        return cached

    url = f"https://users.roblox.com/v1/users/{robloxUserId}"
    try:
        status, data = await _requestJson("GET", url, timeoutSec=10)
    except Exception as exc:
        return RobloxUserProfileResult(None, 0, error=str(exc))

    if status != 200 or not isinstance(data, dict):
        return RobloxUserProfileResult(None, status, error=f"Profile lookup failed ({status}).")

    created = data.get("created")
    username = data.get("name")
    result = RobloxUserProfileResult(
        created if isinstance(created, str) else None,
        status,
        username=username if isinstance(username, str) else None,
    )
    _cacheSet(
        "profiles",
        cacheKey,
        result,
        ttlName="robloxProfileCacheTtlSec",
        defaultTtlSec=86400,
    )
    return result


async def fetchRobloxUsernameHistory(
    robloxUserId: int,
    *,
    maxNames: int = 50,
) -> RobloxUsernameHistoryResult:
    try:
        normalizedUserId = int(robloxUserId)
    except (TypeError, ValueError):
        return RobloxUsernameHistoryResult([], 0, error="Username history lookup failed (invalid Roblox user ID).")
    if normalizedUserId <= 0:
        return RobloxUsernameHistoryResult([], 0, error="Username history lookup failed (invalid Roblox user ID).")
    try:
        normalizedMaxNames = max(1, min(int(maxNames or 50), 100))
    except (TypeError, ValueError):
        normalizedMaxNames = 50
    cacheKey = (normalizedUserId, normalizedMaxNames)
    cached = _cacheGet(
        "username_history",
        cacheKey,
        ttlName="robloxProfileCacheTtlSec",
        defaultTtlSec=86400,
    )
    if isinstance(cached, RobloxUsernameHistoryResult):
        return cached

    url = f"https://users.roblox.com/v1/users/{normalizedUserId}/username-history"
    try:
        status, data = await _requestJson(
            "GET",
            url,
            params={"limit": str(normalizedMaxNames), "sortOrder": "Desc"},
            timeoutSec=10,
        )
    except Exception as exc:
        return RobloxUsernameHistoryResult([], 0, error=str(exc))

    if status != 200 or not isinstance(data, dict):
        return RobloxUsernameHistoryResult([], int(status or 0), error=f"Username history lookup failed ({status}).")
    rows = data.get("data")
    if not isinstance(rows, list):
        return RobloxUsernameHistoryResult([], int(status or 0), error="Username history lookup returned invalid data.")
    usernames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        lowered = name.lower()
        if not name or lowered in seen:
            continue
        seen.add(lowered)
        usernames.append(name)
    result = RobloxUsernameHistoryResult(usernames[:normalizedMaxNames], int(status or 200))
    _cacheSet(
        "username_history",
        cacheKey,
        result,
        ttlName="robloxProfileCacheTtlSec",
        defaultTtlSec=86400,
    )
    return result
