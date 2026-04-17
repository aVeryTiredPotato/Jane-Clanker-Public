from __future__ import annotations

from typing import Any, Callable, Optional

import discord

from runtime import orgProfiles
from features.staff.sessions.bgBuckets import (
    adultBgReviewBucket,
    bgReviewBucketLabel,
    isMinorAgeGroup,
    minorBgReviewBucket,
)


async def classifyBgReviewBucketForMember(
    member: Optional[discord.Member],
    *,
    configModule: Any,
    resolveOrbatAgeGroup: Callable[[int], Any],
    userId: int,
    guildId: int | None = None,
) -> tuple[str, str]:
    minorRoleIds: set[int] = set()
    adultRoleIds: set[int] = set()
    resolvedGuildId = int(guildId or 0)
    if resolvedGuildId <= 0 and isinstance(member, discord.Member):
        resolvedGuildId = int(getattr(getattr(member, "guild", None), "id", 0) or 0)

    minorRoleConfig = orgProfiles.getOrganizationValue(
        configModule,
        "bgMinorAgeRoleIds",
        guildId=resolvedGuildId,
        default=[],
    )
    for rawRoleId in list(minorRoleConfig or []):
        try:
            parsedRoleId = int(rawRoleId)
        except (TypeError, ValueError):
            parsedRoleId = 0
        if parsedRoleId > 0:
            minorRoleIds.add(parsedRoleId)
    majorRoleConfig = orgProfiles.getOrganizationValue(
        configModule,
        "bgMajorAgeRoleIds",
        guildId=resolvedGuildId,
        default=None,
    )
    if majorRoleConfig is None:
        majorRoleConfig = orgProfiles.getOrganizationValue(
            configModule,
            "bgAdultAgeRoleIds",
            guildId=resolvedGuildId,
            default=[],
        )
    for rawRoleId in list(majorRoleConfig or []):
        try:
            parsedRoleId = int(rawRoleId)
        except (TypeError, ValueError):
            parsedRoleId = 0
        if parsedRoleId > 0:
            adultRoleIds.add(parsedRoleId)

    if isinstance(member, discord.Member):
        memberRoleIds = {int(role.id) for role in list(member.roles or [])}
        if minorRoleIds and memberRoleIds.intersection(minorRoleIds):
            return minorBgReviewBucket, "role"
        if adultRoleIds and memberRoleIds.intersection(adultRoleIds):
            return adultBgReviewBucket, "role"

    ageGroup = await resolveOrbatAgeGroup(int(userId))
    normalizedMinorAgeGroups = list(
        orgProfiles.getOrganizationValue(
            configModule,
            "bgMinorAgeGroups",
            guildId=resolvedGuildId,
            default=["13-15", "16-17"],
        )
        or ["13-15", "16-17"]
    )
    if isMinorAgeGroup(ageGroup, normalizedMinorAgeGroups):
        return minorBgReviewBucket, f"orbat:{bgReviewBucketLabel(minorBgReviewBucket)}"

    normalizedAdultAgeGroups = {
        str(value or "").strip().upper()
        for value in list(
            orgProfiles.getOrganizationValue(
                configModule,
                "bgAdultAgeGroups",
                guildId=resolvedGuildId,
                default=["18-20", "21+"],
            )
            or ["18-20", "21+"]
        )
        if str(value or "").strip()
    }
    normalizedAgeGroup = str(ageGroup or "").strip().upper()
    if normalizedAgeGroup and normalizedAgeGroup in normalizedAdultAgeGroups:
        return adultBgReviewBucket, f"orbat:{bgReviewBucketLabel(adultBgReviewBucket)}"

    if bool(
        orgProfiles.getOrganizationValue(
            configModule,
            "bgUnknownDefaultsToMinor",
            guildId=resolvedGuildId,
            default=True,
        )
    ):
        return minorBgReviewBucket, "fallback"
    return adultBgReviewBucket, "fallback"
