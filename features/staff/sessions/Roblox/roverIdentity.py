from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

import config
from db.sqlite import execute, fetchOne
from features.staff.sessions.Roblox import robloxTransport
from features.staff.sessions.Roblox.robloxModels import RoverLookupResult


_roverCache: dict[tuple[int, int], tuple[datetime, RoverLookupResult]] = {}


def _roverCacheTtlSec() -> int:
    try:
        value = int(getattr(config, "roverCacheTtlSec", 120) or 120)
    except (TypeError, ValueError):
        value = 120
    return max(0, min(value, 3600))


def _roverCacheKey(discordId: int, guildId: Optional[int]) -> tuple[int, int]:
    resolvedGuild = int(guildId or getattr(config, "serverId", 0) or 0)
    return int(discordId), resolvedGuild


def _roverCacheGet(discordId: int, guildId: Optional[int]) -> Optional[RoverLookupResult]:
    ttlSec = _roverCacheTtlSec()
    if ttlSec <= 0:
        return None
    key = _roverCacheKey(discordId, guildId)
    cached = _roverCache.get(key)
    if not cached:
        return None
    cachedAt, cachedValue = cached
    if robloxTransport.utcNow() - cachedAt > timedelta(seconds=ttlSec):
        _roverCache.pop(key, None)
        return None
    return cachedValue


def _roverCacheSet(discordId: int, guildId: Optional[int], result: RoverLookupResult) -> None:
    ttlSec = _roverCacheTtlSec()
    if ttlSec <= 0:
        return
    now = robloxTransport.utcNow()
    if _roverCache:
        cutoff = now - timedelta(seconds=ttlSec)
        staleKeys = [key for key, (cachedAt, _) in _roverCache.items() if cachedAt < cutoff]
        for key in staleKeys:
            _roverCache.pop(key, None)

    maxEntries = int(getattr(config, "roverCacheMaxEntries", 2000) or 2000)
    if maxEntries > 0 and len(_roverCache) >= maxEntries:
        oldestKey = min(_roverCache.items(), key=lambda item: item[1][0])[0]
        _roverCache.pop(oldestKey, None)

    _roverCache[_roverCacheKey(discordId, guildId)] = (now, result)


def clearRobloxIdentityCache(discordId: int) -> None:
    try:
        safeDiscordId = int(discordId)
    except (TypeError, ValueError):
        return
    for key in [key for key in _roverCache if key[0] == safeDiscordId]:
        _roverCache.pop(key, None)


def _roverUrl(discordId: int, guildId: Optional[int] = None) -> str:
    base = getattr(config, "roverApiBaseUrl", "https://verify.eryn.io/api/user/").rstrip("/")
    if "{discordId}" in base or "{guildId}" in base:
        resolvedGuild = guildId or getattr(config, "serverId", None) or ""
        return base.format(discordId=discordId, guildId=resolvedGuild)
    return f"{base}/{discordId}"


def extractRobloxFields(payload: dict) -> tuple[Optional[int], Optional[str]]:
    def _pick(obj: dict) -> tuple[Optional[int], Optional[str]]:
        candidates = [
            ("robloxId", obj.get("robloxId")),
            ("roblox_id", obj.get("roblox_id")),
            ("robloxID", obj.get("robloxID")),
            ("id", obj.get("id")),
        ]
        robloxId = None
        for _, value in candidates:
            try:
                if value is not None:
                    robloxId = int(value)
                    break
            except (TypeError, ValueError):
                continue
        username = (
            obj.get("robloxUsername")
            or obj.get("roblox_username")
            or obj.get("cachedUsername")
            or obj.get("username")
        )
        if isinstance(username, str) and not username:
            username = None
        return robloxId, username if isinstance(username, str) else None

    robloxId, username = _pick(payload)
    if robloxId or username:
        return robloxId, username
    data = payload.get("data")
    if isinstance(data, dict):
        return _pick(data)
    return None, None


def _cleanRobloxUsername(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip() if not ch.isspace())


def _identityDbTimeoutSec() -> float:
    try:
        value = float(getattr(config, "roverIdentityDbTimeoutSec", 1.5) or 1.5)
    except (TypeError, ValueError):
        value = 1.5
    return max(0.1, min(value, 10.0))


