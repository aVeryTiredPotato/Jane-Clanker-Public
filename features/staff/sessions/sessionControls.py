from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

import discord
from discord import ui
from features.staff.sessions import bgBuckets

log = logging.getLogger(__name__)

_service: Any = None
_canClockIn: Optional[Callable[[discord.Member], bool]] = None
_clockInDeniedMessage: Optional[Callable[[], str]] = None
_parseSessionId: Optional[Callable[[str], int]] = None
_safeInteractionReply: Optional[Callable[..., Awaitable[None]]] = None
_safeInteractionDefer: Optional[Callable[..., Awaitable[None]]] = None
_safeInteractionEditMessage: Optional[Callable[..., Awaitable[None]]] = None
_safeInteractionSendModal: Optional[Callable[..., Awaitable[None]]] = None
_requestSessionMessageUpdate: Optional[Callable[..., Awaitable[None]]] = None
_updateSessionMessage: Optional[Callable[..., Awaitable[None]]] = None
_buildGradingEmbed: Optional[Callable[..., discord.Embed]] = None
_setPendingBgRole: Optional[Callable[..., Awaitable[None]]] = None
_postOrientationResults: Optional[Callable[..., Awaitable[None]]] = None
_deleteSessionMessage: Optional[Callable[..., Awaitable[None]]] = None
_postBgQueue: Optional[Callable[..., Awaitable[None]]] = None


def configure(
    *,
    serviceModule: Any,
    canClockIn: Callable[[discord.Member], bool],
    clockInDeniedMessage: Callable[[], str],
    parseSessionId: Callable[[str], int],
    safeInteractionReply: Callable[..., Awaitable[None]],
    safeInteractionDefer: Callable[..., Awaitable[None]],
    safeInteractionEditMessage: Callable[..., Awaitable[None]],
    safeInteractionSendModal: Callable[..., Awaitable[None]],
    requestSessionMessageUpdate: Callable[..., Awaitable[None]],
    updateSessionMessage: Callable[..., Awaitable[None]],
    buildGradingEmbed: Callable[..., discord.Embed],
    setPendingBgRole: Callable[..., Awaitable[None]],
    postOrientationResults: Callable[..., Awaitable[None]],
    deleteSessionMessage: Callable[..., Awaitable[None]],
    postBgQueue: Callable[..., Awaitable[None]],
) -> None:
    global _service
    global _canClockIn
    global _clockInDeniedMessage
    global _parseSessionId
    global _safeInteractionReply
    global _safeInteractionDefer
    global _safeInteractionEditMessage
    global _safeInteractionSendModal
    global _requestSessionMessageUpdate
    global _updateSessionMessage
    global _buildGradingEmbed
    global _setPendingBgRole
    global _postOrientationResults
    global _deleteSessionMessage
    global _postBgQueue

    _service = serviceModule
    _canClockIn = canClockIn
    _clockInDeniedMessage = clockInDeniedMessage
    _parseSessionId = parseSessionId
    _safeInteractionReply = safeInteractionReply
    _safeInteractionDefer = safeInteractionDefer
    _safeInteractionEditMessage = safeInteractionEditMessage
    _safeInteractionSendModal = safeInteractionSendModal
    _requestSessionMessageUpdate = requestSessionMessageUpdate
    _updateSessionMessage = updateSessionMessage
    _buildGradingEmbed = buildGradingEmbed
    _setPendingBgRole = setPendingBgRole
    _postOrientationResults = postOrientationResults
    _deleteSessionMessage = deleteSessionMessage
    _postBgQueue = postBgQueue


async def _bgQueuePostingSummary(sessionId: int) -> tuple[int, int, bool, bool]:
    session = await _service.getSession(sessionId)
    attendees = await _service.getAttendees(sessionId)
    adultCount = 0
    minorCount = 0
    for attendee in list(attendees or []):
        if str(attendee.get("examGrade") or "").upper() != "PASS":
            continue
        reviewBucket = bgBuckets.normalizeBgReviewBucket(
            attendee.get("bgReviewBucket"),
            default=bgBuckets.adultBgReviewBucket,
        )
        if reviewBucket == bgBuckets.minorBgReviewBucket:
            minorCount += 1
        else:
            adultCount += 1

    adultPosted = adultCount <= 0 or int((session or {}).get("bgQueueMessageId") or 0) > 0
    minorPosted = minorCount <= 0 or int((session or {}).get("bgQueueMinorMessageId") or 0) > 0
    return adultCount, minorCount, adultPosted, minorPosted


