# Best Of

This is the practical map for the Best Of voting feature.

Best Of looks like a simple poll from the outside, but it has a handful of little rules that matter: section ballots, private voting, creator-only close behavior, and DM-only results. This doc is here so nobody has to rediscover those rules by poking the button gremlin.

## Where It Lives

- `cogs/community/bestOfCog.py`
- `features/community/bestOf/service.py`
- `db/sqlite.py`

## Command Shape

- `/best-of`
  Creates a Best Of vote in the current channel.

The command requires administrator permission.

If an open poll already exists in the channel, running `/best-of` again starts the close flow instead of creating another poll.

## Poll Creation

When the command creates a fresh poll, Jane:

1. Builds the candidate list from configured role IDs.
2. Optionally adds selected LR candidates from config or command input.
3. Stores the poll row.
4. Stores candidate rows.
5. Posts the public poll message with a `Vote` button.
6. Sends an ephemeral creator note explaining that `/best-of` again closes the poll.

## Candidate Sections

Candidates are grouped by priority labels.

The intended display order is:

- `ANROCOM`
- `HR`
- `MR`
- `Former ANROCOM`
- `Former HR`
- `Former MR`

`Command Staff` is displayed as `HR`.

Display names are cleaned to remove leading bracket prefixes. When possible, the feature resolves Roblox names through the session Roblox helper so Discord nicknames are less confusing.

## Voting Flow

Voters press `Vote` on the public poll message.

Jane opens an owner-locked ephemeral ballot with:

- section selector
- candidate selector
- candidate pagination
- one vote per section

After a voter completes every section, Jane sends:

- `Thank you for voting!`

Jane does not send a message for every individual selection.

## Closing

Polls close in two ways:

- the creator runs `/best-of` again and confirms
- the auto-finalizer closes polls older than 24 hours

Only the poll creator can close an existing open poll with `/best-of`.

Poll close refreshes the public poll message so it no longer looks open.

## Results

Results are DM-only.

Jane first tries the poll creator. If that DM fails, Jane falls back to the hardcoded fallback recipient in `bestOfCog.py`, because sometimes the emergency scroll needs somewhere to go.

Results include:

- a summary embed
- per-section result embeds
- per-section pagination when a section has more than 10 entries

Per-section colors are configured in `bestOfCog.py`.

## Stored State

Best Of uses these tables:

- `best_of_polls`
- `best_of_poll_candidates`
- `best_of_poll_votes`
- `best_of_poll_section_votes`

`best_of_poll_votes` is kept for backward compatibility. Current voting uses `best_of_poll_section_votes`.

## Startup Behavior

On cog load, Jane restores persistent views for open polls that still have message IDs. In normal words: if Jane restarts mid-poll, the `Vote` button should come back.

The auto-finalizer runs in the background and checks for expired polls every 60 seconds.

## Troubleshooting

- Voters cannot use a ballot.
  Check whether they opened their own ballot and whether the view expired.

- Candidate list is empty.
  Check Best Of role IDs in `config.py`.

- A section has too many people.
  That is expected. Candidate and result pagination should handle it.

- Results did not reach the creator.
  Check whether the creator has DMs closed. Jane should try the fallback recipient next.

- A poll refuses to close.
  Check whether the caller is the poll creator and has administrator permission.

## Safe Edit Rules

- Keep result delivery DM-only unless the behavior is intentionally changed.
- Keep owner-locked views for ballots and result pagination.
- Do not re-add a public close button unless the close model is redesigned.
- Keep the 24-hour auto-finalizer in mind when changing poll status logic.
- Be careful changing candidate grouping because old polls store candidate labels.
