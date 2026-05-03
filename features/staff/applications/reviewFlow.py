from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import discord

from features.staff.applications import rendering as applicationsRendering
from features.staff.applications import service as applicationsService
from features.staff.applications import workflowBridge as applicationsWorkflowBridge
from runtime import interaction as interactionRuntime


def _isFinal(status: str) -> bool:
    return (status or "").upper() in {"APPROVED", "DENIED"}


async def refreshReviewCard(
    *,
    cog: Any,
    applicationId: int,
    buildReviewView: Callable[[int, str, int], discord.ui.View],
) -> None:
    application = await applicationsService.getApplicationById(applicationId)
    if not application:
        return
    await applicationsWorkflowBridge.ensureApplicationWorkflowCurrent(application)
    division = cog.getDivision(str(application.get("divisionKey") or ""))
    if not division:
        return
    reviewChannelId = int(application.get("reviewChannelId") or 0)
    reviewMessageId = int(application.get("reviewMessageId") or 0)
    if reviewChannelId <= 0 or reviewMessageId <= 0:
        return

    channel = cog.bot.get_channel(reviewChannelId)
    if channel is None:
        channel = await interactionRuntime.safeFetchChannel(cog.bot, reviewChannelId)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return
    message = await interactionRuntime.safeFetchMessage(channel, reviewMessageId)
    if message is None:
        return

    workflowSummary = await applicationsWorkflowBridge.getApplicationWorkflowSummary(application)
    workflowHistorySummary = await applicationsWorkflowBridge.getApplicationWorkflowHistorySummary(application)
    embed = applicationsRendering.buildReviewEmbed(
        application,
        division,
        workflowSummary=workflowSummary,
        workflowHistorySummary=workflowHistorySummary,
    )
    view = buildReviewView(
        applicationId,
        str(application.get("status") or "PENDING"),
        int(application.get("applicantId") or 0),
    )
    if _isFinal(str(application.get("status") or "")):
        disableAll = getattr(view, "disableAll", None)
        if callable(disableAll):
            disableAll()
    await interactionRuntime.safeMessageEdit(message, embed=embed, view=view)


async def handleApplicantAnswerButton(
    *,
    cog: Any,
    interaction: discord.Interaction,
    applicationId: int,
    applicantId: int,
) -> None:
    if not isinstance(interaction.user, discord.Member):
        return await cog.safeReply(interaction, "This action can only be used in a server.")
    if interaction.user.id != int(applicantId):
        return await cog.safeReply(interaction, "Only the applicant can answer this clarification request.")

    application = await applicationsService.getApplicationById(int(applicationId))
    if not application:
        return await cog.safeReply(interaction, "Application not found.")
    if str(application.get("status") or "").upper() != "NEEDS_INFO":
        return await cog.safeReply(interaction, "This application is not waiting on applicant clarification.")

    prompt = str(application.get("reviewNote") or "").strip() or "Clarification request"
    applicantAnswerModalFactory = getattr(cog, "buildApplicantAnswerModal", None)
    if not callable(applicantAnswerModalFactory):
        return await cog.safeReply(interaction, "Applicant answer form is unavailable.")
    await interactionRuntime.safeInteractionSendModal(
        interaction,
        applicantAnswerModalFactory(int(applicationId), prompt),
    )


async def handleApplicantAnswerSubmit(
    *,
    cog: Any,
    interaction: discord.Interaction,
    applicationId: int,
    promptTitle: str,
    answer: str,
) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await cog.safeReply(interaction, "This action can only be used in a server.")

    lock = cog.appLocks.setdefault(int(applicationId), asyncio.Lock())
    async with lock:
        application = await applicationsService.getApplicationById(int(applicationId))
        if not application:
            return await cog.safeReply(interaction, "Application not found.")
        if interaction.user.id != int(application.get("applicantId") or 0):
            return await cog.safeReply(interaction, "Only the applicant can submit this answer.")
        if str(application.get("status") or "").upper() != "NEEDS_INFO":
            return await cog.safeReply(interaction, "This application is not waiting on clarification.")

        try:
            answersPayload = json.loads(str(application.get("answersJson") or "{}"))
            if not isinstance(answersPayload, dict):
                answersPayload = {}
        except json.JSONDecodeError:
            answersPayload = {}

        nowStamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        baseKey = f"Clarification Response - {promptTitle.strip()[:72]}"
        key = baseKey
        suffix = 2
        while key in answersPayload:
            key = f"{baseKey} ({suffix})"
            suffix += 1
        answersPayload[key] = f"Reviewer Request:\n{promptTitle}\n\nApplicant Response ({nowStamp}):\n{answer}"

        await applicationsService.updateApplicationAnswers(int(applicationId), answersPayload)
        await applicationsService.setApplicationStatus(
            int(applicationId),
            "PENDING",
            reviewerId=None,
            reviewNote="",
        )
        await applicationsService.addApplicationEvent(
            int(applicationId),
            interaction.user.id,
            "APPLICANT_ANSWER_SUBMITTED",
            promptTitle,
        )
        refreshedApplication = await applicationsService.getApplicationById(int(applicationId))
        if not refreshedApplication:
            return await cog.safeReply(interaction, "Application not found after saving your answer.")
        await applicationsWorkflowBridge.syncApplicationWorkflow(
            refreshedApplication,
            stateKey="pending-review",
            actorId=int(interaction.user.id),
            note="Applicant submitted clarification.",
            eventType="APPLICANT_RESPONSE",
        )

    await cog.refreshReviewCard(int(applicationId))
    await cog.safeReply(interaction, "Your clarification answer was submitted.")


