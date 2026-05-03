from __future__ import annotations

from typing import Any, Mapping

import discord


def statusIcon(status: str) -> str:
    normalized = str(status or "").strip().upper()
    if normalized == "APPROVED":
        return ":white_check_mark: Approved"
    if normalized == "REJECTED":
        return ":x: Rejected"
    if normalized == "NEEDS_INFO":
        return ":warning: Needs clarification"
    return ":o: Pending"


def _mentionUser(userId: object) -> str:
    try:
        parsed = int(userId or 0)
    except (TypeError, ValueError):
        parsed = 0
    return f"<@{parsed}>" if parsed > 0 else "`unknown user`"


def _formatPoints(value: object) -> str:
    try:
        numeric = float(value or 0)
    except (TypeError, ValueError):
        return "0"
    if numeric.is_integer():
        return str(int(numeric))
    return str(round(numeric, 3))


def _clip(text: str, limit: int = 1024) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: limit - 3].rstrip() + "..."


def buildPointAwardEmbed(submission: Mapping[str, Any]) -> discord.Embed:
    awardedUserId = (
        submission.get("awardedUserId")
        or submission.get("targetUserId")
        or submission.get("recruitUserId")
        or 0
    )
    reason = str(submission.get("reason") or "").strip() or "_No reason provided._"
    embed = discord.Embed(
        title="Honor Guard Point Award",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Awarder", value=_mentionUser(submission.get("submitterId")), inline=False)
    embed.add_field(name="Awarded User", value=_mentionUser(awardedUserId), inline=False)
    embed.add_field(name="Quota Points", value=_formatPoints(submission.get("quotaPoints")), inline=True)
    embed.add_field(
        name="Awarded Promotion Points",
        value=_formatPoints(submission.get("awardedPoints")),
        inline=True,
    )
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Status", value=statusIcon(str(submission.get("status") or "")), inline=False)
    return embed


def buildSentrySubmissionEmbed(submission: Mapping[str, Any]) -> discord.Embed:
    targetUserId = submission.get("targetUserId") or 0
    dutyDate = str(submission.get("eventDate") or "").strip() or "Unknown"
    minutes = int(submission.get("minutes") or 0)
    imageUrls = submission.get("imageUrls") or []
    evidenceMessageUrl = str(submission.get("evidenceMessageUrl") or "").strip()

    embed = discord.Embed(
        title="Honor Guard Solo Sentry",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Submitter", value=_mentionUser(submission.get("submitterId")), inline=False)
    embed.add_field(name="Member", value=_mentionUser(targetUserId), inline=False)
    embed.add_field(name="Duty Date", value=f"`{dutyDate}`", inline=True)
    embed.add_field(name="Minutes", value=f"`{minutes}`", inline=True)
    embed.add_field(name="Quota Points", value=_formatPoints(submission.get("quotaPoints")), inline=True)
    embed.add_field(
        name="Promotion Event Points",
        value=_formatPoints(submission.get("promotionEventPoints")),
        inline=True,
    )
    if evidenceMessageUrl:
        embed.add_field(name="Evidence Message", value=f"[Open message]({evidenceMessageUrl})", inline=False)
    elif isinstance(imageUrls, list) and imageUrls:
        preview = "\n".join(
            f"[Screenshot {index + 1}]({str(url).strip()})"
            for index, url in enumerate(imageUrls[:6])
            if str(url).strip()
        )
        if preview:
            embed.add_field(name="Evidence", value=_clip(preview), inline=False)
    embed.add_field(name="Status", value=statusIcon(str(submission.get("status") or "")), inline=False)
    return embed
