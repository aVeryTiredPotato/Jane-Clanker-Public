from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _normalizeOrgKey(value: object) -> str:
    text = str(value or "").strip().upper()
    return text or "DEFAULT"


def _toPositiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _normalizeIdList(values: object) -> tuple[int, ...]:
    out: list[int] = []
    for rawValue in list(values or []):
        parsedValue = _toPositiveInt(rawValue)
        if parsedValue > 0 and parsedValue not in out:
            out.append(parsedValue)
    return tuple(out)


@dataclass(slots=True, frozen=True)
class OrganizationProfile:
    key: str
    label: str
    primaryGuildId: int
    guildIds: tuple[int, ...]
    values: dict[str, Any]


def _buildProfile(configModule: Any, key: str, rawProfile: object) -> OrganizationProfile:
    profileData = dict(rawProfile or {}) if isinstance(rawProfile, dict) else {}
    label = str(profileData.get("label") or key).strip() or key
    primaryGuildId = _toPositiveInt(profileData.get("primaryGuildId")) or _toPositiveInt(
        getattr(configModule, "serverId", 0)
    )
    guildIds = list(_normalizeIdList(profileData.get("guildIds") or []))
    if primaryGuildId > 0 and primaryGuildId not in guildIds:
        guildIds.insert(0, primaryGuildId)
    return OrganizationProfile(
        key=key,
        label=label,
        primaryGuildId=primaryGuildId,
        guildIds=tuple(guildIds),
        values=profileData,
    )


def _legacyProfiles(configModule: Any) -> dict[str, OrganizationProfile]:
    defaultKey = _normalizeOrgKey(getattr(configModule, "defaultOrganizationKey", "DEFAULT"))
    rawProfile = {
        "label": defaultKey,
        "primaryGuildId": getattr(configModule, "serverId", 0),
        "guildIds": list(getattr(configModule, "allowedCommandGuildIds", []) or []),
    }
    return {defaultKey: _buildProfile(configModule, defaultKey, rawProfile)}


def getOrganizationProfiles(configModule: Any) -> dict[str, OrganizationProfile]:
    rawProfiles = getattr(configModule, "organizationProfiles", None)
    if not isinstance(rawProfiles, dict) or not rawProfiles:
        return _legacyProfiles(configModule)

    profiles: dict[str, OrganizationProfile] = {}
    for rawKey, rawProfile in rawProfiles.items():
        normalizedKey = _normalizeOrgKey(rawKey)
        profiles[normalizedKey] = _buildProfile(configModule, normalizedKey, rawProfile)
    return profiles or _legacyProfiles(configModule)


def getDefaultOrganizationKey(configModule: Any) -> str:
    profiles = getOrganizationProfiles(configModule)
    configuredKey = _normalizeOrgKey(getattr(configModule, "defaultOrganizationKey", ""))
    if configuredKey in profiles:
        return configuredKey
    return next(iter(profiles.keys()), "DEFAULT")


def isGuildAssignedToOrganization(configModule: Any, guildId: object) -> bool:
    profiles = getOrganizationProfiles(configModule)
    parsedGuildId = _toPositiveInt(guildId)
    if parsedGuildId <= 0:
        return False

    rawGuildMap = getattr(configModule, "guildOrganizationKeys", {}) or {}
    explicitKey = rawGuildMap.get(parsedGuildId)
    if explicitKey is None:
        explicitKey = rawGuildMap.get(str(parsedGuildId))
    if _normalizeOrgKey(explicitKey) in profiles:
        return True

    for profile in profiles.values():
        if parsedGuildId in profile.guildIds:
            return True
    return False


def getOrganizationKeyForGuild(configModule: Any, guildId: object) -> str:
    profiles = getOrganizationProfiles(configModule)
    defaultKey = getDefaultOrganizationKey(configModule)
    parsedGuildId = _toPositiveInt(guildId)
    if parsedGuildId <= 0:
        return defaultKey

    rawGuildMap = getattr(configModule, "guildOrganizationKeys", {}) or {}
    explicitKey = rawGuildMap.get(parsedGuildId)
    if explicitKey is None:
        explicitKey = rawGuildMap.get(str(parsedGuildId))
    normalizedExplicitKey = _normalizeOrgKey(explicitKey)
    if normalizedExplicitKey in profiles:
        return normalizedExplicitKey

    for profile in profiles.values():
        if parsedGuildId in profile.guildIds:
            return profile.key

    return defaultKey


def getOrganizationProfile(
    configModule: Any,
    *,
    guildId: object | None = None,
    orgKey: object | None = None,
) -> OrganizationProfile | None:
    profiles = getOrganizationProfiles(configModule)
    if orgKey is not None:
        return profiles.get(_normalizeOrgKey(orgKey))
    if guildId is not None:
        return profiles.get(getOrganizationKeyForGuild(configModule, guildId))
    return profiles.get(getDefaultOrganizationKey(configModule))


def getOrganizationValue(
    configModule: Any,
    key: str,
    *,
    guildId: object | None = None,
    orgKey: object | None = None,
    default: Any = None,
) -> Any:
    profile = getOrganizationProfile(configModule, guildId=guildId, orgKey=orgKey)
    if profile is not None and key in profile.values:
        return profile.values[key]
    if hasattr(configModule, key):
        return getattr(configModule, key)
    return default


def getProfilesWithValue(configModule: Any, key: str) -> list[OrganizationProfile]:
    out: list[OrganizationProfile] = []
    for profile in getOrganizationProfiles(configModule).values():
        value = getOrganizationValue(configModule, key, orgKey=profile.key)
        if value in (None, "", 0, [], (), {}):
            continue
        out.append(profile)
    return out
