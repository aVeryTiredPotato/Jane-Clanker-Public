from __future__ import annotations

from typing import Any

import discord

from db.sqlite import execute, fetchAll, fetchOne


def normalizeEmojiKey(emoji: object) -> str:
    if isinstance(emoji, str):
        value = str(emoji or "").strip()
        if value.startswith("<:") or value.startswith("<a:"):
            parts = value.split(":")
            if len(parts) >= 3:
                numeric = parts[-1].rstrip(">")
                if numeric.isdigit():
                    return f"custom:{numeric}"
        return value

    emojiId = getattr(emoji, "id", None)
    if emojiId is not None:
        return f"custom:{int(emojiId)}"

    return str(getattr(emoji, "name", "") or "").strip()


async def upsertReactionRoleEntry(
    *,
    guildId: int,
    channelId: int,
    messageId: int,
    emojiKey: str,
    roleId: int,
) -> None:
    await execute(
        """
        INSERT INTO reaction_role_entries
            (guildId, channelId, messageId, emojiKey, roleId)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(messageId, emojiKey) DO UPDATE SET
            guildId = excluded.guildId,
            channelId = excluded.channelId,
            roleId = excluded.roleId
        """,
        (
            int(guildId),
            int(channelId),
            int(messageId),
            str(emojiKey or "").strip(),
            int(roleId),
        ),
    )


async def deleteReactionRoleEntry(*, messageId: int, emojiKey: str) -> None:
    await execute(
        """
        DELETE FROM reaction_role_entries
        WHERE messageId = ? AND emojiKey = ?
        """,
        (int(messageId), str(emojiKey or "").strip()),
    )


async def getReactionRoleEntry(*, messageId: int, emojiKey: str) -> dict[str, Any] | None:
    return await fetchOne(
        """
        SELECT entryId, guildId, channelId, messageId, emojiKey, roleId, createdAt
        FROM reaction_role_entries
        WHERE messageId = ? AND emojiKey = ?
        """,
        (int(messageId), str(emojiKey or "").strip()),
    )


async def listReactionRoleEntries(*, messageId: int) -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT entryId, guildId, channelId, messageId, emojiKey, roleId, createdAt
        FROM reaction_role_entries
        WHERE messageId = ?
        ORDER BY emojiKey ASC
        """,
        (int(messageId),),
    )


def reactionRoleSummaryLine(*, entry: dict[str, Any], guild: discord.Guild | None = None) -> str:
    emojiKey = str(entry.get("emojiKey") or "").strip() or "?"
    roleId = int(entry.get("roleId") or 0)
    role = guild.get_role(roleId) if guild is not None and roleId > 0 else None
    roleLabel = role.mention if role is not None else f"<@&{roleId}>"
    return f"{emojiKey} -> {roleLabel}"


def resolveAssignableRole(
    *,
    guild: discord.Guild,
    roleId: int,
    botMember: discord.Member,
) -> discord.Role | None:
    role = guild.get_role(int(roleId))
    if role is None or role.managed or role.is_default():
        return None
    if role.position >= botMember.top_role.position:
        return None
    return role
