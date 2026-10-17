import { randomUUID } from 'node:crypto';
import { chunks } from 'wecom-slack-bridge/text';
import { isExecutionPath } from './execution-scope.js';
import { WORKFLOW_OPERATIONS } from './workflow-control.js';

const manualOperations = WORKFLOW_OPERATIONS.filter(op => ['edit-code', 'run-tests'].includes(op));
const fields = ['repo', 'issue_id', 'issue_number', 'version', 'base_sha', 'files', 'operations', 'summary', 'expires'];
const positive = n => Number.isSafeInteger(n) && n > 0;
const literal = value => JSON.stringify(value).replace(/[<>&`*_\[\]()#]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
// Exact file paths, never glob patterns. A future adapter must enforce these paths
// against the pinned commit, including symlinks, before every operation.

export function parseExecutionPlan(text, repository, now) {
  if (!repository || typeof text !== 'string' || Buffer.byteLength(text) > 16384 || !text.startsWith('[wecom:execution-plan]')) return null;
  let p; try { p = JSON.parse(text.slice('[wecom:execution-plan]'.length)); } catch { return null; }
  if (!p || Array.isArray(p) || Object.keys(p).length !== fields.length || fields.some(k => !Object.hasOwn(p, k))) return null;
  if (p.repo !== repository || typeof p.issue_id !== 'string' || !/^[1-9][0-9]{0,19}$/.test(p.issue_id) || !positive(p.issue_number) || !positive(p.version) || typeof p.base_sha !== 'string' || !/^[0-9a-f]{40}$/.test(p.base_sha)) return null;
  if (!Array.isArray(p.files) || !p.files.length || p.files.length > 30 || new Set(p.files).size !== p.files.length || !p.files.every(isExecutionPath)) return null;
  if (!Array.isArray(p.operations) || !p.operations.length || new Set(p.operations).size !== p.operations.length || !p.operations.every(op => manualOperations.includes(op))) return null;
  if (typeof p.summary !== 'string' || !p.summary.trim() || Buffer.byteLength(p.summary) > 2000 || /[\x00-\x1f\x7f]/.test(p.summary)) return null;
  if (!positive(p.expires) || p.expires <= now || p.expires - now > 7 * 86400000) return null;
  return Object.fromEntries(fields.map(k => [k, p[k]]));
}

export function initializeExecutionRequests(store) {
  store.db.exec(`
    CREATE TABLE IF NOT EXISTS execution_plans (
      id TEXT PRIMARY KEY, request TEXT UNIQUE NOT NULL, repo TEXT NOT NULL,
      issue_id TEXT NOT NULL, issue_number INTEGER NOT NULL, version INTEGER NOT NULL,
      snapshot TEXT NOT NULL, owner TEXT NOT NULL, channel TEXT NOT NULL,
      root TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL,
      UNIQUE(repo,issue_id,version));
    CREATE TABLE IF NOT EXISTS execution_requests (
      id TEXT PRIMARY KEY, plan TEXT UNIQUE NOT NULL, actor TEXT NOT NULL,
      source TEXT UNIQUE NOT NULL, created INTEGER NOT NULL,
      status TEXT NOT NULL CHECK(status IN ('not_ready','requested')), reason TEXT NOT NULL CHECK(reason IN ('executor_unavailable','explicit_owner_request')));
  `);
}

export function executionAuthorized(bridge, request, repository) {
  const p = bridge.store.get('SELECT * FROM execution_plans WHERE request=?', request.request ?? request.id);
  return !!p && !!repository && p.repo === repository && p.owner === bridge.config.ownerUser && p.channel === bridge.config.slackChannel && p.root === request.root;
}

export function receiveExecutionPlan(bridge, event, repository, dispatch = null) {
  const now = bridge.now(), { store, config: c } = bridge;
  const manualExecutionEnabled = dispatch !== null;
  const p = parseExecutionPlan(event.text, repository, now); if (!p) return false;
  const inserted = store.transaction(() => {
    for (const table of ['issue_proposals', 'execution_plans']) {
      if (store.get(`SELECT id FROM ${table} WHERE repo=? AND ((issue_id=? AND issue_number!=?) OR (issue_number=? AND issue_id!=?))`, p.repo, p.issue_id, p.issue_number, p.issue_number, p.issue_id)) return false;
    }
    const latest = store.get('SELECT version FROM execution_plans WHERE repo=? AND issue_id=? ORDER BY version DESC LIMIT 1', p.repo, p.issue_id);
    if (latest && p.version <= latest.version) return false;
    if (store.get('SELECT id FROM requests WHERE root=?', event.ts)) return false;
    const id = randomUUID(), request = randomUUID();
    if (store.get("SELECT name FROM sqlite_master WHERE type='table' AND name='manual_execution_grants'")) {
      // A newer immutable plan withdraws the older version's execution scope.
      store.run('UPDATE manual_execution_grants SET revoked=1 WHERE plan_id IN (SELECT id FROM execution_plans WHERE repo=? AND issue_id=?)', p.repo, p.issue_id);
      store.run('UPDATE manual_dispatch_attempts SET stop_requested=1,updated=? WHERE slot=1 AND grant_id IN (SELECT id FROM manual_execution_grants WHERE revoked=1)', now);
    }
    // Supersede only unsent previews. Immutable plans and attempt records stay intact.
    store.run("UPDATE jobs SET status='superseded' WHERE direction='wecom' AND status='pending' AND json_extract(payload,'$.execution_preview')=1 AND request IN (SELECT request FROM execution_plans WHERE repo=? AND issue_id=?)", p.repo, p.issue_id);
    store.run('INSERT INTO requests(id,source,target,created,expires,root,status,kind,owner,channel) VALUES (?,?,?,?,?,?,?,?,?,?)', request, `execution-plan:${id}`, c.ownerUser, now, now + c.ttlMs, event.ts, 'sent', 'execution_plan', c.ownerUser, c.slackChannel);
    store.run('INSERT INTO execution_plans VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', id, request, p.repo, p.issue_id, p.issue_number, p.version, JSON.stringify(p), c.ownerUser, c.slackChannel, event.ts, now, p.expires);
    const readiness = manualExecutionEnabled ? '执行方案：仅在本人明确请求后进入手动派发；不含推送或部署。' : '执行方案预览：执行器尚未就绪，当前不能执行，也不收集执行批准。';
    const preview = `${readiness}\n${p.repo} #${p.issue_number} · 方案 v${p.version}\n方案编号：${id}\n基准提交：${p.base_sha}\n范围（精确文件）：${literal(p.files)}\n操作：${literal(p.operations)}\n方案：${literal(p.summary)}\n有效期：${new Date(p.expires).toISOString()}\n方案不代表已核验 GitHub 状态；预览本身不会启动代码修改、部署或自动开发。`;
    const parts = chunks(preview, 3400);
    if (manualExecutionEnabled) parts.push(`如要授权此方案，请在本私聊发送：\n/execute #${p.issue_number} v${p.version} ${id}\n仅授权所列提交、文件、操作和有效期；额度与执行环境检查通过后才可启动。`);
    parts.forEach(text => store.addJob(request, 'wecom', { text, execution_preview: true }, 'owner', c.ownerUser, Math.min(p.expires, now + c.ttlMs)));
    return true;
  });
  if (inserted) bridge.drain(); return inserted;
}

