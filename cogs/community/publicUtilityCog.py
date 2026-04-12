from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.community.publicUtility import (
    RoleMenuView,
    configuredRoleMenus,
    deleteReactionRoleEntry,
    getReactionRoleEntry,
    listReactionRoleEntries,
    menuConfig,
    normalizeEmojiKey,
    reactionRoleSummaryLine,
    resolveAssignableRole,
    upsertReactionRoleEntry,
)
from runtime import cogGuards as runtimeCogGuards


class PublicUtilityCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        for menuKey in configuredRoleMenus(config).keys():
            self.bot.add_view(RoleMenuView(configModule=config, menuKey=menuKey))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        channelId = int(getattr(config, "welcomeChannelId", 0) or 0)
        if channelId <= 0:
            return
        channel = member.guild.get_channel(channelId)
        if not isinstance(channel, discord.TextChannel):
            return
        template = str(
            getattr(
                config,
                "welcomeMessageTemplate",
                "Welcome to **{guild}**, {mention}.",
            )
            or "Welcome to **{guild}**, {mention}."
        )
        try:
            await channel.send(
                template.format(
                    mention=member.mention,
                    user=member.display_name,
                    guild=member.guild.name,
                )
            )
        except (discord.Forbidden, discord.HTTPException, KeyError):
            return

    @app_commands.command(name="post-role-menu", description="Post a public self-role menu.")
    @app_commands.rename(menu_key="menu-key")
    async def postRoleMenu(self, interaction: discord.Interaction, menu_key: str) -> None:
        if await self._requireAdminOrManageGuild(interaction) is None:
            return
        row = menuConfig(config, menu_key)
        if row is None:
            return await self._safeReply(interaction, "That role menu key is not configured.")
        title = str(row.get("title") or "Choose Your Roles").strip()[:256]
        description = str(row.get("description") or "Select the roles you want from the menu below.").strip()
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blurple(),
        )
        await self._safeReply(
            interaction,
            embed=embed,
            view=RoleMenuView(configModule=config, menuKey=menu_key),
        )

    @app_commands.command(name="post-reaction-role", description="Post a reaction-role message in this channel.")
    async def postReactionRole(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
    ) -> None:
        if await self._requireAdminOrManageGuild(interaction) is None:
            return
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await self._safeReply(interaction, "Use this command in a text channel or thread.")
        embed = discord.Embed(
            title=str(title or "Choose Your Roles").strip()[:256],
            description=str(description or "React below to add or remove roles.").strip()[:4096],
            color=discord.Color.blurple(),
        )
        try:
            message = await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            return await self._safeReply(interaction, "I could not post the reaction-role message here.")
        await self._safeReply(
            interaction,
            f"Posted reaction-role message: `{int(message.id)}` in {channel.mention}.",
        )

    @app_commands.command(name="add-reaction-role", description="Link an emoji reaction on a message to a role.")
    @app_commands.rename(message_id="message-id")
    async def addReactionRole(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
        role: discord.Role,
    ) -> None:
        member = await self._requireAdminOrManageGuild(interaction)
        if member is None:
            return
        if not interaction.guild:
            return await self._safeReply(interaction, "Use this command in a server.")
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await self._safeReply(interaction, "Use this command in the channel that has the target message.")
        try:
            targetMessageId = int(str(message_id or "").strip())
        except (TypeError, ValueError):
            return await self._safeReply(interaction, "Message ID must be a number.")
        emojiKey = normalizeEmojiKey(emoji)
        if not emojiKey:
            return await self._safeReply(interaction, "Emoji is required.")

        me = interaction.guild.me or interaction.guild.get_member(int(getattr(self.bot.user, "id", 0) or 0))
        if me is None:
            try:
                me = await interaction.guild.fetch_member(int(getattr(self.bot.user, "id", 0) or 0))
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                me = None
        if me is None:
            return await self._safeReply(interaction, "Jane could not resolve her member record in this server.")
        if resolveAssignableRole(guild=interaction.guild, roleId=int(role.id), botMember=me) is None:
            return await self._safeReply(interaction, "I cannot manage that role. Move my bot role above it and try again.")

        try:
            targetMessage = await channel.fetch_message(targetMessageId)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return await self._safeReply(interaction, "I could not find that message in this channel.")

        try:
            await targetMessage.add_reaction(emoji)
        except (discord.Forbidden, discord.HTTPException):
            return await self._safeReply(interaction, "I could not add that reaction to the message.")

        await upsertReactionRoleEntry(
            guildId=int(interaction.guild.id),
            channelId=int(channel.id),
            messageId=int(targetMessage.id),
            emojiKey=emojiKey,
            roleId=int(role.id),
        )
        await self._safeReply(
            interaction,
            f"Linked `{emojiKey}` to {role.mention} on message `{int(targetMessage.id)}`.",
        )

    @app_commands.command(name="remove-reaction-role", description="Remove an emoji-to-role mapping from a message.")
    @app_commands.rename(message_id="message-id")
    async def removeReactionRole(
        self,
        interaction: discord.Interaction,
        message_id: str,
        emoji: str,
    ) -> None:
        if await self._requireAdminOrManageGuild(interaction) is None:
            return
        try:
            targetMessageId = int(str(message_id or "").strip())
        except (TypeError, ValueError):
            return await self._safeReply(interaction, "Message ID must be a number.")
        emojiKey = normalizeEmojiKey(emoji)
        if not emojiKey:
            return await self._safeReply(interaction, "Emoji is required.")

        existing = await getReactionRoleEntry(messageId=targetMessageId, emojiKey=emojiKey)
        if existing is None:
            return await self._safeReply(interaction, "That reaction-role entry does not exist.")

        await deleteReactionRoleEntry(messageId=targetMessageId, emojiKey=emojiKey)
        await self._safeReply(
            interaction,
            f"Removed `{emojiKey}` from message `{targetMessageId}`.",
        )

    @app_commands.command(name="list-reaction-roles", description="Show the reaction-role mappings for a message.")
    @app_commands.rename(message_id="message-id")
    async def listReactionRoles(self, interaction: discord.Interaction, message_id: str) -> None:
        if await self._requireAdminOrManageGuild(interaction) is None:
            return
        try:
            targetMessageId = int(str(message_id or "").strip())
        except (TypeError, ValueError):
            return await self._safeReply(interaction, "Message ID must be a number.")
        rows = await listReactionRoleEntries(messageId=targetMessageId)
        if not rows:
            return await self._safeReply(interaction, "No reaction roles are configured for that message.")
        description = "\n".join(
            reactionRoleSummaryLine(entry=row, guild=interaction.guild)
            for row in rows
        )[:4000]
        embed = discord.Embed(
            title=f"Reaction Roles for {targetMessageId}",
            description=description,
            color=discord.Color.blurple(),
        )
        await self._safeReply(interaction, embed=embed)

    async def _resolveBotMember(self, guild: discord.Guild) -> discord.Member | None:
        botUserId = int(getattr(self.bot.user, "id", 0) or 0)
        if botUserId <= 0:
            return None
        member = guild.me or guild.get_member(botUserId)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(botUserId)
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return None

    async def _resolvePayloadMember(
        self,
        guild: discord.Guild,
        payload: discord.RawReactionActionEvent,
    ) -> discord.Member | None:
        if isinstance(payload.member, discord.Member):
            return payload.member
        member = guild.get_member(int(payload.user_id))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(int(payload.user_id))
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return None

    async def _handleReactionRoleEvent(
        self,
        payload: discord.RawReactionActionEvent,
        *,
        adding: bool,
    ) -> None:
        if payload.guild_id is None:
            return
        if self.bot.user and int(payload.user_id) == int(self.bot.user.id):
            return
        guild = self.bot.get_guild(int(payload.guild_id))
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(int(payload.guild_id))
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                return
        entry = await getReactionRoleEntry(
            messageId=int(payload.message_id),
            emojiKey=normalizeEmojiKey(payload.emoji),
        )
        if entry is None or int(entry.get("guildId") or 0) != int(guild.id):
            return
        member = await self._resolvePayloadMember(guild, payload)
        if member is None or member.bot:
            return
        botMember = await self._resolveBotMember(guild)
        if botMember is None:
            return
        role = resolveAssignableRole(
            guild=guild,
            roleId=int(entry.get("roleId") or 0),
            botMember=botMember,
        )
        if role is None:
            return
        try:
            if adding:
                if role not in member.roles:
                    await member.add_roles(role, reason="Reaction role opt-in.")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason="Reaction role opt-out.")
        except (discord.Forbidden, discord.HTTPException):
            return

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handleReactionRoleEvent(payload, adding=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        await self._handleReactionRoleEvent(payload, adding=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PublicUtilityCog(bot))
