from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Optional

import discord
from PIL import Image, ImageDraw, ImageFont

from features.staff.bgIntelligence import scoring
from features.staff.sessions import bgBuckets


_FIELD_LIMIT = 1024
_SPIDEREYE_RED = 0xED1C24
_BADGE_GRAPH_FILENAME = "bg-intel-badge-timeline.png"
_REPORT_TEXT_FILENAME = "bg-intel-report.txt"
_REPORT_TEXT_UPLOAD_LIMIT_BYTES = 7_500_000


def _truncate(text: str, limit: int = _FIELD_LIMIT) -> str:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean or "(none)"
    return clean[: max(0, limit - 3)].rstrip() + "..."


def _formatAge(days: Optional[int]) -> str:
    if days is None:
        return "unknown"
    if days >= 365:
        years = days / 365
        return f"{days:,} days ({years:.1f} years)"
    return f"{days:,} days"


def _scoreColor(score: scoring.RiskScore) -> discord.Color:
    if not score.scored:
        return discord.Color.dark_grey()
    scoreValue = int(score.score)
    if scoreValue >= 80:
        return discord.Color.dark_red()
    if scoreValue >= 60:
        return discord.Color.red()
    if scoreValue >= 40:
        return discord.Color.orange()
    if scoreValue >= 20:
        return discord.Color.gold()
    return discord.Color.green()


def _overviewColor() -> discord.Color:
    return discord.Color(_SPIDEREYE_RED)


