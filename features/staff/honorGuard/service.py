from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any, Optional

import config
from db.sqlite import execute, executeReturnId, fetchAll, fetchOne
from features.staff.sessions.Roblox import roverIdentity


@dataclass(slots=True, frozen=True)
class HonorGuardConfig:
    enabled: bool
    reviewChannelId: int
    logChannelId: int
    archiveChannelId: int
    spreadsheetId: str
    memberSheetName: str
    scheduleSheetName: str
    archiveSheetName: str
    eventHostsSheetName: str


@dataclass(slots=True, frozen=True)
class HonorGuardPointDeltas:
    quotaPoints: float = 0
    promotionEventPoints: float = 0
    promotionAwardedPoints: float = 0
    hostedEvents: int = 0


@dataclass(slots=True, frozen=True)
class HonorGuardScaffoldStatus:
    config: HonorGuardConfig
    plannedDbTables: tuple[str, ...]
    plannedModules: tuple[str, ...]
    nextMilestones: tuple[str, ...]
    sheetProblems: tuple[str, ...] = ()


def _normalizePositiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _jsonText(value: object) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        text = value.strip()
        return text or "{}"
    try:
        return json.dumps(value, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return "{}"


def _jsonDict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _rememberHonorGuardIdentity(
    *,
    userId: int,
    robloxUsername: str,
    guildId: int,
    source: str,
) -> None:
    if int(userId or 0) <= 0 or not str(robloxUsername or "").strip():
        return
    await roverIdentity.rememberKnownRobloxIdentity(
        int(userId),
        str(robloxUsername or "").strip(),
        source=source,
        guildId=int(guildId or 0),
        confidence=80,
    )


def _normalizeKey(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _normalizeStatus(value: object, fallback: str = "PENDING") -> str:
    text = str(value or fallback).strip().upper()
    return text or fallback


def _normalizePointType(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"QUOTA", "PROMOTION_EVENT", "PROMOTION_AWARDED"}:
        return text
    return "PROMOTION_AWARDED"


def _eventType(value: object) -> str:
    aliases = {
        "game": "gamenight",
        "gamenite": "gamenight",
        "gameNight": "gamenight",
        "juniorguardsmanexam": "jge",
        "juniorexam": "jge",
        "jge": "jge",
        "ncoexam": "nco_exam",
        "nco": "nco_exam",
        "ncoexamination": "nco_exam",
        "honorguardtryout": "tryout",
        "hgtryout": "tryout",
    }
    raw = str(value or "").strip()
    normalized = _normalizeKey(raw)
    return aliases.get(normalized, normalized or "event")


def _participationRole(value: object) -> str:
    aliases = {
        "manager": "supervisor",
        "managed": "supervisor",
        "supervised": "supervisor",
        "cohost": "cohost",
        "cohosted": "cohost",
        "co-host": "cohost",
        "co-hosted": "cohost",
        "grader": "grader",
        "graded": "grader",
        "screen": "screen_assist",
        "screenassist": "screen_assist",
        "screens": "screen_assist",
        "hosted": "host",
        "hosting": "host",
    }
    normalized = _normalizeKey(value)
    return aliases.get(normalized, normalized or "attendee")


def _rankGroup(rank: object, *, configModule: Any) -> str:
    rankKey = _normalizeKey(rank)
    if not rankKey:
        return ""
    groupConfig = (
        ("enlisted", getattr(configModule, "honorGuardEnlistedRanks", []) or []),
        ("nco", getattr(configModule, "honorGuardNcoRanks", []) or []),
        ("officer", getattr(configModule, "honorGuardOfficerRanks", []) or []),
    )
    for groupName, ranks in groupConfig:
        if rankKey in {_normalizeKey(item) for item in ranks}:
            return groupName
    return ""


def _configuredPointMap(configModule: Any, attrName: str) -> dict[str, float]:
    raw = getattr(configModule, attrName, {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        try:
            out[_eventType(key)] = float(value)
        except (TypeError, ValueError):
            continue
    return out


def _attendanceQuotaPoints(configModule: Any, eventType: str) -> float:
    byType = _configuredPointMap(configModule, "honorGuardAttendanceQuotaPointsByEventType")
    return float(byType.get(eventType, 1))


def _attendancePromotionPoints(configModule: Any, eventType: str) -> float:
    byType = _configuredPointMap(configModule, "honorGuardAttendancePromotionPointsByEventType")
    return float(byType.get(eventType, 0))


def _ceilPoints(value: float) -> int:
    return int(math.ceil(max(0.0, float(value or 0))))


def calculatePointDeltas(
    *,
    configModule: Any,
    memberRank: str = "",
    memberGroup: str = "",
    eventType: str = "",
    participationRole: str = "attendee",
    attendeeCount: int = 0,
    gradedAttendeeCount: int = 0,
    promotionAwardedPoints: float = 0,
) -> HonorGuardPointDeltas:
    normalizedEvent = _eventType(eventType)
    normalizedRole = _participationRole(participationRole)
    group = str(memberGroup or "").strip().lower() or _rankGroup(memberRank, configModule=configModule)
    attendeeTotal = max(0, int(attendeeCount or 0))
    gradedTotal = max(0, int(gradedAttendeeCount or 0))

    quotaPoints = 0.0
    promotionEventPoints = 0.0
    hostedEvents = 0

    attendanceEligible = group in {"enlisted", "nco"}
    officerLike = group in {"officer", "nco", ""}

    if normalizedRole == "attendee":
        if attendanceEligible:
            quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)
            promotionEventPoints = _attendancePromotionPoints(configModule, normalizedEvent)
        elif normalizedEvent == "inspection":
            promotionEventPoints = _attendancePromotionPoints(configModule, normalizedEvent) or 8

    elif normalizedRole == "host":
        hostedEvents = 1
        if officerLike:
            hostMap = _configuredPointMap(configModule, "honorGuardOfficerHostPromotionPointsByEventType")
            promotionEventPoints = float(hostMap.get(normalizedEvent, 0))
        if group == "nco":
            quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)

    elif normalizedRole == "supervisor":
        if officerLike:
            supervisorMap = _configuredPointMap(configModule, "honorGuardOfficerSupervisorPromotionPointsByEventType")
            promotionEventPoints = float(supervisorMap.get(normalizedEvent, 0))
        if group == "nco":
            quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)

    elif normalizedRole == "cohost":
        if officerLike:
            cohostMap = _configuredPointMap(configModule, "honorGuardOfficerCohostPromotionPointsByEventType")
            promotionEventPoints = float(cohostMap.get(normalizedEvent, 0))
        if group == "nco":
            quotaPoints = _attendanceQuotaPoints(configModule, normalizedEvent)

    if normalizedEvent == "jge" and normalizedRole in {"host", "grader"}:
        rate = float(getattr(configModule, "honorGuardJgePointsPerGradedAttendee", 0.75) or 0.75)
        count = gradedTotal or attendeeTotal
        promotionEventPoints = max(promotionEventPoints, _ceilPoints(rate * count))

    if normalizedEvent == "nco_exam":
        rate = float(getattr(configModule, "honorGuardNcoExamPointsPerGradedAttendee", 1.5) or 1.5)
        screenAssistPoints = float(getattr(configModule, "honorGuardNcoExamScreenAssistPoints", 2) or 2)
        if normalizedRole in {"host", "grader"}:
            count = gradedTotal or attendeeTotal
            promotionEventPoints = max(promotionEventPoints, _ceilPoints(rate * count))
        elif normalizedRole in {"cohost", "screen_assist", "supervisor"}:
            promotionEventPoints = max(promotionEventPoints, screenAssistPoints)
            if gradedTotal > 0:
                promotionEventPoints += _ceilPoints(rate * gradedTotal)

    return HonorGuardPointDeltas(
        quotaPoints=float(quotaPoints),
        promotionEventPoints=float(promotionEventPoints),
        promotionAwardedPoints=max(0.0, float(promotionAwardedPoints or 0)),
        hostedEvents=int(hostedEvents),
    )


def loadHonorGuardConfig(*, configModule: Any) -> HonorGuardConfig:
    return HonorGuardConfig(
        enabled=bool(getattr(configModule, "honorGuardEnabled", False)),
        reviewChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardReviewChannelId", 0)),
        logChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardLogChannelId", 0)),
        archiveChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardArchiveChannelId", 0)),
        spreadsheetId=str(getattr(configModule, "honorGuardSpreadsheetId", "") or "").strip(),
        memberSheetName=str(getattr(configModule, "honorGuardMemberSheetName", "") or "").strip(),
        scheduleSheetName=str(getattr(configModule, "honorGuardScheduleSheetName", "") or "").strip(),
        archiveSheetName=str(getattr(configModule, "honorGuardArchiveSheetName", "") or "").strip(),
        eventHostsSheetName=str(getattr(configModule, "honorGuardEventHostsSheetName", "") or "").strip(),
    )


def buildScaffoldStatus(*, configModule: Any) -> HonorGuardScaffoldStatus:
    try:
        from features.staff.honorGuard import sheets as honorGuardSheets

        sheetProblems = honorGuardSheets.configurationProblems(configModule=configModule)
    except Exception as exc:
        sheetProblems = (f"Sheet adapter check failed: {exc.__class__.__name__}",)

    return HonorGuardScaffoldStatus(
        config=loadHonorGuardConfig(configModule=configModule),
        plannedDbTables=(
            "hg_submissions",
            "hg_submission_events",
            "hg_point_awards",
            "hg_attendance_records",
            "hg_sentry_logs",
            "hg_quota_cycles",
            "hg_event_records",
        ),
        plannedModules=(
            "cogs.staff.honorGuardCog",
            "features.staff.honorGuard.service",
            "features.staff.honorGuard.sheets",
            "features.staff.honorGuard",
        ),
        nextMilestones=(
            "Build the command/view review flow on top of hg_submissions.",
            "Call syncApprovedSubmissionToSheet after reviewer approval.",
            "Live-test member row lookup and archive/schedule handling against the HG spreadsheet.",
            "Add bi-weekly quota reset automation after sheet columns are confirmed.",
        ),
        sheetProblems=sheetProblems,
    )


async def createPointAwardSubmission(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    awardedUserId: int,
    reason: str,
    awardedPoints: float = 0,
    quotaPoints: float = 0,
    awardedUserDisplayName: str = "",
    targetRobloxUsername: str = "",
    eventPoints: float = 0,
) -> int:
    awardedDelta = float(awardedPoints or eventPoints or 0)
    return await createSubmission(
        guildId=int(guildId),
        channelId=int(channelId),
        submitterId=int(submitterId),
        submissionType="POINT_AWARD",
        targetUserId=int(awardedUserId or 0),
        targetRobloxUsername=str(targetRobloxUsername or "").strip(),
        targetDisplayName=str(awardedUserDisplayName or "").strip(),
        eventType="award",
        eventTitle="Manual Point Award",
        deltas=HonorGuardPointDeltas(
            quotaPoints=float(quotaPoints or 0),
            promotionAwardedPoints=awardedDelta,
        ),
        metadata={
            "reason": str(reason or "").strip(),
            "awardedPoints": awardedDelta,
            "quotaPoints": float(quotaPoints or 0),
        },
    )


async def getPointAwardSubmission(submissionId: int) -> Optional[dict[str, Any]]:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        return None
    metadata = _jsonDict(submission.get("metadataJson"))
    enriched = dict(submission)
    targetUserId = int(submission.get("targetUserId") or 0)
    awardedPoints = float(
        submission.get("promotionAwardedPoints")
        or metadata.get("awardedPoints")
        or metadata.get("eventPoints")
        or 0
    )
    enriched["awardedUserId"] = targetUserId
    enriched["recruitUserId"] = targetUserId
    enriched["awardedPoints"] = awardedPoints
    enriched["eventPoints"] = float(submission.get("promotionEventPoints") or metadata.get("eventPoints") or 0)
    enriched["reason"] = str(metadata.get("reason") or "").strip()
    return enriched


async def setPointAwardMessageId(submissionId: int, messageId: int) -> None:
    await setSubmissionMessageId(int(submissionId), int(messageId or 0))


async def updatePointAwardStatus(
    submissionId: int,
    status: str,
    *,
    reviewerId: int,
    note: str | None = None,
    threadId: int | None = None,
) -> None:
    details = {"threadId": int(threadId)} if int(threadId or 0) > 0 else None
    await setSubmissionStatus(
        submissionId=int(submissionId),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
        details=details,
    )
    if _normalizeStatus(status) == "APPROVED":
        submission = await getSubmission(int(submissionId))
        if submission is not None:
            await ensurePointAwardRecordsForSubmission(
                submission=submission,
                sheetSynced=bool(int(submission.get("sheetSynced") or 0)),
            )


async def _appendSubmissionEvent(
    *,
    submissionId: int,
    actorId: int = 0,
    eventType: str,
    fromStatus: str = "",
    toStatus: str = "",
    note: str = "",
    details: object = None,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO hg_submission_events
            (submissionId, actorId, eventType, fromStatus, toStatus, note, detailsJson)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId),
            int(actorId or 0),
            str(eventType or "EVENT").strip().upper(),
            str(fromStatus or "").strip().upper(),
            str(toStatus or "").strip().upper(),
            str(note or "").strip(),
            _jsonText(details),
        ),
    )


