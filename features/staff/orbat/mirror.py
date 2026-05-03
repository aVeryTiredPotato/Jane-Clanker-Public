from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

import config
from db.sqlite import fetchAll, runWriteTransaction
from features.staff.departmentOrbat.layouts import loadDepartmentLayouts
from features.staff.sessions.Roblox import roverIdentity

from .a1 import columnIndex, indexToColumn, normalizeColumn
from .multiEngine import getMultiOrbatEngine
from .multiRegistry import MultiOrbatSheetConfig


log = logging.getLogger(__name__)

_DISCORD_ID_RE = re.compile(r"(\d{15,25})")
_ROBLOX_USERNAME_RE = re.compile(r"[A-Za-z0-9_]{3,20}")
_HEADER_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_USERNAME_HEADER_WORDS = {
    "roblox",
    "robloxuser",
    "robloxusername",
    "username",
    "user",
    "members",
    "member",
    "name",
}


def _nowIso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonDump(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _asDict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _asText(value: object) -> str:
    return str(value or "").strip()


def _asInt(value: object, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _configBool(name: str, default: bool) -> bool:
    return bool(getattr(config, name, default))


def _configInt(name: str, default: int, *, minimum: int = 1, maximum: int = 5000) -> int:
    try:
        value = int(getattr(config, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def _sheetRange(sheetName: str, startCol: str, endCol: str, startRow: int, endRow: int) -> str:
    safeTitle = str(sheetName or "").replace("'", "''")
    return f"'{safeTitle}'!{normalizeColumn(startCol)}{int(startRow)}:{normalizeColumn(endCol)}{int(endRow)}"


def _columnMap(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        col = normalizeColumn(str(value or ""))
        if col:
            out[str(key)] = col
    return out


def _layoutBySheetKey() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for raw in loadDepartmentLayouts():
        if not isinstance(raw, dict):
            continue
        sheetKey = _asText(raw.get("sheetKey"))
        if sheetKey:
            out[sheetKey] = raw
    return out


def _configuredColumns(sheet: MultiOrbatSheetConfig, layout: Optional[dict[str, Any]]) -> dict[str, dict[str, str]]:
    rowModel = _asDict(sheet.rowModel)
    identity = _columnMap(rowModel.get("identity"))
    profile = _columnMap(rowModel.get("profileColumns"))
    points = _columnMap(rowModel.get("pointColumns"))
    events = _columnMap(rowModel.get("eventColumns"))

    if layout:
        usernameCol = normalizeColumn(_asText(layout.get("usernameColumn")))
        rankCol = normalizeColumn(_asText(layout.get("rankColumn")))
        if usernameCol:
            identity.setdefault("robloxUserColumn", usernameCol)
        if rankCol:
            profile.setdefault("rank", rankCol)

    return {
        "identity": identity,
        "profile": profile,
        "points": points,
        "events": events,
    }


def _sheetMirrorEligible(sheet: MultiOrbatSheetConfig, layout: Optional[dict[str, Any]]) -> bool:
    rowModel = _asDict(sheet.rowModel)
    metadata = _asDict(sheet.metadata)
    if bool(rowModel.get("mirrorEnabled")) or bool(metadata.get("orbatMirrorEnabled")):
        return True
    identity = _asDict(rowModel.get("identity"))
    for key in ("discordIdColumn", "robloxUserColumn", "robloxUsernameColumn", "usernameColumn"):
        if normalizeColumn(_asText(identity.get(key))):
            return True
    if layout and normalizeColumn(_asText(layout.get("usernameColumn"))):
        return True
    return False


def _configuredMaxRow(sheet: MultiOrbatSheetConfig, layout: Optional[dict[str, Any]]) -> int:
    rowModel = _asDict(sheet.rowModel)
    candidates = [
        _configInt("orbatMirrorMaxRows", 800, minimum=50, maximum=5000),
        _asInt(rowModel.get("maxRow"), 0),
        _asInt(rowModel.get("scanEndRow"), 0),
    ]
    if layout:
        candidates.extend(
            [
                _asInt(layout.get("membersScanEndRow"), 0),
                _asInt(layout.get("membersEndRow"), 0),
            ]
        )
    return max(value for value in candidates if value > 0)


def _configuredStartRow(sheet: MultiOrbatSheetConfig, layout: Optional[dict[str, Any]]) -> int:
    rowModel = _asDict(sheet.rowModel)
    candidates = [
        _asInt(rowModel.get("membersStartRow"), 0),
        _asInt(rowModel.get("startRow"), 0),
    ]
    if layout:
        candidates.append(_asInt(layout.get("membersStartRow"), 0))
    return max(1, next((value for value in candidates if value > 0), 1))


def _configuredMaxColumn(sheet: MultiOrbatSheetConfig, columns: dict[str, dict[str, str]], layout: Optional[dict[str, Any]]) -> str:
    maxIndex = columnIndex(str(getattr(config, "orbatMirrorMaxColumn", "AZ") or "AZ"))
    for columnSet in columns.values():
        for col in columnSet.values():
            maxIndex = max(maxIndex, columnIndex(col))
    if layout:
        for key in ("sortEndColumn", "formatEndColumn"):
            maxIndex = max(maxIndex, columnIndex(_asText(layout.get(key))))
    return indexToColumn(maxIndex)


def _headerKey(value: object) -> str:
    return _HEADER_PUNCT_RE.sub(" ", str(value or "").lower()).strip()


def _compactHeaderKey(value: object) -> str:
    return _HEADER_PUNCT_RE.sub("", str(value or "").lower()).strip()


def _fieldForHeader(label: object) -> str:
    key = _headerKey(label)
    compact = _compactHeaderKey(label)
    if not key:
        return ""

    if "discord" in key and ("id" in key or "user" in key):
        return "discordUserId"
    if "roblox" in key and "id" in key and "username" not in key:
        return "robloxUserId"
    if (
        compact in {"roblox", "robloxuser", "robloxusername", "username"}
        or ("roblox" in key and ("user" in key or "username" in key))
    ):
        return "robloxUsername"
    if key in {"rank", "current rank", "position"} or "rank" in key:
        return "rank"
    if key in {"status", "quota status", "member status"} or key.endswith(" status"):
        return "status"
    if key in {"department", "dept", "division", "office", "section"}:
        return "department"
    if any(token in key for token in ("point", "quota", "patrol", "shift", "event", "total", "all time", "hosted")):
        suffix = compact or "value"
        return f"points.{suffix}"
    return ""


def _inferHeaders(rows: list[list[Any]], scanRows: int) -> tuple[int, dict[str, str], dict[str, str]]:
    bestIndex = -1
    bestScore = 0
    bestFields: dict[str, str] = {}
    bestLabels: dict[str, str] = {}
    for rowIndex, row in enumerate(rows[:scanRows]):
        fields: dict[str, str] = {}
        labels: dict[str, str] = {}
        for valueIndex, value in enumerate(row):
            label = _asText(value)
            if not label:
                continue
            col = indexToColumn(valueIndex + 1)
            labels[col] = label
            field = _fieldForHeader(label)
            if field:
                fields.setdefault(field, col)
        identityHit = "robloxUsername" in fields or "discordUserId" in fields
        score = len(fields) + (3 if identityHit else 0)
        if score > bestScore and (identityHit or len(fields) >= 3):
            bestIndex = rowIndex
            bestScore = score
            bestFields = fields
            bestLabels = labels
    return bestIndex, bestFields, bestLabels


def _cell(row: list[Any], col: str) -> str:
    idx = columnIndex(col) - 1
    if idx < 0 or idx >= len(row):
        return ""
    return _asText(row[idx])


def _parseDiscordUserId(value: object) -> int:
    match = _DISCORD_ID_RE.search(str(value or ""))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _cleanRobloxUsername(value: object) -> str:
    text = _asText(value).strip("@")
    if not text:
        return ""
    compactHeader = _compactHeaderKey(text)
    if compactHeader in _USERNAME_HEADER_WORDS:
        return ""
    exact = _ROBLOX_USERNAME_RE.fullmatch(text)
    if exact:
        return text
    match = _ROBLOX_USERNAME_RE.search(text)
    if not match:
        return ""
    candidate = match.group(0)
    if _compactHeaderKey(candidate) in _USERNAME_HEADER_WORDS:
        return ""
    return candidate


def _parseRobloxUserId(value: object) -> Optional[int]:
    try:
        parsed = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonEmptyColumns(row: list[Any], maxColumnIndex: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for valueIndex in range(min(len(row), maxColumnIndex)):
        value = _asText(row[valueIndex])
        if value:
            out[indexToColumn(valueIndex + 1)] = value
    return out


def _rowByHeader(row: list[Any], headerLabels: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for col, label in headerLabels.items():
        value = _cell(row, col)
        if value:
            out[label] = value
    return out


def _readFirst(columns: dict[str, str], row: list[Any], *keys: str) -> str:
    for key in keys:
        col = columns.get(key)
        if not col:
            continue
        value = _cell(row, col)
        if value:
            return value
    return ""


def _buildPointValues(
    row: list[Any],
    configured: dict[str, dict[str, str]],
    headerFields: dict[str, str],
    headerLabels: dict[str, str],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for groupName in ("points", "events"):
        for name, col in configured.get(groupName, {}).items():
            value = _cell(row, col)
            if value:
                out[name] = value
    for field, col in headerFields.items():
        if not field.startswith("points."):
            continue
        label = headerLabels.get(col, field.removeprefix("points."))
        value = _cell(row, col)
        if value:
            out.setdefault(label, value)
    return out


def _rowFingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_jsonDump(payload).encode("utf-8")).hexdigest()


def _knownSectionLabels(layout: Optional[dict[str, Any]]) -> set[str]:
    labels: set[str] = set()
    if layout:
        for key in ("sectionHeaders", "managedSectionHeaders"):
            raw = layout.get(key)
            if isinstance(raw, list):
                labels.update(_asText(item).lower() for item in raw if _asText(item))
    rawConfigHeaders = getattr(config, "recruitmentSectionHeaders", [])
    if isinstance(rawConfigHeaders, list):
        labels.update(_asText(item).lower() for item in rawConfigHeaders if _asText(item))
    return labels


def _looksLikeSectionRow(rowNumber: int, row: list[Any], headerRowNumber: int, knownSections: set[str]) -> str:
    if rowNumber == headerRowNumber:
        return ""
    nonEmpty = [_asText(value) for value in row if _asText(value)]
    if not nonEmpty or len(nonEmpty) > 2:
        return ""
    label = nonEmpty[0]
    if not label:
        return ""
    lowered = label.lower()
    if lowered in knownSections:
        return label
    if len(nonEmpty) == 1 and len(label) <= 80:
        return label
    return ""


def _extractRowsForSheet(
    sheet: MultiOrbatSheetConfig,
    sheetName: str,
    rows: list[list[Any]],
    layout: Optional[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    configured = _configuredColumns(sheet, layout)
    startRow = _configuredStartRow(sheet, layout)
    headerScanRows = _configInt("orbatMirrorHeaderScanRows", 12, minimum=1, maximum=50)
    headerIndex, headerFields, headerLabels = _inferHeaders(rows, headerScanRows)
    headerRowNumber = headerIndex + 1 if headerIndex >= 0 else 0
    maxColIndex = columnIndex(_configuredMaxColumn(sheet, configured, layout))
    knownSections = _knownSectionLabels(layout)

    identityColumns = dict(configured.get("identity", {}))
    profileColumns = dict(configured.get("profile", {}))
    if "robloxUserColumn" not in identityColumns and "robloxUsername" in headerFields:
        identityColumns["robloxUserColumn"] = headerFields["robloxUsername"]
    if "discordIdColumn" not in identityColumns and "discordUserId" in headerFields:
        identityColumns["discordIdColumn"] = headerFields["discordUserId"]
    if "robloxIdColumn" not in identityColumns and "robloxUserId" in headerFields:
        identityColumns["robloxIdColumn"] = headerFields["robloxUserId"]
    for field in ("rank", "status", "department"):
        if field not in profileColumns and field in headerFields:
            profileColumns[field] = headerFields[field]

    currentSection = ""
    out: list[dict[str, Any]] = []
    for rowIndex, row in enumerate(rows):
        rowNumber = rowIndex + 1
        if rowNumber < startRow:
            continue
        if rowNumber == headerRowNumber:
            continue

        discordUserId = _parseDiscordUserId(_cell(row, identityColumns.get("discordIdColumn", "")))
        robloxUserId = _parseRobloxUserId(_cell(row, identityColumns.get("robloxIdColumn", "")))
        robloxUsername = _cleanRobloxUsername(
            _readFirst(identityColumns, row, "robloxUserColumn", "robloxUsernameColumn", "usernameColumn")
        )
        if not discordUserId and not robloxUsername:
            sectionLabel = _looksLikeSectionRow(rowNumber, row, headerRowNumber, knownSections)
            if sectionLabel:
                currentSection = sectionLabel
                continue
            continue

        rank = _readFirst(profileColumns, row, "rank", "rankColumn")
        status = _readFirst(profileColumns, row, "status", "statusColumn")
        department = _readFirst(profileColumns, row, "department", "departmentColumn", "division")
        pointValues = _buildPointValues(row, configured, headerFields, headerLabels)
        rowColumns = _nonEmptyColumns(row, maxColIndex)
        rowByHeader = _rowByHeader(row, headerLabels)

        identityJson = {
            "discordUserId": discordUserId,
            "robloxUserId": robloxUserId,
            "robloxUsername": robloxUsername,
            "columns": identityColumns,
        }
        rowJson = {
            "columns": rowColumns,
            "headers": rowByHeader,
            "headerRow": headerRowNumber,
            "rowModel": configured,
        }
        fingerprintPayload = {
            "identity": identityJson,
            "rank": rank,
            "status": status,
            "department": department,
            "sectionLabel": currentSection,
            "points": pointValues,
            "row": rowColumns,
        }
        out.append(
            {
                "sheetKey": sheet.key,
                "spreadsheetId": sheet.spreadsheetId,
                "sheetName": sheetName,
                "rowNumber": rowNumber,
                "rowFingerprint": _rowFingerprint(fingerprintPayload),
                "discordUserId": discordUserId,
                "robloxUserId": robloxUserId,
                "robloxUsername": robloxUsername,
                "robloxUsernameKey": robloxUsername.lower(),
                "rank": rank,
                "status": status,
                "department": department,
                "sectionLabel": currentSection,
                "pointsJson": _jsonDump(pointValues),
                "identityJson": _jsonDump(identityJson),
                "rowJson": _jsonDump(rowJson),
                "rawRowJson": _jsonDump([_asText(value) for value in row[:maxColIndex]]),
            }
        )

    metadata = {
        "headerRow": headerRowNumber,
        "headerFields": headerFields,
        "headerLabels": headerLabels,
        "startRow": startRow,
        "configuredColumns": configured,
    }
    return out, metadata


def buildOrbatMirrorSnapshot(sheetKeys: Optional[list[str]] = None) -> dict[str, Any]:
    engine = getMultiOrbatEngine()
    layouts = _layoutBySheetKey()
    results: dict[str, Any] = {}
    maxColumnByDefault = str(getattr(config, "orbatMirrorMaxColumn", "AZ") or "AZ")
    if sheetKeys is None:
        requestedSheetKeys = engine.listSheetKeys()
    else:
        requestedSheetKeys = [str(key) for key in sheetKeys if str(key or "").strip()]

    for sheetKey in requestedSheetKeys:
        try:
            sheet = engine.getSheetConfig(sheetKey)
        except KeyError as exc:
            results[sheetKey] = {
                "ok": False,
                "displayName": sheetKey,
                "spreadsheetId": "",
                "sheetName": "",
                "rowCount": 0,
                "memberRowCount": 0,
                "rows": [],
                "metadata": {},
                "error": str(exc),
            }
            continue
        if not _asText(sheet.spreadsheetId):
            results[sheetKey] = {
                "ok": False,
                "displayName": sheet.displayName,
                "spreadsheetId": sheet.spreadsheetId,
                "sheetName": sheet.sheetName,
                "rowCount": 0,
                "memberRowCount": 0,
                "rows": [],
                "metadata": {},
                "error": "Missing spreadsheet id.",
            }
            continue

        layout = layouts.get(sheetKey)
        if not _sheetMirrorEligible(sheet, layout):
            results[sheetKey] = {
                "ok": True,
                "skipped": True,
                "displayName": sheet.displayName,
                "spreadsheetId": sheet.spreadsheetId,
                "sheetName": sheet.sheetName,
                "rowCount": 0,
                "memberRowCount": 0,
                "rows": [],
                "metadata": {
                    "mirrorSkipped": True,
                    "reason": "No ORBAT member identity column configured.",
                },
                "error": "",
            }
            continue
        try:
            sheetName = engine.getSheetName(sheetKey)
            configuredColumns = _configuredColumns(sheet, layout)
            maxColumn = _configuredMaxColumn(sheet, configuredColumns, layout) or maxColumnByDefault
            maxRow = _configuredMaxRow(sheet, layout)
            rangeA1 = _sheetRange(sheetName, "A", maxColumn, 1, maxRow)
            values = engine.getValues(sheetKey, rangeA1, valueRenderOption="FORMATTED_VALUE")
            memberRows, metadata = _extractRowsForSheet(sheet, sheetName, values, layout)
            results[sheetKey] = {
                "ok": True,
                "displayName": sheet.displayName,
                "spreadsheetId": sheet.spreadsheetId,
                "sheetName": sheetName,
                "rowCount": len(values),
                "memberRowCount": len(memberRows),
                "rows": memberRows,
                "metadata": metadata,
                "error": "",
            }
        except Exception as exc:
            results[sheetKey] = {
                "ok": False,
                "displayName": sheet.displayName,
                "spreadsheetId": sheet.spreadsheetId,
                "sheetName": sheet.sheetName,
                "rowCount": 0,
                "memberRowCount": 0,
                "rows": [],
                "metadata": {},
                "error": f"{exc.__class__.__name__}: {exc}",
            }
    return {"ok": True, "results": results}


async def persistOrbatMirrorSnapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    results = snapshot.get("results") if isinstance(snapshot, dict) else None
    if not isinstance(results, dict):
        return {"ok": False, "error": "Invalid ORBAT mirror snapshot.", "syncedSheets": 0, "memberRows": 0}

    syncedSheets = 0
    skippedSheets = 0
    failedSheets = 0
    memberRows = 0
    linkedDiscordIds: set[int] = set()
    nowIso = _nowIso()

    async def _write(db):
        nonlocal syncedSheets, skippedSheets, failedSheets, memberRows
        for sheetKey, sheetResult in results.items():
            if not isinstance(sheetResult, dict):
                continue
            ok = bool(sheetResult.get("ok"))
            error = _asText(sheetResult.get("error"))
            rows = sheetResult.get("rows") if isinstance(sheetResult.get("rows"), list) else []
            displayName = _asText(sheetResult.get("displayName"))
            spreadsheetId = _asText(sheetResult.get("spreadsheetId"))
            sheetName = _asText(sheetResult.get("sheetName"))
            metadata = sheetResult.get("metadata") if isinstance(sheetResult.get("metadata"), dict) else {}

            if ok:
                if bool(sheetResult.get("skipped")):
                    skippedSheets += 1
                else:
                    syncedSheets += 1
                    memberRows += len(rows)
                await db.execute(
                    "UPDATE orbat_member_mirror SET active = 0, lastSyncedAt = ? WHERE sheetKey = ?",
                    (nowIso, str(sheetKey)),
                )
                if rows and not bool(sheetResult.get("skipped")):
                    await db.executemany(
                        """
                        INSERT INTO orbat_member_mirror (
                            sheetKey,
                            spreadsheetId,
                            sheetName,
                            rowNumber,
                            rowFingerprint,
                            discordUserId,
                            robloxUserId,
                            robloxUsername,
                            robloxUsernameKey,
                            rank,
                            status,
                            department,
                            sectionLabel,
                            pointsJson,
                            identityJson,
                            rowJson,
                            rawRowJson,
                            active,
                            lastSyncedAt
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                        ON CONFLICT(sheetKey, rowNumber) DO UPDATE SET
                            spreadsheetId = excluded.spreadsheetId,
                            sheetName = excluded.sheetName,
                            rowFingerprint = excluded.rowFingerprint,
                            discordUserId = excluded.discordUserId,
                            robloxUserId = excluded.robloxUserId,
                            robloxUsername = excluded.robloxUsername,
                            robloxUsernameKey = excluded.robloxUsernameKey,
                            rank = excluded.rank,
                            status = excluded.status,
                            department = excluded.department,
                            sectionLabel = excluded.sectionLabel,
                            pointsJson = excluded.pointsJson,
                            identityJson = excluded.identityJson,
                            rowJson = excluded.rowJson,
                            rawRowJson = excluded.rawRowJson,
                            active = 1,
                            lastSyncedAt = excluded.lastSyncedAt
                        """,
                        [
                            (
                                str(row.get("sheetKey") or sheetKey),
                                _asText(row.get("spreadsheetId")),
                                _asText(row.get("sheetName")),
                                _asInt(row.get("rowNumber"), 0),
                                _asText(row.get("rowFingerprint")),
                                _asInt(row.get("discordUserId"), 0),
                                row.get("robloxUserId"),
                                _asText(row.get("robloxUsername")),
                                _asText(row.get("robloxUsernameKey")),
                                _asText(row.get("rank")),
                                _asText(row.get("status")),
                                _asText(row.get("department")),
                                _asText(row.get("sectionLabel")),
                                _asText(row.get("pointsJson")) or "{}",
                                _asText(row.get("identityJson")) or "{}",
                                _asText(row.get("rowJson")) or "{}",
                                _asText(row.get("rawRowJson")) or "[]",
                                nowIso,
                            )
                            for row in rows
                            if _asInt(row.get("rowNumber"), 0) > 0
                        ],
                    )

                    identityParams = []
                    for row in rows:
                        discordId = _asInt(row.get("discordUserId"), 0)
                        username = _cleanRobloxUsername(row.get("robloxUsername"))
                        if discordId <= 0 or not username:
                            continue
                        linkedDiscordIds.add(discordId)
                        identityParams.append(
                            (
                                discordId,
                                row.get("robloxUserId"),
                                username,
                                f"orbat-mirror:{sheetKey}"[:80],
                                0,
                                70,
                                nowIso,
                            )
                        )
                    if identityParams:
                        await db.executemany(
                            """
                            INSERT INTO roblox_identity_links
                                (discordUserId, robloxUserId, robloxUsername, source, guildId, confidence, updatedAt)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(discordUserId) DO UPDATE SET
                                robloxUserId = COALESCE(roblox_identity_links.robloxUserId, excluded.robloxUserId),
                                robloxUsername = CASE
                                    WHEN roblox_identity_links.confidence <= excluded.confidence
                                    THEN excluded.robloxUsername
                                    ELSE roblox_identity_links.robloxUsername
                                END,
                                source = CASE
                                    WHEN roblox_identity_links.confidence <= excluded.confidence
                                    THEN excluded.source
                                    ELSE roblox_identity_links.source
                                END,
                                guildId = CASE
                                    WHEN roblox_identity_links.confidence <= excluded.confidence
                                    THEN excluded.guildId
                                    ELSE roblox_identity_links.guildId
                                END,
                                confidence = MAX(roblox_identity_links.confidence, excluded.confidence),
                                updatedAt = CASE
                                    WHEN roblox_identity_links.confidence <= excluded.confidence
                                    THEN excluded.updatedAt
                                    ELSE roblox_identity_links.updatedAt
                                END
                            """,
                            identityParams,
                        )
            else:
                failedSheets += 1

            await db.execute(
                """
                INSERT INTO orbat_mirror_sync_state (
                    sheetKey,
                    displayName,
                    spreadsheetId,
                    sheetName,
                    lastSyncedAt,
                    rowCount,
                    memberRowCount,
                    error,
                    metadataJson
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sheetKey) DO UPDATE SET
                    displayName = excluded.displayName,
                    spreadsheetId = excluded.spreadsheetId,
                    sheetName = excluded.sheetName,
                    lastSyncedAt = excluded.lastSyncedAt,
                    rowCount = excluded.rowCount,
                    memberRowCount = excluded.memberRowCount,
                    error = excluded.error,
                    metadataJson = excluded.metadataJson
                """,
                (
                    str(sheetKey),
                    displayName,
                    spreadsheetId,
                    sheetName,
                    nowIso if ok else None,
                    _asInt(sheetResult.get("rowCount"), 0),
                    _asInt(sheetResult.get("memberRowCount"), 0),
                    error,
                    _jsonDump(metadata),
                ),
            )

    await runWriteTransaction(_write)
    for discordId in linkedDiscordIds:
        roverIdentity.clearRobloxIdentityCache(discordId)
    return {
        "ok": failedSheets == 0,
        "syncedSheets": syncedSheets,
        "skippedSheets": skippedSheets,
        "failedSheets": failedSheets,
        "memberRows": memberRows,
    }


async def refreshAllOrbatMirrors(
    *,
    taskBudgeter: object | None = None,
    sheetKeys: Optional[list[str]] = None,
) -> dict[str, Any]:
    if not _configBool("orbatMirrorEnabled", True):
        return {
            "ok": True,
            "enabled": False,
            "syncedSheets": 0,
            "skippedSheets": 0,
            "failedSheets": 0,
            "memberRows": 0,
        }

    if sheetKeys is None:
        requestedSheetKeys = getMultiOrbatEngine().listSheetKeys()
    else:
        requestedSheetKeys = [str(key) for key in sheetKeys if str(key or "").strip()]

    summary = {
        "ok": True,
        "syncedSheets": 0,
        "skippedSheets": 0,
        "failedSheets": 0,
        "memberRows": 0,
    }
    for sheetKey in requestedSheetKeys:
        if taskBudgeter is not None and hasattr(taskBudgeter, "runBackgroundSheetsThread"):
            snapshot = await taskBudgeter.runBackgroundSheetsThread(
                buildOrbatMirrorSnapshot,
                sheetKeys=[sheetKey],
            )
        else:
            snapshot = buildOrbatMirrorSnapshot(sheetKeys=[sheetKey])
        result = await persistOrbatMirrorSnapshot(snapshot)
        summary["ok"] = bool(summary["ok"]) and bool(result.get("ok"))
        for key in ("syncedSheets", "skippedSheets", "failedSheets", "memberRows"):
            summary[key] = int(summary.get(key, 0) or 0) + int(result.get(key, 0) or 0)
    return summary


async def getActiveMirrorRowsForDiscord(discordId: int) -> list[dict[str, Any]]:
    safeDiscordId = _asInt(discordId, 0)
    if safeDiscordId <= 0:
        return []
    return await fetchAll(
        """
        SELECT *
        FROM orbat_member_mirror
        WHERE discordUserId = ?
          AND active = 1
        ORDER BY sheetKey, rowNumber
        """,
        (safeDiscordId,),
    )


async def getActiveMirrorRowsForRobloxUsername(robloxUsername: str) -> list[dict[str, Any]]:
    usernameKey = _cleanRobloxUsername(robloxUsername).lower()
    if not usernameKey:
        return []
    return await fetchAll(
        """
        SELECT *
        FROM orbat_member_mirror
        WHERE robloxUsernameKey = ?
          AND active = 1
        ORDER BY sheetKey, rowNumber
        """,
        (usernameKey,),
    )


async def getMirrorSyncState() -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT *
        FROM orbat_mirror_sync_state
        ORDER BY sheetKey
        """
    )
