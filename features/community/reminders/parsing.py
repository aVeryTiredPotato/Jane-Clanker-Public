from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from runtime import timezones as timezoneRuntime

_relativeReminderRegex = re.compile(
    r"^(?:in\s+)?(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$",
    re.IGNORECASE,
)


def _unitToTimedelta(amount: int, unit: str) -> timedelta:
    normalized = str(unit or "").strip().lower()
    if normalized.startswith("s"):
        return timedelta(seconds=amount)
    if normalized.startswith("m"):
        return timedelta(minutes=amount)
    if normalized.startswith("h"):
        return timedelta(hours=amount)
    if normalized.startswith("d"):
        return timedelta(days=amount)
    return timedelta(weeks=amount)


def parseReminderWhen(raw: str) -> tuple[datetime, str]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("Reminder time is required.")

    relativeMatch = _relativeReminderRegex.fullmatch(text)
    if relativeMatch:
        amount = int(relativeMatch.group(1))
        delta = _unitToTimedelta(amount, relativeMatch.group(2))
        remindAtUtc = datetime.now(timezone.utc) + delta
        return remindAtUtc, f"in {amount} {relativeMatch.group(2)}"

    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        raise ValueError(
            "Use a relative time like `10m` / `2h` / `3d`, or an absolute time like `2026-03-20 19:30 CST`."
        )

    dateText = ""
    timeAndTimezoneText = text
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
        dateText = parts[0]
        timeAndTimezoneText = str(parts[1] or "").strip()

    remindAtUtc, timezoneLabel = timezoneRuntime.parseDateTimeWithTimezone(
        dateText,
        timeAndTimezoneText,
        allowIana=False,
    )
    return remindAtUtc, timezoneLabel


def parseRecurringInterval(raw: str) -> int:
    text = str(raw or "").strip()
    if not text:
        return 0
    match = _relativeReminderRegex.fullmatch(text)
    if not match:
        raise ValueError("Repeat interval must look like `30m`, `6h`, `1d`, or `1w`.")
    amount = int(match.group(1))
    delta = _unitToTimedelta(amount, match.group(2))
    return max(0, int(delta.total_seconds()))
