from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp

import config
from runtime import taskBudgeter


_httpSession: Optional[aiohttp.ClientSession] = None
_robloxCache: dict[str, dict[object, tuple[datetime, object]]] = {}


def utcNow() -> datetime:
    return datetime.now(timezone.utc)


def cacheTtlSec(name: str, default: int) -> int:
    try:
        value = int(getattr(config, name, default) or 0)
    except (TypeError, ValueError):
        value = default
    return max(0, value)


def cacheGet(storeName: str, key: object, *, ttlName: str, defaultTtlSec: int) -> object | None:
    ttlSec = cacheTtlSec(ttlName, defaultTtlSec)
    if ttlSec <= 0:
        return None
    store = _robloxCache.get(storeName)
    if not store:
        return None
    cached = store.get(key)
    if not cached:
        return None
    cachedAt, value = cached
    if utcNow() - cachedAt > timedelta(seconds=ttlSec):
        store.pop(key, None)
        return None
    return value


def cacheSet(storeName: str, key: object, value: object, *, ttlName: str, defaultTtlSec: int) -> None:
    ttlSec = cacheTtlSec(ttlName, defaultTtlSec)
    if ttlSec <= 0:
        return
    store = _robloxCache.setdefault(storeName, {})
    maxEntries = int(getattr(config, "robloxApiCacheMaxEntries", 5000) or 5000)
    if maxEntries > 0 and len(store) >= maxEntries:
        oldestKey = min(store.items(), key=lambda item: item[1][0])[0]
        store.pop(oldestKey, None)
    store[key] = (utcNow(), value)


async def getHttpSession() -> aiohttp.ClientSession:
    global _httpSession
    if _httpSession is None or _httpSession.closed:
        timeoutSec = int(getattr(config, "robloxHttpTimeoutSec", 10) or 10)
        timeout = aiohttp.ClientTimeout(total=max(3, timeoutSec))
        _httpSession = aiohttp.ClientSession(timeout=timeout)
    return _httpSession


async def closeHttpSession() -> None:
    global _httpSession
    if _httpSession is None:
        return
    if not _httpSession.closed:
        await _httpSession.close()
    _httpSession = None


async def requestJson(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict] = None,
    timeoutSec: int = 10,
    jsonBody: Optional[dict] = None,
) -> tuple[int, object]:
    session = await getHttpSession()
    timeout = aiohttp.ClientTimeout(total=max(3, int(timeoutSec or 10)))
    maxRetryCount = max(0, int(getattr(config, "robloxApi429MaxRetries", 2) or 2))
    baseDelaySec = max(0.1, float(getattr(config, "robloxApi429RetryDelaySec", 1.0) or 1.0))

    for attempt in range(maxRetryCount + 1):
        async def _runRequest() -> tuple[int, object, dict[str, str]]:
            async with session.request(
                method.upper(),
                url,
                headers=headers,
                params=params,
                json=jsonBody,
                timeout=timeout,
            ) as resp:
                status = resp.status
                try:
                    payload = await resp.json(content_type=None)
                except Exception:
                    payload = None
                responseHeaders = dict(resp.headers or {})
                return status, payload, responseHeaders

        status, payload, responseHeaders = await taskBudgeter.runRoblox(_runRequest)

        if status != 429 or attempt >= maxRetryCount:
            return status, payload

        retryAfterSec = 0.0
        retryAfterHeader = responseHeaders.get("Retry-After")
        if retryAfterHeader:
            try:
                retryAfterSec = float(retryAfterHeader)
            except (TypeError, ValueError):
                retryAfterSec = 0.0
        if isinstance(payload, dict):
            payloadRetry = payload.get("retry_after") or payload.get("retryAfter")
            if payloadRetry is not None:
                try:
                    retryAfterSec = max(retryAfterSec, float(payloadRetry))
                except (TypeError, ValueError):
                    pass

        await asyncio.sleep(max(baseDelaySec * (attempt + 1), retryAfterSec))

    return 429, None


async def fetchBytes(
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    params: Optional[dict] = None,
    timeoutSec: int = 10,
    errorPrefix: str = "HTTP fetch failed",
) -> bytes:
    session = await getHttpSession()
    timeout = aiohttp.ClientTimeout(total=max(3, int(timeoutSec or 10)))

    async def _runRequest() -> bytes:
        async with session.get(url, headers=headers, params=params, timeout=timeout) as response:
            if response.status != 200:
                raise RuntimeError(f"{errorPrefix} ({response.status}).")
            return await response.read()

    return await taskBudgeter.runRoblox(_runRequest)
