from __future__ import annotations

from dataclasses import dataclass, fields
import json
from typing import Any, Optional
import time

import config
from features.staff.orbat.a1 import cellRange, columnIndex, indexToColumn
from features.staff.orbat.multiEngine import getMultiOrbatEngine


_memberSheetKey = "honorGuard_members"
_archiveSheetKey = "honorGuard_archive"
_eventHostsSheetKey = "honorGuard_eventHosts"
_engine = getMultiOrbatEngine()
_rowLookupCache: dict[tuple[str, str, str, str], tuple[float, dict[str, int]]] = {}


@dataclass(slots=True, frozen=True)
class HonorGuardMemberColumns:
    discordId: str
    robloxUsername: str
    rank: str
    activityStatus: str
    quotaPoints: str
    eventPoints: str
    platoonPoints: str
    awardedPoints: str
    totalPoints: str
    juniorExamPassed: str
    ncoExamPassed: str


@dataclass(slots=True, frozen=True)
class HonorGuardMemberRow:
    row: int
    discordId: int
    robloxUsername: str
    rank: str
    activityStatus: str
    quotaPoints: float
    eventPoints: float
    platoonPoints: float
    awardedPoints: float
    totalPoints: float
    juniorExamPassed: str
    ncoExamPassed: str


@dataclass(slots=True, frozen=True)
class HonorGuardMemberPointUpdate:
    row: int
    robloxUsername: str
    previousQuotaPoints: float
    quotaPoints: float
    previousEventPoints: float
    eventPoints: float
    previousPlatoonPoints: float
    platoonPoints: float
    previousAwardedPoints: float
    awardedPoints: float
    previousTotalPoints: float
    totalPoints: float
    activityStatus: str
    passedJGE: bool
    passedNCOE: bool

@dataclass(slots=True, frozen=True)
class HonorGuardMemberPlatoonUpdate:
    row: int
    robloxUsername: str
    previousEventPoints: float
    eventPoints: float

@dataclass(slots=True, frozen=True)
class HonorGuardArchiveRecord:
    eventType: str = ""
    eventTimeUtc: str = ""
    eventTitle: str = ""
    host: str = ""
    coHosts: str = ""
    supervisors: str = ""
    eventDuration: str = ""
    eventDetail: str = ""
    attendeeCount: int = 0
    notes: str = ""
    eventId: str = ""


@dataclass(slots=True, frozen=True)
class HonorGuardEventHostUpdate:
    row: int
    host: str
    eventType: str
    column: str
    previousValue: int
    value: int

@dataclass(slots=True)
class HonorGuardMemberColumnsBatch:
    discordId: int
    robloxUsername: str
    rank: str
    quotaPoints: float
    activityStatus: str
    eventPoints: float
    awardedPoints: float
    platoonPoints: float
    totalPoints: float
    juniorExamPassed: bool
    ncoExamPassed: bool
    promotionEligible: bool

@dataclass(slots=True)
class HonorGuardPlatoonColumnsBatch:
    discordId: int
    robloxUsername: str
    rank: str
    platoonPoints: float

@dataclass(slots=True)
class ApprovedLogUpdate:
    robloxUsername: str
    discordId: int
    quotaDelta: float
    eventDelta: float
    platoonDelta: float
    juniorExamPassed: bool | None
    ncoExamPassed: bool | None

@dataclass(slots=True)
class HonorGuardMemberRowBatch:
    row: int
    discordId: int
    robloxUsername: str
    rank: str
    quotaPoints: float
    activityStatus: str
    eventPoints: float
    platoonPoints: float
    awardedPoints: float
    totalPoints: float
    juniorExamPassed: bool
    ncoExamPassed: bool
    promotionEligible: bool

@dataclass(slots=True)
class HonorGuardPlatoonRowBatch:
    row: int
    discordId: int
    robloxUsername: str
    rank: str
    platoonPoints: float

@dataclass(slots=True)
class MemberResolvedUpdate:
    member: HonorGuardMemberRowBatch
    update: ApprovedLogUpdate

@dataclass(slots=True)
class PlatoonResolvedUpdate:
    platoon: HonorGuardPlatoonRowBatch
    update: ApprovedLogUpdate

def _normalizeColumn(value: object) -> str:
    return str(value or "").strip().upper()


def _normalizeKey(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _normalizeUsername(value: object) -> str:
    return "".join(ch for ch in str(value or "").strip().casefold() if not ch.isspace())


def _toInt(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value or "").strip()))
    except (TypeError, ValueError):
        return default


def _toFloat(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value or "").strip())
    except (TypeError, ValueError):
        return default

def _toBool(value: object, default: bool = False) -> bool:
    text = str(value or "").strip().upper()
    if text == "TRUE":
        return True
    if text == "FALSE":
        return False
    return default

def _pointValue(value: float) -> int | float:
    numeric = float(value or 0)
    if numeric.is_integer():
        return int(numeric)
    return round(numeric, 3)


def _sheetName(sheetKey: str) -> str:
    return _engine.getSheetName(sheetKey)


def _sheetAvailable(sheetKey: str) -> bool:
    try:
        _engine.getSheetConfig(sheetKey)
        return True
    except KeyError:
        return False
    
def _range(sheetKey, col: str, row: int) -> str:
    return cellRange(_sheetName(sheetKey=sheetKey), col, row)


def configuredSheetKeys() -> tuple[str, ...]:
    return tuple(
        key
        for key in (_memberSheetKey, _archiveSheetKey, _eventHostsSheetKey)
        if _sheetAvailable(key)
    )

def _rowLookupCacheTtlSec() -> float:
    try:
        value = float(getattr(config, "recruitmentRowLookupCacheTtlSec", 120) or 120)
    except (TypeError, ValueError):
        value = 120.0
    return max(0.0, value)

def _isWritableMemberRow(usernameCell: str, rankCell: str) -> bool:
    return isHonorGuardOrbatLabel(usernameCell) and isAllowedHonorGuardRank(rankCell)

