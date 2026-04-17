from __future__ import annotations

import logging
import os
import importlib
from datetime import datetime, timezone
from typing import Any

import discord

from runtime import orgProfiles

log = logging.getLogger(__name__)

try:
    runtimeRestartStatus = importlib.import_module("runtime.restartStatus")
except ModuleNotFoundError:
    runtimeRestartStatus = None


class BootstrapCoordinator:
    def __init__(
        self,
        *,
        botClient: Any,
        configModule: Any,
        initDbFn: Any,
        loadMultiRegistryFn: Any,
        sessionViews: Any,
        maintenanceCoordinator: Any,
        taskBudgeter: Any,
        recruitmentService: Any,
        helpCommandsModule: Any,
        pluginRegistry: Any,
        extensionNames: list[str],
    ) -> None:
        self.botClient = botClient
        self.config = configModule
        self.initDb = initDbFn
        self.loadMultiRegistry = loadMultiRegistryFn
        self.sessionViews = sessionViews
        self.maintenance = maintenanceCoordinator
        self.taskBudgeter = taskBudgeter
        self.recruitmentService = recruitmentService
        self.helpCommands = helpCommandsModule
        self.pluginRegistry = pluginRegistry
        self.extensionNames = extensionNames

        self.startupGreetingSent = False
        self.readyCommandSyncCompleted = False

    @staticmethod
    def _parseIsoDatetime(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None

    def _logUserVisibleCommandNamingSanity(self) -> None:
        self.helpCommands.logUserVisibleCommandNamingSanity(self.botClient.tree)

    def _isGuildAllowedForCommands(self, guildId: int | None) -> bool:
        if guildId is None or int(guildId) <= 0:
            return False
        allowedGuildIds = {
            int(rawGuildId)
            for rawGuildId in getattr(self.config, "allowedCommandGuildIds", [])
            if int(rawGuildId) > 0
        }
        if not allowedGuildIds:
            return True
        return int(guildId) in allowedGuildIds

    async def syncCommandsOnReady(self) -> None:
        if self.readyCommandSyncCompleted:
            return

        syncGlobalOnReady = bool(getattr(self.config, "syncGlobalCommandsOnReady", False))
        syncGuildsOnReady = bool(getattr(self.config, "syncGuildCommandsOnReady", True))
        copyGlobalsToGuildOnReady = bool(getattr(self.config, "copyGlobalCommandsToGuildOnReady", True))
        clearGlobalWhenUsingGuildSync = bool(getattr(self.config, "clearGlobalCommandsWhenUsingGuildSync", False))
        if not syncGlobalOnReady and not syncGuildsOnReady:
            self.readyCommandSyncCompleted = True
            return

        guildStatuses: dict[str, str] = {}
        registeredCommands = self.botClient.tree.get_commands(guild=None, type=discord.AppCommandType.chat_input)
        if not registeredCommands:
            log.warning(
                "No registered global command definitions found before sync. "
                "Command tree may have been cleared earlier in startup."
            )

        if syncGuildsOnReady and clearGlobalWhenUsingGuildSync:
            log.warning(
                "clearGlobalCommandsWhenUsingGuildSync is enabled, but this behavior is disabled "
                "to prevent wiping in-memory command definitions."
            )

        if syncGuildsOnReady:
            for guild in self.botClient.guilds:
                guildLabel = f"{guild.name} ({guild.id})"
                try:
                    if copyGlobalsToGuildOnReady:
                        self.botClient.tree.copy_global_to(guild=guild)
                    syncedGuild = await self.botClient.tree.sync(guild=guild)
                    if self._isGuildAllowedForCommands(int(guild.id)):
                        guildStatuses[guildLabel] = f"{len(syncedGuild)} command(s)"
                    else:
                        guildStatuses[guildLabel] = "Server Not Recognized"
                except Exception as exc:
                    guildStatuses[guildLabel] = f"FAILED: {exc.__class__.__name__}"
                    log.exception(
                        "Guild command sync failed for guild %s (%s).",
                        guild.id,
                        guild.name,
                    )

        globalCountText = "skipped"
        if syncGlobalOnReady:
            try:
                syncedGlobal = await self.botClient.tree.sync()
                globalCountText = str(len(syncedGlobal))
            except Exception as exc:
                globalCountText = f"FAILED: {exc.__class__.__name__}"
                log.exception("Global command sync (on_ready) failed.")

        guildLines = (
            "\n".join(f"- {guildName}: {status}" for guildName, status in guildStatuses.items())
            if guildStatuses
            else "- (none)"
        )
        log.info(
            "Command sync complete\nGuilds:\n%s\nGlobal: %s",
            guildLines,
            globalCountText,
        )
        self._logUserVisibleCommandNamingSanity()
        self.readyCommandSyncCompleted = True

    async def setupHook(self) -> None:
        await self.initDb()
        try:
            multiRegistry = self.loadMultiRegistry()
            log.info(
                "Multi-ORBAT registry loaded: %d sheet(s): %s",
                len(multiRegistry),
                ", ".join(sorted(multiRegistry.keys())),
            )
        except Exception:
            log.exception("Failed to load multi-ORBAT registry.")

        for extensionName in self.extensionNames:
            await self.botClient.load_extension(extensionName)
            self.pluginRegistry.registerExtension(extensionName)

        try:
            restoredViews = await self.sessionViews.restorePersistentViews(self.botClient)
            log.info(
                "Session persistent views restored: sessions=%d, bgQueues=%d, bgChecks=%d",
                int(restoredViews.get("sessions", 0)),
                int(restoredViews.get("bgQueues", 0)),
                int(restoredViews.get("bgChecks", 0)),
            )
        except Exception:
            log.exception("Failed to restore session persistent views.")

        self.maintenance.ensureBackgroundTasksStarted()

        serverId = os.getenv("DISCORD_GUILD_ID") or str(getattr(self.config, "serverId", "")) or None
        clearGlobal = os.getenv("CLEAR_GLOBAL_COMMANDS")
        if clearGlobal is None:
            clearGlobal = getattr(self.config, "clearGlobalCommands", False)
        else:
            clearGlobal = clearGlobal.strip().lower() in {"1", "true", "yes"}

        clearGuild = os.getenv("CLEAR_GUILD_COMMANDS")
        if clearGuild is None:
            clearGuild = getattr(self.config, "clearGuildCommands", False)
        else:
            clearGuild = clearGuild.strip().lower() in {"1", "true", "yes"}

        if clearGlobal:
            log.warning(
                "clearGlobalCommands was requested, but global clear is skipped to avoid "
                "wiping in-memory command definitions."
            )

        if clearGuild and serverId:
            guild = discord.Object(id=int(serverId))
            self.botClient.tree.clear_commands(guild=guild)
            await self.botClient.tree.sync(guild=guild)

        syncGlobalInSetup = bool(getattr(self.config, "syncGlobalCommandsInSetupHook", False))
        if syncGlobalInSetup:
            synced = await self.botClient.tree.sync()
            log.info(
                "Synced %d global commands: %s",
                len(synced),
                ", ".join(command.name for command in synced),
            )
        else:
            log.info("Skipped setup_hook global sync (on_ready handles command sync).")

    async def onReady(self) -> None:
        log.info("Logged in as %s", self.botClient.user)
        await self.syncCommandsOnReady()
        if runtimeRestartStatus is not None:
            try:
                await runtimeRestartStatus.finalizePendingRestart(
                    botClient=self.botClient,
                    taskBudgeter=self.taskBudgeter,
                )
            except Exception:
                log.exception("Failed to finalize pending restart status message.")
        if self.startupGreetingSent:
            return

        greetingCooldownSec = max(0, int(getattr(self.config, "startupGreetingCooldownSec", 1800) or 1800))
        if greetingCooldownSec > 0:
            try:
                lastGreetingRaw = await self.recruitmentService.getSetting("startupGreetingLastSentAt")
                lastGreetingAt = self._parseIsoDatetime(lastGreetingRaw)
                if lastGreetingAt is not None:
                    if lastGreetingAt.tzinfo is None:
                        lastGreetingAt = lastGreetingAt.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(timezone.utc) - lastGreetingAt.astimezone(timezone.utc)).total_seconds()
                    if elapsed < greetingCooldownSec:
                        log.info(
                            "Startup greeting skipped (cooldown active: %.0fs remaining).",
                            max(0.0, greetingCooldownSec - elapsed),
                        )
                        self.startupGreetingSent = True
                        return
            except Exception:
                log.exception("Failed to evaluate startup greeting cooldown.")

        channelId = int(
            orgProfiles.getOrganizationValue(
                self.config,
                "startupGreetingChannelId",
                orgKey=orgProfiles.getDefaultOrganizationKey(self.config),
                default=0,
            )
            or 0
        )
        if channelId <= 0:
            self.startupGreetingSent = True
            return

        channel = self.botClient.get_channel(channelId)
        if channel is None:
            try:
                channel = await self.taskBudgeter.runDiscord(lambda: self.botClient.fetch_channel(channelId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                channel = None

        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            try:
                await self.taskBudgeter.runDiscord(lambda: channel.send("Hi everyone!"))
                try:
                    await self.recruitmentService.setSetting(
                        "startupGreetingLastSentAt",
                        datetime.now(timezone.utc).isoformat(),
                    )
                except Exception:
                    log.exception("Failed to persist startup greeting timestamp.")
                self.startupGreetingSent = True
            except (discord.Forbidden, discord.HTTPException):
                log.warning("Startup greeting failed for channel %s.", channelId)
        else:
            log.warning("Startup greeting channel %s is unavailable.", channelId)
            self.startupGreetingSent = True
