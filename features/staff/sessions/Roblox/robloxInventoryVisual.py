from __future__ import annotations

from typing import Optional

import config
from features.staff.sessions.Roblox import robloxAssets, robloxInventoryText, robloxPayloads

_extractAssetId = robloxPayloads.extractAssetId
_extractAssetName = robloxPayloads.extractAssetName
_extractInventoryItemType = robloxPayloads.extractInventoryItemType
_extractCreatorId = robloxPayloads.extractCreatorId
_extractCreatorName = robloxPayloads.extractCreatorName
_optionalInt = robloxPayloads.optionalInt
fetchRobloxAssetThumbnailHashes = robloxAssets.fetchRobloxAssetThumbnailHashes
_imageHashDistance = robloxAssets.imageHashDistance
_inventoryMatchPriority = robloxInventoryText.inventoryMatchPriority

def _inventoryVisualMatchingEnabled() -> bool:
    return bool(getattr(config, "bgIntelligenceInventoryVisualMatchingEnabled", True))


def _inventoryVisualCandidateLimit() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryVisualCandidateLimit", 120) or 120)
    except (TypeError, ValueError):
        configured = 120
    return max(0, min(configured, 300))


def _inventoryVisualReferenceLimit() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryVisualReferenceLimit", 80) or 80)
    except (TypeError, ValueError):
        configured = 80
    return max(0, min(configured, 250))


def _inventoryVisualHashDistanceMax() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryVisualHashDistanceMax", 3) or 3)
    except (TypeError, ValueError):
        configured = 3
    return max(0, min(configured, 32))


def _inventoryVisualHashSize() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryVisualHashSize", 8) or 8)
    except (TypeError, ValueError):
        configured = 8
    return max(4, min(configured, 16))

_INVENTORY_VISUAL_TYPE_MARKERS = (
    "accessory",
    "shirt",
    "pants",
    "pant",
    "tshirt",
    "t-shirt",
    "jacket",
    "sweater",
    "shorts",
    "dress",
    "skirt",
    "shoe",
    "hair",
    "hat",
    "face",
    "neck",
    "shoulder",
    "back",
    "front",
    "waist",
)

_ASSET_TYPE_VISUAL_CATEGORIES: dict[int, str] = {
    2: "classic_tshirt",
    8: "hat",
    11: "classic_shirt",
    12: "classic_pants",
    18: "face",
    41: "hair",
    42: "face_accessory",
    43: "neck_accessory",
    44: "shoulder_accessory",
    45: "front_accessory",
    46: "back_accessory",
    47: "waist_accessory",
    64: "layered_tshirt",
    65: "layered_shirt",
    66: "layered_pants",
    67: "jacket",
    68: "sweater",
    69: "shorts",
    70: "left_shoe",
    71: "right_shoe",
    72: "dress_skirt",
    76: "eyebrow",
    77: "eyelash",
}


def _inventoryAssetTypeId(raw: dict) -> Optional[int]:
    asset = raw.get("asset") if isinstance(raw.get("asset"), dict) else None
    assetDetails = raw.get("assetDetails") if isinstance(raw.get("assetDetails"), dict) else None
    assetType = raw.get("assetType") if isinstance(raw.get("assetType"), dict) else None
    for value in (
        raw.get("assetTypeId"),
        raw.get("asset_type_id"),
        raw.get("AssetTypeId"),
        assetType.get("id") if assetType else None,
        assetType.get("assetTypeId") if assetType else None,
        assetType.get("AssetTypeId") if assetType else None,
        asset.get("assetTypeId") if asset else None,
        asset.get("AssetTypeId") if asset else None,
        assetDetails.get("assetTypeId") if assetDetails else None,
        assetDetails.get("AssetTypeId") if assetDetails else None,
    ):
        parsed = _optionalInt(value)
        if parsed is not None and parsed > 0:
            return int(parsed)
    return None


def _visualCategoryFromType(itemType: object = "", assetTypeId: object = None) -> str:
    parsedTypeId = _optionalInt(assetTypeId)
    if parsedTypeId is not None and int(parsedTypeId) in _ASSET_TYPE_VISUAL_CATEGORIES:
        return _ASSET_TYPE_VISUAL_CATEGORIES[int(parsedTypeId)]

    if isinstance(itemType, dict):
        for key in ("name", "type", "displayName"):
            category = _visualCategoryFromType(itemType.get(key), assetTypeId)
            if category:
                return category
        return ""

    normalized = str(itemType or "").replace("_", " ").replace("-", " ").strip().lower()
    compact = normalized.replace(" ", "")
    if not normalized:
        return ""
    if "tshirt" in compact or "t shirt" in normalized:
        return "classic_tshirt"
    if "shirt" in normalized:
        return "classic_shirt"
    if "pants" in normalized or "pant" == normalized:
        return "classic_pants"
    if "hat" in normalized:
        return "hat"
    if "hair" in normalized:
        return "hair"
    if "face accessory" in normalized:
        return "face_accessory"
    if normalized == "face" or " face" in normalized:
        return "face"
    if "neck" in normalized:
        return "neck_accessory"
    if "shoulder" in normalized:
        return "shoulder_accessory"
    if "front" in normalized:
        return "front_accessory"
    if "back" in normalized:
        return "back_accessory"
    if "waist" in normalized:
        return "waist_accessory"
    if "jacket" in normalized:
        return "jacket"
    if "sweater" in normalized:
        return "sweater"
    if "shorts" in normalized:
        return "shorts"
    if "dress" in normalized or "skirt" in normalized:
        return "dress_skirt"
    if "shoe" in normalized:
        return "shoe"
    if "eyebrow" in normalized:
        return "eyebrow"
    if "eyelash" in normalized:
        return "eyelash"
    return ""


