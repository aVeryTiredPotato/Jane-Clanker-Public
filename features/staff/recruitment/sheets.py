from typing import Any, Optional, Dict

import config
from features.staff.recruitment import sheetRules
from features.staff.orbat.a1 import cellRange, columnIndex, indexToColumn
from features.staff.orbat.engineFacade import createEngineServiceFacade
from features.staff.orbat.multiEngine import getMultiOrbatEngine


_recruitmentSheetKey = "recruitment"
_engine = getMultiOrbatEngine()
_serviceFacade = None


def _getService():
    global _serviceFacade
    if _serviceFacade is None:
        _serviceFacade = createEngineServiceFacade(_engine, _recruitmentSheetKey)
    return _serviceFacade


def _spreadsheetId() -> str:
    value = _engine.getSpreadsheetId(_recruitmentSheetKey)
    if not value:
        raise RuntimeError("Missing recruitment spreadsheet ID.")
    return value


def _sheetName() -> str:
    return _engine.getSheetName(_recruitmentSheetKey)


_normalize = sheetRules.normalize
_cleanRobloxUsername = sheetRules.cleanRobloxUsername
_usernameLookupKey = sheetRules.usernameLookupKey
_usernameSortTuple = sheetRules.usernameSortTuple
_toInt = sheetRules.toInt
_toBool = sheetRules.toBool
_isRecruitmentMemberLabel = sheetRules.isRecruitmentMemberLabel
_isAllowedRecruitmentRank = sheetRules.isAllowedRecruitmentRank
_isWritableMemberRow = sheetRules.isWritableMemberRow
_findSectionHeaderRow = sheetRules.findSectionHeaderRow
_membersSectionHeaderCandidates = sheetRules.membersSectionHeaderCandidates
_findMembersSectionHeaderRow = sheetRules.findMembersSectionHeaderRow
_sectionBoundsByHeader = sheetRules.sectionBoundsByHeader
_sectionHeaderRows = sheetRules.sectionHeaderRows
_detectFooterRow = sheetRules.detectFooterRow
_membersRankOrderMap = sheetRules.membersRankOrderMap
_isMembersRank = sheetRules.isMembersRank
_isHighCommandRank = sheetRules.isHighCommandRank
_isManagerRank = sheetRules.isManagerRank
_normalizeQuotaStatus = sheetRules.normalizeQuotaStatus
_computeQuotaStatus = sheetRules.computeQuotaStatus
_resolveConfiguredRankLabel = sheetRules.resolveConfiguredRankLabel
_nextPromotionRank = sheetRules.nextPromotionRank
_sectionInsertRow = sheetRules.sectionInsertRow


def _columnLetter(index1: int) -> str:
    return indexToColumn(index1)


def _columnIndex(col: str) -> int:
    return columnIndex(col)


def _indexToColumn(index1: int) -> str:
    return indexToColumn(index1)


def _range(col: str, row: int) -> str:
    return cellRange(_sheetName(), col, row)


def _fillEmptyCellsWithZero(
    service,
    startRow: int,
    endRow: int,
    startCol: str = "E",
    endCol: str = "F",
    rowUsernames: Optional[list[str]] = None,
) -> int:
    if startRow <= 0 or endRow < startRow:
        return 0

    startIndex = _columnIndex(startCol)
    endIndex = _columnIndex(endCol)
    if startIndex <= 0 or endIndex <= 0:
        return 0
    if endIndex < startIndex:
        startIndex, endIndex = endIndex, startIndex

    rangeA1 = f"{_sheetName()}!{_indexToColumn(startIndex)}{startRow}:{_indexToColumn(endIndex)}{endRow}"
    values = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=rangeA1)
        .execute()
        .get("values", [])
    )

    data = []
    width = endIndex - startIndex + 1
    for rowOffset in range(endRow - startRow + 1):
        if rowUsernames is not None:
            username = rowUsernames[rowOffset] if rowOffset < len(rowUsernames) else ""
            if not str(username or "").strip():
                continue
        rowValues = values[rowOffset] if rowOffset < len(values) else []
        rowNumber = startRow + rowOffset
        for colOffset in range(width):
            cell = rowValues[colOffset] if colOffset < len(rowValues) else ""
            if str(cell).strip() != "":
                continue
            col = _indexToColumn(startIndex + colOffset)
            data.append({"range": _range(col, rowNumber), "values": [[0]]})

    if not data:
        return 0

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    return len(data)


def _zeroFillColumnRange(
    header: Dict[str, str],
    fallbackStart: str = "C",
    fallbackEnd: str = "E",
) -> tuple[str, str]:
    pointCols: list[tuple[int, str]] = []
    for key in ("monthly", "allTime", "patrols"):
        col = str(header.get(key, "") or "").strip().upper()
        idx = _columnIndex(col)
        if idx > 0:
            pointCols.append((idx, col))
    if not pointCols:
        return fallbackStart, fallbackEnd
    pointCols.sort(key=lambda item: item[0])
    return pointCols[0][1], pointCols[-1][1]


def _getSheetTabId(service) -> int:
    return _engine.getSheetTabId(_recruitmentSheetKey)


def _applyRecruitmentRowFormatting(service, row: int, sheetId: Optional[int] = None) -> bool:
    if row <= 0:
        return False
    if sheetId is None:
        sheetId = _getSheetTabId(service)
    rankUnmergeRange = {
        "sheetId": sheetId,
        "startRowIndex": row - 1,
        "endRowIndex": row,
        "startColumnIndex": 1,  # B
        "endColumnIndex": 4,    # D
    }
    # Best-effort cleanup to ensure rank is not merged beyond column B.
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=_spreadsheetId(),
            body={"requests": [{"unmergeCells": {"range": rankUnmergeRange}}]},
        ).execute()
    except Exception:
        pass

    try:
        requests = [
            {
                "updateBorders": {
                    "range": {
                        "sheetId": sheetId,
                        "startRowIndex": row - 1,
                        "endRowIndex": row,
                        "startColumnIndex": 4,   # E
                        "endColumnIndex": 9,     # I
                    },
                    "top": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                    "bottom": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                    "left": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                    "right": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                    "innerHorizontal": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                    "innerVertical": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheetId,
                        "startRowIndex": row - 1,
                        "endRowIndex": row,
                        "startColumnIndex": 4,   # E
                        "endColumnIndex": 9,     # I
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {
                                "bold": True,
                                "fontSize": 13,
                            },
                        }
                    },
                    "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.verticalAlignment,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize",
                }
            },
        ]
        service.spreadsheets().batchUpdate(
            spreadsheetId=_spreadsheetId(),
            body={"requests": requests},
        ).execute()
        return True
    except Exception:
        # Formatting should not block functional updates.
        return False


