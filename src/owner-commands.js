/** Only invoked by the bridge's authenticated owner-private ingress transaction.
 * These commands never execute work or verify that an external worker stopped. */
export function initializeOwnerCommands(store) {
  store.db.exec(`CREATE TABLE IF NOT EXISTS owner_execution_commands (
    source TEXT PRIMARY KEY, command TEXT NOT NULL, request TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS owner_execution_stops (
    grant_id TEXT PRIMARY KEY, source TEXT NOT NULL);`);
}
const attemptLabels = { reserved: '已占用执行槽，尚未派发', dispatch_started: '派发已登记，工作任务待确认', running: '任务已登记运行', unknown: '任务状态未知，待核实', stopped: '已确认停止', completed: '已确认完成' };
const hasDispatch = store => !!store.get("SELECT name FROM sqlite_master WHERE type='table' AND name='manual_execution_grants'");
function state(store, p) {
  const grant = hasDispatch(store) ? store.get('SELECT * FROM manual_execution_grants WHERE plan_id=?', p.id) : null;
  return { grant, attempt: grant ? store.get('SELECT * FROM manual_dispatch_attempts WHERE grant_id=?', grant.id) : null };
}
function describe(store, p, now) {
  const { grant: g, attempt: a } = state(store, p);
  const request = store.get('SELECT status FROM execution_requests WHERE plan=?', p.id);
  const authorization = !g ? (request?.status === 'not_ready' ? '执行器未就绪，未授权' : '未授权执行') : g.revoked ? '执行授权已撤销' : now >= g.expires ? '执行授权已过期' : g.used ? '执行授权已使用' : '已授权，尚未派发';
  const execution = a ? `${attemptLabels[a.status] ?? '任务状态待核实'}${a.slot === 1 ? '；执行槽仍占用' : ''}${a.stop_requested && a.slot === 1 ? '；已请求停止，尚未确认任务停止' : ''}` : '尚无派发记录';
  const result = a && store.get("SELECT name FROM sqlite_master WHERE type='table' AND name='execution_results'") ? store.get('SELECT * FROM execution_results WHERE attempt_id=? AND plan_id=? AND grant_id=? AND owner=? AND channel=? AND root=? AND request=? AND controller_run_id=?', a.attempt_id, p.id, g.id, p.owner, p.channel, p.root, p.request, a.controller_run_id) : null;
  const summary = result ? String(JSON.parse(result.report).summary).replace(/[<>&`*_\[\]()#]/g, ' ').replace(/[\r\n]/g, ' ').slice(0, 500) : '';
  return `方案 v${p.version}：${now >= p.expires ? '已过期；' : ''}${authorization}；${execution}。${summary ? `\n处理结果：${summary}` : ''}`;
}
export function receiveOwnerCommand(bridge, text, source, now, repository) {
  if (!/^(?:查询|停止)(?:\s|$)/.test(text)) return null;
  const match = /^(查询|停止) #([1-9][0-9]*)$/.exec(text);
  if (!match || !repository || !Number.isSafeInteger(Number(match[2]))) return null;
  const { store, config: c } = bridge;
  if (store.db.isTransaction !== true) throw new Error('transaction_required');
  const old = store.get('SELECT * FROM owner_execution_commands WHERE source=?', source);
  if (old) return false;
  const plans = store.all('SELECT * FROM execution_plans WHERE repo=? AND issue_number=? AND owner=? AND channel=? ORDER BY version DESC', repository, Number(match[2]), c.ownerUser, c.slackChannel);
  if (!plans.length) return null;
  if (new Set(plans.map(p => p.issue_id)).size !== 1) return false;
  // The original immutable plan root is the only permitted event destination.
  if (plans.some(p => {
    const r = store.get('SELECT * FROM requests WHERE id=?', p.request);
    return !r || r.root !== p.root || r.owner !== p.owner || r.channel !== p.channel || r.kind !== 'execution_plan';
  })) return false;
  const latest = plans[0];
  let reply;
  if (match[1] === '查询') {
    const relevant = plans.filter((p, i) => i === 0 || state(store, p).attempt?.slot === 1);
    reply = `问题 #${latest.issue_number}：\n${relevant.map(p => describe(store, p, now)).join('\n')}`;
  } else {
    let active = false, changed = false;
    for (const p of plans) {
      const { grant: g, attempt: a } = state(store, p);
      if (!g || g.actor !== c.ownerUser || (a && a.slot !== 1) || (!a && (g.used || g.revoked))) continue;
      active ||= a?.slot === 1;
      store.run('UPDATE manual_execution_grants SET revoked=1 WHERE id=?', g.id);
      if (a?.slot === 1) store.run('UPDATE manual_dispatch_attempts SET stop_requested=1,updated=? WHERE attempt_id=?', now, a.attempt_id);
      if (store.get('SELECT grant_id FROM owner_execution_stops WHERE grant_id=?', g.id)) continue;
      changed = true;
      store.run('INSERT INTO owner_execution_stops VALUES (?,?)', g.id, source);
      const result = { plan_id: p.id, grant_id: g.id, repo: p.repo, issue_id: p.issue_id, issue_number: p.issue_number, version: p.version, source, stop_requested: true, worker_stopped: false,
        attempt_id: a?.attempt_id ?? null, controller_run_id: a?.controller_run_id ?? null, thread_id: a?.thread_id ?? null, turn_id: a?.turn_id ?? null };
      const record = JSON.stringify(result).replace(/[<>&]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
      store.addJob(p.request, 'slack', { part: 1, parts: 1, metadata: { event_type: 'hiveflow_execution_stop_requested', event_payload: result }, text: `Execution stop requested\n${record}\n问题 #${p.issue_number} v${p.version}：本人请求停止。已撤销授权；请核实并协作停止绑定任务，再回报终态。` }, `owner-stop:${g.id}`, c.slackChannel, now + c.ttlMs);
    }
    reply = `问题 #${latest.issue_number}：${active ? '已请求停止并撤销执行授权，尚未确认任务停止；执行槽仍保留，待核实终态。' : changed ? '已撤销执行授权，后续派发已阻止；尚无活动任务记录。' : '当前没有待停止的任务。'}`;
  }
  store.run('INSERT INTO owner_execution_commands VALUES (?,?,?)', source, text, latest.request);
  store.addJob(latest.request, 'wecom', { text: reply }, 'owner', c.ownerUser, now + c.ttlMs);
  return latest.request;
}