async def createSubmission(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    submissionType: str,
    targetUserId: int = 0,
    targetRobloxUsername: str = "",
    targetDisplayName: str = "",
    eventType: str = "",
    eventTitle: str = "",
    eventDate: str = "",
    deltas: HonorGuardPointDeltas | None = None,
    metadata: object = None,
) -> int:
    pointDeltas = deltas or HonorGuardPointDeltas()
    submissionId = await executeReturnId(
        """
        INSERT INTO hg_submissions
            (
                guildId, channelId, submitterId, targetUserId, targetRobloxUsername,
                targetDisplayName, submissionType, eventType, eventTitle, eventDate,
                quotaPoints, promotionEventPoints, promotionAwardedPoints, hostedEvents,
                metadataJson
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guildId),
            int(channelId),
            int(submitterId),
            int(targetUserId or 0),
            str(targetRobloxUsername or "").strip(),
            str(targetDisplayName or "").strip(),
            str(submissionType or "").strip().upper(),
            _eventType(eventType),
            str(eventTitle or "").strip(),
            str(eventDate or "").strip(),
            float(pointDeltas.quotaPoints),
            float(pointDeltas.promotionEventPoints),
            float(pointDeltas.promotionAwardedPoints),
            int(pointDeltas.hostedEvents),
            _jsonText(metadata),
        ),
    )
    await _appendSubmissionEvent(
        submissionId=submissionId,
        actorId=submitterId,
        eventType="CREATED",
        toStatus="PENDING",
        details=metadata,
    )
    await _rememberHonorGuardIdentity(
        userId=int(targetUserId or 0),
        robloxUsername=targetRobloxUsername,
        guildId=int(guildId),
        source="honor-guard-submission",
    )
    return submissionId


async def setSubmissionMessageId(submissionId: int, messageId: int) -> None:
    await execute(
        """
        UPDATE hg_submissions
        SET messageId = ?, updatedAt = datetime('now')
        WHERE submissionId = ?
        """,
        (int(messageId or 0), int(submissionId)),
    )


async def getSubmission(submissionId: int) -> Optional[dict[str, Any]]:
    return await fetchOne(
        "SELECT * FROM hg_submissions WHERE submissionId = ?",
        (int(submissionId),),
    )


async def listPendingSubmissions(*, guildId: int, limit: int = 100) -> list[dict[str, Any]]:
    safeLimit = max(1, min(500, int(limit or 100)))
    return await fetchAll(
        """
        SELECT *
        FROM hg_submissions
        WHERE guildId = ? AND status = 'PENDING'
        ORDER BY createdAt ASC, submissionId ASC
        LIMIT ?
        """,
        (int(guildId), safeLimit),
    )


async def setSubmissionStatus(
    *,
    submissionId: int,
    status: str,
    reviewerId: int = 0,
    note: str = "",
    details: object = None,
) -> None:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        raise ValueError(f"Honor Guard submission not found: {submissionId}")
    fromStatus = _normalizeStatus(submission.get("status"))
    toStatus = _normalizeStatus(status)
    await execute(
        """
        UPDATE hg_submissions
        SET status = ?,
            reviewerId = ?,
            reviewNote = ?,
            reviewedAt = CASE WHEN ? IN ('APPROVED', 'REJECTED', 'CANCELED') THEN datetime('now') ELSE reviewedAt END,
            updatedAt = datetime('now')
        WHERE submissionId = ?
        """,
        (
            toStatus,
            int(reviewerId or 0),
            str(note or "").strip(),
            toStatus,
            int(submissionId),
        ),
    )
    await _appendSubmissionEvent(
        submissionId=int(submissionId),
        actorId=int(reviewerId or 0),
        eventType=f"STATUS_{toStatus}",
        fromStatus=fromStatus,
        toStatus=toStatus,
        note=note,
        details=details,
    )


async def createPointAward(
    *,
    guildId: int,
    targetUserId: int = 0,
    targetRobloxUsername: str = "",
    pointType: str,
    points: float,
    reason: str = "",
    awardedBy: int = 0,
    approvedBy: int = 0,
    submissionId: int = 0,
    sheetSynced: bool = False,
) -> int:
    awardId = await executeReturnId(
        """
        INSERT INTO hg_point_awards
            (
                submissionId, guildId, targetUserId, targetRobloxUsername, pointType,
                points, reason, awardedBy, approvedBy, sheetSynced
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId or 0),
            int(guildId),
            int(targetUserId or 0),
            str(targetRobloxUsername or "").strip(),
            _normalizePointType(pointType),
            float(points or 0),
            str(reason or "").strip(),
            int(awardedBy or 0),
            int(approvedBy or 0),
            1 if sheetSynced else 0,
        ),
    )
    await _rememberHonorGuardIdentity(
        userId=int(targetUserId or 0),
        robloxUsername=targetRobloxUsername,
        guildId=int(guildId),
        source="honor-guard-award",
    )
    return awardId


