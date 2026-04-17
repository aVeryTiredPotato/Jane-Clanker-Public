# Server Recovery

This is the "oh no, fix the server" map for Jane's snapshot-based recovery system.

The goal is not perfection. Discord will not let a bot perfectly rebuild every possible thing. The goal is to keep enough structure saved that Jane can rebuild the important parts: roles, channels, permissions, and member role assignments.

## Where It Lives

- `cogs/operations/serverSafetyCog.py`
- `features/operations/serverSafety/snapshotStore.py`
- `features/operations/serverSafety/snapshotRestore.py`
- `features/operations/serverSafety/restoreRoles.py`
- `features/operations/serverSafety/restoreChannels.py`
- `features/operations/serverSafety/restoreMembers.py`
- `features/operations/serverSafety/preview.py`
- `runtime/maintenance.py`

## Snapshot Contents

A snapshot stores the boring-but-critical stuff:

- guild ID and name
- role names, permissions, colors, hoist state, mentionable state, managed/default flags, and position
- member IDs, display names, and non-managed role IDs
- category, text, forum, voice, and stage channel metadata
- channel permission overwrites for roles and members
- snapshot kind and label
- offsite mirror metadata when enabled

Snapshots intentionally skip channels filtered by the server-safety filters.

## Snapshot Storage

Local snapshots are stored under:

- `backups/serverSnapshots`

Offsite mirrors are stored under:

- `backups/serverSnapshotsOffsite`

The exact directories can be overridden with:

- `serverSafetySnapshotDir`
- `serverSafetyOffsiteSnapshotDir`

## Retention

Retention is intentionally small, because hoarding stale recovery files is how you end up confidently restoring the wrong disaster:

- `serverSafetyWeeklySnapshotKeepCount`
  Defaults to `2`.

- `serverSafetyManualSnapshotKeepCount`
  Defaults to `1`.

This is deliberate. The weekly flow keeps two snapshots so a bad weekly snapshot does not immediately erase the previous good one.

## Weekly Snapshots

Weekly snapshots run from `runtime/maintenance.py`.

Jane chooses target guilds in this order:

1. `serverSafetyWeeklySnapshotGuildIds`
2. `allowedCommandGuildIds`
3. every guild Jane is in

Weekly snapshots do not run while Jane is paused.

## Manual Snapshots

Use `/snapshot-menu`.

The menu is the human-friendly surface for:

- show known snapshots
- create a manual snapshot
- preview a restore
- restore the selected snapshot

Snapshot controls require:

- administrator or manage-server permission
- a user ID allowed by `serverSafetyAllowedUserIds`, if configured

Restore actions also go through the destructive action gate.

## Restore Flow

Snapshot restore is basically:

1. Reads the selected snapshot.
2. Restores or maps roles.
3. Restores categories.
4. Restores channels.
5. Finalizes category and channel order.
6. Restores member role assignments.
7. Returns counts and failure details.

By default, snapshot restore does not delete extra roles or channels. Cleanup is a separate dangerous mode in the lower-level restore helper and should not be casually enabled.

## Restore Limits

Discord hierarchy rules still apply.

Jane cannot magic past Discord. She still cannot:

- edit roles above her highest role
- assign roles above her highest role
- manage channels she cannot see or manage
- restore managed roles created by integrations or bots
- perfectly restore every Discord setting if the API does not expose it

Partial success is normal. A restore can map most roles and channels while still reporting some failures.

## Emergency Checklist

If the server is actively on fire, do this slowly and deliberately:

1. Pause Jane if other automation might make the situation worse.
2. Open `/snapshot-menu` in the damaged guild.
3. Select the newest known-good snapshot.
4. Use preview before restore.
5. Confirm the snapshot is for the same guild.
6. Run restore only from an authorized recovery account.
7. Read the returned counts for role, channel, category, and member changes.
8. Check audit logs and `logs/general-errors.log`.
9. Create a new manual snapshot only after the server is stable again.

## Config Checklist

- `serverSafetyAlertChannelId`
- `serverSafetyAlertRoleId`
- `serverSafetySnapshotDir`
- `serverSafetyOffsiteSnapshotDir`
- `serverSafetyOffsiteSnapshotsEnabled`
- `serverSafetyWeeklySnapshotKeepCount`
- `serverSafetyManualSnapshotKeepCount`
- `serverSafetyWeeklySnapshotGuildIds`
- `serverSafetyAllowedUserIds`
- `serverSafetyIgnoredCategoryIds`
- `serverSafetyPreservedChannelIds`

## Quarantine Note

Quarantine is separate from snapshot recovery.

It is currently disabled by `serverSafetyQuarantineEnabled = False`. Treat any re-enable work as high-risk operational work, not a cute toggle.

## Safe Edit Rules

- Keep audit logging around snapshot creation and restore.
- Do not increase destructive behavior without a very obvious config gate.
- Do not reduce retention below two weekly snapshots.
- Keep offsite mirroring boring and predictable.
- Test restore changes in a test guild before trusting them in production.
