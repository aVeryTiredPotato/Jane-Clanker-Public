from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.staff.projects import service as projectService
from features.staff.projects import workflowBridge as projectWorkflowBridge
from features.staff.workflows import rendering as workflowRendering
from runtime import commandScopes as runtimeCommandScopes
from runtime import interaction as interactionRuntime
from runtime import normalization
from runtime import permissions as runtimePermissions
from runtime import taskBudgeter
from runtime import textFormatting as textFormattingRuntime

log = logging.getLogger(__name__)

_SERVER_NOT_RECOGNIZED_MESSAGE = (
    "Server not recognized. Please reach out to a_very_tired_potato for assistance."
)
_PROJECT_ACTIVE_STATUSES = {"PENDING_APPROVAL", "APPROVED", "SUBMITTED"}
_THREAD_NAME_SANITIZER = re.compile(r"[^a-z0-9-]+")


def _toPositiveInt(value: object, default: int = 0) -> int:
    return normalization.toPositiveInt(value, default)


def _toRoleIdList(values: object) -> list[int]:
    return normalization.normalizeIntList(values)


def _statusLabel(status: object) -> str:
    normalized = str(status or "").strip().upper()
    labels = {
        "PENDING_APPROVAL": "Pending HOD Approval",
        "APPROVED": "Approved by HOD",
        "DENIED": "Denied",
        "SUBMITTED": "Submitted for Finalization",
        "FINALIZED": "Finalized",
    }
    return labels.get(normalized, normalized or "Unknown")


def _statusColor(status: object) -> discord.Color:
    normalized = str(status or "").strip().upper()
    if normalized == "APPROVED":
        return discord.Color.green()
    if normalized == "DENIED":
        return discord.Color.red()
    if normalized == "SUBMITTED":
        return discord.Color.orange()
    if normalized == "FINALIZED":
        return discord.Color.blue()
    return discord.Color.blurple()


def _clip(value: object, maxLen: int) -> str:
    return textFormattingRuntime.clipText(value, maxLen, strip=True)


def _parseDbDatetime(value: object) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _discordTimestamp(value: object, style: str = "f") -> str:
    dt = _parseDbDatetime(value)
    if dt is None:
        return "unknown"
    return f"<t:{int(dt.timestamp())}:{style}>"


def _projectMessageUrl(row: dict) -> str:
    guildId = _toPositiveInt(row.get("guildId"))
    channelId = _toPositiveInt(row.get("reviewChannelId"))
    messageId = _toPositiveInt(row.get("reviewMessageId"))
    if guildId <= 0 or channelId <= 0 or messageId <= 0:
        return ""
    return f"https://discord.com/channels/{guildId}/{channelId}/{messageId}"


