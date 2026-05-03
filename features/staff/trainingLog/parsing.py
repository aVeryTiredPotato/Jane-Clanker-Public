from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import discord

hostMentionRegex = re.compile(r"<@!?(\d+)>")
certificationTitleRegex = re.compile(
    r"^(Grid|Emergency|Turbine|Solo|Supervisor) Certification(?: (Training|Examination))? Session Completed$",
    re.IGNORECASE,
)
mirrorSourceFooterRegex = re.compile(r"^Source message ID:\s*(\d+)\s*$", re.IGNORECASE)
defaultStatsOrder = [
    "ORIENTATION",
    "GRID_TRAINING",
    "GRID_EXAM",
    "EMERGENCY_TRAINING",
    "EMERGENCY_EXAM",
    "TURBINE",
    "SOLO",
    "SUPERVISOR",
]
weeklySummaryTypeOrder = [
    ("GRID", "Grid"),
    ("EMERGENCY", "Emergency"),
    ("TURBINE", "Turbine"),
    ("SOLO", "Solo"),
    ("SUPERVISOR", "Supervisor"),
]
trainingMirrorColor = discord.Color.from_rgb(245, 150, 78)
summaryEmbedTitle = "Training Log Summary"


@dataclass(slots=True)
class ParsedTrainingResult:
    eventKind: str
    certType: str
    certVariant: str
    title: str
    hostId: int
    hostText: str
    passCount: int
    failCount: int
    passAttendees: tuple[str, ...] = ()
    failAttendees: tuple[str, ...] = ()