def _safeInt(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _scanStatus(value: Any) -> str:
    return str(value or "SKIPPED").strip().upper() or "SKIPPED"


def _scanSummary(status: Any, error: Optional[str] = None) -> str:
    normalized = _scanStatus(status)
    if normalized == "OK":
        return "Scan complete."
    if normalized == "PRIVATE":
        return "Inventory is private or hidden."
    if normalized == "NO_ROVER":
        return "No Roblox account was resolved."
    if normalized == "SKIPPED":
        return "This check was skipped."
    if error:
        return _truncate(str(error), 220)
    return f"Status: `{normalized}`"


def _parseDate(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _plainDate(value: Any) -> str:
    parsed = _parseDate(value)
    if parsed is None:
        return "unknown"
    return f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}"


def _discordDateWithRelative(value: Any) -> str:
    parsed = _parseDate(value)
    if parsed is None:
        return "unknown"
    unix = int(parsed.astimezone(timezone.utc).timestamp())
    return f"<t:{unix}:D> (<t:{unix}:R>)"


def _shortDate(value: Any) -> str:
    parsed = _parseDate(value)
    if parsed is None:
        return "unknown"
    return parsed.strftime("%b %d, %Y")


def _formatPercent(value: float) -> str:
    return f"{round(value * 100):.0f}%"


def _formatRobux(value: Any) -> str:
    return f"{_safeInt(value):,} Robux"


def _compactConfidenceValue(score: scoring.RiskScore) -> str:
    return f"{score.confidenceLabel} ({int(score.confidence)}%)"


def _publicBandValue(score: scoring.RiskScore) -> str:
    if not score.scored:
        return str(score.band or "Manual Review")
    return str(score.band or "Manual Review")


def _publicReviewRiskValue(score: scoring.RiskScore) -> str:
    if not score.scored:
        return "Not scored"
    return f"{int(score.score)}/100"


def _displayName(report: Any) -> str:
    robloxUsername = str(getattr(report, "robloxUsername", "") or "").strip()
    if robloxUsername:
        return robloxUsername
    displayName = str(getattr(report, "discordDisplayName", "") or "").strip()
    return displayName or "Unknown User"


def _markdownLinkLabel(value: Any) -> str:
    clean = str(value or "Roblox Profile").strip() or "Roblox Profile"
    return clean.replace("[", "(").replace("]", ")")


def _robloxProfileUrl(report: Any) -> str | None:
    robloxUserId = getattr(report, "robloxUserId", None)
    try:
        parsed = int(robloxUserId)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return f"https://www.roblox.com/users/{parsed}/profile"


def _robloxProfileLink(report: Any) -> str | None:
    profileUrl = _robloxProfileUrl(report)
    if not profileUrl:
        return None
    return f"[{_markdownLinkLabel(_displayName(report))}]({profileUrl})"


def _publicHeaderLine(report: Any, prefix: str = "Background Check Overview") -> str:
    displayName = _displayName(report)
    profileLink = _robloxProfileLink(report)
    linkedName = profileLink or _markdownLinkLabel(displayName)
    return f"## {prefix} - {linkedName}"


def _field(
    embed: discord.Embed,
    name: str,
    value: str,
) -> None:
    embed.add_field(name=name, value=_truncate(value), inline=False)


def _groupLine(group: dict[str, Any]) -> str:
    groupName = group.get("name") or "(unknown)"
    groupId = group.get("id") or "?"
    role = group.get("role")
    rank = group.get("rank")
    roleText = f" - {role} ({rank})" if role or rank else ""
    detailParts: list[str] = []
    try:
        memberCount = int(group.get("memberCount")) if group.get("memberCount") is not None else None
    except (TypeError, ValueError):
        memberCount = None
    if memberCount is not None:
        detailParts.append(f"{memberCount:,} members")
    if group.get("hasVerifiedBadge") is True:
        detailParts.append("verified")
    if group.get("isLocked") is True:
        detailParts.append("locked")
    if group.get("publicEntryAllowed") is False:
        detailParts.append("closed entry")
    detailText = f" | {', '.join(detailParts)}" if detailParts else ""
    return f"{groupName} [{groupId}]{roleText}{detailText}"


def _itemLine(item: dict[str, Any]) -> str:
    itemName = item.get("name") or "Unknown item"
    itemId = item.get("id") or "?"
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
    if matchType == "item":
        detailParts.append("exact item")
    elif matchType == "creator":
        detailParts.append("flagged creator")
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
            fuzzyScore = item.get("fuzzyScore")
            try:
                fuzzyLabel = f"{float(fuzzyScore):.0f}"
            except (TypeError, ValueError):
                fuzzyLabel = "?"
            detailParts.append(f"fuzzy keyword {fuzzyLabel}: {keyword}")
        elif matchMode == "normalized":
            detailParts.append(f"normalized keyword: {keyword}")
        else:
            detailParts.append(f"keyword: {keyword}")
    extraSignals = max(0, int(item.get("matchCount") or 0) - 1)
    if extraSignals > 0:
        detailParts.append(f"+{extraSignals} more signal(s)")
    suffix = f" | {', '.join(detailParts)}" if detailParts else ""
    return f"{itemName} [{itemId}]{creatorText}{suffix}"


def _badgeLine(badge: dict[str, Any]) -> str:
    badgeId = badge.get("badgeId") or "?"
    line = f"Badge {badgeId}"
    if badge.get("awardedDate"):
        line += f" (awarded {badge.get('awardedDate')})"
    if badge.get("note"):
        line += f" - {badge.get('note')}"
    return line


def _directMatchLine(match: dict[str, Any]) -> str:
    matchType = str(match.get("type") or "match")
    value = match.get("value") or "?"
    minimumScore = match.get("minimumScore")
    note = str(match.get("note") or "").strip()
    line = f"{matchType}: {value}"
    if minimumScore:
        line += f" (minimum {minimumScore})"
    if note:
        line += f" - {note}"
    return line


def _altMatchLine(match: dict[str, Any]) -> str:
    candidate = str(match.get("candidateUsername") or "unknown").strip()
    known = str(match.get("knownRobloxUsername") or "unknown").strip()
    kind = str(match.get("candidateKind") or "username").strip().replace("_", " ")
    reason = str(match.get("reason") or "username variant").strip()
    source = str(match.get("source") or "known member").strip().replace("_", " ")
    strength = str(match.get("strength") or "weak").strip().lower()
    evidenceType = str(match.get("evidenceType") or "alt signal").strip().replace("_", " ")
    detailParts: list[str] = []
    if match.get("knownDiscordUserId"):
        detailParts.append(f"Discord `{match.get('knownDiscordUserId')}`")
    if match.get("knownRobloxUserId"):
        detailParts.append(f"Roblox `{match.get('knownRobloxUserId')}`")
    if match.get("rank"):
        detailParts.append(str(match.get("rank")))
    if match.get("department"):
        detailParts.append(str(match.get("department")))
    if match.get("knownMemberLabel"):
        detailParts.append(str(match.get("knownMemberLabel")))
    if match.get("sharedGroupCount"):
        detailParts.append(f"{_safeInt(match.get('sharedGroupCount'))} shared group(s)")
    if match.get("similarity") is not None:
        try:
            detailParts.append(f"{float(match.get('similarity')):.0%} similar")
        except (TypeError, ValueError):
            pass
    if match.get("note"):
        detailParts.append(f"note: {str(match.get('note'))[:120]}")
    detail = f" | {', '.join(detailParts)}" if detailParts else ""
    subject = f"{candidate} ({kind})" if candidate != "unknown" else "Target identity"
    return f"[{strength}] {evidenceType}: {subject} -> known `{known}` - {reason}; source `{source}`{detail}"


def _externalMatchLine(match: dict[str, Any]) -> str:
    source = str(match.get("source") or "External").strip()
    matchType = str(match.get("type") or "record").strip().replace("_", " ")
    if source.lower() == "tase":
        scoreSum = match.get("scoreSum")
        guildCount = int(match.get("guildCount") or 0)
        pieces = [f"TASE {matchType}"]
        if scoreSum is not None:
            pieces.append(f"score sum {scoreSum:g}" if isinstance(scoreSum, (int, float)) else f"score sum {scoreSum}")
        if guildCount:
            pieces.append(f"{guildCount} server(s)")
        if match.get("pastOffender"):
            pieces.append("past offender")
        return " - ".join(pieces)
    if source.lower() == "moco-co":
        username = str(match.get("username") or "").strip()
        groupCount = int(match.get("groupCount") or 0)
        pieces = ["Moco-co Roblox safety record"]
        if username:
            pieces.append(username)
        if groupCount:
            pieces.append(f"{groupCount} group(s)")
        if match.get("lastSeen"):
            pieces.append(f"last seen {match.get('lastSeen')}")
        return " - ".join(pieces)
    subjectId = match.get("subjectId") or "?"
    return f"{source} {matchType} - {subjectId}"


def _shouldShowExternalDetail(detail: dict[str, Any]) -> bool:
    status = str(detail.get("status") or "SKIPPED").strip().upper()
    if status != "SKIPPED":
        return True
    summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
    reason = str(summary.get("reason") or "").strip().lower()
    return reason not in {
        "disabled",
        "missing_api_key",
        "missing_token",
        "no_discord_user",
        "no_roblox_user",
    }


def _externalSourceLines(report: Any) -> list[str]:
    details = [
        detail
        for detail in list(getattr(report, "externalSourceDetails", None) or [])
        if isinstance(detail, dict) and _shouldShowExternalDetail(detail)
    ]
    matches = list(getattr(report, "externalSourceMatches", None) or [])
    if not details and not matches:
        return []

    rows = [
        f"Overall status: `{getattr(report, 'externalSourceStatus', 'SKIPPED') or 'SKIPPED'}`",
    ]
    if getattr(report, "externalSourceError", None):
        rows.append(f"Note: {_truncate(getattr(report, 'externalSourceError'), 300)}")
    if details:
        for detail in details:
            if not isinstance(detail, dict):
                continue
            source = str(detail.get("source") or "External").strip()
            status = str(detail.get("status") or "SKIPPED").strip().upper()
            summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
            reason = str(summary.get("reason") or "").strip()
            sourceLine = f"{source}: `{status}`"
            if reason:
                sourceLine += f" ({reason.replace('_', ' ')})"
            elif source.lower() == "tase" and status == "OK":
                sourceLine += (
                    f" - score `{summary.get('scoreSum', 0)}`"
                    f", servers `{int(summary.get('guildCount') or 0)}`"
                )
            elif source.lower() == "moco-co" and status == "OK":
                sourceLine += f" - groups `{int(summary.get('groupCount') or 0)}`"
            if detail.get("error"):
                sourceLine += f" - {_truncate(detail.get('error'), 160)}"
            rows.append(sourceLine)
    if matches:
        rows.append("Matches:")
        rows.extend(_externalMatchLine(match) for match in matches if isinstance(match, dict))
    elif any(isinstance(detail, dict) and str(detail.get("status") or "").upper() == "OK" for detail in details):
        rows.append("No external records matched.")
    return rows


def _externalDetailForSource(report: Any, sourceName: str) -> dict[str, Any] | None:
    target = str(sourceName or "").strip().lower()
    if not target:
        return None
    for detail in list(getattr(report, "externalSourceDetails", None) or []):
        if not isinstance(detail, dict):
            continue
        if str(detail.get("source") or "").strip().lower() == target:
            return detail
    return None


def _taseOverviewLine(report: Any) -> str:
    detail = _externalDetailForSource(report, "TASE")
    if not detail:
        return ""
    status = str(detail.get("status") or "SKIPPED").strip().upper()
    summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
    reason = str(summary.get("reason") or "").strip().lower()
    taseMatches = [
        match
        for match in list(getattr(report, "externalSourceMatches", None) or [])
        if isinstance(match, dict) and str(match.get("source") or "").strip().lower() == "tase"
    ]
    if status == "OK":
        if taseMatches:
            return f"TASE checked and matched **{len(taseMatches):,}** record(s)."
        return "TASE checked: no records matched."
    if status == "ERROR":
        return "TASE check failed."
    if status == "SKIPPED":
        if reason == "missing_token":
            return "TASE was not checked because no token is configured."
        if reason == "no_discord_user":
            return "TASE was not checked because no Discord ID was provided."
        if reason == "disabled":
            return "TASE is disabled."
    if status == "PARTIAL":
        return "TASE was partially checked; expand this section for details."
    return ""


def _connectionDetailLines(report: Any) -> list[str]:
    rows = [_overviewConnectionLine(report)]
    matches = [match for match in list(getattr(report, "externalSourceMatches", None) or []) if isinstance(match, dict)]
    if not matches:
        sourceRows = _externalSourceLines(report)
        if sourceRows:
            rows.extend(sourceRows)
        return rows

    for match in matches:
        source = str(match.get("source") or "External").strip().lower()
        if source == "tase":
            scoreSum = match.get("scoreSum")
            guildCount = _safeInt(match.get("guildCount"))
            lastSeen = match.get("lastSeen") or "unknown"
            rows.append(
                f"TASE: Discord user appeared in `{guildCount}` tracked safety-risk server(s), score `{scoreSum or 0}`, last seen `{lastSeen}`."
            )
            typeNames = [
                str(value).strip()
                for value in list(match.get("typeNames") or [])[:4]
                if str(value).strip()
            ]
            if typeNames:
                rows.append(f"TASE categories: {', '.join(typeNames)}")
            for guild in list(match.get("topGuilds") or [])[:6]:
                if not isinstance(guild, dict):
                    continue
                name = str(guild.get("name") or f"Guild {guild.get('id') or '?'}").strip()
                guildScore = guild.get("score")
                guildLastSeen = guild.get("lastSeen") or "unknown"
                types = ", ".join(str(value) for value in list(guild.get("types") or [])[:2] if str(value).strip())
                suffix = f" | {types}" if types else ""
                rows.append(f"- {name}: score `{guildScore or 0}`, last seen `{guildLastSeen}`{suffix}")
        elif source == "moco-co":
            username = str(match.get("username") or getattr(report, "robloxUsername", "") or "unknown").strip()
            groupCount = _safeInt(match.get("groupCount"))
            lastSeen = match.get("lastSeen") or "unknown"
            rows.append(
                f"Moco-co: Roblox user `{username}` appeared in `{groupCount}` flagged/safety group(s), last seen `{lastSeen}`."
            )
            for group in list(match.get("topGroups") or [])[:6]:
                if not isinstance(group, dict):
                    continue
                name = str(group.get("name") or f"Group {group.get('id') or '?'}").strip()
                groupLastSeen = group.get("lastSeen") or "unknown"
                types = ", ".join(str(value) for value in list(group.get("types") or [])[:2] if str(value).strip())
                suffix = f" | {types}" if types else ""
                rows.append(f"- {name}: last seen `{groupLastSeen}`{suffix}")
        else:
            rows.append(_externalMatchLine(match))
    return rows


def _gameLine(game: dict[str, Any]) -> str:
    name = game.get("name") or "Unknown game"
    universeId = game.get("universeId") or "?"
    placeId = game.get("placeId") or "?"
    matchType = game.get("matchType")
    suffix = f" ({matchType})" if matchType else ""
    if matchType == "keyword" and game.get("keyword"):
        suffix = f" (keyword: {game.get('keyword')})"
    return f"{name} [universe {universeId}, place {placeId}]{suffix}"


def _outfitLine(outfit: dict[str, Any]) -> str:
    name = outfit.get("name") or "Unnamed outfit"
    outfitId = outfit.get("id") or "?"
    outfitType = outfit.get("outfitType")
    suffix = f" ({outfitType})" if outfitType else ""
    return f"{name} [{outfitId}]{suffix}"


def _badgeHistoryLine(badge: dict[str, Any]) -> str:
    name = badge.get("name") or "Unknown badge"
    badgeId = badge.get("id") or badge.get("badgeId") or "?"
    awarded = badge.get("awardedDate")
    awardSource = str(badge.get("awardedDateSource") or "").strip()
    created = badge.get("created")
    if awarded:
        sourceText = " via cursor" if awardSource.startswith("badge_history_") else ""
        suffix = f" (awarded {awarded}{sourceText})"
    elif created:
        suffix = f" (badge created {created})"
    else:
        suffix = ""
    return f"{name} [{badgeId}]{suffix}"


def _priorLine(row: dict[str, Any]) -> str:
    reportId = int(row.get("reportId") or 0)
    scored = bool(int(row.get("scored", 1) if row.get("scored", 1) is not None else 1))
    if scored:
        scoreText = f"{int(row.get('score') or 0)}/100 - {row.get('band') or 'Unknown'}"
    else:
        scoreText = f"Not scored - {row.get('band') or row.get('outcome') or 'Unknown'}"
    created = row.get("createdAt") or "unknown date"
    return f"#{reportId}: {scoreText} ({created})"


def _flagMatchLine(match: dict[str, Any]) -> str:
    matchType = str(match.get("type") or "match").replace("_", " ")
    value = str(match.get("value") or "?").strip() or "?"
    context = str(match.get("context") or "").strip()
    pieces = [f"{matchType}: {value}"]
    if context:
        pieces.append(f"context {context}")
    if match.get("groupName") or match.get("groupId"):
        groupName = str(match.get("groupName") or "group").strip()
        pieces.append(f"{groupName} [{match.get('groupId') or '?'}]")
    if match.get("thresholdDays") is not None:
        pieces.append(f"threshold {match.get('thresholdDays')} day(s)")
    if match.get("created"):
        pieces.append(f"created {match.get('created')}")
    return " - ".join(pieces)


def _groupSummaryLines(report: Any) -> list[str]:
    summary = getattr(report, "groupSummary", None) or {}
    if not isinstance(summary, dict) or not summary:
        return []
    rows = [
        f"Groups checked: `{int(summary.get('totalGroups') or 0)}`",
        f"Base-rank groups: `{int(summary.get('baseRankGroups') or 0)}`",
        f"Elevated-rank groups: `{int(summary.get('elevatedRankGroups') or 0)}`",
        f"Owned groups: `{int(summary.get('ownerRankGroups') or 0)}`",
        f"Named-role groups: `{int(summary.get('namedRoleGroups') or 0)}`",
    ]
    knownMemberCounts = int(summary.get("knownMemberCountGroups") or 0)
    if knownMemberCounts:
        rows.extend(
            [
                f"Known member counts: `{knownMemberCounts}`",
                f"Large groups: `{int(summary.get('largeGroups') or 0)}`",
                f"Very large groups: `{int(summary.get('veryLargeGroups') or 0)}`",
                f"Small groups: `{int(summary.get('smallGroups') or 0)}`",
                f"Largest group: `{int(summary.get('largestGroupMemberCount') or 0):,}` members",
            ]
        )
    verifiedGroups = int(summary.get("verifiedGroups") or 0)
    if verifiedGroups:
        rows.append(f"Roblox-verified groups: `{verifiedGroups}`")
    publicEntryGroups = int(summary.get("publicEntryGroups") or 0)
    lockedGroups = int(summary.get("lockedGroups") or 0)
    if publicEntryGroups or lockedGroups:
        rows.append(f"Joinability: `{publicEntryGroups}` public / `{lockedGroups}` locked")
    highestRank = int(summary.get("highestRank") or 0)
    averageRank = summary.get("averageRank")
    if highestRank or averageRank:
        rows.append(f"Rank shape: highest `{highestRank}`, average `{averageRank}`")
    return rows


def _priorSummaryLines(report: Any) -> list[str]:
    summary = getattr(report, "priorReportSummary", None) or {}
    if not isinstance(summary, dict) or not summary:
        return ["No prior Jane records found for this target."]
    rows = [
        f"Recent intel reports: `{int(summary.get('totalRecent') or 0)}`",
        f"Recent high-risk intel reports: `{int(summary.get('highRiskRecent') or 0)}`",
        f"Recent no-score intel reports: `{int(summary.get('noScoreRecent') or 0)}`",
        f"Queue approvals: `{int(summary.get('queueApprovals') or 0)}`",
        f"Queue rejections: `{int(summary.get('queueRejections') or 0)}`",
    ]
    lastBand = summary.get("lastBand")
    lastScore = summary.get("lastScore")
    if lastBand:
        rows.append(f"Last intel result: `{lastScore}/100 - {lastBand}`")
    return rows


def _badgeTimelineLines(report: Any) -> list[str]:
    summary = getattr(report, "badgeTimelineSummary", None) or {}
    if not isinstance(summary, dict) or not summary:
        return []
    rows = [
        f"History complete: `{'yes' if summary.get('historyComplete', True) else 'no'}`",
        f"Timeline quality: `{summary.get('quality') or 'unknown'}`",
        f"Award-date status: `{summary.get('awardDateStatus') or 'SKIPPED'}`",
        f"Dated awards: `{int(summary.get('datedBadges') or 0)}/{int(summary.get('sampleSize') or 0)}`",
    ]
    if summary.get("historyNextCursor"):
        rows.append("Badge history hit the configured hard page limit before Roblox stopped returning pages.")
    sourceCounts = summary.get("awardDateSources")
    if isinstance(sourceCounts, dict) and sourceCounts:
        formattedSources = []
        sourceLabels = {
            "awarded_dates_endpoint": "Roblox award-date endpoint",
            "user_badges_endpoint": "Roblox badge list",
            "badge_history_next_cursor": "badge-page cursor",
            "badge_history_previous_cursor": "badge-page cursor",
        }
        combinedSources: dict[str, int] = {}
        for source, count in sourceCounts.items():
            label = sourceLabels.get(str(source), str(source).replace("_", " "))
            combinedSources[label] = combinedSources.get(label, 0) + _safeInt(count)
        for label, count in sorted(combinedSources.items()):
            formattedSources.append(f"{label} `{count:,}`")
        rows.append(f"Date sources: {', '.join(formattedSources)}")
    spanDays = int(summary.get("spanDays") or 0)
    distinctYears = int(summary.get("distinctAwardYears") or 0)
    if spanDays or distinctYears:
        rows.append(f"Award span: `{spanDays}` days across `{distinctYears}` year(s)")
    oldest = summary.get("oldestAwardedAt")
    newest = summary.get("newestAwardedAt")
    if oldest or newest:
        rows.append(f"Oldest/newest award: `{oldest or 'unknown'}` / `{newest or 'unknown'}`")
    maxSameDay = int(summary.get("maxSameDayAwards") or 0)
    maxRatio = summary.get("maxSameDayRatio")
    if maxSameDay:
        rows.append(f"Largest same-day burst: `{maxSameDay}` award(s), ratio `{maxRatio}`")
    error = str(summary.get("awardDateError") or "").strip()
    if error:
        rows.append(f"Timeline note: {_truncate(error, 250)}")
    return rows


def _statusSummary(status: str, *, skippedLabel: str = "skipped") -> str:
    normalized = str(status or "SKIPPED").strip().upper()
    if normalized == "OK":
        return "OK"
    if normalized == "PRIVATE":
        return "PARTIAL"
    if normalized in {"SKIPPED", ""}:
        return skippedLabel
    if normalized == "NO_ROVER":
        return "missing identity"
    return "needs review"


def _completenessLines(report: Any, reviewBucket: str) -> list[str]:
    robloxUserId = getattr(report, "robloxUserId", None)
    ageKnown = getattr(report, "robloxAgeDays", None) is not None or bool(getattr(report, "robloxCreated", None))
    isAdultRoute = reviewBucket == bgBuckets.adultBgReviewBucket
    adultSkipped = "not checked"
    rows = [
        f"Identity: `{ 'OK' if robloxUserId else 'missing' }`",
        f"Profile age: `{ 'OK' if ageKnown else 'unknown' }`",
        f"Username history: `{_statusSummary(getattr(report, 'usernameHistoryScanStatus', 'SKIPPED'))}`",
        f"Alt/identity check: `{_statusSummary(getattr(report, 'altScanStatus', 'SKIPPED'))}`",
        f"Connections: `{_statusSummary(getattr(report, 'connectionScanStatus', 'SKIPPED'))}`",
        f"Friend sample: `{_statusSummary(getattr(report, 'friendIdsScanStatus', 'SKIPPED'))}`",
    ]
    if isAdultRoute:
        rows.extend(
            [
                f"Groups: `{_statusSummary(getattr(report, 'groupScanStatus', 'SKIPPED'))}`",
                f"Inventory: `{_statusSummary(getattr(report, 'inventoryScanStatus', 'SKIPPED'))}`",
                f"Gamepasses: `{_statusSummary(getattr(report, 'gamepassScanStatus', 'SKIPPED'))}`",
                f"Favorite games: `{_statusSummary(getattr(report, 'favoriteGameScanStatus', 'SKIPPED'))}`",
                f"Outfits: `{_statusSummary(getattr(report, 'outfitScanStatus', 'SKIPPED'))}`",
            ]
        )
    else:
        rows.extend(
            [
                f"Groups: `{adultSkipped}`",
                f"Inventory: `{adultSkipped}`",
                f"Gamepasses: `{adultSkipped}`",
                f"Favorite games: `{adultSkipped}`",
                f"Outfits: `{adultSkipped}`",
            ]
        )
    rows.append(f"Badges: `{_statusSummary(getattr(report, 'badgeScanStatus', 'SKIPPED'))}`")
    rows.append(f"Badge sample: `{_statusSummary(getattr(report, 'badgeHistoryScanStatus', 'SKIPPED'))}`")
    rows.append(f"External sources: `{_statusSummary(getattr(report, 'externalSourceStatus', 'SKIPPED'))}`")
    prior = getattr(report, "priorReportSummary", None) or {}
    priorCount = int(prior.get("totalRecent") or 0) if isinstance(prior, dict) else 0
    rows.append(f"Prior Jane records: `{priorCount}`")
    return rows


def _scanCoverageLines(report: Any, reviewBucket: str) -> list[str]:
    rows: list[str] = []
    rows.extend(_completenessLines(report, reviewBucket))
    return rows


def _statusWithNote(status: Any, error: Any = None) -> str:
    normalized = _scanStatus(status)
    note = str(error or "").strip()
    if note:
        return f"`{normalized}` - {_truncate(note, 180)}"
    return f"`{normalized}`"


def _decisionReadinessLines(report: Any, score: scoring.RiskScore) -> list[str]:
    outcome = str(getattr(score, "outcome", "") or "").strip().lower()
    robloxUserId = getattr(report, "robloxUserId", None)
    identityMissing = not bool(robloxUserId)
    confidence = int(getattr(score, "confidence", 0) or 0)

    gapLines: list[str] = []
    if identityMissing:
        gapLines.append("Roblox identity is missing.")
    inventoryStatus = _scanStatus(getattr(report, "inventoryScanStatus", "SKIPPED"))
    if inventoryStatus == "PRIVATE":
        gapLines.append("Inventory is private or hidden.")
    elif inventoryStatus == "ERROR":
        gapLines.append("Inventory scan failed.")
    for label, status in (
        ("Username history", getattr(report, "usernameHistoryScanStatus", "SKIPPED")),
        ("Alt/identity check", getattr(report, "altScanStatus", "SKIPPED")),
        ("Friend sample", getattr(report, "friendIdsScanStatus", "SKIPPED")),
        ("Groups", getattr(report, "groupScanStatus", "SKIPPED")),
        ("Favorites", getattr(report, "favoriteGameScanStatus", "SKIPPED")),
        ("Badges", getattr(report, "badgeScanStatus", "SKIPPED")),
        ("Badge history", getattr(report, "badgeHistoryScanStatus", "SKIPPED")),
        ("External sources", getattr(report, "externalSourceStatus", "SKIPPED")),
    ):
        normalized = _scanStatus(status)
        if normalized == "ERROR":
            gapLines.append(f"{label} failed.")
        elif normalized == "PARTIAL":
            gapLines.append(f"{label} was partial.")

    inventorySummary = getattr(report, "inventorySummary", None) or {}
    if isinstance(inventorySummary, dict) and inventorySummary and not bool(inventorySummary.get("complete", True)):
        gapLines.append("Inventory hit the configured page cap.")
    gamepassSummary = getattr(report, "gamepassSummary", None) or {}
    if isinstance(gamepassSummary, dict) and gamepassSummary and not bool(gamepassSummary.get("complete", True)):
        gapLines.append("Gamepass scan hit the configured page cap.")
    badgeSummary = getattr(report, "badgeTimelineSummary", None) or {}
    if isinstance(badgeSummary, dict) and badgeSummary.get("historyNextCursor"):
        gapLines.append("Badge history hit the configured page cap.")

    if not score.scored:
        if outcome == "needs_identity":
            status = "Identity Review Needed"
            nextStep = "Resolve the Roblox identity, then rerun `/bg-intel`."
        else:
            status = "Blocked"
            nextStep = "Rerun later or review manually; too many major sources failed."
    elif outcome == "discord_external_only" or identityMissing:
        status = "Partial"
        nextStep = "Use this only as Discord-side context until a Roblox identity is confirmed."
    elif gapLines or confidence < 50:
        status = "Partial"
        nextStep = "Review the gaps before making a final call; rerun if the missing source matters."
    elif int(score.score or 0) >= 80:
        status = "Ready"
        nextStep = "Escalate to the configured review path with the direct/high-risk evidence attached."
    elif int(score.score or 0) >= 40:
        status = "Ready"
        nextStep = "Manual review is warranted; compare the caution and reassuring signals."
    else:
        status = "Ready"
        nextStep = "Review the source coverage and proceed with the normal background-check decision."

    rows = [
        f"Status: **{status}**",
        f"Next step: {nextStep}",
    ]
    if gapLines:
        rows.append("Important gaps: " + "; ".join(gapLines[:5]))
        if len(gapLines) > 5:
            rows.append(f"... and {len(gapLines) - 5} more gap(s)")
    else:
        rows.append("Important gaps: none detected.")
    return rows


def _sourceProvenanceLines(report: Any) -> list[str]:
    rows = [
        f"Identity source: `{getattr(report, 'identitySource', None) or 'unknown'}`",
        f"Profile age: `{ 'OK' if getattr(report, 'robloxAgeDays', None) is not None or getattr(report, 'robloxCreated', None) else 'unknown' }`",
        f"Username history: {_statusWithNote(getattr(report, 'usernameHistoryScanStatus', 'SKIPPED'), getattr(report, 'usernameHistoryScanError', None))}",
        f"Alt/identity check: {_statusWithNote(getattr(report, 'altScanStatus', 'SKIPPED'), getattr(report, 'altScanError', None))}",
        f"Connections: {_statusWithNote(getattr(report, 'connectionScanStatus', 'SKIPPED'), getattr(report, 'connectionScanError', None))}",
        f"Friend sample: {_statusWithNote(getattr(report, 'friendIdsScanStatus', 'SKIPPED'), getattr(report, 'friendIdsScanError', None))}",
        f"Groups: {_statusWithNote(getattr(report, 'groupScanStatus', 'SKIPPED'), getattr(report, 'groupScanError', None))}",
    ]
    if getattr(report, "roverError", None):
        rows.append(f"Identity note: {_truncate(getattr(report, 'roverError'), 220)}")

    inventorySummary = getattr(report, "inventorySummary", None) or {}
    inventoryStatus = getattr(report, "inventoryScanStatus", "SKIPPED")
    rows.append(f"Inventory: {_statusWithNote(inventoryStatus, getattr(report, 'inventoryScanError', None))}")
    if isinstance(inventorySummary, dict) and inventorySummary:
        rows.append(
            "Inventory source: "
            f"`{inventorySummary.get('valueSource') or 'Roblox inventory'}`; "
            f"pages `{_safeInt(inventorySummary.get('pagesScanned'))}`; "
            f"complete `{'yes' if inventorySummary.get('complete', True) else 'no'}`"
        )
        rows.append(
            "Visual matching: "
            f"refs `{_safeInt(inventorySummary.get('visualReferenceCount'))}`, "
            f"candidates `{_safeInt(inventorySummary.get('visualCandidateCount'))}`, "
            f"hits `{_safeInt(inventorySummary.get('visualMatchedCount'))}`"
        )
        if inventorySummary.get("visualError"):
            rows.append(f"Visual note: {_truncate(inventorySummary.get('visualError'), 180)}")

    gamepassSummary = getattr(report, "gamepassSummary", None) or {}
    rows.append(f"Gamepasses: {_statusWithNote(getattr(report, 'gamepassScanStatus', 'SKIPPED'), getattr(report, 'gamepassScanError', None))}")
    if isinstance(gamepassSummary, dict) and gamepassSummary:
        rows.append(
            "Gamepass source: "
            f"`{gamepassSummary.get('valueSource') or 'Roblox gamepass inventory'}`; "
            f"complete `{'yes' if gamepassSummary.get('complete', True) else 'no'}`"
        )

    rows.extend(
        [
            f"Favorite games: {_statusWithNote(getattr(report, 'favoriteGameScanStatus', 'SKIPPED'), getattr(report, 'favoriteGameScanError', None))}",
            f"Outfits: {_statusWithNote(getattr(report, 'outfitScanStatus', 'SKIPPED'), getattr(report, 'outfitScanError', None))}",
            f"Badge flags: {_statusWithNote(getattr(report, 'badgeScanStatus', 'SKIPPED'), getattr(report, 'badgeScanError', None))}",
            f"Badge history: {_statusWithNote(getattr(report, 'badgeHistoryScanStatus', 'SKIPPED'), getattr(report, 'badgeHistoryScanError', None))}",
        ]
    )
    badgeSummary = getattr(report, "badgeTimelineSummary", None) or {}
    if isinstance(badgeSummary, dict) and badgeSummary:
        rows.append(
            "Badge timeline: "
            f"award dates `{badgeSummary.get('awardDateStatus') or 'SKIPPED'}`, "
            f"complete `{'yes' if badgeSummary.get('historyComplete', True) else 'no'}`, "
            f"dated `{_safeInt(badgeSummary.get('datedBadges'))}/{_safeInt(badgeSummary.get('sampleSize'))}`"
        )
    rows.append(f"External sources: {_statusWithNote(getattr(report, 'externalSourceStatus', 'SKIPPED'), getattr(report, 'externalSourceError', None))}")
    for detail in list(getattr(report, "externalSourceDetails", None) or []):
        if not isinstance(detail, dict) or not _shouldShowExternalDetail(detail):
            continue
        source = str(detail.get("source") or "External").strip()
        status = str(detail.get("status") or "SKIPPED").strip().upper()
        summary = detail.get("summary") if isinstance(detail.get("summary"), dict) else {}
        reason = str(summary.get("reason") or "").replace("_", " ").strip()
        suffix = f" ({reason})" if reason else ""
        rows.append(f"{source}: `{status}`{suffix}")
    return rows


def _overviewProfileLine(report: Any) -> str:
    robloxUserId = getattr(report, "robloxUserId", None)
    userIdText = str(int(robloxUserId)) if robloxUserId else "unknown"
    joinDate = _discordDateWithRelative(getattr(report, "robloxCreated", None))
    return f"User ID: `{userIdText}` | Join Date: {joinDate}"


def _profileDetailLines(report: Any) -> list[str]:
    robloxUserId = getattr(report, "robloxUserId", None)
    userIdText = str(int(robloxUserId)) if robloxUserId else "unknown"
    rows = [
        f"User ID: `{userIdText}`",
        f"Roblox username: `{getattr(report, 'robloxUsername', None) or 'unknown'}`",
        f"Join date: {_discordDateWithRelative(getattr(report, 'robloxCreated', None))}",
        f"Account age: `{_formatAge(getattr(report, 'robloxAgeDays', None))}`",
        f"Identity source: `{getattr(report, 'identitySource', None) or 'unknown'}`",
        f"Username history: `{getattr(report, 'usernameHistoryScanStatus', 'SKIPPED') or 'SKIPPED'}`",
        f"Alt/identity check: `{getattr(report, 'altScanStatus', 'SKIPPED') or 'SKIPPED'}`",
        f"Friend sample: `{getattr(report, 'friendIdsScanStatus', 'SKIPPED') or 'SKIPPED'}` ({len(list(getattr(report, 'friendUserIds', None) or []))} ID(s))",
    ]
    previousUsernames = [
        str(value).strip()
        for value in list(getattr(report, "previousRobloxUsernames", None) or [])
        if str(value).strip()
    ]
    if previousUsernames:
        rows.append("Previous usernames: " + ", ".join(f"`{name}`" for name in previousUsernames[:12]))
        if len(previousUsernames) > 12:
            rows.append(f"... and {len(previousUsernames) - 12} more previous name(s)")
    if getattr(report, "usernameHistoryScanError", None):
        rows.append(f"Username history note: {_truncate(getattr(report, 'usernameHistoryScanError'), 220)}")
    if getattr(report, "altScanError", None):
        rows.append(f"Alt/identity note: {_truncate(getattr(report, 'altScanError'), 220)}")
    altMatches = [match for match in list(getattr(report, "altMatches", None) or []) if isinstance(match, dict)]
    if altMatches:
        rows.append("Alt/identity evidence:")
        rows.extend(_altMatchLine(match) for match in altMatches[:8])
    if getattr(report, "roverError", None):
        rows.append(f"RoVer note: {_truncate(getattr(report, 'roverError'), 220)}")
    profileUrl = _robloxProfileUrl(report)
    if profileUrl:
        rows.append(f"Roblox Profile: {profileUrl}")
    return rows


def _overviewConnectionLine(report: Any) -> str:
    summary = getattr(report, "connectionSummary", None) or {}
    if isinstance(summary, dict) and summary:
        friends = summary.get("friends")
        followers = summary.get("followers")
        following = summary.get("following")
        if any(value is not None for value in (friends, followers, following)):
            return (
                f"User has **{_safeInt(friends):,}** friend(s) and **{_safeInt(followers):,}** follower(s) "
                f"while following **{_safeInt(following):,}** account(s)."
            )
    matches = list(getattr(report, "externalSourceMatches", None) or [])
    if matches:
        return f"External connection sources found **{len(matches):,}** matched record(s)."
    status = _scanStatus(getattr(report, "externalSourceStatus", "SKIPPED"))
    if status in {"OK", "PARTIAL"}:
        return "No external connection records matched."
    return _scanSummary(getattr(report, "connectionScanStatus", "SKIPPED"), getattr(report, "connectionScanError", None))


def _overviewGroupLine(report: Any) -> str:
    summary = getattr(report, "groupSummary", None) or {}
    if not isinstance(summary, dict) or not summary:
        return _scanSummary(getattr(report, "groupScanStatus", "SKIPPED"), getattr(report, "groupScanError", None))
    total = _safeInt(summary.get("totalGroups"))
    baseRank = _safeInt(summary.get("baseRankGroups"))
    baseRatio = float(summary.get("baseRankRatio") or ((baseRank / total) if total else 0.0))
    return (
        f"User is in **{total:,}** group(s) while base rank in "
        f"**{baseRank:,}** group(s) (**{_formatPercent(baseRatio)}**)."
    )


def _overviewInventoryLine(report: Any) -> str:
    status = _scanStatus(getattr(report, "inventoryScanStatus", "SKIPPED"))
    summary = getattr(report, "inventorySummary", None) or {}
    if status == "OK":
        if isinstance(summary, dict) and summary:
            uniqueAssets = _safeInt(summary.get("uniqueAssetCount"))
            line = (
                f"Inventory was visible with **{uniqueAssets:,}** unique non-gamepass asset(s) "
                f"valued at **{_formatRobux(summary.get('knownValueRobux'))}**."
            )
            flaggedCount = _safeInt(summary.get("flaggedItemCount"))
            if flaggedCount > 0:
                line += f" Suspicious item hits: **{flaggedCount:,}**."
            return line
        return "Inventory was visible."
    text = _scanSummary(status, getattr(report, "inventoryScanError", None))
    dmSent = getattr(report, "privateInventoryDmSent", None)
    if dmSent is not None:
        text += "\nPrivate inventory DM: " + ("sent" if dmSent else "not sent")
    return text


def _inventoryDetailLines(report: Any) -> list[str]:
    status = _scanStatus(getattr(report, "inventoryScanStatus", "SKIPPED"))
    summary = getattr(report, "inventorySummary", None) or {}
    flaggedItems = list(getattr(report, "flaggedItems", None) or [])
    if status != "OK" or not isinstance(summary, dict) or not summary:
        rows = [_overviewInventoryLine(report)]
        if flaggedItems:
            rows.append("Configured item flags:")
            rows.extend(_itemLine(item) for item in flaggedItems[:10] if isinstance(item, dict))
        return rows
    rows = [
        f"Items scanned: `{_safeInt(summary.get('itemsScanned')):,}` across `{_safeInt(summary.get('pagesScanned')):,}` page(s)",
        f"Unique non-gamepass assets: `{_safeInt(summary.get('uniqueAssetCount')):,}`",
        f"Excluded gamepasses: `{_safeInt(summary.get('uniqueGamepassCount')):,}`",
        f"Known current asset value: **{_formatRobux(summary.get('knownValueRobux'))}**",
        f"Priced assets: `{_safeInt(summary.get('pricedAssetCount')):,}`; unpriced/off-sale assets: `{_safeInt(summary.get('unpricedAssetCount')):,}`",
        f"Complete inventory scan: `{'yes' if summary.get('complete', True) else 'no'}`",
    ]
    if summary.get("priceError"):
        rows.append(f"Value note: {_truncate(summary.get('priceError'), 220)}")
    selfCreatedAssetCount = _safeInt(summary.get("selfCreatedAssetCount"))
    if selfCreatedAssetCount > 0:
        rows.append(
            "Self-created assets excluded from value: "
            f"`{selfCreatedAssetCount:,}` "
            f"({_formatRobux(summary.get('selfCreatedRobuxExcluded'))})"
        )
    rows.append("Gamepasses are excluded from this inventory value and counted in the Gamepasses section.")
    if flaggedItems:
        rows.append(f"Suspicious item hits: `{_safeInt(summary.get('flaggedItemCount'), len(flaggedItems)):,}`")
        rows.append(
            "Exact item IDs: "
            f"`{_safeInt(summary.get('exactItemMatchCount')):,}`; "
            "flagged creators: "
            f"`{_safeInt(summary.get('creatorMatchCount')):,}`"
        )
        rows.append(
            "Visual thumbnail matches: "
            f"`{_safeInt(summary.get('visualMatchedCount')):,}` "
            f"(candidates `{_safeInt(summary.get('visualCandidateCount')):,}`, refs `{_safeInt(summary.get('visualReferenceCount')):,}`)"
        )
        rows.append(
            "Keyword hits: "
            f"`{_safeInt(summary.get('keywordMatchCount')):,}` exact / "
            f"`{_safeInt(summary.get('normalizedKeywordMatchCount')):,}` normalized / "
            f"`{_safeInt(summary.get('fuzzyKeywordMatchCount')):,}` fuzzy"
        )
        if summary.get("visualError"):
            rows.append(f"Visual note: {_truncate(summary.get('visualError'), 220)}")
        rows.append(
            "Suspicious creators represented: "
            f"`{_safeInt(summary.get('suspiciousCreatorCount')):,}`; "
            "multi-signal items: "
            f"`{_safeInt(summary.get('multiSignalMatchCount')):,}`"
        )
        rows.extend(_itemLine(item) for item in flaggedItems[:10] if isinstance(item, dict))
    else:
        rows.append("Suspicious item hits: `0`")
    return rows


def _overviewGamepassLine(report: Any) -> str:
    status = _scanStatus(getattr(report, "gamepassScanStatus", "SKIPPED"))
    summary = getattr(report, "gamepassSummary", None)
    if status == "OK" and isinstance(summary, dict) and summary:
        total = _safeInt(summary.get("totalGamepasses"))
        return (
            f"User has **{total:,}** gamepass(es) in the visible inventory "
            f"valued at **{_formatRobux(summary.get('totalRobux'))}**."
        )
    return _scanSummary(status, getattr(report, "gamepassScanError", None))


def _gamepassLine(gamepass: dict[str, Any]) -> str:
    name = gamepass.get("name") or "Unknown gamepass"
    gamepassId = gamepass.get("id") or "?"
    price = gamepass.get("price")
    priceText = _formatRobux(price) if price is not None else "unpriced"
    return f"{name} [{gamepassId}] - {priceText}"


def _gamepassDetailLines(report: Any) -> list[str]:
    status = _scanStatus(getattr(report, "gamepassScanStatus", "SKIPPED"))
    summary = getattr(report, "gamepassSummary", None) or {}
    gamepasses = [row for row in list(getattr(report, "ownedGamepasses", None) or []) if isinstance(row, dict)]
    if status != "OK" or not isinstance(summary, dict) or not summary:
        return [_overviewGamepassLine(report)]
    rows = [
        f"Gamepasses found: `{_safeInt(summary.get('totalGamepasses')):,}` across `{_safeInt(summary.get('pagesScanned')):,}` page(s)",
        f"Total known gamepass value: **{_formatRobux(summary.get('totalRobux'))}**",
        f"Priced gamepasses: `{_safeInt(summary.get('pricedGamepasses')):,}`; unpriced/off-sale gamepasses: `{_safeInt(summary.get('unpricedGamepasses')):,}`",
        f"Complete gamepass scan: `{'yes' if summary.get('complete', True) else 'no'}`",
    ]
    if summary.get("priceError"):
        rows.append(f"Value note: {_truncate(summary.get('priceError'), 220)}")
    selfCreatedGamepassCount = _safeInt(summary.get("selfCreatedGamepassCount"))
    if selfCreatedGamepassCount > 0:
        rows.append(
            "Self-created gamepasses excluded from value: "
            f"`{selfCreatedGamepassCount:,}` "
            f"({_formatRobux(summary.get('selfCreatedRobuxExcluded'))})"
        )
    if gamepasses:
        rows.append("Highest visible values:")
        rows.extend(
            _gamepassLine(gamepass)
            for gamepass in sorted(
                gamepasses,
                key=lambda row: _safeInt(row.get("price"), -1),
                reverse=True,
            )[:10]
        )
    return rows


def _favoriteGameDetailLines(report: Any) -> list[str]:
    games = [game for game in list(getattr(report, "favoriteGames", None) or []) if isinstance(game, dict)]
    flaggedGames = [game for game in list(getattr(report, "flaggedFavoriteGames", None) or []) if isinstance(game, dict)]
    rows = [
        f"Status: `{getattr(report, 'favoriteGameScanStatus', 'SKIPPED') or 'SKIPPED'}`",
        f"Error: `{getattr(report, 'favoriteGameScanError', None) or 'none'}`",
        f"Favorite games checked: `{len(games)}`",
        f"Configured flags matched: `{len(flaggedGames)}`",
    ]
    if flaggedGames:
        rows.append("Flagged favorite games:")
        rows.extend(_gameLine(game) for game in flaggedGames[:15])
    if games:
        rows.append("Favorite game sample:")
        rows.extend(_gameLine(game) for game in games[:15])
    return rows


def _overviewFavoritesLine(report: Any) -> str:
    status = _scanStatus(getattr(report, "favoriteGameScanStatus", "SKIPPED"))
    favorites = list(getattr(report, "favoriteGames", None) or [])
    flagged = list(getattr(report, "flaggedFavoriteGames", None) or [])
    if status == "OK":
        return f"Jane sampled **{len(favorites):,}** favorite game(s); configured flags: **{len(flagged):,}**."
    return _scanSummary(status, getattr(report, "favoriteGameScanError", None))


def _overviewSafetyRecordLine(report: Any) -> str:
    matches = list(getattr(report, "externalSourceMatches", None) or [])
    flaggedGroups = list(getattr(report, "flaggedGroups", None) or [])
    totalRecords = len(matches) + len(flaggedGroups)
    taseLine = _taseOverviewLine(report)
    if totalRecords:
        suffix = f" {taseLine}" if taseLine else ""
        return f"Safety-related records found: **{totalRecords:,}**.{suffix}"
    if matches:
        return f"External safety sources found **{len(matches):,}** matched record(s)."
    externalStatus = _scanStatus(getattr(report, "externalSourceStatus", "SKIPPED"))
    if externalStatus in {"OK", "PARTIAL"}:
        suffix = f" {taseLine}" if taseLine else ""
        return f"No safety records found.{suffix}"
    if externalStatus == "ERROR":
        suffix = f" {taseLine}" if taseLine else ""
        return f"Safety record sources could not be checked.{suffix}"
    if taseLine:
        return taseLine
    return "No safety records found."


def _overviewTaseRecordLine(report: Any) -> str:
    taseMatches = [
        match
        for match in list(getattr(report, "externalSourceMatches", None) or [])
        if isinstance(match, dict) and str(match.get("source") or "").strip().lower() == "tase"
    ]
    if taseMatches:
        return f"TASE records found: **{len(taseMatches):,}**."
    detail = _externalDetailForSource(report, "TASE")
    status = str((detail or {}).get("status") or "SKIPPED").strip().upper()
    summary = (detail or {}).get("summary") if isinstance((detail or {}).get("summary"), dict) else {}
    reason = str(summary.get("reason") or "").strip().lower()
    if status == "ERROR":
        return "TASE records could not be checked."
    if status == "OK":
        return "No TASE records found."
    if reason == "missing_token":
        return "TASE records were not checked because no token is configured."
    if reason == "no_discord_user":
        return "TASE records were not checked because no Discord ID was provided."
    if reason == "disabled":
        return "TASE records are disabled."
    return "TASE records were not checked."


def _overviewBadgeLine(report: Any) -> str:
    status = _scanStatus(getattr(report, "badgeHistoryScanStatus", "SKIPPED"))
    summary = getattr(report, "badgeTimelineSummary", None) or {}
    flaggedBadges = list(getattr(report, "flaggedBadges", None) or [])
    if isinstance(summary, dict) and summary:
        sampleSize = _safeInt(summary.get("sampleSize"))
        dated = _safeInt(summary.get("datedBadges"))
        completeText = "complete public history" if summary.get("historyComplete", True) else "partial public history"
        awardStatus = _scanStatus(summary.get("awardDateStatus"))
        if dated > 0:
            if awardStatus == "PARTIAL":
                return f"User has **{sampleSize:,}** badge(s) in Jane's {completeText}; **{dated:,}** dated award(s) from a partial timeline."
            return f"User has **{sampleSize:,}** badge(s) in Jane's {completeText}; **{dated:,}** dated award(s)."
        if awardStatus == "ERROR":
            return f"User has **{sampleSize:,}** badge(s) in Jane's {completeText}; Roblox award dates are currently unavailable."
        if awardStatus == "SKIPPED":
            return f"User has **{sampleSize:,}** badge(s) in Jane's {completeText}; award dates were not checked."
        return f"User has **{sampleSize:,}** badge(s) in Jane's {completeText}; Roblox returned no dated awards."
    if status == "OK":
        sample = list(getattr(report, "badgeHistorySample", None) or [])
        return f"User has **{len(sample):,}** badge(s) in Jane's public badge history."
    return _scanSummary(status, getattr(report, "badgeHistoryScanError", None))


def _overviewOutfitLine(report: Any) -> str:
    status = _scanStatus(getattr(report, "outfitScanStatus", "SKIPPED"))
    outfits = list(getattr(report, "outfits", None) or [])
    if status == "OK":
        return f"Jane sampled **{len(outfits):,}** outfit(s)."
    return _scanSummary(status, getattr(report, "outfitScanError", None))


def _outfitDetailLines(report: Any) -> list[str]:
    outfits = [outfit for outfit in list(getattr(report, "outfits", None) or []) if isinstance(outfit, dict)]
    rows = [
        f"Status: `{getattr(report, 'outfitScanStatus', 'SKIPPED') or 'SKIPPED'}`",
        f"Error: `{getattr(report, 'outfitScanError', None) or 'none'}`",
        f"Outfits checked: `{len(outfits)}`",
    ]
    if outfits:
        rows.append("Outfit sample:")
        rows.extend(_outfitLine(outfit) for outfit in outfits[:20])
    return rows


def _overviewPriorLine(report: Any) -> str:
    summary = getattr(report, "priorReportSummary", None) or {}
    if not isinstance(summary, dict) or not summary:
        return "No prior Jane records found for this target."
    total = _safeInt(summary.get("totalRecent"))
    highRisk = _safeInt(summary.get("highRiskRecent"))
    noScore = _safeInt(summary.get("noScoreRecent"))
    approvals = _safeInt(summary.get("queueApprovals"))
    rejections = _safeInt(summary.get("queueRejections"))
    lines = [
        f"Recent intel reports: **{total:,}**",
        f"Recent high-risk reports: **{highRisk:,}**",
        f"No-score reports: **{noScore:,}**",
        f"Queue history: **{approvals:,}** approval(s), **{rejections:,}** rejection(s).",
    ]
    lastBand = str(summary.get("lastBand") or "").strip()
    lastScore = summary.get("lastScore")
    if lastBand:
        lines.append(f"Last Jane result: **{lastScore}/100 - {lastBand}**")
    return "\n".join(lines)


def _priorDetailLines(report: Any) -> list[str]:
    summary = getattr(report, "priorReportSummary", None) or {}
    if not isinstance(summary, dict) or not summary:
        return ["No prior Jane records found for this target."]
    rows = _priorSummaryLines(report)
    priorRows = [row for row in list(summary.get("rows") or []) if isinstance(row, dict)]
    if priorRows:
        rows.append("Recent report details:")
        rows.extend(_priorLine(row) for row in priorRows[:8])
    return rows


def _recordDetailLines(report: Any) -> list[str]:
    rows: list[str] = []
    flaggedGroups = [group for group in list(getattr(report, "flaggedGroups", None) or []) if isinstance(group, dict)]
    if flaggedGroups:
        rows.append("Configured Roblox flagged group record(s):")
        rows.extend(_groupLine(group) for group in flaggedGroups[:12])
    matches = [match for match in list(getattr(report, "externalSourceMatches", None) or []) if isinstance(match, dict)]
    for match in matches:
        if str(match.get("source") or "").strip().lower() == "moco-co":
            groupCount = _safeInt(match.get("groupCount"))
            rows.append(f"Moco-co Roblox safety group record(s): `{groupCount}`")
            for group in list(match.get("topGroups") or [])[:8]:
                if not isinstance(group, dict):
                    continue
                name = str(group.get("name") or f"Group {group.get('id') or '?'}").strip()
                groupLastSeen = group.get("lastSeen") or "unknown"
                types = ", ".join(str(value) for value in list(group.get("types") or [])[:2] if str(value).strip())
                suffix = f" | {types}" if types else ""
                rows.append(f"- {name}: last seen `{groupLastSeen}`{suffix}")
        elif str(match.get("source") or "").strip().lower() == "tase":
            rows.append(_externalMatchLine(match))
    if not rows:
        rows = _externalSourceLines(report)
    if not rows:
        rows = [_overviewSafetyRecordLine(report)]
    return rows


def _badgeAwardDates(report: Any) -> list[datetime]:
    awardedDates: list[datetime] = []
    for badge in list(getattr(report, "badgeHistorySample", None) or []):
        if not isinstance(badge, dict):
            continue
        awardedAt = _parseDate(badge.get("awardedDate"))
        if awardedAt is not None:
            awardedDates.append(awardedAt.astimezone(timezone.utc))
    awardedDates.sort()
    return awardedDates


def _graphFont(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    fontNames = ["arialbd.ttf" if bold else "arial.ttf", "segoeui.ttf"]
    for fontName in fontNames:
        try:
            return ImageFont.truetype(fontName, int(size))
        except OSError:
            continue
    return ImageFont.load_default()


def buildBadgeTimelineGraphFile(
    report: Any,
    *,
    filename: str = _BADGE_GRAPH_FILENAME,
) -> discord.File | None:
    awardedDates = _badgeAwardDates(report)
    if not awardedDates:
        return None

    dayCounts = Counter(awardedAt.date() for awardedAt in awardedDates)
    orderedDays = sorted(dayCounts)
    total = 0
    points: list[tuple[Any, int]] = []
    for day in orderedDays:
        total += int(dayCounts[day])
        points.append((day, total))
    if not points:
        return None

    width, height = 900, 360
    plotLeft, plotTop, plotRight, plotBottom = 78, 72, 860, 298
    graphBackground = (5, 6, 8)
    plotBackground = (8, 10, 13)
    gridColor = (38, 42, 48)
    axisColor = (92, 98, 108)
    textColor = (235, 238, 242)
    mutedTextColor = (164, 170, 180)
    redColor = (237, 28, 36)
    lineColor = (244, 244, 244)

    image = Image.new("RGB", (width, height), graphBackground)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.rectangle((plotLeft, plotTop, plotRight, plotBottom), fill=plotBackground)

    titleFont = _graphFont(30, bold=True)
    labelFont = _graphFont(14)
    tinyFont = _graphFont(12)

    displayName = _displayName(report)
    oldest = awardedDates[0]
    newest = awardedDates[-1]
    title = f"{displayName} - Badge Timeline"
    titleWidth = draw.textlength(title, font=titleFont)
    draw.text(((width - titleWidth) / 2, 18), title, fill=textColor, font=titleFont)
    subtitle = (
        f"{len(awardedDates):,} dated badge award(s), "
        f"{_shortDate(oldest.isoformat())} to {_shortDate(newest.isoformat())}"
    )
    subtitleWidth = draw.textlength(subtitle, font=labelFont)
    draw.text(
        ((width - subtitleWidth) / 2, 54),
        subtitle,
        fill=mutedTextColor,
        font=labelFont,
    )

    minOrdinal = points[0][0].toordinal()
    maxOrdinal = points[-1][0].toordinal()
    if minOrdinal == maxOrdinal:
        minOrdinal -= 1
        maxOrdinal += 1
    maxCount = max(1, points[-1][1])

    def _x(day: Any) -> float:
        ratio = (day.toordinal() - minOrdinal) / max(1, maxOrdinal - minOrdinal)
        return plotLeft + ratio * (plotRight - plotLeft)

    def _y(count: int) -> float:
        ratio = int(count) / max(1, maxCount)
        return plotBottom - ratio * (plotBottom - plotTop)

    yTicks = sorted({0, maxCount, *[round(maxCount * index / 4) for index in range(1, 4)]})
    for tick in yTicks:
        y = _y(tick)
        draw.line((plotLeft, y, plotRight, y), fill=gridColor, width=1)
        draw.text((22, y - 7), f"{tick:,}", fill=mutedTextColor, font=tinyFont)

    for index in range(5):
        ordinal = minOrdinal + round((maxOrdinal - minOrdinal) * index / 4)
        x = plotLeft + (plotRight - plotLeft) * index / 4
        tickDate = datetime.fromordinal(int(ordinal))
        draw.line((x, plotTop, x, plotBottom), fill=gridColor, width=1)
        label = tickDate.strftime("%Y") if (maxOrdinal - minOrdinal) >= 365 else tickDate.strftime("%b %d")
        textWidth = draw.textlength(label, font=tinyFont)
        draw.text((x - textWidth / 2, plotBottom + 11), label, fill=mutedTextColor, font=tinyFont)

    draw.line((plotLeft, plotBottom, plotRight, plotBottom), fill=axisColor, width=2)
    draw.line((plotLeft, plotTop, plotLeft, plotBottom), fill=axisColor, width=2)

    coords = [(_x(day), _y(count)) for day, count in points]
    if len(coords) == 1:
        x, y = coords[0]
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=lineColor)
    else:
        draw.line(coords, fill=lineColor, width=4, joint="curve")
        for x, y in coords[-8:]:
            draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill=lineColor)

    endX, endY = coords[-1]
    draw.ellipse((endX - 5, endY - 5, endX + 5, endY + 5), fill=lineColor)
    draw.text((min(endX + 8, plotRight - 90), max(plotTop + 4, endY - 20)), f"{maxCount:,}", fill=textColor, font=labelFont)
    draw.rectangle((0, 0, 7, height), fill=redColor)

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename=str(filename or _BADGE_GRAPH_FILENAME))


