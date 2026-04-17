# sqlite.py

[`db/sqlite.py`](../../db/sqlite.py) is Jane's centralized SQLite layer.

It owns the database path, connection lifecycle, schema creation, lightweight migrations, indexes, and a few shared async helper functions used by almost every feature service.

## What It Handles

- stores Jane's main database at repo-root `bot.db`
- opens one shared `aiosqlite` connection
- enables connection pragmas like foreign keys, busy timeout, WAL, and normal synchronous mode
- creates all tables and hot-path indexes during startup
- tracks schema version with SQLite `PRAGMA user_version`
- exposes small query/write helpers for feature modules

## Startup Shape

`bot.py` calls `initDb()` through the bootstrap coordinator before cogs are loaded.

The important startup flow is:

1. `_getConnection()` opens or returns the shared connection.
2. `initDb()` takes `_dbWriteLock`.
3. Jane reads the current `PRAGMA user_version`.
4. `CREATE TABLE IF NOT EXISTS ...` statements create missing tables.
5. Old-column additions run through `_executeOptional(...)`.
6. indexes are created.
7. if the stored schema version is behind `_schemaVersionTarget`, Jane writes a row to `db_schema_migrations` and bumps `PRAGMA user_version`.

The current target is `_schemaVersionTarget = 12`.

## Main Helper Functions

### `fetchOne(query, params=())`

Runs a read query and returns one row as a plain `dict`, or `None`.

### `fetchAll(query, params=())`

Runs a read query and returns a list of plain `dict` rows.

### `execute(query, params=())`

Runs one write under `_dbWriteLock` and commits immediately.

### `executeReturnId(query, params=())`

Runs one insert/update under `_dbWriteLock`, commits, and returns `lastrowid`.

### `executeMany(query, paramsSeq)`

Runs a batch write under `_dbWriteLock`; returns early for an empty batch.

### `runWriteTransaction(callback)`

Runs a callback inside `BEGIN IMMEDIATE`, commits on success, and rolls back on exception.

Use this when a feature needs several related writes to succeed or fail together. The callback receives the raw `aiosqlite.Connection`, so it should stay narrow and should not call the higher-level helpers from inside the transaction.

### `closeDb()`

Closes and clears the shared connection during shutdown.

## Table Groups

The file is dense because almost every feature has at least one table here.

### Sessions / BG Checks / Orientation

- `sessions`
- `attendees`
- `bg_review_actions`

These back orientation sessions, certification sessions, BG queue routing, BG review decisions, and Roblox scan state. Orientation data lives here, not in the recruitment logging tables.

### Points

- `points`
- `points_pending`

`points_pending` is used when a review action credits points that should be processed later.

### Recruitment / ANRORS Logging

- `recruitment_submissions`
- `recruitment_time_submissions`
- `recruitment_patrol_sessions`
- `recruitment_patrol_attendees`

These are the ANRORS recruitment logging tables. New review messages use `config.recruitmentChannelId`, but each stored row keeps its own `guildId`, `channelId`, and `messageId`.

That matters during a recruitment logging server/channel move: future submissions follow config, while old rows still point to their original review messages. Orientation and BG review channel config are separate unless that infrastructure is intentionally moving too.

### ORBAT / LOA

- `orbat_requests`
- `loa_requests`

These store Discord-side request/review state. Sheet writes and layout details live in `features/staff/orbat`, `features/staff/departmentOrbat`, and `config.py`.

### Applications

- `division_applications`
- `division_application_events`
- `division_hub_messages`

These power division application submission, review, hub messages, and event history.

### Ribbons

- `ribbon_assets`
- `ribbon_profiles`
- `ribbon_requests`
- `ribbon_request_proofs`
- `ribbon_request_events`

These store scanned ribbon catalog metadata, user ribbon profiles, request state, proof attachments, and request event history.

### Training Logs / Event Ingest

- `john_event_log_messages`
- `training_result_logs`

These support John/event-log ingestion, training result mirroring, and host stats.

### Staff / Workflow Features

- `department_projects`
- `department_project_history`
- `anrd_payment_requests`
- `workflow_runs`
- `workflow_events`

`workflow_runs` and `workflow_events` are generic review-workflow tracking tables used by newer staff systems.

### Operations / Runtime

- `curfew_targets`
- `jail_records`
- `guild_feature_flags`
- `audit_events`
- `assistant_notes`
- `guild_federation_links`
- `retry_jobs`
- `db_schema_migrations`

These are mostly operational state, runtime auditability, feature flags, retry queue state, and migration bookkeeping.

### Community / Misc

- `scheduled_events`
- `scheduled_event_rsvps`
- `best_of_polls`
- `best_of_poll_candidates`
- `best_of_poll_votes`
- `best_of_poll_section_votes`
- `community_polls`
- `community_poll_votes`
- `reminders`
- `suggestions`
- `suggestion_status_boards`
- `guild_stats_snapshots`
- `guild_member_activity_daily`
- `guild_channel_activity_daily`
- `hall_reaction_posts`
- `silly_gambling_wallets`
- `silly_gambling_api_credits`
- `bunny_certification`

Some of these are legacy or oddball features, but they are still part of live database initialization unless the table is explicitly removed from the schema.

## Migration Notes

The migration style is intentionally simple:

- new tables use `CREATE TABLE IF NOT EXISTS`
- additive columns are usually done with `_executeOptional("ALTER TABLE ... ADD COLUMN ...")`
- schema version is only bumped at the end of `initDb()`

`_executeOptional(...)` catches all exceptions. That makes repeated startup safe when a column already exists, but it also means a typo in an optional migration can be silently ignored. For important migrations, prefer verifying locally with a fresh DB and an existing DB copy.

There is no down-migration system. Treat schema edits as forward-only.

## Things To Be Careful About

- Do not call write helpers from inside `runWriteTransaction(...)`; they will try to take `_dbWriteLock` again.
- Keep DB writes short. The shared write lock protects Jane from concurrent SQLite write errors, but long callbacks can stall unrelated features.
- Do not casually rename/drop columns. Many services read raw dict keys directly.
- Remember that several rows store Discord message locations. Moving a channel does not rewrite old `channelId` / `messageId` pairs.
- Back up `bot.db` before risky schema edits. Runtime backups exist, but development edits should still be deliberate.
- If a feature needs cross-table consistency, use `runWriteTransaction(...)` instead of separate `execute(...)` calls.

## Good Small Edits Here

- add a missing index for a proven slow lookup
- add a new table for a feature with a narrow service module
- add one additive column with a clear default
- move repeated multi-write feature logic into `runWriteTransaction(...)`
- document a table group here after touching it

