import json
from typing import Optional, Dict, List

from db.sqlite import execute, executeReturnId, fetchOne, fetchAll, executeMany


def _jsonText(value) -> str:
    return json.dumps(value or [])


def _normalizePositiveIntList(values: Optional[List[int]]) -> list[int]:
    out: list[int] = []
    for value in values or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            out.append(parsed)
    return out


async def _updateReviewedRecruitmentStatus(
    table: str,
    submissionId: int,
    status: str,
    reviewerId: Optional[int] = None,
    note: Optional[str] = None,
    threadId: Optional[int] = None,
) -> None:
    await execute(
        f"""
        UPDATE {table}
        SET status = ?, reviewedBy = ?, reviewedAt = datetime('now'), reviewNote = ?, threadId = ?
        WHERE submissionId = ?
        """,
        (status, reviewerId, note, threadId, submissionId),
    )


async def createRecruitmentSubmission(
    guildId: int,
    channelId: int,
    submitterId: int,
    recruitUserId: int,
    passedOrientation: bool,
    imageUrls: List[str],
    points: int,
    recruitDisplayName: str = "",
) -> int:
    return await executeReturnId(
        """
        INSERT INTO recruitment_submissions
            (guildId, channelId, messageId, submitterId, recruitUserId, recruitDisplayName, passedOrientation, imageUrls, status, points)
        VALUES (?, ?, 0, ?, ?, ?, ?, ?, 'PENDING', ?)
        """,
        (
            guildId,
            channelId,
            submitterId,
            recruitUserId,
            str(recruitDisplayName or "").strip(),
            1 if passedOrientation else 0,
            _jsonText(imageUrls),
            points,
        ),
    )


async def createRecruitmentTimeSubmission(
    guildId: int,
    channelId: int,
    submitterId: int,
    durationMinutes: int,
    imageUrls: List[str],
    points: int,
    patrolType: str = "solo",
    participantUserIds: Optional[List[int]] = None,
    evidenceMessageUrl: Optional[str] = None,
) -> int:
    patrolTypeValue = str(patrolType or "solo").strip().lower()
    if patrolTypeValue not in {"solo", "group"}:
        patrolTypeValue = "solo"
    normalizedParticipants = _normalizePositiveIntList(participantUserIds)
    participantJson = _jsonText(normalizedParticipants)
    return await executeReturnId(
        """
        INSERT INTO recruitment_time_submissions
            (guildId, channelId, messageId, submitterId, patrolType, participantUserIds, durationMinutes, imageUrls, evidenceMessageUrl, status, points)
        VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
        """,
        (
            guildId,
            channelId,
            submitterId,
            patrolTypeValue,
            participantJson,
            durationMinutes,
            _jsonText(imageUrls),
            evidenceMessageUrl,
            points,
        ),
    )


