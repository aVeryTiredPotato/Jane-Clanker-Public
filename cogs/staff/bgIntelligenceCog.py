from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.staff.bgIntelligence import rendering, scoring, service
from features.staff.sessions.Roblox import robloxUsers
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions

log = logging.getLogger(__name__)

ProgressUpdater = Callable[[str], Awaitable[bool]]


BG_INTEL_SECTIONS: tuple[tuple[str, str], ...] = (
    ("overview", "Overview"),
    ("scan", "Detection Summary"),
    ("sources", "Source Checks"),
    ("profile", "Profile Information"),
    ("connections", "Connections"),
    ("groups", "Groups"),
    ("inventory", "Inventory"),
    ("gamepasses", "Gamepasses"),
    ("games", "Favorites"),
    ("outfits", "Outfits"),
    ("badges", "Badges"),
    ("external", "Safety Records"),
    ("history", "Jane History"),
)


class BgIntelSectionSelect(discord.ui.Select):
    def __init__(self, selectedSection: str = "overview") -> None:
        super().__init__(
            placeholder="Expand a BG intelligence section",
            min_values=1,
            max_values=1,
            row=0,
            options=[
                discord.SelectOption(label=label, value=section, default=section == selectedSection)
                for section, label in BG_INTEL_SECTIONS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BgIntelDetailsView):
            return
        section = str(self.values[0] if self.values else "overview")
        await view.showSection(interaction, section)


class BgIntelDmInventoryButton(discord.ui.Button):
    def __init__(self, *, enabled: bool) -> None:
        super().__init__(
            label="DM Inventory Request",
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=not enabled,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BgIntelDetailsView):
            await view.sendInventoryNotice(interaction)


class BgIntelRerunButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Rerun Scan",
            style=discord.ButtonStyle.primary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BgIntelDetailsView):
            await view.requestRerun(interaction)


class BgIntelSummaryButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Decision Summary",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if isinstance(view, BgIntelDetailsView):
            await view.sendDecisionSummary(interaction)


class BgIntelRobloxUsernameModal(discord.ui.Modal):
    def __init__(self, detailsView: "BgIntelDetailsView") -> None:
        super().__init__(title="Rerun BG Intel")
        self.detailsView = detailsView
        existingUsername = str(getattr(detailsView.report, "robloxUsername", "") or "").strip()
        self.robloxUsername = discord.ui.TextInput(
            label="Roblox username",
            placeholder="Enter the Roblox username to pair with this Discord ID",
            default=existingUsername[:20],
            min_length=3,
            max_length=20,
            required=True,
        )
        self.add_item(self.robloxUsername)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != int(self.detailsView.ownerId):
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="This BG intelligence panel belongs to the reviewer who ran the scan.",
                ephemeral=True,
            )
        await self.detailsView.rerunScan(
            interaction,
            robloxUsernameOverride=str(self.robloxUsername.value or "").strip(),
        )