async def ensurePointAwardRecordsForSubmission(
    *,
    submission: dict[str, Any],
    sheetSynced: bool,
) -> list[int]:
    if _normalizeStatus(submission.get("status")) != "APPROVED":
        return []
    if str(submission.get("submissionType") or "").strip().upper() != "POINT_AWARD":
        return []

    submissionId = int(submission.get("submissionId") or 0)
    if submissionId <= 0:
        return []

    existingRows = await fetchAll(
        "SELECT awardId, pointType, sheetSynced FROM hg_point_awards WHERE submissionId = ?",
        (submissionId,),
    )
    existingByType = {
        str(row.get("pointType") or "").strip().upper(): row
        for row in existingRows
    }

    metadata = _jsonDict(submission.get("metadataJson"))
    quotaPoints = float(submission.get("quotaPoints") or metadata.get("quotaPoints") or 0)
    awardedPoints = float(
        submission.get("promotionAwardedPoints")
        or metadata.get("awardedPoints")
        or metadata.get("eventPoints")
        or 0
    )
    reason = str(metadata.get("reason") or "").strip()

    desiredRows: list[tuple[str, float]] = []
    if quotaPoints > 0:
        desiredRows.append(("QUOTA", quotaPoints))
    if awardedPoints > 0:
        desiredRows.append(("PROMOTION_AWARDED", awardedPoints))

    createdAwardIds: list[int] = []
    for pointType, points in desiredRows:
        existing = existingByType.get(pointType)
        if existing is None:
            awardId = await createPointAward(
                submissionId=submissionId,
                guildId=int(submission.get("guildId") or 0),
                targetUserId=int(submission.get("targetUserId") or 0),
                targetRobloxUsername=str(submission.get("targetRobloxUsername") or "").strip(),
                pointType=pointType,
                points=points,
                reason=reason,
                awardedBy=int(submission.get("submitterId") or 0),
                approvedBy=int(submission.get("reviewerId") or 0),
                sheetSynced=sheetSynced,
            )
            createdAwardIds.append(int(awardId))
            continue

        if bool(int(existing.get("sheetSynced") or 0)) == bool(sheetSynced):
            continue

        await execute(
            """
            UPDATE hg_point_awards
            SET sheetSynced = ?
            WHERE awardId = ?
            """,
            (1 if sheetSynced else 0, int(existing.get("awardId") or 0)),
        )

    return createdAwardIds


