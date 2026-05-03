from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

import discord

import config
from features.staff.sessions import bgScanPipeline
from features.staff.sessions.Roblox import robloxInventory, robloxProfiles, robloxUsers
from runtime import googleOAuth, orgProfiles, taskBudgeter

log = logging.getLogger(__name__)

inventoryLabelPrivate = "private"
inventoryLabelPublic = "public"
inventoryLabelUnknown = "unknown"


@dataclass(slots=True)
class BgSpreadsheetRow:
    discord_id: int
    roblox_user: str
    inventory: str
    no_rover: bool = False

    def sheet_values(self) -> list[str]:
        return [str(int(self.discord_id)), str(self.roblox_user or ""), str(self.inventory or "")]


@dataclass(slots=True)
class BgSpreadsheetResult:
    spreadsheet_id: str = ""
    title: str = ""
    url: str = ""
    rows: list[BgSpreadsheetRow] = field(default_factory=list)
    expected_channel_ids: list[int] = field(default_factory=list)
    posted_channel_ids: list[int] = field(default_factory=list)
    skipped_reason: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def private_count(self) -> int:
        return sum(1 for row in self.rows if row.inventory == inventoryLabelPrivate)

    @property
    def public_count(self) -> int:
        return sum(1 for row in self.rows if row.inventory == inventoryLabelPublic)

    @property
    def unknown_count(self) -> int:
        return sum(1 for row in self.rows if row.inventory == inventoryLabelUnknown)

    @property
    def no_rover_count(self) -> int:
        return sum(1 for row in self.rows if row.no_rover)


