"""副本来源、路径与覆盖保护的离线回归。"""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from sync_from_hiveos import adapt, digest, plan, sync, validate_export

COMMIT = 'a' * 40
NAME = 'docs/06-自动化/HFL-003-协作流程与授权.md'


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.files = {NAME: b'# Rules\n'}
        self.manifest = {'schemaVersion': 1, 'repository': 'xiiiblue/hive-os', 'commit': COMMIT,
                         'includeRoots': ['docs/06-自动化/', 'docs/11-手册/05-自动化手册/', 'scripts/hiveflow/'],
                         'files': [{'path': NAME, 'sourceSHA256': digest(self.files[NAME]),
                                    'exportSHA256': digest(self.files[NAME]), 'adaptations': []}]}

    def test_exact_copy_and_check_leave_bytes_unchanged(self):
        sync(self.root, self.files, self.manifest)
        before = (self.root / 'export-manifest.json').read_bytes()
        sync(self.root, self.files, self.manifest, check=True)
        self.assertEqual((self.root / 'export-manifest.json').read_bytes(), before)
        self.assertEqual(validate_export(self.root, self.manifest), [])

    def test_modified_copy_blocks_update_before_any_write(self):
        sync(self.root, self.files, self.manifest)
        (self.root / NAME).write_bytes(b'# Local edit\n')
        with self.assertRaisesRegex(ValueError, '先处理已有副本差异'):
            sync(self.root, self.files, self.manifest)
        self.assertEqual((self.root / NAME).read_bytes(), b'# Local edit\n')

    def test_unmanaged_file_and_missing_file_are_detected(self):
        sync(self.root, self.files, self.manifest)
        unknown = self.root / 'docs/06-自动化/extra.md'
        unknown.write_text('unknown')
        self.assertTrue(validate_export(self.root, self.manifest))
        with self.assertRaises(ValueError):
            sync(self.root, self.files, self.manifest)
        self.assertTrue(unknown.exists())
        unknown.unlink()
        (self.root / NAME).unlink()
        self.assertTrue(validate_export(self.root, self.manifest))

    def test_first_sync_refuses_existing_unmanaged_target(self):
        target = self.root / NAME
        target.parent.mkdir(parents=True)
        target.write_text('existing')
        with self.assertRaisesRegex(ValueError, '已有未管理文件'):
            sync(self.root, self.files, self.manifest)
        self.assertEqual(target.read_text(), 'existing')
        self.assertFalse((self.root / 'export-manifest.json').exists())

    def test_manifest_identity_duplicate_and_traversal_are_rejected(self):
        sync(self.root, self.files, self.manifest)
        for field, value in [('repository', 'other/repo'), ('commit', 'main'), ('schemaVersion', 2)]:
            changed = copy.deepcopy(self.manifest)
            changed[field] = value
            self.assertTrue(validate_export(self.root, changed))
        for path in ('../outside.md', '/tmp/rules.md', 'src/app.py', 'docs/06-自动化/../../outside.md'):
            changed = copy.deepcopy(self.manifest)
            changed['files'][0]['path'] = path
            self.assertTrue(validate_export(self.root, changed))
        changed = copy.deepcopy(self.manifest)
        changed['files'].append(changed['files'][0])
        self.assertTrue(validate_export(self.root, changed))

    def test_symlink_never_copies_or_reads_external_bytes(self):
        outside = self.root / 'outside.md'
        outside.write_text('private fixture')
        target = self.root / NAME
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)
        self.assertTrue(validate_export(self.root, self.manifest))
        with self.assertRaises(ValueError):
            sync(self.root, self.files, self.manifest)
        self.assertEqual(outside.read_text(), 'private fixture')

    def test_only_excluded_links_are_pinned_and_recorded(self):
        name = 'docs/06-自动化/sample.md'
        raw = '[内部](HFL-003-协作流程与授权.md#范围) [根规则](../../AGENTS.md#规则) [站点](https://example.invalid/)'.encode()
        output, changes = adapt(name, raw, COMMIT, {NAME})
        value = output.decode()
        self.assertIn('[内部](HFL-003-协作流程与授权.md#范围)', value)
        self.assertIn('github.com/xiiiblue/hive-os/blob/' + COMMIT + '/AGENTS.md#规则', value)
        self.assertIn('[站点](https://example.invalid/)', value)
        self.assertEqual(changes, ['pinned-upstream-links'])

    def test_requested_source_or_plan_mismatch_does_not_write(self):
        sync(self.root, self.files, self.manifest)
        before = (self.root / NAME).read_bytes()
        changed = copy.deepcopy(self.manifest)
        changed['commit'] = 'b' * 40
        with self.assertRaises(ValueError):
            sync(self.root, self.files, changed, check=True)
        self.assertEqual((self.root / NAME).read_bytes(), before)

    def test_pinned_entry_lookup_rejects_other_sources_and_commits(self):
        root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(root / 'scripts/hiveflow'))
        self.addCleanup(sys.path.pop, 0)
        spec = importlib.util.spec_from_file_location('export_loading_fixture', root / 'scripts/hiveflow/test_spec_loading.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        commit = json.loads((root / 'export-manifest.json').read_text())['commit']
        base = 'https://github.com/xiiiblue/hive-os/blob/' + commit + '/'
        self.assertEqual(module.export_entry_path('../../AGENTS.md'), 'AGENTS.md')
        self.assertEqual(module.export_entry_path(base + 'AGENTS.md#规则'), 'AGENTS.md')
        for target in (base.replace(commit, '0' * 40) + 'AGENTS.md',
                       base.replace('xiiiblue/hive-os', 'other/repo') + 'AGENTS.md',
                       base + '../AGENTS.md'):
            with self.assertRaises(module.Blocked):
                module.export_entry_path(target)

    def test_malformed_manifest_is_rejected_without_reading_unlisted_files(self):
        for value in (None, [], {'schemaVersion': 1}):
            self.assertTrue(validate_export(self.root, value))
        for field, value in [('commit', None), ('files', [None]), ('files', [{'path': None}])]:
            changed = copy.deepcopy(self.manifest)
            changed[field] = value
            self.assertTrue(validate_export(self.root, changed))

    def test_plan_reads_committed_bytes_only_and_rejects_foreign_origin(self):
        source = self.root / 'source'
        source.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.DEVNULL)
        git('init', '-q')
        target = source / NAME
        target.parent.mkdir(parents=True)
        target.write_bytes(b'# committed fixture\n')
        git('add', NAME)
        git('-c', 'user.name=Offline Fixture', '-c', 'user.email=fixture@example.invalid',
            '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null', 'commit', '-qm', 'fixture')
        commit = git('rev-parse', 'HEAD').decode().strip()
        git('remote', 'add', 'origin', 'https://github.com/xiiiblue/hive-os.git')
        target.write_bytes(b'# uncommitted fixture\n')
        (target.parent / 'untracked.md').write_text('untracked fixture')
        files, manifest = plan(source, commit)
        self.assertEqual(files, {NAME: b'# committed fixture\n'})
        self.assertEqual(manifest['commit'], commit)
        self.assertEqual(target.read_bytes(), b'# uncommitted fixture\n')
        self.assertTrue((target.parent / 'untracked.md').exists())
        git('remote', 'set-url', 'origin', 'https://github.com/other/repo.git')
        with self.assertRaisesRegex(ValueError, 'origin不符'):
            plan(source, commit)


if __name__ == '__main__':
    unittest.main()