def _applyRecruitmentBlockFormatting(
    service,
    startRow: int,
    endRow: int,
    sheetId: Optional[int] = None,
) -> int:
    if startRow <= 0 or endRow < startRow:
        return 0
    if sheetId is None:
        sheetId = _getSheetTabId(service)

    rankUnmergeRange = {
        "sheetId": sheetId,
        "startRowIndex": startRow - 1,
        "endRowIndex": endRow,
        "startColumnIndex": 1,  # B
        "endColumnIndex": 4,    # D
    }
    styleRange = {
        "sheetId": sheetId,
        "startRowIndex": startRow - 1,
        "endRowIndex": endRow,
        "startColumnIndex": 4,   # E
        "endColumnIndex": 9,     # I
    }

    # Best-effort cleanup to ensure rank is not merged beyond column B.
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=_spreadsheetId(),
            body={"requests": [{"unmergeCells": {"range": rankUnmergeRange}}]},
        ).execute()
    except Exception:
        pass

    requests = [
        {
            "updateBorders": {
                "range": styleRange,
                "top": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                "bottom": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                "left": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                "right": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                "innerHorizontal": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
                "innerVertical": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
            }
        },
        {
            "repeatCell": {
                "range": styleRange,
                "cell": {
                    "userEnteredFormat": {
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "textFormat": {
                            "bold": True,
                            "fontSize": 13,
                        },
                    }
                },
                "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.verticalAlignment,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.fontSize",
            }
        },
    ]
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=_spreadsheetId(),
            body={"requests": requests},
        ).execute()
        return endRow - startRow + 1
    except Exception:
        return 0


def _applyRecruitmentRowsFormatting(
    service,
    startRow: int,
    endRow: int,
    sheetId: Optional[int] = None,
) -> int:
    return _applyRecruitmentBlockFormatting(service, startRow, endRow, sheetId=sheetId)


def _applyCheckboxValidation(
    service,
    header: Dict[str, str],
    startRow: int,
    endRow: int,
    sheetId: Optional[int] = None,
) -> int:
    # Legacy compatibility shim.
    # ANRORS no longer uses checkbox-backed boolean columns for quota/status.
    return 0


def _getMembersSectionBounds(
    service,
    usernameCol: str,
) -> Optional[tuple[list, int, int]]:
    usernames = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
        .execute()
        .get("values", [])
    )
    membersHeaderRow = _findMembersSectionHeaderRow(usernames)
    if not membersHeaderRow:
        return None

    bounds: Optional[tuple[int, int]] = None
    for sectionName in _membersSectionHeaderCandidates():
        bounds = _sectionBoundsByHeader(usernames, sectionName)
        if bounds:
            break
    if not bounds:
        return None
    startRow, endRow = bounds
    return usernames, startRow, endRow


def _trimTrailingNonMemberRows(
    service,
    header: Dict[str, str],
    usersAndBounds: tuple[list, int, int],
) -> tuple[list, int, int]:
    usernames, startRow, endRow = usersAndBounds
    if endRow < startRow:
        return usersAndBounds

    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    valueRanges = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=_spreadsheetId(),
            ranges=[
                f"{_sheetName()}!{usernameCol}{startRow}:{usernameCol}{endRow}",
                f"{_sheetName()}!{rankCol}{startRow}:{rankCol}{endRow}",
            ],
        )
        .execute()
        .get("valueRanges", [])
    )
    usernameRows = valueRanges[0].get("values", []) if len(valueRanges) > 0 else []
    rankRows = valueRanges[1].get("values", []) if len(valueRanges) > 1 else []

    span = endRow - startRow + 1
    trimmedEndRow = endRow
    for offset in range(span - 1, -1, -1):
        usernameRow = usernameRows[offset] if offset < len(usernameRows) else []
        rankRow = rankRows[offset] if offset < len(rankRows) else []
        usernameCell = str(usernameRow[0]).strip() if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        if _isWritableMemberRow(usernameCell, rankCell):
            break
        trimmedEndRow -= 1

    if trimmedEndRow < startRow:
        return usernames, startRow, startRow - 1
    return usernames, startRow, trimmedEndRow


def _ensureSectionSpacerRows(service, usernameCol: str) -> int:
    usernames = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
        .execute()
        .get("values", [])
    )
    sectionHeaders = {
        _normalize(item) for item in getattr(config, "recruitmentSectionHeaders", []) if item
    }
    if not sectionHeaders:
        return 0

    sheetTabId = _getSheetTabId(service)
    inserted = 0
    rowIndex = 1
    while rowIndex <= len(usernames):
        row = usernames[rowIndex - 1] if rowIndex - 1 < len(usernames) else []
        cell = str(row[0]).strip() if row else ""
        if _normalize(cell) not in sectionHeaders:
            rowIndex += 1
            continue

        # Keep one blank row above header.
        if rowIndex > 1:
            prevRow = usernames[rowIndex - 2] if rowIndex - 2 < len(usernames) else []
            prevCell = str(prevRow[0]).strip() if prevRow else ""
            if prevCell:
                service.spreadsheets().batchUpdate(
                    spreadsheetId=_spreadsheetId(),
                    body={
                        "requests": [
                            {
                                "insertDimension": {
                                    "range": {
                                        "sheetId": sheetTabId,
                                        "dimension": "ROWS",
                                        "startIndex": rowIndex - 1,
                                        "endIndex": rowIndex,
                                    },
                                    "inheritFromBefore": True,
                                }
                            }
                        ]
                    },
                ).execute()
                usernames.insert(rowIndex - 1, [])
                inserted += 1
                rowIndex += 1

        # Keep one blank row below header.
        belowIndex = rowIndex  # 0-based index of row immediately below header
        belowRow = usernames[belowIndex] if belowIndex < len(usernames) else []
        belowCell = str(belowRow[0]).strip() if belowRow else ""
        if belowIndex >= len(usernames) or belowCell:
            service.spreadsheets().batchUpdate(
                spreadsheetId=_spreadsheetId(),
                body={
                    "requests": [
                        {
                            "insertDimension": {
                                "range": {
                                    "sheetId": sheetTabId,
                                    "dimension": "ROWS",
                                    "startIndex": rowIndex,
                                    "endIndex": rowIndex + 1,
                                },
                                "inheritFromBefore": True,
                            }
                        }
                    ]
                },
            ).execute()
            usernames.insert(belowIndex, [])
            inserted += 1
        rowIndex += 1

    return inserted


def _fillSectionColumnsWithZero(
    service,
    header: Dict[str, str],
    sectionNames: str | list[str] | tuple[str, ...],
    startCol: Optional[str] = None,
    endCol: Optional[str] = None,
) -> int:
    if isinstance(sectionNames, str):
        sectionNameList = [sectionNames]
    else:
        sectionNameList = [str(item).strip() for item in sectionNames if str(item).strip()]
    if not sectionNameList:
        return 0

    usernameCol = header["robloxUsername"]
    usernames = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
        .execute()
        .get("values", [])
    )

    bounds: Optional[tuple[int, int]] = None
    selectedSectionName = ""
    for sectionName in sectionNameList:
        bounds = _sectionBoundsByHeader(usernames, sectionName)
        if bounds:
            selectedSectionName = sectionName
            break
    if not bounds:
        return 0

    if not startCol or not endCol:
        startCol, endCol = _zeroFillColumnRange(header)

    startRow, endRow = bounds
    rankCol = header.get("rsRank")
    rankRows: list[list[Any]] = []
    if rankCol:
        rankRows = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{rankCol}{startRow}:{rankCol}{endRow}")
            .execute()
            .get("values", [])
        )

    sectionNorm = _normalize(selectedSectionName)
    managersNorm = _normalize("Managers")
    employeesNorm = _normalize("Employees")
    membersNorm = _normalize("Members")
    highCommandNorm = _normalize("High Command")

    rowUsernames: list[str] = []
    for rowOffset, rowIndex in enumerate(range(startRow, endRow + 1)):
        row = usernames[rowIndex - 1] if rowIndex - 1 < len(usernames) else []
        usernameCell = str(row[0]).strip() if row else ""
        rankRow = rankRows[rowOffset] if rowOffset < len(rankRows) else []
        rankCell = str(rankRow[0]).strip() if rankRow else ""

        if sectionNorm == managersNorm:
            isWritable = _isRecruitmentMemberLabel(usernameCell) and _isManagerRank(rankCell)
        elif sectionNorm in {employeesNorm, membersNorm}:
            isWritable = _isRecruitmentMemberLabel(usernameCell) and _isMembersRank(rankCell)
        elif sectionNorm == highCommandNorm:
            isWritable = _isRecruitmentMemberLabel(usernameCell) and _isHighCommandRank(rankCell)
        else:
            isWritable = _isWritableMemberRow(usernameCell, rankCell)

        rowUsernames.append("1" if isWritable else "")

    return _fillEmptyCellsWithZero(
        service,
        startRow,
        endRow,
        str(startCol),
        str(endCol),
        rowUsernames=rowUsernames,
    )


