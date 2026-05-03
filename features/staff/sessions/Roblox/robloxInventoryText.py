from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Optional

import config
from features.staff.sessions.Roblox import robloxPayloads

_optionalInt = robloxPayloads.optionalInt
_extractAssetId = robloxPayloads.extractAssetId
_extractAssetName = robloxPayloads.extractAssetName
_extractInventoryItemType = robloxPayloads.extractInventoryItemType
_isGamepassInventoryItem = robloxPayloads.isGamepassInventoryItem
_extractGamepassId = robloxPayloads.extractGamepassId
_extractCreatorId = robloxPayloads.extractCreatorId
_extractCreatorName = robloxPayloads.extractCreatorName

_INVENTORY_MATCH_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_INVENTORY_MATCH_REPEAT_RE = re.compile(r"(.)\1{2,}")
_INVENTORY_MATCH_LEET_MAP = str.maketrans(
    {
        "@": "a",
        "$": "s",
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
    }
)


def _inventoryFuzzyMatchingEnabled() -> bool:
    return bool(getattr(config, "bgIntelligenceInventoryFuzzyMatchingEnabled", True))


def _inventoryFuzzyScoreCutoff() -> float:
    try:
        configured = float(getattr(config, "bgIntelligenceInventoryFuzzyScoreCutoff", 92) or 92)
    except (TypeError, ValueError):
        configured = 92.0
    return max(70.0, min(configured, 100.0))


def _inventoryFuzzyMinKeywordLength() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryFuzzyMinKeywordLength", 6) or 6)
    except (TypeError, ValueError):
        configured = 6
    return max(3, min(configured, 64))

