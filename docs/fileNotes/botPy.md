# bot.py

[`bot.py`](../../bot.py) is Jane's actual entrypoint.

If you want the "where does Jane even start?" answer, this is the file.

## What It Handles

- builds the Discord bot client
- wires shared runtime services together
- loads extensions/cogs
- routes message events
- starts Jane from `.env`
- handles a few startup safety checks

## Parts Worth Knowing

### Startup / `__main__`

Near the bottom of the file, Jane:
- configures logging
- grabs the single-instance lock
- loads `.env`
- checks for BOM weirdness
- starts `botClient.run(...)`

If Jane hard-fails on startup, this is one of the first places to look.

### `on_message`

This is the main text-command routing path.

It does a few important things in order:
- handles paused-runtime behavior
- checks guild allowlisting
- routes hidden/manual commands like `!janeterminal`, `!shutdown`, and `?janeRuntime`
- falls back to normal command processing

If a text command "just does nothing," this is usually the first file to check.

### Tiny Wrapper Helpers For Manual Commands

These are just thin routing functions that hand off to the text-command router in [`runtime/textCommands.py`](../../runtime/textCommands.py).

They are not interesting by themselves, but they make the main message flow a little less unreadable.

## Things To Be Careful About

- `bot.py` is easy to turn into a dumping ground.
  If logic starts getting long, it probably belongs in `runtime/` or `features/`.

- The order inside `on_message` matters.
  If you move handlers around casually, you can change who is allowed to use what, or which commands still work while Jane is paused.

- Startup changes can affect other machines.
  Jane does not just run on one local dev box, so avoid machine-specific paths or assumptions.

## Good Small Edits Here

- add or improve a routing comment
- tighten a confusing startup error
- clean up duplicated pause/allowlist flow
- document a new hidden command after adding it somewhere else