def normalizeWhitespace(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def normalizeTitleLine(value: object) -> str:
    text = normalizeWhitespace(value)
    text = re.sub(r"^#{1,6}\s*", "", text).strip()
    for wrapper in ("**", "__", "*", "_", "`"):
        if text.startswith(wrapper) and text.endswith(wrapper) and len(text) > (len(wrapper) * 2):
            text = text[len(wrapper):-len(wrapper)].strip()
    return normalizeWhitespace(text)


def normalizeSectionControlLine(value: object) -> str:
    text = normalizeTitleLine(value).casefold()
    return text.strip("*_~`> ")


def isSectionBoundaryLine(value: object) -> bool:
    line = normalizeSectionControlLine(value)
    if not line:
        return False
    if line.endswith(":") and (
        line.startswith("certified recipients")
        or line.startswith("failed attendees")
        or line in {"passed:", "failed:"}
    ):
        return True
    if line.startswith("host:") or line.startswith("co-host") or line.startswith("other cohosts"):
        return True
    if line.startswith("each recipient"):
        return True
    if line.startswith("common mistakes") or line.startswith("please do not") or line.startswith("don't be discouraged"):
        return True
    if line.startswith("totally emergency exam") or line.startswith("supervisor cert examination"):
        return True
    if line == "passed" or line == "failed":
        return True
    if line == "none" or line.startswith("none!"):
        return True
    return False


def normalizeNameLookup(value: object) -> str:
    text = normalizeWhitespace(value)
    if text.startswith("@"):
        text = text[1:].strip()
    text = re.sub(r"\[[^\]]+\]", "", text).strip()
    return normalizeWhitespace(text).casefold()


def cleanVisibleLabel(value: object) -> str:
    text = normalizeWhitespace(value)
    if text.startswith("@"):
        text = text[1:].strip()
    text = re.sub(r"\[[^\]]+\]", "", text).strip()
    return normalizeWhitespace(text)


def formatPercent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "n/a"
    return f"{(float(numerator) / float(denominator)) * 100.0:.1f}%"


def parseIsoOrNow(rawValue: object) -> datetime:
    rawText = str(rawValue or "").strip()
    if rawText:
        try:
            parsed = datetime.fromisoformat(rawText)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def extractMessageText(message: discord.Message) -> str:
    parts: list[str] = []
    content = str(getattr(message, "content", "") or "").strip()
    if content:
        parts.append(content)

    for embed in list(getattr(message, "embeds", []) or []):
        title = normalizeWhitespace(getattr(embed, "title", None))
        if title:
            parts.append(title)
        description = str(getattr(embed, "description", "") or "").strip()
        if description:
            parts.append(description)
        for field in list(getattr(embed, "fields", []) or []):
            fieldName = normalizeWhitespace(getattr(field, "name", None))
            fieldValue = str(getattr(field, "value", "") or "").strip()
            if fieldName and fieldValue:
                parts.append(f"{fieldName}:\n{fieldValue}")
            elif fieldValue:
                parts.append(fieldValue)

    return "\n\n".join(part for part in parts if str(part or "").strip())


def resolveMentionLabel(rawLine: str, guild: Optional[discord.Guild]) -> str:
    mentionMatch = hostMentionRegex.search(str(rawLine or ""))
    if mentionMatch:
        mentionedUserId = int(mentionMatch.group(1))
        if guild is not None:
            member = guild.get_member(mentionedUserId)
            if member is not None:
                return normalizeWhitespace(member.display_name)
        return f"user {mentionedUserId}"
    return cleanVisibleLabel(rawLine)


def extractHost(hostLine: str, guild: Optional[discord.Guild]) -> tuple[int, str]:
    mentionMatch = hostMentionRegex.search(hostLine)
    if mentionMatch:
        mentionedUserId = int(mentionMatch.group(1))
        if guild is not None:
            member = guild.get_member(mentionedUserId)
            if member is not None:
                return mentionedUserId, normalizeWhitespace(member.display_name)
        return mentionedUserId, resolveMentionLabel(hostLine.split(":", 1)[-1], guild)

    hostText = cleanVisibleLabel(hostLine.split(":", 1)[-1])
    lookupTarget = normalizeNameLookup(hostText)
    if guild is not None and lookupTarget:
        matches = [
            member
            for member in guild.members
            if normalizeNameLookup(member.display_name) == lookupTarget or normalizeNameLookup(member.name) == lookupTarget
        ]
        if len(matches) == 1:
            return int(matches[0].id), normalizeWhitespace(matches[0].display_name)
    return 0, hostText


def extractSectionEntries(lines: list[str], headerPrefix: str, guild: Optional[discord.Guild]) -> list[str]:
    inSection = False
    entries: list[str] = []
    normalizedHeader = normalizeSectionControlLine(headerPrefix)
    for rawLine in lines:
        line = str(rawLine or "").strip()
        if not inSection:
            if normalizeSectionControlLine(line).startswith(normalizedHeader):
                inSection = True
            continue
        if not line:
            continue

        if isSectionBoundaryLine(line):
            break

        cleaned = resolveMentionLabel(line, guild)
        if cleaned:
            entries.append(cleaned)
    return entries


def countSectionEntries(lines: list[str], headerPrefix: str) -> int:
    inSection = False
    count = 0
    normalizedHeader = normalizeSectionControlLine(headerPrefix)
    for rawLine in lines:
        line = str(rawLine or "").strip()
        if not inSection:
            if normalizeSectionControlLine(line).startswith(normalizedHeader):
                inSection = True
            continue
        if not line:
            continue

        if isSectionBoundaryLine(line):
            break

        count += 1
    return count


def parseSourceMessage(message: discord.Message) -> ParsedTrainingResult | None:
    content = extractMessageText(message)
    if not content:
        return None
    lines = [str(line or "").rstrip() for line in content.splitlines()]
    if not lines:
        return None
    firstLine = str(next((line.strip() for line in lines if line.strip()), "")).strip()
    if not firstLine:
        return None
    normalizedTitle = normalizeTitleLine(firstLine)

    guild = message.guild if isinstance(message.guild, discord.Guild) else None
    hostLine = next((line for line in lines if str(line).strip().lower().startswith("host:")), "")
    hostId, hostText = extractHost(hostLine, guild)
    passAttendees = extractSectionEntries(lines, "**Certified Recipients (Pass):**", guild)
    if not passAttendees:
        passAttendees = extractSectionEntries(lines, "Certified Recipients (Pass):", guild)
    failAttendees = extractSectionEntries(lines, "**Failed Attendees:**", guild)
    if not failAttendees:
        failAttendees = extractSectionEntries(lines, "Failed Attendees:", guild)
    passCount = len(passAttendees)
    failCount = len(failAttendees)

    if normalizedTitle.casefold() == "orientation results":
        return ParsedTrainingResult(
            eventKind="ORIENTATION",
            certType="ORIENTATION",
            certVariant="GENERAL",
            title="Orientation Results",
            hostId=hostId,
            hostText=hostText,
            passCount=passCount,
            failCount=failCount,
            passAttendees=tuple(passAttendees),
            failAttendees=tuple(failAttendees),
        )

    titleMatch = certificationTitleRegex.match(normalizedTitle)
    if titleMatch is None:
        return None

    certType = str(titleMatch.group(1) or "").strip().upper()
    variantRaw = str(titleMatch.group(2) or "").strip().upper()
    if variantRaw == "TRAINING":
        certVariant = "TRAINING"
    elif variantRaw == "EXAMINATION":
        certVariant = "EXAM"
    else:
        certVariant = "GENERAL"

    return ParsedTrainingResult(
        eventKind="CERTIFICATION",
        certType=certType,
        certVariant=certVariant,
        title=normalizedTitle,
        hostId=hostId,
        hostText=hostText,
        passCount=passCount,
        failCount=failCount,
        passAttendees=tuple(passAttendees),
        failAttendees=tuple(failAttendees),
    )
