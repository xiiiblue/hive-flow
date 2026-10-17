import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from 'wecom-slack-bridge/store';
import { Bridge } from 'wecom-slack-bridge/bridge';
import { IssueReviewApplication } from '../src/issue-review.js';
import { initializeExecutionResults, recordExecutionResult } from '../src/execution-results.js';
const repo = 'example/project';
function fixture(t, ready = true) {
  const dir = mkdtempSync(join(tmpdir(), 'owner-command-')), store = new Store(dir), now = () => 100000;
  const application = new IssueReviewApplication({ issueRepository: repo }, { manualExecutionEnabled: ready });
  application.initialize({ store, now });
  const sent = [], posted = [], config = { wecomBotId: 'bot', ownerUser: 'owner', allowedChats: new Set(), slackChannel: 'C123', replyBot: 'BDOT', ttlMs: 3600000, maxBytes: 32000 };
  const bridge = new Bridge({ store, application, config, now, wait: async () => {}, slack: { post: async p => { posted.push(p); return { ok: true, ts: '999.1' }; } }, wecom: { ack: async () => {}, send: async (_, text) => { sent.push(text); return { errcode: 0 }; } } });
  bridge.wecomReady = true;
  t.after(async () => { await bridge.stop(); store.close(); rmSync(dir, { recursive: true, force: true }); });
  let seq = 0;
  return { store, bridge, application, sent, posted,
    plan(number = 12, version = 1) { return bridge.receiveSlack({ type: 'message', channel: 'C123', bot_id: 'BDOT', ts: `${number}.${version}`, text: '[wecom:execution-plan]' + JSON.stringify({ repo, issue_id: String(9000 + number), issue_number: number, version, base_sha: 'a'.repeat(40), files: ['README.md'], operations: ['edit-code', 'run-tests'], summary: 'Docs', expires: 200000 }) }); },
    command(text, id = `m${++seq}`, extra = {}) { return bridge.receiveWecom({ body: { aibotid: 'bot', msgid: id, msgtype: 'text', chattype: 'single', from: { userid: 'owner' }, text: { content: text }, ...extra } }); },
    reserve(number = 12) { const g = store.get('SELECT g.* FROM manual_execution_grants g JOIN execution_plans p ON p.id=g.plan_id WHERE p.issue_number=?', number); application.dispatch.reserve({ grantId: g.id, controllerRunId: 'controller', attemptId: 'attempt' }); return g; },
    events() { return posted.filter(p => p.metadata?.event_type === 'hiveflow_execution_stop_requested'); }
  };
}
test('query is authenticated, exact issue scoped, readonly and uses persisted status', async t => {
  const f = fixture(t); f.plan(); f.plan(13); await f.bridge.drain();
  assert.equal(f.command('查询 #12', 'stranger', { from: { userid: 'stranger' } }), false);
  assert.equal(f.command('查询 #12', 'group', { chattype: 'group', chatid: 'group' }), false);
  for (const text of ['查询 #0', '查询 #012', '查询 #12 v1', '查询 #9007199254740992', '查询 #99']) assert.equal(f.command(text), true);
  assert.equal(f.command('查询 #12'), true); await f.bridge.drain();
  assert.match(f.sent.at(-1), /问题 #12：[\s\S]*未授权执行；尚无派发记录/);
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM manual_execution_grants').n, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM execution_plans').n, 2);
});
test('stop pending grant is atomic, blocks dispatch, remains exact and deduplicates events', async t => {
  const f = fixture(t); f.plan(); f.plan(13); await f.bridge.drain();
  f.command('执行 #12 v1'); f.command('执行 #13 v1'); await f.bridge.drain();
  const g = f.reserve();
  assert.equal(f.command('停止 #12', 'stop<&>'), true); await f.bridge.drain();
  assert.equal(f.application.dispatch.status().active.stop_requested, 1);
  assert.equal(f.application.dispatch.status().active.slot, 1);
  assert.equal(f.store.get('SELECT revoked FROM manual_execution_grants WHERE id=?', g.id).revoked, 1);
  assert.equal(f.store.get('SELECT g.revoked FROM manual_execution_grants g JOIN execution_plans p ON p.id=g.plan_id WHERE p.issue_number=13').revoked, 0);
  assert.throws(() => f.application.dispatch.startDispatch({ attemptId: 'attempt', controllerRunId: 'controller', quota: { remainingPercent: 99, observedAt: 100000 } }), /grant_revoked_or_expired/);
  assert.equal(f.events().length, 1); assert.equal(f.events()[0].thread_ts, '12.1'); assert.equal(f.events()[0].metadata.event_payload.worker_stopped, false);
  const stopText = f.events()[0].text.split('\n'); assert.equal(stopText[0], 'Execution stop requested'); assert.deepEqual(JSON.parse(stopText[1]), f.events()[0].metadata.event_payload); assert.ok(!/[<>&]/.test(stopText[1]));
  assert.match(f.sent.at(-1), /尚未确认任务停止；执行槽仍保留/);
  assert.equal(f.command('停止 #12', 'stop<&>'), false); f.command('停止 #12', 'stop-again'); await f.bridge.drain(); assert.equal(f.events().length, 1);
  f.command('查询 #12'); await f.bridge.drain(); assert.match(f.sent.at(-1), /授权已撤销[\s\S]*尚未确认任务停止/);
});
test('running old version remains visible and stop event binds original task and root', async t => {
  const f = fixture(t); f.plan(); await f.bridge.drain(); f.command('执行 #12 v1'); await f.bridge.drain(); f.reserve();
  f.application.dispatch.startDispatch({ attemptId: 'attempt', controllerRunId: 'controller', quota: { remainingPercent: 99, observedAt: 100000 } });
  f.application.dispatch.recordStarted({ attemptId: 'attempt', controllerRunId: 'controller', threadId: 'mac-child', turnId: 'turn' });
  f.plan(12, 2); await f.bridge.drain(); f.command('查询 #12'); await f.bridge.drain();
  assert.match(f.sent.at(-1), /方案 v2：[\s\S]*方案 v1：[\s\S]*执行槽仍占用/);
  f.command('停止 #12'); await f.bridge.drain();
  assert.equal(f.events().length, 1); const e = f.events()[0]; assert.equal(e.thread_ts, '12.1'); assert.equal(e.metadata.event_payload.thread_id, 'mac-child');
  assert.equal(f.application.dispatch.status().active.slot, 1);
});
test('stop rollback includes revoke, latch, dedup and outbox', async t => {
  const f = fixture(t); f.plan(); await f.bridge.drain(); f.command('执行 #12 v1'); await f.bridge.drain(); const g = f.reserve();
  const add = f.store.addJob; f.store.addJob = () => { throw new Error('enqueue failure'); };
  assert.throws(() => f.command('停止 #12', 'atomic'), /enqueue failure/); f.store.addJob = add;
  assert.equal(f.store.get('SELECT revoked FROM manual_execution_grants WHERE id=?', g.id).revoked, 0);
  assert.equal(f.application.dispatch.status().active.stop_requested, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM owner_execution_stops').n, 0);
  assert.equal(f.command('停止 #12', 'atomic'), true);
});
test('disabled/manual-unapproved query and stop never create grants or change plans', async t => {
  const f = fixture(t, false); f.plan(); await f.bridge.drain(); const before = f.store.get('SELECT * FROM execution_plans');
  f.command('停止 #12'); await f.bridge.drain(); assert.match(f.sent.at(-1), /当前没有待停止的任务/);
  f.command('查询 #12'); await f.bridge.drain(); assert.match(f.sent.at(-1), /未授权执行/);
  assert.deepEqual(f.store.get('SELECT * FROM execution_plans'), before); assert.equal(f.events().length, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
});
test('stop before dispatch delivery suppresses trigger and survives reinitialization', async t => {
  const f = fixture(t); f.plan(); await f.bridge.drain(); f.bridge.wecomReady = false;
  f.command('执行 #12 v1'); f.command('停止 #12', 'stop-pending');
  const g = f.store.get('SELECT * FROM manual_execution_grants');
  assert.throws(() => f.application.dispatch.reserve({ grantId: g.id, controllerRunId: 'controller', attemptId: 'attempt' }), /invalid_execution_grant/);
  f.application.initialize({ store: f.store, now: () => 100000 });
  assert.equal(f.command('停止 #12', 'stop-pending'), false);
  f.bridge.wecomReady = true; await f.bridge.drain();
  assert.equal(f.posted.filter(p => p.metadata?.event_type === 'hiveflow_execution_requested').length, 0);
  assert.equal(f.events().length, 1);
});
test('changed owner/channel/repository cannot retarget records or stop grants', async t => {
  for (const key of ['ownerUser', 'slackChannel', 'issueRepository']) {
    const f = fixture(t); f.plan(); await f.bridge.drain(); f.command('执行 #12 v1'); await f.bridge.drain();
    if (key === 'issueRepository') f.application.config[key] = 'other/repo'; else f.bridge.config[key] = 'other';
    assert.equal(f.command('停止 #12', `stop-${key}`, { from: { userid: f.bridge.config.ownerUser } }), true);
    assert.equal(f.store.get('SELECT revoked FROM manual_execution_grants').revoked, 0);
    assert.equal(f.store.get('SELECT count(*) n FROM owner_execution_stops').n, 0);
  }
});

test('query shows persisted bound report without claiming a worker is terminal', async t => {
  const f = fixture(t); f.plan(); await f.bridge.drain(); f.command('执行 #12 v1'); await f.bridge.drain(); const g = f.reserve();
  initializeExecutionResults(f.store);
  const p = f.store.get('SELECT * FROM execution_plans');
  f.store.transaction(() => recordExecutionResult(f.store, p, g, f.application.dispatch.status().active, { outcome: 'blocked', summary: '环境校验尚未通过', completed_operations: [], verification: [], artifacts: [], remaining: ['核实环境'] }, 100000));
  f.command('查询 #12'); await f.bridge.drain();
  assert.match(f.sent.at(-1), /执行槽仍占用[\s\S]*处理结果：环境校验尚未通过/);
  f.store.run("UPDATE execution_results SET root='unrelated'");
  f.command('查询 #12'); await f.bridge.drain(); assert.ok(!f.sent.at(-1).includes('环境校验尚未通过'));
});

test('revoked grants without attempts do not generate stop events', async t => {
  const f = fixture(t); f.plan(); await f.bridge.drain(); f.command('执行 #12 v1'); await f.bridge.drain();
  f.plan(12, 2); await f.bridge.drain();
  f.command('停止 #12'); await f.bridge.drain();
  assert.equal(f.events().length, 0); assert.match(f.sent.at(-1), /当前没有待停止的任务/);
});
