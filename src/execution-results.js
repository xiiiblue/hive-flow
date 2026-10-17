import { EXECUTION_OPERATIONS } from './execution-scope.js';
// Audit evidence supplied by the authenticated controller, never an authorization
// or independent proof. Caller MUST redact secrets and private data; bounded text
// is not a secret scanner. No raw logs, executable payloads, or external writes.
const fields = ['outcome', 'summary', 'completed_operations', 'verification', 'artifacts', 'remaining'];
const exact = (o, keys) => o && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).length === keys.length && keys.every(k => Object.hasOwn(o, k));
const text = (s, max) => typeof s === 'string' && !!s.trim() && Buffer.byteLength(s) <= max && !/[\x00-\x1f\x7f]/.test(s);
const strings = (a, maxCount, maxLength) => Array.isArray(a) && a.length <= maxCount && [...a].every(s => text(s, maxLength));
export function validExecutionReport(p) {
  return !!(exact(p, fields) && ['completed', 'stopped', 'blocked'].includes(p.outcome)
    && text(p.summary, 600) && Array.isArray(p.completed_operations) && p.completed_operations.length <= EXECUTION_OPERATIONS.length
    && new Set(p.completed_operations).size === p.completed_operations.length && [...p.completed_operations].every(op => EXECUTION_OPERATIONS.includes(op))
    && strings(p.verification, 10, 500) && strings(p.remaining, 10, 500)
    && Array.isArray(p.artifacts) && p.artifacts.length <= 10 && [...p.artifacts].every(a => exact(a, ['kind', 'ref']) && ['diff', 'test', 'commit', 'pr', 'deployment', 'rollback'].includes(a.kind) && text(a.ref, 500))
    && (p.outcome !== 'completed' || (p.verification.length > 0 && p.remaining.length === 0)));
}
export function canonicalExecutionReport(p) {
  return { outcome: p.outcome, summary: p.summary, completed_operations: [...p.completed_operations].sort(), verification: [...p.verification], artifacts: p.artifacts.map(a => ({ kind: a.kind, ref: a.ref })), remaining: [...p.remaining] };
}
export function initializeExecutionResults(store) {
  store.db.exec(`CREATE TABLE IF NOT EXISTS execution_results (
    attempt_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, grant_id TEXT NOT NULL,
    controller_run_id TEXT NOT NULL, request TEXT NOT NULL, owner TEXT NOT NULL,
    channel TEXT NOT NULL, root TEXT NOT NULL, report TEXT NOT NULL, created INTEGER NOT NULL);`);
}
export function getExecutionResult(store, attemptId) {
  const row = store.get('SELECT * FROM execution_results WHERE attempt_id=?', attemptId);
  return row ? { ...row, report: JSON.parse(row.report) } : null;
}
// Called only inside the trusted ingress transaction. Immutable per attempt;
// outbox and audit commit together, including recovery reports after expiry.
export function recordExecutionResult(store, plan, grant, attempt, report, now) {
  if (!store.db.isTransaction) throw new Error('transaction_required');
  if (!validExecutionReport(report)) throw new Error('invalid_execution_report');
  const scope = JSON.parse(grant.binding);
  if (grant.plan_id !== plan.id || attempt.grant_id !== grant.id || scope.id !== plan.id) throw new Error('result_binding_mismatch');
  if (!report.completed_operations.every(op => scope.operations.includes(op))) throw new Error('result_operation_not_granted');
  if (report.outcome === 'completed' && !scope.operations.every(op => report.completed_operations.includes(op))) throw new Error('completion_evidence_required');
  const snapshot = JSON.stringify(canonicalExecutionReport(report));
  const old = getExecutionResult(store, attempt.attempt_id);
  if (old) {
    if (old.plan_id !== plan.id || old.grant_id !== grant.id || old.controller_run_id !== attempt.controller_run_id || old.request !== plan.request || old.owner !== plan.owner || old.channel !== plan.channel || old.root !== plan.root || JSON.stringify(old.report) !== snapshot) throw new Error('immutable_execution_result_mismatch');
    return { record: old, inserted: false };
  }
  store.run('INSERT INTO execution_results VALUES (?,?,?,?,?,?,?,?,?,?)', attempt.attempt_id, plan.id, grant.id, attempt.controller_run_id, plan.request, plan.owner, plan.channel, plan.root, snapshot, now);
  return { record: getExecutionResult(store, attempt.attempt_id), inserted: true };
}
export function hasCompletionEvidence(store, plan, grant, attempt) {
  const r = getExecutionResult(store, attempt.attempt_id);
  return !!(r && r.plan_id === plan.id && r.grant_id === grant.id && r.controller_run_id === attempt.controller_run_id && r.request === plan.request && r.owner === plan.owner && r.channel === plan.channel && r.root === plan.root && r.report.outcome === 'completed' && JSON.parse(grant.binding).operations.every(op => r.report.completed_operations.includes(op)));
}
export function executionResultText(plan, report) {
  const operations = { 'edit-code': '修改代码', 'run-tests': '测试', commit: '提交', push: '推送', 'open-pr': '创建 PR', merge: '合并', deploy: '部署' };
  const artifacts = { diff: '改动', test: '测试', commit: '提交', pr: 'PR', deployment: '部署', rollback: '回退' };
  return [`#${plan.issue_number} v${plan.version} 结果已记录`, report.summary,
    `已完成：${report.completed_operations.map(op => operations[op]).join('、') || '暂无'}`,
    ...report.verification.slice(0, 2).map(s => `验证：${s}`),
    ...report.artifacts.slice(0, 3).map(a => `${artifacts[a.kind]}：${a.ref}`),
    ...report.remaining.map(s => `待处理：${s}`)].join('\n');
}
