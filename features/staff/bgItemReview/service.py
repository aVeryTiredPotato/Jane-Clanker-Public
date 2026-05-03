from __future__ import annotations

from typing import Any, Dict, List, Optional

from db.sqlite import execute, executeReturnId, fetchAll, fetchOne

STATUS_PENDING = "PENDING"
STATUS_SECOND_REVIEW = "SECOND_REVIEW"
STATUS_FLAGGED = "FLAGGED"
STATUS_SAFE = "SAFE"
STATUS_IGNORED = "IGNORED"

OPEN_STATUSES = {STATUS_PENDING, STATUS_SECOND_REVIEW}
FINAL_STATUSES = {STATUS_FLAGGED, STATUS_SAFE, STATUS_IGNORED}
ALL_STATUSES = OPEN_STATUSES | FINAL_STATUSES


def normalizeStatus(value: object) -> str:
    text = str(value or "").strip().upper()
    if text == STATUS_SECOND_REVIEW:
        return STATUS_PENDING
    return text if text in ALL_STATUSES else STATUS_PENDING


def _rowPriority(row: dict[str, Any]) -> tuple[int, int]:
    status = normalizeStatus(row.get("status"))
    if status in OPEN_STATUSES:
        return (3, int(row.get("queueId") or 0))
    if status == STATUS_FLAGGED:
        return (2, int(row.get("queueId") or 0))
    if status == STATUS_SAFE:
        return (1, int(row.get("queueId") or 0))
    return (0, int(row.get("queueId") or 0))


async def getQueueEntry(queueId: int) -> Optional[Dict]:
    return await fetchOne(
        "SELECT * FROM bg_item_review_queue WHERE queueId = ?",
        (int(queueId),),
    )


async def getQueueEntryByReviewMessage(reviewChannelId: int, reviewMessageId: int) -> Optional[Dict]:
    return await fetchOne(
        """
        SELECT *
        FROM bg_item_review_queue
        WHERE reviewChannelId = ? AND reviewMessageId = ?
        """,
        (int(reviewChannelId), int(reviewMessageId)),
    )


async def listOpenQueueEntries() -> List[Dict]:
    return await fetchAll(
        """
        SELECT *
        FROM bg_item_review_queue
        WHERE status IN (?, ?)
        ORDER BY datetime(lastSeenAt) DESC, queueId DESC
        """,
        (STATUS_PENDING, STATUS_SECOND_REVIEW),
    )


async def listQueueCounts(*, guildId: int | None = None) -> Dict[str, int]:
    params: list[int] = []
    query = """
        SELECT status, COUNT(*) AS total
        FROM bg_item_review_queue
    """
    if int(guildId or 0) > 0:
        query += " WHERE guildId = ?"
        params.append(int(guildId))
    query += " GROUP BY status"
    rows = await fetchAll(query, tuple(params))
    counts = {
        STATUS_PENDING: 0,
        STATUS_SECOND_REVIEW: 0,
        STATUS_FLAGGED: 0,
        STATUS_SAFE: 0,
        STATUS_IGNORED: 0,
    }
    for row in rows:
        status = normalizeStatus(row.get("status"))
        counts[status] += int(row.get("total") or 0)
    counts["total"] = sum(counts.values())
    return counts


async def listQueueEntriesByStatus(
    statuses: list[str] | tuple[str, ...],
    *,
    guildId: int | None = None,
    limit: int = 10,
) -> List[Dict]:
    requested: set[str] = set()
    for rawStatus in list(statuses or []):
        normalized = normalizeStatus(rawStatus)
        requested.add(normalized)
        if normalized == STATUS_PENDING:
            requested.add(STATUS_SECOND_REVIEW)
    if not requested:
        return []

    normalizedLimit = max(1, min(int(limit or 10), 100))
    placeholders = ", ".join("?" for _ in requested)
    params: list[object] = list(requested)
    query = f"""
        SELECT *
        FROM bg_item_review_queue
        WHERE status IN ({placeholders})
    """
    if int(guildId or 0) > 0:
        query += " AND guildId = ?"
        params.append(int(guildId))
    query += """
        ORDER BY datetime(COALESCE(reviewedAt, lastSeenAt, updatedAt, createdAt)) DESC, queueId DESC
        LIMIT ?
    """
    params.append(normalizedLimit)
    return await fetchAll(query, tuple(params))


