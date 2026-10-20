#!/usr/bin/env python3
"""从本地HiveOS的完整提交更新固定范围副本；不联网，不读取工作区运行文件。"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'xiiiblue/hive-os'
PREFIXES = ('docs/06-自动化/', 'docs/11-手册/05-自动化手册/', 'scripts/hiveflow/')
MANIFEST = 'export-manifest.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def allowed_path(name):
    if not isinstance(name, str):
        return False
    path = PurePosixPath(name)
    return (not path.is_absolute() and '..' not in path.parts and
            name.startswith(PREFIXES) and len(path.parts) == (4 if name.startswith(PREFIXES[1]) else 3) and
            (path.suffix in ('.md', '.json', '.py', '.mjs') or path.name == '.gitignore'))


def validate_export(root, manifest):
    """只核对当前副本，不把本地摘要当成上游来源认证。"""
    errors = []
    if not isinstance(manifest, dict):
        return ['来源清单必须是JSON对象']
    if (manifest.get('schemaVersion') != 1 or manifest.get('repository') != REPOSITORY or
            not isinstance(manifest.get('commit'), str) or
            not re.fullmatch(r'[0-9a-f]{40}', manifest['commit']) or
            manifest.get('includeRoots') != list(PREFIXES) or not isinstance(manifest.get('files'), list)):
        return ['来源清单结构或仓库身份不符']
    seen = set()
    for row in manifest['files']:
        if not isinstance(row, dict):
            errors.append('文件记录必须是JSON对象')
            continue
        name = row.get('path', '')
        if not allowed_path(name) or name in seen:
            errors.append('清单路径越界或重复')
            continue
        seen.add(name)
        if any(not isinstance(row.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', row[key])
               for key in ('sourceSHA256', 'exportSHA256')):
            errors.append(f'{name}：摘要格式不符')
            continue
        path = root / name
        if any(p.is_symlink() for p in [path, *path.parents]) or not path.resolve().is_relative_to(root.resolve()):
            errors.append(f'{name}：不接受符号链接或仓库外文件')
        elif not path.is_file() or digest(path.read_bytes()) != row['exportSHA256']:
            errors.append(f'{name}：文件缺失或与导出摘要不一致')
    if not seen:
        errors.append('来源清单为空')
    actual = {p.relative_to(root).as_posix() for prefix in PREFIXES for p in (root / prefix).rglob('*')
              if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    if actual != seen:
        errors.append('导出目录存在清单外文件或缺失文件')
    return errors


def adapt(name, raw, commit, included):
    """修正导航、来源上下文和测试路径识别；业务规则及业务工具保持原文。"""
    base = 'https://github.com/' + REPOSITORY + '/blob/' + commit + '/'
    if name == 'scripts/hiveflow/test_spec_loading.py':
        text = raw.decode('utf-8')
        marker = 'ROOT = Path(__file__).resolve().parents[2]\n'
        helper = f'''\n\n# 副本测试识别同仓同提交的上游链接；不改变真实加载器的权限或路径清单。
def export_entry_path(link):
    path = link.split('#')[0]
    if '://' in path:
        base = {base!r}
        require(path.startswith(base), 'export link has different source')
        path = path[len(base):]
        require('..' not in PurePosixPath(path).parts, 'export link leaves source')
        return path
    return posixpath.normpath(str(PurePosixPath('docs/06-自动化', path)))
'''
        old = "            listed = {str(PurePosixPath('docs/06-自动化', link.split('#')[0])) for link in re.findall(r'\\]\\(([^)]+)\\)', row)}\n            listed = {posixpath.normpath(p) for p in listed}"
        assert marker in text and old in text, '上游加载测试变化，需要重新审阅适配'
        text = text.replace(marker, marker + helper, 1)
        text = text.replace(old, "            listed = {export_entry_path(link) for link in re.findall(r'\\]\\(([^)]+)\\)', row)}", 1)
        return text.encode('utf-8'), ['pinned-entry-test-links']
    if not name.endswith('.md'):
        return raw, []
    text = raw.decode('utf-8')
    changes = []

    def link(match):
        target = match.group(1)
        if re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', target) or target.startswith(('#', '//')):
            return match.group(0)
        relative, sep, anchor = target.partition('#')
        parts = list(PurePosixPath(name).parent.parts)
        for part in relative.split('/'):
            if part == '..':
                if not parts:
                    raise ValueError('源文档链接越过仓库根目录')
                parts.pop()
            elif part not in ('', '.'):
                parts.append(part)
        destination = '/'.join(parts)
        if destination not in included:
            if 'pinned-upstream-links' not in changes:
                changes.append('pinned-upstream-links')
            return '](' + base + destination + (sep + anchor if sep else '') + ')'
        return match.group(0)

    text = re.sub(r'\]\(([^)]+)\)', link, text)
    if name == 'docs/06-自动化/README.md':
        old = '本目录与HiveOS业务同仓，是HiveFlow的唯一权威来源。'
        assert old in text, '上游目录说明变化，需要重新审阅副本说明'
        text = text.replace(old, '本目录是HiveOS现行规范的固定版本副本；唯一权威来源仍为HiveOS的`docs/06-自动化/`，本仓库不作为业务任务读取来源。')
        changes.append('export-context')
    if name == 'docs/11-手册/05-自动化手册/README.md':
        assert '本手册不代表网站的实时版本。' in text, '上游手册状态说明变化，需要重新审阅'
    if '51.8' in text:
        text = text.replace('51.8', '示例开发环境')
        changes.append('public-example-environment')
    return text.encode('utf-8'), changes


def plan(source, commit):
    git_env = dict(os.environ, GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], env=git_env)
    origin = git('remote', 'get-url', 'origin').decode().strip()
    if not re.fullmatch(r'(?:https://github\.com/|git@github\.com:)' + REPOSITORY + r'(?:\.git)?/?', origin):
        raise ValueError('源仓库origin不符；未输出实际地址')
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or git('rev-parse', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('必须提供已有的完整提交SHA')
    names = [n for n in git('ls-tree', '-r', '--name-only', '-z', commit, '--', *PREFIXES).decode().split('\0') if n]
    if not names or any(not allowed_path(n) for n in names):
        raise ValueError('源目录出现未审阅的文件类型或子目录')
    included = set(names)
    files, rows = {}, []
    for name in sorted(names):
        mode = git('ls-tree', commit, '--', name).decode().split()[0]
        if mode not in ('100644', '100755'):
            raise ValueError('源文件不是普通文件')
        raw = git('show', commit + ':' + name)
        output, changes = adapt(name, raw, commit, included)
        files[name] = output
        rows.append({'path': name, 'sourceSHA256': digest(raw), 'exportSHA256': digest(output), 'adaptations': changes})
    manifest = {'schemaVersion': 1, 'repository': REPOSITORY, 'commit': commit,
                'exportedOn': datetime.date.today().isoformat(), 'includeRoots': list(PREFIXES),
                'purpose': 'Apache-2.0公开参考版；来源实例的真实任务仍从HiveOS加载；不启用业务运行', 'files': rows}
    return files, manifest


def sync(root, files, manifest, check=False):
    previous_path = root / MANIFEST
    if previous_path.is_symlink():
        raise ValueError('来源清单不能为符号链接')
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    if check:
        if previous is None or previous.get('commit') != manifest['commit'] or previous.get('files') != manifest['files']:
            raise ValueError('来源或导出计划与当前副本不同')
        errors = validate_export(root, previous)
        if errors:
            raise ValueError('\n'.join(errors))
        return
    if previous is not None:
        errors = validate_export(root, previous)
        if errors:
            raise ValueError('先处理已有副本差异，未覆盖：\n' + '\n'.join(errors))
    previous_names = {r['path'] for r in previous['files']} if previous else set()
    for name in files:
        target = root / name
        if (any(p.is_symlink() for p in [target, *target.parents]) or
                not target.resolve().is_relative_to(root.resolve())):
            raise ValueError('目标不是仓库内普通文件')
        if target.exists() and name not in previous_names:
            raise ValueError('目标已有未管理文件，未覆盖：' + name)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in previous_names - files.keys():
        (root / name).unlink()
    previous_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='本地HiveOS仓库，只读')
    parser.add_argument('--commit', required=True, help='经选择的完整40位提交')
    parser.add_argument('--check', action='store_true', help='核对来源和导出结果，不写文件')
    args = parser.parse_args()
    try:
        files, manifest = plan(args.source, args.commit)
        sync(ROOT, files, manifest, args.check)
    except (ValueError, AssertionError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'同步停止：{error}\n')
    print(f"{'核对' if args.check else '同步'}通过：{len(files)}份文件，来源{args.commit}。未联网、未修改上游或启用任务。")


if __name__ == '__main__':
    main()
