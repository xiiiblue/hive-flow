"""合成共享存储/跨宿主接棒；不证明宿主身份或真实平台互斥。"""
from copy import deepcopy
import hashlib
import base64
from pathlib import Path
import json
from threading import Barrier, Lock, Thread
import unittest
from execution_control import PATH, apply, plan as execution_plan
from adopted_issue import plan as issue_plan
from source_evidence import check_baseline, save_baseline, verify_source, expected_block
from test_adopted_issue import fixture

NOW = '2026-01-03T00:00:00Z'
LATER = '2026-01-03T01:00:00Z'


class SharedStore:
    def __init__(self):
        self.values = {}
        self.lock = Lock()
        self.writes = 0

    def read(self, path):
        with self.lock:
            return deepcopy(self.values.get(path, (None, None)))

    def write(self, path, record, sha):
        with self.lock:
            if self.values.get(path, (None, None))[1] != sha:
                raise RuntimeError('CAS conflict')
            self.writes += 1
            digest = hashlib.sha1(json.dumps(record, sort_keys=True).encode()).hexdigest()
            self.values[path] = (deepcopy(record), digest)
            return digest


def run(store, operation, identity='native-A', mode='full_delivery', **kwargs):
    return apply(store, operation, run_id=identity, mode=mode, now=kwargs.pop('now', NOW), **kwargs)


def authorization(lock_sha, issue_sha, mode='full_delivery'):
    return dict(source='trusted_user_channel', approvedAt=LATER, previousRunId='native-A', nextRunId='native-B',
                previousRunEndedAt='2026-01-03T00:10:00Z', resultsVerifiedAt='2026-01-03T00:20:00Z',
                lockBlobSHA=lock_sha, issueBlobSHA=issue_sha, issueNumber=12, mode=mode,
                previousIssueRunId='native-A', previousIssueRunEndedAt='2026-01-03T00:10:00Z')


