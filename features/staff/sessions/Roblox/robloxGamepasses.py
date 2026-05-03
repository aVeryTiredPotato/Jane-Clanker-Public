from __future__ import annotations

import asyncio
from typing import Optional

import config
from features.staff.sessions.Roblox import robloxInventorySources, robloxPayloads, robloxTransport
from features.staff.sessions.Roblox.robloxModels import RobloxGamepassesResult

_cacheGet = robloxTransport.cacheGet
_cacheSet = robloxTransport.cacheSet
_requestJson = robloxTransport.requestJson
_optionalInt = robloxPayloads.optionalInt
_extractAssetId = robloxPayloads.extractAssetId
_extractAssetName = robloxPayloads.extractAssetName
_extractGamepassId = robloxPayloads.extractGamepassId
_extractGamepassName = robloxPayloads.extractGamepassName
_extractRobuxPrice = robloxPayloads.extractRobuxPrice
_extractCreatorId = robloxPayloads.extractCreatorId
_extractCreatorName = robloxPayloads.extractCreatorName
_extractCreatorType = robloxPayloads.extractCreatorType
_fetchPublicInventoryAssetType = robloxInventorySources.fetchPublicInventoryAssetType

def _isSelfCreatedByUser(details: dict, ownerId: int) -> bool:
    creatorId = _extractCreatorId(details)
    if ownerId <= 0 or creatorId != ownerId:
        return False
    creatorType = str(_extractCreatorType(details) or "").strip().lower()
    return creatorType in {"", "user"}


def _gamepassHardMaxPages() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceGamepassHardMaxPages", 100) or 100)
    except (TypeError, ValueError):
        configured = 100
    return max(1, min(configured, 500))
async def _fetchPublicGamepasses(
    robloxUserId: int,
    *,
    maxPages: int,
) -> tuple[list[dict], int, Optional[str], bool]:
    rows, status, error, complete = await _fetchPublicInventoryAssetType(
        int(robloxUserId),
        34,
        maxPages=maxPages,
    )
    gamepasses: list[dict] = []
    seenIds: set[int] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        gamepassId = _extractGamepassId(raw) or _extractAssetId(raw)
        if not gamepassId or int(gamepassId) in seenIds:
            continue
        seenIds.add(int(gamepassId))
        gamepasses.append(
            {
                "id": int(gamepassId),
                "name": _extractGamepassName(raw) or _extractAssetName(raw),
                "price": _extractRobuxPrice(raw),
                "creatorId": _extractCreatorId(raw),
                "creatorName": _extractCreatorName(raw),
                "creatorType": _extractCreatorType(raw),
            }
        )
    return gamepasses, int(status or 0), error, complete


async def _fetchGamepassProductInfo(gamepassId: int) -> tuple[int, dict | None, Optional[str]]:
    cached = _cacheGet(
        "gamepass_product",
        int(gamepassId),
        ttlName="robloxGamepassProductCacheTtlSec",
        defaultTtlSec=86400,
    )
    if isinstance(cached, dict):
        return 200, dict(cached), None

    url = f"https://apis.roblox.com/game-passes/v1/game-passes/{int(gamepassId)}/product-info"
    headers = {
        "Accept": "application/json",
        "User-Agent": str(
            getattr(
                config,
                "robloxPublicApiUserAgent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jane-Clanker/1.0",
            )
        ),
    }
    try:
        status, data = await _requestJson("GET", url, headers=headers, timeoutSec=10)
    except Exception as exc:
        return 0, None, str(exc)
    if status != 200 or not isinstance(data, dict):
        return int(status or 0), None, f"Gamepass product lookup failed ({status})."
    _cacheSet(
        "gamepass_product",
        int(gamepassId),
        dict(data),
        ttlName="robloxGamepassProductCacheTtlSec",
        defaultTtlSec=86400,
    )
    return int(status or 200), data, None


async def _enrichGamepassPrices(gamepasses: list[dict]) -> Optional[str]:
    missing = [
        int(gamepass["id"])
        for gamepass in gamepasses
        if int(gamepass.get("id") or 0) > 0
        and (
            _extractRobuxPrice(gamepass) is None
            or _extractCreatorId(gamepass) is None
            or _extractCreatorType(gamepass) is None
        )
    ]
    if not missing:
        return None
    errors: list[str] = []
    semaphore = asyncio.Semaphore(8)

    async def _lookup(gamepassId: int) -> tuple[int, dict | None, Optional[str]]:
        async with semaphore:
            return await _fetchGamepassProductInfo(gamepassId)

    results = await asyncio.gather(*[_lookup(gamepassId) for gamepassId in missing], return_exceptions=True)
    byId = {int(gamepass.get("id") or 0): gamepass for gamepass in gamepasses}
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        _, payload, error = result
        if error:
            errors.append(error)
            continue
        if not isinstance(payload, dict):
            continue
        gamepassId = _optionalInt(
            payload.get("GamePassId")
            or payload.get("gamePassId")
            or payload.get("TargetId")
            or payload.get("targetId")
            or payload.get("id")
        )
        if not gamepassId:
            continue
        gamepass = byId.get(int(gamepassId))
        if not gamepass:
            continue
        name = payload.get("Name") or payload.get("name")
        if isinstance(name, str) and name.strip() and not gamepass.get("name"):
            gamepass["name"] = name.strip()
        price = _extractRobuxPrice(payload)
        if price is not None:
            gamepass["price"] = int(price)
        creatorId = _extractCreatorId(payload)
        if creatorId is not None:
            gamepass["creatorId"] = int(creatorId)
        creatorName = _extractCreatorName(payload)
        if creatorName:
            gamepass["creatorName"] = creatorName
        creatorType = _extractCreatorType(payload)
        if creatorType:
            gamepass["creatorType"] = creatorType
        productId = _optionalInt(payload.get("ProductId") or payload.get("productId"))
        if productId:
            gamepass["productId"] = int(productId)
    return "; ".join(errors[:3]) or None


