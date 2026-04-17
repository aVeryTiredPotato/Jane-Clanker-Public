from __future__ import annotations

from typing import Optional

from db.sqlite import execute, fetchAll, fetchOne


def _normalizeOrgKey(value: object) -> str:
    return str(value or "").strip().upper()


async def _deleteDuplicateOrgRows(*, orgKey: str, userId: int, keepGuildId: int) -> None:
    normalizedOrgKey = _normalizeOrgKey(orgKey)
    if not normalizedOrgKey:
        return
    await execute(
        """
        DELETE FROM curfew_targets
        WHERE orgKey = ?
          AND userId = ?
          AND guildId <> ?
        """,
        (normalizedOrgKey, int(userId), int(keepGuildId)),
    )


async def upsertCurfewTarget(
    *,
    guildId: int,
    userId: int,
    timezoneName: str,
    addedBy: int,
    orgKey: str = "",
) -> None:
    normalizedOrgKey = _normalizeOrgKey(orgKey)
    if normalizedOrgKey:
        existing = await fetchOne(
            """
            SELECT guildId
            FROM curfew_targets
            WHERE orgKey = ? AND userId = ?
            ORDER BY enabled DESC, updatedAt DESC
            LIMIT 1
            """,
            (normalizedOrgKey, int(userId)),
        )
        if existing is not None:
            existingGuildId = int(existing.get("guildId") or guildId)
            await execute(
                """
                UPDATE curfew_targets
                SET timezone = ?,
                    enabled = 1,
                    addedBy = ?,
                    updatedAt = datetime('now')
                WHERE orgKey = ? AND userId = ?
                  AND guildId = ?
                """,
                (
                    str(timezoneName or "").strip(),
                    int(addedBy),
                    normalizedOrgKey,
                    int(userId),
                    existingGuildId,
                ),
            )
            await _deleteDuplicateOrgRows(
                orgKey=normalizedOrgKey,
                userId=int(userId),
                keepGuildId=existingGuildId,
            )
            return

    await execute(
        """
        INSERT INTO curfew_targets (orgKey, guildId, userId, timezone, enabled, addedBy, createdAt, updatedAt)
        VALUES (?, ?, ?, ?, 1, ?, datetime('now'), datetime('now'))
        ON CONFLICT(guildId, userId)
        DO UPDATE SET
            orgKey = excluded.orgKey,
            timezone = excluded.timezone,
            enabled = 1,
            addedBy = excluded.addedBy,
            updatedAt = datetime('now')
        """,
        (
            normalizedOrgKey,
            int(guildId),
            int(userId),
            str(timezoneName or "").strip(),
            int(addedBy),
        ),
    )
    if normalizedOrgKey:
        await _deleteDuplicateOrgRows(
            orgKey=normalizedOrgKey,
            userId=int(userId),
            keepGuildId=int(guildId),
        )


async def disableCurfewTarget(*, guildId: int, userId: int, orgKey: str = "") -> None:
    normalizedOrgKey = _normalizeOrgKey(orgKey)
    if normalizedOrgKey:
        await execute(
            """
            UPDATE curfew_targets
            SET enabled = 0, updatedAt = datetime('now')
            WHERE orgKey = ? AND userId = ?
            """,
            (normalizedOrgKey, int(userId)),
        )
        return
    await execute(
        """
        UPDATE curfew_targets
        SET enabled = 0, updatedAt = datetime('now')
        WHERE guildId = ? AND userId = ?
        """,
        (int(guildId), int(userId)),
    )


async def getCurfewTarget(*, guildId: int, userId: int) -> Optional[dict]:
    return await fetchOne(
        """
        SELECT *
        FROM curfew_targets
        WHERE guildId = ? AND userId = ?
        """,
        (int(guildId), int(userId)),
    )


async def migrateGuildCurfewTargetsToOrg(*, guildId: int, orgKey: str) -> None:
    normalizedOrgKey = _normalizeOrgKey(orgKey)
    if not normalizedOrgKey:
        return
    await execute(
        """
        UPDATE curfew_targets
        SET orgKey = ?, updatedAt = datetime('now')
        WHERE guildId = ?
          AND (orgKey IS NULL OR orgKey = '')
        """,
        (normalizedOrgKey, int(guildId)),
    )


async def listGuildCurfewTargets(
    *,
    guildId: int,
    includeDisabled: bool = False,
    orgKey: str = "",
) -> list[dict]:
    normalizedOrgKey = _normalizeOrgKey(orgKey)
    if normalizedOrgKey:
        query = """
            SELECT *
            FROM curfew_targets
            WHERE orgKey = ?
        """
        params: tuple[object, ...] = (normalizedOrgKey,)
    else:
        query = """
            SELECT *
            FROM curfew_targets
            WHERE guildId = ?
        """
        params = (int(guildId),)
    if not includeDisabled:
        query += " AND enabled = 1"
    query += " ORDER BY enabled DESC, userId ASC"
    return await fetchAll(query, params)


async def listActiveCurfewTargets() -> list[dict]:
    return await fetchAll(
        """
        SELECT *
        FROM curfew_targets
        WHERE enabled = 1
        ORDER BY orgKey ASC, guildId ASC, userId ASC
        """,
    )


async def setCurfewAppliedAt(
    *,
    guildId: int,
    userId: int,
    appliedAtIso: str,
    orgKey: str = "",
) -> None:
    normalizedOrgKey = _normalizeOrgKey(orgKey)
    if normalizedOrgKey:
        await execute(
            """
            UPDATE curfew_targets
            SET lastAppliedAt = ?, updatedAt = datetime('now')
            WHERE orgKey = ? AND userId = ?
            """,
            (
                str(appliedAtIso or "").strip(),
                normalizedOrgKey,
                int(userId),
            ),
        )
        return
    await execute(
        """
        UPDATE curfew_targets
        SET lastAppliedAt = ?, updatedAt = datetime('now')
        WHERE guildId = ? AND userId = ?
        """,
        (
            str(appliedAtIso or "").strip(),
            int(guildId),
            int(userId),
        ),
    )
