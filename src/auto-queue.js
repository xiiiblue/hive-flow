import { randomUUID } from 'node:crypto';

const demand = (ok, code) => { if (!ok) throw new Error(code); };
const text = s => typeof s === 'string' && !!s.trim();
export const MAX_AUTO_SESSION_MS = 7 * 24 * 60 * 60 * 1000;

/** Dormant state only. A trusted adapter authenticates owners and quota evidence.
 * No runtime imports this module; enabling this object does not create workers.
 * One instance is one controller lifetime; construction never resumes a session.
 */
export class AutoQueue {
  constructor(store, dispatch, { owner, repository, now = Date.now, enabled = false } = {}) {
    demand(store && dispatch?.store === store && typeof now === 'function' && typeof enabled === 'boolean' && text(owner) && /^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repository), 'invalid_configuration');
    this.store = store; this.dispatch = dispatch; this.now = now;
    this.owner = owner; this.repository = repository;
    this.enabled = enabled; this.runId = randomUUID();
    store.db.exec(`CREATE TABLE IF NOT EXISTS auto_queue_sessions (
      id TEXT PRIMARY KEY, actor TEXT NOT NULL, run_id TEXT NOT NULL,
      deadline INTEGER NOT NULL, status TEXT NOT NULL, created INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS auto_queue_entries (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
      grant_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL, authorization TEXT NOT NULL, status TEXT NOT NULL,
      attempt_id TEXT UNIQUE, created INTEGER NOT NULL);`);
    store.transaction(() => {
      for (const s of store.all("SELECT * FROM auto_queue_sessions WHERE status='active'")) this.halt(s, 'restart_paused');
    });
  }
  session(id) { return this.store.get('SELECT * FROM auto_queue_sessions WHERE id=?', id) ?? null; }
  // Transaction-only internal helpers. Halting never releases the pipeline slot.
  halt(s, status) {
    demand(this.store.db.isTransaction === true, 'transaction_required');
    this.store.run('UPDATE auto_queue_sessions SET status=? WHERE id=?', status, s.id);
    this.store.run(`UPDATE manual_dispatch_attempts SET stop_requested=1,updated=?
      WHERE slot=1 AND attempt_id IN (SELECT attempt_id FROM auto_queue_entries WHERE session_id=?)`, this.now(), s.id);
  }
  refresh(s) {
    if (s?.status === 'active' && this.now() >= s.deadline) { this.halt(s, 'expired'); return this.session(s.id); }
    return s;
  }
  status({ sessionId }) {
    return this.store.transaction(() => ({ enabled: this.enabled, executorImplemented: false,
      session: this.refresh(this.session(sessionId)),
      entries: this.store.all('SELECT * FROM auto_queue_entries WHERE session_id=? ORDER BY sequence', sessionId),
      active: this.dispatch.status().active }));
  }
  start({ sessionId, actor, explicitOwnerStart, deadline }) {
    demand(this.enabled, 'auto_disabled');
    demand(text(sessionId) && actor === this.owner && explicitOwnerStart === true, 'explicit_owner_start_required');
    const now = this.now();
    demand(Number.isSafeInteger(deadline) && deadline > now && deadline - now <= MAX_AUTO_SESSION_MS, 'invalid_auto_deadline');
    return this.store.transaction(() => {
      for (const s of this.store.all("SELECT * FROM auto_queue_sessions WHERE status='active'")) this.refresh(s);
      demand(!this.session(sessionId), 'session_id_reused');
      demand(!this.store.get("SELECT id FROM auto_queue_sessions WHERE status='active'"), 'auto_session_active');
      this.store.run("INSERT INTO auto_queue_sessions VALUES (?,?,?,?,'active',?)", sessionId, actor, this.runId, deadline, now);
      return this.session(sessionId);
    });
  }
  // Rejections are returned inside the transaction, then thrown outside, so
  // expiry/quota pause remains durable even when the caller receives an error.
  guarded(sessionId, fn) {
    const result = this.store.transaction(() => {
      demand(this.enabled, 'auto_disabled');
      const s = this.refresh(this.session(sessionId));
      if (!s || s.status !== 'active' || s.run_id !== this.runId) return { error: new Error('auto_session_inactive') };
      try { return { value: fn(s) }; }
      catch (error) {
        if (!['quota_unknown_or_stale', 'quota_too_low'].includes(error.message)) throw error;
        this.halt(s, 'quota_paused');
        return { error };
      }
    });
    if (result.error) throw result.error;
    return result.value;
  }
  eligible(grantId, actor) {
    // Review decisions are deliberately never read. Only the explicit execution
    // grant registry, with its immutable binding, can supply queue candidates.
    const g = this.store.get('SELECT * FROM manual_execution_grants WHERE id=?', grantId);
    return g && g.actor === this.owner && actor === this.owner && JSON.parse(g.binding).repo === this.repository && !this.manualRequested(g) && g.actor === actor && !g.used && !g.revoked && this.now() < g.expires &&
      !this.store.get('SELECT attempt_id FROM manual_dispatch_attempts WHERE grant_id=?', grantId);
  }
  manualRequested(g) {
    return this.store.get("SELECT name FROM sqlite_master WHERE type='table' AND name='execution_requests'") &&
      this.store.get('SELECT id FROM execution_requests WHERE id=? OR plan=?', g.id, g.plan_id);
  }
  enqueue({ sessionId, grantId, actor, explicitAutoAuthorization }) {
    demand(explicitAutoAuthorization === true, 'explicit_auto_authorization_required');
    demand(text(grantId), 'invalid_grant');
    return this.guarded(sessionId, s => {
      demand(s.actor === actor, 'owner_mismatch');
      const old = this.store.get('SELECT * FROM auto_queue_entries WHERE grant_id=?', grantId);
      if (old) { demand(old.session_id === sessionId, 'grant_already_queued'); return old; }
      demand(this.eligible(grantId, s.actor), 'invalid_execution_grant');
      this.store.run("INSERT INTO auto_queue_entries(session_id,grant_id,actor,authorization,status,created) VALUES (?,?,?,'explicit_auto_queue','queued',?)", sessionId, grantId, actor, this.now());
      return this.store.get('SELECT * FROM auto_queue_entries WHERE grant_id=?', grantId);
    });
  }
  claim({ sessionId, controllerRunId, attemptId, quota }) {
    demand(text(controllerRunId) && text(attemptId), 'invalid_attempt');
    return this.guarded(sessionId, s => {
      demand(!this.dispatch.attempt(attemptId), 'attempt_id_reused');
      this.dispatch.quota(quota);
      demand(!this.dispatch.status().active, 'pipeline_busy');
      for (const entry of this.store.all("SELECT * FROM auto_queue_entries WHERE session_id=? AND status='queued' ORDER BY sequence", sessionId)) {
        if (!this.eligible(entry.grant_id, s.actor)) {
          this.store.run("UPDATE auto_queue_entries SET status='ineligible' WHERE sequence=?", entry.sequence);
          continue;
        }
        this.store.run("UPDATE auto_queue_entries SET status='claimed',attempt_id=? WHERE sequence=?", attemptId, entry.sequence);
        const attempt = this.dispatch.executeInTransaction('reserve', { grantId: entry.grant_id, controllerRunId, attemptId });
        return { entry: this.store.get('SELECT * FROM auto_queue_entries WHERE sequence=?', entry.sequence), attempt };
      }
      return null;
    });
  }
  /** Trusted future adapter must use this gate for every auto start/checkpoint,
   * never call ManualDispatch directly for an auto-owned attempt. */
  executionGate({ sessionId, method, ...args }) {
    demand(['startDispatch', 'checkpoint'].includes(method), 'invalid_auto_method');
    return this.guarded(sessionId, () => {
      demand(this.store.get("SELECT sequence FROM auto_queue_entries WHERE session_id=? AND attempt_id=? AND status='claimed'", sessionId, args.attemptId), 'attempt_not_in_session');
      return this.dispatch.executeInTransaction(method, args);
    });
  }
  stop({ sessionId, actor }) {
    return this.store.transaction(() => {
      const s = this.session(sessionId);
      demand(s && s.actor === actor, 'owner_mismatch');
      this.halt(s, 'stopped');
      return this.session(sessionId);
    });
  }
}