def _insertMissingMemberRow(
    service,
    header: Dict[str, str],
    robloxUsername: str,
    *,
    sectionName: Optional[str] = None,
    rank: Optional[str] = None,
) -> Optional[int]:
    robloxUsername = _cleanRobloxUsername(robloxUsername)
    if not robloxUsername:
        return None
    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    _ensureSectionSpacerRows(service, usernameCol)
    usernames = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
        .execute()
        .get("values", [])
    )
    membersHeaderRow = _findMembersSectionHeaderRow(usernames)
    if not membersHeaderRow:
        return None

    targetSections = [str(sectionName).strip()] if sectionName and str(sectionName).strip() else _membersSectionHeaderCandidates()
    insertRow = _sectionInsertRow(usernames, targetSections)
    if not insertRow:
        footerRow = _detectFooterRow(usernames)
        insertRow = max(membersHeaderRow + 2, footerRow)

    sheetId = _getSheetTabId(service)

    service.spreadsheets().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={
            "requests": [
                {
                    "insertDimension": {
                        "range": {
                            "sheetId": sheetId,
                            "dimension": "ROWS",
                            "startIndex": insertRow - 1,
                            "endIndex": insertRow,
                        },
                        "inheritFromBefore": True,
                    }
                }
            ]
        },
    ).execute()

    defaultRank = _resolveConfiguredRankLabel(
        str(rank or getattr(config, "recruitmentNewMemberRank", "Recruiter") or "Recruiter")
    )
    defaultStatus = str(getattr(config, "recruitmentNewMemberStatus", "Active") or "Active").strip() or "Active"
    defaultQuota = _computeQuotaStatus(defaultRank, 0, 0, "")
    data = [
        {"range": _range(usernameCol, insertRow), "values": [[robloxUsername]]},
        {"range": _range(rankCol, insertRow), "values": [[defaultRank]]},
    ]
    if "monthly" in header:
        data.append({"range": _range(header["monthly"], insertRow), "values": [[0]]})
    if "allTime" in header:
        data.append({"range": _range(header["allTime"], insertRow), "values": [[0]]})
    if "patrols" in header:
        data.append({"range": _range(header["patrols"], insertRow), "values": [[0]]})
    if "quota" in header:
        data.append({"range": _range(header["quota"], insertRow), "values": [[defaultQuota]]})
    if "status" in header:
        data.append({"range": _range(header["status"], insertRow), "values": [[defaultStatus]]})
    if "loaExpiration" in header:
        data.append({"range": _range(header["loaExpiration"], insertRow), "values": [[""]]})
    if "notes" in header:
        data.append({"range": _range(header["notes"], insertRow), "values": [[""]]})

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    _applyRecruitmentRowFormatting(service, insertRow, sheetId=sheetId)
    return insertRow


def _deleteEmptyNameRowsInMembersSection(
    service,
    usernameCol: str,
    usersAndBounds: Optional[tuple[list, int, int]] = None,
) -> int:
    if usersAndBounds is None:
        usersAndBounds = _getMembersSectionBounds(service, usernameCol)
    if not usersAndBounds:
        return 0

    usernames, startRow, endRow = usersAndBounds
    emptyRows = []
    for rowIndex in range(startRow, endRow + 1):
        row = usernames[rowIndex - 1] if rowIndex - 1 < len(usernames) else []
        usernameCell = str(row[0]).strip() if row else ""
        if usernameCell:
            continue
        emptyRows.append(rowIndex)

    if not emptyRows:
        return 0

    sheetTabId = _getSheetTabId(service)
    deleteRequests = [
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheetTabId,
                    "dimension": "ROWS",
                    "startIndex": rowIndex - 1,
                    "endIndex": rowIndex,
                }
            }
        }
        for rowIndex in sorted(emptyRows, reverse=True)
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"requests": deleteRequests},
    ).execute()
    return len(emptyRows)


def _recruitmentMovableKeys(header: Dict[str, str]) -> list[str]:
    orderedKeys = [
        "robloxUsername",
        "rsRank",
        "monthly",
        "allTime",
        "patrols",
        "quota",
        "status",
        "loaExpiration",
        "notes",
    ]
    return [key for key in orderedKeys if key in header]


def _readMovableRows(
    service,
    header: Dict[str, str],
    movableKeys: list[str],
    startRow: int,
    endRow: int,
) -> list[dict[str, Any]]:
    if endRow < startRow or not movableKeys:
        return []

    ranges = [f"{_sheetName()}!{header[key]}{startRow}:{header[key]}{endRow}" for key in movableKeys]
    valueRanges = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=_spreadsheetId(), ranges=ranges)
        .execute()
        .get("valueRanges", [])
    )

    totalRows = endRow - startRow + 1
    outRows: list[dict[str, Any]] = []
    for offset in range(totalRows):
        rowData: dict[str, Any] = {}
        for idx, key in enumerate(movableKeys):
            values = valueRanges[idx].get("values", []) if idx < len(valueRanges) else []
            value = ""
            if offset < len(values) and values[offset]:
                value = values[offset][0]
            rowData[key] = value
        outRows.append(rowData)
    return outRows


def _writeMovableRows(
    service,
    header: Dict[str, str],
    movableKeys: list[str],
    startRow: int,
    rows: list[dict[str, Any]],
    rowOffsets: Optional[list[int]] = None,
) -> None:
    if not rows or not movableKeys:
        return

    if rowOffsets is None:
        offsets = list(range(len(rows)))
    else:
        offsets = sorted({int(offset) for offset in rowOffsets if 0 <= int(offset) < len(rows)})
    if not offsets:
        return

    data = []
    for offset in offsets:
        rowData = rows[offset]
        rowIndex = startRow + offset
        for key in movableKeys:
            data.append({"range": _range(header[key], rowIndex), "values": [[rowData.get(key, "")]]})
    if not data:
        return

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()


def _sortRowsPreserveNonSortableSlots(
    rows: list[dict[str, Any]],
    *,
    isSortable,
    sortKey,
) -> tuple[list[dict[str, Any]], int, bool]:
    if not rows:
        return rows, 0, False

    sortedRows = list(rows)
    totalSortable = 0
    changed = False

    # Sort only within contiguous sortable blocks.
    # Any non-sortable row (including header-like marker rows) acts as a hard boundary.
    idx = 0
    while idx < len(rows):
        if not isSortable(rows[idx]):
            idx += 1
            continue

        blockStart = idx
        blockRows: list[dict[str, Any]] = []
        while idx < len(rows) and isSortable(rows[idx]):
            blockRows.append(rows[idx])
            idx += 1

        totalSortable += len(blockRows)
        if len(blockRows) < 2:
            continue

        sortedBlockRows = sorted(blockRows, key=sortKey)
        if sortedBlockRows != blockRows:
            changed = True
            sortedRows[blockStart:idx] = sortedBlockRows

    return sortedRows, totalSortable, changed


