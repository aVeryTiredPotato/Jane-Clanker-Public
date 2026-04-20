from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import discord

import config
from features.staff.recruitment import sheets as recruitmentSheets
from features.staff.sessions import roblox
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


async def resolveRobloxUsernamesForDiscordIds(discordUserIds: Sequence[int]) -> dict[int, str]:
    uniqueUserIds = normalizeDiscordUserIds(discordUserIds)
    if not uniqueUserIds:
        return {}

    lookupConcurrency = max(1, int(getattr(config, "recruitmentRoverLookupConcurrency", 8) or 8))
    semaphore = asyncio.Semaphore(lookupConcurrency)

    async def _resolve(userId: int) -> tuple[int, str] | None:
        try:
            async with semaphore:
                roverResult = await roblox.fetchRobloxUser(int(userId))
        except Exception:
            log.exception("RoVer lookup failed while syncing recruitment sheet for %s", userId)
            return None

        robloxUsername = str(getattr(roverResult, "robloxUsername", "") or "").strip()
        if not robloxUsername:
            return None
        return int(userId), robloxUsername

    lookupResults = await asyncio.gather(
        *(_resolve(userId) for userId in uniqueUserIds),
        return_exceptions=False,
    )
    return {
        int(userId): robloxUsername
        for result in lookupResults
        if result is not None
        for userId, robloxUsername in [result]
    }


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
) -> dict[str, Any]:
    normalized = normalizeSheetLogEntries(entries)
    if not normalized:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0}
    configured = bool(getattr(config, "recruitmentSpreadsheetId", "")) if sheetConfigured is None else bool(sheetConfigured)
    if not configured:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0, "skipped": "missing-spreadsheet"}

    usernameByDiscordId = await resolveRobloxUsernamesForDiscordIds(
        [entry.discordUserId for entry in normalized]
    )
    resolvedEntries = resolveSheetLogEntries(normalized, usernameByDiscordId)
    updates = updateBuilder(resolvedEntries)
    if not updates:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0, "skipped": "no-roblox-usernames"}

    try:
        return await taskBudgeter.runSheetsThread(
            sheetWriter,
            updates,
            bool(organizeAfter),
        )
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
