from typing import Sequence

import discord

from features.staff.sessions import rendering as sessionRendering
from features.staff.sessions import service as sessionService


class OrientationClockinAdapter:
    async def createSession(self, guildId: int, channelId: int, hostId: int, maxAttendeeLimit: int, **kwargs) -> int:
        sessionType = str(kwargs.get("sessionType") or "orientation").strip().lower()
        password = str(kwargs.get("password") or "").strip()
        messageId = int(kwargs.get("messageId") or 0)
        return await sessionService.createSession(
            guildId=int(guildId),
            channelId=int(channelId),
            messageId=messageId,
            sessionType=sessionType,
            hostId=int(hostId),
            password=password,
            maxAttendeeLimit=int(maxAttendeeLimit)
        )

    async def setSessionMessageId(self, sessionId: int, messageId: int) -> None:
        await sessionService.setSessionMessageId(int(sessionId), int(messageId))

    async def getSession(self, sessionId: int) -> dict | None:
        return await sessionService.getSession(int(sessionId))

    async def listOpenSessions(self) -> list[dict]:
        return await sessionService.getSessionsByStatus(["OPEN"])

    async def listAttendees(self, sessionId: int) -> list[dict]:
        return await sessionService.getAttendees(int(sessionId))

    async def addAttendee(self, sessionId: int, userId: int) -> None:
        await sessionService.addAttendee(int(sessionId), int(userId))

    async def removeAttendee(self, sessionId: int, userId: int) -> None:
        await sessionService.removeAttendee(int(sessionId), int(userId))

    async def updateSessionStatus(self, sessionId: int, status: str) -> None:
        await sessionService.setStatus(int(sessionId), str(status))

    def normalizeSession(self, session: dict) -> dict:
        return {
            "sessionId": int(session.get("sessionId") or 0),
            "guildId": int(session.get("guildId") or 0),
            "channelId": int(session.get("channelId") or 0),
            "messageId": int(session.get("messageId") or 0),
            "hostId": int(session.get("hostId") or 0),
            "status": str(session.get("status") or "OPEN").upper(),
            "sessionType": str(session.get("sessionType") or "orientation").strip().lower(),
            "maxAttendeeLimit": int(session.get("maxAttendeeLimit") or 30),
        }

    def buildEmbed(self, session: dict, attendees: Sequence[dict]) -> discord.Embed:
        normalized = self.normalizeSession(session)
        hostMention = f"<@{normalized['hostId']}>"
        return sessionRendering.buildSessionEmbed(
            normalized,
            hostMention,
            list(attendees),
            showBg=False,
        )

