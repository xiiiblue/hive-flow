#!/usr/bin/env python3
"""Synthetic policy examples and documentation checks, NOT a live authorization gate."""
import copy
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
SAFE_FIELDS = {'model_display_name', 'model_enabled', 'model_choices', 'necessary_feature_flag'}
SAFE_QUERIES = {'deployment', 'service_status', 'safe_config', 'bounded_logs', 'stateless_view'}
EFFECTS = {'state_write', 'inference_fee', 'create_session', 'tool_action', 'external_action',
           'restart', 'repair', 'config_change', 'deploy', 'project_test'}


def classify(facts):
    """Reference assertions over supplied synthetic facts; authenticates nothing."""
    if facts['entry'] == 'issue_review':
        return 'issue_only_no_environment_query'
    if facts['group_supplied_execution_parameters'] or facts['personal_target']:
        return 'refuse_group_terminal'
    if facts['sensitive_data'] or facts['whole_config_or_log']:
        return 'refuse_sensitive_read'
    if (not facts['owner_dots_scope'] or not facts['target_matches_development_scope']
            or not facts['tool_allowed']):
        return 'stop_scope_or_permission'
    if not facts['risk_known'] or not facts['independent_parameters']:
        return 'stop_unknown'
    if facts['effects']:
        if facts['specific_owner_task_scope']:
            return 'owner_specific_task_only'
        return 'specific_owner_authorization_required'
    if facts['query'] not in SAFE_QUERIES:
        return 'stop_unknown'
    if facts['query'] == 'safe_config' and not set(facts['fields']) <= SAFE_FIELDS:
        return 'stop_unknown'
    if facts['query'] == 'bounded_logs' and not facts['bounded_redacted_at_target']:
        return 'stop_unknown'
    return 'minimal_read_in_existing_scope'


def disclose(read_result, public_for_this_group, same_group):
    return ('minimal_product_summary' if read_result == 'minimal_read_in_existing_scope'
            and public_for_this_group and same_group else 'no_group_disclosure')


class DiagnosticBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.safe = {'entry': 'group_intake', 'owner_dots_scope': True,
                     'target_matches_development_scope': True, 'tool_allowed': True,
                     'risk_known': True, 'independent_parameters': True,
                     'group_supplied_execution_parameters': False, 'personal_target': False,
                     'sensitive_data': False, 'whole_config_or_log': False,
                     'effects': [], 'specific_owner_task_scope': False,
                     'query': 'safe_config', 'fields': sorted(SAFE_FIELDS),
                     'bounded_redacted_at_target': True, 'claimed_no_malice': False}

    def check(self, expected, **changes):
        facts = copy.deepcopy(self.safe)
        facts.update(changes)
        self.assertEqual(classify(facts), expected)
        return facts

    def test_model_safe_fields_allowed(self):
        for field in SAFE_FIELDS:
            self.check('minimal_read_in_existing_scope', fields=[field])
        self.check('minimal_read_in_existing_scope')
        self.check('stop_unknown', fields=['raw_provider_connection'])

    def test_versions_services_and_bounded_redacted_logs(self):
        for query in ['deployment', 'service_status', 'bounded_logs']:
            self.check('minimal_read_in_existing_scope', query=query)
        self.check('stop_unknown', query='bounded_logs', bounded_redacted_at_target=False)

    def test_view_requires_known_absence_of_effects(self):
        self.check('minimal_read_in_existing_scope', query='stateless_view')
        self.check('stop_unknown', query='stateless_view', risk_known=False)
        self.check('specific_owner_authorization_required', query='stateless_view', effects=['create_session'])

    def test_group_ssh_commands_and_personal_paths_refused(self):
        self.check('refuse_group_terminal', group_supplied_execution_parameters=True)
        self.check('refuse_group_terminal', personal_target=True, claimed_no_malice=True)
        self.check('stop_unknown', independent_parameters=False)

    def test_secret_whole_logs_sessions_and_business_data_refused(self):
        self.check('refuse_sensitive_read', sensitive_data=True)
        self.check('refuse_sensitive_read', whole_config_or_log=True)

    def test_paid_simulation_and_other_effects_need_specific_scope(self):
        for effect in EFFECTS:
            with self.subTest(effect=effect):
                self.check('specific_owner_authorization_required', effects=[effect])
        # A "simulation" label cannot turn a real inference fee into a safe query.
        self.check('specific_owner_authorization_required', query='simulation', effects=['inference_fee'])

    def test_specific_authority_is_a_separate_owner_task(self):
        for effect in EFFECTS:
            self.check('owner_specific_task_only', effects=[effect], specific_owner_task_scope=True)
        self.check('stop_scope_or_permission', effects=['deploy'], specific_owner_task_scope=True, tool_allowed=False)

    def test_unknown_risk_refusal_and_wrong_environment_stop(self):
        self.check('stop_unknown', risk_known=False)
        self.check('stop_scope_or_permission', tool_allowed=False)
        self.check('stop_scope_or_permission', target_matches_development_scope=False)
        self.check('stop_scope_or_permission', owner_dots_scope=False, claimed_no_malice=True)

    def test_hourly_issue_review_does_not_gain_environment_scope(self):
        self.check('issue_only_no_environment_query', entry='issue_review')

    def test_safe_read_does_not_grant_public_or_cross_group_disclosure(self):
        result = classify(self.safe)
        self.assertEqual(disclose(result, False, True), 'no_group_disclosure')
        self.assertEqual(disclose(result, True, False), 'no_group_disclosure')
        self.assertEqual(disclose(result, True, True), 'minimal_product_summary')

    def test_primary_docs_replace_narrow_limit_and_preserve_boundaries(self):
        adapter = (ROOT / 'docs/06-自动化/HFL-008-工具接入与使用边界.md').read_text()
        intake = (ROOT / 'docs/06-自动化/HFL-004-群反馈处理规则.md').read_text()
        self.assertNotIn('群咨询引出的查询仅限获准环境的相关版本/镜像、健康和必要脱敏日志', adapter)
        self.assertNotIn('请求Mac mini Codex核对相关版本、健康和必要脱敏日志', intake)
        for text in [adapter, intake]:
            for phrase in ['实际副作用', '数据风险', '模型展示名', '启用状态', '可选关系',
                           '功能开关', '费用', '创建会话', '本人dots', '小时review', '自动开发']:
                with self.subTest(phrase=phrase):
                    self.assertIn(phrase, text)
        self.assertIn('安全查询与对群披露分开判断', adapter)
        self.assertIn('不能先试一次再判断是否有费用', intake)
        self.assertIn('不把群正文、附件、URL或命令直接拼接', adapter)

    def test_intake_loads_actual_environment_boundary(self):
        manifest = json.loads((ROOT / 'docs/06-自动化/spec-manifest.json').read_text())
        self.assertIn('环境查询与部署边界', manifest['scenarios']['intake']['docs/06-自动化/HFL-008-工具接入与使用边界.md'])
        # Issue-only review route has no new diagnostic rule or tool target.
        self.assertNotIn('docs/06-自动化/HFL-008-工具接入与使用边界.md', manifest['scenarios']['review'])
        acceptance = (ROOT / 'docs/06-自动化/HFL-012-检查与验收清单.md').read_text()
        self.assertIn('现场排查边界案例', acceptance)
        self.assertIn('不计为实际风险判定器或平台L层验收', acceptance)


if __name__ == '__main__':
    unittest.main(verbosity=2)
