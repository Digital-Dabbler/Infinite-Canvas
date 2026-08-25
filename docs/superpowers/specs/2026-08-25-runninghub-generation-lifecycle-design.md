# RunningHub generation lifecycle design

## Goal

Prevent a submitted RunningHub image or video task from remaining in the smart
canvas as an unexplained, indefinitely running job. Preserve legitimate long
generations: an upstream API response that explicitly says queued or running
must not be reported as a generation failure merely because it is slow.

## Scope

- RunningHub image/video task polling performed by the canvas backend.
- Smart-canvas display and recovery handling for terminal, pending, and
  indeterminate task outcomes.
- Focused automated tests for lifecycle decisions.

The change does not alter provider credentials, billing rules, task ownership,
or non-RunningHub provider behavior.

## State model

The backend normalizes each upstream response before deciding what to show.

| Normalized state | Source | Action |
| --- | --- | --- |
| `succeeded` | Explicit API success plus usable output | Store result and finish the usage event as succeeded. |
| `failed` | Explicit API failure, cancellation, rejection, expiration, or terminal timeout | Store a readable error and finish the usage event as failed/cancelled/timed_out. |
| `pending` | Explicit API queued or running | Continue polling; reset the indeterminate-response counter. |
| `indeterminate` | Unknown API status, malformed response, or transient query/network failure | Retry under a bounded indeterminate policy; do not treat it as normal running. |

Only the first three classifications are derived from a provider-declared task
state. `indeterminate` is a local safety state and must preserve a safe,
sanitized diagnostic message.

## Timing policy

Timing is configured by task kind/model class rather than one blanket short
timeout.

- A task with repeated explicit `queued` or `running` responses may remain
  pending for up to 60 minutes from successful submission.
- Each upstream query has a short, bounded request timeout. A stuck HTTP call
  must not extend the 60-minute wall-clock deadline.
- Unknown statuses and query failures share a small consecutive retry budget.
  A successful explicit queued/running response resets that budget.
- When the 60-minute deadline is reached while the API still explicitly reports
  queued/running, the local canvas task ends as a recoverable,
  `upstream_still_processing` outcome. The UI says that upstream processing is
  still in progress and offers a later result query; it must not say generation
  failed.
- When the indeterminate retry budget is exhausted, the task ends as a
  recoverable `status_unconfirmed` outcome if an upstream task ID exists. If
  no reliable task ID exists, it ends as a normal failure.

The existing 20-second submission timeout remains a browser-side safeguard for
the request that creates the local task. Once creation returns a local task ID,
the backend owns upstream polling and persistence.

## Backend design

1. Centralize RunningHub response classification so standard-model, app, and
   workflow polling use the same explicit-terminal and explicit-pending rules.
2. Pass a monotonic overall deadline into each poll loop and cap each HTTP call
   by the remaining deadline and its per-request budget.
3. Persist the upstream task ID as soon as it is returned. Persist terminal
   reason, recoverability, and safe recovery metadata on every non-success
   terminal path.
4. Finish the matching usage event exactly once. A recoverable indeterminate
   result no longer remains queued/running and therefore cannot occupy a
   concurrency slot indefinitely.
5. Keep the restart behavior conservative: do not resubmit work automatically;
   retain the existing manual-recovery pattern when a provider task ID exists.

## Frontend design

- Continue polling local canvas task records, not RunningHub directly.
- Render explicit terminal failures immediately.
- For `upstream_still_processing` and `status_unconfirmed`, stop the animated
  running timer, show a distinct non-failure state and expose the existing
  manual query/recovery affordance when an upstream task ID is available.
- Preserve the elapsed duration as diagnostic history, but do not leave a
  node labelled as actively creating after its local polling lifecycle ended.
- Retain reload recovery and task ownership checks unchanged.

## Validation

Add focused tests for:

1. Explicit queued/running responses continue polling and do not consume the
   indeterminate retry budget.
2. Explicit failure/cancel/timeout statuses end promptly with the matching
   usage-event status.
3. Unknown status, malformed data, and transient query errors exhaust a
   bounded retry budget and produce `status_unconfirmed`, not endless running.
4. A pending response at the 60-minute deadline produces
   `upstream_still_processing`, preserves the upstream task ID, and releases
   local concurrency.
5. A per-query timeout cannot extend the overall deadline.
6. Smart-canvas recovery UI distinguishes these recoverable states from a
   generation failure and from an actively running task.

Run the relevant RunningHub, usage-accounting, API-profile, and smart-canvas
test suites, plus JavaScript syntax and diff hygiene checks. Browser validation
requires an authenticated local session and a safe test provider task.

## Acceptance criteria

- A real slow RunningHub task with explicit queued/running API responses is not
  marked failed before the 60-minute limit.
- Explicit upstream terminal failures appear promptly as errors.
- No unknown or broken upstream response leaves a canvas node in a perpetual
  creating state.
- A task that passes the 60-minute explicit-pending limit is recoverable and
  clearly described, rather than falsely labelled failed or still actively
  generating.
- Usage records and concurrency limits reach a terminal local state for every
  path.