def applyBadgeTimelineGraph(
    embed: discord.Embed,
    report: Any,
    *,
    filename: str = _BADGE_GRAPH_FILENAME,
) -> discord.File | None:
    graphFile = buildBadgeTimelineGraphFile(report, filename=filename)
    if graphFile is None:
        return None
    embed.set_image(url=f"attachment://{filename}")
    return graphFile


def _publicScanLines(score: scoring.RiskScore) -> list[str]:
    return [
        f"Review Band: **{_publicBandValue(score)}**",
        f"Review Risk: **{_publicReviewRiskValue(score)}**",
        f"Confidence: **{_compactConfidenceValue(score)}**",
    ]


def _signalText(signal: scoring.RiskSignal) -> str:
    if signal.kind == "override" and signal.points > 0:
        prefix = f"min {signal.points}"
    elif signal.points > 0:
        prefix = f"+{signal.points}"
    elif signal.points < 0:
        prefix = str(signal.points)
    else:
        prefix = "0"
    return f"`{prefix}` {signal.label}"


def _scanReasonLines(
    score: scoring.RiskScore,
    *,
    cautionLimit: int = 5,
    reassuringLimit: int = 5,
    dataLimit: int = 4,
) -> list[str]:
    signals = list(score.signals or [])
    caution = [
        signal
        for signal in signals
        if signal.kind == "override" or int(signal.points or 0) > 0
    ]
    reassuring = [
        signal
        for signal in signals
        if signal.kind == "reassuring" or int(signal.points or 0) < 0
    ]
    data = [
        signal
        for signal in signals
        if signal not in caution and signal not in reassuring
    ]
    rows = _publicScanLines(score)
    rows.append("")
    rows.append("Why Jane is cautious:")
    cautionLimit = max(1, int(cautionLimit or 5))
    rows.extend(_signalText(signal) for signal in caution[:cautionLimit])
    if len(caution) > cautionLimit:
        rows.append(f"... and {len(caution) - cautionLimit} more caution signal(s)")
    if not caution:
        rows.append("No positive-risk signals matched.")
    rows.append("")
    rows.append("Why this looks real:")
    reassuringLimit = max(1, int(reassuringLimit or 5))
    rows.extend(_signalText(signal) for signal in reassuring[:reassuringLimit])
    if len(reassuring) > reassuringLimit:
        rows.append(f"... and {len(reassuring) - reassuringLimit} more reassuring signal(s)")
    if not reassuring:
        rows.append("No strong reassuring signals matched.")
    dataLimit = max(1, int(dataLimit or 4))
    if data:
        rows.append("")
        rows.append("Data notes:")
        rows.extend(_signalText(signal) for signal in data[:dataLimit])
        if len(data) > dataLimit:
            rows.append(f"... and {len(data) - dataLimit} more data note(s)")
    return rows