def _normalizeInventoryMatchText(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.translate(_INVENTORY_MATCH_LEET_MAP)
    text = _INVENTORY_MATCH_NON_ALNUM_RE.sub(" ", text)
    text = _INVENTORY_MATCH_REPEAT_RE.sub(r"\1\1", text)
    return " ".join(text.split())


def _compactInventoryMatchText(value: object) -> str:
    return _normalizeInventoryMatchText(value).replace(" ", "")


def _inventoryTokenWindows(tokens: list[str], targetTokenCount: int) -> list[str]:
    if not tokens:
        return []
    normalizedTargetCount = max(1, int(targetTokenCount or 1))
    lengths = sorted(
        {
            max(1, normalizedTargetCount - 1),
            normalizedTargetCount,
            min(len(tokens), normalizedTargetCount + 1),
        }
    )
    windows: list[str] = []
    seen: set[str] = set()
    for length in lengths:
        if length <= 0 or length > len(tokens):
            continue
        for start in range(0, len(tokens) - length + 1):
            window = " ".join(tokens[start : start + length])
            if window and window not in seen:
                seen.add(window)
                windows.append(window)
    return windows


def _inventorySimilarityScore(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio() * 100.0


def _bestInventoryPartialKeywordMatch(keyword: str, candidate: str) -> tuple[float, str]:
    if not keyword or not candidate:
        return 0.0, ""
    candidateTokens = [token for token in candidate.split() if token]
    keywordTokens = [token for token in keyword.split() if token]
    candidateWindows = [candidate]
    candidateWindows.extend(_inventoryTokenWindows(candidateTokens, len(keywordTokens) or 1))

    compactKeyword = keyword.replace(" ", "")
    compactWindows = [window.replace(" ", "") for window in candidateWindows if window]
    bestScore = 0.0
    bestWindow = ""
    for window in candidateWindows:
        score = _inventorySimilarityScore(keyword, window)
        if score > bestScore:
            bestScore = score
            bestWindow = window
    if compactKeyword:
        for compactWindow in compactWindows:
            score = _inventorySimilarityScore(compactKeyword, compactWindow)
            if score > bestScore:
                bestScore = score
                bestWindow = compactWindow
    return bestScore, bestWindow


def _keywordInventoryNameMatch(assetName: Optional[str], keywords: list[str]) -> Optional[dict]:
    rawName = str(assetName or "").strip()
    rawNameLower = rawName.lower()
    normalizedName = _normalizeInventoryMatchText(rawName)
    compactName = normalizedName.replace(" ", "")
    if not rawName or not keywords:
        return None

    bestExact: Optional[dict] = None
    bestFuzzy: Optional[dict] = None
    fuzzyEnabled = _inventoryFuzzyMatchingEnabled()
    fuzzyCutoff = _inventoryFuzzyScoreCutoff()
    fuzzyMinLength = _inventoryFuzzyMinKeywordLength()

    for keyword in keywords:
        cleanKeyword = str(keyword or "").strip().lower()
        if not cleanKeyword:
            continue
        normalizedKeyword = _normalizeInventoryMatchText(cleanKeyword)
        compactKeyword = normalizedKeyword.replace(" ", "")
        if not normalizedKeyword:
            continue

        exactMode = ""
        matchedText = ""
        if cleanKeyword in rawNameLower:
            exactMode = "exact"
            matchedText = cleanKeyword
        elif normalizedKeyword in normalizedName or (compactKeyword and compactKeyword in compactName):
            exactMode = "normalized"
            matchedText = normalizedKeyword

        if exactMode:
            candidate = {
                "matchType": "keyword",
                "matchMode": exactMode,
                "matchedField": "name",
                "keyword": cleanKeyword,
                "matchedText": matchedText,
                "fuzzyScore": 100.0,
                "reason": (
                    f"Item name matched keyword `{cleanKeyword}`."
                    if exactMode == "exact"
                    else f"Normalized item name matched keyword `{cleanKeyword}`."
                ),
            }
            if bestExact is None or len(compactKeyword) > len(_compactInventoryMatchText(bestExact.get("keyword") or "")):
                bestExact = candidate
            continue

        if not fuzzyEnabled or len(compactKeyword) < fuzzyMinLength or len(compactName) < fuzzyMinLength:
            continue
        score, matchedWindow = _bestInventoryPartialKeywordMatch(normalizedKeyword, normalizedName)
        if score < fuzzyCutoff:
            continue
        candidate = {
            "matchType": "keyword",
            "matchMode": "fuzzy",
            "matchedField": "name",
            "keyword": cleanKeyword,
            "matchedText": matchedWindow or normalizedName,
            "fuzzyScore": round(score, 1),
            "reason": f"Item name looked similar to keyword `{cleanKeyword}` ({round(score):.0f}).",
        }
        if bestFuzzy is None or float(candidate["fuzzyScore"]) > float(bestFuzzy.get("fuzzyScore") or 0):
            bestFuzzy = candidate

    return bestExact or bestFuzzy


def _inventoryMatchPriority(match: dict) -> float:
    matchType = str(match.get("matchType") or "").strip().lower()
    matchMode = str(match.get("matchMode") or "").strip().lower()
    if matchType == "item":
        return 400.0
    if matchType == "creator":
        return 300.0
    if matchType == "visual":
        try:
            distance = float(match.get("visualDistance"))
        except (TypeError, ValueError):
            distance = 999.0
        return 260.0 - min(distance, 32.0)
    if matchType == "keyword":
        if matchMode == "exact":
            return 220.0
        if matchMode == "normalized":
            return 180.0
        if matchMode == "fuzzy":
            return 120.0 + float(match.get("fuzzyScore") or 0)
    return 0.0


def _inventoryMatchSummary(items: list[dict]) -> dict[str, int]:
    exactItemMatchCount = 0
    creatorMatchCount = 0
    keywordMatchCount = 0
    normalizedKeywordMatchCount = 0
    fuzzyKeywordMatchCount = 0
    visualMatchCount = 0
    multiSignalMatchCount = 0
    suspiciousCreators: set[int] = set()
    for item in list(items or []):
        if not isinstance(item, dict):
            continue
        matchType = str(item.get("matchType") or "").strip().lower()
        matchMode = str(item.get("matchMode") or "").strip().lower()
        creatorId = _optionalInt(item.get("creatorId"))
        if creatorId is not None:
            suspiciousCreators.add(int(creatorId))
        if int(item.get("matchCount") or 0) > 1:
            multiSignalMatchCount += 1
        if matchType == "item":
            exactItemMatchCount += 1
        elif matchType == "creator":
            creatorMatchCount += 1
        elif matchType == "keyword":
            if matchMode == "fuzzy":
                fuzzyKeywordMatchCount += 1
            elif matchMode == "normalized":
                normalizedKeywordMatchCount += 1
            else:
                keywordMatchCount += 1
        elif matchType == "visual":
            visualMatchCount += 1
    return {
        "flaggedItemCount": len([item for item in list(items or []) if isinstance(item, dict)]),
        "exactItemMatchCount": exactItemMatchCount,
        "creatorMatchCount": creatorMatchCount,
        "keywordMatchCount": keywordMatchCount,
        "normalizedKeywordMatchCount": normalizedKeywordMatchCount,
        "fuzzyKeywordMatchCount": fuzzyKeywordMatchCount,
        "visualMatchCount": visualMatchCount,
        "multiSignalMatchCount": multiSignalMatchCount,
        "suspiciousCreatorCount": len(suspiciousCreators),
    }

def _inventoryMatchEntry(
    raw: dict,
    *,
    remaining: Optional[set[int]],
    creatorIds: set[int],
    keywords: list[str],
) -> tuple[Optional[dict], Optional[int], Optional[int]]:
    if _isGamepassInventoryItem(raw):
        return None, None, _extractGamepassId(raw)
    assetId = _extractAssetId(raw)
    if assetId is None:
        return None, None, None
    creatorId = _extractCreatorId(raw)
    creatorName = _extractCreatorName(raw)
    assetName = _extractAssetName(raw)
    itemType = _extractInventoryItemType(raw)
    matchItem = remaining is not None and assetId in remaining
    matchCreator = creatorId in creatorIds if creatorId is not None else False
    keywordMatch = _keywordInventoryNameMatch(assetName, keywords)
    matches: list[dict] = []
    if matchItem:
        matches.append(
            {
                "matchType": "item",
                "matchMode": "exact",
                "matchedField": "id",
                "reason": "Exact flagged item ID matched.",
            }
        )
    if matchCreator:
        matches.append(
            {
                "matchType": "creator",
                "matchMode": "exact",
                "matchedField": "creatorId",
                "reason": "Exact flagged creator ID matched.",
            }
        )
    if keywordMatch:
        matches.append(keywordMatch)
    if not matches:
        return None, int(assetId), None

    primaryMatch = max(matches, key=_inventoryMatchPriority)
    if remaining is not None and assetId in remaining:
        remaining.discard(assetId)
    entry = {
        "id": assetId,
        "name": assetName,
        "itemType": itemType,
        "creatorId": creatorId,
        "creatorName": creatorName,
        "matchType": primaryMatch.get("matchType"),
        "matchMode": primaryMatch.get("matchMode"),
        "matchedField": primaryMatch.get("matchedField"),
        "matchedText": primaryMatch.get("matchedText"),
        "keyword": primaryMatch.get("keyword"),
        "fuzzyScore": primaryMatch.get("fuzzyScore"),
        "reason": primaryMatch.get("reason"),
        "matchCount": len(matches),
        "reasons": [str(match.get("reason") or "").strip() for match in matches if str(match.get("reason") or "").strip()][:4],
    }
    return entry, int(assetId), None

inventoryFuzzyMatchingEnabled = _inventoryFuzzyMatchingEnabled
inventoryFuzzyScoreCutoff = _inventoryFuzzyScoreCutoff
inventoryFuzzyMinKeywordLength = _inventoryFuzzyMinKeywordLength
inventoryMatchPriority = _inventoryMatchPriority
inventoryMatchSummary = _inventoryMatchSummary
inventoryMatchEntry = _inventoryMatchEntry
