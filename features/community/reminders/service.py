#  Credit to MintFlavour(@koni_mint)
from __future__ import annotations
import json
from db.sqlite import execute, executeReturnId, fetchAll, fetchOne

def _normalizeText(value: object) -> str:
    return str(value or "").strip()

def _normalizeRoleIds(values: list[int] | None) -> list[int]:
    return list(set([int(v) for v in (values or []) if str(v).isdigit() and int(v) > 0]))

async def createReminder(*, guildId: int, channelId: int, userId: int, reminderText: str, remindAtUtcIso: str, targetType: str = "USER", targetRoleIds: list[int] | None = None, recurringIntervalSec: int = 0, sourceReminderId: int | None = None) -> int:
    return await executeReturnId(
        "INSERT INTO reminders (guildId, channelId, userId, reminderText, remindAtUtc, targetType, targetRoleIdsJson, recurringIntervalSec, sourceReminderId, status, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', datetime('now'))",
        (int(guildId), int(channelId), int(userId), _normalizeText(reminderText), _normalizeText(remindAtUtcIso), _normalizeText(targetType).upper() or "USER", json.dumps(_normalizeRoleIds(targetRoleIds), ensure_ascii=True), max(0, int(recurringIntervalSec or 0)), int(sourceReminderId) if sourceReminderId is not None else None)
    )

async def getReminder(reminderId: int) -> dict | None:
    return await fetchOne("SELECT * FROM reminders WHERE reminderId = ?", (int(reminderId),))

async def listActiveRemindersForUser(guildId: int, userId: int) -> list[dict]:
    return await fetchAll("SELECT * FROM reminders WHERE guildId = ? AND userId = ? AND status = 'PENDING' ORDER BY datetime(remindAtUtc) ASC, reminderId ASC", (int(guildId), int(userId)))

async def listDueReminders(remindAtUtcIso: str, *, limit: int = 50) -> list[dict]:
    return await fetchAll("SELECT * FROM reminders WHERE status = 'PENDING' AND datetime(remindAtUtc) <= datetime(?) ORDER BY datetime(remindAtUtc) ASC, reminderId ASC LIMIT ?", (_normalizeText(remindAtUtcIso), max(1, int(limit or 50))))

async def markReminderSent(reminderId: int, *, dmDelivered: bool) -> None:
    await execute("UPDATE reminders SET status = 'SENT', dmDelivered = ?, sentAt = datetime('now'), updatedAt = datetime('now') WHERE reminderId = ?", (1 if dmDelivered else 0, int(reminderId)))

async def rescheduleReminder(reminderId: int, *, remindAtUtcIso: str) -> None:
    await execute("UPDATE reminders SET status = 'PENDING', remindAtUtc = ?, updatedAt = datetime('now'), sentAt = NULL WHERE reminderId = ?", (_normalizeText(remindAtUtcIso), int(reminderId)))

async def listSentReminders(*, limit: int = 50) -> list[dict]:
    return await fetchAll("SELECT * FROM reminders WHERE status = 'SENT' ORDER BY datetime(sentAt) DESC, reminderId DESC LIMIT ?", (max(1, int(limit or 50)),))

async def cancelReminder(reminderId: int) -> None:
    await execute("UPDATE reminders SET status = 'CANCELED', updatedAt = datetime('now') WHERE reminderId = ?", (int(reminderId),))