_PUBLIC_SECTION_LABELS = {
    "scan": "Detection Summary",
    "sources": "Source Checks",
    "profile": "Profile Information",
    "connections": "Connections",
    "groups": "Groups",
    "inventory": "Inventory",
    "gamepasses": "Gamepasses",
    "games": "Favorites",
    "outfits": "Outfits",
    "badges": "Badges",
    "external": "Safety Records",
    "history": "Jane History",
}


def _publicSectionField(report: Any, section: str, score: scoring.RiskScore) -> tuple[str, str]:
    if section == "scan":
        lines = _scanReasonLines(score, cautionLimit=10, reassuringLimit=10, dataLimit=10)
        directMatches = [
            match
            for match in list(getattr(report, "directMatches", None) or [])
            if isinstance(match, dict)
        ]
        if directMatches:
            lines.append("")
            lines.append("Direct rule matches:")
            lines.extend(_directMatchLine(match) for match in directMatches[:12])
        return "[Scan] Detection Summary", _listLines(
            lines,
            limit=40,
        )
    if section == "sources":
        lines = _decisionReadinessLines(report, score)
        lines.append("")
        lines.extend(_sourceProvenanceLines(report))
        return "[Sources] Source Checks", _listLines(lines, limit=36)
    if section == "profile":
        reviewBucket = bgBuckets.normalizeBgReviewBucket(getattr(report, "reviewBucket", None))
        lines = _profileDetailLines(report)
        lines.append("")
        lines.extend(_scanCoverageLines(report, reviewBucket))
        return "[Profile] Profile Information", _listLines(lines, limit=24)
    if section == "connections":
        return "[Connections] Connections", _listLines(_connectionDetailLines(report), limit=22)
    if section == "groups":
        lines = _groupSummaryLines(report)
        if not lines:
            lines = [_overviewGroupLine(report)]
        flaggedGroups = [group for group in list(getattr(report, "flaggedGroups", None) or []) if isinstance(group, dict)]
        if flaggedGroups:
            lines.append("Flagged groups:")
            lines.extend(_groupLine(group) for group in flaggedGroups[:15])
        else:
            lines.append("Flagged groups: `0`")
        return "[Groups] Groups", _listLines(lines, empty=_overviewGroupLine(report), limit=24)
    if section == "inventory":
        return "[Inventory] Inventory", _listLines(_inventoryDetailLines(report), limit=24)
    if section == "gamepasses":
        return "[Gamepasses] Gamepasses", _listLines(_gamepassDetailLines(report), limit=24)
    if section == "games":
        return "[Favorites] Favorites", _listLines(_favoriteGameDetailLines(report), limit=24)
    if section == "outfits":
        return "[Outfits] Outfits", _listLines(_outfitDetailLines(report), limit=24)
    if section == "badges":
        lines = [_overviewBadgeLine(report)]
        lines.extend(_badgeTimelineLines(report))
        flaggedBadges = [badge for badge in list(getattr(report, "flaggedBadges", None) or []) if isinstance(badge, dict)]
        badgeHistory = [badge for badge in list(getattr(report, "badgeHistorySample", None) or []) if isinstance(badge, dict)]
        if flaggedBadges:
            lines.append("Flagged badges:")
            lines.extend(_badgeLine(badge) for badge in flaggedBadges[:15])
        if badgeHistory:
            lines.append("Badge sample:")
            lines.extend(_badgeHistoryLine(badge) for badge in badgeHistory[:15])
        return "[Badges] Badges", _listLines(lines, limit=28)
    if section == "external":
        lines = _recordDetailLines(report)
        return "[Records] Safety Records", _listLines(lines, limit=28)
    if section == "history":
        return "[History] Jane History", _listLines(_priorDetailLines(report), limit=28)
    return "[Overview] Overview", "Unknown section."


