from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

import discord

log = logging.getLogger(__name__)
_deps: dict[str, Any] = {}

_handledComponentInteractionIds: dict[int, datetime] = {}
_handledComponentInteractionTtl = timedelta(minutes=5)
_bgQueueUpdateTasks: dict[int, asyncio.Task] = {}
_bgQueueUpdateDirty: set[int] = set()
_sessionMessageUpdateTasks: dict[int, asyncio.Task] = {}
_sessionMessageUpdateDirty: set[int] = set()
_bgQueueRepostTasks: dict[int, asyncio.Task] = {}
_bgQueueClaims: dict[tuple[int, int], int] = {}
_cachedUsersById: dict[int, tuple[datetime, Optional[discord.abc.User]]] = {}
_cachedChannelsById: dict[int, tuple[datetime, Optional[object]]] = {}
_bgFinalSummaryPosted: set[int] = set()
_runtimeSemaphores: dict[str, tuple[int, asyncio.Semaphore]] = {}


def configure(
    *,
    configModule: Any,
    taskBudgeterModule: Any,
    updateBgQueueMessage: Callable[..., Awaitable[None]],
    updateSessionMessage: Callable[..., Awaitable[None]],
    repostBgQueueMessage: Callable[..., Awaitable[bool]],
) -> None:
    _deps["configModule"] = configModule
    _deps["taskBudgeterModule"] = taskBudgeterModule
    _deps["updateBgQueueMessage"] = updateBgQueueMessage
    _deps["updateSessionMessage"] = updateSessionMessage
    _deps["repostBgQueueMessage"] = repostBgQueueMessage


def _dep(name: str) -> Any:
    value = _deps.get(name)
    if value is None:
        raise RuntimeError(f"viewRuntime dependency not configured: {name}")
    return value


def _activeTaskCount(tasksBySessionId: dict[int, asyncio.Task]) -> int:
    return sum(1 for task in tasksBySessionId.values() if task and not task.done())


