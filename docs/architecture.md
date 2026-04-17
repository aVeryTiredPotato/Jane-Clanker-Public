# Architecture

This is the quick mental model for how Jane is put together right now.

## Startup Flow

Very roughly:

1. `config.py` loads `.env`.
2. `bot.py` creates the bot client and the main runtime services.
3. `runtime/privateServices.py` tries to load private-capable services and falls back cleanly if they are disabled or missing.
4. `runtime/extensionLayout.py` builds the extension list.
5. `runtime/bootstrap.py` handles startup work like DB init, command sync, view restore, and maintenance kickoff.
6. Background workers start doing their thing.

If you want the shortest answer to "where does Jane begin", it is `bot.py`.

## Main Layers

### `cogs/`

This is the Discord-facing command layer.

It is grouped by domain now:

- `cogs/community`
- `cogs/operations`
- `cogs/staff`

Most slash commands live here.

### `features/`

This is where the real logic lives.

Grouped by domain:

- `features/community`
- `features/operations`
- `features/staff`

The cogs should mostly be thin-ish wrappers around these feature modules, though some older areas still blur that line.

Gambling is an exception to this grouping right now. Its active code still lives in `silly/gambling*`, with `runtime/gamblingApi.py` as shared runtime support.

### `runtime/`

This is the shared infrastructure layer.

Some of the bigger things in here:

- startup / bootstrap
- maintenance jobs
- error logging
- webhook helpers
- pause / restart / git update flow
- metrics and runtime reporting
- extension loading
- private service loading

If Jane feels like she is "doing something in the background", there is a good chance it lives here.

### `db/`

SQLite lives here.

Right now the DB layer is still centralized pretty heavily in `db/sqlite.py`. It works, but it is one of the denser files in the repo.

### `plugins/`

This is the start of the public/private split.

- `plugins/public`
- `plugins/private`

The core bot can load optional extensions from either place.

Private extensions only load when `config.enablePrivateExtensions` is truthy.

## Current Extension Model

Jane currently loads:

1. core extensions from `runtime/extensionLayout.py`
2. anything in `config.extraExtensionNames`
3. optional modules listed in:
   - `plugins/public/extensionList.py`
   - `plugins/private/extensionList.py`

That keeps the public/private split simple:

- public repo keeps core + public-safe optional stuff
- private repo can add private-only extensions without rewriting startup

## Data / State

Jane stores state in a few places:

- `bot.db`
  Main SQLite database
- `logs/`
  Runtime logs and some transient status files
- `backups/`
  Database backups and server safety snapshots
- config JSON files like:
  - `configData/divisions.json`
  - `configData/ribbons.json`

Some features also keep disk-based helper files where that made more sense than jamming everything into SQLite.

## Command Surfaces

Jane has a few command styles:

- slash commands from `cogs/`
- text commands in `runtime/textCommands.py`
- older legacy/fun commands in `silly/`

That is not perfectly pure, but it is the current reality.

## What Is Still A Bit Weird

A few honest notes:

- `cogs/applicationsCog.py` is still a top-level oddball
- `silly/` still contains some real functionality, not just joke stuff
- some older features still mix UI logic and service logic more than they should
- `db/sqlite.py` is still carrying a lot

None of that blocks Jane from working. It just means the architecture is still mid-cleanup rather than "finished."