def _listLines(rows: list[str], *, empty: str = "(none)", limit: int = 8) -> str:
    if not rows:
        return empty
    visible = rows[:limit]
    if len(rows) > limit:
        visible.append(f"... and {len(rows) - limit} more")
    return _truncate("\n".join(visible))


def _statusBlock(status: str, error: Optional[str], rows: list[str]) -> str:
    normalized = str(status or "SKIPPED").strip().upper()
    base = f"Status: `{normalized}`"
    if error:
        base += f"\nNote: {_truncate(error, 300)}"
    if rows:
        base += "\n" + _listLines(rows)
    return _truncate(base)


def buildReportEmbed(
    report: Any,
    *,
    score: scoring.RiskScore,
    reportId: int | None = None,
    includeTextReport: bool = False,
) -> discord.Embed:
    embed = discord.Embed(
        description=_publicHeaderLine(report),
        color=_overviewColor(),
        timestamp=datetime.now(timezone.utc),
    )

    _field(
        embed,
        "[Scan] Detection Summary",
        _listLines(_publicScanLines(score), limit=3),
    )
    _field(embed, "[Profile] Profile Information", _overviewProfileLine(report))
    _field(embed, "[Connections] Connections", _overviewConnectionLine(report))
    _field(embed, "[Groups] Groups", _overviewGroupLine(report))
    _field(embed, "[Inventory] Inventory", _overviewInventoryLine(report))
    _field(embed, "[Gamepasses] Gamepasses", _overviewGamepassLine(report))
    _field(embed, "[Favorites] Favorites", _overviewFavoritesLine(report))
    _field(embed, "[Records] TASE Records", _overviewTaseRecordLine(report))
    _field(embed, "[Badges] Badges", _overviewBadgeLine(report))

    reportText = "Full text report is attached. " if includeTextReport else ""
    footer = (
        "Use the controls below to expand sections. "
        f"{reportText}Staff still make the actual call."
    )
    if reportId is not None and int(reportId) > 0:
        footer = f"Report #{int(reportId)} | {footer}"
    embed.set_footer(text=footer)
    return embed


