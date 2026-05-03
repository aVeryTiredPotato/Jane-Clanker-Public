# Feature Map

This is not deep documentation yet. It is just a map of the big systems so the repo is easier to navigate.

If you want more specific "what does this actual file do?" docs, check:

- [New Dev Tasks](../newDevTasks.md)
- [File Notes](../fileNotes/README.md)

## Feature Runbooks

- [Sessions And BG Checks](sessions.md)
- [BG Intelligence](bgIntelligence.md)
- [Honor Guard](honorGuard.md)
- [Server Recovery](serverRecovery.md)
- [Training Log Mirror](trainingLogMirror.md)
- [Best Of](bestOf.md)

## Community

- polls
- reminders
- suggestions
- events
- best of
- archive tools
- public utility bits

Look under:

- `cogs/community`
- `features/community`

## Operations

- notes
- federation
- ops dashboard
- curfew
- jail
- runtime control
- server safety / snapshots / recovery

Look under:

- `cogs/operations`
- `features/operations`

## Staff

- sessions / BG checks
- applications
- workflows
- ribbons
- honor guard
- ORBAT
- recruitment
- cohost
- voice chat
- projects
- ANRD payments
- division clock-ins

Look under:

- `cogs/staff`
- `features/staff`

The sessions/BG-check flow is complicated enough that it has its own runbook:

- [Sessions And BG Checks](sessions.md)

## Gambling

Gambling is still in the legacy/fun slice, but it is active code:

- `silly/gambling/`
- `silly/gamblingCog.py`
- `silly/gamblingService.py`
- `runtime/gamblingApi.py`

## Legacy / Weird

`silly/` is still around.

Some of it is active functionality, some is old Jane history, and some is legacy event code kept for context.

Do not assume code is dead just because it lives there. Check whether it is loaded by `runtime/extensionLayout.py` or a plugin list before removing it.
