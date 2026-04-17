# Operations

This is the short practical doc for running Jane without remembering every operational detail.

## Useful Runtime Tools

- `?janeRuntime`
  Runtime status snapshot
- `!janeTerminal`
  Read-only terminal-style status view
- `/pause`
  Pause or unpause the bot
- `/restart`
  Restart Jane, with optional Git update behavior depending on config/env

## Logs

Main general log:

- `logs/general-errors.log`

That is the first place to look if Jane starts acting strange or a background task is blowing up.

## Snapshots / Recovery

Server safety and snapshots live under:

- `backups/serverSnapshots`
- `backups/serverSnapshotsOffsite`

The practical recovery runbook is [Server Recovery](features/serverRecovery.md).

Quarantine is currently disabled by configuration because it is high-risk and has had operational incidents. Treat any re-enable work as destructive/recovery work.

## High-Risk Feature Runbooks

- [Sessions And BG Checks](features/sessions.md)
- [Training Log Mirror](features/trainingLogMirror.md)
- [Best Of](features/bestOf.md)
- [Auto Git Update](autoGitUpdate.md)

## Common Operational Notes

### If Jane seems online but weird

Check:

- `?janeRuntime`
- `!janeTerminal`
- `logs/general-errors.log`

### If commands are missing

Check:

- whether command clear flags are set
- whether the bot synced commands on startup
- whether the bot is connected to the right guild

### If restart/update behavior is odd

Check:

- `JANE_ALLOW_GIT_PULL_ON_RESTART`
- `JANE_ENABLE_AUTO_GIT_UPDATE`
- whether the host is supervisor-managed

### If a feature works locally but not on the server

Usually it is one of these:

- missing env var
- wrong server/channel/role ID in `config.py`
- path issue
- host-specific file not present

## Good Habits

- keep `.env` host-specific
- keep `config.py` in sync with the actual server
- avoid absolute machine paths
- don't treat the public export like production source
- test risky changes on the test server first when possible

## What Not To Do

- don't turn `!janeTerminal` into a remote shell
- don't assume old backup JSON metadata paths are meaningful on a new machine
- don't let auto-update become "blindly trust every push forever"
