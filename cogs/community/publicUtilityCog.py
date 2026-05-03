from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.community.publicUtility import (
    ButtonRoleView,
    RoleMenuView,
    addBlockedSelfRole,
    createButtonRoleEntry,
    deleteButtonRoleEntry,
    groupButtonRoleEntriesByMessage,
    configuredRoleMenus,
    isBlockedSelfRole,
    listAllButtonRoleEntries,
    listBlockedSelfRoles,
    listButtonRoleEntries,
    menuConfig,
    removeBlockedSelfRole,
    resolveAssignableRole,
)
from runtime import cogGuards as runtimeCogGuards
from runtime import permissions as runtimePermissions


class PublicUtilityCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @staticmethod
    def _configuredRoleIdSet(attrName: str) -> set[int]:
        raw = getattr(config, attrName, [])
        if not isinstance(raw, (list, tuple, set)):
            return set()
        out: set[int] = set()
        for value in raw:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                continue
            if parsed > 0:
                out.add(parsed)
        return out

    async def _requireReactionRoleCommandAccess(
        self,
        interaction: discord.Interaction,
    ) -> discord.Member | None:
        member = await self._requireGuildMember(interaction)
        if member is None:
            return None
        allowedRoleIds = self._configuredRoleIdSet("reactionRoleCommandRoleIds")
        if not allowedRoleIds:
            if runtimePermissions.hasAdminOrManageGuild(member):
                return member
            await self._safeReply(interaction, "Administrator or manage-server required.")
            return None
        memberRoleIds = {int(role.id) for role in member.roles}
        if memberRoleIds & allowedRoleIds:
            return member
        await self._safeReply(interaction, "You do not have permission to manage reaction roles.")
        return None

    async def _requireReactionRolePolicyAccess(
        self,
        interaction: discord.Interaction,
    ) -> discord.Member | None:
        member = await self._requireGuildMember(interaction)
        if member is None:
            return None
        policyRoleIds = self._configuredRoleIdSet("reactionRolePolicyRoleIds")
        if not policyRoleIds:
            policyRoleIds = self._configuredRoleIdSet("reactionRoleCommandRoleIds")
        if not policyRoleIds:
            if runtimePermissions.hasAdminOrManageGuild(member):
                return member
            await self._safeReply(interaction, "Administrator or manage-server required.")
            return None
        memberRoleIds = {int(role.id) for role in member.roles}
        if memberRoleIds & policyRoleIds:
            return member
        await self._safeReply(interaction, "You do not have permission to change reaction-role safety rules.")
        return None

    @staticmethod
    def _buildReactionRoleEmbed(*, title: str, description: str) -> discord.Embed:
        safeTitle = str(title or "REACTION ROLES").strip()[:256]
        safeDescription = str(description or "").replace("\\n", "\n").strip()[:4096]
        return discord.Embed(
            title=safeTitle or "REACTION ROLES",
            description=safeDescription or "Click a button below to add or remove roles.",
            color=discord.Color.blurple(),
        )

    async def cog_load(self) -> None:
        for menuKey in configuredRoleMenus(config).keys():
            self.bot.add_view(RoleMenuView(configModule=config, menuKey=menuKey))
        buttonEntries = await listAllButtonRoleEntries()
        for messageId, entries in groupButtonRoleEntriesByMessage(buttonEntries).items():
            self.bot.add_view(ButtonRoleView(entries=entries), message_id=int(messageId))

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

    async def _refreshButtonRoleMessage(
        self,
        *,
        channel: discord.TextChannel | discord.Thread,
        messageId: int,
    ) -> bool:
        entries = await listButtonRoleEntries(messageId=messageId)
        if not entries:
            return False
        try:
            message = await channel.fetch_message(int(messageId))
            await message.edit(view=ButtonRoleView(entries=entries))
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return False
        self.bot.add_view(ButtonRoleView(entries=entries), message_id=int(messageId))
        return True

    async def _isRoleAllowedForSelfAssign(
        self,
        *,
        guild: discord.Guild,
        role: discord.Role,
        botMember: discord.Member,
    ) -> bool:
        if await isBlockedSelfRole(guildId=int(guild.id), roleId=int(role.id)):
            return False
        return resolveAssignableRole(guild=guild, roleId=int(role.id), botMember=botMember) is not None

    async def _resolveBotMember(self, guild: discord.Guild) -> discord.Member | None:
        member = guild.me or guild.get_member(int(getattr(self.bot.user, "id", 0) or 0))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(int(getattr(self.bot.user, "id", 0) or 0))
        except (discord.Forbidden, discord.HTTPException, discord.NotFound):
            return None

    @app_commands.command(
        name="create-reaction-role",
        description="Post a role message with one button.",
    )
    async def createReactionRole(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        role: discord.Role,
        label: str,
        emoji: Optional[str] = None,
    ) -> None:
        if await self._requireReactionRoleCommandAccess(interaction) is None:
            return
        if not interaction.guild:
            return await self._safeReply(interaction, "Use this command in a server.")
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await self._safeReply(interaction, "Use this command in a text channel or thread.")
        botMember = await self._resolveBotMember(interaction.guild)
        if botMember is None:
            return await self._safeReply(interaction, "Jane could not resolve her member record in this server.")
        if not await self._isRoleAllowedForSelfAssign(guild=interaction.guild, role=role, botMember=botMember):
            return await self._safeReply(interaction, "That role is blocked or I cannot manage it.")
        try:
            message = await channel.send(
                embed=self._buildReactionRoleEmbed(title=title, description=description),
            )
        except (discord.Forbidden, discord.HTTPException):
            return await self._safeReply(interaction, "I could not post the button-role message here.")

        safeLabel = str(label or role.name).strip()[:80] or role.name
        await createButtonRoleEntry(
            guildId=int(interaction.guild.id),
            channelId=int(channel.id),
            messageId=int(message.id),
            roleId=int(role.id),
            buttonLabel=safeLabel,
            emojiSpec=str(emoji or "").strip(),
            orderIndex=0,
        )
        refreshed = await self._refreshButtonRoleMessage(channel=channel, messageId=int(message.id))
        if not refreshed:
            return await self._safeReply(interaction, "The message was posted, but I could not attach the button.")
        await self._safeReply(
            interaction,
            f"Created reaction-role message `{int(message.id)}` and linked `{safeLabel}` to {role.mention}.",
        )

    @app_commands.command(
        name="create-reaction-role-bulk",
        description="Post a role message with up to seven buttons.",
    )
    @app_commands.rename(
        role_1="role-1",
        label_1="label-1",
        emoji_1="emoji-1",
        role_2="role-2",
        label_2="label-2",
        emoji_2="emoji-2",
        role_3="role-3",
        label_3="label-3",
        emoji_3="emoji-3",
        role_4="role-4",
        label_4="label-4",
        emoji_4="emoji-4",
        role_5="role-5",
        label_5="label-5",
        emoji_5="emoji-5",
        role_6="role-6",
        label_6="label-6",
        emoji_6="emoji-6",
        role_7="role-7",
        label_7="label-7",
        emoji_7="emoji-7",
    )
    async def createReactionRoleBulk(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        role_1: discord.Role,
        label_1: str,
        emoji_1: Optional[str] = None,
        role_2: Optional[discord.Role] = None,
        label_2: Optional[str] = None,
        emoji_2: Optional[str] = None,
        role_3: Optional[discord.Role] = None,
        label_3: Optional[str] = None,
        emoji_3: Optional[str] = None,
        role_4: Optional[discord.Role] = None,
        label_4: Optional[str] = None,
        emoji_4: Optional[str] = None,
        role_5: Optional[discord.Role] = None,
        label_5: Optional[str] = None,
        emoji_5: Optional[str] = None,
        role_6: Optional[discord.Role] = None,
        label_6: Optional[str] = None,
        emoji_6: Optional[str] = None,
        role_7: Optional[discord.Role] = None,
        label_7: Optional[str] = None,
        emoji_7: Optional[str] = None,
    ) -> None:
        if await self._requireReactionRoleCommandAccess(interaction) is None:
            return
        if not interaction.guild:
            return await self._safeReply(interaction, "Use this command in a server.")
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await self._safeReply(interaction, "Use this command in a text channel or thread.")
        botMember = await self._resolveBotMember(interaction.guild)
        if botMember is None:
            return await self._safeReply(interaction, "Jane could not resolve her member record in this server.")

        rawRows = [
            (role_1, label_1, emoji_1),
            (role_2, label_2, emoji_2),
            (role_3, label_3, emoji_3),
            (role_4, label_4, emoji_4),
            (role_5, label_5, emoji_5),
            (role_6, label_6, emoji_6),
            (role_7, label_7, emoji_7),
        ]
        validRows: list[tuple[discord.Role, str, str]] = []
        seenRoleIds: set[int] = set()
        for index, (roleValue, labelValue, emojiValue) in enumerate(rawRows, start=1):
            if roleValue is None:
                if labelValue or emojiValue:
                    return await self._safeReply(
                        interaction,
                        f"Slot {index} needs a role if you fill in the label or emoji.",
                    )
                continue
            safeLabel = str(labelValue or roleValue.name).strip()[:80]
            if not safeLabel:
                return await self._safeReply(interaction, f"Slot {index} is missing a label.")
            if int(roleValue.id) in seenRoleIds:
                return await self._safeReply(interaction, f"Do not repeat {roleValue.mention} in the same command.")
            if not await self._isRoleAllowedForSelfAssign(
                guild=interaction.guild,
                role=roleValue,
                botMember=botMember,
            ):
                return await self._safeReply(
                    interaction,
                    f"{roleValue.mention} is blocked or I cannot manage it.",
                )
            validRows.append((roleValue, safeLabel, str(emojiValue or "").strip()))
            seenRoleIds.add(int(roleValue.id))
        if not validRows:
            return await self._safeReply(interaction, "Add at least one role button.")

        try:
            message = await channel.send(
                embed=self._buildReactionRoleEmbed(title=title, description=description),
            )
        except (discord.Forbidden, discord.HTTPException):
            return await self._safeReply(interaction, "I could not post the reaction-role message here.")

        addedLines: list[str] = []
        for orderIndex, (roleValue, safeLabel, emojiSpec) in enumerate(validRows):
            await createButtonRoleEntry(
                guildId=int(interaction.guild.id),
                channelId=int(channel.id),
                messageId=int(message.id),
                roleId=int(roleValue.id),
                buttonLabel=safeLabel,
                emojiSpec=emojiSpec,
                orderIndex=orderIndex,
            )
            addedLines.append(f"{safeLabel} -> {roleValue.mention}")

        refreshed = await self._refreshButtonRoleMessage(channel=channel, messageId=int(message.id))
        if not refreshed:
            return await self._safeReply(interaction, "The message was posted, but I could not attach the buttons.")
        await self._safeReply(
            interaction,
            "Created reaction-role message "
            f"`{int(message.id)}` with {len(validRows)} button(s):\n" + "\n".join(addedLines),
        )

    @app_commands.command(
        name="remove-reaction-role",
        description="Remove a role button from a message.",
    )
    @app_commands.rename(message_id="message-id")
    async def removeReactionRole(
        self,
        interaction: discord.Interaction,
        message_id: str,
        role: discord.Role,
    ) -> None:
        if await self._requireReactionRoleCommandAccess(interaction) is None:
            return
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return await self._safeReply(interaction, "Use this command in the channel that has the target message.")
        try:
            targetMessageId = int(str(message_id or "").strip())
        except (TypeError, ValueError):
            return await self._safeReply(interaction, "Message ID must be a number.")
        await deleteButtonRoleEntry(messageId=targetMessageId, roleId=int(role.id))
        remainingEntries = await listButtonRoleEntries(messageId=targetMessageId)
        if remainingEntries:
            await self._refreshButtonRoleMessage(channel=channel, messageId=targetMessageId)
        else:
            try:
                message = await channel.fetch_message(targetMessageId)
                await message.edit(view=None)
            except (discord.Forbidden, discord.HTTPException, discord.NotFound):
                pass
        await self._safeReply(
            interaction,
            f"Removed {role.mention} from button-role message `{targetMessageId}`.",
        )

    @app_commands.command(
        name="block-reaction-role",
        description="Block a role from being used in self-assign reaction-role buttons.",
    )
    async def blockReactionRole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if await self._requireReactionRolePolicyAccess(interaction) is None:
            return
        if not interaction.guild:
            return await self._safeReply(interaction, "Use this command in a server.")
        await addBlockedSelfRole(guildId=int(interaction.guild.id), roleId=int(role.id))
        await self._safeReply(interaction, f"Blocked {role.mention} from future reaction-role buttons.")

    @app_commands.command(
        name="unblock-reaction-role",
        description="Allow a previously blocked role to be used in reaction-role buttons again.",
    )
    async def unblockReactionRole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        if await self._requireReactionRolePolicyAccess(interaction) is None:
            return
        if not interaction.guild:
            return await self._safeReply(interaction, "Use this command in a server.")
        await removeBlockedSelfRole(guildId=int(interaction.guild.id), roleId=int(role.id))
        await self._safeReply(interaction, f"Unblocked {role.mention} for reaction-role buttons.")

    @app_commands.command(
        name="list-blocked-reaction-roles",
        description="Show which roles are blocked from self-assign reaction-role buttons.",
    )
    async def listBlockedReactionRoles(self, interaction: discord.Interaction) -> None:
        if await self._requireReactionRolePolicyAccess(interaction) is None:
            return
        if not interaction.guild:
            return await self._safeReply(interaction, "Use this command in a server.")
        rows = await listBlockedSelfRoles(guildId=int(interaction.guild.id))
        if not rows:
            return await self._safeReply(interaction, "No reaction-role blocks are configured.")
        description = "\n".join(
            interaction.guild.get_role(int(row.get("roleId") or 0)).mention
            if interaction.guild.get_role(int(row.get("roleId") or 0)) is not None
            else f"<@&{int(row.get('roleId') or 0)}>"
            for row in rows
        )[:4000]
        embed = discord.Embed(
            title="Blocked Reaction Roles",
            description=description,
            color=discord.Color.blurple(),
        )
        await self._safeReply(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PublicUtilityCog(bot))
