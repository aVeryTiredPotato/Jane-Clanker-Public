from __future__ import annotations

from typing import Optional

from features.staff.sessions.Roblox import robloxTransport
from features.staff.sessions.Roblox.robloxModels import RobloxOutfitThumbnailsResult, RobloxOutfitsResult

_cacheGet = robloxTransport.cacheGet
_cacheSet = robloxTransport.cacheSet
_requestJson = robloxTransport.requestJson

async def fetchRobloxUserOutfits(
    robloxUserId: int,
    maxOutfits: int = 0,
    page: int = 1,
    itemsPerPage: int = 50,
    editableOnly: bool = True,
    maxPages: int = 20,
) -> RobloxOutfitsResult:
    normalizedMaxOutfits = int(maxOutfits or 0)
    if normalizedMaxOutfits < 0:
        normalizedMaxOutfits = 0

    itemsPerPage = max(1, min(int(itemsPerPage or 50), 50))
    if normalizedMaxOutfits > 0:
        itemsPerPage = min(itemsPerPage, normalizedMaxOutfits)
    maxPages = max(1, int(maxPages or 20))
    cacheKey = (int(robloxUserId or 0), normalizedMaxOutfits, int(page or 1), itemsPerPage, bool(editableOnly), maxPages)
    cached = _cacheGet(
        "outfits",
        cacheKey,
        ttlName="robloxOutfitCacheTtlSec",
        defaultTtlSec=3600,
    )
    if isinstance(cached, RobloxOutfitsResult):
        return cached
    url = f"https://avatar.roblox.com/v1/users/{robloxUserId}/outfits"
    outfits: list[dict] = []
    seenOutfitIds: set[int] = set()
    currentPage = max(1, int(page or 1))
    lastStatus = 200

    for _ in range(maxPages):
        params = {
            "page": str(currentPage),
            "itemsPerPage": str(itemsPerPage),
        }
        if editableOnly:
            params["isEditable"] = "true"

        try:
            status, data = await _requestJson("GET", url, params=params, timeoutSec=10)
        except Exception as exc:
            return RobloxOutfitsResult(outfits, 0, error=str(exc))

        lastStatus = int(status or 0)
        if status != 200 or not isinstance(data, dict):
            if outfits:
                # Keep partial results if later pages fail.
                return RobloxOutfitsResult(outfits, status)
            return RobloxOutfitsResult([], status, error=f"Outfit lookup failed ({status}).")

        raw = data.get("data") or data.get("outfits") or []
        if not isinstance(raw, list):
            if outfits:
                return RobloxOutfitsResult(outfits, status)
            return RobloxOutfitsResult([], status, error="Outfit lookup returned invalid data.")

        if not raw:
            break

        for entry in raw:
            if not isinstance(entry, dict):
                continue
            outfitId = entry.get("id") or entry.get("outfitId")
            name = entry.get("name")
            try:
                outfitId = int(outfitId) if outfitId is not None else None
            except (TypeError, ValueError):
                outfitId = None
            if outfitId is None or outfitId in seenOutfitIds:
                continue
            seenOutfitIds.add(outfitId)
            outfits.append(
                {
                    "id": outfitId,
                    "name": name,
                    "isEditable": entry.get("isEditable"),
                    "outfitType": entry.get("outfitType"),
                }
            )
            if normalizedMaxOutfits > 0 and len(outfits) >= normalizedMaxOutfits:
                result = RobloxOutfitsResult(outfits, status)
                _cacheSet(
                    "outfits",
                    cacheKey,
                    result,
                    ttlName="robloxOutfitCacheTtlSec",
                    defaultTtlSec=3600,
                )
                return result

        # If this page was short, we've reached the end.
        if len(raw) < itemsPerPage:
            break
        currentPage += 1

    result = RobloxOutfitsResult(outfits, lastStatus)
    _cacheSet(
        "outfits",
        cacheKey,
        result,
        ttlName="robloxOutfitCacheTtlSec",
        defaultTtlSec=3600,
    )
    return result

async def fetchRobloxOutfitThumbnails(
    outfitIds: list[int],
    size: str = "420x420",
    imageFormat: str = "Png",
    isCircular: bool = False,
) -> RobloxOutfitThumbnailsResult:
    if not outfitIds:
        return RobloxOutfitThumbnailsResult([], 200)

    url = "https://thumbnails.roblox.com/v1/users/outfits"
    params = {
        "userOutfitIds": ",".join(str(oid) for oid in outfitIds),
        "size": size,
        "format": imageFormat,
        "isCircular": str(isCircular).lower(),
    }
    try:
        status, data = await _requestJson("GET", url, params=params, timeoutSec=10)
    except Exception as exc:
        return RobloxOutfitThumbnailsResult([], 0, error=str(exc))

    if status != 200 or not isinstance(data, dict):
        return RobloxOutfitThumbnailsResult([], status, error=f"Outfit thumbnail lookup failed ({status}).")

    raw = data.get("data")
    if not isinstance(raw, list):
        return RobloxOutfitThumbnailsResult([], status, error="Outfit thumbnail lookup returned invalid data.")

    thumbs: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        targetId = entry.get("targetId")
        try:
            targetId = int(targetId) if targetId is not None else None
        except (TypeError, ValueError):
            targetId = None
        if targetId is None:
            continue
        thumbs.append(
            {
                "id": targetId,
                "imageUrl": entry.get("imageUrl"),
                "state": entry.get("state"),
            }
        )

    return RobloxOutfitThumbnailsResult(thumbs, status)
