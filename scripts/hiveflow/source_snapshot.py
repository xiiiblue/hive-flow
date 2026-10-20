#!/usr/bin/env python3
"""Offline, text-only Git data snapshot helper. Never fetches or executes code."""
import argparse
import base64
import binascii
import contextlib
import fcntl
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import uuid

SCHEMA = 1
FILTER_POLICY_VERSION = 2
MAX_FILE = 2 * 1024 * 1024
MAX_INPUT = 64 * 1024 * 1024
MARKER = {'tool': 'hiveflow-filtered-source-snapshot', 'schema_version': SCHEMA}
SHA = re.compile(r'[0-9a-f]{40}')
TEXT_EXT = {'.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.json', '.yaml', '.yml',
            '.toml', '.md', '.txt', '.html', '.css', '.scss', '.sql', '.sh', '.bash', '.ini',
            '.cfg', '.xml', '.properties', '.vue', '.svelte', '.go', '.rs', '.java', '.kt',
            '.c', '.h', '.cpp', '.hpp', '.rb', '.php', '.proto', '.graphql', '.svg',
            '.dockerfile', '.conf', '.service', '.timer', '.mmd'}
TEXT_NAMES = {'dockerfile', 'makefile', 'license', '.gitignore', '.editorconfig', '.gitattributes', '.dockerignore', '.nvmrc'}
EXCLUSION_REASONS = {'symlink', 'submodule', 'secret_or_auth_path', 'oversized',
                     'pending_review_unknown_format', 'binary_or_non_utf8', 'secret_pattern',
                     'pending_review_secret_literal', 'binary_format', 'generated_or_runtime_data'}


class SnapshotError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise SnapshotError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()