async def rememberRobloxIdentity(
    discordId: int,
    robloxUsername: str,
    *,
    robloxId: Optional[int] = None,
    source: str = "",
    guildId: Optional[int] = None,
    confidence: int = 0,
) -> bool:
    try:
        safeDiscordId = int(discordId)
    except (TypeError, ValueError):
        return False
    username = _cleanRobloxUsername(robloxUsername)
    if safeDiscordId <= 0 or not username:
        return False

    try:
        safeRobloxId = int(robloxId) if robloxId is not None else None
    except (TypeError, ValueError):
        safeRobloxId = None
    try:
        safeGuildId = int(guildId or 0)
    except (TypeError, ValueError):
        safeGuildId = 0
    safeConfidence = max(0, min(int(confidence or 0), 100))

    try:
        await asyncio.wait_for(
            execute(
                """
                INSERT INTO roblox_identity_links
                    (discordUserId, robloxUserId, robloxUsername, source, guildId, confidence, updatedAt)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(discordUserId) DO UPDATE SET
                    robloxUserId = COALESCE(excluded.robloxUserId, roblox_identity_links.robloxUserId),
                    robloxUsername = excluded.robloxUsername,
                    source = excluded.source,
                    guildId = excluded.guildId,
                    confidence = excluded.confidence,
                    updatedAt = datetime('now')
                """,
                (
                    safeDiscordId,
                    safeRobloxId,
                    username,
                    str(source or "").strip()[:80],
                    safeGuildId,
                    safeConfidence,
                ),
            ),
            timeout=_identityDbTimeoutSec(),
        )
    except Exception:
        return False
    clearRobloxIdentityCache(safeDiscordId)
    return True


async def rememberKnownRobloxIdentity(
    discordId: int,
    robloxUsername: str,
    *,
    robloxId: Optional[int] = None,
    source: str = "known",
    guildId: Optional[int] = None,
    confidence: int = 80,
) -> bool:
    return await rememberRobloxIdentity(
        discordId,
        robloxUsername,
        robloxId=robloxId,
        source=source,
        guildId=guildId,
        confidence=confidence,
    )


async def rememberLookupResult(
    discordId: int,
    lookup: RoverLookupResult,
    *,
    source: str,
    guildId: Optional[int] = None,
    confidence: int = 80,
) -> bool:
    username = str(getattr(lookup, "robloxUsername", "") or "").strip()
    if not username:
        return False
    return await rememberRobloxIdentity(
        discordId,
        username,
        robloxId=getattr(lookup, "robloxId", None),
        source=source,
        guildId=guildId,
        confidence=confidence,
    )


async def forgetRobloxIdentity(discordId: int) -> None:
    try:
        safeDiscordId = int(discordId)
    except (TypeError, ValueError):
        return
    if safeDiscordId <= 0:
        return
    try:
        await asyncio.wait_for(
            execute("DELETE FROM roblox_identity_links WHERE discordUserId = ?", (safeDiscordId,)),
            timeout=_identityDbTimeoutSec(),
        )
    finally:
        clearRobloxIdentityCache(safeDiscordId)


