from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.community.suggestions import (
    SuggestionReviewModal,
    SuggestionReviewView,
    buildSuggestionBoardEmbed,
    buildSuggestionEmbed,
    createSuggestion,
    createSuggestionBoard,
    getSuggestion,
    listPendingSuggestions,
    listSuggestionBoards,
    listSuggestionCountsByStatus,
    listSuggestionStatusBoardRows,
    listSuggestions,
    removeSuggestionBoard,
    setSuggestionMessageId,
    setSuggestionThreadId,
    updateSuggestionStatus,
)
from features.community.suggestions.service import addSuggestionToFreedcamp, setSuggestionFreedcampId
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions

log = logging.getLogger(__name__)


def _normalizeRoleIds(values: object) -> list[int]:
    if not isinstance(values, (list, tuple, set)):
        return []
    roleIds: list[int] = []
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in roleIds:
            roleIds.append(parsed)
    return roleIds


class SuggestionCog(commands.Cog):
    suggestionGroup = app_commands.Group(name="suggestion", description="Community suggestion tools.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await self._restoreSuggestionViews()
        await self._refreshSuggestionBoardsForAllGuilds()

    async def _safeEphemeral(self, interaction: discord.Interaction, content: str) -> None:
        await interactionRuntime.safeInteractionReply(interaction, content=content, ephemeral=True)

    def _reviewerRoleIds(self) -> set[int]:
        return set(_normalizeRoleIds(getattr(config, "suggestionReviewerRoleIds", [])))

    def _canReviewSuggestion(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        reviewerRoleIds = self._reviewerRoleIds()
        if not reviewerRoleIds:
            return False
        return any(int(role.id) in reviewerRoleIds for role in member.roles)

    async def _getMessageChannel(self, channelId: int) -> discord.TextChannel | discord.Thread | None:
        if int(channelId) <= 0:
            return None
        channel = self.bot.get_channel(int(channelId))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channelId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def _getForumChannel(self, channelId: int) -> discord.ForumChannel | None:
        if int(channelId) <= 0:
            return None
        channel = self.bot.get_channel(int(channelId))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channelId))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return None
        if isinstance(channel, discord.ForumChannel):
            return channel
        return None

    async def _resolveSuggestionChannel(
        self,
        guild: discord.Guild,
        fallbackChannel: discord.abc.GuildChannel | discord.Thread | None,
    ) -> discord.TextChannel | discord.Thread | None:
        configuredId = int(getattr(config, "suggestionChannelId", 0) or 0)
        if configuredId > 0:
            channel = await self._getMessageChannel(configuredId)
            if channel is not None:
                return channel
        if isinstance(fallbackChannel, (discord.TextChannel, discord.Thread)):
            return fallbackChannel
        return None

    async def _resolveSuggestionForumChannel(
        self,
        guild: discord.Guild,
        fallbackChannel: discord.abc.GuildChannel | discord.Thread | None,
    ) -> discord.ForumChannel | None:
        configuredId = int(getattr(config, "suggestionForumChannelId", 0) or 0)
        if configuredId > 0:
            channel = await self._getForumChannel(configuredId)
            if channel is not None:
                return channel
        if isinstance(fallbackChannel, discord.ForumChannel):
            return fallbackChannel
        return None

    def _buildReviewView(self, row: dict) -> SuggestionReviewView:
        resolved = str(row.get("status") or "PENDING").strip().upper() != "PENDING"
        return SuggestionReviewView(cog=self, suggestionId=int(row.get("suggestionId") or 0), resolved=resolved)

    async def _buildSuggestionBoard(self, guild: discord.Guild) -> discord.Embed:
        return buildSuggestionBoardEmbed(
            guild,
            countsByStatus=await listSuggestionCountsByStatus(int(guild.id)),
            rowsByStatus=await listSuggestionStatusBoardRows(int(guild.id)),
        )

    async def _refreshSuggestionBoards(self, guild: discord.Guild) -> None:
        boardRows = await listSuggestionBoards(int(guild.id))
        if not boardRows:
            return
        embed = await self._buildSuggestionBoard(guild)
        for row in boardRows:
            messageId = int(row.get("messageId") or 0)
            channel = await self._getMessageChannel(int(row.get("channelId") or 0))
            if channel is None:
                await removeSuggestionBoard(messageId)
                continue
            try:
                message = await channel.fetch_message(messageId)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                await removeSuggestionBoard(messageId)
                continue
            await interactionRuntime.safeMessageEdit(message, embed=embed)

    async def _refreshSuggestionBoardsForAllGuilds(self) -> None:
        for guild in list(self.bot.guilds):
            try:
                await self._refreshSuggestionBoards(guild)
            except Exception:
                log.exception("Failed refreshing suggestion boards for guild %s.", guild.id)

    async def _createDiscussionThread(self, message: discord.Message, *, suggestionId: int) -> int:
        if not isinstance(message.channel, discord.TextChannel):
            return 0
        me = message.guild.me if message.guild else None
        if me is None or not message.channel.permissions_for(me).create_public_threads:
            return 0
        try:
            thread = await message.create_thread(
                name=f"suggestion-{int(suggestionId)}-discussion"[:100],
                auto_archive_duration=1440,
            )
        except (discord.Forbidden, discord.HTTPException):
            return 0
        try:
            await thread.send("Suggestion discussion thread.")
        except (discord.Forbidden, discord.HTTPException):
            pass
        return int(thread.id)

    def _suggestionForumTags(self, channel: discord.ForumChannel) -> list[discord.ForumTag]:
        return [
            tag
            for tag in list(getattr(channel, "available_tags", []) or [])
            if str(getattr(tag, "name", "") or "").strip().casefold() == "suggestion"
        ][:1]

    async def _createForumThread(self, channel: discord.ForumChannel, suggestionText: str) -> discord.Thread | None:
        try:
            created = await channel.create_thread(
                name=(str(suggestionText or "").strip() or "Suggestion")[:100],
                content=str(suggestionText or "").strip() or "(empty suggestion)",
                applied_tags=self._suggestionForumTags(channel),
                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
            )
        except (discord.Forbidden, discord.HTTPException):
            return None
        thread = getattr(created, "thread", None)
        return thread if isinstance(thread, discord.Thread) else None

    def _freedcampConfigured(self) -> bool:
        return (
            bool(str(getattr(config, "freedcampApiKey", "") or "").strip())
            and bool(str(getattr(config, "freedcampSecret", "") or "").strip())
            and int(getattr(config, "freedcampProjectId", 0) or 0) > 0
            and int(getattr(config, "freedcampTaskGroupId", 0) or 0) > 0
        )

    def _displayNameForUser(self, guild: discord.Guild, userId: int) -> str:
        member = guild.get_member(int(userId or 0))
        if member is not None:
            return str(member.display_name or member.name or "Unknown")
        user = self.bot.get_user(int(userId or 0))
        return str(getattr(user, "display_name", None) or getattr(user, "name", None) or "Unknown")

    async def _restoreSuggestionViews(self) -> int:
        restored = 0
        for row in await listPendingSuggestions():
            messageId = int(row.get("messageId") or 0)
            channelId = int(row.get("channelId") or 0)
            suggestionId = int(row.get("suggestionId") or 0)
            if messageId <= 0 or channelId <= 0 or suggestionId <= 0:
                continue
            self.bot.add_view(self._buildReviewView(row), message_id=messageId)
            channel = await self._getMessageChannel(channelId)
            if channel is not None:
                try:
                    message = await channel.fetch_message(messageId)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    message = None
                if message is not None:
                    await interactionRuntime.safeMessageEdit(
                        message,
                        embed=buildSuggestionEmbed(row),
                        view=self._buildReviewView(row),
                    )
            restored += 1
        return restored

    @suggestionGroup.command(name="submit", description="Submit a suggestion for the server.")
    @app_commands.rename(suggestion_text="suggestion-text")
    async def submitSuggestion(
        self,
        interaction: discord.Interaction,
        suggestion_text: str,
        anonymous: bool = False,
    ) -> None:
        if not interaction.guild or not interaction.channel:
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        targetChannel = await self._resolveSuggestionChannel(interaction.guild, interaction.channel)
        if targetChannel is None:
            await self._safeEphemeral(interaction, "I could not find a valid suggestion channel.")
            return
        targetForum = await self._resolveSuggestionForumChannel(interaction.guild, interaction.channel)

        suggestionId = await createSuggestion(
            guildId=int(interaction.guild.id),
            channelId=int(targetChannel.id),
            submitterId=int(interaction.user.id),
            content=str(suggestion_text or "").strip(),
            anonymous=bool(anonymous),
        )
        targetThread: discord.Thread | None = None
        if targetForum is not None:
            targetThread = await self._createForumThread(targetForum, suggestion_text)
        if targetThread is not None:
            await setSuggestionThreadId(suggestionId, int(targetThread.id))
        row = await getSuggestion(suggestionId)
        if row is None:
            await self._safeEphemeral(interaction, "Suggestion creation failed.")
            return

        view = self._buildReviewView(row)
        sentMessage = await targetChannel.send(
            embed=buildSuggestionEmbed(row),
            view=view,
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        await sentMessage.add_reaction("👍")
        await sentMessage.add_reaction("👎")
        await setSuggestionMessageId(suggestionId, int(sentMessage.id))
        if targetThread is None:
            threadId = await self._createDiscussionThread(sentMessage, suggestionId=suggestionId)
            if threadId > 0:
                await setSuggestionThreadId(suggestionId, threadId)
        latestRow = await getSuggestion(suggestionId)
        if int((latestRow or {}).get("threadId") or 0) > 0:
            row = latestRow or row
            await interactionRuntime.safeMessageEdit(sentMessage, embed=buildSuggestionEmbed(row), view=view)
        self.bot.add_view(view, message_id=int(sentMessage.id))
        await self._refreshSuggestionBoards(interaction.guild)
        await self._safeEphemeral(interaction, f"Suggestion #{suggestionId} submitted in {targetChannel.mention}.")

    @suggestionGroup.command(name="list", description="List recent suggestions.")
    @app_commands.rename(status_filter="status-filter")
    async def listSuggestionCommand(
        self,
        interaction: discord.Interaction,
        status_filter: str | None = None,
    ) -> None:
        if not interaction.guild:
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        normalizedStatus = str(status_filter or "").strip().upper()
        if normalizedStatus == "ALL":
            normalizedStatus = ""
        rows = await listSuggestions(int(interaction.guild.id), status=normalizedStatus or None, limit=10)
        if not rows:
            await self._safeEphemeral(interaction, "No suggestions found.")
            return
        lines: list[str] = []
        for row in rows:
            suggestionId = int(row.get("suggestionId") or 0)
            status = str(row.get("status") or "PENDING").strip().title()
            channelId = int(row.get("channelId") or 0)
            messageId = int(row.get("messageId") or 0)
            jumpUrl = ""
            if channelId > 0 and messageId > 0:
                jumpUrl = f"https://discord.com/channels/{interaction.guild.id}/{channelId}/{messageId}"
            line = f"`#{suggestionId}` {status}"
            if jumpUrl:
                line = f"{line} ([jump]({jumpUrl}))"
            lines.append(line)
        embed = discord.Embed(title="Recent Suggestions", description="\n".join(lines), color=discord.Color.blurple())
        await interactionRuntime.safeInteractionReply(interaction, embed=embed, ephemeral=True)

    @suggestionGroup.command(name="status-board", description="Post a public suggestion status board in this channel.")
    async def postStatusBoard(self, interaction: discord.Interaction) -> None:
        if not interaction.guild or not interaction.channel or not isinstance(interaction.user, discord.Member):
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        if not self._canReviewSuggestion(interaction.user):
            await self._safeEphemeral(interaction, "You do not have permission to manage suggestion boards.")
            return
        embed = await self._buildSuggestionBoard(interaction.guild)
        message = await interaction.channel.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
        )
        await createSuggestionBoard(int(interaction.guild.id), int(interaction.channel.id), int(message.id))
        await self._safeEphemeral(interaction, "Public suggestion status board posted.")

    async def openSuggestionReviewModal(
        self,
        interaction: discord.Interaction,
        *,
        suggestionId: int,
        newStatus: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._safeEphemeral(interaction, "This action can only be used in a server.")
            return
        if not self._canReviewSuggestion(interaction.user):
            await self._safeEphemeral(interaction, "You do not have permission to review suggestions.")
            return
        row = await getSuggestion(suggestionId)
        if row is None:
            await self._safeEphemeral(interaction, "Suggestion not found.")
            return
        if str(row.get("status") or "").strip().upper() != "PENDING":
            await self._safeEphemeral(interaction, "That suggestion has already been reviewed.")
            return
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            SuggestionReviewModal(cog=self, suggestionId=suggestionId, newStatus=newStatus),
        )

    async def applySuggestionStatusChange(
        self,
        interaction: discord.Interaction,
        *,
        suggestionId: int,
        newStatus: str,
        reviewNote: str,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._safeEphemeral(interaction, "This action can only be used in a server.")
            return
        if not self._canReviewSuggestion(interaction.user):
            await self._safeEphemeral(interaction, "You do not have permission to review suggestions.")
            return
        row = await getSuggestion(suggestionId)
        if row is None:
            await self._safeEphemeral(interaction, "Suggestion not found.")
            return
        if str(row.get("status") or "").strip().upper() != "PENDING":
            await self._safeEphemeral(interaction, "That suggestion has already been reviewed.")
            return

        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=False)
        await updateSuggestionStatus(
            suggestionId,
            status=newStatus,
            reviewerId=int(interaction.user.id),
            reviewNote=str(reviewNote or "").strip() or None,
        )
        refreshed = await getSuggestion(suggestionId)
        if refreshed is not None:
            targetMessage = interaction.message
            if targetMessage is None:
                channel = await self._getMessageChannel(int(refreshed.get("channelId") or 0))
                if channel is not None:
                    try:
                        targetMessage = await channel.fetch_message(int(refreshed.get("messageId") or 0))
                    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                        targetMessage = None
            if targetMessage is not None:
                await interactionRuntime.safeMessageEdit(
                    targetMessage,
                    embed=buildSuggestionEmbed(refreshed),
                    view=self._buildReviewView(refreshed),
                )
            await self._refreshSuggestionBoards(interaction.guild)
            threadId = int(refreshed.get("threadId") or 0)
            targetThread = await self._getMessageChannel(threadId)
            if isinstance(targetThread, discord.Thread):
                try:
                    await targetThread.send(
                        f"Suggestion #{suggestionId} marked {str(newStatus or '').strip().title()} by {interaction.user.mention}."
                        + (f" Note: {reviewNote}" if reviewNote else ""),
                        allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if str(newStatus or "").strip().upper() == "APPROVED" and self._freedcampConfigured():
                jumpUrl = f"https://discord.com/channels/{interaction.guild.id}/{threadId}" if threadId > 0 else ""
                content = str(row.get("content") or "").strip()
                if jumpUrl:
                    content = f"{content}\n\nJump to discussion: {jumpUrl}"
                try:
                    freedcampId = await addSuggestionToFreedcamp(
                        suggestionId=suggestionId,
                        submitterName=self._displayNameForUser(interaction.guild, int(row.get("submitterId") or 0)),
                        content=content,
                        apiKey=str(getattr(config, "freedcampApiKey", "") or "").strip(),
                        keySecret=str(getattr(config, "freedcampSecret", "") or "").strip(),
                        projectId=int(getattr(config, "freedcampProjectId", 0) or 0),
                        taskGroupId=int(getattr(config, "freedcampTaskGroupId", 0) or 0),
                    )
                    if int(freedcampId or 0) > 0:
                        await setSuggestionFreedcampId(suggestionId, int(freedcampId))
                        freedcampUrl = (
                            f"https://freedcamp.com/view/{int(getattr(config, 'freedcampProjectId', 0) or 0)}"
                            f"/tasks/{int(freedcampId)}"
                        )
                        if isinstance(targetThread, discord.Thread):
                            try:
                                await targetThread.send(
                                    f"Suggestion added to Freedcamp: {freedcampUrl}",
                                    allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                                )
                            except (discord.Forbidden, discord.HTTPException):
                                pass
                        refreshedWithTask = await getSuggestion(suggestionId)
                        if refreshedWithTask is not None and targetMessage is not None:
                            await interactionRuntime.safeMessageEdit(
                                targetMessage,
                                embed=buildSuggestionEmbed(refreshedWithTask),
                                view=self._buildReviewView(refreshedWithTask),
                            )
                except Exception:
                    log.exception("Failed to add suggestion #%s to Freedcamp.", suggestionId)
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Suggestion #{int(suggestionId)} marked {str(newStatus or '').strip().title()}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SuggestionCog(bot))
