from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import discord

from runtime import interaction as interactionRuntime


@dataclass(slots=True)
class BgQueueProgressReporter:
    channel: discord.abc.Messageable
    sourceGuildId: int
    totalSteps: int = 5
    message: discord.Message | None = None

    def _buildEmbed(
        self,
        *,
        stepIndex: int,
        detail: str,
        pendingCount: int | None = None,
        finished: bool = False,
        failed: bool = False,
    ) -> discord.Embed:
        safeStepIndex = max(1, min(int(stepIndex), max(int(self.totalSteps), 1)))
        percent = int(round((safeStepIndex / max(int(self.totalSteps), 1)) * 100))
        if finished:
            color = discord.Color.green()
            statusText = "Completed"
        elif failed:
            color = discord.Color.red()
            statusText = "Failed"
        else:
            color = discord.Color.blurple()
            statusText = "In Progress"

        embed = discord.Embed(
            title="Background Check Spreadsheet Creation",
            description=str(detail or "Working...").strip(),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Status",
            value=f"`{statusText}` - Step `{safeStepIndex}/{int(self.totalSteps)}` ({percent}%)",
            inline=False,
        )
        embed.add_field(name="Source Server", value=f"`{int(self.sourceGuildId)}`", inline=True)
        embed.add_field(
            name="Pending Members",
            value="`?`" if pendingCount is None else f"`{int(pendingCount)}`",
            inline=True,
        )
        return embed

    async def start(self, detail: str) -> None:
        self.message = await interactionRuntime.safeChannelSend(
            self.channel,
            embed=self._buildEmbed(
                stepIndex=1,
                detail=detail,
            )
        )

    async def update(
        self,
        *,
        stepIndex: int,
        detail: str,
        pendingCount: int | None = None,
        finished: bool = False,
        failed: bool = False,
    ) -> None:
        if self.message is None:
            return
        await interactionRuntime.safeMessageEdit(
            self.message,
            embed=self._buildEmbed(
                stepIndex=stepIndex,
                detail=detail,
                pendingCount=pendingCount,
                finished=finished,
                failed=failed,
            )
        )


async def resolveBgQueueChannel(
    botClient: discord.Client,
    configModule: object,
    fallbackChannel: discord.abc.Messageable,
) -> discord.abc.Messageable | None:
    if isinstance(fallbackChannel, (discord.TextChannel, discord.Thread)):
        return fallbackChannel

    channelId = getattr(configModule, "bgCheckChannelId", None)
    if channelId:
        try:
            channelIdInt = int(channelId)
            channel = botClient.get_channel(channelIdInt) or await interactionRuntime.safeFetchChannel(botClient, channelIdInt)
            if isinstance(channel, (discord.TextChannel, discord.Thread)):
                return channel
        except (TypeError, ValueError, discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    return None


async def collectPendingMembers(
    sourceGuild: discord.Guild,
    pendingRole: discord.Role,
    pendingRoleId: int,
    progress: BgQueueProgressReporter,
) -> list[discord.Member]:
    pendingMembersById: dict[int, discord.Member] = {}
    await progress.update(
        stepIndex=2,
        detail="Collecting pending members from the role cache...",
    )

    pendingRoleMembers = list(pendingRole.members)
    if not pendingRoleMembers:
        await progress.update(
            stepIndex=2,
            detail="Role cache was empty. Chunking the source guild member list...",
        )
        try:
            await sourceGuild.chunk(cache=True)
            pendingRoleMembers = list(pendingRole.members)
        except Exception:
            pendingRoleMembers = []

    if pendingRoleMembers:
        for member in pendingRoleMembers:
            if member.bot:
                continue
            pendingMembersById[int(member.id)] = member
    else:
        await progress.update(
            stepIndex=2,
            detail="Chunking was not enough. Scanning all source-guild members...",
        )
        scannedCount = 0
        lastProgressScanCount = 0
        lastProgressAt = datetime.now(timezone.utc)
        try:
            async for member in sourceGuild.fetch_members(limit=None):
                scannedCount += 1
                if not member.bot and any(int(role.id) == int(pendingRoleId) for role in member.roles):
                    pendingMembersById[int(member.id)] = member

                now = datetime.now(timezone.utc)
                shouldRefresh = (
                    scannedCount - lastProgressScanCount >= 200
                    and (now - lastProgressAt).total_seconds() >= 2.0
                )
                if shouldRefresh:
                    lastProgressScanCount = scannedCount
                    lastProgressAt = now
                    await progress.update(
                        stepIndex=2,
                        detail=(
                            f"Scanning source guild members...\n"
                            f"Scanned: `{scannedCount}`\n"
                            f"Pending found: `{len(pendingMembersById)}`"
                        ),
                        pendingCount=len(pendingMembersById),
                    )
        except (discord.Forbidden, discord.HTTPException):
            pass

    return sorted(
        pendingMembersById.values(),
        key=lambda member: member.display_name.casefold(),
    )