def _visualCategoriesCompatible(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    shoeCategories = {"left_shoe", "right_shoe", "shoe"}
    return left in shoeCategories and right in shoeCategories


def _isInventoryVisualCandidate(raw: dict) -> bool:
    itemType = _extractInventoryItemType(raw).replace("_", " ").replace("-", " ").strip().lower()
    assetTypeId = _inventoryAssetTypeId(raw)
    if _visualCategoryFromType(itemType, assetTypeId):
        return True
    return bool(itemType) and any(marker in itemType for marker in _INVENTORY_VISUAL_TYPE_MARKERS)


def _inventoryCandidateEntryFromRaw(raw: dict) -> Optional[dict]:
    assetId = _extractAssetId(raw)
    if assetId is None:
        return None
    return {
        "id": int(assetId),
        "name": _extractAssetName(raw),
        "itemType": _extractInventoryItemType(raw),
        "assetTypeId": _inventoryAssetTypeId(raw),
        "visualCategory": _visualCategoryFromType(_extractInventoryItemType(raw), _inventoryAssetTypeId(raw)),
        "creatorId": _extractCreatorId(raw),
        "creatorName": _extractCreatorName(raw),
    }


async def _referenceVisualCategories(referenceIds: list[int]) -> tuple[dict[int, str], Optional[str]]:
    detailsById, error = await robloxAssets.fetchCatalogAssetPrices(referenceIds)
    categories: dict[int, str] = {}
    for assetId, details in dict(detailsById or {}).items():
        if not isinstance(details, dict):
            continue
        category = _visualCategoryFromType(details.get("assetTypeName"), details.get("assetTypeId"))
        if category:
            categories[int(assetId)] = category
    return categories, error


def _appendInventoryVisualCandidate(
    candidates: list[dict],
    seenAssetIds: set[int],
    raw: dict,
) -> None:
    if not _inventoryVisualMatchingEnabled():
        return
    candidateLimit = _inventoryVisualCandidateLimit()
    if candidateLimit <= 0 or len(candidates) >= candidateLimit:
        return
    if not _isInventoryVisualCandidate(raw):
        return
    candidate = _inventoryCandidateEntryFromRaw(raw)
    if not candidate:
        return
    assetId = int(candidate.get("id") or 0)
    if assetId <= 0 or assetId in seenAssetIds:
        return
    seenAssetIds.add(assetId)
    candidates.append(candidate)

def _inventoryPrimarySignal(entry: dict) -> dict:
    return {
        "matchType": entry.get("matchType"),
        "matchMode": entry.get("matchMode"),
        "matchedField": entry.get("matchedField"),
        "matchedText": entry.get("matchedText"),
        "keyword": entry.get("keyword"),
        "fuzzyScore": entry.get("fuzzyScore"),
        "referenceItemId": entry.get("referenceItemId"),
        "visualDistance": entry.get("visualDistance"),
        "reason": entry.get("reason"),
    }


def _applyInventoryPrimarySignal(entry: dict, signal: dict) -> None:
    entry["matchType"] = signal.get("matchType")
    entry["matchMode"] = signal.get("matchMode")
    entry["matchedField"] = signal.get("matchedField")
    entry["matchedText"] = signal.get("matchedText")
    entry["keyword"] = signal.get("keyword")
    entry["fuzzyScore"] = signal.get("fuzzyScore")
    entry["referenceItemId"] = signal.get("referenceItemId")
    entry["visualDistance"] = signal.get("visualDistance")
    entry["reason"] = signal.get("reason")


def _mergeInventoryMatchSignal(entry: dict, signal: dict) -> None:
    reason = str(signal.get("reason") or "").strip()
    reasons = [str(value).strip() for value in list(entry.get("reasons") or []) if str(value).strip()]
    if reason and reason not in reasons:
        reasons.append(reason)
    entry["reasons"] = reasons[:6]
    entry["matchCount"] = max(1, len(reasons))
    if _inventoryMatchPriority(signal) > _inventoryMatchPriority(_inventoryPrimarySignal(entry)):
        _applyInventoryPrimarySignal(entry, signal)


async def _applyInventoryVisualMatches(
    *,
    flaggedItemsById: dict[int, dict],
    candidateItems: list[dict],
    referenceItemIds: set[int],
    referenceHashes: Optional[dict[int, str]] = None,
) -> dict[str, object]:
    if not _inventoryVisualMatchingEnabled():
        return {"candidateCount": 0, "referenceCount": 0, "matchedCount": 0, "error": None}
    candidateLimit = _inventoryVisualCandidateLimit()
    referenceLimit = _inventoryVisualReferenceLimit()
    if candidateLimit <= 0 or referenceLimit <= 0:
        return {"candidateCount": 0, "referenceCount": 0, "matchedCount": 0, "error": None}

    candidates = [row for row in list(candidateItems or []) if isinstance(row, dict)][:candidateLimit]
    exactReferenceItemIds = {int(value) for value in list(referenceItemIds or set()) if int(value or 0) > 0}
    normalizedReferenceHashes = {
        int(assetId): str(hashValue).strip()
        for assetId, hashValue in dict(referenceHashes or {}).items()
        if int(assetId or 0) > 0 and str(hashValue).strip()
    }
    referenceIds = sorted(normalizedReferenceHashes.keys())[:referenceLimit]
    if normalizedReferenceHashes and referenceIds:
        normalizedReferenceHashes = {
            int(referenceId): normalizedReferenceHashes[int(referenceId)]
            for referenceId in referenceIds
        }
    referenceError = None
    if not normalizedReferenceHashes:
        referenceIds = sorted(int(value) for value in list(referenceItemIds or set()) if int(value or 0) > 0)[:referenceLimit]
        if not candidates or not referenceIds:
            return {"candidateCount": len(candidates), "referenceCount": len(referenceIds), "matchedCount": 0, "error": None}
        normalizedReferenceHashes, referenceError = await fetchRobloxAssetThumbnailHashes(referenceIds)
        referenceIds = sorted(normalizedReferenceHashes.keys())[:referenceLimit]
    if not candidates or not referenceIds:
        return {"candidateCount": len(candidates), "referenceCount": len(referenceIds), "matchedCount": 0, "error": referenceError}

    referenceCategories, referenceCategoryError = await _referenceVisualCategories(referenceIds)
    candidateIds = [int(row.get("id") or 0) for row in candidates if int(row.get("id") or 0) > 0]
    candidateHashes, candidateError = await fetchRobloxAssetThumbnailHashes(candidateIds)
    if not normalizedReferenceHashes or not candidateHashes:
        combinedErrors = [value for value in (referenceError, referenceCategoryError, candidateError) if value]
        return {
            "candidateCount": len(candidates),
            "referenceCount": len(referenceIds),
            "matchedCount": 0,
            "error": "; ".join(combinedErrors[:3]) or None,
        }

    distanceMax = _inventoryVisualHashDistanceMax()
    matchedCount = 0
    skippedTypeMismatchCount = 0
    skippedUnknownTypeCount = 0
    for candidate in candidates:
        assetId = int(candidate.get("id") or 0)
        if assetId <= 0:
            continue
        candidateCategory = str(candidate.get("visualCategory") or "").strip()
        if not candidateCategory:
            skippedUnknownTypeCount += 1
            continue
        candidateHash = candidateHashes.get(assetId)
        if not candidateHash:
            continue
        bestReferenceId = 0
        bestDistance: Optional[int] = None
        for referenceId, referenceHash in normalizedReferenceHashes.items():
            if int(referenceId) == assetId and assetId in exactReferenceItemIds:
                continue
            referenceCategory = referenceCategories.get(int(referenceId), "")
            if not _visualCategoriesCompatible(candidateCategory, referenceCategory):
                if referenceCategory:
                    skippedTypeMismatchCount += 1
                else:
                    skippedUnknownTypeCount += 1
                continue
            distance = _imageHashDistance(candidateHash, referenceHash)
            if distance is None or distance > distanceMax:
                continue
            if bestDistance is None or distance < bestDistance:
                bestDistance = distance
                bestReferenceId = int(referenceId)
        if bestDistance is None or bestReferenceId <= 0:
            continue
        signal = {
            "matchType": "visual",
            "matchMode": "thumbnail_hash",
            "matchedField": "thumbnail",
            "referenceItemId": int(bestReferenceId),
            "visualDistance": int(bestDistance),
            "reason": f"Thumbnail looked visually similar to flagged item `{int(bestReferenceId)}` (distance {int(bestDistance)}).",
        }
        existing = flaggedItemsById.get(assetId)
        if existing is None:
            existing = dict(candidate)
            existing["matchCount"] = 0
            existing["reasons"] = []
            _applyInventoryPrimarySignal(existing, signal)
            flaggedItemsById[assetId] = existing
        _mergeInventoryMatchSignal(existing, signal)
        matchedCount += 1

    combinedErrors = [value for value in (referenceError, referenceCategoryError, candidateError) if value]
    return {
        "candidateCount": len(candidates),
        "referenceCount": len(referenceIds),
        "matchedCount": matchedCount,
        "skippedTypeMismatchCount": skippedTypeMismatchCount,
        "skippedUnknownTypeCount": skippedUnknownTypeCount,
        "error": "; ".join(combinedErrors[:3]) or None,
    }

appendInventoryVisualCandidate = _appendInventoryVisualCandidate
applyInventoryVisualMatches = _applyInventoryVisualMatches
