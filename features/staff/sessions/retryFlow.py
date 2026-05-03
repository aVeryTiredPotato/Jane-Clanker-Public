from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

import discord
from discord import ui
from runtime import interaction as interactionRuntime

log = logging.getLogger(__name__)

_serviceModule: Any = None
_claimComponentInteraction: Optional[Callable[[int], bool]] = None
_safeInteractionDefer: Optional[Callable[..., Awaitable[None]]] = None
_sendEphemeralReply: Optional[Callable[..., Awaitable[None]]] = None
_sendInvalidComponentReply: Optional[Callable[..., Awaitable[None]]] = None
_safeInteractionReply: Optional[Callable[..., Awaitable[None]]] = None
_requestBgQueueMessageUpdate: Optional[Callable[..., Awaitable[None]]] = None
_loadFlagRules: Optional[Callable[[], Awaitable[tuple]]] = None
_resolveRobloxIdentity: Optional[Callable[[dict], Awaitable[Any]]] = None
_scanRobloxInventoryForAttendee: Optional[Callable[..., Awaitable[bool]]] = None
_attemptRobloxAutoAccept: Optional[Callable[..., Awaitable[str]]] = None
_dmUserWithView: Optional[Callable[..., Awaitable[bool]]] = None
_robloxGroupUrlProvider: Optional[Callable[[], str]] = None

_inventoryPrivateDmSent: set[tuple[int, int]] = set()


def configure(
    *,
    serviceModule: Any,
    claimComponentInteraction: Callable[[int], bool],
    safeInteractionDefer: Callable[..., Awaitable[None]],
    sendEphemeralReply: Callable[..., Awaitable[None]],
    sendInvalidComponentReply: Callable[..., Awaitable[None]],
    safeInteractionReply: Callable[..., Awaitable[None]],
    requestBgQueueMessageUpdate: Callable[..., Awaitable[None]],
    loadFlagRules: Callable[[], Awaitable[tuple]],
    resolveRobloxIdentity: Callable[[dict], Awaitable[Any]],
    scanRobloxInventoryForAttendee: Callable[..., Awaitable[bool]],
    attemptRobloxAutoAccept: Callable[..., Awaitable[str]],
    dmUserWithView: Callable[..., Awaitable[bool]],
    robloxGroupUrlProvider: Callable[[], str],
) -> None:
    global _serviceModule
    global _claimComponentInteraction
    global _safeInteractionDefer
    global _sendEphemeralReply
    global _sendInvalidComponentReply
    global _safeInteractionReply
    global _requestBgQueueMessageUpdate
    global _loadFlagRules
    global _resolveRobloxIdentity
    global _scanRobloxInventoryForAttendee
    global _attemptRobloxAutoAccept
    global _dmUserWithView
    global _robloxGroupUrlProvider

    _serviceModule = serviceModule
    _claimComponentInteraction = claimComponentInteraction
    _safeInteractionDefer = safeInteractionDefer
    _sendEphemeralReply = sendEphemeralReply
    _sendInvalidComponentReply = sendInvalidComponentReply
    _safeInteractionReply = safeInteractionReply
    _requestBgQueueMessageUpdate = requestBgQueueMessageUpdate
    _loadFlagRules = loadFlagRules
    _resolveRobloxIdentity = resolveRobloxIdentity
    _scanRobloxInventoryForAttendee = scanRobloxInventoryForAttendee
    _attemptRobloxAutoAccept = attemptRobloxAutoAccept
    _dmUserWithView = dmUserWithView
    _robloxGroupUrlProvider = robloxGroupUrlProvider


class RobloxJoinRetryView(ui.View):
    def __init__(self, sessionId: int):
        super().__init__(timeout=None)
        self.sessionId = sessionId
        self.retryBtn.custom_id = f"roblox:retry:{sessionId}"

    @ui.button(label="Retry Join", style=discord.ButtonStyle.primary, emoji="\U0001F501")
    async def retryBtn(self, interaction: discord.Interaction, button: ui.Button):
        await _handleRobloxRetry(interaction, self.sessionId)


