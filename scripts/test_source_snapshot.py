#!/usr/bin/env python3
"""Synthetic, offline tests; no real project content or platform clients."""
import base64
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import source_snapshot as snapshot

SCRIPT = Path(snapshot.__file__).resolve()
ENV = {'PATH': os.environ['PATH'], 'GIT_CONFIG_GLOBAL': os.devnull,
       'GIT_CONFIG_NOSYSTEM': '1', 'PYTHONDONTWRITEBYTECODE': '1'}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hiveflow-source-test-')
        self.area = Path(self.temp.name).resolve()
        self.repo = self.area / 'fixture'
        self.repo.mkdir()
        self.git('init', '-q')
        self.cache = self.area / 'cache'
        snapshot.initialize(self.cache)
        self.write('AGENTS.md', b'# Rules\nRead docs/RULES.md before search.\n')
        self.write('docs/RULES.md', b'# Project rules\nStatic analysis only.\n')
        self.write('src/app.py', b'print("one")\n')
        self.write('src/credential.js', b'export function credential(user) { return user.credential; }\n')
        self.write('src/AGENTS.md', b'# Local rules\nKeep scope narrow.\n')
        self.inv, self.batch = self.commit()

    def tearDown(self):
        for current, dirs, files in os.walk(self.area):
            Path(current).chmod(0o700)
            for name in files:
                p = Path(current) / name
                if not p.is_symlink():
                    p.chmod(0o600)
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=self.repo, env=ENV, stderr=subprocess.DEVNULL)

    def write(self, path, data):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def commit(self, skip_add=False):
        if not skip_add:
            self.git('add', '-A')
        self.git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'synthetic fixture', '--allow-empty')
        sha = self.git('rev-parse', 'HEAD').decode().strip()
        tree = self.git('rev-parse', 'HEAD^{tree}').decode().strip()
        entries, files = [], []
        for row in self.git('ls-tree', '-r', '-t', '-l', '-z', 'HEAD').split(b'\0'):
            if not row:
                continue
            meta, name = row.split(b'\t', 1)
            mode, kind, blob, size = meta.decode().split()
            path = name.decode()
            entry = {'path': path, 'mode': mode, 'type': kind, 'sha': blob}
            if kind == 'blob':
                entry['size'] = int(size)
                data = self.git('cat-file', 'blob', blob)
                files.append({'path': path, 'sha': blob, 'encoding': 'base64',
                              'content': base64.b64encode(data).decode()})
            entries.append(entry)
        inv = {'schema_version': 1, 'repo': 'example/project', 'private': True,
               'default_branch': 'fixture-main', 'observed_at': 'synthetic-UTC', 'commit': sha,
               'tree_sha': tree, 'tree_complete': True, 'entries': entries}
        batch = {'schema_version': 1, 'repo': inv['repo'], 'commit': sha,
                 'files': [f for f in files if snapshot.path_filter(next(e for e in entries if e['path'] == f['path'])) is None]}
        return inv, batch

    def build(self, inv=None, batch=None, previous=None):
        with snapshot.locked(self.cache):
            return snapshot.build(self.cache, inv or self.inv, batch if batch is not None else self.batch, previous)

    def test_cold_build_and_verify(self):
        result = self.build()
        location = Path(result['snapshot'])
        manifest = snapshot.verify(location, self.inv)
        self.assertEqual(manifest['state'], 'ready')
        self.assertEqual(len(manifest['included']), 5)
        self.assertTrue((location / 'files/src/credential.js').exists())
        self.assertFalse((location / 'files/src/app.py').stat().st_mode & 0o111)
        self.assertEqual(list((self.cache / 'staging').iterdir()), [])

    def test_incremental_add_modify_delete_rename_and_reuse(self):
        old = self.build()
        self.write('src/app.py', b'print("two")\n')
        self.write('src/new.ts', b'export const value = 2;\n')
        (self.repo / 'src/credential.js').rename(self.repo / 'src/moved.js')
        (self.repo / 'docs/RULES.md').unlink()
        inv, batch = self.commit()
        with snapshot.locked(self.cache):
            plan = snapshot.plan(self.cache, inv, self.inv['commit'])
        self.assertEqual({r['path'] for r in plan['downloads']}, {'src/app.py', 'src/new.ts'})
        self.assertIn('src/moved.js', plan['reuse_paths'])
        batch['files'] = [f for f in batch['files'] if f['path'] in {r['path'] for r in plan['downloads']}]
        result = self.build(inv, batch, self.inv['commit'])
        folder = Path(result['snapshot']) / 'files'
        self.assertFalse((folder / 'docs/RULES.md').exists())
        self.assertFalse((folder / 'src/credential.js').exists())
        self.assertEqual((folder / 'src/app.py').read_bytes(), b'print("two")\n')
        self.assertTrue((Path(old['snapshot']) / 'files/docs/RULES.md').exists())

    def test_new_commit_unchanged_tree_reuses_empty_batch(self):
        original = self.build()
        inv, batch = self.commit()
        self.assertNotEqual(inv['commit'], self.inv['commit'])
        with snapshot.locked(self.cache):
            plan = snapshot.plan(self.cache, inv, self.inv['commit'])
        self.assertFalse(plan['downloads'])
        batch['files'] = []
        current = self.build(inv, batch, self.inv['commit'])
        self.assertNotEqual(current['snapshot'], original['snapshot'])
        self.assertEqual(len(snapshot.verify(Path(current['snapshot']))['included']), 5)

    def test_same_sha_reverified_new_observation(self):
        self.build()
        fresh = copy.deepcopy(self.inv)
        fresh['observed_at'] = 'new-synthetic-UTC'
        fresh['entries'].reverse()
        result = self.build(fresh, {'schema_version': 1, 'repo': fresh['repo'], 'commit': fresh['commit'], 'files': []})
        self.assertTrue(result['idempotent'])

    def test_missing_download_no_ready_and_failure_manifest(self):
        batch = dict(self.batch, files=self.batch['files'][:-1])
        with self.assertRaises(snapshot.SnapshotError):
            self.build(batch=batch)
        self.assertFalse(snapshot.location(self.cache, self.inv['repo'], self.inv['commit']).exists())
        reports = list((self.cache / 'reports').glob('*.json'))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text())
        self.assertEqual(report['state'], 'failed')
        self.assertTrue(report['included'] and report['failed'])
        self.assertEqual(list((self.cache / 'staging').iterdir()), [])
        self.assertEqual(self.build()['state'], 'ready')

    def test_bad_hash_and_bad_size(self):
        for content in ['!!!!', base64.b64encode(b'x' * 80).decode(),
                        base64.b64encode(b'x' * len(base64.b64decode(self.batch['files'][0]['content']))).decode()]:
            batch = copy.deepcopy(self.batch)
            batch['files'][0]['content'] = content
            with self.assertRaises(snapshot.SnapshotError):
                self.build(batch=batch)
        self.assertEqual(len(list((self.cache / 'reports').iterdir())), 3)

    def test_corrupt_existing_snapshot_not_repaired_or_replaced(self):
        result = self.build()
        target = Path(result['snapshot']) / 'files/src/app.py'
        target.chmod(0o644)
        target.write_bytes(b'corruption')
        target.chmod(0o444)
        with self.assertRaises(snapshot.SnapshotError):
            self.build()
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.verify(Path(result['snapshot']), self.inv)
        self.assertEqual(target.read_bytes(), b'corruption')

    def test_corrupt_previous_stops_reuse(self):
        result = self.build()
        target = Path(result['snapshot']) / 'files/src/app.py'
        target.chmod(0o644)
        target.write_bytes(b'corruption')
        target.chmod(0o444)
        self.write('src/new.py', b'# new\n')
        inv, batch = self.commit()
        with self.assertRaises(snapshot.SnapshotError):
            self.build(inv, batch, self.inv['commit'])

    def test_path_injection_and_collisions(self):
        for path in ['../escape.py', '/absolute.py', 'a/../../escape.py', 'a//b.py', './a.py',
                     'a/./b.py', 'a\\b.py', '.git/config', '.GIT/config', 'https://bad/a.py', 'a\nb.py', 'e\u0301.py']:
            with self.subTest(path=repr(path)), self.assertRaises(snapshot.SnapshotError):
                snapshot.safe_path(path)
        inv = copy.deepcopy(self.inv)
        inv['entries'].append(dict(inv['entries'][0], path=inv['entries'][0]['path'].swapcase()))
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.inventory_check(inv)

    def test_incomplete_tree_wrong_root_and_missing_parent(self):
        for mutation in ['incomplete', 'root', 'drop', 'parent']:
            inv = copy.deepcopy(self.inv)
            if mutation == 'incomplete':
                inv['tree_complete'] = False
            elif mutation == 'root':
                inv['tree_sha'] = '0' * 40
            elif mutation == 'drop':
                inv['entries'].pop()
            else:
                inv['entries'] = [e for e in inv['entries'] if e['path'] != 'src']
            with self.subTest(mutation=mutation), self.assertRaises(snapshot.SnapshotError):
                snapshot.inventory_check(inv)

    def test_symlink_and_submodule_excluded(self):
        (self.repo / 'src/link.py').symlink_to('app.py')
        self.git('add', '-A')
        self.git('update-index', '--add', '--cacheinfo', '160000,' + self.inv['commit'] + ',vendor')
        inv, batch = self.commit(skip_add=True)
        result = self.build(inv, batch)
        reasons = {r['path']: r['reason'] for r in snapshot.verify(Path(result['snapshot']))['excluded']}
        self.assertEqual(reasons['src/link.py'], 'symlink')
        self.assertEqual(reasons['vendor'], 'submodule')

    def test_secret_auth_binary_unknown_excluded(self):
        self.write('.env', b'TOKEN=synthetic-only\n')
        self.write('.npmrc', b'registry=example.invalid\n')
        self.write('credentials.json', b'{}')
        self.write('.env.example', b'API_KEY=<your-key>\n')
        self.write('runtime.yaml', b'password: synthetic-sensitive-value\n')
        self.write('data.json', b'{"token":"synthetic-sensitive-value"}')
        self.write('src/key.py', ('TOKEN = "ghp_' + 'x' * 30 + '"\n').encode())
        self.write('data.bin', b'\x00binary')
        self.write('odd.py', b'\xff\x00')
        self.write('opaque.dat', b'unknown format')
        inv, batch = self.commit()
        result = self.build(inv, batch)
        manifest = snapshot.verify(Path(result['snapshot']))
        excluded = {r['path']: r['reason'] for r in manifest['excluded']}
        for name in ['.env', '.npmrc', 'credentials.json', 'runtime.yaml', 'data.json', 'src/key.py', 'data.bin', 'odd.py', 'opaque.dat']:
            self.assertIn(name, excluded)
            self.assertFalse((Path(result['snapshot']) / 'files' / name).exists())
        self.assertTrue((Path(result['snapshot']) / 'files/.env.example').exists())
        self.assertTrue((Path(result['snapshot']) / 'files/src/credential.js').exists())
        for p in self.cache.rglob('*'):
            if p.is_file():
                self.assertNotIn(b'synthetic-sensitive-value', p.read_bytes())

    def test_batch_identity_extra_duplicate_and_invalid_json(self):
        for mutation in ['commit', 'repo', 'extra', 'duplicate']:
            batch = copy.deepcopy(self.batch)
            if mutation == 'commit':
                batch['commit'] = '1' * 40
            elif mutation == 'repo':
                batch['repo'] = 'another/project'
            elif mutation == 'extra':
                batch['files'][0]['path'] = '../outside.py'
            else:
                batch['files'].append(batch['files'][0])
            with self.subTest(mutation=mutation), self.assertRaises(snapshot.SnapshotError):
                self.build(batch=batch)
        path = self.area / 'bad.json'
        path.write_text('{"files":[],"files":[]}')
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.read_json(path)

    def test_unknown_directory_never_adopted(self):
        folder = self.area / 'user-workspace'
        folder.mkdir()
        marker = folder / 'keep.txt'
        marker.write_text('untouched')
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.initialize(folder)
        self.assertEqual(marker.read_text(), 'untouched')
        unknown = snapshot.location(self.cache, self.inv['repo'], self.inv['commit'])
        unknown.mkdir(parents=True)
        (unknown / 'keep.txt').write_text('untouched')
        with self.assertRaises((snapshot.SnapshotError, OSError)):
            self.build()
        self.assertEqual((unknown / 'keep.txt').read_text(), 'untouched')

    def test_cache_and_snapshot_symlinks_rejected(self):
        alias = self.area / 'alias'
        alias.symlink_to(self.cache, target_is_directory=True)
        with self.assertRaises(snapshot.SnapshotError):
            with snapshot.locked(alias):
                pass
        result = self.build()
        folder = Path(result['snapshot']) / 'files/src'
        folder.chmod(0o755)
        (folder / 'app.py').unlink()
        (folder / 'app.py').symlink_to(self.repo / 'src/app.py')
        folder.chmod(0o555)
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.verify(Path(result['snapshot']))

    def cli(self, command, inv_path, batch_path):
        return [sys.executable, '-B', str(SCRIPT), command, '--cache', str(self.cache),
                '--inventory', str(inv_path), '--batch', str(batch_path)]

    def inputs(self):
        inv = self.area / 'inventory.json'
        batch = self.area / 'batch.json'
        inv.write_text(json.dumps(self.inv))
        batch.write_text(json.dumps(self.batch))
        return inv, batch

    def test_concurrent_build_lock_and_idempotent_retry(self):
        inv, batch = self.inputs()
        command = self.cli('build', inv, batch)
        processes = [subprocess.Popen(command, env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        results = [p.communicate(timeout=30) for p in processes]
        self.assertIn(0, [p.returncode for p in processes])
        for p, result in zip(processes, results):
            if p.returncode:
                self.assertIn(b'cache busy', result[1])
        retry = subprocess.run(command, env=ENV, capture_output=True, timeout=30)
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertTrue(json.loads(retry.stdout)['idempotent'])
        self.assertEqual(len(list((self.cache / 'snapshots').rglob('manifest.json'))), 1)
        self.assertFalse(list((self.cache / 'staging').iterdir()))

    def test_busy_lock_cli_no_publish(self):
        inv, batch = self.inputs()
        with snapshot.locked(self.cache):
            result = subprocess.run(self.cli('build', inv, batch), env=ENV, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b'cache busy', result.stderr)
        self.assertFalse(snapshot.location(self.cache, self.inv['repo'], self.inv['commit']).exists())

    def test_publish_failure_keeps_failed_manifest_and_no_ready(self):
        with mock.patch.object(Path, 'rename', side_effect=OSError('synthetic publication failure')):
            with self.assertRaises(OSError):
                self.build()
        self.assertFalse(snapshot.location(self.cache, self.inv['repo'], self.inv['commit']).exists())
        self.assertEqual(list((self.cache / 'staging').iterdir()), [])
        report = json.loads(next((self.cache / 'reports').iterdir()).read_text())
        self.assertEqual(report['state'], 'failed')
        self.assertTrue(report['included'] and report['failed'])

    def test_identity_schema_and_unknown_fields_rejected(self):
        for key, value in [('schema_version', 2), ('schema_version', True), ('private', False),
                           ('commit', 'main'), ('repo', '../other'), ('extra', 'dynamic')]:
            inv = copy.deepcopy(self.inv)
            inv[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(snapshot.SnapshotError):
                snapshot.inventory_check(inv)

    def test_binary_runtime_and_oversized_path_filters(self):
        for path, size, reason in [('image.png', 12, 'binary_format'), ('huge.py', snapshot.MAX_FILE + 1, 'oversized'),
                                   ('logs/debug.txt', 12, 'generated_or_runtime_data'),
                                   ('sessions/user.json', 12, 'secret_or_auth_path')]:
            entry = {'path': path, 'mode': '100644', 'size': size}
            self.assertEqual(snapshot.path_filter(entry), reason)

    def test_rules_order_and_explicit_project_requirements(self):
        self.build()
        result = snapshot.rules(self.cache, self.inv, ['src/app.py'], ['docs/RULES.md'])
        self.assertEqual(result['state'], 'rules_to_read')
        self.assertEqual([r['path'] for r in result['rules']], ['AGENTS.md', 'src/AGENTS.md', 'docs/RULES.md'])
        self.assertIn('Read docs/RULES.md', result['rules'][0]['text'])
        result = snapshot.rules(self.cache, self.inv, ['src'], ['docs/MISSING.md'])
        self.assertEqual(result['state'], 'blocked_rules')
        self.assertEqual(result['rules'][-1]['reason'], 'absent from pinned tree')

    def test_missing_or_filtered_root_rules_stop_analysis(self):
        for data in [None, ('# Rules\nTOKEN = "ghp_' + 'x' * 30 + '"\n').encode()]:
            if data is None:
                (self.repo / 'AGENTS.md').unlink()
            else:
                self.write('AGENTS.md', data)
            inv, batch = self.commit()
            self.build(inv, batch)
            result = snapshot.rules(self.cache, inv, ['src/app.py'], [])
            self.assertEqual(result['state'], 'blocked_rules')
            self.assertEqual(result['rules'][0]['path'], 'AGENTS.md')
            self.assertEqual(result['rules'][0]['status'], 'blocked')

    def test_multiple_batches_and_duplicate_between_batches(self):
        inv_path, batch_path = self.inputs()
        first = dict(self.batch, files=self.batch['files'][:2])
        second = dict(self.batch, files=self.batch['files'][2:])
        batch_path.write_text(json.dumps(first))
        second_path = self.area / 'second.json'
        second_path.write_text(json.dumps(second))
        combined = snapshot.batches([batch_path, second_path], self.inv)
        self.assertEqual(len(combined['files']), len(self.batch['files']))
        duplicate = snapshot.batches([batch_path, batch_path], self.inv)
        with self.assertRaises(snapshot.SnapshotError):
            self.build(batch=duplicate)
        command = self.cli('build', inv_path, batch_path) + ['--batch', str(second_path)]
        result = subprocess.run(command, env=ENV, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_plan_build_verify_and_new_sha_not_old(self):
        inv, batch = self.inputs()
        for command, state in [('plan', 'needs_build'), ('build', 'ready'), ('verify', 'ready')]:
            result = subprocess.run(self.cli(command, inv, batch), env=ENV, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['state'], state)
        current = copy.deepcopy(self.inv)
        current['commit'] = '1' * 40
        inv.write_text(json.dumps(current))
        result = subprocess.run(self.cli('verify', inv, batch), env=ENV, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b'"ready"', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
