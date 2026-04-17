# recruitment/sheets.py

[`features/staff/recruitment/sheets.py`](../../features/staff/recruitment/sheets.py) is the Google Sheets writer for the recruitment roster.

It handles row lookup, point updates, quota status, promotions, section cleanup, formatting, and row sorting for the recruitment spreadsheet.

## Main Entry Points

- `applyApprovedLog(...)`
  Applies one approved recruitment/application-style update for a Roblox username.

- `applyApprovedLogsBatch(...)`
  Aggregates several point/patrol updates by username, then writes them in fewer Sheets calls.

- `syncRecruitmentRolePlacement(...)`
  Moves or updates a row based on ANRORS member/RM+ role state.

- `resetMonthlyPoints()`
  Resets monthly points and recomputes quota state.

- `touchupRecruitmentRows()`
  Housekeeping pass for spacer rows, zero fills, empty rows, sorting, and formatting.

Known callers include:

- `cogs/staff/recruitmentViews.py`
- `features/staff/applications/cogMixins/flowMixin.py`
- `features/staff/orbat/roleSync.py`
- `features/staff/sessions/postActions.py`
- `runtime/maintenance.py`

## Sheet Model

The file uses the ORBAT multi-engine with the sheet key `recruitment`.

Important assumptions:

- spreadsheet ID and tab name come from the ORBAT engine config
- `_loadHeaderMap(...)` combines configured row-model columns with headers found in the first 10 rows
- missing required headers raise `RuntimeError`
- rank labels, quota thresholds, section headers, and promotion thresholds come from `config.py`

Required header keys are:

- `robloxUsername`
- `rsRank`
- `monthly`
- `allTime`
- `quota`
- `patrols`
- `status`

## Sections

The sheet is treated as a set of named roster sections.

Common section names are:

- `High Command`
- `Managers`
- `Employees`
- `Members`

The code finds section bounds by scanning the username column for configured section headers. A header-like row or non-member row often acts as a boundary, so be careful when changing `_isRecruitmentMemberLabel(...)`, `_findMembersSectionHeaderRow(...)`, or `_sectionBoundsByHeader(...)`.

## Row Movement

There are two row movement styles:

- rewrite sortable row values in place for normal section organization
- insert/copy/delete a row to preserve formatting when moving one member between sections

The preservation path is `_moveRowPreserveFormatting(...)`, used by `syncRecruitmentRolePlacement(...)`.

## Formatting

Formatting is best-effort and should not block functional data writes.

Main helpers:

- `_applyRecruitmentRowFormatting(...)`
- `_applyRecruitmentBlockFormatting(...)`
- `_applyRecruitmentRowsFormattingForRowSet(...)`

They mostly touch columns `E:I`, unmerge rank spillover around `B:D`, and apply border/alignment/font rules.

## Quota And Promotion Logic

Quota state comes from `_computeQuotaStatus(...)`.

Important behavior:

- `Excused` and `Failed` are preserved
- high command is `Exempt`
- managers use patrol quota
- member ranks use monthly point quota
- promotion checks use all-time points

`applyApprovedLogsBatch(...)` also treats `hostedPatrolDelta` differently for RM+ rows.

## Things To Be Careful About

- This file mutates live Sheets state. Test against a copy or test sheet when changing row movement, deletion, or formatting.
- Header detection is intentionally flexible. Tightening normalization can break existing sheets with slightly different labels.
- Batch updates are optimized around row ranges. Changing sort behavior can increase API writes quickly.
- Recruitment logging channel/server changes usually belong in config/org-profile code or upstream recruitment services, not here, unless the spreadsheet key or sheet shape changed.
