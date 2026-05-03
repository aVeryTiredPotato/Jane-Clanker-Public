from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

import discord
from discord import ui

from features.staff.sessions import bgBuckets
from features.staff.sessions.favoriteGamesView import FavoriteGamesPageView
from runtime import taskBudgeter


class BgInfoActionsView(ui.View):
    def __init__(
        self,
        sessionId: int,
        targetUserId: int,
        viewerId: int,
        robloxUserId: Optional[int],
        robloxUsername: Optional[str],
        reviewBucket: str,
        *,
        configModule: Any,
        serviceModule: Any,
        robloxModule: Any,
        safeInteractionReply: Callable[..., Awaitable[bool]],
        safeInteractionDefer: Callable[..., Awaitable[bool]],
        requireModPermission: Callable[[discord.Interaction], Awaitable[bool]],
        robloxGamesModule: Any = None,
    ):
        super().__init__(timeout=300)
        self.sessionId = int(sessionId)
        self.targetUserId = int(targetUserId)
        self.viewerId = int(viewerId)
        self.robloxUserId = robloxUserId
        self.robloxUsername = robloxUsername
        self.reviewBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)
        self.config = configModule
        self.service = serviceModule
        self.robloxUsers = robloxModule
        self.robloxGames = robloxGamesModule or robloxModule
        self.safeInteractionReply = safeInteractionReply
        self.safeInteractionDefer = safeInteractionDefer
        self.requireModPermission = requireModPermission
        if self.reviewBucket == bgBuckets.minorBgReviewBucket:
            self.remove_item(self.favoritedGamesBtn)

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.viewerId:
            await self.safeInteractionReply(
                interaction,
                content="This panel is only for the moderator who opened it.",
                ephemeral=True,
            )
            return False
        if not await self.requireModPermission(interaction):
            return False
        return True

    @ui.button(label="Favorited Games", style=discord.ButtonStyle.secondary)
    async def favoritedGamesBtn(
        self,
        interaction: discord.Interaction,
        _: ui.Button,
    ) -> None:
        if not await self._guard(interaction):
            return

        await self.safeInteractionDefer(interaction, ephemeral=True)
        robloxUserId = self.robloxUserId
        robloxUsername = self.robloxUsername
        session = await self.service.getSession(self.sessionId)
        lookupGuildId = 0
        try:
            lookupGuildId = int((session or {}).get("guildId") or 0)
        except (TypeError, ValueError):
            lookupGuildId = 0
        if lookupGuildId <= 0 and interaction.guild is not None:
            try:
                lookupGuildId = int(interaction.guild.id)
            except (TypeError, ValueError):
                lookupGuildId = 0

        attendee = await self.service.getAttendee(self.sessionId, self.targetUserId)
        if attendee:
            if not robloxUserId:
                attendeeRobloxUserId = attendee.get("robloxUserId")
                try:
                    robloxUserId = int(attendeeRobloxUserId) if attendeeRobloxUserId else None
                except (TypeError, ValueError):
                    robloxUserId = None
            if not robloxUsername:
                attendeeRobloxUsername = attendee.get("robloxUsername")
                if isinstance(attendeeRobloxUsername, str) and attendeeRobloxUsername:
                    robloxUsername = attendeeRobloxUsername

        if not robloxUserId:
            lookup = await self.robloxUsers.fetchRobloxUser(
                self.targetUserId,
                guildId=lookupGuildId or None,
            )
            if lookup.robloxId:
                robloxUserId = lookup.robloxId
            if lookup.robloxUsername and not robloxUsername:
                robloxUsername = lookup.robloxUsername

        if not robloxUserId:
            await self.safeInteractionReply(
                interaction,
                content="No Roblox account linked via RoVer for this attendee.",
                ephemeral=True,
            )
            return

        pageSize = max(1, min(20, int(getattr(self.config, "robloxFavoriteGamesMax", 12) or 12)))
        fetchLimit = max(pageSize, min(100, int(getattr(self.config, "robloxFavoriteGamesFetchMax", 100) or 100)))
        result = await self.robloxGames.fetchRobloxFavoriteGames(robloxUserId, maxGames=fetchLimit)
        if result.error and result.status == 400:
            # Retry once with a fresh RoVer lookup in case the stored Roblox ID is stale.
            lookup = await self.robloxUsers.fetchRobloxUser(
                self.targetUserId,
                guildId=lookupGuildId or None,
            )
            if lookup.robloxId and int(lookup.robloxId) != int(robloxUserId):
                robloxUserId = int(lookup.robloxId)
                if lookup.robloxUsername:
                    robloxUsername = lookup.robloxUsername
                result = await self.robloxGames.fetchRobloxFavoriteGames(robloxUserId, maxGames=fetchLimit)

        if result.error:
            await self.safeInteractionReply(
                interaction,
                content=f"Could not fetch favorited games: {result.error}",
                ephemeral=True,
            )
            return

        view = FavoriteGamesPageView(
            self.targetUserId,
            interaction.user.id,
            result.games[:fetchLimit],
            safeReply=self.safeInteractionReply,
            robloxUsername=robloxUsername,
            pageSize=pageSize,
        )
        await self.safeInteractionReply(
            interaction,
            embed=view.buildEmbed(),
            view=view,
            ephemeral=True,
        )


