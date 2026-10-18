#!/usr/bin/env python3
"""Fresh-directory onboarding rehearsal. Never starts real platform clients."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bridge-source', type=Path, required=True, help='Existing local bridge clone; read-only input')
    parser.add_argument('--cache-source', type=Path, default=Path.home() / '.npm', help='Read only exact public lockfile content digests')
    parser.add_argument('--report', type=Path, default=Path('rehearsal-result.json'))
    args = parser.parse_args()
    report = {'scope': 'offline isolated rehearsal', 'steps': [], 'not_tested': [
        'Real Dots/model integration and account availability', 'Real Slack/WeCom identities, permissions, visibility and roundtrip',
        'Real GitHub issue/comment/state writes and formal ledger', 'Cross-version schema migration and production rollback',
        'OS/data-domain hard isolation, live reconnect and interruption']}
    try:
        bridge_source = args.bridge_source.resolve()
        assert (bridge_source / '.git').exists(), 'Bridge input must be an existing clone'
        with tempfile.TemporaryDirectory(prefix='hiveflow-onboarding-') as folder:
            area = Path(folder).resolve()
            cache = area / 'cache'
            scratch = area / 'tmp'
            scratch.mkdir()
            empty_config = area / 'empty-config'
            empty_config.write_text('')
            empty_global_config = area / 'empty-global-config'
            empty_global_config.write_text('')
            env = {'PATH': os.environ['PATH'], 'TMPDIR': str(scratch), 'GIT_CONFIG_GLOBAL': os.devnull,
                   'GIT_CONFIG_NOSYSTEM': '1', 'npm_config_userconfig': str(empty_config),
                   'npm_config_globalconfig': str(empty_global_config), 'npm_config_cache': str(cache), 'npm_config_offline': 'true'}

            def run(name, command, cwd, expected=0):
                result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
                report['steps'].append({'name': name, 'exit_code': result.returncode, 'passed': result.returncode == expected})
                if result.returncode != expected:
                    raise RuntimeError(name + ' failed; no network fallback attempted')
                return result.stdout

            node = shutil.which('node')
            npm = shutil.which('npm')
            assert node and npm, 'Node.js24 and npm required'
            version = run('node_version', [node, '--version'], ROOT).strip()
            assert version.startswith('v24.'), 'Use Node.js24.x'
            report['node'] = version
            report['bridge_commit'] = run('local_bridge_ref', ['git', 'rev-parse', 'HEAD'], bridge_source).strip()
            spec = area / 'spec'
            spec.mkdir()
            paths = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT).decode().split('\0')
            for name in filter(None, paths):
                origin = ROOT / name
                assert not origin.is_symlink() and origin.resolve().is_relative_to(ROOT), 'Source leaves repository'
                target = spec / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, target)
            run('fresh_spec_index', ['git', 'init', '-q'], spec)
            run('index_documentation', ['git', 'add', '.'], spec)
            run('fresh_documentation_check', ['python3', 'scripts/check_docs.py'], spec)
            bridge = area / 'bridge'
            bridge.mkdir()
            archive = area / 'bridge.tar'
            with archive.open('wb') as out:
                subprocess.run(['git', 'archive', '--format=tar', 'HEAD'], cwd=bridge_source, env=env, stdout=out, check=True)
            with tarfile.open(archive) as bundle:
                for member in bundle.getmembers():
                    assert member.isfile() or member.isdir(), 'Archive symlinks are not accepted in this exercise'
                    assert (bridge / member.name).resolve().is_relative_to(bridge), 'Archive path leaves destination'
                bundle.extractall(bridge)
            lock = json.loads((bridge / 'package-lock.json').read_text())
            seeded = set()
            for package in lock.get('packages', {}).values():
                integrity, url = package.get('integrity'), package.get('resolved')
                if not integrity:
                    continue
                assert urlsplit(url or '').hostname in ['registry.npmjs.org', 'registry.npmmirror.com'], 'Non-public package source'
                algorithm, encoded = integrity.split('-', 1)
                digest = base64.b64decode(encoded).hex()
                content = Path('_cacache/content-v2') / algorithm / digest[:2] / digest[2:4] / digest[4:]
                original = args.cache_source / content
                if original.is_file():
                    data = original.read_bytes()
                    assert hashlib.new(algorithm, data).hexdigest() == digest, 'Cache digest mismatch'
                    target = cache / content
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    seeded.add(integrity)
            report['public_dependency_digests_seeded'] = len(seeded)
            run('fresh_offline_production_install', [npm, 'ci', '--offline', '--omit=dev', '--ignore-scripts', '--no-audit', '--no-fund'], bridge)
            shutil.copyfile(bridge / '.env.example', bridge / '.env')
            os.chmod(bridge / '.env', 0o600)
            run('empty_config_rejected', [node, '--env-file=.env', 'scripts/check-config.js'], bridge, expected=78)
            values = {'WECOM_BOT_ID': 'demo-bot', 'WECOM_BOT_SECRET': 'placeholder-only', 'WECOM_OWNER_USER_ID': 'demo-owner',
                      'WECOM_ALLOWED_CHAT_IDS': 'demo-group', 'SLACK_BOT_TOKEN': 'placeholder-only', 'SLACK_APP_TOKEN': 'placeholder-only',
                      'SLACK_CHANNEL_ID': 'CDEMO', 'SLACK_REPLY_BOT_ID': 'BRESPONDER', 'STATE_DIR': './exercise-state'}
            (bridge / '.env').write_text('\n'.join(key + '=' + value for key, value in values.items()) + '\n')
            run('placeholder_config_syntax', [node, '--env-file=.env', 'scripts/check-config.js'], bridge)
            assert not (bridge / 'exercise-state').exists(), 'Config check must not create state'
            tests = run('bridge_existing_offline_checks', [npm, 'run', 'check'], bridge)
            report['bridge_test_summary'] = [line for line in tests.splitlines() if line.startswith(('ℹ tests ', 'ℹ pass ', 'ℹ fail ', '# tests ', '# pass ', '# fail '))]
            result = run('transport_backup_recovery_fixture', [node, str(spec / 'scripts/onboarding_transport.mjs'), str(bridge), str(area / 'exercise')], spec)
            report['simulation'] = json.loads(result.strip().splitlines()[-1])
            report['result'] = 'passed_offline_only'
    except Exception as error:
        report['result'] = 'incomplete'
        report['failure'] = {'type': type(error).__name__, 'summary': str(error) if isinstance(error, (AssertionError, RuntimeError)) else 'See failed step; no credentials or raw output captured'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['result'] == 'passed_offline_only' else 1


if __name__ == '__main__':
    raise SystemExit(main())
