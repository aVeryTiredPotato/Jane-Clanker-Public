from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any, Optional

import config
from features.staff.bgItemReview import service as itemReviewService
from features.staff.bgItemReview import workflow as itemReviewWorkflow
from runtime import googleOAuth, orgProfiles, taskBudgeter

log = logging.getLogger(__name__)

_ENTRY_DENIED_VALUES = {"denied", "reject", "rejected", "failed", "fail"}


@dataclass(slots=True)
class SpreadsheetDecisionRow:
    spreadsheetId: str
    spreadsheetName: str
    sheetName: str
    rowNumber: int
    discordUserId: int
    robloxUsername: str
    inventoryLabel: str
    entryStatus: str


def _positiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _configValue(name: str, *, guildId: int = 0, default: object = None) -> object:
    return orgProfiles.getOrganizationValue(
        config,
        name,
        guildId=int(guildId or 0),
        default=default,
    )


def _syncEnabled(guildId: int = 0) -> bool:
    return bool(
        _configValue(
            "bgItemReviewSpreadsheetSyncEnabled",
            guildId=guildId,
            default=getattr(config, "bgItemReviewSpreadsheetSyncEnabled", True),
        )
    )


def _folderId(guildId: int = 0) -> str:
    return str(
        _configValue(
            "bgCheckSpreadsheetFolderId",
            guildId=guildId,
            default=getattr(config, "bgCheckSpreadsheetFolderId", ""),
        )
        or ""
    ).strip()


def _sheetName(guildId: int = 0) -> str:
    return str(
        _configValue(
            "bgCheckSpreadsheetSheetName",
            guildId=guildId,
            default=getattr(config, "bgCheckSpreadsheetSheetName", "Sheet1"),
        )
        or "Sheet1"
    ).strip() or "Sheet1"


def _sourceGuildId(guildId: int = 0) -> int:
    return _positiveInt(
        _configValue(
            "bgCheckSourceGuildId",
            guildId=guildId,
            default=getattr(config, "bgCheckSourceGuildId", getattr(config, "serverId", 0)),
        )
    )


def _scanLimit(guildId: int = 0) -> int:
    return max(
        1,
        min(
            _positiveInt(
                _configValue(
                    "bgItemReviewSpreadsheetSyncScanLimit",
                    guildId=guildId,
                    default=getattr(config, "bgItemReviewSpreadsheetSyncScanLimit", 10),
                )
            )
            or 10,
            50,
        ),
    )


def _startupLookbackDays(guildId: int = 0) -> int:
    return max(
        1,
        min(
            _positiveInt(
                _configValue(
                    "bgItemReviewSpreadsheetStartupLookbackDays",
                    guildId=guildId,
                    default=getattr(config, "bgItemReviewSpreadsheetStartupLookbackDays", 5),
                )
            )
            or 5,
            30,
        ),
    )


def _recurringLookbackDays(guildId: int = 0) -> int:
    return max(
        1,
        min(
            _positiveInt(
                _configValue(
                    "bgItemReviewSpreadsheetRecurringLookbackDays",
                    guildId=guildId,
                    default=getattr(config, "bgItemReviewSpreadsheetRecurringLookbackDays", 1),
                )
            )
            or 1,
            30,
        ),
    )


def _maxRows(guildId: int = 0) -> int:
    return max(
        10,
        min(
            _positiveInt(
                _configValue(
                    "bgItemReviewSpreadsheetSyncMaxRows",
                    guildId=guildId,
                    default=getattr(config, "bgItemReviewSpreadsheetSyncMaxRows", 250),
                )
            )
            or 250,
            2000,
        ),
    )


def _quotedSheetName(sheetName: str) -> str:
    safeName = str(sheetName or "Sheet1").replace("'", "''")
    return f"'{safeName}'"


