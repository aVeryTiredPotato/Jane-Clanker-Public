from __future__ import annotations

import logging
from typing import Any, Optional

import discord

import config
from features.staff.bgItemReview import service
from features.staff.bgflags import service as flagService
from features.staff.sessions.Roblox import robloxInventory, robloxUsers
from runtime import interaction as interactionRuntime
from runtime import orgProfiles
from runtime import permissions as runtimePermissions
from runtime import webhooks as runtimeWebhooks

log = logging.getLogger(__name__)


def _positiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _orgConfigValue(name: str, *, guildId: int = 0, default: object = None) -> object:
    return orgProfiles.getOrganizationValue(
        config,
        name,
        guildId=int(guildId or 0),
        default=default,
    )


def _queueEnabled(guildId: int = 0) -> bool:
    return bool(
        _orgConfigValue(
            "bgItemReviewQueueEnabled",
            guildId=guildId,
            default=getattr(config, "bgItemReviewQueueEnabled", True),
        )
    )


def _queueChannelId(guildId: int = 0) -> int:
    return _positiveInt(
        _orgConfigValue(
            "bgItemReviewQueueChannelId",
            guildId=guildId,
            default=getattr(config, "bgItemReviewQueueChannelId", 0),
        )
    )


def _reviewerRoleId(guildId: int = 0) -> int:
    return _positiveInt(
        _orgConfigValue(
            "bgItemReviewReviewerRoleId",
            guildId=guildId,
            default=getattr(config, "bgItemReviewReviewerRoleId", getattr(config, "bgReviewModeratorRoleId", 0)),
        )
    )


def _webhookName(guildId: int = 0) -> str:
    return str(
        _orgConfigValue(
            "bgItemReviewWebhookName",
            guildId=guildId,
            default=getattr(config, "bgItemReviewWebhookName", "Jane BG Item Review"),
        )
        or "Jane BG Item Review"
    ).strip() or "Jane BG Item Review"


def _maxPagesPerType(guildId: int = 0) -> int:
    return max(
        1,
        _positiveInt(
            _orgConfigValue(
                "bgItemReviewMaxPagesPerType",
                guildId=guildId,
                default=getattr(config, "bgItemReviewMaxPagesPerType", 4),
            )
        )
        or 4,
    )


def _candidateLimit(guildId: int = 0) -> int:
    return max(
        1,
        _positiveInt(
            _orgConfigValue(
                "bgItemReviewCandidateLimit",
                guildId=guildId,
                default=getattr(config, "bgItemReviewCandidateLimit", 60),
            )
        )
        or 60,
    )


def _canReview(member: discord.Member) -> bool:
    guildId = _positiveInt(getattr(getattr(member, "guild", None), "id", 0))
    roleId = _reviewerRoleId(guildId)
    if roleId > 0 and runtimePermissions.hasAnyRole(member, [roleId]):
        return True
    if runtimePermissions.hasBgCheckCertifiedRole(member):
        return True
    return runtimePermissions.hasAdminOrManageGuild(member)


def canReviewMember(member: discord.Member) -> bool:
    return _canReview(member)


async def _resolveChannel(
    botClient: discord.Client,
    channelId: int,
) -> Optional[discord.TextChannel | discord.Thread]:
    if int(channelId or 0) <= 0:
        return None
    channel = botClient.get_channel(int(channelId))
    if channel is None:
        channel = await interactionRuntime.safeFetchChannel(botClient, int(channelId))
    if isinstance(channel, (discord.TextChannel, discord.Thread)):
        return channel
    return None


async def _fetchMessage(
    botClient: discord.Client,
    *,
    channelId: int,
    messageId: int,
) -> Optional[discord.Message]:
    channel = await _resolveChannel(botClient, int(channelId))
    if channel is None or int(messageId or 0) <= 0:
        return None
    return await interactionRuntime.safeFetchMessage(channel, int(messageId))


def _statusLabel(status: str) -> str:
    mapping = {
        service.STATUS_PENDING: "Pending Review",
        service.STATUS_FLAGGED: "Flagged",
        service.STATUS_SAFE: "Marked Safe",
        service.STATUS_IGNORED: "Ignored",
    }
    return mapping.get(service.normalizeStatus(status), service.normalizeStatus(status).replace("_", " ").title())