async def createAttendanceRecord(
    *,
    guildId: int,
    eventType: str,
    targetUserId: int = 0,
    targetRobloxUsername: str = "",
    eventTitle: str = "",
    eventDate: str = "",
    participationRole: str = "ATTENDEE",
    memberGroup: str = "",
    attendeeCount: int = 0,
    gradedAttendeeCount: int = 0,
    assistedScreens: bool = False,
    deltas: HonorGuardPointDeltas | None = None,
    createdBy: int = 0,
    approvedBy: int = 0,
    submissionId: int = 0,
) -> int:
    pointDeltas = deltas or HonorGuardPointDeltas()
    recordId = await executeReturnId(
        """
        INSERT INTO hg_attendance_records
            (
                submissionId, guildId, eventType, eventTitle, eventDate, targetUserId,
                targetRobloxUsername, participationRole, memberGroup, attendeeCount,
                gradedAttendeeCount, assistedScreens, quotaPoints, promotionEventPoints,
                hostedEvents, createdBy, approvedBy
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId or 0),
            int(guildId),
            _eventType(eventType),
            str(eventTitle or "").strip(),
            str(eventDate or "").strip(),
            int(targetUserId or 0),
            str(targetRobloxUsername or "").strip(),
            _participationRole(participationRole).upper(),
            str(memberGroup or "").strip().lower(),
            int(attendeeCount or 0),
            int(gradedAttendeeCount or 0),
            1 if assistedScreens else 0,
            float(pointDeltas.quotaPoints),
            float(pointDeltas.promotionEventPoints),
            int(pointDeltas.hostedEvents),
            int(createdBy or 0),
            int(approvedBy or 0),
        ),
    )
    await _rememberHonorGuardIdentity(
        userId=int(targetUserId or 0),
        robloxUsername=targetRobloxUsername,
        guildId=int(guildId),
        source="honor-guard-attendance",
    )
    return recordId


async def createSentryLog(
    *,
    guildId: int,
    userId: int,
    dutyDate: str,
    robloxUsername: str = "",
    minutes: int = 0,
    submissionId: int = 0,
    status: str = "PENDING",
    configModule: Any = config,
) -> int:
    dutyMinutes = int(minutes or getattr(configModule, "honorGuardSentryDutyMinutesRequired", 30) or 30)
    quotaPoints = float(getattr(configModule, "honorGuardSentryDutyQuotaPoints", 1) or 1)
    promotionPoints = float(getattr(configModule, "honorGuardSentryDutyPromotionPoints", 1) or 1)
    sentryLogId = await executeReturnId(
        """
        INSERT INTO hg_sentry_logs
            (
                submissionId, guildId, userId, robloxUsername, dutyDate, minutes,
                quotaPoints, promotionEventPoints, status
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId or 0),
            int(guildId),
            int(userId),
            str(robloxUsername or "").strip(),
            str(dutyDate or "").strip(),
            dutyMinutes,
            quotaPoints,
            promotionPoints,
            _normalizeStatus(status),
        ),
    )
    await _rememberHonorGuardIdentity(
        userId=int(userId),
        robloxUsername=robloxUsername,
        guildId=int(guildId),
        source="honor-guard-sentry",
    )
    return sentryLogId


