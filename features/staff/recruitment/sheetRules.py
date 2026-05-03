from __future__ import annotations

from typing import Any, Optional

import config


def normalize(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def cleanRobloxUsername(value: Any) -> str:
    text = str(value or "").strip()
    for marker in ("\u200b", "\u200c", "\u200d", "\ufeff"):
        text = text.replace(marker, "")
    return "".join(ch for ch in text if not ch.isspace())


def usernameLookupKey(value: Any) -> str:
    return cleanRobloxUsername(value).casefold()


def usernameSortTuple(value: Any) -> tuple[str, str]:
    raw = cleanRobloxUsername(value)
    return (normalize(raw), raw.casefold())


def toInt(value: object) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def toBool(value: object) -> bool:
    text = str(value or "").strip().lower()
    return text in {"true", "yes", "1", "y"}


def isRecruitmentMemberLabel(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    normalized = normalize(text)
    if normalized in {"robloxusername", "robloxuser", "ruser"}:
        return False
    lowered = text.lower()
    if "personnel" in lowered:
        return False
    sectionHeaders = {
        normalize(item) for item in getattr(config, "recruitmentSectionHeaders", []) if item
    }
    if normalized in sectionHeaders:
        return False
    return True


def isAllowedRecruitmentRank(value: str) -> bool:
    rank = str(value or "").strip()
    if not rank:
        return False
    allowed = getattr(config, "recruitmentAllowedRanks", []) or []
    allowedSet = {normalize(item) for item in allowed if item}
    if not allowedSet:
        # Fallback for older configs that do not define recruitmentAllowedRanks.
        allowedSet = {
            normalize("Commissioner 1 IC"),
            normalize("Comissioner 1 IC"),
            normalize("Head Recruiter 1 IC"),
            normalize("Head Recruiter 2 IC"),
            normalize("Head Recruiter 3 IC"),
            normalize("Head Recruiter 4 IC"),
            normalize("Recruitment Manager"),
            normalize("Recruitment Supervisor"),
            normalize("Lead Recruiter"),
            normalize("Senior Recruiter"),
            normalize("Recruiter"),
        }
    return normalize(rank) in allowedSet


def isWritableMemberRow(usernameCell: str, rankCell: str) -> bool:
    return isRecruitmentMemberLabel(usernameCell) and isAllowedRecruitmentRank(rankCell)


def findSectionHeaderRow(usernames: list, sectionName: str) -> Optional[int]:
    target = normalize(sectionName)
    for rowIndex, row in enumerate(usernames, start=1):
        cell = str(row[0]).strip() if row else ""
        if normalize(cell) == target:
            return rowIndex
    return None


def membersSectionHeaderCandidates() -> list[str]:
    configured = getattr(config, "recruitmentMembersSectionHeaderCandidates", None)
    if isinstance(configured, (list, tuple)):
        out = [str(item).strip() for item in configured if str(item).strip()]
        if out:
            return out
    single = str(getattr(config, "recruitmentMembersSectionHeader", "") or "").strip()
    if single:
        return [single, "Members"]
    return ["Employees", "Members"]


def findMembersSectionHeaderRow(usernames: list) -> Optional[int]:
    for sectionName in membersSectionHeaderCandidates():
        row = findSectionHeaderRow(usernames, sectionName)
        if row:
            return row
    return None


def detectFooterRow(usernames: list) -> int:
    configured = int(getattr(config, "recruitmentFooterRow", 0) or 0)
    if configured > 0:
        return configured
    for rowIndex in range(len(usernames), 0, -1):
        row = usernames[rowIndex - 1]
        cell = str(row[0]).strip() if row else ""
        if cell:
            return rowIndex
    return max(len(usernames), 1)


def sectionBoundsByHeader(usernames: list, sectionName: str) -> Optional[tuple[int, int]]:
    headerRow = findSectionHeaderRow(usernames, sectionName)
    if not headerRow:
        return None

    sectionHeaders = {
        normalize(item) for item in getattr(config, "recruitmentSectionHeaders", []) if item
    }
    if not sectionHeaders:
        sectionHeaders = {
            normalize("High Command"),
            normalize("Managers"),
            normalize("Employees"),
            normalize("Members"),
        }

    nextHeaderRow: Optional[int] = None
    for rowIndex in range(headerRow + 1, len(usernames) + 1):
        row = usernames[rowIndex - 1] if rowIndex - 1 < len(usernames) else []
        cell = str(row[0]).strip() if row else ""
        if normalize(cell) in sectionHeaders:
            nextHeaderRow = rowIndex
            break

    startRow = headerRow + 1
    if startRow - 1 < len(usernames):
        firstRow = usernames[startRow - 1] if usernames[startRow - 1] else []
        firstCell = str(firstRow[0]).strip() if firstRow else ""
        if not firstCell:
            startRow += 1

    if nextHeaderRow:
        endRow = nextHeaderRow - 1
    else:
        endRow = detectFooterRow(usernames) - 1

    if endRow - 1 < len(usernames) and endRow >= startRow:
        lastRow = usernames[endRow - 1] if usernames[endRow - 1] else []
        lastCell = str(lastRow[0]).strip() if lastRow else ""
        if not lastCell:
            endRow -= 1

    if endRow < startRow:
        return None
    return startRow, endRow


def sectionHeaderRows(usernames: list) -> list[int]:
    sectionHeaders = {
        normalize(item) for item in getattr(config, "recruitmentSectionHeaders", []) if item
    }
    if not sectionHeaders:
        return []
    rows: list[int] = []
    for rowIndex, row in enumerate(usernames, start=1):
        cell = str(row[0]).strip() if row else ""
        if normalize(cell) in sectionHeaders:
            rows.append(rowIndex)
    return rows


def membersRankOrderMap() -> dict[str, int]:
    order = getattr(config, "recruitmentMembersRankOrder", []) or []
    if not order:
        order = ["Recruitment Supervisor", "Lead Recruiter", "Senior Recruiter", "Recruiter"]
    return {normalize(rank): idx for idx, rank in enumerate(order)}


def isMembersRank(rank: str) -> bool:
    return normalize(rank) in membersRankOrderMap()


def isHighCommandRank(rank: str) -> bool:
    rankNorm = normalize(rank)
    return rankNorm in {
        normalize("Commissioner 1 IC"),
        normalize("Comissioner 1 IC"),
        normalize("Head Recruiter 1 IC"),
        normalize("Head Recruiter 2 IC"),
        normalize("Head Recruiter 3 IC"),
        normalize("Head Recruiter 4 IC"),
    }


def isManagerRank(rank: str) -> bool:
    return normalize(rank) == normalize("Recruitment Manager")


def normalizeQuotaStatus(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    allowed = getattr(config, "recruitmentQuotaStatusValues", []) or [
        "Completed",
        "Incomplete",
        "Excused",
        "Failed",
        "Exempt",
    ]
    byNorm = {normalize(item): str(item).strip() for item in allowed if str(item).strip()}
    return byNorm.get(normalize(raw), "")


def computeQuotaStatus(
    rank: str,
    monthlyPoints: int,
    patrolCount: int,
    currentQuotaStatus: str,
) -> str:
    existing = normalizeQuotaStatus(currentQuotaStatus)
    if existing in {"Excused", "Failed"}:
        return existing

    if isHighCommandRank(rank):
        return "Exempt"

    if isManagerRank(rank):
        requiredPatrols = int(getattr(config, "recruitmentManagerQuotaPatrols", 4) or 4)
        return "Completed" if patrolCount >= requiredPatrols else "Incomplete"

    if isMembersRank(rank):
        requiredPoints = int(getattr(config, "recruitmentEmployeeQuotaPoints", 4) or 4)
        return "Completed" if monthlyPoints >= requiredPoints else "Incomplete"

    return existing or "Incomplete"


def resolveConfiguredRankLabel(rankName: str) -> str:
    target = normalize(rankName)
    for item in getattr(config, "recruitmentAllowedRanks", []) or []:
        if normalize(item) == target:
            return str(item).strip()
    return rankName


def nextPromotionRank(currentRank: str, allTimePoints: int) -> Optional[str]:
    currentNorm = normalize(currentRank)
    recruiterNorm = normalize("Recruiter")
    seniorNorm = normalize("Senior Recruiter")
    leadNorm = normalize("Lead Recruiter")

    if currentNorm not in {recruiterNorm, seniorNorm, leadNorm}:
        return None

    toSeniorAt = int(getattr(config, "recruitmentPromoteRecruiterToSeniorAt", 10) or 10)
    toLeadAt = int(getattr(config, "recruitmentPromoteSeniorToLeadAt", 20) or 20)

    if allTimePoints >= toLeadAt and currentNorm in {recruiterNorm, seniorNorm}:
        return resolveConfiguredRankLabel("Lead Recruiter")
    if allTimePoints >= toSeniorAt and currentNorm == recruiterNorm:
        return resolveConfiguredRankLabel("Senior Recruiter")
    return None


def sectionInsertRow(usernames: list, sectionNames: list[str]) -> Optional[int]:
    for sectionName in sectionNames:
        bounds = sectionBoundsByHeader(usernames, sectionName)
        if bounds:
            _, sectionEndRow = bounds
            return sectionEndRow + 1

        headerRow = findSectionHeaderRow(usernames, sectionName)
        if headerRow:
            return headerRow + 2
    return None
