import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from db.sqlite import fetchOne, fetchAll, execute
from features.staff.cohost import recordCohosts, selectCohosts
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions

_repoRoot = Path(__file__).resolve().parents[2]
_defaultCohostLogPath = _repoRoot / "runtime" / "data" / "cohostLogs.csv"
_legacyCohostLogPath = _repoRoot / "logs.csv"
_configuredCohostLogPath = os.getenv("COHOST_LOG_PATH", "").strip()
if _configuredCohostLogPath:
    logPath = Path(_configuredCohostLogPath)
elif _defaultCohostLogPath.exists():
    logPath = _defaultCohostLogPath
elif _legacyCohostLogPath.exists():
    logPath = _legacyCohostLogPath
else:
    logPath = _defaultCohostLogPath
# Keep backwards compatibility with older deployments while preferring the
# newer runtime/data location.
logPath.parent.mkdir(parents=True, exist_ok=True)
log = logging.getLogger(__name__)
cohostEmoji = os.getenv("COHOST_EMOJI", "\u2705")
preferSro = os.getenv("COHOST_PREFER_SRO", "true").strip().lower() in {"1", "true", "yes"}
finishSelectionTimeoutSec = 30
supervisorRoleIdRaw = getattr(config, "cohostSupervisorRoleId", 0) or os.getenv("COHOST_SUPERVISOR_ROLE_ID")
try:
    supervisorRoleId = int(supervisorRoleIdRaw) if supervisorRoleIdRaw else 0
except (TypeError, ValueError):
    supervisorRoleId = 0
supervisorRoleName = (
    str(getattr(config, "cohostSupervisorRoleName", "") or "").strip()
    or os.getenv("COHOST_SUPERVISOR_ROLE_NAME", "supervisor eligible")
)

rankRoleNames = {
    "SRO": {"SRO"},
    "STA": {"STA"},
    "CRS": {"CRS"},
    "SU": {"SU"},
}
rankOrder = ["SRO", "STA", "CRS", "SU"]

eventSlots = {
    "solo": getattr(config, "cohostSlotsSolo", 2),
    "emergency": getattr(config, "cohostSlotsEmergency", 2),
    "turbine": getattr(config, "cohostSlotsTurbine", 2),
    "grid": getattr(config, "cohostSlotsGrid", 4),
    "shift": getattr(config, "cohostSlotsShift", 2),
}

eventChoices = [
    app_commands.Choice(name="Solo", value="solo"),
    app_commands.Choice(name="Turbine", value="turbine"),
    app_commands.Choice(name="Emergency", value="emergency"),
    app_commands.Choice(name="Grid", value="grid"),
    app_commands.Choice(name="Shift", value="shift"),
]


@dataclass
class CohostRequest:
    messageId: int
    guildId: int
    channelId: int
    hostId: int
    eventType: str
    collectMinutes: int
    status: str
    createdAt: datetime
    finishedAt: Optional[datetime] = None
    autoTask: Optional[asyncio.Task] = None


@dataclass(frozen=True)
class VolunteerEntry:
    userId: str
    rank: str


def _parseDbTime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _rankFromMember(member: discord.Member) -> Optional[str]:
    roleNames = {role.name.strip().upper() for role in member.roles}
    for rank in rankOrder:
        if any(name.upper() in roleNames for name in rankRoleNames.get(rank, set())):
            return rank
    return None


def _resolveSupervisorMention(guild: discord.Guild) -> str:
    if supervisorRoleId:
        return f"<@&{supervisorRoleId}>"
    for role in guild.roles:
        if role.name.strip().lower() == supervisorRoleName.strip().lower():
            return role.mention
    return f"@{supervisorRoleName}"


def _cohostAllowedRoleIds() -> set[int]:
    roleIds = getattr(config, "cohostAllowedRoleIds", None)
    if not roleIds:
        return set()
    normalized: set[int] = set()
    for value in roleIds:
        try:
            normalized.add(int(value))
        except (TypeError, ValueError):
            continue
    return normalized


