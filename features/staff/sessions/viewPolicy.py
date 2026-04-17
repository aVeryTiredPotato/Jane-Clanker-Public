from __future__ import annotations

from typing import Optional

import discord

import config
from runtime import orgProfiles


def _toPositiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _collectRoleIds(rawValue: object) -> set[int]:
    values = rawValue if isinstance(rawValue, (list, tuple, set)) else [rawValue]
    roleIds: set[int] = set()
    for raw in values:
        parsed = _toPositiveInt(raw)
        if parsed > 0:
            roleIds.add(parsed)
    return roleIds


def hasModPerm(member: discord.Member) -> bool:
    guildId = int(getattr(getattr(member, "guild", None), "id", 0) or 0)
    roleIds: set[int] = set()
    for raw in (
        orgProfiles.getOrganizationValue(
            config,
            "moderatorRoleId",
            guildId=guildId,
            default=None,
        ),
        orgProfiles.getOrganizationValue(
            config,
            "bgReviewModeratorRoleId",
            guildId=guildId,
            default=None,
        ),
    ):
        roleIds.update(_collectRoleIds(raw))

    minorReviewGuildId = _toPositiveInt(
        orgProfiles.getOrganizationValue(
            config,
            "bgCheckMinorReviewGuildId",
            guildId=guildId,
            default=0,
        )
    )
    if guildId > 0 and minorReviewGuildId > 0 and guildId == minorReviewGuildId:
        roleIds.update(
            _collectRoleIds(
                orgProfiles.getOrganizationValue(
                    config,
                    "bgCheckMinorReviewRoleId",
                    guildId=guildId,
                    default=0,
                )
            )
        )
        roleIds.update(
            _collectRoleIds(
                orgProfiles.getOrganizationValue(
                    config,
                    "bgCheckMinorReviewRoleIds",
                    guildId=guildId,
                    default=[],
                )
            )
        )

    if not roleIds:
        return True
    return any(int(role.id) in roleIds for role in member.roles)


def resolveBgQueuePingRoleId(channel: object) -> int:
    guildId = 0
    if isinstance(channel, discord.Thread):
        parentGuild = getattr(channel, "guild", None)
        guildId = int(parentGuild.id) if isinstance(parentGuild, discord.Guild) else 0
    elif isinstance(channel, discord.TextChannel):
        guildId = int(channel.guild.id)

    mainGuildId = _toPositiveInt(
        orgProfiles.getOrganizationValue(config, "primaryGuildId", guildId=guildId, default=getattr(config, "serverId", 0))
    )

    mainRoleId = _toPositiveInt(orgProfiles.getOrganizationValue(config, "moderatorRoleId", guildId=guildId, default=0))

    reviewRoleId = _toPositiveInt(orgProfiles.getOrganizationValue(config, "bgReviewModeratorRoleId", guildId=guildId, default=0))
    adultReviewGuildId = _toPositiveInt(orgProfiles.getOrganizationValue(config, "bgCheckAdultReviewGuildId", guildId=guildId, default=0))
    minorReviewGuildId = _toPositiveInt(orgProfiles.getOrganizationValue(config, "bgCheckMinorReviewGuildId", guildId=guildId, default=0))
    minorReviewRoleId = _toPositiveInt(orgProfiles.getOrganizationValue(config, "bgCheckMinorReviewRoleId", guildId=guildId, default=0))
    minorReviewRoleIds = sorted(
        _collectRoleIds(
            orgProfiles.getOrganizationValue(config, "bgCheckMinorReviewRoleIds", guildId=guildId, default=[])
        )
    )
    if minorReviewRoleId <= 0 and minorReviewRoleIds:
        minorReviewRoleId = minorReviewRoleIds[0]

    if guildId > 0 and mainGuildId > 0 and guildId == mainGuildId:
        return mainRoleId if mainRoleId > 0 else reviewRoleId
    if guildId > 0 and adultReviewGuildId > 0 and guildId == adultReviewGuildId:
        return reviewRoleId if reviewRoleId > 0 else mainRoleId
    if guildId > 0 and minorReviewGuildId > 0 and guildId == minorReviewGuildId:
        return minorReviewRoleId
    return reviewRoleId if reviewRoleId > 0 else mainRoleId


def sessionGuild(
    bot: discord.Client,
    session: Optional[dict],
    fallback: Optional[discord.Guild],
) -> Optional[discord.Guild]:
    if isinstance(session, dict):
        try:
            guildId = int(session.get("guildId") or 0)
        except (TypeError, ValueError):
            guildId = 0
        if guildId > 0:
            resolved = bot.get_guild(guildId)
            if resolved is not None:
                return resolved
    return fallback


def canClockIn(member: discord.Member) -> bool:
    roleId = orgProfiles.getOrganizationValue(
        config,
        "newApplicantRoleId",
        guildId=int(getattr(getattr(member, "guild", None), "id", 0) or 0),
        default=None,
    )
    if not roleId:
        return True
    return any(role.id == roleId for role in member.roles)


def clockInDeniedMessage() -> str:
    return (
        "Clock-in is restricted to members who still hold the New Applicant role. "
        "Your account appears to have already completed orientation."
        "If you have not completed an orientation, please create a ticket so our staff may correct this error."
    )


def robloxGroupUrl(guildId: int | None = None) -> str:
    groupUrl = orgProfiles.getOrganizationValue(
        config,
        "robloxGroupUrl",
        guildId=int(guildId or 0),
        default="",
    ) or ""
    if groupUrl:
        return groupUrl
    groupId = orgProfiles.getOrganizationValue(
        config,
        "robloxGroupId",
        guildId=int(guildId or 0),
        default=0,
    )
    if groupId:
        return f"https://www.roblox.com/groups/{groupId}"
    return "https://www.roblox.com/communities/"
