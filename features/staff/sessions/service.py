import hashlib
import json
from typing import Optional, List, Dict
from db.sqlite import fetchOne, fetchAll, execute, executeReturnId, executeMany
from features.staff.sessions.bgBuckets import adultBgReviewBucket, minorBgReviewBucket, normalizeBgReviewBucket


def _jsonArray(value) -> str:
    return json.dumps(value or [])


def _flagValue(value) -> int:
    return 1 if value else 0


def hashPassword(password: str) -> str:
    # simple SHA256; good enough for this use
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

async def createSession(guildId: int, channelId: int, messageId: int, sessionType: str, hostId: int, password: str, maxAttendeeLimit: int) -> int:
    passwordHash = hashPassword(password)
    sessionId = await executeReturnId(
        """INSERT INTO sessions (guildId, channelId, messageId, sessionType, hostId, passwordHash, maxAttendeeLimit, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
        (guildId, channelId, messageId, sessionType, hostId, passwordHash, maxAttendeeLimit)
    )
    return sessionId

async def getSession(sessionId: int) -> Optional[Dict]:
    return await fetchOne("SELECT * FROM sessions WHERE sessionId = ?", (sessionId,))

async def getSessionsByStatus(statuses: List[str]) -> List[Dict]:
    if not statuses:
        return []
    placeholders = ",".join("?" for _ in statuses)
    query = f"SELECT * FROM sessions WHERE status IN ({placeholders})"
    return await fetchAll(query, tuple(statuses))


async def expireStaleSessions(maxAgeHours: int = 48, statuses: Optional[List[str]] = None) -> List[int]:
    targetStatuses = statuses or ["OPEN", "GRADING"]
    if not targetStatuses:
        return []

    normalizedMaxAge = max(1, int(maxAgeHours))
    placeholders = ",".join("?" for _ in targetStatuses)
    cutoffExpr = f"-{normalizedMaxAge} hours"

    staleRows = await fetchAll(
        f"""
        SELECT sessionId
        FROM sessions
        WHERE status IN ({placeholders})
          AND datetime(createdAt) <= datetime('now', ?)
        """,
        tuple(targetStatuses) + (cutoffExpr,),
    )
    if not staleRows:
        return []

    sessionIds = [int(row["sessionId"]) for row in staleRows]
    idPlaceholders = ",".join("?" for _ in sessionIds)
    await execute(
        f"""
        UPDATE sessions
        SET status = 'CANCELED',
            finishedAt = COALESCE(finishedAt, datetime('now'))
        WHERE sessionId IN ({idPlaceholders})
        """,
        tuple(sessionIds),
    )
    return sessionIds


async def setBgQueueMessage(
    sessionId: int,
    messageId: int,
    *,
    reviewBucket: str = adultBgReviewBucket,
):
    normalizedBucket = normalizeBgReviewBucket(reviewBucket)
    columnName = "bgQueueMinorMessageId" if normalizedBucket == minorBgReviewBucket else "bgQueueMessageId"
    await execute(
        f"UPDATE sessions SET {columnName} = ? WHERE sessionId = ?",
        (int(messageId), int(sessionId))
    )


async def setBgReviewBucket(sessionId: int, userId: int, reviewBucket: str) -> None:
    await execute(
        "UPDATE attendees SET bgReviewBucket = ? WHERE sessionId = ? AND userId = ?",
        (
            normalizeBgReviewBucket(reviewBucket),
            int(sessionId),
            int(userId),
        ),
    )


async def setBgReviewBucketsBulk(
    sessionId: int,
    reviewBucketsByUserId: dict[int, str],
) -> None:
    normalizedRows: list[tuple[str, int, int]] = []
    for rawUserId, rawBucket in dict(reviewBucketsByUserId or {}).items():
        try:
            userId = int(rawUserId)
        except (TypeError, ValueError):
            continue
        if userId <= 0:
            continue
        normalizedRows.append(
            (
                normalizeBgReviewBucket(rawBucket),
                int(sessionId),
                userId,
            )
        )
    if not normalizedRows:
        return
    await executeMany(
        "UPDATE attendees SET bgReviewBucket = ? WHERE sessionId = ? AND userId = ?",
        normalizedRows,
    )


async def setSessionMessageId(sessionId: int, messageId: int) -> None:
    await execute(
        "UPDATE sessions SET messageId = ? WHERE sessionId = ?",
        (int(messageId), int(sessionId)),
    )

async def getAttendees(sessionId: int) -> List[Dict]:
    return await fetchAll(
        "SELECT * FROM attendees WHERE sessionId = ? ORDER BY datetime(joinTime) ASC",
        (sessionId,)
    )

async def getAttendee(sessionId: int, userId: int) -> Optional[Dict]:
    return await fetchOne(
        "SELECT * FROM attendees WHERE sessionId = ? AND userId = ?",
        (sessionId, userId)
    )

async def addAttendee(sessionId: int, userId: int):
    await execute(
        "INSERT OR IGNORE INTO attendees (sessionId, userId) VALUES (?, ?)",
        (sessionId, userId)
    )


async def addAttendeesBulk(
    sessionId: int,
    userIds: List[int],
    *,
    examGrade: Optional[str] = None,
) -> int:
    uniqueUserIds: list[int] = []
    seen: set[int] = set()
    for rawUserId in userIds or []:
        try:
            parsedUserId = int(rawUserId)
        except (TypeError, ValueError):
            continue
        if parsedUserId <= 0 or parsedUserId in seen:
            continue
        seen.add(parsedUserId)
        uniqueUserIds.append(parsedUserId)
    if not uniqueUserIds:
        return 0

    insertParams = [(int(sessionId), int(userId)) for userId in uniqueUserIds]
    await executeMany(
        "INSERT OR IGNORE INTO attendees (sessionId, userId) VALUES (?, ?)",
        insertParams,
    )

    normalizedExamGrade = str(examGrade or "").strip().upper()
    if normalizedExamGrade:
        gradeParams = [
            (normalizedExamGrade, int(sessionId), int(userId))
            for userId in uniqueUserIds
        ]
        await executeMany(
            "UPDATE attendees SET examGrade = ? WHERE sessionId = ? AND userId = ?",
            gradeParams,
        )

    return len(uniqueUserIds)


async def removeAttendee(sessionId: int, userId: int) -> None:
    await execute(
        "DELETE FROM attendees WHERE sessionId = ? AND userId = ?",
        (int(sessionId), int(userId)),
    )

async def setStatus(sessionId: int, status: str):
    await execute("UPDATE sessions SET status = ? WHERE sessionId = ?", (status, sessionId))

async def setExamGrade(sessionId: int, userId: int, grade: str):
    await execute(
        "UPDATE attendees SET examGrade = ? WHERE sessionId = ? AND userId = ?",
        (grade, sessionId, userId)
    )

async def incrementGradingIndex(sessionId: int):
    await execute("UPDATE sessions SET gradingIndex = gradingIndex + 1 WHERE sessionId = ?", (sessionId,))

async def resetGradingIndex(sessionId: int):
    await execute("UPDATE sessions SET gradingIndex = 0 WHERE sessionId = ?", (sessionId,))

async def cancelSession(sessionId: int):
    await execute("UPDATE sessions SET status = 'CANCELED' WHERE sessionId = ?", (sessionId,))

async def finishSession(sessionId: int):
    await execute(
        "UPDATE sessions SET status = 'FINISHED', finishedAt = datetime('now') WHERE sessionId = ?",
        (sessionId,)
    )

async def verifyPassword(sessionId: int, password: str) -> bool:
    session = await getSession(sessionId)
    if not session:
        return False
    return session["passwordHash"] == hashPassword(password)

async def isFinishAllowed(sessionId: int) -> tuple[bool, str]:
    attendees = await getAttendees(sessionId)
    if not attendees:
        return False, "No attendees are clocked in."
    for a in attendees:
        if a["examGrade"] == "NOT_GRADED":
            await execute("UPDATE attendees SET examGrade = 'FAIL' WHERE sessionId = ? AND examGrade = 'NOT_GRADED'",
            (sessionId,))
    return True, ""

async def setBgStatus(sessionId: int, userId: int, bgStatus: str):
    current = await fetchOne(
        "SELECT bgStatus FROM attendees WHERE sessionId = ? AND userId = ?",
        (sessionId, userId),
    )
    previousStatus = str((current or {}).get("bgStatus") or "").upper()
    normalizedStatus = str(bgStatus or "").upper()

    await execute(
        "UPDATE attendees SET bgStatus = ? WHERE sessionId = ? AND userId = ?",
        (normalizedStatus, sessionId, userId)
    )
    if normalizedStatus in {"APPROVED", "REJECTED"}:
        await execute(
            """
            UPDATE attendees
            SET robloxGroupsJson = NULL,
                robloxFlaggedGroupsJson = NULL,
                robloxFlagMatchesJson = NULL,
                robloxFlagged = NULL,
                robloxGroupScanStatus = NULL,
                robloxGroupScanError = NULL,
                robloxGroupScanAt = NULL,
                robloxInventoryItemsJson = NULL,
                robloxFlaggedItemsJson = NULL,
                robloxInventoryScanStatus = NULL,
                robloxInventoryScanError = NULL,
                robloxInventoryScanAt = NULL,
                robloxFlaggedBadgesJson = NULL,
                robloxBadgeScanStatus = NULL,
                robloxBadgeScanError = NULL,
                robloxBadgeScanAt = NULL,
                robloxOutfitsJson = NULL,
                robloxOutfitScanStatus = NULL,
                robloxOutfitScanError = NULL,
                robloxOutfitScanAt = NULL
            WHERE sessionId = ? AND userId = ?
            """,
            (sessionId, userId),
        )

    return previousStatus


async def setBgStatusWithReviewer(
    sessionId: int,
    userId: int,
    bgStatus: str,
    reviewerId: Optional[int],
) -> bool:
    previousStatus = await setBgStatus(sessionId, userId, bgStatus)
    normalizedStatus = str(bgStatus or "").upper()
    changed = normalizedStatus != previousStatus
    if normalizedStatus not in {"APPROVED", "REJECTED"}:
        return changed
    if not changed:
        return False
    try:
        reviewerIdInt = int(reviewerId) if reviewerId is not None else 0
    except (TypeError, ValueError):
        reviewerIdInt = 0
    if reviewerIdInt <= 0:
        return True

    await execute(
        """
        INSERT INTO bg_review_actions (sessionId, attendeeUserId, reviewerId, decision)
        VALUES (?, ?, ?, ?)
        """,
        (int(sessionId), int(userId), reviewerIdInt, normalizedStatus),
    )
    return True


async def getBgReviewLeaderboard(limit: int = 10) -> List[Dict]:
    normalizedLimit = max(1, min(int(limit or 10), 50))
    return await fetchAll(
        """
        SELECT
            reviewerId,
            SUM(CASE WHEN decision = 'APPROVED' THEN 1 ELSE 0 END) AS approvals,
            SUM(CASE WHEN decision = 'REJECTED' THEN 1 ELSE 0 END) AS rejections,
            COUNT(*) AS total
        FROM bg_review_actions
        GROUP BY reviewerId
        ORDER BY total DESC, approvals DESC, rejections DESC, reviewerId ASC
        LIMIT ?
        """,
        (normalizedLimit,),
    )


async def getBgReviewSessionStats(sessionId: int) -> List[Dict]:
    return await fetchAll(
        """
        SELECT
            reviewerId,
            SUM(CASE WHEN decision = 'APPROVED' THEN 1 ELSE 0 END) AS approvals,
            SUM(CASE WHEN decision = 'REJECTED' THEN 1 ELSE 0 END) AS rejections,
            COUNT(*) AS total
        FROM bg_review_actions
        WHERE sessionId = ?
        GROUP BY reviewerId
        ORDER BY total DESC, approvals DESC, rejections DESC, reviewerId ASC
        """,
        (int(sessionId),),
    )

async def awardHostPointIfEligible(sessionId: int, userId: int):
    # Called when BG is approved/rejected; points only on PASS + APPROVED, and only once.
    session = await getSession(sessionId)
    if not session:
        return
    attendee = await fetchOne(
        "SELECT * FROM attendees WHERE sessionId = ? AND userId = ?",
        (sessionId, userId)
    )
    if not attendee:
        return
    if attendee["credited"] == 1:
        return
    if attendee["examGrade"] == "PASS" and attendee["bgStatus"] == "APPROVED":
        hostId = session["hostId"]
        await execute("INSERT OR IGNORE INTO points (userId, pointsTotal) VALUES (?, 0)", (hostId,))
        await execute("UPDATE points SET pointsTotal = pointsTotal + 1 WHERE userId = ?", (hostId,))
        await execute(
            "UPDATE attendees SET credited = 1 WHERE sessionId = ? AND userId = ?",
            (sessionId, userId)
        )

async def hasPassedOrientation(userId: int) -> bool:
    row = await fetchOne(
        """
        SELECT 1
        FROM attendees a
        JOIN sessions s ON s.sessionId = a.sessionId
        WHERE a.userId = ?
          AND s.sessionType = 'orientation'
          AND a.examGrade = 'PASS'
          AND a.bgStatus = 'APPROVED'
        LIMIT 1
        """,
        (userId,),
    )
    return row is not None

async def setRobloxStatus(
    sessionId: int,
    userId: int,
    robloxUserId: Optional[int],
    status: str,
    error: Optional[str] = None,
):
    await execute(
        """
        UPDATE attendees
        SET robloxUserId = ?, robloxJoinStatus = ?, robloxLastError = ?, robloxProcessedAt = datetime('now')
        WHERE sessionId = ? AND userId = ?
        """,
        (robloxUserId, status, error, sessionId, userId),
    )

async def setRobloxGroupScan(
    sessionId: int,
    userId: int,
    groups: Optional[list[dict]],
    flaggedGroups: Optional[list[dict]],
    flagMatches: Optional[list[dict]],
    status: str,
    error: Optional[str] = None,
    robloxUserId: Optional[int] = None,
    robloxUsername: Optional[str] = None,
):
    groupsJson = _jsonArray(groups)
    flaggedJson = _jsonArray(flaggedGroups)
    matchesJson = _jsonArray(flagMatches)
    flagged = _flagValue(flaggedGroups or flagMatches)
    await execute(
        """
        UPDATE attendees
        SET robloxGroupsJson = ?, robloxFlaggedGroupsJson = ?, robloxFlagMatchesJson = ?,
            robloxFlagged = CASE WHEN ? = 1 THEN 1 ELSE robloxFlagged END,
            robloxGroupScanStatus = ?, robloxGroupScanError = ?, robloxGroupScanAt = datetime('now'),
            robloxUserId = COALESCE(?, robloxUserId),
            robloxUsername = COALESCE(?, robloxUsername)
        WHERE sessionId = ? AND userId = ?
        """,
        (
            groupsJson,
            flaggedJson,
            matchesJson,
            flagged,
            status,
            error,
            robloxUserId,
            robloxUsername,
            sessionId,
            userId,
        ),
    )

async def setRobloxInventoryScan(
    sessionId: int,
    userId: int,
    items: Optional[list[dict]],
    flaggedItems: Optional[list[dict]],
    status: str,
    error: Optional[str] = None,
    robloxUserId: Optional[int] = None,
    robloxUsername: Optional[str] = None,
):
    itemsJson = _jsonArray(items)
    flaggedJson = _jsonArray(flaggedItems)
    flagged = _flagValue(flaggedItems)
    await execute(
        """
        UPDATE attendees
        SET robloxInventoryItemsJson = ?, robloxFlaggedItemsJson = ?,
            robloxInventoryScanStatus = ?, robloxInventoryScanError = ?, robloxInventoryScanAt = datetime('now'),
            robloxFlagged = CASE WHEN ? = 1 THEN 1 ELSE robloxFlagged END,
            robloxUserId = COALESCE(?, robloxUserId),
            robloxUsername = COALESCE(?, robloxUsername)
        WHERE sessionId = ? AND userId = ?
        """,
        (itemsJson, flaggedJson, status, error, flagged, robloxUserId, robloxUsername, sessionId, userId),
    )

async def setRobloxBadgeScan(
    sessionId: int,
    userId: int,
    flaggedBadges: Optional[list[dict]],
    status: str,
    error: Optional[str] = None,
    robloxUserId: Optional[int] = None,
    robloxUsername: Optional[str] = None,
):
    flaggedJson = _jsonArray(flaggedBadges)
    flagged = _flagValue(flaggedBadges)
    await execute(
        """
        UPDATE attendees
        SET robloxFlaggedBadgesJson = ?,
            robloxBadgeScanStatus = ?, robloxBadgeScanError = ?, robloxBadgeScanAt = datetime('now'),
            robloxFlagged = CASE WHEN ? = 1 THEN 1 ELSE robloxFlagged END,
            robloxUserId = COALESCE(?, robloxUserId),
            robloxUsername = COALESCE(?, robloxUsername)
        WHERE sessionId = ? AND userId = ?
        """,
        (flaggedJson, status, error, flagged, robloxUserId, robloxUsername, sessionId, userId),
    )

async def setRobloxOutfitScan(
    sessionId: int,
    userId: int,
    outfits: Optional[list[dict]],
    status: str,
    error: Optional[str] = None,
    robloxUserId: Optional[int] = None,
    robloxUsername: Optional[str] = None,
):
    outfitsJson = _jsonArray(outfits)
    await execute(
        """
        UPDATE attendees
        SET robloxOutfitsJson = ?,
            robloxOutfitScanStatus = ?, robloxOutfitScanError = ?, robloxOutfitScanAt = datetime('now'),
            robloxUserId = COALESCE(?, robloxUserId),
            robloxUsername = COALESCE(?, robloxUsername)
        WHERE sessionId = ? AND userId = ?
        """,
        (outfitsJson, status, error, robloxUserId, robloxUsername, sessionId, userId),
    )
