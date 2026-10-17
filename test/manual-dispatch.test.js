import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from 'wecom-slack-bridge/store';
import { ManualDispatch } from '../src/manual-dispatch.js';
const plan = (extra = {}) => ({ id: 'plan-1', repo: 'example/project', issue_id: '9001', issue_number: 12, version: 1, base_sha: 'a'.repeat(40), files: ['src/a.js'], operations: ['edit-code', 'run-tests'], expires: 200000, ...extra });
function fixture(t) {
  const dir = mkdtempSync(join(tmpdir(), 'manual-dispatch-')); let store = new Store(dir), time = 100000;
  let control = new ManualDispatch(store, { now: () => time });
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  const ids = { attemptId: 'attempt-1', controllerRunId: 'controller-1' };
  return { get store() { return store; }, get c() { return control; }, ids,
    quota: () => ({ remainingPercent: 50, observedAt: time }),
    advance(ms) { time += ms; },
    grant(extra = {}) { return control.registerGrant({ grantId: 'grant-1', plan: plan(), kind: 'execution', decisionRecordOnly: false, actor: 'authenticated-owner', ...extra }); },
    reserve() { return control.reserve({ ...ids, grantId: 'grant-1' }); },
    start() { return control.startDispatch({ ...ids, quota: this.quota() }); },
    running() { this.grant(); this.reserve(); this.start(); return control.recordStarted({ ...ids, threadId: 'thread-1', turnId: 'turn-1' }); },
    checkpoint(extra = {}) { return control.checkpoint({ ...ids, operation: 'edit-code', files: ['src/a.js'], quota: this.quota(), ...extra }); },
    restart() { store.close(); store = new Store(dir); control = new ManualDispatch(store, { now: () => time }); }
  };
}
test('explicit grant and immutable full binding; record-only decisions cannot authorize', t => {
  const f = fixture(t);
  for (const args of [{ kind: 'decision' }, { decisionRecordOnly: true }, { decisionRecordOnly: undefined }]) assert.throws(() => f.grant(args), /explicit_execution_grant_required/);
  f.grant();
  for (const delta of [{ repo: 'other/repo' }, { issue_id: '9002' }, { issue_number: 13 }, { version: 2 }, { base_sha: 'b'.repeat(40) }, { files: ['src/b.js'] }, { operations: ['run-tests'] }, { expires: 210000 }]) assert.throws(() => f.grant({ plan: plan(delta) }), /immutable_grant_mismatch/);
  assert.equal(f.grant().used, 0);
  assert.throws(() => f.grant({ grantId: 'grant-2' }), /immutable_grant_mismatch/);
});
test('rejects unsafe file scopes and operations including PR creation', t => {
  const f = fixture(t);
  for (const files of [[], ['../file'], ['/etc/passwd'], ['.git/config'], ['src/*'], ['.env'], ['config/.env.production'], ['.ssh/id_rsa'], ['.aws/credentials'], ['src/a.js', 'src/a.js']]) assert.throws(() => f.grant({ plan: plan({ files }) }), /invalid_scope/);
  for (const operations of [[], ['open-draft-pr'], ['delete-repo'], ['run-tests', 'run-tests']]) assert.throws(() => f.grant({ plan: plan({ operations }) }), /invalid_operations/);
});
test('ingress grant and outbox share one atomic transaction', t => {
  const f = fixture(t);
  assert.throws(() => f.store.transaction(() => {
    f.c.registerGrantInTransaction({ grantId: 'g', plan: plan(), actor: 'owner', kind: 'execution', decisionRecordOnly: false });
    f.store.addJob('r', 'slack', { text: 'authorized' });
    throw new Error('rollback');
  }), /rollback/);
  assert.equal(f.store.get('SELECT COUNT(*) AS n FROM manual_execution_grants').n, 0);
  assert.equal(f.store.get('SELECT COUNT(*) AS n FROM jobs').n, 0);
});
test('reserve is quota-free, atomic single slot and idempotent only for same controller/attempt', t => {
  const f = fixture(t); f.grant(); const a = f.reserve(); assert.deepEqual(f.reserve(), a);
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 0);
  assert.throws(() => f.c.reserve({ ...f.ids, grantId: 'grant-1', controllerRunId: 'other' }), /attempt_identity_mismatch/);
  f.grant({ grantId: 'grant-2', plan: plan({ id: 'plan-2', version: 2 }) });
  assert.throws(() => f.c.reserve({ attemptId: 'attempt-2', controllerRunId: 'controller-2', grantId: 'grant-2' }), /pipeline_busy/);
  assert.equal(f.c.status().executorImplemented, false);
});
for (const [label, quota, error] of [
  ['unknown', null, /quota_unknown_or_stale/],
  ['non-numeric', { remainingPercent: '50', observedAt: 100000 }, /quota_unknown_or_stale/],
  ['NaN', { remainingPercent: NaN, observedAt: 100000 }, /quota_unknown_or_stale/],
  ['out of range', { remainingPercent: 101, observedAt: 100000 }, /quota_unknown_or_stale/],
  ['future', { remainingPercent: 50, observedAt: 100001 }, /quota_unknown_or_stale/],
  ['stale', { remainingPercent: 50, observedAt: 39999 }, /quota_unknown_or_stale/],
  ['zero', { remainingPercent: 0, observedAt: 100000 }, /quota_too_low/],
  ['threshold', { remainingPercent: 5, observedAt: 100000 }, /quota_too_low/],
]) test(`start ${label} quota latches stop across restart without consuming grant`, t => {
  const f = fixture(t); f.grant(); f.reserve();
  assert.throws(() => f.c.startDispatch({ ...f.ids, quota }), error);
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 0);
  assert.equal(f.c.status().active.stop_requested, 1);
  assert.equal(f.c.status().active.status, 'reserved');
  assert.equal(f.c.status().active.thread_id, null);
  f.restart();
  assert.throws(() => f.start(), /stop_requested/);
  assert.equal(f.c.status().active.slot, 1);
  f.c.confirmTerminal({ attemptId: f.ids.attemptId, outcome: 'stopped', workerStopped: true });
  assert.equal(f.c.status().active, null);
  assert.throws(() => f.c.reserve({ grantId: 'grant-1', attemptId: 'new-attempt', controllerRunId: 'controller-1' }), /grant_already_reserved/);
});
test('valid boundary quota starts once; wrong controller cannot latch a stop', t => {
  const f = fixture(t); f.grant(); f.reserve();
  assert.throws(() => f.c.startDispatch({ ...f.ids, controllerRunId: 'other', quota: null }), /controller_mismatch/);
  assert.equal(f.c.status().active.stop_requested, 0);
  f.c.startDispatch({ ...f.ids, quota: { remainingPercent: 5.01, observedAt: 40000 } });
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 1);
  assert.equal(f.c.status().active.status, 'dispatch_started');
  assert.throws(() => f.start(), /dispatch_already_started/);
});
test('composed start quota failure commits with rejection, rolls back with failed outbox', t => {
  const f = fixture(t); f.grant(); f.reserve();
  assert.throws(() => f.store.transaction(() => {
    assert.throws(() => f.c.executeInTransaction('startDispatch', { ...f.ids, quota: null }), /quota_unknown_or_stale/);
    assert.equal(f.c.status().active.stop_requested, 1);
    throw new Error('outbox_failure');
  }), /outbox_failure/);
  assert.equal(f.c.status().active.stop_requested, 0);
  f.store.transaction(() => {
    assert.throws(() => f.c.executeInTransaction('startDispatch', { ...f.ids, quota: null }), /quota_unknown_or_stale/);
    f.store.addJob('r', 'slack', { text: 'quota_unknown_or_stale' });
  });
  f.restart();
  assert.equal(f.c.status().active.stop_requested, 1);
  assert.equal(f.store.get('SELECT COUNT(*) AS n FROM jobs').n, 1);
  assert.throws(() => f.start(), /stop_requested/);
});
test('restart and uncertain task creation retain slot and never replay', t => {
  const f = fixture(t); f.grant(); f.reserve(); f.start(); f.restart();
  assert.equal(f.reserve().status, 'dispatch_started');
  assert.throws(() => f.start(), /dispatch_already_started/);
  assert.throws(() => f.c.startDispatch({ ...f.ids, controllerRunId: 'new-controller', quota: f.quota() }), /controller_mismatch/);
  f.c.markUnknown(f.ids); f.advance(1_000_000); f.restart();
  assert.equal(f.c.status().active.status, 'unknown');
  assert.throws(() => f.c.confirmTerminal({ attemptId: f.ids.attemptId, outcome: 'stopped' }), /terminal_confirmation_required/);
  f.c.confirmTerminal({ attemptId: f.ids.attemptId, outcome: 'stopped', workerStopped: true });
  assert.equal(f.c.status().active, null);
  assert.throws(() => f.start(), /attempt_not_active/);
});
test('record exact task identity then enforce every stage scope and quota', t => {
  const f = fixture(t); f.grant(); f.reserve(); f.start();
  assert.throws(() => f.checkpoint(), /worker_not_confirmed/);
  assert.throws(() => f.c.recordStarted({ ...f.ids, threadId: 'thread' }), /task_identity_required/);
  f.c.recordStarted({ ...f.ids, threadId: 'thread-1', turnId: 'turn-1' });
  assert.equal(f.checkpoint().plan.base_sha, 'a'.repeat(40));
  assert.throws(() => f.checkpoint({ operation: 'open-draft-pr' }), /operation_not_granted/);
  for (const operation of ['commit', 'push', 'open-pr', 'merge', 'deploy']) assert.throws(() => f.checkpoint({ operation, delivery: delivery() }), /operation_not_granted/);
  assert.throws(() => f.checkpoint({ files: ['src/b.js'] }), /file_not_granted/);
  assert.throws(() => f.checkpoint({ quota: null }), /quota_unknown_or_stale/);
  assert.throws(() => f.checkpoint(), /stop_requested/);
  assert.throws(() => f.c.recordStarted({ ...f.ids, threadId: 'thread-2', turnId: 'turn-1' }), /task_identity_mismatch/);
  f.c.confirmTerminal({ attemptId: f.ids.attemptId, outcome: 'completed', workerStopped: true });
  assert.equal(f.c.status().active, null);
});
test('stop, revoke and expiry each block the next stage without freeing the slot', t => {
  const f = fixture(t); f.running(); f.c.requestStop({ attemptId: f.ids.attemptId });
  assert.throws(() => f.checkpoint(), /stop_requested/); assert.equal(f.c.status().active.slot, 1);
});
test('revocation prevents both dispatch and stage continuation', t => {
  const f = fixture(t); f.grant(); f.reserve(); f.c.revokeGrant({ grantId: 'grant-1' });
  assert.throws(() => f.start(), /grant_revoked_or_expired/); assert.equal(f.c.status().active.slot, 1);
});
test('expiry blocks continuation, no automatic lease and no resurrection via duplicate grant', t => {
  const f = fixture(t); f.running(); f.advance(100000);
  assert.throws(() => f.checkpoint(), /grant_revoked_or_expired/);
  assert.throws(() => f.grant(), /grant_expired/);
  assert.equal(f.c.status().active.slot, 1);
});
test('unknown can be reconciled to exact known task; recording after stop cannot authorize next stage', t => {
  const f = fixture(t); f.grant(); f.reserve(); f.start(); f.c.markUnknown(f.ids);
  f.c.requestStop({ attemptId: f.ids.attemptId });
  f.c.recordStarted({ ...f.ids, threadId: 'thread-1', turnId: 'turn-1' });
  assert.throws(() => f.checkpoint(), /stop_requested/);
});
test('pre-dispatch stop blocks task creation and terminal reserve never recycles grant', t => {
  const f = fixture(t); f.grant(); f.reserve(); f.c.requestStop({ attemptId: f.ids.attemptId });
  assert.throws(() => f.start(), /stop_requested/);
  assert.throws(() => f.c.confirmTerminal({ attemptId: f.ids.attemptId, outcome: 'completed', workerStopped: true }), /completion_not_confirmed/);
  f.c.confirmTerminal({ attemptId: f.ids.attemptId, outcome: 'stopped', workerStopped: true });
  assert.equal(f.reserve().status, 'stopped');
  assert.throws(() => f.c.reserve({ grantId: 'grant-1', attemptId: 'attempt-2', controllerRunId: 'controller-2' }), /grant_already_reserved/);
});
test('running revocation fences checkpoint and preserves known live task', t => {
  const f = fixture(t); f.running(); f.c.revokeGrant({ grantId: 'grant-1' });
  assert.throws(() => f.checkpoint(), /grant_revoked_or_expired/);
  assert.equal(f.c.status().active.thread_id, 'thread-1');
  assert.equal(f.c.status().active.slot, 1);
});

