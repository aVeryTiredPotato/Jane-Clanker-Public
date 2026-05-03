from __future__ import annotations

from typing import Any, Callable, Optional

import discord

from features.staff.applications import service as applicationsService
from runtime import interaction as interactionRuntime
from runtime import taskBudgeter


async def resolveHubMessage(
    *,
    botClient: discord.Client,
    guild: discord.Guild,
    hubRow: dict[str, Any],
    deleteMissing: bool = False,
) -> Optional[discord.Message]:
    channelId = int(hubRow.get("channelId") or 0)
    messageId = int(hubRow.get("messageId") or 0)
    if channelId <= 0 or messageId <= 0:
        return None

    channel = guild.get_channel(channelId)
    if channel is None:
        channel = await interactionRuntime.safeFetchChannel(botClient, channelId)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return None

    try:
        return await taskBudgeter.runDiscord(lambda: channel.fetch_message(messageId))
    except discord.NotFound:
        if deleteMissing:
            try:
                await applicationsService.deleteHubMessage(messageId)
            except Exception:
                pass
        return None
    except (discord.Forbidden, discord.HTTPException):
        return None


async def refreshHubEmbedsForDivision(
    *,
    cog: Any,
    guild: discord.Guild,
    divisionKey: str,
    buildView: Callable[[str, bool], discord.ui.View],
    buildEmbed: Callable[[dict[str, Any]], discord.Embed],
) -> int:
    rows = await applicationsService.listHubMessagesForDivision(guild.id, divisionKey)
    if not rows:
        return 0
    division = cog.getDivision(divisionKey)
    if not division:
        return 0
    isOpen = await applicationsService.isDivisionOpen(guild.id, divisionKey)
    updated = 0
    for row in rows:
        message = await resolveHubMessage(
            botClient=cog.bot,
            guild=guild,
            hubRow=row,
            deleteMissing=True,
        )
        if message is None:
            continue
        view = buildView(divisionKey, isOpen)
        embed = buildEmbed(division)
        if not await interactionRuntime.safeMessageEdit(message, embed=embed, view=view):
            continue
        cog.bot.add_view(view, message_id=message.id)
        updated += 1
    return updated


async def refreshHubViewsForDivision(
    *,
    cog: Any,
    guild: discord.Guild,
    divisionKey: str,
    buildView: Callable[[str, bool], discord.ui.View],
) -> int:
    rows = await applicationsService.listHubMessagesForDivision(guild.id, divisionKey)
    if not rows:
        return 0
    isOpen = await applicationsService.isDivisionOpen(guild.id, divisionKey)
    updated = 0
    for row in rows:
        message = await resolveHubMessage(
            botClient=cog.bot,
            guild=guild,
            hubRow=row,
            deleteMissing=True,
        )
        if message is None:
            continue
        view = buildView(divisionKey, isOpen)
        if not await interactionRuntime.safeMessageEdit(message, view=view):
            continue
        cog.bot.add_view(view, message_id=message.id)
        updated += 1
    return updated


__all__ = [
    "refreshHubEmbedsForDivision",
    "refreshHubViewsForDivision",
    "resolveHubMessage",
]

