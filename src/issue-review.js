import { initializeOwnerCommands, receiveOwnerCommand } from './owner-commands.js';
import { initializeExecutionControl, receiveExecutionControl } from './execution-control.js';
import { ManualDispatch } from './manual-dispatch.js';
import { randomUUID } from 'node:crypto';
import { initializeExecutionRequests, receiveExecutionPlan, receiveExecutionCommand, receiveExecutionDetails, executionAuthorized } from './execution-requests.js';
import { chunks } from 'wecom-slack-bridge/text';
// Render free text as a single JSON string; encode Markdown/mention syntax literally.
const literal = value => JSON.stringify(value).replace(/[<>&`*_\[\]()#]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
const fields = ['repo', 'issue_id', 'issue_number', 'title', 'revision', 'proposed_action', 'allowed_scope', 'source_kind'];
export function parseProposal(text, repo) {
  if (!repo || typeof text !== 'string' || Buffer.byteLength(text) > 8192 || !text.startsWith('[wecom:issue]')) return null;
  let p; try { p = JSON.parse(text.slice('[wecom:issue]'.length)); } catch { return null; }
  if (!p || Array.isArray(p) || typeof p !== 'object' || Object.keys(p).length !== fields.length || fields.some(k => !Object.hasOwn(p, k))) return null;
  if (p.repo !== repo || typeof p.issue_id !== 'string' || !/^[1-9][0-9]{0,19}$/.test(p.issue_id) || !Number.isSafeInteger(p.issue_number) || p.issue_number < 1 || !Number.isSafeInteger(p.revision) || p.revision < 1 || !['wecom', 'github', 'ai'].includes(p.source_kind)) return null;
  for (const [k, max] of [['title', 300], ['proposed_action', 2000], ['allowed_scope', 1000]]) {
    if (typeof p[k] !== 'string' || !p[k].trim() || Buffer.byteLength(p[k]) > max || /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(p[k])) return null;
  }
  return Object.fromEntries(fields.map(k => [k, p[k]]));
}
function issueAuthorized(bridge, request, issueRepository) {
  const p = bridge.store.get('SELECT * FROM issue_proposals WHERE request=?', request.request ?? request.id);
  return !!p && !!issueRepository && p.repo === issueRepository && p.owner === bridge.config.ownerUser && p.channel === bridge.config.slackChannel && p.root === request.root;
}
function receiveIssue(bridge, event, issueRepository) {
  const { store, config: c } = bridge; const now = bridge.now();
  const p = parseProposal(event.text, issueRepository); if (!p) return false;
  const snapshot = JSON.stringify(p);
  const inserted = store.transaction(() => {
    // GitHub immutable ID and human-facing number must stay bound in both directions.
    for (const table of ['issue_proposals', 'execution_plans']) {
      if (store.get(`SELECT id FROM ${table} WHERE repo=? AND ((issue_id=? AND issue_number!=?) OR (issue_number=? AND issue_id!=?))`, p.repo, p.issue_id, p.issue_number, p.issue_number, p.issue_id)) return false;
    }
    const latest = store.get('SELECT * FROM issue_proposals WHERE repo=? AND issue_id=? ORDER BY revision DESC LIMIT 1', p.repo, p.issue_id);
    // Includes identical redeliveries, conflicting same-version bodies, and stale versions.
    if (latest && p.revision <= latest.revision) return false;
    if (store.get('SELECT id FROM requests WHERE root=?', event.ts)) return false;
    const id = randomUUID(), review = randomUUID();
    const pending = store.all("SELECT request FROM issue_proposals WHERE repo=? AND issue_id=? AND status='pending'", p.repo, p.issue_id);
    for (const old of pending) store.run("UPDATE jobs SET status='superseded' WHERE request=? AND direction='wecom' AND status='pending'", old.request);
    store.run("UPDATE issue_proposals SET status='superseded' WHERE repo=? AND issue_id=? AND status='pending'", p.repo, p.issue_id);
    store.run('INSERT INTO requests(id,source,target,created,expires,root,status,kind,owner,channel) VALUES (?,?,?,?,?,?,?,?,?,?)', id, `issue:${review}`, c.ownerUser, now, now + c.ttlMs, event.ts, 'sent', 'issue', c.ownerUser, c.slackChannel);
    store.run('INSERT INTO issue_proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', review, id, p.repo, p.issue_id, p.issue_number, p.revision, snapshot, c.ownerUser, c.slackChannel, event.ts, now, 'pending');
    const text = `问题决策请求：本阶段仅记录决定，不会自动修改代码或部署\n${p.repo} #${p.issue_number} · 版本 ${p.revision}\nhttps://github.com/${p.repo}/issues/${p.issue_number}\n来源：${p.source_kind}\n标题：${literal(p.title)}\n建议操作：${literal(p.proposed_action)}\n允许范围：${literal(p.allowed_scope)}`;
    const footer = `\n\n请在本私聊复制其中一行，可在末尾空格后添加单行理由：\n/review ${review} 同意\n/review ${review} 不同意\n/review ${review} 暂缓\n只记录一次决定；暂缓后须提交更高版本才能重新决策。decision_record_only=true`;
    const parts = chunks(text, 3400);
    if (Buffer.byteLength(parts.at(-1) + footer) <= 3400) parts[parts.length - 1] += footer;
    else parts.push(footer.trimStart());
    parts.forEach(text => store.addJob(id, 'wecom', { text }, 'owner', c.ownerUser, now + c.ttlMs));
    return true;
  });
  if (inserted) bridge.drain(); return inserted;
}
// Called within the authenticated owner ingress transaction. No expiry is applied to audit records.
function decideIssue(bridge, review, words, source, now, issueRepository) {
  const { store, config: c } = bridge;
  const p = store.get('SELECT * FROM issue_proposals WHERE id=?', review);
  if (!p) return null;
  const m = /^(同意|不同意|暂缓)(?: ([^\r\n]+))?$/.exec(words);
  if (!m || Buffer.byteLength(m[2] || '') > 1000 || /[\x00-\x1f\x7f]/.test(m[2] || '') || p.status !== 'pending' || !issueRepository || p.repo !== issueRepository || p.owner !== c.ownerUser || p.channel !== c.slackChannel) return false;
  if (store.get('SELECT proposal FROM issue_decisions WHERE source=?', source)) return false;
  const decision = { '同意': 'approve', '不同意': 'reject', '暂缓': 'defer' }[m[1]];
  const reason = m[2] || '';
  store.run('INSERT INTO issue_decisions VALUES (?,?,?,?,?,?)', p.id, decision, reason, c.ownerUser, source, now);
  store.run("UPDATE issue_proposals SET status='decided' WHERE id=?", p.id);
  // Never execute a decision. The outbox only reports it to the authoritative root thread.
  const metadata = { event_type: 'wecom_bridge_issue_decision', event_payload: { request_id: p.request, review_id: p.id, repo: p.repo, issue_id: p.issue_id, issue_number: p.issue_number, revision: p.revision, decision, reason, decision_record_only: true } };
  const record = JSON.stringify(metadata.event_payload).replace(/[<>&]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  store.addJob(p.request, 'slack', { part: 1, parts: 1, metadata, text: `Issue decision record\n${record}\n问题 #${p.issue_number} · 版本 ${p.revision}：已记录${m[1]}。\n本阶段仅记录决定，不会自动修改代码或部署。decision_record_only=true` }, 'slack', c.slackChannel, now + c.ttlMs);
  return p.request;
}

// Application policy lives here, independently of generic transport configuration.
export function loadIssueConfig(env = process.env) {
  const issueRepository = env.ISSUE_REPOSITORY?.trim() || '';
  if (issueRepository && (!/^[A-Za-z0-9][A-Za-z0-9-]{0,38}\/[A-Za-z0-9_.-]{1,100}$/.test(issueRepository) || ['.', '..'].includes(issueRepository.split('/')[1]))) throw new Error('Invalid ISSUE_REPOSITORY');
  return { issueRepository };
}

export class IssueReviewApplication {
  constructor(config = loadIssueConfig(), { manualExecutionEnabled = false } = {}) {
    if (typeof manualExecutionEnabled !== 'boolean') throw new Error('Invalid manual execution enabled option');
    this.config = config; this.manualExecutionEnabled = manualExecutionEnabled; this.dispatch = null;
  }
  initialize({ store, now = Date.now }) {
    this.dispatch = this.manualExecutionEnabled ? new ManualDispatch(store, { now }) : null;
    // Additive, in-place schema ownership transfer. Existing audit rows remain untouched.
    store.transaction(() => {
      initializeExecutionRequests(store);
      initializeExecutionControl(store);
      initializeOwnerCommands(store);
      store.db.exec(`
      CREATE TABLE IF NOT EXISTS issue_proposals (id TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, repo TEXT NOT NULL, issue_id TEXT NOT NULL, issue_number INTEGER NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL, owner TEXT NOT NULL, channel TEXT NOT NULL, root TEXT NOT NULL, created INTEGER NOT NULL, status TEXT NOT NULL, UNIQUE(repo,issue_id,revision));
      CREATE TABLE IF NOT EXISTS issue_decisions (proposal TEXT PRIMARY KEY, decision TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, source TEXT UNIQUE NOT NULL, created INTEGER NOT NULL);
    `);
    });
  }
  receiveRoot(bridge, event) {
    if (event.text.startsWith('[wecom:execution-control]')) return receiveExecutionControl(bridge, event, this.config.issueRepository, this.manualExecutionEnabled ? this.dispatch : null);
    if (event.text.startsWith('[wecom:execution-plan]')) return receiveExecutionPlan(bridge, event, this.config.issueRepository, this.manualExecutionEnabled ? this.dispatch : null);
    return receiveIssue(bridge, event, this.config.issueRepository);
  }
  receiveOwnerText(bridge, text, source, now) {
    const ownerCommand = receiveOwnerCommand(bridge, text, source, now, this.config.issueRepository);
    if (ownerCommand !== null) return ownerCommand;
    const details = receiveExecutionDetails(bridge, text, now, this.config.issueRepository);
    if (details !== null) return details;
    const execution = receiveExecutionCommand(bridge, text, source, now, this.config.issueRepository, this.manualExecutionEnabled ? this.dispatch : null);
    if (execution !== null) return execution;
    const match = /^\/review ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}) ([\s\S]+)$/.exec(text);
    if (!match) return null;
    return decideIssue(bridge, match[1], match[2], source, now, this.config.issueRepository);
  }
  authorizeRequest(bridge, request) {
    if (request.kind === 'execution_plan') {
      // A disabled runtime must not forward a formerly queued dispatch trigger.
      const metadata = request.payload ? JSON.parse(request.payload).metadata : null;
      if (metadata?.event_type === 'hiveflow_execution_requested') {
        if (!this.manualExecutionEnabled) return false;
        const grant = bridge.store.get('SELECT * FROM manual_execution_grants WHERE id=?', metadata.event_payload.grant_id);
        if (!grant || grant.revoked || bridge.now() >= grant.expires) return false;
      }
      return executionAuthorized(bridge, request, this.config.issueRepository);
    }
    return request.kind === 'issue' && issueAuthorized(bridge, request, this.config.issueRepository);
  }
}
export function createIssueReviewApplication(env = process.env, options = {}) {
  return new IssueReviewApplication(loadIssueConfig(env), options);
}

// This explicit operator switch enables a protocol, not a proof that external
// quota, worker or webhook integrations are configured. No inbound data sets it.
export function loadManualExecutionEnabled(env = process.env) {
  const value = env.HIVEFLOW_MANUAL_EXECUTION_ENABLED;
  if (value === undefined || value === 'false') return false;
  if (value === 'true') return true;
  throw new Error('Invalid HIVEFLOW_MANUAL_EXECUTION_ENABLED');
}
