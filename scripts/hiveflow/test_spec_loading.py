#!/usr/bin/env python3
"""Offline contract reference/rehearsal, NOT a runtime gate or sandbox."""
import copy
import hashlib
import json
import re
import posixpath
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]


# 副本测试识别同仓同提交的上游链接；不改变真实加载器的权限或路径清单。
def export_entry_path(link):
    path = link.split('#')[0]
    if '://' in path:
        base = 'https://github.com/xiiiblue/hive-os/blob/65cfb248fd2fdd4b26cc6ce9d70ae575060ae041/'
        require(path.startswith(base), 'export link has different source')
        path = path[len(base):]
        require('..' not in PurePosixPath(path).parts, 'export link leaves source')
        return path
    return posixpath.normpath(str(PurePosixPath('docs/06-自动化', path)))
from spec_loader import (REPO, MANIFEST, COMMON, RETIRED_ENTRIES, SCENARIOS, Blocked,
                         require, digest, validate_manifest, git, local_metadata, load, selected_text)

def action(load_result, authorized=False, read_only=False, adopted=False, seen=False, unknown=False):
    # Synthetic decision only: no external write/dispatch function exists here.
    if seen:
        return 'already_processed'
    if unknown:
        return 'verify_existing_result'
    if load_result != 'success' or not authorized or read_only or adopted:
        return 'stop_execution'
    return 'within_existing_authorization'


