from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import config
from features.staff.orbat.a1 import columnIndex, indexToColumn
from features.staff.orbat.multiEngine import getMultiOrbatEngine


_memberSheetKey = "honorGuard_members"
_scheduleSheetKey = "honorGuard_schedule"
_archiveSheetKey = "honorGuard_archive"
_eventHostsSheetKey = "honorGuard_eventHosts"
_engine = getMultiOrbatEngine()


@dataclass(slots=True, frozen=True)
class HonorGuardMemberColumns:
    discordId: str
    robloxUsername: str
    rank: str
    activityStatus: str
    quotaPoints: str
    promotionEventPoints: str
    promotionAwardedPoints: str
    promotionTotalPoints: str
    hostedEvents: str
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
    promotionEventPoints: float
    promotionAwardedPoints: float
    promotionTotalPoints: float
    hostedEvents: int
    juniorExamPassed: str
    ncoExamPassed: str


@dataclass(slots=True, frozen=True)
class HonorGuardMemberPointUpdate:
    row: int
    robloxUsername: str
    previousQuotaPoints: float
    quotaPoints: float
    previousPromotionEventPoints: float
    promotionEventPoints: float
    previousPromotionAwardedPoints: float
    promotionAwardedPoints: float
    previousPromotionTotalPoints: float
    promotionTotalPoints: float
    previousHostedEvents: int
    hostedEvents: int
    activityStatus: str


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
class HonorGuardScheduleRemovalResult:
    row: int
    action: str


@dataclass(slots=True, frozen=True)
class HonorGuardEventHostUpdate:
    row: int
    host: str
    eventType: str
    column: str
    previousValue: int
    value: int


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


def configuredSheetKeys() -> tuple[str, ...]:
    return tuple(
        key
        for key in (_memberSheetKey, _scheduleSheetKey, _archiveSheetKey, _eventHostsSheetKey)
        if _sheetAvailable(key)
    )


def configurationProblems(*, configModule: Any = config) -> tuple[str, ...]:
    problems: list[str] = []
    if not bool(getattr(configModule, "honorGuardEnabled", False)):
        problems.append("honorGuardEnabled is false")
    if not str(getattr(configModule, "honorGuardSpreadsheetId", "") or "").strip():
        problems.append("honorGuardSpreadsheetId is not set")
    if not str(getattr(configModule, "honorGuardMemberSheetName", "") or "").strip():
        problems.append("honorGuardMemberSheetName is not set")
    if not str(getattr(configModule, "honorGuardScheduleSheetName", "") or "").strip():
        problems.append("honorGuardScheduleSheetName is not set")
    if not str(getattr(configModule, "honorGuardArchiveSheetName", "") or "").strip():
        problems.append("honorGuardArchiveSheetName is not set")
    if not str(getattr(configModule, "honorGuardEventHostsSheetName", "") or "").strip():
        problems.append("honorGuardEventHostsSheetName is not set")
    if not _sheetAvailable(_memberSheetKey):
        problems.append("Honor Guard member sheet is not registered")
    if not _sheetAvailable(_scheduleSheetKey):
        problems.append("Honor Guard schedule sheet is not registered")
    if not _sheetAvailable(_archiveSheetKey):
        problems.append("Honor Guard archive sheet is not registered")
    if not _sheetAvailable(_eventHostsSheetKey):
        problems.append("Honor Guard event hosts sheet is not registered")
    return tuple(problems)


def loadMemberColumns(*, configModule: Any = config) -> HonorGuardMemberColumns:
    return HonorGuardMemberColumns(
        discordId=_normalizeColumn(getattr(configModule, "honorGuardDiscordIdColumn", "A")),
        robloxUsername=_normalizeColumn(getattr(configModule, "honorGuardRobloxUsernameColumn", "B")),
        rank=_normalizeColumn(getattr(configModule, "honorGuardRankColumn", "C")),
        activityStatus=_normalizeColumn(getattr(configModule, "honorGuardActivityStatusColumn", "D")),
        quotaPoints=_normalizeColumn(getattr(configModule, "honorGuardQuotaPointsColumn", "E")),
        promotionEventPoints=_normalizeColumn(getattr(configModule, "honorGuardPromotionEventPointsColumn", "F")),
        promotionAwardedPoints=_normalizeColumn(getattr(configModule, "honorGuardPromotionAwardedPointsColumn", "G")),
        promotionTotalPoints=_normalizeColumn(getattr(configModule, "honorGuardPromotionTotalPointsColumn", "H")),
        hostedEvents=_normalizeColumn(getattr(configModule, "honorGuardHostedEventsColumn", "I")),
        juniorExamPassed=_normalizeColumn(getattr(configModule, "honorGuardJuniorExamPassedColumn", "J")),
        ncoExamPassed=_normalizeColumn(getattr(configModule, "honorGuardNcoExamPassedColumn", "K")),
    )


