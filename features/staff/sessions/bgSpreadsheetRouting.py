from __future__ import annotations

from typing import Any

import discord

import config
from features.staff.sessions import bgSpreadsheetQueue
from runtime import interaction as interactionRuntime
from runtime import orgProfiles

_deps: dict[str, Any] = {}


def configure(**deps: Any) -> None:
    _deps.update(deps)


def _dep(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"bgSpreadsheetRouting dependency not configured: {name}")
    return value


def _bgSpreadsheetChannelIds(session: dict[str, Any]) -> list[int]:
    try:
        guildId = int(session.get("guildId") or 0)
    except (TypeError, ValueError):
        guildId = 0
    try:
        channelId = int(
            orgProfiles.getOrganizationValue(
                config,
                "bgCheckChannelId",
                guildId=guildId,
                default=getattr(config, "bgCheckChannelId", 0),
            )
            or 0
        )
    except (TypeError, ValueError):
        channelId = 0
    return [channelId] if channelId > 0 else []


async def routeBgcSpreadsheet(
    bot: discord.Client,
    sessionId: int,
    guild: discord.Guild,
) -> bgSpreadsheetQueue.BgSpreadsheetResult:
    await _dep("ensureBgReviewBuckets")(bot, sessionId, guild)
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))
    if not attendees:
        return bgSpreadsheetQueue.BgSpreadsheetResult(
            skipped_reason="No passing attendees need a BGC spreadsheet."
        )

    session = await _dep("service").getSession(sessionId)
    if not session:
        return bgSpreadsheetQueue.BgSpreadsheetResult(
            skipped_reason="Orientation session could not be found."
        )

    result = await bgSpreadsheetQueue.createSpreadsheetForUserIds(
        [int(attendee["userId"]) for attendee in attendees],
        sourceGuild=guild,
        titlePrefix="Orientation",
        guildId=int(session.get("guildId") or getattr(guild, "id", 0) or 0),
    )
    if not result.url:
        return result

    channelIds = _bgSpreadsheetChannelIds(session)
    result.expected_channel_ids = list(channelIds)
    for channelId in channelIds:
        channel = await _dep("getCachedChannel")(bot, int(channelId))
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            continue
        sentMessage = await interactionRuntime.safeChannelSend(
            channel,
            content=f"Orientation BGC Spreadsheet created: {result.url}",
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if sentMessage is None:
            continue
        result.posted_channel_ids.append(int(channelId))
    return result