def _statusColor(status: str) -> discord.Color:
    normalized = service.normalizeStatus(status)
    if normalized == service.STATUS_FLAGGED:
        return discord.Color.red()
    if normalized == service.STATUS_SAFE:
        return discord.Color.green()
    if normalized == service.STATUS_IGNORED:
        return discord.Color.dark_grey()
    return discord.Color.blurple()


def _truncate(value: object, *, limit: int = 60) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, int(limit) - 3)].rstrip() + "..."


def _queueRowSummaryLine(queueRow: dict[str, Any]) -> str:
    queueId = _positiveInt(queueRow.get("queueId"))
    assetId = _positiveInt(queueRow.get("assetId"))
    assetName = _truncate(queueRow.get("assetName") or f"Asset {assetId}", limit=44) or f"Asset {assetId}"
    creatorName = _truncate(queueRow.get("creatorName") or "unknown", limit=24) or "unknown"
    return f"`#{queueId}` `{assetId}` {assetName} | {creatorName}"


async def buildQueueSummaryEmbed(*, guildId: int = 0) -> discord.Embed:
    normalizedGuildId = int(guildId or 0) or None
    counts = await service.listQueueCounts(guildId=normalizedGuildId)
    pendingRows = await service.listQueueEntriesByStatus(
        [service.STATUS_PENDING],
        guildId=normalizedGuildId,
        limit=5,
    )
    flaggedRows = await service.listQueueEntriesByStatus(
        [service.STATUS_FLAGGED],
        guildId=normalizedGuildId,
        limit=5,
    )

    queueChannelId = _queueChannelId(int(guildId or 0))
    if queueChannelId > 0:
        description = f"Queue channel: <#{queueChannelId}>"
    else:
        description = "Queue channel not configured."

    embed = discord.Embed(
        title="BG Item Review Queue",
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Pending", value=f"`{int(counts.get(service.STATUS_PENDING, 0))}`", inline=True)
    embed.add_field(name="Flagged", value=f"`{int(counts.get(service.STATUS_FLAGGED, 0))}`", inline=True)
    embed.add_field(name="Safe", value=f"`{int(counts.get(service.STATUS_SAFE, 0))}`", inline=True)
    otherCount = int(counts.get(service.STATUS_IGNORED, 0))
    if otherCount > 0:
        embed.add_field(name="Other", value=f"`{otherCount}`", inline=True)
    embed.add_field(name="Total", value=f"`{int(counts.get('total', 0))}`", inline=True)

    pendingText = "\n".join(_queueRowSummaryLine(row) for row in pendingRows[:5]) or "No pending items."
    flaggedText = "\n".join(_queueRowSummaryLine(row) for row in flaggedRows[:5]) or "No flagged items."
    embed.add_field(name="Recent Pending", value=pendingText[:1024], inline=False)
    embed.add_field(name="Recent Flagged", value=flaggedText[:1024], inline=False)
    embed.set_footer(text="Use /bg-item-review to post a fresh queue summary.")
    return embed


async def postQueueSummaryMessage(
    botClient: discord.Client,
    *,
    guildId: int = 0,
) -> Optional[discord.Message]:
    channelId = _queueChannelId(guildId)
    channel = await _resolveChannel(botClient, channelId)
    if channel is None:
        return None

    webhookName = _webhookName(guildId)
    embed = await buildQueueSummaryEmbed(guildId=guildId)
    sentMessage = await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
        botClient=botClient,
        channel=channel,
        webhookName=webhookName,
        embed=embed,
        username="Jane Item Review",
        reason="Jane BG item review summary",
    )
    if sentMessage is not None:
        return sentMessage
    return await interactionRuntime.safeChannelSend(channel, embed=embed)


