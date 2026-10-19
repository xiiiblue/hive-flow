#!/usr/bin/env python3
"""Offline contract reference/rehearsal, NOT a runtime gate or sandbox."""
import copy
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
REPO = 'xiiiblue/hive-flow'
MANIFEST = 'docs/spec-manifest.json'
COMMON = {'docs/SPEC_ENTRY.md', 'docs/SPEC_LOADING.md', MANIFEST}
SCENARIOS = {'intake', 'review', 'delivery'}


class Blocked(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_manifest(manifest, scenario):
    require(set(manifest) == {'schema_version', 'repo', 'common', 'scenarios'}, 'manifest fields')
    require(type(manifest['schema_version']) is int and manifest['schema_version'] == 1, 'schema')
    require(manifest['repo'] == REPO, 'repo')
    require(set(manifest['scenarios']) == SCENARIOS and scenario in SCENARIOS, 'scenario')
    require(set(manifest['common']) == COMMON, 'common files')
    for group in [manifest['common'], *manifest['scenarios'].values()]:
        require(isinstance(group, dict) and bool(group), 'empty file set')
        for path, sections in group.items():
            p = PurePosixPath(path)
            require(str(p) == path and not p.is_absolute() and '..' not in p.parts
                    and '\\' not in path and ':' not in path and '#' not in path
                    and (path == 'AGENTS.md' or path == MANIFEST
                         or (p.parts[0] == 'docs' and p.suffix == '.md')), 'path outside spec')
            require(isinstance(sections, list) and bool(sections)
                    and all(isinstance(s, str) and s for s in sections)
                    and ('*' not in sections or sections == ['*']), 'sections')
    files = dict(manifest['common'])
    require(not (set(files) & set(manifest['scenarios'][scenario])), 'duplicate files')
    files.update(manifest['scenarios'][scenario])
    return files


def git(area, *args):
    return subprocess.check_output(['git', *args], cwd=area, stderr=subprocess.DEVNULL)


def load(area, metadata, scenario, cache=None, fault=None, conflict=False):
    # Metadata is a synthetic input. This function does NOT authenticate GitHub.
    require(metadata.get('repo') == REPO and metadata.get('private') is True
            and metadata.get('default_branch') and metadata.get('observed_at')
            and metadata.get('evidence'), 'unverified metadata')
    sha = metadata.get('sha', '')
    require(re.fullmatch('[0-9a-f]{40}', sha) is not None, 'unknown version')
    require(not conflict, 'conflict')

    def read(path):
        require(fault not in {'denied', 'read_failed'}, 'read blocked')
        try:
            row = git(area, 'ls-tree', sha, '--', path).decode().split()
            require(bool(row) and row[0] == '100644', 'missing file, symlink or unexpected object')
            data = git(area, 'show', f'{sha}:{path}')
        except subprocess.CalledProcessError as error:
            raise Blocked('fixed SHA read failed') from error
        require(fault != 'truncated', 'incomplete response')
        require(fault != 'wrong_sha', 'response version mismatch')
        return data

    manifest = json.loads(read(MANIFEST))
    files = validate_manifest(manifest, scenario)
    key = [REPO, sha, scenario, manifest['schema_version'], sorted(files)]
    if cache is not None:
        require(cache['key'] == key and set(cache['contents']) == set(files), 'cache identity/set')
    contents, receipts = {}, []
    for path, sections in files.items():
        # Opening cached full bytes models actual content retrieval, not a read flag.
        data = cache['contents'][path] if cache is not None else read(path)
        require(isinstance(data, bytes) and bool(data), 'empty content')
        if cache is not None:
            require(cache['digests'][path] == digest(data), 'cache integrity')
        text = data.decode('utf-8')
        headings = re.findall(r'^#{1,6} (.+)$', text, re.M)
        require(sections == ['*'] or all(s in headings for s in sections), 'missing section')
        contents[path] = data
        receipts.append({'sha': sha, 'path': path, 'sections': sections, 'sha256': digest(data),
                         'source': 'cache' if cache is not None else 'fixed_sha_git_show', 'result': 'complete'})
    return {'key': key, 'contents': contents, 'digests': {p: digest(d) for p, d in contents.items()},
            'receipts': receipts, 'load_result': 'success', 'event_result': 'unprocessed'}


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
        metadata = {'repo': REPO, 'private': True, 'default_branch': 'fixture-default', 'sha': first,
                    'observed_at': 'synthetic-time-UTC', 'evidence': 'synthetic-metadata-not-GitHub'}
        for scenario in sorted(SCENARIOS):
            result = load(area, metadata, scenario)
            cold_reads.append({'scenario': scenario, 'files': result['receipts']})
            test('cold_start_' + scenario, lambda: result['load_result'], 'success')
        entry_rows = (area / 'docs/SPEC_ENTRY.md').read_text().splitlines()
        for scenario, label in [('intake', '群反馈 intake'), ('review', 'Issue 采纳评审'), ('delivery', '研发交付')]:
            row = next(r for r in entry_rows if r.startswith('|' + label))
            listed = {str(PurePosixPath('docs', link.split('#')[0])) for link in re.findall(r'\]\(([^)]+)\)', row)}
            listed = {'AGENTS.md' if p == 'docs/../AGENTS.md' else p for p in listed}
            test('entry_manifest_' + scenario,
                 lambda s=scenario, paths=listed: paths == set(manifest['scenarios'][s]), True)
        cache = load(area, metadata, 'intake')
        test('same_sha_full_cache', lambda: load(area, metadata, 'intake', cache)['load_result'], 'success')
        test('intake_loads_binding_only', lambda:
             manifest['scenarios']['intake'].get('docs/MANUAL_DELIVERY.md'), ['先绑定真实来源'])
        test('intake_excludes_execution_route', lambda:
             not {'AGENTS.md', 'docs/WORKFLOW.md'} & set(validate_manifest(manifest, 'intake')), True)
        binding_text = cache['contents']['docs/MANUAL_DELIVERY.md'].decode().split('## 先绑定真实来源\n', 1)[1].split('\n## ', 1)[0]
        test('intake_binding_ack_and_failure_contract', lambda:
             all(term in binding_text for term in ['request 到期前', '[wecom:source-bind]',
                 'source_bound |', '失败或未知', '不能声称可跨天回执', 'source-binding-status',
                 'binding_active=1', '真实 bridge App']), True)
        workflow = load(area, metadata, 'delivery')['contents']['docs/WORKFLOW.md'].decode()
        manual_entry = workflow.split('## 本人直接 Codex 手动入口\n', 1)[1].split('\n## ', 1)[0]
        test('direct_manual_entry_requires_host_identity_scope', lambda:
             all(term in manual_entry for term in ['本地执行宿主', '本人身份/权限可核验', '完整范围',
                 '无须迁回 dots 重新批准', 'Issue/PR 评论', 'dots 不能替外部会话',
                 '仅消费 GitHub 交付证据', '不开启自动开发']), True)

        for field, value in [('sha', ''), ('private', False), ('repo', 'other/spec'), ('default_branch', ''), ('evidence', '')]:
            test('metadata_' + field, lambda f=field, v=value: load(area, dict(metadata, **{f: v}), 'intake'), blocked=True)
        for fault in ['denied', 'read_failed', 'truncated', 'wrong_sha']:
            test(fault, lambda f=fault: load(area, metadata, 'intake', fault=f), blocked=True)
        test('conflict', lambda: load(area, metadata, 'intake', conflict=True), blocked=True)
        for mutation in ['partial', 'corrupt', 'scenario', 'schema', 'files']:
            altered = copy.deepcopy(cache)
            if mutation == 'partial':
                altered['contents'].pop('docs/GROUP_INTAKE.md')
            elif mutation == 'corrupt':
                altered['contents']['docs/GROUP_INTAKE.md'] = b'bad bytes'
            elif mutation == 'scenario':
                altered['key'][2] = 'review'
            elif mutation == 'schema':
                altered['key'][3] = 99
            else:
                altered['key'][4] = [MANIFEST]
            test('cache_' + mutation, lambda c=altered: load(area, metadata, 'intake', c), blocked=True)
        for mutation in ['schema', 'missing_scenario', 'unknown_scenario', 'includes', 'outside', 'url', 'source', 'absolute']:
            altered = copy.deepcopy(manifest)
            if mutation == 'schema':
                altered['schema_version'] = 2
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
        sample = area / 'docs/GROUP_INTAKE.md'
        original = sample.read_bytes()
        sample.unlink()
        absent = commit('missing file fixture')
        test('missing_file', lambda: load(area, dict(metadata, sha=absent), 'intake'), blocked=True)
        sample.symlink_to('SPEC_ENTRY.md')
        linked = commit('symlink fixture')
        test('symlink', lambda: load(area, dict(metadata, sha=linked), 'intake'), blocked=True)
        sample.unlink()
        sample.write_bytes(original)
        changed = copy.deepcopy(manifest)
        changed['scenarios']['intake']['docs/GROUP_INTAKE.md'] = ['missing heading']
        (area / MANIFEST).write_text(json.dumps(changed))
        missing = commit('missing section fixture')
        test('missing_heading', lambda: load(area, dict(metadata, sha=missing), 'intake'), blocked=True)
        (area / MANIFEST).write_text(json.dumps(manifest))
        entry = area / 'docs/SPEC_ENTRY.md'
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
