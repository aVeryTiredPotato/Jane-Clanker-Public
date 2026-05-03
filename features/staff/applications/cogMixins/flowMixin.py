from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

import aiohttp
import discord
from discord.ext import commands

import config
from features.staff.applications import divisionConfigService
from features.staff.applications import hubRefresh as applicationsHubRefresh
from features.staff.applications.interactiveViews import (
    ApplicantAnswerModal,
    DivisionApplyModal,
    DivisionHubView,
    DivisionMultipleChoiceView,
    DivisionReviewView,
    NeedsInfoModal,
)
from features.staff.applications import rendering as applicationsRendering
from features.staff.applications import reviewFlow as applicationsReviewFlow
from features.staff.applications import service as applicationsService
from features.staff.applications import submissionFlow as applicationsSubmissionFlow
from features.staff.applications import workflowBridge as applicationsWorkflowBridge
from features.staff.applications.divisionEditor import (
    ApplicationsDivisionEditorView,
    _parseBoolText,
    _parseSnowflake,
)
from features.staff.applications.panel import ApplicationsPanelView
from runtime import interaction as interactionRuntime
from runtime import taskBudgeter
from features.staff.applications.questionEditor import (
    ApplicationsDivisionChoiceQuestionModal,
    ApplicationsDivisionLinkQuestionModal,
    ApplicationsDivisionQuestionsView,
    ApplicationsDivisionTextQuestionModal,
    _normalizeQuestionStyle,
    _questionStyleLabel,
)
from features.staff.departmentOrbat import sheets as departmentOrbatSheets
from features.staff.recruitment import sheets as recruitmentSheets
from runtime import interaction as interactionRuntime
from runtime import orbatAudit as orbatAuditRuntime
from runtime import taskBudgeter
from features.staff.sessions.Roblox import robloxGroups, robloxUsers

from features.staff.applications.cogShared import _isFinal, _normalizeAppKey, _parseDays, _toIntList, _toPositiveInt

log = logging.getLogger(__name__)


