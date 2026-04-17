# Deployment

This is the practical "how do we actually run Jane" doc.

For a fresh Windows dev machine, start with [New Machine Setup](newMachineSetup.md), then come back here for production/runtime details.

## Main Assumption

Jane is meant to run from the private repo on a separate Windows server.

That matters for a few reasons:

- `.env` needs real secrets
- `config.py` still holds a lot of server-specific IDs/settings
- private extensions should usually be enabled there
- repo-relative paths are safer than machine-specific absolute paths

## Files That Matter

- `.env`
  Secrets and runtime flags
- `config.py`
  Server-specific IDs, channel IDs, role IDs, feature settings
- `bot.py`
  Entry point

## `.env` vs `config.py`

Use `.env` for:

- tokens
- API keys
- credentials paths
- runtime flags that may differ between public/private or between hosts

Use `config.py` for:

- guild IDs
- channel IDs
- role IDs
- feature tuning
- layout choices
- normal server-specific settings

That split is not perfect, but it is the current intended model.

## Important Env Flags

The big ones right now:

- `DISCORD_BOT_TOKEN`
- `ROBLOX_OPEN_CLOUD_API_KEY`
- `ROBLOX_INVENTORY_API_KEY`
- `ROVER_API_KEY`
- `ORBAT_GOOGLE_CREDENTIALS_PATH`

Private/runtime flags:

- `JANE_ENABLE_PRIVATE_EXTENSIONS`
- `ENABLE_DESTRUCTIVE_COMMANDS`
- `DESTRUCTIVE_COMMANDS_DRY_RUN`
- `JANE_ALLOW_GIT_PULL_ON_RESTART`
- `JANE_ENABLE_AUTO_GIT_UPDATE`

Optional host/runtime overrides:

- `DISCORD_GUILD_ID`
- `CLEAR_GLOBAL_COMMANDS`
- `CLEAR_GUILD_COMMANDS`
- `JANE_SUPERVISOR_MANAGED`

## Path Rules

Do not hardcode local machine paths unless you absolutely have to.

Good:

```env
ORBAT_GOOGLE_CREDENTIALS_PATH=localOnly/credentials/jane-clanker-e5a133917b6b.json
```

Bad:

```env
ORBAT_GOOGLE_CREDENTIALS_PATH=C:\Users\someone\Desktop\whatever\jane-clanker-e5a133917b6b.json
```

Jane has already been patched in a few places to prefer repo-relative paths, because she is not supposed to depend on one specific dev machine.

## Running Jane

Current normal startup:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then start Jane with:

```powershell
.\.venv\Scripts\python.exe bot.py
```

If you are running Jane under a service/supervisor, set:

```env
JANE_SUPERVISOR_MANAGED=1
```

That lets restart behavior cooperate better with the host process manager.

## Git Update Behavior

Jane can:

- check for updates
- optionally pull from Git
- restart herself

The detailed updater behavior lives in [Auto Git Update](autoGitUpdate.md).

But production should still be treated carefully. A bad push can still be a bad night.

Important runtime flags:

- `JANE_ALLOW_GIT_PULL_ON_RESTART`
- `JANE_ENABLE_AUTO_GIT_UPDATE`

If you want safer operation, the conservative choice is:

- allow manual pull-on-restart
- keep fully automatic update checks conservative or off

## Files Jane Should Not Treat As Disposable

The updater already protects live runtime state like:

- `bot.db`
- `bot.db-shm`
- `bot.db-wal`
- `configData/divisions.json`
- snapshot folders under `backups/`

That keeps normal code updates from trampling live data.

## Logging / Health

Useful runtime surfaces:

- `logs/general-errors.log`
- `?janeRuntime`
- `!janeTerminal`

`!janeTerminal` is read-only and meant for quick remote visibility, not remote shell access.
