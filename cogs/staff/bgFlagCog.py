from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.staff.bgItemReview import service as itemReviewService
from features.staff.bgflags import service as flagService
from runtime import interaction as interactionRuntime
from runtime import viewBases as runtimeViewBases
from features.staff.sessions.Roblox import robloxBadges

_modsOnlyMessage = "Mods only."

_flagTypeChoices = [
    app_commands.Choice(name="Group", value="group"),
    app_commands.Choice(name="Username", value="username"),
    app_commands.Choice(name="Roblox User ID", value="roblox_user"),
    app_commands.Choice(name="Watchlist User ID", value="watchlist"),
    app_commands.Choice(name="Banned User ID", value="banned_user"),
    app_commands.Choice(name="Keyword", value="keyword"),
    app_commands.Choice(name="Group Keyword", value="group_keyword"),
    app_commands.Choice(name="Item Keyword", value="item_keyword"),
    app_commands.Choice(name="Item", value="item"),
    app_commands.Choice(name="Creator", value="creator"),
    app_commands.Choice(name="Badge", value="badge"),
    app_commands.Choice(name="Favorite Game", value="game"),
    app_commands.Choice(name="Favorite Game Keyword", value="game_keyword"),
]
_flagTypeValues = {choice.value for choice in _flagTypeChoices}
_numericRuleTypes = {"group", "item", "creator", "badge", "roblox_user", "watchlist", "banned_user", "game"}
_rulesCategoryChoices = [
    discord.SelectOption(label="Groups", value="groups"),
    discord.SelectOption(label="Direct Users", value="users"),
    discord.SelectOption(label="Items / Accessories", value="items"),
    discord.SelectOption(label="Favorite Games", value="games"),
    discord.SelectOption(label="Keywords", value="keywords"),
    discord.SelectOption(label="Badges", value="badges"),
]
_rulesCategoryTypeMap = {
    "groups": {"group"},
    "users": {"roblox_user", "watchlist", "banned_user"},
    "items": {"item", "creator"},
    "games": {"game", "game_keyword"},
    "keywords": {"keyword", "username", "group_keyword", "item_keyword", "game_keyword"},
    "badges": {"badge"},
}


def _hasModPerm(member: discord.Member) -> bool:
    roleId = getattr(config, "moderatorRoleId", None)
    if roleId is None:
        return True
    return any(int(role.id) == int(roleId) for role in member.roles)