async def findExistingSentryLogForDate(*, userId: int, dutyDate: str) -> Optional[dict[str, Any]]:
    return await fetchOne(
        """
        SELECT *
        FROM hg_sentry_logs
        WHERE userId = ?
          AND dutyDate = ?
          AND status IN ('PENDING', 'APPROVED')
        ORDER BY sentryLogId DESC
        LIMIT 1
        """,
        (int(userId), str(dutyDate or "").strip()),
    )


async def createSentrySubmission(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    dutyDate: str,
    targetUserId: int = 0,
    targetRobloxUsername: str = "",
    targetDisplayName: str = "",
    minutes: int = 0,
    imageUrls: list[str] | None = None,
    evidenceMessageUrl: str = "",
    configModule: Any = config,
) -> int:
    userId = int(targetUserId or submitterId)
    existing = await findExistingSentryLogForDate(userId=userId, dutyDate=dutyDate)
    if existing is not None:
        raise ValueError("A pending or approved Honor Guard sentry log already exists for that user/date.")

    dutyMinutes = int(minutes or getattr(configModule, "honorGuardSentryDutyMinutesRequired", 30) or 30)
    deltas = HonorGuardPointDeltas(
        quotaPoints=float(getattr(configModule, "honorGuardSentryDutyQuotaPoints", 1) or 1),
        promotionEventPoints=float(getattr(configModule, "honorGuardSentryDutyPromotionPoints", 1) or 1),
    )
    submissionId = await createSubmission(
        guildId=guildId,
        channelId=channelId,
        submitterId=submitterId,
        submissionType="SENTRY",
        targetUserId=userId,
        targetRobloxUsername=targetRobloxUsername,
        targetDisplayName=targetDisplayName,
        eventType="sentry",
        eventTitle="Solo Sentry Duty",
        eventDate=dutyDate,
        deltas=deltas,
        metadata={
            "minutes": dutyMinutes,
            "imageUrls": list(imageUrls or []),
            "evidenceMessageUrl": str(evidenceMessageUrl or "").strip(),
        },
    )
    await createSentryLog(
        guildId=guildId,
        userId=userId,
        dutyDate=dutyDate,
        robloxUsername=targetRobloxUsername,
        minutes=dutyMinutes,
        submissionId=submissionId,
        status="PENDING",
        configModule=configModule,
    )
    return submissionId