class ProjectCog(commands.Cog):
    projectGroup = app_commands.Group(
        name="project",
        description="Department project workflow tools.",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        try:
            workflowRows = await projectService.listProjectsForWorkflowReconciliation()
            reconciled, changed = await projectWorkflowBridge.reconcileProjectWorkflowRows(workflowRows)
            log.info(
                "Project workflow reconciliation: checked=%d changed=%d",
                reconciled,
                changed,
            )
        except Exception:
            log.exception("Project workflow reconciliation failed during startup.")

    def _projectGuildIds(self) -> set[int]:
        return set(_toRoleIdList(getattr(config, "projectCommandGuildIds", []) or []))

    def _projectHodRoleIds(self) -> set[int]:
        return set(_toRoleIdList(getattr(config, "projectHodRoleIds", []) or []))

    def _projectAssistantDirectorRoleIds(self) -> set[int]:
        return set(_toRoleIdList(getattr(config, "projectAssistantDirectorRoleIds", []) or []))

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    async def _ensureProjectGuildAllowed(self, interaction: discord.Interaction) -> bool:
        guildId = _toPositiveInt(getattr(getattr(interaction, "guild", None), "id", 0))
        if guildId <= 0:
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return False
        configured = self._projectGuildIds()
        if not configured:
            return True
        if guildId in configured:
            return True
        await self._safeEphemeral(interaction, _SERVER_NOT_RECOGNIZED_MESSAGE)
        return False

    @staticmethod
    def _hasAnyRole(member: discord.Member, allowedRoleIds: set[int]) -> bool:
        if not allowedRoleIds:
            return False
        return any(int(role.id) in allowedRoleIds for role in member.roles)

    def _canHodApprove(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        return self._hasAnyRole(member, self._projectHodRoleIds())

    def _canFinalize(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        return self._hasAnyRole(member, self._projectAssistantDirectorRoleIds())

    async def _buildProjectEmbed(self, row: dict) -> discord.Embed:
        await projectWorkflowBridge.ensureProjectWorkflowCurrent(row)
        projectId = _toPositiveInt(row.get("projectId"))
        title = str(row.get("title") or "").strip() or f"Project #{projectId}"
        creatorId = _toPositiveInt(row.get("creatorId"))
        status = str(row.get("status") or "").strip().upper()
        requestedPoints = max(0, _toPositiveInt(row.get("requestedPoints")))
        awardedPoints = _toPositiveInt(row.get("awardedPoints"))
        embed = discord.Embed(
            title=f"Project #{projectId} - {title}",
            color=_statusColor(status),
            description=_clip(row.get("idea"), 400),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Creator", value=f"<@{creatorId}>" if creatorId > 0 else "Unknown", inline=True)
        embed.add_field(name="Requested Points", value=str(requestedPoints), inline=True)
        workflowSummary = await projectWorkflowBridge.getProjectWorkflowSummary(row)
        workflowHistorySummary = await projectWorkflowBridge.getProjectWorkflowHistorySummary(row)
        reviewNotes = [text for text in [str(row.get("hodReviewNote") or "").strip(), str(row.get("finalReviewNote") or "").strip()] if text]
        workflowRendering.addReviewWorkflowFields(
            embed,
            statusText=_statusLabel(status),
            workflowSummary=workflowSummary,
            workflowHistorySummary=workflowHistorySummary,
            reviewerNote=" | ".join(reviewNotes),
        )
        if awardedPoints > 0:
            embed.add_field(name="Awarded Points", value=str(awardedPoints), inline=True)

        submitSummary = str(row.get("submitSummary") or "").strip()
        submitProof = str(row.get("submitProof") or "").strip()
        if submitSummary:
            embed.add_field(name="Submission Summary", value=_clip(submitSummary, 1024), inline=False)
        if submitProof:
            embed.add_field(name="Submission Proof", value=_clip(submitProof, 1024), inline=False)

        details = [
            f"Created: {_discordTimestamp(row.get('createdAt'), 'f')} ({_discordTimestamp(row.get('createdAt'), 'R')})",
            f"Updated: {_discordTimestamp(row.get('updatedAt'), 'R')}",
        ]
        if str(row.get("submittedAt") or "").strip():
            details.append(f"Submitted: {_discordTimestamp(row.get('submittedAt'), 'R')}")
        if str(row.get("finalizedAt") or "").strip():
            details.append(f"Finalized: {_discordTimestamp(row.get('finalizedAt'), 'R')}")
        if str(row.get("closedAt") or "").strip():
            details.append(f"Closed: {_discordTimestamp(row.get('closedAt'), 'R')}")
        embed.add_field(name="Timeline", value="\n".join(details), inline=False)

        threadId = _toPositiveInt(row.get("threadId"))
        if threadId > 0:
            embed.add_field(name="Project Thread", value=f"<#{threadId}>", inline=False)

        url = _projectMessageUrl(row)
        if url:
            embed.add_field(name="Review Message", value=f"[Jump to message]({url})", inline=False)
        return embed

    async def _resolveProjectMessage(self, row: dict) -> Optional[discord.Message]:
        channelId = _toPositiveInt(row.get("reviewChannelId"))
        messageId = _toPositiveInt(row.get("reviewMessageId"))
        if channelId <= 0 or messageId <= 0:
            return None
        channel = self.bot.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        return await interactionRuntime.safeFetchMessage(channel, messageId)

    async def _syncProjectMessage(self, row: dict) -> None:
        message = await self._resolveProjectMessage(row)
        if message is None:
            return
        embed = await self._buildProjectEmbed(row)
        await interactionRuntime.safeMessageEdit(message, embed=embed)

    async def _postThreadUpdate(self, row: dict, content: str) -> None:
        threadId = _toPositiveInt(row.get("threadId"))
        if threadId <= 0:
            return
        thread = self.bot.get_channel(threadId)
        if thread is None:
            thread = await interactionRuntime.safeFetchChannel(self.bot, threadId)
        if not isinstance(thread, discord.Thread):
            return
        await interactionRuntime.safeChannelSend(thread, content=content)

    async def _logProjectAction(
        self,
        *,
        guild: discord.Guild,
        actorId: int,
        projectId: int,
        action: str,
        details: dict,
    ) -> None:
        actionLabel = str(action or "update").strip().lower()
        try:
            runtimeServices = getattr(self.bot, "runtimeServices", {}) or {}
            auditStream = runtimeServices.get("auditStream")
            if auditStream is not None:
                await auditStream.logEvent(
                    source="project",
                    action=actionLabel,
                    guildId=int(guild.id),
                    actorId=int(actorId),
                    targetType="project",
                    targetId=str(int(projectId)),
                    severity="INFO",
                    details=details,
                    authorizedBy="project workflow",
                    postToDiscord=False,
                )
        except Exception:
            log.exception("Failed to write project action to runtime audit stream.")

        logChannelId = _toPositiveInt(getattr(config, "projectLogChannelId", 0))
        if logChannelId <= 0:
            return
        channel = self.bot.get_channel(logChannelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, logChannelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        embed = discord.Embed(
            title=f"Project {actionLabel.title()}",
            color=discord.Color.dark_blue(),
            timestamp=datetime.now(timezone.utc),
            description=(
                f"Project: `#{int(projectId)}`\n"
                f"Actor: <@{int(actorId)}>\n"
                f"Guild: `{int(guild.id)}`"
            ),
        )
        if details:
            lines = [f"- **{key}**: {value}" for key, value in details.items()]
            embed.add_field(name="Details", value=_clip("\n".join(lines), 1024), inline=False)
        await interactionRuntime.safeChannelSend(channel, embed=embed)

    async def _appendHistory(
        self,
        *,
        rowBefore: dict,
        actorId: int,
        action: str,
        toStatus: str,
        note: Optional[str] = None,
    ) -> None:
        await projectService.appendProjectHistory(
            projectId=_toPositiveInt(rowBefore.get("projectId")),
            guildId=_toPositiveInt(rowBefore.get("guildId")),
            actorId=int(actorId),
            action=action,
            fromStatus=str(rowBefore.get("status") or ""),
            toStatus=str(toStatus or ""),
            note=note,
        )

    async def _getProjectForGuild(self, guildId: int, projectId: int) -> Optional[dict]:
        row = await projectService.getProject(int(projectId))
        if not row:
            return None
        if _toPositiveInt(row.get("guildId")) != int(guildId):
            return None
        return row

    @projectGroup.command(name="create", description="Submit a new project idea.")
    @app_commands.describe(
        title="Project title.",
        idea="What are you planning to build?",
        requestedPoints="How many points are you requesting?",
        createThread="Create a project thread automatically.",
    )
    @app_commands.rename(
        requestedPoints="requested-points",
        createThread="create-thread",
    )
    async def projectCreate(
        self,
        interaction: discord.Interaction,
        title: str,
        idea: str,
        requestedPoints: app_commands.Range[int, 0, 100000],
        createThread: bool = True,
    ) -> None:
        if not interaction.guild or not interaction.channel or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)

        channelId = _toPositiveInt(getattr(interaction.channel, "id", 0))
        projectId = await projectService.createProject(
            guildId=int(interaction.guild.id),
            channelId=channelId,
            creatorId=int(interaction.user.id),
            title=str(title or "").strip(),
            idea=str(idea or "").strip(),
            requestedPoints=max(0, int(requestedPoints or 0)),
        )
        row = await projectService.getProject(projectId)
        if row is None:
            return await self._safeEphemeral(interaction, "Failed to create project record.")
        await projectWorkflowBridge.syncProjectWorkflow(
            row,
            stateKey="pending-approval",
            actorId=int(interaction.user.id),
            note="Project submitted for HOD approval.",
            eventType="CREATED",
        )
        row = await projectService.getProject(projectId) or row

        reviewMessage: Optional[discord.Message] = None
        channel = interaction.channel
        hodRoleIds = self._projectHodRoleIds()
        hodMentions = " ".join(f"<@&{roleId}>" for roleId in hodRoleIds if roleId > 0)
        createMessageContent = (
            f"{hodMentions}\nNew project pending HOD approval."
            if hodMentions
            else "New project pending HOD approval."
        )
        reviewMessage = await interactionRuntime.safeChannelSend(
            channel,
            content=createMessageContent,
            embed=await self._buildProjectEmbed(row),
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
        if reviewMessage is not None:
            await projectService.setProjectReviewMessage(
                projectId=projectId,
                reviewChannelId=_toPositiveInt(getattr(channel, "id", 0)),
                reviewMessageId=int(reviewMessage.id),
            )
            routedRow = await projectService.getProject(projectId)
            if routedRow is not None:
                await projectWorkflowBridge.syncProjectWorkflow(
                    routedRow,
                    stateKey="pending-approval",
                    actorId=None,
                    note="Routed for review.",
                    eventType="ROUTED_FOR_REVIEW",
                    allowNoopEvent=True,
                )
                await self._syncProjectMessage(routedRow)

        createdThread: Optional[discord.Thread] = None
        if (
            createThread
            and bool(getattr(config, "projectAutoCreateThread", True))
            and reviewMessage is not None
            and isinstance(channel, discord.TextChannel)
        ):
            slug = _THREAD_NAME_SANITIZER.sub(
                "-",
                str(title or "").strip().lower(),
            ).strip("-")
            if not slug:
                slug = f"project-{projectId}"
            threadName = f"{slug[:70]}-{projectId}"
            try:
                createdThread = await taskBudgeter.runDiscord(
                    lambda: reviewMessage.create_thread(
                        name=threadName[:100],
                        auto_archive_duration=10080,
                    )
                )
            except (discord.Forbidden, discord.HTTPException):
                createdThread = None
            if createdThread is not None:
                await projectService.setProjectThreadId(projectId, int(createdThread.id))
                await interactionRuntime.safeChannelSend(
                    createdThread,
                    content=f"Project thread opened for **#{projectId}** by <@{int(interaction.user.id)}>.",
                )

        await projectService.appendProjectHistory(
            projectId=projectId,
            guildId=int(interaction.guild.id),
            actorId=int(interaction.user.id),
            action="CREATED",
            fromStatus=None,
            toStatus="PENDING_APPROVAL",
            note=None,
        )
        row = await projectService.getProject(projectId) or row
        await self._logProjectAction(
            guild=interaction.guild,
            actorId=int(interaction.user.id),
            projectId=projectId,
            action="created",
            details={
                "status": "PENDING_APPROVAL",
                "requestedPoints": max(0, int(requestedPoints or 0)),
                "threadCreated": "yes" if createdThread is not None else "no",
            },
        )

        messageUrl = _projectMessageUrl(row)
        threadText = f"\nThread: <#{int(createdThread.id)}>" if createdThread is not None else ""
        linkText = f"\nReview Message: {messageUrl}" if messageUrl else ""
        await self._safeEphemeral(
            interaction,
            f"Project created: `#{projectId}`{linkText}{threadText}",
        )

    @projectGroup.command(name="approve", description="HOD approval for a pending project.")
    @app_commands.describe(
        projectId="Project ID to approve.",
        note="Optional approval note.",
    )
    @app_commands.rename(projectId="project-id")
    async def projectApprove(
        self,
        interaction: discord.Interaction,
        projectId: int,
        note: Optional[str] = None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return
        if not self._canHodApprove(interaction.user):
            return await self._safeEphemeral(interaction, "Only Head of Department staff can approve projects.")

        row = await self._getProjectForGuild(interaction.guild.id, int(projectId))
        if row is None:
            return await self._safeEphemeral(interaction, "Project not found for this server.")
        currentStatus = str(row.get("status") or "").strip().upper()
        if currentStatus != "PENDING_APPROVAL":
            return await self._safeEphemeral(
                interaction,
                f"Project `#{projectId}` is not pending approval (current: {_statusLabel(currentStatus)}).",
            )

        await projectService.markProjectApproved(
            projectId=int(projectId),
            reviewerId=int(interaction.user.id),
            note=note,
        )
        await self._appendHistory(
            rowBefore=row,
            actorId=int(interaction.user.id),
            action="APPROVED",
            toStatus="APPROVED",
            note=note,
        )
        updated = await projectService.getProject(int(projectId)) or row
        await projectWorkflowBridge.syncProjectWorkflow(
            updated,
            stateKey="approved",
            actorId=int(interaction.user.id),
            note=note or "Project approved by HOD.",
            eventType="APPROVED",
        )
        updated = await projectService.getProject(int(projectId)) or updated
        await self._syncProjectMessage(updated)
        await self._postThreadUpdate(
            updated,
            f":white_check_mark: Project `#{projectId}` approved by <@{int(interaction.user.id)}>.",
        )
        await self._logProjectAction(
            guild=interaction.guild,
            actorId=int(interaction.user.id),
            projectId=int(projectId),
            action="approved",
            details={"from": currentStatus, "to": "APPROVED", "note": str(note or "").strip() or "(none)"},
        )
        await self._safeEphemeral(interaction, f"Project `#{projectId}` approved.")

    @projectGroup.command(name="deny", description="Reject a project.")
    @app_commands.describe(
        projectId="Project ID to deny.",
        note="Reason for denial.",
    )
    @app_commands.rename(projectId="project-id")
    async def projectDeny(
        self,
        interaction: discord.Interaction,
        projectId: int,
        note: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return
        if not self._canHodApprove(interaction.user):
            return await self._safeEphemeral(interaction, "Only Head of Department staff can deny projects.")

        row = await self._getProjectForGuild(interaction.guild.id, int(projectId))
        if row is None:
            return await self._safeEphemeral(interaction, "Project not found for this server.")
        currentStatus = str(row.get("status") or "").strip().upper()
        if currentStatus in {"DENIED", "FINALIZED"}:
            return await self._safeEphemeral(
                interaction,
                f"Project `#{projectId}` is already closed ({_statusLabel(currentStatus)}).",
            )

        await projectService.markProjectDenied(
            projectId=int(projectId),
            reviewerId=int(interaction.user.id),
            note=str(note or "").strip(),
        )
        await self._appendHistory(
            rowBefore=row,
            actorId=int(interaction.user.id),
            action="DENIED",
            toStatus="DENIED",
            note=note,
        )
        updated = await projectService.getProject(int(projectId)) or row
        await projectWorkflowBridge.syncProjectWorkflow(
            updated,
            stateKey="denied",
            actorId=int(interaction.user.id),
            note=str(note or "").strip() or "Project denied.",
            eventType="DENIED",
        )
        updated = await projectService.getProject(int(projectId)) or updated
        await self._syncProjectMessage(updated)
        await self._postThreadUpdate(
            updated,
            (
                f":x: Project `#{projectId}` denied by <@{int(interaction.user.id)}>.\n"
                f"Reason: {_clip(note, 300)}"
            ),
        )
        await self._logProjectAction(
            guild=interaction.guild,
            actorId=int(interaction.user.id),
            projectId=int(projectId),
            action="denied",
            details={"from": currentStatus, "to": "DENIED", "note": _clip(note, 300)},
        )
        await self._safeEphemeral(interaction, f"Project `#{projectId}` denied.")

    @projectGroup.command(name="submit", description="Submit finished project work.")
    @app_commands.describe(
        projectId="Project ID to submit.",
        summary="Summary of what was completed.",
        proof="Proof link or reference (optional).",
    )
    @app_commands.rename(projectId="project-id")
    async def projectSubmit(
        self,
        interaction: discord.Interaction,
        projectId: int,
        summary: str,
        proof: Optional[str] = None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return

        row = await self._getProjectForGuild(interaction.guild.id, int(projectId))
        if row is None:
            return await self._safeEphemeral(interaction, "Project not found for this server.")
        creatorId = _toPositiveInt(row.get("creatorId"))
        isStaffOverride = interaction.user.guild_permissions.administrator or self._canHodApprove(interaction.user)
        if int(interaction.user.id) != creatorId and not isStaffOverride:
            return await self._safeEphemeral(interaction, "Only the project creator can submit this project.")
        currentStatus = str(row.get("status") or "").strip().upper()
        if currentStatus != "APPROVED":
            return await self._safeEphemeral(
                interaction,
                f"Project `#{projectId}` is not approved yet (current: {_statusLabel(currentStatus)}).",
            )

        await projectService.markProjectSubmitted(
            projectId=int(projectId),
            summary=str(summary or "").strip(),
            proof=str(proof or "").strip() or None,
        )
        await self._appendHistory(
            rowBefore=row,
            actorId=int(interaction.user.id),
            action="SUBMITTED",
            toStatus="SUBMITTED",
            note=proof,
        )
        updated = await projectService.getProject(int(projectId)) or row
        await projectWorkflowBridge.syncProjectWorkflow(
            updated,
            stateKey="submitted",
            actorId=int(interaction.user.id),
            note="Project work submitted for finalization.",
            eventType="SUBMITTED",
        )
        updated = await projectService.getProject(int(projectId)) or updated
        await self._syncProjectMessage(updated)
        adMentions = " ".join(
            f"<@&{roleId}>"
            for roleId in self._projectAssistantDirectorRoleIds()
            if roleId > 0
        )
        threadPrefix = f"{adMentions}\n" if adMentions else ""
        await self._postThreadUpdate(
            updated,
            (
                f"{threadPrefix}:inbox_tray: Project `#{projectId}` submitted by <@{int(interaction.user.id)}>.\n"
                f"Summary: {_clip(summary, 300)}"
            ),
        )
        await self._logProjectAction(
            guild=interaction.guild,
            actorId=int(interaction.user.id),
            projectId=int(projectId),
            action="submitted",
            details={"from": currentStatus, "to": "SUBMITTED", "proof": str(proof or "").strip() or "(none)"},
        )
        await self._safeEphemeral(interaction, f"Project `#{projectId}` submitted for finalization.")

    @projectGroup.command(name="finalize", description="Assistant Director finalizes project points.")
    @app_commands.describe(
        projectId="Project ID to finalize.",
        awardedPoints="Final points granted.",
        note="Optional finalization note.",
    )
    @app_commands.rename(
        projectId="project-id",
        awardedPoints="awarded-points",
    )
    async def projectFinalize(
        self,
        interaction: discord.Interaction,
        projectId: int,
        awardedPoints: app_commands.Range[int, 0, 100000],
        note: Optional[str] = None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return
        if not self._canFinalize(interaction.user):
            return await self._safeEphemeral(
                interaction,
                "Only Assistant Director staff can finalize projects.",
            )

        row = await self._getProjectForGuild(interaction.guild.id, int(projectId))
        if row is None:
            return await self._safeEphemeral(interaction, "Project not found for this server.")
        currentStatus = str(row.get("status") or "").strip().upper()
        if currentStatus != "SUBMITTED":
            return await self._safeEphemeral(
                interaction,
                f"Project `#{projectId}` must be submitted before finalization (current: {_statusLabel(currentStatus)}).",
            )

        await projectService.markProjectFinalized(
            projectId=int(projectId),
            reviewerId=int(interaction.user.id),
            awardedPoints=max(0, int(awardedPoints or 0)),
            note=note,
        )
        await self._appendHistory(
            rowBefore=row,
            actorId=int(interaction.user.id),
            action="FINALIZED",
            toStatus="FINALIZED",
            note=note,
        )
        updated = await projectService.getProject(int(projectId)) or row
        await projectWorkflowBridge.syncProjectWorkflow(
            updated,
            stateKey="finalized",
            actorId=int(interaction.user.id),
            note=note or "Project finalized.",
            eventType="FINALIZED",
        )
        updated = await projectService.getProject(int(projectId)) or updated
        await self._syncProjectMessage(updated)
        creatorId = _toPositiveInt(updated.get("creatorId"))
        await self._postThreadUpdate(
            updated,
            (
                f":trophy: Project `#{projectId}` finalized by <@{int(interaction.user.id)}>.\n"
                f"Awarded Points: **{max(0, int(awardedPoints or 0))}**\n"
                f"Creator: <@{creatorId}>"
            ),
        )
        await self._logProjectAction(
            guild=interaction.guild,
            actorId=int(interaction.user.id),
            projectId=int(projectId),
            action="finalized",
            details={
                "from": currentStatus,
                "to": "FINALIZED",
                "awardedPoints": max(0, int(awardedPoints or 0)),
                "note": str(note or "").strip() or "(none)",
            },
        )
        await self._safeEphemeral(interaction, f"Project `#{projectId}` finalized.")

    @projectGroup.command(name="list", description="View active or historical projects.")
    @app_commands.describe(
        status="Filter by status.",
        mineOnly="Only show your projects.",
    )
    @app_commands.rename(mineOnly="mine-only")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Active", value="active"),
            app_commands.Choice(name="Pending", value="pending"),
            app_commands.Choice(name="Approved", value="approved"),
            app_commands.Choice(name="Submitted", value="submitted"),
            app_commands.Choice(name="Finalized", value="finalized"),
            app_commands.Choice(name="Denied", value="denied"),
            app_commands.Choice(name="All", value="all"),
        ]
    )
    async def projectList(
        self,
        interaction: discord.Interaction,
        status: Optional[app_commands.Choice[str]] = None,
        mineOnly: bool = False,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return

        statusKey = str(getattr(status, "value", "active") or "active").strip().lower()
        statusMap = {
            "active": list(_PROJECT_ACTIVE_STATUSES),
            "pending": ["PENDING_APPROVAL"],
            "approved": ["APPROVED"],
            "submitted": ["SUBMITTED"],
            "finalized": ["FINALIZED"],
            "denied": ["DENIED"],
            "all": [],
        }
        statuses = statusMap.get(statusKey, list(_PROJECT_ACTIVE_STATUSES))
        rows = await projectService.listProjects(
            guildId=int(interaction.guild.id),
            statuses=statuses,
            creatorId=int(interaction.user.id) if mineOnly else None,
            limit=30,
        )
        titleSuffix = "Mine" if mineOnly else "Server"
        embed = discord.Embed(
            title=f"Projects - {titleSuffix}",
            color=discord.Color.blurple(),
            description=f"Filter: **{statusKey}**",
        )
        if not rows:
            embed.add_field(name="Projects", value="(none)", inline=False)
        else:
            lines: list[str] = []
            for row in rows[:25]:
                listedProjectId = _toPositiveInt(row.get("projectId"))
                creatorId = _toPositiveInt(row.get("creatorId"))
                requested = max(0, _toPositiveInt(row.get("requestedPoints")))
                statusText = _statusLabel(row.get("status"))
                title = _clip(row.get("title"), 40) or f"Project {listedProjectId}"
                line = (
                    f"`#{listedProjectId}` **{title}**\n"
                    f"Status: {statusText} | Creator: <@{creatorId}> | Requested: {requested}"
                )
                url = _projectMessageUrl(row)
                if url:
                    line += f" | [open]({url})"
                lines.append(line)
            embed.add_field(name=f"Projects ({len(rows)})", value=_clip("\n\n".join(lines), 4000), inline=False)
        await interactionRuntime.safeInteractionReply(interaction, embed=embed, ephemeral=True)

    @projectGroup.command(name="status", description="Check project progress and history.")
    @app_commands.describe(projectId="Project ID to inspect.")
    @app_commands.rename(projectId="project-id")
    async def projectStatus(
        self,
        interaction: discord.Interaction,
        projectId: int,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used in a server.")
        if not await self._ensureProjectGuildAllowed(interaction):
            return

        row = await self._getProjectForGuild(interaction.guild.id, int(projectId))
        if row is None:
            return await self._safeEphemeral(interaction, "Project not found for this server.")
        historyRows = await projectService.listProjectHistory(int(projectId), limit=20)
        embed = await self._buildProjectEmbed(row)
        if historyRows:
            historyLines: list[str] = []
            for entry in reversed(historyRows[-10:]):
                actorId = _toPositiveInt(entry.get("actorId"))
                actorText = f"<@{actorId}>" if actorId > 0 else "system"
                action = str(entry.get("action") or "UPDATE").strip().upper()
                fromStatus = str(entry.get("fromStatus") or "").strip().upper()
                toStatus = str(entry.get("toStatus") or "").strip().upper()
                transition = ""
                if fromStatus or toStatus:
                    transition = f" ({fromStatus or '?'} -> {toStatus or '?'})"
                note = str(entry.get("note") or "").strip()
                noteText = f" - {_clip(note, 120)}" if note else ""
                historyLines.append(
                    f"{_discordTimestamp(entry.get('createdAt'), 'R')} `{action}` by {actorText}{transition}{noteText}"
                )
            embed.add_field(name="History", value=_clip("\n".join(historyLines), 1024), inline=False)
        await interactionRuntime.safeInteractionReply(interaction, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(
        ProjectCog(bot),
        guilds=runtimeCommandScopes.getGuildAndTestGuildObjects(1436862009265229886),
    )