def hash256(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def read_json(path):
    need(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_INPUT,
         'JSON input must be a bounded regular file')
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                need(key not in result, 'duplicate JSON key')
                result[key] = value
            return result
        return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SnapshotError('invalid JSON input') from error


def safe_path(path):
    need(isinstance(path, str) and bool(path), 'invalid path')
    p = PurePosixPath(path)
    need(str(p) == path and not p.is_absolute() and '..' not in p.parts
         and all(x.lower() not in {'', '.', '.git'} for x in p.parts)
         and not any(ord(c) < 32 or ord(c) == 127 or c in '\\:' for c in path), 'unsafe path')
    # NFC and case collisions are refused to behave consistently on Mac/Linux.
    import unicodedata
    need(unicodedata.normalize('NFC', path) == path, 'non-normalized path')
    return p


def inventory_check(inv):
    need(set(inv) == {'schema_version', 'repo', 'private', 'default_branch', 'observed_at',
                      'commit', 'tree_sha', 'tree_complete', 'entries'}, 'inventory fields')
    need(type(inv['schema_version']) is int and inv['schema_version'] == SCHEMA, 'inventory schema')
    need(isinstance(inv['repo'], str) and re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', inv['repo']) and all(p not in {'.', '..'} for p in inv['repo'].split('/')), 'repo')
    need(inv['private'] is True and inv['tree_complete'] is True, 'unverified/incomplete tree')
    need(isinstance(inv['default_branch'], str) and bool(inv['default_branch'])
         and isinstance(inv['observed_at'], str) and bool(inv['observed_at']), 'missing observation')
    need(isinstance(inv['commit'], str) and SHA.fullmatch(inv['commit'])
         and isinstance(inv['tree_sha'], str) and SHA.fullmatch(inv['tree_sha']), 'fixed version required')
    need(isinstance(inv['entries'], list), 'entries')
    entries, folded = {}, set()
    for entry in inv['entries']:
        need(isinstance(entry, dict) and set(entry) <= {'path', 'mode', 'type', 'sha', 'size'}
             and {'path', 'mode', 'type', 'sha'} <= set(entry), 'tree entry fields')
        path = str(safe_path(entry['path']))
        need(path not in entries and path.casefold() not in folded, 'duplicate/case-colliding path')
        folded.add(path.casefold())
        need(isinstance(entry['sha'], str) and SHA.fullmatch(entry['sha']), 'blob/tree SHA')
        need((entry['mode'], entry['type']) in {('040000', 'tree'), ('100644', 'blob'),
             ('100755', 'blob'), ('120000', 'blob'), ('160000', 'commit')}, 'unsupported tree mode')
        if entry['type'] == 'blob':
            need(type(entry.get('size')) is int and entry['size'] >= 0, 'blob size')
        entries[path] = entry
    children = {'': []}
    for path, entry in entries.items():
        parent = str(PurePosixPath(path).parent)
        parent = '' if parent == '.' else parent
        need(not parent or (parent in entries and entries[parent]['type'] == 'tree'), 'missing parent tree')
        children.setdefault(parent, []).append(entry)
        if entry['type'] == 'tree':
            children.setdefault(path, [])
    # Reconstruct every Git tree object: the supplied root digest cannot represent a partial listing.
    for directory in sorted(children, key=lambda p: p.count('/') + bool(p), reverse=True):
        ordered = sorted(children[directory], key=lambda e: (PurePosixPath(e['path']).name +
                         ('/' if e['type'] == 'tree' else '')).encode('utf-8'))
        data = b''.join(e['mode'].lstrip('0').encode() + b' ' + PurePosixPath(e['path']).name.encode()
                        + b'\0' + bytes.fromhex(e['sha']) for e in ordered)
        actual = hashlib.sha1(b'tree ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        need(actual == (inv['tree_sha'] if not directory else entries[directory]['sha']), 'tree digest mismatch')
    return {p: e for p, e in entries.items() if e['type'] != 'tree'}


def path_filter(entry):
    path = PurePosixPath(entry['path'])
    parts = [p.lower() for p in path.parts]
    name = parts[-1]
    if entry['mode'] == '120000':
        return 'symlink'
    if entry['mode'] == '160000':
        return 'submodule'
    if any(p in {'node_modules', '.venv', 'venv', '.cache', 'logs', 'dumps', 'backups', 'uploads'} for p in parts):
        return 'generated_or_runtime_data'
    template = name in {'.env.example', '.env.sample', '.env.template'}
    if (any(p in {'.ssh', '.aws', '.gnupg', '.npm', 'secrets', 'credentials', 'auth-data', 'sessions', 'cookies', 'tokens', '.auth'} for p in parts)
            or name.startswith('.npmrc') or (name.startswith('.env') and not template)
            or (not template and ('.env.' in name or name.endswith('.env')))
            or name.startswith(('disabled-telegram-bot-token', 'disabled-wecom-aibot-secret'))
            or name in {'id_rsa', 'id_ed25519', '.netrc', '.pypirc', 'credentials.json', 'auth.json',
                        'token.json', 'tokens.json', 'cookies.txt', 'keychain.json'}
            or path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.keystore'}):
        return 'secret_or_auth_path'
    if path.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.zip', '.gz', '.tar', '.bin', '.exe', '.dll', '.so', '.dylib', '.woff', '.woff2', '.sqlite', '.db', '.pyc'}:
        return 'binary_format'
    if entry.get('size', 0) > MAX_FILE:
        return 'oversized'
    if not template and not name.endswith('.conf.template') and path.suffix.lower() not in TEXT_EXT and name not in TEXT_NAMES:
        return 'pending_review_unknown_format'
    return None


def content_filter(data, path=""):
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        return 'binary_or_non_utf8'
    if any(ord(c) < 32 and c not in '\t\n\r' for c in text):
        return 'binary_or_non_utf8'
    patterns = [r'\bgh[pousr]_[A-Za-z0-9]{20,}\b', r'\bgithub_pat_[A-Za-z0-9_]{20,}\b',
                r'\bxox[baprs]-[A-Za-z0-9-]{12,}\b', r'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b',
                r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b',
                r'\bAKIA[A-Z0-9]{16}\b', '-----BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
                r'(?i)https?://[^\s/:]+:[^\s/@]+@', r'(?i)\bBearer\s+[A-Za-z0-9_.-]{20,}']
    if any(re.search(p, text) for p in patterns):
        return 'secret_pattern'
    # Look for literal assignments, not the word "credential" or ordinary auth logic.
    assignment = re.compile(r'''(?im)(?:^|[,{])\s*(?:(?:export|const|let|var)\s+)?["']?(?:[\w-]*[_-])?(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|client[_-]?secret|access[_-]?key)["']?\s*[:=]\s*["']([^"'\n]*)["']''')
    for match in assignment.finditer(text):
        value = match.group(1).strip()
        placeholder = (not value or value.lower() in {'placeholder', 'placeholder-only', 'changeme', 'example',
                       'redacted', 'your-token', 'your-secret', 'your-password'} or
                       re.fullmatch(r'(?:\$\{[^}]+\}|<[^>]+>)', value))
        if not placeholder:
            return 'pending_review_secret_literal'
    config = PurePosixPath(path)
    if config.suffix.lower() in {'.yaml', '.yml', '.toml', '.ini', '.cfg', '.properties', '.conf', '.service', '.timer'} or config.name.startswith('.env.') or config.name.lower().endswith('.conf.template'):
        unquoted = re.compile(r'(?im)^\s*(?:export\s+)?(?:[\w-]*[_-])?(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|access[_-]?key)\s*[:=]\s*([^\n#]+)')
        for match in unquoted.finditer(text):
            value = match.group(1).strip().strip('"' + "'")
            if value and value.lower() not in {'placeholder', 'placeholder-only', 'changeme', 'example', 'redacted', 'your-token', 'your-secret', 'your-password'} and not re.fullmatch(r'(?:\$\{[^}]+\}|<[^>]+>)', value):
                return 'pending_review_secret_literal'
    return None


def filter_policy():
    # Fingerprint actual filter code as well as the named version and allowlist.
    # A logic change cannot silently reuse an old same-commit inclusion set.
    config = {'version': FILTER_POLICY_VERSION, 'max_file': MAX_FILE,
              'extensions': sorted(TEXT_EXT), 'names': sorted(TEXT_NAMES),
              'path_filter': inspect.getsource(path_filter), 'content_filter': inspect.getsource(content_filter)}
    return {'version': FILTER_POLICY_VERSION, 'sha256': hash256(canonical(config))}


def candidate_set(inv):
    candidates = [{'path': e['path'], 'blob': e['sha'], 'size': e['size']} for e in inv['entries']
                  if e['type'] != 'tree' and path_filter(e) is None]
    return hash256(canonical(sorted(candidates, key=lambda e: e['path'])))


def included_set(records):
    return hash256(canonical(sorted(records, key=lambda e: e['path'])))


def version_identity(inv):
    return canonical({k: sorted(v, key=lambda e: e['path']) if k == 'entries' else v
                      for k, v in inv.items() if k not in {'observed_at', 'default_branch'}})


def no_symlinks(path):
    for part in [path, *path.parents]:
        need(not part.is_symlink(), 'symlink in cache path')


def initialize(root):
    no_symlinks(root)
    need(root.parent.is_dir() and not root.exists(), 'cache init requires a NEW directory; never adopt workspace')
    root.mkdir(mode=0o700)
    (root / 'marker.json').write_bytes(canonical(MARKER))
    for name in ['snapshots', 'staging', 'reports']:
        (root / name).mkdir(mode=0o700)


@contextlib.contextmanager
def locked(root):
    no_symlinks(root)
    need(root.is_dir() and read_json(root / 'marker.json') == MARKER, 'unknown cache directory')
    need(set(p.name for p in root.iterdir()) <= {'marker.json', '.lock', 'snapshots', 'staging', 'reports'}, 'unknown cache contents')
    for name in ['snapshots', 'staging', 'reports']:
        need((root / name).is_dir() and not (root / name).is_symlink(), 'invalid managed directory')
    fd = os.open(root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        need(stat.S_ISREG(os.fstat(fd).st_mode), 'invalid lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SnapshotError('cache busy; retry after existing builder finishes; never remove lock') from error
        yield
    finally:
        os.close(fd)


def location(root, repo, commit):
    need(SHA.fullmatch(commit) is not None, 'invalid snapshot SHA')
    result = root / 'snapshots' / hash256(repo.encode()) / commit / 'snapshot'
    no_symlinks(result)
    return result


def verify(snapshot, expected=None):
    no_symlinks(snapshot)
    manifest = read_json(snapshot / 'manifest.json')
    need(manifest.get('filter_policy') == filter_policy(),
         'filter policy missing/mismatch; rebuild in a NEW independent cache; existing snapshot left untouched')
    need(set(manifest) == {'schema_version', 'filter_policy', 'candidate_set_sha256', 'included_set_sha256',
                          'state', 'inventory', 'inventory_sha256', 'included', 'excluded', 'failed'}, 'snapshot manifest fields')
    inv = manifest['inventory']
    leaves = inventory_check(inv)
    need(type(manifest['schema_version']) is int and manifest['schema_version'] == SCHEMA and manifest['state'] == 'ready' and not manifest['failed']
         and manifest['inventory_sha256'] == hash256(canonical(inv)), 'snapshot not ready/intact')
    if expected is not None:
        need(version_identity(inv) == version_identity(expected), 'same SHA inventory identity differs')
    included, excluded = manifest['included'], manifest['excluded']
    need(isinstance(included, list) and isinstance(excluded, list), 'manifest records')
    need(manifest['candidate_set_sha256'] == candidate_set(inv)
         and manifest['included_set_sha256'] == included_set(included), 'candidate/included set identity mismatch; rebuild in a NEW cache')
    records = included + excluded
    need(len(records) == len(leaves) and {r['path'] for r in records} == set(leaves), 'incomplete snapshot manifest')
    files = snapshot / 'files'
    need(files.is_dir() and not files.is_symlink(), 'missing files directory')
    expected_files = {'manifest.json'} | {'files/' + r['path'] for r in included}
    expected_dirs = {'files'}
    for path in expected_files:
        expected_dirs.update(str(p) for p in PurePosixPath(path).parents if str(p) != '.')
    actual_files, actual_dirs = set(), set()
    for folder, dirs, names in os.walk(snapshot, followlinks=False):
        current = Path(folder)
        need(not current.is_symlink() and not (current.stat().st_mode & 0o222), 'snapshot directory writable/symlink')
        for name in dirs + names:
            path = current / name
            relative = path.relative_to(snapshot).as_posix()
            need(not path.is_symlink(), 'snapshot contains symlink')
            if path.is_dir():
                actual_dirs.add(relative)
            else:
                need(stat.S_ISREG(path.stat().st_mode) and not (path.stat().st_mode & 0o222), 'snapshot file writable/nonregular')
                actual_files.add(relative)
    need(actual_files == expected_files and actual_dirs == expected_dirs, 'unknown/missing snapshot contents')
    for record in excluded:
        need(set(record) == {'path', 'reason'} and record['reason'] in EXCLUSION_REASONS, 'exclusion record')
        reason = path_filter(leaves[record['path']])
        need(record['reason'] == reason if reason else record['reason'] in
             {'binary_or_non_utf8', 'secret_pattern', 'pending_review_secret_literal'}, 'inconsistent exclusion')
    for record in included:
        path = record['path']
        need(set(record) == {'path', 'blob', 'size', 'sha256'} and path_filter(leaves[path]) is None, 'inclusion record')
        data = (files / path).read_bytes()
        need(record['blob'] == leaves[path]['sha'] == blob_sha(data)
             and record['size'] == leaves[path]['size'] == len(data)
             and record['sha256'] == hash256(data) and content_filter(data, path) is None, 'snapshot file integrity/filter failure')
    return manifest


def prepare(root, inv, previous=None):
    leaves = inventory_check(inv)
    reused = {}
    if previous:
        old = location(root, inv['repo'], previous)
        manifest = verify(old)
        need(manifest['inventory']['repo'] == inv['repo'] and manifest['inventory']['commit'] == previous, 'previous identity')
        reused = {r['blob']: old / 'files' / r['path'] for r in manifest['included']}
    included, excluded, downloads = [], [], []
    for path, entry in sorted(leaves.items()):
        reason = path_filter(entry)
        if reason:
            excluded.append({'path': path, 'reason': reason})
        else:
            included.append(path)
            if entry['sha'] not in reused:
                downloads.append({'path': path, 'sha': entry['sha'], 'size': entry['size']})
    return leaves, reused, included, excluded, downloads


def plan(root, inv, previous=None):
    destination = location(root, inv['repo'], inv['commit'])
    if destination.parent.exists():
        result = verify(destination, inv)
        return {'state': 'ready', 'snapshot': str(destination), 'downloads': [], 'excluded': result['excluded']}
    _, reused, included, excluded, downloads = prepare(root, inv, previous)
    return {'state': 'needs_build', 'repo': inv['repo'], 'commit': inv['commit'], 'downloads': downloads,
            'reuse_paths': [p for p in included if p not in {r['path'] for r in downloads}], 'excluded': excluded}


def readonly(folder):
    for current, dirs, files in os.walk(folder, topdown=False):
        for name in files:
            (Path(current) / name).chmod(0o444)
        Path(current).chmod(0o555)


def build(root, inv, batch, previous=None):
    inventory_check(inv)
    destination = location(root, inv['repo'], inv['commit'])
    if destination.parent.exists():
        verify(destination, inv)
        return {'state': 'ready', 'snapshot': str(destination), 'idempotent': True}
    leaves, reused, candidates, excluded, _ = prepare(root, inv, previous)
    need(set(batch) == {'schema_version', 'repo', 'commit', 'files'} and type(batch['schema_version']) is int and batch['schema_version'] == SCHEMA
         and batch['repo'] == inv['repo'] and batch['commit'] == inv['commit'] and isinstance(batch['files'], list), 'batch version/identity')
    supplied = {}
    for item in batch['files']:
        need(set(item) == {'path', 'sha', 'encoding', 'content'} and item['path'] in candidates
             and item['path'] not in supplied and item['sha'] == leaves[item['path']]['sha']
             and item['encoding'] == 'base64' and isinstance(item['content'], str), 'batch entry')
        supplied[item['path']] = item['content']
    staging = root / 'staging' / uuid.uuid4().hex
    staging.mkdir(mode=0o700)
    payload = staging / 'snapshot'
    payload.mkdir()
    (payload / 'files').mkdir()
    manifest = {'schema_version': SCHEMA, 'filter_policy': filter_policy(),
                'candidate_set_sha256': candidate_set(inv), 'included_set_sha256': included_set([]),
                'state': 'building', 'inventory': inv,
                'inventory_sha256': hash256(canonical(inv)), 'included': [], 'excluded': excluded, 'failed': []}
    try:
        for path in candidates:
            entry = leaves[path]
            try:
                if path in supplied:
                    encoded = ''.join(supplied[path].split())
                    need(len(encoded) == 4 * ((entry['size'] + 2) // 3), 'base64 length/byte count mismatch')
                    try:
                        data = base64.b64decode(encoded, validate=True)
                    except (binascii.Error, ValueError) as error:
                        raise SnapshotError('invalid base64') from error
                else:
                    need(entry['sha'] in reused, 'missing download')
                    data = reused[entry['sha']].read_bytes()
                need(len(data) == entry['size'] and blob_sha(data) == entry['sha'], 'blob size/hash mismatch')
                reason = content_filter(data, path)
                if reason:
                    manifest['excluded'].append({'path': path, 'reason': reason})
                    continue
                target = payload / 'files' / path
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as out:
                    out.write(data)
                    out.flush()
                    os.fsync(out.fileno())
                manifest['included'].append({'path': path, 'blob': entry['sha'], 'size': len(data), 'sha256': hash256(data)})
            except SnapshotError as error:
                manifest['failed'].append({'path': path, 'reason': str(error)})
        manifest['included_set_sha256'] = included_set(manifest['included'])
        manifest['state'] = 'failed' if manifest['failed'] else 'ready'
        if manifest['failed']:
            report = root / 'reports' / (inv['commit'] + '-' + uuid.uuid4().hex + '.json')
            with report.open('xb') as out:
                out.write(canonical(manifest))
            raise SnapshotError('build failed; no ready snapshot; report=' + str(report))
        (payload / 'manifest.json').write_bytes(canonical(manifest))
        readonly(payload)
        verify(payload, inv)
        destination.parent.parent.mkdir(mode=0o700, exist_ok=True)
        need(not destination.parent.exists(), 'destination already exists')
        # Same-filesystem atomic visibility. Root flock serializes cooperating builders.
        staging.rename(destination.parent)
        return {'state': 'ready', 'snapshot': str(destination), 'idempotent': False}
    except (OSError, SnapshotError) as error:
        if not manifest['failed']:
            manifest['state'] = 'failed'
            manifest['included_set_sha256'] = included_set(manifest['included'])
            manifest['failed'].append({'path': '<publication>', 'reason': type(error).__name__})
            report = root / 'reports' / (inv['commit'] + '-' + uuid.uuid4().hex + '.json')
            with report.open('xb') as out:
                out.write(canonical(manifest))
        raise
    finally:
        if staging.exists():
            for current, dirs, files in os.walk(staging):
                Path(current).chmod(0o700)
            shutil.rmtree(staging)



def batches(paths, inv):
    combined = {'schema_version': SCHEMA, 'repo': inv['repo'], 'commit': inv['commit'], 'files': []}
    for path in paths:
        batch = read_json(path)
        need(set(batch) == set(combined) and type(batch['schema_version']) is int
             and batch['schema_version'] == SCHEMA and batch['repo'] == inv['repo']
             and batch['commit'] == inv['commit'] and isinstance(batch['files'], list), 'batch version/identity')
        combined['files'].extend(batch['files'])
    return combined


def rules(root, inv, scopes, required):
    destination = location(root, inv['repo'], inv['commit'])
    manifest = verify(destination, inv)
    entries = {e['path']: e for e in inv['entries']}
    paths = ['AGENTS.md']
    for scope in scopes:
        if scope == '.':
            continue
        parsed = safe_path(scope)
        need(scope in entries, 'rule scope absent from pinned tree')
        directory = parsed if entries[scope]['type'] == 'tree' else parsed.parent
        ancestors = list(reversed([directory, *directory.parents]))
        for parent in ancestors:
            path = (parent / 'AGENTS.md').as_posix()
            if path in entries and path not in paths:
                paths.append(path)
    for path in required:
        safe_path(path)
        if path not in paths:
            paths.append(path)
    included = {r['path']: r for r in manifest['included']}
    excluded = {r['path']: r['reason'] for r in manifest['excluded']}
    records = []
    for path in paths:
        if path not in included:
            records.append({'path': path, 'status': 'blocked',
                            'reason': excluded.get(path, 'absent from pinned tree')})
        else:
            records.append({'path': path, 'status': 'must_read', 'sha256': included[path]['sha256'],
                            'text': (destination / 'files' / path).read_text(encoding='utf-8')})
    return {'state': 'blocked_rules' if any(r['status'] == 'blocked' for r in records) else 'rules_to_read',
            'snapshot': str(destination), 'rules': records,
            'manifest': str(destination / 'manifest.json'),
            'notice': 'Read root AGENTS, scoped AGENTS and all project-required rules before rg; this is not a sandbox or proof of reading.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['init', 'plan', 'build', 'verify', 'rules'])
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--inventory', type=Path)
    parser.add_argument('--batch', type=Path, action='append', help='Repeat for multiple fixed-SHA batches')
    parser.add_argument('--scope', action='append', default=[], help='Rules for a pinned-tree file/directory')
    parser.add_argument('--required', action='append', default=[], help='Additional project-required rule document')
    parser.add_argument('--previous', help='Previously verified commit in the SAME managed repo cache')
    args = parser.parse_args()
    root = args.cache.absolute()
    try:
        if args.command == 'init':
            initialize(root)
            result = {'state': 'initialized', 'cache': str(root)}
        else:
            need(args.inventory is not None, '--inventory required')
            inv = read_json(args.inventory)
            inventory_check(inv)
            with locked(root):
                if args.command == 'plan':
                    result = plan(root, inv, args.previous)
                elif args.command == 'build':
                    need(args.batch is not None, '--batch required (empty files permitted for full reuse)')
                    result = build(root, inv, batches(args.batch, inv), args.previous)
                elif args.command == 'rules':
                    result = rules(root, inv, args.scope or ['.'], args.required)
                else:
                    destination = location(root, inv['repo'], inv['commit'])
                    verify(destination, inv)
                    result = {'state': 'ready', 'snapshot': str(destination)}
        if args.command != 'init':
            result['filter_policy'] = filter_policy()
            result['candidate_set_sha256'] = candidate_set(inv)
            result['observation'] = {k: inv[k] for k in ['repo', 'commit', 'tree_sha', 'default_branch', 'observed_at']}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result['state'] == 'blocked_rules' else 0
    except (SnapshotError, OSError, KeyError, TypeError, UnicodeError) as error:
        # Never echo untrusted content or credentials; expected contract failures have safe summaries.
        reason = str(error) if isinstance(error, SnapshotError) else type(error).__name__
        print(json.dumps({'state': 'failed', 'reason': reason}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