def _isWritablePlatoonRow(usernameCell: str, rankCell: str, platoon) -> bool:
    return isHonorGuardOrbatLabel(usernameCell) and isAllowedPlatoonRank(rankCell, platoon)

def normalize(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

def isHonorGuardOrbatLabel(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = normalize(text)
    if normalized in {"robloxusername", "robloxuser", "ruser", "username"}:
        return False
    lowered = text.lower()
    if "personnel" in lowered:
        return False
    memberHeaders = getattr(config, "honorGuardMemberSectionHeaders", []) or []
    platoonHeaders = getattr(config, "honorGuardPlatoonSectionHeaders", []) or []
    sectionHeaders = {normalize(item) for item in (memberHeaders or platoonHeaders) if item}
    if normalized in sectionHeaders:
        return False
    return True

def isAllowedHonorGuardRank(value: str) -> bool:
    rank = str(value or "").strip()
    if not rank:
        return False
    allowed = getattr(config, "honorGuardAllowedRanks", []) or []
    allowedSet = {normalize(item) for item in allowed if item}
    return normalize(rank) in allowedSet

def isAllowedPlatoonRank(value: str, platoon: str) -> bool:
    platoonNorm = platoon.strip().casefold()
    rank = str(value or "").strip()
    if not rank:
        return False

    allowedMap = {
        normalize(k): v
        for k, v in getattr(config, "honorGuardPlatoonAllowedRanks", {}).items()
    }
    allowed = allowedMap.get(platoonNorm, [])

    allowedSet = {normalize(item) for item in allowed if item}
    return normalize(rank) in allowedSet

def configurationProblems(*, configModule: Any = config) -> tuple[str, ...]:
    problems: list[str] = []
    if not bool(getattr(configModule, "honorGuardEnabled", False)):
        problems.append("honorGuardEnabled is false")
    if not str(getattr(configModule, "honorGuardSpreadsheetId", "") or "").strip():
        problems.append("honorGuardSpreadsheetId is not set")
    if not str(getattr(configModule, "honorGuardMemberSheetName", "") or "").strip():
        problems.append("honorGuardMemberSheetName is not set")
    if not str(getattr(configModule, "honorGuardArchiveSheetName", "") or "").strip():
        problems.append("honorGuardArchiveSheetName is not set")
    if not str(getattr(configModule, "honorGuardEventHostsSheetName", "") or "").strip():
        problems.append("honorGuardEventHostsSheetName is not set")
    if not _sheetAvailable(_memberSheetKey):
        problems.append("Honor Guard member sheet is not registered")
    if not _sheetAvailable(_archiveSheetKey):
        problems.append("Honor Guard archive sheet is not registered")
    if not _sheetAvailable(_eventHostsSheetKey):
        problems.append("Honor Guard event hosts sheet is not registered")
    return tuple(problems)


def loadMemberColumns(*, configModule: Any = config) -> HonorGuardMemberColumns:
    return HonorGuardMemberColumns(
        discordId=_normalizeColumn(getattr(configModule, "honorGuardDiscordIdColumn", "")),
        robloxUsername=_normalizeColumn(getattr(configModule, "honorGuardRobloxUsernameColumn", "A")),
        rank=_normalizeColumn(getattr(configModule, "honorGuardRankColumn", "B")),
        activityStatus=_normalizeColumn(getattr(configModule, "honorGuardActivityStatusColumn", "G")),
        quotaPoints=_normalizeColumn(getattr(configModule, "honorGuardQuotaPointsColumn", "E")),
        eventPoints=_normalizeColumn(getattr(configModule, "honorGuardEventPointsColumn", "J")),
        platoonPoints=_normalizeColumn(getattr(configModule, "honorGuardTotalPlatoonPointsColumn", "L")),
        awardedPoints=_normalizeColumn(getattr(configModule, "honorGuardAwardedPointsColumn", "K")),
        totalPoints=_normalizeColumn(getattr(configModule, "honorGuardTotalPointsColumn", "M")),
        juniorExamPassed=_normalizeColumn(getattr(configModule, "honorGuardJuniorExamPassedColumn", "N")),
        ncoExamPassed=_normalizeColumn(getattr(configModule, "honorGuardNcoExamPassedColumn", "O")),
    )

def loadMemberColumnsBatch(*, configModule: Any = config) -> HonorGuardMemberColumnsBatch:
    return HonorGuardMemberColumnsBatch(
        discordId=_normalizeColumn(getattr(configModule, "honorGuardDiscordIdColumn", "")),
        robloxUsername=_normalizeColumn(getattr(configModule, "honorGuardRobloxUsernameColumn", "A")),
        rank=_normalizeColumn(getattr(configModule, "honorGuardRankColumn", "B")),
        quotaPoints=_normalizeColumn(getattr(configModule, "honorGuardQuotaPointsColumn", "E")),
        activityStatus=_normalizeColumn(getattr(configModule, "honorGuardActivityStatusColumn", "G")),
        eventPoints=_normalizeColumn(getattr(configModule, "honorGuardEventPointsColumn", "J")),
        platoonPoints=_normalizeColumn(getattr(configModule, "honorGuardTotalPlatoonPointsColumn", "L")),
        awardedPoints=_normalizeColumn(getattr(configModule, "honorGuardAwardedPointsColumn", "K")),
        totalPoints=_normalizeColumn(getattr(configModule, "honorGuardTotalPointsColumn", "M")),
        juniorExamPassed=_normalizeColumn(getattr(configModule, "honorGuardJuniorExamPassedColumn", "N")),
        ncoExamPassed=_normalizeColumn(getattr(configModule, "honorGuardNcoExamPassedColumn", "O")),
        promotionEligible=_normalizeColumn(getattr(configModule, "honorGuardPromotionEligibleFormulaColumn", "P")),
    )

def loadPlatoonColumnsBatch(*, configModule: Any = config) -> HonorGuardPlatoonColumnsBatch:
    return HonorGuardPlatoonColumnsBatch(
        discordId=_normalizeColumn(getattr(configModule, "honorGuardPlatoonDiscordIdColumn", "A")),
        robloxUsername=_normalizeColumn(getattr(configModule, "honorGuardPlatoonRobloxUsernameColumn", "B")),
        rank=_normalizeColumn(getattr(configModule, "honorGuardPlatoonRankColumn", "C")),
        platoonPoints=_normalizeColumn(getattr(configModule, "honorGuardPlatoonPointsColumn", "E")),
    )

def _columnMap(columns: HonorGuardMemberColumns) -> dict[str, str]:
    return {
        "discordId": columns.discordId,
        "robloxUsername": columns.robloxUsername,
        "rank": columns.rank,
        "activityStatus": columns.activityStatus,
        "quotaPoints": columns.quotaPoints,
        "eventPoints": columns.eventPoints,
        "platoonPoints": columns.platoonPoints,
        "awardedPoints": columns.awardedPoints,
        "totalPoints": columns.totalPoints,
        "juniorExamPassed": columns.juniorExamPassed,
        "ncoExamPassed": columns.ncoExamPassed,
    }


def _readColumn(sheetKey: str, columnLetter: str) -> list[str]:
    if not columnLetter:
        return []
    rows = _engine.getValues(sheetKey, f"{_sheetName(sheetKey)}!{columnLetter}:{columnLetter}")
    return [str(row[0]).strip() if row else "" for row in rows]


def _findRowByNormalizedValue(
    sheetKey: str,
    *,
    columnLetter: str,
    value: object,
    normalizer,
) -> Optional[int]:
    target = normalizer(value)
    if not target:
        return None
    for rowIndex, current in enumerate(_readColumn(sheetKey, columnLetter), start=1):
        if normalizer(current) == target:
            return rowIndex
    return None


def findMemberRow(
    *,
    discordId: int = 0,
    robloxUsername: str = "",
    configModule: Any = config,
) -> Optional[int]:
    columns = loadMemberColumns(configModule=configModule)
    if int(discordId or 0) > 0 and columns.discordId:
        row = _findRowByNormalizedValue(
            _memberSheetKey,
            columnLetter=columns.discordId,
            value=str(int(discordId)),
            normalizer=lambda raw: str(_toInt(raw, default=0)) if _toInt(raw, default=0) > 0 else str(raw).strip(),
        )
        if row:
            return row
    if str(robloxUsername or "").strip() and columns.robloxUsername:
        return _findRowByNormalizedValue(
            _memberSheetKey,
            columnLetter=columns.robloxUsername,
            value=robloxUsername,
            normalizer=_normalizeUsername,
        )
    return None

def findPlatoonRow(robloxUsername: str, sheetKey: str, configModule: Any = config) -> Optional[int]:
    usernameColumn = _normalizeColumn(getattr(configModule, "honorGuardPlatoonRobloxUsernameColumn", "A"))
    if not usernameColumn:
        return None
    return _findRowByNormalizedValue(
        sheetKey,
        columnLetter=usernameColumn,
        value=robloxUsername,
        normalizer=_normalizeUsername,
    )


def readMember(
    *,
    discordId: int = 0,
    robloxUsername: str = "",
    row: int = 0,
    configModule: Any = config,
) -> Optional[HonorGuardMemberRow]:
    rowIndex = int(row or 0)
    if rowIndex <= 0:
        resolved = findMemberRow(
            discordId=int(discordId or 0),
            robloxUsername=robloxUsername,
            configModule=configModule,
        )
        if resolved is None:
            return None
        rowIndex = int(resolved)

    columns = loadMemberColumns(configModule=configModule)
    values = _engine.readRowColumns(
        _memberSheetKey,
        row=rowIndex,
        columnMap=_columnMap(columns),
    )
    event = _toFloat(values.get("eventPoints"))
    platoon = _toFloat(values.get("platoonPoints"))
    awarded = _toFloat(values.get("awardedPoints"))
    total = _toFloat(values.get("totalPoints"), event + platoon + awarded)
    return HonorGuardMemberRow(
        row=rowIndex,
        discordId=_toInt(values.get("discordId")),
        robloxUsername=str(values.get("robloxUsername") or "").strip(),
        rank=str(values.get("rank") or "").strip(),
        activityStatus=str(values.get("activityStatus") or "").strip(),
        quotaPoints=_toFloat(values.get("quotaPoints")),
        eventPoints=event,
        platoonPoints=platoon,
        awardedPoints=awarded,
        totalPoints=total,
        juniorExamPassed=str(values.get("juniorExamPassed") or "").strip(),
        ncoExamPassed=str(values.get("ncoExamPassed") or "").strip(),
    )

def _isExcuseStatus(value: object, *, configModule: Any = config) -> bool:
    statusKey = _normalizeKey(value)
    if not statusKey:
        return False
    configured = getattr(configModule, "honorGuardExcuseStatusValues", []) or []
    return statusKey in {_normalizeKey(item) for item in configured}

def applyMemberPointDeltas(
    *,
    discordId: int = 0,
    robloxUsername: str = "",
    quotaDelta: float = 0,
    eventDelta: float = 0,
    platoonDelta: float = 0,
    awardedDelta: float = 0,
    passedJGE: bool = False,
    passedNCOE: bool = False,
    promoteWhenEligible: bool = True,
    markActiveWhenEarlyQuotaMet: bool = True,
    configModule: Any = config,
) -> HonorGuardMemberPointUpdate:
    member = readMember(discordId=discordId, robloxUsername=robloxUsername, configModule=configModule)
    if member is None:
        lookup = robloxUsername or str(discordId or "")
        raise ValueError(f"Honor Guard member not found in sheet: {lookup}")

    columns = loadMemberColumns(configModule=configModule)
    nextQuota = max(0.0, float(member.quotaPoints) + float(quotaDelta or 0))
    nextEvent = max(0.0, float(member.eventPoints) + float(eventDelta or 0))
    nextPlatoon = max(0.0, float(member.platoonPoints) + float(platoonDelta or 0))
    nextAwarded = max(0.0, float(member.awardedPoints) + float(awardedDelta or 0))
    nextTotal = nextEvent + nextPlatoon + nextAwarded

    status = member.activityStatus
    earlyQuota = float(getattr(configModule, "honorGuardEarlyActiveQuotaPoints", 8) or 8)
    if (
        markActiveWhenEarlyQuotaMet
        and columns.activityStatus
        and nextQuota >= earlyQuota
        and not _isExcuseStatus(status, configModule=configModule)
    ):
        status = "Active"

    updates: dict[str, tuple[str, Any]] = {}
    if columns.quotaPoints:
        updates["quotaPoints"] = (columns.quotaPoints, _pointValue(nextQuota))
    if columns.eventPoints:
        updates["eventPoints"] = (columns.eventPoints, _pointValue(nextEvent))
    if columns.platoonPoints:
        updates["platoonPoints"] = (columns.platoonPoints, _pointValue(nextPlatoon))
    if columns.awardedPoints:
        updates["awardedPoints"] = (columns.awardedPoints, _pointValue(nextAwarded))
    if columns.activityStatus and status != member.activityStatus:
        updates["activityStatus"] = (columns.activityStatus, status)
    juniorAlreadyTrue = _toBool(member.juniorExamPassed)
    ncoAlreadyTrue = _toBool(member.ncoExamPassed)
    if columns.juniorExamPassed and passedJGE and not juniorAlreadyTrue:
        updates["juniorExamPassed"] = (columns.juniorExamPassed, True)
    if columns.ncoExamPassed and passedNCOE and not ncoAlreadyTrue:
        updates["ncoExamPassed"] = (columns.ncoExamPassed, True)

    _engine.writeRowColumns(_memberSheetKey, row=member.row, columnValues=updates)
    return HonorGuardMemberPointUpdate(
        row=member.row,
        robloxUsername=member.robloxUsername,
        previousQuotaPoints=member.quotaPoints,
        quotaPoints=nextQuota,
        previousEventPoints=member.eventPoints,
        eventPoints=nextEvent,
        previousPlatoonPoints=member.platoonPoints,
        platoonPoints=nextPlatoon,
        previousAwardedPoints=member.awardedPoints,
        awardedPoints=nextAwarded,
        previousTotalPoints=member.totalPoints,
        totalPoints=nextTotal,
        activityStatus=status,
        passedJGE=passedJGE and not juniorAlreadyTrue,
        passedNCOE=passedNCOE and not ncoAlreadyTrue,
    )

def applyMemberPlatoonPoints(
    *,
    platoon: str,
    discordId: int = 0,
    robloxUsername: str = "",
    eventDelta: float = 0,
    configModule: Any = config,
) -> HonorGuardMemberPlatoonUpdate:
    sheetKey = f"honorGuard_platoon_{_normalizeKey(platoon)}"
    row = findPlatoonRow(robloxUsername, sheetKey, configModule=configModule )
    column = _normalizeColumn(getattr(configModule, "honorGuardPlatoonPointsColumn", "D"))
    if row is None:
        return HonorGuardMemberPlatoonUpdate(
            row=0,
            robloxUsername=robloxUsername,
            previousEventPoints=0,
            eventPoints=0,
        )

    rangeA1 = f"{_sheetName(sheetKey)}!{column}{row}:{column}{row}"
    values = _engine.getValues(sheetKey, rangeA1)
    previousValue = _toInt(values[0][0] if values and values[0] else 0)
    nextValue = max(0, previousValue + int(eventDelta or 0))
    _engine.writeRowColumns(
        sheetKey,
        row=row,
        columnValues={"platoonPoints": (column, nextValue)},
    )
    return HonorGuardMemberPlatoonUpdate(
        row=row,
        robloxUsername=robloxUsername,
        previousEventPoints=previousValue,
        eventPoints=nextValue,
    )

def archiveEvent(record: HonorGuardArchiveRecord, *, configModule: Any = config) -> dict[str, Any]:
    columns = list(getattr(configModule, "honorGuardArchiveColumns", []) or [])
    if not columns:
        columns = [
            "eventType",
            "eventTimeUtc",
            "host",
            "coHosts",
            "supervisors",
            "eventDuration",
            "eventDetail",
            "notes",
        ]
    valuesByKey = {
        "eventType": record.eventType.title(),
        "eventTimeUtc": record.eventTimeUtc,
        "eventDate": record.eventTimeUtc,
        "eventTitle": record.eventTitle,
        "host": record.host,
        "coHosts": record.coHosts,
        "supervisors": record.supervisors,
        "eventDuration": record.eventDuration,
        "eventDetail": record.eventDetail,
        "attendeeCount": int(record.attendeeCount or 0),
        "notes": record.notes,
        "eventId": record.eventId,
    }
    rowValues = [valuesByKey.get(str(key or "").strip(), "") for key in columns]
    return _engine.appendValues(
        _archiveSheetKey,
        rangeA1=f"{_sheetName(_archiveSheetKey)}!A:A",
        values=[rowValues],
    )


def _eventHostColumnForEventType(eventType: str, *, configModule: Any = config) -> str:
    eventKey = _normalizeKey(eventType)
    if not eventKey:
        return ""

    configured = getattr(configModule, "honorGuardEventHostEventTypeColumns", {}) or {}
    if isinstance(configured, dict):
        for rawKey, rawColumn in configured.items():
            if _normalizeKey(rawKey) == eventKey:
                return _normalizeColumn(rawColumn)

    examsColumn = _normalizeColumn(getattr(configModule, "honorGuardEventHostExamsColumn", "G"))
    trainingsColumn = _normalizeColumn(getattr(configModule, "honorGuardEventHostTrainingsColumn", "H"))
    tryoutsColumn = _normalizeColumn(getattr(configModule, "honorGuardEventHostTryoutsColumn", "I"))
    inspectionsColumn = _normalizeColumn(getattr(configModule, "honorGuardEventHostInspectionsColumn", "J"))

    fallback: dict[str, str] = {
        "jge": examsColumn,
        "juniorguardsmanexam": examsColumn,
        "ncoexam": examsColumn,
        "exam": examsColumn,
        "examination": examsColumn,
        "orientation": trainingsColumn,
        "training": trainingsColumn,
        "lecture": trainingsColumn,
        "drill": trainingsColumn,
        "tryout": tryoutsColumn,
        "honorguardtryout": tryoutsColumn,
        "inspection": inspectionsColumn,
        "mockinspection": inspectionsColumn,
    }
    return fallback.get(eventKey, "")


def findEventHostRow(host: str, *, configModule: Any = config) -> Optional[int]:
    usernameColumn = _normalizeColumn(getattr(configModule, "honorGuardEventHostUsernameColumn", "A"))
    if not usernameColumn:
        return None
    return _findRowByNormalizedValue(
        _eventHostsSheetKey,
        columnLetter=usernameColumn,
        value=host,
        normalizer=_normalizeUsername,
    )


def incrementEventHostStats(
    *,
    host: str,
    eventType: str,
    delta: int = 1,
    configModule: Any = config,
) -> Optional[HonorGuardEventHostUpdate]:
    column = _eventHostColumnForEventType(eventType, configModule=configModule)
    if not column:
        return None

    row = findEventHostRow(host, configModule=configModule)
    if row is None:
        return None

    rangeA1 = f"{_sheetName(_eventHostsSheetKey)}!{column}{row}:{column}{row}"
    values = _engine.getValues(_eventHostsSheetKey, rangeA1)
    previousValue = _toInt(values[0][0] if values and values[0] else 0)
    nextValue = max(0, previousValue + int(delta or 0))
    _engine.writeRowColumns(
        _eventHostsSheetKey,
        row=row,
        columnValues={"eventHostStat": (column, nextValue)},
    )
    return HonorGuardEventHostUpdate(
        row=row,
        host=str(host or "").strip(),
        eventType=str(eventType or "").strip(),
        column=column,
        previousValue=previousValue,
        value=nextValue,
    )


def memberSheetLastConfiguredColumn(*, configModule: Any = config) -> str:
    columns = loadMemberColumns(configModule=configModule)
    indexes = [columnIndex(col) for col in _columnMap(columns).values() if col]
    return indexToColumn(max(indexes, default=1))


_PLATOON_SHEET_PREFIX = "honorGuard_platoon_"


def _loadWritableMemberRowsByUsername(columns, sheetKey: str) -> tuple[dict[str, int], dict[str, tuple[int, int]]]:
    usernameCol = columns.robloxUsername
    rankCol = columns.rank
    cacheKey = (sheetKey, _sheetName(sheetKey), str(usernameCol).upper(), str(rankCol).upper())
    ttlSec = _rowLookupCacheTtlSec()
    now = time.monotonic()
    cached = _rowLookupCache.get(cacheKey)
    if cached and ttlSec > 0 and (now - cached[0]) <= ttlSec:
        cachedRows, cachedBounds = cached[1]
        return dict(cachedRows), dict(cachedBounds)

    usernameAndRank = _engine.batchGetValues(
        sheetKey,
        [
            f"{_sheetName(sheetKey)}!{usernameCol}:{usernameCol}",
            f"{_sheetName(sheetKey)}!{rankCol}:{rankCol}",
        ]
    )

    isMemberSheet = sheetKey == _memberSheetKey
    platoonName = "" if isMemberSheet else sheetKey.removeprefix(_PLATOON_SHEET_PREFIX)
    previousRank: str | None = None
    rankBounds: dict[str, tuple[int, int]] = {}
    lastRowIdx = 0
    rankStart = 0
    usernames = usernameAndRank[0].get("values", []) if len(usernameAndRank) > 0 else []
    ranks = usernameAndRank[1].get("values", []) if len(usernameAndRank) > 1 else []
    rowByUsername: dict[str, int] = {}
    totalRows = max(len(usernames), len(ranks))
    for idx in range(1, totalRows + 1):
        usernameRow = usernames[idx - 1] if idx - 1 < len(usernames) else []
        rankRow = ranks[idx - 1] if idx - 1 < len(ranks) else []
        usernameCell = str(usernameRow[0]) if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        if isMemberSheet:
            if not _isWritableMemberRow(usernameCell, rankCell):
                continue
        else:
            if not _isWritablePlatoonRow(usernameCell, rankCell, platoonName):
                continue
        if previousRank is None:
            previousRank = rankCell
            rankStart = idx
        elif rankCell != previousRank:
            rankBounds[previousRank.casefold()] = (rankStart, lastRowIdx)
            previousRank = rankCell
            rankStart = idx
        lastRowIdx = idx
        rowByUsername[usernameCell.casefold()] = idx
    if previousRank is not None:
        rankBounds[previousRank.casefold()] = (rankStart, lastRowIdx)
    if ttlSec > 0:
        _rowLookupCache[cacheKey] = (now, (dict(rowByUsername), dict(rankBounds)))
    return dict(rowByUsername), dict(rankBounds)

def _resolveApprovedLogRows(
    aggregate: dict[str, dict[str, int | str]],
    rowByUsername: dict[str, int],
    platoonRowByUsername: dict[str, int] = None,
) -> list[dict[int, dict[str, int | str]]|list[str]]:
    updatesByRow: dict[int, dict[str, int | str]] = {}
    platoonUpdatesByRow: dict[int, dict[str, int | str]] = {}
    errors: list[str] = []
    for key, entry in aggregate.items():
        row = rowByUsername.get(key)
        if not row:
            errors.append(f"Members: {key}")
            continue
        updatesByRow[row] = entry
        if platoonRowByUsername is not None:
            platoonRow = platoonRowByUsername.get(key)
            if not platoonRow:
                errors.append(f"Platoon: {key}")
                continue
            platoonUpdatesByRow[platoonRow] = entry
    return updatesByRow, platoonUpdatesByRow, errors

def _aggregateApprovedLogUpdates(updates: list[dict], platoon: str) -> tuple[dict[str, ApprovedLogUpdate], list[dict]]:
    aggregate: dict[str, ApprovedLogUpdate] = {}
    skipped: list[dict] = []
    for raw in updates:
        if not isinstance(raw, dict):
            continue
        discordId = int(raw.get("userId") or 0)
        username = str(raw.get("robloxUsername") or "").strip()
        if not username:
            skipped.append({
                "userId": discordId,
                "reason": "user lookup failed",
            })
            continue
        if not discordId:
            skipped.append({
                "userId": 0,
                "reason": f"missing Discord user id (roblox={username})",
            })
            continue
        if platoon != "NONE":
            eventDelta = 0
            try:
                platoonDelta = float(raw.get("eventDelta") or 0)
            except (TypeError, ValueError):
                platoonDelta = 0
        else:
            platoonDelta = 0
            try:
                eventDelta = float(raw.get("eventDelta") or 0)
            except (TypeError, ValueError):
                eventDelta = 0
        try:
            quotaDelta = float(raw.get("quotaDelta") or 0)
        except (TypeError, ValueError):
            quotaDelta = 0
        if raw.get("juniorExamPassed") is None:
            juniorExamPassed = None
        else:
            try:
                juniorExamPassed = bool(raw.get("juniorExamPassed", False))
            except (TypeError, ValueError):
                juniorExamPassed = None
        if raw.get("ncoExamPassed") is None:
            ncoExamPassed = None
        else:
            try:
                ncoExamPassed = bool(raw.get("ncoExamPassed", False))
            except (TypeError, ValueError):
                ncoExamPassed = None

        key = username.casefold()
        slot = aggregate.get(key)
        if slot is None:
            aggregate[key] = ApprovedLogUpdate(
                robloxUsername = username,
                discordId = discordId,
                quotaDelta = quotaDelta,
                eventDelta = eventDelta,
                platoonDelta = platoonDelta,
                juniorExamPassed = juniorExamPassed,
                ncoExamPassed = ncoExamPassed,
            )
        else:
            slot.quotaDelta += quotaDelta
            slot.eventDelta += eventDelta
            slot.platoonDelta += platoonDelta
            if juniorExamPassed:
                slot.juniorExamPassed = True
            if ncoExamPassed:
                slot.ncoExamPassed = True
    return aggregate, skipped

def _loadApprovedLogCurrentRows(
    sheetId: str,
    rows: list[int],
    columns: dataclass,
) -> dict[int, dict[str, str]]:
    perRowRanges: list[str] = []
    rangeMeta: list[tuple[int, str]] = []
    for row in rows:
        for column in fields(columns):
            value = getattr(columns, column.name)
            if not value or value == "":
                continue
            perRowRanges.append(_range(sheetId, getattr(columns, column.name), row))
            rangeMeta.append((row, column.name))
    fetchedRanges = _engine.batchGetValues(sheetId, perRowRanges)
    currentByRow: dict[int, dict[str, str]] = {}
    for idx, (row, key) in enumerate(rangeMeta):
        values = fetchedRanges[idx].get("values", []) if idx < len(fetchedRanges) else []
        value = values[0][0] if values and values[0] else ""
        rowData = currentByRow.setdefault(row, {})
        rowData[key] = str(value)
    return currentByRow

def _loadOrbatData(
        platoon: str,
        memberIds: list[int],
        platoonIds: list[int] | None = None,
        *,
        configModule: Any = config,
    ) -> dict[str, HonorGuardMemberRowBatch]:
    columns = loadMemberColumnsBatch(configModule=configModule)
    platoonColumns = loadPlatoonColumnsBatch(configModule=configModule)

    rawOrbatData = _loadApprovedLogCurrentRows(_memberSheetKey, memberIds, columns)
    
    orbatData: dict[str, HonorGuardMemberRowBatch] = {}
    platoonData: dict[str, HonorGuardPlatoonRowBatch] = {}


    for i, rowNumber in enumerate(rawOrbatData, start=1):
        row = rawOrbatData.get(rowNumber)
        username = str(row.get("robloxUsername")).strip()
        rank = str(row.get("rank")).strip()

        orbatData[username.casefold()] = HonorGuardMemberRowBatch(
            row = rowNumber,
            discordId=0,
            robloxUsername=username,
            rank = rank,
            quotaPoints = _toFloat(row.get("quotaPoints", 0.0)),
            activityStatus = str(row.get("activityStatus", 'Inactive')).strip(),
            eventPoints = _toFloat(row.get("eventPoints", 0.0)),
            awardedPoints = _toFloat(row.get("awardedPoints", 0.0)),
            totalPoints = _toFloat(row.get("totalPoints", 0.0)),
            platoonPoints = _toFloat(row.get("platoonPoints", 0.0)),
            juniorExamPassed = _toBool(row.get("juniorExamPassed", False)),
            ncoExamPassed = _toBool(row.get("ncoExamPassed", False)),
            promotionEligible = _toBool(row.get("promotionEligible", False)),
        )


    if platoon != "NONE" and platoonIds:
        sheetKey = f"honorGuard_platoon_{_normalizeKey(platoon)}"
        rawPlatoonData = _loadApprovedLogCurrentRows(sheetKey, platoonIds, platoonColumns)
        for rowNumber, row in rawPlatoonData.items():
            username = str(row.get("robloxUsername")).strip()
            rank = str(row.get("rank")).strip()

            platoonData[username.casefold()] = HonorGuardPlatoonRowBatch(
                row = rowNumber,
                discordId = 0,
                robloxUsername=username,
                rank = rank,
                platoonPoints = _toFloat(row.get("platoonPoints", 0.0)),
            )
    return orbatData, platoonData

def _buildApprovedLogBatchData(
    columns: HonorGuardMemberColumnsBatch,
    platoonColumns: HonorGuardPlatoonColumnsBatch,
    updatesByRowMember: dict[int, ApprovedLogUpdate],
    updatesByRowPlatoon: dict[int, ApprovedLogUpdate],
    memberRows: dict[str, HonorGuardMemberRowBatch],
    platoonRows: dict[str, HonorGuardPlatoonRowBatch] | None = None,
) -> tuple[list[dict], list[int], list[dict], list[int], list[int, dict]]:
    batchDataMember: dict[int, dict[str, tuple[str, Any]]] = {}
    touchedRowsMember: list[int] = []
    batchDataPlatoon: dict[int, dict[str, tuple[str, Any]]] = {}
    touchedRowsPlatoon: list[int] = []
    organizeAfter = False
    for row, update in updatesByRowMember.items():
        member = memberRows.get(update.robloxUsername.casefold())

        nextRank = None
        nextActivityStatus = None
        nextQuotaPoints = max(0.0, member.quotaPoints + update.quotaDelta)
        nextEventPoints = max(0.0, member.eventPoints + update.eventDelta)
        nextPlatoonPoints = max(0.0, member.platoonPoints + update.platoonDelta)
        nextTotalPoints = nextEventPoints + nextPlatoonPoints + member.awardedPoints
        nextJuniorExamPassed = None
        nextNcoExamPassed = None
        nextPromotionEligible = None

        if member.activityStatus in ('Inactive', 'N/A') and nextQuotaPoints >= 8:
            nextActivityStatus = 'Active'
        if not member.juniorExamPassed and update.juniorExamPassed:
            nextJuniorExamPassed = True
        if not member.ncoExamPassed and update.ncoExamPassed:
            nextNcoExamPassed = True
        if member.rank == "Junior Guardsman" and nextTotalPoints >= 15 and (member.juniorExamPassed or nextJuniorExamPassed):
            nextRank = "Guardsman"
            nextPromotionEligible = False
            organizeAfter = True
        if member.rank == "Guardsman" and nextTotalPoints >= 50 and (member.ncoExamPassed or nextNcoExamPassed) and (member.activityStatus == 'Active' or nextActivityStatus == 'Active'):
            nextPromotionEligible = True
        if member.rank == "Junior Guardsman" and nextTotalPoints >= 50 and (member.ncoExamPassed or nextNcoExamPassed) and (member.juniorExamPassed or nextJuniorExamPassed) and (member.activityStatus == 'Active' or nextActivityStatus == 'Active'):
            nextRank = "Guardsman"
            nextPromotionEligible = True
            organizeAfter = True

        rowData = {}
        if nextRank is not None:
            rowData["rank"] = (columns.rank, nextRank)
        if update.quotaDelta != 0:
            rowData["quotaPoints"] = (columns.quotaPoints, nextQuotaPoints)
        if nextActivityStatus is not None:
            rowData["activityStatus"] = (columns.activityStatus, nextActivityStatus)
        if update.eventDelta != 0:
            rowData["eventPoints"] = (columns.eventPoints, nextEventPoints)
        if update.platoonDelta != 0:
            rowData["platoonPoints"] = (columns.platoonPoints, nextPlatoonPoints)
        if nextJuniorExamPassed is not None:
            rowData["juniorExamPassed"] = (columns.juniorExamPassed, nextJuniorExamPassed)
        if nextNcoExamPassed is not None:
            rowData["ncoExamPassed"] = (columns.ncoExamPassed, nextNcoExamPassed)
        if nextPromotionEligible is not None:
            rowData["promotionEligible"] = (columns.promotionEligible, nextPromotionEligible)
        
        batchDataMember[row] = rowData

        touchedRowsMember.append(row)

    if platoonRows is not None:
        for row, update in updatesByRowPlatoon.items():
            platoon = platoonRows.get(update.robloxUsername.casefold())
            if platoon is None:
                continue
            if update.platoonDelta == 0:
                continue
            nextPlatoonPoints = max(0.0, platoon.platoonPoints + update.platoonDelta)

            batchDataPlatoon[platoon.row] = {
                "platoonPoints": (platoonColumns.platoonPoints, nextPlatoonPoints),
            }
            touchedRowsPlatoon.append(platoon.row)

    return batchDataMember, touchedRowsMember, batchDataPlatoon, touchedRowsPlatoon, organizeAfter

def _organizeHonorGuardMembers(rankBounds: dict[str, tuple[int, int]], batchDataMember: dict[int, dict[str, tuple[str, Any]]]) -> None:
    moves: list[tuple[int,int]] = []
    for row in sorted(batchDataMember.keys()):
        rowData = batchDataMember[row]
        nextRank = rowData.get("rank", (None, None))[1]
        if not nextRank:
            continue
        nextRankKey = nextRank.casefold()
        bounds = rankBounds.get(nextRankKey)
        if bounds is None:
            continue
        targetRow = bounds[1]
        if not targetRow or targetRow == row:
            continue
        moves.append((row, targetRow))

    _engine.moveRows(_memberSheetKey, moves=moves)

def applyApprovedLogsBatch(
    updates: list[dict],
    eventPlatoon: str,
    *,
    configModule: Any = config,
) -> dict:
    auditLog = []
    if not updates:
        auditLog.append(
            {"error": f"No updates - applyApprovedLogsBatch() stopped. \nUPDATES: {updates}"}
        )
        return auditLog
    configModule = config

    columns = loadMemberColumnsBatch(configModule=configModule)
    platoonColumns = loadPlatoonColumnsBatch(configModule=configModule)
    rowByUsername, rankBounds = _loadWritableMemberRowsByUsername(columns, _memberSheetKey)
    platoonRowByUsername: dict[str, int] | None = None
    if eventPlatoon != "NONE":
        platoonRows, _platoonBounds = _loadWritableMemberRowsByUsername(
            platoonColumns,
            f"{_PLATOON_SHEET_PREFIX}{_normalizeKey(eventPlatoon)}",
        )
        platoonRowByUsername = platoonRows


    aggregate, skipped = _aggregateApprovedLogUpdates(updates, eventPlatoon)
    for drop in skipped:
        mention = f"<@{drop['userId']}>" if drop.get("userId") else "unknown"
        auditLog.append({
            "error": None,
            "userId": drop.get("userId", 0),
            "skipped": True,
            "note": f"Skipped {mention}: {drop['reason']}",
        })
    if not aggregate:
        auditLog.append(
            {"error": "_aggregateApprovedLogUpdates() produced no updates."}
        )
        return auditLog

    spreadsheetId = str(getattr(config, "honorGuardSpreadsheetId", "") or "").strip()
    if not spreadsheetId:
        auditLog.append(
            {"error": f"Invalid SpreadsheetId - {spreadsheetId}"}
        )
        return auditLog

    updatesByRowMember, updatesByRowPlatoon, errors = _resolveApprovedLogRows(aggregate, rowByUsername, platoonRowByUsername)
    if len(errors) > 0:
        auditLog.append(
            {"error": f"_resolveApprovedLogRows() failed for {len(errors)} members: {errors}"}
        )
    if not updatesByRowMember:
        auditLog.append(
            {"error": "_resolveApprovedLogRows() failed."}
        )
        return auditLog
    rows = sorted(updatesByRowMember.keys())
    platoonRows = sorted(updatesByRowPlatoon.keys()) if updatesByRowPlatoon else []

    orbatMembers, orbatPlatoon = _loadOrbatData(eventPlatoon, rows, platoonRows, configModule=configModule)

    batchDataMember, touchedRowsMember, batchDataPlatoon, touchedRowsPlatoon, organizeAfter = _buildApprovedLogBatchData(columns, platoonColumns, updatesByRowMember, updatesByRowPlatoon, orbatMembers, orbatPlatoon)

    _engine.writeRowsColumnsBatch(_memberSheetKey, rows=touchedRowsMember, columnValuesByRow=batchDataMember)
  # if len(touchedRowsPlatoon) > 0:
  #     platoonSheetKey = f"honorGuard_platoon_{_normalizeKey(eventPlatoon)}"
  #     _engine.writeRowsColumnsBatch(platoonSheetKey, rows=touchedRowsPlatoon, columnValuesByRow=batchDataPlatoon)

    if organizeAfter:
        _organizeHonorGuardMembers(rankBounds=rankBounds, batchDataMember=batchDataMember)

    platoonByUsername = {
        p.robloxUsername.casefold(): p
        for p in updatesByRowPlatoon.values()
    }

    for row, rowData in batchDataMember.items():
        update = updatesByRowMember[row]

        member = orbatMembers.get(update.robloxUsername.casefold())

        attendeePlatoon = platoonByUsername.get(member.robloxUsername.casefold())
        platoonMember = orbatPlatoon.get(member.robloxUsername.casefold()) if attendeePlatoon is not None else None

        nextRank = rowData.get("rank", (None, member.rank))[1]
        nextQuotaPoints = max(0.0, member.quotaPoints + update.quotaDelta)
        nextEventPoints = max(0.0, member.eventPoints + update.eventDelta)
        nextMemberPlatoonPoints = max(0.0, member.platoonPoints + update.platoonDelta)
        nextTotalPoints = nextEventPoints + nextMemberPlatoonPoints + member.awardedPoints
        if member.activityStatus == "Inactive" and nextQuotaPoints >= 8:
            nextActivityStatus = "Active"
        else:
            nextActivityStatus = member.activityStatus
        nextPromotionEligible = rowData.get(
            "promotionEligible",
            (None, member.promotionEligible),
        )[1]
        #nextJuniorExamPassed = rowData.get(
        #    "juniorExamPassed", (None, member.juniorExamPassed),
        #)[1]
        #nextNcoExamPassed = rowData.get(
        #    "ncoExamPassed", (None, member.ncoExamPassed),
        #)[1]
        promotion = None

        if nextRank != member.rank:
            promotion = f"Promoted from {member.rank} to {nextRank}"
        elif nextPromotionEligible:
            if member.rank == "Junior Guardsman":
                promotion = "Eligible for Promotion to Guardsman"
            elif member.rank == "Guardsman":
                promotion = "Eligible for Promotion to Senior Guardsman"

        auditLog.append({
            "error": None,
            "userId": update.discordId,
            "promotion": promotion,
            "pointUpdate": HonorGuardMemberPointUpdate(
                row=member.row,
                robloxUsername=member.robloxUsername,
                previousQuotaPoints=member.quotaPoints,
                quotaPoints=nextQuotaPoints,
                previousEventPoints=member.eventPoints,
                eventPoints=nextEventPoints,
                previousPlatoonPoints=platoonMember.platoonPoints if platoonMember else member.platoonPoints,
                platoonPoints=(max(0.0, platoonMember.platoonPoints + update.platoonDelta)) if platoonMember else nextMemberPlatoonPoints,
                previousAwardedPoints=member.awardedPoints,
                awardedPoints=member.awardedPoints,
                previousTotalPoints=member.totalPoints,
                totalPoints=nextTotalPoints,
                activityStatus=nextActivityStatus,
                passedJGE=update.juniorExamPassed,
                passedNCOE=update.ncoExamPassed,
            ),
        })
        
    return auditLog