def _changedRowOffsets(
    oldRows: list[dict[str, Any]],
    newRows: list[dict[str, Any]],
    movableKeys: list[str],
) -> list[int]:
    if not oldRows or not newRows or len(oldRows) != len(newRows):
        return []
    changed: list[int] = []
    keys = [key for key in movableKeys if key]
    if not keys:
        return changed
    for idx, (oldRow, newRow) in enumerate(zip(oldRows, newRows)):
        for key in keys:
            if str(oldRow.get(key, "")) != str(newRow.get(key, "")):
                changed.append(idx)
                break
    return changed


def _contiguousRanges(rowNumbers: list[int]) -> list[tuple[int, int]]:
    if not rowNumbers:
        return []
    rows = sorted({int(row) for row in rowNumbers if int(row) > 0})
    if not rows:
        return []
    out: list[tuple[int, int]] = []
    start = rows[0]
    prev = rows[0]
    for row in rows[1:]:
        if row == prev + 1:
            prev = row
            continue
        out.append((start, prev))
        start = row
        prev = row
    out.append((start, prev))
    return out


def _memberWritableRowNumbers(
    service,
    header: Dict[str, str],
    startRow: int,
    endRow: int,
) -> list[int]:
    if endRow < startRow:
        return []
    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    valueRanges = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=_spreadsheetId(),
            ranges=[
                f"{_sheetName()}!{usernameCol}{startRow}:{usernameCol}{endRow}",
                f"{_sheetName()}!{rankCol}{startRow}:{rankCol}{endRow}",
            ],
        )
        .execute()
        .get("valueRanges", [])
    )
    usernames = valueRanges[0].get("values", []) if len(valueRanges) > 0 else []
    ranks = valueRanges[1].get("values", []) if len(valueRanges) > 1 else []

    rowNumbers: list[int] = []
    span = endRow - startRow + 1
    for offset in range(span):
        usernameRow = usernames[offset] if offset < len(usernames) else []
        rankRow = ranks[offset] if offset < len(ranks) else []
        usernameCell = str(usernameRow[0]).strip() if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        if _isWritableMemberRow(usernameCell, rankCell):
            rowNumbers.append(startRow + offset)
    return rowNumbers


def _applyRecruitmentRowsFormattingForRowSet(
    service,
    rowNumbers: list[int],
    *,
    sheetId: Optional[int] = None,
) -> int:
    if not rowNumbers:
        return 0
    touched = 0
    for startRow, endRow in _contiguousRanges(rowNumbers):
        touched += _applyRecruitmentBlockFormatting(service, startRow, endRow, sheetId=sheetId)
    return touched


def _organizeMembersSectionRows(
    service,
    header: Dict[str, str],
    applyFormatting: bool = True,
    usersAndBounds: Optional[tuple[list, int, int]] = None,
) -> int:
    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    if usersAndBounds is None:
        usersAndBounds = _getMembersSectionBounds(service, usernameCol)
    if not usersAndBounds:
        return 0
    _, startRow, endRow = _trimTrailingNonMemberRows(service, header, usersAndBounds)
    if endRow < startRow:
        return 0

    movableKeys = _recruitmentMovableKeys(header)
    rows = _readMovableRows(service, header, movableKeys, startRow, endRow)
    if not rows:
        return 0

    rankOrder = _membersRankOrderMap()

    def _sortKey(rowData: dict):
        rankNorm = _normalize(rowData.get("rsRank", ""))
        username = _usernameSortTuple(rowData.get("robloxUsername", ""))
        return (rankOrder.get(rankNorm, 999), username)

    sortedRows, sortedCount, changed = _sortRowsPreserveNonSortableSlots(
        rows,
        isSortable=lambda rowData: (
            bool(str(rowData.get("robloxUsername", "")).strip())
            and _isMembersRank(str(rowData.get("rsRank", "")).strip())
        ),
        sortKey=_sortKey,
    )
    def _applyWritableFormatting() -> None:
        writableRows = _memberWritableRowNumbers(service, header, startRow, endRow)
        _applyRecruitmentRowsFormattingForRowSet(service, writableRows)

    if not changed:
        if applyFormatting:
            _applyWritableFormatting()
        return 0

    changedOffsets = _changedRowOffsets(rows, sortedRows, movableKeys)
    _writeMovableRows(service, header, movableKeys, startRow, sortedRows, rowOffsets=changedOffsets)
    if applyFormatting:
        _applyWritableFormatting()
    return sortedCount


def _loadHeaderMap(service) -> Dict[str, str]:
    out: Dict[str, str] = {}
    sheetConfig = _engine.getSheetConfig(_recruitmentSheetKey)
    rowModel = sheetConfig.rowModel if isinstance(sheetConfig.rowModel, dict) else {}
    identityModel = rowModel.get("identity") if isinstance(rowModel.get("identity"), dict) else {}
    pointModel = rowModel.get("pointColumns") if isinstance(rowModel.get("pointColumns"), dict) else {}

    identityColumn = str(identityModel.get("robloxUserColumn") or "").strip().upper()
    if identityColumn:
        out["robloxUsername"] = identityColumn

    pointColumnMap = {
        "monthly": str(pointModel.get("monthly") or "").strip().upper(),
        "allTime": str(pointModel.get("allTime") or "").strip().upper(),
        "patrols": str(pointModel.get("patrols") or "").strip().upper(),
    }
    for key, col in pointColumnMap.items():
        if col:
            out[key] = col

    rows = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!1:10")
        .execute()
        .get("values", [])
    )
    wanted = {
        "members": "robloxUsername",
        "usernames": "robloxUsername",
        "username": "robloxUsername",
        "robloxusername": "robloxUsername",
        "rank": "rsRank",
        "rsrank": "rsRank",
        "monthly": "monthly",
        "alltime": "allTime",
        "quota": "quota",
        "patrols": "patrols",
        "status": "status",
        "loaexpiration": "loaExpiration",
        "loaexpirationmd": "loaExpiration",
        "notes": "notes",
    }
    for row in rows:
        for idx, cell in enumerate(row, start=1):
            normalized = _normalize(cell)
            key = wanted.get(normalized)
            if not key:
                continue
            if key in out:
                continue
            out[key] = _columnLetter(idx)
    required = {"robloxUsername", "rsRank", "monthly", "allTime", "quota", "patrols", "status"}
    missing = [key for key in required if key not in out]
    if missing:
        raise RuntimeError(f"Recruitment sheet headers missing: {', '.join(missing)}")
    return out


