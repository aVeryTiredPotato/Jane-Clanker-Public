from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from features.community.bestOf import service as bestOfService
from features.staff.sessions.Roblox import robloxUsers
from runtime import interaction as interactionRuntime
from runtime import permissions as runtimePermissions
from runtime import viewBases as runtimeViewBases

# Behold the sacred fallback recipient.
# Keeper of last-resort Best Of scrolls.
# Patron saint of "this made me giggle, leave it in."
_bigFrenchManUserID = 743495349725036625
log = logging.getLogger(__name__)
_userIdRegex = re.compile(r"\d{15,22}")
_candidatePageSize = 25
_resultsSectionPageSize = 10
_leadingPrefixRegex = re.compile(r"^\[[^\]]+\]\s*")
_bestOfSectionDisplayOrder = [
    "ANROCOM",
    "HR",
    "MR",
    "Former ANROCOM",
    "Former HR",
    "Former MR",
]
_bestOfSectionDisplayOrderIndex = {
    label: index for index, label in enumerate(_bestOfSectionDisplayOrder)
}
_bestOfSectionColorHexByLabel = {
    "HR": "7c0cb4",
    "MR": "05c9ff",
    "Former HR": "f1b409",
    "Former MR": "dcbb82",
    "ANROCOM": "e74c3c",
    "Former ANROCOM": "e2da6b",
}

_simpleClosedEmbed = discord.Embed(
    color=discord.Color.red(),
    timestamp=datetime.now(timezone.utc),
    title="Best Awards - Closed",
    description="The vote has been closed. Results have been sent by DM."
)

