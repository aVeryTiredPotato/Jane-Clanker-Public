# Public / Private Split

Jane is moving toward a split where:

- the normal bot framework can live in a public repo
- the more sensitive or destructive pieces stay in a private repo

This is the current state of that plan.

## Current Shape

Core startup loads extensions through:

- `runtime/extensionLayout.py`
- `plugins/public/extensionList.py`
- `plugins/private/extensionList.py`

Private-capable service loading is centralized in:

- `runtime/privateServices.py`

That means the repo no longer has to pretend everything is either fully public or fully private.

## What Counts As Private

Right now, private-only pieces include things like:

- server safety
- runtime control
- DM-only runtime secret management
- private plugin wrappers

The private optional extensions currently listed are:

- `plugins.private.orbatExtension`
- `plugins.private.serverSafetyExtension`
- `plugins.private.runtimeControlExtension`
- `plugins.private.linkHubExtension`

## Hard Gates For Risky Stuff

Just having the file is not supposed to be enough.

Risky actions should also require:

- `ENABLE_DESTRUCTIVE_COMMANDS=1`
- allowed user checks
- allowed guild checks
- cooldowns
- audit logging

That way a bad merge is still annoying, but not automatically catastrophic.

## Public Export

The public-safe export path is:

```powershell
python tools\exportPublicRepo.py C:\path\to\jane-public --clean
```

That target can be a normal folder or a cloned working copy of the public repo. `--clean` keeps the target repo's `.git` directory so you can export straight into the public clone and then commit from there.

That export currently:

- strips known private-only paths
- strips private runtime secret command hooks from public files
- rewrites the private extension list to an empty scaffold
- sanitizes parts of `config.py`
- runs a secret scan
- runs a public smoke test

The smoke test currently does:

- `compileall`
- import smoke on core modules
- import smoke on exported extensions

## Important Rule

The production bot should keep using the private repo.

The public repo is for:

- sharing code
- learning from the structure
- outside contributions
- general visibility

It is not the source Jane should blindly pull production updates from.

## Before Publishing A Public Repo

- keep `.env` and credentials out of the public repo!!!!
