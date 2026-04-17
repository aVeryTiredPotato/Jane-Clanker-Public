from __future__ import annotations

from typing import Any

from runtime import orgProfiles


def _normalizeFeatureKey(value: object) -> str:
    text = str(value or "").strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or ch in {"-", "_", "."})


def _commandRoot(commandName: object) -> str:
    normalized = _normalizeFeatureKey(commandName)
    if not normalized:
        return ""
    return normalized.split(" ", 1)[0].split(".", 1)[0]


def _tokenRoot(token: object) -> str:
    text = str(token or "").strip().lower()
    while text[:1] in {"!", "?", "/", ":"}:
        text = text[1:]
    return _commandRoot(text)


def _featureMap(configModule: Any) -> dict[str, str]:
    rawMap = getattr(configModule, "organizationCommandFeatureMap", {}) or {}
    out: dict[str, str] = {}
    if not isinstance(rawMap, dict):
        return out
    for rawKey, rawValue in rawMap.items():
        normalizedKey = _normalizeFeatureKey(rawKey)
        normalizedValue = _normalizeFeatureKey(rawValue)
        if normalizedKey and normalizedValue:
            out[normalizedKey] = normalizedValue
    return out


def featureKeyForCommand(configModule: Any, commandName: object) -> str:
    root = _commandRoot(commandName)
    return _featureMap(configModule).get(root, "")


def featureKeyForToken(configModule: Any, token: object) -> str:
    root = _tokenRoot(token)
    return _featureMap(configModule).get(root, "")


def _enabledFeaturesForOrg(configModule: Any, orgKey: str) -> set[str]:
    profile = orgProfiles.getOrganizationProfile(configModule, orgKey=orgKey)
    if profile is None:
        return set()
    rawFeatures = profile.values.get("enabledFeatures")
    if not isinstance(rawFeatures, (list, tuple, set)):
        return set()
    return {
        _normalizeFeatureKey(value)
        for value in list(rawFeatures)
        if _normalizeFeatureKey(value)
    }


def isFeatureEnabledForGuild(configModule: Any, guildId: int, featureKey: object) -> bool:
    normalizedFeatureKey = _normalizeFeatureKey(featureKey)
    if not normalizedFeatureKey:
        return True
    try:
        parsedGuildId = int(guildId)
    except (TypeError, ValueError):
        parsedGuildId = 0
    if parsedGuildId <= 0:
        return True
    if not orgProfiles.isGuildAssignedToOrganization(configModule, parsedGuildId):
        return False
    orgKey = orgProfiles.getOrganizationKeyForGuild(configModule, parsedGuildId)
    enabledFeatures = _enabledFeaturesForOrg(configModule, orgKey)
    if not enabledFeatures:
        return False
    return normalizedFeatureKey in enabledFeatures


def isCommandEnabledForGuild(configModule: Any, guildId: int, commandName: object) -> tuple[bool, str]:
    featureKey = featureKeyForCommand(configModule, commandName)
    if not featureKey:
        return True, ""
    return isFeatureEnabledForGuild(configModule, guildId, featureKey), featureKey


def isTokenEnabledForGuild(configModule: Any, guildId: int, token: object) -> tuple[bool, str]:
    featureKey = featureKeyForToken(configModule, token)
    if not featureKey:
        return True, ""
    return isFeatureEnabledForGuild(configModule, guildId, featureKey), featureKey
