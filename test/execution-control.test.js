import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from 'wecom-slack-bridge/store';
import { Bridge } from 'wecom-slack-bridge/bridge';
import { IssueReviewApplication } from '../src/issue-review.js';
import { validExecutionReport } from '../src/execution-results.js';
import { parseExecutionControl, initializeExecutionControl } from '../src/execution-control.js';
const repository = 'example/project';
const plan = { repo: repository, issue_id: '9001', issue_number: 12, version: 1, base_sha: 'a'.repeat(40), files: ['README.md'], operations: ['edit-code', 'run-tests'], summary: 'Update documentation', expires: 200000 };
async function fixture(t, ready = true, planExtra = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'control-test-')), store = new Store(dir); let time = 100000, seq = 0;
  const application = new IssueReviewApplication({ issueRepository: repository }, { manualExecutionEnabled: ready });
  const config = { wecomBotId: 'test-bot', ownerUser: 'owner', allowedChats: new Set(), slackChannel: 'C123', replyBot: 'BDOT', ttlMs: 3600000, maxBytes: 32000 };
  application.initialize({ store, now: () => time }); initializeExecutionControl(store);
  const posted = [], bridge = new Bridge({ store, application, config, now: () => time, wait: async () => {}, slack: { post: async p => { posted.push(p); return { ok: true, ts: '999.1' }; } }, wecom: { ack: async () => {}, send: async () => ({ errcode: 0 }) } });
  bridge.wecomReady = true;
  t.after(async () => { await bridge.stop(); store.close(); rmSync(dir, { recursive: true, force: true }); });
  bridge.receiveSlack({ type: 'message', channel: 'C123', ts: '100.1', bot_id: 'BDOT', text: '[wecom:execution-plan]' + JSON.stringify({ ...plan, ...planExtra }) }); await bridge.drain();
  const row = store.get('SELECT * FROM execution_plans');
  bridge.receiveWecom({ body: { aibotid: 'test-bot', msgid: 'approval', msgtype: 'text', chattype: 'single', from: { userid: 'owner' }, text: { content: `/execute #12 v1 ${row.id}` } } }); await bridge.drain();
  const grant = store.get('SELECT * FROM execution_requests'); posted.length = 0;
  return { dir, store, bridge, application, posted, row, grant, advance(ms) { time += ms; },
    command(action, extra = {}) { return { command_id: `command-${++seq}`, action, plan_id: row.id, grant_id: grant.id, controller_run_id: 'controller-1', attempt_id: 'attempt-1', ...extra }; },
    send(p, extra = {}) { return bridge.receiveSlack({ type: 'message', channel: 'C123', bot_id: 'BDOT', ts: `200.${++seq}`, text: '[wecom:execution-control]' + JSON.stringify(p), ...extra }); },
    quota() { return { remainingPercent: 50, observedAt: time }; },
    result() { return JSON.parse(store.get('SELECT result FROM execution_control_commands ORDER BY rowid DESC LIMIT 1').result); }
  };
}
test('strict schemas reject routing injection, unsupported operations and missing confirmation', () => {
  const base = { command_id: 'c', action: 'reserve', plan_id: 'p', grant_id: 'g', controller_run_id: 'r', attempt_id: 'a' };
  const parse = p => parseExecutionControl('[wecom:execution-control]' + JSON.stringify(p));
  assert.deepEqual(parse(base), base);
  for (const extra of [{ route: 'other' }, { root: 'other' }, { action: 'deploy' }, { action: 'terminal', outcome: 'stopped', worker_stopped: false }, { action: 'start', quota: { remainingPercent: '50', observedAt: 100000 } }, { action: 'checkpoint', operation: 'push', files: ['README.md'], quota: { remainingPercent: 50, observedAt: 100000 } }]) assert.equal(parse({ ...base, ...extra }), null);
});
test('trusted new-root dispatch protocol acknowledges original root and consumes before task result', async t => {
  const f = await fixture(t);
  assert.equal(f.send(f.command('reserve')), true); await f.bridge.drain(); assert.equal(f.result().state.status, 'reserved');
  assert.equal(f.send(f.command('start', { quota: f.quota() })), true); await f.bridge.drain();
  assert.equal(f.result().state.status, 'dispatch_started'); assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 1);
  f.send(f.command('started', { thread_id: 'thread-1', turn_id: 'turn-1' })); await f.bridge.drain();
  f.send(f.command('checkpoint', { operation: 'run-tests', files: ['README.md'], quota: f.quota() })); await f.bridge.drain(); assert.equal(f.result().ok, true);
  assert.ok(f.posted.every(p => p.thread_ts === '100.1' && p.channel === 'C123' && p.metadata.event_type === 'hiveflow_execution_control_result'));
  f.send(f.command('report', { report: completedReport() })); await f.bridge.drain();
  f.send(f.command('terminal', { outcome: 'completed', worker_stopped: true })); await f.bridge.drain(); assert.equal(f.application.dispatch.status().active, null);
});
test('untrusted, threaded, disabled and mismatched binding controls cannot mutate state', async t => {
  const f = await fixture(t); const p = f.command('reserve');
  for (const extra of [{ bot_id: 'OTHER' }, { channel: 'OTHER' }, { thread_ts: '100.1' }, { subtype: 'message_changed' }]) assert.equal(f.send(p, extra), false);
  assert.equal(f.send({ ...p, plan_id: 'other' }), false); assert.equal(f.send({ ...p, grant_id: 'other' }), false);
  assert.equal(f.send(p), true); await f.bridge.drain();
  assert.equal(f.send(f.command('stop', { controller_run_id: 'other' })), false);
  assert.equal(f.application.dispatch.status().active.stop_requested, 0);
  f.bridge.config.ownerUser = 'new-owner'; assert.equal(f.send(f.command('stop')), false);
  const dormant = await fixture(t, false); assert.equal(dormant.send(dormant.command('reserve')), false);
});
test('command dedup never replays create permission or duplicates acknowledgement', async t => {
  const f = await fixture(t); f.send(f.command('reserve')); await f.bridge.drain();
  const start = f.command('start', { quota: f.quota() }); assert.equal(f.send(start), true); await f.bridge.drain();
  const n = f.posted.length; assert.equal(f.send(start), true); await f.bridge.drain(); assert.equal(f.posted.length, n);
  assert.equal(f.send({ ...start, quota: { remainingPercent: 99, observedAt: 100000 } }), false);
  f.send(f.command('start', { quota: f.quota() })); await f.bridge.drain(); assert.equal(f.result().error, 'dispatch_already_started');
  f.send(f.command('unknown')); await f.bridge.drain(); assert.equal(f.application.dispatch.status().active.status, 'unknown');
});
test('outbox failure rolls back grant consumption and command audit atomically', async t => {
  const f = await fixture(t); f.send(f.command('reserve')); await f.bridge.drain();
  const old = f.store.addJob; f.store.addJob = () => { throw new Error('enqueue failure'); };
  const start = f.command('start', { quota: f.quota() }); assert.throws(() => f.send(start), /enqueue failure/); f.store.addJob = old;
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 0);
  assert.equal(f.application.dispatch.status().active.status, 'reserved');
  assert.equal(f.store.get('SELECT * FROM execution_control_commands WHERE command_id=?', start.command_id), undefined);
});
test('expired execution still permits stop/status/terminal recovery without replay', async t => {
  const f = await fixture(t); f.send(f.command('reserve')); await f.bridge.drain(); f.send(f.command('start', { quota: f.quota() })); await f.bridge.drain(); f.advance(100001);
  f.send(f.command('started', { thread_id: 'thread-1', turn_id: 'turn-1' })); await f.bridge.drain(); assert.equal(f.result().ok, true);
  f.send(f.command('checkpoint', { operation: 'run-tests', files: ['README.md'], quota: f.quota() })); await f.bridge.drain(); assert.equal(f.result().error, 'grant_revoked_or_expired');
  f.send(f.command('stop')); await f.bridge.drain(); f.send(f.command('status')); await f.bridge.drain(); assert.equal(f.result().state.stop_requested, 1);
  f.send(f.command('terminal', { outcome: 'stopped', worker_stopped: true })); await f.bridge.drain(); assert.equal(f.application.dispatch.status().active, null);
});

