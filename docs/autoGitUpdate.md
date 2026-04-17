# Auto Git Update

This is the practical map for Jane's Git pull and restart behavior.

The updater exists so the production bot can pull safe code updates without trampling live runtime data. It is conservative by design, because "the bot updated itself into a hole" is not a hobby we need.

## Where It Lives

- `runtime/gitUpdate.py`
- `runtime/configMerge.py`
- `runtime/processControl.py`
- `runtime/pauseState.py`
- `cogs/operations/runtimeControlCog.py`
- `bot.py`

## Main Config

- `JANE_ENABLE_AUTO_GIT_UPDATE`
  Enables scheduled update checks.

- `JANE_ALLOW_GIT_PULL_ON_RESTART`
  Allows manual restart flows to pull before restarting.

- `autoGitUpdateRemote`
  Defaults to `origin`.

- `autoGitUpdateBranch`
  Empty means use the current branch.

- `autoGitUpdateCheckIntervalSec`
  Scheduled check interval.

- `autoGitUpdateInitialDelaySec`
  Startup delay before the updater begins checking.

- `autoGitUpdatePauseDrainSec`
  Pause drain time before pulling.

- `autoGitUpdatePreservePaths`
  Extra runtime paths to preserve during pull.

## Always-Preserved Paths

These are always treated as local runtime state:

- `bot.db`
- `bot.db-shm`
- `bot.db-wal`
- `*.db`
- `*.db-shm`
- `*.db-wal`
- `divisions.json`
- `configData/divisions.json`

The default preserved folders are:

- `backups/serverSnapshots`
- `backups/serverSnapshotsOffsite`

Dirty files under preserved paths do not block the update. They are backed up before pull and restored afterward.

## Managed Merge Paths

`config.py` is special.

If local `config.py` differs from `HEAD`, Jane does the careful little dance before pulling:

1. Parse top-level assignments from `HEAD`.
2. Parse top-level assignments from local `config.py`.
3. Record local assignment values that differ from `HEAD`.
4. Pull from Git.
5. Reapply those local values to the new `config.py` if the same assignment still exists.

This lets new upstream config fields arrive while keeping local server-specific values.

The merge is syntax-based, not magic. It only understands top-level assignments, so do not ask it to read your soul.

## What Blocks An Update

Jane skips pull if:

- auto-update is disabled
- Jane is already paused
- the local branch has commits ahead of remote
- local code/config files changed outside preserved paths and managed config files
- only preserved runtime paths changed upstream
- the repo cannot fetch, inspect, or pull cleanly

Dirty snapshot or database files should not block the whole pull.

## Scheduled Flow

1. Wait for `autoGitUpdateInitialDelaySec`.
2. Fetch the configured remote and branch.
3. Compare `HEAD` to `remote/branch`.
4. Inspect upstream changed paths and local dirty paths.
5. If safe, pause Jane.
6. Back up preserved paths.
7. Temporarily stash preserved and merge-managed dirty paths.
8. Pull with `git pull --ff-only`.
9. Reapply `config.py` local values when needed.
10. Restore preserved runtime paths.
11. Drop the temporary stash.
12. Restart Jane if code changed.

## Manual Restart Flow

Manual restart can pull first only when:

- `JANE_ALLOW_GIT_PULL_ON_RESTART=1`

If manual pull is disabled, Jane restarts without pulling.

If local commits or blocking dirty files exist, Jane skips the pull and restarts.

## Failure Recovery

If the updater fails during apply:

- preserved paths are restored from the temp backup when possible
- merge-managed files are restored when possible
- the temporary stash is dropped only when safe
- Jane unpauses if the updater paused her
- errors are logged and may be written to the audit stream

If a log says a temporary stash was kept, inspect it before doing any destructive Git cleanup. That stash is probably Jane trying to hand you the dropped groceries.

## Troubleshooting

- Jane says local changes block the update.
  Run `git status --short` and look for files outside preserved paths and `config.py`.

- Jane keeps skipping because local commits exist.
  The server branch has commits not on GitHub. Decide whether to push, reset intentionally, or stop auto-pull.

- `config.py` lost a local value.
  Check whether the assignment was renamed or removed upstream. Missing names are logged.

- Pull works manually but not through Jane.
  Check environment flags, branch config, Git availability, and whether Jane is paused.

- Update happens but no restart occurs.
  Check whether upstream changed only preserved runtime paths.

## Safe Edit Rules

- Keep `.db` files always preserved.
- Keep snapshot folders preserved.
- Do not broaden auto-merge beyond top-level assignments without tests.
- Do not replace `--ff-only` with a merge pull.
- Treat any kept stash as important local data.