def buildPublicSectionEmbed(
    report: Any,
    *,
    score: scoring.RiskScore,
    section: str,
    reportId: int | None = None,
    includeTextReport: bool = False,
) -> discord.Embed:
    normalized = str(section or "overview").strip().lower()
    if normalized == "overview":
        return buildReportEmbed(report, score=score, reportId=reportId, includeTextReport=includeTextReport)
    if normalized not in _PUBLIC_SECTION_LABELS:
        normalized = "overview"
        return buildReportEmbed(report, score=score, reportId=reportId, includeTextReport=includeTextReport)

    sectionLabel = _PUBLIC_SECTION_LABELS[normalized]
    embed = discord.Embed(
        description=f"{_publicHeaderLine(report)}\nExpanded section: **{sectionLabel}**",
        color=_overviewColor(),
        timestamp=datetime.now(timezone.utc),
    )

    fieldName, fieldValue = _publicSectionField(report, normalized, score)
    _field(embed, fieldName, fieldValue)

    footer = (
        "Use Overview to return to the full overview. "
        + ("Full text report stays attached." if includeTextReport else "Staff still make the actual call.")
    )
    if reportId is not None and int(reportId) > 0:
        footer = f"Report #{int(reportId)} | {footer}"
    embed.set_footer(text=footer)
    return embed