async def _requireModPermission(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if isinstance(member, discord.Member) and _hasModPerm(member):
        return True
    await interactionRuntime.safeInteractionReply(
        interaction,
        content=_modsOnlyMessage,
        ephemeral=True,
    )
    return False


def _normalizeRuleType(value: str) -> Optional[str]:
    normalized = str(value or "").strip().lower()
    if normalized in _flagTypeValues:
        return normalized
    return None


def _rulesListText(rules: list[dict]) -> str:
    lines: list[str] = []
    for rule in rules[:40]:
        note = f" - {rule['note']}" if rule.get("note") else ""
        severity = int(rule.get("severity") or 0)
        severityText = f" severity={severity}" if severity > 0 else ""
        lines.append(f"#{rule['ruleId']} [{rule['ruleType']}] {rule['ruleValue']}{severityText}{note}")
    if len(rules) > 40:
        lines.append(f"... and {len(rules) - 40} more")
    return "\n".join(lines)


def _ruleField(rule: dict) -> tuple[str, str]:
    ruleId = int(rule.get("ruleId") or 0)
    ruleType = str(rule.get("ruleType") or "").strip().lower()
    ruleValue = str(rule.get("ruleValue") or "").strip()
    note = str(rule.get("note") or "").strip()
    severity = int(rule.get("severity") or 0)
    fieldName = f"#{ruleId} [{ruleType}]"
    fieldValue = (
        f"Value: `{ruleValue}`\n"
        f"Severity: `{severity if severity > 0 else 'default'}`\n"
        f"Note: {note if note else '(none)'}"
    )
    return fieldName, fieldValue


def _formatVisualRefSyncResult(result: dict) -> str:
    assetCount = int(result.get("assetCount") or 0)
    validCount = int(result.get("validatedCount") or 0)
    invalidCount = int(result.get("invalidCount") or 0)
    errorCount = int(result.get("errorCount") or 0)
    pendingCount = int(result.get("pendingCount") or 0)
    checkedCount = int(result.get("checkedCount") or 0)
    removedCount = int(result.get("removedCount") or 0)
    parts = [
        f"assets={assetCount}",
        f"valid={validCount}",
        f"invalid={invalidCount}",
        f"errors={errorCount}",
    ]
    if pendingCount > 0:
        parts.append(f"pending={pendingCount}")
    if checkedCount > 0:
        parts.append(f"checked={checkedCount}")
    if removedCount > 0:
        parts.append(f"removed={removedCount}")
    issues = [str(value).strip() for value in list(result.get("sampleIssues") or []) if str(value).strip()]
    if issues:
        parts.append("issues=" + "; ".join(issues[:3]))
    return ", ".join(parts)


def _buildQueueFlagEmbed(queueRows: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="Queued Item Flags",
        description="Reviewer-flagged queue items that now feed visual matching.",
        color=discord.Color.blurple(),
    )
    if not queueRows:
        embed.add_field(name="Items", value="No flagged queue items found.", inline=False)
        return embed

    lines: list[str] = []
    for row in queueRows[:15]:
        queueId = int(row.get("queueId") or 0)
        assetId = int(row.get("assetId") or 0)
        assetName = str(row.get("assetName") or f"Asset {assetId}").strip() or f"Asset {assetId}"
        creatorName = str(row.get("creatorName") or "unknown").strip() or "unknown"
        reviewNote = str(row.get("reviewNote") or "").strip()
        line = f"`#{queueId}` `{assetId}` {assetName} | {creatorName}"
        if reviewNote:
            trimmedNote = reviewNote if len(reviewNote) <= 80 else reviewNote[:77].rstrip() + "..."
            line += f"\n{trimmedNote}"
        lines.append(line)

    embed.add_field(name="Flagged Queue Items", value="\n".join(lines)[:1024], inline=False)
    if len(queueRows) > 15:
        embed.set_footer(text=f"Showing 15 of {len(queueRows)} flagged queue items.")
    return embed


class BgRulesPanelView(discord.ui.View):
    def __init__(self, rules: list[dict], *, pageSize: int = 5) -> None:
        super().__init__(timeout=600)
        self.rules = list(rules or [])
        self.pageSize = max(1, min(10, int(pageSize)))
        self.selectedCategory = "groups"
        self.pageIndex = 0
        self._syncControls()

    def _filteredRules(self) -> list[dict]:
        allowedTypes = _rulesCategoryTypeMap.get(self.selectedCategory, set())
        if not allowedTypes:
            return []
        return [
            rule for rule in self.rules
            if str(rule.get("ruleType") or "").strip().lower() in allowedTypes
        ]

    def _pageCount(self, filtered: list[dict]) -> int:
        if not filtered:
            return 1
        return max(1, (len(filtered) + self.pageSize - 1) // self.pageSize)

    def _buildEmbed(self) -> discord.Embed:
        filtered = self._filteredRules()
        pageCount = self._pageCount(filtered)
        self.pageIndex = min(max(0, self.pageIndex), pageCount - 1)
        start = self.pageIndex * self.pageSize
        end = min(len(filtered), start + self.pageSize)
        pageRows = filtered[start:end]

        categoryLabel = next(
            (choice.label for choice in _rulesCategoryChoices if choice.value == self.selectedCategory),
            self.selectedCategory.title(),
        )
        embed = discord.Embed(
            title="BG Flag Rules",
            description=(
                f"Category: **{categoryLabel}**\n"
                f"Page **{self.pageIndex + 1}/{pageCount}** | "
                f"Showing **{start + 1 if filtered else 0}-{end}** of **{len(filtered)}**"
            ),
            color=discord.Color.blurple(),
        )
        if not pageRows:
            embed.add_field(name="Rules", value="No rules in this category.", inline=False)
        else:
            for rule in pageRows:
                fieldName, fieldValue = _ruleField(rule)
                embed.add_field(name=fieldName, value=fieldValue, inline=False)
        embed.set_footer(text="Use the dropdown to switch category and buttons to change pages.")
        return embed

    def _syncControls(self) -> None:
        filtered = self._filteredRules()
        pageCount = self._pageCount(filtered)
        self.pageIndex = min(max(0, self.pageIndex), pageCount - 1)
        self.prevBtn.disabled = self.pageIndex <= 0
        self.nextBtn.disabled = self.pageIndex >= pageCount - 1
        self.typeSelect.placeholder = f"Category: {self.selectedCategory}"

    async def _refresh(self, interaction: discord.Interaction) -> None:
        self._syncControls()
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            embed=self._buildEmbed(),
            view=self,
        )

    @discord.ui.select(
        row=0,
        options=_rulesCategoryChoices,
        placeholder="Select rule category",
        min_values=1,
        max_values=1,
    )
    async def typeSelect(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        value = str(select.values[0] if select.values else "").strip().lower()
        if value not in _rulesCategoryTypeMap:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Invalid rule category selection.",
                ephemeral=True,
            )
            return
        self.selectedCategory = value
        self.pageIndex = 0
        await self._refresh(interaction)

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=1)
    async def prevBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.pageIndex = max(0, self.pageIndex - 1)
        await self._refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def nextBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.pageIndex += 1
        await self._refresh(interaction)


