# Jane

Jane is a Discord bot built for ANRO.

This is not a polished SaaS app or a library. It is a fairly large, somewhat practical bot that grew around real staff workflows, Discord moderation/admin tooling, recruitment, ORBAT work, BG checks, reminders, voice chats, and a pile of internal quality-of-life features.

The goal of these docs is not to document every function. The goal is to make the repo easier to understand, run, and split into public/private pieces without guesswork.

## Start Here

- [Architecture](docs/architecture.md)
- [Deployment](docs/deployment.md)
- [Operations](docs/operations.md)
- [Public / Private Split](docs/publicPrivateSplit.md)
- [Feature Map](docs/features/README.md)
- [New Dev Tasks](docs/newDevTasks.md)
- [File Notes](docs/fileNotes/README.md)

## Repo Layout

- `bot.py`
  Main entrypoint. Builds the runtime services, loads extensions, and starts the bot.
- `config.py`
  Main config file. Loads `.env`, keeps most server-specific IDs/settings, and exposes the runtime flags.
- `cogs/`
  Slash-command cogs, grouped by domain.
- `features/`
  The actual feature logic, grouped by domain.
- `runtime/`
  Cross-cutting runtime pieces like startup, maintenance, logging, retries, git update, metrics, and webhook helpers.
- `db/`
  SQLite layer and schema setup.
- `plugins/`
  Optional extension layers for the public/private split.
- `silly/`
  Legacy fun/oddball slice. Some of it is still useful, some of it is just old Jane history.
- `tools/`
  Repo utilities, including the public export script.

## Current Folder Grouping

- `cogs/community`
- `cogs/operations`
- `cogs/staff`
- `features/community`
- `features/operations`
- `features/staff`
- `features/gambling`

There are still a couple oddballs hanging around, like `cogs/applicationsCog.py`, but the structure is much saner than it used to be.

## Running Jane Locally

There is not currently a pinned dependency file in the repo, so local setup is still a little manual.

Basic flow:

1. Create or activate a virtualenv.
2. Copy [`.env.example`](.env.example) to `.env`.
3. Fill in the required secrets/tokens.
4. Adjust `config.py` for any server-specific IDs or behavior.
5. Start Jane with:

```powershell
.\.venv\Scripts\python.exe bot.py
```

Windows helper scripts are also available:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\startBot.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\restartBot.ps1
```

Or use the repo-root batch files:

```bat
startBot.bat
```

```bat
restartBot.bat
```

```bat
stopBot.bat
```

If you are running Jane on another Windows box, prefer repo-relative paths in `.env` where possible. The ORBAT credentials path is set up to work that way.

## Public / Private Repo Split

Jane now has the beginnings of a proper split:

- core extensions load normally
- public plugins can be added under `plugins/public`
- private-only plugins can be added under `plugins/private`

The production bot should keep using the private repo.

If you want a public-safe copy, export one with:

```powershell
python tools\exportPublicRepo.py C:\path\to\jane-public --clean
```

`--clean` is safe to use against a cloned copy of the public repo. Jane preserves the target repo's `.git` directory and replaces the working tree around it.

That export path does a secret scan and a smoke test so the public copy is less likely to be broken or embarrassing.