async def createRecruitmentPatrolSession(
    guildId: int,
    channelId: int,
    hostId: int,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO recruitment_patrol_sessions
            (guildId, channelId, messageId, hostId, status)
        VALUES (?, ?, 0, ?, 'OPEN')
        """,
        (
            guildId,
            channelId,
            hostId,
        ),
    )


async def setRecruitmentPatrolMessageId(patrolId: int, messageId: int) -> None:
    await execute(
        "UPDATE recruitment_patrol_sessions SET messageId = ? WHERE patrolId = ?",
        (messageId, patrolId),
    )


async def getRecruitmentPatrolSession(patrolId: int) -> Optional[Dict]:
    return await fetchOne(
        "SELECT * FROM recruitment_patrol_sessions WHERE patrolId = ?",
        (patrolId,),
    )


async def listOpenRecruitmentPatrolSessions() -> List[Dict]:
    return await fetchAll(
        "SELECT * FROM recruitment_patrol_sessions WHERE status = 'OPEN'",
    )


async def addRecruitmentPatrolAttendee(patrolId: int, userId: int) -> None:
    await execute(
        """
        INSERT OR IGNORE INTO recruitment_patrol_attendees
            (patrolId, userId)
        VALUES (?, ?)
        """,
        (patrolId, userId),
    )


async def removeRecruitmentPatrolAttendee(patrolId: int, userId: int) -> None:
    await execute(
        "DELETE FROM recruitment_patrol_attendees WHERE patrolId = ? AND userId = ?",
        (patrolId, userId),
    )


async def listRecruitmentPatrolAttendees(patrolId: int) -> List[Dict]:
    return await fetchAll(
        """
        SELECT *
        FROM recruitment_patrol_attendees
        WHERE patrolId = ?
        ORDER BY joinTime
        """,
        (patrolId,),
    )


async def updateRecruitmentPatrolStatus(patrolId: int, status: str) -> None:
    if status in {"FINISHED", "CANCELED"}:
        await execute(
            "UPDATE recruitment_patrol_sessions SET status = ?, finishedAt = datetime('now') WHERE patrolId = ?",
            (status, patrolId),
        )
    else:
        await execute(
            "UPDATE recruitment_patrol_sessions SET status = ?, finishedAt = NULL WHERE patrolId = ?",
            (status, patrolId),
        )


async def setRecruitmentMessageId(
    submissionId: int,
    messageId: int,
    channelId: Optional[int] = None,
) -> None:
    if channelId and int(channelId) > 0:
        await execute(
            "UPDATE recruitment_submissions SET channelId = ?, messageId = ? WHERE submissionId = ?",
            (int(channelId), messageId, submissionId),
        )
        return
    await execute(
        "UPDATE recruitment_submissions SET messageId = ? WHERE submissionId = ?",
        (messageId, submissionId),
    )

async def setRecruitmentImageUrls(submissionId: int, imageUrls: List[str]) -> None:
    await execute(
        "UPDATE recruitment_submissions SET imageUrls = ? WHERE submissionId = ?",
        (_jsonText(imageUrls), submissionId),
    )


async def setRecruitmentTimeMessageId(
    submissionId: int,
    messageId: int,
    channelId: Optional[int] = None,
) -> None:
    if channelId and int(channelId) > 0:
        await execute(
            "UPDATE recruitment_time_submissions SET channelId = ?, messageId = ? WHERE submissionId = ?",
            (int(channelId), messageId, submissionId),
        )
        return
    await execute(
        "UPDATE recruitment_time_submissions SET messageId = ? WHERE submissionId = ?",
        (messageId, submissionId),
    )

async def setRecruitmentTimeImageUrls(submissionId: int, imageUrls: List[str]) -> None:
    await execute(
        "UPDATE recruitment_time_submissions SET imageUrls = ? WHERE submissionId = ?",
        (_jsonText(imageUrls), submissionId),
    )


async def getRecruitmentSubmission(submissionId: int) -> Optional[Dict]:
    return await fetchOne(
        "SELECT * FROM recruitment_submissions WHERE submissionId = ?",
        (submissionId,),
    )


async def getRecruitmentTimeSubmission(submissionId: int) -> Optional[Dict]:
    return await fetchOne(
        "SELECT * FROM recruitment_time_submissions WHERE submissionId = ?",
        (submissionId,),
    )


async def updateRecruitmentStatus(
    submissionId: int,
    status: str,
    reviewerId: Optional[int] = None,
    note: Optional[str] = None,
    threadId: Optional[int] = None,
) -> None:
    await _updateReviewedRecruitmentStatus(
        "recruitment_submissions",
        submissionId,
        status,
        reviewerId,
        note,
        threadId,
    )


async def updateRecruitmentTimeStatus(
    submissionId: int,
    status: str,
    reviewerId: Optional[int] = None,
    note: Optional[str] = None,
    threadId: Optional[int] = None,
) -> None:
    await _updateReviewedRecruitmentStatus(
        "recruitment_time_submissions",
        submissionId,
        status,
        reviewerId,
        note,
        threadId,
    )


async def listRecruitmentPendingStatuses() -> List[Dict]:
    return await fetchAll(
        "SELECT * FROM recruitment_submissions WHERE status IN ('PENDING', 'NEEDS_INFO')",
    )


async def listRecruitmentTimePendingStatuses() -> List[Dict]:
    return await fetchAll(
        "SELECT * FROM recruitment_time_submissions WHERE status IN ('PENDING', 'NEEDS_INFO')",
    )


async def awardPoints(userId: int, points: int) -> None:
    await execute(
        """
        INSERT INTO points (userId, pointsTotal)
        VALUES (?, ?)
        ON CONFLICT(userId) DO UPDATE SET pointsTotal = points.pointsTotal + excluded.pointsTotal
        """,
        (userId, points),
    )

async def queuePoints(
    userId: int,
    points: int,
    sourceType: str,
    sourceId: Optional[int] = None,
) -> None:
    await execute(
        """
        INSERT INTO points_pending (userId, points, sourceType, sourceId)
        VALUES (?, ?, ?, ?)
        """,
        (userId, points, sourceType, sourceId),
    )


async def queuePointsBatch(entries: List[tuple[int, int, str, Optional[int]]]) -> int:
    normalized: list[tuple[int, int, str, Optional[int]]] = []
    for userId, points, sourceType, sourceId in entries or []:
        try:
            userIdValue = int(userId)
            pointsValue = int(points)
        except (TypeError, ValueError):
            continue
        sourceTypeValue = str(sourceType or "").strip()
        if userIdValue <= 0 or pointsValue <= 0 or not sourceTypeValue:
            continue
        sourceIdValue: Optional[int]
        try:
            sourceIdValue = int(sourceId) if sourceId is not None else None
        except (TypeError, ValueError):
            sourceIdValue = None
        normalized.append((userIdValue, pointsValue, sourceTypeValue, sourceIdValue))

    if not normalized:
        return 0

    await executeMany(
        """
        INSERT INTO points_pending (userId, points, sourceType, sourceId)
        VALUES (?, ?, ?, ?)
        """,
        normalized,
    )
    return len(normalized)


async def processPendingPoints() -> dict:
    summary = await fetchOne(
        """
        SELECT
            COUNT(*) AS users,
            COALESCE(SUM(total), 0) AS points
        FROM (
            SELECT userId, SUM(points) AS total
            FROM points_pending
            WHERE processedAt IS NULL
            GROUP BY userId
            HAVING SUM(points) > 0
        ) grouped
        """
    )
    totalUsers = int((summary or {}).get("users") or 0)
    totalPoints = int((summary or {}).get("points") or 0)

    if totalUsers <= 0 or totalPoints <= 0:
        await execute(
            "UPDATE points_pending SET processedAt = datetime('now') WHERE processedAt IS NULL"
        )
        return {"users": 0, "points": 0}

    await execute(
        """
        INSERT INTO points (userId, pointsTotal)
        SELECT userId, SUM(points) AS total
        FROM points_pending
        WHERE processedAt IS NULL
        GROUP BY userId
        HAVING SUM(points) > 0
        ON CONFLICT(userId) DO UPDATE SET
            pointsTotal = points.pointsTotal + excluded.pointsTotal
        """
    )
    await execute(
        "UPDATE points_pending SET processedAt = datetime('now') WHERE processedAt IS NULL"
    )
    return {"users": totalUsers, "points": totalPoints}

async def getSetting(key: str) -> Optional[str]:
    row = await fetchOne("SELECT value FROM bot_settings WHERE key = ?", (key,))
    if not row:
        return None
    return row.get("value")

async def setSetting(key: str, value: str) -> None:
    await execute(
        """
        INSERT INTO bot_settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


async def applyOrientationBonusForRecruit(recruitUserId: int, bonusPoints: int) -> list[dict]:
    if bonusPoints <= 0:
        return []
    submissions = await fetchAll(
        """
        SELECT * FROM recruitment_submissions
        WHERE recruitUserId = ? AND passedOrientation = 0
        """,
        (recruitUserId,),
    )
    if not submissions:
        return []

    updates: list[dict] = []
    queueBatch: list[tuple[int, int, str, Optional[int]]] = []
    for submission in submissions:
        submissionId = submission["submissionId"]
        await execute(
            "UPDATE recruitment_submissions SET passedOrientation = 1, points = points + ? WHERE submissionId = ?",
            (bonusPoints, submissionId),
        )
        bonusCredited = False
        if submission["status"] == "APPROVED":
            queueBatch.append((int(submission["submitterId"]), int(bonusPoints), "recruitment-bonus", int(submissionId)))
            bonusCredited = True
        updated = await getRecruitmentSubmission(submissionId)
        if updated:
            updates.append(
                {
                    "submission": updated,
                    "bonusCredited": bonusCredited,
                }
            )
    if queueBatch:
        await queuePointsBatch(queueBatch)
    return updates