async def _buildQueueEmbed(queueRow: dict[str, Any]) -> discord.Embed:
    queueId = int(queueRow.get("queueId") or 0)
    assetId = int(queueRow.get("assetId") or 0)
    status = service.normalizeStatus(queueRow.get("status"))
    sources = await service.listSourcesForQueue(queueId, limit=3)

    embed = discord.Embed(
        title=str(queueRow.get("assetName") or f"Asset {assetId}").strip() or f"Asset {assetId}",
        description=f"Status: **{_statusLabel(status)}**",
        color=_statusColor(status),
    )
    creatorText = str(queueRow.get("creatorName") or "").strip()
    creatorId = _positiveInt(queueRow.get("creatorId"))
    if creatorId > 0:
        creatorText = f"{creatorText} (`{creatorId}`)".strip()
    if not creatorText:
        creatorText = "`unknown`"
    priceValue = queueRow.get("priceRobux")
    if priceValue is None:
        priceText = "`unknown`"
    else:
        priceText = f"`{int(priceValue):,} R$`"
    embed.add_field(name="Creator", value=creatorText, inline=True)
    embed.add_field(name="Price", value=priceText, inline=True)
    embed.add_field(
        name="Seen",
        value=f"`{int(queueRow.get('seenCount') or 0):,}` time(s)",
        inline=True,
    )
    itemType = str(queueRow.get("itemType") or "").strip()
    if itemType:
        embed.add_field(name="Type", value=f"`{itemType}`", inline=True)
    embed.add_field(name="Asset ID", value=f"`{assetId}`", inline=True)
    hashValue = str(queueRow.get("thumbnailHash") or "").strip()
    if hashValue:
        embed.add_field(name="Hash", value=f"`{hashValue}`", inline=True)

    lastSourceUserId = _positiveInt(queueRow.get("sourceUserId"))
    lastSourceRoblox = str(queueRow.get("sourceRobloxUsername") or "").strip()
    if lastSourceUserId > 0 or lastSourceRoblox:
        sourceBits: list[str] = []
        if lastSourceUserId > 0:
            sourceBits.append(f"<@{lastSourceUserId}>")
        if lastSourceRoblox:
            sourceBits.append(f"`{lastSourceRoblox}`")
        embed.add_field(name="Last Source", value=" / ".join(sourceBits), inline=False)

    if sources:
        recentLines: list[str] = []
        for row in sources:
            lineBits: list[str] = []
            sourceUserId = _positiveInt(row.get("sourceUserId"))
            sourceRobloxUsername = str(row.get("sourceRobloxUsername") or "").strip()
            if sourceUserId > 0:
                lineBits.append(f"<@{sourceUserId}>")
            if sourceRobloxUsername:
                lineBits.append(f"`{sourceRobloxUsername}`")
            if not lineBits:
                continue
            recentLines.append(" / ".join(lineBits))
        if recentLines:
            embed.add_field(name="Recent Sources", value="\n".join(recentLines[:3]), inline=False)

    reviewNote = str(queueRow.get("reviewNote") or "").strip()
    if reviewNote:
        embed.add_field(name="Reviewer Note", value=reviewNote[:1024], inline=False)

    reviewedBy = _positiveInt(queueRow.get("reviewedBy"))
    if reviewedBy > 0:
        embed.set_footer(text=f"Queue #{queueId} | Reviewed by {reviewedBy}")
    else:
        embed.set_footer(text=f"Queue #{queueId}")

    thumbnailUrl = str(queueRow.get("thumbnailUrl") or "").strip()
    if thumbnailUrl:
        embed.set_image(url=thumbnailUrl)
    return embed


class BgItemReviewView(discord.ui.View):
    def __init__(self, queueId: int) -> None:
        super().__init__(timeout=None)
        self.queueId = int(queueId)
        self.flagBtn.custom_id = f"bgitemreview:flag:{self.queueId}"
        self.safeBtn.custom_id = f"bgitemreview:safe:{self.queueId}"

    async def _guard(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        if not isinstance(member, discord.Member) or not _canReview(member):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Only BG reviewers can use this queue.",
                ephemeral=True,
            )
            return False
        return True

    async def _applyDecision(self, interaction: discord.Interaction, newStatus: str) -> None:
        if not await self._guard(interaction):
            return
        queueRow = await service.getQueueEntry(self.queueId)
        if not queueRow:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="This item review entry no longer exists.",
                ephemeral=True,
            )
            return

        currentStatus = service.normalizeStatus(queueRow.get("status"))
        if currentStatus in service.FINAL_STATUSES:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"This item has already been marked as {_statusLabel(currentStatus).lower()}.",
                ephemeral=True,
            )
            return

        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
        await service.updateQueueStatus(
            self.queueId,
            status=newStatus,
            reviewerId=int(interaction.user.id),
        )
        await service.addAction(
            self.queueId,
            actorId=int(interaction.user.id),
            action=newStatus,
        )
        await refreshQueueMessage(interaction.client, self.queueId, message=interaction.message)
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=f"Queue item marked as {_statusLabel(newStatus).lower()}.",
            ephemeral=True,
        )

    @discord.ui.button(label="Flag", style=discord.ButtonStyle.danger, row=0)
    async def flagBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._guard(interaction):
            return
        queueRow = await service.getQueueEntry(self.queueId)
        if not queueRow:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="This item review entry no longer exists.",
                ephemeral=True,
            )
            return

        currentStatus = service.normalizeStatus(queueRow.get("status"))
        if currentStatus in service.FINAL_STATUSES:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"This item has already been marked as {_statusLabel(currentStatus).lower()}.",
                ephemeral=True,
            )
            return

        await interactionRuntime.safeInteractionSendModal(
            interaction,
            BgItemReviewFlagModal(self.queueId),
        )

    @discord.ui.button(label="Safe", style=discord.ButtonStyle.success, row=0)
    async def safeBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._applyDecision(interaction, service.STATUS_SAFE)