class InventoryRetryView(ui.View):
    def __init__(self, sessionId: int, userId: int):
        super().__init__(timeout=None)
        self.sessionId = sessionId
        self.userId = userId
        self.retryBtn.custom_id = f"inventory:retry:{sessionId}:{userId}"

    @ui.button(label="Recheck Inventory", style=discord.ButtonStyle.primary)
    async def retryBtn(self, interaction: discord.Interaction, button: ui.Button):
        await _handleInventoryRetry(interaction, self.sessionId, self.userId)


def clearInventoryPrivateDmSent(sessionId: int, userId: int) -> None:
    _inventoryPrivateDmSent.discard((int(sessionId), int(userId)))


async def sendRobloxJoinRequestDm(bot: discord.Client, sessionId: int, userId: int) -> None:
    attendee = await _serviceModule.getAttendee(sessionId, userId)
    if not attendee:
        return
    if attendee["examGrade"] != "PASS" or attendee["bgStatus"] != "APPROVED":
        return
    if attendee.get("robloxJoinStatus") == "ACCEPTED":
        return

    groupUrl = _robloxGroupUrlProvider() if _robloxGroupUrlProvider else ""
    content = (
        "You passed orientation. Please request to join the Roblox group here:\n"
        f"{groupUrl}\n\n"
        "After you request, click **Retry Join** below so I can accept you automatically."
    )
    view = RobloxJoinRetryView(sessionId)
    await _dmUserWithView(bot, userId, content, view)


async def _disableRobloxRetryButton(interaction: discord.Interaction, sessionId: int) -> None:
    if not interaction.message:
        return
    view = RobloxJoinRetryView(sessionId)
    for child in view.children:
        child.disabled = True
    await interactionRuntime.safeMessageEdit(interaction.message, view=view)


async def _disableInventoryRetryButton(
    interaction: discord.Interaction,
    sessionId: int,
    userId: int,
) -> None:
    if not interaction.message:
        return
    view = InventoryRetryView(sessionId, userId)
    for child in view.children:
        child.disabled = True
    await interactionRuntime.safeMessageEdit(interaction.message, view=view)


async def sendInventoryPrivateDm(bot: discord.Client, sessionId: int, userId: int) -> None:
    dmKey = (sessionId, userId)
    if dmKey in _inventoryPrivateDmSent:
        return

    content = (
        "Your Roblox inventory appears to be private or hidden. "
        "Please set it to public, then click **Recheck Inventory** below."
    )
    view = InventoryRetryView(sessionId, userId)
    if await _dmUserWithView(bot, userId, content, view):
        _inventoryPrivateDmSent.add(dmKey)


async def _handleRobloxRetry(interaction: discord.Interaction, sessionId: int) -> None:
    if not _claimComponentInteraction or not _claimComponentInteraction(int(interaction.id)):
        return
    await _safeInteractionDefer(interaction, ephemeral=True)

    status = await _attemptRobloxAutoAccept(
        interaction.client,
        interaction.guild,
        sessionId,
        interaction.user.id,
    )

    if status == "ACCEPTED":
        await _disableRobloxRetryButton(interaction, sessionId)
        return await _sendEphemeralReply(
            interaction,
            "You're now accepted into the group. The retry button has been disabled.",
        )

    statusMessages = {
        "NO_REQUEST": "No pending join request was found. Please request to join the group and try again.",
        "NO_ROVER": "We couldn't find a RoVer link for your Roblox account. Please verify with RoVer and try again.",
        "MISSING_CONFIG": "The bot is missing Roblox configuration. Please contact staff.",
        "NOT_READY": "You're not fully approved yet. Please wait for staff review.",
        "NO_ATTENDEE": "We couldn't find your orientation record. Please contact staff.",
    }
    message = statusMessages.get(status) or "We couldn't accept the join request yet. Please try again later."
    return await _sendEphemeralReply(interaction, message)


