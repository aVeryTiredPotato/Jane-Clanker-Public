from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import discord

import config
from features.staff.sessions import roblox
from runtime import orbatAudit as orbatAuditRuntime
from runtime import taskBudgeter
from features.staff.honorGuard import sheets as honorGuardSheets

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class ResolvedSheetLogEntry:
    discordUserId: int
    robloxUsername: str
    quotaDelta: int = 0
    pointsDelta: int = 0

SheetUpdateBuilder = Callable[[Sequence[ResolvedSheetLogEntry]], list[dict[str, Any]]]
SheetWriter = Callable[[list[dict[str, Any]], bool], dict[str, Any]]

def buildHonorGuardSheetUpdates(
    entries: Sequence[ResolvedSheetLogEntry],
) -> list[dict[str, Any]]:
    pass

async def syncApprovedLogsToSheet(
    discordUserIds: Sequence[int],
    quotaDelta: int,
    pointsDelta: int,
    *,
    organizeAfter: bool = True,
    updateBuilder: SheetUpdateBuilder = buildHonorGuardSheetUpdates,
    sheetWriter: SheetWriter = honorGuardSheets.applyApprovedLogsBatch,
    sheetConfigured: bool | None = None,
) -> dict[str, Any]:
    pass


async def sendHonorGuardSheetChangeLog(
    botClient: discord.Client,
    *,
    reviewerId: int,
    change: str,
    details: str,
    sheetKey: str = "honorguard",
) -> None:
    pass
