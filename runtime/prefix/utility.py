from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import discord

from db.sqlite import fetchOne
from features.staff.sessions.Roblox import robloxUsers


_pairDbNamesActive = False


@dataclass
class _PairDbNamesLookupResult:
    discordUserId: int
    robloxUsername: str = ""
    error: str = ""
    source: str = "rover"

async def handleChannelPurge(router: Any, message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    token = router.firstLowerToken(message.content or "")
    if token != "?cpurgejane":
        return False

    canRun = router._headDeveloperAllowed(message.author.id)

    if not canRun:
        return False

    await router._deleteSourceIfManageable(message)

    channelId = router.indexToken(message.content or "", 1)
    channel = router.botClient.get_channel(channelId)
    if channel is not None:
        try:
            await channel.delete()
        except Exception:
            return False

    return True


async def handleUsernameToUserId(router: Any, message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    token = router.firstLowerToken(message.content or "")
    if token != "?ruid":
        return False

    await router._deleteSourceIfManageable(message)

    robloxAPIEndpoint = "https://users.roblox.com/v1/usernames/users"
    robloxUserName = router.indexToken(message.content or "", 1)
    if not robloxUserName:
        await message.channel.send(content="Usage: `?ruid roblox_username`")
        return True

    payload = {
        "usernames": [robloxUserName],
        "excludeBannedUsers": True,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url=robloxAPIEndpoint, json=payload) as resp:
            if resp.status != 200:
                await message.channel.send(content="There was an error with that username.")
                return False
            data = await resp.json()
            if not data.get("data"):
                await message.channel.send(content="I could not find that Roblox username.")
                return True
            userId = data["data"][0]["id"]
            await message.channel.send(content=f"{userId}")

    return True


def _pairDbNamesConfig(router: Any) -> tuple[int, int, int, int]:
    try:
        channelId = int(getattr(router.config, "pairDbNamesSourceChannelId", 1481170755327885465) or 0)
    except (TypeError, ValueError):
        channelId = 0
    try:
        lookbackDays = int(getattr(router.config, "pairDbNamesLookbackDays", 5) or 5)
    except (TypeError, ValueError):
        lookbackDays = 5
    try:
        concurrency = int(getattr(router.config, "pairDbNamesLookupConcurrency", 4) or 4)
    except (TypeError, ValueError):
        concurrency = 4
    try:
        maxLookups = int(getattr(router.config, "pairDbNamesMaxLookups", 500) or 500)
    except (TypeError, ValueError):
        maxLookups = 500
    return (
        channelId,
        max(1, min(lookbackDays, 30)),
        max(1, min(concurrency, 12)),
        max(1, min(maxLookups, 2000)),
    )


def _pairDbNamesHistoryConfig(router: Any) -> tuple[int, int, float, float]:
    try:
        pageSize = int(getattr(router.config, "pairDbNamesHistoryPageSize", 100) or 100)
    except (TypeError, ValueError):
        pageSize = 100
    try:
        maxAttempts = int(getattr(router.config, "pairDbNamesHistoryMaxAttempts", 5) or 5)
    except (TypeError, ValueError):
        maxAttempts = 5
    try:
        baseDelaySec = float(getattr(router.config, "pairDbNamesHistoryRetryBaseSec", 2) or 2)
    except (TypeError, ValueError):
        baseDelaySec = 2.0
    try:
        maxDelaySec = float(getattr(router.config, "pairDbNamesHistoryRetryMaxDelaySec", 20) or 20)
    except (TypeError, ValueError):
        maxDelaySec = 20.0
    return (
        max(1, min(pageSize, 100)),
        max(1, min(maxAttempts, 10)),
        max(0.0, baseDelaySec),
        max(0.0, maxDelaySec),
    )


def _isRetryableDiscordRestError(exc: BaseException) -> bool:
    if isinstance(exc, discord.DiscordServerError):
        return True
    if isinstance(exc, discord.HTTPException):
        try:
            status = int(getattr(exc, "status", 0) or 0)
        except (TypeError, ValueError):
            status = 0
        return status in {500, 502, 503, 504}
    if isinstance(exc, (ConnectionError, OSError, TimeoutError)):
        return True
    return exc.__class__.__module__.startswith("aiohttp.")


async def _fetchPairDbNamesChannel(router: Any, channelId: int) -> discord.abc.Messageable | None:
    channel = router.botClient.get_channel(channelId)
    if channel is not None:
        return channel
    try:
        return await router.botClient.fetch_channel(channelId)
    except Exception:
        return None


async def _knownRobloxIdentity(discordUserId: int) -> bool:
    row = await fetchOne(
        """
        SELECT robloxUsername
        FROM roblox_identity_links
        WHERE discordUserId = ?
          AND trim(robloxUsername) <> ''
        """,
        (int(discordUserId),),
    )
    return bool(row and str(row.get("robloxUsername") or "").strip())


async def _pairDbNamesLookup(discordUserId: int, guildId: int) -> _PairDbNamesLookupResult:
    robloxUsers.clearRobloxIdentityCache(int(discordUserId))
    result = await robloxUsers.fetchRobloxUser(
        int(discordUserId),
        guildId=int(guildId) if int(guildId or 0) > 0 else None,
    )
    username = str(getattr(result, "robloxUsername", "") or "").strip()
    error = str(getattr(result, "error", "") or "").strip()
    if username:
        source = "local" if error.startswith("RoVer lookup unavailable; using") else "rover"
        return _PairDbNamesLookupResult(int(discordUserId), username, error, source)
    return _PairDbNamesLookupResult(int(discordUserId), "", error or "No Roblox account returned.", "failed")


def _pairDbNamesSummaryLine(
    *,
    scannedMessages: int,
    uniqueUsers: int,
    alreadyKnown: int,
    requestedLookups: int,
    roverPaired: int,
    localPaired: int,
    failed: int,
    truncated: int,
) -> str:
    parts = [
        f"messages={scannedMessages}",
        f"uniqueUsers={uniqueUsers}",
        f"alreadyKnown={alreadyKnown}",
        f"lookups={requestedLookups}",
        f"roverPaired={roverPaired}",
        f"localPaired={localPaired}",
        f"failed={failed}",
    ]
    if truncated > 0:
        parts.append(f"truncated={truncated}")
    return " ".join(parts)


async def _scanPairDbNamesHistory(
    router: Any,
    sourceChannel: Any,
    *,
    cutoff: datetime,
    channelId: int,
    statusMessage: discord.Message,
) -> tuple[int, set[int]]:
    pageSize, maxAttempts, baseDelaySec, maxDelaySec = _pairDbNamesHistoryConfig(router)
    before: discord.Message | None = None
    scannedMessages = 0
    userIds: set[int] = set()

    while True:
        page: list[discord.Message] = []
        attempt = 1
        while True:
            try:
                historyArgs: dict[str, Any] = {
                    "after": cutoff,
                    "limit": pageSize,
                    "oldest_first": False,
                }
                if before is not None:
                    historyArgs["before"] = before
                async for historyMessage in sourceChannel.history(**historyArgs):
                    page.append(historyMessage)
                break
            except discord.Forbidden:
                raise
            except Exception as exc:
                if not _isRetryableDiscordRestError(exc) or attempt >= maxAttempts:
                    raise
                delaySec = min(maxDelaySec, baseDelaySec * attempt)
                try:
                    await statusMessage.edit(
                        content=(
                            f"`!pairDbNames` Discord history page failed with `{exc.__class__.__name__}`; "
                            f"retrying in {delaySec:.1f}s ({attempt}/{maxAttempts})."
                        )
                    )
                except Exception:
                    pass
                await asyncio.sleep(delaySec)
                attempt += 1

        if not page:
            break

        for historyMessage in page:
            scannedMessages += 1
            author = getattr(historyMessage, "author", None)
            if author is None or bool(getattr(author, "bot", False)):
                continue
            try:
                authorId = int(author.id)
            except (TypeError, ValueError):
                continue
            if authorId > 0:
                userIds.add(authorId)

        if scannedMessages % 500 < pageSize:
            try:
                await statusMessage.edit(
                    content=(
                        f"`!pairDbNames` scanning <#{channelId}>..."
                        f" messages={scannedMessages} uniqueUsers={len(userIds)}"
                    )
                )
            except Exception:
                pass

        before = page[-1]
        if len(page) < pageSize:
            break

    return scannedMessages, userIds


async def handlePairDbNamesCommand(router: Any, message: discord.Message) -> bool:
    global _pairDbNamesActive
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    token = router.firstLowerToken(message.content or "")
    if token != "!pairdbnames":
        return False

    if not router._headDeveloperAllowed(int(message.author.id)):
        await message.channel.send("You do not have permission to use this temporary command.")
        return True
    if _pairDbNamesActive:
        await message.channel.send("`!pairDbNames` is already running.")
        return True

    _pairDbNamesActive = True
    try:
        await router._deleteSourceIfManageable(message)
        channelId, lookbackDays, concurrency, maxLookups = _pairDbNamesConfig(router)
        sourceChannel = await _fetchPairDbNamesChannel(router, channelId)
        if sourceChannel is None or not hasattr(sourceChannel, "history"):
            await message.channel.send(f"I could not access source channel `{channelId}`.")
            return True

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookbackDays)
        statusMessage = await message.channel.send(
            f"`!pairDbNames` scanning <#{channelId}> since {discord.utils.format_dt(cutoff, 'R')}..."
        )

        try:
            scannedMessages, userIds = await _scanPairDbNamesHistory(
                router,
                sourceChannel,
                cutoff=cutoff,
                channelId=channelId,
                statusMessage=statusMessage,
            )
        except discord.Forbidden:
            await statusMessage.edit(content=f"I do not have permission to read history in <#{channelId}>.")
            return True
        except Exception as exc:
            await statusMessage.edit(content=f"History scan failed: `{exc.__class__.__name__}: {exc}`")
            return True

        alreadyKnown = 0
        unknownUserIds: list[int] = []
        for userId in sorted(userIds):
            if await _knownRobloxIdentity(userId):
                alreadyKnown += 1
            else:
                unknownUserIds.append(userId)

        truncated = max(0, len(unknownUserIds) - maxLookups)
        lookupUserIds = unknownUserIds[:maxLookups]
        guildId = int(getattr(router.config, "serverId", 0) or getattr(message.guild, "id", 0) or 0)
        await statusMessage.edit(
            content=(
                f"`!pairDbNames` lookup phase: uniqueUsers={len(userIds)} "
                f"alreadyKnown={alreadyKnown} lookups={len(lookupUserIds)}"
                + (f" truncated={truncated}" if truncated else "")
            )
        )

        semaphore = asyncio.Semaphore(concurrency)
        completed = 0
        roverPaired = 0
        localPaired = 0
        failed = 0
        samplePairs: list[str] = []
        sampleFailures: list[str] = []

        async def _boundedLookup(userId: int) -> _PairDbNamesLookupResult:
            async with semaphore:
                return await _pairDbNamesLookup(userId, guildId)

        tasks = [asyncio.create_task(_boundedLookup(userId)) for userId in lookupUserIds]
        for task in asyncio.as_completed(tasks):
            try:
                result = await task
            except Exception as exc:
                completed += 1
                failed += 1
                if len(sampleFailures) < 5:
                    sampleFailures.append(f"lookup exception: {exc.__class__.__name__}")
            else:
                completed += 1
                if result.robloxUsername:
                    if result.source == "local":
                        localPaired += 1
                    else:
                        roverPaired += 1
                    if len(samplePairs) < 8:
                        samplePairs.append(f"<@{result.discordUserId}> -> `{result.robloxUsername}`")
                else:
                    failed += 1
                    if len(sampleFailures) < 5:
                        sampleFailures.append(f"<@{result.discordUserId}>: {result.error[:120]}")

            if completed == len(lookupUserIds) or completed % 25 == 0:
                try:
                    await statusMessage.edit(
                        content=(
                            "`!pairDbNames` running: "
                            + _pairDbNamesSummaryLine(
                                scannedMessages=scannedMessages,
                                uniqueUsers=len(userIds),
                                alreadyKnown=alreadyKnown,
                                requestedLookups=len(lookupUserIds),
                                roverPaired=roverPaired,
                                localPaired=localPaired,
                                failed=failed,
                                truncated=truncated,
                            )
                        )
                    )
                except Exception:
                    pass

        summary = _pairDbNamesSummaryLine(
            scannedMessages=scannedMessages,
            uniqueUsers=len(userIds),
            alreadyKnown=alreadyKnown,
            requestedLookups=len(lookupUserIds),
            roverPaired=roverPaired,
            localPaired=localPaired,
            failed=failed,
            truncated=truncated,
        )
        details: list[str] = [f"`!pairDbNames` complete: {summary}"]
        if samplePairs:
            details.append("Samples: " + ", ".join(samplePairs))
        if sampleFailures:
            details.append("Failures: " + " | ".join(sampleFailures))
        await statusMessage.edit(content="\n".join(details)[:1900])
        return True
    finally:
        _pairDbNamesActive = False


def _permissionSimulatorGuildAllowed(router: Any, guildId: int) -> bool:
    configured = getattr(router.config, "permissionSimulatorGuildIds", None) or [getattr(router.config, "serverId", 0)]
    allowedIds: set[int] = set()
    for raw in configured:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            allowedIds.add(parsed)
    if not allowedIds:
        return False
    return int(guildId) in allowedIds


def _likelyCommandAccess(router: Any, member: discord.Member, commandPath: str) -> str:
    path = str(commandPath or "").strip().lower()
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return "Likely allowed (admin/manage-server bypass)."
    if path.startswith("/orientation"):
        roleId = int(getattr(router.config, "instructorRoleId", 0) or 0)
        hasRole = any(int(role.id) == roleId for role in member.roles) if roleId > 0 else False
        return "Likely allowed." if hasRole else "Likely denied (missing instructor role)."
    if path.startswith("/recruitment"):
        roleId = int(getattr(router.config, "recruiterRoleId", 0) or 0)
        hasRole = any(int(role.id) == roleId for role in member.roles) if roleId > 0 else False
        return "Likely allowed." if hasRole else "Likely denied (missing recruiter role)."
    if path.startswith("/bg-flag"):
        roleIds = router.permissions.getBgCheckCertifiedRoleIds()
        hasRole = any(int(role.id) in roleIds for role in member.roles)
        return "Likely allowed." if hasRole else "Likely denied (missing BG-certified role)."
    if path.startswith("/schedule-event"):
        mr = int(getattr(router.config, "middleRankRoleId", 0) or 0)
        hr = int(getattr(router.config, "highRankRoleId", 0) or 0)
        hasRole = any(int(role.id) in {mr, hr} for role in member.roles if int(role.id) > 0)
        return "Likely allowed." if hasRole else "Likely denied (missing MR/HR role)."
    if path.startswith("/archive") or path.startswith("/best-of") or path.startswith("/jail") or path.startswith("/unjail"):
        return "Likely denied (admin/manage-server required)."
    return "Permission depends on command-specific checks."


async def handlePermissionSimulatorCommand(router: Any, message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False
    if not message.guild or not isinstance(message.author, discord.Member):
        return False

    token = router.firstLowerToken(message.content or "")
    if token not in {"?perm-sim", "?permsim"}:
        return False

    if not _permissionSimulatorGuildAllowed(router, int(message.guild.id)):
        await message.channel.send("Permission simulator is only enabled in the test server.")
        return True
    if not (message.author.guild_permissions.administrator or message.author.guild_permissions.manage_guild):
        await message.channel.send("You do not have permission to use this command.")
        return True

    parts = str(message.content or "").strip().split(maxsplit=2)
    if len(parts) < 2:
        await message.channel.send("Usage: `?perm-sim /command-path [@user]`")
        return True
    commandPath = str(parts[1] or "").strip()
    if not commandPath.startswith("/"):
        commandPath = f"/{commandPath.lstrip('/')}"

    targetMember = message.author
    mentions = list(message.mentions)
    if mentions:
        mentioned = mentions[0]
        if isinstance(mentioned, discord.Member):
            targetMember = mentioned
        else:
            resolved = message.guild.get_member(int(mentioned.id))
            if resolved is not None:
                targetMember = resolved

    hint = router.helpCommands.slashPermissionHint(commandPath)
    likely = _likelyCommandAccess(router, targetMember, commandPath)
    roleIds = ", ".join(str(int(role.id)) for role in targetMember.roles if not role.is_default()) or "(none)"

    embed = discord.Embed(
        title="Permission Simulator (Hidden/Test)",
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
        description=f"Command: `{commandPath}`\nTarget: {targetMember.mention}",
    )
    embed.add_field(name="Policy Hint", value=hint, inline=False)
    embed.add_field(name="Likely Result", value=likely, inline=False)
    embed.add_field(name="Target Roles", value=roleIds[:1000], inline=False)
    await message.channel.send(embed=embed)
    return True
