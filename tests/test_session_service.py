from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from db import sqlite as sqliteDb
from features.staff.sessions import service


class SessionClockInTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tempDir = tempfile.TemporaryDirectory()
        self._originalDbPath = sqliteDb.dbPath
        await sqliteDb.closeDb()
        sqliteDb.dbPath = str(Path(self._tempDir.name) / "test.db")
        await sqliteDb.initDb()

    async def asyncTearDown(self) -> None:
        await sqliteDb.closeDb()
        sqliteDb.dbPath = self._originalDbPath
        self._tempDir.cleanup()

    async def test_attempt_clock_in_respects_limit_under_concurrency(self) -> None:
        sessionId = await service.createSession(
            guildId=1,
            channelId=2,
            messageId=3,
            sessionType="orientation",
            hostId=4,
            password="secret",
            maxAttendeeLimit=2,
        )

        results = await asyncio.gather(
            service.attemptClockIn(sessionId, 101, "secret"),
            service.attemptClockIn(sessionId, 102, "secret"),
            service.attemptClockIn(sessionId, 103, "secret"),
        )

        statuses = [str(result.get("status") or "") for result in results]
        self.assertEqual(statuses.count("ADDED"), 2)
        self.assertEqual(
            sum(
                1
                for result in results
                if str(result.get("status") or "") == "FULL"
                or (
                    str(result.get("status") or "") == "SESSION_CLOSED"
                    and str(result.get("sessionStatus") or "") == "FULL"
                )
            ),
            1,
        )

        attendees = await service.getAttendees(sessionId)
        self.assertEqual(len(attendees), 2)
        self.assertTrue({int(row["userId"]) for row in attendees}.issubset({101, 102, 103}))

        session = await service.getSession(sessionId)
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "FULL")

    async def test_attempt_clock_in_rejects_bad_password_without_using_slot(self) -> None:
        sessionId = await service.createSession(
            guildId=1,
            channelId=2,
            messageId=3,
            sessionType="orientation",
            hostId=4,
            password="secret",
            maxAttendeeLimit=1,
        )

        badResult = await service.attemptClockIn(sessionId, 101, "wrong")
        goodResult = await service.attemptClockIn(sessionId, 101, "secret")

        self.assertEqual(badResult["status"], "BAD_PASSWORD")
        self.assertEqual(goodResult["status"], "ADDED")
        self.assertEqual(await service.getAttendeeCount(sessionId), 1)


if __name__ == "__main__":
    unittest.main()
