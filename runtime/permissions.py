from __future__ import annotations

from functools import lru_cache

import discord

import config


def toPositiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def normalizeRoleIds(raw: object) -> list[int]:
    values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    out: list[int] = []
    for value in values:
        parsed = toPositiveInt(value)
        if parsed > 0 and parsed not in out:
            out.append(parsed)
    return out


def formatRoleIds(roleIds: list[int]) -> str:
    if not roleIds:
        return "none configured"
    return ", ".join(f"`{roleId}`" for roleId in roleIds)


@lru_cache(maxsize=1)
def getCohostAllowedRoleIds() -> set[int]:
    roleIds = getattr(config, "cohostAllowedRoleIds", None)
    if not roleIds:
        return set()
    normalized: set[int] = set()
    for value in roleIds:
        parsed = toPositiveInt(value)
        if parsed > 0:
            normalized.add(parsed)
    return normalized


def hasCohostPermission(member: discord.Member) -> bool:
    allowedRoles = getCohostAllowedRoleIds()
    if not allowedRoles:
        return True
    return any(int(role.id) in allowedRoles for role in member.roles)


@lru_cache(maxsize=1)
def getMiddleHighRankRoleIds() -> set[int]:
    out: set[int] = set()
    for raw in (
        getattr(config, "middleRankRoleId", None),
        getattr(config, "highRankRoleId", None),
    ):
        parsed = toPositiveInt(raw)
        if parsed > 0:
            out.add(parsed)
    return out


def hasMiddleHighRankRole(member: discord.Member) -> bool:
    allowedRoles = getMiddleHighRankRoleIds()
    if not allowedRoles:
        return False
    return any(int(role.id) in allowedRoles for role in member.roles)


def getBgCheckCertifiedRoleIds() -> set[int]:
    out: set[int] = set()
    for raw in (
        getattr(config, "bgCheckCertifiedRoleId", None),
        getattr(config, "bgReviewModeratorRoleId", None),
        getattr(config, "moderatorRoleId", None),
    ):
        parsed = toPositiveInt(raw)
        if parsed > 0:
            out.add(parsed)
    return out


def hasBgCheckCertifiedRole(member: discord.Member) -> bool:
    roleIds = getBgCheckCertifiedRoleIds()
    if not roleIds:
        return False
    return any(int(role.id) in roleIds for role in member.roles)


def hasAdminOrManageGuild(member: discord.Member) -> bool:
    return bool(member.guild_permissions.administrator or member.guild_permissions.manage_guild)


def hasAdministrator(member: discord.Member) -> bool:
    return bool(member.guild_permissions.administrator)


@lru_cache(maxsize=1)
def getTemporaryCommandAllowedUserIds() -> set[int]:
    rawIds = getattr(config, "temporaryCommandAllowedUserIds", [331660652672319488]) or [331660652672319488]
    normalized: set[int] = set()
    for value in rawIds:
        parsed = toPositiveInt(value)
        if parsed > 0:
            normalized.add(parsed)
    return normalized


def isCommandExecutionAllowed(userId: int) -> bool:
    enabled = bool(getattr(config, "temporaryCommandLockEnabled", False))
    if not enabled:
        return True
    allowedIds = getTemporaryCommandAllowedUserIds()
    if not allowedIds:
        return True
    return int(userId) in allowedIds


def clearPermissionCaches() -> None:
    getCohostAllowedRoleIds.cache_clear()
    getMiddleHighRankRoleIds.cache_clear()
    getTemporaryCommandAllowedUserIds.cache_clear()
