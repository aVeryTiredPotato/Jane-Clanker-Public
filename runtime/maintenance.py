from __future__ import annotations

import asyncio
import calendar
import logging
import socket
import ssl
from datetime import datetime, timedelta, timezone
from typing import Any

from runtime import automationReports as automationReportsRuntime

log = logging.getLogger(__name__)


class MaintenanceCoordinator:
    def __init__(
        self,
        *,
        botClient: Any,
        configModule: Any,
        recruitmentService: Any,
        recruitmentSheets: Any,
        departmentOrbatSheets: Any,
        orbatSheets: Any,
        serverSafetyService: Any | None,
        orbatAuditRuntime: Any | None,
        sessionService: Any,
        sessionViews: Any,
        taskBudgeter: Any,
        configSanityModule: Any,
    ) -> None:
        self.botClient = botClient
        self.config = configModule
        self.recruitmentService = recruitmentService
        self.recruitmentSheets = recruitmentSheets
        self.departmentOrbatSheets = departmentOrbatSheets
        self.orbatSheets = orbatSheets
        self.serverSafetyService = serverSafetyService
        self.orbatAuditRuntime = orbatAuditRuntime
        self.sessionService = sessionService
        self.sessionViews = sessionViews
        self.taskBudgeter = taskBudgeter
        self.configSanity = configSanityModule
        self.pauseController = None

        self.globalOrbatUpdateTask: asyncio.Task | None = None
        self.startupMaintenanceTask: asyncio.Task | None = None
        self.lastConfigSanitySummary: dict[str, object] | None = None

        self._orbatMaintenanceLock = asyncio.Lock()
        self._lastSessionExpiryCheckAt: datetime | None = None
        self._lastBgIntelPruneAt: datetime | None = None
        self._lastBgItemReviewSpreadsheetSyncAt: datetime | None = None
        self._bgItemReviewSpreadsheetStartupCatchupPending = True

    def _isPaused(self) -> bool:
        controller = self.pauseController
        if controller is None:
            return False
        try:
            return bool(controller.isPaused())
        except Exception:
            return False

    def _isGoogleRateLimitError(self, exc: Exception) -> bool:
        return self._isRetryableSheetsError(exc, includeTransport=False)

    def _isRetryableSheetsError(self, exc: Exception, *, includeTransport: bool = True) -> bool:
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
            resp = getattr(current, "resp", None)
            status = getattr(resp, "status", None)
            if status in transientStatuses or getattr(current, "status_code", None) in transientStatuses:
                return True
            if includeTransport:
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

    def _parseIsoDatetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _orbatWeeklyScheduleConfig(self) -> tuple[int, int, int]:
        hour = int(getattr(self.config, "orbatOrganizationUtcHour", 3))
        minute = int(getattr(self.config, "orbatOrganizationUtcMinute", 0))
        weekday = int(getattr(self.config, "orbatOrganizationUtcWeekday", 6))
        return hour, minute, weekday

    def _nonRecruitmentOrbatWritesEnabled(self) -> bool:
        return bool(getattr(self.config, "nonRecruitmentOrbatWritesEnabled", False))

    def automaticRecruitmentPayoutEnabled(self) -> bool:
        # Default off: payout and monthly reset are manual unless explicitly enabled.
        return bool(getattr(self.config, "automaticRecruitmentPayoutEnabled", False))

    def _summarizeMaintenanceResult(self, result: object) -> str:
        if isinstance(result, dict):
            nestedResults = result.get("results")
            if isinstance(nestedResults, dict):
                statusBySheet: dict[str, str] = {}
                for sheetName, sheetResult in nestedResults.items():
                    status = "Success"
                    if isinstance(sheetResult, dict):
                        if sheetResult.get("ok") is False:
                            status = "Failed"
                        elif str(sheetResult.get("reason") or "").strip().lower() in {
                            "exception",
                            "error",
                            "failed",
                        }:
                            status = "Failed"
                        elif sheetResult.get("error"):
                            status = "Failed"
                    statusBySheet[str(sheetName)] = status
                return str(statusBySheet)
            return ", ".join(f"{key}={value}" for key, value in result.items())
        return str(result)

    def _authorizedByForRunType(self, runType: str) -> str:
        lowered = str(runType or "").strip().lower()
        if lowered == "startup":
            return "startup touchup"
        if lowered == "weekly":
            return "scheduled touchup"
        if lowered:
            return f"{lowered} touchup"
        return "scheduled touchup"

    @staticmethod
    def _readInt(result: object, key: str) -> int:
        if not isinstance(result, dict):
            return 0
        try:
            return int(result.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _buildMetricDetails(metrics: list[tuple[str, int]]) -> str:
        parts: list[str] = []
        for label, value in metrics:
            if int(value) > 0:
                parts.append(f"{label}: {int(value)}")
        return ", ".join(parts)

    async def _emitMaintenanceAuditLogs(self, label: str, runType: str, result: object) -> None:
        if self.orbatAuditRuntime is None:
            return
        authorizedBy = self._authorizedByForRunType(runType)
        if label == "ORBAT organization":
            updated = self._readInt(result, "updated")
            if updated <= 0:
                return
            sections = self._readInt(result, "sections")
            await self.orbatAuditRuntime.sendOrbatChangeLog(
                self.botClient,
                change="Sorted General Staff ORBAT sections.",
                authorizedBy=authorizedBy,
                details=f"Sections touched: {sections}, rows updated: {updated}",
                sheetKey="generalStaff",
            )
            return

        if label == "Recruitment ORBAT touchup":
            metrics = [
                ("sorted rows", self._readInt(result, "organized")),
                ("deleted empty rows", self._readInt(result, "deletedRows")),
                ("zero-filled cells", self._readInt(result, "zeroFilled")),
                ("checkbox validations", self._readInt(result, "checkboxValidated")),
            ]
            details = self._buildMetricDetails(metrics)
            if not details:
                return
            await self.orbatAuditRuntime.sendOrbatChangeLog(
                self.botClient,
                change="Recruitment ORBAT touchup applied.",
                authorizedBy=authorizedBy,
                details=details,
                sheetKey="recruitment",
            )
            return

        if label != "Department ORBAT touchup":
            return
        if not isinstance(result, dict):
            return
        nestedResults = result.get("results")
        if not isinstance(nestedResults, dict):
            return

        for divisionName, sheetResult in nestedResults.items():
            if not isinstance(sheetResult, dict):
                continue
            if sheetResult.get("ok") is False:
                continue

            divisionLabel = str(divisionName or "").strip() or "Department"
            if divisionLabel == "ANRD_PAYMENT_MANAGER":
                sectionsSorted = self._readInt(sheetResult, "sectionsSorted")
                if sectionsSorted <= 0:
                    continue
                await self.orbatAuditRuntime.sendOrbatChangeLog(
                    self.botClient,
                    change="Sorted ANRD Payment Manager sections.",
                    authorizedBy=authorizedBy,
                    details=f"Sections sorted: {sectionsSorted}",
                    sheetKey="dept_anrd",
                )
                continue

            metrics = [
                ("sorted rows", self._readInt(sheetResult, "sortedRows")),
                ("organized rows", self._readInt(sheetResult, "organized")),
                ("deleted empty rows", self._readInt(sheetResult, "deletedRows")),
                ("zero-filled cells", self._readInt(sheetResult, "zeroFilled")),
                ("manager zero-fills", self._readInt(sheetResult, "managersZeroFilled")),
                ("employee zero-fills", self._readInt(sheetResult, "employeesZeroFilled")),
                ("spacer rows inserted", self._readInt(sheetResult, "spacerRowsAdded")),
            ]
            details = self._buildMetricDetails(metrics)
            if not details:
                continue

            sheetKey = str(sheetResult.get("sheetKey") or "").strip()
            if divisionLabel == "ANRORS" and not sheetKey:
                sheetKey = "recruitment"
            await self.orbatAuditRuntime.sendOrbatChangeLog(
                self.botClient,
                change=f"{divisionLabel} ORBAT touchup applied.",
                authorizedBy=authorizedBy,
                details=details,
                sheetKey=sheetKey or None,
                divisionKey=None if sheetKey else divisionLabel,
                spreadsheetId=str(getattr(self.config, "deptSpreadsheetId", "") or ""),
                sheetName=divisionLabel if divisionLabel and not sheetKey else None,
                label=f"{divisionLabel} ORBAT",
            )

    def _scheduledPayoutTime(self, year: int, month: int) -> datetime:
        lastDay = calendar.monthrange(year, month)[1]
        return datetime(year, month, lastDay, 12, 0, tzinfo=timezone.utc)

    def nextRecruitmentPayoutRun(self, nowUtc: datetime) -> datetime:
        current = self._scheduledPayoutTime(nowUtc.year, nowUtc.month)
        if current > nowUtc:
            return current
        year = nowUtc.year + 1 if nowUtc.month == 12 else nowUtc.year
        month = 1 if nowUtc.month == 12 else nowUtc.month + 1
        return self._scheduledPayoutTime(year, month)

    def _latestWeeklyRunAtOrBefore(
        self,
        nowUtc: datetime,
        hour: int,
        minute: int,
        weekday: int,
    ) -> datetime:
        candidate = nowUtc.replace(hour=hour, minute=minute, second=0, microsecond=0)
        daysBack = (candidate.weekday() - weekday) % 7
        candidate = candidate - timedelta(days=daysBack)
        if candidate > nowUtc:
            candidate = candidate - timedelta(days=7)
        return candidate

    def nextWeeklyRunAfter(
        self,
        nowUtc: datetime,
        hour: int,
        minute: int,
        weekday: int,
    ) -> datetime:
        latest = self._latestWeeklyRunAtOrBefore(nowUtc, hour, minute, weekday)
        if latest > nowUtc:
            return latest
        return latest + timedelta(days=7)

    async def _runAutomationReportsIfDue(self, now: datetime, scheduledWeekly: datetime) -> None:
        if self._isPaused():
            return
        dailyKey = now.date().isoformat()
        lastDaily = await self.recruitmentService.getSetting("automationDailyDigestLastRun")
        if lastDaily != dailyKey:
            sentDaily = await automationReportsRuntime.runDailyDigest(self.botClient, self.config, self.taskBudgeter)
            if sentDaily:
                await self.recruitmentService.setSetting("automationDailyDigestLastRun", dailyKey)

        weeklyKey = scheduledWeekly.date().isoformat()
        lastWeekly = await self.recruitmentService.getSetting("automationWeeklySummaryLastRun")
        if now >= scheduledWeekly and lastWeekly != weeklyKey:
            sentWeekly = await automationReportsRuntime.runWeeklySummary(self.botClient, self.config, self.taskBudgeter)
            if sentWeekly:
                await self.recruitmentService.setSetting("automationWeeklySummaryLastRun", weeklyKey)

        monthlyKey = now.strftime("%Y-%m")
        lastMonthly = await self.recruitmentService.getSetting("automationMonthlyReportLastRun")
        if now.day == 1 and lastMonthly != monthlyKey:
            sentMonthly = await automationReportsRuntime.runMonthlyReport(self.botClient, self.config, self.taskBudgeter)
            if sentMonthly:
                await self.recruitmentService.setSetting("automationMonthlyReportLastRun", monthlyKey)

    def _weeklySnapshotGuilds(self) -> list[Any]:
        configuredIds = {
            int(raw)
            for raw in (getattr(self.config, "serverSafetyWeeklySnapshotGuildIds", []) or [])
            if int(raw) > 0
        }
        guilds = list(getattr(self.botClient, "guilds", []) or [])
        if configuredIds:
            return [guild for guild in guilds if int(getattr(guild, "id", 0) or 0) in configuredIds]

        allowedCommandGuildIds = {
            int(raw)
            for raw in (getattr(self.config, "allowedCommandGuildIds", []) or [])
            if int(raw) > 0
        }
        if allowedCommandGuildIds:
            filteredGuilds = [
                guild for guild in guilds if int(getattr(guild, "id", 0) or 0) in allowedCommandGuildIds
            ]
            if filteredGuilds:
                return filteredGuilds
        return guilds

    async def _runWeeklyServerSnapshotsIfDue(self, now: datetime, scheduled: datetime) -> None:
        if self.serverSafetyService is None:
            return
        if self._isPaused():
            return
        lastRunRaw = await self.recruitmentService.getSetting("serverSafetyWeeklySnapshotLastRun")
        lastRun = self._parseIsoDatetime(lastRunRaw)
        if lastRun and lastRun >= scheduled:
            return

        targetGuilds = self._weeklySnapshotGuilds()
        if not targetGuilds:
            await self.recruitmentService.setSetting("serverSafetyWeeklySnapshotLastRun", now.isoformat())
            log.info("Weekly server snapshot: no eligible guilds found.")
            return

        createdSnapshots: list[str] = []
        failedGuilds: list[str] = []
        for guild in targetGuilds:
            guildName = str(getattr(guild, "name", "") or getattr(guild, "id", "unknown guild"))
            try:
                path = await self.serverSafetyService.createGuildSnapshot(
                    self.config,
                    guild,
                    label="weekly",
                    snapshotKind="weekly",
                )
            except Exception:
                failedGuilds.append(guildName)
                log.exception("Weekly server snapshot failed for %s.", guildName)
                continue
            createdSnapshots.append(f"{guildName}: {path.name}")

        if failedGuilds:
            log.warning(
                "Weekly server snapshot incomplete: created=%d failed=%d (%s)",
                len(createdSnapshots),
                len(failedGuilds),
                ", ".join(failedGuilds),
            )
            return

        await self.recruitmentService.setSetting("serverSafetyWeeklySnapshotLastRun", now.isoformat())
        log.info(
            "Weekly server snapshot: created=%d [%s]",
            len(createdSnapshots),
            "; ".join(createdSnapshots),
        )

    async def runOrbatMaintenance(self, runType: str) -> None:
        if self._isPaused():
            log.info("ORBAT maintenance (%s) skipped; Jane is paused.", runType)
            return
        if self._orbatMaintenanceLock.locked():
            log.info("ORBAT maintenance (%s) skipped; another maintenance run is active.", runType)
            return
        async with self._orbatMaintenanceLock:
            await self._runOrbatMaintenanceLocked(runType)

    async def _runOrbatMaintenanceLocked(self, runType: str) -> None:
        jobs = []
        if self._nonRecruitmentOrbatWritesEnabled():
            if getattr(self.config, "orbatSpreadsheetId", ""):
                jobs.append(("ORBAT organization", self.orbatSheets.organizeOrbatRows))
            if getattr(self.config, "deptSpreadsheetId", "") or self.departmentOrbatSheets.hasConfiguredLayouts():
                jobs.append(("Department ORBAT touchup", self.departmentOrbatSheets.touchupAllDepartmentSheets))
        else:
            if getattr(self.config, "recruitmentSpreadsheetId", ""):
                jobs.append(("Recruitment ORBAT touchup", self.recruitmentSheets.touchupRecruitmentRows))

        maxAttempts = max(1, int(getattr(self.config, "orbatMaintenanceMaxAttempts", 3)))
        retryBaseDelaySec = float(getattr(self.config, "orbatMaintenanceRetryBaseDelaySec", 20))
        interJobDelaySec = float(getattr(self.config, "orbatMaintenanceInterJobDelaySec", 0))

        for index, (label, func) in enumerate(jobs):
            attempt = 1
            while True:
                startedAt = datetime.now(timezone.utc)
                try:
                    result = await self.taskBudgeter.runBackgroundSheetsThread(func)
                    summary = self._summarizeMaintenanceResult(result)
                    elapsedSec = (datetime.now(timezone.utc) - startedAt).total_seconds()
                    log.info("%s (%s): %s [%.2fs]", label, runType, summary, elapsedSec)
                    try:
                        await self._emitMaintenanceAuditLogs(label, runType, result)
                    except Exception:
                        log.exception("Failed emitting ORBAT audit log for %s (%s).", label, runType)
                    break
                except Exception as exc:
                    elapsedSec = (datetime.now(timezone.utc) - startedAt).total_seconds()
                    isRetryable = self._isRetryableSheetsError(exc, includeTransport=True)
                    if isRetryable and attempt < maxAttempts:
                        delaySec = retryBaseDelaySec * attempt
                        log.warning(
                            "%s (%s) retryable sheets failure on attempt %d/%d [%.2fs]; retrying in %.1fs (%s).",
                            label,
                            runType,
                            attempt,
                            maxAttempts,
                            elapsedSec,
                            delaySec,
                            exc.__class__.__name__,
                        )
                        attempt += 1
                        await asyncio.sleep(delaySec)
                        continue
                    if isRetryable:
                        log.warning(
                            "%s (%s) failed after %d retry attempts due to retryable sheets error (%s: %s).",
                            label,
                            runType,
                            maxAttempts,
                            exc.__class__.__name__,
                            exc,
                        )
                    else:
                        log.exception("%s (%s) failed.", label, runType)
                    break

            if index < len(jobs) - 1 and interJobDelaySec > 0:
                await asyncio.sleep(interJobDelaySec)

        await self._runOrbatMirrorRefresh(runType, maxAttempts, retryBaseDelaySec)

    async def _runOrbatMirrorRefresh(
        self,
        runType: str,
        maxAttempts: int,
        retryBaseDelaySec: float,
    ) -> None:
        if not bool(getattr(self.config, "orbatMirrorEnabled", True)):
            return

        from features.staff.orbat import mirror as orbatMirror

        label = "ORBAT mirror refresh"
        attempt = 1
        while True:
            startedAt = datetime.now(timezone.utc)
            try:
                result = await orbatMirror.refreshAllOrbatMirrors(taskBudgeter=self.taskBudgeter)
                summary = self._summarizeMaintenanceResult(result)
                elapsedSec = (datetime.now(timezone.utc) - startedAt).total_seconds()
                if isinstance(result, dict) and result.get("ok") is False:
                    log.warning("%s (%s): %s [%.2fs]", label, runType, summary, elapsedSec)
                else:
                    log.info("%s (%s): %s [%.2fs]", label, runType, summary, elapsedSec)
                return
            except Exception as exc:
                elapsedSec = (datetime.now(timezone.utc) - startedAt).total_seconds()
                isRetryable = self._isRetryableSheetsError(exc, includeTransport=True)
                if isRetryable and attempt < maxAttempts:
                    delaySec = retryBaseDelaySec * attempt
                    log.warning(
                        "%s (%s) retryable sheets failure on attempt %d/%d [%.2fs]; retrying in %.1fs (%s).",
                        label,
                        runType,
                        attempt,
                        maxAttempts,
                        elapsedSec,
                        delaySec,
                        exc.__class__.__name__,
                    )
                    attempt += 1
                    await asyncio.sleep(delaySec)
                    continue
                if isRetryable:
                    log.warning(
                        "%s (%s) failed after %d retry attempts due to retryable sheets error (%s: %s).",
                        label,
                        runType,
                        maxAttempts,
                        exc.__class__.__name__,
                        exc,
                    )
                else:
                    log.exception("%s (%s) failed.", label, runType)
                return

    async def _runRecruitmentPayoutIfDue(self, now: datetime) -> None:
        if self._isPaused():
            return
        if not self.automaticRecruitmentPayoutEnabled():
            return

        scheduledCurrent = self._scheduledPayoutTime(now.year, now.month)
        prevYear = now.year if now.month > 1 else now.year - 1
        prevMonth = now.month - 1 if now.month > 1 else 12
        scheduledPrev = self._scheduledPayoutTime(prevYear, prevMonth)

        lastRunRaw = await self.recruitmentService.getSetting("recruitmentPayoutLastRun")
        lastRun = self._parseIsoDatetime(lastRunRaw)

        dueAt = None
        if now >= scheduledCurrent and (not lastRun or lastRun < scheduledCurrent):
            dueAt = scheduledCurrent
        elif now >= scheduledPrev and now < scheduledCurrent and (not lastRun or lastRun < scheduledPrev):
            dueAt = scheduledPrev

        if not dueAt:
            return

        result = await self.recruitmentService.processPendingPoints()
        resetRows = 0
        if getattr(self.config, "recruitmentSpreadsheetId", ""):
            maxAttempts = max(1, int(getattr(self.config, "orbatMaintenanceMaxAttempts", 3)))
            retryBaseDelaySec = float(getattr(self.config, "orbatMaintenanceRetryBaseDelaySec", 20))
            resetResult: dict[str, Any] = {}
            lastExc: Exception | None = None
            for attempt in range(1, maxAttempts + 1):
                try:
                    resetResult = await self.taskBudgeter.runBackgroundSheetsThread(self.recruitmentSheets.resetMonthlyPoints)
                    lastExc = None
                    break
                except Exception as exc:
                    lastExc = exc
                    if self._isRetryableSheetsError(exc, includeTransport=True) and attempt < maxAttempts:
                        delaySec = retryBaseDelaySec * attempt
                        log.warning(
                            "Recruitment monthly reset retryable sheets failure on attempt %d/%d; retrying in %.1fs (%s).",
                            attempt,
                            maxAttempts,
                            delaySec,
                            exc.__class__.__name__,
                        )
                        await asyncio.sleep(delaySec)
                        continue
                    break
            if lastExc is not None:
                if self._isRetryableSheetsError(lastExc, includeTransport=True):
                    log.warning(
                        "Recruitment monthly reset skipped after retryable sheets failure (%s: %s).",
                        lastExc.__class__.__name__,
                        lastExc,
                    )
                else:
                    raise lastExc
            resetRows = int(resetResult.get("rows", 0))
        await self.recruitmentService.setSetting("recruitmentPayoutLastRun", now.isoformat())
        log.info(
            "Recruitment payout processed: %d users, %d points; monthly reset rows: %d.",
            result.get("users", 0),
            result.get("points", 0),
            resetRows,
        )

    async def expireStaleSessionsIfDue(self, *, force: bool = False) -> None:
        if self._isPaused():
            return
        if not bool(getattr(self.config, "sessionExpiryEnabled", True)):
            return

        checkIntervalSec = max(30, int(getattr(self.config, "sessionExpiryCheckIntervalSec", 300) or 300))
        maxAgeHours = max(1, int(getattr(self.config, "sessionExpiryHours", 48) or 48))
        now = datetime.now(timezone.utc)

        if (
            not force
            and self._lastSessionExpiryCheckAt is not None
            and (now - self._lastSessionExpiryCheckAt).total_seconds() < checkIntervalSec
        ):
            return

        self._lastSessionExpiryCheckAt = now
        expiredSessionIds = await self.sessionService.expireStaleSessions(maxAgeHours=maxAgeHours)
        if not expiredSessionIds:
            return

        for sessionId in expiredSessionIds:
            try:
                await self.sessionViews.updateSessionMessage(self.botClient, int(sessionId))
            except Exception:
                log.exception("Failed to refresh expired session message for session %s.", sessionId)

        log.info(
            "Session expiry: canceled %d stale session(s) older than %d hour(s): %s",
            len(expiredSessionIds),
            maxAgeHours,
            ", ".join(str(value) for value in expiredSessionIds),
        )

    async def pruneBgIntelligenceReportsIfDue(self, *, force: bool = False) -> None:
        if self._isPaused():
            return

        retentionHours = int(getattr(self.config, "bgIntelligenceReportRetentionHours", 24) or 24)
        if retentionHours <= 0:
            return
        indexRetentionDays = int(getattr(self.config, "bgIntelligenceReportIndexRetentionDays", 90) or 90)
        graphRetentionDays = int(getattr(self.config, "bgIntelligenceIdentityGraphRetentionDays", 365) or 365)
        checkIntervalSec = max(
            300,
            int(getattr(self.config, "bgIntelligenceReportPruneCheckIntervalSec", 3600) or 3600),
        )
        now = datetime.now(timezone.utc)
        if (
            not force
            and self._lastBgIntelPruneAt is not None
            and (now - self._lastBgIntelPruneAt).total_seconds() < checkIntervalSec
        ):
            return

        self._lastBgIntelPruneAt = now
        from features.staff.bgIntelligence import service as bgIntelligenceService

        deletedReports = await bgIntelligenceService.pruneExpiredReports(
            keepHours=retentionHours,
            keepIndexDays=indexRetentionDays,
            keepIdentityGraphDays=graphRetentionDays,
        )
        if deletedReports > 0:
            log.info(
                "BG intelligence retention: deleted %d expired report(s) older than %d hour(s).",
                deletedReports,
                retentionHours,
            )

    async def syncBgItemReviewSpreadsheetsIfDue(self, *, force: bool = False) -> None:
        if self._isPaused():
            return
        if not bool(getattr(self.config, "bgItemReviewSpreadsheetSyncEnabled", True)):
            return

        checkIntervalSec = max(
            60,
            int(getattr(self.config, "bgItemReviewSpreadsheetSyncIntervalSec", 300) or 300),
        )
        now = datetime.now(timezone.utc)

        from features.staff.bgItemReview import spreadsheetSync as bgItemReviewSpreadsheetSync

        if self._lastBgItemReviewSpreadsheetSyncAt is None:
            if not force:
                self._lastBgItemReviewSpreadsheetSyncAt = now
                return
        if (
            not force
            and self._lastBgItemReviewSpreadsheetSyncAt is not None
            and (now - self._lastBgItemReviewSpreadsheetSyncAt).total_seconds() < checkIntervalSec
        ):
            return

        self._lastBgItemReviewSpreadsheetSyncAt = now
        lookbackDays = bgItemReviewSpreadsheetSync._recurringLookbackDays()
        if force or self._bgItemReviewSpreadsheetStartupCatchupPending:
            lookbackDays = bgItemReviewSpreadsheetSync._startupLookbackDays()
        result = await bgItemReviewSpreadsheetSync.syncDeniedSpreadsheetRows(
            self.botClient,
            lookbackDays=lookbackDays,
        )
        self._bgItemReviewSpreadsheetStartupCatchupPending = False
        if str(result.get("reason") or "").strip():
            log.warning(
                "BG item review spreadsheet sync: %s",
                str(result.get("reason") or "").strip(),
            )
            return

        createdCount = int(result.get("created") or 0)
        existingCount = int(result.get("existing") or 0)
        errorCount = int(result.get("errors") or 0)
        deniedCount = int(result.get("denied") or 0)
        if createdCount > 0 or existingCount > 0 or errorCount > 0 or deniedCount > 0:
            log.info(
                "BG item review spreadsheet sync: lookbackDays=%d files=%d rows=%d denied=%d created=%d existing=%d known=%d errors=%d",
                int(result.get("lookbackDays") or lookbackDays),
                int(result.get("files") or 0),
                int(result.get("rows") or 0),
                deniedCount,
                createdCount,
                existingCount,
                int(result.get("known") or 0),
                errorCount,
            )

    async def runGlobalOrbatUpdateLoop(self) -> None:
        await self.botClient.wait_until_ready()
        checkIntervalSec = int(getattr(self.config, "globalOrbatUpdateCheckIntervalSec", 60))
        hour, minute, weekday = self._orbatWeeklyScheduleConfig()  # Sunday default
        if self._lastBgItemReviewSpreadsheetSyncAt is None:
            self._lastBgItemReviewSpreadsheetSyncAt = datetime.now(timezone.utc)
        while not self.botClient.is_closed():
            try:
                if self._isPaused():
                    await asyncio.sleep(max(15, checkIntervalSec))
                    continue
                now = datetime.now(timezone.utc)
                await self.expireStaleSessionsIfDue()
                await self.pruneBgIntelligenceReportsIfDue()
                await self.syncBgItemReviewSpreadsheetsIfDue()
                scheduled = self._latestWeeklyRunAtOrBefore(now, hour, minute, weekday)
                lastRunRaw = await self.recruitmentService.getSetting("orbatMaintenanceLastRun")
                lastRun = self._parseIsoDatetime(lastRunRaw)
                if now >= scheduled and (not lastRun or lastRun < scheduled):
                    await self.runOrbatMaintenance("weekly")
                    await self.recruitmentService.setSetting("orbatMaintenanceLastRun", now.isoformat())

                if now >= scheduled:
                    await self._runWeeklyServerSnapshotsIfDue(now, scheduled)

                await self._runRecruitmentPayoutIfDue(now)
                await self._runAutomationReportsIfDue(now, scheduled)
            except Exception:
                log.exception("Global ORBAT update loop error.")
            await asyncio.sleep(max(15, checkIntervalSec))

    async def runStartupMaintenanceOnce(self) -> None:
        await self.botClient.wait_until_ready()
        delaySec = float(getattr(self.config, "orbatStartupMaintenanceDelaySec", 5))
        if delaySec > 0:
            await asyncio.sleep(delaySec)
        try:
            self.lastConfigSanitySummary = await self.configSanity.runConfigSanityCheck(self.botClient)
            if isinstance(self.lastConfigSanitySummary, dict):
                warningCount = int(self.lastConfigSanitySummary.get("warningCount", 0) or 0)
                errorCount = int(self.lastConfigSanitySummary.get("errorCount", 0) or 0)
                if warningCount or errorCount:
                    log.warning(
                        "Config sanity check: errors=%d warnings=%d",
                        errorCount,
                        warningCount,
                    )
                else:
                    log.info("Config sanity check: no issues found.")

            await self.expireStaleSessionsIfDue(force=True)
            await self.pruneBgIntelligenceReportsIfDue(force=True)
            await self.runOrbatMaintenance("startup")
            await self.recruitmentService.setSetting(
                "orbatMaintenanceLastRun",
                datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            log.exception("ORBAT maintenance (startup) failed.")

    def ensureBackgroundTasksStarted(self) -> None:
        if self.startupMaintenanceTask is None:
            self.startupMaintenanceTask = asyncio.create_task(self.runStartupMaintenanceOnce())
        if self.globalOrbatUpdateTask is None:
            self.globalOrbatUpdateTask = asyncio.create_task(self.runGlobalOrbatUpdateLoop())

    def cancelBackgroundTasks(self) -> None:
        for task in (self.startupMaintenanceTask, self.globalOrbatUpdateTask):
            if task is not None and not task.done():
                task.cancel()