def _findRowByRobloxUsername(service, usernameColumn: str, rankColumn: str, username: str) -> Optional[int]:
    valueRanges = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=_spreadsheetId(),
            ranges=[
                f"{_sheetName()}!{usernameColumn}:{usernameColumn}",
                f"{_sheetName()}!{rankColumn}:{rankColumn}",
            ],
        )
        .execute()
        .get("valueRanges", [])
    )
    usernames = valueRanges[0].get("values", []) if len(valueRanges) > 0 else []
    ranks = valueRanges[1].get("values", []) if len(valueRanges) > 1 else []
    target = _usernameLookupKey(username)
    totalRows = max(len(usernames), len(ranks))
    for idx in range(1, totalRows + 1):
        usernameRow = usernames[idx - 1] if idx - 1 < len(usernames) else []
        rankRow = ranks[idx - 1] if idx - 1 < len(ranks) else []
        usernameCell = _cleanRobloxUsername(usernameRow[0]) if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        if not _isWritableMemberRow(usernameCell, rankCell):
            continue
        if _usernameLookupKey(usernameCell) == target:
            return idx
    return None


def _roleRankOrderMap() -> Dict[str, int]:
    ordered = [str(item).strip() for item in (getattr(config, "recruitmentAllowedRanks", []) or []) if str(item).strip()]
    if not ordered:
        ordered = [
            "Commissioner 1 IC",
            "Comissioner 1 IC",
            "Head Recruiter 1 IC",
            "Head Recruiter 2 IC",
            "Head Recruiter 3 IC",
            "Head Recruiter 4 IC",
            "Recruitment Manager",
            "Recruitment Supervisor",
            "Lead Recruiter",
            "Senior Recruiter",
            "Recruiter",
        ]
    return {_normalize(rank): index for index, rank in enumerate(ordered)}


def _organizeSectionRowsByRankAndUsername(
    service,
    header: Dict[str, str],
    sectionName: str,
) -> int:
    usernameCol = header["robloxUsername"]
    boundsSource = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
        .execute()
        .get("values", [])
    )
    bounds = _sectionBoundsByHeader(boundsSource, sectionName)
    if not bounds:
        return 0
    startRow, endRow = bounds
    if endRow < startRow:
        return 0

    movableKeys = _recruitmentMovableKeys(header)
    rows = _readMovableRows(service, header, movableKeys, startRow, endRow)
    if not rows:
        return 0

    rankOrder = _roleRankOrderMap()
    rankDefault = len(rankOrder) + 100

    sortedRows, sortedCount, changed = _sortRowsPreserveNonSortableSlots(
        rows,
        isSortable=lambda rowData: (
            bool(str(rowData.get("robloxUsername", "")).strip())
            and _isAllowedRecruitmentRank(str(rowData.get("rsRank", "")).strip())
        ),
        sortKey=lambda rowData: (
            rankOrder.get(_normalize(rowData.get("rsRank", "")), rankDefault),
            _usernameSortTuple(rowData.get("robloxUsername", "")),
        ),
    )
    if not changed:
        return 0

    changedOffsets = _changedRowOffsets(rows, sortedRows, movableKeys)
    _writeMovableRows(service, header, movableKeys, startRow, sortedRows, rowOffsets=changedOffsets)
    return sortedCount


def _organizeAndFormatMembersSection(
    service,
    header: Dict[str, str],
    *,
    sheetId: Optional[int] = None,
    usersAndBounds: Optional[tuple[list, int, int]] = None,
) -> dict[str, int]:
    usernameCol = header["robloxUsername"]
    if usersAndBounds is None:
        usersAndBounds = _getMembersSectionBounds(service, usernameCol)
    if not usersAndBounds:
        return {"organized": 0, "zeroFilled": 0, "rows": 0, "startRow": 0, "endRow": 0}

    usersAndBounds = _trimTrailingNonMemberRows(service, header, usersAndBounds)
    _, startRow, endRow = usersAndBounds
    if endRow < startRow:
        return {"organized": 0, "zeroFilled": 0, "rows": 0, "startRow": startRow, "endRow": endRow}

    organized = _organizeMembersSectionRows(
        service,
        header,
        applyFormatting=False,
        usersAndBounds=usersAndBounds,
    )
    writableRows = _memberWritableRowNumbers(service, header, startRow, endRow)
    zeroFillStartCol, zeroFillEndCol = _zeroFillColumnRange(header)
    rowUsernamesMask: list[str] = []
    writableRowSet = set(writableRows)
    for rowNumber in range(startRow, endRow + 1):
        rowUsernamesMask.append("1" if rowNumber in writableRowSet else "")
    zeroFilled = _fillEmptyCellsWithZero(
        service,
        startRow,
        endRow,
        zeroFillStartCol,
        zeroFillEndCol,
        rowUsernames=rowUsernamesMask,
    )
    touchedRows = _applyRecruitmentRowsFormattingForRowSet(
        service,
        writableRows,
        sheetId=sheetId,
    )
    return {
        "organized": organized,
        "zeroFilled": zeroFilled,
        "rows": touchedRows,
        "startRow": startRow,
        "endRow": endRow,
    }


def _rowSectionName(usernames: list, rowIndex: int) -> Optional[str]:
    if rowIndex <= 0:
        return None
    sectionNames = [str(item).strip() for item in (getattr(config, "recruitmentSectionHeaders", []) or []) if str(item).strip()]
    if not sectionNames:
        sectionNames = ["High Command", "Managers", "Employees", "Members"]
    for sectionName in sectionNames:
        bounds = _sectionBoundsByHeader(usernames, sectionName)
        if not bounds:
            continue
        startRow, endRow = bounds
        if startRow <= rowIndex <= endRow:
            return sectionName
    return None


