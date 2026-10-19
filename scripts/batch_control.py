"""Private, local cooperative batch controller. No scheduler, model or network calls."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from zoneinfo import ZoneInfo
from validate_delivery import strict_json, validate as validate_delivery


class Blocked(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Blocked(reason)


def exact(obj, keys):
    require(type(obj) is dict and set(obj) == set(keys.split()), 'invalid_fields')


def text(value):
    return type(value) is str and 0 < len(value.strip()) <= 2000 and '\x00' not in value


def positive(value):
    return type(value) is int and 0 < value <= 9007199254740991


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def instant(value):
    require(type(value) is str, 'invalid_time')
    try:
        result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        require(result.tzinfo is not None, 'timezone_required')
        return result.timestamp()
    except (ValueError, OverflowError):
        raise Blocked('invalid_time') from None


def tool_fingerprint():
    h=hashlib.sha256()
    for name in ['batch_control.py','validate_delivery.py']:
        data=(Path(__file__).resolve().parent/name).read_bytes()
        h.update(name.encode()+b'\0'+str(len(data)).encode()+b'\0'+data)
    return h.hexdigest()


def validate_plan(p):
    exact(p, 'schemaVersion batchId repository issues startsAt endsAt timezone maxIssues mode enabled scope specCommit controllerSha256')
    require(type(p['specCommit']) is str and re.fullmatch(r'[0-9a-f]{40}',p['specCommit']), 'fixed_spec_commit_required')
    require(type(p['controllerSha256']) is str and re.fullmatch(r'[0-9a-f]{64}',p['controllerSha256']), 'fixed_tool_digest_required')
    require(type(p['schemaVersion']) is int and p['schemaVersion'] == 1, 'schema')
    require(type(p['batchId']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', p['batchId']), 'batch_id')
    require(type(p['repository']) is str and re.fullmatch(r'[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*', p['repository']), 'repository')
    require(type(p['issues']) is list and 0 < len(p['issues']) <= 100 and all(positive(n) for n in p['issues']) and len(set(p['issues'])) == len(p['issues']), 'fixed_issue_list')
    require(type(p['maxIssues']) is int and 0 < p['maxIssues'] <= len(p['issues']), 'quantity')
    require(p['mode'] == 'pr' and p['enabled'] is False, 'pr_only_disabled_plan_required')
    begin, end = instant(p['startsAt']), instant(p['endsAt'])
    require(0 < end - begin <= 31 * 86400, 'finite_window_max_31_days')
    require(type(p['timezone']) is str, 'timezone_required')
    try:
        zone=ZoneInfo(p['timezone'])
        for key in ['startsAt','endsAt']:
            when=dt.datetime.fromisoformat(p[key].replace('Z','+00:00'))
            require(when.utcoffset()==when.astimezone(zone).utcoffset(), 'time_offset_timezone_mismatch')
    except (ValueError, KeyError):
        raise Blocked('invalid_timezone') from None
    s = p['scope']
    exact(s, 'goal allowedPaths acceptance tests riskBoundaries writeIssueRecord merge deploy closeIssues updateLedger rollback')
    require(text(s['goal']), 'scope_goal')
    for field in ['allowedPaths', 'acceptance', 'tests', 'riskBoundaries']:
        require(type(s[field]) is list and 0 < len(s[field]) <= 100 and all(text(v) for v in s[field]), 'scope_details')
    for path in s['allowedPaths']:
        parts = path.split('/')
        require(not path.startswith('/') and not any(x in ('', '.', '..', '.git') for x in parts) and '\\' not in path and ':' not in path, 'relative_scope_paths')
    require(type(s['writeIssueRecord']) is bool and all(s[x] is False for x in ['merge', 'deploy', 'closeIssues', 'updateLedger']) and s['rollback'] == 'stop_and_report', 'unsupported_delivery_scope')
    return p


class Controller:
    def __init__(self, root, now=None):
        self.root = Path(root).absolute()
        require(self.root.is_dir() and not self.root.is_symlink(), 'missing_or_symlink_state')
        st = self.root.stat()
        require(st.st_uid == os.getuid() and st.st_mode & 0o077 == 0, 'state_must_be_private')
        for name in ['control.sqlite', 'session.lock']:
            p = self.root / name
            require(p.is_file() and not p.is_symlink() and p.stat().st_uid == os.getuid() and p.stat().st_mode & 0o077 == 0, 'unsafe_state_file')
        # mode=rw never silently creates a replacement for missing/corrupt state.
        self.db = sqlite3.connect((self.root / 'control.sqlite').as_uri() + '?mode=rw', uri=True, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA synchronous=FULL')
        require(self.db.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'corrupt_state')
        require(self.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0] == '1', 'state_schema')
        self._session_batch = None
        self._session_digest = None
        self.now = now or (lambda: dt.datetime.now(dt.timezone.utc).timestamp())

    @staticmethod
    def initialize(root):
        root = Path(root).absolute()
        require(not root.exists() and not root.is_symlink() and not root.parent.is_symlink(), 'new_state_directory_required')
        root.mkdir(mode=0o700)
        lock = root / 'session.lock'; lock.touch(mode=0o600)
        db = sqlite3.connect(root / 'control.sqlite')
        os.chmod(root / 'control.sqlite', 0o600)
        db.executescript('''PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL;
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          INSERT INTO meta VALUES ('schema','1'),('halt',''),('last_clock','0');
          CREATE TABLE batches(id TEXT PRIMARY KEY,plan TEXT NOT NULL,digest TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0,stop_current INTEGER NOT NULL DEFAULT 0,approval_ref TEXT,approved_digest TEXT);
          CREATE TABLE items(batch TEXT NOT NULL,issue INTEGER NOT NULL,position INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'pending',run TEXT UNIQUE,reason TEXT,evidence_ref TEXT,record TEXT,PRIMARY KEY(batch,issue));
          CREATE TABLE runs(id TEXT PRIMARY KEY,batch TEXT NOT NULL,issue INTEGER NOT NULL,task TEXT NOT NULL,delivery TEXT NOT NULL UNIQUE,phase TEXT NOT NULL,status TEXT NOT NULL);
          CREATE TABLE operations(id TEXT PRIMARY KEY,digest TEXT NOT NULL,response TEXT NOT NULL);
        ''')
        db.close()

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def tx(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    @contextlib.contextmanager
    def lease(self):
        # Held for the entire native-host execution session, across every Issue/phase.
        fd = os.open(self.root / 'session.lock', os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Blocked('another_execution_session_owns_controller') from None
            yield
        finally:
            os.close(fd)  # Never unlink locks or steal them based on TTL/PID.

    def plan(self, batch):
        row = self.db.execute('SELECT * FROM batches WHERE id=?', (batch,)).fetchone()
        require(row is not None, 'unknown_batch')
        p = validate_plan(strict_json(row['plan']))
        require(digest(p) == row['digest'], 'plan_digest_mismatch')
        require(p['controllerSha256'] == tool_fingerprint(), 'controller_version_changed')
        return row, p

    def halt(self, reason):
        self.db.execute("UPDATE meta SET value=? WHERE key='halt'", (reason,))
        self.db.execute('UPDATE batches SET enabled=0,stop_current=1')

    def guard(self, batch, new=False):
        row, p = self.plan(batch)
        if row['approved_digest'] is None:
            return 'not_enabled_or_approval_missing'
        if row['approved_digest'] != row['digest']:
            self.halt('approval_snapshot_mismatch')
            return 'approval_snapshot_mismatch'
        now = self.now()
        last = float(self.db.execute("SELECT value FROM meta WHERE key='last_clock'").fetchone()[0])
        if now < last - 1:
            self.halt('clock_regressed')
        self.db.execute("UPDATE meta SET value=? WHERE key='last_clock'", (str(max(now, last)),))
        if now >= instant(p['endsAt']):
            self.halt('expired')
        reason = self.db.execute("SELECT value FROM meta WHERE key='halt'").fetchone()[0]
        if reason:
            return reason
        if now < instant(p['startsAt']):
            return 'not_started'
        if row['stop_current']:
            return 'stop_current'
        if new and not row['enabled']:
            return 'not_enabled_or_stop_new'
        return None

    def add(self, p):
        validate_plan(p)
        require(p['controllerSha256'] == tool_fingerprint(), 'controller_version_mismatch')
        with self.lease(), self.tx():
            old = self.db.execute('SELECT digest FROM batches WHERE id=?', (p['batchId'],)).fetchone()
            if old:
                require(old[0] == digest(p), 'immutable_batch_conflict')
                return {'batchId':p['batchId'], 'planDigest':old[0], 'created':False}
            self.db.execute('INSERT INTO batches(id,plan,digest) VALUES (?,?,?)', (p['batchId'], encoded(p), digest(p)))
            self.db.executemany('INSERT INTO items(batch,issue,position) VALUES (?,?,?)', [(p['batchId'], n, i) for i,n in enumerate(p['issues'])])
            return {'batchId':p['batchId'], 'planDigest':digest(p), 'created':True, 'enabled':False}

    def enable(self, batch, plan_digest, approval_ref, verified_idle):
        # This explicit operator action is NOT identity authentication. Trusted host must verify it.
        require(text(approval_ref) and verified_idle is True, 'trusted_approval_and_idle_verification_required')
        with self.lease(), self.tx():
            row, p = self.plan(batch)
            require(row['digest'] == plan_digest, 'approval_plan_digest_mismatch')
            require(self.now() < instant(p['endsAt']), 'outside_approved_window')
            require(not self.db.execute("SELECT 1 FROM items WHERE status IN ('running','unknown')").fetchone(), 'reconciliation_required')
            require(not self.db.execute('SELECT 1 FROM batches WHERE enabled=1 AND id!=?', (batch,)).fetchone(), 'another_batch_enabled')
            require(self.now() >= float(self.db.execute("SELECT value FROM meta WHERE key='last_clock'").fetchone()[0]) - 1, 'clock_regressed')
            self.db.execute("UPDATE meta SET value='' WHERE key='halt'")
            self.db.execute('UPDATE batches SET enabled=1,stop_current=0,approval_ref=?,approved_digest=? WHERE id=?', (approval_ref,row['digest'],batch))
            return {'enabled':True, 'planDigest':row['digest']}

    def stop(self, batch, current=False):
        with self.tx():
            require(self.db.execute('SELECT 1 FROM batches WHERE id=?',(batch,)).fetchone(), 'unknown_batch')
            self.db.execute('UPDATE batches SET enabled=0,stop_current=max(stop_current,?) WHERE id=?', (int(current),batch))
        actual=bool(self.db.execute('SELECT stop_current FROM batches WHERE id=?',(batch,)).fetchone()[0])
        return {'stopNew':True, 'stopCurrent':actual, 'inFlightEffectsWithdrawn':False}

    def status(self, batch):
        row, _ = self.plan(batch)
        return {'batchId':batch, 'planDigest':row['digest'], 'enabled':bool(row['enabled']), 'stopCurrent':bool(row['stop_current']),
                'globalHalt':self.db.execute("SELECT value FROM meta WHERE key='halt'").fetchone()[0],
                'items':[dict(r) for r in self.db.execute('SELECT issue,status,run,reason,evidence_ref FROM items WHERE batch=? ORDER BY position',(batch,))]}

    @contextlib.contextmanager
    def session(self, batch):
        with self.lease():
            with self.tx():
                self._session_digest = self.plan(batch)[0]['digest']
                if self.db.execute("SELECT 1 FROM items WHERE status='running'").fetchone():
                    self.db.execute("UPDATE items SET status='unknown',reason='previous_session_lost' WHERE status='running'")
                    self.db.execute("UPDATE runs SET status='unknown' WHERE status='running'")
                    self.halt('reconciliation_required')
            self._session_batch = batch
            try:
                yield lambda request:self.command(batch, request)
            finally:
                self._session_batch = None
                self._session_digest = None
                with self.tx():
                    if self.db.execute("SELECT 1 FROM items WHERE batch=? AND status='running'",(batch,)).fetchone():
                        self.db.execute("UPDATE items SET status='unknown',reason='session_closed_without_terminal_evidence' WHERE batch=? AND status='running'",(batch,))
                        self.db.execute("UPDATE runs SET status='unknown' WHERE batch=? AND status='running'",(batch,))
                        self.halt('reconciliation_required')

    def command(self, batch, req):
        require(self._session_batch == batch, 'execution_session_required')
        require(type(req) is dict and req.get('action') in ['next','claim','checkpoint','finish','halt'], 'invalid_action')
        extra = {'next':'','claim':'preflight','checkpoint':'runId taskId phase evidenceRef','finish':'runId taskId outcome evidenceRef record','halt':'reason'}[req['action']]
        exact(req, 'action operationId ' + extra)
        op = req.get('operationId')
        require(type(op) is str and re.fullmatch(r'[A-Za-z0-9._:-]{1,128}',op), 'stable_operation_id_required')
        fingerprint = digest({'batch':batch,'request':req})
        with self.tx():
            prior = self.db.execute('SELECT * FROM operations WHERE id=?',(op,)).fetchone()
            if prior:
                require(prior['digest'] == fingerprint, 'operation_id_conflict')
                result = strict_json(prior['response'])
                if req['action'] in ['next','claim','checkpoint'] and result.get('allowed'):
                    reason = self.guard(batch,new=req['action'] in ['next','claim'])
                    rid = req.get('runId') or result.get('runId')
                    if rid and not self.db.execute("SELECT 1 FROM runs WHERE id=? AND status='running'",(rid,)).fetchone():
                        reason = reason or 'run_terminal'
                    if reason:return {'allowed':False,'reason':reason,'historicalOperation':True}
                return result
            result = self._command(batch, req)
            self.db.execute('INSERT INTO operations VALUES (?,?,?)',(op,fingerprint,encoded(result)))
            return result

    def _command(self, batch, req):
        action=req['action'];row,p=self.plan(batch)
        require(row['digest'] == self._session_digest, 'session_plan_changed')
        if action=='halt':
            require(req.get('reason') in ['identity','permission','environment','occupancy_unknown','suspected_leak','unknown_result','platform_error'], 'invalid_halt_reason')
            self.halt(req['reason']);return {'halted':True}
        if action in ['next','claim']:
            reason=self.guard(batch,new=True)
            if reason:return {'allowed':False,'reason':reason}
            if self.db.execute("SELECT 1 FROM items WHERE status IN ('running','unknown')").fetchone():
                return {'allowed':False,'reason':'active_or_unknown_execution'}
            count=self.db.execute("SELECT count(*) FROM items WHERE batch=? AND status!='pending'",(batch,)).fetchone()[0]
            if count>=p['maxIssues']:return {'allowed':False,'reason':'quantity_reached'}
            item=self.db.execute("SELECT * FROM items WHERE batch=? AND status='pending' ORDER BY position LIMIT 1",(batch,)).fetchone()
            if not item:return {'allowed':False,'reason':'exhausted'}
            if action=='next':return {'allowed':True,'repository':p['repository'],'issue':item['issue'],'planDigest':row['digest']}
            f=req.get('preflight')
            exact(f,'repository issue taskId adopted acceptanceReady occupancy identityVerified permissionsVerified environmentVerified leakSuspected evidenceRef')
            require(f['repository']==p['repository'] and type(f['issue']) is int and f['issue']==item['issue'] and text(f['taskId']) and text(f['evidenceRef']), 'preflight_target_or_evidence')
            require(all(type(f[k]) is bool for k in ['adopted','acceptanceReady','identityVerified','permissionsVerified','environmentVerified','leakSuspected']) and f['occupancy'] in ['clear','busy','unknown'], 'preflight_types')
            if not all(f[k] for k in ['identityVerified','permissionsVerified','environmentVerified']) or f['occupancy']=='unknown' or f['leakSuspected']:
                self.halt('unsafe_preflight');return {'allowed':False,'reason':'unsafe_preflight'}
            if not f['adopted'] or not f['acceptanceReady'] or f['occupancy']=='busy':
                self.db.execute("UPDATE items SET status='paused',reason='not_eligible',evidence_ref=? WHERE batch=? AND issue=?",(f['evidenceRef'],batch,item['issue']))
                return {'allowed':False,'reason':'item_paused'}
            rid=str(uuid.uuid4());delivery='hf:'+batch+':'+str(item['issue'])+':'+rid[:12]
            self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,'running')",(rid,batch,item['issue'],f['taskId'],delivery,'claimed'))
            self.db.execute("UPDATE items SET status='running',run=?,evidence_ref=? WHERE batch=? AND issue=?",(rid,f['evidenceRef'],batch,item['issue']))
            return {'allowed':True,'runId':rid,'taskId':f['taskId'],'deliveryId':delivery,'planDigest':row['digest'],'scope':p['scope']}
        run=self.db.execute('SELECT * FROM runs WHERE id=? AND batch=?',(req.get('runId'),batch)).fetchone()
        require(run is not None and run['task']==req.get('taskId') and run['status']=='running', 'run_identity_or_terminal_state')
        if action=='checkpoint':
            phase=req.get('phase')
            require(phase in ['modify','test','commit','push','pr','record'], 'unsupported_phase')
            reason=self.guard(batch)
            if reason:return {'allowed':False,'reason':reason,'cooperativeStopRequired':True}
            require(phase!='record' or p['scope']['writeIssueRecord'], 'writeback_not_authorized')
            # Host must supply actual test/commit/PR evidence. A bool is never proof of it.
            if phase in ['commit','push','pr','record']:
                require(text(req.get('evidenceRef')), 'phase_evidence_required')
            self.db.execute('UPDATE runs SET phase=? WHERE id=?',(phase,run['id']))
            return {'allowed':True,'phase':phase,'planDigest':row['digest']}
        return self.terminal(run, req)

    def terminal(self, run, req):
        outcome=req.get('outcome')
        require(outcome in ['pr_ready','blocked','test_failed','stopped','unknown'] and text(req.get('evidenceRef')), 'terminal_evidence_required')
        record=req.get('record')
        if outcome=='pr_ready':
            validate_delivery(record)
            _,p=self.plan(run['batch'])
            require(record['repository']==p['repository'] and record['issue']==run['issue'] and record['deliveryId']==run['delivery'] and positive(record['pullRequest']) and record['deployment']['status']=='not_required', 'delivery_record_mismatch_or_deployment')
        else:
            require(record is None, 'unexpected_terminal_record')
        self.db.execute('UPDATE items SET status=?,reason=?,evidence_ref=?,record=? WHERE run=?',(outcome,outcome,req['evidenceRef'],encoded(record) if record else None,run['id']))
        self.db.execute('UPDATE runs SET status=? WHERE id=?',(outcome,run['id']))
        if outcome=='unknown':self.halt('unknown_result')
        return {'recorded':True,'outcome':outcome,'runId':run['id'],'deliveryId':run['delivery'],'externalNotificationSent':False}

    def reconcile(self, run_id, task_id, evidence_ref, verified_idle, outcome, record=None):
        require(verified_idle is True and text(evidence_ref), 'actual_host_idle_and_reconciliation_evidence_required')
        with self.lease(), self.tx():
            run=self.db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
            require(run is not None and run['task']==task_id and run['status'] in ['running','unknown'], 'reconcile_original_task_required')
            require(outcome in ['pr_ready','blocked','test_failed','stopped'], 'no_automatic_retry_or_unknown_release')
            result=self.terminal(run,dict(outcome=outcome,evidenceRef=evidence_ref,record=record))
            self.db.execute('UPDATE batches SET enabled=0,stop_current=1')
            return dict(result,enabled=False,reEnableNeedsCurrentAuthorization=True)


def read_json(path):
    with open(path,encoding='utf-8') as f:payload=f.read(131073)
    require(len(payload.encode())<=131072,'input_too_large')
    return strict_json(payload)


def main():
    if sys.argv[1:] == ['fingerprint']:
        print(encoded({'controllerSha256':tool_fingerprint()}));return 0
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    sub.add_parser('init')
    add=sub.add_parser('add');add.add_argument('plan')
    for name in ['status','stop-new','stop-current','session']:
        cmd=sub.add_parser(name);cmd.add_argument('batch')
    enable=sub.add_parser('enable');enable.add_argument('batch');enable.add_argument('--plan-digest',required=True);enable.add_argument('--approval-ref',required=True);enable.add_argument('--verified-idle',action='store_true')
    recover=sub.add_parser('reconcile');recover.add_argument('evidence')
    a=parser.parse_args();c=None
    try:
        if a.action=='init':Controller.initialize(a.state);result={'initialized':True,'enabled':False}
        else:
            c=Controller(a.state)
            if a.action=='add':result=c.add(read_json(a.plan))
            elif a.action=='enable':result=c.enable(a.batch,a.plan_digest,a.approval_ref,a.verified_idle)
            elif a.action=='status':result=c.status(a.batch)
            elif a.action in ['stop-new','stop-current']:result=c.stop(a.batch,a.action=='stop-current')
            elif a.action=='reconcile':
                p=read_json(a.evidence);exact(p,'runId taskId evidenceRef verifiedIdle outcome record')
                result=c.reconcile(p['runId'],p['taskId'],p['evidenceRef'],p['verifiedIdle'],p['outcome'],p['record'])
            else:
                with c.session(a.batch) as command:
                    print(encoded({'session':'ready','cooperativeOnly':True}),flush=True)
                    for line in iter(lambda:sys.stdin.readline(131073), ''):
                        try:
                            require(len(line.encode())<=131072,'input_too_large')
                            result=command(strict_json(line));print(encoded(result),flush=True)
                        except (Blocked,ValueError,TypeError,KeyError):
                            with c.tx():c.halt('invalid_session_input')
                            print(encoded({'allowed':False,'reason':'invalid_session_input','globalStop':True}),flush=True)
                return 0
        print(encoded(result));return 0
    except (Blocked,ValueError,TypeError,KeyError,OSError,sqlite3.Error):
        print(encoded({'allowed':False,'reason':'controller_blocked_verify_state_authorization_and_evidence'}),file=sys.stderr);return 1
    finally:
        if c:c.close()


if __name__=='__main__':
    sys.exit(main())