class BgItemReviewFlagModal(discord.ui.Modal, title="Flag Item Review Entry"):
    note = discord.ui.TextInput(
        label="Flag Reason",
        style=discord.TextStyle.paragraph,
        placeholder="Why should this item be treated as flagged?",
        required=True,
        max_length=400,
    )

    def __init__(self, queueId: int) -> None:
        super().__init__()
        self.queueId = int(queueId)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        member = interaction.user
        if not isinstance(member, discord.Member) or not _canReview(member):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Only BG reviewers can use this queue.",
                ephemeral=True,
            )
            return

        queueRow = await service.getQueueEntry(self.queueId)
        if not queueRow:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="This item review entry no longer exists.",
                ephemeral=True,
            )
            return

        currentStatus = service.normalizeStatus(queueRow.get("status"))
        if currentStatus in service.FINAL_STATUSES:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content=f"This item has already been marked as {_statusLabel(currentStatus).lower()}.",
                ephemeral=True,
            )
            return

        noteText = str(self.note).strip()
        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)
        await service.updateQueueStatus(
            self.queueId,
            status=service.STATUS_FLAGGED,
            reviewerId=int(interaction.user.id),
            note=noteText,
        )
        await service.addAction(
            self.queueId,
            actorId=int(interaction.user.id),
            action=service.STATUS_FLAGGED,
            note=noteText,
        )
        await refreshQueueMessage(interaction.client, self.queueId)
        await interactionRuntime.safeInteractionReply(
            interaction,
            content="Queue item marked as flagged.",
            ephemeral=True,
        )


def _viewForRow(queueRow: dict[str, Any]) -> BgItemReviewView:
    view = BgItemReviewView(int(queueRow.get("queueId") or 0))
    if service.normalizeStatus(queueRow.get("status")) in service.FINAL_STATUSES:
        for child in view.children:
            child.disabled = True
    return view


async def refreshQueueMessage(
    botClient: discord.Client,
    queueId: int,
    *,
    message: Optional[discord.Message] = None,
) -> bool:
    queueRow = await service.getQueueEntry(int(queueId))
    if not queueRow:
        return False
    channelId = _positiveInt(queueRow.get("reviewChannelId"))
    messageId = _positiveInt(queueRow.get("reviewMessageId"))
    webhookName = _webhookName(_positiveInt(queueRow.get("guildId")))
    if message is None and channelId > 0 and messageId > 0:
        message = await _fetchMessage(
            botClient,
            channelId=channelId,
            messageId=messageId,
        )
    if message is None:
        return False
    embed = await _buildQueueEmbed(queueRow)
    view = _viewForRow(queueRow)
    edited = await runtimeWebhooks.editOwnedWebhookMessage(
        botClient=botClient,
        message=message,
        webhookName=webhookName,
        embed=embed,
        view=view,
        reason="Jane BG item review update",
    )
    if edited:
        return True
    return await interactionRuntime.safeMessageEdit(message, embed=embed, view=view)