async def getSentrySubmission(submissionId: int) -> Optional[dict[str, Any]]:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        return None
    if str(submission.get("submissionType") or "").strip().upper() != "SENTRY":
        return None

    metadata = _jsonDict(submission.get("metadataJson"))
    sentryLog = await fetchOne(
        "SELECT * FROM hg_sentry_logs WHERE submissionId = ? ORDER BY sentryLogId DESC LIMIT 1",
        (int(submissionId),),
    )
    enriched = dict(submission)
    enriched["minutes"] = int(
        (sentryLog or {}).get("minutes")
        or metadata.get("minutes")
        or getattr(config, "honorGuardSentryDutyMinutesRequired", 30)
        or 30
    )
    enriched["imageUrls"] = [
        str(value).strip()
        for value in metadata.get("imageUrls", [])
        if str(value).strip()
    ] if isinstance(metadata.get("imageUrls"), list) else []
    enriched["evidenceMessageUrl"] = str(metadata.get("evidenceMessageUrl") or "").strip()
    enriched["sentryLogId"] = int((sentryLog or {}).get("sentryLogId") or 0)
    return enriched


async def setSentryLogStatus(
    *,
    sentryLogId: int,
    status: str,
    reviewerId: int = 0,
    note: str = "",
) -> None:
    await execute(
        """
        UPDATE hg_sentry_logs
        SET status = ?,
            reviewerId = ?,
            reviewNote = ?,
            reviewedAt = CASE WHEN ? IN ('APPROVED', 'REJECTED', 'CANCELED') THEN datetime('now') ELSE reviewedAt END
        WHERE sentryLogId = ?
        """,
        (
            _normalizeStatus(status),
            int(reviewerId or 0),
            str(note or "").strip(),
            _normalizeStatus(status),
            int(sentryLogId),
        ),
    )


