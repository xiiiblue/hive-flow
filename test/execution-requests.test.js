import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Bridge } from 'wecom-slack-bridge/bridge';
import { Store } from 'wecom-slack-bridge/store';
import { IssueReviewApplication, loadManualExecutionEnabled } from '../src/issue-review.js';
import { parseExecutionPlan } from '../src/execution-requests.js';
const repository = 'example/project';
const config = { wecomBotId: 'test-bot', ownerUser: 'owner', allowedChats: new Set(['group']), slackChannel: 'C123', replyBot: 'BDOT', ttlMs: 3600000, maxBytes: 32000 };
const plan = (extra = {}) => ({ repo: repository, issue_id: '9001', issue_number: 12, version: 1, base_sha: 'a'.repeat(40), files: ['README.md'], operations: ['edit-code', 'run-tests'], summary: 'Update installation instructions', expires: 200000, ...extra });
const event = (p = plan(), extra = {}) => ({ type: 'message', channel: 'C123', ts: '100.1', bot_id: 'BDOT', text: '[wecom:execution-plan]' + JSON.stringify(p), ...extra });
const frame = (text, id = 'm1', extra = {}) => ({ body: { aibotid: 'test-bot', msgid: id, msgtype: 'text', chattype: 'single', from: { userid: 'owner' }, text: { content: text }, ...extra } });
function fixture(t, options = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'execution-test-')); let store = new Store(dir), time = 100000;
  const sent = [], posted = [], c = { ...config };
  const application = new IssueReviewApplication({ issueRepository: repository }, options);
  const deps = { config: c, now: () => time, wait: async () => {}, slack: { post: async p => { posted.push(p); return { ok: true, ts: '200.1' }; } }, wecom: { ack: async () => {}, send: async (target, text) => { sent.push({ target, text }); return { errcode: 0 }; } }, application };
  application.initialize({ store, now: () => time }); let bridge = new Bridge({ ...deps, store }); bridge.wecomReady = true;
  t.after(async () => { await bridge.stop(); store.close(); rmSync(dir, { recursive: true, force: true }); });
  return { get bridge() { return bridge; }, get store() { return store; }, application, c, sent, posted, advance(ms) { time += ms; }, row() { return store.get('SELECT * FROM execution_plans ORDER BY version DESC LIMIT 1'); }, command(row = this.row()) { return `/execute #${row.issue_number} v${row.version} ${row.id}`; }, async restart() { await bridge.stop(); store.close(); store = new Store(dir); application.initialize({ store, now: () => time }); bridge = new Bridge({ ...deps, store }); bridge.wecomReady = true; } };
}
test('strict independent plan schema pins commit, exact files, operations, identity and bounded expiry', () => {
  assert.deepEqual(parseExecutionPlan(event().text, repository, 100000), plan());
  for (const extra of [{ extra: true }, { repo: 'other/project' }, { issue_id: '01' }, { issue_number: 0 }, { version: '1' }, { base_sha: 'main' }, { base_sha: 'A'.repeat(40) }, { files: [] }, { files: ['../a'] }, { files: ['/a'] }, { files: ['a//b'] }, { files: ['a/./b'] }, { files: ['.git/config'] }, { files: ['a/.GIT/config'] }, { files: ['src/*'] }, { files: ['a\\b'] }, { files: ['.env'] }, { files: ['src/.env.production'] }, { files: ['.ssh/id_rsa'] }, { files: ['.aws/credentials'] }, { files: ['a', 'a'] }, { operations: ['deploy'] }, { operations: ['open-draft-pr'] }, { operations: ['edit-code', 'edit-code'] }, { summary: 'a\nb' }, { expires: 100000 }, { expires: 100000 + 7 * 86400000 + 1 }]) assert.equal(parseExecutionPlan(event(plan(extra)).text, repository, 100000), null, JSON.stringify(extra));
});
test('authenticated plan preview creates no approval and advertises no execute command', async t => {
  const f = fixture(t);
  for (const extra of [{ channel: 'other' }, { bot_id: 'other' }, { thread_ts: '99.1' }, { subtype: 'message_changed' }]) assert.equal(f.bridge.receiveSlack(event(plan(), extra)), false);
  assert.equal(f.bridge.receiveSlack(event()), true); await f.bridge.drain();
  assert.equal(f.store.get('SELECT count(*) n FROM issue_proposals').n, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
  assert.match(f.sent[0].text, /执行器尚未就绪/); assert.ok(!f.sent[0].text.includes('/execute'));
  assert.ok(f.sent.every(m => m.target === 'owner'));
});
test('owner explicit version-bound attempt is terminal not_ready, routes atomically, never executes or replays', async t => {
  const f = fixture(t); f.bridge.receiveSlack(event()); await f.bridge.drain(); const command = f.command();
  assert.equal(f.bridge.receiveWecom(frame(command, 'other', { from: { userid: 'other' } })), false);
  f.bridge.receiveWecom(frame(command, 'group', { chattype: 'group', chatid: 'group' })); await f.bridge.drain();
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
  for (const [i, text] of [command.replace('#12', '#13'), command.replace('v1', 'v2'), command + ' yes', '/execute #12', '/execute #12 v1', '/execute\n' + command, '/execute #12 v01 ' + f.row().id].entries()) assert.equal(f.bridge.receiveWecom(frame(text, `bad-${i}`)), false);
  f.posted.length = 0;
  assert.equal(f.bridge.receiveWecom(frame(command, 'execute')), true); await f.bridge.drain();
  assert.equal(f.posted.length, 1); assert.equal(f.posted[0].thread_ts, '100.1'); assert.equal(f.posted[0].channel, config.slackChannel);
  const result = f.posted[0].metadata.event_payload;
  assert.equal(result.status, 'not_ready'); assert.equal(result.execution_started, false); assert.equal(result.execution_authorized, false);
  assert.equal(f.store.get('SELECT status FROM execution_requests').status, 'not_ready');
  assert.match(f.sent.at(-1).text, /未授权、未启动执行/);
  assert.equal(f.bridge.receiveWecom(frame(command, 'execute')), false);
  assert.equal(f.bridge.receiveWecom(frame(command, 'duplicate')), false);
  await f.restart(); assert.equal(f.bridge.receiveWecom(frame(command, 'restart')), false); await f.bridge.drain();
  assert.equal(f.posted.length, 1); assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 1);
  assert.equal(f.store.get("SELECT count(*) n FROM sqlite_master WHERE name='workflow_grants'").n, 0);
});
test('immutable version/identity history rejects conflicts, stale plans and expired attempts', async t => {
  const f = fixture(t); f.bridge.wecomReady = false; assert.equal(f.bridge.receiveSlack(event()), true); const old = f.row();
  for (const extra of [{}, { summary: 'conflict' }, { issue_id: '9002' }, { issue_number: 13 }]) assert.equal(f.bridge.receiveSlack(event(plan(extra), { ts: '101.1' })), false);
  assert.equal(f.bridge.receiveSlack(event(plan({ version: 2 }), { ts: '102.1' })), true);
  assert.equal(f.bridge.receiveWecom(frame(f.command(old), 'old')), false);
  assert.equal(f.store.get('SELECT snapshot FROM execution_plans WHERE id=?', old.id).snapshot, JSON.stringify(plan()));
  assert.equal(f.store.get('SELECT count(*) n FROM execution_plans').n, 2);
  f.advance(100000); assert.equal(f.bridge.receiveWecom(frame(f.command(), 'expired')), false);
  f.bridge.wecomReady = true; await f.bridge.drain();
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
});
test('plan and record-only Issue identities bind in both directions without linking their versions', async t => {
  const f = fixture(t); f.bridge.receiveSlack(event()); await f.bridge.drain();
  const proposal = { repo: repository, issue_id: '9002', issue_number: 12, title: 'Example', revision: 1, proposed_action: 'Review', allowed_scope: 'Docs', source_kind: 'github' };
  assert.equal(f.bridge.receiveSlack(event(plan(), { ts: '101.1', text: '[wecom:issue]' + JSON.stringify(proposal) })), false);
  proposal.issue_id = '9001'; proposal.revision = 50;
  assert.equal(f.bridge.receiveSlack(event(plan(), { ts: '101.1', text: '[wecom:issue]' + JSON.stringify(proposal) })), true); await f.bridge.drain();
  const review = f.store.get('SELECT id FROM issue_proposals').id;
  assert.equal(f.bridge.receiveWecom(frame(`/review ${review} 同意`, 'review')), true); await f.bridge.drain();
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
  assert.equal(f.bridge.receiveWecom(frame(f.command(), 'execute')), true); await f.bridge.drain();
  assert.equal(f.posted.at(-1).thread_ts, '100.1');
});
test('attempt and both outbox results rollback together on enqueue failure', async t => {
  const f = fixture(t); f.bridge.receiveSlack(event()); await f.bridge.drain();
  const original = f.store.addJob.bind(f.store); let count = 0;
  f.store.addJob = (...args) => { if (++count === 2) throw new Error('synthetic failure'); return original(...args); };
  const jobs = f.store.get('SELECT count(*) n FROM jobs').n;
  assert.throws(() => f.bridge.receiveWecom(frame(f.command(), 'atomic')), /synthetic failure/);
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM jobs').n, jobs);
  f.store.addJob = original;
  assert.equal(f.bridge.receiveWecom(frame(f.command(), 'atomic')), true); await f.bridge.drain();
});
test('pinned configuration revalidated across restart, no retargeting or group result', async t => {
  for (const key of ['ownerUser', 'slackChannel', 'issueRepository']) {
    const f = fixture(t); f.bridge.wecomReady = false; f.bridge.receiveSlack(event()); await f.bridge.drain(); const command = f.command();
    if (key === 'issueRepository') f.application.config[key] = 'other/project'; else f.c[key] = 'other';
    await f.restart(); await f.bridge.drain(); assert.equal(f.sent.length, 0);
    assert.equal(f.bridge.receiveWecom(frame(command, 'changed', { from: { userid: f.c.ownerUser } })), false);
  }
});
test('durable history survives prune and fresh plan permits a new non-executing attempt', async t => {
  const f = fixture(t); f.bridge.receiveSlack(event()); await f.bridge.drain(); f.bridge.receiveWecom(frame(f.command(), 'first')); await f.bridge.drain();
  f.advance(10 * 86400000); f.store.prune(100000 + 10 * 86400000); await f.restart();
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 1);
  assert.equal(f.bridge.receiveSlack(event(plan({ version: 2, expires: 100000 + 11 * 86400000 }), { ts: '103.1' })), true); await f.bridge.drain();
  assert.equal(f.bridge.receiveWecom(frame(f.command(), 'second')), true); await f.bridge.drain();
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 2);
});
test('trusted ready path atomically grants exact plan, emits dispatch request, and gates actual stages', async t => {
  const f = fixture(t, { manualExecutionEnabled: true }); f.bridge.receiveSlack(event()); await f.bridge.drain();
  assert.ok(f.sent.some(m => m.text.includes(f.command())));
  assert.equal(f.bridge.receiveWecom(frame(f.command(), 'ready')), true); await f.bridge.drain();
  const payload = f.posted[0].metadata.event_payload, dispatch = f.application.dispatch;
  assert.equal(f.posted[0].metadata.event_type, 'hiveflow_execution_requested');
  assert.equal(payload.execution_authorized, true); assert.equal(payload.execution_started, false);
  assert.deepEqual(payload.plan, { ...plan(), id: f.row().id });
  assert.equal(f.store.get('SELECT count(*) n FROM manual_execution_grants').n, 1);
  assert.equal(f.store.get('SELECT count(*) n FROM manual_dispatch_attempts').n, 0);
  const args = { grantId: payload.grant_id, controllerRunId: 'synthetic-controller', attemptId: 'synthetic-attempt' };
  assert.equal(dispatch.reserve(args).status, 'reserved');
  assert.throws(() => dispatch.startDispatch({ ...args, quota: { remainingPercent: 5, observedAt: 100000 } }), /quota_too_low/);
  assert.equal(f.store.get('SELECT used FROM manual_execution_grants').used, 0);
  assert.equal(dispatch.startDispatch({ ...args, quota: { remainingPercent: 90, observedAt: 100000 } }).status, 'dispatch_started');
  assert.throws(() => dispatch.startDispatch({ ...args, quota: { remainingPercent: 90, observedAt: 100000 } }), /dispatch_already_started/);
  dispatch.recordStarted({ ...args, threadId: 'synthetic-thread', turnId: 'synthetic-turn' });
  assert.equal(dispatch.checkpoint({ ...args, operation: 'edit-code', files: ['README.md'], quota: { remainingPercent: 90, observedAt: 100000 } }).plan.base_sha, plan().base_sha);
  assert.throws(() => dispatch.checkpoint({ ...args, operation: 'edit-code', files: ['src/main.js'], quota: { remainingPercent: 90, observedAt: 100000 } }), /file_not_granted/);
  dispatch.confirmTerminal({ attemptId: args.attemptId, outcome: 'completed', workerStopped: true });
  assert.equal(dispatch.status().active, null);
  assert.equal(f.bridge.receiveWecom(frame(f.command(), 'again')), false);
});
test('ready grant and request rollback with outbox; new plan revokes and stops old scope', async t => {
  const f = fixture(t, { manualExecutionEnabled: true }); f.bridge.receiveSlack(event()); await f.bridge.drain();
  const original = f.store.addJob.bind(f.store); f.store.addJob = () => { throw new Error('synthetic enqueue failure'); };
  assert.throws(() => f.bridge.receiveWecom(frame(f.command(), 'atomic-ready')), /synthetic enqueue/);
  assert.equal(f.store.get('SELECT count(*) n FROM manual_execution_grants').n, 0);
  assert.equal(f.store.get('SELECT count(*) n FROM execution_requests').n, 0);
  f.store.addJob = original; f.bridge.receiveWecom(frame(f.command(), 'atomic-ready')); await f.bridge.drain();
  const grantId = f.posted[0].metadata.event_payload.grant_id, args = { grantId, controllerRunId: 'controller', attemptId: 'attempt' };
  f.application.dispatch.reserve(args);
  f.bridge.receiveSlack(event(plan({ version: 2 }), { ts: '105.1' })); await f.bridge.drain();
  assert.equal(f.store.get('SELECT revoked FROM manual_execution_grants').revoked, 1);
  assert.equal(f.store.get('SELECT stop_requested FROM manual_dispatch_attempts').stop_requested, 1);
  assert.throws(() => f.application.dispatch.startDispatch({ ...args, quota: { remainingPercent: 90, observedAt: 100000 } }), /grant_revoked_or_expired/);
});
test('disabled runtime cannot forward a previously queued dispatch trigger', async t => {
  const f = fixture(t, { manualExecutionEnabled: true }); f.bridge.receiveSlack(event()); await f.bridge.drain();
  // Hold the pump while ingest commits the outbox, then simulate disabled startup.
  const drain = f.bridge.drain.bind(f.bridge); f.bridge.drain = () => null;
  f.bridge.receiveWecom(frame(f.command(), 'queue')); f.application.manualExecutionEnabled = false;
  f.bridge.drain = drain; await f.bridge.drain();
  assert.equal(f.posted.length, 0);
  assert.equal(f.store.get("SELECT status FROM jobs WHERE direction='slack'").status, 'unknown');
});

