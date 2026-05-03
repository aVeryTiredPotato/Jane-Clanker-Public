from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import aiohttp

import config


@dataclass(frozen=True)
class ExternalSourceResult:
    source: str
    status: str
    subjectType: str
    subjectId: int
    matches: list[dict[str, Any]]
    summary: dict[str, Any]
    error: Optional[str] = None


@dataclass(frozen=True)
class ExternalScanResult:
    status: str
    matches: list[dict[str, Any]]
    details: list[dict[str, Any]]
    error: Optional[str] = None


def _cfg(configModule: Any, name: str, default: Any) -> Any:
    return getattr(configModule, name, default)


def _safeInt(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safeFloat(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _asBool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _baseUrl(value: Any, default: str) -> str:
    raw = str(value or default).strip() or default
    return raw.rstrip("/") + "/"


def _bearerHeader(token: str) -> str:
    clean = str(token or "").strip()
    if clean.lower().startswith("bearer "):
        return clean
    return f"Bearer {clean}"


def _sourceDetail(result: ExternalSourceResult) -> dict[str, Any]:
    return {
        "source": result.source,
        "status": result.status,
        "subjectType": result.subjectType,
        "subjectId": int(result.subjectId or 0),
        "matches": result.matches,
        "summary": result.summary,
        "error": result.error,
    }


async def _requestJson(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: dict[str, str],
    timeoutSec: int,
) -> tuple[int, Any, Optional[str]]:
    timeout = aiohttp.ClientTimeout(total=max(2, int(timeoutSec or 10)))
    try:
        async with session.get(url, headers=headers, timeout=timeout) as response:
            text = await response.text()
            if response.status in {204, 404}:
                return response.status, None, None
            if response.status >= 400:
                return response.status, None, f"HTTP {response.status}: {text[:300]}"
            try:
                return response.status, await response.json(content_type=None), None
            except Exception as exc:
                return response.status, None, f"Invalid JSON: {exc}"
    except asyncio.TimeoutError:
        return 0, None, "Request timed out."
    except aiohttp.ClientError as exc:
        return 0, None, str(exc)


def _pickTaseUserRecord(payload: Any, discordUserId: int) -> dict[str, Any]:
    if isinstance(payload, dict):
        if isinstance(payload.get("results"), list):
            for row in payload["results"]:
                if isinstance(row, dict) and _safeInt(row.get("userId")) == int(discordUserId):
                    return row
            return {}
        if isinstance(payload.get("data"), dict):
            return _pickTaseUserRecord(payload["data"], discordUserId)
        if isinstance(payload.get("data"), list):
            return _pickTaseUserRecord(payload["data"], discordUserId)
        if _safeInt(payload.get("userId")) == int(discordUserId):
            return payload
        return {}
    if isinstance(payload, list):
        for row in payload:
            if isinstance(row, dict) and _safeInt(row.get("userId")) == int(discordUserId):
                return row
    return {}


def _compactTaseGuild(guild: dict[str, Any]) -> dict[str, Any]:
    types: list[str] = []
    for typeRow in list(guild.get("types") or []):
        if not isinstance(typeRow, dict):
            continue
        name = str(typeRow.get("name") or "").strip()
        summary = str(typeRow.get("summary") or "").strip()
        if name and summary:
            types.append(f"{name}: {summary}")
        elif name:
            types.append(name)
    detail = guild.get("detail") if isinstance(guild.get("detail"), dict) else {}
    return {
        "id": _safeInt(guild.get("id")),
        "name": str(guild.get("name") or "").strip(),
        "score": _safeFloat(guild.get("score")),
        "firstSeen": guild.get("firstSeen"),
        "lastSeen": guild.get("lastSeen"),
        "types": types[:5],
        "detail": {
            "messages": _safeInt(detail.get("messages")),
            "typing": _safeInt(detail.get("typing")),
            "interaction": _safeInt(detail.get("interaction")),
            "indirect": _safeInt(detail.get("indirect")),
            "staff": _safeInt(detail.get("staff")),
            "booster": _safeInt(detail.get("booster")),
        },
    }


def _normalizeTasePayload(payload: Any, discordUserId: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    record = _pickTaseUserRecord(payload, discordUserId)
    if not record:
        return [], {"recordsFound": 0}

    detail = record.get("detail") if isinstance(record.get("detail"), dict) else {}
    guilds = [guild for guild in list(record.get("guilds") or []) if isinstance(guild, dict)]
    topGuilds = sorted((_compactTaseGuild(guild) for guild in guilds), key=lambda row: float(row.get("score") or 0), reverse=True)
    typeNames: list[str] = []
    for guild in topGuilds:
        for typeName in list(guild.get("types") or []):
            clean = str(typeName or "").strip()
            if clean and clean not in typeNames:
                typeNames.append(clean)

    scoreSum = _safeFloat(detail.get("scoreSum"))
    summary = {
        "recordsFound": 1 if guilds or scoreSum > 0 or detail else 0,
        "scoreSum": scoreSum,
        "guildCount": len(guilds),
        "pastOffender": _asBool(detail.get("pastOffender")),
        "appealing": _asBool(detail.get("appealing")),
        "lastSeen": detail.get("lastSeen"),
        "typeNames": typeNames[:10],
    }
    if not summary["recordsFound"]:
        return [], summary

    return [
        {
            "source": "TASE",
            "type": "discord_safety",
            "subjectType": "discord",
            "subjectId": int(discordUserId),
            "scoreSum": scoreSum,
            "guildCount": len(guilds),
            "pastOffender": summary["pastOffender"],
            "appealing": summary["appealing"],
            "lastSeen": summary["lastSeen"],
            "typeNames": typeNames[:10],
            "topGuilds": topGuilds[:5],
        }
    ], summary


async def scanTase(
    *,
    discordUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any = config,
) -> ExternalSourceResult:
    if int(discordUserId or 0) <= 0:
        return ExternalSourceResult("TASE", "SKIPPED", "discord", 0, [], {"reason": "no_discord_user"})
    if not bool(_cfg(configModule, "bgIntelligenceTaseEnabled", True)):
        return ExternalSourceResult("TASE", "SKIPPED", "discord", int(discordUserId), [], {"reason": "disabled"})

    token = str(_cfg(configModule, "bgIntelligenceTaseApiToken", "") or "").strip()
    if not token:
        return ExternalSourceResult("TASE", "SKIPPED", "discord", int(discordUserId), [], {"reason": "missing_token"})

    baseUrl = _baseUrl(_cfg(configModule, "bgIntelligenceTaseApiBaseUrl", "https://api.tasebot.org"), "https://api.tasebot.org")
    url = urljoin(baseUrl, f"v2/check/{int(discordUserId)}")
    timeoutSec = _safeInt(_cfg(configModule, "bgIntelligenceTaseTimeoutSec", 10), 10)
    statusCode, payload, error = await _requestJson(
        session,
        url,
        headers={"Authorization": _bearerHeader(token), "Accept": "application/json"},
        timeoutSec=timeoutSec,
    )
    if error:
        return ExternalSourceResult(
            "TASE",
            "ERROR",
            "discord",
            int(discordUserId),
            [],
            {"httpStatus": statusCode},
            error,
        )

    matches, summary = _normalizeTasePayload(payload, int(discordUserId))
    summary["httpStatus"] = statusCode
    return ExternalSourceResult("TASE", "OK", "discord", int(discordUserId), matches, summary)


def _compactMocoGroup(group: dict[str, Any]) -> dict[str, Any]:
    typeNames: list[str] = []
    for value in list(group.get("types") or group.get("categories") or []):
        clean = str(value.get("name") if isinstance(value, dict) else value or "").strip()
        if clean:
            typeNames.append(clean)
    return {
        "id": _safeInt(group.get("id") or group.get("groupId")),
        "name": str(group.get("name") or group.get("groupName") or "").strip(),
        "lastSeen": group.get("lastSeen") or group.get("last_seen"),
        "types": typeNames[:5],
    }


def _normalizeMocoPayload(payload: Any, robloxUserId: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        return [], {"recordsFound": 0}

    users = payload.get("users")
    record: dict[str, Any] = payload
    if isinstance(users, list):
        for row in users:
            if isinstance(row, dict) and _safeInt(row.get("userId")) == int(robloxUserId):
                record = row
                break
        else:
            record = payload if _safeInt(payload.get("userId")) == int(robloxUserId) else {}

    found = bool(record.get("found", record.get("success", payload.get("found", payload.get("success", False)))))
    rawGroups = record.get("groups") or record.get("flaggedGroups") or record.get("matches") or []
    topGroups = [
        _compactMocoGroup(group)
        for group in rawGroups
        if isinstance(group, dict)
    ][:5] if isinstance(rawGroups, list) else []
    groupCount = _safeInt(record.get("groupCount") or record.get("groupsCount") or len(topGroups))
    lastSeen = record.get("lastSeen")
    username = str(record.get("username") or "").strip()
    summary = {
        "recordsFound": 1 if found else 0,
        "username": username,
        "groupCount": groupCount,
        "lastSeen": lastSeen,
        "topGroups": topGroups,
    }
    if not found:
        return [], summary
    return [
        {
            "source": "Moco-co",
            "type": "roblox_safety",
            "subjectType": "roblox",
            "subjectId": int(robloxUserId),
            "username": username,
            "groupCount": groupCount,
            "lastSeen": lastSeen,
            "topGroups": topGroups,
        }
    ], summary


async def scanMoco(
    *,
    robloxUserId: int,
    session: aiohttp.ClientSession,
    configModule: Any = config,
) -> ExternalSourceResult:
    if int(robloxUserId or 0) <= 0:
        return ExternalSourceResult("Moco-co", "SKIPPED", "roblox", 0, [], {"reason": "no_roblox_user"})
    if not bool(_cfg(configModule, "bgIntelligenceMocoEnabled", True)):
        return ExternalSourceResult("Moco-co", "SKIPPED", "roblox", int(robloxUserId), [], {"reason": "disabled"})

    apiKey = str(_cfg(configModule, "bgIntelligenceMocoApiKey", "") or "").strip()
    if not apiKey:
        return ExternalSourceResult("Moco-co", "SKIPPED", "roblox", int(robloxUserId), [], {"reason": "missing_api_key"})

    baseUrl = _baseUrl(_cfg(configModule, "bgIntelligenceMocoApiBaseUrl", "https://api.moco-co.org"), "https://api.moco-co.org")
    url = urljoin(baseUrl, f"checkuser/{int(robloxUserId)}")
    timeoutSec = _safeInt(_cfg(configModule, "bgIntelligenceMocoTimeoutSec", 10), 10)
    statusCode, payload, error = await _requestJson(
        session,
        url,
        headers={"x-api-key": apiKey, "Accept": "application/json"},
        timeoutSec=timeoutSec,
    )
    if error:
        return ExternalSourceResult(
            "Moco-co",
            "ERROR",
            "roblox",
            int(robloxUserId),
            [],
            {"httpStatus": statusCode},
            error,
        )

    matches, summary = _normalizeMocoPayload(payload, int(robloxUserId))
    summary["httpStatus"] = statusCode
    return ExternalSourceResult("Moco-co", "OK", "roblox", int(robloxUserId), matches, summary)


async def scanExternalSources(
    *,
    discordUserId: int,
    robloxUserId: int | None,
    configModule: Any = config,
) -> ExternalScanResult:
    if not bool(_cfg(configModule, "bgIntelligenceExternalSourcesEnabled", True)):
        return ExternalScanResult("SKIPPED", [], [], "External sources disabled.")

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            scanTase(discordUserId=int(discordUserId or 0), session=session, configModule=configModule),
            scanMoco(robloxUserId=int(robloxUserId or 0), session=session, configModule=configModule),
            return_exceptions=True,
        )

    normalizedResults: list[ExternalSourceResult] = []
    for result in results:
        if isinstance(result, ExternalSourceResult):
            normalizedResults.append(result)
        elif isinstance(result, Exception):
            normalizedResults.append(
                ExternalSourceResult(
                    "External",
                    "ERROR",
                    "unknown",
                    0,
                    [],
                    {},
                    str(result),
                )
            )

    details = [_sourceDetail(result) for result in normalizedResults]
    matches: list[dict[str, Any]] = []
    for result in normalizedResults:
        matches.extend(result.matches)

    attempted = [result for result in normalizedResults if result.status != "SKIPPED"]
    errors = [result for result in attempted if result.status == "ERROR"]
    if not attempted:
        status = "SKIPPED"
    elif errors and len(errors) == len(attempted):
        status = "ERROR"
    elif errors:
        status = "PARTIAL"
    else:
        status = "OK"

    errorText = "; ".join(
        f"{result.source}: {result.error}"
        for result in errors
        if result.error
    ) or None
    return ExternalScanResult(status, matches, details, errorText)
