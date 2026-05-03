from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from features.staff.sessions import postActions


class RobloxAutoAcceptTests(unittest.IsolatedAsyncioTestCase):
    async def test_attempt_roblox_auto_accept_for_group_uses_supplied_group_id(self) -> None:
        guild = SimpleNamespace(id=77)
        dmUser = AsyncMock(return_value=True)
        notifyMods = AsyncMock()

        with (
            patch.object(
                postActions.sessionService,
                "getAttendee",
                AsyncMock(
                    return_value={
                        "examGrade": "PASS",
                        "bgStatus": "APPROVED",
                        "robloxJoinStatus": None,
                        "robloxUserId": None,
                    }
                ),
            ),
            patch.object(postActions.sessionService, "setRobloxStatus", AsyncMock()),
            patch.object(
                postActions.robloxUsers,
                "fetchRobloxUser",
                AsyncMock(return_value=SimpleNamespace(robloxId=12345, error=None)),
            ),
            patch.object(
                postActions.robloxGroups,
                "fetchRobloxGroups",
                AsyncMock(return_value=SimpleNamespace(status=200, groups=[])),
            ),
            patch.object(
                postActions.robloxGroups,
                "acceptJoinRequestForGroup",
                AsyncMock(return_value=SimpleNamespace(ok=True, status=200, error=None)),
            ) as acceptMock,
            patch.object(postActions.config, "robloxOpenCloudApiKey", "test-key"),
        ):
            status = await postActions.attemptRobloxAutoAcceptForGroup(
                bot=SimpleNamespace(),
                guild=guild,
                sessionId=10,
                targetUserId=20,
                groupId=555666,
                groupUrl="https://www.roblox.com/groups/555666",
                dmUser=dmUser,
                notifyMods=notifyMods,
            )

        self.assertEqual(status, "ACCEPTED")
        acceptMock.assert_awaited_once_with(12345, 555666)
        notifyMods.assert_not_called()
        dmUser.assert_not_called()

    async def test_attempt_roblox_auto_accept_delegates_to_group_helper(self) -> None:
        helper = AsyncMock(return_value="ACCEPTED")
        guild = SimpleNamespace(id=88)

        with (
            patch.object(postActions, "attemptRobloxAutoAcceptForGroup", helper),
            patch.object(
                postActions.orgProfiles,
                "getOrganizationValue",
                side_effect=lambda *_args, **kwargs: 999001 if kwargs.get("default", None) == 0 else 0,
            ),
        ):
            status = await postActions.attemptRobloxAutoAccept(
                bot=SimpleNamespace(),
                guild=guild,
                sessionId=11,
                targetUserId=22,
                dmUser=AsyncMock(),
                notifyMods=AsyncMock(),
                groupUrlProvider=lambda: "https://www.roblox.com/groups/999001",
            )

        self.assertEqual(status, "ACCEPTED")
        helper.assert_awaited_once()
        _, _, _, _ = helper.await_args.args
        self.assertEqual(helper.await_args.kwargs["groupId"], 999001)
        self.assertEqual(helper.await_args.kwargs["groupUrl"], "https://www.roblox.com/groups/999001")


if __name__ == "__main__":
    unittest.main()
