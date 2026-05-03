from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.operations.linkHub import rendering as hubRendering
from features.operations.linkHub import service as hubService
from runtime import cogGuards as runtimeCogGuards
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions
from runtime import webhooks as runtimeWebhooks

log = logging.getLogger(__name__)


def _normalizeRoleIds(values: object) -> set[int]:
    out: set[int] = set()
    if not isinstance(values, (list, tuple, set)):
        return out
    for value in values:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            out.add(parsed)
    return out


class LinkHubSectionModal(discord.ui.Modal, title="Add Section"):
    sectionName = discord.ui.TextInput(
        label="Section name",
        required=True,
        max_length=120,
        placeholder="Example: ORBAT",
    )
    sectionDescription = discord.ui.TextInput(
        label="Section description (optional)",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
        placeholder="Short note about what lives in this section.",
    )

    def __init__(self, *, cog: "LinkHubCog", hubId: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.hubId = int(hubId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handleAddSectionModal(
            interaction,
            hubId=self.hubId,
            sectionName=str(self.sectionName.value or ""),
            sectionDescription=str(self.sectionDescription.value or ""),
        )


class LinkHubEntryModal(discord.ui.Modal, title="Add Link"):
    sectionName = discord.ui.TextInput(
        label="Section name",
        required=True,
        max_length=120,
        placeholder="Use the exact section name.",
    )
    entryTitle = discord.ui.TextInput(
        label="Link title",
        required=True,
        max_length=120,
        placeholder="Example: CE Event Log",
    )
    entryType = discord.ui.TextInput(
        label="Type",
        required=True,
        max_length=20,
        placeholder="document or webhook",
        default="document",
    )
    entryUrl = discord.ui.TextInput(
        label="URL",
        required=True,
        max_length=500,
        placeholder="https://...",
    )
    entryNote = discord.ui.TextInput(
        label="Note (optional)",
        required=False,
        max_length=300,
        style=discord.TextStyle.paragraph,
        placeholder="Short reminder about what this link is for.",
    )

    def __init__(self, *, cog: "LinkHubCog", hubId: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.hubId = int(hubId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handleAddEntryModal(
            interaction,
            hubId=self.hubId,
            sectionName=str(self.sectionName.value or ""),
            entryTitle=str(self.entryTitle.value or ""),
            entryType=str(self.entryType.value or ""),
            entryUrl=str(self.entryUrl.value or ""),
            entryNote=str(self.entryNote.value or ""),
        )


class LinkHubRenameSectionModal(discord.ui.Modal, title="Rename Section"):
    currentName = discord.ui.TextInput(
        label="Current section name",
        required=True,
        max_length=120,
        placeholder="Exact current section name",
    )
    newName = discord.ui.TextInput(
        label="New section name",
        required=True,
        max_length=120,
        placeholder="New section name",
    )
    newDescription = discord.ui.TextInput(
        label="New description (optional)",
        required=False,
        max_length=500,
        style=discord.TextStyle.paragraph,
        placeholder="Leave blank to clear it.",
    )

    def __init__(self, *, cog: "LinkHubCog", hubId: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.hubId = int(hubId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handleRenameSectionModal(
            interaction,
            hubId=self.hubId,
            currentName=str(self.currentName.value or ""),
            newName=str(self.newName.value or ""),
            newDescription=str(self.newDescription.value or ""),
        )


class LinkHubRemoveEntryModal(discord.ui.Modal, title="Remove Link"):
    sectionName = discord.ui.TextInput(
        label="Section name",
        required=True,
        max_length=120,
        placeholder="Exact section name",
    )
    entryTitle = discord.ui.TextInput(
        label="Link title",
        required=True,
        max_length=120,
        placeholder="Exact link title",
    )

    def __init__(self, *, cog: "LinkHubCog", hubId: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.hubId = int(hubId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handleRemoveEntryModal(
            interaction,
            hubId=self.hubId,
            sectionName=str(self.sectionName.value or ""),
            entryTitle=str(self.entryTitle.value or ""),
        )


class LinkHubRemoveSectionModal(discord.ui.Modal, title="Remove Section"):
    sectionName = discord.ui.TextInput(
        label="Section name",
        required=True,
        max_length=120,
        placeholder="Exact section name",
    )
    confirmText = discord.ui.TextInput(
        label="Type DELETE to confirm",
        required=True,
        max_length=10,
        placeholder="DELETE",
    )

    def __init__(self, *, cog: "LinkHubCog", hubId: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.hubId = int(hubId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handleRemoveSectionModal(
            interaction,
            hubId=self.hubId,
            sectionName=str(self.sectionName.value or ""),
            confirmText=str(self.confirmText.value or ""),
        )


class LinkHubManageView(discord.ui.View):
    def __init__(self, *, cog: "LinkHubCog", hubId: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.hubId = int(hubId)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = await self.cog._requireHubManager(interaction)
        return member is not None

    @discord.ui.button(
        label="Add Section",
        style=discord.ButtonStyle.primary,
        custom_id="link_hub:add_section",
        row=0,
    )
    async def addSectionBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            LinkHubSectionModal(cog=self.cog, hubId=self.hubId),
        )

    @discord.ui.button(
        label="Add Link",
        style=discord.ButtonStyle.success,
        custom_id="link_hub:add_link",
        row=0,
    )
    async def addLinkBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            LinkHubEntryModal(cog=self.cog, hubId=self.hubId),
        )

    @discord.ui.button(
        label="Rename Section",
        style=discord.ButtonStyle.secondary,
        custom_id="link_hub:rename_section",
        row=0,
    )
    async def renameSectionBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            LinkHubRenameSectionModal(cog=self.cog, hubId=self.hubId),
        )

    @discord.ui.button(
        label="Remove Link",
        style=discord.ButtonStyle.secondary,
        custom_id="link_hub:remove_link",
        row=1,
    )
    async def removeLinkBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            LinkHubRemoveEntryModal(cog=self.cog, hubId=self.hubId),
        )

    @discord.ui.button(
        label="Remove Section",
        style=discord.ButtonStyle.danger,
        custom_id="link_hub:remove_section",
        row=1,
    )
    async def removeSectionBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionSendModal(
            interaction,
            LinkHubRemoveSectionModal(cog=self.cog, hubId=self.hubId),
        )

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.secondary,
        custom_id="link_hub:refresh",
        row=1,
    )
    async def refreshBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        ok, message = await self.cog.refreshHub(self.hubId)
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message if ok else f"Refresh failed: {message}",
            ephemeral=True,
        )


class LinkHubCog(runtimeCogGuards.InteractionGuardMixin, commands.Cog):
    linkHubGroup = app_commands.Group(name="link-hub", description="Manage Jane's internal link hub.")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._hubLocks: dict[int, asyncio.Lock] = {}

    async def cog_load(self) -> None:
        restored = 0
        for row in await hubService.listRenderableHubs():
            rootMessageId = int(row.get("rootMessageId") or 0)
            if rootMessageId <= 0:
                continue
            self.bot.add_view(LinkHubManageView(cog=self, hubId=int(row["hubId"])), message_id=rootMessageId)
            restored += 1
        log.info("Link hub persistent views restored: %d", restored)

    def _getHubLock(self, hubId: int) -> asyncio.Lock:
        lock = self._hubLocks.get(int(hubId))
        if lock is None:
            lock = asyncio.Lock()
            self._hubLocks[int(hubId)] = lock
        return lock

    def _managerRoleIds(self) -> set[int]:
        return _normalizeRoleIds(getattr(config, "masterLinkHubManagerRoleIds", []))

    def _allowedUserIds(self) -> set[int]:
        return _normalizeRoleIds(getattr(config, "opsAllowedUserIds", []))

    def _canManage(self, member: discord.Member) -> bool:
        if runtimePermissions.hasAdminOrManageGuild(member):
            return True
        if int(member.id) in self._allowedUserIds():
            return True
        allowedRoleIds = self._managerRoleIds()
        if not allowedRoleIds:
            return False
        return any(int(role.id) in allowedRoleIds for role in member.roles)

    async def _requireHubManager(self, interaction: discord.Interaction) -> discord.Member | None:
        member = await self._requireGuildMember(interaction)
        if member is None:
            return None
        if self._canManage(member):
            return member
        await self._safeReply(interaction, "You do not have permission to manage the master link hub.")
        return None

    async def _getMessageChannel(self, channelId: int) -> discord.TextChannel | discord.Thread | None:
        if int(channelId) <= 0:
            return None
        channel = self.bot.get_channel(int(channelId))
        if channel is None:
            channel = await interactionRuntime.safeFetchChannel(self.bot, int(channelId))
        if isinstance(channel, (discord.TextChannel, discord.Thread)):
            return channel
        return None

    async def _fetchChannelMessage(
        self,
        *,
        channel: discord.TextChannel | discord.Thread,
        messageId: int,
    ) -> discord.Message | None:
        if int(messageId) <= 0:
            return None
        return await interactionRuntime.safeFetchMessage(channel, int(messageId))

    async def _upsertWebhookMessage(
        self,
        *,
        channel: discord.TextChannel | discord.Thread,
        messageId: int,
        embed: discord.Embed,
        view: discord.ui.View | None = None,
    ) -> discord.Message | None:
        existing = await self._fetchChannelMessage(channel=channel, messageId=int(messageId))
        if existing is not None:
            edited = await runtimeWebhooks.editOwnedWebhookMessage(
                botClient=self.bot,
                message=existing,
                webhookName=str(getattr(config, "masterLinkHubWebhookName", "Jane Master Directory") or "Jane Master Directory"),
                embed=embed,
                view=view,
                reason="Jane link hub refresh",
            )
            if edited:
                return existing

        return await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
            botClient=self.bot,
            channel=channel,
            webhookName=str(getattr(config, "masterLinkHubWebhookName", "Jane Master Directory") or "Jane Master Directory"),
            embed=embed,
            view=view,
            reason="Jane link hub post",
        )

    async def _deleteWebhookMessage(
        self,
        *,
        channel: discord.TextChannel | discord.Thread,
        messageId: int,
    ) -> bool:
        existing = await self._fetchChannelMessage(channel=channel, messageId=int(messageId))
        if existing is None:
            return False
        return await runtimeWebhooks.deleteOwnedWebhookMessage(
            botClient=self.bot,
            message=existing,
            webhookName=str(getattr(config, "masterLinkHubWebhookName", "Jane Master Directory") or "Jane Master Directory"),
            reason="Jane link hub cleanup",
        )

    async def refreshHub(self, hubId: int) -> tuple[bool, str]:
        async with self._getHubLock(int(hubId)):
            snapshot = await hubService.buildHubSnapshot(hubId=int(hubId))
            if snapshot is None:
                return False, "That link hub no longer exists."
            hub = dict(snapshot["hub"])
            sections = list(snapshot["sections"])

            channel = await self._getMessageChannel(int(hub.get("channelId") or 0))
            if channel is None:
                return False, "I could not reach the hub channel anymore."

            rootMessage = await self._upsertWebhookMessage(
                channel=channel,
                messageId=int(hub.get("rootMessageId") or 0),
                embed=hubRendering.buildHubOverviewEmbed(hub, sections),
                view=LinkHubManageView(cog=self, hubId=int(hub["hubId"])),
            )
            if rootMessage is None:
                return False, "I could not post the root webhook message. Check manage-webhooks permission."
            if int(hub.get("rootMessageId") or 0) != int(rootMessage.id):
                await hubService.setHubRootMessageId(hubId=int(hub["hubId"]), messageId=int(rootMessage.id))

            for section in sections:
                sectionMessage = await self._upsertWebhookMessage(
                    channel=channel,
                    messageId=int(section.get("messageId") or 0),
                    embed=hubRendering.buildSectionEmbed(hub, section),
                    view=None,
                )
                if sectionMessage is None:
                    return False, f"I could not post section `{section.get('title')}`."
                if int(section.get("messageId") or 0) != int(sectionMessage.id):
                    await hubService.setSectionMessageId(
                        sectionId=int(section["sectionId"]),
                        messageId=int(sectionMessage.id),
                    )

            return True, f"Refreshed `{str(hub.get('title') or 'link hub').strip()}`."

    async def _resolveHubForInteraction(
        self,
        interaction: discord.Interaction,
        hubId: int,
    ) -> dict[str, Any] | None:
        hub = await hubService.getHub(int(hubId))
        if hub is None:
            await self._safeReply(interaction, "That link hub does not exist anymore.")
            return None
        if interaction.guild is None or int(hub.get("guildId") or 0) != int(interaction.guild.id):
            await self._safeReply(interaction, "That link hub belongs to a different server.")
            return None
        return hub

    async def handleAddSectionModal(
        self,
        interaction: discord.Interaction,
        *,
        hubId: int,
        sectionName: str,
        sectionDescription: str,
    ) -> None:
        member = await self._requireHubManager(interaction)
        if member is None:
            return
        if await self._resolveHubForInteraction(interaction, hubId) is None:
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        created = await hubService.createSection(
            hubId=int(hubId),
            title=sectionName,
            description=sectionDescription,
        )
        if created is None:
            await self._safeReply(interaction, "That section name is already taken or invalid.")
            return
        ok, message = await self.refreshHub(int(hubId))
        await self._safeReply(interaction, message if ok else f"Section added, but refresh failed: {message}")

    async def handleAddEntryModal(
        self,
        interaction: discord.Interaction,
        *,
        hubId: int,
        sectionName: str,
        entryTitle: str,
        entryType: str,
        entryUrl: str,
        entryNote: str,
    ) -> None:
        member = await self._requireHubManager(interaction)
        if member is None:
            return
        if await self._resolveHubForInteraction(interaction, hubId) is None:
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        section = await hubService.getSectionByTitle(hubId=int(hubId), title=sectionName)
        if section is None:
            await self._safeReply(interaction, "I could not find that section. Use the exact section name.")
            return
        created = await hubService.createEntry(
            sectionId=int(section["sectionId"]),
            entryType=entryType,
            title=entryTitle,
            url=entryUrl,
            note=entryNote,
        )
        if created is None:
            await self._safeReply(
                interaction,
                "I could not add that link. Check the section name, type, URL, and make sure the title is not duplicated.",
            )
            return
        ok, message = await self.refreshHub(int(hubId))
        await self._safeReply(interaction, message if ok else f"Link added, but refresh failed: {message}")

    async def handleRenameSectionModal(
        self,
        interaction: discord.Interaction,
        *,
        hubId: int,
        currentName: str,
        newName: str,
        newDescription: str,
    ) -> None:
        member = await self._requireHubManager(interaction)
        if member is None:
            return
        if await self._resolveHubForInteraction(interaction, hubId) is None:
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        renamed = await hubService.renameSection(
            hubId=int(hubId),
            currentTitle=currentName,
            newTitle=newName,
            newDescription=newDescription,
        )
        if not renamed:
            await self._safeReply(
                interaction,
                "I could not rename that section. Make sure the current name exists and the new name is not already used.",
            )
            return
        ok, message = await self.refreshHub(int(hubId))
        await self._safeReply(interaction, message if ok else f"Section renamed, but refresh failed: {message}")

    async def handleRemoveEntryModal(
        self,
        interaction: discord.Interaction,
        *,
        hubId: int,
        sectionName: str,
        entryTitle: str,
    ) -> None:
        member = await self._requireHubManager(interaction)
        if member is None:
            return
        if await self._resolveHubForInteraction(interaction, hubId) is None:
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        section = await hubService.getSectionByTitle(hubId=int(hubId), title=sectionName)
        if section is None:
            await self._safeReply(interaction, "I could not find that section.")
            return
        entry = await hubService.getEntryByTitle(sectionId=int(section["sectionId"]), title=entryTitle)
        if entry is None:
            await self._safeReply(interaction, "I could not find that link in the selected section.")
            return
        await hubService.deleteEntry(sectionId=int(section["sectionId"]), entryId=int(entry["entryId"]))
        ok, message = await self.refreshHub(int(hubId))
        await self._safeReply(interaction, message if ok else f"Link removed, but refresh failed: {message}")

    async def handleRemoveSectionModal(
        self,
        interaction: discord.Interaction,
        *,
        hubId: int,
        sectionName: str,
        confirmText: str,
    ) -> None:
        member = await self._requireHubManager(interaction)
        if member is None:
            return
        if await self._resolveHubForInteraction(interaction, hubId) is None:
            return
        if str(confirmText or "").strip().upper() != "DELETE":
            await self._safeReply(interaction, "Type `DELETE` exactly to remove a section.")
            return
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True, thinking=True)
        section = await hubService.getSectionByTitle(hubId=int(hubId), title=sectionName)
        if section is None:
            await self._safeReply(interaction, "I could not find that section.")
            return
        channel = await self._getMessageChannel(int((await hubService.getHub(int(hubId)) or {}).get("channelId") or 0))
        if channel is not None and int(section.get("messageId") or 0) > 0:
            await self._deleteWebhookMessage(channel=channel, messageId=int(section["messageId"]))
        await hubService.deleteSection(hubId=int(hubId), sectionId=int(section["sectionId"]))
        ok, message = await self.refreshHub(int(hubId))
        await self._safeReply(interaction, message if ok else f"Section removed, but refresh failed: {message}")

    @linkHubGroup.command(name="post", description="Post or refresh the master link hub in this channel.")
    async def postLinkHub(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str = "",
    ) -> None:
        member = await self._requireHubManager(interaction)
        if member is None:
            return
        channel = interaction.channel
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            await self._safeReply(interaction, "Use this command in a text channel or thread.")
            return
        if not str(title or "").strip():
            await self._safeReply(interaction, "Title is required.")
            return

        hub = await hubService.createOrUpdateHub(
            guildId=int(member.guild.id),
            channelId=int(channel.id),
            title=title,
            description=description,
            createdBy=int(member.id),
        )
        ok, message = await self.refreshHub(int(hub["hubId"]))
        await self._safeReply(interaction, message if ok else f"Hub saved, but refresh failed: {message}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkHubCog(bot))
