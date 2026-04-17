# Sessions And BG Checks

This is the practical map for Jane's orientation session flow and the background-check queue that follows it.

The important idea: a session is not just a Discord message. It is database state, persistent buttons, grading state, BG routing state, Roblox scan state, and post-orientation side effects all tied into one slightly cursed but very useful machine.

## Where It Lives

- `cogs/staff/sessionCog.py`
- `features/staff/clockins/engine.py`
- `features/staff/clockins/orientationAdapter.py`
- `features/staff/sessions/service.py`
- `features/staff/sessions/sessionControls.py`
- `features/staff/sessions/views.py`
- `features/staff/sessions/bgRouting.py`
- `features/staff/sessions/bgQueueMessaging.py`
- `features/staff/sessions/bgQueueViews.py`
- `features/staff/sessions/bgCheckViews.py`
- `features/staff/sessions/bgScanPipeline.py`
- `features/staff/sessions/postActions.py`
- `features/staff/sessions/viewPolicy.py`

## Commands People Actually Touch

- `/orientation`
  Starts an orientation session in the current text channel.

- `/bg-check`
  Creates a background-check queue from pending users without running a full orientation session.

## Orientation Flow

The normal happy path looks like this:

1. An instructor runs `/orientation password:<value>`.
2. Jane creates a `sessions` row with status `OPEN`.
3. Jane posts the orientation message with a persistent `SessionView`.
4. Attendees press the join button and enter the password in a modal.
5. Jane stores each attendee in the `attendees` table.
6. The host presses `Change Grade`.
7. Jane moves the session to `GRADING` and shows host-only grading controls.
8. The host marks attendees as `PASS` or `FAIL`.
9. The host presses `Finish`.
10. Jane posts BG queues for passing attendees.
11. If all needed BG queues post successfully, Jane posts orientation results, marks the session `FINISHED`, and deletes the live session message.

THIS IS SUBJECT TO CHANGE, AS THE ABOVE PORTION IS CURRENTLY BEING WORKED ON 4/13/26

## BG Queue Flow

Only passing attendees become BG candidates.

Jane routes each passing attendee into a review bucket with this priority:

1. Role routing from `bgMinorAgeRoleIds` and `bgMajorAgeRoleIds`.
2. ORBAT age group routing from `bgMinorAgeGroups` and `bgAdultAgeGroups`.
3. Fallback routing from `bgUnknownDefaultsToMinor`.

The two buckets are:

- `adult`
  Displayed as `+18`.

- `minor`
  Displayed as `-18`.

Queue message IDs are stored separately:

- `sessions.bgQueueMessageId`
- `sessions.bgQueueMinorMessageId`

## Review Controls

Reviewers can approve, reject, claim, inspect info, and open the next pending attendee. This is the part staff actually lives in during BG work.

Access is checked through `features/staff/sessions/viewPolicy.py`.

The main review permissions are:

- `moderatorRoleId`
- `bgReviewModeratorRoleId`
- `bgCheckMinorReviewRoleId`
- `bgCheckMinorReviewRoleIds`

For the minor review guild, `bgCheckMinorReviewRoleId` is the ping role and `bgCheckMinorReviewRoleIds` is the allowed-role list.

## What Approval Does

When a reviewer approves a candidate:

1. The attendee row is marked `APPROVED`.
2. Any active claim for that attendee is cleared.
3. If this is an orientation or BG-check session, Jane removes the pending BG role.
4. If this is an orientation session, Jane can award the host point.
5. Jane refreshes the session and BG queue messages.
6. Jane may attempt Roblox group auto-accept.
7. Jane may DM the user with Roblox join instructions if auto-accept cannot complete.
8. Jane may apply recruitment orientation bonus work.

## What Rejection Does

When a reviewer rejects a candidate:

1. The attendee row is marked `REJECTED`.
2. Any active claim for that attendee is cleared.
3. Jane refreshes the session and BG queue messages.
4. Jane keeps the result visible in the BG summary.

## Persistent Views

Session and BG views are restored at startup by the runtime bootstrap path.

This matters after restarts. If a queue is still pending and the message IDs are still stored, Jane should reattach the buttons instead of leaving everyone staring at a decorative embed.

If buttons do not work after restart, check:

- the relevant message still exists
- the session is not already `FINISHED` or `CANCELED`
- the message ID columns are populated
- startup logs mention persistent view restore

## Stored State

The main tables are created in `db/sqlite.py`.

Session-level state lives in `sessions`. The fields people usually care about are:

- `sessionId`
- `guildId`
- `channelId`
- `messageId`
- `sessionType`
- `hostId`
- `passwordHash`
- `status`
- `gradingIndex`
- `bgQueueMessageId`
- `bgQueueMinorMessageId`

Attendee-level state lives in `attendees`. The useful bits are:

- `sessionId`
- `userId`
- `examGrade`
- `bgStatus`
- `bgReviewBucket`
- Roblox scan and join status fields
- credit and processing fields

## Config Checklist

Check these first when sessions or BG queues start acting haunted:

- `instructorRoleId`
- `newApplicantRoleId`
- `pendingBgRoleId`
- `bgCheckChannelId`
- `bgCheckAdultReviewGuildId`
- `bgCheckAdultReviewChannelId`
- `bgCheckMinorReviewGuildId`
- `bgCheckMinorReviewChannelId`
- `bgCheckMinorReviewRoleId`
- `bgCheckMinorReviewRoleIds`
- `bgMinorAgeRoleIds`
- `bgMajorAgeRoleIds`
- `bgMinorAgeGroups`
- `bgAdultAgeGroups`
- `bgUnknownDefaultsToMinor`
- `bgReviewModeratorRoleId`
- `moderatorRoleId`
- `trainingResultsChannelId`
- `robloxGroupId`
- `robloxOpenCloudApiKey`
- `roverApiKey`

## Common Failures

- A user cannot clock in.
  Check whether they still have the New Applicant role if `newApplicantRoleId` is configured.

- The host cannot start orientation.
  Check `instructorRoleId`.

- The host cannot finish.
  Check that every attendee has a grade.

- BG queues do not post.
  Check adult and minor review channel IDs, Jane's channel permissions, and logs around `postBgQueue`.

- The wrong queue gets an attendee.
  Check role routing first, then ORBAT age group, then `bgUnknownDefaultsToMinor`.

- Review buttons deny a reviewer.
  Check the review guild, the reviewer roles, and `viewPolicy.py`.

- Roblox auto-accept fails.
  Check RoVer lookup, Open Cloud config, group ID, and whether the user actually requested to join the group.

## Safe Edit Rules

- Keep session DB changes backward-compatible.
- Do not remove persistent view custom IDs without a migration plan.
- Treat `Finish` as a high-risk path because it posts queues, posts results, changes session status, and triggers side effects.
- Be careful with role and age routing changes because they decide whether users are sent to `+18` or `-18` review.
- Prefer small targeted changes and smoke-test with a fake session in a test server.
