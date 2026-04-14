from __future__ import annotations

from collections import defaultdict
from typing import Any

import discord

from db.sqlite import execute, executeReturnId, fetchAll, fetchOne
from runtime import interaction as interactionRuntime


def parseButtonEmoji(rawValue: str | None) -> discord.PartialEmoji | str | None:
    value = str(rawValue or "").strip()
    if not value:
        return None
    # Accept both built-in emoji and Discord custom emoji strings like <:name:id>.
    parsed = discord.PartialEmoji.from_str(value)
    if parsed.id is not None or parsed.name:
        return parsed
    return value


async def addBlockedSelfRole(*, guildId: int, roleId: int) -> None:
    await execute(
        """
        INSERT OR REPLACE INTO blocked_self_roles
            (guildId, roleId)
        VALUES (?, ?)
        """,
        (int(guildId), int(roleId)),
    )


async def removeBlockedSelfRole(*, guildId: int, roleId: int) -> None:
    await execute(
        """
        DELETE FROM blocked_self_roles
        WHERE guildId = ? AND roleId = ?
        """,
        (int(guildId), int(roleId)),
    )


async def isBlockedSelfRole(*, guildId: int, roleId: int) -> bool:
    row = await fetchOne(
        """
        SELECT roleId
        FROM blocked_self_roles
        WHERE guildId = ? AND roleId = ?
        """,
        (int(guildId), int(roleId)),
    )
    return row is not None


async def listBlockedSelfRoles(*, guildId: int) -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT guildId, roleId, createdAt
        FROM blocked_self_roles
        WHERE guildId = ?
        ORDER BY roleId ASC
        """,
        (int(guildId),),
    )


async def createButtonRoleEntry(
    *,
    guildId: int,
    channelId: int,
    messageId: int,
    roleId: int,
    buttonLabel: str,
    emojiSpec: str,
    orderIndex: int,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO button_role_entries
            (guildId, channelId, messageId, roleId, buttonLabel, emojiSpec, orderIndex)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(guildId),
            int(channelId),
            int(messageId),
            int(roleId),
            str(buttonLabel or "").strip(),
            str(emojiSpec or "").strip(),
            int(orderIndex),
        ),
    )


async def listButtonRoleEntries(*, messageId: int) -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT entryId, guildId, channelId, messageId, roleId, buttonLabel, emojiSpec, orderIndex, createdAt
        FROM button_role_entries
        WHERE messageId = ?
        ORDER BY orderIndex ASC, entryId ASC
        """,
        (int(messageId),),
    )


async def listAllButtonRoleEntries() -> list[dict[str, Any]]:
    return await fetchAll(
        """
        SELECT entryId, guildId, channelId, messageId, roleId, buttonLabel, emojiSpec, orderIndex, createdAt
        FROM button_role_entries
        ORDER BY messageId ASC, orderIndex ASC, entryId ASC
        """
    )


async def deleteButtonRoleEntry(*, messageId: int, roleId: int) -> None:
    await execute(
        """
        DELETE FROM button_role_entries
        WHERE messageId = ? AND roleId = ?
        """,
        (int(messageId), int(roleId)),
    )


def groupButtonRoleEntriesByMessage(
    entries: list[dict[str, Any]],
) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in entries:
        messageId = int(entry.get("messageId") or 0)
        if messageId > 0:
            grouped[messageId].append(entry)
    return dict(grouped)


class ButtonRoleActionButton(discord.ui.Button):
    def __init__(self, *, entry: dict[str, Any]):
        self.entry = entry
        roleId = int(entry.get("roleId") or 0)
        label = str(entry.get("buttonLabel") or "").strip()[:80] or f"Role {roleId}"
        emoji = parseButtonEmoji(str(entry.get("emojiSpec") or ""))
        orderIndex = max(0, int(entry.get("orderIndex") or 0))
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label=label,
            emoji=emoji,
            custom_id=f"button-role:{int(entry.get('entryId') or 0)}",
            row=min(4, orderIndex // 5),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="This button can only be used inside a server.",
                ephemeral=True,
            )
        roleId = int(self.entry.get("roleId") or 0)
        role = interaction.guild.get_role(roleId)
        me = interaction.guild.me or interaction.guild.get_member(int(getattr(interaction.client.user, "id", 0) or 0))
        # Re-check safety here so older messages still respect newer block rules.
        if (
            role is None
            or me is None
            or role.managed
            or role.is_default()
            or role.position >= me.top_role.position
            or await isBlockedSelfRole(guildId=int(interaction.guild.id), roleId=roleId)
        ):
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="I cannot manage that role right now.",
                ephemeral=True,
            )
        try:
            if role in interaction.user.roles:
                await interaction.user.remove_roles(role, reason="Button role opt-out.")
                return await interactionRuntime.safeInteractionReply(
                    interaction,
                    content=f"Removed {role.mention}.",
                    ephemeral=True,
                )
            await interaction.user.add_roles(role, reason="Button role opt-in.")
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"Added {role.mention}.",
                ephemeral=True,
            )
        except (discord.Forbidden, discord.HTTPException):
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="I could not update your role.",
                ephemeral=True,
            )


class ButtonRoleView(discord.ui.View):
    def __init__(self, *, entries: list[dict[str, Any]]):
        super().__init__(timeout=None)
        # Discord limits one message to 25 components, so cap the restored view.
        for entry in entries[:25]:
            self.add_item(ButtonRoleActionButton(entry=entry))