def _gamepassValueSummary(gamepasses: list[dict], *, ownerRobloxUserId: int = 0) -> dict:
    totalRobux = 0
    pricedGamepasses = 0
    selfCreatedGamepasses = 0
    selfCreatedPricedGamepasses = 0
    selfCreatedRobuxExcluded = 0
    for gamepass in gamepasses:
        price = _extractRobuxPrice(gamepass)
        if _isSelfCreatedByUser(gamepass, int(ownerRobloxUserId or 0)):
            selfCreatedGamepasses += 1
            if price is not None:
                selfCreatedPricedGamepasses += 1
                selfCreatedRobuxExcluded += int(price)
            continue
        if price is None:
            continue
        totalRobux += int(price)
        pricedGamepasses += 1
    return {
        "pricedGamepasses": pricedGamepasses,
        "unpricedGamepasses": max(0, len(gamepasses) - pricedGamepasses - selfCreatedPricedGamepasses),
        "totalRobux": totalRobux,
        "selfCreatedGamepassCount": selfCreatedGamepasses,
        "selfCreatedPricedGamepassCount": selfCreatedPricedGamepasses,
        "selfCreatedRobuxExcluded": selfCreatedRobuxExcluded,
    }


async def fetchRobloxGamepassesByIds(
    gamepassIds: list[int] | set[int] | tuple[int, ...],
    *,
    ownerRobloxUserId: int = 0,
) -> RobloxGamepassesResult:
    uniqueIds = sorted({int(value) for value in list(gamepassIds or []) if int(value or 0) > 0})
    ownerId = int(ownerRobloxUserId or 0)
    if not uniqueIds:
        return RobloxGamepassesResult([], 200, summary={
            "status": "OK",
            "pagesScanned": 0,
            "totalGamepasses": 0,
            "pricedGamepasses": 0,
            "unpricedGamepasses": 0,
            "totalRobux": 0,
            "selfCreatedGamepassCount": 0,
            "selfCreatedPricedGamepassCount": 0,
            "selfCreatedRobuxExcluded": 0,
            "complete": True,
            "valueSource": "Roblox game-pass product-info current price",
        })
    cacheKey = (tuple(uniqueIds), ownerId)
    cached = _cacheGet(
        "gamepasses_by_ids",
        cacheKey,
        ttlName="robloxGamepassCacheTtlSec",
        defaultTtlSec=21600,
    )
    if isinstance(cached, RobloxGamepassesResult):
        return cached
    gamepasses = [{"id": int(gamepassId), "name": None, "price": None} for gamepassId in uniqueIds]
    priceError = await _enrichGamepassPrices(gamepasses)
    valueSummary = _gamepassValueSummary(gamepasses, ownerRobloxUserId=ownerId)
    summary = {
        "status": "OK",
        "pagesScanned": 0,
        "totalGamepasses": len(gamepasses),
        "complete": True,
        "valueSource": "Roblox game-pass product-info current price",
    }
    summary.update(valueSummary)
    if priceError:
        summary["priceError"] = priceError
    result = RobloxGamepassesResult(gamepasses, 200, summary=summary)
    _cacheSet(
        "gamepasses_by_ids",
        cacheKey,
        result,
        ttlName="robloxGamepassCacheTtlSec",
        defaultTtlSec=21600,
    )
    return result


