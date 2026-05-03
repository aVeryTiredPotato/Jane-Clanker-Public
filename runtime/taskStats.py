from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from time import monotonic
from typing import Any

import config

log = logging.getLogger(__name__)


def _statsPath() -> Path:
    rawPath = str(getattr(config, "runtimeTaskStatsPath", "runtime/data/task-stats.json") or "").strip()
    return Path(rawPath or "runtime/data/task-stats.json")


def _flushIntervalSec() -> float:
    try:
        value = float(getattr(config, "runtimeTaskStatsFlushIntervalSec", 30.0) or 30.0)
    except (TypeError, ValueError):
        value = 30.0
    return max(1.0, value)


def _flushDirtyCount() -> int:
    try:
        value = int(getattr(config, "runtimeTaskStatsFlushDirtyCount", 25) or 25)
    except (TypeError, ValueError):
        value = 25
    return max(1, value)


def _normalizeName(name: object) -> str:
    return str(name or "unknown").strip()[:160] or "unknown"


class _TaskStatsStore:
    def __init__(self) -> None:
        self._loaded = False
        self._entries: dict[str, dict[str, float | int]] = {}
        self._lock = asyncio.Lock()
        self._dirtyCount = 0
        self._lastFlushMonotonic = monotonic()
        self._flushTask: asyncio.Task | None = None

    def _loadSync(self) -> dict[str, dict[str, float | int]]:
        path = _statsPath()
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("Could not read runtime task stats from %s.", path, exc_info=True)
            return {}

        rawEntries: Any
        if isinstance(payload, dict) and isinstance(payload.get("entries"), list):
            rawEntries = payload.get("entries")
        elif isinstance(payload, list):
            rawEntries = payload
        else:
            rawEntries = []

        entries: dict[str, dict[str, float | int]] = {}
        for row in rawEntries:
            if not isinstance(row, dict):
                continue
            name = _normalizeName(row.get("name"))
            try:
                avgMs = float(row.get("timeMs", row.get("time", 0.0)) or 0.0)
                amount = int(row.get("amount", 0) or 0)
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            entries[name] = {
                "name": name,
                "timeMs": max(0.0, avgMs),
                "amount": amount,
            }
        return entries

    def _writeSync(self, entries: list[dict[str, float | int | str]]) -> None:
        path = _statsPath()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": 1,
            "entries": entries,
        }
        tempPath = path.with_name(f"{path.name}.tmp")
        tempPath.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(tempPath, path)

    async def _ensureLoadedLocked(self) -> None:
        if self._loaded:
            return
        self._entries = await asyncio.to_thread(self._loadSync)
        self._loaded = True

    async def record(self, name: object, elapsedMs: float) -> None:
        normalizedName = _normalizeName(name)
        try:
            normalizedElapsed = max(0.0, float(elapsedMs))
        except (TypeError, ValueError):
            return

        shouldFlushNow = False
        shouldScheduleFlush = False
        async with self._lock:
            await self._ensureLoadedLocked()
            row = self._entries.get(normalizedName)
            if row is None:
                row = {"name": normalizedName, "timeMs": 0.0, "amount": 0}
                self._entries[normalizedName] = row
            previousAmount = int(row.get("amount", 0) or 0)
            previousAvgMs = float(row.get("timeMs", 0.0) or 0.0)
            newAmount = previousAmount + 1
            row["amount"] = newAmount
            row["timeMs"] = ((previousAvgMs * previousAmount) + normalizedElapsed) / newAmount

            self._dirtyCount += 1
            now = monotonic()
            shouldFlushNow = (
                self._dirtyCount >= _flushDirtyCount()
                or now - self._lastFlushMonotonic >= _flushIntervalSec()
            )
            shouldScheduleFlush = self._flushTask is None or self._flushTask.done()

        if shouldFlushNow:
            await self.flush()
        elif shouldScheduleFlush:
            self._flushTask = asyncio.create_task(self._delayedFlush())

    async def _delayedFlush(self) -> None:
        await asyncio.sleep(_flushIntervalSec())
        await self.flush()

    async def flush(self) -> None:
        async with self._lock:
            await self._ensureLoadedLocked()
            if self._dirtyCount <= 0:
                return
            entries = [
                {
                    "name": str(row.get("name") or name),
                    "timeMs": round(float(row.get("timeMs", 0.0) or 0.0), 3),
                    "amount": int(row.get("amount", 0) or 0),
                }
                for name, row in sorted(self._entries.items())
            ]
            self._dirtyCount = 0
            self._lastFlushMonotonic = monotonic()

        try:
            await asyncio.to_thread(self._writeSync, entries)
        except Exception:
            log.warning("Could not write runtime task stats.", exc_info=True)

    async def snapshot(self) -> list[dict[str, float | int | str]]:
        async with self._lock:
            await self._ensureLoadedLocked()
            return [
                {
                    "name": str(row.get("name") or name),
                    "timeMs": round(float(row.get("timeMs", 0.0) or 0.0), 3),
                    "amount": int(row.get("amount", 0) or 0),
                }
                for name, row in sorted(self._entries.items())
            ]


_store = _TaskStatsStore()


async def record(name: object, elapsedMs: float) -> None:
    await _store.record(name, elapsedMs)


async def flush() -> None:
    await _store.flush()


async def snapshot() -> list[dict[str, float | int | str]]:
    return await _store.snapshot()