class ExecutionTests(unittest.TestCase):
    def test_different_issues_and_modes_share_global_occupancy(self):
        store = SharedStore()
        first = run(store, 'acquire')
        self.assertTrue(first['write'])
        for identity, mode in [('native-B', 'pr_review'), ('native-A', 'full_delivery')]:
            self.assertEqual(run(store, 'acquire', identity, mode)['nextAction'], 'busy_exit')
        run(store, 'bind', issue=12)
        self.assertFalse(run(store, 'guard', issue=12)['write'])
        for kwargs in ({'identity': 'native-B'}, {'issue': 13}, {'mode': 'pr_review'}):
            with self.assertRaises(ValueError): run(store, 'guard', **({'issue': 12} | kwargs))
        with self.assertRaises(ValueError): run(store, 'bind', issue=13)
        with self.assertRaises(ValueError): run(store, 'release')
        run(store, 'release', results_known=True)
        self.assertTrue(run(store, 'acquire', 'native-B', 'pr_review')['write'])

    def test_atomic_create_and_update_have_one_winner(self):
        for initial in (False, True):
            store = SharedStore()
            if initial:
                run(store, 'acquire'); run(store, 'release', results_known=True)
            barrier = Barrier(2)
            original = store.read
            def read(path):
                value = original(path)
                barrier.wait()
                return value
            # 读回不在apply中重复触发barrier；真实适配器另有独立读回。
            store.read = read
            outcomes = []
            def compete(identity):
                try: outcomes.append(run(store, 'acquire', identity)['nextAction'])
                except RuntimeError: outcomes.append('conflict')
            tasks = [Thread(target=compete, args=(name,)) for name in ('native-A', 'native-B')]
            for t in tasks: t.start()
            for t in tasks: t.join()
            self.assertCountEqual(outcomes, ['acquired_after_readback', 'conflict'])

    def test_no_expiry_or_unknown_release(self):
        store = SharedStore(); run(store, 'acquire'); run(store, 'unknown')
        self.assertEqual(run(store, 'acquire', 'native-B', now='2030-01-01T00:00:00Z')['nextAction'], 'busy_exit')
        for op in ('guard', 'release', 'bind'):
            with self.assertRaises(ValueError): run(store, op, issue=12, results_known=True)

    def test_network_result_unknown_is_not_retried(self):
        store = SharedStore(); original = store.write
        def lost_reply(path, record, sha):
            original(path, record, sha)
            raise RuntimeError('response lost after commit')
        store.write = lost_reply
        with self.assertRaises(RuntimeError): run(store, 'acquire')
        self.assertEqual(store.writes, 1)
        self.assertEqual(run(store, 'acquire', 'native-B')['nextAction'], 'busy_exit')

    def test_same_owner_can_resolve_verified_unknown_without_reclaim(self):
        store = SharedStore(); run(store, 'acquire'); run(store, 'bind', issue=12); run(store, 'unknown')
        with self.assertRaises(ValueError): run(store, 'resolve')
        with self.assertRaises(ValueError): run(store, 'resolve', 'native-B', results_known=True)
        lock = run(store, 'resolve', results_known=True)['record']
        data = fixture(); data['record'] = issue_plan(data)['record']; data['record'].update(runId='native-A',state='unknown')
        data.update(operation='resolve',runId='native-A',blobSHA='a'*40,executionRecord=lock,
                    resolutionEvidence={'recordBlobSHA':'a'*40,'verifiedAt':NOW},
                    evidenceUrls=['https://github.com/xiiiblue/hive-os/issues/12'])
        self.assertEqual(issue_plan(data)['record']['state'],'claimed')
        data['resolutionEvidence']['recordBlobSHA']='b'*40
        with self.assertRaises(ValueError): issue_plan(data)

    def test_handoff_fences_old_owner_and_continues_existing_issue(self):
        store = SharedStore(); run(store, 'acquire'); run(store, 'bind', issue=12)
        record, lock_sha = store.read(PATH)
        auth = authorization(lock_sha, 'a' * 40)
        new = run(store, 'handoff', 'native-B', now=LATER, authorization=auth)
        with self.assertRaises(ValueError): run(store, 'guard', issue=12, now=LATER)
        self.assertFalse(run(store, 'guard', 'native-B', issue=12, now=LATER)['write'])
        for state in ('claimed', 'blocked', 'unknown'):
            data = fixture(); data['record'] = issue_plan(data)['record']; data['record'].update(runId='native-A', state=state)
            data.update(operation='handoff', runId='native-B', now=LATER, blobSHA='a'*40,
                        handoffAuthorization=auth, executionRecord=new['record'])
            result = issue_plan(data)
            self.assertEqual(result['record']['runId'], 'native-B')
            self.assertEqual(result['record']['state'], 'claimed')
            self.assertEqual(result['record']['revision'], 2)
            self.assertEqual(result['expectedBlobSHA'], 'a'*40)
            changed = deepcopy(data); changed['issue']['body'] += '\n增加范围'
            with self.assertRaises(ValueError): issue_plan(changed)

    def test_handoff_authorization_cannot_be_replayed_or_unbound(self):
        store = SharedStore(); run(store, 'acquire'); run(store, 'bind', issue=12)
        record, sha = store.read(PATH); auth = authorization(sha, 'a'*40)
        for key, value in [('lockBlobSHA', 'b'*40), ('previousRunId', 'other'), ('nextRunId', 'other'),
                           ('issueNumber', 13), ('mode', 'pr_review'), ('source', 'issue_comment'),
                           ('previousRunEndedAt', '2026-01-03T02:00:00Z'), ('approvedAt', '2026-01-04T00:00:00Z'), ('approvedAt', NOW)]:
            invalid = dict(auth, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                run(store, 'handoff', 'native-B', now=LATER, authorization=invalid)
        run(store, 'handoff', 'native-B', now=LATER, authorization=auth)
        with self.assertRaises(ValueError): run(store, 'handoff', 'native-C', now=LATER, authorization=auth)

    def test_unbound_crash_can_be_handed_off_before_selection(self):
        store = SharedStore(); run(store, 'acquire')
        _, sha = store.read(PATH)
        auth = authorization(sha, None)
        auth.update(issueNumber=None, previousIssueRunId=None, previousIssueRunEndedAt=None,
                    approvedAt='2026-01-03T00:15:00Z')
        result = run(store, 'handoff', 'native-B', now=LATER, authorization=auth)
        self.assertIsNone(result['record']['owner']['issueNumber'])
        run(store, 'bind', 'native-B', issue=13, now=LATER)

    def test_interrupted_two_step_transfer_can_continue_under_next_real_run(self):
        store = SharedStore(); run(store,'acquire'); run(store,'bind',issue=12)
        _,sha=store.read(PATH)
        # B只完成全仓转移；Issue仍归A。B结束后，C的两层前任不同。
        run(store,'handoff','native-B',now=LATER,authorization=authorization(sha,'a'*40))
        _,sha=store.read(PATH)
        auth=authorization(sha,'a'*40)
        auth.update(previousRunId='native-B',nextRunId='native-C',previousRunEndedAt='2026-01-03T01:10:00Z',
                    resultsVerifiedAt='2026-01-03T01:20:00Z',approvedAt='2026-01-03T01:15:00Z')
        lock=run(store,'handoff','native-C',now='2026-01-03T02:00:00Z',authorization=auth)['record']
        data=fixture(); data['record']=issue_plan(data)['record']; data['record']['runId']='native-A'
        data.update(operation='handoff',runId='native-C',now='2026-01-03T02:00:00Z',blobSHA='a'*40,
                    handoffAuthorization=auth,executionRecord=lock)
        self.assertEqual(issue_plan(data)['record']['runId'],'native-C')
        auth['previousIssueRunId']='native-B'
        with self.assertRaises(ValueError): issue_plan(data)

    def test_closed_unknown_can_finalize_without_reopening_or_business_guard(self):
        for operation in ('resolve','handoff'):
            store=SharedStore();run(store,'acquire');run(store,'bind',issue=12)
            data=fixture();data['record']=issue_plan(data)['record'];data['record'].update(runId='native-A',state='unknown')
            data['issue']['state']='closed';data.update(operation=operation,runId='native-A',now=LATER,blobSHA='a'*40)
            if operation=='handoff':
                _,sha=store.read(PATH);auth=authorization(sha,'a'*40)
                lock=run(store,'handoff','native-B',now=LATER,authorization=auth)['record']
                data.update(runId='native-B',executionRecord=lock,handoffAuthorization=auth)
            else:
                data.update(executionRecord=store.read(PATH)[0],resolutionEvidence={'recordBlobSHA':'a'*40,'verifiedAt':LATER},
                            evidenceUrls=['https://github.com/xiiiblue/hive-os/issues/12'])
            resolved=issue_plan(data)
            self.assertEqual(resolved['nextAction'],'finalize_only')
            self.assertEqual(resolved['record']['state'],'finalizing')
            data.update(record=resolved['record'],executionRecord=None,handoffAuthorization=None,resolutionEvidence=None,
                        evidenceUrls=[],operation='guard')
            with self.assertRaises(ValueError):issue_plan(data)
            data['issue']['state']='open'
            with self.assertRaises(ValueError):issue_plan(data)
            data['issue']['state']='closed'
            data.update(operation='finish',result='full_delivery',evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/14'])
            self.assertEqual(issue_plan(data)['record']['state'],'full_delivery')

    def test_finalization_restriction_survives_unknown_and_reopened_issue(self):
        for operation in ('resolve','handoff'):
            store=SharedStore();run(store,'acquire');run(store,'bind',issue=12)
            data=fixture();data['record']=issue_plan(data)['record'];data['record'].update(runId='native-A',state='finalizing')
            data['issue']['state']='closed'
            data.update(operation='finish',runId='native-A',blobSHA='a'*40,result='unknown')
            unknown=issue_plan(data)['record']
            self.assertEqual(unknown['state'],'finalizing_unknown')
            data.update(operation=operation,record=unknown,result=None,now=LATER)
            data['issue']['state']='open'
            if operation=='resolve':
                data.update(executionRecord=store.read(PATH)[0],resolutionEvidence={'recordBlobSHA':'a'*40,'verifiedAt':LATER},
                            evidenceUrls=['https://github.com/xiiiblue/hive-os/issues/12'])
            else:
                _,sha=store.read(PATH);auth=authorization(sha,'a'*40)
                lock=run(store,'handoff','native-B',now=LATER,authorization=auth)['record']
                data.update(runId='native-B',executionRecord=lock,handoffAuthorization=auth)
            limited=issue_plan(data)
            self.assertEqual(limited['record']['state'],'finalizing')
            data.update(operation='guard',record=limited['record'],executionRecord=None,resolutionEvidence=None,
                        handoffAuthorization=None,evidenceUrls=[])
            with self.assertRaises(ValueError): issue_plan(data)

    def test_all_finalizing_states_keep_restriction_across_modes_and_reopen(self):
        for state in ('finalizing', 'finalizing_blocked', 'finalizing_unknown'):
            for mode in ('pr_review', 'full_delivery'):
                for issue_state in ('open', 'closed'):
                    with self.subTest(state=state, mode=mode, issue_state=issue_state):
                        store = SharedStore(); run(store, 'acquire'); run(store, 'bind', issue=12)
                        _, sha = store.read(PATH)
                        auth = authorization(sha, 'a'*40, mode)
                        lock = run(store, 'handoff', 'native-B', mode, now=LATER, authorization=auth)['record']
                        data = fixture(); data['record'] = issue_plan(data)['record']
                        data['record'].update(state=state, runId='native-A')
                        data['issue']['state'] = issue_state
                        data.update(operation='handoff', mode=mode, runId='native-B', now=LATER,
                                    blobSHA='a'*40, handoffAuthorization=auth, executionRecord=lock)
                        if mode == 'pr_review':
                            with self.assertRaises(ValueError): issue_plan(data)
                        else:
                            result = issue_plan(data)
                            self.assertEqual(result['record']['state'], 'finalizing')
                            self.assertEqual(result['nextAction'], 'finalize_only')

    def test_unresolved_finalization_cannot_finish_successfully(self):
        for state in ('finalizing_unknown', 'finalizing_blocked'):
            with self.subTest(state=state):
                data = fixture(); data['record'] = issue_plan(data)['record']
                data['record']['state'] = state
                data['issue']['state'] = 'closed'
                data.update(operation='finish', blobSHA='a'*40, result='full_delivery',
                            evidenceUrls=['https://github.com/xiiiblue/hive-os/issues/12'])
                with self.assertRaises(ValueError): issue_plan(data)
                # 仍可记录停止事实，不能把未知结果改成成功。
                data['result'] = 'unknown'
                self.assertEqual(issue_plan(data)['record']['state'], 'finalizing_unknown')
                store = SharedStore()
                run(store, 'acquire', data['runId']); run(store, 'bind', data['runId'], issue=12)
                data.update(operation='resolve', result=None, executionRecord=store.read(PATH)[0],
                            resolutionEvidence={'recordBlobSHA': 'a'*40, 'verifiedAt': NOW})
                resolved = issue_plan(data)
                self.assertEqual(resolved['nextAction'], 'finalize_only')
                data.update(operation='finish', record=resolved['record'], result='full_delivery',
                            executionRecord=None, resolutionEvidence=None)
                self.assertEqual(issue_plan(data)['record']['state'], 'full_delivery')

    def test_pr_to_full_and_completed_notification_only(self):
        for old_mode, new_mode, state in [('pr_review','full_delivery','pr_review'),
                                          ('pr_review','pr_review','pr_review'),
                                          ('full_delivery','full_delivery','full_delivery')]:
            store = SharedStore(); run(store, 'acquire', mode=old_mode); run(store, 'bind', mode=old_mode, issue=12)
            _, sha = store.read(PATH); auth = authorization(sha, 'a'*40, new_mode)
            lock = run(store, 'handoff', 'native-B', new_mode, now=LATER, authorization=auth)['record']
            data = fixture(); data['mode'] = old_mode; data['record'] = issue_plan(data)['record']
            data['record'].update(state=state, runId='native-A', evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/14'])
            if state == 'full_delivery': data['issue']['state'] = 'closed'
            data.update(operation='handoff', mode=new_mode, runId='native-B', now=LATER, blobSHA='a'*40,
                        handoffAuthorization=auth, executionRecord=lock)
            result = issue_plan(data)
            self.assertEqual(result['record']['evidenceUrls'], data['record']['evidenceUrls'])
            if old_mode == new_mode:
                self.assertEqual(result['nextAction'], 'notification_only')
                data.update(operation='notify', record=result['record'], handoffAuthorization=None, executionRecord=None)
                self.assertEqual(issue_plan(data)['nextAction'], 'notification_only')
            else:
                self.assertEqual(result['record']['state'], 'claimed')


class BaselineTests(unittest.TestCase):
    def test_shared_baseline_survives_new_client_without_local_memory(self):
        store = SharedStore(); data = fixture()
        sha = save_baseline(store, data['issue'], data['actor'], data['boundary'], data['now'])
        path = '.hiveflow/control/baselines/12.json'
        portable, actual_sha = store.read(path)
        self.assertEqual(actual_sha, sha)
        self.assertEqual(check_baseline(portable, 12, data['actor'], data['boundary'], LATER), {k:v for k,v in data['issue'].items() if k != 'body'})
        self.assertNotIn(data['issue']['body'], json.dumps(portable,ensure_ascii=False))
        self.assertEqual(save_baseline(store, data['issue'], data['actor'], data['boundary'], LATER), sha)
        self.assertEqual(store.writes, 1)
        changed = deepcopy(data['issue']); changed['body'] += '\n新范围'
        with self.assertRaises(ValueError): save_baseline(store, changed, data['actor'], data['boundary'], LATER)
        portable['bodySHA256'] = 'b'*64
        with self.assertRaises(ValueError): check_baseline(portable, 12, data['actor'], data['boundary'], LATER)

    def test_sync_verifier_uses_shared_source_and_synthetic_generator(self):
        from unittest.mock import patch
        store = SharedStore(); data = fixture()
        save_baseline(store, data['issue'], data['actor'], data['boundary'], data['now'])
        workflow = dict(id=7, repository={'full_name': 'xiiiblue/hive-os'}, path='.github/workflows/issue-sync.yml',
                        head_branch='main', head_sha='a'*40, status='completed', conclusion='success',
                        event='push', html_url='https://github.com/xiiiblue/hive-os/actions/runs/7')
        registry = """|问题编号|问题摘要|外部Issue|严重级别|状态|处理ACL|
|---|---|---|---|---|---|
|ISSUE-000012|示例|[#12](https://github.com/xiiiblue/hive-os/issues/12)|S3|In Progress|—|
"""
        # 公开副本只测同步核验契约；真实HiveOS生成器集成仍由上游验证。
        generator_fixtures = {'scripts/workflows/manage-issue.mjs': 'export function managedIssueBlock() { return "<!-- HIVE-ISSUE-SYNC:START -->\\n> \\u6b63\\u5f0f\\u72b6\\u6001\\uff1aIn Progress\\n<!-- HIVE-ISSUE-SYNC:END -->"; }', 'scripts/lib/governance/issue-registry.mjs': 'export function extractIssueRows() { return [{external:"[#12](https://github.com/xiiiblue/hive-os/issues/12)"}]; }'}
        def api(endpoint):
            if endpoint.startswith('users/'):
                return dict(id=41898282, type='Bot', login='github-actions[bot]')
            if '/actions/runs/' in endpoint:
                return deepcopy(workflow)
            path = endpoint.split('/contents/')[1].split('?')[0]
            content = registry.encode() if path.endswith('ISSUE/README.md') else generator_fixtures[path].encode()
            return dict(type='file', encoding='base64', content=base64.b64encode(content).decode())
        store.api = api
        block = expected_block(store, workflow, 12)
        current = deepcopy(data['issue']); current.update(editor='github-actions', body=block+'\n\n'+current['body'])
        verified = verify_source(store, current, data['actor'], data['boundary'], data['now'], 7)
        self.assertEqual(verified['identity']['scopeSHA'], issue_plan(data)['record']['scopeSHA'])
        for body in (current['body'].replace('只改示例组件。','扩大权限。'), current['body'].replace('In Progress','Closed')):
            changed = dict(current, body=body)
            with self.assertRaises(ValueError): verify_source(store, changed, data['actor'], data['boundary'], data['now'], 7)
        for field,value in [('conclusion','failure'), ('path','other.yml'), ('head_branch','other')]:
            old = workflow[field]; workflow[field] = value
            with patch('source_evidence.expected_block', side_effect=AssertionError('untrusted tool executed')):
                with self.assertRaises(ValueError): verify_source(store,current,data['actor'],data['boundary'],data['now'],7)
            workflow[field] = old

    def test_missing_baseline_or_wrong_editor_is_not_bypassed(self):
        store = SharedStore(); data = fixture(); data['issue']['editor'] = 'github-actions'
        with self.assertRaisesRegex(ValueError, 'baseline missing'):
            verify_source(store, data['issue'], data['actor'], data['boundary'], data['now'], 1)


if __name__ == '__main__':
    unittest.main()
