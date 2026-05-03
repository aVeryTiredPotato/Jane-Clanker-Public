from __future__ import annotations

from typing import Optional


def optionalInt(value: object) -> Optional[int]:
    try:
        if value is not None:
            return int(value)
    except (TypeError, ValueError):
        return None
    return None


def optionalBool(value: object) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def extractGroupOwner(group: dict) -> tuple[Optional[int], Optional[str]]:
    owner = group.get("owner") if isinstance(group.get("owner"), dict) else None
    if not owner:
        return None, None
    ownerId = optionalInt(owner.get("userId") or owner.get("id"))
    ownerName = owner.get("username") or owner.get("name") or owner.get("displayName")
    return ownerId, ownerName if isinstance(ownerName, str) and ownerName else None


def extractAssetId(item: dict) -> Optional[int]:
    candidates = [
        item.get("assetId"),
        item.get("asset_id"),
        item.get("id"),
    ]
    asset = item.get("asset") if isinstance(item.get("asset"), dict) else None
    if asset:
        candidates.extend([asset.get("id"), asset.get("assetId")])
    assetDetails = item.get("assetDetails") if isinstance(item.get("assetDetails"), dict) else None
    if assetDetails:
        candidates.extend([assetDetails.get("assetId"), assetDetails.get("id")])
    for value in candidates:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def extractAssetName(item: dict) -> Optional[str]:
    for key in ("name", "assetName", "displayName"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return value
    asset = item.get("asset") if isinstance(item.get("asset"), dict) else None
    if asset:
        value = asset.get("name")
        if isinstance(value, str) and value:
            return value
    assetDetails = item.get("assetDetails") if isinstance(item.get("assetDetails"), dict) else None
    if assetDetails:
        for key in ("name", "assetName", "displayName"):
            value = assetDetails.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def extractInventoryItemType(item: dict) -> str:
    candidates: list[object] = [
        item.get("itemType"),
        item.get("inventoryItemType"),
        item.get("type"),
        item.get("assetType"),
        item.get("assetTypeName"),
    ]
    asset = item.get("asset") if isinstance(item.get("asset"), dict) else None
    if asset:
        candidates.extend(
            [
                asset.get("type"),
                asset.get("assetType"),
                asset.get("assetTypeName"),
            ]
        )
    assetDetails = item.get("assetDetails") if isinstance(item.get("assetDetails"), dict) else None
    if assetDetails:
        candidates.extend(
            [
                assetDetails.get("inventoryItemAssetType"),
                assetDetails.get("assetType"),
                assetDetails.get("assetTypeName"),
            ]
        )
    if isinstance(item.get("gamePassDetails"), dict):
        candidates.append("GAME_PASS")
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nestedKey in ("type", "name", "displayName"):
                nested = value.get(nestedKey)
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return ""


def isGamepassInventoryItem(item: dict) -> bool:
    itemType = extractInventoryItemType(item).replace("_", "").replace("-", "").replace(" ", "").lower()
    if "gamepass" in itemType:
        return True
    for key in ("gamePassId", "gamepassId", "game_pass_id", "passId"):
        if item.get(key) is not None:
            return True
    gamepass = item.get("gamePass") if isinstance(item.get("gamePass"), dict) else None
    gamepassDetails = item.get("gamePassDetails") if isinstance(item.get("gamePassDetails"), dict) else None
    return bool(gamepass or gamepassDetails)


def extractGamepassId(item: dict) -> Optional[int]:
    candidates = [
        item.get("gamePassId"),
        item.get("gamepassId"),
        item.get("game_pass_id"),
        item.get("passId"),
    ]
    gamepass = item.get("gamePass") if isinstance(item.get("gamePass"), dict) else None
    if gamepass:
        candidates.extend([gamepass.get("id"), gamepass.get("gamePassId")])
    gamepassDetails = item.get("gamePassDetails") if isinstance(item.get("gamePassDetails"), dict) else None
    if gamepassDetails:
        candidates.extend([gamepassDetails.get("id"), gamepassDetails.get("gamePassId")])
    if isGamepassInventoryItem(item):
        candidates.append(extractAssetId(item))
    for value in candidates:
        try:
            if value is not None:
                parsed = int(value)
                if parsed > 0:
                    return parsed
        except (TypeError, ValueError):
            continue
    return None


def extractRobuxPrice(item: dict) -> Optional[int]:
    for key in ("price", "priceInRobux", "PriceInRobux", "lowestPrice", "lowestPriceInRobux"):
        value = item.get(key)
        try:
            if value is not None:
                parsed = int(value)
                if parsed >= 0:
                    return parsed
        except (TypeError, ValueError):
            continue
    product = item.get("product") if isinstance(item.get("product"), dict) else None
    if product:
        return extractRobuxPrice(product)
    return None


def extractCreatorId(item: dict) -> Optional[int]:
    candidates = [
        item.get("creatorId"),
        item.get("creator_id"),
        item.get("creatorTargetId"),
        item.get("CreatorId"),
        item.get("CreatorTargetId"),
    ]
    creator = None
    if isinstance(item.get("creator"), dict):
        creator = item.get("creator")
    elif isinstance(item.get("Creator"), dict):
        creator = item.get("Creator")
    if creator:
        candidates.extend(
            [
                creator.get("id"),
                creator.get("creatorId"),
                creator.get("creatorTargetId"),
                creator.get("Id"),
                creator.get("CreatorId"),
                creator.get("CreatorTargetId"),
                creator.get("userId"),
                creator.get("UserId"),
            ]
        )
    for value in candidates:
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def extractCreatorType(item: dict) -> Optional[str]:
    candidates = [
        item.get("creatorType"),
        item.get("creator_type"),
        item.get("CreatorType"),
    ]
    creator = None
    if isinstance(item.get("creator"), dict):
        creator = item.get("creator")
    elif isinstance(item.get("Creator"), dict):
        creator = item.get("Creator")
    if creator:
        candidates.extend(
            [
                creator.get("type"),
                creator.get("creatorType"),
                creator.get("CreatorType"),
            ]
        )
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def extractCreatorName(item: dict) -> Optional[str]:
    creator = None
    if isinstance(item.get("creator"), dict):
        creator = item.get("creator")
    elif isinstance(item.get("Creator"), dict):
        creator = item.get("Creator")
    if creator:
        value = creator.get("name") or creator.get("Name")
        if isinstance(value, str) and value:
            return value
    return None


def extractGamepassName(item: dict) -> Optional[str]:
    for key in ("name", "gamePassName", "displayName"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    gamepass = item.get("gamePass") if isinstance(item.get("gamePass"), dict) else None
    if gamepass:
        return extractGamepassName(gamepass)
    return None


def extractBadgeId(entry: dict) -> Optional[int]:
    for key in ("id", "badgeId", "badge_id"):
        value = entry.get(key)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    return None
