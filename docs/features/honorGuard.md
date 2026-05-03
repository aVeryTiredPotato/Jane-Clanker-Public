# Honor Guard

This is the current intended model for Jane's Honor Guard flow.

It is based on:

- the current private-repo Honor Guard backend
- the imported public-branch scaffolding
- the follow-up transcript explaining how HG actually wants the system to work

This is not a finished implementation doc. It is the working contract we should code against so we stop mixing draft branch assumptions with the real workflow.

## Where It Lives

- `cogs/staff/honorGuardCog.py`
- `cogs/staff/honorGuardViews.py`
- `features/staff/honorGuard/service.py`
- `features/staff/honorGuard/sheets.py`
- `features/staff/honorGuard/outputs.py`
- `features/staff/honorGuard/rendering.py`
- `db/sqlite.py`
- `config.py`

## Core Idea

Honor Guard has:

- `quota points`
- `promotion points`

Promotion points are tracked as:

- `event points`
- `awarded points`

The system also cares about three groups:

- `enlisted`
  `Jr Guardsman`, `Guardsman`
- `nco`
  `Sr Guardsman`, `Patrol Sergeant`
- `officer`
  `Parade Officer+`

The important behavior split is:

- enlisteds get normal event credit by attending
- officers do not get points for just attending
- officers get points by hosting, co-hosting, or supervising
- NCOs can earn points both ways

## Source Of Truth

For the current private-repo design, Jane should treat the Honor Guard workbook as the live member-state source of truth.

That means:

- the member sheet is the current HG ORBAT-like member state
- the schedule sheet is the live upcoming-event list
- the archive sheet is the finished-event history
- the event-host sheet tracks hosted-event counts and type totals

The database should store logs, approvals, and workflow state.

The database is not just a testing layer. It is the durable internal record Jane should work from so she can batch or defer Google Sheets writes instead of depending on the Sheets API for every state change.

The database should not become a second competing copy of the full member ORBAT unless we explicitly decide to do that later.

This is one of the main places where the old public branch drifted. That branch experimented with an `hg_main` style DB copy. The current private repo should not assume that model.

## Non-Goals For Phase 1

Do not try to replace Apollo immediately.

The intended order is:

- make logging work first
- add archive/schedule cleanup second
- only consider full scheduling / announcements later if HG still wants it

Jane's core value here is logging and workflow state, not replacing an existing event scheduler just because one already exists.

## Intended Logging Model

The system should split into three main paths:

1. `solo sentry`
2. `event clock-in / attendance`
3. `manual point awards`

### Solo Sentry

Solo sentry is the only thing that should be individually logged by the member.

Expected behavior:

- one log per user per day
- 30 minutes required
- evidence attachments required
- manual review required
- earns 1 quota point
- earns 1 promotion event point

Do not fully automate acceptance for solo sentry. HG explicitly wants fraud resistance here.

### Event Clock-In

Trainings, orientations, lectures, inspections, tryouts, JGEs, NCO exams, and similar hosted events should use an event record plus attendance flow.

That means:

- the host creates the event record
- attendees join through a clock-in style flow
- event attendance records are generated from that flow
- staff should not have to submit individual manual logs for normal hosted events

This was one of the biggest clarifications from the transcript: normal HG activities should not be per-user manual submissions.

The clock-in flow also needs to support Honor Guard-specific adjustments that existing recruitment flows do not fully cover yet:

- host, co-host, and supervisor attribution
- event-type-specific point rules
- late-join / early-leave point adjustments when HG wants reduced credit

### Manual Point Awards

Manual point awards are for awarded points and exceptions.

Examples:

- dev work
- document writing
- special officer-awarded extra credit

These should go through an approval flow and then sync into the member sheet as awarded promotion points, not event attendance points.

## Point Rules

These are the working rules we should implement unless HG changes them again.

### Enlisted Attendance

- quota points: `1` per event
- exception: `gamenight` gives `0.5` quota points
- promotion event points come from attendance-based event logic

### Officer Hosting / Supervising / Cohosting

Officers should not receive points for just attending.

They receive promotion points for:

- `gamenight` host: `1`
- `orientation` supervisor/manager from start to finish: `2`
- `training` or `lecture` host: `3`
- `Honor Guard-wide tryout` host: `6`
- `inspection` host or attend: `8`