def _formatGroup(group: dict[str, Any]) -> str:
    name = group.get("name") or "(unknown)"
    gid = group.get("id") or "?"
    role = group.get("role")
    rank = group.get("rank")
    roleText = f" - {role} ({rank})" if role or rank else ""
    return f"{name} [{gid}]{roleText}"


async def sendBgInfoForTarget(
    interaction: discord.Interaction,
    sessionId: int,
    targetUserId: int,
    *,
    reviewBucket: str = bgBuckets.adultBgReviewBucket,
    configModule: Any,
    serviceModule: Any,
    safeInteractionDefer: Callable[..., Awaitable[bool]],
    safeInteractionReply: Callable[..., Awaitable[bool]],
    bgCandidates: Callable[[list[dict[str, Any]]], list[dict[str, Any]]],
    loadFlagRules: Callable[[], Awaitable[tuple[set[int], list[str], list[str], list[str], set[int], set[int], set[int], dict[int, str], int]]],
    resolveRobloxIdentity: Callable[[dict[str, Any]], Awaitable[Any]],
    scanRobloxGroupsForAttendee: Callable[..., Awaitable[bool]],
    scanRobloxInventoryForAttendee: Callable[..., Awaitable[bool]],
    scanRobloxBadgesForAttendee: Callable[..., Awaitable[bool]],
    sendInventoryPrivateDm: Callable[..., Awaitable[None]],
    loadJsonList: Callable[[Any], list[dict[str, Any]]],
    buildActionsView: Callable[[int, int, int, Optional[int], Optional[str], str], ui.View],
) -> None:
    # oh my fucking god
    await safeInteractionDefer(interaction, ephemeral=True)
    normalizedBucket = bgBuckets.normalizeBgReviewBucket(reviewBucket)
    attendees = bgCandidates(await serviceModule.getAttendees(sessionId))
    attendee = next((a for a in attendees if a["userId"] == targetUserId), None)
    if attendee is None:
        await safeInteractionReply(
            interaction,
            content="This attendee is no longer present in the background-check queue.",
            ephemeral=True,
        )
        return

    session = await serviceModule.getSession(sessionId)
    guild = None
    if isinstance(session, dict):
        try:
            sessionGuildId = int(session.get("guildId") or 0)
        except (TypeError, ValueError):
            sessionGuildId = 0
        if sessionGuildId > 0:
            guild = interaction.client.get_guild(sessionGuildId)
    if guild is None:
        guild = interaction.guild
    member = guild.get_member(targetUserId) if guild else None
    if member is None and guild is not None:
        try:
            member = await taskBudgeter.runDiscord(lambda: guild.fetch_member(targetUserId))
        except discord.NotFound:
            member = None

    nickname = member.nick if member and member.nick else "(none)"
    username = member.name if member else "(unknown)"
    mention = f"<@{targetUserId}>"
    joinedAt = getattr(member, "joined_at", None)
    createdAt = getattr(member, "created_at", None)

    (
        flagIds,
        flagUsernames,
        groupKeywords,
        itemKeywords,
        flagItemIds,
        flagCreatorIds,
        flagBadgeIds,
        badgeNotes,
        accountAgeDays,
    ) = await loadFlagRules()
    scanGroups = normalizedBucket == bgBuckets.adultBgReviewBucket and bool(flagIds or flagUsernames or groupKeywords or accountAgeDays > 0)
    # Always run inventory scan when enabled so we can determine public vs private,
    # even if there are no inventory-specific flag rules configured.
    scanInventory = normalizedBucket == bgBuckets.adultBgReviewBucket and bool(getattr(configModule, "robloxInventoryScanEnabled", False))
    scanBadges = bool(flagBadgeIds)
    identity: Optional[Any] = None

    async def getIdentity() -> Any:
        nonlocal identity
        if identity is None:
            identity = await resolveRobloxIdentity(attendee)
        return identity

    if scanGroups and not attendee.get("robloxGroupsJson"):
        await scanRobloxGroupsForAttendee(
            sessionId,
            attendee,
            await getIdentity(),
            flagIds,
            flagUsernames,
            groupKeywords,
            accountAgeDays,
        )
        attendee = await serviceModule.getAttendee(sessionId, targetUserId) or attendee
    if scanInventory and not attendee.get("robloxInventoryScanAt"):
        previousInventoryStatus = attendee.get("robloxInventoryScanStatus")
        await scanRobloxInventoryForAttendee(
            sessionId,
            attendee,
            await getIdentity(),
            itemKeywords,
            flagItemIds,
            flagCreatorIds,
        )
        attendee = await serviceModule.getAttendee(sessionId, targetUserId) or attendee
        if (
            attendee.get("robloxInventoryScanStatus") == "PRIVATE"
            and previousInventoryStatus != "PRIVATE"
        ):
            await sendInventoryPrivateDm(interaction.client, sessionId, targetUserId)
    if scanBadges and not attendee.get("robloxBadgeScanAt"):
        await scanRobloxBadgesForAttendee(
            sessionId,
            attendee,
            await getIdentity(),
            flagBadgeIds,
            badgeNotes,
        )
        attendee = await serviceModule.getAttendee(sessionId, targetUserId) or attendee

    robloxUserId = attendee.get("robloxUserId")
    robloxUsername = attendee.get("robloxUsername")
    if not robloxUserId or not robloxUsername:
        resolved = await getIdentity()
        robloxUserId = robloxUserId or resolved.robloxUserId
        robloxUsername = robloxUsername or resolved.robloxUsername

    flaggedGroups = loadJsonList(attendee.get("robloxFlaggedGroupsJson"))
    matches = loadJsonList(attendee.get("robloxFlagMatchesJson"))
    flaggedItems = loadJsonList(attendee.get("robloxFlaggedItemsJson"))
    flaggedBadges = loadJsonList(attendee.get("robloxFlaggedBadgesJson"))
    groupStatus = attendee.get("robloxGroupScanStatus")
    groupError = attendee.get("robloxGroupScanError")
    inventoryStatus = attendee.get("robloxInventoryScanStatus")
    inventoryError = attendee.get("robloxInventoryScanError")
    badgeStatus = attendee.get("robloxBadgeScanStatus")
    badgeError = attendee.get("robloxBadgeScanError")

    flaggedLines = [_formatGroup(group) for group in flaggedGroups[:10]]
    if len(flaggedGroups) > 10:
        flaggedLines.append(f"... and {len(flaggedGroups) - 10} more")

    matchLines: list[str] = []
    for match in matches[:15]:
        if not isinstance(match, dict):
            continue
        matchType = match.get("type", "match")
        value = match.get("value", "")
        context = match.get("context")
        if matchType == "accountAge":
            created = match.get("created")
            threshold = match.get("thresholdDays")
            suffix = f" (created {created})" if created else ""
            if threshold:
                matchLines.append(f"account age {value} < {threshold} days{suffix}")
            else:
                matchLines.append(f"account age {value}{suffix}")
            continue
        if matchType == "keyword" and context == "group":
            groupName = match.get("groupName") or "group"
            matchLines.append(f"keyword '{value}' in {groupName}")
        else:
            matchLines.append(f"{matchType}: {value}")
    if len(matches) > 15:
        matchLines.append(f"... and {len(matches) - 15} more")

    embed = discord.Embed(title="Attendee Info")
    embed.add_field(name="Nickname", value=nickname, inline=False)
    embed.add_field(name="Mention", value=mention, inline=False)
    embed.add_field(name="User ID", value=str(targetUserId), inline=False)
    embed.add_field(name="Username", value=username, inline=False)
    if createdAt is not None:
        embed.add_field(name="Discord Created", value=discord.utils.format_dt(createdAt, "f"), inline=False)
    if joinedAt is not None:
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(joinedAt, "f"), inline=False)
    if robloxUserId:
        robloxLabel = robloxUsername or str(robloxUserId)
        if normalizedBucket == bgBuckets.minorBgReviewBucket:
            embed.add_field(name="Roblox Account", value=robloxLabel, inline=False)
        else:
            robloxProfileUrl = f"https://www.roblox.com/users/{robloxUserId}/profile"
            embed.add_field(
                name="Roblox Account",
                value=f"[{robloxLabel}]({robloxProfileUrl})",
                inline=False,
            )
    elif robloxUsername:
        embed.add_field(name="Roblox Account", value=robloxUsername, inline=False)
    else:
        embed.add_field(name="Roblox Account", value="Not linked via RoVer.", inline=False)
    if normalizedBucket == bgBuckets.adultBgReviewBucket and flaggedGroups:
        embed.add_field(
            name="Flagged Groups",
            value="\n".join(flaggedLines) if flaggedLines else "FLAGGED",
            inline=False,
        )
    elif normalizedBucket == bgBuckets.adultBgReviewBucket and scanGroups:
        embed.add_field(
            name="Flagged Groups",
            value="No flagged groups detected.",
            inline=False,
        )
    if normalizedBucket == bgBuckets.adultBgReviewBucket and matchLines:
        embed.add_field(
            name="Flag Matches",
            value="\n".join(matchLines),
            inline=False,
        )
    if normalizedBucket == bgBuckets.adultBgReviewBucket and flaggedItems:
        itemLines: list[str] = []
        for item in flaggedItems[:15]:
            if not isinstance(item, dict):
                continue
            itemId = item.get("id")
            itemName = item.get("name")
            itemType = str(item.get("itemType") or "").strip()
            creatorId = item.get("creatorId")
            creatorName = item.get("creatorName")
            matchType = str(item.get("matchType") or "").strip().lower()
            matchMode = str(item.get("matchMode") or "").strip().lower()
            creatorText = ""
            if creatorName or creatorId:
                creatorLabel = creatorName or "creator"
                creatorText = f" by {creatorLabel} [{creatorId}]" if creatorId else f" by {creatorLabel}"
            detailParts: list[str] = []
            if itemType:
                detailParts.append(itemType)
            if matchType == "creator":
                detailParts.append("flagged creator")
            elif matchType == "item":
                detailParts.append("exact item")
            elif matchType == "visual":
                referenceItemId = item.get("referenceItemId")
                visualDistance = item.get("visualDistance")
                if referenceItemId:
                    detailParts.append(f"visual match to {referenceItemId} (d={visualDistance if visualDistance is not None else '?'})")
                else:
                    detailParts.append("visual match")
            elif matchType == "keyword" and item.get("keyword"):
                keyword = str(item.get("keyword") or "").strip()
                if matchMode == "fuzzy":
                    try:
                        fuzzyText = f"{float(item.get('fuzzyScore')):.0f}"
                    except (TypeError, ValueError):
                        fuzzyText = "?"
                    detailParts.append(f"fuzzy keyword {fuzzyText}: {keyword}")
                elif matchMode == "normalized":
                    detailParts.append(f"normalized keyword: {keyword}")
                else:
                    detailParts.append(f"keyword: {keyword}")
            extraSignals = max(0, int(item.get("matchCount") or 0) - 1)
            if extraSignals > 0:
                detailParts.append(f"+{extraSignals} more signal(s)")
            suffix = f" | {', '.join(detailParts)}" if detailParts else ""
            if itemName:
                itemLines.append(f"{itemName} [{itemId}]{creatorText}{suffix}")
            else:
                itemLines.append(f"{itemId}{creatorText}{suffix}")
        if len(flaggedItems) > 15:
            itemLines.append(f"... and {len(flaggedItems) - 15} more")
        embed.add_field(
            name="Flagged Items",
            value="\n".join(itemLines) if itemLines else "FLAGGED",
            inline=False,
        )
    if flaggedBadges:
        badgeLines: list[str] = []
        for badge in flaggedBadges[:15]:
            if not isinstance(badge, dict):
                continue
            badgeId = badge.get("badgeId")
            awarded = badge.get("awardedDate")
            note = badge.get("note")
            line = f"Badge {badgeId}"
            if awarded:
                line += f" (awarded {awarded})"
            if note:
                line += f" - {note}"
            badgeLines.append(line)
        if len(flaggedBadges) > 15:
            badgeLines.append(f"... and {len(flaggedBadges) - 15} more")
        embed.add_field(
            name="Flagged Badges",
            value="\n".join(badgeLines) if badgeLines else "FLAGGED",
            inline=False,
        )
    if normalizedBucket == bgBuckets.adultBgReviewBucket and groupStatus and groupStatus != "OK":
        note = groupStatus
        if groupError:
            note = f"{groupStatus}: {groupError}"
        embed.add_field(name="Group Scan", value=note, inline=False)
    if normalizedBucket == bgBuckets.adultBgReviewBucket and inventoryStatus and inventoryStatus != "OK":
        note = inventoryStatus
        if inventoryError:
            note = f"{inventoryStatus}: {inventoryError}"
        embed.add_field(name="Inventory Scan", value=note, inline=False)
    if badgeStatus and badgeStatus != "OK":
        note = badgeStatus
        if badgeError:
            note = f"{badgeStatus}: {badgeError}"
        embed.add_field(name="Badge Scan", value=note, inline=False)

    robloxUserIdInt: Optional[int] = None
    try:
        if robloxUserId is not None:
            robloxUserIdInt = int(robloxUserId)
    except (TypeError, ValueError):
        robloxUserIdInt = None

    await safeInteractionReply(
        interaction,
        embed=embed,
        view=buildActionsView(
            sessionId,
            targetUserId,
            interaction.user.id,
            robloxUserIdInt,
            robloxUsername,
            normalizedBucket,
        ),
        ephemeral=True,
    )