// Trusted bridge invokes only inside authenticated owner private ingress transaction.
// Actor strings supplied outside that boundary are not authentication.
// Readiness is a trusted application capability, never data from a message.
// Even the ready path only emits a request; no network execution happens here.
export function receiveExecutionCommand(bridge, text, source, now, repository, dispatch = null) {
  if (!/^\/execute(?:\s|$)/.test(text)) return null;
  const match = new RegExp(`^/execute #([1-9][0-9]*) v([1-9][0-9]*) (${uuid})$`).exec(text);
  if (!match) return false;
  const { store, config: c } = bridge;
  const p = store.get('SELECT * FROM execution_plans WHERE id=?', match[3]);
  if (!p || !repository || p.repo !== repository || p.owner !== c.ownerUser || p.channel !== c.slackChannel || p.issue_number !== Number(match[1]) || p.version !== Number(match[2]) || now >= p.expires) return false;
  const latest = store.get('SELECT id FROM execution_plans WHERE repo=? AND issue_id=? ORDER BY version DESC LIMIT 1', p.repo, p.issue_id);
  if (latest.id !== p.id || store.get('SELECT id FROM execution_requests WHERE plan=? OR source=?', p.id, source)) return false;
  const id = randomUUID(), ready = dispatch !== null;
  const status = ready ? 'requested' : 'not_ready', reason = ready ? 'explicit_owner_request' : 'executor_unavailable';
  const plan = { ...JSON.parse(p.snapshot), id: p.id };
  if (ready) dispatch.registerGrantInTransaction({ grantId: id, plan, kind: 'execution', decisionRecordOnly: false, actor: c.ownerUser });
  store.run('INSERT INTO execution_requests VALUES (?,?,?,?,?,?,?)', id, p.id, c.ownerUser, source, now, status, reason);
  const result = { request_id: id, plan_id: p.id, repo: p.repo, issue_id: p.issue_id, issue_number: p.issue_number, version: p.version, status, reason, execution_authorized: ready, execution_started: false,
    ...(ready ? { grant_id: id, plan } : {}) };
  const explanation = ready
    ? `问题 #${p.issue_number} · 方案 v${p.version}：已记录本人明确的执行请求，尚未启动。须通过额度、环境与范围检查后才能派发；不含推送或部署。`
    : `问题 #${p.issue_number} · 方案 v${p.version}：执行器尚未就绪，未授权、未启动执行。此请求不会排队或在重启后执行。执行能力接入后须重新发布新版本方案并明确请求。`;
  const eventType = ready ? 'hiveflow_execution_requested' : 'hiveflow_execution_request_result';
  const record = JSON.stringify(result).replace(/[<>&]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  store.addJob(p.request, 'slack', { part: 1, parts: 1, metadata: { event_type: eventType, event_payload: result }, text: `${ready ? 'Execution requested' : 'Execution request result'}\n${record}\n${explanation}` }, 'slack', c.slackChannel, Math.min(p.expires, now + c.ttlMs));
  store.addJob(p.request, 'wecom', { text: explanation }, 'owner', c.ownerUser, now + c.ttlMs);
  return p.request;
}