def buildSectionEmbed(
    report: Any,
    *,
    score: scoring.RiskScore,
    section: str,
    reportId: int | None = None,
) -> discord.Embed:
    normalized = str(section or "overview").strip().lower()
    if normalized == "overview":
        return buildReportEmbed(report, score=score, reportId=reportId)

    displayName = str(getattr(report, "discordDisplayName", "") or getattr(report, "robloxUsername", "") or "Unknown User")
    sectionLabels = {
        "profile": "Profile Information",
        "groups": "Groups",
        "inventory": "Inventory",
        "games": "Favorites",
        "outfits": "Outfits",
        "badges": "Badges",
        "external": "Safety Records",
    }
    sectionLabel = sectionLabels.get(normalized, normalized.title())
    embed = discord.Embed(
        title=f"BG Intel Details - {displayName}",
        description=f"Section: **{sectionLabel}**\nScore: {scoring.compactScoreLine(score)}",
        color=_scoreColor(score),
    )

    if normalized == "profile":
        robloxUserId = getattr(report, "robloxUserId", None)
        robloxUrl = f"https://www.roblox.com/users/{int(robloxUserId)}/profile" if robloxUserId else ""
        rows = [
            f"Discord ID: `{int(getattr(report, 'discordUserId', 0) or 0) or 'none'}`",
            f"Roblox User ID: `{robloxUserId or 'none'}`",
            f"Roblox Username: `{getattr(report, 'robloxUsername', None) or 'none'}`",
            f"Roblox Profile: {robloxUrl or '(none)'}",
            f"Identity source: `{getattr(report, 'identitySource', 'unknown')}`",
            f"Roblox created: `{getattr(report, 'robloxCreated', None) or 'unknown'}`",
            f"Roblox age: `{_formatAge(getattr(report, 'robloxAgeDays', None))}`",
            f"Alt/identity check: `{getattr(report, 'altScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"Friend sample: `{getattr(report, 'friendIdsScanStatus', 'SKIPPED') or 'SKIPPED'}` ({len(list(getattr(report, 'friendUserIds', None) or []))} ID(s))",
            f"RoVer note: `{getattr(report, 'roverError', None) or 'none'}`",
        ]
        altMatches = [match for match in list(getattr(report, "altMatches", None) or []) if isinstance(match, dict)]
        if altMatches:
            rows.append("Alt/identity evidence:")
            rows.extend(_altMatchLine(match) for match in altMatches[:8])
        embed.add_field(name="Profile", value=_truncate("\n".join(rows)), inline=False)
        embed.add_field(name="Data Completeness", value=_truncate("\n".join(_completenessLines(report, bgBuckets.normalizeBgReviewBucket(getattr(report, "reviewBucket", None))))), inline=False)
    elif normalized == "groups":
        groups = list(getattr(report, "groups", None) or [])
        flaggedGroups = list(getattr(report, "flaggedGroups", None) or [])
        rows = [
            f"Status: `{getattr(report, 'groupScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"Error: `{getattr(report, 'groupScanError', None) or 'none'}`",
        ]
        rows.extend(_groupSummaryLines(report))
        embed.add_field(name="Summary", value=_truncate("\n".join(rows)), inline=False)
        embed.add_field(name="Flagged Groups", value=_listLines([_groupLine(group) for group in flaggedGroups], limit=20), inline=False)
        embed.add_field(name="Sample Groups", value=_listLines([_groupLine(group) for group in groups[:20]], empty="No groups found.", limit=20), inline=False)
    elif normalized == "inventory":
        items = list(getattr(report, "flaggedItems", None) or [])
        summary = getattr(report, "inventorySummary", None) or {}
        rows = [
            f"Status: `{getattr(report, 'inventoryScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"Error: `{getattr(report, 'inventoryScanError', None) or 'none'}`",
            f"Private inventory DM: `{getattr(report, 'privateInventoryDmSent', None)}`",
        ]
        if isinstance(summary, dict) and summary:
            rows.extend(
                [
                    f"Items scanned: `{_safeInt(summary.get('itemsScanned')):,}`",
                    f"Unique assets: `{_safeInt(summary.get('uniqueAssetCount')):,}`",
                    f"Suspicious item hits: `{_safeInt(summary.get('flaggedItemCount'), len(items)):,}`",
                    f"Visual matches: `{_safeInt(summary.get('visualMatchedCount')):,}` from `{_safeInt(summary.get('visualCandidateCount')):,}` candidate(s)",
                    f"Keyword hits: `{_safeInt(summary.get('keywordMatchCount')):,}` exact / `{_safeInt(summary.get('normalizedKeywordMatchCount')):,}` normalized / `{_safeInt(summary.get('fuzzyKeywordMatchCount')):,}` fuzzy",
                ]
            )
            if summary.get("visualError"):
                rows.append(f"Visual note: {_truncate(summary.get('visualError'), 220)}")
        embed.add_field(name="Inventory", value=_truncate("\n".join(rows)), inline=False)
        embed.add_field(name="Flagged Items", value=_listLines([_itemLine(item) for item in items], limit=20), inline=False)
    elif normalized == "games":
        games = list(getattr(report, "favoriteGames", None) or [])
        flaggedGames = list(getattr(report, "flaggedFavoriteGames", None) or [])
        rows = [
            f"Status: `{getattr(report, 'favoriteGameScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"Error: `{getattr(report, 'favoriteGameScanError', None) or 'none'}`",
            f"Favorite games checked: `{len(games)}`",
        ]
        embed.add_field(name="Favorite Games", value=_truncate("\n".join(rows)), inline=False)
        embed.add_field(name="Flagged Games", value=_listLines([_gameLine(game) for game in flaggedGames], limit=20), inline=False)
        embed.add_field(name="Sample Games", value=_listLines([_gameLine(game) for game in games[:20]], empty="No favorite games found.", limit=20), inline=False)
    elif normalized == "outfits":
        outfits = list(getattr(report, "outfits", None) or [])
        rows = [
            f"Status: `{getattr(report, 'outfitScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"Error: `{getattr(report, 'outfitScanError', None) or 'none'}`",
            f"Outfits checked: `{len(outfits)}`",
        ]
        embed.add_field(name="Outfits", value=_truncate("\n".join(rows)), inline=False)
        embed.add_field(name="Sample Outfits", value=_listLines([_outfitLine(outfit) for outfit in outfits[:20]], empty="No outfits found.", limit=20), inline=False)
    elif normalized == "badges":
        flaggedBadges = list(getattr(report, "flaggedBadges", None) or [])
        badgeHistory = list(getattr(report, "badgeHistorySample", None) or [])
        rows = [
            f"Flag scan status: `{getattr(report, 'badgeScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"Flag scan error: `{getattr(report, 'badgeScanError', None) or 'none'}`",
            f"History sample status: `{getattr(report, 'badgeHistoryScanStatus', 'SKIPPED') or 'SKIPPED'}`",
            f"History sample error: `{getattr(report, 'badgeHistoryScanError', None) or 'none'}`",
            f"Public badge sample size: `{len(badgeHistory)}`",
        ]
        rows.extend(_badgeTimelineLines(report))
        embed.add_field(name="Badges", value=_truncate("\n".join(rows)), inline=False)
        embed.add_field(name="Flagged Badges", value=_listLines([_badgeLine(badge) for badge in flaggedBadges], limit=20), inline=False)
        embed.add_field(name="Badge Sample", value=_listLines([_badgeHistoryLine(badge) for badge in badgeHistory[:20]], empty="No public badges found.", limit=20), inline=False)
    elif normalized == "external":
        externalLines = _externalSourceLines(report)
        matches = list(getattr(report, "externalSourceMatches", None) or [])
        embed.add_field(name="Safety Record Sources", value=_truncate("\n".join(externalLines or ["No external source data."])), inline=False)
        embed.add_field(name="Matched Records", value=_listLines([_externalMatchLine(match) for match in matches if isinstance(match, dict)], empty="No external records matched.", limit=20), inline=False)
        topGuildLines: list[str] = []
        for match in matches:
            if not isinstance(match, dict) or str(match.get("source") or "").lower() != "tase":
                continue
            for guild in list(match.get("topGuilds") or [])[:10]:
                if not isinstance(guild, dict):
                    continue
                guildName = guild.get("name") or "Unknown server"
                guildScore = guild.get("score") or 0
                guildTypes = ", ".join(str(value) for value in list(guild.get("types") or [])[:2])
                suffix = f" - {guildTypes}" if guildTypes else ""
                topGuildLines.append(f"{guildName} - score `{guildScore}`{suffix}")
        if topGuildLines:
            embed.add_field(name="TASE Top Servers", value=_listLines(topGuildLines, limit=10), inline=False)
    else:
        embed.description = "Unknown detail section."

    footer = f"Section: {sectionLabel} | Use this as a triage aid. Staff still make the actual call."
    if reportId is not None and int(reportId) > 0:
        footer = f"Report #{int(reportId)} | {footer}"
    embed.set_footer(text=footer)
    return embed


