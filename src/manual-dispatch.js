/** Manual parent-managed dispatch protocol, not an executor or scanner.
 * Uses the bridge Store and its existing SQLite transaction boundary. This is
 * deliberately separate from dormant WorkflowControl's standalone legacy DB.
 * A trusted caller authenticates approval, verifies the current GitHub plan and
 * quota provenance, enforces file/symlink scope, and invokes cloud_threads itself.
 * Actor strings and quota values here are NOT authentication or provider proof.
 */
const demand = (ok, code) => { if (!ok) throw new Error(code); };
const text = s => typeof s === 'string' && !!s.trim();
const positive = n => Number.isSafeInteger(n) && n > 0;
import { isExecutionPath as validPath, EXECUTION_OPERATIONS, DELIVERY_OPERATIONS, isExecutionDelivery } from './execution-scope.js';
export const MANUAL_OPERATIONS = EXECUTION_OPERATIONS;
export function canonicalDelivery(d) {
  if (d === undefined) return undefined;
  return { target_branch: d.target_branch, work_branch: d.work_branch, deployment: d.deployment === null ? null : { environment: d.deployment.environment, service: d.deployment.service, verification: d.deployment.verification, rollback: d.deployment.rollback } };
}
function binding(p) {
  demand(p && text(p.id) && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(p.repo) && typeof p.issue_id === 'string' && /^[1-9][0-9]{0,19}$/.test(p.issue_id) && positive(p.issue_number) && positive(p.version) && /^[0-9a-f]{40}$/.test(p.base_sha) && positive(p.expires), 'invalid_plan');
  demand(Array.isArray(p.files) && p.files.length > 0 && p.files.length <= 30 && new Set(p.files).size === p.files.length && [...p.files].every(validPath), 'invalid_scope');
  demand(Array.isArray(p.operations) && p.operations.length > 0 && new Set(p.operations).size === p.operations.length && [...p.operations].every(op => MANUAL_OPERATIONS.includes(op)), 'invalid_operations');
  demand(isExecutionDelivery(p.delivery, p.operations), 'invalid_delivery');
  return JSON.stringify({ id: p.id, repo: p.repo, issue_id: p.issue_id, issue_number: p.issue_number, version: p.version, base_sha: p.base_sha, files: [...p.files].sort(), operations: [...p.operations].sort(), ...(p.delivery === undefined ? {} : { delivery: canonicalDelivery(p.delivery) }), expires: p.expires });
}
export class ManualDispatch {
  constructor(store, { now = Date.now } = {}) {
    demand(store && typeof store.transaction === 'function' && typeof now === 'function', 'invalid_configuration');
    this.store = store; this.now = now;
    store.db.exec(`CREATE TABLE IF NOT EXISTS manual_execution_grants (
      id TEXT PRIMARY KEY, plan_id TEXT NOT NULL UNIQUE, binding TEXT NOT NULL,
      actor TEXT NOT NULL, expires INTEGER NOT NULL, used INTEGER NOT NULL DEFAULT 0,
      revoked INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS manual_dispatch_attempts (
      attempt_id TEXT PRIMARY KEY, controller_run_id TEXT NOT NULL,
      grant_id TEXT UNIQUE NOT NULL, slot INTEGER UNIQUE CHECK(slot=1),
      status TEXT NOT NULL, stop_requested INTEGER NOT NULL DEFAULT 0,
      thread_id TEXT, turn_id TEXT, created INTEGER NOT NULL, updated INTEGER NOT NULL);`);
  }
  transaction(fn) { return this.store.transaction(fn); }
  // Trusted ingress may compose state + acknowledgement in its existing atomic
  // Store transaction. Never call this seam without an open transaction.
  executeInTransaction(method, args) {
    demand(this.store.db.isTransaction === true, 'transaction_required');
    demand(['reserve', 'startDispatch', 'recordStarted', 'markUnknown', 'checkpoint', 'requestStop', 'confirmTerminal'].includes(method), 'invalid_control_method');
    const scoped = Object.create(this);
    scoped.transaction = fn => fn();
    return scoped[method](args);
  }
  registerGrant(args) { return this.transaction(() => this.registerGrantInTransaction(args)); }
  // Only for an already-open Store transaction (e.g. authenticated ingress with outbox).
  registerGrantInTransaction({ grantId, plan, kind, decisionRecordOnly, actor }) {
    demand(this.store.db.isTransaction === true, 'transaction_required');
    demand(kind === 'execution' && decisionRecordOnly === false && text(actor), 'explicit_execution_grant_required');
    demand(text(grantId), 'invalid_grant');
    const snapshot = binding(plan);
    demand(plan.expires > this.now(), 'grant_expired');
    const old = this.store.get('SELECT * FROM manual_execution_grants WHERE id=? OR plan_id=?', grantId, plan.id);
    if (old) { demand(old.id === grantId && old.binding === snapshot && old.actor === actor, 'immutable_grant_mismatch'); return old; }
    this.store.run('INSERT INTO manual_execution_grants(id,plan_id,binding,actor,expires) VALUES (?,?,?,?,?)', grantId, plan.id, snapshot, actor, plan.expires);
    return this.store.get('SELECT * FROM manual_execution_grants WHERE id=?', grantId);
  }
  attempt(attemptId) { return this.store.get('SELECT * FROM manual_dispatch_attempts WHERE attempt_id=?', attemptId) ?? null; }
  status() { return { executorImplemented: false, active: this.store.get('SELECT * FROM manual_dispatch_attempts WHERE slot=1') ?? null }; }
  active({ controllerRunId, attemptId }) {
    const a = this.attempt(attemptId);
    demand(a && a.slot === 1, 'attempt_not_active');
    demand(a.controller_run_id === controllerRunId, 'controller_mismatch');
    return a;
  }
  autoEntry(grantId) {
    if (!this.store.get("SELECT name FROM sqlite_master WHERE type='table' AND name='auto_queue_entries'")) return null;
    return this.store.get(`SELECT q.attempt_id,q.status AS queue_status,s.id AS session_id,s.status,s.deadline
      FROM auto_queue_entries q LEFT JOIN auto_queue_sessions s ON s.id=q.session_id WHERE q.grant_id=?`, grantId) ?? null;
  }
  pauseAuto(a, reason) {
    const auto = a && this.autoEntry(a.grant_id);
    if (!auto || auto.attempt_id !== a.attempt_id) return;
    this.store.run("UPDATE auto_queue_sessions SET status=? WHERE id=? AND status='active'", reason, auto.session_id);
    this.store.run('UPDATE manual_dispatch_attempts SET stop_requested=1,updated=? WHERE attempt_id=? AND slot=1', this.now(), a.attempt_id);
  }
  grant(a) {
    const g = this.store.get('SELECT * FROM manual_execution_grants WHERE id=?', a.grant_id);
    demand(g && !g.revoked && this.now() < g.expires, 'grant_revoked_or_expired');
    demand(!a.stop_requested, 'stop_requested');
    // Dormant auto queue shares this slot. Enforce its session even if a future
    // adapter accidentally enters through the manual control route.
    const auto = this.autoEntry(a.grant_id);
    demand(!auto || (auto.attempt_id === a.attempt_id && auto.queue_status === 'claimed' && auto.status === 'active' && this.now() < auto.deadline), 'auto_session_inactive');
    return g;
  }
  quota(q) {
    const now = this.now();
    demand(q && Number.isFinite(q.remainingPercent) && q.remainingPercent >= 0 && q.remainingPercent <= 100 && Number.isSafeInteger(q.observedAt) && q.observedAt <= now && now - q.observedAt <= 60_000, 'quota_unknown_or_stale');
    demand(q.remainingPercent > 5, 'quota_too_low');
  }
  reserve({ grantId, controllerRunId, attemptId }) {
    demand(text(grantId) && text(controllerRunId) && text(attemptId), 'invalid_attempt');
    return this.transaction(() => {
      const old = this.attempt(attemptId);
      if (old) { demand(old.controller_run_id === controllerRunId && old.grant_id === grantId, 'attempt_identity_mismatch'); return old; }
      demand(!this.status().active, 'pipeline_busy');
      const auto = this.autoEntry(grantId);
      demand(!auto || (auto.attempt_id === attemptId && auto.queue_status === 'claimed' && auto.status === 'active' && this.now() < auto.deadline), 'auto_session_inactive');
      const g = this.store.get('SELECT * FROM manual_execution_grants WHERE id=?', grantId);
      demand(g && !g.used && !g.revoked && this.now() < g.expires, 'invalid_execution_grant');
      demand(!this.store.get('SELECT attempt_id FROM manual_dispatch_attempts WHERE grant_id=?', grantId), 'grant_already_reserved');
      const now = this.now();
      this.store.run("INSERT INTO manual_dispatch_attempts(attempt_id,controller_run_id,grant_id,slot,status,created,updated) VALUES (?,?,?,1,'reserved',?,?)", attemptId, controllerRunId, grantId, now, now);
      return this.attempt(attemptId);
    });
  }
  /** Commit this BEFORE external task creation. Repeated calls never authorize a
   * second create. Crash after commit is uncertain and retains the slot forever
   * until explicit reconciliation; there is no lease, retry, or restart takeover. */
  startDispatch(args) {
    return this.withQuotaStop(args, () => {
      const a = this.active(args);
      demand(a.status === 'reserved', 'dispatch_already_started');
      const g = this.grant(a); demand(!g.used, 'grant_consumed'); this.quota(args.quota);
      this.store.run('UPDATE manual_execution_grants SET used=1 WHERE id=?', g.id);
      this.store.run("UPDATE manual_dispatch_attempts SET status='dispatch_started',updated=? WHERE attempt_id=?", this.now(), a.attempt_id);
      return { ...this.attempt(a.attempt_id), plan: JSON.parse(g.binding) };
    });
  }
  // Recording a known task is allowed after stop/expiry: never hide a live worker.
  recordStarted(args) {
    demand(text(args.threadId) && text(args.turnId), 'task_identity_required');
    return this.transaction(() => {
      const a = this.active(args);
      demand(['dispatch_started', 'unknown', 'running'].includes(a.status), 'invalid_attempt_state');
      demand(!a.thread_id || (a.thread_id === args.threadId && a.turn_id === args.turnId), 'task_identity_mismatch');
      this.store.run("UPDATE manual_dispatch_attempts SET status='running',thread_id=?,turn_id=?,updated=? WHERE attempt_id=?", args.threadId, args.turnId, this.now(), a.attempt_id);
      return this.attempt(a.attempt_id);
    });
  }
  markUnknown(args) {
    return this.transaction(() => {
      const a = this.active(args); demand(['dispatch_started', 'running', 'unknown'].includes(a.status), 'invalid_attempt_state');
      this.store.run("UPDATE manual_dispatch_attempts SET status='unknown',updated=? WHERE attempt_id=?", this.now(), a.attempt_id);
      this.pauseAuto(a, 'unknown_paused');
      return this.attempt(a.attempt_id);
    });
  }
  /** Cooperative checkpoint immediately before every stage. Does not interrupt
   * an in-flight side effect. Actual repository/path enforcement is adapter-owned. */
  checkpoint(args) {
    return this.withQuotaStop(args, () => this.checkpointInTransaction(args));
  }
  // Both pre-dispatch and running quota failures latch a stop. Identity/state
  // checks run before quota validation, so a rejected caller cannot stop a peer.
  withQuotaStop(args, fn) {
    const result = this.transaction(() => {
      try { return { value: fn() }; }
      catch (error) {
        if (!['quota_unknown_or_stale', 'quota_too_low', 'auto_session_inactive'].includes(error.message)) throw error;
        // A fresh quota snapshot is not resume authorization. Retain the slot
        // until the trusted controller verifies terminal/no-worker state.
        this.store.run('UPDATE manual_dispatch_attempts SET stop_requested=1,updated=? WHERE attempt_id=? AND slot=1', this.now(), args.attemptId);
        this.pauseAuto(this.attempt(args.attemptId), error.message === 'auto_session_inactive' ? 'inactive_paused' : 'quota_paused');
        return { error };
      }
    });
    if (result.error) throw result.error;
    return result.value;
  }
  checkpointInTransaction(args) {
      const a = this.active(args); demand(a.status === 'running', 'worker_not_confirmed');
      const g = this.grant(a), p = JSON.parse(g.binding); this.quota(args.quota);
      demand(p.operations.includes(args.operation), 'operation_not_granted');
      if (DELIVERY_OPERATIONS.includes(args.operation)) {
        demand(isExecutionDelivery(args.delivery, p.operations) && args.delivery !== undefined && JSON.stringify(canonicalDelivery(args.delivery)) === JSON.stringify(p.delivery), 'delivery_scope_mismatch');
      } else demand(args.delivery === undefined, 'delivery_scope_mismatch');
      demand(Array.isArray(args.files) && args.files.length > 0 && [...args.files].every(f => p.files.includes(f)), 'file_not_granted');
      return { attemptId: a.attempt_id, threadId: a.thread_id, turnId: a.turn_id, plan: p, operation: args.operation, files: [...args.files], ...(args.delivery === undefined ? {} : { delivery: canonicalDelivery(args.delivery) }) };
  }
  requestStop({ attemptId }) {
    return this.transaction(() => {
      demand(this.attempt(attemptId)?.slot === 1, 'attempt_not_active');
      this.store.run('UPDATE manual_dispatch_attempts SET stop_requested=1,updated=? WHERE attempt_id=?', this.now(), attemptId);
      this.pauseAuto(this.attempt(attemptId), 'stop_paused');
      return this.attempt(attemptId);
    });
  }
  revokeGrant({ grantId }) {
    return this.transaction(() => {
      demand(this.store.get('SELECT id FROM manual_execution_grants WHERE id=?', grantId), 'invalid_grant');
      this.store.run('UPDATE manual_execution_grants SET revoked=1 WHERE id=?', grantId);
      this.store.run('UPDATE manual_dispatch_attempts SET stop_requested=1,updated=? WHERE grant_id=? AND slot=1', this.now(), grantId);
      this.pauseAuto(this.store.get('SELECT * FROM manual_dispatch_attempts WHERE grant_id=? AND slot=1', grantId), 'revoked_paused');
    });
  }
  /** Caller must explicitly verify terminal worker state (or verify no task was
   * created). Unknown/timeout/closed controller alone is never that verification. */
  confirmTerminal({ attemptId, outcome, workerStopped }) {
    demand(workerStopped === true && ['stopped', 'completed'].includes(outcome), 'terminal_confirmation_required');
    return this.transaction(() => {
      const a = this.attempt(attemptId); demand(a && a.slot === 1, 'attempt_not_active');
      demand(outcome !== 'completed' || a.status === 'running', 'completion_not_confirmed');
      if (outcome === 'stopped') this.pauseAuto(a, 'stopped_paused');
      this.store.run('UPDATE manual_dispatch_attempts SET slot=NULL,status=?,updated=? WHERE attempt_id=?', outcome, this.now(), attemptId);
      return this.attempt(attemptId);
    });
  }
}
