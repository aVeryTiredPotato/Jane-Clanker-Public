# New Machine Setup

This is the practical setup path for a new Windows dev machine. It is intentionally boring, because new-machine setup should not be a rite of passage.

Use repo-relative commands from the Jane project folder.

## Prerequisites

Install:

- Git
- Python 3.11 or newer
- VS Code

Optional but useful:

- GitHub Desktop
- Windows Terminal

## Clone And Open

Clone the private Jane repo, then open the repo folder in VS Code.

The expected project root looks like:

```text
C:\Projects\Jane-Clanker
```

## Create The Virtual Environment

From PowerShell in the project root:

```powershell
py -m venv .venv
```

If `py` is not available:

```powershell
python -m venv .venv
```

## Install Dependencies

You do not need to activate the virtual environment.

Run:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Running Jane

Preferred dev command:

```powershell
.\.venv\Scripts\python.exe bot.py
```

This avoids PowerShell activation policy issues, which are very real and very annoying.

If you do want to activate the venv and PowerShell blocks scripts, use a process-local policy:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

That only affects the current terminal window.

## Environment File

Jane needs secrets and runtime flags.

Start from:

```text
.env.example
```

Create a local `.env` with real values for the machine. Keep it local. Secrets do not belong in Git.

Important values commonly needed:

- `DISCORD_BOT_TOKEN`
- `ROBLOX_OPEN_CLOUD_API_KEY`
- `ROBLOX_INVENTORY_API_KEY`
- `ROVER_API_KEY`
- `ORBAT_GOOGLE_CREDENTIALS_PATH`
- `JANE_ENABLE_PRIVATE_EXTENSIONS`
- `JANE_ALLOW_GIT_PULL_ON_RESTART`
- `JANE_ENABLE_AUTO_GIT_UPDATE`

Never commit `.env`.

## Local Config

Most server IDs, channel IDs, role IDs, and feature tuning live in:

```text
config.py
```

Before running against a dev server, check:

- `serverId`
- `serverIdTesting`
- `allowedCommandGuildIds`
- channel IDs for the feature being tested
- role IDs for the feature being tested
- destructive command flags

## Google Credentials

ORBAT and sheet-backed features need the Google credentials JSON file.

Prefer repo-relative paths in `.env`, for example:

```env
ORBAT_GOOGLE_CREDENTIALS_PATH=localOnly/credentials/jane-clanker.json
```

Do not commit credential JSON files.

## Database

Jane uses SQLite:

```text
bot.db
```

The DB is local runtime state. Do not commit it. The database is not a souvenir.

If a dev machine needs production-like state, use an intentional backup/restore path rather than pulling a random `.db` through Git.

## First Startup Checklist

1. Install dependencies.
2. Add `.env`.
3. Confirm `config.py` points at the intended guild.
4. Start with `.\.venv\Scripts\python.exe bot.py`.
5. Watch terminal startup logs.
6. Check `logs/general-errors.log` if startup fails.
7. Confirm Jane appears online in the target guild.
8. Confirm slash commands are synced before testing commands.

## Common Problems

- `Activate.ps1 cannot be loaded because running scripts is disabled`
  Use `.\.venv\Scripts\python.exe bot.py` directly or set process-local execution policy.

- `ModuleNotFoundError`
  Run dependency install again with the venv Python.

- Jane starts but commands are missing.
  Check guild IDs, command sync logs, and command clear flags.

- Jane cannot access a channel.
  Check the channel ID and bot permissions.

- Roblox or ORBAT features fail.
  Check API keys, credential path, and whether the local file exists.

- Git update behavior is unexpected.
  Check `JANE_ALLOW_GIT_PULL_ON_RESTART`, `JANE_ENABLE_AUTO_GIT_UPDATE`, and [Auto Git Update](autoGitUpdate.md).

## Safe Dev Habits

- Run risky features in a test guild first.
- Keep `.env`, `.db`, and credentials out of Git.
- Prefer repo-relative paths.
- Use `git status --short` before pulling or pushing.
- Ask before wiping local changes on a shared machine.