async def _postQueueMessage(
    botClient: discord.Client,
    queueRow: dict[str, Any],
) -> bool:
    guildId = _positiveInt(queueRow.get("guildId"))
    channelId = _queueChannelId(guildId)
    channel = await _resolveChannel(botClient, channelId)
    if channel is None:
        return False

    webhookName = _webhookName(guildId)
    embed = await _buildQueueEmbed(queueRow)
    view = _viewForRow(queueRow)
    sentMessage = await runtimeWebhooks.sendOwnedWebhookMessageDetailed(
        botClient=botClient,
        channel=channel,
        webhookName=webhookName,
        embed=embed,
        view=view,
        username="Jane Item Review",
        reason="Jane BG item review queue",
    )
    if sentMessage is None:
        sentMessage = await interactionRuntime.safeChannelSend(channel, embed=embed, view=view)
        if sentMessage is None:
            return False
    await service.setReviewMessage(
        int(queueRow.get("queueId") or 0),
        int(channel.id),
        int(sentMessage.id),
    )
    return True


async def restorePersistentViews(botClient: discord.Client) -> int:
    rows = await service.listOpenQueueEntries()
    restored = 0
    for row in rows:
        messageId = _positiveInt(row.get("reviewMessageId"))
        if messageId <= 0:
            continue
        botClient.add_view(_viewForRow(row), message_id=messageId)
        try:
            await refreshQueueMessage(botClient, int(row.get("queueId") or 0))
        except Exception:
            log.exception("Failed to refresh BG item review queue message %s during restore.", messageId)
        restored += 1
    return restored


