import { chunks } from 'wecom-slack-bridge/text';
import { initializeExecutionResults, validExecutionReport, canonicalExecutionReport, recordExecutionResult, hasCompletionEvidence, executionResultText } from './execution-results.js';
import { EXECUTION_OPERATIONS, DELIVERY_OPERATIONS, isExecutionDelivery } from './execution-scope.js';
import { canonicalDelivery } from './manual-dispatch.js';
/** Internal control messages from the bridge-authenticated configured Slack
 * responder, on NEW roots only. This module performs no external task actions.
 * Quota and worker-state provenance must be verified by that trusted responder.
 */
const prefix = '[wecom:execution-control]';
const common = ['command_id', 'action', 'plan_id', 'grant_id', 'controller_run_id', 'attempt_id'];
const extras = Object.freeze({ reserve: [], start: ['quota'], status: [], started: ['thread_id', 'turn_id'], checkpoint: ['operation', 'files', 'quota'], stop: [], terminal: ['outcome', 'worker_stopped'], report: ['report'], unknown: [] });
const actionFields = p => [...common, ...extras[p.action], ...(p.action === 'checkpoint' && DELIVERY_OPERATIONS.includes(p.operation) ? ['delivery'] : [])];
const id = s => typeof s === 'string' && /^[A-Za-z0-9_.:-]{1,200}$/.test(s);
const exact = (o, fields) => o && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).length === fields.length && fields.every(k => Object.hasOwn(o, k));
export function parseExecutionControl(text) {
  if (typeof text !== 'string' || !text.startsWith(prefix) || Buffer.byteLength(text) > 16384) return null;
  let p; try { p = JSON.parse(text.slice(prefix.length)); } catch { return null; }
  if (!p || !Object.hasOwn(extras, p.action) || !exact(p, actionFields(p)) || !common.filter(k => k !== 'action').every(k => id(p[k]))) return null;
  if (p.action === 'started' && (!id(p.thread_id) || !id(p.turn_id))) return null;
  if (Object.hasOwn(p, 'quota') && p.quota !== null && (!exact(p.quota, ['remainingPercent', 'observedAt']) || typeof p.quota.remainingPercent !== 'number' || !Number.isFinite(p.quota.remainingPercent) || !Number.isSafeInteger(p.quota.observedAt))) return null;
  if (p.action === 'checkpoint' && (!EXECUTION_OPERATIONS.includes(p.operation) || !Array.isArray(p.files) || !p.files.length || p.files.length > 30 || !p.files.every(f => typeof f === 'string' && f.length <= 300))) return null;
  if (p.action === 'checkpoint' && DELIVERY_OPERATIONS.includes(p.operation) && !isExecutionDelivery(p.delivery, p.delivery?.deployment ? ['deploy'] : ['push'])) return null;
  if (p.action === 'terminal' && (!['stopped', 'completed'].includes(p.outcome) || p.worker_stopped !== true)) return null;
  if (p.action === 'report' && !validExecutionReport(p.report)) return null;
  return p;
}
export function initializeExecutionControl(store) {
  initializeExecutionResults(store);
  store.db.exec(`CREATE TABLE IF NOT EXISTS execution_control_commands (
    command_id TEXT PRIMARY KEY, source TEXT UNIQUE NOT NULL, plan_id TEXT NOT NULL,
    snapshot TEXT NOT NULL, result TEXT NOT NULL, created INTEGER NOT NULL);`);
}
const methodFor = Object.freeze({ reserve: 'reserve', start: 'startDispatch', started: 'recordStarted', checkpoint: 'checkpoint', stop: 'requestStop', terminal: 'confirmTerminal', unknown: 'markUnknown' });
export function receiveExecutionControl(bridge, event, repository, dispatch) {
  const p = parseExecutionControl(event.text);
  if (!p || !dispatch || !repository || event.thread_ts || !id(event.ts)) return false;
  const { store, config } = bridge, now = bridge.now();
  // Stable canonical snapshot makes JSON object key ordering irrelevant to dedup.
  const snapshot = JSON.stringify(Object.fromEntries(actionFields(p).map(k => [k, k === 'delivery' ? canonicalDelivery(p[k]) : k === 'report' ? canonicalExecutionReport(p[k]) : p[k]])));
  const accepted = store.transaction(() => {
    const plan = store.get('SELECT * FROM execution_plans WHERE id=?', p.plan_id);
    if (!plan || plan.repo !== repository || plan.owner !== config.ownerUser || plan.channel !== config.slackChannel) return false;
    const request = store.get('SELECT * FROM requests WHERE id=?', plan.request);
    if (!request || request.root !== plan.root || request.owner !== plan.owner || request.channel !== plan.channel || request.kind !== 'execution_plan') return false;
    const grant = store.get('SELECT * FROM manual_execution_grants WHERE id=? AND plan_id=?', p.grant_id, p.plan_id);
    if (!grant || grant.actor !== plan.owner) return false;
    const old = store.get('SELECT * FROM execution_control_commands WHERE command_id=? OR source=?', p.command_id, event.ts);
    if (old) return old.command_id === p.command_id && old.snapshot === snapshot;
    const attempt = dispatch.attempt(p.attempt_id);
    // Never permit one plan/controller to stop, release, inspect, or mutate another.
    if (attempt && (attempt.grant_id !== p.grant_id || attempt.controller_run_id !== p.controller_run_id)) return false;
    if (p.action !== 'reserve' && !attempt) return false;
    const args = { grantId: p.grant_id, attemptId: p.attempt_id, controllerRunId: p.controller_run_id, quota: p.quota, threadId: p.thread_id, turnId: p.turn_id, files: p.files, operation: p.operation, delivery: p.delivery, outcome: p.outcome, workerStopped: p.worker_stopped };
    let result;
    try {
      if (p.action === 'terminal' && p.outcome === 'completed' && !hasCompletionEvidence(store, plan, grant, attempt)) throw new Error('completion_evidence_required');
      let state;
      if (p.action === 'report') {
        const saved = recordExecutionResult(store, plan, grant, attempt, p.report, now);
        state = { attempt, report: saved.record.report };
        if (saved.inserted) chunks(executionResultText(plan, saved.record.report), 3400).forEach(text => store.addJob(plan.request, 'wecom', { text, execution_result: true, attempt_id: attempt.attempt_id }, 'owner', plan.owner, now + config.ttlMs));
      } else state = p.action === 'status' ? attempt : dispatch.executeInTransaction(methodFor[p.action], args);
      result = { command_id: p.command_id, action: p.action, plan_id: p.plan_id, grant_id: p.grant_id, controller_run_id: p.controller_run_id, attempt_id: p.attempt_id, ok: true, state };
    } catch (error) {
      // Known state-machine rejections are durable results; SQLite/unexpected
      // errors must roll back rather than acknowledging partially applied state.
      const allowed = /^(invalid_attempt|attempt_identity_mismatch|pipeline_busy|invalid_execution_grant|grant_already_reserved|attempt_not_active|controller_mismatch|dispatch_already_started|grant_revoked_or_expired|stop_requested|grant_consumed|quota_unknown_or_stale|quota_too_low|task_identity_required|invalid_attempt_state|task_identity_mismatch|worker_not_confirmed|operation_not_granted|file_not_granted|delivery_scope_mismatch|terminal_confirmation_required|completion_not_confirmed|completion_evidence_required|invalid_execution_report|result_binding_mismatch|result_operation_not_granted|immutable_execution_result_mismatch)$/;
      if (!allowed.test(error.message)) throw error;
      result = { command_id: p.command_id, action: p.action, plan_id: p.plan_id, grant_id: p.grant_id, controller_run_id: p.controller_run_id, attempt_id: p.attempt_id, ok: false, error: error.message };
    }
    store.run('INSERT INTO execution_control_commands VALUES (?,?,?,?,?,?)', p.command_id, event.ts, p.plan_id, snapshot, JSON.stringify(result), now);
    store.addJob(plan.request, 'slack', { part: 1, parts: 1, metadata: { event_type: 'hiveflow_execution_control_result', event_payload: result }, text: `Execution control result\n${JSON.stringify(result)}` }, `execution-control:${p.command_id}`, plan.channel, now + config.ttlMs);
    return true;
  });
  if (accepted) bridge.drain();
  return accepted;
}