test('unknown quota acknowledgement commits checkpoint stop and fresh quota cannot resume', async t => {
  const f = await fixture(t); f.send(f.command('reserve')); await f.bridge.drain();
  f.send(f.command('start', { quota: f.quota() })); await f.bridge.drain();
  f.send(f.command('started', { thread_id: 'thread-1', turn_id: 'turn-1' })); await f.bridge.drain();
  f.send(f.command('checkpoint', { operation: 'run-tests', files: ['README.md'], quota: null })); await f.bridge.drain();
  assert.equal(f.result().error, 'quota_unknown_or_stale'); assert.equal(f.application.dispatch.status().active.stop_requested, 1);
  f.send(f.command('checkpoint', { operation: 'run-tests', files: ['README.md'], quota: f.quota() })); await f.bridge.drain();
  assert.equal(f.result().error, 'stop_requested');
});

test('delivery checkpoints bind full approved targets and cannot substitute branch or environment', async t => {
  const delivery = { target_branch: 'main', work_branch: 'hiveflow/issue-12', deployment: { environment: 'pve', service: 'hiveflow', verification: 'Health check', rollback: 'Restore verified backup' } };
  const f = await fixture(t, true, { operations: ['edit-code', 'run-tests', 'commit', 'push', 'open-pr', 'merge', 'deploy'], delivery });
  f.send(f.command('reserve')); await f.bridge.drain(); f.send(f.command('start', { quota: f.quota() })); await f.bridge.drain();
  f.send(f.command('started', { thread_id: 'thread-1', turn_id: 'turn-1' })); await f.bridge.drain();
  for (const operation of ['commit', 'push', 'open-pr', 'merge', 'deploy']) {
    assert.equal(f.send(f.command('checkpoint', { operation, files: ['README.md'], quota: f.quota() })), false);
    assert.equal(f.send(f.command('checkpoint', { operation, files: ['README.md'], quota: f.quota(), delivery: { ...delivery, target_branch: 'other' } })), true); await f.bridge.drain();
    assert.equal(f.result().error, 'delivery_scope_mismatch');
    assert.equal(f.send(f.command('checkpoint', { operation, files: ['README.md'], quota: f.quota(), delivery })), true); await f.bridge.drain(); assert.equal(f.result().ok, true);
  }
  f.send(f.command('checkpoint', { operation: 'deploy', files: ['README.md'], quota: f.quota(), delivery: { ...delivery, deployment: { ...delivery.deployment, environment: 'other' } } })); await f.bridge.drain();
  assert.equal(f.result().error, 'delivery_scope_mismatch');
});

