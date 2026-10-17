import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from 'wecom-slack-bridge/store';
import { ManualDispatch } from '../src/manual-dispatch.js';
import { AutoQueue, MAX_AUTO_SESSION_MS } from '../src/auto-queue.js';
function fixture(t, enabled = true) {
  const dir = mkdtempSync(join(tmpdir(), 'auto-queue-')); let store = new Store(dir), time = 100000;
  let dispatch = new ManualDispatch(store, { now: () => time });
  let auto = new AutoQueue(store, dispatch, { owner: 'owner', repository: 'example/project', now: () => time, enabled });
  t.after(() => { store.close(); rmSync(dir, { recursive: true, force: true }); });
  const f = { get store() { return store; }, get d() { return dispatch; }, get a() { return auto; },
    quota: () => ({ remainingPercent: 50, observedAt: time }), advance: ms => { time += ms; },
    start: (extra = {}) => auto.start({ sessionId: 's1', actor: 'owner', explicitOwnerStart: true, deadline: time + 100000, ...extra }),
    grant: (n = 1, extra = {}) => dispatch.registerGrant({ grantId: `g${n}`, actor: 'owner', kind: 'execution', decisionRecordOnly: false, plan: { id: `p${n}`, repo: 'example/project', issue_id: `${9000+n}`, issue_number: n, version: 1, base_sha: 'a'.repeat(40), files: ['src/a.js'], operations: ['edit-code', 'run-tests'], expires: time + 200000 }, ...extra }),
    enqueue: (n = 1, extra = {}) => auto.enqueue({ sessionId: 's1', grantId: `g${n}`, actor: 'owner', explicitAutoAuthorization: true, ...extra }),
    claim: (extra = {}) => auto.claim({ sessionId: 's1', controllerRunId: 'controller', attemptId: 'a1', quota: f.quota(), ...extra }),
    gate: (extra = {}) => auto.executionGate({ sessionId: 's1', method: 'startDispatch', controllerRunId: 'controller', attemptId: 'a1', quota: f.quota(), ...extra }),
    restart: () => { store.close(); store = new Store(dir); dispatch = new ManualDispatch(store, { now: () => time }); auto = new AutoQueue(store, dispatch, { owner: 'owner', repository: 'example/project', now: () => time, enabled }); }
  }; return f;
}
test('disabled by default and owner must explicitly approve a bounded fresh session', t => {
  const f = fixture(t, false); assert.throws(() => f.start(), /auto_disabled/);
  const g = fixture(t);
  for (const deadline of [100000, NaN, 100000 + MAX_AUTO_SESSION_MS + 1]) assert.throws(() => g.start({ deadline }), /invalid_auto_deadline/);
  assert.throws(() => g.start({ explicitOwnerStart: false }), /explicit_owner_start_required/);
  g.start({ deadline: 100000 + MAX_AUTO_SESSION_MS });
  assert.throws(() => g.start({ sessionId: 's2' }), /auto_session_active/);
  assert.equal(g.a.status({ sessionId: 's1' }).executorImplemented, false);
});
test('only explicit auto queue intent and immutable owner execution grants qualify', t => {
  const f = fixture(t); f.start();
  assert.throws(() => f.grant(1, { decisionRecordOnly: true }), /explicit_execution_grant_required/);
  assert.throws(() => f.enqueue(), /invalid_execution_grant/);
  f.grant();
  assert.throws(() => f.enqueue(1, { explicitAutoAuthorization: false }), /explicit_auto_authorization_required/);
  assert.throws(() => f.enqueue(1, { actor: 'other' }), /owner_mismatch/);
  const row = f.enqueue(); assert.equal(row.authorization, 'explicit_auto_queue'); assert.deepEqual(f.enqueue(), row);
  f.store.db.exec('CREATE TABLE execution_requests(id TEXT, plan TEXT)');
  f.grant(2); f.store.run('INSERT INTO execution_requests VALUES (?,?)', 'g2', 'p2');
  assert.throws(() => f.enqueue(2), /invalid_execution_grant/);
});
test('FIFO dedup and shared manual slot block all additional claims', t => {
  const f = fixture(t); f.start(); f.grant(1); f.grant(2); f.enqueue(2); f.enqueue(1);
  assert.equal(f.claim().attempt.grant_id, 'g2');
  assert.throws(() => f.d.reserve({ grantId: 'g1', controllerRunId: 'manual', attemptId: 'manual' }), /pipeline_busy/);
  assert.throws(() => f.claim({ attemptId: 'a2' }), /pipeline_busy/);
  f.gate(); f.d.recordStarted({ attemptId: 'a1', controllerRunId: 'controller', threadId: 'synthetic-thread', turnId: 'synthetic-turn' });
  assert.throws(() => f.claim({ attemptId: 'a2' }), /pipeline_busy/);
  f.d.confirmTerminal({ attemptId: 'a1', outcome: 'completed', workerStopped: true });
  assert.equal(f.claim({ attemptId: 'a2' }).attempt.grant_id, 'g1');
});
test('independent manual slot blocks auto; stopping auto leaves manual attempt untouched', t => {
  const f = fixture(t); f.start(); f.grant(); f.grant(2); f.enqueue();
  f.d.reserve({ grantId: 'g2', controllerRunId: 'manual', attemptId: 'manual' });
  assert.throws(() => f.claim(), /pipeline_busy/);
  f.a.stop({ sessionId: 's1', actor: 'owner' });
  assert.equal(f.d.status().active.stop_requested, 0);
});
for (const [name, quota] of [['unknown', null], ['stale', { remainingPercent: 50, observedAt: 39999 }], ['low', { remainingPercent: 5, observedAt: 100000 }]]) {
  test(`${name} quota durably pauses without a claim and cannot self resume`, t => {
    const f = fixture(t); f.start(); f.grant(); f.enqueue();
    assert.throws(() => f.claim({ quota }), /quota_/);
    assert.equal(f.a.session('s1').status, 'quota_paused'); assert.equal(f.d.status().active, null);
    assert.throws(() => f.claim(), /auto_session_inactive/);
    f.restart(); assert.throws(() => f.claim(), /auto_session_inactive/);
  });
}
for (const reason of ['stop', 'expiry', 'restart']) test(`${reason} fences auto-owned reservation and retains slot`, t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue(); f.claim();
  if (reason === 'stop') f.a.stop({ sessionId: 's1', actor: 'owner' });
  if (reason === 'expiry') f.advance(100000);
  if (reason === 'restart') f.restart();
  assert.throws(() => f.gate(), /auto_session_inactive/);
  assert.equal(f.d.status().active.stop_requested, 1); assert.equal(f.d.status().active.slot, 1);
  assert.throws(() => f.d.startDispatch({ attemptId: 'a1', controllerRunId: 'controller', quota: f.quota() }), /stop_requested|auto_session_inactive/);
  assert.throws(() => f.claim({ attemptId: 'a2' }), /auto_session_inactive/);
});
test('old queued grants never move into a fresh session', t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue(); f.a.stop({ sessionId: 's1', actor: 'owner' });
  f.start({ sessionId: 's2' });
  assert.throws(() => f.enqueue(1, { sessionId: 's2' }), /grant_already_queued/);
  assert.equal(f.claim({ sessionId: 's2' }), null);
  assert.throws(() => f.start(), /session_id_reused/);
});
test('revoked queue heads are skipped and manual takeover is rejected', t => {
  const f = fixture(t); f.start(); for (let n=1;n<=3;n++) { f.grant(n); f.enqueue(n); }
  f.d.revokeGrant({ grantId: 'g1' });
  assert.throws(() => f.d.reserve({ grantId: 'g2', controllerRunId: 'manual', attemptId: 'manual' }), /auto_session_inactive/);
  f.d.revokeGrant({ grantId: 'g2' });
  assert.equal(f.claim().attempt.grant_id, 'g3');
  assert.deepEqual(f.a.status({ sessionId: 's1' }).entries.map(e => e.status), ['ineligible', 'ineligible', 'claimed']);
});
test('reservation and queue update roll back atomically on write failure', t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue();
  f.store.db.exec("CREATE TRIGGER reject_claim BEFORE UPDATE ON auto_queue_entries WHEN NEW.status='claimed' BEGIN SELECT RAISE(ABORT, 'synthetic_failure'); END");
  assert.throws(() => f.claim(), /synthetic_failure/);
  assert.equal(f.d.status().active, null); assert.equal(f.a.status({ sessionId: 's1' }).entries[0].status, 'queued');
  f.store.db.exec('DROP TRIGGER reject_claim'); assert.equal(f.claim().attempt.status, 'reserved');
});
test('running auto checkpoint quota failure pauses session and latches stop', t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue(); f.claim(); f.gate();
  f.d.recordStarted({ attemptId: 'a1', controllerRunId: 'controller', threadId: 'synthetic-thread', turnId: 'synthetic-turn' });
  assert.throws(() => f.gate({ method: 'checkpoint', files: ['src/a.js'], operation: 'edit-code', quota: null }), /quota_unknown_or_stale/);
  assert.equal(f.a.session('s1').status, 'quota_paused'); assert.equal(f.d.status().active.stop_requested, 1);
});
test('fixed trusted owner and repository reject cross-scope grants', t => {
  const f = fixture(t);
  assert.throws(() => new AutoQueue(f.store, f.d), /invalid_configuration/);
  assert.throws(() => f.start({ actor: 'other' }), /explicit_owner_start_required/);
  f.start();
  f.grant(1, { actor: 'other' }); assert.throws(() => f.enqueue(), /invalid_execution_grant/);
  f.grant(2, { plan: { id: 'p2', repo: 'other/project', issue_id: '9002', issue_number: 2, version: 1, base_sha: 'b'.repeat(40), files: ['src/a.js'], operations: ['run-tests'], expires: 200000 } });
  assert.throws(() => f.enqueue(2), /invalid_execution_grant/);
});
test('manual request recorded after enqueue excludes candidate at claim', t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue();
  f.store.db.exec('CREATE TABLE execution_requests(id TEXT, plan TEXT)');
  f.store.run('INSERT INTO execution_requests VALUES (?,?)', 'different-id', 'p1');
  assert.equal(f.claim(), null); assert.equal(f.a.status({ sessionId: 's1' }).entries[0].status, 'ineligible');
});
for (const stage of ['reserved', 'running']) test(`direct manual ${stage} route cannot bypass auto expiry without polling`, t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue(); f.claim();
  if (stage === 'running') {
    f.gate(); f.d.recordStarted({ attemptId: 'a1', controllerRunId: 'controller', threadId: 'synthetic-thread', turnId: 'synthetic-turn' });
  }
  f.advance(100000);
  const args = { attemptId: 'a1', controllerRunId: 'controller', quota: f.quota(), operation: 'edit-code', files: ['src/a.js'] };
  assert.throws(() => stage === 'running' ? f.d.checkpoint(args) : f.d.startDispatch(args), /auto_session_inactive/);
  assert.equal(f.d.status().active.stop_requested, 1);
  assert.equal(f.d.status().active.slot, 1);
});
test('wrong owner stop and wrong controller quota cannot stop legitimate active work', t => {
  const f = fixture(t); f.start(); f.grant(); f.enqueue(); f.claim();
  assert.throws(() => f.a.stop({ sessionId: 's1', actor: 'other' }), /owner_mismatch/);
  assert.throws(() => f.gate({ controllerRunId: 'other', quota: null }), /controller_mismatch/);
  assert.equal(f.a.session('s1').status, 'active'); assert.equal(f.d.status().active.stop_requested, 0);
});

for (const action of ['unknown', 'stop', 'terminal', 'quota']) test(`direct ${action} stops the auto session before another queued claim`, t => {
  const f = fixture(t); f.start(); f.grant(); f.grant(2); f.enqueue(); f.enqueue(2); f.claim();
  const args = { attemptId: 'a1', controllerRunId: 'controller' };
  if (action === 'unknown') { f.gate(); f.d.markUnknown(args); }
  if (action === 'stop') f.d.requestStop(args);
  if (action === 'quota') assert.throws(() => f.d.startDispatch({ ...args, quota: null }), /quota_unknown_or_stale/);
  f.d.confirmTerminal({ attemptId: 'a1', outcome: 'stopped', workerStopped: true });
  assert.throws(() => f.claim({ attemptId: 'a2' }), /auto_session_inactive/);
  assert.equal(f.d.status().active, null);
});
