from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from db.sqlite import execute, executeReturnId, fetchAll, fetchOne


def _normalizeTitle(value: object, *, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _normalizeDescription(value: object, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _normalizeNote(value: object, *, limit: int = 300) -> str:
    return str(value or "").strip()[:limit]


def normalizeEntryType(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"webhook", "message", "jump"}:
        return "WEBHOOK"
    if raw in {"document", "doc", "link", "url"}:
        return "DOCUMENT"
    return ""


def isValidHttpUrl(value: object) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


async def listRenderableHubs() -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT hubId, guildId, channelId, rootMessageId, title, description, createdBy, createdAt, updatedAt
        FROM link_hub_boards
        WHERE rootMessageId > 0
        ORDER BY hubId ASC
        """
    )


async def getHub(hubId: int) -> dict[str, Any] | None:
    return await fetchOne(
        """
        SELECT hubId, guildId, channelId, rootMessageId, title, description, createdBy, createdAt, updatedAt
        FROM link_hub_boards
        WHERE hubId = ?
        """,
        (int(hubId),),
    )


async def getHubByChannel(*, guildId: int, channelId: int) -> dict[str, Any] | None:
    return await fetchOne(
        """
        SELECT hubId, guildId, channelId, rootMessageId, title, description, createdBy, createdAt, updatedAt
        FROM link_hub_boards
        WHERE guildId = ? AND channelId = ?
        """,
        (int(guildId), int(channelId)),
    )


async def createOrUpdateHub(
    *,
    guildId: int,
    channelId: int,
    title: str,
    description: str,
    createdBy: int,
) -> dict[str, Any]:
    normalizedTitle = _normalizeTitle(title)
    normalizedDescription = _normalizeDescription(description, limit=2000)
    existing = await getHubByChannel(guildId=int(guildId), channelId=int(channelId))
    if existing is not None:
        await execute(
            """
            UPDATE link_hub_boards
            SET title = ?, description = ?, updatedAt = datetime('now')
            WHERE hubId = ?
            """,
            (normalizedTitle, normalizedDescription, int(existing["hubId"])),
        )
        refreshed = await getHub(int(existing["hubId"]))
        return refreshed or existing

    hubId = await executeReturnId(
        """
        INSERT INTO link_hub_boards (
            guildId, channelId, title, description, createdBy, createdAt, updatedAt
        )
        VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            int(guildId),
            int(channelId),
            normalizedTitle,
            normalizedDescription,
            int(createdBy or 0),
        ),
    )
    created = await getHub(hubId)
    if created is None:
        raise RuntimeError("Failed to create link hub.")
    return created


async def setHubRootMessageId(*, hubId: int, messageId: int) -> None:
    await execute(
        """
        UPDATE link_hub_boards
        SET rootMessageId = ?, updatedAt = datetime('now')
        WHERE hubId = ?
        """,
        (int(messageId or 0), int(hubId)),
    )


async def listSections(*, hubId: int) -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT sectionId, hubId, title, description, sortOrder, messageId, createdAt, updatedAt
        FROM link_hub_sections
        WHERE hubId = ?
        ORDER BY sortOrder ASC, sectionId ASC
        """,
        (int(hubId),),
    )


async def getSection(*, sectionId: int) -> dict[str, Any] | None:
    return await fetchOne(
        """
        SELECT sectionId, hubId, title, description, sortOrder, messageId, createdAt, updatedAt
        FROM link_hub_sections
        WHERE sectionId = ?
        """,
        (int(sectionId),),
    )


async def getSectionByTitle(*, hubId: int, title: str) -> dict[str, Any] | None:
    return await fetchOne(
        """
        SELECT sectionId, hubId, title, description, sortOrder, messageId, createdAt, updatedAt
        FROM link_hub_sections
        WHERE hubId = ? AND lower(title) = lower(?)
        """,
        (int(hubId), _normalizeTitle(title)),
    )


async def _nextSectionSortOrder(hubId: int) -> int:
    row = await fetchOne(
        "SELECT COALESCE(MAX(sortOrder), 0) AS maxSortOrder FROM link_hub_sections WHERE hubId = ?",
        (int(hubId),),
    )
    return max(1, int((row or {}).get("maxSortOrder") or 0) + 1)


async def _nextEntrySortOrder(sectionId: int) -> int:
    row = await fetchOne(
        "SELECT COALESCE(MAX(sortOrder), 0) AS maxSortOrder FROM link_hub_entries WHERE sectionId = ?",
        (int(sectionId),),
    )
    return max(1, int((row or {}).get("maxSortOrder") or 0) + 1)


async def createSection(
    *,
    hubId: int,
    title: str,
    description: str,
) -> dict[str, Any] | None:
    normalizedTitle = _normalizeTitle(title)
    if not normalizedTitle:
        return None
    if await getSectionByTitle(hubId=int(hubId), title=normalizedTitle) is not None:
        return None
    sectionId = await executeReturnId(
        """
        INSERT INTO link_hub_sections (
            hubId, title, description, sortOrder, createdAt, updatedAt
        )
        VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            int(hubId),
            normalizedTitle,
            _normalizeDescription(description),
            await _nextSectionSortOrder(int(hubId)),
        ),
    )
    await execute(
        "UPDATE link_hub_boards SET updatedAt = datetime('now') WHERE hubId = ?",
        (int(hubId),),
    )
    return await getSection(sectionId)