async def fetchRobloxUserGamepasses(
    robloxUserId: int,
    *,
    maxPages: int = 0,
) -> RobloxGamepassesResult:
    try:
        normalizedUserId = int(robloxUserId)
    except (TypeError, ValueError):
        return RobloxGamepassesResult([], 0, error="Gamepass inventory lookup failed (invalid Roblox user ID).")
    if normalizedUserId <= 0:
        return RobloxGamepassesResult([], 0, error="Gamepass inventory lookup failed (invalid Roblox user ID).")

    try:
        normalizedMaxPages = int(maxPages or 0)
    except (TypeError, ValueError):
        normalizedMaxPages = 0
    pageLimit = _gamepassHardMaxPages() if normalizedMaxPages <= 0 else max(1, normalizedMaxPages)
    requestedAllPages = normalizedMaxPages <= 0
    cacheKey = ("v3", normalizedUserId, normalizedMaxPages)
    cached = _cacheGet(
        "user_gamepasses",
        cacheKey,
        ttlName="robloxGamepassCacheTtlSec",
        defaultTtlSec=21600,
    )
    if isinstance(cached, RobloxGamepassesResult):
        return cached
    url = f"https://inventory.roblox.com/v1/users/{normalizedUserId}/items/GamePass"
    headers = {
        "Accept": "application/json",
        "User-Agent": str(
            getattr(
                config,
                "robloxPublicApiUserAgent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jane-Clanker/1.0",
            )
        ),
    }
    cursor: Optional[str] = None
    gamepasses: list[dict] = []
    seenIds: set[int] = set()
    pageCount = 0
    status = 200
    complete = True

    try:
        while pageCount < pageLimit:
            params = {"limit": "100", "sortOrder": "Desc"}
            if cursor:
                params["cursor"] = cursor
            status, data = await _requestJson("GET", url, headers=headers, params=params, timeoutSec=10)
            if status != 200 or not isinstance(data, dict):
                errorDetail = ""
                if isinstance(data, dict):
                    message = data.get("message") or data.get("error")
                    if isinstance(message, str) and message.strip():
                        errorDetail = f": {message.strip()}"
                fallbackRows, fallbackStatus, fallbackError, fallbackComplete = await _fetchPublicGamepasses(
                    normalizedUserId,
                    maxPages=pageLimit,
                )
                if fallbackRows:
                    gamepasses = fallbackRows
                    status = fallbackStatus or status
                    cursor = None
                    complete = fallbackComplete
                    break
                return RobloxGamepassesResult(
                    gamepasses,
                    int(status or 0),
                    nextCursor=cursor,
                    error=f"Gamepass inventory lookup failed ({status}){errorDetail}.",
                    summary={
                        "status": "ERROR",
                        "pagesScanned": pageCount,
                        "totalGamepasses": len(gamepasses),
                        "complete": False,
                        "requestedAllPages": requestedAllPages,
                        "fallbackError": fallbackError,
                    },
                )
            rawItems = data.get("data") or data.get("items") or []
            if not isinstance(rawItems, list):
                return RobloxGamepassesResult(
                    gamepasses,
                    int(status or 0),
                    nextCursor=cursor,
                    error="Gamepass inventory lookup returned invalid data.",
                )
            for raw in rawItems:
                if not isinstance(raw, dict):
                    continue
                gamepassId = _extractGamepassId(raw) or _optionalInt(raw.get("id"))
                if not gamepassId or int(gamepassId) in seenIds:
                    continue
                seenIds.add(int(gamepassId))
                gamepasses.append(
                    {
                        "id": int(gamepassId),
                        "name": _extractGamepassName(raw),
                        "price": _extractRobuxPrice(raw),
                        "creatorId": _extractCreatorId(raw),
                        "creatorName": _extractCreatorName(raw),
                        "creatorType": _extractCreatorType(raw),
                    }
                )
            pageCount += 1
            cursor = data.get("nextPageCursor") or data.get("nextPageToken") or data.get("nextCursor")
            if not cursor:
                break
        else:
            complete = not bool(cursor)
    except Exception as exc:
        return RobloxGamepassesResult(gamepasses, 0, nextCursor=cursor, error=str(exc))

    if not gamepasses:
        fallbackRows, fallbackStatus, fallbackError, fallbackComplete = await _fetchPublicGamepasses(
            normalizedUserId,
            maxPages=pageLimit,
        )
        if fallbackRows:
            gamepasses = fallbackRows
            status = fallbackStatus or status
            cursor = None
            complete = fallbackComplete
        elif fallbackError:
            return RobloxGamepassesResult(
                gamepasses,
                fallbackStatus or status,
                nextCursor=cursor,
                error=fallbackError,
                summary={
                    "status": "ERROR",
                    "pagesScanned": pageCount,
                    "totalGamepasses": 0,
                    "complete": False,
                    "requestedAllPages": requestedAllPages,
                },
            )

    priceError = await _enrichGamepassPrices(gamepasses)
    valueSummary = _gamepassValueSummary(gamepasses, ownerRobloxUserId=normalizedUserId)

    summary = {
        "status": "OK",
        "pagesScanned": pageCount,
        "totalGamepasses": len(gamepasses),
        "complete": complete and not bool(cursor),
        "nextPageCursor": cursor,
        "requestedAllPages": requestedAllPages,
        "valueSource": "Roblox game-pass product-info current price",
    }
    summary.update(valueSummary)
    if priceError:
        summary["priceError"] = priceError
    result = RobloxGamepassesResult(gamepasses, int(status or 200), nextCursor=cursor, summary=summary)
    _cacheSet(
        "user_gamepasses",
        cacheKey,
        result,
        ttlName="robloxGamepassCacheTtlSec",
        defaultTtlSec=21600,
    )
    return result
