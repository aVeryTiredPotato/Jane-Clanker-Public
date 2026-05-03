from __future__ import annotations

import config
from features.staff.sessions.Roblox import robloxPayloads, robloxTransport
from features.staff.sessions.Roblox.robloxModels import RobloxAcceptResult, RobloxGroupsResult


async def acceptJoinRequest(robloxUserId: int) -> RobloxAcceptResult:
    groupId = getattr(config, "robloxGroupId", 0)
    return await acceptJoinRequestForGroup(robloxUserId, int(groupId or 0))


async def acceptJoinRequestForGroup(robloxUserId: int, groupId: int) -> RobloxAcceptResult:
    apiKey = getattr(config, "robloxOpenCloudApiKey", "") or ""
    if not groupId or not apiKey:
        return RobloxAcceptResult(False, 0, error="Missing Roblox Open Cloud configuration.")

    url = f"https://apis.roblox.com/cloud/v2/groups/{groupId}/join-requests/{robloxUserId}:accept"
    headers = {"x-api-key": apiKey}

    try:
        # Roblox's accept endpoint expects JSON content-type.
        status, payload = await robloxTransport.requestJson(
            "POST",
            url,
            headers=headers,
            timeoutSec=10,
            jsonBody={},
        )
        if 200 <= status < 300:
            return RobloxAcceptResult(True, status)
    except Exception as exc:
        return RobloxAcceptResult(False, 0, error=str(exc))

    errorMsg = None
    if isinstance(payload, dict):
        errorMsg = payload.get("message") or payload.get("error")
        if not errorMsg and isinstance(payload.get("errors"), list) and payload["errors"]:
            errorMsg = payload["errors"][0].get("message")
    if not errorMsg:
        errorMsg = f"Roblox API error ({status})."

    return RobloxAcceptResult(False, status, error=errorMsg)


async def fetchRobloxGroups(robloxUserId: int) -> RobloxGroupsResult:
    cacheKey = int(robloxUserId or 0)
    cached = robloxTransport.cacheGet(
        "groups",
        cacheKey,
        ttlName="robloxGroupCacheTtlSec",
        defaultTtlSec=3600,
    )
    if isinstance(cached, RobloxGroupsResult):
        return cached

    url = f"https://groups.roblox.com/v2/users/{robloxUserId}/groups/roles"
    try:
        status, data = await robloxTransport.requestJson("GET", url, timeoutSec=10)
    except Exception as exc:
        return RobloxGroupsResult([], 0, error=str(exc))

    if status != 200 or not isinstance(data, dict):
        return RobloxGroupsResult([], status, error=f"Roblox groups lookup failed ({status}).")

    rawGroups = data.get("data", [])
    if not isinstance(rawGroups, list):
        return RobloxGroupsResult([], status, error="Roblox groups lookup returned invalid data.")

    groups: list[dict] = []
    for entry in rawGroups:
        group = entry.get("group") if isinstance(entry, dict) else None
        role = entry.get("role") if isinstance(entry, dict) else None
        if not isinstance(group, dict):
            continue
        groupId = robloxPayloads.optionalInt(group.get("id"))
        groupName = group.get("name")
        roleName = role.get("name") if isinstance(role, dict) else None
        roleId = robloxPayloads.optionalInt(role.get("id")) if isinstance(role, dict) else None
        rank = robloxPayloads.optionalInt(role.get("rank")) if isinstance(role, dict) else None
        ownerId, ownerName = robloxPayloads.extractGroupOwner(group)
        groups.append(
            {
                "id": groupId,
                "name": groupName,
                "memberCount": robloxPayloads.optionalInt(group.get("memberCount")),
                "hasVerifiedBadge": robloxPayloads.optionalBool(group.get("hasVerifiedBadge")),
                "isLocked": robloxPayloads.optionalBool(group.get("isLocked")),
                "publicEntryAllowed": robloxPayloads.optionalBool(group.get("publicEntryAllowed")),
                "ownerId": ownerId,
                "ownerName": ownerName,
                "roleId": roleId,
                "role": roleName,
                "rank": rank,
            }
        )

    result = RobloxGroupsResult(groups, status)
    robloxTransport.cacheSet(
        "groups",
        cacheKey,
        result,
        ttlName="robloxGroupCacheTtlSec",
        defaultTtlSec=3600,
    )
    return result
