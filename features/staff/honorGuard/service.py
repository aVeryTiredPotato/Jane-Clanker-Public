from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from db.sqlite import execute, executeReturnId


@dataclass(slots=True, frozen=True)
class HonorGuardConfig:
    enabled: bool
    reviewChannelId: int
    logChannelId: int
    archiveChannelId: int
    spreadsheetId: str
    memberSheetName: str
    scheduleSheetName: str
    archiveSheetName: str


@dataclass(slots=True, frozen=True)
class HonorGuardScaffoldStatus:
    config: HonorGuardConfig
    plannedDbTables: tuple[str, ...]
    plannedModules: tuple[str, ...]
    nextMilestones: tuple[str, ...]


def _normalizePositiveInt(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def loadHonorGuardConfig(*, configModule: Any) -> HonorGuardConfig:
    return HonorGuardConfig(
        enabled=bool(getattr(configModule, "honorGuardEnabled", False)),
        reviewChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardReviewChannelId", 0)),
        logChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardLogChannelId", 0)),
        archiveChannelId=_normalizePositiveInt(getattr(configModule, "honorGuardArchiveChannelId", 0)),
        spreadsheetId=str(getattr(configModule, "honorGuardSpreadsheetId", "") or "").strip(),
        memberSheetName=str(getattr(configModule, "honorGuardMemberSheetName", "") or "").strip(),
        scheduleSheetName=str(getattr(configModule, "honorGuardScheduleSheetName", "") or "").strip(),
        archiveSheetName=str(getattr(configModule, "honorGuardArchiveSheetName", "") or "").strip(),
    )


def buildScaffoldStatus(*, configModule: Any) -> HonorGuardScaffoldStatus:
    return HonorGuardScaffoldStatus(
        config=loadHonorGuardConfig(configModule=configModule),
        plannedDbTables=(
            "hg_submissions",
            "hg_submission_events",
            "hg_point_awards",
            "hg_attendance_records",
            "hg_sentry_logs",
            "hg_quota_cycles",
            "hg_event_records",
        ),
        plannedModules=(
            "cogs.staff.honorGuardCog",
            "features.staff.honorGuard.service",
            "features.staff.honorGuard",
        ),
        nextMilestones=(
            "Finalize DB schema ownership and add tables.",
            "Build approval workflow for manual awards and sentry logs.",
            "Add event clock-in flow and point calculation logic.",
            "Wire approved records into the Honor Guard sheet adapter.",
        ),
    )

def submitPoints(awardedId: int, submitterId: int, approverId: int, points: int):
    await execute("""
        INSERT INTO hg_point_awards(awardedId, submitterId, approverId, points, timestamp, status)
        VALUES (?, ?, ?, ?, datetime('now'), 'PENDING')
        """,
        (awardedId, submitterId, approverId, points)
    )

def approvePoints(timestamp: str, approverId: int):
    await execute("""
        UPDATE hg_point_awards
        SET status = 'APPROVED',
            approverId = ?
        WHERE timestamp = ?
    """, 
    (approverId, timestamp)
    )

def rejectPoints(timestamp: str, approverId: int):
    await execute("""
        UPDATE hg_point_awards
        SET status = 'REJECTED',
            approverId = ?
        WHERE timestamp = ?
    """,
    (approverId, timestamp)
    )

def createEvent(messageId: int, name: str, type: str, time: str, hostId: int, cohostsString: int, supervisorsString: int):
    eventId = await executeReturnId("""
        INSERT INTO hg_event(messageId, name, type, time, hostId, cohostsString, supervisorsString)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
    (messageId, name, type, time, hostId, cohostsString, supervisorsString)
    )
    return eventId
