from __future__ import annotations

from typing import Any, Optional

import discord

from runtime import interaction as interactionRuntime
from runtime import taskBudgeter

_deps: dict[str, Any] = {}


def configure(**deps: Any) -> None:
    _deps.update(deps)


def _dep(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"sessionNotifications dependency not configured: {name}")
    return value


async def postBgFinalSummary(bot: discord.Client, sessionId: int) -> None:
    await _dep("postBgFinalSummaryFn")(bot, sessionId)


async def maybeNotifyBgComplete(interaction: discord.Interaction, sessionId: int) -> None:
    attendees = _dep("bgCandidates")(await _dep("service").getAttendees(sessionId))
    if not attendees:
        return
    if any(a["bgStatus"] == "PENDING" for a in attendees):
        return
    await postBgFinalSummary(interaction.client, sessionId)
    await _dep("safeInteractionReply")(interaction, "All attendees processed.", ephemeral=False)


async def dmUser(bot: discord.Client, userId: int, message: str) -> bool:
    try:
        user = await _dep("getCachedUser")(bot, userId)
        if user is None:
            return False
        await taskBudgeter.runDiscord(lambda: user.send(message))
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        return False


async def notifyMods(bot: discord.Client, message: str) -> None:
    channelId = int(getattr(_dep("configModule"), "bgCheckChannelId", 0) or 0)
    if channelId <= 0:
        return
    channel = await _dep("getCachedChannel")(bot, channelId)
    if channel:
        await interactionRuntime.safeChannelSend(channel, content=message)


async def postBgFailureForumEntry(
    bot: discord.Client,
    guild: Optional[discord.Guild],
    targetUserId: int,
    reviewerId: int,
) -> None:
    forumChannelId = int(getattr(_dep("configModule"), "bgFailureForumChannelId", 0) or 0)
    if forumChannelId <= 0:
        return

    channel = await _dep("getCachedChannel")(bot, forumChannelId)
    if not isinstance(channel, discord.ForumChannel):
        return

    failedUsername: Optional[str] = None
    if guild is not None:
        member = guild.get_member(int(targetUserId))
        if member is not None:
            failedUsername = str(member.nick or member.display_name or member.name).strip()
    if not failedUsername:
        user = await _dep("getCachedUser")(bot, int(targetUserId))
        if user is not None:
            failedUsername = user.name

    postTitle = _dep("normalizeForumPostTitle")(
        failedUsername or "",
        fallback=f"user-{int(targetUserId)}",
    )
    try:
        await taskBudgeter.runDiscord(
            lambda: channel.create_thread(
                name=postTitle,
                content=f"<@{int(reviewerId)}>",
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )
        )
    except (discord.Forbidden, discord.HTTPException):
        return