def _columnMap(columns: HonorGuardMemberColumns) -> dict[str, str]:
    return {
        "discordId": columns.discordId,
        "robloxUsername": columns.robloxUsername,
        "rank": columns.rank,
        "activityStatus": columns.activityStatus,
        "quotaPoints": columns.quotaPoints,
        "promotionEventPoints": columns.promotionEventPoints,
        "promotionAwardedPoints": columns.promotionAwardedPoints,
        "promotionTotalPoints": columns.promotionTotalPoints,
        "hostedEvents": columns.hostedEvents,
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
    promotionEvent = _toFloat(values.get("promotionEventPoints"))
    promotionAwarded = _toFloat(values.get("promotionAwardedPoints"))
    promotionTotal = _toFloat(values.get("promotionTotalPoints"), promotionEvent + promotionAwarded)
    return HonorGuardMemberRow(
        row=rowIndex,
        discordId=_toInt(values.get("discordId")),
        robloxUsername=str(values.get("robloxUsername") or "").strip(),
        rank=str(values.get("rank") or "").strip(),
        activityStatus=str(values.get("activityStatus") or "").strip(),
        quotaPoints=_toFloat(values.get("quotaPoints")),
        promotionEventPoints=promotionEvent,
        promotionAwardedPoints=promotionAwarded,
        promotionTotalPoints=promotionTotal,
        hostedEvents=_toInt(values.get("hostedEvents")),
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
    promotionEventDelta: float = 0,
    promotionAwardedDelta: float = 0,
    hostedEventsDelta: int = 0,
    markActiveWhenEarlyQuotaMet: bool = True,
    configModule: Any = config,
) -> HonorGuardMemberPointUpdate:
    member = readMember(discordId=discordId, robloxUsername=robloxUsername, configModule=configModule)
    if member is None:
        lookup = robloxUsername or str(discordId or "")
        raise ValueError(f"Honor Guard member not found in sheet: {lookup}")

    columns = loadMemberColumns(configModule=configModule)
    nextQuota = max(0.0, float(member.quotaPoints) + float(quotaDelta or 0))
    nextPromotionEvent = max(0.0, float(member.promotionEventPoints) + float(promotionEventDelta or 0))
    nextPromotionAwarded = max(0.0, float(member.promotionAwardedPoints) + float(promotionAwardedDelta or 0))
    nextPromotionTotal = nextPromotionEvent + nextPromotionAwarded
    nextHostedEvents = max(0, int(member.hostedEvents) + int(hostedEventsDelta or 0))

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
    if columns.promotionEventPoints:
        updates["promotionEventPoints"] = (columns.promotionEventPoints, _pointValue(nextPromotionEvent))
    if columns.promotionAwardedPoints:
        updates["promotionAwardedPoints"] = (columns.promotionAwardedPoints, _pointValue(nextPromotionAwarded))
    if columns.promotionTotalPoints:
        updates["promotionTotalPoints"] = (columns.promotionTotalPoints, _pointValue(nextPromotionTotal))
    if columns.hostedEvents:
        updates["hostedEvents"] = (columns.hostedEvents, nextHostedEvents)
    if columns.activityStatus and status != member.activityStatus:
        updates["activityStatus"] = (columns.activityStatus, status)

    _engine.writeRowColumns(_memberSheetKey, row=member.row, columnValues=updates)
    return HonorGuardMemberPointUpdate(
        row=member.row,
        robloxUsername=member.robloxUsername,
        previousQuotaPoints=member.quotaPoints,
        quotaPoints=nextQuota,
        previousPromotionEventPoints=member.promotionEventPoints,
        promotionEventPoints=nextPromotionEvent,
        previousPromotionAwardedPoints=member.promotionAwardedPoints,
        promotionAwardedPoints=nextPromotionAwarded,
        previousPromotionTotalPoints=member.promotionTotalPoints,
        promotionTotalPoints=nextPromotionTotal,
        previousHostedEvents=member.hostedEvents,
        hostedEvents=nextHostedEvents,
        activityStatus=status,
    )


def appendArchiveRecord(record: HonorGuardArchiveRecord, *, configModule: Any = config) -> dict[str, Any]:
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
        "eventType": record.eventType,
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


def _scheduleColumn(name: str, default: str, *, configModule: Any = config) -> str:
    return _normalizeColumn(getattr(configModule, name, default))


def _findScheduleRow(
    *,
    eventId: str = "",
    eventTitle: str = "",
    eventType: str = "",
    eventTimeUtc: str = "",
    host: str = "",
    configModule: Any = config,
) -> Optional[int]:
    eventIdColumn = _scheduleColumn("honorGuardScheduleEventIdColumn", "A", configModule=configModule)
    eventTypeColumn = _scheduleColumn("honorGuardScheduleEventTypeColumn", "A", configModule=configModule)
    eventTimeColumn = _scheduleColumn("honorGuardScheduleEventTimeColumn", "B", configModule=configModule)
    hostColumn = _scheduleColumn("honorGuardScheduleHostColumn", "C", configModule=configModule)
    eventDetailColumn = _scheduleColumn("honorGuardScheduleEventDetailColumn", "F", configModule=configModule)
    if str(eventId or "").strip() and eventIdColumn:
        row = _findRowByNormalizedValue(
            _scheduleSheetKey,
            columnLetter=eventIdColumn,
            value=eventId,
            normalizer=lambda raw: str(raw or "").strip().casefold(),
        )
        if row:
            return row

    typeKey = _normalizeKey(eventType or eventTitle)
    timeKey = str(eventTimeUtc or "").strip().casefold()
    hostKey = _normalizeUsername(host)
    detailKey = _normalizeKey(eventTitle)
    if not typeKey:
        return None
    eventTypes = _readColumn(_scheduleSheetKey, eventTypeColumn)
    eventTimes = _readColumn(_scheduleSheetKey, eventTimeColumn) if timeKey else []
    hosts = _readColumn(_scheduleSheetKey, hostColumn) if hostKey else []
    details = _readColumn(_scheduleSheetKey, eventDetailColumn) if detailKey else []
    for index, currentEventType in enumerate(eventTypes, start=1):
        if _normalizeKey(currentEventType) != typeKey:
            continue
        if timeKey:
            currentTime = eventTimes[index - 1] if index - 1 < len(eventTimes) else ""
            if str(currentTime or "").strip().casefold() != timeKey:
                continue
        if hostKey:
            currentHost = hosts[index - 1] if index - 1 < len(hosts) else ""
            if _normalizeUsername(currentHost) != hostKey:
                continue
        if detailKey:
            currentDetail = details[index - 1] if index - 1 < len(details) else ""
            if _normalizeKey(currentDetail) != detailKey:
                continue
        return index
    return None


def removeScheduledEvent(
    *,
    eventId: str = "",
    eventTitle: str = "",
    eventType: str = "",
    eventTimeUtc: str = "",
    host: str = "",
    configModule: Any = config,
) -> Optional[HonorGuardScheduleRemovalResult]:
    row = _findScheduleRow(
        eventId=eventId,
        eventTitle=eventTitle,
        eventType=eventType,
        eventTimeUtc=eventTimeUtc,
        host=host,
        configModule=configModule,
    )
    if row is None:
        return None

    statusColumn = _scheduleColumn("honorGuardScheduleStatusColumn", "", configModule=configModule)
    if statusColumn:
        _engine.writeRowColumns(
            _scheduleSheetKey,
            row=row,
            columnValues={"status": (statusColumn, "Archived")},
        )
        return HonorGuardScheduleRemovalResult(row=row, action="marked_archived")

    sheetId = _engine.getSheetTabId(_scheduleSheetKey)
    _engine.batchUpdateRequests(
        _scheduleSheetKey,
        [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": sheetId,
                        "dimension": "ROWS",
                        "startIndex": row - 1,
                        "endIndex": row,
                    }
                }
            }
        ],
    )
    return HonorGuardScheduleRemovalResult(row=row, action="deleted")


def archiveEventAndRemoveSchedule(
    record: HonorGuardArchiveRecord,
    *,
    removeFromSchedule: bool = True,
    configModule: Any = config,
) -> dict[str, Any]:
    archiveResult = appendArchiveRecord(record, configModule=configModule)
    removalResult = None
    if removeFromSchedule:
        removalResult = removeScheduledEvent(
            eventId=record.eventId,
            eventTitle=record.eventTitle,
            eventType=record.eventType,
            eventTimeUtc=record.eventTimeUtc,
            host=record.host,
            configModule=configModule,
        )
    return {
        "archiveResult": archiveResult,
        "scheduleRemoval": removalResult,
    }


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