class BgFlagAddRuleModal(discord.ui.Modal, title="Add BG Flag Rule"):
    ruleType = discord.ui.TextInput(
        label="Rule Type",
        placeholder="group / username / roblox_user / watchlist / banned_user / keyword / item / badge / game",
        required=True,
        max_length=32,
    )
    value = discord.ui.TextInput(
        label="Rule Value",
        placeholder="ID (numeric) or lowercase value/keyword",
        required=True,
        max_length=200,
    )
    note = discord.ui.TextInput(
        label="Note (optional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300,
    )
    severity = discord.ui.TextInput(
        label="Severity / min score (optional)",
        placeholder="1-100. Blank uses Jane's default for this rule type.",
        required=False,
        max_length=3,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _requireModPermission(interaction):
            return

        normalizedRuleType = _normalizeRuleType(str(self.ruleType))
        if not normalizedRuleType:
            valid = ", ".join(sorted(_flagTypeValues))
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"Invalid rule type. Valid options: {valid}",
                ephemeral=True,
            )
            return

        rawValue = str(self.value).strip()
        if normalizedRuleType in _numericRuleTypes:
            try:
                parsed = int(rawValue)
            except ValueError:
                await interactionRuntime.safeInteractionReply(
                    interaction,
                    content="IDs must be numeric for group/item/creator/badge/direct-user/game rules.",
                    ephemeral=True,
                )
                return
            normalizedValue = str(parsed)
        else:
            normalizedValue = rawValue.lower()

        noteText = str(self.note).strip() or None
        rawSeverity = str(self.severity).strip()
        severityValue = 0
        if rawSeverity:
            try:
                severityValue = flagService.normalizeSeverity(rawSeverity)
            except (TypeError, ValueError):
                severityValue = 0
            if severityValue <= 0:
                await interactionRuntime.safeInteractionReply(
                    interaction,
                    content="Severity must be a number from 1 to 100, or left blank for Jane's default.",
                    ephemeral=True,
                )
                return

        if normalizedRuleType == "item":
            await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
            validation = await flagService.validateItemVisualReference(int(normalizedValue))
            validationState = str(validation.get("validationState") or "").strip().upper()
            if validationState != "VALID":
                errorText = str(validation.get("validationError") or "").strip() or "Item thumbnail validation failed."
                await interactionRuntime.safeInteractionReply(
                    interaction,
                    content=f"Jane could not validate item `{normalizedValue}` as a usable visual reference. {errorText}",
                    ephemeral=True,
                )
                return

        ruleId = await flagService.addRule(
            normalizedRuleType,
            normalizedValue,
            noteText,
            interaction.user.id,
            severityValue,
        )
        severityText = f" with severity {severityValue}" if severityValue > 0 else ""
        extraText = ""
        if normalizedRuleType == "item":
            syncResult = await flagService.syncItemVisualReferences(force=False)
            extraText = f" Visual refs synced: {_formatVisualRefSyncResult(syncResult)}."
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Added {normalizedRuleType} rule #{ruleId}{severityText}.{extraText}",
            ephemeral=True,
        )


