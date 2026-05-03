from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.staff.divisionClockin import service as divisionClockinService
from features.staff.sessions import service as sessionService
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions

log = logging.getLogger(__name__)


def _allowedRoleIds() -> set[int]:
    roleIds = getattr(config, "divisionClockinAllowedRoleIds", []) or []
    out: set[int] = set()
    for value in roleIds:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            out.add(parsed)
    return out


def _statusLabel(rawStatus: str) -> str:
    normalized = str(rawStatus or "").strip().upper()
    if normalized == "OPEN":
        return "Open"
    if normalized == "FINISHED":
        return "Finished"
    if normalized == "CANCELED":
        return "Canceled"
    return normalized or "Unknown"


class DivisionClockinView(discord.ui.View):
    def __init__(self, cog: "DivisionClockinCog", sessionId: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.sessionId = int(sessionId)
        self.clockinBtn.custom_id = f"division-clockin:join:{self.sessionId}"
        self.closeBtn.custom_id = f"division-clockin:close:{self.sessionId}"

    @discord.ui.button(label="Clock In", style=discord.ButtonStyle.success, row=0)
    async def clockinBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleJoinButton(interaction, sessionId=self.sessionId)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, row=0)
    async def closeBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleCloseButton(interaction, sessionId=self.sessionId)


class DivisionClockinCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    def _canStart(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        allowed = _allowedRoleIds()
        if not allowed:
            return True
        return any(int(role.id) in allowed for role in member.roles)

    def _extractSubdepartment(self, session: dict) -> str:
        sessionType = str(session.get("sessionType") or "").strip().lower()
        subdepartment = divisionClockinService.extractDivisionClockinSubdepartment(sessionType)
        return subdepartment or "unknown"

    def _buildEmbed(
        self,
        session: dict,
        attendees: list[dict],
        *,
        subdepartment: str,
    ) -> discord.Embed:
        hostId = int(session.get("hostId") or 0)
        status = _statusLabel(str(session.get("status") or "OPEN"))
        title = f"Division Clock-in - {subdepartment.upper()}"
        embed = discord.Embed(
            title=title,
            description="Skeleton flow active. Sheet mapping/writes will be wired next.",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Host", value=f"<@{hostId}>" if hostId > 0 else "Unknown", inline=True)
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Attendees", value=str(len(attendees)), inline=True)
        if attendees:
            lines = [f"{index + 1}. <@{int(row.get('userId') or 0)}>" for index, row in enumerate(attendees[:25])]
            if len(attendees) > 25:
                lines.append(f"...and {len(attendees) - 25} more")
            embed.add_field(name="Clocked In Users", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="Clocked In Users", value="(none)", inline=False)
        return embed

    async def _resolveSessionMessage(self, session: dict) -> Optional[discord.Message]:
        channelId = int(session.get("channelId") or 0)
        messageId = int(session.get("messageId") or 0)
        if channelId <= 0 or messageId <= 0:
            return None

        channel = self.bot.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        return await interactionRuntime.safeFetchMessage(channel, messageId)

    async def _refreshSessionMessage(self, sessionId: int) -> None:
        session = await sessionService.getSession(int(sessionId))
        if not session:
            return
        message = await self._resolveSessionMessage(session)
        if message is None:
            return
        attendees = await sessionService.getAttendees(int(sessionId))
        subdepartment = self._extractSubdepartment(session)
        view = DivisionClockinView(self, int(sessionId))
        if str(session.get("status") or "").upper() != "OPEN":
            for child in view.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
        await interactionRuntime.safeMessageEdit(
            message,
            embed=self._buildEmbed(session, attendees, subdepartment=subdepartment),
            view=view,
        )

    async def cog_load(self) -> None:
        try:
            sessions = await sessionService.getSessionsByStatus(["OPEN"])
        except Exception:
            log.exception("Failed to restore division clock-in views.")
            return

        restored = 0
        for session in sessions:
            sessionType = str(session.get("sessionType") or "").strip().lower()
            if divisionClockinService.extractDivisionClockinSubdepartment(sessionType) is None:
                continue
            messageId = int(session.get("messageId") or 0)
            sessionId = int(session.get("sessionId") or 0)
            if messageId <= 0 or sessionId <= 0:
                continue
            self.bot.add_view(DivisionClockinView(self, sessionId), message_id=messageId)
            restored += 1
        log.info("Division clock-in persistent views restored: %d", restored)

    async def handleJoinButton(self, interaction: discord.Interaction, *, sessionId: int) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This action can only be used inside a server channel.")

        session = await sessionService.getSession(int(sessionId))
        if not session:
            return await self._safeEphemeral(interaction, "Clock-in session not found.")
        if str(session.get("status") or "").upper() != "OPEN":
            return await self._safeEphemeral(interaction, "This clock-in is no longer open.")

        attendee = await sessionService.getAttendee(int(sessionId), int(interaction.user.id))
        if attendee:
            return await self._safeEphemeral(interaction, "You are already clocked in.")

        await sessionService.addAttendee(int(sessionId), int(interaction.user.id))
        await self._refreshSessionMessage(int(sessionId))
        await self._safeEphemeral(interaction, "You are now clocked in.")

    async def handleCloseButton(self, interaction: discord.Interaction, *, sessionId: int) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This action can only be used inside a server channel.")

        session = await sessionService.getSession(int(sessionId))
        if not session:
            return await self._safeEphemeral(interaction, "Clock-in session not found.")
        if int(session.get("hostId") or 0) != int(interaction.user.id):
            return await self._safeEphemeral(interaction, "Only the host may close this clock-in.")
        if str(session.get("status") or "").upper() != "OPEN":
            return await self._safeEphemeral(interaction, "This clock-in is already closed.")

        await sessionService.setStatus(int(sessionId), "FINISHED")
        attendees = await sessionService.getAttendees(int(sessionId))
        subdepartment = self._extractSubdepartment(session)
        syncResult = await divisionClockinService.syncDivisionClockinSkeleton(
            int(sessionId),
            subdepartment,
            len(attendees),
        )
        await self._refreshSessionMessage(int(sessionId))
        mappedText = "mapped" if bool(syncResult.get("mapped")) else "not mapped yet"
        await self._safeEphemeral(
            interaction,
            f"Division clock-in closed. Skeleton sheet sync ran ({mappedText}).",
        )

    @app_commands.command(name="division-clockin", description="Start a subdepartment clock-in (skeleton).")
    @app_commands.describe(
        subdepartment="Subdepartment key (for future sheet mapping).",
    )
    async def divisionClockin(self, interaction: discord.Interaction, subdepartment: str) -> None:
        if not interaction.guild or not interaction.channel or not isinstance(interaction.user, discord.Member):
            return await self._safeEphemeral(interaction, "This command can only be used inside a server channel.")
        if not self._canStart(interaction.user):
            return await self._safeEphemeral(interaction, "You do not have permission to start division clock-ins.")

        normalizedSubdepartment = divisionClockinService.normalizeSubdepartmentKey(subdepartment)
        sessionType = divisionClockinService.buildDivisionClockinSessionType(normalizedSubdepartment)

        await self._safeEphemeral(interaction, "Creating division clock-in session...")

        channel = interaction.channel
        sessionId = await sessionService.createSession(
            guildId=int(interaction.guild.id),
            channelId=int(channel.id),
            messageId=0,
            sessionType=sessionType,
            hostId=int(interaction.user.id),
            password="",
        )
        session = await sessionService.getSession(int(sessionId))
        if session is None:
            return await self._safeEphemeral(interaction, "Failed to create the division clock-in session.")

        attendees = await sessionService.getAttendees(int(sessionId))
        view = DivisionClockinView(self, int(sessionId))
        message = await interactionRuntime.safeChannelSend(
            channel,
            embed=self._buildEmbed(session, attendees, subdepartment=normalizedSubdepartment),
            view=view,
        )
        if message is None:
            await sessionService.setStatus(int(sessionId), "CANCELED")
            return await self._safeEphemeral(interaction, "I could not post the division clock-in message.")

        await sessionService.setSessionMessageId(int(sessionId), int(message.id))
        self.bot.add_view(view, message_id=message.id)

        mapping = divisionClockinService.getDivisionClockinSheetMapping(normalizedSubdepartment)
        mappingText = "configured" if mapping else "not configured yet"
        await self._safeEphemeral(
            interaction,
            (
                f"Division clock-in created for `{normalizedSubdepartment}`.\n"
                f"Sheet mapping: {mappingText} (skeleton mode)."
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DivisionClockinCog(bot))

