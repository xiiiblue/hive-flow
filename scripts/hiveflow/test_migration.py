"""Local migration inventory and authority checks. Never queries the former source."""
import ast
import json
from pathlib import Path, PurePosixPath
import re
import unittest
from check_docs import SECRET_PATTERNS
from test_spec_loading import validate_manifest

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs/06-自动化'
PUBLIC_LICENSE_URL = 'https://github.com/xiiiblue/hive-flow/blob/main/LICENSE'


def retired_runtime_links(text):
    """公开许可证是分发声明；其它旧仓库文件入口仍不能作为执行依据。"""
    return [match.group(0) for match in re.finditer(
        r'https://github\.com/xiiiblue/hive-flow/(?:blob|raw|tree)/[^\s)\'"<>]+', text)
        if match.group(0) != PUBLIC_LICENSE_URL]


class MigrationTests(unittest.TestCase):
    def test_complete_source_inventory_and_targets(self):
        data = json.loads((DOCS / 'migration-map.json').read_text())
        self.assertEqual(data['sourceMain'], '787f21ef950e0352138677e9e9643c61f4da5af0')
        self.assertEqual(data['sourceCandidate'], '533853c05de617b3eaf0539c0bd616b527031de1')
        self.assertEqual(data['targetRepository'], 'xiiiblue/hive-os')
        self.assertEqual(len(data['files']), 33)
        self.assertEqual(len({r['source'] for r in data['files']}), 33)
        self.assertEqual(len({r['target'] for r in data['files']}), 33)
        self.assertEqual(sum(r['source'].endswith('.md') for r in data['files']), 19)
        self.assertEqual(sum(r['source'].startswith('scripts/') for r in data['files']), 11)
        for row in data['files']:
            with self.subTest(source=row['source']):
                self.assertRegex(row['sourceSHA256'], r'^[0-9a-f]{64}$')
                self.assertRegex(row['migrationTargetSHA256'], r'^[0-9a-f]{64}$')
                target=ROOT / row['target']
                self.assertTrue(target.is_file())
                self.assertFalse(target.is_symlink())
                self.assertTrue(target.resolve().is_relative_to(ROOT))
                if target.name.startswith('HFL-'):
                    # 原迁入摘要继续指向改名前的字节，不被当前正文摘要替换。
                    self.assertEqual(row['migrationTargetPath'],
                                     'docs/06-自动化/' + PurePosixPath(row['source']).name)

    def test_numbered_docs_and_readable_index(self):
        index = (DOCS / 'README.md').read_text()
        numbered = list(DOCS.glob('HFL-*.md'))
        self.assertTrue(numbered)
        identifiers = set()
        for path in numbered:
            with self.subTest(path=path.name):
                match = re.fullmatch(r'(HFL-\d{3})-([^/]+)\.md', path.name)
                self.assertIsNotNone(match)
                identifier, title = match.groups()
                self.assertNotIn(identifier, identifiers)
                identifiers.add(identifier)
                self.assertRegex(title, r'[\u4e00-\u9fff]')
                self.assertEqual(path.read_text().splitlines()[0], f'# {identifier}：{title}')
                self.assertIn(f']({path.name})', index)
        exceptions = {'README.md', 'AGENTS.md'}
        self.assertEqual({p.name for p in DOCS.glob('*.md') if not p.name.startswith('HFL-')},
                         exceptions)

    def test_runtime_status_has_dated_evidence_and_no_manifest_expansion(self):
        status = DOCS / 'HFL-019-接入与验收记录.md'
        text = status.read_text()
        self.assertIn('资料核对日期：2026-10-20', text)
        self.assertIn('9a581522c40ca0e4e5ffa2bcab57959b0e733df9', text)
        self.assertIn('不能把资料核对日期当成现场验收日期', text)
        manifest = json.loads((DOCS / 'spec-manifest.json').read_text())
        for scenario in ('intake', 'review', 'delivery'):
            self.assertNotIn(str(status.relative_to(ROOT)), validate_manifest(manifest, scenario))

    def test_manifest_same_repository_and_narrow_scenarios(self):
        manifest=json.loads((DOCS / 'spec-manifest.json').read_text())
        for scenario in ('intake','review','delivery'):
            for path in validate_manifest(manifest,scenario):
                self.assertTrue(path=='AGENTS.md' or path.startswith('docs/06-自动化/'))
        delivery=manifest['scenarios']['delivery']
        self.assertEqual(delivery['AGENTS.md'],['*'])
        self.assertEqual(delivery['docs/06-自动化/AGENTS.md'],['*'])
        for scenario in ('intake','review'):
            self.assertNotIn('AGENTS.md',manifest['scenarios'][scenario])

    def test_no_old_runtime_authority_or_unmigrated_commands(self):
        for folder in (DOCS,ROOT/'scripts/hiveflow'):
            for path in folder.iterdir():
                if path.suffix not in ('.md','.py','.mjs'): continue
                text=path.read_text()
                self.assertEqual(retired_runtime_links(text), [], path.name)
                self.assertNotRegex(text,r"REPO\s*=\s*['\"]xiiiblue/hive-flow")
                self.assertNotRegex(text,r'python3(?: -B)? scripts/(?!hiveflow/)[a-z_]+\.py')
        root=(ROOT/'AGENTS.md').read_text()
        self.assertIn('docs/06-自动化/HFL-001-规范加载入口.md',root)
        self.assertNotIn('xiiiblue/hive-flow',root)

    def test_public_license_exception_rejects_runtime_and_nearby_paths(self):
        self.assertEqual(retired_runtime_links(f'[Apache-2.0]({PUBLIC_LICENSE_URL})'), [])
        base = PUBLIC_LICENSE_URL.split('/blob/')[0] + '/'
        for suffix in ('blob/main/docs/SPEC_ENTRY.md', 'raw/main/LICENSE',
                       'tree/main/docs', 'blob/main/LICENSE/extra',
                       'blob/main/LICENSE?path=docs'):
            with self.subTest(suffix=suffix):
                self.assertEqual(retired_runtime_links(base + suffix), [base + suffix])

    def test_pure_tools_compile_without_import_side_effects(self):
        for path in (ROOT/'scripts/hiveflow').glob('*.py'):
            compile(path.read_text(),str(path),'exec')
        tree=ast.parse((ROOT/'scripts/hiveflow/adoption_record.py').read_text())
        names={n.name for n in tree.body if isinstance(n,ast.FunctionDef)}
        self.assertTrue({'validate_idle','validate_record','plan'}.issubset(names))

    def test_human_handbook_has_navigation_and_no_private_material(self):
        path=ROOT/'docs/11-手册/05-自动化手册/README.md'
        text=path.read_text()
        for marker in ('PR待审','完整交付','dots','Codex','桥接','采纳','验收','暂停','恢复','尚未启用'):
            self.assertIn(marker,text)
        for name,pattern in SECRET_PATTERNS.items():
            self.assertIsNone(re.search(pattern,text),name)
        self.assertNotRegex(text,r'(?:wecom-intake:|chat_id|source_request_id|SLACK_.*TOKEN|Sentinel_|issuecomment-\d+)')
        self.assertIn('05-自动化手册/README.md',(ROOT/'docs/11-手册/README.md').read_text())

    def test_ci_only_runs_offline_helpers(self):
        text=(ROOT/'.github/workflows/hiveflow.yml').read_text()
        self.assertIn('contents: read',text)
        self.assertNotRegex(text,r'(?m)^\s*(?:schedule|cron|secrets):')
        self.assertNotRegex(text,r'(?:dev:deploy|automation_update|issues: write|contents: write)')
        self.assertIn('scripts/hiveflow/test_adoption_record.py',text)


if __name__ == '__main__': unittest.main()
