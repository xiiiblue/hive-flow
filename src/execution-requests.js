import { randomUUID } from 'node:crypto';
import { chunks } from 'wecom-slack-bridge/text';
import { isExecutionPath, EXECUTION_OPERATIONS, isExecutionDelivery } from './execution-scope.js';

const fields = ['repo', 'issue_id', 'issue_number', 'version', 'base_sha', 'files', 'operations', 'summary', 'expires'];
const positive = n => Number.isSafeInteger(n) && n > 0;
// Keep untrusted prose readable without activating Markdown links or mentions.
const readable = value => value.replace(/[<>&`*_\[\]()#]/g, c => ({ '<': '＜', '>': '＞', '&': '＆', '`': '｀', '*': '＊', '_': '＿', '[': '［', ']': '］', '(': '（', ')': '）', '#': '＃' })[c]);
const operationLabels = { 'edit-code': '修改代码', 'run-tests': '测试', commit: '提交', push: '推送', 'open-pr': '创建 PR', merge: '合并', deploy: '部署' };
const expiryText = expires => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(expires) + ' 北京时间';
function scopeLines(p, compact = false) {
  const lines = [`操作：${p.operations.map(op => operationLabels[op]).join('、')}`];
  if (p.delivery) lines.push(`目标分支：${p.delivery.target_branch}${compact ? '' : `（工作分支：${p.delivery.work_branch}）`}`);
  if (p.delivery?.deployment) {
    const d = p.delivery.deployment;
    lines.push(`部署：${d.environment} / ${d.service}`);
    if (!compact) lines.push(`验收：${readable(d.verification)}；回退：${readable(d.rollback)}`);
  }
  return lines;
}
// Prefer line boundaries so copyable commands and exact file paths stay intact.
function messageParts(text) {
  const parts = [];
  for (const line of text.split('\n')) {
    for (const piece of chunks(line || ' ', 3400)) {
      if (parts.length && Buffer.byteLength(parts.at(-1) + '\n' + piece) <= 3400) parts[parts.length - 1] += '\n' + piece;
      else parts.push(piece);
    }
  }
  return parts;
}
export function executionPreview(p, id, ready) {
  return [
    `#${p.issue_number} v${p.version} · ${readable(p.summary)}`,
    `${p.repo} · ${p.files.length} 个文件`,
    ...scopeLines(p, true),
    `有效至：${expiryText(p.expires)}`,
    `完整方案：/plan #${p.issue_number} v${p.version}`,
    ready ? '确认完整方案后授权；额度/环境检查通过才启动，失败或范围变化即停。' : '执行器尚未就绪：仅预览，不收集执行批准。',
    ...(ready ? [`执行 #${p.issue_number} v${p.version}`] : []),
  ].join('\n');
}

const uuid = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
// Exact file paths, never glob patterns. A future adapter must enforce these paths
// against the pinned commit, including symlinks, before every operation.

export function parseExecutionPlan(text, repository, now) {
  if (!repository || typeof text !== 'string' || Buffer.byteLength(text) > 16384 || !text.startsWith('[wecom:execution-plan]')) return null;
  let p; try { p = JSON.parse(text.slice('[wecom:execution-plan]'.length)); } catch { return null; }
  if (!p || Array.isArray(p) || Object.keys(p).length !== fields.length + (Object.hasOwn(p, 'delivery') ? 1 : 0) || fields.some(k => !Object.hasOwn(p, k))) return null;
  if (p.repo !== repository || typeof p.issue_id !== 'string' || !/^[1-9][0-9]{0,19}$/.test(p.issue_id) || !positive(p.issue_number) || !positive(p.version) || typeof p.base_sha !== 'string' || !/^[0-9a-f]{40}$/.test(p.base_sha)) return null;
  if (!Array.isArray(p.files) || !p.files.length || p.files.length > 30 || new Set(p.files).size !== p.files.length || !p.files.every(isExecutionPath)) return null;
  if (!Array.isArray(p.operations) || !p.operations.length || new Set(p.operations).size !== p.operations.length || !p.operations.every(op => EXECUTION_OPERATIONS.includes(op))) return null;
  if (!isExecutionDelivery(p.delivery, p.operations)) return null;
  if (typeof p.summary !== 'string' || !p.summary.trim() || Buffer.byteLength(p.summary) > 2000 || /[\x00-\x1f\x7f]/.test(p.summary)) return null;
  if (!positive(p.expires) || p.expires <= now || p.expires - now > 7 * 86400000) return null;
  return Object.fromEntries([...fields, ...(Object.hasOwn(p, 'delivery') ? ['delivery'] : [])].map(k => [k, p[k]]));
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
    const parts = messageParts(executionPreview(p, id, manualExecutionEnabled));
    parts.forEach(text => store.addJob(request, 'wecom', { text, execution_preview: true }, 'owner', c.ownerUser, Math.min(p.expires, now + c.ttlMs)));
    return true;
  });
  if (inserted) bridge.drain(); return inserted;
}