const completedReport = () => ({ outcome: 'completed', summary: 'Updated synthetic documentation.', completed_operations: ['edit-code', 'run-tests'], verification: ['Synthetic tests: 2 passed.'], artifacts: [{ kind: 'diff', ref: 'README.md: 2 changed lines' }], remaining: [] });
async function running(f) {
  f.send(f.command('reserve')); await f.bridge.drain();
  f.send(f.command('start', { quota: f.quota() })); await f.bridge.drain();
  f.send(f.command('started', { thread_id: 'thread-1', turn_id: 'turn-1' })); await f.bridge.drain();
}
test('final reports persist and enqueue once, remain evidence only, and completed requires them', async t => {
  const f = await fixture(t); await running(f);
  f.send(f.command('terminal', { outcome: 'completed', worker_stopped: true })); await f.bridge.drain();
  assert.equal(f.result().error, 'completion_evidence_required');
  const priorOwnerJobs = f.store.get("SELECT count(*) n FROM jobs WHERE direction='wecom'").n;
  const report = completedReport();
  f.send(f.command('report', { report })); await f.bridge.drain();
  assert.equal(f.result().ok, true); assert.equal(f.application.dispatch.status().active.status, 'running');
  const row = f.store.get('SELECT * FROM execution_results');
  assert.equal(row.root, f.row.root); assert.equal(row.owner, 'owner'); assert.equal(row.request, f.row.request);
  const jobs = () => f.store.all("SELECT * FROM jobs WHERE direction='wecom' ORDER BY id").slice(priorOwnerJobs);
  assert.equal(jobs().length, 1); assert.equal(jobs()[0].lane, 'owner'); assert.equal(jobs()[0].target, 'owner');
  f.send(f.command('report', { report: { ...report, completed_operations: [...report.completed_operations].reverse(), artifacts: [{ ref: report.artifacts[0].ref, kind: 'diff' }] } })); await f.bridge.drain();
  assert.equal(f.result().ok, true); assert.equal(jobs().length, 1);
  f.send(f.command('report', { report: { ...report, summary: 'Conflicting report.' } })); await f.bridge.drain();
  assert.equal(f.result().error, 'immutable_execution_result_mismatch'); assert.equal(jobs().length, 1);
  f.send(f.command('terminal', { outcome: 'completed', worker_stopped: true })); await f.bridge.drain();
  assert.equal(f.application.dispatch.status().active, null);
  await f.bridge.stop(); f.store.close(); f.store.close = () => {};
  const reopened = new Store(f.dir); assert.equal(reopened.get('SELECT report FROM execution_results').report, row.report); reopened.close();
});
test('report rejects forged identity, extra fields, broadened operations and incomplete completion', async t => {
  const f = await fixture(t); await running(f);
  for (const extra of [{ bot_id: 'OTHER' }, { channel: 'OTHER' }, { thread_ts: f.row.root }]) assert.equal(f.send(f.command('report', { report: completedReport() }), extra), false);
  assert.equal(f.send(f.command('report', { controller_run_id: 'other', report: completedReport() })), false);
  for (const report of [{ ...completedReport(), target: 'group' }, { ...completedReport(), verification: [] }, { ...completedReport(), summary: 'x'.repeat(601) }, { ...completedReport(), artifacts: [{ kind: 'raw-log', ref: 'log' }] }]) assert.equal(f.send(f.command('report', { report })), false);
  f.send(f.command('report', { report: { ...completedReport(), completed_operations: ['deploy'] } })); await f.bridge.drain(); assert.equal(f.result().error, 'result_operation_not_granted');
  assert.equal(f.store.get('SELECT count(*) n FROM execution_results').n, 0);
  f.send(f.command('report', { report: { ...completedReport(), completed_operations: ['edit-code'] } })); await f.bridge.drain();
  assert.equal(f.result().error, 'completion_evidence_required');
  assert.equal(f.store.get('SELECT count(*) n FROM execution_results').n, 0);
  f.send(f.command('report', { report: completedReport() })); await f.bridge.drain(); assert.equal(f.result().ok, true);
  f.send(f.command('terminal', { outcome: 'completed', worker_stopped: true })); await f.bridge.drain(); assert.equal(f.result().ok, true);
});
test('unknown and expired reports never release slot, and report enqueue failure rolls back', async t => {
  const f = await fixture(t); await running(f);
  f.send(f.command('unknown')); await f.bridge.drain(); f.advance(100001);
  const old = f.store.addJob; f.store.addJob = () => { throw new Error('enqueue failure'); };
  assert.throws(() => f.send(f.command('report', { report: completedReport() })), /enqueue failure/); f.store.addJob = old;
  assert.equal(f.store.get('SELECT count(*) n FROM execution_results').n, 0);
  f.send(f.command('report', { report: completedReport() })); await f.bridge.drain(); assert.equal(f.result().ok, true);
  assert.equal(f.application.dispatch.status().active.status, 'unknown');
  f.send(f.command('terminal', { outcome: 'completed', worker_stopped: true })); await f.bridge.drain(); assert.equal(f.result().error, 'completion_not_confirmed');
});

test('recovery acknowledgements bypass a stale trigger without changing original root', async t => {
  const f = await fixture(t); await running(f);
  f.store.run("INSERT INTO jobs(request,direction,payload,status,lane,target,expires) VALUES (?, 'slack', '{}', 'unknown', 'slack', 'C123', ?)", f.row.request, 999999);
  f.posted.length = 0;
  f.send(f.command('report', { report: completedReport() })); await f.bridge.drain();
  assert.equal(f.posted.length, 1); assert.equal(f.posted[0].thread_ts, f.row.root);
  assert.equal(f.posted[0].metadata.event_payload.action, 'report');
});

test('direct report validator rejects sparse evidence arrays', () => {
  assert.equal(validExecutionReport({ ...completedReport(), verification: Array(1) }), false);
});
