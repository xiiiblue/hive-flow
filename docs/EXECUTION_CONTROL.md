# Manual execution control protocol

This is a local, opt-in state protocol for a trusted parent controller. It does not call `cloud_threads`, create tasks, run commands, push commits, publish, or deploy. See [manual execution boundaries](MANUAL_EXECUTION.md). The deployment flag enables request/control processing only; it proves neither adapter readiness nor permission to execute an Issue.

## Authenticated ingress and routing

Send a **new root message** through the configured Slack responder identity to the configured channel:

```text
[wecom:execution-control]{"command_id":"command-uuid","action":"reserve","plan_id":"plan-uuid","grant_id":"grant-uuid","controller_run_id":"controller-uuid","attempt_id":"attempt-uuid"}
```

The bridge authenticates that outer sender/channel before calling the application. Thread replies, edited messages, other senders, and disabled deployments are rejected. Do not treat strings inside JSON as authentication.

Every command must include exactly these common fields:

- `command_id`: unique command identity, retained across retries of that command
- `action`: one of the actions below
- `plan_id`: immutable persisted plan UUID
- `grant_id`: the separate explicit execution grant UUID, equal to the approved execution request ID
- `controller_run_id`: controller-run identity that reserved the attempt
- `attempt_id`: unique execution-attempt identity

IDs are 1–200 characters from letters, digits, underscore, dot, colon, and hyphen. Extra fields, including supplied destination/channel/root, are rejected. The plan's persisted repository, owner, channel, original request, and root are revalidated against configuration. An existing attempt must match both grant and controller. Changing an ID does not transfer or reclaim a slot.

## Actions and exact additional fields

| Action | Additional fields | Effect |
|---|---|---|
| `reserve` | none | Atomically reserves the single pipeline slot for read-only preflight; no quota needed, no task created |
| `start` | `quota` | Consumes the grant and persists `dispatch_started` **before** external task creation |
| `started` | `thread_id`, `turn_id` | Records the exact task and turn after verified creation/reconciliation |
| `checkpoint` | `operation`, `files`, `quota` | Gates the next cooperative worker stage against grant, expiry, stop, scope, and quota |
| `status` | none | Reports only the specified bound attempt |
| `stop` | none | Durably requests a cooperative stop; does not kill a worker or free the slot |
| `unknown` | none | Records uncertainty and retains the occupied slot |
| `terminal` | `outcome`, `worker_stopped` | Releases only after explicit verification; outcome is `stopped` or `completed`, and `worker_stopped` must be `true` |

`quota` is exactly `{"remainingPercent":50,"observedAt":1700000000000}` or `null` for unavailable quota. The trusted controller must obtain verified provider/account data and supply the minimum percentage across applicable finite windows. Numeric remaining quota must be greater than 5 and at most 100; observation must not be future-dated or older than 60 seconds. User or Issue text is never a quota source. A low/stale/unknown running checkpoint durably latches stop; a later fresh reading does not resume it.

`operation` is `edit-code` or `run-tests`. `files` lists exact approved relative paths; no globs, traversal, Git metadata, or credential paths. The actual adapter must still enforce repository, pinned base commit, symlink/real-path scope, and safe test commands. A stored scope is not a sandbox.

Expiry and supersession block new execution stages. Known task identity, status, stop, unknown, and terminal recovery remain possible after expiry/supersession so that a live worker is never hidden. Completion requires a known running task; uncertain or never-created tasks can only be reconciled as stopped after verification.

## Acknowledgements, deduplication, and uncertainty

The command audit, state transition, and Slack acknowledgement outbox entry commit in one shared-Store transaction. Acknowledgements are authored by the bridge and posted to the **original execution-plan thread**, never the control-message root. Their metadata event type is `hiveflow_execution_control_result`; event payload includes the common IDs, `ok`, and either `state` or `error`. Reject messages with the wrong bridge identity, plan, grant, controller, attempt, or command.

An identical duplicate command returns its prior acceptance without re-running the operation or queuing another acknowledgement. Reusing a command ID with a different body is rejected. Repeating `start` with a new command ID after the grant is consumed produces `dispatch_already_started`, never another creation permit. A failed outbox insertion rolls the state change back.

The parent must persist its own external-create intent and correlate an acknowledged first `start` with exactly one `cloud_threads.create` call. Duplicate/delayed acknowledgements never authorize a second call. A timeout, missing acknowledgement, process restart, or uncertain create response must **not** trigger automatic replay. Inspect the persisted bound attempt and external task state; use `unknown` if unresolved. The slot has no lease and never expires automatically. A different controller cannot reclaim it.

Do not assert `worker_stopped:true` because a controller timed out, a task window closed, or a stop was merely requested. Verify the worker is terminal (or verify no task was created), then explicitly reconcile. This protocol provides cooperative checkpoints, not instantaneous cancellation.