test('disabled-mode new plan still withdraws grants left by a ready runtime', async t => {
  const f = fixture(t, { manualExecutionEnabled: true }); f.bridge.receiveSlack(event()); await f.bridge.drain();
  f.bridge.receiveWecom(frame(f.command(), 'old-grant')); await f.bridge.drain();
  const grantId = f.posted[0].metadata.event_payload.grant_id;
  const args = { grantId, controllerRunId: 'old-controller', attemptId: 'old-attempt' };
  f.application.dispatch.reserve(args);
  f.application.manualExecutionEnabled = false; await f.restart();
  assert.equal(f.application.dispatch, null);
  f.bridge.receiveSlack(event(plan({ version: 2 }), { ts: '106.1' })); await f.bridge.drain();
  assert.equal(f.store.get('SELECT revoked FROM manual_execution_grants').revoked, 1);
  assert.equal(f.store.get('SELECT stop_requested FROM manual_dispatch_attempts').stop_requested, 1);
  f.application.manualExecutionEnabled = true; await f.restart();
  assert.throws(() => f.application.dispatch.startDispatch({ ...args, quota: { remainingPercent: 90, observedAt: 100000 } }), /grant_revoked_or_expired/);
});

test('manual protocol startup switch is strict, disabled by default, and independent of Issue input', () => {
  assert.equal(loadManualExecutionEnabled({}), false);
  assert.equal(loadManualExecutionEnabled({ HIVEFLOW_MANUAL_EXECUTION_ENABLED: 'false' }), false);
  assert.equal(loadManualExecutionEnabled({ HIVEFLOW_MANUAL_EXECUTION_ENABLED: 'true' }), true);
  for (const value of ['', 'TRUE', '1', 'yes', ' true ', null]) assert.throws(() => loadManualExecutionEnabled({ HIVEFLOW_MANUAL_EXECUTION_ENABLED: value }));
  assert.equal(parseExecutionPlan(event(plan({ manualExecutionEnabled: true })).text, repository, 100000), null);
  assert.ok(parseExecutionPlan(event(plan({ files: ['.env.example', 'config/settings.json'] })).text, repository, 100000));
});

