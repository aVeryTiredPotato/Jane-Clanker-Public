import asyncio
import logging
import os
from pathlib import Path
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv, find_dotenv
import codecs

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.sqlite import closeDb, execute, fetchOne, initDb
from features.staff.recruitment import (
    service as recruitmentService,
    sheets as recruitmentSheets,
)
from features.staff.trainingLog import trainingLogService
from features.staff.sessions import (
    roblox,
    service as sessionService,
    views as sessionViews,
)
from runtime import (
    auditStream as runtimeAuditStream,
    bootstrap as runtimeBootstrap,
    configSanity as runtimeConfigSanity,
    extensionLayout as runtimeExtensionLayout,
    errorLogging as runtimeErrorLogging,
    errors as runtimeErrors,
    eventIngest as runtimeEventIngest,
    featureFlags as runtimeFeatureFlags,
    gamblingApi as runtimeGamblingApi,
    helpCommands as runtimeHelpCommands,
    interaction as interactionRuntime,
    loggingConsole as runtimeLoggingConsole,
    maintenance as runtimeMaintenance,
    metricsExport as runtimeMetricsExport,
    orgFeatureGate as runtimeOrgFeatureGate,
    pauseState as runtimePauseState,
    permissions as runtimePermissions,
    privateServices as runtimePrivateServices,
    pluginRegistry as runtimePluginRegistry,
    retryQueue as runtimeRetryQueue,
    singleInstance as runtimeSingleInstance,
    taskBudgeter,
    textCommands as runtimeTextCommands,
    webhookHealth as runtimeWebhookHealth,
    webhooks as runtimeWebhooks,
)
from silly import commands as sillyCommands


_privateServices = runtimePrivateServices.loadPrivateServices(configModule=config)
departmentOrbatSheets = _privateServices.departmentOrbatSheets
orbatRoleSync = _privateServices.orbatRoleSync
orbatSheets = _privateServices.orbatSheets
runtimeGitUpdate = _privateServices.gitUpdateModule
runtimeProcessControl = _privateServices.processControlModule

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

botClient = commands.Bot(command_prefix="!", intents=intents)
interactionRuntime.installRetrySafeInteractionLayer()
_botStartedAt = datetime.now(timezone.utc)
_lockedPrefixCommandTokens = {
    "!kill",
    "!skin",
    "?janeruntime",
    "?bgleaderboard",
    "?bg-leaderboard",
    "?perm-sim",
    "?permsim",
}
_manualTextCommandTokens = _lockedPrefixCommandTokens | {
    "!casinotoggle",
    "!janeterminal",
    ":)help",
    "?trainingstats",
    "?hoststats",
}
_runtimeControlAllowedWhilePaused = {
    "pause",
    "restart",
}
_runtimePausedMessage = "Jane is currently paused. Use /pause again to resume actions."
_serverNotRecognizedMessage = (
    "Server not recognized. Please reach out to a_very_tired_potato for assistance."
)
_organizationFeatureUnavailableMessage = "This feature is not enabled for this organization."
_temporaryLockMessage = "Commands are temporarily restricted to specific staff during rollout."
_allowedCommandGuildIds = {
    int(guildId)
    for guildId in getattr(config, "allowedCommandGuildIds", [])
    if int(guildId) > 0
}
_activeAppCommandInvocations: dict[tuple[int, str], datetime] = {}
_activeInvocationTtlSec = 600
_roleOrbatSyncLastRunByUser: dict[int, datetime] = {}
_eventDispatcher = runtimeEventIngest.EventIngestDispatcher(
    [runtimeEventIngest.JohnEventLogAdapter()]
)
_featureFlags = runtimeFeatureFlags.FeatureFlagService(configModule=config)
_pluginRegistry = runtimePluginRegistry.PluginRegistry()
_pauseController = runtimePauseState.PauseController()
_singleInstanceLock = runtimeSingleInstance.SingleInstanceLock(
    Path(__file__).resolve().parent / "logs" / "jane-runtime.lock"
)
_retryQueue = runtimeRetryQueue.RetryQueueCoordinator(
    taskBudgeter=taskBudgeter,
    pollIntervalSec=int(getattr(config, "retryQueuePollIntervalSec", 6) or 6),
)
_auditStream = runtimeAuditStream.AuditStream(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
)
_webhookHealthWatcher = runtimeWebhookHealth.WebhookHealthWatcher(
    botClient=botClient,
    taskBudgeter=taskBudgeter,
    auditStream=_auditStream,
    checkIntervalSec=int(getattr(config, "webhookHealthCheckIntervalSec", 600) or 600),
)
_gitUpdateCoordinator = (
    runtimeGitUpdate.GitUpdateCoordinator(
        botClient=botClient,
        configModule=config,
        pauseController=_pauseController,
        processControlModule=runtimeProcessControl,
        repoRoot=os.path.dirname(os.path.abspath(__file__)),
        auditStream=_auditStream,
    )
    if runtimeGitUpdate is not None and runtimeProcessControl is not None
    else None
)
_textCommandRouter: runtimeTextCommands.TextCommandRouter | None = None
_maintenanceCoordinator = runtimeMaintenance.MaintenanceCoordinator(
    botClient=botClient,
    configModule=config,
    recruitmentService=recruitmentService,
    recruitmentSheets=recruitmentSheets,
    departmentOrbatSheets=departmentOrbatSheets,
    orbatSheets=orbatSheets,
    serverSafetyService=_privateServices.serverSafetyService,
    orbatAuditRuntime=_privateServices.orbatAuditRuntime,
    sessionService=sessionService,
    sessionViews=sessionViews,
    taskBudgeter=taskBudgeter,
    configSanityModule=runtimeConfigSanity,
)
_maintenanceCoordinator.pauseController = _pauseController
_bootstrapCoordinator = runtimeBootstrap.BootstrapCoordinator(
    botClient=botClient,
    configModule=config,
    initDbFn=initDb,
    loadMultiRegistryFn=_privateServices.loadMultiOrbatRegistry,
    sessionViews=sessionViews,
    maintenanceCoordinator=_maintenanceCoordinator,
    taskBudgeter=taskBudgeter,
    recruitmentService=recruitmentService,
    helpCommandsModule=runtimeHelpCommands,
    pluginRegistry=_pluginRegistry,
    extensionNames=runtimeExtensionLayout.buildExtensionNames(configModule=config),
)
_errorCoordinator = runtimeErrors.ErrorCoordinator(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
    retryQueue=_retryQueue,
)
_metricsExporter: runtimeMetricsExport.MetricsExporter | None = None
_gamblingApiServer: runtimeGamblingApi.GamblingApiServer | None = None
_trainingLogSyncTask: asyncio.Task | None = None


