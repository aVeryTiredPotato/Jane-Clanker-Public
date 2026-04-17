from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Optional

import aiohttp

from db.sqlite import execute, executeReturnId, fetchAll, fetchOne

_httpSession: Optional[aiohttp.ClientSession] = None


def _normalizeText(value: object) -> str:
    return str(value or "").strip()


async def _getHttpSession() -> aiohttp.ClientSession:
    global _httpSession
    if _httpSession is None or _httpSession.closed:
        timeout = aiohttp.ClientTimeout(total=10)
        _httpSession = aiohttp.ClientSession(timeout=timeout)
    return _httpSession


def make_digest(message: str, key: str) -> str:
    keyBytes = bytes(str(key or ""), "UTF-8")
    messageBytes = bytes(str(message or ""), "UTF-8")
    digester = hmac.new(keyBytes, messageBytes, hashlib.sha1)
    return digester.digest().hex()


async def addSuggestionToFreedcamp(
    *,
    suggestionId: int,
    submitterName: str,
    content: str,
    apiKey: str,
    keySecret: str,
    projectId: int,
    taskGroupId: int,
) -> int:
    normalizedApiKey = _normalizeText(apiKey)
    normalizedSecret = _normalizeText(keySecret)
    if not normalizedApiKey or not normalizedSecret or int(projectId or 0) <= 0 or int(taskGroupId or 0) <= 0:
        raise ValueError("Freedcamp is not configured.")

    session = await _getHttpSession()
    timestamp = str(int(1000 * time.time()))
    authParams = {
        "api_key": normalizedApiKey,
        "timestamp": timestamp,
        "hash": make_digest(normalizedApiKey + timestamp, normalizedSecret),
    }
    payload = {
        "project_id": int(projectId),
        "task_group_id": int(taskGroupId),
        "title": f"Suggestion #{int(suggestionId)} from User {_normalizeText(submitterName) or 'Unknown'}",
        "description": str(content or ""),
        "priority": 0,
        "assigned_to_id": 0,
    }
    headers = {"Content-Type": "application/json"}
    async with session.post(
        "https://freedcamp.com/api/v1/tasks/",
        json=payload,
        headers=headers,
        params=authParams,
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"Freedcamp API request failed with status {response.status}: {response.reason}")
        responseObj = await response.json(content_type=None)
        taskId = ((responseObj.get("data") or {}).get("tasks") or [{}])[0].get("id")
        return int(taskId or 0)


async def createSuggestion(
    *,
    guildId: int,
    channelId: int,
    submitterId: int,
    content: str,
    anonymous: bool = False,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO suggestions
            (guildId, channelId, submitterId, content, anonymous, status, updatedAt)
        VALUES (?, ?, ?, ?, ?, 'PENDING', datetime('now'))
        """,
        (
            int(guildId),
            int(channelId),
            int(submitterId),
            _normalizeText(content),
            1 if anonymous else 0,
        ),
    )


async def setSuggestionMessageId(suggestionId: int, messageId: int) -> None:
    await execute(
        """
        UPDATE suggestions
        SET messageId = ?, updatedAt = datetime('now')
        WHERE suggestionId = ?
        """,
        (int(messageId), int(suggestionId)),
    )


async def getSuggestion(suggestionId: int) -> dict | None:
    return await fetchOne("SELECT * FROM suggestions WHERE suggestionId = ?", (int(suggestionId),))


async def getSuggestionByMessageId(messageId: int) -> dict | None:
    return await fetchOne("SELECT * FROM suggestions WHERE messageId = ?", (int(messageId),))


async def setSuggestionThreadId(suggestionId: int, threadId: int) -> None:
    await execute(
        """
        UPDATE suggestions
        SET threadId = ?, updatedAt = datetime('now')
        WHERE suggestionId = ?
        """,
        (int(threadId), int(suggestionId)),
    )


async def setSuggestionFreedcampId(suggestionId: int, freedcampId: int) -> None:
    await execute(
        """
        UPDATE suggestions
        SET freedcampId = ?, updatedAt = datetime('now')
        WHERE suggestionId = ?
        """,
        (int(freedcampId), int(suggestionId)),
    )


async def listSuggestions(guildId: int, *, status: str | None = None, limit: int = 10) -> list[dict]:
    normalizedStatus = _normalizeText(status).upper()
    if normalizedStatus:
        return await fetchAll(
            """
            SELECT *
            FROM suggestions
            WHERE guildId = ? AND status = ?
            ORDER BY suggestionId DESC
            LIMIT ?
            """,
            (int(guildId), normalizedStatus, max(1, int(limit or 10))),
        )
    return await fetchAll(
        """
        SELECT *
        FROM suggestions
        WHERE guildId = ?
        ORDER BY suggestionId DESC
        LIMIT ?
        """,
        (int(guildId), max(1, int(limit or 10))),
    )


async def listPendingSuggestions() -> list[dict]:
    return await fetchAll(
        """
        SELECT *
        FROM suggestions
        WHERE status = 'PENDING'
        ORDER BY suggestionId ASC
        """
    )


async def createSuggestionBoard(guildId: int, channelId: int, messageId: int) -> None:
    await execute(
        """
        INSERT OR REPLACE INTO suggestion_status_boards (messageId, guildId, channelId)
        VALUES (?, ?, ?)
        """,
        (int(messageId), int(guildId), int(channelId)),
    )


async def listSuggestionBoards(guildId: int) -> list[dict]:
    return await fetchAll(
        """
        SELECT *
        FROM suggestion_status_boards
        WHERE guildId = ?
        ORDER BY createdAt ASC, messageId ASC
        """,
        (int(guildId),),
    )


async def removeSuggestionBoard(messageId: int) -> None:
    await execute("DELETE FROM suggestion_status_boards WHERE messageId = ?", (int(messageId),))


async def listSuggestionCountsByStatus(guildId: int) -> list[dict]:
    return await fetchAll(
        """
        SELECT status, COUNT(*) AS total
        FROM suggestions
        WHERE guildId = ?
        GROUP BY status
        ORDER BY status ASC
        """,
        (int(guildId),),
    )


async def listSuggestionStatusBoardRows(guildId: int, *, limitPerStatus: int = 5) -> dict[str, list[dict]]:
    rows = await fetchAll(
        """
        SELECT *
        FROM suggestions
        WHERE guildId = ?
        ORDER BY suggestionId DESC
        """,
        (int(guildId),),
    )
    byStatus: dict[str, list[dict]] = {}
    for row in rows:
        status = str(row.get("status") or "PENDING").strip().upper()
        bucket = byStatus.setdefault(status, [])
        if len(bucket) < max(1, int(limitPerStatus or 5)):
            bucket.append(row)
    return byStatus


async def updateSuggestionStatus(
    suggestionId: int,
    *,
    status: str,
    reviewerId: int,
    reviewNote: str | None = None,
) -> None:
    await execute(
        """
        UPDATE suggestions
        SET status = ?,
            reviewerId = ?,
            reviewNote = ?,
            reviewedAt = datetime('now'),
            updatedAt = datetime('now')
        WHERE suggestionId = ?
        """,
        (
            _normalizeText(status).upper(),
            int(reviewerId),
            _normalizeText(reviewNote) or None,
            int(suggestionId),
        ),
    )
