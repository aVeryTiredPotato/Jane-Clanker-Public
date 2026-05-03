from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable

import config
from . import taskStats


@dataclass
class FeatureStats:
    name: str
    limit: int
    waiting: int
    inFlight: int
    completed: int
    failed: int
    avgLatencyMs: float
    avgQueueWaitMs: float


class _FeatureBudget:
    def __init__(self, name: str, limit: int):
        self.name = str(name)
        self.limit = max(1, int(limit))
        self._semaphore = asyncio.Semaphore(self.limit)
        self._waiting = 0
        self._inFlight = 0
        self._completed = 0
        self._failed = 0
        self._latencySamples = deque(maxlen=500)
        self._queueSamples = deque(maxlen=500)
        self._lock = asyncio.Lock()

    async def run(
        self,
        opFactory: Callable[[], Awaitable[Any]],
    ) -> Any:
        queuedAt = perf_counter()
        async with self._lock:
            self._waiting += 1

        await self._semaphore.acquire()
        startedAt = perf_counter()
        queueMs = (startedAt - queuedAt) * 1000.0
        async with self._lock:
            self._waiting = max(0, self._waiting - 1)
            self._inFlight += 1
            self._queueSamples.append(queueMs)

        failed = False
        try:
            return await opFactory()
        except Exception:
            failed = True
            raise
        finally:
            latencyMs = (perf_counter() - startedAt) * 1000.0
            async with self._lock:
                self._inFlight = max(0, self._inFlight - 1)
                self._completed += 1
                if failed:
                    self._failed += 1
                self._latencySamples.append(latencyMs)
            self._semaphore.release()
            try:
                await taskStats.record(self.name, latencyMs)
            except Exception:
                pass

    async def snapshot(self) -> FeatureStats:
        async with self._lock:
            latencySamples = list(self._latencySamples)
            queueSamples = list(self._queueSamples)
            avgLatencyMs = (sum(latencySamples) / len(latencySamples)) if latencySamples else 0.0
            avgQueueMs = (sum(queueSamples) / len(queueSamples)) if queueSamples else 0.0
            return FeatureStats(
                name=self.name,
                limit=self.limit,
                waiting=self._waiting,
                inFlight=self._inFlight,
                completed=self._completed,
                failed=self._failed,
                avgLatencyMs=avgLatencyMs,
                avgQueueWaitMs=avgQueueMs,
            )


class AsyncTaskBudgeter:
    def __init__(self, limits: dict[str, int]):
        self._features: dict[str, _FeatureBudget] = {
            name: _FeatureBudget(name, limit)
            for name, limit in limits.items()
        }

    def hasFeature(self, feature: str) -> bool:
        return feature in self._features

    def _ensureFeature(self, feature: str) -> _FeatureBudget:
        budget = self._features.get(feature)
        if budget is not None:
            return budget
        budget = _FeatureBudget(feature, 1)
        self._features[feature] = budget
        return budget

    async def run(
        self,
        feature: str,
        opFactory: Callable[[], Awaitable[Any]],
    ) -> Any:
        return await self._ensureFeature(feature).run(opFactory)

    async def snapshot(self) -> dict[str, Any]:
        features: dict[str, dict[str, Any]] = {}
        waitingTotal = 0
        inFlightTotal = 0
        totalOps = 0
        weightedLatency = 0.0
        for name, feature in self._features.items():
            stats = await feature.snapshot()
            features[name] = {
                "limit": stats.limit,
                "waiting": stats.waiting,
                "inFlight": stats.inFlight,
                "pending": stats.waiting + stats.inFlight,
                "completed": stats.completed,
                "failed": stats.failed,
                "avgLatencyMs": round(stats.avgLatencyMs, 2),
                "avgQueueWaitMs": round(stats.avgQueueWaitMs, 2),
            }
            waitingTotal += stats.waiting
            inFlightTotal += stats.inFlight
            totalOps += stats.completed
            weightedLatency += stats.avgLatencyMs * stats.completed
        avgLatencyTotal = (weightedLatency / totalOps) if totalOps else 0.0
        return {
            "features": features,
            "totals": {
                "waiting": waitingTotal,
                "inFlight": inFlightTotal,
                "pending": waitingTotal + inFlightTotal,
                "completed": totalOps,
                "avgLatencyMs": round(avgLatencyTotal, 2),
            },
        }


def _limitFromConfig(key: str, default: int) -> int:
    try:
        value = int(getattr(config, key, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(1, value)


_defaultBudgeter = AsyncTaskBudgeter(
    {
        "robloxApi": _limitFromConfig("runtimeBudgetRobloxConcurrency", 6),
        "sheetsIo": _limitFromConfig("runtimeBudgetSheetsConcurrency", 2),
        "sheetsInteractive": _limitFromConfig(
            "runtimeBudgetInteractiveSheetsConcurrency",
            _limitFromConfig("runtimeBudgetSheetsConcurrency", 2),
        ),
        "sheetsBackground": _limitFromConfig("runtimeBudgetBackgroundSheetsConcurrency", 1),
        "discordIo": _limitFromConfig("runtimeBudgetDiscordConcurrency", 6),
        "interactionAck": _limitFromConfig("runtimeBudgetInteractionAckConcurrency", 24),
        "backgroundJobs": _limitFromConfig("runtimeBudgetBackgroundConcurrency", 2),
    }
)


def getBudgeter() -> AsyncTaskBudgeter:
    return _defaultBudgeter


async def runBudgeted(feature: str, opFactory: Callable[[], Awaitable[Any]]) -> Any:
    return await _defaultBudgeter.run(feature, opFactory)


async def runSheetsThread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await runInteractiveSheetsThread(fn, *args, **kwargs)


async def runInteractiveSheetsThread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await runThreaded("sheetsInteractive", fn, *args, **kwargs)


async def runBackgroundSheetsThread(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return await runThreaded("sheetsBackground", fn, *args, **kwargs)


async def runThreaded(
    feature: str,
    fn: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    return await runBudgeted(
        feature,
        lambda: asyncio.to_thread(fn, *args, **kwargs),
    )


async def runDiscord(opFactory: Callable[[], Awaitable[Any]]) -> Any:
    return await runBudgeted("discordIo", opFactory)


async def runInteractionAck(opFactory: Callable[[], Awaitable[Any]]) -> Any:
    return await runBudgeted("interactionAck", opFactory)


async def runRoblox(opFactory: Callable[[], Awaitable[Any]]) -> Any:
    return await runBudgeted("robloxApi", opFactory)


async def runBackground(opFactory: Callable[[], Awaitable[Any]]) -> Any:
    return await runBudgeted("backgroundJobs", opFactory)