class BgFlagRemoveRuleModal(discord.ui.Modal, title="Remove BG Flag Rule"):
    ruleId = discord.ui.TextInput(
        label="Rule ID",
        placeholder="Numeric rule ID",
        required=True,
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _requireModPermission(interaction):
            return

        try:
            parsedRuleId = int(str(self.ruleId).strip())
        except ValueError:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Rule ID must be numeric.",
                ephemeral=True,
            )
            return

        existingRule = await flagService.getRule(parsedRuleId)
        removedRuleType = str((existingRule or {}).get("ruleType") or "").strip().lower()
        await flagService.removeRule(parsedRuleId)
        extraText = ""
        if removedRuleType == "item":
            await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
            syncResult = await flagService.syncItemVisualReferences(force=False)
            extraText = f" Visual refs synced: {_formatVisualRefSyncResult(syncResult)}."
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Removed rule #{parsedRuleId}.{extraText}",
            ephemeral=True,
        )


class BgFlagImportBadgesModal(discord.ui.Modal, title="Import Badge Rules"):
    universeId = discord.ui.TextInput(
        label="Universe ID",
        placeholder="Numeric Roblox universe ID",
        required=True,
        max_length=30,
    )
    maxBadges = discord.ui.TextInput(
        label="Max Badges (optional)",
        placeholder="Default from config if blank",
        required=False,
        max_length=6,
    )
    note = discord.ui.TextInput(
        label="Note (optional)",
        required=False,
        max_length=200,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _requireModPermission(interaction):
            return

        try:
            parsedUniverseId = int(str(self.universeId).strip())
        except ValueError:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Universe ID must be numeric.",
                ephemeral=True,
            )
            return

        rawMax = str(self.maxBadges).strip()
        if rawMax:
            try:
                limit = int(rawMax)
            except ValueError:
                await interactionRuntime.safeInteractionReply(
                    interaction,
                    content="Max badges must be numeric.",
                    ephemeral=True,
                )
                return
        else:
            limit = int(getattr(config, "robloxBadgeImportMax", 200) or 200)

        if limit <= 0:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Max badges must be greater than 0.",
                ephemeral=True,
            )
            return

        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)

        existing = await flagService.listRules("badge")
        existingIds: set[int] = set()
        for rule in existing:
            try:
                existingIds.add(int(rule.get("ruleValue")))
            except (TypeError, ValueError):
                continue

        added = 0
        skipped = 0
        cursor: Optional[str] = None
        sortOrder = "Asc"
        batchSize = 100
        noteSuffix = str(self.note).strip() or None

        while added < limit:
            pageLimit = min(batchSize, limit - added)
            result = await robloxBadges.fetchRobloxUniverseBadges(
                parsedUniverseId,
                limit=pageLimit,
                cursor=cursor,
                sortOrder=sortOrder,
            )
            if result.error:
                await interactionRuntime.safeInteractionReply(
                    interaction,
                    content=f"Import stopped: {result.error}",
                    ephemeral=True,
                )
                return
            if not result.badges:
                break

            for badge in result.badges:
                badgeId = badge.get("id")
                if badgeId is None:
                    continue
                if badgeId in existingIds:
                    skipped += 1
                    continue
                badgeName = badge.get("name")
                noteParts = []
                if badgeName:
                    noteParts.append(str(badgeName))
                noteParts.append(f"universe {parsedUniverseId}")
                if noteSuffix:
                    noteParts.append(noteSuffix)
                noteText = " | ".join(noteParts) if noteParts else None
                await flagService.addRule("badge", str(badgeId), noteText, interaction.user.id)
                existingIds.add(badgeId)
                added += 1
                if added >= limit:
                    break

            cursor = result.nextCursor
            if not cursor:
                break

        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Imported {added} badge rules (skipped {skipped}).",
            ephemeral=True,
        )


class BgFlagPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=600)

    @discord.ui.button(label="Add Rule", style=discord.ButtonStyle.success, row=0)
    async def addRuleBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _requireModPermission(interaction):
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            BgFlagAddRuleModal(),
        )

    @discord.ui.button(label="Remove Rule", style=discord.ButtonStyle.danger, row=0)
    async def removeRuleBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _requireModPermission(interaction):
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            BgFlagRemoveRuleModal(),
        )

    @discord.ui.button(label="List Rules", style=discord.ButtonStyle.secondary, row=0)
    async def listRulesBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _requireModPermission(interaction):
            return
        rules = await flagService.listRules(None)
        if not rules:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="No rules found.",
                ephemeral=True,
            )
            return
        view = BgRulesPanelView(rules)
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=view._buildEmbed(),
            view=view,
            ephemeral=True,
        )

    @discord.ui.button(label="Import Badges", style=discord.ButtonStyle.primary, row=0)
    async def importBadgesBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _requireModPermission(interaction):
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            BgFlagImportBadgesModal(),
        )

    @discord.ui.button(label="Sync Visual Refs", style=discord.ButtonStyle.secondary, row=1)
    async def syncVisualRefsBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _requireModPermission(interaction):
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
        result = await flagService.syncItemVisualReferences(force=True)
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Visual reference sync complete: {_formatVisualRefSyncResult(result)}.",
            ephemeral=True,
        )

    @discord.ui.button(label="List Queue Flags", style=discord.ButtonStyle.secondary, row=1)
    async def listQueueFlagsBtn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _requireModPermission(interaction):
            return
        queueRows = await itemReviewService.listQueueEntriesByStatus(
            [itemReviewService.STATUS_FLAGGED],
            guildId=int(interaction.guild_id or 0) or None,
            limit=15,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=_buildQueueFlagEmbed(queueRows),
            ephemeral=True,
        )


class BgFlagCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="bg-flag", description="Open the background-check flag manager panel.")
    async def bgFlagPanel(self, interaction: discord.Interaction) -> None:
        if not await _requireModPermission(interaction):
            return

        embed = discord.Embed(
            title="BG Flag Manager",
            description=(
                "Use the panel buttons below to manage background-check flags.\n"
                "Optional severity is a 1-100 minimum score for direct user rules. "
                "Leave it blank for Jane's default.\n"
                "Exact `item` rules also feed Jane's visual thumbnail matcher, so new item IDs must resolve to a valid Roblox thumbnail.\n"
                "Supported rule types:\n"
                "`group`, `username`, `roblox_user`, `watchlist`, `banned_user`, "
                "`keyword`, `group_keyword`, `item_keyword`, `game_keyword`, "
                "`item`, `creator`, `badge`, `game`"
            ),
            color=discord.Color.blurple(),
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=embed,
            view=BgFlagPanelView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(BgFlagCog(bot))

