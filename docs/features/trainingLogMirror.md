# Training Log Mirror

This is the practical map for Jane's training-result capture and mirror system.

The mirror is basically a tiny archivist. It watches a source channel, parses John/Jane training result posts, stores normalized rows in SQLite, and mirrors tidy embeds into an archive channel without pinging half the server into dust.

## Where It Lives

- `features/staff/trainingLog/trainingLogService.py`
- `bot.py`
- `runtime/textCommands.py`
- `runtime/webhooks.py`
- `db/sqlite.py`

## Config Values

- `trainingResultsChannelId`
  Source channel Jane reads from.

- `trainingArchiveChannelId`
  Destination channel Jane mirrors into.

- `trainingLogBackfillDays`
  Number of history days to scan on startup or manual backfill. Clamped to 7 through 365.

- `trainingSummaryWebhookName`
  Webhook name for the summary panel.

- `trainingMirrorWebhookName`
  Webhook name for mirrored training log embeds.

These values are read through organization profiles, so multi-org behavior depends on `organizationProfiles` and the `anro-training-logs` feature gate.

## Accepted Event Types

The parser currently knows these shapes:

- `Orientation Results`
- `Grid Certification Training Session Completed`
- `Grid Certification Examination Session Completed`
- `Emergency Certification Training Session Completed`
- `Emergency Certification Examination Session Completed`
- `Turbine Certification Session Completed`
- `Solo Certification Session Completed`
- `Supervisor Certification Session Completed`

Certification posts are accepted from the source channel after parsing.

Orientation posts are accepted only when the author is Jane or John. This avoids double-logging staff-written grade summaries.

## Parse Shape

Jane reads message content and embed content.

The parser is looking for:

- the title line
- `Host:`
- `Certified Recipients (Pass):`
- `Failed Attendees:`

Section parsing stops on control lines like:

- `Host:`
- `Co-host:`
- `Each recipient also received...`
- `Common mistakes`
- `Passed`
- `Failed`
- `None`

Markdown wrappers are stripped for control-line checks, so `*Each recipient also received 1 points...*` should not become a fake attendee.

## Mirror Behavior

When a valid source message shows up:

1. Jane parses the message.
2. Jane upserts a row in `training_result_logs`.
3. Jane builds one or more embeds.
4. Jane sends or edits the archive mirror through an owned webhook.
5. Jane stores `mirrorChannelId` and `mirrorMessageId`.
6. Jane refreshes the summary panel when needed.

The mirror uses a webhook username and avatar based on the source author, but disables mentions in fallback sends.

Each mirror embed footer includes:

- `Source message ID: <id>`

Keep that footer stable. It is used to recover existing mirrors if stored message IDs are stale.

The summary panel is intentionally re-sent instead of edited after live mirror activity. Jane sends the fresh summary to the bottom, then deletes the previous summary card.

## Backfill

Backfill runs at startup through `syncRecentMessages()`.

After startup backfill, Jane checks the latest archive message. If the latest message is already the summary panel, she leaves it alone and records that message ID. If anything else is below it, she re-sends the summary to the bottom.

Manual backfill uses:

- `!mirrortraininghistory`

Backfill scans oldest-first after the cutoff date. Before creating any mirror messages, Jane scans the archive channel for existing mirror footers. If a source message ID is already present in the archive, Jane records that mirror ID and refuses to send the log again.

Normal restart backfill does not create missing mirror messages. It only stores parsed rows, reconnects known mirror IDs, and keeps the summary panel at the bottom.

Manual backfill can create missing mirror messages for source results that are not already in the archive index. If the archive scan fails, manual backfill still stores parsed rows, but it will not create mirror messages. That is intentional. Not duplicating a year's worth of logs is more important than guessing.

If Jane has a stored mirror message ID but cannot fetch that mirror later, she does not create a replacement copy. The source row stays stored, but the archive is not spammed with duplicates.

Individual message failures are logged and skipped, because one cursed old post should not ruin the whole history scan.

Progress is logged every 50 scanned messages.

## Stats Commands

Training stats use stored rows from `training_result_logs`.

Supported text commands:

- `?trainingstats`
- `?hoststats`

Targets can be the caller, a mention, or a numeric Discord user ID.

## Troubleshooting

- Nothing mirrors.
  Check source channel ID, archive channel ID, feature gate, and startup logs.

- Backfill scans but captures nothing.
  Check source authors and title formats.

- Orientation logs double-count.
  Check whether non-Jane orientation summaries are being accepted.

- Attendee counts are off by one.
  Check section boundary lines and Markdown wrappers.

- Existing mirrors duplicate.
  Check whether the footer recovery text changed or old mirror message IDs point to another channel.

- Webhook sends fail.
  Check Jane's `Manage Webhooks`, `Send Messages`, and `Embed Links` permissions in the archive channel.

## Safe Edit Rules

- Treat accepted titles as a data contract with John/Jane posts.
- Keep parser changes small and test with real saved examples.
- Do not make name matching more aggressive without checking false positives.
- Keep per-message exception handling in backfill.
- Keep webhook sends mention-safe.