def _moveRowPreserveFormatting(
    service,
    sheetTabId: int,
    sourceRow: int,
    targetRow: int,
) -> int:
    if sourceRow <= 0 or targetRow <= 0 or sourceRow == targetRow:
        return sourceRow

    adjustedSourceRow = sourceRow + 1 if targetRow <= sourceRow else sourceRow
    requests = [
        {
            "insertDimension": {
                "range": {
                    "sheetId": sheetTabId,
                    "dimension": "ROWS",
                    "startIndex": targetRow - 1,
                    "endIndex": targetRow,
                },
                "inheritFromBefore": True,
            }
        },
        {
            "copyPaste": {
                "source": {
                    "sheetId": sheetTabId,
                    "startRowIndex": adjustedSourceRow - 1,
                    "endRowIndex": adjustedSourceRow,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9,
                },
                "destination": {
                    "sheetId": sheetTabId,
                    "startRowIndex": targetRow - 1,
                    "endRowIndex": targetRow,
                    "startColumnIndex": 0,
                    "endColumnIndex": 9,
                },
                "pasteType": "PASTE_NORMAL",
                "pasteOrientation": "NORMAL",
            }
        },
        {
            "deleteDimension": {
                "range": {
                    "sheetId": sheetTabId,
                    "dimension": "ROWS",
                    "startIndex": adjustedSourceRow - 1,
                    "endIndex": adjustedSourceRow,
                }
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"requests": requests},
    ).execute()
    return targetRow


def syncRecruitmentRolePlacement(
    robloxUsername: str,
    hasAnrorsMemberRole: bool,
    hasAnrorsRmPlusRole: bool,
    organizeAfter: bool = True,
) -> dict:
    username = _cleanRobloxUsername(robloxUsername)
    if not username:
        return {"ok": False, "reason": "missing-username"}

    service = _getService()
    header = _loadHeaderMap(service)
    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    sheetTabId = _getSheetTabId(service)

    _ensureSectionSpacerRows(service, usernameCol)
    usernames = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
        .execute()
        .get("values", [])
    )

    row = _findRowByRobloxUsername(service, usernameCol, rankCol, username)
    created = False
    if not row:
        if hasAnrorsRmPlusRole:
            row = _insertMissingMemberRow(
                service,
                header,
                username,
                sectionName="Managers",
                rank=_resolveConfiguredRankLabel("Recruitment Manager"),
            )
        elif hasAnrorsMemberRole:
            row = _insertMissingMemberRow(
                service,
                header,
                username,
                sectionName=_membersSectionHeaderCandidates()[0],
            )
        if row:
            created = True
            usernames = (
                service.spreadsheets()
                .values()
                .get(spreadsheetId=_spreadsheetId(), range=f"{_sheetName()}!{usernameCol}:{usernameCol}")
                .execute()
                .get("values", [])
            )
        else:
            return {
                "ok": True,
                "found": False,
                "created": False,
                "moved": False,
                "updated": False,
                "organized": 0,
                "hasAnrorsMemberRole": bool(hasAnrorsMemberRole),
                "hasAnrorsRmPlusRole": bool(hasAnrorsRmPlusRole),
            }

    if not row:
        return {
            "ok": True,
            "found": False,
            "created": False,
            "moved": False,
            "updated": False,
            "organized": 0,
            "hasAnrorsMemberRole": bool(hasAnrorsMemberRole),
            "hasAnrorsRmPlusRole": bool(hasAnrorsRmPlusRole),
        }

    sectionName = _rowSectionName(usernames, row) or ""
    sectionNorm = _normalize(sectionName)
    inHighCommand = sectionNorm == _normalize("High Command")
    inManagers = sectionNorm == _normalize("Managers")
    moved = False
    updated = False

    if hasAnrorsRmPlusRole and not inHighCommand and not inManagers:
        managerBounds = _sectionBoundsByHeader(usernames, "Managers")
        if managerBounds:
            managerStart, managerEnd = managerBounds
            targetRow = managerEnd + 1 if managerEnd >= managerStart else managerStart
            row = _moveRowPreserveFormatting(service, sheetTabId, row, targetRow)
            moved = True
            inManagers = True
            inHighCommand = False

    if hasAnrorsRmPlusRole and not inHighCommand:
        managerRank = _resolveConfiguredRankLabel("Recruitment Manager")
        quotaStatus = _computeQuotaStatus(managerRank, 0, 0, "")
        updateData = [
            {"range": _range(header["rsRank"], row), "values": [[managerRank]]},
        ]
        if "monthly" in header:
            updateData.append({"range": _range(header["monthly"], row), "values": [[0]]})
        if "allTime" in header:
            updateData.append({"range": _range(header["allTime"], row), "values": [[0]]})
        if "patrols" in header:
            updateData.append({"range": _range(header["patrols"], row), "values": [[0]]})
        if "quota" in header:
            updateData.append({"range": _range(header["quota"], row), "values": [[quotaStatus]]})
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=_spreadsheetId(),
            body={"valueInputOption": "USER_ENTERED", "data": updateData},
        ).execute()
        _applyRecruitmentRowFormatting(service, row, sheetId=sheetTabId)
        updated = True

    organized = 0
    if organizeAfter:
        organized += _organizeSectionRowsByRankAndUsername(service, header, "Managers")
        membersResult = _organizeAndFormatMembersSection(
            service,
            header,
            sheetId=sheetTabId,
        )
        organized += int(membersResult.get("organized", 0))
        zeroFillStartCol, zeroFillEndCol = _zeroFillColumnRange(header)
        _fillSectionColumnsWithZero(service, header, "Managers", zeroFillStartCol, zeroFillEndCol)

    return {
        "ok": True,
        "found": True,
        "created": created,
        "moved": moved,
        "updated": updated,
        "organized": organized,
        "row": row,
        "section": sectionName,
        "hasAnrorsMemberRole": bool(hasAnrorsMemberRole),
        "hasAnrorsRmPlusRole": bool(hasAnrorsRmPlusRole),
    }


def applyApprovedLog(
    robloxUsername: str,
    pointsDelta: int,
    patrolDelta: int,
    organizeAfter: bool = True,
) -> bool:
    robloxUsername = _cleanRobloxUsername(robloxUsername)
    if not robloxUsername:
        return False

    service = _getService()
    sheetId = _getSheetTabId(service)
    header = _loadHeaderMap(service)
    row = _findRowByRobloxUsername(
        service,
        header["robloxUsername"],
        header["rsRank"],
        robloxUsername,
    )
    if not row:
        row = _insertMissingMemberRow(service, header, robloxUsername)
        if not row:
            return False

    ranges = {
        "rsRank": _range(header["rsRank"], row),
        "monthly": _range(header["monthly"], row),
        "allTime": _range(header["allTime"], row),
        "patrols": _range(header["patrols"], row),
        "quota": _range(header["quota"], row),
    }
    current = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=_spreadsheetId(), ranges=list(ranges.values()))
        .execute()
        .get("valueRanges", [])
    )
    currentFormula = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=_spreadsheetId(),
            ranges=list(ranges.values()),
            valueRenderOption="FORMULA",
        )
        .execute()
        .get("valueRanges", [])
    )

    def _at(index: int):
        try:
            values = current[index].get("values", [])
            if not values or not values[0]:
                return ""
            return values[0][0]
        except Exception:
            return ""

    def _formulaAt(index: int):
        try:
            values = currentFormula[index].get("values", [])
            if not values or not values[0]:
                return ""
            return values[0][0]
        except Exception:
            return ""

    currentRank = str(_at(0) or "").strip()
    monthly = _toInt(_at(1)) + max(0, int(pointsDelta))
    allTime = _toInt(_at(2)) + max(0, int(pointsDelta))
    patrols = _toInt(_at(3)) + max(0, int(patrolDelta))
    currentQuotaStatus = str(_at(4) or "").strip()
    allTimeFormula = str(_formulaAt(2) or "").strip()
    promotedRank = _nextPromotionRank(currentRank, allTime)
    rankForQuota = promotedRank or currentRank
    quotaStatus = _computeQuotaStatus(rankForQuota, monthly, patrols, currentQuotaStatus)

    data = [
        {"range": _range(header["robloxUsername"], row), "values": [[robloxUsername]]},
        {"range": ranges["monthly"], "values": [[monthly]]},
        {"range": ranges["patrols"], "values": [[patrols]]},
        {"range": ranges["quota"], "values": [[quotaStatus]]},
    ]
    if not allTimeFormula.startswith("="):
        data.append({"range": ranges["allTime"], "values": [[allTime]]})
    if promotedRank and _normalize(promotedRank) != _normalize(currentRank):
        data.append({"range": ranges["rsRank"], "values": [[promotedRank]]})
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    zeroFillStartCol, zeroFillEndCol = _zeroFillColumnRange(header)
    _fillEmptyCellsWithZero(service, row, row, zeroFillStartCol, zeroFillEndCol)
    _applyRecruitmentRowFormatting(service, row, sheetId=sheetId)
    if organizeAfter:
        _ensureSectionSpacerRows(service, header["robloxUsername"])
        _organizeMembersSectionRows(service, header)
    return True