For exams:

- `JGE`
  `0.75` per graded attendee, rounded up
- `NCO exam`
  `1.5` per graded attendee, rounded up
- `NCO exam` screen-assist co-host
  `2` points even without grading
- `NCO exam` screen-assist plus grading
  `2` plus personal graded-attendee points

Co-hosts and supervisors should receive points like attendees unless the specific event rule says otherwise.

### NCO Behavior

NCOs are the mixed case.

They can:

- receive attendance-style quota credit
- receive hosting/supervising/cohosting promotion credit

## Promotion / Status Rules

### Guardsman

- `15` promotion points
- passed `Junior Guardsman Exam`

### Senior Guardsman

- `50` promotion points
- passed `NCO exam`
- active status required unless `Retired` or `LoA`

### Special Status Notes

- some officers retire into `Senior Guardsman`
- those should not be blindly auto-demoted
- if someone has an excuse status or is new, their activity status should be `N/A`

## Quota Cycle Rules

Quota resets are bi-weekly.

Status logic:

- `>= 4` quota points at reset: `Active`
- `< 4` quota points at reset and no excuse status: `Inactive`
- `8` quota points before reset: may be marked `Active` early

Do not automate kicks just because someone has `0` quota points.

HG mentioned that as a policy outcome, but it is safer to leave that as manual staff action unless we intentionally automate it later.

## Intended Database Meanings

The current private-repo table layout is close to the right shape if we use it consistently.

This should also stay compatible with the broader direction Potato described: generic division logging groundwork first, then Honor Guard-specific rules layered on top.

### `hg_submissions`

Generic approval queue for things that need human review before sheet sync.

Examples:

- manual point awards
- solo sentry submissions
- possibly other future manual exceptions

### `hg_submission_events`

Audit trail for submission state changes.

Examples:

- created
- approved
- rejected
- synced to sheet

This is not the same thing as hosted event attendance.

### `hg_point_awards`

Approved manual awarded-point records.

These are the durable accounting rows for awarded points, not the live review queue itself.

### `hg_attendance_records`

Per-user attendance results from approved event clock-ins.

These should be generated from event flows, not typed in one-by-one for normal hosted events.

### `hg_sentry_logs`

Solo-sentry-only records.

This should stay separate from generic event attendance.

### `hg_event_records`

Hosted event records.

This is the main event-level object for:

- event type
- host
- attendee count
- archive sync
- schedule removal
- host-stat updates

### `hg_quota_cycles`

Cycle history / reset history.

This is useful for:

- recording reset windows
- recording who ran the reset
- capturing metadata about the cycle

It is not required to make clock-ins work in phase 1.

## Sheet Side Effects

When a hosted event is finalized, Jane should:

1. sync the relevant member point deltas
2. append the event to the archive sheet
3. remove or mark the event from the schedule sheet
4. increment the host's event-host stats

That archive/schedule/host-stat trio is part of the real workflow, not an optional nice-to-have.

## Permissions

Expected permission model:

- approvers are a dedicated Honor Guard reviewer / command role
- most hosting/cohosting/supervising permissions can be derived from rank roles
- special SGM hosting permission may need its own role if HG wants that granularity

Do not build a complicated custom permission DB unless the Discord role model genuinely cannot cover the needed cases.

## Phase 1 Priority

The safest implementation order is:

1. manual point-award approval flow
2. solo-sentry approval flow
3. event record + attendance clock-in flow
4. sheet sync for approved records
5. manual quota-cycle/reset tooling
6. quota automation later

More specifically, the project roadmap implied by the chat is:

1. generic division logging groundwork / future-proofed DB shapes
2. Honor Guard config placeholders and sheet-adapter skeleton
3. manual awarded-point logs first
4. event attendance logs next
5. officer host / co-host / supervisor handling
6. solo-sentry daily lockout
7. archive / schedule updates
8. bi-weekly quota reset and status update tooling
9. promotion-readiness reporting
10. only after live testing, consider heavier automation like promo automation

## Practical Rule

When branch code, transcript guesses, and current private backend disagree:

- prefer the real HG workflow described in the transcript
- prefer the private repo's newer table/service model over the older public-branch DB draft
- do not introduce a second member-state source of truth unless we explicitly choose that on purpose
