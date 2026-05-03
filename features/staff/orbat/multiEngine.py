from __future__ import annotations

import os
import socket
import ssl
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

import config
from runtime.sheetsRuntime import installGoogleSheetsRuntime

from .multiRegistry import MultiOrbatSheetConfig, loadMultiOrbatRegistry


SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


def _repoRoot() -> Path:
    return Path(__file__).resolve().parents[3]


def _resolveCredentialsPath(rawPath: str) -> str:
    pathText = str(rawPath or "").strip()
    if not pathText:
        return ""

    raw = Path(pathText).expanduser()
    primary = raw if raw.is_absolute() else (_repoRoot() / raw).resolve()
    if primary.exists():
        return str(primary)

    fallbackByName = _repoRoot() / "localOnly" / "credentials" / raw.name
    if raw.name and fallbackByName.exists():
        return str(fallbackByName)

    return str(primary)


def _isRateLimitError(exc: Exception) -> bool:
    current: Exception | None = exc
    while current is not None:
        resp = getattr(current, "resp", None)
        status = getattr(resp, "status", None)
        if status == 429 or getattr(current, "status_code", None) == 429:
            return True
        current = current.__cause__ if isinstance(current.__cause__, Exception) else None
    return False


def _isTransientTransportError(exc: Exception) -> bool:
    transientStatuses = {408, 429, 500, 502, 503, 504}
    transientMarkers = (
        "wrong version number",
        "cipher operation failed",
        "bad record mac",
        "unexpected eof",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timed out",
        "timeout",
        "tls",
        "ssl",
    )

    current: Exception | None = exc
    while current is not None:
        # HTTP style retry statuses.
        resp = getattr(current, "resp", None)
        status = getattr(resp, "status", None)
        if status in transientStatuses or getattr(current, "status_code", None) in transientStatuses:
            return True

        # Network/SSL exceptions.
        if isinstance(current, (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError)):
            return True
        if isinstance(current, OSError):
            lowerMessage = str(current).lower()
            if any(marker in lowerMessage for marker in transientMarkers):
                return True

        lowerMessage = str(current).lower()
        if any(marker in lowerMessage for marker in transientMarkers):
            return True

        current = current.__cause__ if isinstance(current.__cause__, Exception) else None
    return False