def _aggregateApprovedLogUpdates(updates: list[dict]) -> dict[str, dict[str, int | str]]:
    aggregate: dict[str, dict[str, int | str]] = {}
    for raw in updates:
        if not isinstance(raw, dict):
            continue
        username = _cleanRobloxUsername(raw.get("robloxUsername"))
        if not username:
            continue
        try:
            pointsDelta = int(raw.get("pointsDelta") or 0)
        except (TypeError, ValueError):
            pointsDelta = 0
        try:
            patrolDelta = int(raw.get("patrolDelta") or 0)
        except (TypeError, ValueError):
            patrolDelta = 0
        try:
            hostedPatrolDelta = int(raw.get("hostedPatrolDelta") or 0)
        except (TypeError, ValueError):
            hostedPatrolDelta = 0
        if pointsDelta == 0 and patrolDelta == 0 and hostedPatrolDelta == 0:
            continue
        key = _usernameLookupKey(username)
        slot = aggregate.get(key)
        if slot is None:
            aggregate[key] = {
                "robloxUsername": username,
                "pointsDelta": pointsDelta,
                "patrolDelta": patrolDelta,
                "hostedPatrolDelta": hostedPatrolDelta,
            }
        else:
            slot["pointsDelta"] = int(slot.get("pointsDelta", 0)) + pointsDelta
            slot["patrolDelta"] = int(slot.get("patrolDelta", 0)) + patrolDelta
            slot["hostedPatrolDelta"] = int(slot.get("hostedPatrolDelta", 0)) + hostedPatrolDelta
    return aggregate


def _loadWritableMemberRowsByUsername(service, sheetId: str, header: Dict[str, str]) -> dict[str, int]:
    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    usernameAndRank = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=sheetId,
            ranges=[
                f"{_sheetName()}!{usernameCol}:{usernameCol}",
                f"{_sheetName()}!{rankCol}:{rankCol}",
            ],
        )
        .execute()
        .get("valueRanges", [])
    )
    usernames = usernameAndRank[0].get("values", []) if len(usernameAndRank) > 0 else []
    ranks = usernameAndRank[1].get("values", []) if len(usernameAndRank) > 1 else []
    rowByUsername: dict[str, int] = {}
    totalRows = max(len(usernames), len(ranks))
    for idx in range(1, totalRows + 1):
        usernameRow = usernames[idx - 1] if idx - 1 < len(usernames) else []
        rankRow = ranks[idx - 1] if idx - 1 < len(ranks) else []
        usernameCell = _cleanRobloxUsername(usernameRow[0]) if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        if not _isWritableMemberRow(usernameCell, rankCell):
            continue
        rowByUsername[_usernameLookupKey(usernameCell)] = idx
    return rowByUsername


def listWritableRobloxUsernames() -> list[str]:
    service = _getService()
    header = _loadHeaderMap(service)
    sheetId = _spreadsheetId()
    usernameCol = header["robloxUsername"]
    rankCol = header["rsRank"]
    usernameAndRank = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=sheetId,
            ranges=[
                f"{_sheetName()}!{usernameCol}:{usernameCol}",
                f"{_sheetName()}!{rankCol}:{rankCol}",
            ],
        )
        .execute()
        .get("valueRanges", [])
    )
    usernames = usernameAndRank[0].get("values", []) if len(usernameAndRank) > 0 else []
    ranks = usernameAndRank[1].get("values", []) if len(usernameAndRank) > 1 else []
    out: list[str] = []
    totalRows = max(len(usernames), len(ranks))
    for idx in range(1, totalRows + 1):
        usernameRow = usernames[idx - 1] if idx - 1 < len(usernames) else []
        rankRow = ranks[idx - 1] if idx - 1 < len(ranks) else []
        usernameCell = _cleanRobloxUsername(usernameRow[0]) if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        if _isWritableMemberRow(usernameCell, rankCell):
            out.append(usernameCell)
    return out


def _resolveApprovedLogRows(
    service,
    header: Dict[str, str],
    aggregate: dict[str, dict[str, int | str]],
    rowByUsername: dict[str, int],
) -> dict[int, dict[str, int | str]]:
    updatesByRow: dict[int, dict[str, int | str]] = {}
    for key, entry in aggregate.items():
        row = rowByUsername.get(key)
        if not row:
            row = _insertMissingMemberRow(service, header, str(entry["robloxUsername"]))
            if not row:
                continue
            rowByUsername[key] = row
        entry["robloxUsername"] = _cleanRobloxUsername(entry.get("robloxUsername"))
        updatesByRow[row] = entry
    return updatesByRow


def _loadApprovedLogCurrentRows(
    service,
    sheetId: str,
    header: Dict[str, str],
    rows: list[int],
) -> dict[int, dict[str, str]]:
    perRowRanges: list[str] = []
    rangeMeta: list[tuple[int, str]] = []
    keys = ("rsRank", "monthly", "allTime", "patrols", "quota")
    for row in rows:
        for key in keys:
            perRowRanges.append(_range(header[key], row))
            rangeMeta.append((row, key))

    fetchedRanges = (
        service.spreadsheets()
        .values()
        .batchGet(spreadsheetId=sheetId, ranges=perRowRanges)
        .execute()
        .get("valueRanges", [])
    )
    fetchedFormulaRanges = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=sheetId,
            ranges=perRowRanges,
            valueRenderOption="FORMULA",
        )
        .execute()
        .get("valueRanges", [])
    )

    currentByRow: dict[int, dict[str, str]] = {}
    for idx, (row, key) in enumerate(rangeMeta):
        values = fetchedRanges[idx].get("values", []) if idx < len(fetchedRanges) else []
        value = values[0][0] if values and values[0] else ""
        rowData = currentByRow.setdefault(row, {})
        rowData[key] = str(value)
        formulaValues = (
            fetchedFormulaRanges[idx].get("values", [])
            if idx < len(fetchedFormulaRanges)
            else []
        )
        formulaValue = formulaValues[0][0] if formulaValues and formulaValues[0] else ""
        if isinstance(formulaValue, str) and formulaValue.strip().startswith("="):
            rowData[f"{key}Formula"] = formulaValue.strip()
    return currentByRow


def _buildApprovedLogBatchData(
    header: Dict[str, str],
    rows: list[int],
    currentByRow: dict[int, dict[str, str]],
    updatesByRow: dict[int, dict[str, int | str]],
) -> tuple[list[dict], list[int]]:
    batchData: list[dict] = []
    touchedRows: list[int] = []
    for row in rows:
        current = currentByRow.get(row, {})
        entry = updatesByRow[row]
        currentRank = str(current.get("rsRank", "")).strip()
        isRmPlus = _isManagerRank(currentRank) or _isHighCommandRank(currentRank)
        monthly = _toInt(current.get("monthly", "")) + max(0, int(entry.get("pointsDelta", 0)))
        allTime = _toInt(current.get("allTime", "")) + max(0, int(entry.get("pointsDelta", 0)))
        patrolDeltaValue = max(0, int(entry.get("patrolDelta", 0)))
        hostedDeltaValue = max(0, int(entry.get("hostedPatrolDelta", 0)))
        effectivePatrolDelta = hostedDeltaValue if isRmPlus else patrolDeltaValue
        patrols = _toInt(current.get("patrols", "")) + effectivePatrolDelta
        currentQuotaStatus = str(current.get("quota", "")).strip()
        promotedRank = _nextPromotionRank(currentRank, allTime)
        rankForQuota = promotedRank or currentRank
        quotaStatus = _computeQuotaStatus(rankForQuota, monthly, patrols, currentQuotaStatus)

        batchData.append({"range": _range(header["robloxUsername"], row), "values": [[entry["robloxUsername"]]]})
        batchData.append({"range": _range(header["monthly"], row), "values": [[monthly]]})
        if not str(current.get("allTimeFormula", "")).strip().startswith("="):
            batchData.append({"range": _range(header["allTime"], row), "values": [[allTime]]})
        batchData.append({"range": _range(header["patrols"], row), "values": [[patrols]]})
        batchData.append({"range": _range(header["quota"], row), "values": [[quotaStatus]]})
        if promotedRank and _normalize(promotedRank) != _normalize(currentRank):
            batchData.append({"range": _range(header["rsRank"], row), "values": [[promotedRank]]})
        touchedRows.append(row)
    return batchData, touchedRows