async def handleRobloxRetryInteraction(interaction: discord.Interaction) -> bool:
    if interaction.response.is_done():
        return False
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data if isinstance(interaction.data, dict) else None
    if not data:
        return False
    customId = data.get("custom_id")
    if not isinstance(customId, str) or not customId.startswith("roblox:retry:"):
        return False
    parts = customId.split(":")
    if len(parts) < 3:
        await _sendInvalidComponentReply(
            interaction,
            "This retry button is invalid or expired.",
        )
        return True
    try:
        sessionId = int(parts[2])
    except ValueError:
        await _sendInvalidComponentReply(
            interaction,
            "This retry button is invalid or expired.",
        )
        return True
    await _handleRobloxRetry(interaction, sessionId)
    return True


async def _handleInventoryRetry(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
) -> None:
    if not _claimComponentInteraction or not _claimComponentInteraction(int(interaction.id)):
        return
    if interaction.user.id != targetUserId:
        return await _sendInvalidComponentReply(
            interaction,
            "This inventory retry button is not for your account.",
        )

    await _safeInteractionDefer(interaction, ephemeral=True)
    attendee = await _serviceModule.getAttendee(sessionId, targetUserId)
    if attendee is None:
        return await _safeInteractionReply(
            interaction,
            "We could not find your orientation record for this retry request.",
            ephemeral=True,
        )

    (
        _flagIds,
        _flagUsernames,
        _groupKeywords,
        itemKeywords,
        flagItemIds,
        flagCreatorIds,
        _flagBadgeIds,
        _badgeNotes,
        _accountAgeDays,
    ) = await _loadFlagRules()

    identity = await _resolveRobloxIdentity(attendee)
    await _scanRobloxInventoryForAttendee(
        sessionId,
        attendee,
        identity,
        itemKeywords,
        flagItemIds,
        flagCreatorIds,
        forceRescan=True,
    )
    attendee = await _serviceModule.getAttendee(sessionId, targetUserId) or attendee
    await _requestBgQueueMessageUpdate(interaction.client, sessionId)

    status = attendee.get("robloxInventoryScanStatus")
    if status == "OK":
        _inventoryPrivateDmSent.discard((sessionId, targetUserId))
        await _disableInventoryRetryButton(interaction, sessionId, targetUserId)
        return await _sendEphemeralReply(
            interaction,
            "Inventory is now public. The background-check queue has been updated.",
        )
    statusMessages = {
        "PRIVATE": "Inventory is still private or hidden. Please update the privacy setting and try again.",
        "NO_ROVER": "We could not find your Roblox account via RoVer. Please verify and try again.",
    }
    if status in statusMessages:
        return await _sendEphemeralReply(interaction, statusMessages[status])
    error = attendee.get("robloxInventoryScanError") or "unknown"
    return await _sendEphemeralReply(
        interaction,
        f"Inventory recheck did not complete successfully ({status or 'ERROR'}). Error: {error}",
    )


async def handleInventoryRetryInteraction(interaction: discord.Interaction) -> bool:
    if interaction.response.is_done():
        return False
    if interaction.type != discord.InteractionType.component:
        return False
    data = interaction.data if isinstance(interaction.data, dict) else None
    if not data:
        return False
    customId = data.get("custom_id")
    if not isinstance(customId, str) or not customId.startswith("inventory:retry:"):
        return False
    parts = customId.split(":")
    if len(parts) < 4:
        await _sendInvalidComponentReply(
            interaction,
            "This inventory retry button is invalid or expired.",
        )
        return True
    try:
        sessionId = int(parts[2])
        targetUserId = int(parts[3])
    except ValueError:
        await _sendInvalidComponentReply(
            interaction,
            "This inventory retry button is invalid or expired.",
        )
        return True
    await _handleInventoryRetry(interaction, sessionId, targetUserId)
    return True