class MultiOrbatEngine:
    def __init__(self, registry: Optional[dict[str, MultiOrbatSheetConfig]] = None):
        self.registry = registry or loadMultiOrbatRegistry()
        self._credentialsCache: dict[str, Any] = {}
        self._serviceCache: dict[str, Any] = {}
        self._sheetIdCache: dict[tuple[str, str], int] = {}
        self._sheetTitleCache: dict[str, str] = {}
        self._serviceLocal = threading.local()
        self._serviceLock = threading.Lock()
        self._requestLock = threading.Lock()
        self._nextAllowedRequestAt = 0.0

    def _invalidateServiceCaches(self) -> None:
        # Rebuild service clients after transport-level failures so we do not
        # keep retrying on a poisoned/closed underlying HTTP connection.
        with self._serviceLock:
            self._serviceCache.clear()
        self._sheetIdCache.clear()
        self._sheetTitleCache.clear()

    def listSheetKeys(self) -> list[str]:
        return sorted(self.registry.keys())

    def getSheetConfig(self, sheetKey: str) -> MultiOrbatSheetConfig:
        if sheetKey not in self.registry:
            raise KeyError(f"Unknown ORBAT sheet key: {sheetKey}")
        return self.registry[sheetKey]

    def getSpreadsheetId(self, sheetKey: str) -> str:
        return self.getSheetConfig(sheetKey).spreadsheetId

    def getSheetName(self, sheetKey: str) -> str:
        cached = self._sheetTitleCache.get(sheetKey)
        if cached:
            return cached
        sheet = self.getSheetConfig(sheetKey)
        try:
            _, resolvedTitle = self._resolveSheetIdentity(sheet)
            return resolvedTitle
        except Exception:
            return sheet.sheetName

    def _throttle(self) -> None:
        minInterval = float(getattr(config, "googleSheetsMinRequestIntervalSec", 0.05) or 0.05)
        with self._requestLock:
            now = time.monotonic()
            waitFor = self._nextAllowedRequestAt - now
            if waitFor > 0:
                time.sleep(waitFor)
            self._nextAllowedRequestAt = time.monotonic() + max(0.0, minInterval)

    def _withRetry(self, fn: Callable[[], Any]) -> Any:
        maxAttempts = max(1, int(getattr(config, "googleSheetsMaxAttempts", 3) or 3))
        retryBase = float(getattr(config, "googleSheetsRetryBaseSec", 1.5) or 1.5)
        transportRetryBase = float(getattr(config, "googleSheetsTransportRetryBaseSec", retryBase) or retryBase)
        lastError: Optional[Exception] = None
        for attempt in range(1, maxAttempts + 1):
            self._throttle()
            try:
                return fn()
            except Exception as exc:
                lastError = exc
                isRateLimited = _isRateLimitError(exc)
                isTransient = _isTransientTransportError(exc)
                if (isRateLimited or isTransient) and attempt < maxAttempts:
                    if isTransient:
                        self._invalidateServiceCaches()
                    baseDelay = retryBase if isRateLimited else transportRetryBase
                    time.sleep(max(0.0, baseDelay) * attempt)
                    continue
                raise
        if lastError:
            raise lastError
        raise RuntimeError("Google Sheets operation failed with unknown error.")

    def _loadCredentials(self, sheet: MultiOrbatSheetConfig):
        cacheKey = sheet.key
        cached = self._credentialsCache.get(cacheKey)
        if cached is not None:
            return cached

        path = os.getenv(sheet.credentialsPathEnvVar) or str(
            getattr(config, sheet.credentialsPathConfigKey, "") or ""
        ).strip()
        # Fallback: use the primary ORBAT credentials configured for general staff.
        if not path:
            path = (
                os.getenv("ORBAT_GOOGLE_CREDENTIALS_PATH")
                or str(getattr(config, "orbatGoogleCredentialsPath", "") or "").strip()
            )

        from google.oauth2.service_account import Credentials

        if path:
            path = _resolveCredentialsPath(path)
            creds = Credentials.from_service_account_file(path, scopes=SCOPES)
            self._credentialsCache[cacheKey] = creds
            return creds

        raise RuntimeError(
            f"Missing Google credentials for '{sheet.key}' "
            f"(env: {sheet.credentialsPathEnvVar})."
        )

    def _getService(self, sheet: MultiOrbatSheetConfig):
        with self._serviceLock:
            service = self._serviceCache.get(sheet.key)
            if service is not None:
                return service
            installGoogleSheetsRuntime()
            from googleapiclient.discovery import build

            creds = self._loadCredentials(sheet)
            service = build("sheets", "v4", credentials=creds, cache_discovery=False)
            self._serviceCache[sheet.key] = service
            return service

    def _getSheetTabId(self, sheet: MultiOrbatSheetConfig) -> int:
        knownTitle = self._sheetTitleCache.get(sheet.key, sheet.sheetName)
        cacheKey = (sheet.spreadsheetId, sheet.sheetName)
        cached = self._sheetIdCache.get(cacheKey)
        if cached is not None:
            return cached
        knownCacheKey = (sheet.spreadsheetId, knownTitle)
        cachedKnown = self._sheetIdCache.get(knownCacheKey)
        if cachedKnown is not None:
            return cachedKnown

        resolvedId, resolvedTitle = self._resolveSheetIdentity(sheet)
        self._sheetIdCache[(sheet.spreadsheetId, sheet.sheetName)] = resolvedId
        self._sheetIdCache[(sheet.spreadsheetId, resolvedTitle)] = resolvedId
        self._sheetTitleCache[sheet.key] = resolvedTitle
        return resolvedId

    @staticmethod
    def _normalizeSheetTitle(value: str) -> str:
        return "".join(ch for ch in str(value or "").lower() if ch.isalnum())

    def _resolveSheetIdentity(self, sheet: MultiOrbatSheetConfig) -> tuple[int, str]:
        service = self._getService(sheet)

        def _fetchMeta():
            return service.spreadsheets().get(spreadsheetId=sheet.spreadsheetId).execute()

        metadata = self._withRetry(_fetchMeta)
        sheets = metadata.get("sheets", [])
        configuredTitle = str(sheet.sheetName or "").strip()

        # Exact match first.
        for entry in sheets:
            props = entry.get("properties", {})
            if props.get("title") == configuredTitle:
                return int(props.get("sheetId")), str(props.get("title"))

        # Normalized fallback match for minor naming drift (spaces/case/symbols).
        configuredNorm = self._normalizeSheetTitle(configuredTitle)
        if configuredNorm:
            normalizedMatches: list[tuple[int, str]] = []
            for entry in sheets:
                props = entry.get("properties", {})
                title = str(props.get("title") or "")
                if self._normalizeSheetTitle(title) == configuredNorm:
                    normalizedMatches.append((int(props.get("sheetId")), title))
            if len(normalizedMatches) == 1:
                return normalizedMatches[0]
            if len(normalizedMatches) > 1:
                # Prefer case-insensitive exact string match if multiple normalized hits.
                for sheetId, title in normalizedMatches:
                    if title.lower() == configuredTitle.lower():
                        return sheetId, title

        available = ", ".join(
            str(entry.get("properties", {}).get("title") or "")
            for entry in sheets
        )
        raise RuntimeError(
            f"Sheet tab '{sheet.sheetName}' not found for '{sheet.key}'. "
            f"Available tabs: {available}"
        )

    def getSheetTabId(self, sheetKey: str) -> int:
        sheet = self.getSheetConfig(sheetKey)
        return self._getSheetTabId(sheet)

    def getValues(self, sheetKey: str, rangeA1: str, **kwargs: Any) -> list[list[Any]]:
        sheet = self.getSheetConfig(sheetKey)
        service = self._getService(sheet)

        def _run():
            requestParams = {"spreadsheetId": sheet.spreadsheetId, "range": rangeA1}
            requestParams.update(kwargs)
            return (
                service.spreadsheets()
                .values()
                .get(**requestParams)
                .execute()
                .get("values", [])
            )

        return self._withRetry(_run)

    def batchGetValues(self, sheetKey: str, ranges: list[str], **kwargs: Any) -> list[dict[str, Any]]:
        sheet = self.getSheetConfig(sheetKey)
        service = self._getService(sheet)

        def _run():
            requestParams = {"spreadsheetId": sheet.spreadsheetId, "ranges": ranges}
            requestParams.update(kwargs)
            return (
                service.spreadsheets()
                .values()
                .batchGet(**requestParams)
                .execute()
                .get("valueRanges", [])
            )

        return self._withRetry(_run)

    def batchUpdateValues(self, sheetKey: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        sheet = self.getSheetConfig(sheetKey)
        service = self._getService(sheet)
        body = {
            "valueInputOption": "USER_ENTERED",
            "data": rows,
        }

        def _run():
            service.spreadsheets().values().batchUpdate(
                spreadsheetId=sheet.spreadsheetId,
                body=body,
            ).execute()

        self._withRetry(_run)

    def batchUpdateRequests(self, sheetKey: str, requests: list[dict[str, Any]]) -> None:
        if not requests:
            return
        sheet = self.getSheetConfig(sheetKey)
        service = self._getService(sheet)
        body = {"requests": requests}

        def _run():
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet.spreadsheetId,
                body=body,
            ).execute()

        self._withRetry(_run)

    def appendValues(
        self,
        sheetKey: str,
        *,
        rangeA1: str,
        values: list[list[Any]],
        valueInputOption: str = "USER_ENTERED",
        insertDataOption: str = "INSERT_ROWS",
    ) -> dict[str, Any]:
        if not values:
            return {}
        sheet = self.getSheetConfig(sheetKey)
        service = self._getService(sheet)
        body = {"values": values}

        def _run():
            return (
                service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=sheet.spreadsheetId,
                    range=rangeA1,
                    valueInputOption=valueInputOption,
                    insertDataOption=insertDataOption,
                    body=body,
                )
                .execute()
            )

        return self._withRetry(_run)

    def getSpreadsheetMetadata(self, sheetKey: str) -> dict[str, Any]:
        sheet = self.getSheetConfig(sheetKey)
        service = self._getService(sheet)

        def _run():
            return service.spreadsheets().get(spreadsheetId=sheet.spreadsheetId).execute()

        return self._withRetry(_run)

    def findRowByValue(
        self,
        sheetKey: str,
        *,
        columnLetter: str,
        value: str,
        caseInsensitive: bool = True,
    ) -> Optional[int]:
        sheet = self.getSheetConfig(sheetKey)
        sheetName = self.getSheetName(sheetKey)
        lookup = str(value or "").strip()
        if not lookup:
            return None
        values = self.getValues(sheetKey, f"{sheetName}!{columnLetter}:{columnLetter}")
        target = lookup.lower() if caseInsensitive else lookup
        for rowIndex, row in enumerate(values, start=1):
            if not row:
                continue
            current = str(row[0]).strip()
            compare = current.lower() if caseInsensitive else current
            if compare == target:
                return rowIndex
        return None

    def readRowColumns(
        self,
        sheetKey: str,
        *,
        row: int,
        columnMap: dict[str, str],
    ) -> dict[str, str]:
        sheetName = self.getSheetName(sheetKey)
        ranges: list[str] = []
        keys: list[str] = []
        for key, col in columnMap.items():
            if not col:
                continue
            ranges.append(f"{sheetName}!{col}{row}:{col}{row}")
            keys.append(key)
        if not ranges:
            return {}
        valueRanges = self.batchGetValues(sheetKey, ranges)
        out: dict[str, str] = {}
        for index, key in enumerate(keys):
            value = ""
            try:
                values = valueRanges[index].get("values", [])
                if values and values[0]:
                    value = str(values[0][0])
            except Exception:
                value = ""
            out[key] = value
        return out

    def writeRowColumns(
        self,
        sheetKey: str,
        *,
        row: int,
        columnValues: dict[str, tuple[str, Any]],
    ) -> None:
        updates: list[dict[str, Any]] = []
        sheetName = self.getSheetName(sheetKey)
        for _, payload in columnValues.items():
            if not isinstance(payload, tuple) or len(payload) != 2:
                continue
            col, rawValue = payload
            if not col:
                continue
            updates.append(
                {
                    "range": f"{sheetName}!{col}{row}:{col}{row}",
                    "values": [[rawValue]],
                }
            )
        self.batchUpdateValues(sheetKey, updates)

    def incrementIntCell(self, sheetKey: str, *, row: int, columnLetter: str, delta: int = 1) -> int:
        sheetName = self.getSheetName(sheetKey)
        rangeA1 = f"{sheetName}!{columnLetter}{row}:{columnLetter}{row}"
        values = self.getValues(sheetKey, rangeA1)
        current = 0
        if values and values[0]:
            try:
                current = int(float(str(values[0][0]).strip()))
            except (TypeError, ValueError):
                current = 0
        nextValue = current + int(delta)
        self.batchUpdateValues(
            sheetKey,
            [{"range": rangeA1, "values": [[nextValue]]}],
        )
        return nextValue


_engineInstance: Optional[MultiOrbatEngine] = None
_engineLock = threading.Lock()


def getMultiOrbatEngine() -> MultiOrbatEngine:
    global _engineInstance
    with _engineLock:
        if _engineInstance is None:
            _engineInstance = MultiOrbatEngine()
        return _engineInstance