async def updateSentrySubmissionStatus(
    submissionId: int,
    status: str,
    *,
    reviewerId: int,
    note: str | None = None,
    threadId: int | None = None,
) -> None:
    details = {"threadId": int(threadId)} if int(threadId or 0) > 0 else None
    await setSubmissionStatus(
        submissionId=int(submissionId),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
        details=details,
    )
    sentryLog = await fetchOne(
        "SELECT sentryLogId FROM hg_sentry_logs WHERE submissionId = ? ORDER BY sentryLogId DESC LIMIT 1",
        (int(submissionId),),
    )
    if sentryLog is None:
        return
    await setSentryLogStatus(
        sentryLogId=int(sentryLog.get("sentryLogId") or 0),
        status=str(status or "").strip().upper(),
        reviewerId=int(reviewerId or 0),
        note=str(note or "").strip(),
    )


async def createEventRecord(
    *,
    guildId: int,
    eventType: str,
    eventTitle: str = "",
    eventDate: str = "",
    hostUserId: int = 0,
    hostRobloxUsername: str = "",
    attendeeCount: int = 0,
    metadata: object = None,
    createdBy: int = 0,
    submissionId: int = 0,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO hg_event_records
            (
                submissionId, guildId, eventType, eventTitle, eventDate, hostUserId,
                hostRobloxUsername, attendeeCount, metadataJson, createdBy
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(submissionId or 0),
            int(guildId),
            _eventType(eventType),
            str(eventTitle or "").strip(),
            str(eventDate or "").strip(),
            int(hostUserId or 0),
            str(hostRobloxUsername or "").strip(),
            int(attendeeCount or 0),
            _jsonText(metadata),
            int(createdBy or 0),
        ),
    )


async def getEventRecord(eventRecordId: int) -> Optional[dict[str, Any]]:
    return await fetchOne(
        "SELECT * FROM hg_event_records WHERE eventRecordId = ?",
        (int(eventRecordId),),
    )