def buildDecisionSummary(
    report: Any,
    *,
    score: scoring.RiskScore,
    reportId: int | None = None,
) -> str:
    header = f"BG Intel Summary - {_displayName(report)}"
    if reportId is not None and int(reportId) > 0:
        header += f" (Report #{int(reportId)})"
    scoreText = f"{int(score.score)}/100 - {score.band}" if score.scored else f"Not scored - {score.band}"
    rows = [
        header,
        f"Score: {scoreText}",
        f"Confidence: {score.confidenceLabel} ({int(score.confidence)}%)",
        "",
        "Decision Readiness:",
        *_decisionReadinessLines(report, score),
        "",
        "Top caution signals:",
    ]
    cautionSignals = [
        signal
        for signal in list(score.signals or [])
        if signal.kind == "override" or int(signal.points or 0) > 0
    ]
    rows.extend(_signalText(signal) for signal in cautionSignals[:6])
    if not cautionSignals:
        rows.append("No positive-risk signals matched.")
    rows.extend(
        [
            "",
            "Coverage:",
            *_scanCoverageLines(report, bgBuckets.normalizeBgReviewBucket(getattr(report, "reviewBucket", None))),
            "",
            "Staff still make the actual call.",
        ]
    )
    return "\n".join(str(row) for row in rows)


def buildReportText(
    report: Any,
    *,
    score: scoring.RiskScore,
    reportId: int | None = None,
) -> str:
    discordUserId = int(getattr(report, "discordUserId", 0) or 0)
    reviewBucket = bgBuckets.normalizeBgReviewBucket(getattr(report, "reviewBucket", None))
    lines: list[str] = [
        "Jane Background Intelligence Report",
        "=" * 35,
    ]
    if reportId is not None and int(reportId) > 0:
        lines.append(f"Report ID: {int(reportId)}")
    scoreText = f"{score.score}/100" if score.scored else "Not scored"
    lines.extend(
        [
            f"Discord User: {getattr(report, 'discordDisplayName', 'Unknown')} ({discordUserId})",
            f"Discord Username: {getattr(report, 'discordUsername', '')}",
            f"Review Route: {bgBuckets.bgReviewBucketLabel(reviewBucket)} via {getattr(report, 'reviewBucketSource', 'unknown')}",
            "",
            "Jane Analysis",
            f"Rating: {score.band}",
            f"Score: {scoreText}",
            f"Confidence: {score.confidenceLabel} ({score.confidence}/100)",
            f"Outcome: {score.outcome}",
            f"Hard Minimum: {score.hardMinimum}/100",
            "",
            "Decision Readiness",
        ]
    )
    lines.extend(_decisionReadinessLines(report, score))
    lines.extend(
        [
            "",
            "Roblox Profile",
            f"Discord User ID: {discordUserId or '(none)'}",
            f"Roblox User ID: {getattr(report, 'robloxUserId', None) or '(none)'}",
            f"Roblox Username: {getattr(report, 'robloxUsername', None) or '(none)'}",
            f"Identity Source: {getattr(report, 'identitySource', 'rover')}",
            f"RoVer Error: {getattr(report, 'roverError', None) or '(none)'}",
            f"Roblox Created: {getattr(report, 'robloxCreated', None) or '(unknown)'}",
            f"Roblox Age: {_formatAge(getattr(report, 'robloxAgeDays', None))}",
            f"Username History Status: {getattr(report, 'usernameHistoryScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Username History Error: {getattr(report, 'usernameHistoryScanError', None) or '(none)'}",
            "Previous Usernames: "
            + (
                ", ".join(str(value) for value in list(getattr(report, "previousRobloxUsernames", None) or []))
                or "(none)"
            ),
            f"Known-Member Alt Status: {getattr(report, 'altScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Known-Member Alt Error: {getattr(report, 'altScanError', None) or '(none)'}",
            f"Friend Sample Status: {getattr(report, 'friendIdsScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Friend Sample Error: {getattr(report, 'friendIdsScanError', None) or '(none)'}",
            f"Friend IDs Sampled: {len(list(getattr(report, 'friendUserIds', None) or []))}",
            "",
            "Data Completeness",
        ]
    )
    lines.extend(_completenessLines(report, reviewBucket))
    lines.extend(["", "Source Checks"])
    lines.extend(_sourceProvenanceLines(report) or ["(none)"])
    lines.extend(
        [
            "",
            "Signals",
        ]
    )
    lines.extend(scoring.signalLines(score, limit=999))

    lines.extend(["", "Direct Matches:"])
    directMatches = list(getattr(report, "directMatches", None) or [])
    lines.extend([_directMatchLine(match) for match in directMatches] or ["(none)"])

    lines.extend(["", "Alt / Identity Evidence:"])
    altMatches = [match for match in list(getattr(report, "altMatches", None) or []) if isinstance(match, dict)]
    lines.extend([_altMatchLine(match) for match in altMatches] or ["(none)"])

    lines.extend(["", "External Sources"])
    lines.extend(_externalSourceLines(report) or ["(none)"])

    lines.extend(
        [
            "",
            "Groups",
            f"Status: {getattr(report, 'groupScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'groupScanError', None) or '(none)'}",
            f"Groups Checked: {len(list(getattr(report, 'groups', None) or []))}",
            "Flagged Groups:",
        ]
    )
    flaggedGroups = list(getattr(report, "flaggedGroups", None) or [])
    lines.extend([_groupLine(group) for group in flaggedGroups] or ["(none)"])
    groupSummaryLines = _groupSummaryLines(report)
    if groupSummaryLines:
        lines.extend(["", "Group Summary:"])
        lines.extend(groupSummaryLines)

    lines.extend(["", "Flag Matches:"])
    flagMatches = list(getattr(report, "flagMatches", None) or [])
    if flagMatches:
        for match in flagMatches:
            if isinstance(match, dict):
                lines.append(_flagMatchLine(match))
            else:
                lines.append(str(match))
    else:
        lines.append("(none)")

    lines.extend(
        [
            "",
            "Inventory",
            f"Status: {getattr(report, 'inventoryScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'inventoryScanError', None) or '(none)'}",
            f"Private Inventory DM: {getattr(report, 'privateInventoryDmSent', None)}",
        ]
    )
    inventorySummary = getattr(report, "inventorySummary", None) or {}
    if isinstance(inventorySummary, dict) and inventorySummary:
        lines.extend(
            [
                f"Items Scanned: {_safeInt(inventorySummary.get('itemsScanned'))}",
                f"Unique Assets: {_safeInt(inventorySummary.get('uniqueAssetCount'))}",
                f"Suspicious Item Hits: {_safeInt(inventorySummary.get('flaggedItemCount'))}",
                f"Visual Matches: {_safeInt(inventorySummary.get('visualMatchedCount'))} from {_safeInt(inventorySummary.get('visualCandidateCount'))} candidate(s)",
                "Keyword Hits: "
                f"{_safeInt(inventorySummary.get('keywordMatchCount'))} exact / "
                f"{_safeInt(inventorySummary.get('normalizedKeywordMatchCount'))} normalized / "
                f"{_safeInt(inventorySummary.get('fuzzyKeywordMatchCount'))} fuzzy",
            ]
        )
        if inventorySummary.get("visualError"):
            lines.append(f"Visual Note: {inventorySummary.get('visualError')}")
    lines.append("Flagged Items:")
    flaggedItems = list(getattr(report, "flaggedItems", None) or [])
    lines.extend([_itemLine(item) for item in flaggedItems] or ["(none)"])

    lines.extend(
        [
            "",
            "Gamepasses",
            f"Status: {getattr(report, 'gamepassScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'gamepassScanError', None) or '(none)'}",
        ]
    )
    gamepassSummary = getattr(report, "gamepassSummary", None) or {}
    if isinstance(gamepassSummary, dict) and gamepassSummary:
        lines.extend(
            [
                f"Gamepasses Found: {_safeInt(gamepassSummary.get('totalGamepasses'))}",
                f"Total Known Gamepass Value: {_formatRobux(gamepassSummary.get('totalRobux'))}",
                f"Priced Gamepasses: {_safeInt(gamepassSummary.get('pricedGamepasses'))}",
                f"Unpriced Gamepasses: {_safeInt(gamepassSummary.get('unpricedGamepasses'))}",
                f"Complete Gamepass Scan: {'yes' if gamepassSummary.get('complete', True) else 'no'}",
            ]
        )
        if gamepassSummary.get("priceError"):
            lines.append(f"Value Note: {gamepassSummary.get('priceError')}")
    lines.append("Owned Gamepasses:")
    ownedGamepasses = list(getattr(report, "ownedGamepasses", None) or [])
    lines.extend([_gamepassLine(gamepass) for gamepass in ownedGamepasses] or ["(none)"])

    lines.extend(
        [
            "",
            "Favorite Games",
            f"Status: {getattr(report, 'favoriteGameScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'favoriteGameScanError', None) or '(none)'}",
            f"Favorite Games Checked: {len(list(getattr(report, 'favoriteGames', None) or []))}",
            "Flagged Favorite Games:",
        ]
    )
    flaggedFavoriteGames = list(getattr(report, "flaggedFavoriteGames", None) or [])
    lines.extend([_gameLine(game) for game in flaggedFavoriteGames] or ["(none)"])
    lines.append("Favorite Game Sample:")
    favoriteGames = list(getattr(report, "favoriteGames", None) or [])
    lines.extend([_gameLine(game) for game in favoriteGames] or ["(none)"])

    lines.extend(
        [
            "",
            "Outfits",
            f"Status: {getattr(report, 'outfitScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'outfitScanError', None) or '(none)'}",
            f"Outfits Checked: {len(list(getattr(report, 'outfits', None) or []))}",
            "Outfits:",
        ]
    )
    outfits = list(getattr(report, "outfits", None) or [])
    lines.extend([_outfitLine(outfit) for outfit in outfits] or ["(none)"])

    lines.extend(
        [
            "",
            "Badges",
            f"Status: {getattr(report, 'badgeScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'badgeScanError', None) or '(none)'}",
            "Flagged Badges:",
        ]
    )
    flaggedBadges = list(getattr(report, "flaggedBadges", None) or [])
    lines.extend([_badgeLine(badge) for badge in flaggedBadges] or ["(none)"])

    lines.extend(
        [
            "",
            "Badge History Sample",
            f"Status: {getattr(report, 'badgeHistoryScanStatus', 'SKIPPED') or 'SKIPPED'}",
            f"Error: {getattr(report, 'badgeHistoryScanError', None) or '(none)'}",
            f"Public Badge Sample Size: {len(list(getattr(report, 'badgeHistorySample', None) or []))}",
            "Badge Timeline:",
        ]
    )
    timelineLines = _badgeTimelineLines(report)
    lines.extend(timelineLines or ["(none)"])
    lines.extend(["", "Sample Badges:"])
    badgeHistory = list(getattr(report, "badgeHistorySample", None) or [])
    lines.extend([_badgeHistoryLine(badge) for badge in badgeHistory] or ["(none)"])

    lines.extend(["", "Jane History"])
    lines.extend(_priorDetailLines(report) or ["(none)"])

    lines.extend(
        [
            "",
            "Reminder",
            "This report is a triage aid. Staff still make the actual decision.",
        ]
    )
    return "\n".join(lines)


def buildReportTextFile(
    report: Any,
    *,
    score: scoring.RiskScore,
    reportId: int | None = None,
    filename: str = _REPORT_TEXT_FILENAME,
) -> discord.File:
    text = buildReportText(report, score=score, reportId=reportId)
    encoded = text.encode("utf-8")
    if len(encoded) > _REPORT_TEXT_UPLOAD_LIMIT_BYTES:
        note = "\n\n[Report text truncated to fit Discord upload limits.]\n"
        noteBytes = note.encode("utf-8")
        availableBytes = max(0, _REPORT_TEXT_UPLOAD_LIMIT_BYTES - len(noteBytes))
        encoded = (
            encoded[:availableBytes]
            .decode("utf-8", errors="ignore")
            .rstrip()
            .encode("utf-8")
        )
        encoded += noteBytes
    buffer = BytesIO(encoded)
    return discord.File(buffer, filename=str(filename or _REPORT_TEXT_FILENAME))