async def findCandidateMatch(assetId: int, thumbnailHash: str) -> Optional[Dict]:
    normalizedHash = str(thumbnailHash or "").strip()
    rows = await fetchAll(
        """
        SELECT *
        FROM bg_item_review_queue
        WHERE assetId = ?
           OR (COALESCE(thumbnailHash, '') <> '' AND thumbnailHash = ?)
        ORDER BY datetime(lastSeenAt) DESC, queueId DESC
        """,
        (int(assetId), normalizedHash),
    )
    if not rows:
        return None
    rows.sort(key=_rowPriority, reverse=True)
    return rows[0]


async def createQueueEntry(
    *,
    guildId: int,
    sessionId: int,
    assetId: int,
    assetName: Optional[str],
    itemType: Optional[str],
    creatorId: Optional[int],
    creatorName: Optional[str],
    priceRobux: Optional[int],
    thumbnailHash: str,
    thumbnailUrl: Optional[str],
    thumbnailState: Optional[str],
    sourceUserId: int,
    sourceRobloxUserId: Optional[int],
    sourceRobloxUsername: Optional[str],
    queuedByReviewerId: int,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO bg_item_review_queue (
            guildId, sessionId, assetId, assetName, itemType,
            creatorId, creatorName, priceRobux,
            thumbnailHash, thumbnailUrl, thumbnailState,
            status, seenCount, sourceUserId, sourceRobloxUserId, sourceRobloxUsername,
            lastQueuedByReviewerId
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
        """,
        (
            int(guildId),
            int(sessionId) if int(sessionId or 0) > 0 else None,
            int(assetId),
            str(assetName or "").strip() or None,
            str(itemType or "").strip() or None,
            int(creatorId) if creatorId is not None else None,
            str(creatorName or "").strip() or None,
            int(priceRobux) if priceRobux is not None else None,
            str(thumbnailHash or "").strip(),
            str(thumbnailUrl or "").strip() or None,
            str(thumbnailState or "").strip() or None,
            STATUS_PENDING,
            int(sourceUserId or 0),
            int(sourceRobloxUserId) if sourceRobloxUserId is not None else None,
            str(sourceRobloxUsername or "").strip() or None,
            int(queuedByReviewerId or 0),
        ),
    )


async def touchQueueEntry(
    queueId: int,
    *,
    guildId: int,
    sessionId: int,
    sourceUserId: int,
    sourceRobloxUserId: Optional[int],
    sourceRobloxUsername: Optional[str],
    queuedByReviewerId: int,
) -> None:
    await execute(
        """
        UPDATE bg_item_review_queue
        SET guildId = ?,
            sessionId = COALESCE(?, sessionId),
            seenCount = seenCount + 1,
            lastSeenAt = datetime('now'),
            sourceUserId = ?,
            sourceRobloxUserId = COALESCE(?, sourceRobloxUserId),
            sourceRobloxUsername = COALESCE(?, sourceRobloxUsername),
            lastQueuedByReviewerId = ?,
            updatedAt = datetime('now')
        WHERE queueId = ?
        """,
        (
            int(guildId),
            int(sessionId) if int(sessionId or 0) > 0 else None,
            int(sourceUserId or 0),
            int(sourceRobloxUserId) if sourceRobloxUserId is not None else None,
            str(sourceRobloxUsername or "").strip() or None,
            int(queuedByReviewerId or 0),
            int(queueId),
        ),
    )


async def setReviewMessage(queueId: int, reviewChannelId: int, reviewMessageId: int) -> None:
    await execute(
        """
        UPDATE bg_item_review_queue
        SET reviewChannelId = ?, reviewMessageId = ?, updatedAt = datetime('now')
        WHERE queueId = ?
        """,
        (int(reviewChannelId), int(reviewMessageId), int(queueId)),
    )


async def addSourceRecord(
    *,
    queueId: int,
    guildId: int,
    sessionId: int,
    sourceUserId: int,
    sourceRobloxUserId: Optional[int],
    sourceRobloxUsername: Optional[str],
    queuedByReviewerId: int,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO bg_item_review_sources (
            queueId, guildId, sessionId,
            sourceUserId, sourceRobloxUserId, sourceRobloxUsername, queuedByReviewerId
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(queueId),
            int(guildId),
            int(sessionId) if int(sessionId or 0) > 0 else None,
            int(sourceUserId or 0),
            int(sourceRobloxUserId) if sourceRobloxUserId is not None else None,
            str(sourceRobloxUsername or "").strip() or None,
            int(queuedByReviewerId or 0),
        ),
    )


async def addAction(
    queueId: int,
    *,
    actorId: int,
    action: str,
    note: Optional[str] = None,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO bg_item_review_actions (queueId, actorId, action, note)
        VALUES (?, ?, ?, ?)
        """,
        (
            int(queueId),
            int(actorId or 0),
            str(action or "").strip().upper() or "UNKNOWN",
            str(note or "").strip() or None,
        ),
    )


async def updateQueueStatus(
    queueId: int,
    *,
    status: str,
    reviewerId: int,
    note: Optional[str] = None,
) -> None:
    normalizedStatus = normalizeStatus(status)
    await execute(
        """
        UPDATE bg_item_review_queue
        SET status = ?,
            reviewNote = ?,
            reviewedBy = ?,
            reviewedAt = datetime('now'),
            updatedAt = datetime('now')
        WHERE queueId = ?
        """,
        (
            normalizedStatus,
            str(note or "").strip() or None,
            int(reviewerId or 0),
            int(queueId),
        ),
    )


async def listSourcesForQueue(queueId: int, *, limit: int = 5) -> List[Dict]:
    normalizedLimit = max(1, min(int(limit or 5), 20))
    return await fetchAll(
        """
        SELECT *
        FROM bg_item_review_sources
        WHERE queueId = ?
        ORDER BY datetime(createdAt) DESC, sourceId DESC
        LIMIT ?
        """,
        (int(queueId), normalizedLimit),
    )


async def listActionsForQueue(queueId: int, *, limit: int = 10) -> List[Dict]:
    normalizedLimit = max(1, min(int(limit or 10), 50))
    return await fetchAll(
        """
        SELECT *
        FROM bg_item_review_actions
        WHERE queueId = ?
        ORDER BY datetime(createdAt) DESC, actionId DESC
        LIMIT ?
        """,
        (int(queueId), normalizedLimit),
    )


async def getSheetSyncState(
    spreadsheetId: str,
    sheetName: str,
    rowNumber: int,
) -> Optional[Dict]:
    return await fetchOne(
        """
        SELECT *
        FROM bg_item_review_sheet_sync
        WHERE spreadsheetId = ? AND sheetName = ? AND rowNumber = ?
        """,
        (
            str(spreadsheetId or "").strip(),
            str(sheetName or "").strip(),
            int(rowNumber or 0),
        ),
    )


async def upsertSheetSyncState(
    *,
    spreadsheetId: str,
    sheetName: str,
    rowNumber: int,
    discordUserId: int,
    entryStatus: str,
    fingerprint: str,
    queued: bool = False,
) -> None:
    normalizedSpreadsheetId = str(spreadsheetId or "").strip()
    normalizedSheetName = str(sheetName or "").strip()
    normalizedEntryStatus = str(entryStatus or "").strip().lower()
    normalizedFingerprint = str(fingerprint or "").strip()
    if not normalizedSpreadsheetId or not normalizedSheetName or int(rowNumber or 0) <= 0:
        return

    await execute(
        """
        INSERT INTO bg_item_review_sheet_sync (
            spreadsheetId, sheetName, rowNumber,
            discordUserId, entryStatus, fingerprint,
            processedAt, lastQueuedAt
        )
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), CASE WHEN ? THEN datetime('now') ELSE NULL END)
        ON CONFLICT(spreadsheetId, sheetName, rowNumber) DO UPDATE SET
            discordUserId = excluded.discordUserId,
            entryStatus = excluded.entryStatus,
            fingerprint = excluded.fingerprint,
            processedAt = datetime('now'),
            lastQueuedAt = CASE
                WHEN excluded.lastQueuedAt IS NOT NULL THEN datetime('now')
                ELSE bg_item_review_sheet_sync.lastQueuedAt
            END
        """,
        (
            normalizedSpreadsheetId,
            normalizedSheetName,
            int(rowNumber),
            int(discordUserId or 0),
            normalizedEntryStatus,
            normalizedFingerprint,
            1 if queued else 0,
        ),
    )