def _positiveInt(value: object, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return int(default)
    return parsed if parsed > 0 else int(default)


def _configValue(name: str, *, guildId: int = 0, default: object = None) -> object:
    return orgProfiles.getOrganizationValue(config, name, guildId=int(guildId or 0), default=default)


def _spreadsheetTemplateId(guildId: int = 0) -> str:
    return str(
        _configValue(
            "bgCheckSpreadsheetTemplateId",
            guildId=guildId,
            default=getattr(config, "bgCheckSpreadsheetTemplateId", ""),
        )
        or ""
    ).strip()


def _spreadsheetFolderId(guildId: int = 0) -> str:
    return str(
        _configValue(
            "bgCheckSpreadsheetFolderId",
            guildId=guildId,
            default=getattr(config, "bgCheckSpreadsheetFolderId", ""),
        )
        or ""
    ).strip()


def _spreadsheetSheetName(guildId: int = 0) -> str:
    return str(
        _configValue(
            "bgCheckSpreadsheetSheetName",
            guildId=guildId,
            default=getattr(config, "bgCheckSpreadsheetSheetName", "Sheet1"),
        )
        or "Sheet1"
    ).strip() or "Sheet1"


def _quotedSheetName(sheetName: str) -> str:
    safeName = str(sheetName or "Sheet1").replace("'", "''")
    return f"'{safeName}'"


def _uniqueUserIds(userIds: Iterable[object]) -> list[int]:
    normalized: list[int] = []
    seen: set[int] = set()
    for rawUserId in userIds or []:
        userId = _positiveInt(rawUserId)
        if userId <= 0 or userId in seen:
            continue
        seen.add(userId)
        normalized.append(userId)
    return normalized


def _spreadsheetTitle(titlePrefix: str) -> str:
    cleanPrefix = str(titlePrefix or "BGC Spreadsheet").strip() or "BGC Spreadsheet"
    stamp = datetime.now().strftime("%Y-%m-%d")
    return f"{cleanPrefix} {stamp}"


def _spreadsheetFailureMessage(exc: Exception) -> str:
    text = str(exc or "").strip()
    if isinstance(exc, FileNotFoundError) and "Google OAuth token file is missing" in text:
        return text
    if "Google OAuth token" in text:
        return text
    if "BGC spreadsheet template ID is not configured" in text:
        return text
    if text:
        return f"BGC spreadsheet creation failed: {text}"
    return "BGC spreadsheet creation failed."


async def _progressUpdate(
    progress: Any,
    *,
    stepIndex: int,
    detail: str,
    pendingCount: Optional[int] = None,
    finished: bool = False,
    failed: bool = False,
) -> None:
    if progress is None or not hasattr(progress, "update"):
        return
    await progress.update(
        stepIndex=stepIndex,
        detail=detail,
        pendingCount=pendingCount,
        finished=finished,
        failed=failed,
    )


async def _resolveRobloxUserText(lookup: robloxUsers.RoverLookupResult) -> str:
    username = str(lookup.robloxUsername or "").strip()
    if username:
        return username
    robloxId = _positiveInt(lookup.robloxId)
    if robloxId <= 0:
        return ""
    profile = await robloxProfiles.fetchRobloxUserProfile(robloxId)
    if not profile.error and profile.username:
        return str(profile.username).strip()
    return str(robloxId)


async def _inventoryLabel(robloxUserId: int) -> str:
    result = await robloxInventory.fetchRobloxInventory(int(robloxUserId), maxPages=1)
    if result.error:
        if bgScanPipeline.isPrivateInventoryStatus(int(result.status or 0), result.error):
            return inventoryLabelPrivate
        log.info(
            "BGC spreadsheet inventory probe for Roblox user %s returned unknown status %s: %s",
            robloxUserId,
            result.status,
            result.error,
        )
        return inventoryLabelUnknown
    return inventoryLabelPublic


async def buildRowsForUserIds(
    userIds: Iterable[object],
    *,
    sourceGuild: Optional[discord.Guild] = None,
    progress: Any = None,
) -> list[BgSpreadsheetRow]:
    normalizedUserIds = _uniqueUserIds(userIds)
    total = len(normalizedUserIds)
    rows: list[BgSpreadsheetRow] = []
    guildId = _positiveInt(getattr(sourceGuild, "id", 0))

    for index, userId in enumerate(normalizedUserIds, start=1):
        if index == 1 or index % 5 == 0 or index == total:
            await _progressUpdate(
                progress,
                stepIndex=3,
                detail=(
                    "Resolving Roblox accounts and inventory privacy...\n"
                    f"Checked: `{index - 1}/{total}`"
                ),
                pendingCount=total,
            )

        lookup = await robloxUsers.fetchRobloxUser(int(userId), guildId=guildId or None)
        if not lookup.robloxId:
            rows.append(
                BgSpreadsheetRow(
                    discord_id=int(userId),
                    roblox_user=str(lookup.robloxUsername or "").strip(),
                    inventory=inventoryLabelUnknown,
                    no_rover=True,
                )
            )
            continue

        robloxUserText = await _resolveRobloxUserText(lookup)
        rows.append(
            BgSpreadsheetRow(
                discord_id=int(userId),
                roblox_user=robloxUserText,
                inventory=await _inventoryLabel(int(lookup.robloxId)),
            )
        )

    return rows


def _copyTemplateAndWriteRows(
    *,
    title: str,
    rows: list[BgSpreadsheetRow],
    guildId: int = 0,
) -> tuple[str, str, str]:
    templateId = _spreadsheetTemplateId(guildId)
    if not templateId:
        raise RuntimeError("BGC spreadsheet template ID is not configured.")

    folderId = _spreadsheetFolderId(guildId)
    sheetName = _spreadsheetSheetName(guildId)

    drive = googleOAuth.buildService("drive", "v3")
    body: dict[str, Any] = {"name": title}
    if folderId:
        body["parents"] = [folderId]

    copied = (
        drive.files()
        .copy(
            fileId=templateId,
            body=body,
            supportsAllDrives=True,
            fields="id,name,webViewLink",
        )
        .execute()
    )
    spreadsheetId = str(copied.get("id") or "").strip()
    if not spreadsheetId:
        raise RuntimeError("Google Drive did not return a spreadsheet ID for the BGC copy.")

    drive.permissions().create(
        fileId=spreadsheetId,
        body={
            "type": "anyone",
            "role": "writer",
            "allowFileDiscovery": False,
        },
        supportsAllDrives=True,
        fields="id",
    ).execute()

    if rows:
        endRow = len(rows) + 1
        sheets = googleOAuth.buildService("sheets", "v4")
        sheets.spreadsheets().values().update(
            spreadsheetId=spreadsheetId,
            range=f"{_quotedSheetName(sheetName)}!E2:G{endRow}",
            valueInputOption="USER_ENTERED",
            body={"values": [row.sheet_values() for row in rows]},
        ).execute()

    copiedTitle = str(copied.get("name") or title)
    url = str(copied.get("webViewLink") or "").strip()
    if not url:
        url = f"https://docs.google.com/spreadsheets/d/{spreadsheetId}/edit"
    return spreadsheetId, copiedTitle, url


async def createSpreadsheetForUserIds(
    userIds: Iterable[object],
    *,
    sourceGuild: Optional[discord.Guild] = None,
    titlePrefix: str = "BGC Spreadsheet",
    guildId: int = 0,
    progress: Any = None,
) -> BgSpreadsheetResult:
    normalizedUserIds = _uniqueUserIds(userIds)
    if not normalizedUserIds:
        return BgSpreadsheetResult(skipped_reason="No users were provided for the BGC spreadsheet.")

    rows = await buildRowsForUserIds(
        normalizedUserIds,
        sourceGuild=sourceGuild,
        progress=progress,
    )
    title = _spreadsheetTitle(titlePrefix)
    await _progressUpdate(
        progress,
        stepIndex=4,
        detail="Copying the BGC template and writing columns E, F, and G...",
        pendingCount=len(rows),
    )
    try:
        spreadsheetId, copiedTitle, url = await taskBudgeter.runSheetsThread(
            _copyTemplateAndWriteRows,
            title=title,
            rows=rows,
            guildId=int(guildId or getattr(sourceGuild, "id", 0) or 0),
        )
    except Exception as exc:
        message = _spreadsheetFailureMessage(exc)
        if isinstance(exc, FileNotFoundError) or "Google OAuth token" in str(exc):
            log.warning("BGC spreadsheet creation unavailable: %s", message)
        else:
            log.exception("BGC spreadsheet creation failed.")
        await _progressUpdate(
            progress,
            stepIndex=5,
            detail=message,
            pendingCount=len(rows),
            failed=True,
        )
        return BgSpreadsheetResult(rows=rows, skipped_reason=message)
    return BgSpreadsheetResult(
        spreadsheet_id=spreadsheetId,
        title=copiedTitle,
        url=url,
        rows=rows,
    )