class JoinPasswordModal(ui.Modal, title="Enter Password"):
    password = ui.TextInput(label="Password", style=discord.TextStyle.short, required=True)

    def __init__(self, sessionId: int):
        super().__init__()
        self.sessionId = sessionId

    async def on_submit(self, interaction: discord.Interaction):
        await _safeInteractionDefer(interaction, ephemeral=True)

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await _safeInteractionReply(
                interaction,
                content="This action can only be used inside a server channel.",
                ephemeral=True,
            )

        if not _canClockIn(interaction.user):
            return await _safeInteractionReply(
                interaction,
                content=_clockInDeniedMessage(),
                ephemeral=True,
            )

        session = await _service.getSession(self.sessionId)
        if not session or session["status"] != "OPEN":
            return await _safeInteractionReply(
                interaction,
                content="This orientation is not currently open for clock-ins.",
                ephemeral=True,
            )

        attendeeCount = len(await _service.getAttendees(self.sessionId))
        if session.get("maxAttendeeLimit") <= attendeeCount:
            return await _safeInteractionReply(
                interaction,
                content="This orientation has reached its attendee limit, try your luck next time!", 
                ephemeral=True,
            )

        existing = await _service.getAttendee(self.sessionId, interaction.user.id)
        if existing:
            return await _safeInteractionReply(
                interaction,
                content="You are already clocked in to this orientation.",
                ephemeral=True,
            )

        ok = await _service.verifyPassword(self.sessionId, str(self.password.value))
        if not ok:
            return await _safeInteractionReply(
                interaction,
                content="The password you entered is incorrect. Please try again.",
                ephemeral=True,
            )

        await _service.addAttendee(self.sessionId, interaction.user.id)
        await _safeInteractionReply(
            interaction,
            content="You have clocked in to this orientation.",
            ephemeral=True,
        )

        attendeeCount = len(await _service.getAttendees(self.sessionId))
        if attendeeCount >= session.get("maxAttendeeLimit", 30):
            await _service.setStatus(self.sessionId, "FULL")
            await _updateSessionMessage(interaction.client, self.sessionId)

        try:
            await _requestSessionMessageUpdate(interaction.client, self.sessionId)
        except Exception:
            log.exception("Failed to refresh session message after attendee clock-in (session=%s).", self.sessionId)


