# Dormant automatic queue state

This is a local, synthetic-test-only state core. `src/auto-queue.js` is not imported
by the runtime. No owner command, environment flag, network call, timer, scanner,
worker creation, deployment, or automatic resumption has been connected. Existing
manual execution behavior stays unchanged for manual-owned attempts. Passing
`enabled: true` to a test object permits state transitions only; it does not enable
production automatic development. No actual issue is executed by these tests.

## Trusted API and authorization

Construct `AutoQueue(store, manualDispatch, { owner, repository, now, enabled })`
with the same authoritative bridge Store and ManualDispatch object. `enabled`
defaults to false. Owner and repository are fixed trusted configuration, not chat
arguments. Actor strings and quota snapshots are not authentication or proof:
a future adapter must verify the actual owner, source, immutable current plan,
repository/file scope, delivery binding, quota provenance and execution readiness.

- `start({ sessionId, actor, explicitOwnerStart: true, deadline })` records an
  explicit owner session with a future deadline no more than seven days away.
  Three days may be selected by a future user interface; there is no default
  implicit authorization. Only one active session exists. IDs cannot be reused.
- `enqueue({ sessionId, actor, grantId, explicitAutoAuthorization: true })` records
  separately authenticated owner intent to queue that exact immutable grant.
  It requires a matching owner and repository, an unused, unrevoked, unexpired
  explicit execution grant, and no prior attempt. It persists owner and
  `explicit_auto_queue` provenance. This marker does not itself authenticate the
  caller. Review-only approvals are never read or promoted to grants.
- Existing manual `/execute` requests are excluded by grant or plan identity at
  enqueue and again at claim, even if their manual notification has not delivered.
  There is no current automatic grant ingress. A future authenticated auto ingress
  must create its own explicitly authorized immutable grant without a competing
  manual dispatch notification; that integration is outside this change.
- `claim({ sessionId, controllerRunId, attemptId, quota })` selects the oldest
  eligible queued grant. Expired, revoked, consumed, attempted, or newly manual
  candidates are marked ineligible. Exact grant IDs are globally deduplicated;
  old queue entries never move into a later session, and neither cancellation nor
  failure permits automatic retry. A later attempt needs a newly approved grant.

## One pipeline and fail-closed continuation

Queue selection and `ManualDispatch.executeInTransaction('reserve', ...)` share
one Store transaction. There is no second pipeline lock. A manual or automatic
reserved, dispatch-started, running, or unknown attempt holds the same existing
single slot. Queue-write failure rolls back reservation. Claim is not dispatch
or evidence that a worker exists. No grant is consumed until startDispatch.

Use `executionGate({ sessionId, method: 'startDispatch' | 'checkpoint', ...args })`
for every future auto execution stage. It verifies session membership before
calling the existing ManualDispatch protocol in the same transaction. Scope,
controller identity, consumed grants and delivery targets retain their existing
checks. The tiny ManualDispatch grant guard also checks an auto-owned attempt's
persisted session status/deadline, so a direct manual control call cannot bypass
an expired or stopped automatic session. Session denial durably latches the
attempt's stop flag; the control protocol recognizes it as a rejected operation.

Queued auto grants cannot be taken over by direct manual reservation. The atomic
claim binds the queue entry before reserving; both writes commit together. Direct
manual control quota failure, stop, unknown outcome, revoked active grant, or a
stopped terminal outcome also pauses its bound auto session. Releasing the slot
after an uncertain/failed/stopped task does not authorize the next queued task.
Only a verified successful completion leaves an otherwise valid session active.

Unknown, invalid, future-dated, or older-than-60-second quota, and remaining quota
at or below five percent, pause the session durably. A fresh observation does not
resume it. The future adapter must supply trusted live quota evidence. There is
no job-count allowance: the bounds here are the explicit deadline, individually
approved grants, quota, and single concurrency.

`stop({ sessionId, actor })` prevents further claims immediately and latches stop
on that session's active attempt only. Expiry is evaluated at each state access
or execution gate; there are no background timers. Create one AutoQueue per process/controller lifetime, not per query. Creating
another AutoQueue instance represents controller restart and durably pauses all previously active
sessions, including when the new instance is disabled. It never adopts or replays
an old attempt. Future integration must construct this recovery component before
exposing any dispatch/control ingress; ManualDispatch alone cannot detect a
process restart. Stop, expiry, quota failure and restart never release a live or
unknown slot and never interrupt an already in-flight external side effect.
Actual interruption/reconciliation remains the trusted controller's duty.

`status({ sessionId })` reports state, ordered queue and shared active attempt;
`executorImplemented` remains false. A trusted controller must verify the worker
stopped or was never created before using the existing terminal protocol. Neither
completion nor improved quota renews a session. A new session requires a fresh
explicit owner start, and no old queued grants are carried forward.

## Validation and remaining integration

`node --test test/auto-queue.test.js` covers synthetic owner/repository isolation,
manual-request exclusion (including after enqueue), FIFO/deduplication, atomic
rollback, shared-slot contention, unknown outcomes, deadline bounds, stop,
restart, stale/low/unknown quota, durable pause and direct manual-route expiry
fencing. `npm run check` runs the entire local suite.

Before any runtime activation, separately implement and approve authenticated
owner commands, distinct auto-grant ingress, trusted quota retrieval, live task
creation and cancellation, source/symlink scope checks, failure handling, result
verification, and deployment/rollback acceptance. These tests do not establish
that those integrations exist or that automation is ready to enable.