async def renameSection(
    *,
    hubId: int,
    currentTitle: str,
    newTitle: str,
    newDescription: str,
) -> bool:
    section = await getSectionByTitle(hubId=int(hubId), title=currentTitle)
    normalizedNewTitle = _normalizeTitle(newTitle)
    if section is None or not normalizedNewTitle:
        return False
    existing = await getSectionByTitle(hubId=int(hubId), title=normalizedNewTitle)
    if existing is not None and int(existing["sectionId"]) != int(section["sectionId"]):
        return False
    await execute(
        """
        UPDATE link_hub_sections
        SET title = ?, description = ?, updatedAt = datetime('now')
        WHERE sectionId = ? AND hubId = ?
        """,
        (
            normalizedNewTitle,
            _normalizeDescription(newDescription),
            int(section["sectionId"]),
            int(hubId),
        ),
    )
    await execute(
        "UPDATE link_hub_boards SET updatedAt = datetime('now') WHERE hubId = ?",
        (int(hubId),),
    )
    return True


async def deleteSection(*, hubId: int, sectionId: int) -> None:
    await execute(
        "DELETE FROM link_hub_sections WHERE hubId = ? AND sectionId = ?",
        (int(hubId), int(sectionId)),
    )
    await execute(
        "UPDATE link_hub_boards SET updatedAt = datetime('now') WHERE hubId = ?",
        (int(hubId),),
    )


async def setSectionMessageId(*, sectionId: int, messageId: int) -> None:
    await execute(
        """
        UPDATE link_hub_sections
        SET messageId = ?, updatedAt = datetime('now')
        WHERE sectionId = ?
        """,
        (int(messageId or 0), int(sectionId)),
    )


async def listEntriesForSection(*, sectionId: int) -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT entryId, sectionId, entryType, title, url, note, sortOrder, createdAt, updatedAt
        FROM link_hub_entries
        WHERE sectionId = ?
        ORDER BY sortOrder ASC, entryId ASC
        """,
        (int(sectionId),),
    )


async def getEntryByTitle(*, sectionId: int, title: str) -> dict[str, Any] | None:
    return await fetchOne(
        """
        SELECT entryId, sectionId, entryType, title, url, note, sortOrder, createdAt, updatedAt
        FROM link_hub_entries
        WHERE sectionId = ? AND lower(title) = lower(?)
        """,
        (int(sectionId), _normalizeTitle(title)),
    )


async def createEntry(
    *,
    sectionId: int,
    entryType: str,
    title: str,
    url: str,
    note: str,
) -> dict[str, Any] | None:
    normalizedType = normalizeEntryType(entryType)
    normalizedTitle = _normalizeTitle(title)
    normalizedUrl = str(url or "").strip()[:500]
    if not normalizedType or not normalizedTitle or not isValidHttpUrl(normalizedUrl):
        return None
    if await getEntryByTitle(sectionId=int(sectionId), title=normalizedTitle) is not None:
        return None
    entryId = await executeReturnId(
        """
        INSERT INTO link_hub_entries (
            sectionId, entryType, title, url, note, sortOrder, createdAt, updatedAt
        )
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """,
        (
            int(sectionId),
            normalizedType,
            normalizedTitle,
            normalizedUrl,
            _normalizeNote(note),
            await _nextEntrySortOrder(int(sectionId)),
        ),
    )
    await execute(
        """
        UPDATE link_hub_sections
        SET updatedAt = datetime('now')
        WHERE sectionId = ?
        """,
        (int(sectionId),),
    )
    row = await fetchOne(
        """
        SELECT hubId
        FROM link_hub_sections
        WHERE sectionId = ?
        """,
        (int(sectionId),),
    )
    if row is not None:
        await execute(
            "UPDATE link_hub_boards SET updatedAt = datetime('now') WHERE hubId = ?",
            (int(row["hubId"]),),
        )
    return await fetchOne(
        """
        SELECT entryId, sectionId, entryType, title, url, note, sortOrder, createdAt, updatedAt
        FROM link_hub_entries
        WHERE entryId = ?
        """,
        (int(entryId),),
    )


async def deleteEntry(*, sectionId: int, entryId: int) -> None:
    await execute(
        "DELETE FROM link_hub_entries WHERE sectionId = ? AND entryId = ?",
        (int(sectionId), int(entryId)),
    )
    await execute(
        "UPDATE link_hub_sections SET updatedAt = datetime('now') WHERE sectionId = ?",
        (int(sectionId),),
    )
    row = await fetchOne(
        "SELECT hubId FROM link_hub_sections WHERE sectionId = ?",
        (int(sectionId),),
    )
    if row is not None:
        await execute(
            "UPDATE link_hub_boards SET updatedAt = datetime('now') WHERE hubId = ?",
            (int(row["hubId"]),),
        )


async def buildHubSnapshot(*, hubId: int) -> dict[str, Any] | None:
    hub = await getHub(int(hubId))
    if hub is None:
        return None
    sections = await listSections(hubId=int(hubId))
    enrichedSections: list[dict[str, Any]] = []
    for section in sections:
        copiedSection = dict(section)
        copiedSection["entries"] = await listEntriesForSection(sectionId=int(section["sectionId"]))
        enrichedSections.append(copiedSection)
    return {
        "hub": hub,
        "sections": enrichedSections,
    }