def _toPositiveInt(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def _parseUserIdList(rawText: str) -> list[int]:
    out: list[int] = []
    for match in _userIdRegex.findall(str(rawText or "")):
        parsed = _toPositiveInt(match)
        if parsed <= 0 or parsed in out:
            continue
        out.append(parsed)
    return out


def _rolePrioritySpecs() -> list[dict[str, object]]:
    bestOfFormerAnrocomRoleIds: list[int] = []
    for raw in getattr(config, "bestOfFormerAnrocomRoleIds", []) or []:
        parsed = _toPositiveInt(raw)
        if parsed > 0 and parsed not in bestOfFormerAnrocomRoleIds:
            bestOfFormerAnrocomRoleIds.append(parsed)
    legacyBestOfFormerAnrocomRoleId = _toPositiveInt(getattr(config, "bestOfFormerAnrocomRoleId", 0))
    if (
        legacyBestOfFormerAnrocomRoleId > 0
        and legacyBestOfFormerAnrocomRoleId not in bestOfFormerAnrocomRoleIds
    ):
        bestOfFormerAnrocomRoleIds.append(legacyBestOfFormerAnrocomRoleId)

    bestOfAnrocomRoleIds: list[int] = []
    for raw in getattr(config, "bestOfAnrocomRoleIds", []) or []:
        parsed = _toPositiveInt(raw)
        if parsed > 0 and parsed not in bestOfAnrocomRoleIds:
            bestOfAnrocomRoleIds.append(parsed)
    legacyBestOfAnrocomRoleId = _toPositiveInt(getattr(config, "bestOfAnrocomRoleId", 0))
    if legacyBestOfAnrocomRoleId > 0 and legacyBestOfAnrocomRoleId not in bestOfAnrocomRoleIds:
        bestOfAnrocomRoleIds.append(legacyBestOfAnrocomRoleId)
    if not bestOfAnrocomRoleIds:
        for fallbackRoleId in (
            _toPositiveInt(getattr(config, "sectionChiefRoleId", 0)),
            _toPositiveInt(getattr(config, "cnoRoleId", 0)),
            _toPositiveInt(getattr(config, "dooRoleId", 0)),
            _toPositiveInt(getattr(config, "ddooRoleId", 0)),
        ):
            if fallbackRoleId > 0 and fallbackRoleId not in bestOfAnrocomRoleIds:
                bestOfAnrocomRoleIds.append(fallbackRoleId)

    return [
        {
            "label": "Former MR",
            "roleId": _toPositiveInt(getattr(config, "bestOfFormerMrRoleId", 0)),
            "priorityRank": 1,
        },
        {
            "label": "MR",
            "roleId": _toPositiveInt(
                getattr(config, "bestOfMrRoleId", getattr(config, "middleRankRoleId", 0))
            ),
            "priorityRank": 2,
        },
        {
            "label": "Former HR",
            "roleId": _toPositiveInt(getattr(config, "bestOfFormerHrRoleId", 0)),
            "priorityRank": 3,
        },
        {
            "label": "HR",
            "roleId": _toPositiveInt(
                getattr(config, "bestOfHrRoleId", getattr(config, "highRankRoleId", 0))
            ),
            "priorityRank": 4,
        },
        {
            "label": "Former ANROCOM",
            "roleIds": bestOfFormerAnrocomRoleIds,
            "priorityRank": 5,
        },
        {
            "label": "Command Staff",
            "roleId": _toPositiveInt(
                getattr(config, "bestOfCommandStaffRoleId", getattr(config, "commandStaffRoleId", 0))
            ),
            "priorityRank": 6,
        },
        {
            "label": "ANROCOM",
            "roleIds": bestOfAnrocomRoleIds,
            "priorityRank": 7,
        },
    ]


def _priorityLegendText() -> str:
    return "Former MR -> MR -> Former HR -> HR -> Former ANROCOM -> Command Staff -> ANROCOM"


def _candidateSortTuple(candidate: dict[str, object]) -> tuple[int, str, int]:
    return (
        -int(candidate.get("priorityRank") or 0),
        str(candidate.get("sortName") or "").lower(),
        int(candidate.get("userId") or 0),
    )


def _sectionSortTuple(
    sectionLabel: str,
    sectionRankByLabel: dict[str, int],
) -> tuple[int, int, int, str]:
    orderIndex = _bestOfSectionDisplayOrderIndex.get(sectionLabel)
    if orderIndex is not None:
        return (0, int(orderIndex), 0, "")
    return (
        1,
        999,
        -int(sectionRankByLabel.get(sectionLabel) or 0),
        sectionLabel.lower(),
    )


def _displaySectionLabel(sectionLabel: str) -> str:
    label = str(sectionLabel or "").strip()
    if not label:
        return "Candidates"
    if label.lower() == "command staff":
        return "HR"
    return label


def _cleanBestOfDisplayName(name: str) -> str:
    text = str(name or "").strip()
    if not text:
        return text
    if text.startswith("["):
        cleaned = _leadingPrefixRegex.sub("", text).strip()
        if cleaned:
            return cleaned
    return text


def _bestOfSectionColor(sectionLabel: str, default: discord.Color) -> discord.Color:
    rawHex = str(_bestOfSectionColorHexByLabel.get(str(sectionLabel or "").strip()) or "").strip()
    if not rawHex:
        return default
    try:
        return discord.Color(int(rawHex, 16))
    except ValueError:
        return default


class BestOfBallotView(runtimeViewBases.OwnerLockedView):
    def __init__(
        self,
        cog: "BestOfCog",
        *,
        pollRow: dict,
        candidateRows: list[dict],
        memberNames: dict[int, str],
        voterId: int,
        sectionVotesByLabel: dict[str, int],
        selectedSectionLabel: str = "",
        candidatePageIndex: int = 0,
    ):
        super().__init__(
            openerId=voterId,
            timeout=900,
            ownerMessage="This ballot belongs to another user.",
        )
        self.cog = cog
        self.pollRow = pollRow
        self.candidateRows = candidateRows
        self.memberNames = memberNames
        self.voterId = int(voterId)
        self.sectionVotesByLabel: dict[str, int] = {
            str(label): int(userId)
            for label, userId in sectionVotesByLabel.items()
            if str(label).strip() and int(userId) > 0
        }
        self.selectedSectionLabel = str(selectedSectionLabel or "").strip()
        self.candidatePageIndex = max(0, int(candidatePageIndex or 0))

        self.sectionCandidatesByLabel: dict[str, list[dict]] = {}
        self.sectionRankByLabel: dict[str, int] = {}
        for row in self.candidateRows:
            sectionLabel = _displaySectionLabel(str(row.get("priorityLabel") or ""))
            self.sectionCandidatesByLabel.setdefault(sectionLabel, []).append(row)
            currentRank = int(self.sectionRankByLabel.get(sectionLabel) or 0)
            rowRank = int(row.get("priorityRank") or 0)
            if rowRank > currentRank:
                self.sectionRankByLabel[sectionLabel] = rowRank

        self.sectionLabels: list[str] = sorted(
            self.sectionCandidatesByLabel.keys(),
            key=lambda label: _sectionSortTuple(label, self.sectionRankByLabel),
        )
        if not self.sectionLabels:
            self.sectionLabels = ["Candidates"]
            self.sectionCandidatesByLabel["Candidates"] = []
            self.sectionRankByLabel["Candidates"] = 0

        if self.selectedSectionLabel not in self.sectionLabels:
            self.selectedSectionLabel = self.sectionLabels[0]

        self.sectionSelect = discord.ui.Select(
            placeholder="Select section",
            min_values=1,
            max_values=1,
            row=0,
        )
        self.sectionSelect.callback = self.onSectionSelected
        self.add_item(self.sectionSelect)

        self.candidateSelect = discord.ui.Select(
            placeholder="Select candidate",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.candidateSelect.callback = self.onCandidateSelected
        self.add_item(self.candidateSelect)
        self._refreshSectionOptions()
        self._refreshCandidateOptions()
        self._refreshPagerButtons()

    @property
    def pollId(self) -> int:
        return int(self.pollRow.get("pollId") or 0)

    @property
    def pollTitle(self) -> str:
        return str(self.pollRow.get("title") or "Best Of Vote").strip() or "Best Of Vote"

    def _activeSectionRows(self) -> list[dict]:
        return self.sectionCandidatesByLabel.get(self.selectedSectionLabel, [])

    def _hasCompletedAllSections(self) -> bool:
        return bool(self.sectionLabels) and all(
            int(self.sectionVotesByLabel.get(sectionLabel) or 0) > 0
            for sectionLabel in self.sectionLabels
        )

    def _maxCandidatePageIndex(self) -> int:
        totalRows = len(self._activeSectionRows())
        if totalRows <= 0:
            return 0
        return max(0, math.ceil(totalRows / _candidatePageSize) - 1)

    def _activeCandidatePageRows(self) -> list[dict]:
        start = self.candidatePageIndex * _candidatePageSize
        end = start + _candidatePageSize
        return self._activeSectionRows()[start:end]

    def _memberDisplayName(self, userId: int) -> str:
        return str(self.memberNames.get(int(userId)) or f"User {int(userId)}")

    def _refreshSectionOptions(self) -> None:
        options: list[discord.SelectOption] = []
        for sectionLabel in self.sectionLabels:
            sectionCandidates = self.sectionCandidatesByLabel.get(sectionLabel, [])
            selectedUserId = int(self.sectionVotesByLabel.get(sectionLabel) or 0)
            selectedSuffix = " | Voted" if selectedUserId > 0 else " | Not voted"
            label = sectionLabel if len(sectionLabel) <= 95 else sectionLabel[:95] + "..."
            options.append(
                discord.SelectOption(
                    label=label,
                    value=sectionLabel,
                    description=f"{len(sectionCandidates)} candidate(s){selectedSuffix}"[:100],
                    default=sectionLabel == self.selectedSectionLabel,
                )
            )
        self.sectionSelect.options = options
        self.sectionSelect.placeholder = "Auto-advanced section (read-only)"
        self.sectionSelect.disabled = True

    def _nextSectionLabel(self, currentSectionLabel: str) -> str:
        if not self.sectionLabels:
            return str(currentSectionLabel or "")
        try:
            currentIndex = self.sectionLabels.index(currentSectionLabel)
        except ValueError:
            return self.sectionLabels[0]

        # Prefer the next section that is not yet voted.
        total = len(self.sectionLabels)
        for offset in range(1, total + 1):
            sectionLabel = self.sectionLabels[(currentIndex + offset) % total]
            if int(self.sectionVotesByLabel.get(sectionLabel) or 0) <= 0:
                return sectionLabel

        # If all sections already have votes, rotate normally.
        return self.sectionLabels[(currentIndex + 1) % total]

    def _refreshCandidateOptions(self) -> None:
        maxPage = self._maxCandidatePageIndex()
        if self.candidatePageIndex > maxPage:
            self.candidatePageIndex = maxPage

        selectedUserId = int(self.sectionVotesByLabel.get(self.selectedSectionLabel) or 0)
        options: list[discord.SelectOption] = []
        for row in self._activeCandidatePageRows():
            candidateUserId = int(row.get("userId") or 0)
            if candidateUserId <= 0:
                continue
            candidateName = self._memberDisplayName(candidateUserId)
            if len(candidateName) > 95:
                candidateName = candidateName[:95] + "..."
            options.append(
                discord.SelectOption(
                    label=candidateName,
                    value=str(candidateUserId),
                    default=candidateUserId == selectedUserId,
                )
            )

        if not options:
            self.candidateSelect.options = [
                discord.SelectOption(label="No candidates found", value="0", default=True)
            ]
            self.candidateSelect.disabled = True
            self.candidateSelect.placeholder = "No candidates in this section"
            return

        self.candidateSelect.disabled = False
        self.candidateSelect.options = options
        self.candidateSelect.placeholder = (
            f"Select {self.selectedSectionLabel} candidate "
            f"(Page {self.candidatePageIndex + 1}/{maxPage + 1})"
        )

    def _refreshPagerButtons(self) -> None:
        maxPage = self._maxCandidatePageIndex()
        self.prevCandidatesBtn.disabled = self.candidatePageIndex <= 0
        self.nextCandidatesBtn.disabled = self.candidatePageIndex >= maxPage

    def _votesSummaryText(self) -> str:
        lines: list[str] = []
        for sectionLabel in self.sectionLabels:
            selectedUserId = int(self.sectionVotesByLabel.get(sectionLabel) or 0)
            if selectedUserId > 0:
                lines.append(f"{sectionLabel}: <@{selectedUserId}>")
            else:
                lines.append(f"{sectionLabel}: (not selected)")
        summary = "\n".join(lines).strip()
        return summary[:1024] if summary else "(none)"

    def _buildEmbed(self) -> discord.Embed:
        votedSectionCount = 0
        for sectionLabel in self.sectionLabels:
            if int(self.sectionVotesByLabel.get(sectionLabel) or 0) > 0:
                votedSectionCount += 1
        embed = discord.Embed(
            title=f"Best Of Ballot - {self.pollTitle}",
            description=(
                "Thank you for voting!"
                if self._hasCompletedAllSections()
                else "Choose a section, then pick one person for that section. Votes save immediately."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Selected Section", value=self.selectedSectionLabel, inline=True)
        embed.add_field(
            name="Section Progress",
            value=f"{votedSectionCount}/{len(self.sectionLabels)}",
            inline=True,
        )
        embed.add_field(
            name="Candidate Page",
            value=f"{self.candidatePageIndex + 1}/{self._maxCandidatePageIndex() + 1}",
            inline=True,
        )
        embed.add_field(name="Your Votes", value=self._votesSummaryText(), inline=False)
        return embed

    async def _refreshBallotMessage(self, interaction: discord.Interaction) -> None:
        embed = self._buildEmbed()
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            embed=embed,
            view=self,
        )

    def _selectedValueFromInteraction(
        self,
        interaction: discord.Interaction,
        *,
        fallback: str = "",
    ) -> str:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        values = data.get("values")
        if isinstance(values, list) and values:
            first = str(values[0]).strip()
            if first:
                return first
        return str(fallback or "").strip()

    async def onSectionSelected(self, interaction: discord.Interaction) -> None:
        selectedSectionLabel = self._selectedValueFromInteraction(
            interaction,
            fallback=self.selectedSectionLabel,
        )
        if selectedSectionLabel not in self.sectionCandidatesByLabel:
            return
        self.selectedSectionLabel = selectedSectionLabel
        self.candidatePageIndex = 0
        self._refreshSectionOptions()
        self._refreshCandidateOptions()
        self._refreshPagerButtons()
        await self._refreshBallotMessage(interaction)

    async def onCandidateSelected(self, interaction: discord.Interaction) -> None:
        latestPollRow = await bestOfService.getBestOfPoll(self.pollId)
        if not latestPollRow:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="This poll no longer exists.",
                ephemeral=True,
            )
            return
        self.pollRow = latestPollRow
        if await self.cog._autoFinalizePoll(latestPollRow):
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Voting is finalized. This Best Of poll closed after 24 hours.",
                ephemeral=True,
            )
            return
        if str(self.pollRow.get("status") or "").strip().upper() != "OPEN":
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="This poll is already closed.",
                ephemeral=True,
            )
            return

        selectedRaw = self._selectedValueFromInteraction(interaction, fallback="0")
        candidateUserId = _toPositiveInt(selectedRaw, 0)
        validCandidateIds = {
            int(row.get("userId") or 0) for row in self.sectionCandidatesByLabel.get(self.selectedSectionLabel, [])
        }
        if candidateUserId <= 0 or candidateUserId not in validCandidateIds:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="That candidate is not valid for this section.",
                ephemeral=True,
            )
            return

        wasCompletedBeforeVote = self._hasCompletedAllSections()
        await bestOfService.upsertBestOfSectionVote(
            pollId=self.pollId,
            voterId=int(interaction.user.id),
            sectionLabel=self.selectedSectionLabel,
            candidateUserId=candidateUserId,
        )
        self.sectionVotesByLabel[self.selectedSectionLabel] = candidateUserId
        completedAllSections = self._hasCompletedAllSections()
        if not completedAllSections:
            self.selectedSectionLabel = self._nextSectionLabel(self.selectedSectionLabel)
        elif wasCompletedBeforeVote:
            self.selectedSectionLabel = self._nextSectionLabel(self.selectedSectionLabel)
        self.candidatePageIndex = 0
        self._refreshSectionOptions()
        self._refreshCandidateOptions()
        self._refreshPagerButtons()
        await self._refreshBallotMessage(interaction)
        if completedAllSections and not wasCompletedBeforeVote:
            await interactionRuntime.safeInteractionReply(
                interaction,
                content="Thank you for voting!",
                ephemeral=True,
            )

    @discord.ui.button(label="Prev", style=discord.ButtonStyle.secondary, row=2)
    async def prevCandidatesBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.candidatePageIndex = max(0, self.candidatePageIndex - 1)
        self._refreshCandidateOptions()
        self._refreshPagerButtons()
        await self._refreshBallotMessage(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=2)
    async def nextCandidatesBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.candidatePageIndex = min(self._maxCandidatePageIndex(), self.candidatePageIndex + 1)
        self._refreshCandidateOptions()
        self._refreshPagerButtons()
        await self._refreshBallotMessage(interaction)


