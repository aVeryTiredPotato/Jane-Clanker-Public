from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import discord

from db.sqlite import dbPath as sqliteDbPath
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime.prefix import bg as prefixBg
from runtime.prefix import copyserver as prefixCopyserver
from runtime.prefix import runtime_admin as prefixRuntimeAdmin
from runtime.prefix import utility as prefixUtility


class TextCommandRouter:
    def __init__(
        self,
        *,
        botClient: Any,
        configModule: Any,
        sessionService: Any,
        sessionViews: Any,
        taskBudgeter: Any,
        helpCommandsModule: Any,
        permissionsModule: Any,
        maintenanceCoordinator: Any,
        botStartedAt: datetime,
        formatUptime: Callable[[Any], str],
        discordTimestamp: Callable[[datetime, str], str],
        getProcessResourceSnapshot: Callable[[datetime], dict[str, str]],
        sendRuntimeWebhookMessage: Callable[[discord.Message, discord.Embed], Any],
        sendTerminalWebhookMessage: Callable[[discord.Message, str], Any],
        sendCopyServerWebhookMessage: Callable[[discord.Message, str, discord.ui.View], Any],
        hasCohostPermission: Callable[[discord.Member], bool],
        isGuildAllowedForCommands: Callable[[int], bool],
        allowGuildForCommands: Callable[[int], str],
        orbatWeeklyScheduleConfig: Callable[[], tuple[int, int, int]],
        trainingLogCoordinator: Any | None = None,
        serverSafetyService: Any | None = None,
        gitUpdateCoordinator: Any | None = None,
        generalErrorLogPath: str = "",
    ) -> None:
        self.botClient = botClient
        self.config = configModule
        self.sessionService = sessionService
        self.sessionViews = sessionViews
        self.taskBudgeter = taskBudgeter
        self.helpCommands = helpCommandsModule
        self.permissions = permissionsModule
        self.maintenance = maintenanceCoordinator
        self.botStartedAt = botStartedAt
        self.formatUptime = formatUptime
        self.discordTimestamp = discordTimestamp
        self.getProcessResourceSnapshot = getProcessResourceSnapshot
        self.sendRuntimeWebhookMessage = sendRuntimeWebhookMessage
        self.sendTerminalWebhookMessage = sendTerminalWebhookMessage
        self.sendCopyServerWebhookMessage = sendCopyServerWebhookMessage
        self.hasCohostPermission = hasCohostPermission
        self.isGuildAllowedForCommands = isGuildAllowedForCommands
        self.allowGuildForCommands = allowGuildForCommands
        self.orbatWeeklyScheduleConfig = orbatWeeklyScheduleConfig
        self.trainingLogCoordinator = trainingLogCoordinator
        self.serverSafetyService = serverSafetyService
        self.gitUpdateCoordinator = gitUpdateCoordinator
        self.generalErrorLogPath = str(generalErrorLogPath or "").strip()
        self._activeCopyServerGuildIds: set[int] = set()
        self._pendingApprovedGuildCopyServerWarnings: dict[tuple[int, int], datetime] = {}
        self._approvedGuildCopyServerWarningTtlSec = 300

    async def createBgCheckQueue(
        self,
        *,
        guild: discord.Guild,
        channel: discord.abc.Messageable,
        actor: discord.Member,
        sourceMessage: discord.Message | None = None,
    ) -> tuple[bool, str]:
        return await prefixBg.createBgCheckQueue(
            self,
            guild=guild,
            channel=channel,
            actor=actor,
            sourceMessage=sourceMessage,
        )

    def _formatIsoTimestampOrNever(self, rawValue: object) -> str:
        rawText = str(rawValue or "").strip()
        if not rawText:
            return "`never`"
        try:
            parsed = datetime.fromisoformat(rawText)
        except ValueError:
            return f"`{rawText}`"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return self.discordTimestamp(parsed.astimezone(timezone.utc), "f")

    def firstLowerToken(self, content: str) -> str:
        token, _ = normalization.commandParts(content)
        return token

    def indexToken(self, content: str, index: int) -> str:
        return normalization.tokenAt(content, index)

    def _formatTerminalTime(self, rawValue: object) -> str:
        rawText = str(rawValue or "").strip()
        if not rawText:
            return "never"
        try:
            parsed = datetime.fromisoformat(rawText)
        except ValueError:
            return rawText
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _janeTerminalAllowedUserId(self) -> int:
        try:
            configured = int(
                getattr(self.config, "janeTerminalAllowedUserId", 0)
                or getattr(self.config, "errorMirrorUserId", 0)
                or 0
            )
        except (TypeError, ValueError):
            configured = 0
        return configured if configured > 0 else 0

    def _copyServerAllowed(self, userId: int) -> bool:
        return int(userId or 0) in prefixCopyserver.COPYSERVER_ALLOWED_USER_IDS

    def _shutdownAllowed(self, userId: int) -> bool:
        try:
            configuredIds = [int(value or 0) for value in list(getattr(self.config, "opsAllowedUserIds", []) or [])]
        except Exception:
            configuredIds = []
        allowedIds = {userId for userId in configuredIds if int(userId or 0) > 0}
        if not allowedIds:
            allowedIds = set(prefixCopyserver.COPYSERVER_ALLOWED_USER_IDS)
        return int(userId or 0) in allowedIds

    def noteCopyServerWarningMessage(self, message: discord.Message) -> None:
        prefixCopyserver.noteCopyServerWarningMessage(self, message)

    def _readGeneralErrorLogTail(self, *, maxLines: int = 10, maxChars: int = 900) -> list[str]:
        logPathText = str(self.generalErrorLogPath or "").strip()
        if not logPathText:
            return ["(general error log path unavailable)"]
        logPath = Path(logPathText)
        if not logPath.exists():
            return [f"(log file missing: {logPath.name})"]
        try:
            lines = logPath.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            return [f"(failed to read {logPath.name})"]

        filtered = [line.rstrip() for line in lines if line.strip() and not set(line.strip()) <= {"-"}]
        if not filtered:
            return [f"(no entries in {logPath.name})"]

        tailLines = filtered[-maxLines:]
        clipped: list[str] = []
        remainingChars = maxChars
        for line in tailLines:
            compactLine = line[:180]
            if len(compactLine) + 1 > remainingChars:
                break
            clipped.append(compactLine)
            remainingChars -= len(compactLine) + 1
        return clipped or ["(log tail truncated)"]

    def _dbPath(self) -> Path:
        return Path(sqliteDbPath)

    def _buildJaneTerminalContent(self) -> str:
        now = datetime.now(timezone.utc)
        uptime = self.formatUptime(now - self.botStartedAt)
        processResources = self.getProcessResourceSnapshot(now)
        latencyValue = float(getattr(self.botClient, "latency", 0.0) or 0.0)
        latencyText = f"{round(latencyValue * 1000)} ms" if math.isfinite(latencyValue) else "unavailable"

        gitStats: dict[str, Any] = {}
        if self.gitUpdateCoordinator is not None:
            try:
                gitStats = dict(self.gitUpdateCoordinator.getStats())
            except Exception:
                gitStats = {}

        gitCheckText = self._formatTerminalTime(gitStats.get("lastCheckAt"))
        gitUpdateText = self._formatTerminalTime(gitStats.get("lastUpdateAt"))
        gitResultText = str(gitStats.get("lastResult") or "idle").strip() or "idle"

        lines = [
            f"Jane Terminal :: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"status      ONLINE",
            f"uptime      {uptime}",
            f"ping        {latencyText}",
            f"guilds      {len(self.botClient.guilds)}",
            f"cogs        {len(self.botClient.cogs)}",
            f"rss         {processResources.get('rss', 'unavailable')}",
            f"dbSize      {((self._dbPath().stat().st_size / (1024 * 1024)) if self._dbPath().exists() else 0.0):.2f} MB",
            f"gitCheck    {gitCheckText}",
            f"gitUpdate   {gitUpdateText}",
            f"gitResult   {gitResultText}",
            "-" * 54,
            "general-errors tail",
        ]
        lines.extend(self._readGeneralErrorLogTail())

        body = "\n".join(lines)
        if len(body) > 1900:
            body = body[:1897] + "..."
        return f"```ansi\n{body}\n```"

    async def _deleteSourceIfManageable(self, message: discord.Message) -> bool:
        guild = message.guild
        if guild is None or guild.me is None:
            return False
        if not message.channel.permissions_for(guild.me).manage_messages:
            return False
        return await interactionRuntime.safeMessageDelete(message)

    async def handleUsernameToUserId(self, message: discord.Message) -> bool:
        return await prefixUtility.handleUsernameToUserId(self, message)
    async def handleChannelPurge(self, message: discord.Message) -> bool:
        return await prefixUtility.handleChannelPurge(self, message)


    async def handleJaneHelp(self, message: discord.Message) -> bool:
        return await prefixRuntimeAdmin.handleJaneHelp(self, message)

    async def handleJaneRuntime(self, message: discord.Message) -> bool:
        return await prefixRuntimeAdmin.handleJaneRuntime(self, message)

    async def handleJaneTerminal(self, message: discord.Message) -> bool:
        return await prefixRuntimeAdmin.handleJaneTerminal(self, message)

    async def handleShutdown(self, message: discord.Message) -> bool:
        return await prefixRuntimeAdmin.handleShutdown(self, message)

    async def handleAllowServer(self, message: discord.Message) -> bool:
        return await prefixRuntimeAdmin.handleAllowServer(self, message)

    async def handleMirrorTrainingHistory(self, message: discord.Message) -> bool:
        return await prefixRuntimeAdmin.handleMirrorTrainingHistory(self, message)

    async def handleCopyServer(self, message: discord.Message) -> bool:
        return await prefixCopyserver.handleCopyServer(self, message)

    async def handleBgCheckCommand(self, message: discord.Message) -> bool:
        return await prefixBg.handleBgCheckCommand(self, message)

    async def handleBgLeaderboardCommand(self, message: discord.Message) -> bool:
        return await prefixBg.handleBgLeaderboardCommand(self, message)

    async def handlePermissionSimulatorCommand(self, message: discord.Message) -> bool:
        return await prefixUtility.handlePermissionSimulatorCommand(self, message)

    async def handlePairDbNamesCommand(self, message: discord.Message) -> bool:
        return await prefixUtility.handlePairDbNamesCommand(self, message)