async def queueRejectedAttendeeInventory(
    botClient: discord.Client,
    *,
    session: dict[str, Any] | None,
    attendee: dict[str, Any] | None,
    reviewerId: int,
    guild: discord.Guild | None = None,
) -> dict[str, int | str]:
    if not isinstance(attendee, dict):
        return {"created": 0, "existing": 0, "known": 0, "errors": 1, "reason": "Missing attendee."}

    sessionRow = dict(session or {})
    guildId = _positiveInt(sessionRow.get("guildId") or getattr(guild, "id", 0))
    if not _queueEnabled(guildId):
        return {"created": 0, "existing": 0, "known": 0, "errors": 0, "reason": "Queue disabled."}
    if _queueChannelId(guildId) <= 0:
        return {"created": 0, "existing": 0, "known": 0, "errors": 1, "reason": "No queue channel configured."}

    sourceUserId = _positiveInt(attendee.get("userId"))
    sourceRobloxUserId = _positiveInt(attendee.get("robloxUserId"))
    sourceRobloxUsername = str(attendee.get("robloxUsername") or "").strip() or None
    if sourceRobloxUserId <= 0 and sourceUserId > 0:
        lookup = await robloxUsers.fetchRobloxUser(sourceUserId, guildId=guildId or None)
        sourceRobloxUserId = _positiveInt(lookup.robloxId)
        sourceRobloxUsername = str(lookup.robloxUsername or sourceRobloxUsername or "").strip() or None
    if sourceRobloxUserId <= 0 and sourceRobloxUsername:
        lookup = await robloxUsers.fetchRobloxUserByUsername(sourceRobloxUsername)
        sourceRobloxUserId = _positiveInt(lookup.robloxId)
        sourceRobloxUsername = str(lookup.robloxUsername or sourceRobloxUsername or "").strip() or None
    if sourceUserId > 0 and sourceRobloxUsername:
        await robloxUsers.rememberKnownRobloxIdentity(
            sourceUserId,
            sourceRobloxUsername,
            robloxId=sourceRobloxUserId if sourceRobloxUserId > 0 else None,
            source="bg-item-review",
            guildId=guildId or None,
            confidence=90 if sourceRobloxUserId > 0 else 70,
        )
    if sourceRobloxUserId <= 0:
        return {"created": 0, "existing": 0, "known": 0, "errors": 1, "reason": "No Roblox identity."}

    reviewItemsResult = await robloxInventory.fetchRobloxInventoryReviewItems(
        int(sourceRobloxUserId),
        maxPagesPerType=_maxPagesPerType(guildId),
        candidateLimit=_candidateLimit(guildId),
    )
    if reviewItemsResult.error:
        return {
            "created": 0,
            "existing": 0,
            "known": 0,
            "errors": 1,
            "reason": str(reviewItemsResult.error),
        }

    knownFlagHashes = await flagService.getValidatedItemVisualHashes(ensureSynced=True)
    knownAssetIds = {int(assetId) for assetId in knownFlagHashes.keys()}
    knownHashes = {str(hashValue).strip() for hashValue in knownFlagHashes.values() if str(hashValue).strip()}

    createdCount = 0
    existingCount = 0
    knownCount = 0
    errorCount = 0
    sessionId = _positiveInt(sessionRow.get("sessionId"))

    for item in list(reviewItemsResult.items or []):
        if not isinstance(item, dict):
            continue
        assetId = _positiveInt(item.get("id"))
        thumbnailHash = str(item.get("thumbnailHash") or "").strip()
        validationState = str(item.get("validationState") or "").strip().upper()
        if assetId <= 0 or not thumbnailHash or validationState != "VALID":
            continue
        if assetId in knownAssetIds or thumbnailHash in knownHashes:
            knownCount += 1
            continue

        existing = await service.findCandidateMatch(assetId, thumbnailHash)
        if existing is not None:
            await service.touchQueueEntry(
                int(existing.get("queueId") or 0),
                guildId=guildId,
                sessionId=sessionId,
                sourceUserId=sourceUserId,
                sourceRobloxUserId=sourceRobloxUserId,
                sourceRobloxUsername=sourceRobloxUsername,
                queuedByReviewerId=int(reviewerId or 0),
            )
            await service.addSourceRecord(
                queueId=int(existing.get("queueId") or 0),
                guildId=guildId,
                sessionId=sessionId,
                sourceUserId=sourceUserId,
                sourceRobloxUserId=sourceRobloxUserId,
                sourceRobloxUsername=sourceRobloxUsername,
                queuedByReviewerId=int(reviewerId or 0),
            )
            await service.addAction(
                int(existing.get("queueId") or 0),
                actorId=int(reviewerId or 0),
                action="SEEN_AGAIN",
            )
            if service.normalizeStatus(existing.get("status")) in service.OPEN_STATUSES:
                refreshed = await refreshQueueMessage(botClient, int(existing.get("queueId") or 0))
                if not refreshed and _positiveInt(existing.get("reviewMessageId")) <= 0:
                    refreshed = await _postQueueMessage(
                        botClient,
                        await service.getQueueEntry(int(existing.get("queueId") or 0)) or existing,
                    )
                if not refreshed and _positiveInt(existing.get("reviewMessageId")) <= 0:
                    errorCount += 1
            existingCount += 1
            continue

        try:
            queueId = await service.createQueueEntry(
                guildId=guildId,
                sessionId=sessionId,
                assetId=assetId,
                assetName=str(item.get("name") or "").strip() or None,
                itemType=str(item.get("itemType") or "").strip() or None,
                creatorId=_positiveInt(item.get("creatorId")) or None,
                creatorName=str(item.get("creatorName") or "").strip() or None,
                priceRobux=item.get("price"),
                thumbnailHash=thumbnailHash,
                thumbnailUrl=str(item.get("thumbnailUrl") or "").strip() or None,
                thumbnailState=str(item.get("thumbnailState") or "").strip() or None,
                sourceUserId=sourceUserId,
                sourceRobloxUserId=sourceRobloxUserId or None,
                sourceRobloxUsername=sourceRobloxUsername,
                queuedByReviewerId=int(reviewerId or 0),
            )
        except Exception:
            existing = await service.findCandidateMatch(assetId, thumbnailHash)
            if existing is None:
                log.exception("Failed to create BG item review queue row for asset %s.", assetId)
                errorCount += 1
                continue
            queueId = int(existing.get("queueId") or 0)

        await service.addSourceRecord(
            queueId=queueId,
            guildId=guildId,
            sessionId=sessionId,
            sourceUserId=sourceUserId,
            sourceRobloxUserId=sourceRobloxUserId or None,
            sourceRobloxUsername=sourceRobloxUsername,
            queuedByReviewerId=int(reviewerId or 0),
        )
        await service.addAction(
            queueId,
            actorId=int(reviewerId or 0),
            action="QUEUED",
        )
        queueRow = await service.getQueueEntry(queueId)
        if queueRow is None:
            errorCount += 1
            continue
        if not await _postQueueMessage(botClient, queueRow):
            errorCount += 1
            continue
        createdCount += 1

    return {
        "created": createdCount,
        "existing": existingCount,
        "known": knownCount,
        "errors": errorCount,
        "reason": "",
    }