async def syncEventRecordToSheets(eventRecordId: int) -> dict[str, Any]:
    record = await getEventRecord(int(eventRecordId))
    if record is None:
        raise ValueError(f"Honor Guard event record not found: {eventRecordId}")

    from features.staff.honorGuard import sheets as honorGuardSheets

    hostText = str(record.get("hostRobloxUsername") or "").strip()
    if not hostText and int(record.get("hostUserId") or 0) > 0:
        hostText = str(int(record.get("hostUserId") or 0))
    metadata = _jsonDict(record.get("metadataJson"))
    scheduleEventId = str(metadata.get("scheduleEventId") or metadata.get("eventId") or "").strip()
    eventType = str(record.get("eventType") or "").strip()
    eventTitle = str(record.get("eventTitle") or "").strip()
    eventDetail = str(metadata.get("eventDetail") or eventTitle).strip()
    syncResult = honorGuardSheets.archiveEventAndRemoveSchedule(
        honorGuardSheets.HonorGuardArchiveRecord(
            eventType=eventType,
            eventTimeUtc=str(record.get("eventDate") or "").strip(),
            eventTitle=eventTitle,
            host=hostText,
            coHosts=str(metadata.get("coHosts") or "").strip(),
            supervisors=str(metadata.get("supervisors") or "").strip(),
            eventDuration=str(metadata.get("eventDuration") or "").strip(),
            eventDetail=eventDetail,
            attendeeCount=int(record.get("attendeeCount") or 0),
            notes=str(metadata.get("notes") or "").strip(),
            eventId=scheduleEventId,
        )
    )
    eventHostUpdate = None
    if hostText:
        eventHostUpdate = honorGuardSheets.incrementEventHostStats(host=hostText, eventType=eventType)
    scheduleRemoval = syncResult.get("scheduleRemoval")
    await execute(
        """
        UPDATE hg_event_records
        SET archiveSynced = 1,
            scheduleRemoved = ?
        WHERE eventRecordId = ?
        """,
        (1 if scheduleRemoval is not None else 0, int(eventRecordId)),
    )
    return {
        "eventRecordId": int(eventRecordId),
        "archiveSynced": True,
        "scheduleRemoval": scheduleRemoval,
        "eventHostUpdate": eventHostUpdate,
    }


async def syncApprovedSubmissionToSheet(submissionId: int) -> dict[str, Any]:
    submission = await getSubmission(int(submissionId))
    if submission is None:
        raise ValueError(f"Honor Guard submission not found: {submissionId}")
    if _normalizeStatus(submission.get("status")) != "APPROVED":
        raise ValueError("Only approved Honor Guard submissions can be synced.")
    if int(submission.get("sheetSynced") or 0):
        await ensurePointAwardRecordsForSubmission(
            submission=submission,
            sheetSynced=True,
        )
        if str(submission.get("submissionType") or "").strip().upper() == "SENTRY":
            await execute(
                """
                UPDATE hg_sentry_logs
                SET sheetSynced = 1
                WHERE submissionId = ?
                """,
                (int(submissionId),),
            )
        return {"alreadySynced": True, "submissionId": int(submissionId)}

    from features.staff.honorGuard import sheets as honorGuardSheets

    updateResult = honorGuardSheets.applyMemberPointDeltas(
        discordId=int(submission.get("targetUserId") or 0),
        robloxUsername=str(submission.get("targetRobloxUsername") or "").strip(),
        quotaDelta=float(submission.get("quotaPoints") or 0),
        promotionEventDelta=float(submission.get("promotionEventPoints") or 0),
        promotionAwardedDelta=float(submission.get("promotionAwardedPoints") or 0),
        hostedEventsDelta=int(submission.get("hostedEvents") or 0),
    )
    await execute(
        """
        UPDATE hg_submissions
        SET sheetSynced = 1,
            appliedAt = datetime('now'),
            updatedAt = datetime('now')
        WHERE submissionId = ?
        """,
        (int(submissionId),),
    )
    await ensurePointAwardRecordsForSubmission(
        submission={**submission, "sheetSynced": 1},
        sheetSynced=True,
    )
    if str(submission.get("submissionType") or "").strip().upper() == "SENTRY":
        await execute(
            """
            UPDATE hg_sentry_logs
            SET sheetSynced = 1
            WHERE submissionId = ?
            """,
            (int(submissionId),),
        )
    await _appendSubmissionEvent(
        submissionId=int(submissionId),
        actorId=int(submission.get("reviewerId") or 0),
        eventType="SHEET_SYNCED",
        fromStatus="APPROVED",
        toStatus="APPROVED",
        details={
            "row": updateResult.row,
            "quotaPoints": updateResult.quotaPoints,
            "promotionTotalPoints": updateResult.promotionTotalPoints,
        },
    )
    return {
        "alreadySynced": False,
        "submissionId": int(submissionId),
        "row": updateResult.row,
        "robloxUsername": updateResult.robloxUsername,
        "quotaPoints": updateResult.quotaPoints,
        "promotionEventPoints": updateResult.promotionEventPoints,
        "promotionAwardedPoints": updateResult.promotionAwardedPoints,
        "promotionTotalPoints": updateResult.promotionTotalPoints,
        "hostedEvents": updateResult.hostedEvents,
        "activityStatus": updateResult.activityStatus,
    }
