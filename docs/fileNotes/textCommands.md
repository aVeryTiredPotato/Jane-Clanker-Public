# textCommands.py

[`runtime/textCommands.py`](../../runtime/textCommands.py) is where a lot of Jane's hidden/manual text-command logic lives.

If slash commands are the normal command surface, this file is the manual/runtime command surface.

## What Lives Here

- `?janeRuntime`
- `!janeterminal`
- `!shutdown`
- `!allowserver`
- `!mirrortraininghistory`
- `?bgcheck` / `?bg-check`
- `?bgleaderboard` / `?bg-leaderboard`
- `?perm-sim` / `?permsim`
- `:)help`
- helper methods for hidden/runtime-only workflows

Training stats are handled next to this router from `bot.py`, but the command lives in the same text-command path:

- `?trainingstats`
- `?hoststats`

## Main Class

### `TextCommandRouter`

This is the actual router for the manual text commands.

It gets built once from [`bot.py`](../../bot.py) and then reused when `on_message` needs it.

Good methods to know:

- `firstLowerToken(...)`
  tiny helper that figures out which text command is being called

- `handleJaneRuntime(...)`
  builds the runtime/status embed

- `handleJaneTerminal(...)`
  builds the hidden read-only terminal view

- `handleShutdown(...)`
  hidden lead-dev shutdown command

- `handleAllowServer(...)`
  runtime/manual allowed-guild helper

- `handleMirrorTrainingHistory(...)`
  manual training-log mirror/backfill trigger

- `handlePermissionSimulatorCommand(...)`
  test-server permission helper for checking likely slash-command access

## Handler Order

`bot.py` owns the actual `on_message` ordering.

Important shape:

- some high-risk commands are checked early before the normal message path finishes
- `:)help` deletes the trigger message when Jane has manage-message permissions
- denied hidden commands often return `True` without explaining anything publicly
- `?trainingstats` / `?hoststats` route through `TrainingLogCoordinator`, not `TextCommandRouter`

If a text command seems to be ignored, check both this file and the order in `bot.py`.

## Things To Be Careful About

- This file mixes "small utility command" logic with hidden runtime workflows.
  So it is easy to accidentally break a simple command while touching a different one.

- A lot of these commands are intentionally hidden or restricted.
  If you touch the allowlist logic, double-check who can still run the command afterward.

- `!shutdown`, `!allowserver`, and `!mirrortraininghistory` share lead-dev style authorization.
  Be careful when changing one helper because it may affect more than one command.

- Webhook-authored messages behave differently from normal bot-authored messages.
  If a hidden command edits a webhook-authored message, check the webhook helper path before assuming a normal message edit will work.

## Good Small Edits Here

- improve one status line
- improve one denial message
- add one missing log/warning
- extract one ugly little repeated helper
- add docs for one handler you touched
