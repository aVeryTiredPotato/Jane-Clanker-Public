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


class ApplicationsOpsMixin:
    async def runApplicationsHubPost(
        self,
        interaction: discord.Interaction,
        *,
        divisionKey: str,
        channelInput: str = "",
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.isAdmin(interaction.user):
            return await self.safeReply(interaction, "Only application admins can post hub cards.")

        division = self.getDivision(divisionKey)
        if not division:
            return await self.safeReply(interaction, "Unknown division key.")

        targetChannel = await self.resolveActionTargetChannel(interaction, channelInput)
        if not isinstance(targetChannel, (discord.TextChannel, discord.Thread)):
            return await self.safeReply(interaction, "Target channel must be a text channel.")

        isOpen = await applicationsService.isDivisionOpen(interaction.guild.id, division["key"])
        view = DivisionHubView(self, division["key"], isOpen=isOpen)
        message = await self.sendHubMessage(targetChannel, division, view)
        if message is None:
            return await self.safeReply(interaction, "Failed to post hub message in that channel.")

        await applicationsService.saveHubMessage(message.id, interaction.guild.id, targetChannel.id, division["key"])
        self.bot.add_view(view, message_id=message.id)
        await self.createHubThread(message, division["displayName"])
        await self.safeReply(interaction, f"Hub posted for **{division['displayName']}**.")

    async def runApplicationsHubPostAll(
        self,
        interaction: discord.Interaction,
        *,
        channelInput: str = "",
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.isAdmin(interaction.user):
            return await self.safeReply(interaction, "Only application admins can post hub cards.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        targetChannel = await self.resolveActionTargetChannel(interaction, channelInput)
        if not isinstance(targetChannel, (discord.TextChannel, discord.Thread)):
            return await self.safeReply(interaction, "Target channel must be a text channel.")

        posted = 0
        for divisionKey in self.divisionOrder:
            division = self.divisions[divisionKey]
            isOpen = await applicationsService.isDivisionOpen(interaction.guild.id, divisionKey)
            view = DivisionHubView(self, divisionKey, isOpen=isOpen)
            message = await self.sendHubMessage(targetChannel, division, view)
            if message is None:
                continue
            await applicationsService.saveHubMessage(message.id, interaction.guild.id, targetChannel.id, divisionKey)
            self.bot.add_view(view, message_id=message.id)
            await self.createHubThread(message, division["displayName"])
            posted += 1

        await self.safeReply(interaction, f"Posted {posted}/{len(self.divisionOrder)} hub messages.")

    async def runAppsPending(self, interaction: discord.Interaction, *, division: Optional[str] = None) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.canUseApplicationsReviewTools(interaction.user):
            return await self.safeReply(interaction, "You are not authorized to view the queue.")

        divisionKey = str(division or "").strip().lower() if division else None
        if divisionKey and divisionKey not in self.divisions:
            return await self.safeReply(interaction, "Unknown division key.")

        rows = await applicationsService.listPendingApplications(divisionKey)
        if not rows:
            return await self.safeReply(interaction, "No pending applications.")

        lines: list[str] = []
        for row in rows[:20]:
            appCode = row.get("appCode") or f"APP-{row['applicationId']}"
            divKey = str(row.get("divisionKey") or "")
            lines.append(f"`{appCode}` - <@{row['applicantId']}> - `{divKey}` - {row.get('status', 'PENDING')}")
        if len(rows) > 20:
            lines.append(f"...and {len(rows) - 20} more")

        embed = discord.Embed(title="Pending Applications", description="\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def runAppsStats(
        self,
        interaction: discord.Interaction,
        *,
        division: Optional[str] = None,
        last: str = "7d",
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.canUseApplicationsReviewTools(interaction.user):
            return await self.safeReply(interaction, "You are not authorized to view stats.")

        divisionKey = str(division or "").strip().lower() if division else None
        if divisionKey and divisionKey not in self.divisions:
            return await self.safeReply(interaction, "Unknown division key.")

        try:
            days = _parseDays(last)
        except ValueError:
            return await self.safeReply(interaction, "Invalid window. Use format like 7d or 30d.")

        stats = await applicationsService.statsForDivision(divisionKey, days)
        embed = discord.Embed(
            title="Application Stats",
            description=f"Window: last {days} day(s)",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Pending", value=str(stats.get("PENDING", 0)), inline=True)
        embed.add_field(name="Needs Info", value=str(stats.get("NEEDS_INFO", 0)), inline=True)
        embed.add_field(name="Approved", value=str(stats.get("APPROVED", 0)), inline=True)
        embed.add_field(name="Denied", value=str(stats.get("DENIED", 0)), inline=True)
        embed.add_field(name="Total", value=str(stats.get("TOTAL", 0)), inline=True)
        if divisionKey:
            embed.add_field(name="Division", value=divisionKey, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def runAppsReopen(
        self,
        interaction: discord.Interaction,
        *,
        applicationId: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.canUseApplicationsReviewTools(interaction.user):
            return await self.safeReply(interaction, "You are not authorized to reopen applications.")

        appCode = str(applicationId or "").strip().upper()
        application = await applicationsService.getApplicationByCode(appCode)
        if not application:
            return await self.safeReply(interaction, "Application not found.")

        await applicationsService.setApplicationStatus(
            int(application["applicationId"]),
            "PENDING",
            reviewerId=interaction.user.id,
            reviewNote="Reopened by staff.",
        )
        await applicationsService.incrementReopenCount(int(application["applicationId"]))
        await applicationsService.addApplicationEvent(
            int(application["applicationId"]),
            interaction.user.id,
            "REOPENED",
            "Reopened via /apps reopen",
        )
        refreshedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
        if refreshedApplication is not None:
            await applicationsWorkflowBridge.syncApplicationWorkflow(
                refreshedApplication,
                stateKey="pending-review",
                actorId=int(interaction.user.id),
                note="Application reopened by staff.",
                eventType="REOPENED",
            )
        await self.refreshReviewCard(int(application["applicationId"]))
        await self.safeReply(interaction, f"Reopened `{appCode}`.")

    async def runAppsForceApprove(
        self,
        interaction: discord.Interaction,
        *,
        applicationId: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self.safeReply(interaction, "This command can only be used in a server.")
        if not self.isAdmin(interaction.user):
            return await self.safeReply(interaction, "Only application admins can force approve.")

        appCode = str(applicationId or "").strip().upper()
        application = await applicationsService.getApplicationByCode(appCode)
        if not application:
            return await self.safeReply(interaction, "Application not found.")

        division = self.getDivision(str(application.get("divisionKey") or ""))
        if not division:
            return await self.safeReply(interaction, "Division config missing for this application.")
        if _isFinal(str(application.get("status") or "")):
            return await self.safeReply(interaction, "This application is already finalized.")

        await interaction.response.defer(ephemeral=True, thinking=True)
        await applicationsService.setApplicationStatus(
            int(application["applicationId"]),
            "APPROVED",
            reviewerId=interaction.user.id,
            reviewNote="Force-approved by admin command.",
        )
        await applicationsService.addApplicationEvent(
            int(application["applicationId"]),
            interaction.user.id,
            "FORCE_APPROVED",
            "Approved via /apps force-approve",
        )
        refreshedApplication = await applicationsService.getApplicationById(int(application["applicationId"]))
        if refreshedApplication is not None:
            await applicationsWorkflowBridge.syncApplicationWorkflow(
                refreshedApplication,
                stateKey="approved",
                actorId=int(interaction.user.id),
                note="Force-approved by application admin.",
                eventType="FORCE_APPROVED",
            )

        grantedCount, grantedRoles = await self.grantRoles(
            interaction.guild,
            int(application["applicantId"]),
            _toIntList(division.get("grantRoleIds") or []),
            interaction.user.id,
        )
        await self.notifyApplicant(
            int(application["applicantId"]),
            f"Your application `{appCode}` for **{division['displayName']}** was approved.",
        )
        await self.sendOrbatKickoff(interaction.guild, application, division)
        recruitmentSummary = await self.syncRecruitmentOrbatOnApprove(
            application,
            division,
            reviewerId=int(interaction.user.id),
        )
        await self.refreshReviewCard(int(application["applicationId"]))

        details = ", ".join(grantedRoles) if grantedRoles else "no new roles"
        await interaction.followup.send(
            f"Force-approved `{appCode}` ({grantedCount} roles applied: {details}).{recruitmentSummary}",
            ephemeral=True,
        )

