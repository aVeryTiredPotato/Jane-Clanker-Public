from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import discord

from db.sqlite import dbPath as sqliteDbPath
from runtime import bgQueueCommand as runtimeBgQueueCommand
from runtime import copyServerState as runtimeCopyServerState
from runtime import helpMenu as runtimeHelpMenu
from runtime import interaction as interactionRuntime
from runtime import orgProfiles
from runtime import webhooks as runtimeWebhooks
from features.staff.sessions import bgBuckets

try:
    from features.operations.serverSafety.filters import filterLiveChannels, filterSnapshotChannelRows
    from features.operations.serverSafety.snapshotStore import readSnapshot
except ModuleNotFoundError:
    def filterLiveChannels(configModule: Any, channels: list[Any] | tuple[Any, ...]) -> list[Any]:
        return list(channels or [])

    def filterSnapshotChannelRows(configModule: Any, rows: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
        return list(rows or [])

    def readSnapshot(path: os.PathLike[str] | str) -> dict[str, Any]:
        raise RuntimeError("Snapshot support is unavailable on this build.")


_copyServerAllowedUserIds = {
    331660652672319488,
    1086979130572165231,
}
log = logging.getLogger(__name__)


def _copyServerRoleCounter(snapshot: dict[str, Any]) -> Counter[str]:
    out: Counter[str] = Counter()
    for row in list(snapshot.get("roles") or []):
        if bool(row.get("isDefault")) or bool(row.get("managed")):
            continue
        roleName = str(row.get("name") or "").strip().lower()
        if roleName:
            out[roleName] += 1
    return out


def _normalizeRoleName(value: object) -> str:
    return str(value or "").strip().lower()


def _isReusablePlaceholderRoleName(value: object) -> bool:
    return _normalizeRoleName(value) == "new role"


def _supportsEnhancedRoleColors(guild: discord.Guild) -> bool:
    try:
        features = {str(feature or "").strip().upper() for feature in list(getattr(guild, "features", []) or [])}
    except Exception:
        return False
    return "ENHANCED_ROLE_COLORS" in features


def _grantablePermissionMask(guild: discord.Guild) -> int:
    me = getattr(guild, "me", None)
    permissions = getattr(me, "guild_permissions", None)
    if permissions is None:
        return 0
    if bool(getattr(permissions, "administrator", False)):
        return int(discord.Permissions.all().value)
    return int(getattr(permissions, "value", 0) or 0)


def _clampPermissionValueToGrantable(guild: discord.Guild, permissionValue: int) -> int:
    return int(permissionValue or 0) & _grantablePermissionMask(guild)


def _buildLiveRoleBuckets(guild: discord.Guild) -> dict[str, list[discord.Role]]:
    buckets: dict[str, list[discord.Role]] = {}
    for role in guild.roles:
        if role.is_default():
            continue
        key = _normalizeRoleName(role.name)
        if not key:
            continue
        buckets.setdefault(key, []).append(role)
    for roles in buckets.values():
        roles.sort(key=lambda item: (int(item.position or 0), int(item.id or 0)))
    return buckets


def _resolveExistingRoleForPreview(
    guild: discord.Guild,
    *,
    roleId: int,
    roleName: str,
    isDefault: bool,
    usedRoleIds: set[int],
    liveRoleBuckets: dict[str, list[discord.Role]],
) -> discord.Role | None:
    if isDefault:
        return guild.default_role

    current = guild.get_role(int(roleId or 0))
    if current is not None and int(current.id or 0) not in usedRoleIds:
        usedRoleIds.add(int(current.id))
        return current

    key = _normalizeRoleName(roleName)
    if not key:
        return None
    for candidate in liveRoleBuckets.get(key, []):
        candidateId = int(candidate.id or 0)
        if candidateId in usedRoleIds:
            continue
        usedRoleIds.add(candidateId)
        return candidate
    for candidate in liveRoleBuckets.get("new role", []):
        candidateId = int(candidate.id or 0)
        if candidateId in usedRoleIds:
            continue
        if not _isReusablePlaceholderRoleName(candidate.name):
            continue
        usedRoleIds.add(candidateId)
        return candidate
    return None


def _roleNeedsEditForPreview(
    current: discord.Role,
    row: dict[str, Any],
    *,
    guild: discord.Guild,
    supportsEnhancedRoleColors: bool,
) -> bool:
    permissions = discord.Permissions(_clampPermissionValueToGrantable(guild, int(row.get("permissions") or 0)))
    if bool(row.get("isDefault")):
        return int(current.permissions.value) != int(permissions.value)

    if current.managed:
        return False

    if str(current.name or "") != str(row.get("name") or ""):
        return True
    if int(current.permissions.value) != int(permissions.value):
        return True
    if int(current.color.value) != int(row.get("color") or 0):
        return True
    if bool(current.hoist) != bool(row.get("hoist")):
        return True
    if bool(current.mentionable) != bool(row.get("mentionable")):
        return True

    if supportsEnhancedRoleColors and "secondaryColor" in row:
        currentSecondary = int(getattr(getattr(current, "secondary_color", None), "value", 0) or 0)
        desiredSecondary = int(row.get("secondaryColor") or 0)
        if currentSecondary != desiredSecondary:
            return True
    if supportsEnhancedRoleColors and "tertiaryColor" in row:
        currentTertiary = int(getattr(getattr(current, "tertiary_color", None), "value", 0) or 0)
        desiredTertiary = int(row.get("tertiaryColor") or 0)
        if currentTertiary != desiredTertiary:
            return True
    return False


def _snapshotCategoryNameById(snapshotChannels: list[dict[str, Any]]) -> dict[int, str]:
    return {
        int(row.get("id") or 0): str(row.get("name") or "").strip()
        for row in snapshotChannels
        if str(row.get("type") or "").strip().lower() == "category"
    }


def _liveChannelTypeName(channel: discord.abc.GuildChannel) -> str:
    if isinstance(channel, discord.TextChannel):
        return "text"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    return str(getattr(channel, "type", "unknown") or "unknown").strip().lower()


def _snapshotChannelSignature(
    row: dict[str, Any],
    categoryNameById: dict[int, str],
) -> tuple[str, str, str]:
    channelType = str(row.get("type") or "").strip().lower()
    channelName = str(row.get("name") or "").strip().lower()
    categoryName = ""
    if channelType != "category":
        categoryName = str(categoryNameById.get(int(row.get("categoryId") or 0)) or "").strip().lower()
    return (channelType, channelName, categoryName)


def _liveChannelSignature(channel: discord.abc.GuildChannel) -> tuple[str, str, str]:
    channelType = _liveChannelTypeName(channel)
    channelName = str(getattr(channel, "name", "") or "").strip().lower()
    categoryName = ""
    if not isinstance(channel, discord.CategoryChannel):
        categoryName = str(getattr(getattr(channel, "category", None), "name", "") or "").strip().lower()
    return (channelType, channelName, categoryName)


def _estimateCopyServerCleanup(
    configModule: Any,
    guild: discord.Guild,
    snapshot: dict[str, Any],
) -> dict[str, int]:
    snapshotChannels = filterSnapshotChannelRows(configModule, list(snapshot.get("channels") or []))
    categoryNameById = _snapshotCategoryNameById(snapshotChannels)

    expectedRoleCounts = _copyServerRoleCounter(snapshot)
    expectedChannelCounts: Counter[tuple[str, str, str]] = Counter(
        _snapshotChannelSignature(row, categoryNameById)
        for row in snapshotChannels
    )

    extraRoles = 0
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        roleName = str(role.name or "").strip().lower()
        if not roleName:
            continue
        if expectedRoleCounts[roleName] > 0:
            expectedRoleCounts[roleName] -= 1
            continue
        extraRoles += 1

    extraCategories = 0
    extraChannels = 0
    liveChannels = list(filterLiveChannels(configModule, guild.channels))
    expectedCounts = Counter(expectedChannelCounts)
    for channel in liveChannels:
        signature = _liveChannelSignature(channel)
        if expectedCounts[signature] > 0:
            expectedCounts[signature] -= 1
            continue
        if isinstance(channel, discord.CategoryChannel):
            extraCategories += 1
        else:
            extraChannels += 1

    return {
        "extraRoles": int(extraRoles),
        "extraCategories": int(extraCategories),
        "extraChannels": int(extraChannels),
    }


def _estimateCopyServerResumeProgress(
    configModule: Any,
    guild: discord.Guild,
    snapshot: dict[str, Any],
) -> dict[str, int]:
    snapshotChannels = filterSnapshotChannelRows(configModule, list(snapshot.get("channels") or []))
    categoryNameById = _snapshotCategoryNameById(snapshotChannels)

    expectedRoleCounts = _copyServerRoleCounter(snapshot)
    totalRoles = int(sum(expectedRoleCounts.values()))
    matchedRoles = 0
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        roleName = str(role.name or "").strip().lower()
        if not roleName:
            continue
        if expectedRoleCounts[roleName] > 0:
            expectedRoleCounts[roleName] -= 1
            matchedRoles += 1

    orderedRoles = sorted(list(snapshot.get("roles") or []), key=lambda item: int(item.get("position") or 0))
    liveRoleBuckets = _buildLiveRoleBuckets(guild)
    supportsEnhancedRoleColors = _supportsEnhancedRoleColors(guild)
    usedRoleIds: set[int] = set()
    contiguousCompletedRoles = 0
    resumeRoleNumber = 0
    resumeRoleName = ""
    resumeRoleState = ""
    for index, row in enumerate(orderedRoles, start=1):
        roleId = int(row.get("id") or 0)
        roleName = str(row.get("name") or "")
        isDefault = bool(row.get("isDefault"))
        managed = bool(row.get("managed"))
        current = _resolveExistingRoleForPreview(
            guild,
            roleId=roleId,
            roleName=roleName,
            isDefault=isDefault,
            usedRoleIds=usedRoleIds,
            liveRoleBuckets=liveRoleBuckets,
        )
        if current is None and not managed and not isDefault:
            resumeRoleNumber = int(index)
            resumeRoleName = roleName
            resumeRoleState = "missing"
            break
        if isinstance(current, discord.Role) and _roleNeedsEditForPreview(
            current,
            row,
            guild=guild,
            supportsEnhancedRoleColors=supportsEnhancedRoleColors,
        ):
            resumeRoleNumber = int(index)
            resumeRoleName = roleName
            resumeRoleState = "needs edits"
            break
        contiguousCompletedRoles = int(index)
    if resumeRoleNumber <= 0 and orderedRoles:
        contiguousCompletedRoles = int(len(orderedRoles))

    expectedChannelCounts: Counter[tuple[str, str, str]] = Counter(
        _snapshotChannelSignature(row, categoryNameById)
        for row in snapshotChannels
    )
    totalCategories = sum(
        1 for row in snapshotChannels if str(row.get("type") or "").strip().lower() == "category"
    )
    totalChannels = len(snapshotChannels) - totalCategories
    matchedCategories = 0
    matchedChannels = 0
    for channel in filterLiveChannels(configModule, guild.channels):
        signature = _liveChannelSignature(channel)
        if expectedChannelCounts[signature] <= 0:
            continue
        expectedChannelCounts[signature] -= 1
        if isinstance(channel, discord.CategoryChannel):
            matchedCategories += 1
        else:
            matchedChannels += 1

    return {
        "matchedRoles": int(matchedRoles),
        "missingRoles": max(0, int(totalRoles - matchedRoles)),
        "totalRoles": int(totalRoles),
        "contiguousCompletedRoles": int(contiguousCompletedRoles),
        "resumeRoleNumber": int(resumeRoleNumber),
        "resumeRoleName": str(resumeRoleName or ""),
        "resumeRoleState": str(resumeRoleState or ""),
        "matchedCategories": int(matchedCategories),
        "missingCategories": max(0, int(totalCategories - matchedCategories)),
        "totalCategories": int(totalCategories),
        "matchedChannels": int(matchedChannels),
        "missingChannels": max(0, int(totalChannels - matchedChannels)),
        "totalChannels": int(totalChannels),
    }


def _applyPinnedResumeEstimate(
    resumeEstimate: dict[str, int],
    existingState: dict[str, Any] | None,
) -> tuple[dict[str, int], int]:
    merged = dict(resumeEstimate or {})
    return merged, 0


class CopyServerConfirmView(discord.ui.View):
    def __init__(
        self,
        *,
        openerId: int,
        sourceGuildId: int,
        sourceGuildLabel: str,
        targetGuild: discord.Guild,
        botClient: Any,
        allowGuildForCommands: Callable[[int], str],
        configModule: Any,
        serverSafetyService: Any,
        snapshotPath: Path,
        snapshot: dict[str, Any],
        cleanupEstimate: dict[str, int],
        resumeEstimate: dict[str, int],
        resumeRoleFloor: int,
        statusChannelId: int,
        auditUserId: int,
        beginCopyServerRun: Callable[[int], bool],
        endCopyServerRun: Callable[[int], None],
        existingTargetBackupPath: str = "",
    ) -> None:
        super().__init__(timeout=21600)
        self.openerId = int(openerId)
        self.sourceGuildId = int(sourceGuildId)
        self.sourceGuildLabel = str(sourceGuildLabel or f"guild {sourceGuildId}")
        self.targetGuild = targetGuild
        self.botClient = botClient
        self.allowGuildForCommands = allowGuildForCommands
        self.config = configModule
        self.serverSafetyService = serverSafetyService
        self.snapshotPath = snapshotPath
        self.snapshot = snapshot
        self.cleanupEstimate = dict(cleanupEstimate or {})
        self.resumeEstimate = dict(resumeEstimate or {})
        self.resumeRoleFloor = max(0, int(resumeRoleFloor or 0))
        self.statusChannelId = int(statusChannelId or 0)
        self.auditUserId = int(auditUserId or 0)
        self.beginCopyServerRun = beginCopyServerRun
        self.endCopyServerRun = endCopyServerRun
        self.existingTargetBackupPath = str(existingTargetBackupPath or "").strip()
        self.stage = "preview"
        self.lastPausedResult: dict[str, Any] = {}
        self.lastTargetBackupPath = ""
        self.autoRetryAt: datetime | None = None
        self.retryTask: asyncio.Task | None = None
        self.autoRetryEnabled = False

    async def _denyIfWrongUser(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.openerId:
            return False
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="This confirmation is not for you.",
            ephemeral=True,
        )
        return True

    @staticmethod
    def _allowedMentions() -> discord.AllowedMentions:
        return discord.AllowedMentions(users=False, roles=False, everyone=False)

    def _disableButtons(self) -> None:
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    def _setRetryButtonState(self, *, enabled: bool, label: str = "Enable Auto Retry") -> None:
        retryButton = getattr(self, "retryAfterCooldown", None)
        if isinstance(retryButton, discord.ui.Button):
            retryButton.label = label
            retryButton.style = discord.ButtonStyle.success
            retryButton.disabled = not enabled

    def _setInitialButtons(self) -> None:
        yesButton = getattr(self, "confirmYes", None)
        noButton = getattr(self, "confirmNo", None)
        if isinstance(yesButton, discord.ui.Button):
            yesButton.disabled = False
        if isinstance(noButton, discord.ui.Button):
            noButton.disabled = False
        self._setRetryButtonState(enabled=False)

    def _setInProgressButtons(self) -> None:
        self._disableButtons()

    def _setPausedButtons(self, *, canAutoRetry: bool) -> None:
        yesButton = getattr(self, "confirmYes", None)
        noButton = getattr(self, "confirmNo", None)
        if isinstance(yesButton, discord.ui.Button):
            yesButton.disabled = True
        if isinstance(noButton, discord.ui.Button):
            noButton.disabled = True
        retryLabel = "Auto Retry Enabled" if self.autoRetryEnabled else "Enable Auto Retry"
        self._setRetryButtonState(enabled=bool(canAutoRetry) and not self.autoRetryEnabled, label=retryLabel)

    def _setAutoRetryArmedButtons(self) -> None:
        yesButton = getattr(self, "confirmYes", None)
        noButton = getattr(self, "confirmNo", None)
        if isinstance(yesButton, discord.ui.Button):
            yesButton.disabled = True
        if isinstance(noButton, discord.ui.Button):
            noButton.disabled = True
        self._setRetryButtonState(enabled=False, label="Auto Retry Enabled")

    def _setLabels(self) -> None:
        yesButton = getattr(self, "confirmYes", None)
        noButton = getattr(self, "confirmNo", None)
        if isinstance(yesButton, discord.ui.Button):
            yesButton.label = "Continue" if self.stage == "preview" else "Yes, Copy Server"
            yesButton.style = discord.ButtonStyle.primary if self.stage == "preview" else discord.ButtonStyle.danger
        if isinstance(noButton, discord.ui.Button):
            noButton.label = "Cancel"
            noButton.style = discord.ButtonStyle.secondary
        self._setRetryButtonState(enabled=False)

    def _defaultAutoRetryDelaySec(self) -> float:
        return max(30.0, float(getattr(self.config, "copyServerAutoRetryFallbackSec", 120) or 120))

    def _batchLimitAutoRetryDelaySec(self) -> float:
        return max(15.0, float(getattr(self.config, "copyServerBatchAutoRetryDelaySec", 30) or 30))

    def _effectiveRetryAfterSec(self, result: dict[str, Any]) -> float:
        retryAfterSec = max(0.0, float(result.get("retryAfterSec") or 0.0))
        if retryAfterSec > 0:
            return retryAfterSec
        pauseReason = str(result.get("pauseReason") or "").strip().lower()
        if pauseReason == "batch-limit":
            return self._batchLimitAutoRetryDelaySec()
        if pauseReason in {"timeout-create", "timeout-edit", "local-ratelimit-create", "ratelimited-create", "ratelimited-edit"}:
            return self._defaultAutoRetryDelaySec()
        return 0.0

    @staticmethod
    def _discordTimestamp(value: datetime, style: str) -> str:
        dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return f"<t:{int(dt.timestamp())}:{style}>"

    def _retryTimeText(self, retryAfterSec: float) -> tuple[str, datetime | None]:
        retryAfterSec = max(0.0, float(retryAfterSec or 0.0))
        if retryAfterSec <= 0:
            return "", None
        retryAt = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(math.ceil(retryAfterSec))))
        return (
            f"{self._discordTimestamp(retryAt, 'F')} ({self._discordTimestamp(retryAt, 'R')})",
            retryAt,
        )

    def _snapshotCounts(self) -> dict[str, int]:
        filteredChannels = filterSnapshotChannelRows(self.config, list(self.snapshot.get("channels") or []))
        categories = 0
        otherChannels = 0
        for row in filteredChannels:
            if str(row.get("type") or "").strip().lower() == "category":
                categories += 1
            else:
                otherChannels += 1
        roles = sum(
            1
            for row in list(self.snapshot.get("roles") or [])
            if not bool(row.get("isDefault")) and not bool(row.get("managed"))
        )
        return {
            "roles": roles,
            "categories": categories,
            "channels": otherChannels,
            "members": len(list(self.snapshot.get("members") or [])),
        }

    def _previewContent(self) -> str:
        counts = self._snapshotCounts()
        createdAtText = str(self.snapshot.get("createdAt") or "").strip() or "unknown"
        return (
            "Final confirmation for `!copyserver`.\n"
            f"Source: `{self.sourceGuildLabel}`\n"
            f"Snapshot: `{self.snapshotPath.name}`\n"
            f"Snapshot created: `{createdAtText}`\n"
            f"Target: **{self.targetGuild.name}**\n\n"
            "Detected progress on target:\n"
            f"- roles already present: `{int(self.resumeEstimate.get('matchedRoles') or 0)}/{int(self.resumeEstimate.get('totalRoles') or 0)}`\n"
            f"- contiguous roles completed from start: `{int(self.resumeEstimate.get('contiguousCompletedRoles') or 0)}/{int(self.resumeEstimate.get('totalRoles') or 0)}`\n"
            f"- next role to resume: `{(str(self.resumeEstimate.get('resumeRoleName') or 'none') + ' #' + str(int(self.resumeEstimate.get('resumeRoleNumber') or 0))) if int(self.resumeEstimate.get('resumeRoleNumber') or 0) > 0 else 'none'}`\n"
            f"- next role state: `{str(self.resumeEstimate.get('resumeRoleState') or 'complete')}`\n"
            f"- categories already present: `{int(self.resumeEstimate.get('matchedCategories') or 0)}/{int(self.resumeEstimate.get('totalCategories') or 0)}`\n"
            f"- channels already present: `{int(self.resumeEstimate.get('matchedChannels') or 0)}/{int(self.resumeEstimate.get('totalChannels') or 0)}`\n"
            f"- roles still missing: `{int(self.resumeEstimate.get('missingRoles') or 0)}`\n"
            f"- categories still missing: `{int(self.resumeEstimate.get('missingCategories') or 0)}`\n"
            f"- channels still missing: `{int(self.resumeEstimate.get('missingChannels') or 0)}`\n\n"
            "Preview:\n"
            f"- roles in snapshot: `{counts['roles']}`\n"
            f"- categories in snapshot: `{counts['categories']}`\n"
            f"- channels in snapshot: `{counts['channels']}`\n"
            f"- tracked members in snapshot: `{counts['members']}`\n"
            f"- extra roles to delete: `{int(self.cleanupEstimate.get('extraRoles') or 0)}`\n"
            f"- extra categories to delete: `{int(self.cleanupEstimate.get('extraCategories') or 0)}`\n"
            f"- extra channels to delete: `{int(self.cleanupEstimate.get('extraChannels') or 0)}`\n\n"
            "Notes:\n"
            f"- target backup: `{'reusing existing backup' if self.existingTargetBackupPath else 'will create a new pre-copy backup'}`\n"
            f"- Jane will only do up to `{int(getattr(self.config, 'copyServerRoleBatchCreateLimit', 12) or 12)}` role creates per run.\n"
            "- Jane will clean up extra roles/channels/categories that are not in the snapshot.\n"
            "- Jane will not restore member role assignments for this copy.\n"
            "- The channel you run this in is kept alive so progress updates do not disappear.\n\n"
            "Press `Continue` to get the final confirmation."
        )

    def _finalContent(self) -> str:
        return (
            "Second confirmation for `!copyserver`.\n"
            f"You are about to copy `{self.snapshotPath.name}` from `{self.sourceGuildLabel}` "
            f"into **{self.targetGuild.name}**.\n"
            "This will rewrite server structure and delete extras on the mimic server.\n\n"
            "Press `Yes, Copy Server` if you really want Jane to do it."
        )

    def _progressContent(self, detail: str) -> str:
        return (
            f"`!copyserver` in progress for **{self.targetGuild.name}**.\n"
            f"Snapshot: `{self.snapshotPath.name}`\n"
            f"Status: {detail}"
        )

    def _pausedContent(
        self,
        *,
        result: dict[str, Any],
        targetBackupPath: Path | str,
        autoRetryAt: datetime | None = None,
    ) -> str:
        resumeRoleNumber = int(result.get("resumeRoleNumber") or 0)
        resumeRoleName = str(result.get("resumeRoleName") or "").strip() or "unnamed role"
        pauseReason = str(result.get("pauseReason") or "").strip().lower()
        retryAfterSec = self._effectiveRetryAfterSec(result)
        retryTimeText, retryAt = self._retryTimeText(retryAfterSec)
        pauseLine = "Run `!copyserver` again."
        if pauseReason == "timeout-create":
            pauseLine = "Role create timed out. Run `!copyserver` again later."
        elif pauseReason == "timeout-edit":
            pauseLine = "Role edit timed out. Run `!copyserver` again later."
        elif pauseReason == "create-failed":
            pauseLine = "Role create failed. Check Jane's grantable permissions and hierarchy."
        elif pauseReason == "edit-failed":
            pauseLine = "Role edit failed. Check Jane's grantable permissions and hierarchy."
        elif pauseReason in {"local-ratelimit-create", "ratelimited-create"} and retryTimeText:
            pauseLine = f"Role create cooldown until {retryTimeText}."
        elif pauseReason == "ratelimited-edit" and retryTimeText:
            pauseLine = f"Role edit cooldown until {retryTimeText}."
        elif pauseReason == "batch-limit":
            pauseLine = "Batch limit reached."

        autoRetryLine = ""
        if autoRetryAt is not None:
            autoRetryLine = (
                f"\nAuto retry: {self._discordTimestamp(autoRetryAt, 'F')} "
                f"({self._discordTimestamp(autoRetryAt, 'R')})"
            )
        elif retryAt is not None:
            autoRetryLine = "\nPress `Enable Auto Retry` to keep going automatically."

        createDiagnostics = ""
        createAttempts = int(result.get("roleCreateAttempts") or 0)
        createRateLimits = int(result.get("roleCreateRateLimits") or 0)
        createWaitSec = int(round(float(result.get("roleCreateWaitSec") or 0.0)))
        if createAttempts > 0 or createRateLimits > 0 or createWaitSec > 0:
            createDiagnostics = (
                f"\ncreateCalls=`{createAttempts}` | "
                f"create429s=`{createRateLimits}` | "
                f"createWait=`{createWaitSec}s`"
            )

        editDiagnostics = ""
        editAttempts = int(result.get("roleEditAttempts") or 0)
        editRateLimits = int(result.get("roleEditRateLimits") or 0)
        editWaitSec = int(round(float(result.get("roleEditWaitSec") or 0.0)))
        if editAttempts > 0 or editRateLimits > 0 or editWaitSec > 0:
            editDiagnostics = (
                f"\neditCalls=`{editAttempts}` | "
                f"edit429s=`{editRateLimits}` | "
                f"editWait=`{editWaitSec}s`"
            )

        return (
            f"Copyserver paused on `{self.snapshotPath.name}`.\n"
            f"next=`{resumeRoleName} #{resumeRoleNumber}` | "
            f"done=`{int(result.get('contiguousCompletedRoles') or 0)}/{int(result.get('totalRoles') or 0)}` | "
            f"created=`{int(result.get('roleCreatesApplied') or 0)}` | "
            f"edited=`{int(result.get('roleEditsApplied') or 0)}`\n"
            f"backup=`{getattr(targetBackupPath, 'name', str(targetBackupPath))}`\n"
            f"{pauseLine}{createDiagnostics}{editDiagnostics}{autoRetryLine}"
        )

    async def _editStatusMessage(
        self,
        statusMessage: discord.Message,
        *,
        content: str,
    ) -> None:
        if int(getattr(statusMessage, "webhook_id", 0) or 0) > 0:
            edited = await runtimeWebhooks.editOwnedWebhookMessage(
                botClient=self.botClient,
                message=statusMessage,
                webhookName="Jane Copyserver",
                content=content,
                view=self,
                reason="Hidden copyserver status update",
            )
            if edited:
                return
        await statusMessage.edit(
            content=content,
            view=self,
            allowed_mentions=self._allowedMentions(),
        )

    async def _fetchStatusMessage(
        self,
        *,
        channelId: int,
        messageId: int,
    ) -> discord.Message | None:
        channel = self.botClient.get_channel(int(channelId or 0))
        if channel is None:
            try:
                channel = await self.botClient.fetch_channel(int(channelId or 0))
            except Exception:
                return None
        if channel is None or not hasattr(channel, "fetch_message"):
            return None
        try:
            return await channel.fetch_message(int(messageId or 0))
        except Exception:
            return None

    async def _sendAuditDm(self, content: str) -> None:
        if self.auditUserId <= 0:
            return
        try:
            targetUser = self.botClient.get_user(self.auditUserId)
            if targetUser is None:
                targetUser = await self.botClient.fetch_user(self.auditUserId)
        except Exception:
            return
        if targetUser is None:
            return
        try:
            await targetUser.send(content[:1900])
        except Exception:
            return

    async def _editProgress(self, interaction: discord.Interaction, detail: str) -> None:
        await interaction.edit_original_response(
            content=self._progressContent(detail),
            view=self,
            allowed_mentions=self._allowedMentions(),
        )

    async def _ensureCopyServerDevRole(self) -> str:
        roleName = "Jane Clanker Dev"
        desiredPermissions = discord.Permissions(_grantablePermissionMask(self.targetGuild))
        me = self.targetGuild.me
        if me is None:
            return "skipped-no-member"
        if not bool(getattr(getattr(me, "guild_permissions", None), "manage_roles", False)):
            return "skipped-no-manage-roles"
        current = discord.utils.get(self.targetGuild.roles, name=roleName)
        if current is None:
            try:
                await asyncio.wait_for(
                    self.targetGuild.create_role(
                        name=roleName,
                        permissions=desiredPermissions,
                        hoist=True,
                        mentionable=False,
                        reason="Copyserver developer access role.",
                    ),
                    timeout=20.0,
                )
            except asyncio.TimeoutError:
                return "failed-timeout"
            except (discord.Forbidden, discord.HTTPException):
                return "failed"
            except Exception:
                log.exception("Copyserver dev role creation failed unexpectedly for guildId=%d", int(self.targetGuild.id))
                return "failed"
            return "created"

        if current.managed:
            return "managed-existing"

        needsEdit = (
            int(current.permissions.value) != int(desiredPermissions.value)
            or str(current.name or "") != roleName
            or not bool(current.hoist)
            or bool(current.mentionable)
        )
        if not needsEdit:
            return "already"

        try:
            await asyncio.wait_for(
                current.edit(
                    name=roleName,
                    permissions=desiredPermissions,
                    hoist=True,
                    mentionable=False,
                    reason="Copyserver developer access role.",
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            return "failed-timeout"
        except (discord.Forbidden, discord.HTTPException):
            return "failed"
        except Exception:
            log.exception("Copyserver dev role edit failed unexpectedly for guildId=%d", int(self.targetGuild.id))
            return "failed"
        return "updated"

    def _cancelRetryTask(self) -> None:
        if self.retryTask is not None and not self.retryTask.done():
            self.retryTask.cancel()
        self.retryTask = None
        self.autoRetryAt = None
        self.autoRetryEnabled = False

    async def _armAutoRetry(
        self,
        *,
        statusMessage: discord.Message,
        result: dict[str, Any],
        targetBackupPath: Path | str,
    ) -> bool:
        retryAfterSec = self._effectiveRetryAfterSec(result)
        if retryAfterSec <= 0:
            return False
        retryAt = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(math.ceil(retryAfterSec))))
        currentTask = asyncio.current_task()
        existingRetryTask = self.retryTask

        self.autoRetryEnabled = True
        self.autoRetryAt = retryAt
        self._setAutoRetryArmedButtons()
        await self._editStatusMessage(
            statusMessage,
            content=self._pausedContent(
                result=result,
                targetBackupPath=targetBackupPath,
                autoRetryAt=retryAt,
            ),
        )
        if existingRetryTask is not None and not existingRetryTask.done() and existingRetryTask is not currentTask:
            existingRetryTask.cancel()
        self.retryTask = asyncio.create_task(
            self._runAutoRetryAfterDelay(
                channelId=int(getattr(getattr(statusMessage, "channel", None), "id", 0) or 0),
                messageId=int(getattr(statusMessage, "id", 0) or 0),
                retryAt=retryAt,
            ),
            name=f"copyserver-auto-retry-{int(self.targetGuild.id)}",
        )
        return True

    async def _runAutoRetryAfterDelay(
        self,
        *,
        channelId: int,
        messageId: int,
        retryAt: datetime,
    ) -> None:
        currentTask = asyncio.current_task()
        try:
            waitSec = max(0.0, (retryAt - datetime.now(timezone.utc)).total_seconds())
            if waitSec > 0:
                await asyncio.sleep(waitSec)
            statusMessage = await self._fetchStatusMessage(channelId=channelId, messageId=messageId)
            if statusMessage is None:
                return
            self.autoRetryAt = None
            self._setInProgressButtons()
            await self._editStatusMessage(
                statusMessage,
                content=self._progressContent("Automatic retry starting..."),
            )
            await self._runCopyServer(statusMessage)
        except asyncio.CancelledError:
            raise
        finally:
            if self.retryTask is currentTask:
                self.retryTask = None
                self.autoRetryAt = None

    async def _runCopyServer(self, statusMessage: discord.Message) -> None:
        runStarted = self.beginCopyServerRun(int(self.targetGuild.id))
        if not runStarted:
            await self._editStatusMessage(
                statusMessage,
                content=(
                    f"`!copyserver` is already running for **{self.targetGuild.name}**.\n"
                    "Wait for the existing run to finish, or restart Jane if that run is genuinely dead."
                ),
            )
            return

        try:
            try:
                await self._sendAuditDm(
                    (
                        f"Copyserver started by `{self.openerId}`.\n"
                        f"Source: `{self.sourceGuildLabel}` (`{self.sourceGuildId}`)\n"
                        f"Target: `{self.targetGuild.name}` (`{self.targetGuild.id}`)\n"
                        f"Snapshot: `{self.snapshotPath.name}`"
                    )
                )

                existingTargetBackupPath = Path(self.existingTargetBackupPath) if self.existingTargetBackupPath else None
                if existingTargetBackupPath is not None and existingTargetBackupPath.exists():
                    targetBackupPath = existingTargetBackupPath
                    await self._editStatusMessage(
                        statusMessage,
                        content=self._progressContent("Reusing the existing pre-copy backup for this target server..."),
                    )
                else:
                    await self._editStatusMessage(
                        statusMessage,
                        content=self._progressContent("Creating a backup of the target server..."),
                    )
                    targetBackupPath = await self.serverSafetyService.createGuildSnapshot(
                        self.config,
                        self.targetGuild,
                        label=f"copyserver_pre_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
                        snapshotKind="manual",
                    )
                    self.existingTargetBackupPath = str(targetBackupPath)

                runtimeCopyServerState.saveGuildState(
                    int(self.targetGuild.id),
                    sourceGuildId=int(self.sourceGuildId),
                    sourceGuildLabel=self.sourceGuildLabel,
                    snapshotPath=str(self.snapshotPath),
                    targetBackupPath=str(getattr(targetBackupPath, "resolve", lambda: targetBackupPath)()),
                    resumeRoleNumber=int(self.resumeRoleFloor or 0),
                    resumeRoleName=str(self.resumeEstimate.get("resumeRoleName") or ""),
                    contiguousCompletedRoles=int(self.resumeEstimate.get("contiguousCompletedRoles") or 0),
                    totalRoles=int(self.resumeEstimate.get("totalRoles") or 0),
                )

                async def _progress(detail: str) -> None:
                    await self._editStatusMessage(
                        statusMessage,
                        content=self._progressContent(detail),
                    )

                result = await self.serverSafetyService.applySnapshotPathToGuild(
                    self.config,
                    self.targetGuild,
                    snapshotPath=self.snapshotPath,
                    cleanupExtras=True,
                    restoreMembers=False,
                    progressCallback=_progress,
                    protectedChannelId=self.statusChannelId,
                    maxRoleCreates=int(getattr(self.config, "copyServerRoleBatchCreateLimit", 12) or 12),
                    maxRoleMutations=int(getattr(self.config, "copyServerRoleBatchMutationLimit", 18) or 18),
                    resumeRoleFloor=int(self.resumeRoleFloor or 0),
                )
            except Exception as exc:
                self.lastPausedResult = {}
                self.lastTargetBackupPath = ""
                self.autoRetryEnabled = False
                self._setInProgressButtons()
                await self._editStatusMessage(
                    statusMessage,
                    content=(
                        f"Copyserver failed while applying `{self.snapshotPath.name}`.\n"
                        f"`{exc.__class__.__name__}: {exc}`"
                    )[:1900],
                )
                await self._sendAuditDm(
                    (
                        f"Copyserver failed.\n"
                        f"Target: `{self.targetGuild.name}` (`{self.targetGuild.id}`)\n"
                        f"Snapshot: `{self.snapshotPath.name}`\n"
                        f"Error: `{exc.__class__.__name__}: {exc}`"
                    )
                )
                return

            if bool(result.get("batchPaused")):
                self.lastPausedResult = dict(result)
                self.lastTargetBackupPath = str(targetBackupPath)
                self.autoRetryAt = None
                self.resumeRoleFloor = 0
                self.resumeEstimate["contiguousCompletedRoles"] = int(result.get("contiguousCompletedRoles") or 0)
                self.resumeEstimate["resumeRoleNumber"] = int(result.get("resumeRoleNumber") or 0)
                self.resumeEstimate["resumeRoleName"] = str(result.get("resumeRoleName") or self.resumeEstimate.get("resumeRoleName") or "")
                self.resumeEstimate["resumeRoleState"] = str(result.get("pauseReason") or self.resumeEstimate.get("resumeRoleState") or "")
                self.resumeEstimate["totalRoles"] = int(result.get("totalRoles") or self.resumeEstimate.get("totalRoles") or 0)
                runtimeCopyServerState.saveGuildState(
                    int(self.targetGuild.id),
                    sourceGuildId=int(self.sourceGuildId),
                    sourceGuildLabel=self.sourceGuildLabel,
                    snapshotPath=str(self.snapshotPath),
                    targetBackupPath=str(getattr(targetBackupPath, "resolve", lambda: targetBackupPath)()),
                    resumeRoleNumber=int(result.get("resumeRoleNumber") or 0),
                    resumeRoleName=str(self.resumeEstimate.get("resumeRoleName") or ""),
                    contiguousCompletedRoles=int(self.resumeEstimate.get("contiguousCompletedRoles") or 0),
                    totalRoles=int(self.resumeEstimate.get("totalRoles") or 0),
                )
                retryAfterSec = self._effectiveRetryAfterSec(result)
                self._setPausedButtons(canAutoRetry=retryAfterSec > 0)
                if self.autoRetryEnabled and retryAfterSec > 0:
                    await self._armAutoRetry(
                        statusMessage=statusMessage,
                        result=result,
                        targetBackupPath=targetBackupPath,
                    )
                else:
                    await self._editStatusMessage(
                        statusMessage,
                        content=self._pausedContent(
                            result=result,
                            targetBackupPath=targetBackupPath,
                        ),
                    )
                await self._sendAuditDm(
                    (
                        f"Copyserver paused after a conservative role batch.\n"
                        f"Target: `{self.targetGuild.name}` (`{self.targetGuild.id}`)\n"
                        f"Snapshot: `{self.snapshotPath.name}`\n"
                        f"Target backup: `{getattr(targetBackupPath, 'name', str(targetBackupPath))}`\n"
                        f"rolesCreatedThisRun={int(result.get('roleCreatesApplied') or 0)} "
                        f"rolesEditedThisRun={int(result.get('roleEditsApplied') or 0)} "
                        f"roleCreateCalls={int(result.get('roleCreateAttempts') or 0)} "
                        f"roleCreate429s={int(result.get('roleCreateRateLimits') or 0)} "
                        f"roleCreateWaitSec={int(round(float(result.get('roleCreateWaitSec') or 0.0)))} "
                        f"roleEditCalls={int(result.get('roleEditAttempts') or 0)} "
                        f"roleEdit429s={int(result.get('roleEditRateLimits') or 0)} "
                        f"roleEditWaitSec={int(round(float(result.get('roleEditWaitSec') or 0.0)))} "
                        f"contiguousRolesDone={int(result.get('contiguousCompletedRoles') or 0)}/{int(result.get('totalRoles') or 0)} "
                        f"nextRole={str(result.get('resumeRoleName') or 'unnamed role').strip() or 'unnamed role'} "
                        f"#{int(result.get('resumeRoleNumber') or 0)} "
                        f"pauseReason={str(result.get('pauseReason') or '').strip().lower() or 'batch-limit'} "
                        f"retryAfter={max(1, int(math.ceil(retryAfterSec)))}s"
                    )
                )
                return

            self.lastPausedResult = {}
            self.lastTargetBackupPath = ""
            self.autoRetryAt = None
            self.autoRetryEnabled = False
            self._setInProgressButtons()
            await self._editStatusMessage(
                statusMessage,
                content=self._progressContent("Ensuring the `Jane Clanker Dev` role exists..."),
            )
            devRoleResult = await self._ensureCopyServerDevRole()
            if bool(result.get("hadFailures")):
                failedRoleNames = list(result.get("failedRoleNames") or [])
                failedPositionRoleNames = list(result.get("failedPositionRoleNames") or [])
                failedCategoryNames = list(result.get("failedCategoryNames") or [])
                failedChannelNames = list(result.get("failedChannelNames") or [])
                failedSummaryParts: list[str] = []
                if int(result.get("roleCreateFailures") or 0) > 0 or int(result.get("roleEditFailures") or 0) > 0:
                    if failedRoleNames:
                        failedSummaryParts.append(f"roles: {', '.join(str(name) for name in failedRoleNames[:3])}")
                if int(result.get("positionSyncFailures") or 0) > 0 and failedPositionRoleNames:
                    failedSummaryParts.append(f"role positions: {', '.join(str(name) for name in failedPositionRoleNames[:3])}")
                if int(result.get("categoryFailures") or 0) > 0 and failedCategoryNames:
                    failedSummaryParts.append(f"categories: {', '.join(str(name) for name in failedCategoryNames[:3])}")
                if int(result.get("channelFailures") or 0) > 0 and failedChannelNames:
                    failedSummaryParts.append(f"channels: {', '.join(str(name) for name in failedChannelNames[:3])}")
                failedSummary = "; ".join(failedSummaryParts) or "Discord rejected part of the restore."
                self._disableButtons()
                await self._editStatusMessage(
                    statusMessage,
                    content=(
                        f"Copyserver finished with restore failures from `{self.snapshotPath.name}`.\n"
                        f"target backup: `{getattr(targetBackupPath, 'name', str(targetBackupPath))}`\n"
                        f"devRole: `{devRoleResult}`\n"
                        f"roleCreateFailures=`{int(result.get('roleCreateFailures') or 0)}` | "
                        f"roleEditFailures=`{int(result.get('roleEditFailures') or 0)}` | "
                        f"rolePositionFailures=`{int(result.get('positionSyncFailures') or 0)}` | "
                        f"categoryFailures=`{int(result.get('categoryFailures') or 0)}` | "
                        f"channelFailures=`{int(result.get('channelFailures') or 0)}`\n"
                        f"examples: `{failedSummary[:1200]}`\n"
                        "Jane did not mark this guild as copyserver-ready. Run `!copyserver` again after fixing the Discord-side issue."
                    ),
                )
                await self._sendAuditDm(
                    (
                        f"Copyserver finished with restore failures.\n"
                        f"Target: `{self.targetGuild.name}` (`{self.targetGuild.id}`)\n"
                        f"Snapshot: `{self.snapshotPath.name}`\n"
                        f"Target backup: `{getattr(targetBackupPath, 'name', str(targetBackupPath))}`\n"
                        f"devRole: `{devRoleResult}`\n"
                        f"roleCreateFailures={int(result.get('roleCreateFailures') or 0)} "
                        f"roleEditFailures={int(result.get('roleEditFailures') or 0)} "
                        f"rolePositionFailures={int(result.get('positionSyncFailures') or 0)} "
                        f"categoryFailures={int(result.get('categoryFailures') or 0)} "
                        f"channelFailures={int(result.get('channelFailures') or 0)}\n"
                        f"examples: `{failedSummary[:1200]}`"
                    )
                )
                return
            await self._editStatusMessage(
                statusMessage,
                content=self._progressContent("Adding this server to Jane's allowed guild list..."),
            )
            allowResult = str(self.allowGuildForCommands(int(self.targetGuild.id)) or "").strip().lower() or "unknown"
            runtimeCopyServerState.clearGuildState(int(self.targetGuild.id))
            self._disableButtons()
            await self._editStatusMessage(
                statusMessage,
                content=(
                    f"Copyserver complete from `{self.snapshotPath.name}`.\n"
                    f"allowedGuild: `{allowResult}`\n"
                    f"devRole: `{devRoleResult}`\n"
                    f"target backup: `{getattr(targetBackupPath, 'name', str(targetBackupPath))}`\n"
                    f"roles={int(result.get('rolesMapped') or 0)} | "
                    f"categories={int(result.get('categoriesMapped') or 0)} | "
                    f"channels={int(result.get('channelsMapped') or 0)} | "
                    f"deletedRoles={int(result.get('rolesDeleted') or 0)} | "
                    f"deletedCategories={int(result.get('categoriesDeleted') or 0)} | "
                    f"deletedChannels={int(result.get('channelsDeleted') or 0)} | "
                    f"membersRestored={int(result.get('membersUpdated') or 0)}"
                ),
            )
            await self._sendAuditDm(
                (
                    f"Copyserver complete.\n"
                    f"Target: `{self.targetGuild.name}` (`{self.targetGuild.id}`)\n"
                    f"Snapshot: `{self.snapshotPath.name}`\n"
                    f"allowedGuild: `{allowResult}`\n"
                    f"devRole: `{devRoleResult}`\n"
                    f"Target backup: `{getattr(targetBackupPath, 'name', str(targetBackupPath))}`\n"
                    f"roles={int(result.get('rolesMapped') or 0)} "
                    f"categories={int(result.get('categoriesMapped') or 0)} "
                    f"channels={int(result.get('channelsMapped') or 0)} "
                    f"deletedRoles={int(result.get('rolesDeleted') or 0)} "
                    f"deletedCategories={int(result.get('categoriesDeleted') or 0)} "
                    f"deletedChannels={int(result.get('channelsDeleted') or 0)}"
                )
            )
        finally:
            self.endCopyServerRun(int(self.targetGuild.id))

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.danger)
    async def confirmYes(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if await self._denyIfWrongUser(interaction):
            return

        if self.stage == "preview":
            self.stage = "final"
            self._setLabels()
            self._setInitialButtons()
            await interaction.response.edit_message(
                content=self._finalContent(),
                view=self,
                allowed_mentions=self._allowedMentions(),
            )
            return

        if self.retryTask is not None and not self.retryTask.done():
            self.retryTask.cancel()
        self.retryTask = None
        self.autoRetryAt = None
        self._setInProgressButtons()
        await interaction.response.edit_message(
            content=self._progressContent("Starting copy..."),
            view=self,
            allowed_mentions=self._allowedMentions(),
        )
        await self._runCopyServer(interaction.message)

    @discord.ui.button(label="Enable Auto Retry", style=discord.ButtonStyle.success, disabled=True)
    async def retryAfterCooldown(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if await self._denyIfWrongUser(interaction):
            return

        retryAfterSec = self._effectiveRetryAfterSec(self.lastPausedResult)
        if retryAfterSec <= 0:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="There is no active copyserver cooldown to retry from.",
                ephemeral=True,
            )
            return
        if self.retryTask is not None and not self.retryTask.done():
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Auto retry is already armed for this copyserver message.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        await self._armAutoRetry(
            statusMessage=interaction.message,
            result=self.lastPausedResult,
            targetBackupPath=self.lastTargetBackupPath or self.existingTargetBackupPath,
        )

    @discord.ui.button(label="No", style=discord.ButtonStyle.secondary)
    async def confirmNo(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        del button
        if await self._denyIfWrongUser(interaction):
            return

        self._cancelRetryTask()
        self._disableButtons()
        await interaction.response.edit_message(
            content="Copyserver cancelled.",
            view=self,
            allowed_mentions=self._allowedMentions(),
        )

    async def on_timeout(self) -> None:
        self._disableButtons()

    def buildInitialContent(self) -> str:
        self._setLabels()
        self._setInitialButtons()
        return self._previewContent()


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
        pendingRoleId = orgProfiles.getOrganizationValue(
            self.config,
            "pendingBgRoleId",
            guildId=int(getattr(guild, "id", 0) or 0),
            default=None,
        )
        try:
            pendingRoleIdInt = int(pendingRoleId) if pendingRoleId else 0
        except (TypeError, ValueError):
            pendingRoleIdInt = 0

        sourceGuildId = (
            orgProfiles.getOrganizationValue(
                self.config,
                "bgCheckSourceGuildId",
                guildId=int(getattr(guild, "id", 0) or 0),
                default=None,
            )
            or orgProfiles.getOrganizationValue(
                self.config,
                "primaryGuildId",
                guildId=int(getattr(guild, "id", 0) or 0),
                default=getattr(self.config, "serverId", None),
            )
            or guild.id
        )
        try:
            sourceGuildIdInt = int(sourceGuildId)
        except (TypeError, ValueError):
            sourceGuildIdInt = int(guild.id)

        progress = runtimeBgQueueCommand.BgQueueProgressReporter(
            channel=channel,
            sourceGuildId=sourceGuildIdInt,
            totalSteps=5,
        )
        await progress.start("Resolving the source server and pending BG role...")

        if pendingRoleIdInt <= 0:
            await progress.update(
                stepIndex=1,
                detail="Pending Background Check role is not configured.",
                failed=True,
            )
            return False, "Pending Background Check role is not configured."

        sourceGuild = self.botClient.get_guild(sourceGuildIdInt)
        if sourceGuild is None:
            await progress.update(
                stepIndex=1,
                detail="Source guild is not available to Jane right now.",
                failed=True,
            )
            return False, "Source guild is not available to Jane right now."

        pendingRole = sourceGuild.get_role(pendingRoleIdInt)
        if pendingRole is None:
            await progress.update(
                stepIndex=1,
                detail="Pending Background Check role could not be found in the source server.",
                failed=True,
            )
            return False, "Pending Background Check role could not be found in the source server."

        try:
            pendingMembers = await runtimeBgQueueCommand.collectPendingMembers(
                sourceGuild,
                pendingRole,
                pendingRoleIdInt,
                progress,
            )
            if not pendingMembers:
                await progress.update(
                    stepIndex=2,
                    detail="No members currently have the Pending Background Check role.",
                    pendingCount=0,
                    failed=True,
                )
                return False, "No members currently have the Pending Background Check role."

            await progress.update(
                stepIndex=3,
                detail="Creating the BG queue session and attendee list...",
                pendingCount=len(pendingMembers),
            )

            me = getattr(guild, "me", None)
            sourceChannel = getattr(sourceMessage, "channel", None)
            if (
                sourceMessage is not None
                and me is not None
                and hasattr(sourceChannel, "permissions_for")
                and bool(sourceChannel.permissions_for(me).manage_messages)
            ):
                try:
                    await sourceMessage.delete()
                except Exception:
                    pass

            sessionId = await self.sessionService.createSession(
                guildId=int(sourceGuild.id),
                channelId=int(getattr(channel, "id", 0) or 0),
                messageId=int(getattr(sourceMessage, "id", 0) or 0),
                sessionType="bg-check",
                hostId=int(actor.id),
                password=os.urandom(8).hex(),
            )
            attendeeUserIds = [int(member.id) for member in pendingMembers]
            await self.sessionService.addAttendeesBulk(
                int(sessionId),
                attendeeUserIds,
                examGrade="PASS",
            )
            bucketCounts = await self.sessionViews.ensureBgReviewBuckets(
                self.botClient,
                int(sessionId),
                sourceGuild,
            )
            adultCount = int(bucketCounts.get(bgBuckets.adultBgReviewBucket, 0) or 0)
            minorCount = int(bucketCounts.get(bgBuckets.minorBgReviewBucket, 0) or 0)

            await progress.update(
                stepIndex=4,
                detail=(
                    "Posting the split BG queues...\n"
                    f"+18: `{adultCount}` attendee(s)\n"
                    f"-18: `{minorCount}` attendee(s)"
                ),
                pendingCount=len(pendingMembers),
            )
            await self.sessionViews.postBgQueue(self.botClient, sessionId, sourceGuild)
            updatedSession = await self.sessionService.getSession(int(sessionId))
            adultQueueMessageId = int((updatedSession or {}).get("bgQueueMessageId") or 0)
            minorQueueMessageId = int((updatedSession or {}).get("bgQueueMinorMessageId") or 0)
            if adultQueueMessageId <= 0 and minorQueueMessageId <= 0:
                await progress.update(
                    stepIndex=5,
                    detail="BG queue channels are not configured or inaccessible.",
                    pendingCount=len(pendingMembers),
                    failed=True,
                )
                return False, "BG queue channels are not configured or inaccessible."
            await progress.update(
                stepIndex=5,
                detail=(
                    f"Background-check queues created for `{len(pendingMembers)}` member(s).\n"
                    f"+18 routed: `{adultCount}`\n"
                    f"-18 routed: `{minorCount}`\n"
                    "Initial Roblox scans will continue in the background."
                ),
                pendingCount=len(pendingMembers),
                finished=True,
            )
            return True, (
                f"Background-check queues created for `{len(pendingMembers)}` member(s).\n"
                f"+18 routed: `{adultCount}`\n"
                f"-18 routed: `{minorCount}`"
            )
        except Exception as exc:
            await progress.update(
                stepIndex=5,
                detail=f"Queue creation failed: `{exc.__class__.__name__}`",
                pendingCount=None,
                failed=True,
            )
            raise

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
        stripped = content.strip()
        if not stripped:
            return ""
        return stripped.split(maxsplit=1)[0].lower()

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
        return int(userId or 0) in _copyServerAllowedUserIds

    def _shutdownAllowed(self, userId: int) -> bool:
        try:
            configuredIds = [int(value or 0) for value in list(getattr(self.config, "opsAllowedUserIds", []) or [])]
        except Exception:
            configuredIds = []
        allowedIds = {userId for userId in configuredIds if int(userId or 0) > 0}
        if not allowedIds:
            allowedIds = set(_copyServerAllowedUserIds)
        return int(userId or 0) in allowedIds

    def _copyServerApprovedGuildWarningKey(self, guildId: int, userId: int) -> tuple[int, int]:
        return (int(guildId or 0), int(userId or 0))

    def _pruneExpiredApprovedGuildCopyServerWarnings(self) -> None:
        now = datetime.now(timezone.utc)
        staleKeys = [
            key
            for key, expiresAt in self._pendingApprovedGuildCopyServerWarnings.items()
            if not isinstance(expiresAt, datetime) or expiresAt <= now
        ]
        for key in staleKeys:
            self._pendingApprovedGuildCopyServerWarnings.pop(key, None)

    def noteCopyServerWarningMessage(self, message: discord.Message) -> None:
        self._pruneExpiredApprovedGuildCopyServerWarnings()
        if message.author.bot or not message.guild:
            return
        guildId = int(getattr(message.guild, "id", 0) or 0)
        userId = int(getattr(message.author, "id", 0) or 0)
        key = self._copyServerApprovedGuildWarningKey(guildId, userId)
        if key not in self._pendingApprovedGuildCopyServerWarnings:
            return
        token = self.firstLowerToken(message.content or "")
        if token != "!copyserver":
            self._pendingApprovedGuildCopyServerWarnings.pop(key, None)

    def _armApprovedGuildCopyServerWarning(self, guildId: int, userId: int) -> None:
        self._pruneExpiredApprovedGuildCopyServerWarnings()
        key = self._copyServerApprovedGuildWarningKey(guildId, userId)
        self._pendingApprovedGuildCopyServerWarnings[key] = (
            datetime.now(timezone.utc) + timedelta(seconds=int(self._approvedGuildCopyServerWarningTtlSec))
        )

    def _consumeApprovedGuildCopyServerWarning(self, guildId: int, userId: int) -> bool:
        self._pruneExpiredApprovedGuildCopyServerWarnings()
        key = self._copyServerApprovedGuildWarningKey(guildId, userId)
        expiresAt = self._pendingApprovedGuildCopyServerWarnings.pop(key, None)
        return isinstance(expiresAt, datetime) and expiresAt > datetime.now(timezone.utc)

    def _copyServerSourceGuildId(self) -> int:
        try:
            sourceGuildId = int(getattr(self.config, "serverId", 0) or 0)
        except (TypeError, ValueError):
            sourceGuildId = 0
        return sourceGuildId if sourceGuildId > 0 else 0

    def _copyServerSourceGuildLabel(self, guildId: int) -> str:
        guild = self.botClient.get_guild(int(guildId or 0))
        if guild is not None and str(guild.name or "").strip():
            return str(guild.name).strip()
        return f"guild {int(guildId or 0)}"

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

    async def handleJaneHelp(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return False

        token = self.firstLowerToken(message.content or "")
        if token != ":)help":
            return False

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        sections = self.helpCommands.buildHelpSections(
            self.botClient.tree,
            guild=message.guild,
        )
        if bool(getattr(self.config, "temporaryCommandLockEnabled", False)):
            allowedIds = sorted(self.permissions.getTemporaryCommandAllowedUserIds())
            restrictionText = (
                "Temporary command lock is ON. Most commands are restricted to: "
                + (", ".join(f"`{userId}`" for userId in allowedIds) if allowedIds else "`(none configured)`")
            )
            if sections:
                overviewSection = dict(sections[0])
                overviewItems = list(overviewSection.get("items") or [])
                overviewItems.insert(
                    0,
                    {
                        "name": "Temporary Command Lock",
                        "description": restrictionText,
                        "permission": "Applies bot-wide until the rollout lock is disabled.",
                    },
                )
                overviewSection["items"] = overviewItems
                sections[0] = overviewSection

        view = runtimeHelpMenu.HelpMenuView(
            openerId=int(message.author.id),
            helpCommandsModule=self.helpCommands,
            sections=sections,
            currentSectionKey="overview",
        )
        await message.channel.send(
            embed=view.buildEmbed(),
            view=view,
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        return True

    async def handleJaneRuntime(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False

        token = self.firstLowerToken(message.content or "")
        if token != "?janeruntime":
            return False

        if not message.guild or not isinstance(message.author, discord.Member):
            return False

        member = message.author
        allowed = (
            member.id == message.guild.owner_id
            or member.guild_permissions.manage_guild
            or member.guild_permissions.administrator
            or self.hasCohostPermission(member)
        )
        if not allowed:
            try:
                await message.channel.send("You do not have permission to use this command.")
            except Exception:
                pass
            return True

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        now = datetime.now(timezone.utc)
        uptime = self.formatUptime(now - self.botStartedAt)
        startedAt = self.discordTimestamp(self.botStartedAt, "s")
        loop = asyncio.get_running_loop()

        def taskState(task: asyncio.Task | None) -> str:
            if task is None:
                return "not started"
            if task.cancelled():
                return "cancelled"
            if task.done():
                return "done"
            return "running"

        embed = discord.Embed(
            title="Jane Runtime",
            color=discord.Color.blurple(),
            timestamp=now,
        )
        embed.add_field(name="Ping", value=f"{round(self.botClient.latency * 1000)} ms", inline=True)
        embed.add_field(name="Uptime", value=uptime, inline=True)
        embed.add_field(name="Started", value=startedAt, inline=False)
        embed.add_field(name="Guilds", value=str(len(self.botClient.guilds)), inline=True)
        embed.add_field(name="Users Cached", value=str(len(self.botClient.users)), inline=True)
        embed.add_field(name="Cogs", value=str(len(self.botClient.cogs)), inline=True)
        nowUtc = datetime.now(timezone.utc)
        weeklyHour, weeklyMinute, weeklyWeekday = self.orbatWeeklyScheduleConfig()
        nextWeekly = self.maintenance.nextWeeklyRunAfter(
            nowUtc,
            weeklyHour,
            weeklyMinute,
            weeklyWeekday,
        )
        autoRecruitmentPayout = bool(self.maintenance.automaticRecruitmentPayoutEnabled())
        nextPayoutText = (
            self.discordTimestamp(self.maintenance.nextRecruitmentPayoutRun(nowUtc), "s")
            if autoRecruitmentPayout
            else "manual-only (disabled)"
        )
        embed.add_field(
            name="Background Tasks",
            value=(
                f"startupMaintenance: `{taskState(self.maintenance.startupMaintenanceTask)}`\n"
                f"globalOrbatUpdate: `{taskState(self.maintenance.globalOrbatUpdateTask)}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="Next Scheduled Checks",
            value=(
                f"weeklyOrbat: {self.discordTimestamp(nextWeekly, 's')}\n"
                f"recruitmentPayout: {nextPayoutText}"
            ),
            inline=False,
        )
        gitStats = {}
        if self.gitUpdateCoordinator is not None:
            try:
                gitStats = dict(self.gitUpdateCoordinator.getStats())
            except Exception:
                gitStats = {}
        if gitStats:
            lastCheckAt = str(gitStats.get("lastCheckAt") or "").strip()
            gitLines = [
                f"lastPull: {self._formatIsoTimestampOrNever(lastCheckAt)}",
                f"lastUpdate: {self._formatIsoTimestampOrNever(gitStats.get('lastUpdateAt'))}",
            ]
            embed.add_field(
                name="Most Recent Git Pull",
                value="\n".join(gitLines),
                inline=False,
            )
        budgetSnapshot = await self.taskBudgeter.getBudgeter().snapshot()
        budgetTotals = budgetSnapshot.get("totals", {}) if isinstance(budgetSnapshot, dict) else {}
        featureStats = budgetSnapshot.get("features", {}) if isinstance(budgetSnapshot, dict) else {}
        queueTelemetry = self.sessionViews.getRuntimeQueueTelemetry()
        pendingBackgroundTasks = (
            int(queueTelemetry.get("bgQueueUpdateActiveTasks", 0))
            + int(queueTelemetry.get("sessionUpdateActiveTasks", 0))
            + int(queueTelemetry.get("bgQueueRepostActiveTasks", 0))
        )
        embed.add_field(
            name="Background Job Telemetry",
            value=(
                f"queueDepth: `{int(budgetTotals.get('waiting', 0))}`\n"
                f"pendingTasks: `{int(budgetTotals.get('pending', 0)) + pendingBackgroundTasks}`\n"
                f"avgOpLatency: `{float(budgetTotals.get('avgLatencyMs', 0.0)):.2f} ms`"
            ),
            inline=False,
        )
        if isinstance(featureStats, dict) and featureStats:
            lines: list[str] = []
            for featureName in sorted(featureStats.keys()):
                stats = featureStats.get(featureName)
                if not isinstance(stats, dict):
                    continue
                lines.append(
                    f"{featureName}: q={int(stats.get('waiting', 0))} "
                    f"in={int(stats.get('inFlight', 0))} "
                    f"lat={float(stats.get('avgLatencyMs', 0.0)):.1f}ms"
                )
            if lines:
                embed.add_field(
                    name="Budgeted Features",
                    value="\n".join(lines[:8]),
                    inline=False,
                )
        if isinstance(self.maintenance.lastConfigSanitySummary, dict):
            warningCount = int(self.maintenance.lastConfigSanitySummary.get("warningCount", 0) or 0)
            errorCount = int(self.maintenance.lastConfigSanitySummary.get("errorCount", 0) or 0)
            embed.add_field(
                name="Config Sanity",
                value=f"errors: `{errorCount}` | warnings: `{warningCount}`",
                inline=True,
            )
        embed.add_field(
            name="Runtime",
            value=f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} | discord.py {discord.__version__}",
            inline=False,
        )
        processResources = self.getProcessResourceSnapshot(now)
        embed.add_field(
            name="Process Resources",
            value=(
                f"pid: `{processResources['pid']}`\n"
                f"cpu(avg): `{processResources['cpuPercent']}`\n"
                f"ram(rss): `{processResources['rss']}`\n"
                f"threads: `{processResources['threads']}`"
            ),
            inline=False,
        )
        dbSizeMb = 0.0
        try:
            dbSizeMb = self._dbPath().stat().st_size / (1024 * 1024)
        except Exception:
            dbSizeMb = 0.0
        embed.add_field(name="DB Size", value=f"{dbSizeMb:.2f} MB", inline=True)
        embed.add_field(
            name="Loop Time",
            value=f"{loop.time():.2f}",
            inline=True,
        )

        sentViaWebhook = await self.sendRuntimeWebhookMessage(message, embed)
        if not sentViaWebhook:
            await message.channel.send(embed=embed)
        return True

    async def handleJaneTerminal(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False

        token = self.firstLowerToken(message.content or "")
        if token != "!janeterminal":
            return False

        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        allowedUserId = self._janeTerminalAllowedUserId()
        if allowedUserId <= 0 or int(message.author.id) != allowedUserId:
            if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
                try:
                    await message.delete()
                except Exception:
                    pass
            return True

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        terminalContent = self._buildJaneTerminalContent()
        sentViaWebhook = await self.sendTerminalWebhookMessage(message, terminalContent)
        if not sentViaWebhook:
            await message.channel.send(terminalContent)
        return True

    async def handleShutdown(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False

        token = self.firstLowerToken(message.content or "")
        if token != "!shutdown":
            return False

        if not self._shutdownAllowed(int(message.author.id)):
            return True

        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        try:
            await message.channel.send(
                "Shutting down Jane.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
        except Exception:
            pass

        await self.botClient.close()
        return True

    async def handleAllowServer(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False

        token = self.firstLowerToken(message.content or "")
        if token != "!allowserver":
            return False

        if not self._shutdownAllowed(int(message.author.id)):
            return True

        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        status = str(self.allowGuildForCommands(int(message.guild.id)) or "invalid").strip().lower()
        if status == "already":
            response = "This server is already in Jane's allowed guild list."
        elif status == "runtime-only":
            response = "Added this server for the current runtime, but Jane could not persist it into config.py."
        elif status == "added":
            response = "Added this server to Jane's allowed guild list."
        else:
            response = "Jane could not add this server to the allowed guild list."

        try:
            await message.channel.send(
                response,
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
        except Exception:
            pass
        return True

    async def handleMirrorTrainingHistory(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False

        token = self.firstLowerToken(message.content or "")
        if token != "!mirrortraininghistory":
            return False

        if not self._shutdownAllowed(int(message.author.id)):
            return True

        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        if self.trainingLogCoordinator is None:
            try:
                await message.channel.send(
                    "Training history mirror is unavailable on this build.",
                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                )
            except Exception:
                pass
            return True

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        try:
            await message.channel.send(
                "Running the training history mirror now.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
        except Exception:
            pass

        try:
            succeeded, response = await self.trainingLogCoordinator.runManualMirrorBackfillOnce(
                userId=int(message.author.id),
            )
        except Exception as exc:
            response = f"Training history mirror failed: `{exc.__class__.__name__}`"
            succeeded = False

        try:
            await message.channel.send(
                response,
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
        except Exception:
            pass
        return True

    async def handleCopyServer(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False

        token = self.firstLowerToken(message.content or "")
        if token != "!copyserver":
            return False

        if not self._copyServerAllowed(int(message.author.id)):
            return True

        if not message.guild or not isinstance(message.author, discord.Member):
            return True

        if isinstance(message.channel, discord.Thread):
            await message.channel.send(
                "Run `!copyserver` in a normal text channel, not a thread.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
            return True

        guildId = int(message.guild.id)
        if self.isGuildAllowedForCommands(guildId):
            if not self._consumeApprovedGuildCopyServerWarning(guildId, int(message.author.id)):
                self._armApprovedGuildCopyServerWarning(guildId, int(message.author.id))
                await message.channel.send(
                    "Be VERY sure that you want to do this. This can be a nuke if used incorrectly. "
                    "Please retype `!copyserver` if you're certain.",
                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                )
                return True

        if guildId in self._activeCopyServerGuildIds:
            await message.channel.send(
                "A `!copyserver` run is already active for this guild. Wait for it to finish, or restart Jane if that run is truly dead.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
            return True

        if (
            self.serverSafetyService is None
            or not hasattr(self.serverSafetyService, "listGuildSnapshots")
            or not hasattr(self.serverSafetyService, "applySnapshotPathToGuild")
        ):
            await message.channel.send(
                "Copyserver is unavailable on this build.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
            return True

        sourceGuildId = self._copyServerSourceGuildId()
        if sourceGuildId <= 0:
            await message.channel.send(
                "Copyserver is unavailable because Jane's source guild is not configured.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
            return True

        existingState = runtimeCopyServerState.loadGuildState(guildId)
        sourceGuildLabel = self._copyServerSourceGuildLabel(sourceGuildId)
        latestSnapshotPath: Path | None = None
        existingTargetBackupPath = ""
        if isinstance(existingState, dict):
            existingSourceGuildId = int(existingState.get("sourceGuildId") or 0)
            candidateSnapshotPath = Path(str(existingState.get("snapshotPath") or "").strip())
            if existingSourceGuildId == sourceGuildId and candidateSnapshotPath.exists():
                latestSnapshotPath = candidateSnapshotPath
                sourceGuildLabel = str(existingState.get("sourceGuildLabel") or sourceGuildLabel).strip() or sourceGuildLabel
                existingTargetBackupPath = str(existingState.get("targetBackupPath") or "").strip()
            else:
                runtimeCopyServerState.clearGuildState(guildId)

        if latestSnapshotPath is None:
            try:
                latestSnapshots = await self.serverSafetyService.listGuildSnapshots(
                    self.config,
                    sourceGuildId,
                    limit=1,
                )
            except Exception:
                await message.channel.send(
                    "Copyserver could not load the latest main-server snapshot.",
                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                )
                return True
            if not latestSnapshots:
                await message.channel.send(
                    "No source snapshots were found for Jane's main server.",
                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                )
                return True
            latestSnapshotPath = latestSnapshots[0]
            latestSnapshotPath = latestSnapshotPath if isinstance(latestSnapshotPath, Path) else Path(str(latestSnapshotPath))

        latestSnapshotName = str(getattr(latestSnapshotPath, "name", latestSnapshotPath) or "unknown snapshot")
        try:
            snapshot = readSnapshot(latestSnapshotPath)
        except Exception:
            await message.channel.send(
                "Copyserver could not read the latest main-server snapshot.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
            return True
        cleanupEstimate = _estimateCopyServerCleanup(self.config, message.guild, snapshot)
        resumeEstimate = _estimateCopyServerResumeProgress(self.config, message.guild, snapshot)
        resumeEstimate, resumeRoleFloor = _applyPinnedResumeEstimate(resumeEstimate, existingState)
        auditUserId = 0
        try:
            auditUserId = int(getattr(self.config, "errorMirrorUserId", 0) or 0)
        except (TypeError, ValueError):
            auditUserId = 0
        view = CopyServerConfirmView(
            openerId=int(message.author.id),
            sourceGuildId=sourceGuildId,
            sourceGuildLabel=sourceGuildLabel,
            targetGuild=message.guild,
            botClient=self.botClient,
            allowGuildForCommands=self.allowGuildForCommands,
            configModule=self.config,
            serverSafetyService=self.serverSafetyService,
            snapshotPath=latestSnapshotPath,
            snapshot=snapshot,
            cleanupEstimate=cleanupEstimate,
            resumeEstimate=resumeEstimate,
            resumeRoleFloor=resumeRoleFloor,
            statusChannelId=int(message.channel.id),
            auditUserId=auditUserId,
            beginCopyServerRun=self._beginCopyServerRun,
            endCopyServerRun=self._endCopyServerRun,
            existingTargetBackupPath=existingTargetBackupPath,
        )
        content = view.buildInitialContent()

        sentViaWebhook = await self.sendCopyServerWebhookMessage(message, content, view)
        if not sentViaWebhook:
            await message.channel.send(
                content,
                view=view,
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
        return True

    def _beginCopyServerRun(self, guildId: int) -> bool:
        guildId = int(guildId or 0)
        if guildId <= 0:
            return False
        if guildId in self._activeCopyServerGuildIds:
            return False
        self._activeCopyServerGuildIds.add(guildId)
        return True

    def _endCopyServerRun(self, guildId: int) -> None:
        guildId = int(guildId or 0)
        if guildId > 0:
            self._activeCopyServerGuildIds.discard(guildId)

    async def handleBgCheckCommand(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return False

        token = self.firstLowerToken(message.content or "")
        if token not in {"?bgcheck", "?bg-check"}:
            return False

        if not self.permissions.hasBgCheckCertifiedRole(message.author):
            await message.channel.send("You do not have permission to start background-check queues.")
            return True
        ok, response = await self.createBgCheckQueue(
            guild=message.guild,
            channel=message.channel,
            actor=message.author,
            sourceMessage=message,
        )
        if not ok:
            await message.channel.send(response)
        return True

    async def handleBgLeaderboardCommand(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return False

        token = self.firstLowerToken(message.content or "")
        if token not in {"?bgleaderboard", "?bg-leaderboard"}:
            return False

        if not self.permissions.hasBgCheckCertifiedRole(message.author):
            await message.channel.send("You do not have permission to view the background-check leaderboard.")
            return True

        rows = await self.sessionService.getBgReviewLeaderboard(limit=15)
        if not rows:
            await message.channel.send("No background-check actions are logged yet.")
            return True

        if message.guild.me and message.channel.permissions_for(message.guild.me).manage_messages:
            try:
                await message.delete()
            except Exception:
                pass

        lines: list[str] = []
        for idx, row in enumerate(rows, start=1):
            reviewerId = int(row.get("reviewerId") or 0)
            approvals = int(row.get("approvals") or 0)
            rejections = int(row.get("rejections") or 0)
            total = int(row.get("total") or (approvals + rejections))
            if reviewerId <= 0:
                continue
            lines.append(
                f"{idx}. <@{reviewerId}>  |  Approved: `{approvals}`  |  Rejected: `{rejections}`  |  Total: `{total}`"
            )
        if not lines:
            await message.channel.send("No background-check actions are logged yet.")
            return True

        embed = discord.Embed(
            title="Background Check Leaderboard",
            description="\n".join(lines),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Counts are based on logged approve/reject decisions.")
        await message.channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
        return True

    def _permissionSimulatorGuildAllowed(self, guildId: int) -> bool:
        configured = getattr(self.config, "permissionSimulatorGuildIds", None) or [getattr(self.config, "serverId", 0)]
        allowedIds: set[int] = set()
        for raw in configured:
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                allowedIds.add(parsed)
        if not allowedIds:
            return False
        return int(guildId) in allowedIds

    def _likelyCommandAccess(self, member: discord.Member, commandPath: str) -> str:
        path = str(commandPath or "").strip().lower()
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return "Likely allowed (admin/manage-server bypass)."
        if path.startswith("/orientation"):
            roleId = int(getattr(self.config, "instructorRoleId", 0) or 0)
            hasRole = any(int(role.id) == roleId for role in member.roles) if roleId > 0 else False
            return "Likely allowed." if hasRole else "Likely denied (missing instructor role)."
        if path.startswith("/recruitment"):
            roleId = int(getattr(self.config, "recruiterRoleId", 0) or 0)
            hasRole = any(int(role.id) == roleId for role in member.roles) if roleId > 0 else False
            return "Likely allowed." if hasRole else "Likely denied (missing recruiter role)."
        if path.startswith("/bg-flag"):
            roleIds = self.permissions.getBgCheckCertifiedRoleIds()
            hasRole = any(int(role.id) in roleIds for role in member.roles)
            return "Likely allowed." if hasRole else "Likely denied (missing BG-certified role)."
        if path.startswith("/schedule-event"):
            mr = int(getattr(self.config, "middleRankRoleId", 0) or 0)
            hr = int(getattr(self.config, "highRankRoleId", 0) or 0)
            hasRole = any(int(role.id) in {mr, hr} for role in member.roles if int(role.id) > 0)
            return "Likely allowed." if hasRole else "Likely denied (missing MR/HR role)."
        if path.startswith("/archive") or path.startswith("/best-of") or path.startswith("/jail") or path.startswith("/unjail"):
            return "Likely denied (admin/manage-server required)."
        return "Permission depends on command-specific checks."

    async def handlePermissionSimulatorCommand(self, message: discord.Message) -> bool:
        if message.author.bot or not message.content:
            return False
        if not message.guild or not isinstance(message.author, discord.Member):
            return False

        token = self.firstLowerToken(message.content or "")
        if token not in {"?perm-sim", "?permsim"}:
            return False

        if not self._permissionSimulatorGuildAllowed(int(message.guild.id)):
            await message.channel.send("Permission simulator is only enabled in the test server.")
            return True
        if not (message.author.guild_permissions.administrator or message.author.guild_permissions.manage_guild):
            await message.channel.send("You do not have permission to use this command.")
            return True

        parts = str(message.content or "").strip().split(maxsplit=2)
        if len(parts) < 2:
            await message.channel.send("Usage: `?perm-sim /command-path [@user]`")
            return True
        commandPath = str(parts[1] or "").strip()
        if not commandPath.startswith("/"):
            commandPath = f"/{commandPath.lstrip('/')}"

        targetMember = message.author
        mentions = list(message.mentions)
        if mentions:
            mentioned = mentions[0]
            if isinstance(mentioned, discord.Member):
                targetMember = mentioned
            else:
                resolved = message.guild.get_member(int(mentioned.id))
                if resolved is not None:
                    targetMember = resolved

        hint = self.helpCommands.slashPermissionHint(commandPath)
        likely = self._likelyCommandAccess(targetMember, commandPath)
        roleIds = ", ".join(str(int(role.id)) for role in targetMember.roles if not role.is_default()) or "(none)"

        embed = discord.Embed(
            title="Permission Simulator (Hidden/Test)",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
            description=f"Command: `{commandPath}`\nTarget: {targetMember.mention}",
        )
        embed.add_field(name="Policy Hint", value=hint, inline=False)
        embed.add_field(name="Likely Result", value=likely, inline=False)
        embed.add_field(name="Target Roles", value=roleIds[:1000], inline=False)
        await message.channel.send(embed=embed)
        return True
