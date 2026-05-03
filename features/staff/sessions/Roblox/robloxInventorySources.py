from __future__ import annotations

import asyncio
from typing import Optional

import config
from features.staff.sessions.Roblox import robloxTransport

_requestJson = robloxTransport.requestJson

_PUBLIC_INVENTORY_ASSET_TYPE_IDS: tuple[int, ...] = (
    1, 2, 3, 4, 5, 8, 9, 10, 11, 12, 13, 17, 18, 19, 24,
    27, 28, 29, 30, 31, 32, 38, 40, 41, 42, 43, 44, 45,
    46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 61, 62,
    64, 65, 66, 67, 68, 69, 70, 71, 72, 76, 77,
)


def _publicInventoryAssetTypeIds() -> tuple[int, ...]:
    configured = getattr(config, "bgIntelligencePublicInventoryAssetTypeIds", None)
    if isinstance(configured, (list, tuple, set)):
        values: list[int] = []
        for value in configured:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in {21, 34}:
                values.append(parsed)
        if values:
            return tuple(values)
    return _PUBLIC_INVENTORY_ASSET_TYPE_IDS


async def _fetchPublicInventoryAssetType(
    robloxUserId: int,
    assetTypeId: int,
    *,
    maxPages: int,
) -> tuple[list[dict], int, Optional[str], bool]:
    url = f"https://inventory.roblox.com/v2/users/{int(robloxUserId)}/inventory/{int(assetTypeId)}"
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
    rows: list[dict] = []
    status = 200
    complete = True
    try:
        for _ in range(max(1, int(maxPages or 1))):
            params = {"limit": "100", "sortOrder": "Desc"}
            if cursor:
                params["cursor"] = cursor
            status, data = await _requestJson("GET", url, headers=headers, params=params, timeoutSec=10)
            if status != 200 or not isinstance(data, dict):
                detail = None
                if isinstance(data, dict):
                    detail = data.get("message") or data.get("error")
                return rows, int(status or 0), str(detail or f"Inventory asset type lookup failed ({status})."), False
            rawRows = data.get("data")
            if not isinstance(rawRows, list):
                return rows, int(status or 0), "Inventory asset type lookup returned invalid data.", False
            for raw in rawRows:
                if isinstance(raw, dict):
                    raw = dict(raw)
                    raw.setdefault("assetTypeId", int(assetTypeId))
                    rows.append(raw)
            cursor = data.get("nextPageCursor") or data.get("nextCursor")
            if not cursor:
                complete = True
                break
        else:
            complete = not bool(cursor)
    except Exception as exc:
        return rows, 0, str(exc), False
    return rows, int(status or 200), None, complete


async def _fetchPublicInventoryAssets(
    robloxUserId: int,
    *,
    maxPagesPerType: int,
) -> tuple[list[dict], int, Optional[str], bool]:
    rows: list[dict] = []
    errors: list[str] = []
    statuses: list[int] = []
    complete = True
    semaphore = asyncio.Semaphore(8)

    async def _fetch(assetTypeId: int) -> tuple[list[dict], int, Optional[str], bool]:
        async with semaphore:
            return await _fetchPublicInventoryAssetType(
                robloxUserId,
                assetTypeId,
                maxPages=maxPagesPerType,
            )

    results = await asyncio.gather(
        *[_fetch(assetTypeId) for assetTypeId in _publicInventoryAssetTypeIds()],
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, Exception):
            errors.append(str(result))
            complete = False
            continue
        typeRows, status, error, typeComplete = result
        statuses.append(int(status or 0))
        rows.extend(typeRows)
        complete = complete and bool(typeComplete)
        if error and int(status or 0) not in {400, 404}:
            errors.append(error)
    if rows:
        return rows, 200, "; ".join(errors[:3]) or None, complete
    status = max(statuses) if statuses else 0
    return rows, status, "; ".join(errors[:3]) or None, complete

fetchPublicInventoryAssetType = _fetchPublicInventoryAssetType
fetchPublicInventoryAssets = _fetchPublicInventoryAssets
