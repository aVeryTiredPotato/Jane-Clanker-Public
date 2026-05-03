from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import discord

from features.staff.sessions import bgSpreadsheetQueue
from runtime import bgQueueCommand as runtimeBgQueueCommand
from runtime import orgProfiles


async def createBgCheckQueue(
    router: Any,
    *,
    guild: discord.Guild,
    channel: discord.abc.Messageable,
    actor: discord.Member,
    sourceMessage: discord.Message | None = None,
) -> tuple[bool, str]:
    pendingRoleId = orgProfiles.getOrganizationValue(
        router.config,
        "pendingBgRoleId",
        guildId=int(getattr(guild, "id", 0) or 0),
        default=None,
    )
    try:
        pendingRoleIdInt = int(pendingRoleId) if pendingRoleId else 0
    except (TypeError, ValueError):
        pendingRoleIdInt = 0

    sourceGuildId = (
        orgProfiles.getOrganizationValue(
            router.config,
            "bgCheckSourceGuildId",
            guildId=int(getattr(guild, "id", 0) or 0),
            default=None,
        )
        or orgProfiles.getOrganizationValue(
            router.config,
            "primaryGuildId",
            guildId=int(getattr(guild, "id", 0) or 0),
            default=getattr(router.config, "serverId", None),
        )
        or guild.id
    )
    try:
        sourceGuildIdInt = int(sourceGuildId)
    except (TypeError, ValueError):
        sourceGuildIdInt = int(guild.id)

    progress = runtimeBgQueueCommand.BgQueueProgressReporter(
        channel=channel,
        sourceGuildId=sourceGuildIdInt,
        totalSteps=5,
    )
    await progress.start("Resolving the source server and pending BG role...")

    if pendingRoleIdInt <= 0:
        await progress.update(
            stepIndex=1,
            detail="Pending Background Check role is not configured.",
            failed=True,
        )
        return False, "Pending Background Check role is not configured."

    sourceGuild = router.botClient.get_guild(sourceGuildIdInt)
    if sourceGuild is None:
        await progress.update(
            stepIndex=1,
            detail="Source guild is not available to Jane right now.",
            failed=True,
        )
        return False, "Source guild is not available to Jane right now."

    pendingRole = sourceGuild.get_role(pendingRoleIdInt)
    if pendingRole is None:
        await progress.update(
            stepIndex=1,
            detail="Pending Background Check role could not be found in the source server.",
            failed=True,
        )
        return False, "Pending Background Check role could not be found in the source server."

    try:
        pendingMembers = await runtimeBgQueueCommand.collectPendingMembers(
            sourceGuild,
            pendingRole,
            pendingRoleIdInt,
            progress,
        )
        if not pendingMembers:
            await progress.update(
                stepIndex=2,
                detail="No members currently have the Pending Background Check role.",
                pendingCount=0,
                failed=True,
            )
            return False, "No members currently have the Pending Background Check role."

        me = getattr(guild, "me", None)
        sourceChannel = getattr(sourceMessage, "channel", None)
        if (
            sourceMessage is not None
            and me is not None
            and hasattr(sourceChannel, "permissions_for")
            and bool(sourceChannel.permissions_for(me).manage_messages)
        ):
            try:
                await sourceMessage.delete()
            except Exception:
                pass

        spreadsheet = await bgSpreadsheetQueue.createSpreadsheetForUserIds(
            [int(member.id) for member in pendingMembers],
            sourceGuild=sourceGuild,
            titlePrefix="BGC",
            guildId=int(getattr(guild, "id", 0) or 0),
            progress=progress,
        )
        if not spreadsheet.url:
            await progress.update(
                stepIndex=5,
                detail=spreadsheet.skipped_reason or "BGC spreadsheet creation failed.",
                pendingCount=len(pendingMembers),
                failed=True,
            )
            return False, spreadsheet.skipped_reason or "BGC spreadsheet creation failed."

        publicMessage = f"BGC Spreadsheet created: {spreadsheet.url}"
        await channel.send(
            publicMessage,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await progress.update(
            stepIndex=5,
            detail=(
                f"BGC spreadsheet created for `{spreadsheet.row_count}` member(s).\n"
                f"Inventory private: `{spreadsheet.private_count}`\n"
                f"Inventory public: `{spreadsheet.public_count}`\n"
                f"Inventory unknown: `{spreadsheet.unknown_count}`"
            ),
            pendingCount=len(pendingMembers),
            finished=True,
        )
        return True, publicMessage
    except Exception as exc:
        await progress.update(
            stepIndex=5,
            detail=f"Queue creation failed: `{exc.__class__.__name__}`",
            pendingCount=None,
            failed=True,
        )
        raise


async def handleBgCheckCommand(router: Any, message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    token = router.firstLowerToken(message.content or "")
    if token not in {"?bgcheck", "?bg-check"}:
        return False

    if not router.permissions.hasBgCheckCertifiedRole(message.author):
        await message.channel.send("You do not have permission to start background-check queues.")
        return True
    ok, response = await createBgCheckQueue(
        router,
        guild=message.guild,
        channel=message.channel,
        actor=message.author,
        sourceMessage=message,
    )
    if not ok:
        await message.channel.send(response)
    return True


async def handleBgLeaderboardCommand(router: Any, message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    token = router.firstLowerToken(message.content or "")
    if token not in {"?bgleaderboard", "?bg-leaderboard"}:
        return False

    if not router.permissions.hasBgCheckCertifiedRole(message.author):
        await message.channel.send("You do not have permission to view the background-check leaderboard.")
        return True

    rows = await router.sessionService.getBgReviewLeaderboard(limit=15)
    if not rows:
        await message.channel.send("No background-check actions are logged yet.")
        return True

    await router._deleteSourceIfManageable(message)

    lines: list[str] = []
    for idx, row in enumerate(rows, start=1):
        reviewerId = int(row.get("reviewerId") or 0)
        approvals = int(row.get("approvals") or 0)
        rejections = int(row.get("rejections") or 0)
        total = int(row.get("total") or (approvals + rejections))
        if reviewerId <= 0:
            continue
        lines.append(
            f"{idx}. <@{reviewerId}>  |  Approved: `{approvals}`  |  Rejected: `{rejections}`  |  Total: `{total}`"
        )
    if not lines:
        await message.channel.send("No background-check actions are logged yet.")
        return True

    embed = discord.Embed(
        title="Background Check Leaderboard",
        description="\n".join(lines),
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Counts are based on logged approve/reject decisions.")
    await message.channel.send(
        embed=embed,
        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
    )
    return True