def _formatUptime(delta: timedelta) -> str:
    totalSec = max(0, int(delta.total_seconds()))
    days, rem = divmod(totalSec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days > 0:
        return f"{days}d {hours:02d}h {minutes:02d}m {seconds:02d}s"
    return f"{hours:02d}h {minutes:02d}m {seconds:02d}s"


def _discordTimestamp(value: datetime, style: str = "s") -> str:
    dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:{style}>"


def _formatBytes(value: int | None) -> str:
    if value is None or value < 0:
        return "unavailable"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    unitIndex = 0
    while size >= 1024.0 and unitIndex < len(units) - 1:
        size /= 1024.0
        unitIndex += 1
    return f"{size:.2f} {units[unitIndex]}"


def _getProcessRssBytes() -> int | None:
    # Windows: use GetProcessMemoryInfo from psapi.
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class _ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = _ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(_ProcessMemoryCounters)
            processHandle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.psapi.GetProcessMemoryInfo(
                processHandle,
                ctypes.byref(counters),
                counters.cb,
            )
            if ok:
                return int(counters.WorkingSetSize)
        except Exception:
            return None

    # POSIX fallback: resource.ru_maxrss.
    try:
        import resource  # type: ignore

        maxRss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if maxRss <= 0:
            return None
        # Linux reports KB, macOS reports bytes.
        if sys.platform == "darwin":
            return maxRss
        return maxRss * 1024
    except Exception:
        return None


def _getProcessResourceSnapshot(nowUtc: datetime) -> dict[str, str]:
    uptimeSec = max((nowUtc - _botStartedAt).total_seconds(), 1.0)
    cpuSec = max(time.process_time(), 0.0)
    cpuCount = max(int(os.cpu_count() or 1), 1)
    avgCpuPercent = (cpuSec / (uptimeSec * cpuCount)) * 100.0
    avgCpuPercent = max(0.0, min(avgCpuPercent, 999.9))

    return {
        "pid": str(os.getpid()),
        "threads": str(threading.active_count()),
        "rss": _formatBytes(_getProcessRssBytes()),
        "cpuPercent": f"{avgCpuPercent:.2f}%",
    }


_metricsExporter = runtimeMetricsExport.MetricsExporter(
    botClient=botClient,
    taskBudgeter=taskBudgeter,
    maintenanceCoordinator=_maintenanceCoordinator,
    retryQueue=_retryQueue,
    featureFlags=_featureFlags,
    webhookHealthWatcher=_webhookHealthWatcher,
    auditStream=_auditStream,
    botStartedAt=_botStartedAt,
    getProcessResourceSnapshot=_getProcessResourceSnapshot,
)
_gamblingApiServer = runtimeGamblingApi.GamblingApiServer(
    configModule=config,
    metricsProvider=_metricsExporter.snapshot,
)
_trainingLogCoordinator = trainingLogService.TrainingLogCoordinator(
    botClient=botClient,
    configModule=config,
    taskBudgeter=taskBudgeter,
    recruitmentService=recruitmentService,
    webhookModule=runtimeWebhooks,
)


async def _runTrainingLogStartupSync() -> None:
    await botClient.wait_until_ready()
    maxAttempts = 3
    retryDelaySec = 30
    for attempt in range(1, maxAttempts + 1):
        logging.info(
            "Training log startup sync attempt %s/%s beginning.",
            attempt,
            maxAttempts,
        )
        await _trainingLogCoordinator.syncRecentMessages()
        if getattr(_trainingLogCoordinator, "_lastReadySyncAt", None) is not None:
            logging.info("Training log startup sync completed.")
            return
        if attempt < maxAttempts:
            logging.warning(
                "Training log startup sync did not complete on attempt %s/%s. Retrying in %ss.",
                attempt,
                maxAttempts,
                retryDelaySec,
            )
            await asyncio.sleep(retryDelaySec)
    logging.warning("Training log startup sync gave up after %s attempt(s).", maxAttempts)


def _startTrainingLogSyncTask() -> None:
    global _trainingLogSyncTask
    if _trainingLogSyncTask is not None and not _trainingLogSyncTask.done():
        return
    task = asyncio.create_task(
        _runTrainingLogStartupSync(),
        name="training-log-backfill",
    )
    _trainingLogSyncTask = task

    def _doneCallback(doneTask: asyncio.Task) -> None:
        try:
            doneTask.result()
        except asyncio.CancelledError:
            logging.info("Training log backfill task was cancelled.")
        except Exception:
            logging.exception("Training log backfill task crashed.")

    task.add_done_callback(_doneCallback)


def _orbatWeeklyScheduleConfig() -> tuple[int, int, int]:
    hour = int(getattr(config, "orbatOrganizationUtcHour", 3))
    minute = int(getattr(config, "orbatOrganizationUtcMinute", 0))
    weekday = int(getattr(config, "orbatOrganizationUtcWeekday", 6))
    return hour, minute, weekday


def _nonRecruitmentOrbatWritesEnabled() -> bool:
    return bool(getattr(config, "nonRecruitmentOrbatWritesEnabled", False)) and bool(
        _privateServices.privateExtensionsEnabled
    )


async def _safeInteractionSend(
    interaction: discord.Interaction,
    message: str,
    *,
    ephemeral: bool = True,
) -> None:
    await interactionRuntime.safeInteractionReply(
        interaction,
        content=message,
        ephemeral=ephemeral,
    )


def _interactionCommandName(interaction: discord.Interaction) -> str:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    parts: list[str] = []

    rootName = str(data.get("name") or "").strip()
    if rootName:
        parts.append(rootName)

    options = data.get("options")
    while isinstance(options, list) and options:
        first = options[0]
        if not isinstance(first, dict):
            break
        optionType = int(first.get("type") or 0)
        if optionType not in {1, 2}:
            break
        optionName = str(first.get("name") or "").strip()
        if optionName:
            parts.append(optionName)
        options = first.get("options")

    return " ".join(parts).strip() or "unknown"


async def _mirrorUnapprovedGuildCommandAttempt(
    *,
    commandName: str,
    userLabel: str,
    userId: int,
    guildName: str,
    guildId: int,
) -> None:
    targetUserId = int(getattr(config, "errorMirrorUserId", 0) or 0)
    if targetUserId <= 0:
        return

    description = (
        f"**Command:** `{str(commandName or 'unknown').strip()}`\n"
        f"**User:** {str(userLabel or 'Unknown User').strip()} (`{int(userId)}`)\n"
        f"**Server:** {str(guildName or 'Unknown Server').strip()} (`{int(guildId)}`)"
    )

    try:
        targetUser = botClient.get_user(targetUserId)
        if targetUser is None:
            targetUser = await taskBudgeter.runDiscord(lambda: botClient.fetch_user(targetUserId))
        if targetUser is None:
            return
        embed = discord.Embed(
            title="Jane Guild Lock Alert",
            description=description,
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        await taskBudgeter.runDiscord(
            lambda: targetUser.send(
                content="A command was attempted in a non-approved guild.",
                embed=embed,
            )
        )
    except Exception:
        try:
            await _retryQueue.enqueue(
                jobType="error-mirror-dm",
                payload={
                    "targetUserId": int(targetUserId),
                    "content": "A command was attempted in a non-approved guild.",
                    "title": "Jane Guild Lock Alert",
                    "description": description,
                },
                maxAttempts=6,
                initialDelaySec=10,
                source="guild-lock",
            )
        except Exception:
            pass


def _invocationKey(
    interaction: discord.Interaction,
    command: app_commands.Command | app_commands.ContextMenu | None = None,
) -> tuple[int, str]:
    commandName = ""
    if command is not None:
        commandName = str(getattr(command, "qualified_name", "") or getattr(command, "name", ""))
    if not commandName:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        commandName = str(data.get("name") or "unknown")
    return int(interaction.user.id), commandName.lower()


def _pruneActiveInvocations() -> None:
    if not _activeAppCommandInvocations:
        return
    now = datetime.now(timezone.utc)
    expired: list[tuple[int, str]] = []
    for key, startedAt in _activeAppCommandInvocations.items():
        if (now - startedAt).total_seconds() > _activeInvocationTtlSec:
            expired.append(key)
    for key in expired:
        _activeAppCommandInvocations.pop(key, None)


async def _maybeSyncRoleBasedOrbats(member: discord.Member, guildId: int) -> None:
    if not _nonRecruitmentOrbatWritesEnabled():
        return
    if not bool(getattr(config, "roleOrbatSyncEnabled", True)):
        return
    if not getattr(config, "deptSpreadsheetId", ""):
        return

    minIntervalSec = max(60, int(getattr(config, "roleOrbatSyncMinIntervalSec", 600) or 600))
    nowUtc = datetime.now(timezone.utc)
    lastRun = _roleOrbatSyncLastRunByUser.get(member.id)
    if lastRun and (nowUtc - lastRun).total_seconds() < minIntervalSec:
        return
    _roleOrbatSyncLastRunByUser[member.id] = nowUtc

    try:
        syncSummary = await orbatRoleSync.syncMemberRoleOrbats(member, guildId)
        if not isinstance(syncSummary, dict):
            return
        if not bool(syncSummary.get("changed", False)):
            return
        for result in syncSummary.get("results", []):
            if not isinstance(result, dict):
                continue
            if not (
                result.get("moved")
                or result.get("updated")
                or result.get("rankUpdated")
                or result.get("created")
            ):
                continue
            logging.info(
                "Role-based ORBAT sync applied for %s (%s): syncType=%s moved=%s updated=%s rankUpdated=%s created=%s section=%s targetRank=%s",
                member.id,
                result.get("robloxUsername", "unknown"),
                result.get("syncType", "unknown"),
                result.get("moved"),
                result.get("updated"),
                result.get("rankUpdated"),
                result.get("created"),
                result.get("section"),
                result.get("targetRank"),
            )
    except Exception:
        logging.exception("Role-based ORBAT sync failed for member %s.", member.id)


async def _postRuntimeWebhookMessage(
    message: discord.Message,
    embed: discord.Embed,
) -> bool:
    return await runtimeWebhooks.sendOwnedWebhookMessage(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Runtime",
        embed=embed,
        username="Jane Runtime",
        avatarUrl=botClient.user.display_avatar.url if botClient.user else None,
        reason="Runtime diagnostics command",
    )


async def _postTerminalWebhookMessage(
    message: discord.Message,
    content: str,
) -> bool:
    return await runtimeWebhooks.sendOwnedWebhookMessage(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Terminal",
        content=content,
        username="Jane Terminal",
        avatarUrl=botClient.user.display_avatar.url if botClient.user else None,
        reason="Read-only terminal diagnostics command",
    )


async def _postCopyServerWebhookMessage(
    message: discord.Message,
    content: str,
    view: discord.ui.View,
) -> bool:
    return await runtimeWebhooks.sendOwnedWebhookMessage(
        botClient=botClient,
        channel=message.channel,
        webhookName="Jane Copyserver",
        content=content,
        view=view,
        username="Jane Copyserver",
        avatarUrl=botClient.user.display_avatar.url if botClient.user else None,
        reason="Hidden copyserver confirmation",
    )


def _hasCohostPermission(member: discord.Member) -> bool:
    return runtimePermissions.hasCohostPermission(member)


def _isCommandExecutionAllowed(userId: int) -> bool:
    return runtimePermissions.isCommandExecutionAllowed(userId)


def _isGuildAllowedForCommands(guildId: int | None) -> bool:
    if guildId is None or guildId <= 0:
        return False
    if not _allowedCommandGuildIds:
        return True
    return guildId in _allowedCommandGuildIds


def _persistAllowedCommandGuildId(guildId: int) -> bool:
    configPath = Path(__file__).resolve().with_name("config.py")
    source = configPath.read_text(encoding="utf-8")
    newline = "\r\n" if "\r\n" in source else "\n"
    lines = source.splitlines()

    startIndex = -1
    endIndex = -1
    for index, line in enumerate(lines):
        if line.strip() == "allowedCommandGuildIds = [":
            startIndex = index
            continue
        if startIndex >= 0 and line.strip() == "]":
            endIndex = index
            break

    if startIndex < 0 or endIndex <= startIndex:
        raise RuntimeError("allowedCommandGuildIds block not found in config.py")

    for line in lines[startIndex + 1 : endIndex]:
        raw = str(line or "").strip().rstrip(",")
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed == int(guildId):
            return False

    lines.insert(endIndex, f"    {int(guildId)},")
    trailingNewline = newline if source.endswith(("\n", "\r\n")) else ""
    configPath.write_text(newline.join(lines) + trailingNewline, encoding="utf-8")
    return True


def _allowGuildForCommands(guildId: int | None) -> str:
    if guildId is None:
        return "invalid"
    try:
        guildIdInt = int(guildId)
    except (TypeError, ValueError):
        return "invalid"
    if guildIdInt <= 0:
        return "invalid"

    configuredGuildIds: list[int] = []
    for raw in (getattr(config, "allowedCommandGuildIds", []) or []):
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            configuredGuildIds.append(parsed)
    alreadyAllowed = guildIdInt in _allowedCommandGuildIds and guildIdInt in configuredGuildIds

    if guildIdInt not in _allowedCommandGuildIds:
        _allowedCommandGuildIds.add(guildIdInt)
    if guildIdInt not in configuredGuildIds:
        configuredGuildIds.append(guildIdInt)
        setattr(config, "allowedCommandGuildIds", configuredGuildIds)

    if alreadyAllowed:
        return "already"

    try:
        wroteConfig = _persistAllowedCommandGuildId(guildIdInt)
    except Exception:
        logging.exception("Failed to persist allowed command guild %s into config.py.", guildIdInt)
        return "runtime-only"

    return "added" if wroteConfig else "already"


def _getTextCommandRouter() -> runtimeTextCommands.TextCommandRouter:
    global _textCommandRouter
    if _textCommandRouter is None:
        _textCommandRouter = runtimeTextCommands.TextCommandRouter(
            botClient=botClient,
            configModule=config,
            sessionService=sessionService,
            sessionViews=sessionViews,
            taskBudgeter=taskBudgeter,
            helpCommandsModule=runtimeHelpCommands,
            permissionsModule=runtimePermissions,
            maintenanceCoordinator=_maintenanceCoordinator,
            botStartedAt=_botStartedAt,
            formatUptime=_formatUptime,
            discordTimestamp=_discordTimestamp,
            getProcessResourceSnapshot=_getProcessResourceSnapshot,
            sendRuntimeWebhookMessage=_postRuntimeWebhookMessage,
            sendTerminalWebhookMessage=_postTerminalWebhookMessage,
            sendCopyServerWebhookMessage=_postCopyServerWebhookMessage,
            hasCohostPermission=_hasCohostPermission,
            isGuildAllowedForCommands=_isGuildAllowedForCommands,
            allowGuildForCommands=_allowGuildForCommands,
            orbatWeeklyScheduleConfig=_orbatWeeklyScheduleConfig,
            trainingLogCoordinator=_trainingLogCoordinator,
            serverSafetyService=_privateServices.serverSafetyService,
            gitUpdateCoordinator=_gitUpdateCoordinator,
            generalErrorLogPath=str(getattr(botClient, "runtimeServices", {}).get("generalErrorLogPath", "") or ""),
        )
    return _textCommandRouter


async def _handleJaneHelp(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleJaneHelp(message)


async def _handleJaneRuntime(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleJaneRuntime(message)


async def _handleJaneTerminal(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleJaneTerminal(message)


async def _handleShutdownCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleShutdown(message)


async def _handleAllowServerCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleAllowServer(message)


async def _handleMirrorTrainingHistoryCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleMirrorTrainingHistory(message)


async def _handleCopyServerCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleCopyServer(message)


async def _handleBgCheckCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleBgCheckCommand(message)


async def _handleBgLeaderboardCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handleBgLeaderboardCommand(message)


async def _handlePermissionSimulatorCommand(message: discord.Message) -> bool:
    return await _getTextCommandRouter().handlePermissionSimulatorCommand(message)


async def _handleTrainingStatsCommand(message: discord.Message) -> bool:
    return await _trainingLogCoordinator.handleTrainingStats(message)


async def _retryErrorMirrorDmHandler(payload: dict) -> None:
    targetUserId = int(payload.get("targetUserId") or 0)
    if targetUserId <= 0:
        return

    targetUser = botClient.get_user(targetUserId)
    if targetUser is None:
        targetUser = await taskBudgeter.runDiscord(lambda: botClient.fetch_user(targetUserId))
    if targetUser is None:
        raise RuntimeError("target user unavailable")

    title = str(payload.get("title") or "Jane Error Mirror").strip()[:200]
    description = str(payload.get("description") or "").strip()
    if len(description) > 3800:
        description = f"{description[:3797]}..."
    embed = discord.Embed(
        title=title,
        description=description or "(no description)",
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    content = str(payload.get("content") or "").strip()
    await taskBudgeter.runDiscord(lambda: targetUser.send(content=content or None, embed=embed))


@botClient.event
async def setup_hook() -> None:
    runtimeErrorLogging.installAsyncioExceptionLogging(asyncio.get_running_loop())
    _retryQueue.registerHandler("error-mirror-dm", _retryErrorMirrorDmHandler)
    _retryQueue.start()
    _webhookHealthWatcher.start()
    botClient.runtimeServices = {
        "featureFlags": _featureFlags,
        "pluginRegistry": _pluginRegistry,
        "pauseController": _pauseController,
        "retryQueue": _retryQueue,
        "auditStream": _auditStream,
        "metricsExporter": _metricsExporter,
        "webhookHealthWatcher": _webhookHealthWatcher,
        "gitUpdateCoordinator": _gitUpdateCoordinator,
        "generalErrorLogPath": runtimeErrorLogging.currentProcessLogSummary(configModule=config),
        "createBgCheckQueue": (
            lambda *, guild, channel, actor, sourceMessage=None: _getTextCommandRouter().createBgCheckQueue(
                guild=guild,
                channel=channel,
                actor=actor,
                sourceMessage=sourceMessage,
            )
        ),
    }
    await _bootstrapCoordinator.setupHook()
    await _gamblingApiServer.start()
    if _gitUpdateCoordinator is not None:
        _gitUpdateCoordinator.start()
    _startTrainingLogSyncTask()


@botClient.event
async def on_ready() -> None:
    await _bootstrapCoordinator.onReady()
    logging.info("on_ready reached; ensuring training log startup sync task is running.")
    _startTrainingLogSyncTask()


@botClient.check
async def prefixCommandSafetyCheck(ctx: commands.Context) -> bool:
    guildId = int(getattr(getattr(ctx, "guild", None), "id", 0) or 0)
    if not _isGuildAllowedForCommands(guildId):
        if guildId > 0:
            asyncio.create_task(
                _mirrorUnapprovedGuildCommandAttempt(
                    commandName=str(getattr(getattr(ctx, "command", None), "qualified_name", "unknown")),
                    userLabel=str(getattr(ctx, "author", "Unknown User")),
                    userId=int(getattr(getattr(ctx, "author", None), "id", 0) or 0),
                    guildName=str(getattr(getattr(ctx, "guild", None), "name", "Unknown Server")),
                    guildId=guildId,
                )
            )
        try:
            await ctx.reply(
                _serverNotRecognizedMessage,
                mention_author=False,
            )
        except Exception:
            pass
        return False
    commandName = str(getattr(getattr(ctx, "command", None), "qualified_name", "") or getattr(getattr(ctx, "command", None), "name", "") or "").strip().lower()
    orgFeatureEnabled, orgFeatureKey = runtimeOrgFeatureGate.isCommandEnabledForGuild(config, guildId, commandName)
    if not orgFeatureEnabled:
        try:
            await ctx.reply(
                f"{_organizationFeatureUnavailableMessage} (`{orgFeatureKey}`)",
                mention_author=False,
            )
        except Exception:
            pass
        return False
    if _isCommandExecutionAllowed(int(ctx.author.id)):
        return True
    try:
        await ctx.reply(
            _temporaryLockMessage,
            mention_author=False,
        )
    except Exception:
        pass
    return False


@botClient.tree.interaction_check
async def interactionSafetyCheck(interaction: discord.Interaction) -> bool:
    if interaction.type is not discord.InteractionType.application_command:
        return True
    commandName = ""
    if isinstance(interaction.data, dict):
        commandName = str(interaction.data.get("name") or "").strip().lower()
    guildId = int(getattr(getattr(interaction, "guild", None), "id", 0) or 0)
    if not _isGuildAllowedForCommands(guildId):
        if guildId > 0:
            asyncio.create_task(
                _mirrorUnapprovedGuildCommandAttempt(
                    commandName=_interactionCommandName(interaction),
                    userLabel=str(getattr(interaction, "user", "Unknown User")),
                    userId=int(getattr(getattr(interaction, "user", None), "id", 0) or 0),
                    guildName=str(getattr(getattr(interaction, "guild", None), "name", "Unknown Server")),
                    guildId=guildId,
                )
            )
        await _safeInteractionSend(
            interaction,
            _serverNotRecognizedMessage,
            ephemeral=True,
        )
        return False
    if not _isCommandExecutionAllowed(int(interaction.user.id)):
        await _safeInteractionSend(
            interaction,
            _temporaryLockMessage,
            ephemeral=True,
        )
        return False
    orgFeatureEnabled, orgFeatureKey = runtimeOrgFeatureGate.isCommandEnabledForGuild(config, guildId, commandName)
    if not orgFeatureEnabled:
        await _safeInteractionSend(
            interaction,
            f"{_organizationFeatureUnavailableMessage} (`{orgFeatureKey}`)",
            ephemeral=True,
        )
        return False
    if _pauseController.isPaused() and commandName not in _runtimeControlAllowedWhilePaused:
        await _safeInteractionSend(
            interaction,
            _runtimePausedMessage,
            ephemeral=True,
        )
        return False
    featureEnabled, featureKey = await _featureFlags.isCommandEnabled(int(interaction.guild.id), commandName)
    if not featureEnabled:
        await _safeInteractionSend(
            interaction,
            f"This command is disabled in this server (feature `{featureKey}`).",
            ephemeral=True,
        )
        return False
    _pruneActiveInvocations()
    key = _invocationKey(interaction)
    if key in _activeAppCommandInvocations:
        await _safeInteractionSend(
            interaction,
            "That command is already running for you. Please wait for it to finish.",
            ephemeral=True,
        )
        return False
    _activeAppCommandInvocations[key] = datetime.now(timezone.utc)
    if isinstance(interaction.user, discord.Member):
        asyncio.create_task(_maybeSyncRoleBasedOrbats(interaction.user, interaction.guild.id))
    return True


@botClient.event
async def on_app_command_completion(
    interaction: discord.Interaction,
    command: app_commands.Command | app_commands.ContextMenu,
) -> None:
    _activeAppCommandInvocations.pop(_invocationKey(interaction, command), None)
    _activeAppCommandInvocations.pop(_invocationKey(interaction), None)


@botClient.tree.error
async def onAppCommandError(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    _activeAppCommandInvocations.pop(_invocationKey(interaction), None)
    commandName = ""
    if isinstance(interaction.data, dict):
        commandName = str(interaction.data.get("name") or "")
    await _auditStream.logEvent(
        source="app-command",
        action="command error",
        guildId=int(getattr(getattr(interaction, "guild", None), "id", 0) or 0),
        actorId=int(getattr(getattr(interaction, "user", None), "id", 0) or 0),
        targetType="command",
        targetId=commandName or "unknown",
        severity="ERROR",
        details={"errorType": error.__class__.__name__, "error": str(error)},
        authorizedBy="runtime",
        postToDiscord=False,
    )
    await _errorCoordinator.handleAppCommandError(
        interaction=interaction,
        error=error,
        safeInteractionSend=lambda itx, message: _safeInteractionSend(
            itx,
            message,
            ephemeral=True,
        ),
    )


@botClient.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
    await _errorCoordinator.handlePrefixCommandError(ctx=ctx, error=error)


@botClient.event
async def on_close() -> None:
    _maintenanceCoordinator.cancelBackgroundTasks()
    if _gitUpdateCoordinator is not None:
        await _gitUpdateCoordinator.stop()
    await _webhookHealthWatcher.stop()
    await _retryQueue.stop()
    await _gamblingApiServer.stop()
    await roblox.closeHttpSession()
    await closeDb()

async def _alreadyProcessedJohnLog(messageId: int) -> bool:
    row = await fetchOne(
        "SELECT messageId FROM john_event_log_messages WHERE messageId = ?",
        (messageId,),
    )
    return row is not None

async def _markProcessedJohnLog(message: discord.Message, hostId: int | None, category: str) -> None:
    await execute(
        """
        INSERT OR IGNORE INTO john_event_log_messages
        (messageId, channelId, hostId, eventCategory)
        VALUES (?, ?, ?, ?)
        """,
        (message.id, message.channel.id, hostId, category),
    )


async def _handleIngestedEvent(
    message: discord.Message,
    event: runtimeEventIngest.IngestEvent,
) -> None:
    if event.eventType != "john.orbatIncrement":
        return

    if await _alreadyProcessedJohnLog(event.messageId):
        return

    if not _nonRecruitmentOrbatWritesEnabled():
        return

    hostId = int(event.hostId or 0)
    if hostId <= 0:
        logging.warning("John log message %s missing host mention.", message.id)
        return

    category = str(event.payload.get("eventCategory") or "other").strip().lower()
    columnKey = "shifts" if category == "shift" else "otherEvents"

    row = orbatSheets.incrementEventCount(hostId, columnKey, 1)
    if row == 0:
        lookup = await roblox.fetchRobloxUser(hostId)
        if lookup.robloxUsername:
            row = orbatSheets.incrementEventCount(
                hostId,
                columnKey,
                1,
                robloxUser=lookup.robloxUsername,
            )

    if row == 0:
        logging.warning("ORBAT row not found for host %s in John log %s.", hostId, message.id)
        return

    await _markProcessedJohnLog(message, hostId, category if category in {"shift", "other"} else "other")


def _firstLowerToken(content: str) -> str:
    return _getTextCommandRouter().firstLowerToken(content)

async def _processCommands(message: discord.Message) -> None:
    await botClient.process_commands(message)


@botClient.event
async def on_message(message: discord.Message) -> None:
    try:
        await _trainingLogCoordinator.handleSourceMessage(message)
    except Exception:
        logging.exception("Training log capture failed for message %s.", getattr(message, "id", 0))
    if not message.author.bot:
        _getTextCommandRouter().noteCopyServerWarningMessage(message)
    if _pauseController.isPaused():
        if not message.author.bot:
            token = _firstLowerToken(message.content or "")
            guildId = int(getattr(getattr(message, "guild", None), "id", 0) or 0)
            orgFeatureEnabled, orgFeatureKey = runtimeOrgFeatureGate.isTokenEnabledForGuild(config, guildId, token)
            if not orgFeatureEnabled:
                try:
                    await message.channel.send(f"{_organizationFeatureUnavailableMessage} (`{orgFeatureKey}`)")
                except Exception:
                    pass
                return
            if token == "!allowserver":
                if await _handleAllowServerCommand(message):
                    return
            if token == "!copyserver":
                if await _handleCopyServerCommand(message):
                    return
            if token == "!shutdown":
                if await _handleShutdownCommand(message):
                    return
            if token == "!mirrortraininghistory":
                if await _handleMirrorTrainingHistoryCommand(message):
                    return
            if token == "!janeterminal":
                if await _handleJaneTerminal(message):
                    return
            if token in _manualTextCommandTokens or token in _lockedPrefixCommandTokens:
                try:
                    await message.channel.send(_runtimePausedMessage)
                except Exception:
                    pass
                return
            ctx = await botClient.get_context(message)
            if ctx.command is not None:
                try:
                    await message.channel.send(_runtimePausedMessage)
                except Exception:
                    pass
                return
        return
    if not message.author.bot:
        token = _firstLowerToken(message.content or "")
        guildId = int(getattr(getattr(message, "guild", None), "id", 0) or 0)
        orgFeatureEnabled, orgFeatureKey = runtimeOrgFeatureGate.isTokenEnabledForGuild(config, guildId, token)
        if not orgFeatureEnabled:
            try:
                await message.channel.send(f"{_organizationFeatureUnavailableMessage} (`{orgFeatureKey}`)")
            except Exception:
                pass
            return
        if await _handleAllowServerCommand(message):
            return
        if await _handleMirrorTrainingHistoryCommand(message):
            return
        if token in _manualTextCommandTokens:
            if not _isGuildAllowedForCommands(guildId):
                if guildId > 0:
                    asyncio.create_task(
                        _mirrorUnapprovedGuildCommandAttempt(
                            commandName=token,
                            userLabel=str(message.author),
                            userId=int(message.author.id),
                            guildName=str(getattr(message.guild, "name", "Unknown Server")),
                            guildId=guildId,
                        )
                    )
                try:
                    await message.channel.send(_serverNotRecognizedMessage)
                except Exception:
                    pass
                return
        await sillyCommands.maybeHandleSillyMentions(message, botClient)
        if await _handleJaneHelp(message):
            return
        if not _isCommandExecutionAllowed(int(message.author.id)):
            if token in _lockedPrefixCommandTokens:
                await message.channel.send(_temporaryLockMessage)
                return
            return await _processCommands(message)
        if await sillyCommands.maybeHandleSixtySevenSpam(message):
            return
        if await sillyCommands.handleSkinCommand(
            message,
            botClient,
            hasSkinPermission=_hasCohostPermission,
        ):
            return
        if await sillyCommands.handleKillCommand(message, botClient):
            return
        if await sillyCommands.handleCasinoToggleCommand(message):
            return
        if await _handleTrainingStatsCommand(message):
            return
        if await _handleJaneTerminal(message):
            return
        if await _handleShutdownCommand(message):
            return
        if await _handleCopyServerCommand(message):
            return
        if await _handleJaneRuntime(message):
            return
        if await _handleBgLeaderboardCommand(message):
            return
        if await _handlePermissionSimulatorCommand(message):
            return
        return await _processCommands(message)

    parsedEvents = await _eventDispatcher.parse(message)
    for event in parsedEvents:
        try:
            await _handleIngestedEvent(message, event)
        except Exception:
            logging.exception(
                "Event ingest handler failed (source=%s type=%s message=%s).",
                event.source,
                event.eventType,
                message.id,
            )
    return await _processCommands(message)


@botClient.event
async def on_message_edit(before: discord.Message, after: discord.Message) -> None:
    if int(getattr(before, "id", 0) or 0) != int(getattr(after, "id", 0) or 0):
        return
    try:
        await _trainingLogCoordinator.handleSourceMessage(after)
    except Exception:
        logging.exception("Training log capture failed for edited message %s.", getattr(after, "id", 0))

@botClient.listen("on_interaction")
async def handleRobloxRetry(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    try:
        handled = await sessionViews.handleRobloxRetryInteraction(interaction)
    except Exception:
        logging.exception("Roblox retry handler failed.")
        return
    if handled:
        return
    try:
        handled = await sessionViews.handleInventoryRetryInteraction(interaction)
    except Exception:
        logging.exception("Inventory retry handler failed.")
        return
    if handled:
        return

def has_utf8_bom(filepath):
    with open(filepath, 'rb') as f:
        header = f.read(3)
        return header.startswith(codecs.BOM_UTF8)

if __name__ == "__main__":
    runtimeLoggingConsole.configureConsoleLogging(level=logging.INFO)
    generalErrorLogPath = runtimeErrorLogging.configureGeneralErrorLogging(configModule=config)
    runtimeErrorLogging.installGlobalExceptionHooks()
    logging.info("General error log enabled: %s", generalErrorLogPath)
    lockAcquired, lockOwnerPid = _singleInstanceLock.acquire()
    if not lockAcquired:
        raise RuntimeError(
            f"Another Jane process is already running for this repo (pid={int(lockOwnerPid or 0) or 'unknown'})."
        )
    envPath = find_dotenv(usecwd=True)
    loadedEnvironmentVariables = load_dotenv(envPath, verbose=True, override=True)
    if not loadedEnvironmentVariables:
        raise RuntimeError(".env file not correctly loaded.")
    if has_utf8_bom(envPath):
        raise RuntimeError(".env file has a UTF-8 BOM.")
    logging.info(f"Loaded Environment Variables: {loadedEnvironmentVariables}")
    logging.info(f"Current .env file: {envPath}")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is not set.")
    if runtimeProcessControl is not None:
        runtimeProcessControl.clearRestartRequest()
    restartRequested = False
    try:
        botClient.run(token, log_handler=None)
        restartRequested = bool(runtimeProcessControl is not None and runtimeProcessControl.restartRequested())
    finally:
        _singleInstanceLock.release()
    if restartRequested and runtimeProcessControl is not None:
        logging.warning("Runtime restart requested; relaunching Jane.")
        runtimeProcessControl.relaunchCurrentProcess(scriptPath=__file__)