class ApplicationsFlowMixin:
    async def collectProofMessage(self, channel: discord.abc.GuildChannel, userId: int, timeoutSec: int = 180) -> Optional[discord.Message]:
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None

        def check(message: discord.Message) -> bool:
            return message.author.id == userId and message.channel.id == channel.id and len(message.attachments) > 0

        try:
            return await self.bot.wait_for("message", check=check, timeout=max(10, timeoutSec))
        except asyncio.TimeoutError:
            return None

    async def handleApplicantAnswerButton(
        self,
        interaction: discord.Interaction,
        applicationId: int,
        applicantId: int,
    ) -> None:
        await applicationsReviewFlow.handleApplicantAnswerButton(
            cog=self,
            interaction=interaction,
            applicationId=int(applicationId),
            applicantId=int(applicantId),
        )

    async def handleApplicantAnswerSubmit(
        self,
        interaction: discord.Interaction,
        applicationId: int,
        promptTitle: str,
        answer: str,
    ) -> None:
        await applicationsReviewFlow.handleApplicantAnswerSubmit(
            cog=self,
            interaction=interaction,
            applicationId=int(applicationId),
            promptTitle=promptTitle,
            answer=answer,
        )

    async def createHubThread(self, message: discord.Message, divisionName: str) -> None:
        if not isinstance(message.channel, discord.TextChannel):
            return
        threadName = f"{divisionName} applications"
        try:
            thread = await taskBudgeter.runDiscord(
                lambda: message.create_thread(name=threadName[:100], auto_archive_duration=10080)
            )
        except (discord.Forbidden, discord.HTTPException):
            return
        await interactionRuntime.safeChannelSend(thread, content="Applications thread created automatically for this division hub.")

    async def refreshHubViewsForDivision(self, guild: discord.Guild, divisionKey: str) -> int:
        return await applicationsHubRefresh.refreshHubViewsForDivision(
            cog=self,
            guild=guild,
            divisionKey=divisionKey,
            buildView=lambda key, isOpen: DivisionHubView(self, key, isOpen=isOpen),
        )

    async def _fetchWebhookAvatarBytes(self, avatarUrl: str) -> Optional[bytes]:
        normalizedUrl = str(avatarUrl or "").strip()
        if not normalizedUrl:
            return None

        timeoutSec = float(getattr(config, "applicationsHubAvatarFetchTimeoutSec", 8.0) or 8.0)
        maxBytes = int(getattr(config, "applicationsHubAvatarMaxBytes", 2_000_000) or 2_000_000)
        if maxBytes < 64 * 1024:
            maxBytes = 64 * 1024

        try:
            timeout = aiohttp.ClientTimeout(total=max(2.0, timeoutSec))
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(normalizedUrl) as response:
                    if int(response.status) < 200 or int(response.status) >= 300:
                        return None
                    contentType = str(response.headers.get("Content-Type") or "").lower()
                    if contentType and "image" not in contentType:
                        return None
                    data = await response.read()
        except Exception:
            return None

        if not data:
            return None
        if len(data) > maxBytes:
            return None
        return data

    async def sendHubMessage(
        self,
        targetChannel: discord.abc.Messageable,
        division: dict[str, Any],
        view: discord.ui.View,
    ) -> Optional[discord.Message]:
        embed = applicationsRendering.buildApplicationHubEmbed(division)
        customName = str(division.get("hubMessageName") or "").strip()
        customAvatarUrl = str(division.get("hubMessageAvatarUrl") or "").strip()
        if not customName and not customAvatarUrl:
            return await interactionRuntime.safeChannelSend(targetChannel, embed=embed, view=view)

        if not self.bot.user:
            return await interactionRuntime.safeChannelSend(targetChannel, embed=embed, view=view)

        threadTarget: Optional[discord.Thread] = None
        webhookHostChannel: Optional[discord.TextChannel] = None
        if isinstance(targetChannel, discord.Thread):
            threadTarget = targetChannel
            if isinstance(targetChannel.parent, discord.TextChannel):
                webhookHostChannel = targetChannel.parent
        elif isinstance(targetChannel, discord.TextChannel):
            webhookHostChannel = targetChannel

        if webhookHostChannel is None:
            return await interactionRuntime.safeChannelSend(targetChannel, embed=embed, view=view)

        me = webhookHostChannel.guild.me
        if me is None or not webhookHostChannel.permissions_for(me).manage_webhooks:
            return await interactionRuntime.safeChannelSend(targetChannel, embed=embed, view=view)

        try:
            webhooks = await taskBudgeter.runDiscord(lambda: webhookHostChannel.webhooks())
            divisionKey = str(division.get("key") or "").strip().lower() or "default"
            webhookName = f"jane-division-hub-{divisionKey}"[:80]
            webhook = next(
                (
                    hook
                    for hook in webhooks
                    if hook.user and hook.user.id == self.bot.user.id and hook.name == webhookName
                ),
                None,
            )
            if webhook is None:
                webhook = await taskBudgeter.runDiscord(
                    lambda: webhookHostChannel.create_webhook(
                        name=webhookName,
                        reason="Division-specific hub branding",
                    )
                )

            desiredDisplayName = str(customName or division.get("displayName") or "Jane Clanker").strip()[:80] or "Jane Clanker"
            avatarBytes = await self._fetchWebhookAvatarBytes(customAvatarUrl) if customAvatarUrl else None
            try:
                if avatarBytes is not None:
                    await taskBudgeter.runDiscord(
                        lambda: webhook.edit(
                            name=desiredDisplayName,
                            avatar=avatarBytes,
                            reason="Division-specific hub branding",
                        )
                    )
                elif str(webhook.name or "") != desiredDisplayName:
                    await taskBudgeter.runDiscord(
                        lambda: webhook.edit(
                            name=desiredDisplayName,
                            reason="Division-specific hub branding",
                        )
                    )
            except (discord.Forbidden, discord.HTTPException):
                pass

            kwargs: dict[str, Any] = {
                "embed": embed,
                "view": view,
                "wait": True,
                "allowed_mentions": discord.AllowedMentions.none(),
            }
            if threadTarget is not None:
                kwargs["thread"] = threadTarget

            sent = await taskBudgeter.runDiscord(lambda: webhook.send(**kwargs))
            if isinstance(sent, discord.Message):
                return sent
            return None
        except Exception:
            return await interactionRuntime.safeChannelSend(targetChannel, embed=embed, view=view)

    async def refreshReviewCard(self, applicationId: int) -> None:
        await applicationsReviewFlow.refreshReviewCard(
            cog=self,
            applicationId=int(applicationId),
            buildReviewView=lambda appId, status, applicantId: self.buildDivisionReviewView(appId, status, applicantId),
        )

    def buildApplicantAnswerModal(self, applicationId: int, promptTitle: str) -> ApplicantAnswerModal:
        return ApplicantAnswerModal(self, applicationId, promptTitle)

    async def notifyApplicant(self, applicantId: int, message: str) -> None:
        user = self.bot.get_user(applicantId)
        if user is None:
            try:
                user = await taskBudgeter.runDiscord(lambda: self.bot.fetch_user(applicantId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        try:
            await taskBudgeter.runDiscord(lambda: user.send(message))
        except (discord.Forbidden, discord.HTTPException):
            return

    async def grantRoles(self, guild: discord.Guild, applicantId: int, roleIds: list[int], reviewerId: int) -> tuple[int, list[str]]:
        if not roleIds:
            return 0, []
        member = guild.get_member(applicantId)
        if member is None:
            try:
                member = await taskBudgeter.runDiscord(lambda: guild.fetch_member(applicantId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return 0, []

        addedNames: list[str] = []
        for roleId in roleIds:
            role = guild.get_role(roleId)
            if role is None or role in member.roles:
                continue
            try:
                await taskBudgeter.runDiscord(
                    lambda role=role: member.add_roles(role, reason=f"Division app approved by {reviewerId}")
                )
                addedNames.append(role.name)
            except (discord.Forbidden, discord.HTTPException):
                continue
        return len(addedNames), addedNames

    async def sendOrbatKickoff(self, guild: discord.Guild, application: dict[str, Any], division: dict[str, Any]) -> None:
        channelId = int(getattr(config, "orbatReviewChannelId", 0) or 0)
        if channelId <= 0:
            return
        channel = guild.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return

        appCode = str(application.get("appCode") or f"APP-{application.get('applicationId')}")
        embed = discord.Embed(
            title="Application Approved - ORBAT Follow-up",
            description=f"Applicant: <@{application['applicantId']}>\nDivision: {division['displayName']}\nApplication: `{appCode}`",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        await interactionRuntime.safeChannelSend(channel, embed=embed)

    async def autoAcceptDivisionGroup(
        self,
        application: dict[str, Any],
        division: dict[str, Any],
    ) -> str:
        groupId = int(division.get("autoAcceptGroupId") or 0)
        if groupId <= 0:
            return ""

        lookup = await robloxUsers.fetchRobloxUser(int(application["applicantId"]))
        if not lookup.robloxId:
            return " Group auto-accept skipped (no RoVer link)."

        accept = await robloxGroups.acceptJoinRequestForGroup(int(lookup.robloxId), groupId)
        if accept.ok:
            return f" Group auto-accept succeeded for group `{groupId}`."

        lowerError = (accept.error or "").lower()
        noRequestMarkers = ("join request", "not found", "404", "no pending")
        if accept.status == 404 or any(marker in lowerError for marker in noRequestMarkers):
            return f" Group auto-accept skipped for group `{groupId}` (no pending join request)."
        return f" Group auto-accept failed for group `{groupId}`."

    async def syncRecruitmentOrbatOnApprove(
        self,
        application: dict[str, Any],
        division: dict[str, Any],
        reviewerId: Optional[int] = None,
    ) -> str:
        divisionKey = str(division.get("key") or "").strip()
        normalizedDivision = _normalizeAppKey(divisionKey)
        rawSeedKeyMap = getattr(config, "divisionOrbatSeedKeyMap", {}) or {}
        seedKeyMap: dict[str, str] = {}
        if isinstance(rawSeedKeyMap, dict):
            for rawKey, rawTarget in rawSeedKeyMap.items():
                keyNorm = _normalizeAppKey(rawKey)
                targetKey = str(rawTarget or "").strip()
                if keyNorm and targetKey:
                    seedKeyMap[keyNorm] = targetKey
        targetDivisionKey = str(seedKeyMap.get(normalizedDivision) or divisionKey).strip() or divisionKey
        targetDivisionNormalized = _normalizeAppKey(targetDivisionKey)
        rawStartRankMap = getattr(config, "divisionOrbatSeedStartRankMap", {}) or {}
        startRankMap: dict[str, str] = {}
        if isinstance(rawStartRankMap, dict):
            for rawKey, rawRank in rawStartRankMap.items():
                keyNorm = _normalizeAppKey(rawKey)
                rankValue = str(rawRank or "").strip()
                if keyNorm and rankValue:
                    startRankMap[keyNorm] = rankValue
        initialRank = str(
            startRankMap.get(targetDivisionNormalized)
            or startRankMap.get(normalizedDivision)
            or ""
        ).strip()
        recruitmentAliases = {
            _normalizeAppKey(value)
            for value in (getattr(config, "recruitmentDivisionKeyAliases", []) or [])
        }
        if not recruitmentAliases:
            recruitmentAliases = {"recruitment"}

        lookup = await robloxUsers.fetchRobloxUser(int(application["applicantId"]))
        robloxUsername = str(lookup.robloxUsername or "").strip()
        if not robloxUsername:
            return " ORBAT seed skipped (no RoVer link)."

        # Recruitment apps seed the recruitment tracker.
        if normalizedDivision in recruitmentAliases:
            if not getattr(config, "recruitmentSpreadsheetId", ""):
                return " Recruitment ORBAT seed skipped (sheet not configured)."
            try:
                seeded = await taskBudgeter.runSheetsThread(
                    recruitmentSheets.applyApprovedLog,
                    robloxUsername,
                    0,
                    0,
                    True,
                )
            except Exception:
                log.exception(
                    "Recruitment ORBAT seed failed for division app %s (%s).",
                    application.get("appCode"),
                    robloxUsername,
                )
                return " Recruitment ORBAT seed failed."
            if seeded:
                try:
                    await orbatAuditRuntime.sendOrbatChangeLog(
                        self.bot,
                        change="Added or updated user in Recruitment ORBAT from an approved application.",
                        authorizedBy=f"<@{int(reviewerId)}>" if reviewerId else "application approval",
                        details=(
                            f"Applicant: <@{int(application.get('applicantId') or 0)}> | "
                            f"Division: {divisionKey or 'Unknown'}"
                        ),
                        sheetKey="recruitment",
                    )
                except Exception:
                    log.exception("Failed to post Recruitment ORBAT seed audit log.")
                return " Recruitment ORBAT seed completed."
            return " Recruitment ORBAT seed skipped (unable to upsert row)."

        # All other divisions seed their mapped department ORBAT layout.
        if not bool(getattr(config, "nonRecruitmentOrbatWritesEnabled", False)):
            return " Department ORBAT seed skipped (non-recruitment ORBAT writes are disabled)."

        try:
            result = await taskBudgeter.runSheetsThread(
                departmentOrbatSheets.upsertDivisionMemberByRobloxUsername,
                targetDivisionKey,
                robloxUsername,
                initialRank or None,
            )
        except Exception:
            log.exception(
                "Department ORBAT seed failed for division app %s (%s -> %s).",
                application.get("appCode"),
                targetDivisionKey,
                robloxUsername,
            )
            return " Department ORBAT seed failed."

        if result.get("ok"):
            try:
                await orbatAuditRuntime.sendOrbatChangeLog(
                    self.bot,
                    change="Added or updated user in Department ORBAT from an approved application.",
                    authorizedBy=f"<@{int(reviewerId)}>" if reviewerId else "application approval",
                    details=(
                        f"Applicant: <@{int(application.get('applicantId') or 0)}> | "
                        f"Division: {targetDivisionKey}"
                    ),
                    sheetKey=str(result.get("sheetKey") or "") or None,
                    divisionKey=targetDivisionKey,
                )
            except Exception:
                log.exception("Failed to post Department ORBAT seed audit log.")
            return f" Department ORBAT seed completed ({targetDivisionKey})."

        reason = str(result.get("reason") or "unknown")
        if reason == "layout-not-configured":
            return f" Department ORBAT seed skipped ({targetDivisionKey} layout not configured yet)."
        return f" Department ORBAT seed skipped ({reason})."

    async def _submissionGateMessage(
        self,
        guildId: int,
        divisionKey: str,
        userId: int,
    ) -> Optional[str]:
        return await applicationsSubmissionFlow.submissionGateMessage(
            guildId=int(guildId),
            divisionKey=str(divisionKey or ""),
            userId=int(userId),
        )

    async def _resolveSubmissionReviewChannel(
        self,
        interaction: discord.Interaction,
        division: dict[str, Any],
        *,
        preferCurrentThread: bool = False,
    ) -> Optional[discord.abc.Messageable]:
        return await applicationsSubmissionFlow.resolveSubmissionReviewChannel(
            cog=self,
            interaction=interaction,
            division=division,
            preferCurrentThread=bool(preferCurrentThread),
        )

    def _splitDivisionApplyQuestions(
        self,
        division: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return applicationsSubmissionFlow.splitDivisionApplyQuestions(division=division)

    def buildDivisionMultipleChoiceView(
        self,
        *,
        divisionKey: str,
        applicantId: int,
        answersSoFar: dict[str, str],
        choiceQuestions: list[dict[str, Any]],
        remainingQuestions: list[dict[str, Any]] | None = None,
    ) -> DivisionMultipleChoiceView:
        return DivisionMultipleChoiceView(
            self,
            divisionKey,
            int(applicantId),
            answersSoFar=answersSoFar,
            choiceQuestions=choiceQuestions,
            remainingQuestions=remainingQuestions or [],
        )

    def buildDivisionApplyModal(
        self,
        *,
        division: dict[str, Any],
        textQuestions: list[dict[str, Any]],
        remainingQuestions: list[dict[str, Any]] | None = None,
        answersSoFar: dict[str, str] | None = None,
    ) -> DivisionApplyModal:
        return DivisionApplyModal(
            self,
            division,
            textQuestions,
            remainingQuestions=remainingQuestions or [],
            answersSoFar=answersSoFar or {},
        )

    def buildDivisionReviewView(
        self,
        applicationId: int,
        status: str,
        applicantId: int,
    ) -> DivisionReviewView:
        return DivisionReviewView(self, applicationId, status, applicantId)

    async def openMultipleChoiceStep(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        textAnswers: dict[str, str],
        choiceQuestions: list[dict[str, Any]],
        remainingQuestions: list[dict[str, Any]] | None = None,
    ) -> None:
        await applicationsSubmissionFlow.openMultipleChoiceStep(
            cog=self,
            interaction=interaction,
            divisionKey=divisionKey,
            textAnswers=textAnswers,
            choiceQuestions=choiceQuestions,
            remainingQuestions=remainingQuestions or [],
        )

    async def openApplicationQuestionStep(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        answers: dict[str, str],
        remainingQuestions: list[dict[str, Any]],
    ) -> None:
        await applicationsSubmissionFlow.openApplicationQuestionStep(
            cog=self,
            interaction=interaction,
            divisionKey=divisionKey,
            answers=answers,
            remainingQuestions=remainingQuestions,
        )

    async def handleApply(self, interaction: discord.Interaction, divisionKey: str) -> None:
        await applicationsSubmissionFlow.handleApply(
            cog=self,
            interaction=interaction,
            divisionKey=divisionKey,
        )

    async def handleModalSubmit(self, interaction: discord.Interaction, divisionKey: str, answers: dict[str, str]) -> None:
        await applicationsSubmissionFlow.handleModalSubmit(
            cog=self,
            interaction=interaction,
            divisionKey=divisionKey,
            answers=answers,
        )

    async def handleReviewDecision(
        self,
        interaction: discord.Interaction,
        applicationId: int,
        status: str,
        note: Optional[str],
    ) -> None:
        await applicationsReviewFlow.handleReviewDecision(
            cog=self,
            interaction=interaction,
            applicationId=int(applicationId),
            status=status,
            note=note,
        )

    async def _deleteHubArtifacts(
        self,
        guild: discord.Guild,
        hubRow: dict[str, Any],
    ) -> tuple[bool, bool, bool]:
        channelId = int(hubRow.get("channelId") or 0)
        messageId = int(hubRow.get("messageId") or 0)
        messageDeletedOrMissing = False
        threadDeleted = False
        deleteFailed = False

        channel = await self._resolveGuildChannel(guild, channelId)
        if channel is None and messageId > 0:
            messageDeletedOrMissing = True
        if isinstance(channel, (discord.TextChannel, discord.Thread)) and messageId > 0:
            try:
                message = await taskBudgeter.runDiscord(lambda: channel.fetch_message(messageId))
            except discord.NotFound:
                messageDeletedOrMissing = True
            except (discord.Forbidden, discord.HTTPException):
                deleteFailed = True
            else:
                deleted = await interactionRuntime.safeMessageDelete(message)
                if deleted:
                    messageDeletedOrMissing = True
                else:
                    deleteFailed = True

        threadChannel = await self._resolveGuildChannel(guild, messageId)
        if isinstance(threadChannel, discord.Thread):
            try:
                await taskBudgeter.runDiscord(
                    lambda: threadChannel.delete(reason="Applications bulk close by server administrator.")
                )
                threadDeleted = True
            except (discord.Forbidden, discord.HTTPException):
                pass

        if messageDeletedOrMissing and messageId > 0:
            await applicationsService.deleteHubMessage(messageId)
        return messageDeletedOrMissing, threadDeleted, deleteFailed

    async def _deleteApplicationReviewMessage(
        self,
        guild: discord.Guild,
        application: dict[str, Any],
    ) -> tuple[bool, bool]:
        reviewChannelId = int(application.get("reviewChannelId") or 0)
        reviewMessageId = int(application.get("reviewMessageId") or 0)
        if reviewChannelId <= 0 or reviewMessageId <= 0:
            return False, False
        channel = await self._resolveGuildChannel(guild, reviewChannelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return False, True
        try:
            message = await taskBudgeter.runDiscord(lambda: channel.fetch_message(reviewMessageId))
        except discord.NotFound:
            return False, False
        except (discord.Forbidden, discord.HTTPException):
            return False, True
        if await interactionRuntime.safeMessageDelete(message):
            return True, False
        return False, True

    async def runApplicationsCloseAllActive(
        self,
        interaction: discord.Interaction,
        *,
        confirmation: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.isServerAdministrator(interaction.user):
            return await self.safeReply(interaction, "Only server administrators can run bulk close.")

        normalized = "".join(ch for ch in str(confirmation or "").upper() if ch.isalnum())
        if normalized != "CLOSEALL":
            return await self.safeReply(interaction, "Confirmation failed. Type exactly `CLOSE ALL`.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        closedDivisions = 0
        for divisionKey in self.divisionOrder:
            await applicationsService.setDivisionOpen(guild.id, divisionKey, False)
            closedDivisions += 1

        hubRows = await applicationsService.listHubMessagesForGuild(guild.id)
        hubMessagesCleared = 0
        hubThreadsDeleted = 0
        hubDeleteFailures = 0
        for hubRow in hubRows:
            messageCleared, threadDeleted, deleteFailed = await self._deleteHubArtifacts(guild, hubRow)
            if messageCleared:
                hubMessagesCleared += 1
            if threadDeleted:
                hubThreadsDeleted += 1
            if deleteFailed:
                hubDeleteFailures += 1

        activeRows = await applicationsService.listActiveApplicationsForGuild(guild.id)
        requestsClosed = 0
        reviewMessagesDeleted = 0
        reviewCardsDisabled = 0
        reviewDeleteFailures = 0
        for application in activeRows:
            applicationId = int(application.get("applicationId") or 0)
            if applicationId <= 0:
                continue
            reviewDeleted, reviewDeleteFailed = await self._deleteApplicationReviewMessage(guild, application)
            if reviewDeleted:
                reviewMessagesDeleted += 1
            if reviewDeleteFailed:
                reviewDeleteFailures += 1

            await applicationsService.setApplicationStatus(
                applicationId,
                "DENIED",
                reviewerId=interaction.user.id,
                reviewNote="Bulk closed by server administrator.",
            )
            await applicationsService.addApplicationEvent(
                applicationId,
                interaction.user.id,
                "BULK_CLOSED",
                "Bulk closed by server administrator.",
            )
            refreshedApplication = await applicationsService.getApplicationById(applicationId)
            if refreshedApplication is not None:
                await applicationsWorkflowBridge.syncApplicationWorkflow(
                    refreshedApplication,
                    stateKey="denied",
                    actorId=int(interaction.user.id),
                    note="Bulk closed by server administrator.",
                    eventType="BULK_CLOSED",
                )
            if not reviewDeleted:
                await self.refreshReviewCard(applicationId)
                reviewCardsDisabled += 1
            await applicationsService.setApplicationReviewMessage(applicationId, 0, 0)
            requestsClosed += 1

        hubViewsUpdated = 0
        for divisionKey in self.divisionOrder:
            hubViewsUpdated += await self.refreshHubViewsForDivision(guild, divisionKey)

        await interaction.followup.send(
            (
                "Applications bulk-close complete.\n"
                f"- Divisions closed: {closedDivisions}\n"
                f"- Hub messages cleared: {hubMessagesCleared}\n"
                f"- Hub threads deleted: {hubThreadsDeleted}\n"
                f"- Active requests closed: {requestsClosed}\n"
                f"- Review messages deleted: {reviewMessagesDeleted}\n"
                f"- Review cards disabled: {reviewCardsDisabled}\n"
                f"- Hub delete failures: {hubDeleteFailures}\n"
                f"- Review delete failures: {reviewDeleteFailures}\n"
                f"- Remaining hub views forced closed: {hubViewsUpdated}"
            ),
            ephemeral=True,
        )