async def _localIdentityCandidate(discordId: int) -> tuple[Optional[int], str, str] | None:
    queries: tuple[tuple[str, str], ...] = (
        (
            "attendees",
            """
            SELECT robloxUserId, robloxUsername
            FROM attendees
            WHERE userId = ?
              AND robloxUsername IS NOT NULL
              AND trim(robloxUsername) <> ''
            ORDER BY rowid DESC
            LIMIT 1
            """,
        ),
        (
            "bg-intelligence",
            """
            SELECT robloxUserId, robloxUsername
            FROM bg_intelligence_reports
            WHERE targetUserId = ?
              AND robloxUsername IS NOT NULL
              AND trim(robloxUsername) <> ''
            ORDER BY datetime(createdAt) DESC, reportId DESC
            LIMIT 1
            """,
        ),
        (
            "bg-item-review-queue",
            """
            SELECT sourceRobloxUserId AS robloxUserId, sourceRobloxUsername AS robloxUsername
            FROM bg_item_review_queue
            WHERE sourceUserId = ?
              AND sourceRobloxUsername IS NOT NULL
              AND trim(sourceRobloxUsername) <> ''
            ORDER BY datetime(updatedAt) DESC, queueId DESC
            LIMIT 1
            """,
        ),
        (
            "bg-item-review-source",
            """
            SELECT sourceRobloxUserId AS robloxUserId, sourceRobloxUsername AS robloxUsername
            FROM bg_item_review_sources
            WHERE sourceUserId = ?
              AND sourceRobloxUsername IS NOT NULL
              AND trim(sourceRobloxUsername) <> ''
            ORDER BY datetime(createdAt) DESC, sourceId DESC
            LIMIT 1
            """,
        ),
        (
            "honor-guard-submission",
            """
            SELECT NULL AS robloxUserId, targetRobloxUsername AS robloxUsername
            FROM hg_submissions
            WHERE targetUserId = ?
              AND trim(targetRobloxUsername) <> ''
            ORDER BY datetime(updatedAt) DESC, submissionId DESC
            LIMIT 1
            """,
        ),
        (
            "honor-guard-award",
            """
            SELECT NULL AS robloxUserId, targetRobloxUsername AS robloxUsername
            FROM hg_point_awards
            WHERE targetUserId = ?
              AND trim(targetRobloxUsername) <> ''
            ORDER BY datetime(createdAt) DESC, awardId DESC
            LIMIT 1
            """,
        ),
        (
            "honor-guard-attendance",
            """
            SELECT NULL AS robloxUserId, targetRobloxUsername AS robloxUsername
            FROM hg_attendance_records
            WHERE targetUserId = ?
              AND trim(targetRobloxUsername) <> ''
            ORDER BY datetime(createdAt) DESC, recordId DESC
            LIMIT 1
            """,
        ),
        (
            "honor-guard-sentry",
            """
            SELECT NULL AS robloxUserId, robloxUsername
            FROM hg_sentry_logs
            WHERE userId = ?
              AND trim(robloxUsername) <> ''
            ORDER BY datetime(createdAt) DESC, sentryLogId DESC
            LIMIT 1
            """,
        ),
        (
            "orbat-mirror",
            """
            SELECT robloxUserId, robloxUsername
            FROM orbat_member_mirror
            WHERE discordUserId = ?
              AND active = 1
              AND trim(robloxUsername) <> ''
            ORDER BY datetime(lastSyncedAt) DESC, sheetKey, rowNumber
            LIMIT 1
            """,
        ),
        (
            "orbat-request",
            """
            SELECT NULL AS robloxUserId, robloxUser AS robloxUsername
            FROM orbat_requests
            WHERE submitterId = ?
              AND trim(robloxUser) <> ''
            ORDER BY datetime(createdAt) DESC, requestId DESC
            LIMIT 1
            """,
        ),
    )
    for source, query in queries:
        try:
            row = await asyncio.wait_for(
                fetchOne(query, (int(discordId),)),
                timeout=_identityDbTimeoutSec(),
            )
        except Exception:
            continue
        if not row:
            continue
        username = _cleanRobloxUsername(row.get("robloxUsername"))
        if not username:
            continue
        try:
            robloxId = int(row.get("robloxUserId") or 0) or None
        except (TypeError, ValueError):
            robloxId = None
        return robloxId, username, source
    return None


async def getStoredRobloxIdentity(discordId: int) -> Optional[RoverLookupResult]:
    try:
        safeDiscordId = int(discordId)
    except (TypeError, ValueError):
        return None
    if safeDiscordId <= 0:
        return None
    try:
        row = await asyncio.wait_for(
            fetchOne(
                """
                SELECT robloxUserId, robloxUsername, source
                FROM roblox_identity_links
                WHERE discordUserId = ?
                """,
                (safeDiscordId,),
            ),
            timeout=_identityDbTimeoutSec(),
        )
    except Exception:
        return None
    if not row:
        localCandidate = await _localIdentityCandidate(safeDiscordId)
        if localCandidate is None:
            return None
        robloxId, username, source = localCandidate
        await rememberRobloxIdentity(
            safeDiscordId,
            username,
            robloxId=robloxId,
            source=f"local:{source}",
            confidence=85 if robloxId else 65,
        )
        return RoverLookupResult(
            robloxId,
            username,
            error=f"RoVer lookup unavailable; using local Roblox identity ({source}).",
        )
    username = _cleanRobloxUsername(row.get("robloxUsername"))
    if not username:
        return None
    try:
        robloxId = int(row.get("robloxUserId") or 0) or None
    except (TypeError, ValueError):
        robloxId = None
    try:
        await asyncio.wait_for(
            execute(
                "UPDATE roblox_identity_links SET lastUsedAt = datetime('now') WHERE discordUserId = ?",
                (safeDiscordId,),
            ),
            timeout=_identityDbTimeoutSec(),
        )
    except Exception:
        pass
    source = str(row.get("source") or "internal").strip() or "internal"
    return RoverLookupResult(
        robloxId,
        username,
        error=f"RoVer lookup unavailable; using internal Roblox identity link ({source}).",
    )


