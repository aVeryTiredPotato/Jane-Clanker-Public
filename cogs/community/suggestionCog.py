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

    async def _resolveSuggestionChannel(
        self,
        guild: discord.Guild,
        fallbackChannel: discord.ForumChannel | None,
    ) -> discord.ForumChannel | None:
        configuredId = int(getattr(config, "suggestionChannelId", 0) or 0)
        if configuredId > 0:
            channel = await self._getMessageChannel(configuredId)
            if channel is not None:
                return channel
        if isinstance(fallbackChannel, discord.ForumChannel):
            return fallbackChannel
        return None
    
    async def _resolveSuggestionForumChannel(
        self,
        guild: discord.Guild,
        fallbackChannel: discord.ForumChannel | None,
    ) -> discord.ForumChannel | None:
        configuredId = int(getattr(config, "suggestionForumChannelId", 0) or 0)
        if configuredId > 0:
            channel = await self._getMessageChannel(configuredId)
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

    async def _createThread(self, channelId: int, suggestionText: str) -> discord.ForumThread | None:
        channel = await self._getMessageChannel(channelId)
        if not isinstance(channel, discord.ForumChannel):
            return None
        try:
            thread = await channel.create_thread(
                name=f"{suggestionText[:50]}",
                content=suggestionText,
                applied_tags=[discord.ForumTag(name="Suggestion")],
            )
            return thread
        except (discord.Forbidden, discord.HTTPException):
            return None

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
        
        targetForum = await self._resolveSuggestionForumChannel(interaction.guild)
        if targetForum is None:
            await self._safeEphemeral(interaction, "I could not find a valid suggestion forum.")
            return

        targetThread = await self._createThread(targetForum.id, suggestion_text)

        suggestionId = await createSuggestion(
            guildId=int(interaction.guild.id),
            channelId=int(targetChannel.id),
            submitterId=int(interaction.user.id),
            content=str(suggestion_text or "").strip(),
            anonymous=bool(anonymous),
        )
        await setSuggestionThreadId(suggestionId, targetThread.id)
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
            targetThread = await self._getMessageChannel(int(refreshed.get("threadId") or 0))
            if isinstance(targetThread, discord.Thread):
                try:
                    await targetThread.send(
                        f"Suggestion #{suggestionId} marked {str(newStatus or '').strip().title()} by {interaction.user.mention}."
                        + (f" Note: {reviewNote}" if reviewNote else ""),
                        allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
            if newStatus == "APPROVED":
                suggestionText = str(row.get("content"))
                jumpUrl = f"https://discord.com/channels/{interaction.guild.id}/{row.get("threadId")}"
                content = f"{suggestionText}\n\nJump to discussion: {jumpUrl}"
                try:
                    id = await addSuggestionToFreedcamp(
                        suggestionId=suggestionId,
                        submitterId=int(row.get("submitterId") or 0),
                        content=content,
                        apiKey=str(getattr(config, "freedcampApiKey", "") or "").strip(),
                        projectId=int(getattr(config, "freedcampProjectId", 0) or 0),
                        taskGroupId=int(getattr(config, "freedcampTaskGroupId", 0) or 0),
                    )
                    await setSuggestionFreedcampId(suggestionId, int(id))
                    url = f"https://freedcamp.com/view/{getattr(config, 'freedcampProjectId', 0)}/tasks/{id}"
                    if isinstance(targetThread, discord.Thread):
                        try:
                            await targetThread.send(
                                f"Suggestion added to Freedcamp: {url}",
                                allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                            )
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                except Exception:
                    log.exception("Failed to add suggestion #%s to Freedcamp.", suggestionId)
                
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Suggestion #{int(suggestionId)} marked {str(newStatus or '').strip().title()}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SuggestionCog(bot))
