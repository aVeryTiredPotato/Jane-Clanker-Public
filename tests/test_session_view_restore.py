from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from features.staff.sessions import views


class SessionViewRestoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_restore_persistent_views_reattaches_full_orientation_sessions(self) -> None:
        bot = SimpleNamespace(add_view=Mock())

        fullSession = {
            "sessionId": 123,
            "guildId": 1,
            "channelId": 2,
            "messageId": 456,
            "sessionType": "orientation",
            "status": "FULL",
            "createdAt": "2099-01-01 00:00:00",
            "bgQueueMessageId": None,
            "bgQueueMinorMessageId": None,
        }

        with (
            patch.object(views.service, "getSessionsByStatus", AsyncMock(return_value=[fullSession])) as getSessions,
            patch.object(views.service, "getAttendees", AsyncMock(return_value=[])),
        ):
            result = await views.restorePersistentViews(bot)

        getSessions.assert_awaited_once_with(["OPEN", "FULL", "GRADING", "FINISHED"])
        bot.add_view.assert_called_once()
        self.assertEqual(result["sessions"], 1)
        self.assertEqual(result["bgQueues"], 0)
        self.assertEqual(result["bgChecks"], 0)


if __name__ == "__main__":
    unittest.main()
