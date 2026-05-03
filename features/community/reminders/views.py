from __future__ import annotations

from typing import Any

import discord

from runtime import viewBases as runtimeViewBases


class ReminderSnoozeView(runtimeViewBases.OwnerLockedView):
    def __init__(self, *, cog: Any, reminderId: int, userId: int) -> None:
        super().__init__(
            openerId=userId,
            timeout=86400,
            ownerMessage="Only the reminder owner can snooze this reminder.",
        )
        self.cog = cog
        self.reminderId = int(reminderId)

    @discord.ui.button(label="Snooze 10m", style=discord.ButtonStyle.secondary, row=0)
    async def snooze10Btn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleReminderSnooze(interaction, reminderId=self.reminderId, delaySeconds=600)

    @discord.ui.button(label="Snooze 1h", style=discord.ButtonStyle.secondary, row=0)
    async def snoozeHourBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleReminderSnooze(interaction, reminderId=self.reminderId, delaySeconds=3600)

    @discord.ui.button(label="Snooze 1d", style=discord.ButtonStyle.secondary, row=0)
    async def snoozeDayBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.handleReminderSnooze(interaction, reminderId=self.reminderId, delaySeconds=86400)
