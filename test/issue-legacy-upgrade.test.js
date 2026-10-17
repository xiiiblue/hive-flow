import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from 'wecom-slack-bridge/store';
import { Bridge } from 'wecom-slack-bridge/bridge';
import { IssueReviewApplication } from '../src/issue-review.js';

// Exact prior business schema and synthetic rows, without running the new application.
function seedLegacy(store) {
  store.db.exec(`CREATE TABLE issue_proposals (id TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, repo TEXT NOT NULL, issue_id TEXT NOT NULL, issue_number INTEGER NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL, owner TEXT NOT NULL, channel TEXT NOT NULL, root TEXT NOT NULL, created INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(repo,issue_id,revision));
    CREATE TABLE issue_decisions (proposal TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, source TEXT UNIQUE NOT NULL, created INTEGER NOT NULL);`);
  for (const [index, status] of ['pending', 'decided'].entries()) {
    const id = `00000000-0000-0000-0000-00000000000${index}`;
    const request = `legacy-request-${index}`;
    const snapshot = JSON.stringify({ repo: 'example/project', issue_id: `${9001 + index}`, issue_number: 12 + index, title: '原始快照', revision: 1, proposed_action: '修改说明', allowed_scope: '仅文档', source_kind: 'github' });
    store.run('INSERT INTO requests(id,source,target,created,expires,root,status,kind,owner,channel) VALUES (?,?,?,?,?,?,?,?,?,?)', request, `issue:${id}`, 'owner', 100, 200, `100.${index}`, 'sent', 'issue', 'owner', 'C123');
    store.run('INSERT INTO issue_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', id, request, 'example/project', `${9001 + index}`, 12 + index, 1, snapshot, 'owner', 'C123', `100.${index}`, 100, status);
    if (status === 'decided') store.run('INSERT INTO issue_decisions VALUES (?,?,?,?,?,?)', id, 'defer', '旧理由', 'owner', 'legacy-source', 150);
    store.addJob(request, 'wecom', { text: '旧通知' }, 'owner', 'owner', 900000);
    if (status === 'decided') store.run("UPDATE jobs SET status='unknown' WHERE request=?", request);
  }
}

test('legacy business tables, IDs, audit snapshots and queued jobs survive generic startup and application upgrade in place', async t => {
  const dir = mkdtempSync(join(tmpdir(), 'hiveflow-legacy-'));
  let store = new Store(dir), bridge;
  t.after(async () => { await bridge?.stop(); store.close(); rmSync(dir, { recursive: true, force: true }); });
  seedLegacy(store);
  const before = { proposals: store.all('SELECT * FROM issue_proposals ORDER BY id'), decisions: store.all('SELECT * FROM issue_decisions'), jobs: store.all('SELECT * FROM jobs ORDER BY id') };
  store.close(); store = new Store(dir);
  const config = { wecomBotId: 'test-bot', ownerUser: 'owner', allowedChats: new Set(['group']), slackChannel: 'C123', replyBot: 'BDOT', ttlMs: 3600000, maxBytes: 32000 };
  const sent = [], posted = [];
  const deps = { config, store, now: () => 100000, wait: async () => {}, wecom: { ack: async () => {}, send: async (target, text) => { sent.push({ target, text }); return { errcode: 0 }; } }, slack: { post: async payload => { posted.push(payload); return { ok: true, ts: '200.1' }; } } };
  bridge = new Bridge(deps); bridge.wecomReady = true;
  await bridge.drain();
  assert.deepEqual(store.all('SELECT * FROM jobs ORDER BY id'), before.jobs);
  assert.equal(sent.length, 0);
  await bridge.stop();
  const application = new IssueReviewApplication({ issueRepository: 'example/project' });
  application.initialize({ store, config }); application.initialize({ store, config });
  assert.deepEqual(store.all('SELECT * FROM issue_proposals ORDER BY id'), before.proposals);
  assert.deepEqual(store.all('SELECT * FROM issue_decisions'), before.decisions);
  assert.deepEqual(store.all('SELECT * FROM jobs ORDER BY id'), before.jobs);
  bridge = new Bridge({ ...deps, application }); bridge.wecomReady = true;
  await bridge.drain();
  assert.deepEqual(sent, [{ target: 'owner', text: '旧通知' }]);
  assert.equal(store.get("SELECT status FROM jobs WHERE request='legacy-request-1'").status, 'unknown');
  store.prune(100000);
  assert.equal(store.get('SELECT count(*) n FROM requests').n, 2);
  const token = before.proposals[0].id;
  assert.equal(bridge.receiveWecom({ body: { aibotid: 'test-bot', msgid: 'new-decision', msgtype: 'text', chattype: 'single', from: { userid: 'owner' }, text: { content: `/review ${token} 同意` } } }), true);
  await bridge.drain();
  assert.equal(posted.length, 1);
  assert.equal(posted[0].thread_ts, '100.0');
  assert.equal(posted[0].metadata.event_payload.decision_record_only, true);
  assert.equal(store.get('SELECT count(*) n FROM issue_decisions').n, 2);
  assert.equal(store.get('SELECT snapshot FROM issue_proposals WHERE id=?', token).snapshot, before.proposals[0].snapshot);
});
