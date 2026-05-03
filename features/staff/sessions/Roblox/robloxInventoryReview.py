from __future__ import annotations

import config
from features.staff.sessions.Roblox import robloxAssets, robloxInventoryMatching, robloxInventorySources, robloxPayloads
from features.staff.sessions.Roblox.robloxModels import RobloxInventoryReviewItemsResult

_optionalBool = robloxPayloads.optionalBool
_optionalInt = robloxPayloads.optionalInt
_extractRobuxPrice = robloxPayloads.extractRobuxPrice
_fetchCatalogAssetPrices = robloxAssets.fetchCatalogAssetPrices
validateRobloxAssetVisualReferences = robloxAssets.validateRobloxAssetVisualReferences
_appendInventoryVisualCandidate = robloxInventoryMatching.appendInventoryVisualCandidate
_fetchPublicInventoryAssets = robloxInventorySources.fetchPublicInventoryAssets

def _inventoryReviewMaxPagesPerType() -> int:
    try:
        configured = int(getattr(config, "bgItemReviewMaxPagesPerType", 4) or 4)
    except (TypeError, ValueError):
        configured = 4
    return max(1, min(configured, 25))


def _inventoryReviewCandidateLimit() -> int:
    try:
        configured = int(getattr(config, "bgItemReviewCandidateLimit", 60) or 60)
    except (TypeError, ValueError):
        configured = 60
    return max(1, min(configured, 250))

async def fetchRobloxInventoryReviewItems(
    robloxUserId: int,
    *,
    maxPagesPerType: int = 0,
    candidateLimit: int = 0,
) -> RobloxInventoryReviewItemsResult:
    try:
        resolvedPages = int(maxPagesPerType or 0)
    except (TypeError, ValueError):
        resolvedPages = 0
    if resolvedPages <= 0:
        resolvedPages = _inventoryReviewMaxPagesPerType()

    try:
        resolvedLimit = int(candidateLimit or 0)
    except (TypeError, ValueError):
        resolvedLimit = 0
    if resolvedLimit <= 0:
        resolvedLimit = _inventoryReviewCandidateLimit()

    rawRows, status, error, complete = await _fetchPublicInventoryAssets(
        int(robloxUserId),
        maxPagesPerType=resolvedPages,
    )
    if error and not rawRows:
        return RobloxInventoryReviewItemsResult(
            [],
            int(status or 0),
            error=error,
            summary={
                "itemsScanned": 0,
                "candidates": 0,
                "complete": False,
            },
        )

    candidates: list[dict] = []
    seenAssetIds: set[int] = set()
    for raw in rawRows:
        if not isinstance(raw, dict):
            continue
        _appendInventoryVisualCandidate(candidates, seenAssetIds, raw)
        if len(candidates) >= resolvedLimit:
            break

    assetIds = [int(row.get("id") or 0) for row in candidates if int(row.get("id") or 0) > 0]
    prices, priceError = await _fetchCatalogAssetPrices(assetIds)
    validationRows = await validateRobloxAssetVisualReferences(assetIds)
    validationByAssetId = {
        int(row.get("assetId")): row
        for row in validationRows
        if _optionalInt(row.get("assetId")) is not None
    }

    items: list[dict] = []
    validCount = 0
    invalidCount = 0
    for candidate in candidates:
        assetId = int(candidate.get("id") or 0)
        if assetId <= 0:
            continue
        priceInfo = prices.get(assetId) or {}
        validation = validationByAssetId.get(assetId) or {}
        validationState = str(validation.get("validationState") or "").strip().upper()
        if validationState == "VALID":
            validCount += 1
        else:
            invalidCount += 1
        items.append(
            {
                "id": assetId,
                "name": candidate.get("name"),
                "itemType": candidate.get("itemType"),
                "creatorId": candidate.get("creatorId"),
                "creatorName": candidate.get("creatorName"),
                "price": _extractRobuxPrice(priceInfo),
                "isForSale": _optionalBool(priceInfo.get("isForSale")),
                "thumbnailHash": str(validation.get("thumbnailHash") or "").strip() or None,
                "thumbnailUrl": str(validation.get("thumbnailUrl") or "").strip() or None,
                "thumbnailState": str(validation.get("thumbnailState") or "").strip() or None,
                "validationState": validationState or "PENDING",
                "validationError": str(validation.get("validationError") or "").strip() or None,
            }
        )

    summary = {
        "itemsScanned": len(rawRows),
        "candidates": len(items),
        "validCandidates": validCount,
        "invalidCandidates": invalidCount,
        "complete": bool(complete),
        "scanError": error,
        "priceError": priceError,
    }
    finalError = error if not rawRows else None
    return RobloxInventoryReviewItemsResult(
        items,
        int(status or 200),
        error=finalError,
        summary=summary,
    )