def _modifiedAfterIso(*, lookbackDays: int) -> str:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max(1, int(lookbackDays or 1)))
    return cutoff.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _listRecentSpreadsheetFiles(
    *,
    folderId: str,
    limit: int,
    modifiedAfterIso: str = "",
) -> list[dict[str, Any]]:
    drive = googleOAuth.buildService("drive", "v3")
    queryParts = [
        f"'{str(folderId).strip()}' in parents",
        "mimeType='application/vnd.google-apps.spreadsheet'",
        "trashed=false",
    ]
    normalizedModifiedAfter = str(modifiedAfterIso or "").strip()
    if normalizedModifiedAfter:
        queryParts.append(f"modifiedTime >= '{normalizedModifiedAfter}'")
    query = " and ".join(queryParts)
    result = drive.files().list(
        q=query,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
        corpora="allDrives",
        fields="files(id,name,modifiedTime)",
        orderBy="modifiedTime desc",
        pageSize=int(limit),
    ).execute()
    return [row for row in list(result.get("files") or []) if isinstance(row, dict)]


def _readSpreadsheetRows(
    *,
    spreadsheetId: str,
    sheetName: str,
    maxRows: int,
) -> list[list[str]]:
    sheets = googleOAuth.buildService("sheets", "v4")
    result = sheets.spreadsheets().values().get(
        spreadsheetId=str(spreadsheetId).strip(),
        range=f"{_quotedSheetName(sheetName)}!A2:K{int(maxRows) + 1}",
    ).execute()
    rows: list[list[str]] = []
    for rawRow in list(result.get("values") or []):
        if not isinstance(rawRow, list):
            continue
        rows.append([str(cell or "") for cell in rawRow])
    return rows


def _normalizeEntryStatus(value: object) -> str:
    text = str(value or "").strip().lower()
    if text in _ENTRY_DENIED_VALUES:
        return "denied"
    if text in {"accepted", "accept", "approved", "approve", "passed", "pass"}:
        return "accepted"
    return text


def _parseDecisionRow(
    *,
    spreadsheetId: str,
    spreadsheetName: str,
    sheetName: str,
    rowNumber: int,
    rawRow: list[str],
) -> Optional[SpreadsheetDecisionRow]:
    cells = list(rawRow or [])
    if len(cells) < 11:
        cells.extend([""] * (11 - len(cells)))

    discordUserId = _positiveInt(cells[4])
    if discordUserId <= 0:
        return None

    robloxUsername = str(cells[5] or "").strip()
    if robloxUsername == "~":
        robloxUsername = ""
    inventoryLabel = str(cells[6] or "").strip().lower()
    if inventoryLabel == "~":
        inventoryLabel = ""

    return SpreadsheetDecisionRow(
        spreadsheetId=str(spreadsheetId or "").strip(),
        spreadsheetName=str(spreadsheetName or "").strip(),
        sheetName=str(sheetName or "").strip(),
        rowNumber=int(rowNumber or 0),
        discordUserId=discordUserId,
        robloxUsername=robloxUsername,
        inventoryLabel=inventoryLabel,
        entryStatus=_normalizeEntryStatus(cells[9]),
    )


