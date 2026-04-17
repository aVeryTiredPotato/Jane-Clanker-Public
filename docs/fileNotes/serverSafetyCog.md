# serverSafetyCog.py

[`cogs/operations/serverSafetyCog.py`](../../cogs/operations/serverSafetyCog.py) is the Discord-facing cog for server safety controls.

It owns the `/quarantine` and `/snapshot-menu` slash commands, persistent safety views, suspicious delete detection, quarantine entry/release, and snapshot restore UI.

## Load Path

This cog is private-extension loaded through:

- `plugins/private/serverSafetyExtension.py`
- `plugins/private/extensionList.py`

The underlying service functions live under:

- `features/operations/serverSafety/`

## Main Surfaces

- `/quarantine`
  Opens the quarantine control panel.

- `/snapshot-menu`
  Opens the snapshot create/preview/restore menu.

- `on_guild_channel_delete(...)`
  Records channel deletions and sends an incident alert after the configured threshold.

- `on_guild_role_delete(...)`
  Records role deletions and sends an incident alert after the configured threshold.

## Access And Safety Gates

Access is layered:

- Discord admin/manage-server permission
- `serverSafetyAllowedUserIds`
- destructive action gate for quarantine and restore actions
- feature config such as `serverSafetyQuarantineEnabled`

Snapshots and quarantine are intentionally separated into `_ensureSnapshotAccess(...)` and `_ensureQuarantineAccess(...)`.

## Quarantine State

The cog keeps short-lived in-memory state for:

- recent security events by guild
- quarantine locks
- cached quarantine state
- pending suspicious-activity incidents

Persistent quarantine state is saved through `features.operations.serverSafety.service`.

There are two quarantine scopes:

- member quarantine, which removes roles and may timeout the suspected member
- guild quarantine, which strips risky role permissions and locks writable channels

## Snapshot Flow

Snapshot menu actions delegate to the server-safety service:

- `describeGuildSnapshots(...)`
- `buildRestorePreview(...)`
- `createGuildSnapshot(...)`
- `applyGuildSnapshot(...)`

Restore is destructive and should stay behind the destructive gate.

## Things To Be Careful About

- Quarantine is currently disabled in config. Treat re-enable work as high-risk operational work.
- Permission hierarchy matters. The bot cannot edit roles above its top role or channels it cannot manage.
- Delete detection no longer auto-starts quarantine; it sends an alert with a manual start option.
- The alert channel falls back through several candidates. Changing this can make incident alerts disappear.
- Keep audit logging around quarantine and snapshot restore actions.
