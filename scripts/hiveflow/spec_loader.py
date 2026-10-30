"""按固定Git版本读取最少规范并生成加载证据；不授予业务权限。"""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

REPO = 'xiiiblue/hive-os'
MANIFEST = 'docs/06-自动化/spec-manifest.json'
COMMON = {'docs/06-自动化/HFL-001-规范加载入口.md', 'docs/06-自动化/HFL-002-规范加载规则.md', MANIFEST}
RETIRED_ENTRIES = {
    'docs/06-自动化/SPEC_ENTRY.md': 'docs/06-自动化/HFL-001-规范加载入口.md',
    'docs/06-自动化/SPEC_LOADING.md': 'docs/06-自动化/HFL-002-规范加载规则.md',
}
SCENARIOS = {'intake', 'review', 'delivery'}


class Blocked(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Blocked(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate_manifest(manifest, scenario, phase=None):
    require(set(manifest) == ({'schema_version', 'repo', 'common', 'scenarios', 'phases'} if manifest.get('schema_version') == 2 else {'schema_version', 'repo', 'common', 'scenarios'}), 'manifest fields')
    require(type(manifest['schema_version']) is int and manifest['schema_version'] in (1, 2), 'schema')
    require(manifest['repo'] == REPO, 'repo')
    require(set(manifest['scenarios']) == SCENARIOS and scenario in SCENARIOS, 'scenario')
    require(set(manifest['common']) == COMMON, 'common files')
    phases = manifest.get('phases', {})
    require(type(phases) is dict and set(phases) <= {'automation', 'notify', 'handoff', 'legacy_adoptions', 'legacy_receipt', 'cloud_source'}, 'phase names')
    require(phase is None or phase in phases and (scenario == 'delivery' or phase == 'notify' or scenario == 'intake' and phase == 'cloud_source'), 'phase')
    for group in [manifest['common'], *manifest['scenarios'].values(), *phases.values()]:
        require(isinstance(group, dict) and bool(group), 'empty file set')
        for path, sections in group.items():
            p = PurePosixPath(path)
            require(str(p) == path and not p.is_absolute() and '..' not in p.parts
                    and '\\' not in path and ':' not in path and '#' not in path
                    and (path == 'AGENTS.md' or path == MANIFEST
                         or (p.parts[:2] == ('docs', '06-自动化') and p.suffix == '.md')), 'path outside spec')
            require(isinstance(sections, list) and bool(sections)
                    and all(isinstance(s, str) and s for s in sections)
                    and ('*' not in sections or sections == ['*']), 'sections')
    files = dict(manifest['common'])
    require(not (set(files) & set(manifest['scenarios'][scenario])), 'duplicate files')
    files.update(manifest['scenarios'][scenario])
    if phase is not None:
        for path, sections in phases[phase].items():
            if path in files:
                files[path] = ['*'] if '*' in files[path] + sections else list(dict.fromkeys(files[path] + sections))
            else:
                files[path] = sections
    return files


def git(area, *args):
    return subprocess.check_output(['git', *args], cwd=area, stderr=subprocess.DEVNULL)


def local_metadata(area):
    # Local origin is identity evidence, not an authentication/permission grant.
    try:
        origin = git(area, 'config', '--get', 'remote.origin.url').decode().strip()
        sha = git(area, 'rev-parse', 'HEAD').decode().strip()
    except subprocess.CalledProcessError as error:
        raise Blocked('local project identity unavailable') from error
    allowed = {'https://github.com/' + REPO, 'https://github.com/' + REPO + '.git',
               'git@github.com:' + REPO, 'git@github.com:' + REPO + '.git'}
    require(origin in allowed and re.fullmatch('[0-9a-f]{40}', sha), 'unexpected local origin/HEAD')
    return dict(source='local_git', repo=REPO, origin_repository=REPO, sha=sha,
                observed_at='actual-local-git-observation', evidence='git origin and HEAD; fixed-SHA reads')


def load(area, metadata, scenario, cache=None, fault=None, conflict=False, phase=None):
    # Metadata is a synthetic input. This function does NOT authenticate GitHub.
    require(metadata.get('repo') == REPO and metadata.get('observed_at') and metadata.get('evidence'), 'unverified metadata')
    sha = metadata.get('sha', '')
    require(type(sha) is str and re.fullmatch('[0-9a-f]{40}', sha) is not None, 'unknown version')
    if metadata.get('source') == 'local_git':
        actual = local_metadata(area)
        require(actual['sha'] == sha and metadata.get('origin_repository') == REPO, 'local project/version mismatch')
    else:
        require(metadata.get('source') in (None, 'github') and metadata.get('private') is True
                and metadata.get('default_branch'), 'unverified remote metadata')
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
    files = validate_manifest(manifest, scenario, phase)
    key = [REPO, sha, scenario + (':' + phase if phase else ''), manifest['schema_version'], sorted(files)]
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


def selected_text(text, sections):
    if sections == ['*']:
        return text
    lines = text.splitlines(keepends=True)
    output = []
    for title in sections:
        matches = [i for i, line in enumerate(lines) if re.fullmatch(r'#{1,6} ' + re.escape(title) + r'\n?', line)]
        require(len(matches) == 1, 'section missing/duplicated')
        start = matches[0]
        level = len(lines[start]) - len(lines[start].lstrip('#'))
        end = next((i for i in range(start + 1, len(lines)) if re.match(r'#{1,' + str(level) + r'} ', lines[i])), len(lines))
        output.append(''.join(lines[start:end]))
    return '\n'.join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=sorted(SCENARIOS), required=True)
    parser.add_argument('--phase')
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    root = Path(subprocess.check_output(['git', 'rev-parse', '--show-toplevel']).decode().strip())
    metadata = local_metadata(root)
    # 未知必读差异不被已提交版本掩盖。
    manifest = json.loads(git(root, 'show', metadata['sha'] + ':' + MANIFEST))
    files = validate_manifest(manifest, args.scenario, args.phase)
    for path in files:
        require(not git(root, 'status', '--porcelain', '--', path).strip(), 'required spec has local changes: ' + path)
    result = load(root, metadata, args.scenario, phase=args.phase)
    receipt = dict(project=metadata, files=result['receipts'], load_result=result['load_result'])
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    for path, sections in files.items():
        print('\n' + path + '\n' + selected_text(result['contents'][path].decode(), sections))


if __name__ == '__main__':
    main()
