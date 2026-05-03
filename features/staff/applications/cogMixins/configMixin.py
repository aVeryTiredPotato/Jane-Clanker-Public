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
from features.staff.applications import workflowBridge as applicationsWorkflowBridge
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
from features.staff.applications.divisionEditor import (
    ApplicationsDivisionEditorView,
    _parseBoolText,
    _parseSnowflake,
)
from features.staff.applications.panel import ApplicationsPanelView
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

from features.staff.applications.cogShared import _isFinal, _normalizeAppKey, _parseDays, _toIntList, _toPositiveInt

log = logging.getLogger(__name__)


class ApplicationsConfigMixin:
    async def cog_load(self) -> None:
        try:
            hubRows = await applicationsService.listHubMessages()
            reviewRows = await applicationsService.listApplicationsForReviewViews()
            workflowRows = await applicationsService.listApplicationsForWorkflowReconciliation()
        except Exception:
            log.exception("Failed to restore applications persistent views.")
            return

        restoredHub = 0
        restoredReview = 0
        for row in hubRows:
            divisionKey = str(row.get("divisionKey") or "").strip().lower()
            messageId = int(row.get("messageId") or 0)
            if messageId <= 0:
                continue
            isOpen = await applicationsService.isDivisionOpen(int(row.get("guildId") or 0), divisionKey)
            self.bot.add_view(DivisionHubView(self, divisionKey, isOpen=isOpen), message_id=messageId)
            restoredHub += 1

        for row in reviewRows:
            messageId = int(row.get("reviewMessageId") or 0)
            if messageId <= 0:
                continue
            self.bot.add_view(
                DivisionReviewView(
                    self,
                    int(row["applicationId"]),
                    str(row.get("status") or "PENDING"),
                    int(row.get("applicantId") or 0),
                ),
                message_id=messageId,
            )
            restoredReview += 1

        reconciledWorkflow = 0
        reconciledWorkflowChanged = 0
        try:
            reconciledWorkflow, reconciledWorkflowChanged = await applicationsWorkflowBridge.reconcileApplicationWorkflowRows(
                workflowRows
            )
        except Exception:
            log.exception("Failed to reconcile application workflows during cog load.")

        log.info(
            "Applications persistent views restored: hubs=%d, reviews=%d | workflows=%d synced (%d changed)",
            restoredHub,
            restoredReview,
            reconciledWorkflow,
            reconciledWorkflowChanged,
        )

    def loadDivisionConfig(self) -> None:
        rawPath = str(
            getattr(config, "divisionApplicationsConfigPath", "configData/divisions.json")
            or "configData/divisions.json"
        )
        basePath = os.path.dirname(os.path.abspath(getattr(config, "__file__", __file__)))
        path = rawPath if os.path.isabs(rawPath) else os.path.join(basePath, rawPath)
        self.divisionsConfigPath = path
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            log.exception("Failed to load division config: %s", path)
            self.divisions = {}
            self.divisionOrder = []
            return

        divisionsRaw = payload.get("divisions", []) if isinstance(payload, dict) else []
        divisions: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for rawDivision in divisionsRaw:
            if not isinstance(rawDivision, dict):
                continue
            key = str(rawDivision.get("key") or "").strip().lower()
            if not key:
                continue
            questions: list[dict[str, Any]] = []

            def _firstNonEmptyUrl(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
                for keyName in keys:
                    value = str(payload.get(keyName) or "").strip()
                    if value:
                        return value
                return ""

            formUrl = _firstNonEmptyUrl(
                rawDivision,
                ("formUrl", "formLink", "applicationFormUrl", "applicationFormLink", "link"),
            )
            discordServerUrl = _firstNonEmptyUrl(
                rawDivision,
                (
                    "discordServerUrl",
                    "discordInviteUrl",
                    "serverUrl",
                    "serverInviteUrl",
                    "inviteUrl",
                    "invite",
                    "link",
                ),
            )
            for rawQuestion in rawDivision.get("questions") or []:
                if not isinstance(rawQuestion, dict):
                    continue
                label = str(rawQuestion.get("label") or "").strip()
                styleRaw = _normalizeQuestionStyle(rawQuestion.get("style") or "paragraph")
                if styleRaw == "form":
                    if not formUrl:
                        formUrl = _firstNonEmptyUrl(
                            rawQuestion,
                            ("url", "formUrl", "formLink", "applicationFormUrl", "link"),
                        )
                    continue
                if styleRaw == "server-invite":
                    if not discordServerUrl:
                        discordServerUrl = _firstNonEmptyUrl(
                            rawQuestion,
                            (
                                "url",
                                "discordServerUrl",
                                "discordInviteUrl",
                                "serverUrl",
                                "serverInviteUrl",
                                "inviteUrl",
                                "invite",
                                "link",
                            ),
                        )
                    continue
                if not label:
                    continue
                if styleRaw == "multiple-choice":
                    rawChoices = rawQuestion.get("choices")
                    choices: list[str] = []
                    if isinstance(rawChoices, list):
                        for rawChoice in rawChoices:
                            cleanChoice = str(rawChoice or "").strip()
                            if cleanChoice and cleanChoice not in choices:
                                choices.append(cleanChoice)
                    questions.append(
                        {
                            "key": "",
                            "label": label,
                            "required": bool(rawQuestion.get("required", True)),
                            "style": "multiple-choice",
                            "choices": choices[:25],
                        }
                    )
                    continue
                try:
                    maxLength = int(rawQuestion.get("maxLength", 400))
                except (TypeError, ValueError):
                    maxLength = 400
                questions.append(
                    {
                        "key": "",
                        "label": label,
                        "required": bool(rawQuestion.get("required", True)),
                        "style": "paragraph" if styleRaw == "paragraph" else "short",
                        "maxLength": max(1, min(maxLength, 4000)),
                        "placeholder": str(rawQuestion.get("placeholder") or "").strip(),
                    }
                )
            if len(questions) > 5:
                log.warning("Division %s has too many questions; only first 5 are used.", key)
                questions = questions[:5]
            questions = self._assignDivisionQuestionKeys(key, questions)

            eligibility = rawDivision.get("eligibility") if isinstance(rawDivision.get("eligibility"), dict) else {}
            divisions[key] = {
                "key": key,
                "displayName": str(rawDivision.get("displayName") or key).strip(),
                "description": str(rawDivision.get("description") or "Click Apply to submit your application.").strip(),
                "requirements": str(rawDivision.get("requirements") or "").strip(),
                "whatYouDo": str(rawDivision.get("whatYouDo") or "").strip(),
                "bannerUrl": str(rawDivision.get("bannerUrl") or "").strip(),
                "hubMessageName": str(rawDivision.get("hubMessageName") or "").strip(),
                "hubMessageAvatarUrl": str(rawDivision.get("hubMessageAvatarUrl") or "").strip(),
                "grantRoleIds": _toIntList(rawDivision.get("grantRoleIds") or []),
                "reviewerRoleIds": _toIntList(rawDivision.get("reviewerRoleIds") or []),
                "eligibility": {
                    "requiredRoleIds": _toIntList(eligibility.get("requiredRoleIds") or []),
                    "denyMessage": str(eligibility.get("denyMessage") or "").strip(),
                },
                "questions": questions,
                "requiresProof": bool(rawDivision.get("requiresProof", False)),
                "proofPrompt": str(rawDivision.get("proofPrompt") or "").strip(),
                "formUrl": formUrl,
                "discordServerUrl": discordServerUrl,
                "autoAcceptGroupId": _toPositiveInt(rawDivision.get("autoAcceptGroupId"), 0),
                "seedOrbatOnApprove": bool(
                    rawDivision.get(
                        "seedOrbatOnApprove",
                        rawDivision.get("seedRecruitmentOrbatOnApprove", False),
                    )
                ),
            }
            order.append(key)

        self.divisions = divisions
        self.divisionOrder = order
        log.info("Loaded %d division profiles.", len(self.divisions))

    def getDivision(self, divisionKey: str) -> Optional[dict[str, Any]]:
        key = str(divisionKey or "").strip().lower()
        division = self.divisions.get(key)
        if division:
            return division
        legacyAliases = {
            "lore": "anld",
        }
        aliasKey = legacyAliases.get(key, "")
        if aliasKey:
            return self.divisions.get(aliasKey)
        return None

    def adminRoleIds(self) -> set[int]:
        return set(_toIntList(getattr(config, "divisionApplicationsAdminRoleIds", [])))

    def globalReviewerRoleIds(self) -> set[int]:
        return set(_toIntList(getattr(config, "divisionApplicationsGlobalReviewerRoleIds", [])))

    def controlRoleIds(self) -> set[int]:
        return set(_toIntList(getattr(config, "divisionApplicationsControlRoleIds", [])))

    def hasAnyRole(self, member: discord.Member, roleIds: set[int]) -> bool:
        return bool(roleIds) and any(role.id in roleIds for role in member.roles)

    def isAdmin(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return True
        return self.hasAnyRole(member, self.adminRoleIds())

    def isServerAdministrator(self, member: discord.Member) -> bool:
        return bool(member.guild_permissions.administrator)

    def canControlDivisionState(self, member: discord.Member) -> bool:
        if self.isAdmin(member):
            return True
        return self.hasAnyRole(member, self.controlRoleIds())

    def canEditDivisionConfig(self, member: discord.Member, division: dict[str, Any]) -> bool:
        if self.isAdmin(member):
            return True
        roleIds = set(_toIntList(division.get("reviewerRoleIds") or []))
        return self.hasAnyRole(member, roleIds)

    def isDivisionReviewer(self, member: discord.Member, division: dict[str, Any]) -> bool:
        if self.isAdmin(member):
            return True
        localIds = set(_toIntList(division.get("reviewerRoleIds") or []))
        return self.hasAnyRole(member, localIds | self.globalReviewerRoleIds())

    def checkEligibility(self, member: discord.Member, division: dict[str, Any]) -> tuple[bool, str]:
        requiredRoleIds = set(_toIntList((division.get("eligibility") or {}).get("requiredRoleIds") or []))
        if not requiredRoleIds or self.hasAnyRole(member, requiredRoleIds):
            return True, ""
        denyMessage = str((division.get("eligibility") or {}).get("denyMessage") or "").strip()
        return False, denyMessage or "You are not eligible for this division yet."

    def reviewMentions(self, division: dict[str, Any]) -> str:
        roleIds = _toIntList(division.get("reviewerRoleIds") or [])
        roleIds.extend(_toIntList(getattr(config, "divisionApplicationsGlobalReviewerRoleIds", [])))
        deduped = sorted(set(roleIds))
        return " ".join(f"<@&{roleId}>" for roleId in deduped)

    async def _sendEphemeral(
        self,
        interaction: discord.Interaction,
        content: str,
        *,
        embed: Optional[discord.Embed] = None,
    ) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=content,
            embed=embed,
            ephemeral=True,
        )

    async def safeReply(self, interaction: discord.Interaction, content: str, *, embed: Optional[discord.Embed] = None) -> None:
        await self._sendEphemeral(interaction, content, embed=embed)

    def canUseApplicationsPanel(self, member: discord.Member) -> bool:
        panelRoleIds = set(self.globalReviewerRoleIds())
        highRankRoleId = _toPositiveInt(getattr(config, "highRankRoleId", 0), 0)
        if highRankRoleId > 0:
            panelRoleIds.add(highRankRoleId)
        return self.isAdmin(member) or self.hasAnyRole(member, panelRoleIds)

    def canUseApplicationsReviewTools(self, member: discord.Member) -> bool:
        return self.isAdmin(member) or self.hasAnyRole(member, self.globalReviewerRoleIds())

    async def resolveActionTargetChannel(
        self,
        interaction: discord.Interaction,
        channelInput: str,
    ) -> Optional[discord.abc.Messageable]:
        if not interaction.guild:
            return None
        channelId = _parseSnowflake(channelInput)
        if channelId <= 0:
            return interaction.channel if isinstance(interaction.channel, (discord.TextChannel, discord.Thread)) else None
        channel = interaction.guild.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        channelGuild = getattr(channel, "guild", None)
        if channelGuild is not None and int(getattr(channelGuild, "id", 0) or 0) != int(interaction.guild.id):
            return None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def _resolveGuildChannel(
        self,
        guild: discord.Guild,
        channelId: int,
    ) -> Optional[discord.abc.GuildChannel]:
        if channelId <= 0:
            return None
        channel = guild.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        channelGuild = getattr(channel, "guild", None)
        if channelGuild is not None and int(getattr(channelGuild, "id", 0) or 0) != int(guild.id):
            return None
        return channel if isinstance(channel, discord.abc.GuildChannel) else None

    async def resolveReviewChannel(self, guild: discord.Guild, division: dict[str, Any]) -> Optional[discord.abc.Messageable]:
        channelId = int(division.get("reviewChannelId") or 0)
        if channelId <= 0:
            return None
        channel = guild.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    def _loadDivisionsPayload(self) -> Optional[dict[str, Any]]:
        return divisionConfigService.loadDivisionsPayload(self.divisionsConfigPath)

    def _saveDivisionsPayload(self, payload: dict[str, Any]) -> bool:
        return divisionConfigService.saveDivisionsPayload(self.divisionsConfigPath, payload)

    def _findRawDivision(self, payload: dict[str, Any], divisionKey: str) -> Optional[dict[str, Any]]:
        return divisionConfigService.findRawDivision(payload, divisionKey)

    def _assignDivisionQuestionKeys(
        self,
        divisionKey: str,
        questions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return divisionConfigService.assignDivisionQuestionKeys(divisionKey, questions)

    def _sanitizeRawQuestions(self, divisionKey: str, rawQuestions: Any) -> list[dict[str, Any]]:
        return divisionConfigService.sanitizeRawQuestions(divisionKey, rawQuestions)

    def getDivisionQuestionsForEditor(self, divisionKey: str) -> list[dict[str, Any]]:
        payload = self._loadDivisionsPayload()
        if payload is None:
            return []
        rawDivision = self._findRawDivision(payload, divisionKey)
        if rawDivision is None:
            return []
        return self._sanitizeRawQuestions(divisionKey, rawDivision.get("questions"))

    def buildDivisionQuestionsEmbed(self, divisionKey: str, selectedIndex: int = 0) -> discord.Embed:
        division = self.getDivision(divisionKey) or {}
        displayName = str(division.get("displayName") or divisionKey)
        questions = self.getDivisionQuestionsForEditor(divisionKey)
        count = len(questions)
        safeIndex = min(max(int(selectedIndex or 0), 0), max(count - 1, 0))
        lines: list[str] = []
        for index, question in enumerate(questions):
            marker = "->" if index == safeIndex else "  "
            label = str(question.get("label") or f"Question {index + 1}")
            styleLabel = _questionStyleLabel(str(question.get("style") or "short"))
            lines.append(f"{marker} {index + 1}. {label} ({styleLabel})")
        if not lines:
            lines.append("(No questions configured)")

        embed = discord.Embed(
            title=f"Edit Questions - {displayName}",
            description="\n".join(lines[:25]),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="How To Use",
            value="Select a question from the dropdown, then use Edit/Change Type/Toggle Required.",
            inline=False,
        )
        if count:
            selected = questions[safeIndex]
            style = _normalizeQuestionStyle(selected.get("style"))
            details = [f"Style: {_questionStyleLabel(style)}"]
            if style in {"short", "paragraph"}:
                details.append(f"Required: {'Yes' if selected.get('required') else 'No'}")
                details.append(f"Max Length: {selected.get('maxLength', 400)}")
                placeholder = str(selected.get("placeholder") or "").strip()
                if placeholder:
                    details.append(f"Placeholder: {placeholder}")
            elif style == "multiple-choice":
                details.append(f"Required: {'Yes' if selected.get('required') else 'No'}")
                rawChoices = selected.get("choices")
                choices = rawChoices if isinstance(rawChoices, list) else []
                choicePreview = [str(choice).strip() for choice in choices if str(choice).strip()]
                if choicePreview:
                    details.append("Choices: " + ", ".join(choicePreview[:8]))
                    if len(choicePreview) > 8:
                        details.append(f"...and {len(choicePreview) - 8} more")
                else:
                    details.append("Choices: (none)")
            elif style == "form":
                details.append(f"URL: {str(selected.get('link') or '') or '(missing)'}")
            else:
                details.append(f"URL: {str(selected.get('inviteUrl') or '') or '(missing)'}")
            embed.add_field(name=f"Selected Question #{safeIndex + 1}", value="\n".join(details), inline=False)
        embed.set_footer(text=f"Total Questions: {count}/5")
        return embed

    async def openDivisionQuestionsPanel(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        selectedIndex: int = 0,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")
        embed = self.buildDivisionQuestionsEmbed(divisionKey, selectedIndex)
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=embed,
            view=ApplicationsDivisionQuestionsView(self, divisionKey, selectedIndex),
            ephemeral=True,
        )

    async def _persistDivisionQuestions(
        self,
        guild: discord.Guild,
        divisionKey: str,
        questions: list[dict[str, Any]],
    ) -> bool:
        payload = self._loadDivisionsPayload()
        if payload is None:
            return False
        rawDivision = self._findRawDivision(payload, divisionKey)
        if rawDivision is None:
            return False
        rawDivision["questions"] = self._sanitizeRawQuestions(divisionKey, questions)[:5]
        if not self._saveDivisionsPayload(payload):
            return False
        self.loadDivisionConfig()
        await self.refreshHubEmbedsForDivision(guild, str(divisionKey or "").strip().lower())
        return True

    async def refreshHubEmbedsForDivision(
        self,
        guild: discord.Guild,
        divisionKey: str,
    ) -> int:
        return await applicationsHubRefresh.refreshHubEmbedsForDivision(
            cog=self,
            guild=guild,
            divisionKey=divisionKey,
            buildView=lambda key, isOpen: DivisionHubView(self, key, isOpen=isOpen),
            buildEmbed=applicationsRendering.buildApplicationHubEmbed,
        )

    async def _resolveHubMessage(
        self,
        guild: discord.Guild,
        hubRow: dict[str, Any],
        *,
        deleteMissing: bool = False,
    ) -> Optional[discord.Message]:
        return await applicationsHubRefresh.resolveHubMessage(
            botClient=self.bot,
            guild=guild,
            hubRow=hubRow,
            deleteMissing=deleteMissing,
        )

    async def resolveHubChannel(self, guild: discord.Guild, divisionKey: str) -> Optional[discord.abc.Messageable]:
        hubRow = await applicationsService.getLatestHubMessageForDivision(guild.id, divisionKey)
        if not hubRow:
            return None
        channelId = int(hubRow.get("channelId") or 0)
        hubMessageId = int(hubRow.get("messageId") or 0)
        if channelId <= 0:
            return None

        # Preferred destination: the auto-created hub thread.
        # For message-started threads, thread ID matches the starter message ID.
        if hubMessageId > 0:
            thread = guild.get_channel(hubMessageId)
            if thread is None:
                thread = await interactionRuntime.safeFetchChannel(self.bot, hubMessageId)
            if isinstance(thread, discord.Thread):
                return thread

        channel = guild.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)

        # If the thread is missing, try to re-create it from the stored hub message.
        if isinstance(channel, discord.TextChannel) and hubMessageId > 0:
            hubMessage = await interactionRuntime.safeFetchMessage(channel, hubMessageId)
            if hubMessage is not None:
                try:
                    threadName = f"{divisionKey} applications"
                    thread = await taskBudgeter.runDiscord(
                        lambda: hubMessage.create_thread(
                            name=threadName[:100],
                            auto_archive_duration=10080,
                        )
                    )
                except (discord.Forbidden, discord.HTTPException):
                    thread = None
                if isinstance(thread, discord.Thread):
                    await interactionRuntime.safeChannelSend(
                        thread,
                        content="Applications thread created automatically for this division hub.",
                    )
                    return thread

        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def editSelectedDivisionQuestion(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: int,
    ) -> None:
        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if not questions:
            return await interactionRuntime.safeInteractionReply(
                interaction,
                content="No questions configured yet. Use Add Question first.",
                ephemeral=True,
            )
        safeIndex = min(max(int(questionIndex), 0), len(questions) - 1)
        question = questions[safeIndex]
        style = _normalizeQuestionStyle(question.get("style"))
        if style == "multiple-choice":
            await interactionRuntime.safeInteractionSendModal(
                interaction,
                ApplicationsDivisionChoiceQuestionModal(
                    self,
                    divisionKey,
                    questionIndex=safeIndex,
                    question=question,
                ),
            )
            return
        if style in {"short", "paragraph"}:
            await interactionRuntime.safeInteractionSendModal(
                interaction,
                ApplicationsDivisionTextQuestionModal(
                    self,
                    divisionKey,
                    questionIndex=safeIndex,
                    question=question,
                ),
            )
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            ApplicationsDivisionLinkQuestionModal(
                self,
                divisionKey,
                questionIndex=safeIndex,
                question=question,
            ),
        )

    async def upsertDivisionTextQuestion(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: Optional[int],
        label: str,
        placeholder: str,
        maxLengthRaw: str,
        requiredRaw: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")

        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if questionIndex is None and len(questions) >= 5:
            return await self.safeReply(interaction, "A division can only have up to 5 questions.")
        if questionIndex is not None and (questionIndex < 0 or questionIndex >= len(questions)):
            return await self.safeReply(interaction, "Question index is out of range.")

        cleanLabel = str(label or "").strip()
        if not cleanLabel:
            return await self.safeReply(interaction, "Question label is required.")
        try:
            maxLength = int(maxLengthRaw or "400")
        except (TypeError, ValueError):
            maxLength = 400
        maxLength = max(1, min(maxLength, 4000))
        required = _parseBoolText(requiredRaw, default=True)
        style = "short"
        if questionIndex is not None:
            existingStyle = _normalizeQuestionStyle(questions[questionIndex].get("style"))
            style = existingStyle if existingStyle in {"short", "paragraph"} else "short"

        entry: dict[str, Any] = {
            "key": str(cleanLabel).lower(),
            "label": cleanLabel,
            "required": required,
            "style": style,
            "maxLength": maxLength,
            "placeholder": str(placeholder or "").strip(),
        }
        if questionIndex is None:
            questions.append(entry)
            safeIndex = len(questions) - 1
        else:
            questions[questionIndex] = entry
            safeIndex = questionIndex

        if not await self._persistDivisionQuestions(interaction.guild, divisionKey, questions):
            return await self.safeReply(interaction, "Failed to update division questions.")
        await self.openDivisionQuestionsPanel(interaction, divisionKey=divisionKey, selectedIndex=safeIndex)

    async def updateDivisionLinkQuestion(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: int,
        style: str,
        label: str,
        url: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")

        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if questionIndex < 0 or questionIndex >= len(questions):
            return await self.safeReply(interaction, "Question index is out of range.")

        cleanLabel = str(label or "").strip()
        cleanUrl = str(url or "").strip()
        if not cleanLabel or not cleanUrl:
            return await self.safeReply(interaction, "Both label and URL are required.")
        normalizedStyle = _normalizeQuestionStyle(style)
        if normalizedStyle == "form":
            questions[questionIndex] = {
                "key": str(cleanLabel).lower(),
                "label": cleanLabel,
                "required": True,
                "style": "form",
                "link": cleanUrl,
            }
        else:
            questions[questionIndex] = {
                "key": str(cleanLabel).lower(),
                "label": cleanLabel,
                "required": True,
                "style": "server-invite",
                "inviteUrl": cleanUrl,
            }
        if not await self._persistDivisionQuestions(interaction.guild, divisionKey, questions):
            return await self.safeReply(interaction, "Failed to update division questions.")
        await self.openDivisionQuestionsPanel(interaction, divisionKey=divisionKey, selectedIndex=questionIndex)

    async def updateDivisionChoiceQuestion(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: int,
        label: str,
        choicesRaw: str,
        requiredRaw: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")

        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if questionIndex < 0 or questionIndex >= len(questions):
            return await self.safeReply(interaction, "Question index is out of range.")

        cleanLabel = str(label or "").strip()
        if not cleanLabel:
            return await self.safeReply(interaction, "Question label is required.")

        choices: list[str] = []
        normalizedRaw = str(choicesRaw or "").replace(",", "\n")
        for line in normalizedRaw.splitlines():
            cleanChoice = str(line or "").strip()
            if cleanChoice and cleanChoice not in choices:
                choices.append(cleanChoice)
        if len(choices) < 2:
            return await self.safeReply(interaction, "Multiple-choice questions need at least 2 unique options.")

        questions[questionIndex] = {
            "key": str(cleanLabel).lower(),
            "label": cleanLabel,
            "required": _parseBoolText(requiredRaw, default=True),
            "style": "multiple-choice",
            "choices": choices[:25],
        }
        if not await self._persistDivisionQuestions(interaction.guild, divisionKey, questions):
            return await self.safeReply(interaction, "Failed to update division questions.")
        await self.openDivisionQuestionsPanel(interaction, divisionKey=divisionKey, selectedIndex=questionIndex)

    async def updateDivisionQuestionStyle(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: int,
        style: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")

        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if questionIndex < 0 or questionIndex >= len(questions):
            return await self.safeReply(interaction, "Question index is out of range.")

        selected = questions[questionIndex]
        label = str(selected.get("label") or f"Question {questionIndex + 1}").strip() or f"Question {questionIndex + 1}"
        key = str(selected.get("key") or label).strip().lower()
        normalizedStyle = _normalizeQuestionStyle(style)
        if normalizedStyle in {"short", "paragraph"}:
            selected = {
                "key": key,
                "label": label,
                "required": bool(selected.get("required", True)),
                "style": normalizedStyle,
                "maxLength": int(selected.get("maxLength") or 400),
                "placeholder": str(selected.get("placeholder") or "").strip(),
            }
        elif normalizedStyle == "multiple-choice":
            rawChoices = selected.get("choices")
            choices = rawChoices if isinstance(rawChoices, list) else []
            cleanChoices = [str(choice).strip() for choice in choices if str(choice).strip()]
            if len(cleanChoices) < 2:
                cleanChoices = ["Option 1", "Option 2"]
            selected = {
                "key": key,
                "label": label,
                "required": bool(selected.get("required", True)),
                "style": "multiple-choice",
                "choices": cleanChoices[:25],
            }
        elif normalizedStyle == "form":
            selected = {
                "key": key,
                "label": label,
                "required": True,
                "style": "form",
                "link": str(selected.get("link") or selected.get("url") or "").strip(),
            }
        else:
            selected = {
                "key": key,
                "label": label,
                "required": True,
                "style": "server-invite",
                "inviteUrl": str(selected.get("inviteUrl") or selected.get("url") or "").strip(),
            }
        questions[questionIndex] = selected
        if not await self._persistDivisionQuestions(interaction.guild, divisionKey, questions):
            return await self.safeReply(interaction, "Failed to update division questions.")
        await self.openDivisionQuestionsPanel(interaction, divisionKey=divisionKey, selectedIndex=questionIndex)

    async def removeDivisionQuestion(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: int,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        division = self.getDivision(divisionKey)
        if not division:
            return
        if not self.canEditDivisionConfig(interaction.user, division):
            return
        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if questionIndex < 0 or questionIndex >= len(questions):
            return
        del questions[questionIndex]
        await self._persistDivisionQuestions(interaction.guild, divisionKey, questions)

    async def toggleDivisionQuestionRequired(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        questionIndex: int,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return
        division = self.getDivision(divisionKey)
        if not division:
            return
        if not self.canEditDivisionConfig(interaction.user, division):
            return
        questions = self.getDivisionQuestionsForEditor(divisionKey)
        if questionIndex < 0 or questionIndex >= len(questions):
            return
        style = _normalizeQuestionStyle(questions[questionIndex].get("style"))
        if style not in {"short", "paragraph", "multiple-choice"}:
            return
        questions[questionIndex]["required"] = not bool(questions[questionIndex].get("required", True))
        await self._persistDivisionQuestions(interaction.guild, divisionKey, questions)

    async def openDivisionEditor(self, interaction: discord.Interaction, divisionKey: str) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")
        isOpen = await applicationsService.isDivisionOpen(interaction.guild.id, str(division.get("key") or divisionKey))

        embed = discord.Embed(
            title=f"Edit Division - {division.get('displayName') or divisionKey}",
            description="Choose which section to edit.",
            color=discord.Color.blurple(),
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=embed,
            view=ApplicationsDivisionEditorView(self, divisionKey, isOpen=isOpen),
            ephemeral=True,
        )

    async def updateDivisionConfig(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        updates: dict[str, Any],
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This action can only be used in a server.")
        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")
        if not self.canEditDivisionConfig(interaction.user, division):
            return await self.safeReply(interaction, "You are not allowed to edit this division.")

        payload = self._loadDivisionsPayload()
        if payload is None:
            return await self.safeReply(interaction, "Could not load divisions config.")
        divisionsRaw = payload.get("divisions")
        if not isinstance(divisionsRaw, list):
            return await self.safeReply(interaction, "Divisions config is invalid.")

        updated = False
        for rawDivision in divisionsRaw:
            if not isinstance(rawDivision, dict):
                continue
            key = str(rawDivision.get("key") or "").strip().lower()
            if key != str(divisionKey or "").strip().lower():
                continue

            for field, value in updates.items():
                if field == "eligibility" and isinstance(value, dict):
                    rawDivision["eligibility"] = value
                else:
                    rawDivision[field] = value
            updated = True
            break

        if not updated:
            return await self.safeReply(interaction, "Division key not found in config.")
        if not self._saveDivisionsPayload(payload):
            return await self.safeReply(interaction, "Failed to save divisions config.")

        self.loadDivisionConfig()
        normalizedDivisionKey = str(divisionKey).strip().lower()
        updatedCards = await self.refreshHubEmbedsForDivision(
            interaction.guild,
            normalizedDivisionKey,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Division config updated. Updated {updatedCards} hub card(s).",
            ephemeral=True,
        )