def _configuredLimit(configKey: str, default: int) -> int:
    try:
        value = int(getattr(_dep("configModule"), configKey, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, value)


def _runtimeSemaphore(name: str, configKey: str, default: int) -> asyncio.Semaphore:
    limit = _configuredLimit(configKey, default)
    existing = _runtimeSemaphores.get(name)
    if existing is not None and existing[0] == limit:
        return existing[1]
    semaphore = asyncio.Semaphore(limit)
    _runtimeSemaphores[name] = (limit, semaphore)
    return semaphore


def getRuntimeQueueTelemetry() -> dict[str, int]:
    return {
        "bgQueueUpdateDirty": len(_bgQueueUpdateDirty),
        "bgQueueUpdateActiveTasks": _activeTaskCount(_bgQueueUpdateTasks),
        "sessionUpdateDirty": len(_sessionMessageUpdateDirty),
        "sessionUpdateActiveTasks": _activeTaskCount(_sessionMessageUpdateTasks),
        "bgQueueRepostActiveTasks": _activeTaskCount(_bgQueueRepostTasks),
        "bgClaimsActive": len(_bgQueueClaims),
        "bgQueueUpdateLimit": _configuredLimit("bgQueueUpdateConcurrency", 2),
        "sessionUpdateLimit": _configuredLimit("sessionMessageUpdateConcurrency", 2),
        "bgQueueRepostLimit": _configuredLimit("bgQueueRepostConcurrency", 1),
    }


def claimComponentInteraction(interactionId: int) -> bool:
    now = datetime.now()
    expiredIds = [
        key
        for key, seenAt in _handledComponentInteractionIds.items()
        if now - seenAt > _handledComponentInteractionTtl
    ]
    for key in expiredIds:
        _handledComponentInteractionIds.pop(key, None)

    if interactionId in _handledComponentInteractionIds:
        return False
    _handledComponentInteractionIds[interactionId] = now
    return True


def _bgClaimKey(sessionId: int, userId: int) -> tuple[int, int]:
    return int(sessionId), int(userId)


def _cacheTtlSec() -> int:
    return max(30, int(getattr(_dep("configModule"), "discordEntityCacheTtlSec", 300) or 300))


def _pruneCacheBySize(cache: dict, maxSize: int = 2048) -> None:
    if len(cache) <= maxSize:
        return
    ordered = sorted(cache.items(), key=lambda item: item[1][0])
    removeCount = len(cache) - maxSize
    for key, _ in ordered[:removeCount]:
        cache.pop(key, None)


def _cacheIsFresh(cachedAt: datetime) -> bool:
    return (datetime.utcnow() - cachedAt).total_seconds() <= _cacheTtlSec()


async def getCachedUser(bot: discord.Client, userId: int) -> Optional[discord.abc.User]:
    key = int(userId)
    cached = _cachedUsersById.get(key)
    if cached and _cacheIsFresh(cached[0]):
        return cached[1]

    user = bot.get_user(key)
    if user is None:
        try:
            user = await _dep("taskBudgeterModule").runDiscord(lambda: bot.fetch_user(key))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            user = None

    _cachedUsersById[key] = (datetime.utcnow(), user)
    _pruneCacheBySize(_cachedUsersById)
    return user


async def getCachedChannel(bot: discord.Client, channelId: int) -> Optional[object]:
    key = int(channelId)
    cached = _cachedChannelsById.get(key)
    if cached and _cacheIsFresh(cached[0]):
        return cached[1]

    channel = bot.get_channel(key)
    if channel is None:
        try:
            channel = await _dep("taskBudgeterModule").runDiscord(lambda: bot.fetch_channel(key))
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            channel = None

    _cachedChannelsById[key] = (datetime.utcnow(), channel)
    _pruneCacheBySize(_cachedChannelsById)
    return channel


def getBgClaimOwnerId(sessionId: int, userId: int) -> Optional[int]:
    return _bgQueueClaims.get(_bgClaimKey(sessionId, userId))


def setBgClaimOwnerId(sessionId: int, userId: int, ownerId: int) -> None:
    _bgQueueClaims[_bgClaimKey(sessionId, userId)] = int(ownerId)


def clearBgClaim(sessionId: int, userId: int) -> None:
    _bgQueueClaims.pop(_bgClaimKey(sessionId, userId), None)


def clearBgClaimsForSession(sessionId: int) -> None:
    targetSessionId = int(sessionId)
    staleKeys = [key for key in _bgQueueClaims.keys() if key[0] == targetSessionId]
    for key in staleKeys:
        _bgQueueClaims.pop(key, None)


def getBgClaimsForSession(sessionId: int) -> dict[int, int]:
    out: dict[int, int] = {}
    targetSessionId = int(sessionId)
    for (claimSessionId, userId), ownerId in _bgQueueClaims.items():
        if claimSessionId != targetSessionId:
            continue
        out[int(userId)] = int(ownerId)
    return out


async def requestBgQueueMessageUpdate(
    bot: discord.Client,
    sessionId: int,
    *,
    delaySec: Optional[float] = None,
) -> None:
    debounceDelaySec = float(
        delaySec
        if delaySec is not None
        else getattr(_dep("configModule"), "bgQueueUpdateDebounceSec", 1.0)
    )
    debounceDelaySec = max(0.0, debounceDelaySec)

    _bgQueueUpdateDirty.add(sessionId)
    existing = _bgQueueUpdateTasks.get(sessionId)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            while True:
                _bgQueueUpdateDirty.discard(sessionId)
                if debounceDelaySec > 0:
                    await asyncio.sleep(debounceDelaySec)
                async with _runtimeSemaphore("bgQueueUpdate", "bgQueueUpdateConcurrency", 2):
                    await _dep("updateBgQueueMessage")(bot, sessionId)
                if sessionId not in _bgQueueUpdateDirty:
                    break
        except Exception:
            log.exception("Debounced BG queue update failed for session %s.", sessionId)
        finally:
            current = _bgQueueUpdateTasks.get(sessionId)
            if current is asyncio.current_task():
                _bgQueueUpdateTasks.pop(sessionId, None)
            _bgQueueUpdateDirty.discard(sessionId)

    _bgQueueUpdateTasks[sessionId] = asyncio.create_task(_runner())


def _sessionMessageUpdateDebounceSec() -> float:
    return max(0.0, float(getattr(_dep("configModule"), "sessionMessageUpdateDebounceSec", 0.75) or 0.75))


async def requestSessionMessageUpdate(
    bot: discord.Client,
    sessionId: int,
    *,
    delaySec: Optional[float] = None,
) -> None:
    debounceDelaySec = float(
        delaySec
        if delaySec is not None
        else _sessionMessageUpdateDebounceSec()
    )
    debounceDelaySec = max(0.0, debounceDelaySec)

    _sessionMessageUpdateDirty.add(sessionId)
    existing = _sessionMessageUpdateTasks.get(sessionId)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            while True:
                _sessionMessageUpdateDirty.discard(sessionId)
                if debounceDelaySec > 0:
                    await asyncio.sleep(debounceDelaySec)
                async with _runtimeSemaphore("sessionUpdate", "sessionMessageUpdateConcurrency", 2):
                    await _dep("updateSessionMessage")(bot, sessionId)
                if sessionId not in _sessionMessageUpdateDirty:
                    break
        except Exception:
            log.exception("Debounced session message update failed for session %s.", sessionId)
        finally:
            current = _sessionMessageUpdateTasks.get(sessionId)
            if current is asyncio.current_task():
                _sessionMessageUpdateTasks.pop(sessionId, None)
            _sessionMessageUpdateDirty.discard(sessionId)

    _sessionMessageUpdateTasks[sessionId] = asyncio.create_task(_runner())


def _queueRepostIntervalSec() -> int:
    return max(60, int(getattr(_dep("configModule"), "bgQueueRepostIntervalSec", 300) or 300))


def ensureBgQueueRepostTask(bot: discord.Client, sessionId: int) -> None:
    existing = _bgQueueRepostTasks.get(sessionId)
    if existing and not existing.done():
        return

    async def _runner() -> None:
        try:
            await bot.wait_until_ready()
            intervalSec = _queueRepostIntervalSec()
            while not bot.is_closed():
                await asyncio.sleep(intervalSec)
                async with _runtimeSemaphore("bgQueueRepost", "bgQueueRepostConcurrency", 1):
                    shouldContinue = await _dep("repostBgQueueMessage")(bot, sessionId)
                if not shouldContinue:
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("BG queue repost loop failed for session %s.", sessionId)
        finally:
            current = _bgQueueRepostTasks.get(sessionId)
            if current is asyncio.current_task():
                _bgQueueRepostTasks.pop(sessionId, None)

    _bgQueueRepostTasks[sessionId] = asyncio.create_task(_runner())


def stopBgQueueRepostTask(sessionId: int) -> None:
    task = _bgQueueRepostTasks.pop(sessionId, None)
    if task and not task.done():
        task.cancel()


def getBgFinalSummaryPostedSet() -> set[int]:
    return _bgFinalSummaryPosted
