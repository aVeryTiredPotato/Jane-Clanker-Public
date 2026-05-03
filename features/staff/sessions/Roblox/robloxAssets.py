from __future__ import annotations

import asyncio
from io import BytesIO
from typing import Optional

from PIL import Image

import config
from features.staff.sessions.Roblox import robloxPayloads, robloxTransport
from features.staff.sessions.Roblox.robloxModels import RobloxAssetThumbnailsResult

_utcNow = robloxTransport.utcNow
_cacheGet = robloxTransport.cacheGet
_cacheSet = robloxTransport.cacheSet
_requestJson = robloxTransport.requestJson
_optionalBool = robloxPayloads.optionalBool
_optionalInt = robloxPayloads.optionalInt
_extractRobuxPrice = robloxPayloads.extractRobuxPrice
_extractCreatorId = robloxPayloads.extractCreatorId
_extractCreatorName = robloxPayloads.extractCreatorName
_extractCreatorType = robloxPayloads.extractCreatorType

async def _fetchCatalogAssetPrices(assetIds: list[int]) -> tuple[dict[int, dict], Optional[str]]:
    uniqueIds = sorted({int(assetId) for assetId in assetIds if int(assetId or 0) > 0})
    if not uniqueIds:
        return {}, None

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": str(
            getattr(
                config,
                "robloxPublicApiUserAgent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jane-Clanker/1.0",
            )
        ),
    }
    prices: dict[int, dict] = {}
    missingIds: list[int] = []
    for assetId in uniqueIds:
        cached = _cacheGet(
            "asset_prices",
            int(assetId),
            ttlName="robloxAssetPriceCacheTtlSec",
            defaultTtlSec=86400,
        )
        if isinstance(cached, dict):
            if (
                cached.get("creatorId") is not None
                and (cached.get("assetTypeId") is not None or cached.get("assetTypeName") is not None)
                and "creatorType" in cached
            ):
                prices[int(assetId)] = dict(cached)
            else:
                missingIds.append(int(assetId))
        else:
            missingIds.append(int(assetId))
    if not missingIds:
        return prices, None

    errors: list[str] = []
    semaphore = asyncio.Semaphore(8)

    async def _fetchAsset(assetId: int) -> tuple[int, dict | None, Optional[str]]:
        url = f"https://economy.roblox.com/v2/assets/{int(assetId)}/details"
        async with semaphore:
            try:
                status, data = await _requestJson("GET", url, headers=headers, timeoutSec=10)
            except Exception as exc:
                return int(assetId), None, str(exc)
        if status != 200 or not isinstance(data, dict):
            return int(assetId), None, f"Asset price lookup failed ({status})."
        entry = {
            "id": int(assetId),
            "name": data.get("Name") or data.get("name"),
            "price": _extractRobuxPrice(data),
            "isForSale": _optionalBool(
                data.get("IsForSale") if data.get("IsForSale") is not None else data.get("isForSale")
            ),
            "isLimited": _optionalBool(
                data.get("IsLimited") if data.get("IsLimited") is not None else data.get("isLimited")
            ),
            "isLimitedUnique": _optionalBool(
                data.get("IsLimitedUnique") if data.get("IsLimitedUnique") is not None else data.get("isLimitedUnique")
            ),
            "creatorId": _extractCreatorId(data),
            "creatorName": _extractCreatorName(data),
            "creatorType": _extractCreatorType(data),
            "assetTypeId": _optionalInt(data.get("AssetTypeId") or data.get("assetTypeId")),
            "assetTypeName": data.get("AssetType") or data.get("assetType") or data.get("assetTypeName"),
        }
        return int(assetId), entry, None

    results = await asyncio.gather(
        *[_fetchAsset(assetId) for assetId in missingIds],
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        assetId, entry, error = result
        if error:
            errors.append(error)
            continue
        if not isinstance(entry, dict):
            continue
        prices[int(assetId)] = entry
        _cacheSet(
            "asset_prices",
            int(assetId),
            dict(entry),
            ttlName="robloxAssetPriceCacheTtlSec",
            defaultTtlSec=86400,
        )

    return prices, "; ".join(errors[:3]) or None
def _inventoryVisualHashSize() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryVisualHashSize", 8) or 8)
    except (TypeError, ValueError):
        configured = 8
    return max(4, min(configured, 16))
def _averageImageHash(image: Image.Image, hashSize: int) -> str:
    resized = image.convert("L").resize((hashSize, hashSize), Image.Resampling.LANCZOS)
    pixels = list(resized.getdata())
    if not pixels:
        return ""
    average = sum(int(pixel) for pixel in pixels) / len(pixels)
    bits = 0
    for pixel in pixels:
        bits = (bits << 1) | (1 if int(pixel) >= average else 0)
    hexLength = max(1, (hashSize * hashSize + 3) // 4)
    return format(bits, f"0{hexLength}x")


def _imageHashDistance(left: str, right: str) -> Optional[int]:
    if not left or not right:
        return None
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return None


async def _fetchThumbnailImageBytes(url: str) -> bytes:
    return await robloxTransport.fetchBytes(url, timeoutSec=10, errorPrefix="Thumbnail fetch failed")


async def fetchRobloxAssetThumbnails(
    assetIds: list[int],
    *,
    size: str = "420x420",
    imageFormat: str = "Png",
    returnPolicy: str = "PlaceHolder",
) -> RobloxAssetThumbnailsResult:
    uniqueIds = sorted({int(assetId) for assetId in list(assetIds or []) if int(assetId or 0) > 0})
    if not uniqueIds:
        return RobloxAssetThumbnailsResult([], 200)

    cachedRows: list[dict] = []
    missingIds: list[int] = []
    for assetId in uniqueIds:
        cached = _cacheGet(
            "asset_thumbnail",
            int(assetId),
            ttlName="robloxAssetThumbnailCacheTtlSec",
            defaultTtlSec=86400,
        )
        if isinstance(cached, dict):
            cachedRows.append(dict(cached))
        else:
            missingIds.append(int(assetId))

    rows = list(cachedRows)
    if not missingIds:
        return RobloxAssetThumbnailsResult(rows, 200)

    url = "https://thumbnails.roblox.com/v1/assets"
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

    errors: list[str] = []
    status = 200
    for start in range(0, len(missingIds), 100):
        batch = missingIds[start : start + 100]
        params = {
            "assetIds": ",".join(str(assetId) for assetId in batch),
            "returnPolicy": returnPolicy,
            "size": size,
            "format": imageFormat,
            "isCircular": "false",
        }
        try:
            batchStatus, data = await _requestJson("GET", url, headers=headers, params=params, timeoutSec=10)
        except Exception as exc:
            return RobloxAssetThumbnailsResult(rows, 0, error=str(exc))
        status = int(batchStatus or status or 0)
        if batchStatus != 200 or not isinstance(data, dict):
            return RobloxAssetThumbnailsResult(rows, status, error=f"Asset thumbnail lookup failed ({batchStatus}).")
        rawRows = data.get("data")
        if not isinstance(rawRows, list):
            return RobloxAssetThumbnailsResult(rows, status, error="Asset thumbnail lookup returned invalid data.")
        for entry in rawRows:
            if not isinstance(entry, dict):
                continue
            targetId = _optionalInt(entry.get("targetId"))
            if targetId is None:
                continue
            row = {
                "id": int(targetId),
                "imageUrl": entry.get("imageUrl"),
                "state": entry.get("state"),
            }
            rows.append(row)
            _cacheSet(
                "asset_thumbnail",
                int(targetId),
                dict(row),
                ttlName="robloxAssetThumbnailCacheTtlSec",
                defaultTtlSec=86400,
    )
    return RobloxAssetThumbnailsResult(rows, status, error="; ".join(errors[:3]) or None)


async def validateRobloxAssetVisualReferences(assetIds: list[int]) -> list[dict]:
    uniqueIds = sorted({int(assetId) for assetId in list(assetIds or []) if int(assetId or 0) > 0})
    if not uniqueIds:
        return []

    thumbnailResult = await fetchRobloxAssetThumbnails(uniqueIds)
    thumbnailRows = {
        int(row.get("id")): row
        for row in list(thumbnailResult.thumbnails or [])
        if isinstance(row, dict) and _optionalInt(row.get("id")) is not None
    }
    hashSize = _inventoryVisualHashSize()
    semaphore = asyncio.Semaphore(6)

    async def _validateAsset(assetId: int) -> dict:
        row = thumbnailRows.get(int(assetId))
        base = {
            "assetId": int(assetId),
            "thumbnailUrl": None,
            "thumbnailState": None,
            "thumbnailHash": None,
            "hashSize": int(hashSize),
            "validationState": "PENDING",
            "validationError": None,
            "lastValidatedAt": _utcNow().replace(microsecond=0).isoformat(),
        }
        if not isinstance(row, dict):
            base["validationState"] = "INVALID"
            base["validationError"] = "Thumbnail metadata missing."
            return base

        imageUrl = str(row.get("imageUrl") or "").strip()
        state = str(row.get("state") or "").strip().lower()
        base["thumbnailUrl"] = imageUrl or None
        base["thumbnailState"] = state or None

        if not imageUrl:
            base["validationState"] = "INVALID"
            base["validationError"] = "Thumbnail image URL missing."
            return base
        if state not in {"completed", ""}:
            base["validationState"] = "INVALID"
            base["validationError"] = f"Thumbnail state was `{state or 'unknown'}`."
            return base

        cachedHash = _cacheGet(
            "asset_thumbnail_hash",
            int(assetId),
            ttlName="robloxAssetThumbnailHashCacheTtlSec",
            defaultTtlSec=86400,
        )
        if isinstance(cachedHash, str) and cachedHash:
            base["thumbnailHash"] = cachedHash
            base["validationState"] = "VALID"
            return base

        try:
            async with semaphore:
                imageBytes = await _fetchThumbnailImageBytes(imageUrl)
            with Image.open(BytesIO(imageBytes)) as image:
                hashValue = _averageImageHash(image, hashSize)
        except Exception as exc:
            base["validationState"] = "ERROR"
            base["validationError"] = str(exc)
            return base

        if not hashValue:
            base["validationState"] = "INVALID"
            base["validationError"] = "Thumbnail hash was empty."
            return base

        _cacheSet(
            "asset_thumbnail_hash",
            int(assetId),
            hashValue,
            ttlName="robloxAssetThumbnailHashCacheTtlSec",
            defaultTtlSec=86400,
        )
        base["thumbnailHash"] = hashValue
        base["validationState"] = "VALID"
        return base

    results = await asyncio.gather(*[_validateAsset(assetId) for assetId in uniqueIds], return_exceptions=True)
    rows: list[dict] = []
    for assetId, result in zip(uniqueIds, results):
        if isinstance(result, Exception):
            rows.append(
                {
                    "assetId": int(assetId),
                    "thumbnailUrl": None,
                    "thumbnailState": None,
                    "thumbnailHash": None,
                    "hashSize": int(hashSize),
                    "validationState": "ERROR",
                    "validationError": str(result),
                    "lastValidatedAt": _utcNow().replace(microsecond=0).isoformat(),
                }
            )
            continue
        rows.append(dict(result))
    return rows
async def fetchRobloxAssetThumbnailHashes(assetIds: list[int]) -> tuple[dict[int, str], Optional[str]]:
    rows = await validateRobloxAssetVisualReferences(assetIds)
    hashes: dict[int, str] = {}
    errors: list[str] = []
    for row in rows:
        assetId = _optionalInt(row.get("assetId"))
        thumbnailHash = str(row.get("thumbnailHash") or "").strip()
        if assetId is not None and thumbnailHash:
            hashes[int(assetId)] = thumbnailHash
            continue
        state = str(row.get("validationState") or "").strip().upper() or "UNKNOWN"
        errorText = str(row.get("validationError") or "").strip() or state.title()
        if assetId is not None:
            errors.append(f"{int(assetId)}: {errorText}")
        else:
            errors.append(errorText)
    return hashes, "; ".join(errors[:3]) or None

fetchCatalogAssetPrices = _fetchCatalogAssetPrices
imageHashDistance = _imageHashDistance
