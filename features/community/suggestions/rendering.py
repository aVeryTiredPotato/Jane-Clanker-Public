from __future__ import annotations

from datetime import datetime, timezone

import discord

import config


def _discordTimestamp(value: object, style: str = "f") -> str:
    raw = str(value or "").strip()
    if not raw:
        return "Unknown"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return discord.utils.format_dt(dt, style=style)


def _statusColor(status: str) -> discord.Color:
    normalized = str(status or "").strip().upper()
    if normalized == "APPROVED":
        return discord.Color.green()
    if normalized == "REJECTED":
        return discord.Color.red()
    if normalized == "IMPLEMENTED":
        return discord.Color.blue()
    return discord.Color.blurple()


def buildSuggestionEmbed(row: dict) -> discord.Embed:
    suggestionId = int(row.get("suggestionId") or 0)
    status = str(row.get("status") or "PENDING").strip().upper()
    submitterId = int(row.get("submitterId") or 0)
    reviewerId = int(row.get("reviewerId") or 0)
    anonymous = bool(int(row.get("anonymous") or 0))
    threadId = int(row.get("threadId") or 0)
    freedcampId = int(row.get("freedcampId") or 0)

    embed = discord.Embed(
        title=f"Suggestion #{suggestionId}",
        description=str(row.get("content") or "").strip() or "(empty suggestion)",
        color=_statusColor(status),
    )
    embed.add_field(name="Status", value=status.title(), inline=True)
    embed.add_field(
        name="Submitted By",
        value="Anonymous" if anonymous else (f"<@{submitterId}>" if submitterId > 0 else "Unknown"),
        inline=True,
    )
    embed.add_field(name="Created", value=_discordTimestamp(row.get("createdAt"), "R"), inline=True)
    if reviewerId > 0:
        embed.add_field(name="Reviewed By", value=f"<@{reviewerId}>", inline=True)
    if str(row.get("reviewedAt") or "").strip():
        embed.add_field(name="Reviewed", value=_discordTimestamp(row.get("reviewedAt"), "R"), inline=True)
    reviewNote = str(row.get("reviewNote") or "").strip()
    if reviewNote:
        embed.add_field(name="Review Note", value=reviewNote[:1024], inline=False)
    if threadId > 0:
        embed.add_field(name="Discussion Thread", value=f"<#{threadId}>", inline=False)
    if freedcampId > 0:
        projectId = int(getattr(config, "freedcampProjectId", 0) or 0)
        embed.add_field(
            name="Freedcamp Task",
            value=f"https://freedcamp.com/view/{projectId}/tasks/{freedcampId}",
            inline=False,
        )
    embed.set_footer(text="Community suggestion")
    return embed


def buildSuggestionBoardEmbed(guild: discord.Guild, *, countsByStatus: list[dict], rowsByStatus: dict[str, list[dict]]) -> discord.Embed:
    countLookup = {
        str(row.get("status") or "").strip().upper(): int(row.get("total") or 0)
        for row in countsByStatus
    }
    sections = [
        ("PENDING", "Pending"),
        ("APPROVED", "Approved"),
        ("IMPLEMENTED", "Implemented"),
        ("REJECTED", "Rejected"),
    ]

    embed = discord.Embed(
        title="Suggestion Status Board",
        description=f"Public suggestion snapshot for **{guild.name}**.",
        color=discord.Color.blurple(),
    )
    summary = " | ".join(
        f"{label}: `{countLookup.get(key, 0)}`"
        for key, label in sections
    )
    embed.add_field(name="Counts", value=summary, inline=False)

    for statusKey, statusLabel in sections:
        rows = rowsByStatus.get(statusKey, [])
        if not rows:
            embed.add_field(name=statusLabel, value="(none)", inline=False)
            continue
        lines: list[str] = []
        for row in rows:
            suggestionId = int(row.get("suggestionId") or 0)
            content = str(row.get("content") or "").strip()
            clipped = content if len(content) <= 80 else f"{content[:77]}..."
            lines.append(f"`#{suggestionId}` {clipped}")
        embed.add_field(name=statusLabel, value="\n".join(lines), inline=False)
    return embed
