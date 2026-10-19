"""Synthetic host, GitHub facts and private temp state; never invokes models or network."""
import copy
import datetime as dt
import json
import os
import select
import sqlite3
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from batch_control import Blocked, Controller, digest, validate_plan, tool_fingerprint

ROOT=Path(__file__).resolve().parents[1]
PLAN={
 'specCommit':'b'*40,'controllerSha256':tool_fingerprint(),
 'schemaVersion':1,'batchId':'synthetic-batch','repository':'example/project','issues':[42,43,44],
 'startsAt':'2030-01-01T08:00:00+08:00','endsAt':'2030-01-02T08:00:00+08:00','timezone':'Asia/Shanghai',
 'maxIssues':3,'mode':'pr','enabled':False,
 'scope':{'goal':'Synthetic bounded change','allowedPaths':['src','test'],'acceptance':['Observable synthetic behavior'],
          'tests':['synthetic unit checks'],'riskBoundaries':['No environment or permissions changes'],
          'writeIssueRecord':True,'merge':False,'deploy':False,'closeIssues':False,'updateLedger':False,'rollback':'stop_and_report'}}


def preflight(issue=42,**extra):
 return dict(repository='example/project',issue=issue,taskId='synthetic-existing-task',adopted=True,acceptanceReady=True,
             occupancy='clear',identityVerified=True,permissionsVerified=True,environmentVerified=True,leakSuspected=False,
             evidenceRef='synthetic-host-preflight',**extra)


def record(claim,commit='a'*40,issue=42):
 return dict(schemaVersion=1,deliveryId=claim['deliveryId'],repository='example/project',issue=issue,pullRequest=7,commit=commit,
             checks=[dict(name='unit',status='passed',evidenceUrl='https://github.com/example/project/actions/runs/1')],
             deployment=dict(status='not_required',commit=commit,evidenceUrl='https://github.com/example/project/pull/7',scope='PR delivery only'),
             acceptance=dict(status='passed',evidenceUrl='https://github.com/example/project/pull/7'),publicSummary='Synthetic PR ready; no deployment.')


class BatchTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'private-control';Controller.initialize(self.root)
  self.clock=[dt.datetime(2030,1,1,1,tzinfo=dt.timezone.utc).timestamp()]
  self.c=Controller(self.root,now=lambda:self.clock[0]);self.addCleanup(lambda:self.c.close())
  self.p=copy.deepcopy(PLAN);self.c.add(self.p);self.sequence=0

 def enable(self,batch=None,plan=None):
  return self.c.enable(batch or self.p['batchId'],digest(plan or self.p),'synthetic-trusted-owner-approval',True)

 def cmd(self,send,action,**values):
  self.sequence+=1
  return send(dict(action=action,operationId='synthetic-op-'+str(self.sequence),**values))

 def claim(self,send,issue=42):
  return self.cmd(send,'claim',preflight=preflight(issue))

 def finish(self,send,claim,outcome='pr_ready',issue=42,**extra):
  return self.cmd(send,'finish',runId=claim['runId'],taskId=claim['taskId'],outcome=outcome,
                  evidenceRef='synthetic-observed-result',record=record(claim,issue=issue) if outcome=='pr_ready' else None,**extra)

 def test_default_disabled_and_immutable_scope(self):
  with self.c.session(self.p['batchId']) as send:
   self.assertFalse(self.cmd(send,'next')['allowed'])
  self.assertFalse(self.c.status(self.p['batchId'])['enabled'])
  altered=copy.deepcopy(self.p);altered['issues']=[55]
  with self.assertRaises(Blocked):self.c.add(altered)
  with self.assertRaises(Blocked):self.c.enable(self.p['batchId'],'wrong','approval',True)
  with self.assertRaises(Blocked):self.c.enable(self.p['batchId'],digest(self.p),'approval',False)

 def test_strict_plan_and_no_side_effect_scope(self):
  mutations=[('enabled',True),('maxIssues',True),('issues',[42,42]),('issues',[True]),('mode','deploy'),
             ('endsAt','2030-01-01T00:00:00Z'),('startsAt','2030-01-01T00:00:00'),('timezone','unknown/zone')]
  for field,value in mutations:
   altered=copy.deepcopy(self.p);altered[field]=value
   with self.subTest(field=field),self.assertRaises((Blocked,ValueError)):validate_plan(altered)
  for field in ['merge','deploy','closeIssues','updateLedger']:
   altered=copy.deepcopy(self.p);altered['scope'][field]=True
   with self.subTest(field=field),self.assertRaises(Blocked):validate_plan(altered)
  for path in ['../outside','/etc','src/../private','.git','src\\private','src//other']:
   altered=copy.deepcopy(self.p);altered['scope']['allowedPaths']=[path]
   with self.subTest(path=path),self.assertRaises(Blocked):validate_plan(altered)

 def test_serial_completed_delivery_replay_and_quantity_survive_reopen(self):
  self.p['maxIssues']=1
  # A separately authorized immutable batch; old disabled batch remains harmless.
  self.p['batchId']='limit-one';self.c.add(self.p);self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   self.assertFalse(self.cmd(send,'claim',preflight=preflight(43))['allowed'])
   request=dict(action='finish',operationId='stable-completion',runId=claim['runId'],taskId=claim['taskId'],outcome='pr_ready',evidenceRef='actual-mock-result',record=record(claim))
   first=send(request);self.assertEqual(send(request),first)
   self.assertFalse(first['externalNotificationSent'])
   self.assertEqual(self.cmd(send,'next')['reason'],'quantity_reached')
  reopened=Controller(self.root,now=lambda:self.clock[0])
  try:
   with reopened.session(self.p['batchId']) as send:self.assertEqual(self.cmd(send,'next')['reason'],'quantity_reached')
  finally:reopened.close()

 def test_ineligible_or_failed_single_item_never_hourly_retries(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   f=preflight();f['acceptanceReady']=False
   self.assertEqual(self.cmd(send,'claim',preflight=f)['reason'],'item_paused')
   claim=self.claim(send,43);self.finish(send,claim,'test_failed')
   self.assertEqual(self.cmd(send,'next')['issue'],44)
   claim=self.claim(send,44);self.finish(send,claim,'blocked')
   self.assertEqual(self.cmd(send,'next')['reason'],'quantity_reached')
  with self.c.session(self.p['batchId']) as send:self.assertFalse(self.cmd(send,'next')['allowed'])

 def test_unknown_permissions_environment_occupancy_or_leak_global_stop(self):
  for field,value in [('identityVerified',False),('permissionsVerified',False),('environmentVerified',False),('occupancy','unknown'),('leakSuspected',True)]:
   self.enable()
   with self.c.session(self.p['batchId']) as send:
    f=preflight();f[field]=value
    self.assertEqual(self.cmd(send,'claim',preflight=f)['reason'],'unsafe_preflight')
    self.assertFalse(self.cmd(send,'next')['allowed'])
   self.assertFalse(self.c.status(self.p['batchId'])['enabled'])

 def test_stop_new_allows_current_but_stop_current_denies_future_checkpoint(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send);self.c.stop(self.p['batchId'])
   checkpoint=dict(runId=claim['runId'],taskId=claim['taskId'],phase='modify',evidenceRef=None)
   self.assertTrue(self.cmd(send,'checkpoint',**checkpoint)['allowed'])
   self.c.stop(self.p['batchId'],True)
   self.assertFalse(self.cmd(send,'checkpoint',**checkpoint)['allowed'])
   self.finish(send,claim,'stopped');self.assertFalse(self.cmd(send,'next')['allowed'])

 def test_expiry_and_cached_authorization_never_allow_new_effects(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   checkpoint=dict(action='checkpoint',operationId='cached-permit',runId=claim['runId'],taskId=claim['taskId'],phase='test',evidenceRef=None)
   self.assertTrue(send(checkpoint)['allowed'])
   self.clock[0]+=2*86400
   self.assertFalse(send(checkpoint)['allowed'])
   self.assertEqual(self.c.status(self.p['batchId'])['globalHalt'],'expired')
   self.finish(send,claim,'stopped')

 def test_lost_session_recovery_keeps_original_run_and_delivery(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:claim=self.claim(send)
  self.assertEqual(self.c.status(self.p['batchId'])['items'][0]['status'],'unknown')
  with self.c.session(self.p['batchId']) as send:self.assertFalse(self.cmd(send,'next')['allowed'])
  with self.assertRaises(Blocked):self.c.enable(self.p['batchId'],digest(self.p),'new approval',True)
  with self.assertRaises(Blocked):self.c.reconcile(claim['runId'],claim['taskId'],'evidence',False,'stopped')
  with self.assertRaises(Blocked):self.c.reconcile(claim['runId'],'different-task','evidence',True,'stopped')
  result=self.c.reconcile(claim['runId'],claim['taskId'],'observed host idle and existing PR',True,'pr_ready',record(claim))
  self.assertEqual(result['deliveryId'],claim['deliveryId']);self.assertFalse(result['enabled'])
  self.enable()
  with self.c.session(self.p['batchId']) as send:self.assertEqual(self.cmd(send,'next')['issue'],43)

 def test_run_target_record_cannot_expand_or_claim_product_deployment(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   for field,value in [('repository','other/project'),('issue',43),('deliveryId','another-delivery')]:
    r=record(claim);r[field]=value
    with self.subTest(field=field),self.assertRaises(Blocked):
     self.cmd(send,'finish',runId=claim['runId'],taskId=claim['taskId'],outcome='pr_ready',evidenceRef='mock',record=r)
   r=record(claim);r['deployment']['status']='passed'
   with self.assertRaises(Blocked):self.cmd(send,'finish',runId=claim['runId'],taskId=claim['taskId'],outcome='pr_ready',evidenceRef='mock',record=r)
   for phase in ['merge','deploy','close','ledger']:
    with self.assertRaises(Blocked):self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase=phase,evidenceRef='mock')
   self.finish(send,claim,'stopped')

 def test_lock_covers_other_batches_for_entire_session(self):
  other=copy.deepcopy(self.p);other['batchId']='second-batch';self.c.add(other)
  self.enable();second=Controller(self.root,now=lambda:self.clock[0])
  try:
   with self.c.session(self.p['batchId']) as send:
    claim=self.claim(send)
    with self.assertRaises(Blocked):
     with second.session(other['batchId']):pass
    with self.assertRaises(Blocked):second.enable(other['batchId'],digest(other),'approval',True)
    self.finish(send,claim,'stopped')
  finally:second.close()

 def test_changed_plan_snapshot_and_corruption_fail_closed(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   altered=copy.deepcopy(self.p);altered['issues']=[90]
   self.c.db.execute('UPDATE batches SET plan=?,digest=? WHERE id=?',(json.dumps(altered),digest(altered),self.p['batchId']))
   with self.assertRaises(Blocked):self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='modify',evidenceRef=None)
  bad=Path(self.tmp.name)/'corrupt';Controller.initialize(bad);(bad/'control.sqlite').write_bytes(b'broken database')
  with self.assertRaises(Exception):Controller(bad)

 def test_unknown_external_result_blocks_all_following_items_and_replay(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send);self.finish(send,claim,'unknown')
   self.assertFalse(self.cmd(send,'next')['allowed'])
  with self.c.session(self.p['batchId']) as send:self.assertFalse(self.cmd(send,'next')['allowed'])
  self.assertEqual(self.c.db.execute('SELECT count(*) FROM runs').fetchone()[0],1)

 def test_clock_rollback_halts(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   self.cmd(send,'next');self.clock[0]-=100
   self.assertEqual(self.cmd(send,'next')['reason'],'clock_regressed')

 def test_mock_host_with_real_temporary_git_remote(self):
  # Local-only commit/push demonstrates a PR boundary record, never creates a real PR.
  area=Path(self.tmp.name)/'checkout';remote=Path(self.tmp.name)/'remote.git';area.mkdir()
  env={**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_SYSTEM':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}
  def git(*args):return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',*args],cwd=area,env=env,stderr=subprocess.DEVNULL).decode().strip()
  git('init','-q');git('init','--bare','-q',str(remote));git('remote','add','origin',str(remote))
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   self.assertTrue(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='modify',evidenceRef=None)['allowed'])
   self.assertTrue(self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='modify',evidenceRef=None,effectId='write-stage',inputDigest=digest('write and stage synthetic.txt'))['executeOnce'])
   (area/'synthetic.txt').write_text('synthetic change\n');git('add','synthetic.txt')
   self.cmd(send,'settle',runId=claim['runId'],taskId=claim['taskId'],effectId='write-stage',outcome='confirmed',evidenceRef='observed staged synthetic file',resultDigest=digest(git('diff','--cached')))
   self.assertTrue(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='commit',evidenceRef='synthetic checks passed')['allowed'])
   self.assertTrue(self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='commit',evidenceRef='synthetic checks passed',effectId='commit-one',inputDigest=digest('commit staged fixture'))['executeOnce'])
   git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-qm','synthetic');commit=git('rev-parse','HEAD')
   self.cmd(send,'settle',runId=claim['runId'],taskId=claim['taskId'],effectId='commit-one',outcome='confirmed',evidenceRef='actual local commit receipt',resultDigest=digest(commit))
   self.assertTrue(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='push',evidenceRef='actual local commit '+commit)['allowed'])
   self.assertTrue(self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='push',evidenceRef='actual local commit '+commit,effectId='push-one',inputDigest=digest({'commit':commit,'target':'local synthetic branch'}))['executeOnce'])
   git('push','origin','HEAD:refs/heads/synthetic')
   self.cmd(send,'settle',runId=claim['runId'],taskId=claim['taskId'],effectId='push-one',outcome='confirmed',evidenceRef='actual local remote ref',resultDigest=digest(git('ls-remote','origin','refs/heads/synthetic')))
   self.cmd(send,'finish',runId=claim['runId'],taskId=claim['taskId'],outcome='pr_ready',evidenceRef='mock GitHub PR; local git receipt',record=record(claim,commit))
   self.assertEqual(git('ls-remote','origin','refs/heads/synthetic').split()[0],commit)


 def test_terminal_write_failure_after_mock_side_effect_is_unknown_not_replayed(self):
  self.enable()
  with self.assertRaises(sqlite3.IntegrityError):
   with self.c.session(self.p['batchId']) as send:
    claim=self.claim(send)
    # Simulate a PR already created; only the local terminal write fails.
    self.c.db.execute("CREATE TRIGGER fail_terminal BEFORE UPDATE OF status ON items WHEN NEW.status='pr_ready' BEGIN SELECT RAISE(FAIL,'mock disk fault');END")
    self.finish(send,claim)
  self.assertEqual(self.c.status(self.p['batchId'])['items'][0]['status'],'unknown')
  self.assertEqual(self.c.db.execute('SELECT count(*) FROM runs').fetchone()[0],1)
  with self.c.session(self.p['batchId']) as send:self.assertFalse(self.cmd(send,'next')['allowed'])

 def test_real_process_lock_cooperative_stop_and_crash_with_harmless_live_child(self):
  root=Path(self.tmp.name)/'process-control';Controller.initialize(root)
  now=dt.datetime.now(dt.timezone.utc);p=copy.deepcopy(PLAN)
  p['startsAt']=(now-dt.timedelta(minutes=5)).isoformat();p['endsAt']=(now+dt.timedelta(minutes=30)).isoformat();p['timezone']='UTC'
  c=Controller(root);self.addCleanup(c.close);c.add(p);c.enable(p['batchId'],digest(p),'synthetic trusted owner',True)
  base=[sys.executable,'-B',str(ROOT/'scripts/batch_control.py'),'--state',str(root)]
  proc=subprocess.Popen(base+['session',p['batchId']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
  def cleanup():
   if proc.poll() is None:proc.kill()
   proc.communicate(timeout=3)
  self.addCleanup(cleanup)
  def response():
   self.assertTrue(select.select([proc.stdout],[],[],3)[0], 'controller response timeout')
   return json.loads(proc.stdout.readline())
  self.assertEqual(response()['session'],'ready')
  req=dict(action='claim',operationId='process-claim',preflight=preflight())
  proc.stdin.write(json.dumps(req)+'\n');proc.stdin.flush();claim=response();self.assertTrue(claim['allowed'])
  other=subprocess.run(base+['session',p['batchId']],input='',text=True,capture_output=True,timeout=3)
  self.assertNotEqual(other.returncode,0)
  # This child models a side effect already in flight. Stop flags do not kill it.
  child=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'])
  def child_cleanup():
   if child.poll() is None:child.terminate()
   child.wait(timeout=3)
  self.addCleanup(child_cleanup)
  stopped=subprocess.run(base+['stop-current',p['batchId']],text=True,capture_output=True,timeout=3)
  self.assertEqual(stopped.returncode,0);self.assertIsNone(child.poll())
  checkpoint=dict(action='checkpoint',operationId='process-checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='modify',evidenceRef=None)
  proc.stdin.write(json.dumps(checkpoint)+'\n');proc.stdin.flush();self.assertFalse(response()['allowed'])
  # SIGKILL is used only on our temporary test controller to model a crash.
  proc.kill();proc.communicate(timeout=3)
  with c.session(p['batchId']) as send:
   self.assertFalse(send(dict(action='next',operationId='after-crash'))['allowed'])
  self.assertEqual(c.status(p['batchId'])['items'][0]['status'],'unknown')
  with self.assertRaises(Blocked):c.reconcile(claim['runId'],claim['taskId'],'child still in flight',False,'stopped')
  self.assertIsNone(child.poll());child_cleanup()
  c.reconcile(claim['runId'],claim['taskId'],'mock child exited and original task reconciled',True,'stopped')
  self.assertFalse(c.status(p['batchId'])['enabled'])


 def test_future_start_and_cached_permit_after_stop(self):
  self.clock[0]=dt.datetime(2029,12,31,23,tzinfo=dt.timezone.utc).timestamp()
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   self.assertEqual(self.cmd(send,'next')['reason'],'not_started')
   self.clock[0]+=7200;claim=self.claim(send)
   req=dict(action='checkpoint',operationId='before-stop-permit',runId=claim['runId'],taskId=claim['taskId'],phase='test',evidenceRef=None)
   self.assertTrue(send(req)['allowed']);self.c.stop(self.p['batchId'],True)
   self.assertFalse(send(req)['allowed']);self.assertTrue(self.c.stop(self.p['batchId'])['stopCurrent'])
   self.finish(send,claim,'stopped')

 def test_operation_conflict_timezone_and_tool_pin(self):
  altered=copy.deepcopy(self.p);altered['timezone']='UTC'
  with self.assertRaises(Blocked):validate_plan(altered)
  altered=copy.deepcopy(self.p);altered['batchId']='bad-tool';altered['controllerSha256']='0'*64
  with self.assertRaises(Blocked):self.c.add(altered)
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   req=dict(action='finish',operationId='one-result',runId=claim['runId'],taskId=claim['taskId'],outcome='blocked',evidenceRef='first evidence',record=None)
   send(req);req['evidenceRef']='different evidence'
   with self.assertRaises(Blocked):send(req)


 def assert_semantic_corruption_blocks(self,sql,params=()):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   self.c.db.execute(sql,params)
   snapshot=[tuple(r) for r in self.c.db.execute('SELECT * FROM items ORDER BY issue')]
   for request in [dict(action='next',operationId='bad-next'),dict(action='claim',operationId='bad-claim',preflight=preflight(99))]:
    with self.assertRaises(Blocked):send(request)
  self.assertEqual([tuple(r) for r in self.c.db.execute('SELECT * FROM items ORDER BY issue')],snapshot)
  with self.assertRaises(Blocked):Controller(self.root,now=lambda:self.clock[0])

 def test_semantic_replaced_issue_fails_without_reset(self):
  self.assert_semantic_corruption_blocks('UPDATE items SET issue=99 WHERE issue=42')

 def test_semantic_extra_pending_fails_without_reset(self):
  self.assert_semantic_corruption_blocks('INSERT INTO items(batch,issue,position) VALUES (?,?,?)',(self.p['batchId'],99,3))

 def test_semantic_reordered_item_fails(self):
  self.assert_semantic_corruption_blocks('UPDATE items SET position=9 WHERE issue=42')

 def test_semantic_status_without_run_fails(self):
  self.assert_semantic_corruption_blocks("UPDATE items SET status='running' WHERE issue=42")

 def test_semantic_run_item_association_fails(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send);self.c.db.execute("UPDATE runs SET issue=99 WHERE id=?",(claim['runId'],))
   with self.assertRaises(Blocked):self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='modify',evidenceRef=None)
  self.assertEqual(self.c.db.execute('SELECT issue FROM runs').fetchone()[0],99)
  with self.assertRaises(Blocked):Controller(self.root,now=lambda:self.clock[0])

 def test_expired_disabled_history_and_local_operation_ids_do_not_stop_new_batch(self):
  self.enable()
  old_req=None
  with self.c.session(self.p['batchId']) as send:
   self.assertTrue(send(dict(action='next',operationId='next-1'))['allowed'])
   claim=self.claim(send)
   old_req=dict(action='checkpoint',operationId='old-checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='test',evidenceRef=None)
   self.assertTrue(send(old_req)['allowed']);self.finish(send,claim,'stopped')
  self.c.stop(self.p['batchId']);self.clock[0]+=86400
  b=copy.deepcopy(self.p);b.update(batchId='new-batch',startsAt='2030-01-02T08:00:00+08:00',endsAt='2030-01-03T08:00:00+08:00')
  self.c.add(b);self.enable(b['batchId'],b)
  before=self.c.status(b['batchId'])
  with self.c.session(self.p['batchId']) as send:
   self.assertFalse(send(dict(action='next',operationId='next-1'))['allowed'])
   self.assertFalse(send(old_req)['allowed'])
   self.assertFalse(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='push',evidenceRef='old evidence')['allowed'])
  self.assertEqual(self.c.status(b['batchId']),before)
  with self.c.session(b['batchId']) as send:
   self.assertTrue(send(dict(action='next',operationId='next-1'))['allowed'])
   current=self.claim(send);self.clock[0]+=86400
   self.assertFalse(self.cmd(send,'checkpoint',runId=current['runId'],taskId=current['taskId'],phase='modify',evidenceRef=None)['allowed'])
   self.assertEqual(self.c.status(b['batchId'])['globalHalt'],'expired');self.finish(send,current,'stopped')

 def test_genuine_global_risk_still_stops_all_batches(self):
  b=copy.deepcopy(self.p);b['batchId']='approved-b';self.c.add(b);self.enable(b['batchId'],b)
  with self.c.session(self.p['batchId']) as send:self.cmd(send,'halt',reason='suspected_leak')
  self.assertFalse(self.c.status(b['batchId'])['enabled']);self.assertEqual(self.c.status(b['batchId'])['globalHalt'],'suspected_leak')

 def test_effect_intent_confirmed_replay_never_grants_external_write_twice(self):
  self.enable();external_writes=0
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   request=dict(action='intent',operationId='pr-intent',runId=claim['runId'],taskId=claim['taskId'],phase='pr',evidenceRef='verified candidate',effectId='create-pr',inputDigest=digest('fixed PR target/head'))
   permit=send(request);self.assertTrue(permit['executeOnce']);external_writes+=1
   self.assertFalse(send(request)['allowed'])
   self.assertFalse(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='pr',evidenceRef='candidate')['allowed'])
   settle=dict(action='settle',operationId='pr-confirmed',runId=claim['runId'],taskId=claim['taskId'],effectId='create-pr',outcome='confirmed',evidenceRef='observed existing mock PR',resultDigest=digest({'pr':7}))
   self.assertTrue(send(settle)['recorded']);self.assertEqual(send(settle),send(settle))
   replay={**request,'operationId':'another-request','effectId':'changed-id'}
   self.assertFalse(send(replay)['allowed']);self.assertEqual(external_writes,1)
   self.finish(send,claim)
  with self.c.session(self.p['batchId']) as send:self.assertFalse(send(request)['allowed'])
  self.assertEqual(external_writes,1)

 def test_uncertain_effect_blocks_finish_and_requires_original_result_reconciliation(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='pr',evidenceRef='candidate',effectId='create-pr',inputDigest=digest('mock existing PR'))
   with self.assertRaises(Blocked):self.finish(send,claim)
   self.cmd(send,'settle',runId=claim['runId'],taskId=claim['taskId'],effectId='create-pr',outcome='uncertain',evidenceRef='network outcome unknown',resultDigest=None)
   self.assertFalse(self.cmd(send,'next')['allowed'])
  with self.assertRaises(Blocked):self.c.reconcile(claim['runId'],claim['taskId'],'mock existing PR',True,'pr_ready',record(claim))
  proof=dict(runId=claim['runId'],taskId=claim['taskId'],effectId='create-pr',evidenceRef='independently observed mock existing PR',verifiedIdle=True,outcome='confirmed',resultDigest=digest({'pr':7}))
  self.c.reconcile_effect(proof);self.assertEqual(self.c.status(self.p['batchId'])['effects'][0]['status'],'confirmed')
  result=self.c.reconcile(claim['runId'],claim['taskId'],'original task and PR reconciled',True,'pr_ready',record(claim))
  self.assertEqual(result['deliveryId'],claim['deliveryId']);self.assertFalse(result['enabled'])

 def test_crash_intent_becomes_uncertain_without_a_new_execution_permit(self):
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='push',evidenceRef='commit receipt',effectId='push',inputDigest=digest('push same commit'))
  self.assertEqual(self.c.status(self.p['batchId'])['effects'][0]['status'],'uncertain')
  with self.assertRaises(Blocked):self.c.enable(self.p['batchId'],digest(self.p),'old approval',True)

 def test_stop_between_temporary_git_commit_and_push_prevents_push(self):
  area=Path(self.tmp.name)/'stopped-checkout';remote=Path(self.tmp.name)/'stopped-remote.git';area.mkdir()
  env={**os.environ,'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_SYSTEM':os.devnull,'GIT_CONFIG_NOSYSTEM':'1'}
  def git(*args):return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',*args],cwd=area,env=env,stderr=subprocess.DEVNULL).decode().strip()
  git('init','-q');git('init','--bare','-q',str(remote));git('remote','add','origin',str(remote))
  (area/'fixture').write_text('fixture');git('add','fixture')
  self.enable()
  with self.c.session(self.p['batchId']) as send:
   claim=self.claim(send)
   self.assertTrue(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='commit',evidenceRef='mock checks passed')['allowed'])
   self.assertTrue(self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='commit',evidenceRef='mock checks passed',effectId='commit',inputDigest=digest('fixture commit'))['executeOnce'])
   git('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','-c','commit.gpgsign=false','commit','-qm','synthetic');commit=git('rev-parse','HEAD')
   self.cmd(send,'settle',runId=claim['runId'],taskId=claim['taskId'],effectId='commit',outcome='confirmed',evidenceRef='local commit exists',resultDigest=digest(commit))
   self.c.stop(self.p['batchId'],True)
   self.assertFalse(self.cmd(send,'checkpoint',runId=claim['runId'],taskId=claim['taskId'],phase='push',evidenceRef='local commit')['allowed'])
   self.assertFalse(self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='push',evidenceRef='local commit',effectId='push',inputDigest=digest(commit))['allowed'])
   self.assertEqual(git('ls-remote','origin'),'');self.finish(send,claim,'stopped')


 def test_semantic_deleted_item_fails_without_reset(self):
  self.assert_semantic_corruption_blocks('DELETE FROM items WHERE issue=43')

 def test_old_state_schema_is_rejected_without_migration(self):
  self.c.db.execute("UPDATE meta SET value='1' WHERE key='schema'")
  with self.assertRaises(Blocked):Controller(self.root)
  self.assertEqual(self.c.db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()[0],'1')

 def test_effect_receipt_write_failure_preserves_uncertainty(self):
  self.enable()
  with self.assertRaises(sqlite3.IntegrityError):
   with self.c.session(self.p['batchId']) as send:
    claim=self.claim(send)
    self.cmd(send,'intent',runId=claim['runId'],taskId=claim['taskId'],phase='pr',evidenceRef='candidate',effectId='pr',inputDigest=digest('actual mock PR created'))
    self.c.db.execute("CREATE TRIGGER fail_receipt BEFORE UPDATE OF status ON effects WHEN NEW.status='confirmed' BEGIN SELECT RAISE(FAIL,'mock receipt write failure');END")
    self.cmd(send,'settle',runId=claim['runId'],taskId=claim['taskId'],effectId='pr',outcome='confirmed',evidenceRef='mock PR observed',resultDigest=digest({'pr':7}))
  state=self.c.status(self.p['batchId']);self.assertEqual(state['items'][0]['status'],'unknown');self.assertEqual(state['effects'][0]['status'],'uncertain')
  with self.c.session(self.p['batchId']) as send:self.assertFalse(self.cmd(send,'next')['allowed'])


if __name__=='__main__':unittest.main()