class SessionView(ui.View):
    def __init__(self, sessionId: int):
        super().__init__(timeout=None)
        self.sessionId = sessionId

        self.deleteBtn.custom_id = f"session:delete:{sessionId}"
        self.gradeBtn.custom_id = f"session:grade:{sessionId}"
        self.finishBtn.custom_id = f"session:finish:{sessionId}"
        self.joinBtn.custom_id = f"session:join:{sessionId}"

    async def disableIfLocked(self):
        session = await _service.getSession(self.sessionId)
        if not session:
            return
        if session["status"] in ("CANCELED", "FINISHED"):
            for child in self.children:
                child.disabled = True
        if session["status"] == "GRADING" or session["status"] == "FULL":
            self.joinBtn.disabled = True

    @ui.button(label="Delete", style=discord.ButtonStyle.danger, row=0)
    async def deleteBtn(self, interaction: discord.Interaction, button: ui.Button):
        sessionId = _parseSessionId(button.custom_id)
        session = await _service.getSession(sessionId)
        if not session:
            return await _safeInteractionReply(
                interaction,
                "This orientation session could not be found.",
                ephemeral=True,
            )
        if interaction.user.id != session["hostId"]:
            return await _safeInteractionReply(
                interaction,
                "Only the session host may Delete the current session.",
                ephemeral=True,
            )

        await _service.cancelSession(sessionId)
        await _updateSessionMessage(interaction.client, sessionId)
        await _safeInteractionReply(interaction, "Session canceled.", ephemeral=True)

    @ui.button(label="Change Grade", style=discord.ButtonStyle.primary, row=0)
    async def gradeBtn(self, interaction: discord.Interaction, button: ui.Button):
        sessionId = _parseSessionId(button.custom_id)
        session = await _service.getSession(sessionId)
        if not session:
            return await _safeInteractionReply(
                interaction,
                "This orientation session could not be found.",
                ephemeral=True,
            )
        if interaction.user.id != session["hostId"]:
            return await _safeInteractionReply(
                interaction,
                "Only the session host may open or use grading controls.",
                ephemeral=True,
            )

        attendees = await _service.getAttendees(sessionId)
        if not attendees:
            return await _safeInteractionReply(
                interaction,
                "No attendees are currently clocked in for grading.",
                ephemeral=True,
            )

        await _service.setStatus(sessionId, "GRADING")
        await _updateSessionMessage(interaction.client, sessionId)

        idx = session["gradingIndex"]
        if idx >= len(attendees):
            await _service.resetGradingIndex(sessionId)
            session = await _service.getSession(sessionId)
            idx = session["gradingIndex"]

        attendeeUserId = attendees[idx]["userId"]
        embed = _buildGradingEmbed(session, interaction.user, attendeeUserId, idx + 1, len(attendees))
        view = GradingView(sessionId, interaction.user.id)
        await _safeInteractionReply(interaction, embed=embed, view=view, ephemeral=True)

    @ui.button(label="Finish", style=discord.ButtonStyle.success, row=0)
    async def finishBtn(self, interaction: discord.Interaction, button: ui.Button):
        sessionId = _parseSessionId(button.custom_id)
        session = await _service.getSession(sessionId)
        if not session:
            return await _safeInteractionReply(
                interaction,
                "This orientation session could not be found.",
                ephemeral=True,
            )
        if interaction.user.id != session["hostId"]:
            return await _safeInteractionReply(
                interaction,
                "Only the session host may Finish the orientation.",
                ephemeral=True,
            )

        allowed, reason = await _service.isFinishAllowed(sessionId)
        if not allowed:
            return await _safeInteractionReply(interaction, reason, ephemeral=True)

        await _safeInteractionDefer(interaction, ephemeral=True)
        try:
            if session.get("sessionType") == "orientation":
                await _postBgQueue(interaction.client, sessionId, interaction.guild)
                adultCount, minorCount, adultPosted, minorPosted = await _bgQueuePostingSummary(sessionId)
                if adultPosted and minorPosted:
                    await _postOrientationResults(interaction.client, sessionId)
                    await _service.finishSession(sessionId)
                    await _deleteSessionMessage(interaction.client, sessionId)
                    await _safeInteractionReply(
                        interaction,
                        (
                            "Finished. BG queues posted for moderation.\n"
                            f"+18 routed: `{adultCount}`\n"
                            f"-18 routed: `{minorCount}`"
                        ),
                        ephemeral=True,
                    )
                    return
                await _updateSessionMessage(interaction.client, sessionId)
                log.error(
                    "Orientation session %s finished, but BG queue posting was incomplete (adultPosted=%s minorPosted=%s adultCount=%s minorCount=%s).",
                    sessionId,
                    adultPosted,
                    minorPosted,
                    adultCount,
                    minorCount,
                )
                await _safeInteractionReply(
                    interaction,
                    (
                        "Finished, but Jane could not post all BG queues correctly.\n"
                        f"+18 routed: `{adultCount}` (`{'ok' if adultPosted else 'missing'}`)\n"
                        f"-18 routed: `{minorCount}` (`{'ok' if minorPosted else 'missing'}`)\n"
                        "Check the configured BG review channels."
                    ),
                    ephemeral=True,
                )
                return
            else:
                await _service.finishSession(sessionId)
                await _updateSessionMessage(interaction.client, sessionId)
            await _safeInteractionReply(
                interaction,
                "Finished. BG checks posted for moderation.",
                ephemeral=True,
            )
        except Exception:
            log.exception("Failed to finish orientation session %s", sessionId)
            await _safeInteractionReply(
                interaction,
                "The session could not be finalized due to an internal error.",
                ephemeral=True,
            )

    @ui.button(emoji="\u2705", style=discord.ButtonStyle.success, row=1)
    async def joinBtn(self, interaction: discord.Interaction, button: ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await _safeInteractionReply(
                interaction,
                "This action can only be used inside a server channel.",
                ephemeral=True,
            )
        sessionId = _parseSessionId(button.custom_id)
        session = await _service.getSession(sessionId)
        if not session or session["status"] != "OPEN":
            return await _safeInteractionReply(
                interaction,
                "This orientation is not currently open for clock-ins.",
                ephemeral=True,
            )
        attendeeCount = len(await _service.getAttendees(sessionId))
        if session.get("maxAttendeeLimit") <= attendeeCount:
            return await _safeInteractionReply(
                interaction,
                "This orientation has reached its attendee limit, try your luck next time!", 
                ephemeral=True,
            )
        if not _canClockIn(interaction.user):
            return await _safeInteractionReply(interaction, _clockInDeniedMessage(), ephemeral=True)
        existing = await _service.getAttendee(sessionId, interaction.user.id)
        if existing:
            return await _safeInteractionReply(interaction, "You are already clocked in to this orientation.", ephemeral=True)
        await _safeInteractionSendModal(interaction, JoinPasswordModal(sessionId))


class GradingView(ui.View):
    def __init__(self, sessionId: int, hostId: int):
        super().__init__(timeout=900)
        self.sessionId = sessionId
        self.hostId = hostId

        self.passBtn.custom_id = f"grading:pass:{sessionId}"
        self.failBtn.custom_id = f"grading:fail:{sessionId}"

    async def applyGrade(self, interaction: discord.Interaction, grade: str):
        if interaction.user.id != self.hostId:
            return await _safeInteractionReply(
                interaction,
                "Only the session host may use grading controls.",
                ephemeral=True,
            )
        await _safeInteractionDefer(interaction, ephemeral=True)

        session = await _service.getSession(self.sessionId)
        if not session:
            for child in self.children:
                child.disabled = True
            await _safeInteractionEditMessage(interaction, content="Session not found.", view=self)
            return
        attendees = await _service.getAttendees(self.sessionId)
        if not attendees:
            for child in self.children:
                child.disabled = True
            await _safeInteractionEditMessage(interaction, content="No attendees.", view=self)
            return

        idx = session["gradingIndex"]
        if idx >= len(attendees):
            for child in self.children:
                child.disabled = True
            await _updateSessionMessage(interaction.client, self.sessionId)
            await _safeInteractionEditMessage(interaction, content="Grading complete.", view=self)
            return await _safeInteractionReply(interaction, "All attendees processed.", ephemeral=True)

        userId = attendees[idx]["userId"]
        await _service.setExamGrade(self.sessionId, userId, grade)
        if session.get("sessionType") == "orientation":
            await _setPendingBgRole(interaction.guild, userId, grade == "PASS")
        await _service.incrementGradingIndex(self.sessionId)

        await _updateSessionMessage(interaction.client, self.sessionId)

        session = await _service.getSession(self.sessionId)
        attendees = await _service.getAttendees(self.sessionId)
        idx = session["gradingIndex"]

        if idx >= len(attendees):
            for child in self.children:
                child.disabled = True
            await _safeInteractionEditMessage(interaction, content="Grading complete.", view=self)
            return await _safeInteractionReply(interaction, "All attendees processed.", ephemeral=True)

        nextUserId = attendees[idx]["userId"]
        hostMember = interaction.guild.get_member(self.hostId) or interaction.user
        embed = _buildGradingEmbed(session, hostMember, nextUserId, idx + 1, len(attendees))
        await _safeInteractionEditMessage(interaction, embed=embed, view=self)

    @ui.button(label="Pass", style=discord.ButtonStyle.success, emoji="\u2705")
    async def passBtn(self, interaction: discord.Interaction, button: ui.Button):
        await self.applyGrade(interaction, "PASS")

    @ui.button(label="Fail", style=discord.ButtonStyle.danger, emoji="\u274C")
    async def failBtn(self, interaction: discord.Interaction, button: ui.Button):
        await self.applyGrade(interaction, "FAIL")