// Read-only view of the immutable snapshot, under the same authenticated owner
// ingress and pinned repository/channel checks as execution. Never creates a grant.
export function receiveExecutionDetails(bridge, text, now, repository) {
  if (!/^\/plan(?:\s|$)/.test(text)) return null;
  const match = new RegExp(`^/plan #([1-9][0-9]*) v([1-9][0-9]*)(?: (${uuid}))?$`).exec(text);
  if (!match || !repository) return false;
  const { store, config: c } = bridge;
  const row = match[3]
    ? store.get('SELECT * FROM execution_plans WHERE id=?', match[3])
    : store.get('SELECT * FROM execution_plans WHERE repo=? AND issue_number=? AND version=? AND owner=? AND channel=?', repository, Number(match[1]), Number(match[2]), c.ownerUser, c.slackChannel);
  if (!row || !repository || row.repo !== repository || row.owner !== c.ownerUser || row.channel !== c.slackChannel || row.issue_number !== Number(match[1]) || row.version !== Number(match[2])) return false;
  const p = JSON.parse(row.snapshot);
  const latest = store.get('SELECT id FROM execution_plans WHERE repo=? AND issue_id=? ORDER BY version DESC LIMIT 1', row.repo, row.issue_id);
  const status = now >= row.expires ? '已过期' : latest.id !== row.id ? '已被新版本替代' : '有效期内';
  const detail = [
    `完整方案 #${p.issue_number} v${p.version}（${status}；查询不会授权或执行）`,
    `仓库：${p.repo}`, `方案编号：${row.id}`, `Issue ID：${p.issue_id}`,
    `方案：${readable(p.summary)}`, `基准提交：${p.base_sha}`,
    `精确文件（${p.files.length}）：`, ...p.files.map(path => `- ${path}`),
    ...scopeLines(p), `有效至：${expiryText(p.expires)}`,
  ].join('\n');
  messageParts(detail).forEach(text => store.addJob(row.request, 'wecom', { text }, 'owner', c.ownerUser, now + c.ttlMs));
  return row.request;
}

// Trusted bridge invokes only inside authenticated owner private ingress transaction.
// Actor strings supplied outside that boundary are not authentication.
// Readiness is a trusted application capability, never data from a message.
// Even the ready path only emits a request; no network execution happens here.
export function receiveExecutionCommand(bridge, text, source, now, repository, dispatch = null) {
  if (!/^(?:执行|\/execute)(?:\s|$)/.test(text)) return null;
  const match = new RegExp(`^(?:执行 #([1-9][0-9]*) v([1-9][0-9]*)|/execute #([1-9][0-9]*) v([1-9][0-9]*)(?: (${uuid}))?)(?![\\s\\S])`).exec(text);
  if (!match || !repository) return false;
  const issueNumber = Number(match[1] ?? match[3]), version = Number(match[2] ?? match[4]);
  if (!positive(issueNumber) || !positive(version)) return false;
  const { store, config: c } = bridge;
  // Resolve an exact pinned version only; ambiguity must never choose a plan.
  const candidates = match[5]
    ? store.all('SELECT * FROM execution_plans WHERE id=?', match[5])
    : store.all('SELECT * FROM execution_plans WHERE repo=? AND issue_number=? AND version=? AND owner=? AND channel=? LIMIT 2', repository, issueNumber, version, c.ownerUser, c.slackChannel);
  if (candidates.length !== 1) return false;
  const [p] = candidates;
  if (!p || !repository || p.repo !== repository || p.owner !== c.ownerUser || p.channel !== c.slackChannel || p.issue_number !== issueNumber || p.version !== version || now >= p.expires) return false;
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
    ? `问题 #${p.issue_number} · 方案 v${p.version}：已记录本人明确的执行请求，尚未启动。须通过额度、环境与范围检查后才能派发；仅执行方案列明的操作及交付目标。`
    : `问题 #${p.issue_number} · 方案 v${p.version}：执行器尚未就绪，未授权、未启动执行。此请求不会排队或在重启后执行。执行能力接入后须重新发布新版本方案并明确请求。`;
  const eventType = ready ? 'hiveflow_execution_requested' : 'hiveflow_execution_request_result';
  const record = JSON.stringify(result).replace(/[<>&]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
  store.addJob(p.request, 'slack', { part: 1, parts: 1, metadata: { event_type: eventType, event_payload: result }, text: `${ready ? 'Execution requested' : 'Execution request result'}\n${record}\n${explanation}` }, 'slack', c.slackChannel, Math.min(p.expires, now + c.ttlMs));
  store.addJob(p.request, 'wecom', { text: explanation }, 'owner', c.ownerUser, now + c.ttlMs);
  return p.request;
}