def main():
    cases, cold_reads = [], []

    def test(name, operation, expected=None, blocked=False):
        try:
            result = operation()
            require(not blocked and (expected is None or result == expected), 'unexpected result')
        except Blocked:
            if not blocked:
                raise
        cases.append({'name': name, 'passed': True})

    with tempfile.TemporaryDirectory(prefix='hiveflow-spec-contract-') as folder:
        area = Path(folder)
        git(area, 'init', '-q')
        manifest = json.loads((ROOT / MANIFEST).read_text())
        paths = set()
        for scenario in SCENARIOS:
            paths.update(validate_manifest(manifest, scenario))
        for group in manifest.get('phases', {}).values():
            paths.update(group)
        for path in sorted(RETIRED_ENTRIES):
            test('retired_entry_absent_' + PurePosixPath(path).stem,
                 lambda retired=ROOT / path: not retired.exists() and not retired.is_symlink(), True)
        for path in paths:
            source = ROOT / path
            require(not source.is_symlink(), 'input symlink')
            target = area / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())

        def commit(message):
            git(area, 'add', '.')
            git(area, '-c', 'user.name=Offline Fixture', '-c', 'user.email=fixture@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', message)
            return git(area, 'rev-parse', 'HEAD').decode().strip()

        first = commit('fixture version one')
        git(area, 'config', 'remote.origin.url', 'https://github.com/' + REPO + '.git')
        for scenario in sorted(SCENARIOS):
            test('local_first_' + scenario, lambda route=scenario: load(area, local_metadata(area), route)['load_result'], 'success')
        local = local_metadata(area)
        test('local_wrong_head', lambda: load(area, dict(local, sha='0'*40), 'intake'), blocked=True)
        git(area, 'config', 'remote.origin.url', 'https://github.com/other/repo.git')
        test('local_wrong_origin', lambda: load(area, local, 'intake'), blocked=True)
        git(area, 'config', 'remote.origin.url', 'https://github.com/' + REPO + '.git')
        test('local_has_no_remote_default_requirement', lambda: 'default_branch' not in local and 'private' not in local, True)
        metadata = {'repo': REPO, 'private': True, 'default_branch': 'fixture-default', 'sha': first,
                    'observed_at': 'synthetic-time-UTC', 'evidence': 'synthetic-metadata-not-GitHub'}
        for scenario in sorted(SCENARIOS):
            result = load(area, metadata, scenario)
            cold_reads.append({'scenario': scenario, 'files': result['receipts']})
            test('cold_start_' + scenario, lambda: result['load_result'], 'success')
            test('canonical_only_' + scenario,
                 lambda loaded=result: COMMON <= set(loaded['contents'])
                 and not RETIRED_ENTRIES.keys() & set(loaded['contents'])
                 and all(row['sha'] == first for row in loaded['receipts']), True)
        entry_rows = (area / 'docs/06-自动化/HFL-001-规范加载入口.md').read_text().splitlines()
        for scenario, label in [('intake', '群反馈intake'), ('review', 'Issue采纳评审'), ('delivery', '研发交付')]:
            row = next(r for r in entry_rows if r.startswith('|' + label))
            listed = {export_entry_path(link) for link in re.findall(r'\]\(([^)]+)\)', row)}
            test('entry_manifest_' + scenario,
                 lambda s=scenario, paths=listed: paths == set(manifest['scenarios'][s]), True)
        cache = load(area, metadata, 'intake')
        test('same_sha_full_cache', lambda: load(area, metadata, 'intake', cache)['load_result'], 'success')
        test('intake_loads_binding_only', lambda:
             manifest['scenarios']['intake'].get('docs/06-自动化/HFL-006-研发交付与原群回执.md'), ['Hermes来源关联'])
        test('intake_excludes_execution_route', lambda:
             not {'AGENTS.md', 'docs/06-自动化/HFL-003-协作流程与授权.md'} & set(validate_manifest(manifest, 'intake')), True)
        binding_text = cache['contents']['docs/06-自动化/HFL-006-研发交付与原群回执.md'].decode().split('## Hermes来源关联\n', 1)[1].split('\n## ', 1)[0]
        test('intake_hermes_source_and_failure_contract', lambda:
             all(term in binding_text for term in ['真实原来源', '成功读回', '不再发送Slack source-bind',
                 '跨天', '未经批准', '建单未知', '私聊隔离']), True)
        workflow = load(area, metadata, 'delivery')['contents']['docs/06-自动化/HFL-003-协作流程与授权.md'].decode()
        manual_entry = workflow.split('## 本人直接Codex手动入口\n', 1)[1].split('\n## ', 1)[0]
        test('direct_manual_entry_requires_host_identity_scope', lambda:
             all(term in manual_entry for term in ['实际可信人类用户通道', '本人、权限及本次范围',
                 '不迁回Hermes重审', '根AGENTS.md']), True)

        for phase in manifest['phases']:
            test('phase_' + phase, lambda p=phase: load(area, metadata, 'delivery', phase=p)['load_result'], 'success')
        test('base_delivery_excludes_notification_protocol', lambda:
             'docs/06-自动化/HFL-007-HTTP回执接口.md' not in validate_manifest(manifest, 'delivery'), True)
        for route in ('intake', 'review'):
            test(route + '_formal_receipt_loads_notify', lambda r=route:
                 load(area, metadata, r, phase='notify')['load_result'], 'success')
        notification = load(area, metadata, 'delivery', phase='notify')
        selected = validate_manifest(manifest, 'delivery', 'notify')
        receipt_path = 'docs/06-自动化/HFL-006-研发交付与原群回执.md'
        webhook_path = 'docs/06-自动化/HFL-007-HTTP回执接口.md'
        receipt_text = selected_text(notification['contents'][receipt_path].decode(), selected[receipt_path])
        webhook_text = selected_text(notification['contents'][webhook_path].decode(), selected[webhook_path])
        test('current_receipt_does_not_load_legacy_send', lambda:
             '## 选择单一通知路径' not in receipt_text and '## GitHub交付记录' in receipt_text
             and '即时答疑与正式回执' in receipt_text, True)
        test('webhook_load_includes_mentions_and_current_recovery', lambda:
             all(t in webhook_text for t in ['文本回执与成员提醒', '当前发布操作与恢复', '--resume-notice'])
             and 'POST /v1/receipts' not in webhook_text, True)
        test('review_cannot_load_automation', lambda: load(area, metadata, 'review', phase='automation'), blocked=True)
        test('unknown_phase', lambda: load(area, metadata, 'delivery', phase='unexpected'), blocked=True)
        legacy = copy.deepcopy(manifest); legacy.pop('phases'); legacy['schema_version'] = 1
        test('schema1_read_compatibility', lambda: bool(validate_manifest(legacy, 'delivery')), True)

        for field, value in [('sha', ''), ('private', False), ('repo', 'other/spec'), ('default_branch', ''), ('evidence', '')]:
            test('metadata_' + field, lambda f=field, v=value: load(area, dict(metadata, **{f: v}), 'intake'), blocked=True)
        for fault in ['denied', 'read_failed', 'truncated', 'wrong_sha']:
            test(fault, lambda f=fault: load(area, metadata, 'intake', fault=f), blocked=True)
        test('conflict', lambda: load(area, metadata, 'intake', conflict=True), blocked=True)
        for mutation in ['partial', 'corrupt', 'scenario', 'schema', 'files', 'retired_paths']:
            altered = copy.deepcopy(cache)
            if mutation == 'partial':
                altered['contents'].pop('docs/06-自动化/HFL-004-群反馈处理规则.md')
            elif mutation == 'corrupt':
                altered['contents']['docs/06-自动化/HFL-004-群反馈处理规则.md'] = b'bad bytes'
            elif mutation == 'scenario':
                altered['key'][2] = 'review'
            elif mutation == 'schema':
                altered['key'][3] = 99
            elif mutation == 'files':
                altered['key'][4] = [MANIFEST]
            else:
                for retired, original in RETIRED_ENTRIES.items():
                    altered['contents'][retired] = altered['contents'].pop(original)
                    altered['digests'][retired] = altered['digests'].pop(original)
                altered['key'][4] = sorted(altered['contents'])
            test('cache_' + mutation, lambda c=altered: load(area, metadata, 'intake', c), blocked=True)
        for mutation in ['schema', 'missing_scenario', 'unknown_scenario', 'includes', 'outside', 'url', 'source', 'absolute']:
            altered = copy.deepcopy(manifest)
            if mutation == 'schema':
                altered['schema_version'] = 99
            elif mutation == 'missing_scenario':
                del altered['scenarios']['review']
            elif mutation == 'unknown_scenario':
                altered['scenarios']['extra'] = {'AGENTS.md': ['*']}
            elif mutation == 'includes':
                altered['includes'] = ['dynamic']
            else:
                path = {'outside': '../other.md', 'url': 'https://example.invalid/rules.md',
                        'source': 'src/business.py', 'absolute': '/tmp/rules.md'}[mutation]
                altered['scenarios']['review'][path] = ['*']
            test('manifest_' + mutation, lambda m=altered: validate_manifest(m, 'review'), blocked=True)
        test('unknown_requested_scenario', lambda: load(area, metadata, 'unknown'), blocked=True)
        for retired, current in RETIRED_ENTRIES.items():
            altered = copy.deepcopy(manifest)
            altered['common'][retired] = altered['common'].pop(current)
            test('manifest_retired_' + PurePosixPath(retired).stem,
                 lambda old_manifest=altered: validate_manifest(old_manifest, 'intake'), blocked=True)
        sample = area / 'docs/06-自动化/HFL-004-群反馈处理规则.md'
        original = sample.read_bytes()
        sample.unlink()
        absent = commit('missing file fixture')
        test('missing_file', lambda: load(area, dict(metadata, sha=absent), 'intake'), blocked=True)
        sample.symlink_to('HFL-001-规范加载入口.md')
        linked = commit('symlink fixture')
        test('symlink', lambda: load(area, dict(metadata, sha=linked), 'intake'), blocked=True)
        sample.unlink()
        sample.write_bytes(original)
        changed = copy.deepcopy(manifest)
        changed['scenarios']['intake']['docs/06-自动化/HFL-004-群反馈处理规则.md'] = ['missing heading']
        (area / MANIFEST).write_text(json.dumps(changed))
        missing = commit('missing section fixture')
        test('missing_heading', lambda: load(area, dict(metadata, sha=missing), 'intake'), blocked=True)
        (area / MANIFEST).write_text(json.dumps(manifest))
        entry = area / 'docs/06-自动化/HFL-001-规范加载入口.md'
        entry.write_text(entry.read_text() + '\n合成新版本：不产生额外权限。\n')
        second = commit('fixture version two')
        current = dict(metadata, sha=second)
        test('version_changed_old_cache', lambda: load(area, current, 'intake', cache), blocked=True)
        test('no_fallback_on_failure', lambda: load(area, current, 'intake', cache, fault='denied'), blocked=True)
        fresh = load(area, current, 'intake')
        test('version_changed_reload', lambda: fresh['key'][1], second)
        test('new_version_no_authority', lambda: action(fresh['load_result']), 'stop_execution')
        test('loaded_event_not_processed', lambda: fresh['event_result'], 'unprocessed')
        # Stable event key deliberately contains no specification SHA.
        event_key = ('synthetic-source', 'synthetic-event')
        completed = {event_key}
        for sha in [first, second]:
            test('duplicate_' + sha[:8], lambda: action('success', authorized=True, seen=event_key in completed), 'already_processed')
        test('unknown_write', lambda: action('success', authorized=True, unknown=True), 'verify_existing_result')
        test('read_only', lambda: action('success', authorized=True, read_only=True), 'stop_execution')
        test('adoption_only', lambda: action('success', authorized=True, adopted=True), 'stop_execution')
        test('failed_load_with_authority', lambda: action('failed', authorized=True), 'stop_execution')
        test('existing_authority', lambda: action('success', authorized=True), 'within_existing_authorization')
        report = {'scope': 'S offline synthetic contract reference; no platform enforcement tested',
                  'source_sha': git(ROOT, 'rev-parse', 'HEAD').decode().strip(),
                  'input_note': 'working-tree bytes copied; source_sha alone does not identify uncommitted inputs',
                  'fixture_versions': [first, second], 'cold_reads': cold_reads, 'cases': cases,
                  'result': 'passed_offline_only', 'count': len(cases)}
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
