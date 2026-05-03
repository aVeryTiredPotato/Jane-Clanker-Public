from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import discord

from features.staff.honorGuard import sheets as honorGuardSheets
from runtime import normalization
from runtime import orbatAudit as orbatAuditRuntime


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolvedSheetLogEntry:
    discordUserId: int
    quotaDelta: float = 0
    pointsDelta: float = 0


SheetUpdateBuilder = Callable[[Sequence[ResolvedSheetLogEntry]], list[dict[str, Any]]]
SheetWriter = Callable[[list[dict[str, Any]], bool], dict[str, Any]]


def buildHonorGuardSheetUpdates(
    entries: Sequence[ResolvedSheetLogEntry],
) -> list[dict[str, Any]]:
    return [
        {
            "discordUserId": int(entry.discordUserId),
            "quotaDelta": float(entry.quotaDelta),
            "pointsDelta": float(entry.pointsDelta),
        }
        for entry in entries
    ]


async def syncApprovedLogsToSheet(
    discordUserIds: Sequence[int],
    quotaDelta: int,
    pointsDelta: int,
    *,
    organizeAfter: bool = True,
    updateBuilder: SheetUpdateBuilder = buildHonorGuardSheetUpdates,
    sheetWriter: SheetWriter | None = None,
    sheetConfigured: bool | None = None,
) -> dict[str, Any]:
    entries = [
        ResolvedSheetLogEntry(
            discordUserId=userId,
            quotaDelta=float(quotaDelta),
            pointsDelta=float(pointsDelta),
        )
        for userId in normalization.normalizeIntList(discordUserIds)
    ]
    updates = updateBuilder(entries)
    if sheetWriter is not None:
        return sheetWriter(updates, organizeAfter)
    if sheetConfigured is False:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0, "error": "sheet-disabled"}

    updatedRows = 0
    failures: list[int] = []
    for entry in entries:
        try:
            honorGuardSheets.applyMemberPointDeltas(
                discordId=int(entry.discordUserId),
                quotaDelta=float(entry.quotaDelta),
                promotionEventDelta=float(entry.pointsDelta),
            )
            updatedRows += 1
        except Exception:
            failures.append(int(entry.discordUserId))
            log.exception("Honor Guard sheet sync failed for discord user %s", int(entry.discordUserId))
    result: dict[str, Any] = {
        "updatedUsers": updatedRows,
        "updatedRows": updatedRows,
        "organized": 0,
    }
    if failures:
        result["failedUserIds"] = failures
    return result


async def sendHonorGuardSheetChangeLog(
    botClient: discord.Client,
    *,
    reviewerId: int,
    change: str,
    details: str,
    sheetKey: str = "honorGuard_members",
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
        log.exception("Failed to post Honor Guard ORBAT audit log.")
