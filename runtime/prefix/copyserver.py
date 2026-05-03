from __future__ import annotations

import asyncio
import logging
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import discord

from runtime import copyServerState as runtimeCopyServerState
from runtime import interaction as interactionRuntime
from runtime import webhooks as runtimeWebhooks

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


COPYSERVER_ALLOWED_USER_IDS = {
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


def _copyServerApprovedGuildWarningKey(guildId: int, userId: int) -> tuple[int, int]:
    return (int(guildId or 0), int(userId or 0))


def _pruneExpiredApprovedGuildCopyServerWarnings(router: Any) -> None:
    now = datetime.now(timezone.utc)
    staleKeys = [
        key
        for key, expiresAt in router._pendingApprovedGuildCopyServerWarnings.items()
        if not isinstance(expiresAt, datetime) or expiresAt <= now
    ]
    for key in staleKeys:
        router._pendingApprovedGuildCopyServerWarnings.pop(key, None)


def noteCopyServerWarningMessage(router: Any, message: discord.Message) -> None:
    _pruneExpiredApprovedGuildCopyServerWarnings(router)
    if message.author.bot or not message.guild:
        return
    guildId = int(getattr(message.guild, "id", 0) or 0)
    userId = int(getattr(message.author, "id", 0) or 0)
    key = _copyServerApprovedGuildWarningKey(guildId, userId)
    if key not in router._pendingApprovedGuildCopyServerWarnings:
        return
    token = router.firstLowerToken(message.content or "")
    if token != "!copyserver":
        router._pendingApprovedGuildCopyServerWarnings.pop(key, None)


def _armApprovedGuildCopyServerWarning(router: Any, guildId: int, userId: int) -> None:
    _pruneExpiredApprovedGuildCopyServerWarnings(router)
    key = _copyServerApprovedGuildWarningKey(guildId, userId)
    router._pendingApprovedGuildCopyServerWarnings[key] = (
        datetime.now(timezone.utc) + timedelta(seconds=int(router._approvedGuildCopyServerWarningTtlSec))
    )


def _consumeApprovedGuildCopyServerWarning(router: Any, guildId: int, userId: int) -> bool:
    _pruneExpiredApprovedGuildCopyServerWarnings(router)
    key = _copyServerApprovedGuildWarningKey(guildId, userId)
    expiresAt = router._pendingApprovedGuildCopyServerWarnings.pop(key, None)
    return isinstance(expiresAt, datetime) and expiresAt > datetime.now(timezone.utc)


def _copyServerSourceGuildId(router: Any) -> int:
    try:
        sourceGuildId = int(getattr(router.config, "serverId", 0) or 0)
    except (TypeError, ValueError):
        sourceGuildId = 0
    return sourceGuildId if sourceGuildId > 0 else 0


def _copyServerSourceGuildLabel(router: Any, guildId: int) -> str:
    guild = router.botClient.get_guild(int(guildId or 0))
    if guild is not None and str(guild.name or "").strip():
        return str(guild.name).strip()
    return f"guild {int(guildId or 0)}"


def beginCopyServerRun(router: Any, guildId: int) -> bool:
    guildId = int(guildId or 0)
    if guildId <= 0:
        return False
    if guildId in router._activeCopyServerGuildIds:
        return False
    router._activeCopyServerGuildIds.add(guildId)
    return True


def endCopyServerRun(router: Any, guildId: int) -> None:
    guildId = int(guildId or 0)
    if guildId > 0:
        router._activeCopyServerGuildIds.discard(guildId)


async def handleCopyServer(router: Any, message: discord.Message) -> bool:
    if message.author.bot or not message.content:
        return False

    token = router.firstLowerToken(message.content or "")
    if token != "!copyserver":
        return False

    if not router._copyServerAllowed(int(message.author.id)):
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
    if router.isGuildAllowedForCommands(guildId):
        if not _consumeApprovedGuildCopyServerWarning(router, guildId, int(message.author.id)):
            _armApprovedGuildCopyServerWarning(router, guildId, int(message.author.id))
            await message.channel.send(
                "Be VERY sure that you want to do this. This can be a nuke if used incorrectly. "
                "Please retype `!copyserver` if you're certain.",
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
            return True

    if guildId in router._activeCopyServerGuildIds:
        await message.channel.send(
            "A `!copyserver` run is already active for this guild. Wait for it to finish, or restart Jane if that run is truly dead.",
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        return True

    if (
        router.serverSafetyService is None
        or not hasattr(router.serverSafetyService, "listGuildSnapshots")
        or not hasattr(router.serverSafetyService, "applySnapshotPathToGuild")
    ):
        await message.channel.send(
            "Copyserver is unavailable on this build.",
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        return True

    sourceGuildId = _copyServerSourceGuildId(router)
    if sourceGuildId <= 0:
        await message.channel.send(
            "Copyserver is unavailable because Jane's source guild is not configured.",
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        return True

    existingState = runtimeCopyServerState.loadGuildState(guildId)
    sourceGuildLabel = _copyServerSourceGuildLabel(router, sourceGuildId)
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
            latestSnapshots = await router.serverSafetyService.listGuildSnapshots(
                router.config,
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

    try:
        snapshot = readSnapshot(latestSnapshotPath)
    except Exception:
        await message.channel.send(
            "Copyserver could not read the latest main-server snapshot.",
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        return True
    cleanupEstimate = _estimateCopyServerCleanup(router.config, message.guild, snapshot)
    resumeEstimate = _estimateCopyServerResumeProgress(router.config, message.guild, snapshot)
    resumeEstimate, resumeRoleFloor = _applyPinnedResumeEstimate(resumeEstimate, existingState)
    auditUserId = 0
    try:
        auditUserId = int(getattr(router.config, "errorMirrorUserId", 0) or 0)
    except (TypeError, ValueError):
        auditUserId = 0
    view = CopyServerConfirmView(
        openerId=int(message.author.id),
        sourceGuildId=sourceGuildId,
        sourceGuildLabel=sourceGuildLabel,
        targetGuild=message.guild,
        botClient=router.botClient,
        allowGuildForCommands=router.allowGuildForCommands,
        configModule=router.config,
        serverSafetyService=router.serverSafetyService,
        snapshotPath=latestSnapshotPath,
        snapshot=snapshot,
        cleanupEstimate=cleanupEstimate,
        resumeEstimate=resumeEstimate,
        resumeRoleFloor=resumeRoleFloor,
        statusChannelId=int(message.channel.id),
        auditUserId=auditUserId,
        beginCopyServerRun=lambda targetGuildId: beginCopyServerRun(router, targetGuildId),
        endCopyServerRun=lambda targetGuildId: endCopyServerRun(router, targetGuildId),
        existingTargetBackupPath=existingTargetBackupPath,
    )
    content = view.buildInitialContent()

    sentViaWebhook = await router.sendCopyServerWebhookMessage(message, content, view)
    if not sentViaWebhook:
        await message.channel.send(
            content,
            view=view,
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
    return True