test('sparse paths and operation bundles fail closed', t => {
  const f = fixture(t);
  assert.throws(() => f.grant({ plan: plan({ files: new Array(1) }) }), /invalid_scope/);
  assert.throws(() => f.grant({ plan: plan({ operations: new Array(1) }) }), /invalid_operations/);
  f.running();
  assert.throws(() => f.checkpoint({ files: new Array(1) }), /file_not_granted/);
});
test('low quota checkpoint durably stops and fresh quota cannot self-resume', t => {
  const f = fixture(t); f.running();
  assert.throws(() => f.checkpoint({ quota: { ...f.quota(), remainingPercent: 5 } }), /quota_too_low/);
  f.restart();
  assert.throws(() => f.checkpoint(), /stop_requested/);
  assert.equal(f.c.status().active.slot, 1);
});

test('transaction-only composition seams reject calls outside a transaction', t => {
  const f = fixture(t);
  assert.throws(() => f.c.registerGrantInTransaction({ grantId: 'g', plan: plan(), actor: 'owner', kind: 'execution', decisionRecordOnly: false }), /transaction_required/);
  assert.throws(() => f.c.executeInTransaction('reserve', { ...f.ids, grantId: 'g' }), /transaction_required/);
  assert.equal(f.store.get('SELECT COUNT(*) AS n FROM manual_execution_grants').n, 0);
});
const delivery = (extra = {}) => ({ target_branch: 'main', work_branch: 'hiveflow/issue-12', deployment: { environment: 'pve', service: 'hiveflow', verification: 'Check service health and smoke tests', rollback: 'Restore verified backup and restart previous service' }, ...extra });
test('delivery grant immutably binds branches, deployment, verification and rollback', t => {
  const f = fixture(t), operations = ['edit-code', 'run-tests', 'commit', 'push', 'open-pr', 'merge', 'deploy'];
  assert.throws(() => f.grant({ plan: plan({ operations }) }), /invalid_delivery/);
  f.grant({ plan: plan({ operations, delivery: delivery() }) });
  for (const d of [delivery({ target_branch: 'release' }), delivery({ work_branch: 'other' }), ...['environment', 'service', 'verification', 'rollback'].map(k => delivery({ deployment: { ...delivery().deployment, [k]: 'changed' } }))]) assert.throws(() => f.grant({ plan: plan({ operations, delivery: d }) }), /immutable_grant_mismatch/);
  f.reserve(); const started = f.start(); assert.deepEqual(started.plan.delivery, delivery());
  f.c.recordStarted({ ...f.ids, threadId: 'thread-1', turnId: 'turn-1' });
  for (const operation of ['commit', 'push', 'open-pr', 'merge', 'deploy']) {
    assert.throws(() => f.checkpoint({ operation }), /delivery_scope_mismatch/);
    assert.throws(() => f.checkpoint({ operation, delivery: delivery({ target_branch: 'release' }) }), /delivery_scope_mismatch/);
    assert.throws(() => f.checkpoint({ operation, delivery: delivery({ deployment: { ...delivery().deployment, environment: 'other' } }) }), /delivery_scope_mismatch/);
    assert.deepEqual(f.checkpoint({ operation, delivery: delivery() }).delivery, delivery());
  }
  assert.throws(() => f.checkpoint({ delivery: delivery() }), /delivery_scope_mismatch/);
});
test('delivery canonicalization tolerates key order, never broadens edit/test grants', t => {
  const f = fixture(t), d = { deployment: null, work_branch: 'hiveflow/issue-12', target_branch: 'main' };
  f.grant({ plan: plan({ operations: ['push'], delivery: d }) }); f.reserve(); f.start(); f.c.recordStarted({ ...f.ids, threadId: 'thread', turnId: 'turn' });
  assert.equal(f.checkpoint({ operation: 'push', delivery: { target_branch: 'main', work_branch: 'hiveflow/issue-12', deployment: null } }).operation, 'push');
  assert.throws(() => f.checkpoint({ operation: 'deploy', delivery: delivery() }), /operation_not_granted/);
  assert.throws(() => f.checkpoint({ operation: 'push', delivery: { ...d, extra: true } }), /delivery_scope_mismatch/);
});
