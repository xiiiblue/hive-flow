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
  for (const operations of [[], ['open-draft-pr'], ['push'], ['run-tests', 'run-tests']]) assert.throws(() => f.grant({ plan: plan({ operations }) }), /invalid_operations/);
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
test('quota must be fresh numeric verified-adapter minimum-window percentage strictly above 5', t => {
  const f = fixture(t); f.grant(); f.reserve();
  for (const quota of [null, { remainingPercent: '50', observedAt: 100000 }, { remainingPercent: NaN, observedAt: 100000 }, { remainingPercent: 101, observedAt: 100000 }, { remainingPercent: 50, observedAt: 100001 }, { remainingPercent: 50, observedAt: 39999 }]) assert.throws(() => f.c.startDispatch({ ...f.ids, quota }), /quota_unknown_or_stale/);
  for (const remainingPercent of [0, 5]) assert.throws(() => f.c.startDispatch({ ...f.ids, quota: { remainingPercent, observedAt: 100000 } }), /quota_too_low/);
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 0);
  f.c.startDispatch({ ...f.ids, quota: { remainingPercent: 5.01, observedAt: 40000 } });
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 1);
  assert.equal(f.c.status().active.status, 'dispatch_started');
  assert.throws(() => f.start(), /dispatch_already_started/);
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
