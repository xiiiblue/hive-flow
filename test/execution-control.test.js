import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from 'wecom-slack-bridge/store';
import { Bridge } from 'wecom-slack-bridge/bridge';
import { IssueReviewApplication } from '../src/issue-review.js';
import { parseExecutionControl, initializeExecutionControl } from '../src/execution-control.js';
const repository = 'example/project';
const plan = { repo: repository, issue_id: '9001', issue_number: 12, version: 1, base_sha: 'a'.repeat(40), files: ['README.md'], operations: ['edit-code', 'run-tests'], summary: 'Update documentation', expires: 200000 };
async function fixture(t, ready = true) {
  const dir = mkdtempSync(join(tmpdir(), 'control-test-')), store = new Store(dir); let time = 100000, seq = 0;
  const application = new IssueReviewApplication({ issueRepository: repository }, { manualExecutionEnabled: ready });
  const config = { wecomBotId: 'test-bot', ownerUser: 'owner', allowedChats: new Set(), slackChannel: 'C123', replyBot: 'BDOT', ttlMs: 3600000, maxBytes: 32000 };
  application.initialize({ store, now: () => time }); initializeExecutionControl(store);
  const posted = [], bridge = new Bridge({ store, application, config, now: () => time, wait: async () => {}, slack: { post: async p => { posted.push(p); return { ok: true, ts: '999.1' }; } }, wecom: { ack: async () => {}, send: async () => ({ errcode: 0 }) } });
  bridge.wecomReady = true;
  t.after(async () => { await bridge.stop(); store.close(); rmSync(dir, { recursive: true, force: true }); });
  bridge.receiveSlack({ type: 'message', channel: 'C123', ts: '100.1', bot_id: 'BDOT', text: '[wecom:execution-plan]' + JSON.stringify(plan) }); await bridge.drain();
  const row = store.get('SELECT * FROM execution_plans');
  bridge.receiveWecom({ body: { aibotid: 'test-bot', msgid: 'approval', msgtype: 'text', chattype: 'single', from: { userid: 'owner' }, text: { content: `/execute #12 v1 ${row.id}` } } }); await bridge.drain();
  const grant = store.get('SELECT * FROM execution_requests'); posted.length = 0;
  return { store, bridge, application, posted, row, grant, advance(ms) { time += ms; },
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