class BestOfPollView(discord.ui.View):
    def __init__(self, cog: "BestOfCog", pollId: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.pollId = int(pollId)

    async def _loadPoll(self) -> Optional[dict]:
        return await bestOfService.getBestOfPoll(self.pollId)

    @discord.ui.button(
        label="Vote",
        style=discord.ButtonStyle.primary,
        custom_id="best_of:vote",
        row=0,
    )
    async def voteBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.openBallot(interaction, self.pollId)


class BestOfCloseConfirmView(runtimeViewBases.OwnerLockedView):
    def __init__(self, cog: "BestOfCog", *, pollId: int, openerId: int) -> None:
        super().__init__(
            openerId=openerId,
            timeout=300,
            ownerMessage="This confirmation belongs to someone else.",
        )
        self.cog = cog
        self.pollId = int(pollId)

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger)
    async def confirmBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            content="Closing Best Of poll...",
            embed=None,
            view=self,
        )
        await self.cog.closePoll(interaction, self.pollId)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancelBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        for item in self.children:
            item.disabled = True
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            content="Best Of close cancelled.",
            embed=None,
            view=self,
        )


class BestOfResultsSectionView(runtimeViewBases.OwnerLockedView):
    def __init__(
        self,
        *,
        openerId: int,
        pollTitle: str,
        sectionLabel: str,
        entries: list[str],
        status: str,
    ) -> None:
        super().__init__(
            openerId=openerId,
            timeout=86400,
            ownerMessage="These results belong to someone else.",
        )
        self.pollTitle = str(pollTitle or "Best Of Vote").strip() or "Best Of Vote"
        self.sectionLabel = str(sectionLabel or "Results").strip() or "Results"
        self.entries = [str(entry or "").strip() for entry in entries if str(entry or "").strip()] or ["No votes yet."]
        self.status = str(status or "OPEN").strip().upper()
        self.pageIndex = 0
        self._refreshButtons()

    def _maxPageIndex(self) -> int:
        return max(0, math.ceil(len(self.entries) / _resultsSectionPageSize) - 1)

    def _pageEntries(self) -> list[str]:
        start = self.pageIndex * _resultsSectionPageSize
        end = start + _resultsSectionPageSize
        return self.entries[start:end]

    def hasMultiplePages(self) -> bool:
        return self._maxPageIndex() > 0

    def _refreshButtons(self) -> None:
        maxPage = self._maxPageIndex()
        self.prevBtn.disabled = self.pageIndex <= 0
        self.nextBtn.disabled = self.pageIndex >= maxPage

    def _buildEmbed(self) -> discord.Embed:
        defaultColor = discord.Color.green() if self.status == "CLOSED" else discord.Color.blurple()
        embed = discord.Embed(
            title=f"Best Of Results - {self.pollTitle}",
            color=_bestOfSectionColor(self.sectionLabel, defaultColor),
            timestamp=datetime.now(timezone.utc),
        )
        totalEntries = len(self.entries)
        start = self.pageIndex * _resultsSectionPageSize + 1
        end = min(totalEntries, (self.pageIndex + 1) * _resultsSectionPageSize)
        embed.add_field(
            name=f"{self.sectionLabel} ({start}-{end} of {totalEntries})",
            value="\n".join(self._pageEntries())[:1024],
            inline=False,
        )
        embed.set_footer(text=f"Page {self.pageIndex + 1}/{self._maxPageIndex() + 1}")
        return embed

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def prevBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.pageIndex = max(0, self.pageIndex - 1)
        self._refreshButtons()
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            embed=self._buildEmbed(),
            view=self,
        )

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def nextBtn(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.pageIndex = min(self._maxPageIndex(), self.pageIndex + 1)
        self._refreshButtons()
        await runtimeViewBases.safeRefreshInteractionMessage(
            interaction,
            embed=self._buildEmbed(),
            view=self,
        )


class BestOfCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._finalizeTask: asyncio.Task | None = None

    async def cog_load(self) -> None:
        restored = 0
        rows = await bestOfService.listOpenBestOfPollsForViews()
        for row in rows:
            pollId = int(row.get("pollId") or 0)
            messageId = int(row.get("messageId") or 0)
            if pollId <= 0 or messageId <= 0:
                continue
            self.bot.add_view(BestOfPollView(self, pollId), message_id=messageId)
            restored += 1
        if restored > 0:
            log.info("Best Of persistent poll views restored: %d", restored)
        if self._finalizeTask is None or self._finalizeTask.done():
            self._finalizeTask = asyncio.create_task(self._runBestOfFinalizeLoop())

    def cog_unload(self) -> None:
        if self._finalizeTask and not self._finalizeTask.done():
            self._finalizeTask.cancel()
        self._finalizeTask = None

    async def _safeEphemeral(self, interaction: discord.Interaction, message: str) -> None:
        await interactionRuntime.safeInteractionReply(
            interaction,
            content=message,
            ephemeral=True,
        )

    def _isPollExpired(self, pollRow: dict, *, maxOpenHours: int = 24) -> bool:
        status = str(pollRow.get("status") or "").strip().upper()
        if status != "OPEN":
            return False
        createdAtRaw = str(pollRow.get("createdAt") or "").strip()
        if not createdAtRaw:
            return False
        try:
            createdAt = datetime.fromisoformat(createdAtRaw)
        except ValueError:
            return False
        if createdAt.tzinfo is None:
            createdAt = createdAt.replace(tzinfo=timezone.utc)
        deadline = createdAt + timedelta(hours=max(1, int(maxOpenHours or 24)))
        return datetime.now(timezone.utc) >= deadline

    async def _autoFinalizePoll(self, pollRow: dict) -> bool:
        pollId = int(pollRow.get("pollId") or 0)
        if pollId <= 0:
            return False
        if not self._isPollExpired(pollRow, maxOpenHours=24):
            return False
        closedBy = int(self.bot.user.id) if self.bot.user else 0
        await bestOfService.closeBestOfPoll(pollId=pollId, closedBy=closedBy)
        await self._refreshPollMessage(pollId)
        log.info("Best Of poll auto-finalized after 24h: pollId=%d", pollId)
        return True

    async def _runBestOfFinalizeLoop(self) -> None:
        while True:
            try:
                await self._runBestOfFinalizeTick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Best Of auto-finalize loop failed.")
            await asyncio.sleep(60)

    async def _runBestOfFinalizeTick(self) -> None:
        duePollRows = await bestOfService.listBestOfPollsReadyToFinalize(maxOpenHours=24)
        for pollRow in duePollRows:
            try:
                await self._autoFinalizePoll(pollRow)
            except Exception:
                pollId = int(pollRow.get("pollId") or 0)
                log.exception("Best Of auto-finalize failed for pollId=%d", pollId)

    async def _resolveMemberById(self, guild: discord.Guild, userId: int) -> Optional[discord.Member]:
        if userId <= 0:
            return None
        member = guild.get_member(int(userId))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(int(userId))
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    def _configuredSelectedLrIds(self) -> list[int]:
        out: list[int] = []
        for raw in getattr(config, "bestOfSelectedLrUserIds", []) or []:
            parsed = _toPositiveInt(raw)
            if parsed <= 0 or parsed in out:
                continue
            out.append(parsed)
        return out

    async def _resolveCandidateDisplayNames(
        self,
        guild: discord.Guild,
        candidateMembersByUserId: dict[int, discord.Member],
    ) -> dict[int, str]:
        out: dict[int, str] = {}
        if not candidateMembersByUserId:
            return out

        lookupConcurrency = max(
            1,
            min(20, int(getattr(config, "recruitmentRoverLookupConcurrency", 8) or 8)),
        )
        semaphore = asyncio.Semaphore(lookupConcurrency)

        async def _resolve(member: discord.Member) -> None:
            fallbackName = _cleanBestOfDisplayName(
                str(member.display_name or member.name or f"User {member.id}")
            )
            resolvedName = fallbackName
            try:
                async with semaphore:
                    lookup = await robloxUsers.fetchRobloxUser(int(member.id), guildId=int(guild.id))
                robloxUsername = str(lookup.robloxUsername or "").strip()
                if robloxUsername:
                    resolvedName = robloxUsername
            except Exception:
                log.exception("Best Of RoVer lookup failed for userId=%d", int(member.id))
            out[int(member.id)] = resolvedName

        await asyncio.gather(*(_resolve(member) for member in candidateMembersByUserId.values()))
        return out

    async def _candidateRowsForPoll(
        self,
        guild: discord.Guild,
        selectedLrIds: list[int],
    ) -> tuple[list[dict], dict[int, str], dict[str, int]]:
        candidatesByUserId: dict[int, dict] = {}
        roleHits: dict[str, int] = {}
        roleHitMembersByLabel: dict[str, set[int]] = {}
        candidateMembersByUserId: dict[int, discord.Member] = {}
        memberNameByUserId: dict[int, str] = {}

        async def _registerCandidate(member: discord.Member, *, label: str, priorityRank: int) -> None:
            if member.bot:
                return
            existing = candidatesByUserId.get(int(member.id))
            if existing and int(existing.get("priorityRank") or 0) > int(priorityRank):
                return
            displayName = _cleanBestOfDisplayName(str(member.display_name or member.name or f"User {member.id}"))
            candidateMembersByUserId[int(member.id)] = member
            memberNameByUserId[int(member.id)] = displayName
            candidatesByUserId[int(member.id)] = {
                "userId": int(member.id),
                "priorityRank": int(priorityRank),
                "priorityLabel": str(label),
                "displayName": displayName,
                "sortName": displayName,
            }

        # Selected LRs are explicitly added and stay at the lowest priority.
        for userId in selectedLrIds:
            member = await self._resolveMemberById(guild, int(userId))
            if member is None:
                continue
            await _registerCandidate(member, label="Selected LR", priorityRank=0)
            roleHits["Selected LR"] = roleHits.get("Selected LR", 0) + 1

        for roleSpec in _rolePrioritySpecs():
            label = str(roleSpec.get("label") or "Role")
            priorityRank = int(roleSpec.get("priorityRank") or 0)
            if label not in roleHitMembersByLabel:
                roleHitMembersByLabel[label] = set()

            rawRoleIds = roleSpec.get("roleIds")
            roleIds: list[int] = []
            if isinstance(rawRoleIds, (list, tuple, set)):
                for rawRoleId in rawRoleIds:
                    parsedRoleId = _toPositiveInt(rawRoleId)
                    if parsedRoleId > 0 and parsedRoleId not in roleIds:
                        roleIds.append(parsedRoleId)
            if not roleIds:
                fallbackRoleId = _toPositiveInt(roleSpec.get("roleId"))
                if fallbackRoleId > 0:
                    roleIds.append(fallbackRoleId)

            for roleId in roleIds:
                role = guild.get_role(roleId)
                if role is None:
                    continue
                for member in role.members:
                    roleHitMembersByLabel[label].add(int(member.id))
                    await _registerCandidate(member, label=label, priorityRank=priorityRank)

        resolvedDisplayNames = await self._resolveCandidateDisplayNames(guild, candidateMembersByUserId)
        for userId, row in candidatesByUserId.items():
            resolvedName = str(resolvedDisplayNames.get(int(userId)) or row.get("displayName") or "").strip()
            if resolvedName:
                row["displayName"] = resolvedName
                row["sortName"] = resolvedName
                memberNameByUserId[int(userId)] = resolvedName

        candidateRows = sorted(candidatesByUserId.values(), key=_candidateSortTuple)
        for index, row in enumerate(candidateRows):
            row["sortOrder"] = int(index)
            row.pop("sortName", None)
        for label, memberIds in roleHitMembersByLabel.items():
            roleHits[label] = len(memberIds)
        return candidateRows, memberNameByUserId, roleHits

    async def _buildPollEmbed(self, pollRow: dict) -> discord.Embed:
        pollId = int(pollRow.get("pollId") or 0)
        title = str(pollRow.get("title") or "Best Of Vote").strip() or "Best Of Vote"

        candidateRows = await bestOfService.listBestOfCandidates(pollId)
        sectionLabelsPresent: set[str] = set()
        sectionRankByLabel: dict[str, int] = {}
        for row in candidateRows:
            sectionLabel = _displaySectionLabel(str(row.get("priorityLabel") or ""))
            if sectionLabel:
                sectionLabelsPresent.add(sectionLabel)
            currentRank = int(sectionRankByLabel.get(sectionLabel) or 0)
            rowRank = int(row.get("priorityRank") or 0)
            if rowRank > currentRank:
                sectionRankByLabel[sectionLabel] = rowRank
        orderedSectionLabels = sorted(
            sectionLabelsPresent,
            key=lambda label: _sectionSortTuple(label, sectionRankByLabel),
        )

        sectionVoteRows = await bestOfService.listBestOfSectionVoteCounts(pollId)
        rowsBySection: dict[str, list[dict]] = {}
        for row in sectionVoteRows:
            sectionLabel = _displaySectionLabel(str(row.get("sectionLabel") or ""))
            if not sectionLabel:
                continue
            rowsBySection.setdefault(sectionLabel, []).append(row)

        leadingVoteBySection: dict[str, dict] = {}
        for sectionLabel, rows in rowsBySection.items():
            if not rows:
                continue
            leadingVoteBySection[sectionLabel] = max(
                rows,
                key=lambda row: (
                    int(row.get("voteCount") or 0),
                    -int(row.get("candidateUserId") or 0),
                ),
            )

        embed = discord.Embed(
            title=f"Best Of Vote - {title}",
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.description = "Use **Vote** to choose one person in each section. Results are sent by DM when the poll closes."
        if orderedSectionLabels:
            leaderLines: list[str] = []
            for sectionLabel in orderedSectionLabels:
                leadingRow = leadingVoteBySection.get(sectionLabel)
                if not leadingRow:
                    leaderLines.append(f"{sectionLabel}: (no votes yet)")
                    continue
                candidateUserId = int(leadingRow.get("candidateUserId") or 0)
                voteCount = int(leadingRow.get("voteCount") or 0)
                if candidateUserId <= 0:
                    leaderLines.append(f"{sectionLabel}: (no votes yet)")
                    continue
                leaderLines.append(f"{sectionLabel}: <@{candidateUserId}> ({voteCount})")
            embed.add_field(name="Current Leaders", value="\n".join(leaderLines)[:1024], inline=False)
        embed.set_footer(text="One vote per section")
        return embed

    async def _candidateNameMap(self, guild: discord.Guild, pollId: int) -> dict[int, str]:
        out: dict[int, str] = {}
        candidateRows = await bestOfService.listBestOfCandidates(pollId)
        for row in candidateRows:
            userId = int(row.get("userId") or 0)
            if userId <= 0:
                continue
            storedDisplayName = str(row.get("displayName") or "").strip()
            if storedDisplayName:
                out[userId] = storedDisplayName
                continue
            member = guild.get_member(userId)
            if member is not None:
                out[userId] = _cleanBestOfDisplayName(str(member.display_name or member.name or f"User {userId}"))
        return out

    async def _resultsDmPayload(
        self,
        guild: discord.Guild,
        pollId: int,
    ) -> tuple[list[discord.Embed], list[dict[str, object]]]:
        pollRow = await bestOfService.getBestOfPoll(pollId)
        if not pollRow:
            return (
                [
                    discord.Embed(
                        title="Best Of Results",
                        description="Poll not found.",
                        color=discord.Color.red(),
                    )
                ],
                [],
            )

        sectionVoteRows = await bestOfService.listBestOfSectionVoteCounts(pollId)
        voterCount = await bestOfService.countBestOfVotes(pollId)
        sectionVoteCount = await bestOfService.countBestOfSectionVotes(pollId)
        title = str(pollRow.get("title") or "Best Of Vote").strip() or "Best Of Vote"
        status = str(pollRow.get("status") or "OPEN").strip().upper()
        candidateRows = await bestOfService.listBestOfCandidates(pollId)
        candidateByUserId: dict[int, dict] = {int(row.get("userId") or 0): row for row in candidateRows}

        summaryEmbed = discord.Embed(
            title=f"Best Of Results - {title}",
            color=discord.Color.green() if status == "CLOSED" else discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        summaryEmbed.add_field(name="Status", value=("Closed" if status == "CLOSED" else "Open"), inline=True)
        summaryEmbed.add_field(name="Voters", value=str(voterCount), inline=True)
        summaryEmbed.add_field(name="Section Votes", value=str(sectionVoteCount), inline=True)

        if not sectionVoteRows:
            summaryEmbed.add_field(name="Leaderboard", value="No votes yet.", inline=False)
            return ([summaryEmbed], [])

        sectionRowsByLabel: dict[str, list[dict]] = {}
        for row in sectionVoteRows:
            sectionLabel = _displaySectionLabel(str(row.get("sectionLabel") or ""))
            sectionRowsByLabel.setdefault(sectionLabel, []).append(row)

        sectionRankByLabel: dict[str, int] = {}
        for row in candidateRows:
            sectionLabel = _displaySectionLabel(str(row.get("priorityLabel") or ""))
            currentRank = int(sectionRankByLabel.get(sectionLabel) or 0)
            rowRank = int(row.get("priorityRank") or 0)
            if rowRank > currentRank:
                sectionRankByLabel[sectionLabel] = rowRank

        sectionLabels = sorted(
            sectionRowsByLabel.keys(),
            key=lambda label: _sectionSortTuple(label, sectionRankByLabel),
        )
        sectionPayloads: list[dict[str, object]] = []
        for sectionLabel in sectionLabels[:15]:
            rows = sorted(
                sectionRowsByLabel.get(sectionLabel, []),
                key=lambda row: (
                    -int(row.get("voteCount") or 0),
                    int(row.get("candidateUserId") or 0),
                ),
            )
            entries: list[str] = []
            for row in rows:
                candidateUserId = int(row.get("candidateUserId") or 0)
                voteCount = int(row.get("voteCount") or 0)
                candidate = candidateByUserId.get(candidateUserId)
                storedDisplayName = str(candidate.get("displayName") or "").strip() if candidate is not None else ""
                member = guild.get_member(candidateUserId)
                if storedDisplayName:
                    candidateName = storedDisplayName
                elif member is not None:
                    candidateName = _cleanBestOfDisplayName(
                        str(member.display_name or member.name or f"User {candidateUserId}")
                    )
                elif candidate is not None:
                    candidateName = f"User {candidateUserId}"
                else:
                    candidateName = f"User {candidateUserId}"
                entries.append(f"{candidateName} - {voteCount}")
            if not entries:
                entries = ["No votes yet."]
            sectionPayloads.append(
                {
                    "pollTitle": title,
                    "sectionLabel": sectionLabel,
                    "status": status,
                    "entries": entries,
                }
            )
        return ([summaryEmbed], sectionPayloads)

    async def _sendResultsDm(self, guild: discord.Guild, pollRow: dict, pollId: int) -> None:
        summaryEmbeds, sectionPayloads = await self._resultsDmPayload(guild, pollId)
        creatorUserId = int(pollRow.get("createdBy") or 0)
        targetUserIds: list[int] = []
        for rawUserId in (creatorUserId, _bigFrenchManUserID):
            userId = int(rawUserId or 0)
            if userId > 0 and userId not in targetUserIds:
                targetUserIds.append(userId)

        for index, userId in enumerate(targetUserIds):
            try:
                user = await self.bot.fetch_user(userId)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                user = None
            if user is None:
                log.warning(
                    "Best Of results recipient lookup failed for pollId=%d targetUserId=%d",
                    pollId,
                    userId,
                )
                continue

            try:
                for embed in summaryEmbeds:
                    await user.send(embed=embed)
                for payload in sectionPayloads:
                    pollTitle = str(payload.get("pollTitle") or "Best Of Vote")
                    sectionLabel = str(payload.get("sectionLabel") or "Results")
                    status = str(payload.get("status") or "OPEN")
                    entries = [str(entry) for entry in (payload.get("entries") or [])]
                    view = BestOfResultsSectionView(
                        openerId=int(user.id),
                        pollTitle=pollTitle,
                        sectionLabel=sectionLabel,
                        entries=entries,
                        status=status,
                    )
                    if view.hasMultiplePages():
                        await user.send(embed=view._buildEmbed(), view=view)
                    else:
                        await user.send(embed=view._buildEmbed())
                if index > 0:
                    log.info(
                        "Best Of results for pollId=%d were delivered to the fallback recipient userId=%d",
                        pollId,
                        userId,
                    )
                return
            except discord.Forbidden:
                log.warning(
                    "Best Of results DM was blocked for pollId=%d targetUserId=%d",
                    pollId,
                    userId,
                )
            except Exception as exc:
                log.warning(
                    "Best Of results DM failed for pollId=%d targetUserId=%d: %s",
                    pollId,
                    userId,
                    exc,
                )

        log.warning(
            "Best Of results could not be delivered to the creator or fallback recipient for pollId=%d",
            pollId,
        )

    async def _refreshPollMessage(self, pollId: int) -> None:
        pollRow = await bestOfService.getBestOfPoll(pollId)
        if not pollRow:
            return
        messageId = int(pollRow.get("messageId") or 0)
        channelId = int(pollRow.get("channelId") or 0)
        guildId = int(pollRow.get("guildId") or 0)
        if messageId <= 0 or channelId <= 0 or guildId <= 0:
            return

        guild = self.bot.get_guild(guildId)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(guildId)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return
        channel = guild.get_channel(channelId)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channelId)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException):
                return
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        try:
            message = await channel.fetch_message(messageId)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            return

        view = BestOfPollView(self, pollId)
        if str(pollRow.get("status") or "").strip().upper() != "OPEN":
            view.voteBtn.disabled = True
        await interactionRuntime.safeMessageEdit(
            message,
            embed=_simpleClosedEmbed,
            view=view,
        )

        await self._sendResultsDm(guild, pollRow, pollId)

        self.bot.add_view(view, message_id=message.id)

    async def openBallot(self, interaction: discord.Interaction, pollId: int) -> None:
        pollRow = await bestOfService.getBestOfPoll(int(pollId))
        if not pollRow:
            await self._safeEphemeral(interaction, "Poll not found.")
            return
        if await self._autoFinalizePoll(pollRow):
            await self._safeEphemeral(
                interaction,
                "Voting is finalized. This Best Of poll closed after 24 hours.",
            )
            return
        if str(pollRow.get("status") or "").strip().upper() != "OPEN":
            await self._safeEphemeral(interaction, "This poll is closed.")
            return
        if not interaction.guild:
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return

        candidateRows = await bestOfService.listBestOfCandidates(int(pollId))
        if not candidateRows:
            await self._safeEphemeral(interaction, "This poll has no candidates.")
            return
        memberNames = await self._candidateNameMap(interaction.guild, int(pollId))
        sectionVoteRows = await bestOfService.listBestOfSectionVotesForVoter(
            int(pollId),
            int(interaction.user.id),
        )
        sectionVotesByLabel: dict[str, int] = {}
        for row in sectionVoteRows:
            sectionLabel = _displaySectionLabel(str(row.get("sectionLabel") or ""))
            if not sectionLabel:
                continue
            sectionVotesByLabel[sectionLabel] = int(row.get("candidateUserId") or 0)

        defaultSectionLabel = ""
        for row in candidateRows:
            sectionLabel = _displaySectionLabel(str(row.get("priorityLabel") or ""))
            if sectionLabel:
                defaultSectionLabel = sectionLabel
                break
        if sectionVotesByLabel:
            for sectionLabel in sectionVotesByLabel.keys():
                if sectionLabel:
                    defaultSectionLabel = sectionLabel
                    break

        ballotView = BestOfBallotView(
            self,
            pollRow=pollRow,
            candidateRows=candidateRows,
            memberNames=memberNames,
            voterId=int(interaction.user.id),
            sectionVotesByLabel=sectionVotesByLabel,
            selectedSectionLabel=defaultSectionLabel,
        )
        await interactionRuntime.safeInteractionReply(
            interaction,
            embed=ballotView._buildEmbed(),
            view=ballotView,
            ephemeral=True,
        )

    async def closePoll(self, interaction: discord.Interaction, pollId: int) -> None:
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await self._safeEphemeral(interaction, "This command can only be used in a server.")
            return
        if not runtimePermissions.hasAdministrator(interaction.user):
            await self._safeEphemeral(interaction, "Only administrators can close this poll.")
            return

        pollRow = await bestOfService.getBestOfPoll(int(pollId))
        if not pollRow:
            await self._safeEphemeral(interaction, "Poll not found.")
            return
        if str(pollRow.get("status") or "").strip().upper() != "OPEN":
            await self._safeEphemeral(interaction, "This poll is already closed.")
            return
        createdBy = int(pollRow.get("createdBy") or 0)
        if createdBy > 0 and createdBy != int(interaction.user.id):
            await self._safeEphemeral(interaction, "Only the poll creator can close this poll with `/best-of`.")
            return

        await bestOfService.closeBestOfPoll(pollId=int(pollId), closedBy=int(interaction.user.id))
        await self._refreshPollMessage(int(pollId))
        await self._safeEphemeral(interaction, "Best Of poll closed.")

    @app_commands.command(name="best-of", description="Create a high-capacity Best Of vote.")
    @app_commands.describe(
        title="Optional poll title (defaults to this month).",
        selected_lrs="Optional mentions or user IDs for selected LR candidates.",
    )
    @app_commands.rename(selected_lrs="selected-lrs")
    @app_commands.checks.has_permissions(administrator=True)
    async def bestOf(
        self,
        interaction: discord.Interaction,
        title: Optional[str] = None,
        selected_lrs: Optional[str] = None,
    ) -> None:
        if not interaction.guild or not isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            await self._safeEphemeral(interaction, "Use this command in a server text channel.")
            return

        existingOpenPoll = await bestOfService.getOpenBestOfPollForChannel(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
        )
        if existingOpenPoll:
            if await self._autoFinalizePoll(existingOpenPoll):
                existingOpenPoll = None
            else:
                createdBy = int(existingOpenPoll.get("createdBy") or 0)
                if createdBy > 0 and createdBy != int(interaction.user.id):
                    await self._safeEphemeral(
                        interaction,
                        f"An open Best Of poll already exists in this channel. Only <@{createdBy}> can close it with `/best-of`.",
                    )
                    return
                await interactionRuntime.safeInteractionReply(
                    interaction,
                    content="An open Best Of poll already exists in this channel. Confirm below if you want to close it.",
                    view=BestOfCloseConfirmView(
                        self,
                        pollId=int(existingOpenPoll.get("pollId") or 0),
                        openerId=int(interaction.user.id),
                    ),
                    ephemeral=True,
                )
                return

        await interactionRuntime.safeInteractionDefer(interaction, ephemeral=True)

        createdAt = datetime.now(timezone.utc)
        defaultTitle = createdAt.strftime("%B %Y")
        pollTitle = str(title or defaultTitle).strip() or defaultTitle

        configuredLrIds = self._configuredSelectedLrIds()
        inlineLrIds = _parseUserIdList(str(selected_lrs or ""))
        selectedLrIds: list[int] = []
        for userId in configuredLrIds + inlineLrIds:
            if userId > 0 and userId not in selectedLrIds:
                selectedLrIds.append(userId)

        candidateRows, _, roleHits = await self._candidateRowsForPoll(interaction.guild, selectedLrIds)
        if not candidateRows:
            await self._safeEphemeral(
                interaction,
                "No candidates found. Configure the Best Of role IDs first.",
            )
            return

        pollId = await bestOfService.createBestOfPoll(
            guildId=int(interaction.guild.id),
            channelId=int(interaction.channel.id),
            createdBy=int(interaction.user.id),
            title=pollTitle,
        )
        await bestOfService.replaceBestOfCandidates(int(pollId), candidateRows)

        pollRow = await bestOfService.getBestOfPoll(int(pollId))
        if not pollRow:
            await self._safeEphemeral(interaction, "Could not create the poll.")
            return

        pollView = BestOfPollView(self, int(pollId))
        embed = await self._buildPollEmbed(pollRow)
        postContent = (
            "Best Of voting is now open.\n"
            "Use **Vote** to pick one person per section."
        )

        try:
            message = await interaction.channel.send(content=postContent, embed=embed, view=pollView)
        except (discord.Forbidden, discord.HTTPException):
            await self._safeEphemeral(interaction, "I could not post the poll in this channel.")
            return

        await bestOfService.setBestOfPollMessageId(int(pollId), int(message.id))
        self.bot.add_view(pollView, message_id=message.id)

        nonZeroRoleHits = {label: count for label, count in roleHits.items() if int(count) > 0}
        roleHitSummary = ", ".join(f"{label}: {count}" for label, count in nonZeroRoleHits.items()) or "none"
        await self._safeEphemeral(
            interaction,
            (
                f"Best Of poll created with {len(candidateRows)} candidates. "
                f"Source hits -> {roleHitSummary}\n"
                "Please use `/best-of` again if you want to close the poll."
            ),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BestOfCog(bot))