async def handleReviewDecision(
    *,
    cog: Any,
    interaction: discord.Interaction,
    applicationId: int,
    status: str,
    note: Optional[str],
) -> None:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return await cog.safeReply(interaction, "This action can only be used in a server.")

    lock = cog.appLocks.setdefault(int(applicationId), asyncio.Lock())
    async with lock:
        application = await applicationsService.getApplicationById(int(applicationId))
        if not application:
            return await cog.safeReply(interaction, "Application not found.")
        division = cog.getDivision(str(application.get("divisionKey") or ""))
        if not division:
            return await cog.safeReply(interaction, "Division config missing for this application.")
        if not cog.isDivisionReviewer(interaction.user, division):
            return await cog.safeReply(interaction, "You are not authorized to review this application.")
        if _isFinal(str(application.get("status") or "")):
            return await cog.safeReply(interaction, "This application is already finalized.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        normalizedStatus = str(status or "").upper()
        await applicationsService.setApplicationStatus(
            int(application["applicationId"]),
            normalizedStatus,
            reviewerId=interaction.user.id,
            reviewNote=note or "",
        )
        await applicationsService.addApplicationEvent(
            int(application["applicationId"]),
            interaction.user.id,
            f"STATUS_{normalizedStatus}",
            note or "",
        )
        refreshedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
        if refreshedApplication is None:
            return await interaction.followup.send("Application status updated, but the row could not be reloaded.", ephemeral=True)
        workflowNote = note or {
            "APPROVED": "Application approved.",
            "DENIED": "Application denied.",
            "NEEDS_INFO": "Reviewer requested clarification.",
        }.get(normalizedStatus, f"Application moved to {normalizedStatus}.")
        await applicationsWorkflowBridge.syncApplicationWorkflow(
            refreshedApplication,
            stateKey={
                "APPROVED": "approved",
                "DENIED": "denied",
                "NEEDS_INFO": "needs-info",
            }.get(normalizedStatus, "pending-review"),
            actorId=int(interaction.user.id),
            note=workflowNote,
            eventType=f"STATUS_{normalizedStatus}",
        )

        summary = ""
        if normalizedStatus == "APPROVED":
            grantRoleIds: list[int] = []
            for rawRoleId in division.get("grantRoleIds") or []:
                try:
                    parsedRoleId = int(rawRoleId)
                except (TypeError, ValueError):
                    continue
                if parsedRoleId > 0:
                    grantRoleIds.append(parsedRoleId)
            grantedCount, grantedRoles = await cog.grantRoles(
                interaction.guild,
                int(application["applicantId"]),
                grantRoleIds,
                interaction.user.id,
            )
            await cog.notifyApplicant(
                int(application["applicantId"]),
                f"Your application `{application.get('appCode')}` for **{division['displayName']}** was approved.",
            )
            await cog.sendOrbatKickoff(interaction.guild, application, division)
            groupSummary = await cog.autoAcceptDivisionGroup(application, division)
            recruitmentSummary = await cog.syncRecruitmentOrbatOnApprove(
                application,
                division,
                reviewerId=int(interaction.user.id),
            )
            if " seed skipped" in str(recruitmentSummary or "").lower():
                recruitmentSummary = ""
            if grantedCount > 0:
                summary = f" Roles granted: {', '.join(grantedRoles)}."
            else:
                summary = " No new roles were granted."
            summary = f"{summary}{groupSummary}{recruitmentSummary}"
        elif normalizedStatus == "DENIED":
            await cog.notifyApplicant(
                int(application["applicantId"]),
                f"Your application `{application.get('appCode')}` for **{division['displayName']}** was denied.",
            )
        elif normalizedStatus == "NEEDS_INFO":
            summary = " Awaiting applicant answer via the Answer button."

        await cog.refreshReviewCard(int(application["applicationId"]))
        await interaction.followup.send(f"Application marked as {normalizedStatus.lower()}.{summary}", ephemeral=True)


__all__ = [
    "handleApplicantAnswerButton",
    "handleApplicantAnswerSubmit",
    "handleReviewDecision",
    "refreshReviewCard",
]

