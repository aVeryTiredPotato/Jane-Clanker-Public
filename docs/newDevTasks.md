# New Dev Tasks

This is a starter list for new Jane devs.

None of these are meant to be giant "prove yourself" jobs. They are mostly just small repo-familiarity tasks so people can poke around, learn the shape of things, and ship something without immediately getting thrown into one of Jane's cursed systems.

## Good Starter Tasks

- Pick one file in [`docs/fileNotes/`](fileNotes/README.md) and add one more section or one more function breakdown.
  This is probably the easiest low-risk way to get familiar with the repo without breaking anything.

- Add one more useful warning log in the snapshot restore path.
  Good files for this:
  - [`features/operations/serverSafety/snapshotRestore.py`](../features/operations/serverSafety/snapshotRestore.py)
  - [`features/operations/serverSafety/restoreChannels.py`](../features/operations/serverSafety/restoreChannels.py)
  - [`features/operations/serverSafety/restoreRoles.py`](../features/operations/serverSafety/restoreRoles.py)

- Clean up one small repeated code path in the voice chat feature.
  Good files for this:
  - [`cogs/staff/voiceChatCog.py`](../cogs/staff/voiceChatCog.py)
  - [`features/staff/voiceChat/voiceChatManager.py`](../features/staff/voiceChat/voiceChatManager.py)

- Add one more tiny quality-of-life field to `?janeRuntime` or `!janeterminal`.
  Good file:
  - [`runtime/textCommands.py`](../runtime/textCommands.py)

- Pick one older comment/message that is confusing and rewrite it so the next person has an easier time.
  That sounds tiny, but it genuinely helps in this repo.

## Slightly More Real Tasks

- Add a safer timeout/progress wrapper to one more Discord-heavy restore path.
- Make one subsystem log a little more honestly when it partially succeeds instead of only saying success/failure.
- Write a file note for a cog or feature you touched so the next dev has a map.

## Good Files To Read First

- [`bot.py`](../bot.py)
- [`runtime/textCommands.py`](../runtime/textCommands.py)
- [`features/operations/serverSafety/snapshotRestore.py`](../features/operations/serverSafety/snapshotRestore.py)
- [`features/operations/serverSafety/restoreChannels.py`](../features/operations/serverSafety/restoreChannels.py)
- [`features/staff/voiceChat/voiceChatManager.py`](../features/staff/voiceChat/voiceChatManager.py)

## General Advice

- Do not assume a weird-looking part of Jane is unused. A lot of weird-looking parts are live.
- Prefer small patches over hero rewrites.
- If you touch something annoying, leave it a little easier to read than you found it.
- If you add a feature, add at least a little logging or docs with it.