class CohostView(discord.ui.View):
    def __init__(self, cog: "CohostCog"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger, row=0, custom_id="cohost:delete")
    async def deleteBtn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handleDelete(interaction)

    @discord.ui.button(label="Finish", style=discord.ButtonStyle.success, row=0, custom_id="cohost:finish")
    async def finishBtn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handleFinish(interaction)

    @discord.ui.button(emoji="\u2705", style=discord.ButtonStyle.success, row=1, custom_id="cohost:join")
    async def joinBtn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handleJoin(interaction)


class CohostCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.requests: dict[int, CohostRequest] = {}
        self._finalizeLocks: dict[int, asyncio.Lock] = {}

    @staticmethod
    def _summarizeException(exc: Exception) -> str:
        detail = " ".join(str(exc or "").strip().split())
        if not detail:
            return exc.__class__.__name__
        if len(detail) > 220:
            detail = detail[:217].rstrip() + "..."
        return f"{exc.__class__.__name__}: {detail}"

    async def cog_load(self) -> None:
        self.bot.add_view(CohostView(self))
        await self._restoreOpenRequests()

    @app_commands.command(name="cohost", description="Pick cohosts from a join list.")
    @app_commands.choices(event=eventChoices)
    @app_commands.describe(
        event="Event type for the cohost queue.",
        minutes="Minutes to collect volunteers before selecting.",
    )
    async def cohost(
        self,
        interaction: discord.Interaction,
        event: str,
        minutes: app_commands.Range[int, 1, 60],
    ) -> None:
        if not interaction.guild or not interaction.channel:
            await interaction.response.send_message("This command must be used in a server channel.")
            return

        slots = int(eventSlots.get(event, 2))
        if slots <= 0:
            await interaction.response.send_message(
                "Cohost slots are not configured for this event.",
                ephemeral=True,
            )
            return

        member = interaction.user
        if not isinstance(member, discord.Member) or not runtimePermissions.hasCohostPermission(member):
            await interaction.response.send_message(
                "You do not have permission to start a cohost request.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Creating cohost request...", ephemeral=True)

        eventType = event
        collectMinutes = minutes
        supervisorMention = _resolveSupervisorMention(interaction.guild)

        embed = discord.Embed(
            title=f"{eventType.title()} Co-host Request",
            description=f"Click {cohostEmoji} **Join** to volunteer.",
        )
        embed.add_field(name="Host", value=interaction.user.mention, inline=False)
        embed.add_field(name="Recent Volunteers (0)", value="None", inline=False)
        embed.add_field(name="Status", value="OPEN", inline=False)
        embed.set_footer(text=f"Open for {collectMinutes} minutes. Host can Finish early.")

        view = CohostView(self)
        allowedMentions = discord.AllowedMentions(roles=True, users=True)
        message = await interactionRuntime.safeChannelSend(
            interaction.channel,
            content=supervisorMention,
            embed=embed,
            view=view,
            allowed_mentions=allowedMentions,
        )
        if message is None:
            return await interaction.followup.send(
                "I couldn't post the cohost request message in this channel.",
                ephemeral=True,
            )

        createdAt = datetime.now()
        await execute(
            """
            INSERT OR REPLACE INTO cohost_requests
                (messageId, guildId, channelId, hostId, eventType, collectMinutes, status, createdAt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                interaction.guild.id,
                interaction.channel.id,
                interaction.user.id,
                eventType,
                collectMinutes,
                "OPEN",
                createdAt.isoformat(sep=" ", timespec="seconds"),
            ),
        )

        request = CohostRequest(
            messageId=message.id,
            guildId=interaction.guild.id,
            channelId=interaction.channel.id,
            hostId=interaction.user.id,
            eventType=eventType,
            collectMinutes=collectMinutes,
            status="OPEN",
            createdAt=createdAt,
        )
        self.requests[message.id] = request
        self._scheduleAutoFinish(request)
        await interaction.edit_original_response(
            content=f"{eventType.title()} cohost request created."
        )

    async def handleDelete(self, interaction: discord.Interaction) -> None:
        request = await self._getRequestFromInteraction(interaction)
        if not request:
            return
        if interaction.user.id != request.hostId:
            await interaction.response.send_message("Only the host can delete.", ephemeral=True)
            return

        await self._setStatus(request, "DELETED")
        if request.autoTask:
            request.autoTask.cancel()
        await interaction.response.send_message("Cohost request deleted.", ephemeral=True)
        await self._updateMessage(request, interaction.message)
        self.requests.pop(request.messageId, None)
        self._finalizeLocks.pop(request.messageId, None)

    async def handleFinish(self, interaction: discord.Interaction) -> None:
        request = await self._getRequestFromInteraction(interaction)
        if not request:
            return
        if interaction.user.id != request.hostId:
            await interaction.response.send_message("Only the host can finish.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with asyncio.timeout(finishSelectionTimeoutSec):
                resultMessage = await self._finalizeRequest(request.messageId)
            await interaction.edit_original_response(content=resultMessage)
        except TimeoutError:
            log.warning("Cohost finalize timed out for messageId=%d", request.messageId)
            await interaction.edit_original_response(
                content="Cohost selection timed out. The request is still open, so you can try Finish again."
            )
        except Exception as exc:
            log.exception("Manual cohost finalize failed for messageId=%d", request.messageId)
            await interaction.edit_original_response(
                content=(
                    "I hit an error while finishing this cohost request.\n"
                    f"`{self._summarizeException(exc)}`"
                )
            )

    async def handleJoin(self, interaction: discord.Interaction) -> None:
        request = await self._getRequestFromInteraction(interaction)
        if not request:
            return
        if request.status != "OPEN":
            await interaction.response.send_message("This request is closed.", ephemeral=True)
            return
        if interaction.user.bot:
            await interaction.response.send_message("Bots cannot join.", ephemeral=True)
            return

        existing = await fetchOne(
            "SELECT 1 FROM cohost_volunteers WHERE messageId = ? AND userId = ?",
            (request.messageId, interaction.user.id),
        )
        if existing:
            await interaction.response.send_message("You are already in the pool.", ephemeral=True)
            return

        memberRank = ""
        if isinstance(interaction.user, discord.Member):
            memberRank = _rankFromMember(interaction.user) or ""

        await execute(
            "INSERT OR IGNORE INTO cohost_volunteers (messageId, userId, rank) VALUES (?, ?, ?)",
            (request.messageId, interaction.user.id, memberRank),
        )
        await interaction.response.send_message("You have been added to the pool.", ephemeral=True)
        await self._updateMessage(request, interaction.message)

    async def _restoreOpenRequests(self) -> None:
        rows = await fetchAll("SELECT * FROM cohost_requests WHERE status = 'OPEN'")
        for row in rows:
            request = self._requestFromRow(row)
            if not request:
                continue
            self.requests[request.messageId] = request
            self._scheduleAutoFinish(request)
            await self._updateMessage(request)

    def _requestFromRow(self, row: dict) -> Optional[CohostRequest]:
        createdAt = _parseDbTime(row.get("createdAt"))
        if not createdAt:
            createdAt = datetime.now()
        finishedAt = _parseDbTime(row.get("finishedAt"))
        return CohostRequest(
            messageId=row["messageId"],
            guildId=row["guildId"],
            channelId=row["channelId"],
            hostId=row["hostId"],
            eventType=row["eventType"],
            collectMinutes=row["collectMinutes"],
            status=row["status"],
            createdAt=createdAt,
            finishedAt=finishedAt,
        )

    async def _getRequestFromInteraction(self, interaction: discord.Interaction) -> Optional[CohostRequest]:
        if not interaction.message:
            await interaction.response.send_message("This request is no longer active.", ephemeral=True)
            return None
        request = await self._getRequest(interaction.message.id)
        if not request:
            await interaction.response.send_message("This request is no longer active.", ephemeral=True)
            return None
        return request

    async def _getRequest(self, messageId: int) -> Optional[CohostRequest]:
        if messageId in self.requests:
            return self.requests[messageId]
        row = await fetchOne("SELECT * FROM cohost_requests WHERE messageId = ?", (messageId,))
        if not row:
            return None
        request = self._requestFromRow(row)
        self.requests[messageId] = request
        return request

    def _scheduleAutoFinish(self, request: CohostRequest) -> None:
        endTime = request.createdAt + timedelta(minutes=request.collectMinutes)
        delay = max(0, (endTime - datetime.now()).total_seconds())
        request.autoTask = asyncio.create_task(self._autoFinish(request.messageId, delay))

    async def _autoFinish(self, messageId: int, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        request = await self._getRequest(messageId)
        if not request or request.status != "OPEN":
            return
        try:
            await self._finalizeRequest(messageId)
        except Exception:
            log.exception("Auto-finish failed for cohost request messageId=%d", messageId)

    async def _setStatus(self, request: CohostRequest, status: str) -> None:
        finishedAt = None
        if status != "OPEN":
            finishedAt = datetime.now().isoformat(sep=" ", timespec="seconds")
        await execute(
            "UPDATE cohost_requests SET status = ?, finishedAt = ? WHERE messageId = ?",
            (status, finishedAt, request.messageId),
        )
        request.status = status
        request.finishedAt = _parseDbTime(finishedAt) if finishedAt else None
        if status != "OPEN" and request.autoTask and not request.autoTask.done():
            request.autoTask.cancel()

    async def _safeChannelSend(
        self,
        channel: Optional[discord.abc.Messageable],
        content: str,
    ) -> bool:
        if channel is None:
            return False
        sentMessage = await interactionRuntime.safeChannelSend(channel, content=content)
        return sentMessage is not None

    def _formatSelectionMessage(
        self,
        request: CohostRequest,
        selectedUserIds: list[str],
    ) -> str:
        if not selectedUserIds:
            return f"No eligible cohost found for {request.eventType.title()}."
        mentions = ", ".join(f"<@{userId}>" for userId in selectedUserIds)
        return f"Cohost selection finished for {request.eventType.title()}: {mentions}"

    async def _loadVolunteers(self, messageId: int) -> list[VolunteerEntry]:
        volunteerRows = await fetchAll(
            "SELECT userId, rank FROM cohost_volunteers WHERE messageId = ? ORDER BY joinTime",
            (messageId,),
        )
        volunteers: list[VolunteerEntry] = []
        for row in volunteerRows:
            userId = str(row.get("userId") or "").strip()
            if not userId:
                continue
            rank = str(row.get("rank") or "").strip().upper()
            volunteers.append(VolunteerEntry(userId=userId, rank=rank))
        return volunteers

    async def _finalizeRequest(self, messageId: int) -> str:
        lock = self._finalizeLocks.setdefault(messageId, asyncio.Lock())
        try:
            async with lock:
                # In-memory guard: prevents concurrent finalize flows from
                # processing the same request twice.
                request = await self._getRequest(messageId)
                if not request or request.status != "OPEN":
                    return "This cohost request is already closed."

                row = await fetchOne(
                    "SELECT status FROM cohost_requests WHERE messageId = ?",
                    (messageId,),
                )
                # DB re-check: covers race windows across restarts/processes.
                if not row or row["status"] != "OPEN":
                    return "This cohost request is already closed."

                message = await self._fetchMessage(request)
                channel = message.channel if message else None

                volunteers = await self._loadVolunteers(messageId)
                poolUserIds = [entry.userId for entry in volunteers]
                poolRanks = {
                    entry.userId: entry.rank
                    for entry in volunteers
                    if entry.rank
                }

                if not poolUserIds:
                    await self._setStatus(request, "FINISHED")
                    await self._updateMessage(request, message)
                    await self._safeChannelSend(
                        channel,
                        f"No volunteers collected for {request.eventType.title()}.",
                    )
                    return f"No volunteers collected for {request.eventType.title()}."

                asOf = datetime.now()
                selections = await asyncio.to_thread(
                    selectCohosts,
                    logPath,
                    request.eventType,
                    poolUserIds=poolUserIds,
                    poolRanks=poolRanks,
                    asOf=asOf,
                    saveFirstAttempts=True,
                    preferSro=preferSro,
                    slots=max(0, int(eventSlots.get(request.eventType, 2))),
                )

                if not selections:
                    await self._setStatus(request, "FINISHED")
                    await self._updateMessage(request, message)
                    await self._safeChannelSend(channel, "No eligible cohost found.")
                    return f"No eligible cohost found for {request.eventType.title()}."

                ranks = {selection.userId: selection.rank for selection in selections if selection.rank}
                selectedUserIds = [selection.userId for selection in selections]
                await asyncio.to_thread(
                    recordCohosts,
                    logPath,
                    selectedUserIds,
                    request.eventType,
                    date=asOf,
                    ranks=ranks or None,
                )

                await self._setStatus(request, "FINISHED")
                await self._updateMessage(request, message)
                lines = [f"<@{selection.userId}>" for selection in selections]
                await self._safeChannelSend(
                    channel,
                    f"Selected cohosts for {request.eventType.title()}:\n" + "\n".join(lines),
                )
                self.requests.pop(messageId, None)
                return self._formatSelectionMessage(request, selectedUserIds)
        finally:
            cachedRequest = self.requests.get(messageId)
            if cachedRequest and cachedRequest.status != "OPEN":
                self.requests.pop(messageId, None)
            current = self._finalizeLocks.get(messageId)
            if current is lock and not lock.locked():
                self._finalizeLocks.pop(messageId, None)

    async def _fetchMessage(self, request: CohostRequest) -> Optional[discord.Message]:
        channel = self.bot.get_channel(request.channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, request.channelId)
        if not hasattr(channel, "fetch_message"):
            return None
        return await interactionRuntime.safeFetchMessage(channel, request.messageId)

    async def _updateMessage(self, request: CohostRequest, message: Optional[discord.Message] = None) -> None:
        message = message or await self._fetchMessage(request)
        if message is None:
            return

        volunteerCountRow = await fetchOne(
            "SELECT COUNT(*) AS countValue FROM cohost_volunteers WHERE messageId = ?",
            (request.messageId,),
        )
        volunteerCount = int(volunteerCountRow.get("countValue") or 0) if volunteerCountRow else 0
        recentVolunteerRows = await fetchAll(
            "SELECT userId FROM cohost_volunteers WHERE messageId = ? ORDER BY joinTime DESC LIMIT 10",
            (request.messageId,),
        )
        volunteers = [f"<@{row['userId']}>" for row in recentVolunteerRows]
        if volunteers:
            displayLines = volunteers
            remainingCount = max(0, volunteerCount - len(volunteers))
            if remainingCount > 0:
                displayLines.append(f"Plus {remainingCount} earlier volunteer(s).")
            display = "\n".join(displayLines)
        else:
            display = "None"

        embed = discord.Embed(
            title=f"{request.eventType.title()} Co-host Request",
            description=f"Click {cohostEmoji} **Join** to volunteer.",
        )
        embed.add_field(name="Host", value=f"<@{request.hostId}>", inline=False)
        embed.add_field(name=f"Recent Volunteers ({volunteerCount})", value=display, inline=False)
        embed.add_field(name="Status", value=request.status, inline=False)
        embed.set_footer(text=f"Open for {request.collectMinutes} minutes. Host can Finish early.")

        view = CohostView(self)
        if request.status != "OPEN":
            for child in view.children:
                child.disabled = True
        await interactionRuntime.safeMessageEdit(
            message,
            content=_resolveSupervisorMention(message.guild),
            embed=embed,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(CohostCog(bot))

