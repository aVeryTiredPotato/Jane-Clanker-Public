from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, List, Dict

import config
from db.sqlite import execute, executeMany, executeReturnId, fetchAll, fetchOne
from features.staff.sessions.Roblox import robloxAssets

_VALID_VISUAL_REF_STATE = "VALID"
_INVALID_VISUAL_REF_STATE = "INVALID"
_ERROR_VISUAL_REF_STATE = "ERROR"
_PENDING_VISUAL_REF_STATE = "PENDING"


def normalizeSeverity(value: object) -> int:
    try:
        severity = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, severity))


async def addRule(
    ruleType: str,
    ruleValue: str,
    note: Optional[str],
    createdBy: Optional[int],
    severity: int = 0,
) -> int:
    return await executeReturnId(
        """
        INSERT INTO bg_flag_rules (ruleType, ruleValue, note, severity, createdBy)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ruleType, ruleValue, note, normalizeSeverity(severity), createdBy),
    )


async def removeRule(ruleId: int) -> None:
    await execute("DELETE FROM bg_flag_rules WHERE ruleId = ?", (ruleId,))


async def getRule(ruleId: int) -> Optional[Dict]:
    return await fetchOne(
        "SELECT * FROM bg_flag_rules WHERE ruleId = ?",
        (int(ruleId),),
    )


async def listRules(ruleType: Optional[str] = None) -> List[Dict]:
    if ruleType:
        return await fetchAll(
            "SELECT * FROM bg_flag_rules WHERE ruleType = ? ORDER BY ruleId ASC",
            (ruleType,),
        )
    return await fetchAll("SELECT * FROM bg_flag_rules ORDER BY ruleId ASC")


def _normalizeAssetId(value: object) -> Optional[int]:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _visualHashSize() -> int:
    try:
        configured = int(getattr(config, "bgIntelligenceInventoryVisualHashSize", 8) or 8)
    except (TypeError, ValueError):
        configured = 8
    return max(4, min(configured, 32))


def _utcIsoNow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _itemRuleMeta(itemRules: list[dict]) -> dict[int, dict[str, object]]:
    metaByAssetId: dict[int, dict[str, object]] = {}
    for rule in list(itemRules or []):
        assetId = _normalizeAssetId(rule.get("ruleValue"))
        if assetId is None:
            continue
        existing = metaByAssetId.get(assetId)
        note = str(rule.get("note") or "").strip() or None
        ruleId = int(rule.get("ruleId") or 0)
        if existing is None:
            metaByAssetId[assetId] = {
                "sourceRuleId": ruleId if ruleId > 0 else None,
                "sourceRuleCount": 1,
                "note": note,
            }
            continue
        existing["sourceRuleCount"] = int(existing.get("sourceRuleCount") or 0) + 1
        currentSourceRuleId = int(existing.get("sourceRuleId") or 0)
        if ruleId > 0 and (currentSourceRuleId <= 0 or ruleId < currentSourceRuleId):
            existing["sourceRuleId"] = ruleId
        if not existing.get("note") and note:
            existing["note"] = note
    return metaByAssetId


def _normalizeValidationRow(assetId: int, row: dict[str, Any] | None = None) -> dict[str, Any]:
    source = dict(row or {})
    state = str(source.get("validationState") or _PENDING_VISUAL_REF_STATE).strip().upper()
    if state not in {
        _VALID_VISUAL_REF_STATE,
        _INVALID_VISUAL_REF_STATE,
        _ERROR_VISUAL_REF_STATE,
        _PENDING_VISUAL_REF_STATE,
    }:
        state = _PENDING_VISUAL_REF_STATE
    return {
        "assetId": int(assetId),
        "thumbnailHash": str(source.get("thumbnailHash") or "").strip() or None,
        "hashSize": int(source.get("hashSize") or 0),
        "thumbnailUrl": str(source.get("thumbnailUrl") or "").strip() or None,
        "thumbnailState": str(source.get("thumbnailState") or "").strip() or None,
        "validationState": state,
        "validationError": str(source.get("validationError") or "").strip() or None,
        "lastValidatedAt": str(source.get("lastValidatedAt") or "").strip() or None,
    }


async def listItemVisualReferences(*, validOnly: bool = False) -> List[Dict]:
    query = "SELECT * FROM bg_item_visual_refs"
    params: tuple = ()
    if validOnly:
        query += " WHERE validationState = ? AND COALESCE(thumbnailHash, '') <> ''"
        params = (_VALID_VISUAL_REF_STATE,)
    query += " ORDER BY assetId ASC"
    return await fetchAll(query, params)


async def validateItemVisualReference(assetId: int) -> dict[str, Any]:
    rows = await robloxAssets.validateRobloxAssetVisualReferences([int(assetId)])
    if rows:
        return dict(rows[0])
    return _normalizeValidationRow(
        int(assetId),
        {
            "validationState": _ERROR_VISUAL_REF_STATE,
            "validationError": "Validation did not return a result.",
            "lastValidatedAt": _utcIsoNow(),
        },
    )


async def syncItemVisualReferences(*, force: bool = False) -> dict[str, Any]:
    itemRules = await listRules("item")
    itemMetaByAssetId = _itemRuleMeta(itemRules)
    currentAssetIds = set(itemMetaByAssetId.keys())
    existingRows = await listItemVisualReferences(validOnly=False)
    existingByAssetId = {
        int(row.get("assetId")): row
        for row in existingRows
        if _normalizeAssetId(row.get("assetId")) is not None
    }

    staleAssetIds = sorted(set(existingByAssetId.keys()) - currentAssetIds)
    if staleAssetIds:
        await executeMany(
            "DELETE FROM bg_item_visual_refs WHERE assetId = ?",
            [(int(assetId),) for assetId in staleAssetIds],
        )

    if not currentAssetIds:
        return {
            "ruleCount": 0,
            "assetCount": 0,
            "validatedCount": 0,
            "invalidCount": 0,
            "errorCount": 0,
            "pendingCount": 0,
            "checkedCount": 0,
            "removedCount": len(staleAssetIds),
            "sampleIssues": [],
        }

    targetHashSize = _visualHashSize()
    needsValidation: set[int] = set()
    for assetId in currentAssetIds:
        row = existingByAssetId.get(int(assetId))
        if force or row is None:
            needsValidation.add(int(assetId))
            continue
        state = str(row.get("validationState") or "").strip().upper()
        hashSize = int(row.get("hashSize") or 0)
        thumbnailHash = str(row.get("thumbnailHash") or "").strip()
        if hashSize != targetHashSize:
            needsValidation.add(int(assetId))
        elif state in {_PENDING_VISUAL_REF_STATE, _ERROR_VISUAL_REF_STATE}:
            needsValidation.add(int(assetId))
        elif state == _VALID_VISUAL_REF_STATE and not thumbnailHash:
            needsValidation.add(int(assetId))

    validatedRows: dict[int, dict[str, Any]] = {}
    if needsValidation:
        for row in await robloxAssets.validateRobloxAssetVisualReferences(sorted(needsValidation)):
            assetId = _normalizeAssetId(row.get("assetId"))
            if assetId is None:
                continue
            validatedRows[assetId] = _normalizeValidationRow(assetId, row)

    upsertRows: list[tuple] = []
    finalRows: list[dict[str, Any]] = []
    for assetId in sorted(currentAssetIds):
        meta = itemMetaByAssetId.get(assetId) or {}
        existing = existingByAssetId.get(assetId)
        if assetId in validatedRows:
            effective = dict(validatedRows[assetId])
        elif existing is not None:
            effective = _normalizeValidationRow(assetId, existing)
        else:
            effective = _normalizeValidationRow(
                assetId,
                {
                    "validationState": _PENDING_VISUAL_REF_STATE,
                    "validationError": "Validation pending.",
                },
            )
        finalRow = {
            "assetId": int(assetId),
            "sourceRuleId": meta.get("sourceRuleId"),
            "sourceRuleCount": int(meta.get("sourceRuleCount") or 0) or 1,
            "note": meta.get("note"),
            **effective,
        }
        finalRows.append(finalRow)
        upsertRows.append(
            (
                int(finalRow["assetId"]),
                int(finalRow["sourceRuleId"] or 0) or None,
                int(finalRow["sourceRuleCount"] or 1),
                finalRow["note"],
                finalRow["thumbnailHash"],
                int(finalRow["hashSize"] or 0),
                finalRow["thumbnailUrl"],
                finalRow["thumbnailState"],
                finalRow["validationState"],
                finalRow["validationError"],
                finalRow["lastValidatedAt"],
            )
        )

    await executeMany(
        """
        INSERT INTO bg_item_visual_refs (
            assetId,
            sourceRuleId,
            sourceRuleCount,
            note,
            thumbnailHash,
            hashSize,
            thumbnailUrl,
            thumbnailState,
            validationState,
            validationError,
            lastValidatedAt
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(assetId) DO UPDATE SET
            sourceRuleId = excluded.sourceRuleId,
            sourceRuleCount = excluded.sourceRuleCount,
            note = excluded.note,
            thumbnailHash = excluded.thumbnailHash,
            hashSize = excluded.hashSize,
            thumbnailUrl = excluded.thumbnailUrl,
            thumbnailState = excluded.thumbnailState,
            validationState = excluded.validationState,
            validationError = excluded.validationError,
            lastValidatedAt = excluded.lastValidatedAt,
            updatedAt = datetime('now')
        """,
        upsertRows,
    )

    sampleIssues: list[str] = []
    for row in finalRows:
        state = str(row.get("validationState") or "").upper()
        if state not in {_INVALID_VISUAL_REF_STATE, _ERROR_VISUAL_REF_STATE}:
            continue
        errorText = str(row.get("validationError") or "").strip() or state.title()
        sampleIssues.append(f"{int(row.get('assetId') or 0)}: {errorText}")
        if len(sampleIssues) >= 5:
            break

    return {
        "ruleCount": len(itemRules),
        "assetCount": len(currentAssetIds),
        "validatedCount": sum(1 for row in finalRows if str(row.get("validationState") or "").upper() == _VALID_VISUAL_REF_STATE),
        "invalidCount": sum(1 for row in finalRows if str(row.get("validationState") or "").upper() == _INVALID_VISUAL_REF_STATE),
        "errorCount": sum(1 for row in finalRows if str(row.get("validationState") or "").upper() == _ERROR_VISUAL_REF_STATE),
        "pendingCount": sum(1 for row in finalRows if str(row.get("validationState") or "").upper() == _PENDING_VISUAL_REF_STATE),
        "checkedCount": len(needsValidation),
        "removedCount": len(staleAssetIds),
        "sampleIssues": sampleIssues,
    }


async def getValidatedItemVisualHashes(*, ensureSynced: bool = True) -> Dict[int, str]:
    itemRules = await listRules("item")
    currentAssetIds = {assetId for assetId in (_normalizeAssetId(rule.get("ruleValue")) for rule in itemRules) if assetId is not None}

    if ensureSynced and currentAssetIds:
        existingRows = await listItemVisualReferences(validOnly=False)
        existingByAssetId = {
            int(row.get("assetId")): row
            for row in existingRows
            if _normalizeAssetId(row.get("assetId")) is not None
        }
        targetHashSize = _visualHashSize()
        needsSync = set(existingByAssetId.keys()) != currentAssetIds
        if not needsSync:
            for assetId in currentAssetIds:
                row = existingByAssetId.get(assetId)
                state = str((row or {}).get("validationState") or "").strip().upper()
                hashSize = int((row or {}).get("hashSize") or 0)
                thumbnailHash = str((row or {}).get("thumbnailHash") or "").strip()
                if state == _PENDING_VISUAL_REF_STATE or hashSize != targetHashSize:
                    needsSync = True
                    break
                if state == _VALID_VISUAL_REF_STATE and not thumbnailHash:
                    needsSync = True
                    break
        if needsSync:
            await syncItemVisualReferences(force=False)

    rows = await fetchAll(
        """
        SELECT assetId, thumbnailHash
        FROM bg_item_visual_refs
        WHERE validationState = ? AND COALESCE(thumbnailHash, '') <> ''
        ORDER BY assetId ASC
        """,
        (_VALID_VISUAL_REF_STATE,),
    )
    hashes: dict[int, str] = {}
    for row in rows:
        assetId = _normalizeAssetId(row.get("assetId"))
        thumbnailHash = str(row.get("thumbnailHash") or "").strip()
        if assetId is None or assetId not in currentAssetIds or not thumbnailHash:
            continue
        hashes[int(assetId)] = thumbnailHash

    flaggedRows = await fetchAll(
        """
        SELECT assetId, thumbnailHash
        FROM bg_item_review_queue
        WHERE status = 'FLAGGED' AND COALESCE(thumbnailHash, '') <> ''
        ORDER BY queueId ASC
        """
    )
    for row in flaggedRows:
        assetId = _normalizeAssetId(row.get("assetId"))
        thumbnailHash = str(row.get("thumbnailHash") or "").strip()
        if assetId is None or not thumbnailHash:
            continue
        hashes[int(assetId)] = thumbnailHash
    return hashes