def _rowFingerprint(row: SpreadsheetDecisionRow) -> str:
    raw = "|".join(
        [
            str(row.discordUserId),
            str(row.robloxUsername or "").strip().lower(),
            str(row.inventoryLabel or "").strip().lower(),
            str(row.entryStatus or "").strip().lower(),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


async def syncDeniedSpreadsheetRows(
    botClient: Any,
    *,
    guildId: int = 0,
    lookbackDays: int | None = None,
) -> dict[str, int | str | bool]:
    normalizedGuildId = int(guildId or 0)
    if not _syncEnabled(normalizedGuildId):
        return {"enabled": False, "files": 0, "rows": 0, "denied": 0, "created": 0, "existing": 0, "known": 0, "errors": 0}

    folderId = _folderId(normalizedGuildId)
    if not folderId:
        return {
            "enabled": True,
            "files": 0,
            "rows": 0,
            "denied": 0,
            "created": 0,
            "existing": 0,
            "known": 0,
            "errors": 1,
            "reason": "No BGC spreadsheet folder configured.",
        }

    sheetName = _sheetName(normalizedGuildId)
    effectiveGuildId = int(normalizedGuildId or _sourceGuildId(normalizedGuildId) or 0)
    effectiveLookbackDays = max(1, int(lookbackDays or _recurringLookbackDays(normalizedGuildId) or 1))
    files = await taskBudgeter.runBackgroundSheetsThread(
        _listRecentSpreadsheetFiles,
        folderId=folderId,
        limit=_scanLimit(normalizedGuildId),
        modifiedAfterIso=_modifiedAfterIso(lookbackDays=effectiveLookbackDays),
    )

    filesScanned = 0
    rowsSeen = 0
    deniedRows = 0
    createdCount = 0
    existingCount = 0
    knownCount = 0
    errorCount = 0
    unchangedCount = 0
    updatedStates = 0

    for fileRow in files:
        spreadsheetId = str(fileRow.get("id") or "").strip()
        spreadsheetName = str(fileRow.get("name") or "").strip()
        if not spreadsheetId:
            continue
        filesScanned += 1
        try:
            rawRows = await taskBudgeter.runBackgroundSheetsThread(
                _readSpreadsheetRows,
                spreadsheetId=spreadsheetId,
                sheetName=sheetName,
                maxRows=_maxRows(normalizedGuildId),
            )
        except Exception:
            log.exception("Failed reading BGC spreadsheet %s (%s).", spreadsheetName or spreadsheetId, spreadsheetId)
            errorCount += 1
            continue

        for offset, rawRow in enumerate(rawRows, start=2):
            parsed = _parseDecisionRow(
                spreadsheetId=spreadsheetId,
                spreadsheetName=spreadsheetName,
                sheetName=sheetName,
                rowNumber=offset,
                rawRow=rawRow,
            )
            if parsed is None:
                continue
            rowsSeen += 1
            fingerprint = _rowFingerprint(parsed)
            existingState = await itemReviewService.getSheetSyncState(
                spreadsheetId=parsed.spreadsheetId,
                sheetName=parsed.sheetName,
                rowNumber=parsed.rowNumber,
            )
            if str((existingState or {}).get("fingerprint") or "").strip() == fingerprint:
                unchangedCount += 1
                continue

            queued = False
            if parsed.entryStatus == "denied":
                deniedRows += 1
                queueResult = await itemReviewWorkflow.queueRejectedAttendeeInventory(
                    botClient,
                    session={"guildId": effectiveGuildId},
                    attendee={
                        "userId": int(parsed.discordUserId),
                        "robloxUsername": parsed.robloxUsername or None,
                    },
                    reviewerId=0,
                    guild=None,
                )
                createdCount += int(queueResult.get("created") or 0)
                existingCount += int(queueResult.get("existing") or 0)
                knownCount += int(queueResult.get("known") or 0)
                errorCount += int(queueResult.get("errors") or 0)
                queued = (
                    int(queueResult.get("created") or 0) > 0
                    or int(queueResult.get("existing") or 0) > 0
                    or int(queueResult.get("known") or 0) > 0
                )

            await itemReviewService.upsertSheetSyncState(
                spreadsheetId=parsed.spreadsheetId,
                sheetName=parsed.sheetName,
                rowNumber=parsed.rowNumber,
                discordUserId=parsed.discordUserId,
                entryStatus=parsed.entryStatus,
                fingerprint=fingerprint,
                queued=queued,
            )
            updatedStates += 1

    return {
        "enabled": True,
        "files": filesScanned,
        "rows": rowsSeen,
        "denied": deniedRows,
        "lookbackDays": effectiveLookbackDays,
        "created": createdCount,
        "existing": existingCount,
        "known": knownCount,
        "errors": errorCount,
        "unchanged": unchangedCount,
        "stateUpdates": updatedStates,
    }