class BgIntelDetailsView(discord.ui.View):
    def __init__(
        self,
        *,
        ownerId: int,
        report,
        riskScore: scoring.RiskScore,
        reportId: int,
        includeTextReport: bool = False,
        roverGuildId: int | None = None,
        robloxUsernameOverride: str | None = None,
        notifyPrivateInventory: bool = False,
    ) -> None:
        super().__init__(timeout=900)
        self.ownerId = int(ownerId)
        self.report = report
        self.riskScore = riskScore
        self.reportId = int(reportId or 0)
        self.includeTextReport = bool(includeTextReport)
        self.roverGuildId = int(roverGuildId or 0) or None
        self.robloxUsernameOverride = str(robloxUsernameOverride or "").strip() or None
        self.notifyPrivateInventory = bool(notifyPrivateInventory)
        self.currentSection = "overview"
        self._rebuildControls("overview")

    def _robloxProfileUrl(self) -> str | None:
        try:
            robloxUserId = int(getattr(self.report, "robloxUserId", 0) or 0)
        except (TypeError, ValueError):
            return None
        if robloxUserId <= 0:
            return None
        return f"https://www.roblox.com/users/{robloxUserId}/profile"

    def _addActionButtons(self) -> None:
        profileUrl = self._robloxProfileUrl()
        if profileUrl:
            self.add_item(
                discord.ui.Button(
                    label="Roblox Profile",
                    style=discord.ButtonStyle.link,
                    url=profileUrl,
                    row=1,
                )
            )
        self.add_item(BgIntelSummaryButton())
        self.add_item(BgIntelRerunButton())
        inventoryPrivate = str(getattr(self.report, "inventoryScanStatus", "") or "").strip().upper() == "PRIVATE"
        hasDiscordTarget = int(getattr(self.report, "discordUserId", 0) or 0) > 0
        self.add_item(BgIntelDmInventoryButton(enabled=inventoryPrivate and hasDiscordTarget))

    def _rebuildControls(self, section: str) -> None:
        self.clear_items()
        self.add_item(BgIntelSectionSelect(section))
        self._addActionButtons()
        self._syncSelectedSection(section)

    def _badgeGraphFilename(self) -> str:
        reportId = self.reportId if self.reportId > 0 else 0
        robloxUserId = int(getattr(self.report, "robloxUserId", 0) or 0)
        suffix = reportId or robloxUserId or int(self.ownerId)
        return f"bg-intel-badges-{suffix}.png"

    def _reportTextFilename(self) -> str:
        reportId = self.reportId if self.reportId > 0 else 0
        robloxUserId = int(getattr(self.report, "robloxUserId", 0) or 0)
        suffix = reportId or robloxUserId or int(self.ownerId)
        return f"bg-intel-report-{suffix}.txt"

    def _applyBadgeGraph(self, embed: discord.Embed, section: str) -> discord.File | None:
        normalizedSection = str(section or "overview").strip().lower()
        if normalizedSection not in {"overview", "badges"}:
            return None
        return rendering.applyBadgeTimelineGraph(
            embed,
            self.report,
            filename=self._badgeGraphFilename(),
        )

    def _buildPublicPayload(self, section: str) -> tuple[discord.Embed, list[discord.File]]:
        normalizedSection = str(section or "overview").strip().lower()
        embed = rendering.buildPublicSectionEmbed(
            self.report,
            score=self.riskScore,
            section=normalizedSection,
            reportId=self.reportId if self.reportId > 0 else None,
            includeTextReport=self.includeTextReport,
        )
        graphFile = self._applyBadgeGraph(embed, normalizedSection)
        files: list[discord.File] = []
        if graphFile is not None:
            files.append(graphFile)
        if self.includeTextReport:
            files.append(
                rendering.buildReportTextFile(
                    self.report,
                    score=self.riskScore,
                    reportId=self.reportId if self.reportId > 0 else None,
                    filename=self._reportTextFilename(),
                )
            )
        return embed, files

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == int(self.ownerId):
            return True
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="This BG intelligence panel belongs to the reviewer who ran the scan.",
            ephemeral=True,
        )
        return False

    def _syncSelectedSection(self, section: str) -> None:
        normalizedSection = str(section or "overview").strip().lower()
        validSections = {item[0] for item in BG_INTEL_SECTIONS}
        if normalizedSection not in validSections:
            normalizedSection = "overview"
        self.currentSection = normalizedSection
        for child in self.children:
            if isinstance(child, BgIntelSectionSelect):
                for option in child.options:
                    option.default = option.value == normalizedSection

    async def _finishEphemeral(self, interaction: discord.Interaction, content: str) -> None:
        try:
            await interaction.edit_original_response(content=content, embed=None, view=None, attachments=[])
            return
        except (discord.NotFound, discord.HTTPException, AttributeError, TypeError):
            pass
        try:
            await interaction.followup.send(
                content=content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.NotFound, discord.HTTPException, AttributeError, TypeError):
            return

    async def _fetchMemberForAction(
        self,
        interaction: discord.Interaction,
        discordUserId: int,
    ) -> discord.Member | None:
        if discordUserId <= 0:
            return None
        guild = interaction.guild
        if guild is not None:
            member = guild.get_member(int(discordUserId))
            if member is not None:
                return member
            try:
                return await guild.fetch_member(int(discordUserId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        try:
            mainGuildId = int(getattr(config, "serverId", 0) or 0)
        except (TypeError, ValueError):
            mainGuildId = 0
        if mainGuildId <= 0 or (guild is not None and int(guild.id) == mainGuildId):
            return None
        mainGuild = interaction.client.get_guild(mainGuildId)
        if mainGuild is None:
            try:
                mainGuild = await interaction.client.fetch_guild(mainGuildId)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
                return None
        try:
            return mainGuild.get_member(int(discordUserId)) or await mainGuild.fetch_member(int(discordUserId))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, AttributeError):
            return None

    async def sendDecisionSummary(self, interaction: discord.Interaction) -> None:
        summary = rendering.buildDecisionSummary(
            self.report,
            score=self.riskScore,
            reportId=self.reportId if self.reportId > 0 else None,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"```text\n{summary[:1850]}\n```",
            ephemeral=True,
            allowedMentions=discord.AllowedMentions.none(),
        )

    async def sendInventoryNotice(self, interaction: discord.Interaction) -> None:
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        discordUserId = int(getattr(self.report, "discordUserId", 0) or 0)
        member = await self._fetchMemberForAction(interaction, discordUserId)
        if member is None:
            return await self._finishEphemeral(
                interaction,
                "I couldn't find that Discord member to send the inventory request.",
            )
        sent = await service.sendPrivateInventoryNotice(member, reviewer=interaction.user)
        self.report.privateInventoryDmSent = bool(sent)
        messageObject = getattr(interaction, "message", None)
        if messageObject is not None:
            embed, attachments = self._buildPublicPayload(self.currentSection)
            await interactionRuntime.safeMessageEdit(
                messageObject,
                embed=embed,
                view=self,
                attachments=attachments,
            )
        message = "Inventory request DM sent." if sent else "I couldn't DM that user. They may have DMs closed."
        await self._finishEphemeral(interaction, message)

    def _needsRobloxUsernameForRerun(self) -> bool:
        try:
            discordUserId = int(getattr(self.report, "discordUserId", 0) or 0)
        except (TypeError, ValueError):
            discordUserId = 0
        try:
            robloxUserId = int(getattr(self.report, "robloxUserId", 0) or 0)
        except (TypeError, ValueError):
            robloxUserId = 0
        return discordUserId > 0 and robloxUserId <= 0

    async def requestRerun(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="This scan can only be rerun inside a server.",
                ephemeral=True,
            )
        if self._needsRobloxUsernameForRerun():
            try:
                await interaction.response.send_modal(BgIntelRobloxUsernameModal(self))
                return
            except (discord.NotFound, discord.HTTPException, AttributeError):
                return await interactionRuntime.safeInteractionReply(
                    interaction,
                    content="I couldn't open the Roblox username prompt. Run `/bg-intel` again with the Discord ID and Roblox username.",
                    ephemeral=True,
                )
        await self.rerunScan(interaction)

    async def rerunScan(
        self,
        interaction: discord.Interaction,
        *,
        robloxUsernameOverride: str | None = None,
    ) -> None:
        if interaction.guild is None:
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="This scan can only be rerun inside a server.",
                ephemeral=True,
            )
        cleanRobloxUsernameOverride = str(robloxUsernameOverride or "").strip()
        if robloxUsernameOverride is not None and not cleanRobloxUsernameOverride:
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="Please enter a Roblox username before rerunning the scan.",
                ephemeral=True,
            )
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)

        async def progress(status: str) -> bool:
            cleanStatus = str(status or "Running scan...").strip() or "Running scan..."
            try:
                await interaction.edit_original_response(
                    content=f"Jane is rerunning the background intel scan.\nStatus: {cleanStatus}",
                    embed=None,
                    view=None,
                    attachments=[],
                )
                return True
            except (discord.NotFound, discord.HTTPException, AttributeError, TypeError):
                return False

        try:
            await progress("Preparing rerun...")
            discordUserId = int(getattr(self.report, "discordUserId", 0) or 0)
            robloxUserId = int(getattr(self.report, "robloxUserId", 0) or 0)
            effectiveRobloxUsernameOverride = cleanRobloxUsernameOverride or self.robloxUsernameOverride
            member = await self._fetchMemberForAction(interaction, discordUserId) if discordUserId > 0 else None
            if member is not None:
                report = await service.buildReport(
                    member,
                    guild=interaction.guild,
                    reviewBucketOverride="adult",
                    roverGuildId=self.roverGuildId,
                    robloxUsernameOverride=effectiveRobloxUsernameOverride,
                    notifyPrivateInventory=self.notifyPrivateInventory,
                    reviewer=interaction.user,
                    configModule=config,
                    progressCallback=progress,
                )
            elif discordUserId > 0:
                report = await service.buildReportForDiscordId(
                    guild=interaction.guild,
                    discordUserId=discordUserId,
                    displayMember=None,
                    roverGuildId=self.roverGuildId,
                    robloxUsernameOverride=effectiveRobloxUsernameOverride,
                    reviewBucketOverride="adult",
                    configModule=config,
                    progressCallback=progress,
                )
            else:
                report = await service.buildReportForRobloxIdentity(
                    guild=interaction.guild,
                    robloxUserId=robloxUserId if robloxUserId > 0 else None,
                    robloxUsername=effectiveRobloxUsernameOverride or getattr(self.report, "robloxUsername", None),
                    reviewBucketOverride="adult",
                    configModule=config,
                    progressCallback=progress,
                )
            await progress("Scoring and saving rerun...")
            riskScore = scoring.scoreReport(report, configModule=config)
            channelId = int(getattr(getattr(interaction, "channel", None), "id", 0) or 0)
            reportId = await service.recordReport(
                guildId=int(interaction.guild.id),
                channelId=channelId,
                reviewerId=int(interaction.user.id),
                report=report,
                riskScore=riskScore,
            )
        except Exception:
            log.exception("BG intelligence rerun failed.")
            return await self._finishEphemeral(
                interaction,
                "BG intelligence rerun failed internally. Check Jane's logs before trusting the result.",
            )

        self.report = report
        self.riskScore = riskScore
        self.reportId = int(reportId or 0)
        if cleanRobloxUsernameOverride:
            self.robloxUsernameOverride = cleanRobloxUsernameOverride
        self._rebuildControls("overview")
        embed, attachments = self._buildPublicPayload("overview")
        message = getattr(interaction, "message", None)
        if message is not None:
            await interactionRuntime.safeMessageEdit(
                message,
                embed=embed,
                view=self,
                attachments=attachments,
            )
        await self._finishEphemeral(interaction, "Rerun complete. The BG intelligence panel was refreshed.")

    async def showSection(self, interaction: discord.Interaction, section: str) -> None:
        normalizedSection = str(section or "overview").strip().lower()
        self._syncSelectedSection(normalizedSection)
        embed, attachments = self._buildPublicPayload(normalizedSection)
        try:
            await interaction.response.edit_message(embed=embed, view=self, attachments=attachments)
            return
        except (discord.NotFound, discord.HTTPException):
            message = getattr(interaction, "message", None)
            if message is not None:
                fallbackEmbed, fallbackAttachments = self._buildPublicPayload(normalizedSection)
                edited = await interactionRuntime.safeMessageEdit(
                    message,
                    embed=fallbackEmbed,
                    view=self,
                    attachments=fallbackAttachments,
                )
                if edited:
                    await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
                    return
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="I couldn't expand that section on the webhook message.",
            ephemeral=True,
            allowedMentions=discord.AllowedMentions.none(),
        )


class BgIntelligenceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _bgIntelProgressContent(status: str, *, targetLabel: str) -> str:
        cleanStatus = str(status or "Starting scan...").strip() or "Starting scan..."
        cleanTarget = str(targetLabel or "selected user").strip() or "selected user"
        cleanTarget = cleanTarget.replace("`", "'")[:80]
        return (
            "Jane is running the background intel scan.\n"
            f"Target: `{cleanTarget}`\n"
            f"Status: {cleanStatus}\n"
            "Large badge or inventory histories can take a moment."
        )

    async def _editBgIntelStatus(
        self,
        interaction: discord.Interaction,
        status: str,
        *,
        targetLabel: str,
    ) -> bool:
        try:
            await interaction.edit_original_response(
                content=self._bgIntelProgressContent(status, targetLabel=targetLabel),
            )
            return True
        except (discord.NotFound, discord.HTTPException, AttributeError, TypeError):
            return False

    async def _finishBgIntelStatus(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> bool:
        try:
            await interaction.edit_original_response(content=message)
            return True
        except (discord.NotFound, discord.HTTPException, AttributeError, TypeError):
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content=message,
                ephemeral=True,
                allowedMentions=discord.AllowedMentions.none(),
            )

    async def _sendBgIntelMessage(
        self,
        interaction: discord.Interaction,
        *,
        embed: discord.Embed,
        view: BgIntelDetailsView,
        files: list[discord.File] | None = None,
    ) -> None:
        channel = getattr(interaction, "channel", None)
        sentMessage = None
        if channel is not None:
            payload = {
                "embed": embed,
                "view": view,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if files:
                payload["files"] = files
            sentMessage = await interactionRuntime.safeChannelSend(channel, **payload)
        if sentMessage is not None:
            await self._finishBgIntelStatus(
                interaction,
                "Background-check overview posted.",
            )
            return

        await self._finishBgIntelStatus(
            interaction,
            "I couldn't post the background-check overview in this channel.",
        )

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    def _canUse(self, member: discord.Member) -> bool:
        extraReviewerRoleIds: set[int] = set()
        for rawRoleId in list(getattr(config, "bgCheckMinorReviewRoleIds", []) or []):
            try:
                parsedRoleId = int(rawRoleId)
            except (TypeError, ValueError):
                continue
            if parsedRoleId > 0:
                extraReviewerRoleIds.add(parsedRoleId)
        try:
            primaryMinorRoleId = int(getattr(config, "bgCheckMinorReviewRoleId", 0) or 0)
        except (TypeError, ValueError):
            primaryMinorRoleId = 0
        if primaryMinorRoleId > 0:
            extraReviewerRoleIds.add(primaryMinorRoleId)
        return (
            runtimePermissions.hasBgCheckCertifiedRole(member)
            or runtimePermissions.hasAdminOrManageGuild(member)
            or any(int(role.id) in extraReviewerRoleIds for role in list(member.roles or []))
        )

    @staticmethod
    def _parseDiscordId(rawValue: str | None) -> Optional[int]:
        clean = str(rawValue or "").strip()
        if not clean:
            return None
        if clean.startswith("<@") and clean.endswith(">"):
            clean = clean[2:-1].lstrip("!")
        if not clean.isdigit():
            return None
        parsed = int(clean)
        return parsed if parsed > 0 else None

    async def _fetchGuildMemberById(self, guild: discord.Guild, discordUserId: int) -> discord.Member | None:
        member = guild.get_member(int(discordUserId))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(int(discordUserId))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _mainGuildId(self) -> int:
        try:
            mainGuildId = int(getattr(config, "serverId", 0) or 0)
        except (TypeError, ValueError):
            return 0
        return mainGuildId if mainGuildId > 0 else 0

    def _mainGuild(self) -> discord.Guild | None:
        mainGuildId = self._mainGuildId()
        if mainGuildId <= 0:
            return None
        return self.bot.get_guild(mainGuildId)

    async def _fetchMainGuildMemberById(
        self,
        discordUserId: int,
        *,
        currentGuild: discord.Guild,
    ) -> discord.Member | None:
        mainGuildId = self._mainGuildId()
        if mainGuildId <= 0 or int(mainGuildId) == int(currentGuild.id):
            return None
        mainGuild = self._mainGuild()
        if mainGuild is None:
            try:
                mainGuild = await self.bot.fetch_guild(mainGuildId)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        return await self._fetchGuildMemberById(mainGuild, int(discordUserId))

    async def _resolveAltLinkEndpoint(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member | None,
        discordId: str | None,
        robloxUsername: str | None,
    ) -> tuple[int, int | None, str, str | None]:
        cleanDiscordId = str(discordId or "").strip()
        parsedDiscordId = self._parseDiscordId(cleanDiscordId)
        if cleanDiscordId and parsedDiscordId is None:
            return 0, None, "", "Please provide a valid Discord user ID or mention."
        if member is not None and parsedDiscordId is not None:
            return 0, None, "", "Please provide either a Discord member or Discord ID for one side, not both."

        resolvedDiscordId = int(getattr(member, "id", 0) or parsedDiscordId or 0)
        resolvedRobloxId: int | None = None
        resolvedRobloxUsername = str(robloxUsername or "").strip()

        if resolvedRobloxUsername:
            lookup = await robloxUsers.fetchRobloxUserByUsername(resolvedRobloxUsername)
            if lookup.robloxId:
                resolvedRobloxId = int(lookup.robloxId)
            if lookup.robloxUsername:
                resolvedRobloxUsername = str(lookup.robloxUsername)
        elif resolvedDiscordId > 0:
            lookup = await robloxUsers.fetchRobloxUser(resolvedDiscordId, int(guild.id))
            if lookup.robloxId:
                resolvedRobloxId = int(lookup.robloxId)
            if lookup.robloxUsername:
                resolvedRobloxUsername = str(lookup.robloxUsername)

        if resolvedDiscordId <= 0 and not resolvedRobloxUsername and not resolvedRobloxId:
            return 0, None, "", "Each side needs a Discord identity or Roblox username."
        return resolvedDiscordId, resolvedRobloxId, resolvedRobloxUsername, None

    @app_commands.command(
        name="bg-alt-link",
        description="Record a confirmed, related, or cleared alt relationship for BG intelligence.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        status="How Jane should treat the relationship.",
        source_member="First Discord member, if known.",
        source_discord_id="First Discord user ID or mention, if not a member.",
        source_roblox_username="First Roblox username, if known.",
        target_member="Second Discord member, if known.",
        target_discord_id="Second Discord user ID or mention, if not a member.",
        target_roblox_username="Second Roblox username, if known.",
        note="Optional reviewer note.",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Confirmed alt", value="CONFIRMED"),
            app_commands.Choice(name="Related / allowed", value="RELATED"),
            app_commands.Choice(name="Cleared / not an alt", value="CLEARED"),
        ]
    )
    async def bgAltLink(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str],
        source_member: discord.Member | None = None,
        source_discord_id: str | None = None,
        source_roblox_username: str | None = None,
        target_member: discord.Member | None = None,
        target_discord_id: str | None = None,
        target_roblox_username: str | None = None,
        note: str | None = None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used inside a server.")
        if not self._canUse(interaction.user):
            return await self._safeEphemeral(interaction, "You do not have permission to record BG alt links.")

        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        sourceDiscordId, sourceRobloxId, sourceRobloxName, sourceError = await self._resolveAltLinkEndpoint(
            guild=interaction.guild,
            member=source_member,
            discordId=source_discord_id,
            robloxUsername=source_roblox_username,
        )
        if sourceError:
            return await self._finishBgIntelStatus(interaction, f"Source identity error: {sourceError}")
        targetDiscordId, targetRobloxId, targetRobloxName, targetError = await self._resolveAltLinkEndpoint(
            guild=interaction.guild,
            member=target_member,
            discordId=target_discord_id,
            robloxUsername=target_roblox_username,
        )
        if targetError:
            return await self._finishBgIntelStatus(interaction, f"Target identity error: {targetError}")
        if (
            sourceDiscordId > 0
            and sourceDiscordId == targetDiscordId
            and (sourceRobloxId or 0) == (targetRobloxId or 0)
            and (sourceRobloxName or "").lower() == (targetRobloxName or "").lower()
        ):
            return await self._finishBgIntelStatus(interaction, "Those identities are the same endpoint.")

        linkId = await service.recordAltLink(
            guildId=int(interaction.guild.id),
            createdBy=int(interaction.user.id),
            status=str(status.value),
            sourceDiscordUserId=sourceDiscordId,
            sourceRobloxUserId=sourceRobloxId,
            sourceRobloxUsername=sourceRobloxName,
            targetDiscordUserId=targetDiscordId,
            targetRobloxUserId=targetRobloxId,
            targetRobloxUsername=targetRobloxName,
            note=str(note or "").strip(),
        )
        statusLabel = {
            "CONFIRMED": "confirmed alt",
            "RELATED": "related / allowed",
            "CLEARED": "cleared / not an alt",
        }.get(str(status.value), str(status.value))
        await self._finishBgIntelStatus(
            interaction,
            f"Recorded BG alt link #{int(linkId)} as **{statusLabel}**. Future `/bg-intel` scans will use it.",
        )

    @app_commands.command(
        name="bg-intel",
        description="Run Jane's standalone Roblox background intelligence report.",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        member="The Discord member to analyze. Optional if a Discord ID or Roblox username is supplied.",
        discord_id="Optional Discord user ID. Paste as plain ID or mention.",
        roblox_username="Optional Roblox username. Can be used without a Discord member.",
        notify_private_inventory="DM the user if their inventory is private or hidden. Defaults to off.",
        text_report="Attach the full text report dump. Defaults to no.",
    )
    async def bgIntel(
        self,
        interaction: discord.Interaction,
        member: discord.Member | None = None,
        discord_id: str | None = None,
        roblox_username: str | None = None,
        notify_private_inventory: bool = False,
        text_report: bool = False,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used inside a server.")
        if not self._canUse(interaction.user):
            return await self._safeEphemeral(interaction, "You do not have permission to run BG intelligence scans.")
        cleanRobloxUsername = str(roblox_username or "").strip()
        cleanDiscordId = str(discord_id or "").strip()
        parsedDiscordId = self._parseDiscordId(cleanDiscordId)
        if cleanDiscordId and parsedDiscordId is None:
            return await self._safeEphemeral(interaction, "Please provide a valid Discord user ID or mention.")
        if member is not None and parsedDiscordId is not None:
            return await self._safeEphemeral(interaction, "Please provide either a Discord member or Discord ID, not both.")
        if member is None and parsedDiscordId is None and not cleanRobloxUsername:
            return await self._safeEphemeral(
                interaction,
                "Please provide a Discord member, a Discord ID, or a Roblox username.",
            )

        await interactionRuntime.safeInteractionDefer(
            interaction,
            ephemeral=True,
            thinking=True,
        )

        targetLabel = str(
            getattr(member, "display_name", None)
            or cleanRobloxUsername
            or parsedDiscordId
            or "selected user"
        )
        progressUpdater: ProgressUpdater = lambda status: self._editBgIntelStatus(
            interaction,
            status,
            targetLabel=targetLabel,
        )
        await progressUpdater("Checking Discord membership and main-server lookup...")

        targetMember = member
        if targetMember is None and parsedDiscordId is not None:
            targetMember = await self._fetchGuildMemberById(interaction.guild, parsedDiscordId)
        targetDiscordId = int(getattr(targetMember, "id", 0) or parsedDiscordId or 0)
        mainGuildMember = (
            await self._fetchMainGuildMemberById(targetDiscordId, currentGuild=interaction.guild)
            if targetDiscordId > 0
            else None
        )
        scanMember = targetMember or mainGuildMember
        roverMember = mainGuildMember or targetMember
        roverGuildId = int(getattr(getattr(roverMember, "guild", None), "id", 0) or 0) if roverMember is not None else None
        if member is not None and member.bot:
            return await self._finishBgIntelStatus(
                interaction,
                "That is a bot account. Jane is not emotionally prepared to background-check the appliances.",
            )
        if targetMember is not None and targetMember.bot:
            return await self._finishBgIntelStatus(
                interaction,
                "That is a bot account. Jane is not emotionally prepared to background-check the appliances.",
            )
        if mainGuildMember is not None and mainGuildMember.bot:
            return await self._finishBgIntelStatus(
                interaction,
                "That is a bot account. Jane is not emotionally prepared to background-check the appliances.",
            )

        try:
            if scanMember is not None:
                report = await service.buildReport(
                    scanMember,
                    guild=interaction.guild,
                    reviewBucketOverride="adult",
                    roverGuildId=roverGuildId,
                    robloxUsernameOverride=cleanRobloxUsername or None,
                    notifyPrivateInventory=bool(notify_private_inventory),
                    reviewer=interaction.user,
                    configModule=config,
                    progressCallback=progressUpdater,
                )
            elif parsedDiscordId is not None:
                report = await service.buildReportForDiscordId(
                    guild=interaction.guild,
                    discordUserId=parsedDiscordId,
                    displayMember=mainGuildMember,
                    roverGuildId=roverGuildId,
                    robloxUsernameOverride=cleanRobloxUsername or None,
                    reviewBucketOverride="adult",
                    configModule=config,
                    progressCallback=progressUpdater,
                )
            else:
                report = await service.buildReportForRobloxIdentity(
                    guild=interaction.guild,
                    robloxUsername=cleanRobloxUsername or None,
                    reviewBucketOverride="adult",
                    configModule=config,
                    progressCallback=progressUpdater,
                )
            await progressUpdater("Scoring the completed scan...")
            riskScore = scoring.scoreReport(report, configModule=config)
            try:
                await progressUpdater("Saving the audit record...")
                channelId = int(getattr(getattr(interaction, "channel", None), "id", 0) or 0)
                reportId = await service.recordReport(
                    guildId=int(interaction.guild.id),
                    channelId=channelId,
                    reviewerId=int(interaction.user.id),
                    report=report,
                    riskScore=riskScore,
                )
            except Exception:
                reportId = 0
                log.exception(
                    "BG intelligence audit insert failed for guild=%s target=%s.",
                    int(interaction.guild.id),
                    int(report.discordUserId or 0),
                )
        except Exception:
            log.exception(
                "BG intelligence scan failed for guild=%s target=%s.",
                int(interaction.guild.id),
                int(scanMember.id) if scanMember is not None else int(parsedDiscordId or 0),
            )
            return await self._finishBgIntelStatus(
                interaction,
                "BG intelligence scan failed internally. Check Jane's logs before trusting the result.",
            )

        await progressUpdater("Rendering the overview...")
        view = BgIntelDetailsView(
            ownerId=int(interaction.user.id),
            report=report,
            riskScore=riskScore,
            reportId=reportId,
            includeTextReport=bool(text_report),
            roverGuildId=roverGuildId,
            robloxUsernameOverride=cleanRobloxUsername or None,
            notifyPrivateInventory=bool(notify_private_inventory),
        )
        embed, files = view._buildPublicPayload("overview")
        await progressUpdater("Posting the overview...")
        await self._sendBgIntelMessage(interaction, embed=embed, view=view, files=files)


async def setup(bot: commands.Bot):
    await bot.add_cog(BgIntelligenceCog(bot))
