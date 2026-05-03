import re
from typing import Callable, Optional, Protocol, Sequence

import discord

from runtime import interaction as interactionRuntime

_mentionRegex = re.compile(r"<@!?(?P<user_id>\d+)>")


def _setAllButtonsDisabled(view: discord.ui.View, disabled: bool) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button):
            child.disabled = disabled


def resolveAttendeeUserIdFromToken(
    token: str,
    attendees: Sequence[dict],
) -> Optional[int]:
    raw = str(token or "").strip()
    if not raw:
        return None

    attendeeUserIds = [int(row.get("userId") or 0) for row in attendees]
    attendeeUserIds = [userId for userId in attendeeUserIds if userId > 0]
    if not attendeeUserIds:
        return None

    if raw.isdigit():
        indexOrId = int(raw)
        if 1 <= indexOrId <= len(attendeeUserIds):
            return int(attendeeUserIds[indexOrId - 1])
        if indexOrId in attendeeUserIds:
            return int(indexOrId)
        return None

    match = _mentionRegex.search(raw)
    if not match:
        return None
    userId = int(match.group("user_id"))
    return userId if userId in attendeeUserIds else None


class ClockinAdapter(Protocol):
    async def createSession(self, guildId: int, channelId: int, hostId: int, maxAttendeeLimit: int=30, **kwargs) -> int: ...

    async def setSessionMessageId(self, sessionId: int, messageId: int) -> None: ...

    async def getSession(self, sessionId: int) -> Optional[dict]: ...

    async def listOpenSessions(self) -> list[dict]: ...

    async def listAttendees(self, sessionId: int) -> list[dict]: ...

    async def addAttendee(self, sessionId: int, userId: int) -> None: ...

    async def removeAttendee(self, sessionId: int, userId: int) -> None: ...

    async def updateSessionStatus(self, sessionId: int, status: str) -> None: ...

    def normalizeSession(self, session: dict) -> dict: ...

    def buildEmbed(self, session: dict, attendees: Sequence[dict]) -> discord.Embed: ...


class ClockinEngine:
    def __init__(self, bot: discord.Client, adapter: ClockinAdapter):
        self.bot = bot
        self.adapter = adapter

    async def restoreOpenViews(self, viewFactory: Callable[[int], discord.ui.View]) -> int:
        restored = 0
        sessions = await self.adapter.listOpenSessions()
        for session in sessions:
            normalized = self.adapter.normalizeSession(session)
            sessionId = int(normalized.get("sessionId") or 0)
            messageId = int(normalized.get("messageId") or 0)
            if sessionId <= 0 or messageId <= 0:
                continue
            self.bot.add_view(viewFactory(sessionId), message_id=messageId)
            restored += 1
        return restored

    async def createSession(self, guildId: int, channelId: int, hostId: int, maxAttendeeLimit: int = 30, **kwargs) -> int:
        return await self.adapter.createSession(guildId, channelId, hostId, maxAttendeeLimit, **kwargs)

    async def setSessionMessageId(self, sessionId: int, messageId: int) -> None:
        await self.adapter.setSessionMessageId(sessionId, messageId)

    async def getSession(self, sessionId: int) -> Optional[dict]:
        return await self.adapter.getSession(sessionId)

    async def listAttendees(self, sessionId: int) -> list[dict]:
        return await self.adapter.listAttendees(sessionId)

    async def addAttendee(self, sessionId: int, userId: int) -> None:
        await self.adapter.addAttendee(sessionId, userId)

    async def removeAttendee(self, sessionId: int, userId: int) -> None:
        await self.adapter.removeAttendee(sessionId, userId)

    async def updateSessionStatus(self, sessionId: int, status: str) -> None:
        await self.adapter.updateSessionStatus(sessionId, status)

    async def _resolveMessageFromSession(
        self,
        session: dict,
        *,
        message: Optional[discord.Message] = None,
    ) -> Optional[discord.Message]:
        if message is not None:
            return message

        normalized = self.adapter.normalizeSession(session)
        channelId = int(normalized.get("channelId") or 0)
        messageId = int(normalized.get("messageId") or 0)
        if channelId <= 0 or messageId <= 0:
            return None

        channel = self.bot.get_channel(channelId)
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, channelId)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return None
        return await interactionRuntime.safeFetchMessage(channel, messageId)

    async def updateClockinMessage(
        self,
        sessionId: int,
        *,
        viewFactory: Callable[[int], discord.ui.View],
        message: Optional[discord.Message] = None,
    ) -> None:
        session = await self.getSession(sessionId)
        if not session:
            return
        attendees = await self.listAttendees(sessionId)

        embed = self.adapter.buildEmbed(session, attendees)
        view = viewFactory(int(sessionId))
        normalized = self.adapter.normalizeSession(session)
        status = str(normalized.get("status") or "OPEN").upper()
        if status != "OPEN":
            _setAllButtonsDisabled(view, True)

        targetMessage = await self._resolveMessageFromSession(session, message=message)
        if targetMessage is None:
            return
        await interactionRuntime.safeMessageEdit(targetMessage, embed=embed, view=view)

    async def deleteClockinMessage(
        self,
        session: dict,
        *,
        message: Optional[discord.Message] = None,
    ) -> None:
        targetMessage = await self._resolveMessageFromSession(session, message=message)
        if targetMessage is None:
            return
        await interactionRuntime.safeMessageDelete(targetMessage)
