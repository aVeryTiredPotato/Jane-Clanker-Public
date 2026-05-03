from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord

import config
from features.staff.applications import rendering as applicationsRendering
from features.staff.applications import service as applicationsService
from features.staff.applications import workflowBridge as applicationsWorkflowBridge
from features.staff.applications.questionEditor import _normalizeQuestionStyle
from runtime import interaction as interactionRuntime


def _parseDbTime(rawValue: Optional[str]) -> Optional[datetime]:
    if not rawValue:
        return None
    try:
        parsed = datetime.fromisoformat(rawValue)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def submissionGateMessage(
    *,
    guildId: int,
    divisionKey: str,
    userId: int,
) -> Optional[str]:
    maxActive = int(getattr(config, "divisionApplicationsMaxActivePerDivision", 1) or 1)
    if maxActive > 0:
        activeCount = await applicationsService.activeApplicationCount(guildId, divisionKey, userId)
        if activeCount >= maxActive:
            return "You already have an active application for this division."

    cooldownMinutes = int(getattr(config, "divisionApplicationsCooldownMinutes", 0) or 0)
    if cooldownMinutes > 0:
        lastCreated = _parseDbTime(
            await applicationsService.lastApplicationTimestamp(guildId, divisionKey, userId)
        )
        if lastCreated is not None:
            readyAt = lastCreated + timedelta(minutes=cooldownMinutes)
            nowUtc = datetime.now(timezone.utc)
            if readyAt > nowUtc:
                remainingMinutes = max(1, int((readyAt - nowUtc).total_seconds() // 60))
                return f"Please wait {remainingMinutes} minute(s) before re-applying."

    return None


async def resolveSubmissionReviewChannel(
    *,
    cog: Any,
    interaction: discord.Interaction,
    division: dict[str, Any],
    preferCurrentThread: bool = False,
) -> Optional[discord.abc.Messageable]:
    if not interaction.guild:
        return None
    if preferCurrentThread and isinstance(interaction.channel, discord.Thread):
        return interaction.channel

    reviewChannel = await cog.resolveHubChannel(interaction.guild, division["key"])
    if reviewChannel is None:
        reviewChannel = await cog.resolveReviewChannel(interaction.guild, division)
    if reviewChannel is None and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        reviewChannel = interaction.channel
    return reviewChannel


def normalizeDivisionApplyQuestions(
    *,
    division: dict[str, Any],
) -> list[dict[str, Any]]:
    normalizedQuestions: list[dict[str, Any]] = []
    for question in division.get("questions") or []:
        if not isinstance(question, dict):
            continue
        style = _normalizeQuestionStyle(question.get("style"))
        if style == "multiple-choice":
            rawChoices = question.get("choices")
            choices = rawChoices if isinstance(rawChoices, list) else []
            cleanChoices = [str(choice).strip() for choice in choices if str(choice).strip()]
            if len(cleanChoices) >= 2:
                normalizedQuestions.append(
                    {
                        "key": str(question.get("key") or question.get("label") or "").strip().lower(),
                        "label": str(question.get("label") or "Question").strip(),
                        "required": bool(question.get("required", True)),
                        "style": "multiple-choice",
                        "choices": cleanChoices[:25],
                    }
                )
            continue
        normalizedQuestions.append(question)
    return normalizedQuestions[:5]


def splitDivisionApplyQuestions(
    *,
    division: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    textQuestions: list[dict[str, Any]] = []
    choiceQuestions: list[dict[str, Any]] = []
    for question in normalizeDivisionApplyQuestions(division=division):
        style = _normalizeQuestionStyle(question.get("style"))
        if style == "multiple-choice":
            choiceQuestions.append(question)
        else:
            textQuestions.append(question)
    return textQuestions[:5], choiceQuestions[:5]


def splitLeadingQuestionChunk(
    questions: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    if not questions:
        return "done", [], []
    firstStyle = _normalizeQuestionStyle(questions[0].get("style"))
    if firstStyle == "multiple-choice":
        chunk: list[dict[str, Any]] = []
        index = 0
        while index < len(questions) and _normalizeQuestionStyle(questions[index].get("style")) == "multiple-choice":
            chunk.append(questions[index])
            index += 1
        return "choice", chunk, questions[index:]

    chunk = []
    index = 0
    while index < len(questions) and _normalizeQuestionStyle(questions[index].get("style")) != "multiple-choice":
        chunk.append(questions[index])
        index += 1
    return "text", chunk, questions[index:]


async def openApplicationQuestionStep(
    *,
    cog: Any,
    interaction: discord.Interaction,
    divisionKey: str,
    answers: dict[str, str],
    remainingQuestions: list[dict[str, Any]],
) -> None:
    if not remainingQuestions:
        await cog.handleModalSubmit(interaction, divisionKey, answers)
        return

    division = cog.getDivision(divisionKey)
    if not division:
        await cog.safeReply(interaction, "This division is no longer configured.")
        return

    chunkType, chunkQuestions, restQuestions = splitLeadingQuestionChunk(list(remainingQuestions))
    if chunkType == "choice":
        await openMultipleChoiceStep(
            cog=cog,
            interaction=interaction,
            divisionKey=divisionKey,
            textAnswers=answers,
            choiceQuestions=chunkQuestions,
            remainingQuestions=restQuestions,
        )
        return

    await interactionRuntime.safeInteractionSendModal(
        interaction,
        cog.buildDivisionApplyModal(
            division=division,
            textQuestions=chunkQuestions,
            remainingQuestions=restQuestions,
            answersSoFar=answers,
        ),
    )


async def openMultipleChoiceStep(
    *,
    cog: Any,
    interaction: discord.Interaction,
    divisionKey: str,
    textAnswers: dict[str, str],
    choiceQuestions: list[dict[str, Any]],
    remainingQuestions: list[dict[str, Any]] | None = None,
) -> None:
    if not choiceQuestions:
        await cog.handleModalSubmit(interaction, divisionKey, textAnswers)
        return
    view = cog.buildDivisionMultipleChoiceView(
        divisionKey=divisionKey,
        applicantId=int(getattr(interaction.user, "id", 0) or 0),
        answersSoFar=textAnswers,
        choiceQuestions=choiceQuestions,
        remainingQuestions=remainingQuestions or [],
    )
    embed = view.buildEmbed()
    await interactionRuntime.safeInteractionReply(
        interaction,
        content="Finish the multiple-choice section, then submit.",
        embed=embed,
        view=view,
        ephemeral=True,
    )


async def handleApply(
    *,
    cog: Any,
    interaction: discord.Interaction,
    divisionKey: str,
) -> None:
    division = cog.getDivision(divisionKey)
    if not division:
        return await cog.safeReply(interaction, "This division is no longer configured.")
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await cog.safeReply(interaction, "This action can only be used in a server.")
    if not await applicationsService.isDivisionOpen(interaction.guild.id, division["key"]):
        return await cog.safeReply(interaction, "Applications are currently closed for this division.")

    eligible, reason = cog.checkEligibility(interaction.user, division)
    if not eligible:
        return await cog.safeReply(interaction, reason)

    gateMessage = await submissionGateMessage(
        guildId=interaction.guild.id,
        divisionKey=division["key"],
        userId=interaction.user.id,
    )
    if gateMessage:
        return await cog.safeReply(interaction, gateMessage)

    formUrl = str(division.get("formUrl") or "").strip()
    discordServerUrl = str(division.get("discordServerUrl") or "").strip()
    if discordServerUrl:
        serverView = discord.ui.View()
        serverView.add_item(
            discord.ui.Button(
                label="Join Division Server",
                style=discord.ButtonStyle.link,
                url=discordServerUrl,
            )
        )
        return await interaction.response.send_message(
            "Applications for this division are handled in its dedicated Discord server.",
            view=serverView,
            ephemeral=True,
        )
    if formUrl:
        formView = discord.ui.View()
        formView.add_item(
            discord.ui.Button(
                label="Open Application Form",
                style=discord.ButtonStyle.link,
                url=formUrl,
            )
        )
        reviewChannel = await resolveSubmissionReviewChannel(
            cog=cog,
            interaction=interaction,
            division=division,
            preferCurrentThread=True,
        )
        if reviewChannel is None:
            return await interaction.response.send_message(
                "This division uses an external application form, but I could not resolve a review destination.",
                view=formView,
                ephemeral=True,
            )

        answersPayload = {
            "Application Type": "External Form",
            "Form Link": formUrl,
            "Reviewer Note": "Applicant clicked the form link. Verify completion externally before approving.",
        }
        application = await applicationsService.createDivisionApplication(
            guildId=interaction.guild.id,
            divisionKey=division["key"],
            applicantId=interaction.user.id,
            answers=answersPayload,
            proofMessageUrl="",
            proofAttachments=[],
            reviewChannelId=int(reviewChannel.id),
        )
        await applicationsWorkflowBridge.syncApplicationWorkflow(
            application,
            stateKey="submitted",
            actorId=int(interaction.user.id),
            note="Application submitted through external form routing.",
            eventType="SUBMITTED",
        )
        workflowSummary = await applicationsWorkflowBridge.getApplicationWorkflowSummary(application)
        workflowHistorySummary = await applicationsWorkflowBridge.getApplicationWorkflowHistorySummary(application)

        reviewEmbed = applicationsRendering.buildReviewEmbed(
            application,
            division,
            workflowSummary=workflowSummary,
            workflowHistorySummary=workflowHistorySummary,
        )
        reviewView = cog.buildDivisionReviewView(
            int(application["applicationId"]),
            str(application.get("status") or "PENDING"),
            int(application.get("applicantId") or 0),
        )
        mentionText = cog.reviewMentions(division)
        reviewMessage = await interactionRuntime.safeChannelSend(
            reviewChannel,
            content=mentionText or None,
            embed=reviewEmbed,
            view=reviewView,
            allowed_mentions=discord.AllowedMentions(roles=True),
        )
        if reviewMessage is None:
            await applicationsService.setApplicationStatus(
                int(application["applicationId"]),
                "DENIED",
                reviewerId=None,
                reviewNote="Auto-denied: failed to post review card.",
            )
            await applicationsService.addApplicationEvent(
                int(application["applicationId"]),
                interaction.user.id,
                "AUTO_DENIED_POST_FAILED",
                "Could not post form-style review message.",
            )
            deniedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
            if deniedApplication is not None:
                await applicationsWorkflowBridge.syncApplicationWorkflow(
                    deniedApplication,
                    stateKey="denied",
                    actorId=None,
                    note="Auto-denied: failed to post external-form review card.",
                    eventType="AUTO_DENIED",
                )
            return await interaction.response.send_message(
                "External form opened, but I couldn't create the review card.",
                view=formView,
                ephemeral=True,
            )

        await applicationsService.setApplicationReviewMessage(
            int(application["applicationId"]),
            int(reviewChannel.id),
            reviewMessage.id,
        )
        await applicationsService.addApplicationEvent(
            int(application["applicationId"]),
            interaction.user.id,
            "FORM_LINK_OPENED",
            formUrl,
        )
        routedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
        if routedApplication is not None:
            await applicationsWorkflowBridge.syncApplicationWorkflow(
                routedApplication,
                stateKey="pending-review",
                actorId=None,
                note="Routed for review.",
                eventType="ROUTED_FOR_REVIEW",
            )
        cog.bot.add_view(reviewView, message_id=reviewMessage.id)
        await cog.refreshReviewCard(int(application["applicationId"]))
        appCode = str(application.get("appCode") or f"APP-{application.get('applicationId')}")
        return await interaction.response.send_message(
            f"This division uses an external application form. Reviewer card created as `{appCode}`.",
            view=formView,
            ephemeral=True,
        )

    orderedQuestions = normalizeDivisionApplyQuestions(division=division)
    if not orderedQuestions:
        return await cog.safeReply(interaction, "This division has no questions configured yet.")
    await openApplicationQuestionStep(
        cog=cog,
        interaction=interaction,
        divisionKey=division["key"],
        answers={},
        remainingQuestions=orderedQuestions,
    )


async def handleModalSubmit(
    *,
    cog: Any,
    interaction: discord.Interaction,
    divisionKey: str,
    answers: dict[str, str],
) -> None:
    division = cog.getDivision(divisionKey)
    if not division:
        return await cog.safeReply(interaction, "This division is no longer configured.")
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await cog.safeReply(interaction, "This action can only be used in a server.")

    gateMessage = await submissionGateMessage(
        guildId=interaction.guild.id,
        divisionKey=division["key"],
        userId=interaction.user.id,
    )
    if gateMessage:
        return await cog.safeReply(interaction, gateMessage)

    reviewChannel = await resolveSubmissionReviewChannel(
        cog=cog,
        interaction=interaction,
        division=division,
    )
    if reviewChannel is None:
        return await cog.safeReply(interaction, "I could not resolve where to post this application review.")

    proofMessageUrl: Optional[str] = None
    proofAttachments: list[str] = []
    if division.get("requiresProof"):
        proofPrompt = str(division.get("proofPrompt") or "").strip() or "Upload proof attachments in your next message in this channel within 3 minutes."
        await interaction.response.send_message(proofPrompt, ephemeral=True)
        proofMessage = await cog.collectProofMessage(interaction.channel, interaction.user.id)
        if proofMessage is None:
            return await interaction.followup.send("Timed out waiting for proof attachments.", ephemeral=True)
        proofMessageUrl = proofMessage.jump_url
        proofAttachments = [attachment.url for attachment in proofMessage.attachments]
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)

    application = await applicationsService.createDivisionApplication(
        guildId=interaction.guild.id,
        divisionKey=division["key"],
        applicantId=interaction.user.id,
        answers=answers,
        proofMessageUrl=proofMessageUrl,
        proofAttachments=proofAttachments,
        reviewChannelId=int(reviewChannel.id),
    )
    await applicationsWorkflowBridge.syncApplicationWorkflow(
        application,
        stateKey="submitted",
        actorId=int(interaction.user.id),
        note="Application submitted.",
        eventType="SUBMITTED",
    )
    workflowSummary = await applicationsWorkflowBridge.getApplicationWorkflowSummary(application)
    workflowHistorySummary = await applicationsWorkflowBridge.getApplicationWorkflowHistorySummary(application)

    reviewEmbed = applicationsRendering.buildReviewEmbed(
        application,
        division,
        workflowSummary=workflowSummary,
        workflowHistorySummary=workflowHistorySummary,
    )
    reviewView = cog.buildDivisionReviewView(
        int(application["applicationId"]),
        str(application.get("status") or "PENDING"),
        int(application.get("applicantId") or 0),
    )
    mentionText = cog.reviewMentions(division)
    reviewMessage = await interactionRuntime.safeChannelSend(
        reviewChannel,
        content=mentionText or None,
        embed=reviewEmbed,
        view=reviewView,
        allowed_mentions=discord.AllowedMentions(roles=True),
    )
    if reviewMessage is None:
        await applicationsService.setApplicationStatus(
            int(application["applicationId"]),
            "DENIED",
            reviewerId=None,
            reviewNote="Auto-denied: failed to post review card.",
        )
        await applicationsService.addApplicationEvent(
            int(application["applicationId"]),
            interaction.user.id,
            "AUTO_DENIED_POST_FAILED",
            "Could not post review message.",
        )
        deniedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
        if deniedApplication is not None:
            await applicationsWorkflowBridge.syncApplicationWorkflow(
                deniedApplication,
                stateKey="denied",
                actorId=None,
                note="Auto-denied: failed to post review card.",
                eventType="AUTO_DENIED",
            )
        return await interaction.followup.send("Application failed to route for review.", ephemeral=True)

    await applicationsService.setApplicationReviewMessage(
        int(application["applicationId"]),
        int(reviewChannel.id),
        reviewMessage.id,
    )
    await applicationsService.addApplicationEvent(
        int(application["applicationId"]),
        interaction.user.id,
        "ROUTED_FOR_REVIEW",
        f"Review message {reviewMessage.id}",
    )
    routedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
    if routedApplication is not None:
        await applicationsWorkflowBridge.syncApplicationWorkflow(
            routedApplication,
            stateKey="pending-review",
            actorId=None,
            note="Routed for review.",
            eventType="ROUTED_FOR_REVIEW",
        )
    cog.bot.add_view(reviewView, message_id=reviewMessage.id)
    await cog.refreshReviewCard(int(application["applicationId"]))
    appCode = str(application.get("appCode") or f"APP-{application.get('applicationId')}")
    await interaction.followup.send(f"Submitted. Application ID: `{appCode}`.", ephemeral=True)


__all__ = [
    "handleApply",
    "handleModalSubmit",
    "normalizeDivisionApplyQuestions",
    "openApplicationQuestionStep",
    "openMultipleChoiceStep",
    "resolveSubmissionReviewChannel",
    "splitLeadingQuestionChunk",
    "splitDivisionApplyQuestions",
    "submissionGateMessage",
]

