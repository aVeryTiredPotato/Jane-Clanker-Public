from __future__ import annotations

from typing import Optional

import config
from features.staff.sessions.Roblox import robloxTransport
from features.staff.sessions.Roblox.robloxModels import RobloxFavoriteGamesResult

_cacheGet = robloxTransport.cacheGet
_cacheSet = robloxTransport.cacheSet
_requestJson = robloxTransport.requestJson

async def fetchRobloxFavoriteGames(
    robloxUserId: int,
    maxGames: int = 10,
) -> RobloxFavoriteGamesResult:
    try:
        normalizedUserId = int(robloxUserId)
    except (TypeError, ValueError):
        return RobloxFavoriteGamesResult([], 0, error="Favorite games lookup failed (invalid Roblox user ID).")

    if normalizedUserId <= 0:
        return RobloxFavoriteGamesResult([], 0, error="Favorite games lookup failed (invalid Roblox user ID).")

    requestedLimit = max(1, min(int(maxGames or 10), 100))
    cacheKey = (normalizedUserId, requestedLimit)
    cached = _cacheGet(
        "favorite_games",
        cacheKey,
        ttlName="robloxFavoriteGamesCacheTtlSec",
        defaultTtlSec=3600,
    )
    if isinstance(cached, RobloxFavoriteGamesResult):
        return cached
    apiLimit = 100
    for candidateLimit in (10, 25, 50, 100):
        if requestedLimit <= candidateLimit:
            apiLimit = candidateLimit
            break
    url = f"https://games.roblox.com/v2/users/{normalizedUserId}/favorite/games"
    params = {"limit": str(apiLimit), "sortOrder": "Desc"}
    headers = {
        "User-Agent": str(
            getattr(
                config,
                "robloxPublicApiUserAgent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jane-Clanker/1.0",
            )
        ),
        "Accept": "application/json",
    }

    try:
        status, data = await _requestJson(
            "GET",
            url,
            headers=headers,
            params=params,
            timeoutSec=10,
        )
    except Exception as exc:
        return RobloxFavoriteGamesResult([], 0, error=str(exc))

    # Some accounts intermittently reject explicit sortOrder. Retry once without it.
    if status == 400:
        try:
            statusNoSort, dataNoSort = await _requestJson(
                "GET",
                url,
                headers=headers,
                params={"limit": str(apiLimit)},
                timeoutSec=10,
            )
            if statusNoSort == 200:
                status, data = statusNoSort, dataNoSort
        except Exception:
            pass

    def _extractApiError(payload: object) -> Optional[str]:
        if not isinstance(payload, dict):
            return None
        rawErrors = payload.get("errors")
        if isinstance(rawErrors, list):
            parts: list[str] = []
            for entry in rawErrors:
                if isinstance(entry, dict):
                    message = entry.get("message")
                    if isinstance(message, str) and message.strip():
                        parts.append(message.strip())
                elif isinstance(entry, str) and entry.strip():
                    parts.append(entry.strip())
            if parts:
                return "; ".join(parts)
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None

    if status != 200 or not isinstance(data, dict):
        apiError = _extractApiError(data)
        if apiError:
            return RobloxFavoriteGamesResult([], status, error=f"Favorite games lookup failed ({status}): {apiError}")
        if status in (400, 403):
            return RobloxFavoriteGamesResult(
                [],
                status,
                error="Favorite games are unavailable for this user (private or Roblox API rejection).",
            )
        return RobloxFavoriteGamesResult([], status, error=f"Favorite games lookup failed ({status}).")

    raw = data.get("data")
    if not isinstance(raw, list):
        return RobloxFavoriteGamesResult([], status, error="Favorite games lookup returned invalid data.")

    games: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        universeId = entry.get("universeId")
        placeId = (
            entry.get("rootPlaceId")
            or entry.get("placeId")
            or entry.get("placeID")
        )
        name = entry.get("name")
        try:
            universeId = int(universeId) if universeId is not None else None
        except (TypeError, ValueError):
            universeId = None
        try:
            placeId = int(placeId) if placeId is not None else None
        except (TypeError, ValueError):
            placeId = None
        games.append(
            {
                "name": name if isinstance(name, str) else None,
                "universeId": universeId,
                "placeId": placeId,
            }
        )
        if len(games) >= requestedLimit:
            break

    result = RobloxFavoriteGamesResult(games, status)
    _cacheSet(
        "favorite_games",
        cacheKey,
        result,
        ttlName="robloxFavoriteGamesCacheTtlSec",
        defaultTtlSec=3600,
    )
    return result
