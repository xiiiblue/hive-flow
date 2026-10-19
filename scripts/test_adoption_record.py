"""Synthetic single-slot tests; no GitHub, credentials, tasks or business execution."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from adoption_record import BRANCH, PATH, REPOSITORY, config_sha, plan, strict_json

CONFIG = dict(schemaVersion=1, configId='synthetic-config', mode='full_delivery', repository=REPOSITORY,
              control=dict(repository=REPOSITORY, branch=BRANCH, path=PATH),
              issue=dict(number=424242, createdAt='2030-01-01T00:30:00Z'), maxIssues=1, executionEnabled=True,
              activationBoundary='2030-01-01T01:00:00Z', authorizedStart='2030-01-01T00:00:00Z',
              authorizedEnd='2030-01-02T00:00:00Z', scopeRef='approved-scope', acceptanceRef='approved-acceptance',
              environmentRef='approved-development', reviewRef='required-review', recoveryRef='approved-recovery')


def envelope(op='create', record=None, **changes):
    value = dict(operation=op, config=deepcopy(CONFIG), observedIssue=dict(repository=REPOSITORY, **CONFIG['issue'], url='https://github.com/xiiiblue/hive-os/issues/424242', state='open'),
                 now='2030-01-01T02:00:00Z', nativeActive=True, record=deepcopy(record),
                 expectedBlobSHA='a'*40 if record else None, observedBlobSHA='a'*40 if record else None,
                 runId=None if op in ('create', 'cancel') else 'synthetic-run',
                 decision=dict(decidedAt='2030-01-01T01:30:00Z', sourceRef='dots-decision:synthetic0001') if op == 'create' else None,
                 result=None)
    value.update(changes)
    return value


def pending():
    return plan(envelope())['record']


def claimed():
    return plan(envelope('claim', pending()))['record']


class AdoptionTests(unittest.TestCase):
    def reject(self, data):
        before = deepcopy(data)
        with self.assertRaises((ValueError, TypeError)):
            plan(data)
        self.assertEqual(data, before, 'planner must not mutate inputs')

    def test_create_and_stable_duplicate(self):
        first = plan(envelope())
        self.assertTrue(first['write'])
        again = plan(envelope(record=first['record']))
        self.assertFalse(again['write'])
        self.assertEqual(first['record'], again['record'])
        self.assertEqual(first['record']['configSHA'], config_sha(CONFIG))

    def test_inactive_and_unapproved_window(self):
        for changes in (dict(nativeActive=False), dict(now='2030-01-01T01:00:00Z'), dict(now='2030-01-03T00:00:00Z')):
            self.reject(envelope(**changes))
        data = envelope(); data['config']['executionEnabled'] = False; self.reject(data)

    def test_historical_and_future_decisions(self):
        for date in ('2029-12-31T00:00:00Z', '2030-01-01T01:00:00Z', '2030-01-01T03:00:00Z'):
            data=envelope(); data['decision']['decidedAt']=date; self.reject(data)

    def test_location_cannot_be_injected(self):
        for key, value in (('repository','elsewhere/repo'),('branch','main'),('path','../control.json')):
            data=envelope(); data['config']['control'][key]=value; self.reject(data)
        data=envelope(); data['recipient']='forged'; self.reject(data)

    def test_unique_actual_issue(self):
        for key, value in (('repository','elsewhere/repo'),('number',424243),('createdAt','2029-01-01T00:00:00Z')):
            data=envelope(); data['observedIssue'][key]=value; self.reject(data)

    def test_closed_issue_and_pr_cannot_start(self):
        data=envelope();data['observedIssue']['state']='closed';self.reject(data)
        data=envelope('claim',pending());data['observedIssue']['state']='closed';self.reject(data)
        data=envelope();data['observedIssue']['url']='https://github.com/xiiiblue/hive-os/pull/424242';self.reject(data)
        data=envelope('guard',claimed());data['observedIssue']['state']='closed'
        self.assertFalse(plan(data)['write'])  # Existing run may finish bookkeeping after authorized close.

    def test_config_and_decision_cannot_be_rebound(self):
        record=pending()
        for key, value in (('configSHA','b'*64),('configId','other'),('decisionId','c'*64)):
            other=deepcopy(record); other[key]=value; self.reject(envelope('claim',other))
        data=envelope(record=record); data['decision']['sourceRef']='dots-decision:other0000001'; self.reject(data)
        data=envelope(record=record); data['decision']['decidedAt']='2030-01-01T01:31:00Z'; self.reject(data)

    def test_single_issue_and_strict_values(self):
        for value in (2, True):
            data=envelope(); data['config']['maxIssues']=value; self.reject(data)
        data=envelope(); data['config']['executionEnabled']='true'; self.reject(data)
        data=envelope(); data['config']['issue']['number']=True; self.reject(data)

    def test_claim_needs_cas_and_readback(self):
        record=pending(); data=envelope('claim',record,observedBlobSHA='b'*40); self.reject(data)
        proposal=plan(envelope('claim',record))
        self.assertEqual(proposal['record']['state'],'claimed')
        self.assertEqual(proposal['nextAction'],'cas_write_then_readback_no_business_replay')
        self.assertEqual(record['state'],'pending')

    def test_two_stale_claims_only_one_mock_cas_wins(self):
        initial=pending()
        a=plan(envelope('claim',initial,runId='run-a'))
        b=plan(envelope('claim',initial,runId='run-b'))
        # Model GitHub's old-blob precondition, not a real API integration test.
        stored={'sha':'a'*40,'record':initial}
        def cas(proposal, expected):
            if stored['sha'] != expected: return False
            stored.update(sha='b'*40,record=proposal['record']); return True
        self.assertTrue(cas(a,'a'*40)); self.assertFalse(cas(b,'a'*40))
        loser=plan(envelope('claim',stored['record'],runId='run-b',expectedBlobSHA='b'*40,observedBlobSHA='b'*40))
        self.assertFalse(loser['write'])
        self.assertIn('no_reexecution',loser['nextAction'])

    def test_duplicate_claim_never_restarts_business(self):
        for run in ('synthetic-run','different-run'):
            answer=plan(envelope('claim',claimed(),runId=run))
            self.assertFalse(answer['write']); self.assertIn('reconcile',answer['nextAction'])

    def test_guard_checks_owner_pause_expiry_cancel(self):
        record=claimed()
        self.assertIn('not_execution_authority',plan(envelope('guard',record))['nextAction'])
        for changes in (dict(runId='other'),dict(nativeActive=False),dict(now='2030-01-03T00:00:00Z')):
            self.reject(envelope('guard',record,**changes))
        cancelled=plan(envelope('cancel',record))['record']; self.reject(envelope('guard',cancelled))

    def test_cancel_before_claim(self):
        record=plan(envelope('cancel',pending(),nativeActive=False))['record']
        self.assertEqual(record['state'],'cancelled')
        self.reject(envelope('claim',record))
        self.assertFalse(plan(envelope('cancel',record))['write'])

    def test_cancel_after_claim_and_unknown_never_releases_slot(self):
        record=plan(envelope('cancel',claimed()))['record']
        self.assertEqual(record['state'],'claimed'); self.assertTrue(record['cancelRequested'])
        result=dict(stage='unknown',evidenceUrls=[])
        unknown=plan(envelope('unknown',record,result=result))['record']
        self.assertEqual(unknown['claim'],record['claim'])
        self.reject(envelope('guard',unknown))
        self.reject(envelope('claim',unknown))

    def test_unknown_and_blocked_never_auto_release(self):
        for stage in ('unknown','blocked'):
            record=plan(envelope(stage,claimed(),result=dict(stage=stage,evidenceUrls=[])))['record']
            self.assertFalse(plan(envelope('claim',record,runId='new-run'))['write'])
            self.assertFalse(plan(envelope(record=record))['write'])
            cancelled=plan(envelope('cancel',record))['record']
            self.assertEqual(cancelled['state'],stage)
            self.assertEqual(cancelled['claim'],record['claim'])
            self.assertTrue(cancelled['cancelRequested'])

    def test_decision_type_and_sources_are_strict(self):
        for source in ('https://example.com/chat',42,'dots-decision:short'):
            data=envelope();data['decision']['sourceRef']=source;self.reject(data)
        record=pending();record['decision']='approved';self.reject(envelope('claim',record))
        data=envelope();data['config']['issue']['createdAt']='2031-01-01T00:00:00Z'
        data['observedIssue']['createdAt']=data['config']['issue']['createdAt'];self.reject(data)

    def test_unknown_write_reconciliation_uses_exact_proposal(self):
        proposal=plan(envelope('claim',pending()))['record']
        # Readback equal to the proposal is evidence of write, NOT permission to restart.
        observed=deepcopy(proposal)
        self.assertEqual(observed,proposal)
        self.assertIn('reconcile',plan(envelope('claim',observed))['nextAction'])
        self.reject(envelope('guard',observed,runId='replacement-run'))

    def test_results_and_mode_endpoint(self):
        for mode in ('pr_review','full_delivery'):
            cfg=deepcopy(CONFIG);cfg['mode']=mode
            fresh=plan(envelope(config=cfg))['record']
            active=plan(envelope('claim',fresh,config=cfg))['record']
            result=dict(stage=mode,evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/424242'])
            ended=plan(envelope('complete',active,config=cfg,result=result))['record']
            self.assertEqual(ended['state'],'completed')
            self.assertFalse(plan(envelope('claim',ended,config=cfg))['write'])
            self.assertFalse(plan(envelope('cancel',ended,config=cfg))['write'])

    def test_cancel_blocks_completion_but_allows_observed_bookkeeping(self):
        record=plan(envelope('cancel',claimed()))['record']
        result=dict(stage='full_delivery',evidenceUrls=['https://github.com/xiiiblue/hive-os/issues/424242'])
        self.reject(envelope('complete',record,result=result))
        blocked=plan(envelope('blocked',record,nativeActive=False,now='2030-01-03T00:00:00Z',result=dict(stage='blocked',evidenceUrls=[])))
        self.assertEqual(blocked['record']['state'],'blocked')

    def test_completion_cannot_overwrite_concurrent_cancel(self):
        initial=claimed()
        result=dict(stage='full_delivery',evidenceUrls=['https://github.com/xiiiblue/hive-os/issues/424242'])
        self.assertTrue(plan(envelope('complete',initial,result=result))['write'])
        latest=plan(envelope('cancel',initial))['record']
        self.reject(envelope('complete',latest,result=result,observedBlobSHA='b'*40))
        self.reject(envelope('complete',latest,result=result,expectedBlobSHA='b'*40,observedBlobSHA='b'*40))

    def test_bad_results_and_unknown_fields(self):
        for result in (dict(stage='pr_review',evidenceUrls=[]),dict(stage='full_delivery',evidenceUrls=[]),dict(stage='full_delivery',evidenceUrls=['https://github.com/other/repo/issues/1'])):
            self.reject(envelope('complete',claimed(),result=result))
        record=claimed();record['claim']['command']='unsafe';self.reject(envelope('guard',record))

    def test_duplicate_keys_and_nonfinite_rejected(self):
        for raw in ('{"a":1,"a":2}','{"a":NaN}'):
            with self.assertRaises(ValueError): strict_json(raw)

    def test_cli_only_prints_proposal(self):
        tool=Path(__file__).with_name('adoption_record.py')
        answer=subprocess.run([sys.executable,'-B',str(tool)],input=json.dumps(envelope()),capture_output=True,text=True,check=True)
        self.assertTrue(json.loads(answer.stdout)['write'])
        bad=subprocess.run([sys.executable,'-B',str(tool)],input='{}',capture_output=True,text=True)
        self.assertNotEqual(bad.returncode,0)


if __name__ == '__main__': unittest.main()
