from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import discord

import config
from features.staff.recruitment import sheets as recruitmentSheets
from features.staff.recruitment import sheetRules
from features.staff.sessions.Roblox import robloxUsers
from runtime import orbatAudit as orbatAuditRuntime
from runtime import taskBudgeter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SheetLogEntry:
    discordUserId: int
    pointsDelta: int = 0
    patrolDelta: int = 0
    hostedPatrolDelta: int = 0


@dataclass(frozen=True)
class ResolvedSheetLogEntry:
    discordUserId: int
    robloxUsername: str
    pointsDelta: int = 0
    patrolDelta: int = 0
    hostedPatrolDelta: int = 0


SheetUpdateBuilder = Callable[[Sequence[ResolvedSheetLogEntry]], list[dict[str, Any]]]
SheetWriter = Callable[[list[dict[str, Any]], bool], dict[str, Any]]
_robloxUsernameTokenPattern = re.compile(r"[A-Za-z0-9_]{3,20}")


def _toInt(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _normalizeEntry(raw: SheetLogEntry | Mapping[str, Any]) -> SheetLogEntry | None:
    if isinstance(raw, SheetLogEntry):
        entry = raw
    elif isinstance(raw, Mapping):
        entry = SheetLogEntry(
            discordUserId=_toInt(raw.get("discordUserId") or raw.get("userId")),
            pointsDelta=_toInt(raw.get("pointsDelta")),
            patrolDelta=_toInt(raw.get("patrolDelta")),
            hostedPatrolDelta=_toInt(raw.get("hostedPatrolDelta")),
        )
    else:
        return None
    if entry.discordUserId <= 0:
        return None
    if entry.pointsDelta == 0 and entry.patrolDelta == 0 and entry.hostedPatrolDelta == 0:
        return None
    return entry


def normalizeSheetLogEntries(entries: Sequence[SheetLogEntry | Mapping[str, Any]]) -> list[SheetLogEntry]:
    normalized: list[SheetLogEntry] = []
    for raw in entries or []:
        entry = _normalizeEntry(raw)
        if entry is not None:
            normalized.append(entry)
    return normalized


def uniqueDiscordUserIds(entries: Sequence[SheetLogEntry]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for entry in entries or []:
        userId = int(entry.discordUserId)
        if userId <= 0 or userId in seen:
            continue
        seen.add(userId)
        out.append(userId)
    return out


def normalizeDiscordUserIds(discordUserIds: Sequence[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for rawUserId in discordUserIds or []:
        userId = _toInt(rawUserId)
        if userId <= 0 or userId in seen:
            continue
        seen.add(userId)
        out.append(userId)
    return out


def _configuredRoverGuildIds() -> list[int]:
    rawGuildIds = [
        getattr(config, "recruitmentSourceGuildId", 0),
        getattr(config, "serverId", 0),
        *(getattr(config, "recruitmentRoverGuildIds", []) or []),
    ]
    out: list[int] = []
    seen: set[int] = set()
    for rawGuildId in rawGuildIds:
        guildId = _toInt(rawGuildId)
        if guildId <= 0 or guildId in seen:
            continue
        seen.add(guildId)
        out.append(guildId)
    return out


def _appendUsernameCandidate(candidates: list[str], seen: set[str], value: object) -> None:
    candidate = sheetRules.cleanRobloxUsername(value)
    if candidate.startswith("@"):
        candidate = candidate[1:]
    candidate = re.sub(r"[^A-Za-z0-9_]", "", candidate)
    key = sheetRules.usernameLookupKey(candidate)
    if not key or key in seen:
        return
    seen.add(key)
    candidates.append(candidate)


def _stripLeadingBracketPrefixes(value: str) -> tuple[str, bool]:
    text = str(value or "").strip()
    strippedAny = False
    while text.startswith("["):
        end = text.find("]")
        if end < 0:
            break
        text = text[end + 1 :].lstrip()
        strippedAny = True
    return text, strippedAny


def _cleanNicknameToken(value: object) -> str:
    token = str(value or "").strip()
    if token.startswith("@"):
        token = token[1:]
    token = re.sub(r"[^A-Za-z0-9_]", "", token)
    return sheetRules.cleanRobloxUsername(token)


def _firstNicknameUsernameToken(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    token = text.split()[0].strip()
    return _cleanNicknameToken(token)


def _rankPrefixLabels() -> list[str]:
    rawLabels = [
        *(getattr(config, "recruitmentAllowedRanks", []) or []),
        *(getattr(config, "recruitmentMembersRankOrder", []) or []),
    ]
    labels: list[str] = []
    seen: set[str] = set()
    for rawLabel in rawLabels:
        label = str(rawLabel or "").strip()
        key = sheetRules.normalize(label)
        if not label or key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return sorted(labels, key=len, reverse=True)


def _stripLeadingRankLabel(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    for label in _rankPrefixLabels():
        labelLower = label.lower()
        if lowered == labelLower:
            return ""
        if not lowered.startswith(labelLower):
            continue
        nextChar = text[len(label) : len(label) + 1]
        if nextChar and not nextChar.isspace() and nextChar not in {"-", ":", "|"}:
            continue
        return text[len(label) :].lstrip(" -:|")
    return text


def _looksLikeRankPrefixToken(value: object) -> bool:
    raw = str(value or "").strip()
    cleaned = _cleanNicknameToken(raw)
    if not cleaned:
        return False
    if any(separator in raw for separator in ("-", "/", "|")):
        return True
    return cleaned.isupper() and len(cleaned) <= 6


def _displayNameUsernameCandidates(value: object) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    bracketStripped, hadBracketPrefix = _stripLeadingBracketPrefixes(text)
    bracketStripped = re.sub(r"\([^)]*\)", " ", bracketStripped).strip()
    if hadBracketPrefix:
        _appendUsernameCandidate(candidates, seen, _firstNicknameUsernameToken(bracketStripped))
        _appendUsernameCandidate(candidates, seen, bracketStripped)

    rankStripped = _stripLeadingRankLabel(bracketStripped if hadBracketPrefix else text)
    rankStripped = re.sub(r"\[[^\]]*\]", " ", rankStripped)
    rankStripped = re.sub(r"\([^)]*\)", " ", rankStripped).strip()
    if rankStripped and rankStripped != text:
        _appendUsernameCandidate(candidates, seen, _firstNicknameUsernameToken(rankStripped))
        _appendUsernameCandidate(candidates, seen, rankStripped)

    withoutDecorators = re.sub(r"\[[^\]]*\]", " ", text)
    withoutDecorators = re.sub(r"\([^)]*\)", " ", withoutDecorators).strip()
    rawTokens = withoutDecorators.split()
    if (
        not hadBracketPrefix
        and not (rankStripped and rankStripped != text)
        and len(rawTokens) > 1
        and _looksLikeRankPrefixToken(rawTokens[0])
    ):
        _appendUsernameCandidate(
            candidates,
            seen,
            _firstNicknameUsernameToken(" ".join(rawTokens[1:])),
        )
    if not (rankStripped and rankStripped != text):
        _appendUsernameCandidate(candidates, seen, withoutDecorators)

    tokenSource = rankStripped if rankStripped and rankStripped != text else withoutDecorators
    for token in _robloxUsernameTokenPattern.findall(tokenSource):
        _appendUsernameCandidate(candidates, seen, token)
    return candidates


async def _memberDisplayNameCandidates(
    botClient: discord.Client,
    userId: int,
) -> list[str]:
    candidates: list[str] = []
    seenTexts: set[str] = set()
    for guildId in _configuredRoverGuildIds():
        guild = botClient.get_guild(guildId)
        if guild is None:
            try:
                guild = await taskBudgeter.runDiscord(lambda: botClient.fetch_guild(guildId))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                guild = None
        if guild is None:
            continue
        member = guild.get_member(int(userId))
        if member is None:
            try:
                member = await taskBudgeter.runDiscord(lambda: guild.fetch_member(int(userId)))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                member = None
        if member is None:
            continue
        for rawText in (
            getattr(member, "display_name", None),
            getattr(member, "nick", None),
            getattr(member, "global_name", None),
            getattr(member, "name", None),
        ):
            text = str(rawText or "").strip()
            if not text or text in seenTexts:
                continue
            seenTexts.add(text)
            candidates.extend(_displayNameUsernameCandidates(text))

    out: list[str] = []
    seenKeys: set[str] = set()
    for candidate in candidates:
        key = sheetRules.usernameLookupKey(candidate)
        if not key or key in seenKeys:
            continue
        seenKeys.add(key)
        out.append(candidate)
    return out


async def _resolveDisplayNameFallbacks(
    discordUserIds: Sequence[int],
    *,
    botClient: discord.Client | None,
) -> dict[int, str]:
    if botClient is None or not discordUserIds:
        return {}

    try:
        sheetUsernames = await taskBudgeter.runSheetsThread(
            recruitmentSheets.listWritableRobloxUsernames,
        )
    except Exception:
        log.exception("Failed to read recruitment sheet usernames for display-name fallback.")
        return {}

    usernameByLookupKey = {
        sheetRules.usernameLookupKey(username): str(username).strip()
        for username in sheetUsernames
        if str(username or "").strip()
    }
    if not usernameByLookupKey:
        return {}

    resolved: dict[int, str] = {}
    for userId in normalizeDiscordUserIds(discordUserIds):
        candidates = await _memberDisplayNameCandidates(botClient, userId)
        for candidate in candidates:
            matched = usernameByLookupKey.get(sheetRules.usernameLookupKey(candidate))
            if not matched:
                continue
            try:
                await robloxUsers.rememberRobloxIdentity(
                    userId,
                    matched,
                    source="discord-nickname",
                    guildId=getattr(config, "recruitmentSourceGuildId", getattr(config, "serverId", 0)),
                    confidence=70,
                )
            except Exception:
                log.exception("Failed to store internal Roblox identity link for Discord user %s.", userId)
            resolved[int(userId)] = matched
            break
    return resolved


async def resolveRobloxUsernamesForDiscordIds(
    discordUserIds: Sequence[int],
    *,
    botClient: discord.Client | None = None,
) -> dict[int, str]:
    uniqueUserIds = normalizeDiscordUserIds(discordUserIds)
    if not uniqueUserIds:
        return {}

    lookupConcurrency = max(1, int(getattr(config, "recruitmentRoverLookupConcurrency", 8) or 8))
    semaphore = asyncio.Semaphore(lookupConcurrency)
    roverGuildIds = _configuredRoverGuildIds()
    roverErrors: dict[int, str] = {}

    async def _resolve(userId: int) -> tuple[int, str] | None:
        errors: list[str] = []
        for guildId in roverGuildIds:
            try:
                async with semaphore:
                    roverResult = await robloxUsers.fetchRobloxUser(int(userId), guildId=guildId)
            except Exception as exc:
                errors.append(f"{guildId}: {exc}")
                continue

            robloxUsername = str(getattr(roverResult, "robloxUsername", "") or "").strip()
            if robloxUsername:
                return int(userId), robloxUsername

            errorText = str(getattr(roverResult, "error", "") or "").strip()
            if errorText:
                errors.append(f"{guildId}: {errorText}")
        if errors:
            roverErrors[int(userId)] = "; ".join(errors)
        else:
            roverErrors[int(userId)] = "no configured RoVer guilds returned a username"

        return None

    lookupResults = await asyncio.gather(
        *(_resolve(userId) for userId in uniqueUserIds),
        return_exceptions=False,
    )
    resolved = {
        int(userId): robloxUsername
        for result in lookupResults
        if result is not None
        for userId, robloxUsername in [result]
    }
    unresolvedUserIds = [userId for userId in uniqueUserIds if userId not in resolved]
    fallbackResolved = await _resolveDisplayNameFallbacks(
        unresolvedUserIds,
        botClient=botClient,
    )
    for userId, robloxUsername in fallbackResolved.items():
        resolved[int(userId)] = robloxUsername
        log.info(
            "Resolved recruitment sheet username for Discord user %s from Discord display name after RoVer failed.",
            userId,
        )

    for userId in uniqueUserIds:
        if userId in resolved:
            continue
        log.warning(
            "Could not resolve recruitment sheet username for Discord user %s (%s).",
            userId,
            roverErrors.get(userId, "no resolver matched"),
        )
    return resolved


def resolveSheetLogEntries(
    entries: Sequence[SheetLogEntry],
    usernameByDiscordId: Mapping[int, str],
) -> list[ResolvedSheetLogEntry]:
    resolved: list[ResolvedSheetLogEntry] = []
    for entry in entries or []:
        robloxUsername = str(usernameByDiscordId.get(int(entry.discordUserId)) or "").strip()
        if not robloxUsername:
            continue
        resolved.append(
            ResolvedSheetLogEntry(
                discordUserId=int(entry.discordUserId),
                robloxUsername=robloxUsername,
                pointsDelta=int(entry.pointsDelta),
                patrolDelta=int(entry.patrolDelta),
                hostedPatrolDelta=int(entry.hostedPatrolDelta),
            )
        )
    return resolved


def buildAnrorsSheetUpdates(
    entries: Sequence[ResolvedSheetLogEntry],
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    for entry in entries or []:
        updates.append(
            {
                "robloxUsername": entry.robloxUsername,
                "pointsDelta": int(entry.pointsDelta),
                "patrolDelta": int(entry.patrolDelta),
                "hostedPatrolDelta": int(entry.hostedPatrolDelta),
            }
        )
    return updates


async def syncApprovedLogEntriesToSheet(
    entries: Sequence[SheetLogEntry | Mapping[str, Any]],
    *,
    organizeAfter: bool = True,
    updateBuilder: SheetUpdateBuilder = buildAnrorsSheetUpdates,
    sheetWriter: SheetWriter = recruitmentSheets.applyApprovedLogsBatch,
    sheetConfigured: bool | None = None,
    botClient: discord.Client | None = None,
) -> dict[str, Any]:
    normalized = normalizeSheetLogEntries(entries)
    if not normalized:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0}
    configured = bool(getattr(config, "recruitmentSpreadsheetId", "")) if sheetConfigured is None else bool(sheetConfigured)
    if not configured:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0, "skipped": "missing-spreadsheet"}

    normalizedUserIds = [entry.discordUserId for entry in normalized]
    usernameByDiscordId = await resolveRobloxUsernamesForDiscordIds(
        normalizedUserIds,
        botClient=botClient,
    )
    unresolved = [
        int(userId)
        for userId in normalizeDiscordUserIds(normalizedUserIds)
        if int(userId) not in usernameByDiscordId
    ]
    resolvedEntries = resolveSheetLogEntries(normalized, usernameByDiscordId)
    updates = updateBuilder(resolvedEntries)
    if not updates:
        return {
            "updatedUsers": 0,
            "updatedRows": 0,
            "organized": 0,
            "skipped": "no-roblox-usernames",
            "unresolvedUsers": unresolved,
        }

    try:
        result = await taskBudgeter.runSheetsThread(
            sheetWriter,
            updates,
            bool(organizeAfter),
        )
        if unresolved:
            result = dict(result or {})
            result["skipped"] = "partial-no-roblox-usernames"
            result["unresolvedUsers"] = unresolved
        return result
    except Exception:
        affected = ", ".join(
            f"{entry.discordUserId}:{usernameByDiscordId.get(int(entry.discordUserId), '?')}"
            for entry in normalized
            if int(entry.discordUserId) in usernameByDiscordId
        )
        log.exception("Recruitment sheet batch sync failed for %s", affected or "unresolved users")
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0, "error": "sheet-sync-failed"}


async def syncApprovedLogsToSheet(
    discordUserIds: Sequence[int],
    pointsDelta: int,
    patrolDelta: int,
    hostedPatrolDelta: int = 0,
    *,
    organizeAfter: bool = True,
    updateBuilder: SheetUpdateBuilder = buildAnrorsSheetUpdates,
    sheetWriter: SheetWriter = recruitmentSheets.applyApprovedLogsBatch,
    sheetConfigured: bool | None = None,
    botClient: discord.Client | None = None,
) -> dict[str, Any]:
    entries = [
        SheetLogEntry(
            discordUserId=userId,
            pointsDelta=int(pointsDelta),
            patrolDelta=int(patrolDelta),
            hostedPatrolDelta=int(hostedPatrolDelta),
        )
        for userId in normalizeDiscordUserIds(discordUserIds)
    ]
    return await syncApprovedLogEntriesToSheet(
        entries,
        organizeAfter=organizeAfter,
        updateBuilder=updateBuilder,
        sheetWriter=sheetWriter,
        sheetConfigured=sheetConfigured,
        botClient=botClient,
    )


async def sendRecruitmentSheetChangeLog(
    botClient: discord.Client,
    *,
    reviewerId: int,
    change: str,
    details: str,
    sheetKey: str = "recruitment",
) -> None:
    try:
        await orbatAuditRuntime.sendOrbatChangeLog(
            botClient,
            change=change,
            authorizedBy=f"<@{int(reviewerId)}>",
            details=details,
            sheetKey=sheetKey,
        )
    except Exception:
        log.exception("Failed to post recruitment ORBAT audit log.")