def applyApprovedLogsBatch(
    updates: list[dict],
    organizeAfter: bool = True,
) -> dict:
    if not updates:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0}

    aggregate = _aggregateApprovedLogUpdates(updates)
    if not aggregate:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0}

    service = _getService()
    sheetId = _spreadsheetId()
    sheetTabId = _getSheetTabId(service)
    header = _loadHeaderMap(service)

    rowByUsername = _loadWritableMemberRowsByUsername(service, sheetId, header)
    updatesByRow = _resolveApprovedLogRows(service, header, aggregate, rowByUsername)
    if not updatesByRow:
        return {"updatedUsers": 0, "updatedRows": 0, "organized": 0}

    rows = sorted(updatesByRow.keys())
    currentByRow = _loadApprovedLogCurrentRows(service, sheetId, header, rows)
    batchData, touchedRows = _buildApprovedLogBatchData(header, rows, currentByRow, updatesByRow)

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=sheetId,
        body={"valueInputOption": "USER_ENTERED", "data": batchData},
    ).execute()

    startRow = min(touchedRows)
    endRow = max(touchedRows)
    zeroFillStartCol, zeroFillEndCol = _zeroFillColumnRange(header)
    _fillEmptyCellsWithZero(service, startRow, endRow, zeroFillStartCol, zeroFillEndCol)
    _applyRecruitmentRowsFormatting(service, startRow, endRow, sheetId=sheetTabId)

    organized = 0
    if organizeAfter:
        _ensureSectionSpacerRows(service, header["robloxUsername"])
        organized = _organizeMembersSectionRows(service, header)
    return {
        "updatedUsers": len(updatesByRow),
        "updatedRows": len(touchedRows),
        "organized": organized,
    }


def resetMonthlyPoints() -> dict:
    service = _getService()
    header = _loadHeaderMap(service)
    usernameCol = header["robloxUsername"]
    monthlyCol = header["monthly"]
    rankCol = header["rsRank"]
    patrolCol = header["patrols"]
    quotaCol = header["quota"]

    valueRanges = (
        service.spreadsheets()
        .values()
        .batchGet(
            spreadsheetId=_spreadsheetId(),
            ranges=[
                f"{_sheetName()}!{usernameCol}:{usernameCol}",
                f"{_sheetName()}!{rankCol}:{rankCol}",
                f"{_sheetName()}!{patrolCol}:{patrolCol}",
                f"{_sheetName()}!{quotaCol}:{quotaCol}",
            ],
        )
        .execute()
        .get("valueRanges", [])
    )
    usernames = valueRanges[0].get("values", []) if len(valueRanges) > 0 else []
    ranks = valueRanges[1].get("values", []) if len(valueRanges) > 1 else []
    patrols = valueRanges[2].get("values", []) if len(valueRanges) > 2 else []
    quotas = valueRanges[3].get("values", []) if len(valueRanges) > 3 else []

    data = []
    totalRows = max(len(usernames), len(ranks), len(patrols), len(quotas))
    for rowIndex in range(1, totalRows + 1):
        usernameRow = usernames[rowIndex - 1] if rowIndex - 1 < len(usernames) else []
        rankRow = ranks[rowIndex - 1] if rowIndex - 1 < len(ranks) else []
        patrolRow = patrols[rowIndex - 1] if rowIndex - 1 < len(patrols) else []
        quotaRow = quotas[rowIndex - 1] if rowIndex - 1 < len(quotas) else []
        usernameCell = str(usernameRow[0]).strip() if usernameRow else ""
        rankCell = str(rankRow[0]).strip() if rankRow else ""
        patrolCell = _toInt(patrolRow[0]) if patrolRow else 0
        quotaCell = str(quotaRow[0]).strip() if quotaRow else ""
        if not _isWritableMemberRow(usernameCell, rankCell):
            continue
        data.append({"range": _range(monthlyCol, rowIndex), "values": [[0]]})
        quotaStatus = _computeQuotaStatus(rankCell, 0, patrolCell, quotaCell)
        data.append({"range": _range(quotaCol, rowIndex), "values": [[quotaStatus]]})

    if not data:
        return {"rows": 0}

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=_spreadsheetId(),
        body={"valueInputOption": "USER_ENTERED", "data": data},
    ).execute()
    return {"rows": len(data) // 2}


def touchupRecruitmentRows() -> dict:
    service = _getService()
    sheetId = _getSheetTabId(service)
    header = _loadHeaderMap(service)
    zeroFillStartCol, zeroFillEndCol = _zeroFillColumnRange(header)
    spacerRowsAdded = _ensureSectionSpacerRows(service, header["robloxUsername"])
    managersZeroFilled = _fillSectionColumnsWithZero(
        service,
        header,
        "Managers",
        zeroFillStartCol,
        zeroFillEndCol,
    )
    employeesZeroFilled = _fillSectionColumnsWithZero(
        service,
        header,
        _membersSectionHeaderCandidates(),
        zeroFillStartCol,
        zeroFillEndCol,
    )
    usersAndBounds = _getMembersSectionBounds(service, header["robloxUsername"])
    if not usersAndBounds:
        return {
            "rows": 0,
            "organized": 0,
            "deletedRows": 0,
            "spacerRowsAdded": spacerRowsAdded,
            "managersZeroFilled": managersZeroFilled,
            "employeesZeroFilled": employeesZeroFilled,
        }
    deletedRows = _deleteEmptyNameRowsInMembersSection(
        service,
        header["robloxUsername"],
        usersAndBounds=usersAndBounds,
    )
    if deletedRows:
        usersAndBounds = _getMembersSectionBounds(service, header["robloxUsername"])
        if not usersAndBounds:
            return {"rows": 0, "organized": 0, "deletedRows": deletedRows}
    membersResult = _organizeAndFormatMembersSection(
        service,
        header,
        sheetId=sheetId,
        usersAndBounds=usersAndBounds,
    )
    startRow = int(membersResult.get("startRow", 0))
    endRow = int(membersResult.get("endRow", 0))
    if endRow < startRow:
        return {
            "rows": 0,
            "organized": 0,
            "deletedRows": deletedRows,
            "spacerRowsAdded": spacerRowsAdded,
            "managersZeroFilled": managersZeroFilled,
            "employeesZeroFilled": employeesZeroFilled,
            "zeroFilled": 0,
        }
    organized = int(membersResult.get("organized", 0))
    zeroFilled = int(membersResult.get("zeroFilled", 0))
    touchedRows = int(membersResult.get("rows", 0))

    return {
        "rows": touchedRows,
        "organized": organized,
        "deletedRows": deletedRows,
        "spacerRowsAdded": spacerRowsAdded,
        "managersZeroFilled": managersZeroFilled,
        "employeesZeroFilled": employeesZeroFilled,
        "zeroFilled": zeroFilled,
        "startRow": startRow,
        "endRow": endRow,
    }

