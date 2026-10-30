"""仅合成数据；不证明GitHub来源、CAS或真实自动交付。"""
from copy import deepcopy
import unittest
from adopted_issue import candidate, plan

BODY = '''提出人：示例
评审状态：已采纳（待实施授权）
登记状态：待登记

## 问题或需求说明
调整示例按钮。
## 期望 / 调整范围
只改示例组件。
## 验收要点
按钮布局正常。
<!-- wecom-intake:11111111-2222-3333-4444-555555555555 -->
'''


def fixture(number=12):
    return dict(operation='claim', issue=dict(repository='xiiiblue/hive-os', number=number,
        url=f'https://github.com/xiiiblue/hive-os/issues/{number}', state='open',
        createdAt='2026-01-02T02:00:00Z', author='example-owner', editor='example-owner', body=BODY),
        boundary='2026-01-02T00:00:00Z', now='2026-01-03T00:00:00Z', actor='example-owner',
        runId='native-run-example', mode='full_delivery', record=None, blobSHA=None, result=None, evidenceUrls=[])


class AdoptedIssueTests(unittest.TestCase):
    def test_existing_standard_body_keeps_its_record_digest(self):
        self.assertEqual(plan(fixture())['record']['scopeSHA'],
                         '0b91be47d81ebf8309defc8dd2286898fa0310cd0fc5519554b717d9d68f1360')

    def test_equivalent_headings_keep_the_same_scope(self):
        baseline = plan(fixture())['record']['scopeSHA']
        variants = (
            ('## 期望 / 调整范围', '## 期望与调整范围'),
            ('## 期望 / 调整范围', '### 调整范围'),
            ('## 期望 / 调整范围', '  ##  ２、 **期望 ／ 调整范围：**  ##'),
            ('## 期望 / 调整范围', '## **（二）调整范围**'),
            ('## 期望 / 调整范围', '## **2. 调整范围**'),
            ('## 期望 / 调整范围', '## **调整范围**：'),
            ('## 期望 / 调整范围', '## 变更范围'),
            ('## 问题或需求说明', '## 一、问题描述'),
            ('## 问题或需求说明', '## 需求说明'),
            ('## 验收要点', '## 验收标准'),
            ('## 验收要点', '## __验收要求__'),
            ('## 验收要点', '# 验收清单'),
        )
        for before, after in variants:
            data = fixture(); data['issue']['body'] = BODY.replace(before, after)
            with self.subTest(heading=after):
                self.assertEqual(plan(data)['record']['scopeSHA'], baseline)

    def test_header_boundary_and_guard_follow_real_headings(self):
        data = self.claimed(); data['operation'] = 'guard'
        data['issue']['body'] = BODY.replace('## ', '### ').replace('期望 / 调整范围', '期望与调整范围')
        self.assertFalse(plan(data)['write'])
        data['issue']['body'] += '\n### 备注\n评审状态：暂缓\n'
        # 正文其它位置的字段不作采纳决定，也不混入此前验收章节。
        self.assertFalse(plan(data)['write'])
        data['issue']['body'] = data['issue']['body'].replace('只改示例组件。', '增加真实业务权限。')
        with self.assertRaisesRegex(ValueError, 'scope changed'): plan(data)

    def test_duplicate_aliases_and_unknown_headings_remain_invalid(self):
        for body in (
            BODY.replace('## 验收要点', '## 验收要点\n已有验收。\n### 验收标准'),
            BODY.replace('## 期望 / 调整范围', '## 调整范围\n原范围。\n## 期望与调整范围'),
            BODY.replace('## 期望 / 调整范围', '## 以后也许会做'),
            BODY.replace('## 验收要点', '## 初步建议'),
        ):
            data = fixture(); data['issue']['body'] = body
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'missing or duplicated section'):
                plan(data)

    def test_hidden_quoted_and_code_headings_cannot_supply_missing_sections(self):
        for wrap in (lambda text: '> ' + text,
                     lambda text: '  > ' + text,
                     lambda text: '    ' + text,
                     lambda text: '<!--\n' + text + '\n-->',
                     lambda text: '```markdown\n' + text + '\n```',
                     lambda text: '~~~\n' + text + '\n~~~',
                     lambda text: '<!-- first --> <!-- second\n' + text + '\n-->',
                     lambda text: '<pre>\n' + text + '\n</pre>',
                     lambda text: '<CODE class="sample">\n' + text + '\n</CODE>'):
            data = fixture(); data['issue']['body'] = BODY.replace('## 期望 / 调整范围', wrap('## 调整范围'))
            with self.subTest(body=data['issue']['body']), self.assertRaisesRegex(ValueError, 'missing or duplicated section'):
                plan(data)

    def test_heading_examples_inside_sections_are_not_duplicate_sections(self):
        for example in ('```markdown\n## 调整范围\n```',
                        '> ## 调整范围', '<!--\n## 调整范围\n-->'):
            data = fixture(); data['issue']['body'] = BODY.replace('只改示例组件。', '只改示例组件。\n' + example)
            self.assertTrue(plan(data)['write'])

    def test_empty_alias_sections_cannot_borrow_following_sections(self):
        for content in ('', '\n<!-- 仅为注释 -->'):
            data = fixture(); data['issue']['body'] = BODY.replace('只改示例组件。', content).replace('## 期望 / 调整范围', '## 调整范围')
            with self.subTest(content=content), self.assertRaisesRegex(ValueError, 'empty section'):
                plan(data)

    def test_real_section_content_can_include_quoted_or_code_requirements(self):
        for content in ('> 只改示例组件。', '```text\n只改示例组件。\n```', '只改<code>示例组件</code>。'):
            data = fixture(); data['issue']['body'] = BODY.replace('只改示例组件。', content)
            with self.subTest(content=content):
                self.assertTrue(plan(data)['write'])

    def test_acceptance_cannot_be_replaced_by_following_notes(self):
        for heading in ('## 备注', '### 备注', '### **附注**：', '#### 补充说明'):
            data = fixture(); data['issue']['body'] = BODY.replace('按钮布局正常。', heading + '\n以后再补验收。')
            with self.subTest(heading=heading), self.assertRaisesRegex(ValueError, 'empty section'):
                plan(data)

    def test_normal_acceptance_subheadings_are_preserved(self):
        data = fixture(); data['issue']['body'] = BODY.replace('按钮布局正常。', '### 正常流程\n按钮布局正常。\n### 请求失败\n显示错误提示。')
        self.assertTrue(plan(data)['write'])

    def test_unclosed_comment_inside_fenced_example_does_not_hide_real_sections(self):
        data = fixture(); data['issue']['body'] = BODY.replace('只改示例组件。', '只改示例组件。\n```html\n<!--\n```')
        self.assertTrue(plan(data)['write'])

    def test_repeated_future_issues_need_no_allowlist(self):
        for number in (12, 13, 1042):
            data = fixture(number)
            result = plan(data)
            self.assertTrue(result['write'])
            self.assertEqual(result['path'], f'.hiveflow/control/issues/{number}.json')
            self.assertEqual(result['record']['issueNumber'], number)

    def test_creation_boundary_and_future(self):
        for created in ('2025-12-01T00:00:00Z', '2026-02-01T00:00:00Z'):
            data = fixture(); data['issue']['createdAt'] = created
            with self.assertRaises(ValueError): plan(data)

    def test_wrong_repository_pr_closed_and_actor_rejected(self):
        for key, value in [('repository', 'other/project'), ('url', 'https://github.com/xiiiblue/hive-os/pull/12'),
                           ('state', 'closed'), ('author', 'other'), ('editor', None), ('editor', 'other')]:
            data = fixture(); data['issue'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError): plan(data)

    def test_missing_ambiguous_and_quoted_adoption(self):
        for body in (BODY.replace('已采纳（待实施授权）', '待负责人确认'),
                     BODY.replace('已采纳（待实施授权）', '不采纳'),
                     BODY.replace('已采纳（待实施授权）', '暂缓'),
                     BODY.replace('评审状态：', '> 评审状态：'),
                     '```\n' + BODY + '\n```',
                     BODY.replace('## 问题', '评审状态：暂缓\n## 问题'),
                     BODY.replace('<!-- wecom-intake:', '<!-- other:'),
                     BODY + '<!-- wecom-intake:11111111-2222-3333-4444-555555555555 -->\n',
                     BODY.replace('## 验收要点\n按钮布局正常。', '## 验收要点\n')):
            data = fixture(); data['issue']['body'] = body
            with self.subTest(body=body), self.assertRaises(ValueError): plan(data)

    def claimed(self):
        data = fixture(); data['record'] = plan(data)['record']; data['blobSHA'] = 'a' * 40
        return data

    def test_existing_claim_never_restarts_either_mode(self):
        data = self.claimed()
        for mode in ('pr_review', 'full_delivery'):
            data['mode'] = mode
            self.assertFalse(plan(data)['write'])
            self.assertEqual(plan(data)['nextAction'], 'existing_record_do_not_restart')

    def test_guard_requires_current_owner_scope_and_mode(self):
        original = self.claimed(); original['operation'] = 'guard'
        self.assertFalse(plan(original)['write'])
        for key, value in [('runId', 'other-run'), ('mode', 'pr_review')]:
            data = deepcopy(original); data[key] = value
            with self.assertRaises(ValueError): plan(data)
        data = deepcopy(original); data['issue']['body'] += '\n范围变化'
        with self.assertRaises(ValueError): plan(data)

    def test_finish_and_repeated_runs(self):
        for mode in ('pr_review', 'full_delivery'):
            data = fixture(); data['mode'] = mode
            data['record'] = plan(data)['record']; data['blobSHA'] = 'a' * 40
            data.update(operation='finish', result=mode, evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/14'])
            if mode == 'full_delivery': data['issue']['state'] = 'closed'
            finished = plan(data)
            self.assertEqual(finished['record']['state'], mode)
            self.assertEqual(finished['expectedBlobSHA'], 'a' * 40)
            data['issue']['state'] = 'open'  # 已关闭单重开也不得自动重做。
            data.update(operation='claim', record=finished['record'], result=None, evidenceUrls=[])
            self.assertFalse(plan(data)['write'])

    def test_unknown_and_blocked_cannot_reclaim(self):
        for state in ('unknown', 'blocked'):
            data = self.claimed(); data.update(operation='finish', result=state)
            record = plan(data)['record']
            data.update(operation='claim', record=record, result=None)
            self.assertFalse(plan(data)['write'])
            data['operation'] = 'guard'
            with self.assertRaises(ValueError): plan(data)

    def test_forged_identity_missing_sha_and_completion_evidence(self):
        for mutate in (lambda d: d['record'].update(issueNumber=13),
                       lambda d: d.update(blobSHA=None),
                       lambda d: d.update(operation='finish', result='full_delivery'),
                       lambda d: d.update(operation='finish', result='pr_review')):
            data = self.claimed(); mutate(data)
            with self.assertRaises(ValueError): plan(data)

    def test_hidden_header_and_code_source_are_not_candidates(self):
        hidden_header = BODY.replace('提出人：示例', '<!--\n提出人：示例').replace('## 问题', '-->\n## 问题')
        code_source = BODY.replace('<!-- wecom-intake:', '```text\n<!-- wecom-intake:') + '```\n'
        commented_source = BODY.replace('<!-- wecom-intake:', '<!-- hidden\n<!-- wecom-intake:')
        inline_hidden = BODY.replace('提出人：示例', '提出人：示例 <!--').replace('## 问题', '-->\n## 问题')
        for body in (hidden_header, inline_hidden, code_source, commented_source):
            data = fixture(); data['issue']['body'] = body
            with self.subTest(body=body), self.assertRaises(ValueError): plan(data)

    def test_success_requires_unchanged_scope_and_correct_issue_state(self):
        for mode in ('pr_review', 'full_delivery'):
            data = fixture(); data['mode'] = mode
            data['record'] = plan(data)['record']; data['blobSHA'] = 'a' * 40
            data.update(operation='finish', result=mode, evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/14'])
            if mode == 'full_delivery': data['issue']['state'] = 'closed'
            changed = deepcopy(data); changed['issue']['body'] = BODY.replace('只改示例组件。', '改动全局权限。')
            with self.assertRaises(ValueError): plan(changed)
            changed = deepcopy(data); changed['issue']['state'] = 'open' if mode == 'full_delivery' else 'closed'
            with self.assertRaises(ValueError): plan(changed)
            managed = '<!-- HIVE-ISSUE-SYNC:START -->\n> 正式状态：Closed\n<!-- HIVE-ISSUE-SYNC:END -->\n'
            data['issue']['body'] = managed + BODY.replace('登记状态：待登记', '登记状态：已登记')
            self.assertTrue(plan(data)['write'])

    def test_stop_record_allowed_after_actual_adoption_withdrawal_or_scope_change(self):
        for state in ('blocked', 'unknown'):
            data = self.claimed(); data.update(operation='finish', result=state)
            data['issue']['body'] = '评审状态：暂缓\n范围已经改变'
            data['issue']['editor'] = 'different-actor'
            self.assertTrue(plan(data)['write'])
            data['result'] = 'full_delivery'
            with self.assertRaises(ValueError): plan(data)

    def test_guard_never_claims_platform_authorization(self):
        data = self.claimed(); data['operation'] = 'guard'
        self.assertEqual(plan(data)['nextAction'], 'check_live_task_and_side_effects_before_action')

    def synced(self):
        data = self.claimed(); data['operation'] = 'guard'
        baseline = deepcopy(data['issue'])
        block = '<!-- HIVE-ISSUE-SYNC:START -->\n> 正式状态：In Progress\n<!-- HIVE-ISSUE-SYNC:END -->'
        data['issue'].update(editor='github-actions', body=block + '\n\n' + BODY)
        data['sourceEvidence'] = dict(baselineIssue=baseline,
            editor={'login': 'github-actions', '__typename': 'Bot', 'databaseId': 41898282},
            workflowRun=dict(id=123, repository='xiiiblue/hive-os', path='.github/workflows/issue-sync.yml',
                head_branch='main', head_sha='b' * 40, status='completed', conclusion='success', event='push',
                html_url='https://github.com/xiiiblue/hive-os/actions/runs/123'), expectedManagedBlock=block)
        return data

    def test_controlled_metadata_sync_keeps_guard_and_success_valid(self):
        data = self.synced()
        self.assertFalse(plan(data)['write'])
        data.update(operation='finish', result='full_delivery', evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/14'])
        data['issue']['state'] = 'closed'
        self.assertEqual(plan(data)['record']['state'], 'full_delivery')

    def test_metadata_exception_cannot_bootstrap_new_adoption(self):
        data = self.synced(); data.update(operation='claim', record=None, blobSHA=None)
        with self.assertRaisesRegex(ValueError, 'initial adoption'): plan(data)
        data.pop('sourceEvidence')
        with self.assertRaisesRegex(ValueError, 'evidence required'): plan(data)

    def test_metadata_sync_requires_real_bot_and_fixed_successful_workflow(self):
        changes = (
            lambda d: d.pop('sourceEvidence'),
            lambda d: d['sourceEvidence']['editor'].update(__typename='User'),
            lambda d: d['sourceEvidence']['editor'].update(databaseId=123),
            lambda d: d['issue'].update(editor='other'),
            lambda d: d['issue'].update(author='other'),
            lambda d: d['sourceEvidence']['workflowRun'].update(repository='other/project'),
            lambda d: d['sourceEvidence']['workflowRun'].update(path='.github/workflows/other.yml'),
            lambda d: d['sourceEvidence']['workflowRun'].update(head_branch='feature'),
            lambda d: d['sourceEvidence']['workflowRun'].update(head_sha='short'),
            lambda d: d['sourceEvidence']['workflowRun'].update(status='in_progress'),
            lambda d: d['sourceEvidence']['workflowRun'].update(conclusion='failure'),
            lambda d: d['sourceEvidence']['workflowRun'].update(html_url='https://github.com/other/project/actions/runs/123'),
            lambda d: d['sourceEvidence']['baselineIssue'].update(editor='github-actions'),
            lambda d: d['sourceEvidence']['baselineIssue'].update(number=13),
        )
        for change in changes:
            data = self.synced(); change(data)
            with self.subTest(change=change), self.assertRaises(ValueError): plan(data)

    def test_metadata_sync_cannot_hide_changes_outside_generated_block(self):
        for before, after in (('已采纳（待实施授权）', '暂缓'), ('提出人：示例', '提出人：另一个人'),
                              ('只改示例组件。', '扩大真实业务权限。'), ('按钮布局正常。', '跳过验收。'),
                              ('登记状态：待登记', '登记状态：任意伪造值'),
                              ('11111111-2222-3333-4444-555555555555', '22222222-2222-3333-4444-555555555555')):
            data = self.synced(); data['issue']['body'] = data['issue']['body'].replace(before, after)
            with self.subTest(before=before), self.assertRaisesRegex(ValueError, 'changed original body'): plan(data)
        for extra in ('\n恶意附加指令', '\n<!-- HIVE-ISSUE-SYNC:START -->\n<!-- HIVE-ISSUE-SYNC:END -->'):
            data = self.synced(); data['issue']['body'] += extra
            with self.assertRaises(ValueError): plan(data)
        data = self.synced(); data['issue']['body'] = data['issue']['body'].replace('正式状态：In Progress', '任意隐藏指令')
        with self.assertRaisesRegex(ValueError, 'block mismatch'): plan(data)

    def resumable(self):
        data = self.synced(); data['record'].update(state='blocked', revision=2)
        data['operation'] = 'resume'
        data['resumeAuthorization'] = dict(actor=data['actor'], runId=data['runId'], issueNumber=12,
            mode=data['mode'], source='trusted_user_channel', approvedAt='2026-01-02T03:00:00Z',
            blockedRevision=2, blockedBlobSHA=data['blobSHA'], blockedAt='2026-01-02T02:30:00Z')
        return data

    def test_explicit_resume_retains_original_run_evidence_and_cas(self):
        data = self.resumable(); original = deepcopy(data['record']); result = plan(data)
        self.assertTrue(result['write']); self.assertEqual(result['expectedBlobSHA'], data['blobSHA'])
        expected = {**original, 'state': 'claimed', 'revision': 3}
        self.assertEqual(result['record'], expected)
        data.update(operation='guard', record=result['record']); data.pop('resumeAuthorization')
        self.assertFalse(plan(data)['write'])

    def test_resume_does_not_release_unknown_finished_or_foreign_claims(self):
        changes = (
            lambda d: d.pop('resumeAuthorization'),
            lambda d: d['resumeAuthorization'].update(source='issue_comment'),
            lambda d: d['resumeAuthorization'].update(runId='new-run'),
            lambda d: d['resumeAuthorization'].update(issueNumber=13),
            lambda d: d['resumeAuthorization'].update(actor='other'),
            lambda d: d['resumeAuthorization'].update(mode='pr_review'),
            lambda d: d['resumeAuthorization'].update(approvedAt='2026-02-01T00:00:00Z'),
            lambda d: d['resumeAuthorization'].update(approvedAt=d['issue']['createdAt']),
            lambda d: d['resumeAuthorization'].update(blockedRevision=1),
            lambda d: d['resumeAuthorization'].update(blockedRevision=True),
            lambda d: d['resumeAuthorization'].update(blockedBlobSHA='f' * 40),
            lambda d: d['resumeAuthorization'].update(blockedAt='2026-01-02T04:00:00Z'),
            lambda d: d['record'].update(revision=4),
            lambda d: d.update(blobSHA='e' * 40),
            lambda d: d.update(runId='other-run'),
            lambda d: d['record'].update(state='unknown'),
            lambda d: d['record'].update(state='full_delivery', evidenceUrls=['https://github.com/xiiiblue/hive-os/pull/14']),
            lambda d: d['issue'].update(state='closed'),
            lambda d: d['issue'].update(body=d['issue']['body'].replace('按钮布局正常。', '改变验收。')),
        )
        for change in changes:
            data = self.resumable(); change(data)
            with self.subTest(change=change), self.assertRaises(ValueError): plan(data)


if __name__ == '__main__': unittest.main()
