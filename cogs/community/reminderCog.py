from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.community.reminders import (
    ReminderSnoozeView,
    cancelReminder,
    createReminder,
    getReminder,
    listActiveRemindersForUser,
    listDueReminders,
    markReminderSent,
    parseRecurringInterval,
    parseReminderWhen,
    rescheduleReminder,
)
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions

log = logging.getLogger(__name__)


def _parseRoleIdsText(raw: str) -> list[int]:
    roleIds: list[int] = []
    for token in str(raw or "").replace("\n", " ").replace(",", " ").split():
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            continue
        parsed = int(digits)
        if parsed > 0 and parsed not in roleIds:
            roleIds.append(parsed)
    return roleIds


def _formatIntervalText(totalSeconds: int) -> str:
    seconds = max(0, int(totalSeconds or 0))
    if seconds <= 0:
        return "none"
    if seconds % 604800 == 0:
        weeks = seconds // 604800
        return f"every {weeks} week{'s' if weeks != 1 else ''}"
    if seconds % 86400 == 0:
        days = seconds // 86400
        return f"every {days} day{'s' if days != 1 else ''}"
    if seconds % 3600 == 0:
        hours = seconds // 3600
        return f"every {hours} hour{'s' if hours != 1 else ''}"
    if seconds % 60 == 0:
        minutes = seconds // 60
        return f"every {minutes} minute{'s' if minutes != 1 else ''}"
    return f"every {seconds} second{'s' if seconds != 1 else ''}"


class ReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._reminderTask: asyncio.Task | None = None

    async def cog_load(self) -> None:
        if self._reminderTask is None or self._reminderTask.done():
            self._reminderTask = asyncio.create_task(self._runReminderLoop())

    def cog_unload(self) -> None:
        if self._reminderTask is not None and not self._reminderTask.done():
            self._reminderTask.cancel()
        self._reminderTask = None

    async def _safeEphemeral(self, interaction: discord.Interaction, content: str) -> None:
        await interactionRuntime.safeInteractionReply(interaction, content=content, ephemeral=True)

    def _canCreateTeamReminder(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        allowedRoleIds = {
            int(getattr(config, "middleRankRoleId", 0) or 0),
            int(getattr(config, "highRankRoleId", 0) or 0),
        }
        allowedRoleIds = {roleId for roleId in allowedRoleIds if roleId > 0}
        if not allowedRoleIds:
            return False
        return any(int(role.id) in allowedRoleIds for role in member.roles)

    async def _getReminderChannel(self, channelId: int) -> discord.TextChannel | discord.Thread | None:
        if int(channelId) <= 0:
            return None
        channel = self.bot.get_channel(int(channelId))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channelId))
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    def _parseReminderTime(self, row: dict) -> datetime | None:
        raw = str(row.get("remindAtUtc") or "").strip()
        if not raw:
            return None
        try:
            remindAt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if remindAt.tzinfo is None:
            remindAt = remindAt.replace(tzinfo=timezone.utc)
        return remindAt.astimezone(timezone.utc)

    def _parseTargetRoleIds(self, row: dict) -> list[int]:
        raw = str(row.get("targetRoleIdsJson") or "").strip()
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        roleIds: list[int] = []
        for value in data:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0 and parsed not in roleIds:
                roleIds.append(parsed)
        return roleIds

    def _buildReminderEmbed(self, row: dict) -> discord.Embed:
        reminderId = int(row.get("reminderId") or 0)
        reminderText = str(row.get("reminderText") or "").strip() or "Reminder"
        targetType = str(row.get("targetType") or "USER").strip().upper()
        recurringIntervalSec = int(row.get("recurringIntervalSec") or 0)
        embed = discord.Embed(
            title=f"Reminder #{reminderId}",
            description=reminderText,
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        remindAt = self._parseReminderTime(row)
        if remindAt is not None:
            embed.add_field(
                name="Scheduled For",
                value=f"{discord.utils.format_dt(remindAt, 'F')}\n{discord.utils.format_dt(remindAt, 'R')}",
                inline=False,
            )
        embed.add_field(name="Target", value="Team reminder" if targetType == "ROLE" else "Personal reminder", inline=True)
        if recurringIntervalSec > 0:
            embed.add_field(name="Repeats", value=_formatIntervalText(recurringIntervalSec), inline=True)
        return embed

    def _nextRecurringTime(self, row: dict, *, now: datetime) -> datetime | None:
        intervalSeconds = int(row.get("recurringIntervalSec") or 0)
        if intervalSeconds <= 0:
            return None
        remindAt = self._parseReminderTime(row) or now
        nextTime = remindAt
        step = timedelta(seconds=intervalSeconds)
        while nextTime <= now:
            nextTime += step
        return nextTime

    async def _deliverUserReminder(self, row: dict, embed: discord.Embed) -> bool:
        reminderId = int(row.get("reminderId") or 0)
        userId = int(row.get("userId") or 0)
        channelId = int(row.get("channelId") or 0)
        user = self.bot.get_user(userId)
        if user is None:
            try:
                user = await self.bot.fetch_user(userId)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                user = None

        view = ReminderSnoozeView(cog=self, reminderId=reminderId, userId=userId)
        dmDelivered = False
        if user is not None:
            try:
                await user.send(embed=embed, view=view)
                dmDelivered = True
            except (discord.Forbidden, discord.HTTPException):
                dmDelivered = False

        if not dmDelivered:
            channel = await self._getReminderChannel(channelId)
            if channel is not None:
                try:
                    await channel.send(
                        content=f"<@{userId}> reminder:",
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
        return dmDelivered

    async def _deliverRoleReminder(self, row: dict, embed: discord.Embed) -> None:
        channel = await self._getReminderChannel(int(row.get("channelId") or 0))
        if channel is None:
            return
        roleMentions = " ".join(f"<@&{roleId}>" for roleId in self._parseTargetRoleIds(row))
        creatorId = int(row.get("userId") or 0)
        prefix = f"{roleMentions}\n" if roleMentions else ""
        try:
            await channel.send(
                content=f"{prefix}Team reminder from <@{creatorId}>:",
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=True, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    async def _processReminder(self, row: dict, *, now: datetime) -> None:
        reminderId = int(row.get("reminderId") or 0)
        embed = self._buildReminderEmbed(row)
        guildId = int(row.get("guildId") or 0)
        if guildId > 0:
            guild = self.bot.get_guild(guildId)
            if guild is not None:
                embed.add_field(name="Server", value=guild.name, inline=False)

        dmDelivered = False
        targetType = str(row.get("targetType") or "USER").strip().upper()
        if targetType == "ROLE":
            await self._deliverRoleReminder(row, embed)
        else:
            dmDelivered = await self._deliverUserReminder(row, embed)

        nextTime = self._nextRecurringTime(row, now=now)
        if nextTime is not None:
            await rescheduleReminder(reminderId, remindAtUtcIso=nextTime.isoformat())
            return
        await markReminderSent(reminderId, dmDelivered=dmDelivered)

    async def _runReminderLoop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                await self._runReminderTick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Reminder loop failed.")
            await asyncio.sleep(2)

    async def _runReminderTick(self) -> None:
        now = datetime.now(timezone.utc)
        dueRows = await listDueReminders(now.isoformat(), limit=50)
        if not dueRows:
            return
        results = await asyncio.gather(
            *(self._processReminder(row, now=now) for row in dueRows),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                log.exception(
                    "Reminder processing failed.",
                    exc_info=(type(result), result, result.__traceback__),
                )

    @app_commands.command(name="reminder", description="Create, list, or cancel reminders.")
    @app_commands.describe(
        action="What reminder action to run.",
        when="When to remind you. Required for add/team.",
        reminder_text="Reminder text. Required for add/team.",
        repeat="Optional repeat interval, such as 1d or 2w.",
        role_ids="Role IDs to ping. Required for team reminders.",
        reminder_id="Reminder ID to cancel. Required for cancel.",
        attachment="Optional attachment to include with the reminder.",
    )
    @app_commands.rename(reminder_text="reminder-text")
    @app_commands.rename(role_ids="role-ids")
    @app_commands.rename(reminder_id="reminder-id")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add personal reminder", value="add"),
            app_commands.Choice(name="Add team reminder", value="team"),
            app_commands.Choice(name="List my reminders", value="list"),
            app_commands.Choice(name="Cancel reminder", value="cancel"),
        ]
    )
    async def reminderCommand(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        when: str | None = None,
        reminder_text: str | None = None,
        repeat: str | None = None,
        role_ids: str | None = None,
        reminder_id: int | None = None,
        attachment: discord.Attachment | None = None,
    ) -> None:
        selectedAction = str(getattr(action, "value", action) or "").strip().lower()
        if selectedAction == "add":
            if not when or not reminder_text:
                await self._safeEphemeral(interaction, "`when` and `reminder-text` are required for add.")
                return
            await self.addReminder(
                interaction,
                when=when,
                reminder_text=reminder_text,
                repeat=repeat,
                attachment=attachment,
            )
            return
        if selectedAction == "team":
            if not when or not reminder_text or not role_ids:
                await self._safeEphemeral(interaction, "`when`, `reminder-text`, and `role-ids` are required for team.")
                return
            await self.addTeamReminder(
                interaction,
                when=when,
                role_ids=role_ids,
                reminder_text=reminder_text,
                repeat=repeat,
                attachment=attachment,
            )
            return
        if selectedAction == "list":
            await self.listReminders(interaction)
            return
        if selectedAction == "cancel":
            if reminder_id is None:
                await self._safeEphemeral(interaction, "`reminder-id` is required for cancel.")
                return
            await self.cancelReminderCommand(interaction, reminder_id=reminder_id)
            return
        await self._safeEphemeral(interaction, "Unknown reminder action.")

    async def addReminder(
        self,
        interaction: discord.Interaction,
        when: str,
        reminder_text: str,
        repeat: str | None = None,
        attachment: discord.Attachment | None = None,
    ) -> None:
        if not interaction.guild or not interaction.channel:
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        if attachment is not None:
            reminder_text = f"{reminder_text}\n{attachment.url}"
        try:
            remindAtUtc, label = parseReminderWhen(when)
            repeatSeconds = parseRecurringInterval(str(repeat or ""))
        except ValueError as exc:
            await self._safeEphemeral(interaction, str(exc))
            return
        if remindAtUtc <= datetime.now(timezone.utc):
            await self._safeEphemeral(interaction, "That reminder time has already passed.")
            return

        reminderId = await createReminder(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
            userId=int(interaction.user.id),
            reminderText=str(reminder_text or "").strip(),
            remindAtUtcIso=remindAtUtc.isoformat(),
            recurringIntervalSec=repeatSeconds,
        )
        repeatText = f" Repeats {_formatIntervalText(repeatSeconds)}." if repeatSeconds > 0 else ""
        await self._safeEphemeral(
            interaction,
            f"Reminder #{reminderId} set for {discord.utils.format_dt(remindAtUtc, 'F')} ({label}).{repeatText}",
        )

    async def addTeamReminder(
        self,
        interaction: discord.Interaction,
        when: str,
        role_ids: str,
        reminder_text: str,
        repeat: str | None = None,
        attachment: discord.Attachment | None = None,
    ) -> None:
        if not interaction.guild or not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        if not self._canCreateTeamReminder(interaction.user):
            await self._safeEphemeral(interaction, "MR/HR roles or administrator/manage-server required.")
            return

        roleIds = [roleId for roleId in _parseRoleIdsText(role_ids) if interaction.guild.get_role(roleId) is not None]
        if not roleIds:
            await self._safeEphemeral(interaction, "Please provide at least one valid role ID.")
            return
        if attachment is not None:
            reminder_text = f"{reminder_text}\n{attachment.url}"
        try:
            remindAtUtc, label = parseReminderWhen(when)
            repeatSeconds = parseRecurringInterval(str(repeat or ""))
        except ValueError as exc:
            await self._safeEphemeral(interaction, str(exc))
            return

        reminderId = await createReminder(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
            userId=int(interaction.user.id),
            reminderText=str(reminder_text or "").strip(),
            remindAtUtcIso=remindAtUtc.isoformat(),
            targetType="ROLE",
            targetRoleIds=roleIds,
            recurringIntervalSec=repeatSeconds,
        )
        roleMentions = " ".join(f"<@&{roleId}>" for roleId in roleIds)
        repeatText = f" Repeats {_formatIntervalText(repeatSeconds)}." if repeatSeconds > 0 else ""
        await self._safeEphemeral(
            interaction,
            f"Team reminder #{reminderId} set for {discord.utils.format_dt(remindAtUtc, 'F')} ({label}) for {roleMentions}.{repeatText}",
        )

    async def listReminders(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        rows = await listActiveRemindersForUser(int(interaction.guild.id), int(interaction.user.id))
        if not rows:
            await self._safeEphemeral(interaction, "You do not have any active reminders.")
            return

        lines: list[str] = []
        for row in rows[:15]:
            reminderId = int(row.get("reminderId") or 0)
            remindAt = self._parseReminderTime(row)
            timestampText = discord.utils.format_dt(remindAt, "R") if remindAt is not None else str(row.get("remindAtUtc") or "").strip()
            targetType = str(row.get("targetType") or "USER").strip().upper()
            recurringIntervalSec = int(row.get("recurringIntervalSec") or 0)
            suffixParts: list[str] = []
            if targetType == "ROLE":
                roleMentions = " ".join(f"<@&{roleId}>" for roleId in self._parseTargetRoleIds(row))
                if roleMentions:
                    suffixParts.append(roleMentions)
            if recurringIntervalSec > 0:
                suffixParts.append(_formatIntervalText(recurringIntervalSec))
            suffix = f" ({' | '.join(suffixParts)})" if suffixParts else ""
            lines.append(f"`#{reminderId}` {timestampText} - {str(row.get('reminderText') or '').strip()}{suffix}")
        embed = discord.Embed(title="Your Active Reminders", description="\n".join(lines), color=discord.Color.orange())
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=embed,
            ephemeral=True,
            allowedMentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )

    async def cancelReminderCommand(self, interaction: discord.Interaction, reminder_id: int) -> None:
        reminder = await getReminder(int(reminder_id))
        if reminder is None:
            await self._safeEphemeral(interaction, "Reminder not found.")
            return
        if str(reminder.get("status") or "").strip().upper() != "PENDING":
            await self._safeEphemeral(interaction, "That reminder is no longer active.")
            return
        isOwner = int(reminder.get("userId") or 0) == int(interaction.user.id)
        isAdmin = bool(
            isinstance(interaction.user, discord.Member)
            and (interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild)
        )
        if not isOwner and not isAdmin:
            await self._safeEphemeral(interaction, "You can only cancel your own reminders.")
            return
        await cancelReminder(int(reminder_id))
        await self._safeEphemeral(interaction, f"Reminder #{int(reminder_id)} canceled.")

    async def handleReminderSnooze(
        self,
        interaction: discord.Interaction,
        *,
        reminderId: int,
        delaySeconds: int,
    ) -> None:
        row = await getReminder(int(reminderId))
        if row is None:
            await self._safeEphemeral(interaction, "Reminder not found.")
            return
        if int(row.get("userId") or 0) != int(interaction.user.id):
            await self._safeEphemeral(interaction, "Only the reminder owner can snooze this reminder.")
            return
        newTime = datetime.now(timezone.utc) + timedelta(seconds=max(60, int(delaySeconds or 0)))
        await rescheduleReminder(int(reminderId), remindAtUtcIso=newTime.isoformat())
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Reminder #{int(reminderId)} snoozed until {discord.utils.format_dt(newTime, 'R')}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReminderCog(bot))