const delivery = (deployment = null) => ({ target_branch: 'main', work_branch: 'hiveflow/issue-12-v2', deployment });
const deployment = () => ({ environment: 'synthetic-staging', service: 'example-service', verification: 'Check the service health endpoint and smoke test the documented flow', rollback: 'Restore the previously verified release and recheck health' });
const fullOperations = ['edit-code', 'run-tests', 'commit', 'push', 'open-pr', 'merge', 'deploy'];
test('delivery operations require exact approved branch and deployment targets', () => {
  const parse = p => parseExecutionPlan(event(p).text, repository, 100000);
  const p = plan({ operations: fullOperations, delivery: delivery(deployment()) });
  assert.deepEqual(parse(p), p);
  assert.ok(parse(plan({ operations: ['commit', 'push', 'open-pr', 'merge'], delivery: delivery() })));
  for (const d of [undefined, {}, { ...delivery(deployment()), repo: 'other/project' }, { ...delivery(deployment()), target_branch: 'main; echo hi' }, { ...delivery(deployment()), work_branch: 'main' }, { ...delivery(deployment()), work_branch: '../other' }, { ...delivery(deployment()), work_branch: 'topic.lock' }, delivery(), delivery({ ...deployment(), environment: '' }), delivery({ ...deployment(), service: 'service && reboot' }), delivery({ ...deployment(), verification: '' }), delivery({ ...deployment(), rollback: '' }), delivery({ ...deployment(), command: 'deploy' })]) assert.equal(parse(plan({ operations: fullOperations, ...(d === undefined ? {} : { delivery: d }) })), null);
  assert.equal(parse(plan({ delivery: delivery() })), null);
  assert.equal(parse(plan({ operations: ['push'], delivery: delivery(deployment()) })), null);
});
test('one explicit owner request grants bounded full delivery plan and shows targets before approval', async t => {
  const f = fixture(t, { manualExecutionEnabled: true });
  const p = plan({ operations: fullOperations, delivery: delivery(deployment()) });
  assert.equal(f.bridge.receiveSlack(event(p)), true); await f.bridge.drain();
  const shown = f.sent.map(m => m.text).join('\n');
  assert.match(shown, /目标分支/); assert.match(shown, /隔离工作分支/); assert.match(shown, /synthetic-staging/); assert.match(shown, /verification/); assert.match(shown, /rollback/);
  assert.ok(shown.includes(f.command()));
  assert.equal(f.bridge.receiveWecom(frame(f.command(), 'full-delivery')), true); await f.bridge.drain();
  const result = f.posted[0].metadata.event_payload;
  assert.deepEqual(result.plan.delivery, p.delivery);
  assert.deepEqual(result.plan.operations, p.operations);
  assert.equal(result.execution_started, false);
  assert.deepEqual(JSON.parse(f.store.get('SELECT binding FROM manual_execution_grants').binding).delivery, p.delivery);
  assert.equal(f.store.get('SELECT count(*) n FROM manual_dispatch_attempts').n, 0);
});
