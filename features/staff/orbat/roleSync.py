from __future__ import annotations

from typing import Any, Optional

import discord

import config
from features.staff.departmentOrbat import sheets as departmentOrbatSheets
from features.staff.recruitment import sheets as recruitmentSheets
from runtime import taskBudgeter
from features.staff.sessions.Roblox import robloxUsers


def _asInt(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _memberHasRoleId(member: discord.Member, roleId: int) -> bool:
    if roleId <= 0:
        return False
    return any(role.id == roleId for role in member.roles)


def _mappingList() -> list[dict[str, Any]]:
    raw = getattr(config, "roleOrbatSyncMappings", None)
    if isinstance(raw, list):
        out = [item for item in raw if isinstance(item, dict)]
        if out:
            return out

    # Backward-compatible fallback.
    return [
        {
            "syncType": "recruitment.anrorsPlacement",
            "enabled": True,
            "memberRoleId": _asInt(getattr(config, "anrorsMemberRoleId", 0), 0),
            "rmPlusRoleId": _asInt(getattr(config, "anrorsRmPlusRoleId", 0), 0),
            "requireAnyRole": True,
            "organizeAfter": True,
        },
        {
            "syncType": "department.anrdRankByRole",
            "enabled": True,
            "divisionKey": "ANRD",
            "roleRankMap": {
                _asInt(getattr(config, "anrdRoleDevelopmentProjectLeadId", 0), 0): "Development Project Lead",
                _asInt(getattr(config, "anrdRoleSeniorDeveloperId", 0), 0): "Senior Developer",
                _asInt(getattr(config, "anrdRoleDeveloperId", 0), 0): "Developer",
                _asInt(getattr(config, "anrdRoleContributorId", 0), 0): "Contributor",
                _asInt(getattr(config, "anrdRoleProbationaryId", 0), 0): "Probationary",
            },
            "rolePriority": [
                _asInt(getattr(config, "anrdRoleDevelopmentProjectLeadId", 0), 0),
                _asInt(getattr(config, "anrdRoleSeniorDeveloperId", 0), 0),
                _asInt(getattr(config, "anrdRoleDeveloperId", 0), 0),
                _asInt(getattr(config, "anrdRoleContributorId", 0), 0),
                _asInt(getattr(config, "anrdRoleProbationaryId", 0), 0),
            ],
            "requireMappedRole": True,
            "organizeAfter": True,
            "fundingRoleId": _asInt(getattr(config, "anrdFundingBenefactorsRoleId", 0), 0),
        },
    ]


async def _syncAnrorsPlacement(
    member: discord.Member,
    guildId: int,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    memberRoleId = _asInt(mapping.get("memberRoleId"), 0)
    rmPlusRoleId = _asInt(mapping.get("rmPlusRoleId"), 0)
    requireAnyRole = bool(mapping.get("requireAnyRole", True))
    organizeAfter = bool(mapping.get("organizeAfter", True))

    hasMemberRole = _memberHasRoleId(member, memberRoleId)
    hasRmPlusRole = _memberHasRoleId(member, rmPlusRoleId)
    if requireAnyRole and not hasMemberRole and not hasRmPlusRole:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no-relevant-roles",
            "syncType": "recruitment.anrorsPlacement",
        }

    roverResult = await robloxUsers.fetchRobloxUser(member.id, guildId)
    robloxUsername = str(roverResult.robloxUsername or "").strip()
    if not robloxUsername:
        return {
            "ok": False,
            "syncType": "recruitment.anrorsPlacement",
            "reason": str(roverResult.error or "missing-roblox-username"),
        }

    sheetResult = await taskBudgeter.runBackgroundSheetsThread(
        recruitmentSheets.syncRecruitmentRolePlacement,
        robloxUsername,
        hasMemberRole,
        hasRmPlusRole,
        organizeAfter,
    )
    if not isinstance(sheetResult, dict):
        return {
            "ok": False,
            "syncType": "recruitment.anrorsPlacement",
            "reason": "invalid-sheet-response",
        }
    sheetResult["syncType"] = "recruitment.anrorsPlacement"
    sheetResult["robloxUsername"] = robloxUsername
    sheetResult["memberId"] = member.id
    return sheetResult


def _resolveTargetRankFromRoles(member: discord.Member, mapping: dict[str, Any]) -> tuple[Optional[str], Optional[int]]:
    rawRoleRankMap = mapping.get("roleRankMap")
    if not isinstance(rawRoleRankMap, dict):
        return None, None

    roleRankMap: dict[int, str] = {}
    for rawRoleId, rawRank in rawRoleRankMap.items():
        roleId = _asInt(rawRoleId, 0)
        rankName = str(rawRank or "").strip()
        if roleId <= 0 or not rankName:
            continue
        roleRankMap[roleId] = rankName
    if not roleRankMap:
        return None, None

    rawPriority = mapping.get("rolePriority")
    if isinstance(rawPriority, list) and rawPriority:
        for rawRoleId in rawPriority:
            roleId = _asInt(rawRoleId, 0)
            if roleId <= 0:
                continue
            if _memberHasRoleId(member, roleId) and roleId in roleRankMap:
                return roleRankMap[roleId], roleId

    for roleId, rankName in roleRankMap.items():
        if _memberHasRoleId(member, roleId):
            return rankName, roleId
    return None, None


async def _syncDepartmentRankByRole(
    member: discord.Member,
    guildId: int,
    mapping: dict[str, Any],
) -> dict[str, Any]:
    divisionKey = str(mapping.get("divisionKey") or "").strip() or "ANRD"
    organizeAfter = bool(mapping.get("organizeAfter", True))
    requireMappedRole = bool(mapping.get("requireMappedRole", True))
    targetRank, matchedRoleId = _resolveTargetRankFromRoles(member, mapping)
    if requireMappedRole and not targetRank:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no-mapped-role",
            "syncType": "department.anrdRankByRole",
            "divisionKey": divisionKey,
        }

    roverResult = await robloxUsers.fetchRobloxUser(member.id, guildId)
    robloxUsername = str(roverResult.robloxUsername or "").strip()
    if not robloxUsername:
        return {
            "ok": False,
            "syncType": "department.anrdRankByRole",
            "divisionKey": divisionKey,
            "reason": str(roverResult.error or "missing-roblox-username"),
        }

    if not targetRank:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no-target-rank",
            "syncType": "department.anrdRankByRole",
            "divisionKey": divisionKey,
            "robloxUsername": robloxUsername,
        }

    fundingRoleId = _asInt(mapping.get("fundingRoleId"), 0)
    hasFundingRole = _memberHasRoleId(member, fundingRoleId)

    sheetResult = await taskBudgeter.runBackgroundSheetsThread(
        departmentOrbatSheets.syncDivisionMemberRankByRobloxUsername,
        divisionKey,
        robloxUsername,
        targetRank,
        organizeAfter,
    )
    if not isinstance(sheetResult, dict):
        return {
            "ok": False,
            "syncType": "department.anrdRankByRole",
            "divisionKey": divisionKey,
            "reason": "invalid-sheet-response",
        }
    sheetResult["syncType"] = "department.anrdRankByRole"
    sheetResult["robloxUsername"] = robloxUsername
    sheetResult["memberId"] = member.id
    sheetResult["matchedRoleId"] = matchedRoleId
    sheetResult["hasFundingRole"] = hasFundingRole
    return sheetResult


async def syncMemberRoleOrbats(member: discord.Member, guildId: int) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    changed = False
    for mapping in _mappingList():
        if not bool(mapping.get("enabled", True)):
            continue
        syncType = str(mapping.get("syncType") or "").strip().lower()
        if syncType == "recruitment.anrorsplacement":
            result = await _syncAnrorsPlacement(member, guildId, mapping)
        elif syncType == "department.anrdrankbyrole":
            result = await _syncDepartmentRankByRole(member, guildId, mapping)
        else:
            result = {
                "ok": False,
                "syncType": syncType or "unknown",
                "reason": "unsupported-sync-type",
            }
        if isinstance(result, dict) and (
            result.get("moved")
            or result.get("updated")
            or result.get("rankUpdated")
            or result.get("created")
        ):
            changed = True
        results.append(result)
    return {
        "ok": True,
        "changed": changed,
        "results": results,
    }