async def _fallbackStoredRobloxIdentity(
    discordId: int,
    guildId: Optional[int],
    error: str,
) -> RoverLookupResult:
    stored = await getStoredRobloxIdentity(discordId)
    if stored is not None:
        _roverCacheSet(discordId, guildId, stored)
        return stored
    return RoverLookupResult(None, None, error=error)


def _roverResponseError(status: int, data: object, default: str = "RoVer lookup error.") -> str:
    if isinstance(data, dict):
        code = str(data.get("errorCode") or data.get("code") or "").strip()
        message = str(data.get("message") or data.get("error") or default).strip()
        if code and message:
            return f"RoVer lookup failed ({int(status)} {code}): {message}"
        if message:
            return f"RoVer lookup failed ({int(status)}): {message}"
    return f"RoVer lookup failed ({int(status)})."


async def fetchRobloxUser(discordId: int, guildId: Optional[int] = None) -> RoverLookupResult:
    cached = _roverCacheGet(discordId, guildId)
    if cached is not None:
        return cached

    url = _roverUrl(discordId, guildId=guildId)
    headers: dict[str, str] = {}
    apiKey = getattr(config, "roverApiKey", "") or ""
    if apiKey:
        headerName = getattr(config, "roverApiKeyHeader", "Authorization") or "Authorization"
        headerValue = apiKey
        if getattr(config, "roverApiKeyUseBearer", False) and not apiKey.lower().startswith("bearer "):
            headerValue = f"Bearer {apiKey}"
        headers[headerName] = headerValue
        extraHeader = getattr(config, "roverApiKeyHeaderAlt", "") or ""
        if extraHeader:
            headers[extraHeader] = headerValue

    try:
        status, data = await robloxTransport.requestJson("GET", url, headers=headers, timeoutSec=10)
    except Exception as exc:
        return await _fallbackStoredRobloxIdentity(discordId, guildId, str(exc))

    if status != 200 or not isinstance(data, dict):
        return await _fallbackStoredRobloxIdentity(
            discordId,
            guildId,
            _roverResponseError(status, data),
        )

    if data.get("status") == "error" or data.get("success") is False:
        return await _fallbackStoredRobloxIdentity(
            discordId,
            guildId,
            _roverResponseError(status, data),
        )

    robloxId, username = extractRobloxFields(data)
    if username:
        await rememberRobloxIdentity(
            discordId,
            username,
            robloxId=robloxId,
            source="rover",
            guildId=guildId,
            confidence=100 if robloxId else 80,
        )

    if not robloxId and not username:
        return await _fallbackStoredRobloxIdentity(
            discordId,
            guildId,
            "No Roblox account linked via RoVer.",
        )

    if not robloxId:
        result = RoverLookupResult(None, username, error="No Roblox account linked via RoVer.")
        _roverCacheSet(discordId, guildId, result)
        return result

    result = RoverLookupResult(robloxId, username)
    _roverCacheSet(discordId, guildId, result)
    return result


async def fetchRobloxUserByUsername(username: str) -> RoverLookupResult:
    cleanUsername = str(username or "").strip()
    if not cleanUsername:
        return RoverLookupResult(None, None, error="Missing Roblox username.")

    url = "https://users.roblox.com/v1/usernames/users"
    body = {
        "usernames": [cleanUsername],
        "excludeBannedUsers": False,
    }
    try:
        status, data = await robloxTransport.requestJson("POST", url, jsonBody=body, timeoutSec=10)
    except Exception as exc:
        return RoverLookupResult(None, None, error=str(exc))

    if status != 200 or not isinstance(data, dict):
        return RoverLookupResult(None, None, error=f"Roblox username lookup failed ({status}).")

    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        return RoverLookupResult(None, None, error=f"No Roblox user found for `{cleanUsername}`.")

    first = rows[0]
    if not isinstance(first, dict):
        return RoverLookupResult(None, None, error="Roblox username lookup returned invalid data.")

    try:
        robloxId = int(first.get("id"))
    except (TypeError, ValueError):
        robloxId = None
    resolvedUsername = first.get("name") or first.get("displayName") or cleanUsername
    if not robloxId:
        return RoverLookupResult(None, str(resolvedUsername), error="Roblox username lookup did not return a user ID.")
    return RoverLookupResult(robloxId, str(resolvedUsername))


_roverCacheClearDiscordId = clearRobloxIdentityCache
