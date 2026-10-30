"""保存/读取同仓可信Issue基线，一次核验受控登记同步；不修改Issue。"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from adopted_issue import REPOSITORY, candidate, require, managed_body, issue_identity


def baseline_path(number):
    require(type(number) is int and number > 0, 'baseline Issue')
    return f'.hiveflow/control/baselines/{number}.json'


def checksum(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


def make_baseline(issue, actor, boundary, now):
    require(set(issue) == {'repository', 'number', 'url', 'state', 'createdAt', 'author', 'editor', 'body'}, 'baseline Issue fields')
    identity = candidate(issue, boundary, now, actor)
    # 整段正文只参与哈希，不额外提交Slack来源URL、路由或正文中的敏感材料。
    metadata = {key: value for key, value in issue.items() if key != 'body'}
    record = dict(schemaVersion=2, repository=REPOSITORY, identity=identity, issue=metadata,
                  bodySHA256=hashlib.sha256(managed_body(issue['body'])[1].encode()).hexdigest())
    return dict(record, sha256=checksum(record))


def check_baseline(record, number, actor, boundary, now):
    from re import fullmatch
    require(type(record) is dict and set(record) == {'schemaVersion', 'repository', 'identity', 'issue', 'bodySHA256', 'sha256'}
            and type(record['schemaVersion']) is int and record['schemaVersion'] == 2
            and record['repository'] == REPOSITORY, 'baseline identity')
    metadata = record['issue']
    require(type(metadata) is dict and set(metadata) == {'repository', 'number', 'url', 'state', 'createdAt', 'author', 'editor'}
            and metadata['number'] == number and metadata['author'] == metadata['editor'] == actor
            and metadata['state'] == 'open', 'baseline source identity')
    basic = issue_identity(metadata, boundary, now)
    identity = record['identity']
    require(type(identity) is dict and set(identity) == set(basic) | {'scopeSHA', 'sourceMarker'}
            and all(identity[k] == v for k,v in basic.items()), 'baseline scope identity')
    require(type(identity['scopeSHA']) is str and fullmatch('[0-9a-f]{64}',identity['scopeSHA'])
            and type(identity['sourceMarker']) is str and fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',identity['sourceMarker'])
            and type(record['bodySHA256']) is str and fullmatch('[0-9a-f]{64}',record['bodySHA256']), 'baseline digests')
    require(record['sha256'] == checksum({k:v for k,v in record.items() if k != 'sha256'}), 'baseline integrity/source')
    return metadata


def save_baseline(store, issue, actor, boundary, now):
    proposal = make_baseline(issue, actor, boundary, now)
    path = baseline_path(issue['number'])
    existing, sha = store.read(path)
    if existing is not None:
        check_baseline(existing, issue['number'], actor, boundary, now)
        require(existing == proposal, 'baseline exists with different content; do not overwrite')
        return sha
    return store.write(path, proposal, None)


def expected_block(store, run, number):
    # 下载同一成功同步提交中的三个明确文件；执行的是已合入的可信同步工具，非Issue命令。
    sha = run['head_sha']
    paths = ['scripts/workflows/manage-issue.mjs', 'scripts/lib/governance/issue-registry.mjs',
             'docs/03-实施/ISSUE/README.md']
    with tempfile.TemporaryDirectory(prefix='hiveflow-sync-') as folder:
        root = Path(folder)
        for path in paths:
            obj = store.api(f'repos/{REPOSITORY}/contents/{path}?ref={sha}')
            require(obj['type'] == 'file' and obj['encoding'] == 'base64', 'sync source file')
            dest = root / path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(obj['content']))
        code = '''import {readFileSync} from 'node:fs';
import {managedIssueBlock} from './scripts/workflows/manage-issue.mjs';
import {extractIssueRows} from './scripts/lib/governance/issue-registry.mjs';
const data=JSON.parse(readFileSync(0,'utf8'));
const matches=extractIssueRows(readFileSync('docs/03-实施/ISSUE/README.md','utf8')).filter(row=>
 row.external?.includes('https://github.com/'+data.repository+'/issues/'+data.number+')'));
if(matches.length!==1) throw Error('Issue mapping missing or duplicated');
process.stdout.write(managedIssueBlock(matches[0],data.repository));'''
        return subprocess.check_output(['node', '--input-type=module', '-e', code], cwd=root,
            input=json.dumps({'repository': REPOSITORY, 'number': number}).encode()).decode()


def verify_source(store, issue, actor, boundary, now, workflow_id=None):
    if issue['editor'] == actor:
        return dict(identity=candidate(issue, boundary, now, actor, allow_closed=True), sourceEvidence=None)
    baseline, _ = store.read(baseline_path(issue['number']))
    require(baseline is not None, 'shared baseline missing; recover original verified snapshot before continuing')
    original = check_baseline(baseline, issue['number'], actor, boundary, now)
    require(all(issue[k] == original[k] for k in ('repository', 'number', 'url', 'createdAt', 'author')), 'source identity changed')
    require(issue['editor'] == 'github-actions' and type(workflow_id) is int and workflow_id > 0,
            'official sync workflow evidence required')
    bot = store.api('users/github-actions%5Bbot%5D')
    require(bot['id'] == 41898282 and bot['type'] == 'Bot' and bot['login'] == 'github-actions[bot]', 'official bot identity')
    run = store.api(f'repos/{REPOSITORY}/actions/runs/{workflow_id}')
    # 在执行同步版本的Node模块前先核验真实响应的来源、状态与固定SHA。
    require(run['repository']['full_name'] == REPOSITORY and run['path'] == '.github/workflows/issue-sync.yml'
            and run['head_branch'] == 'main' and run['status'] == 'completed' and run['conclusion'] == 'success'
            and run['event'] in ('push', 'workflow_dispatch'), 'untrusted sync workflow')
    from re import fullmatch
    require(type(run['head_sha']) is str and fullmatch('[0-9a-f]{40}', run['head_sha']), 'sync commit')
    fields = ('id', 'path', 'head_branch', 'head_sha', 'status', 'conclusion', 'event', 'html_url')
    evidence = dict(baselineRecord=baseline, editor={'login': 'github-actions', '__typename': 'Bot', 'databaseId': bot['id']},
                    workflowRun=dict(repository=REPOSITORY, **{k: run[k] for k in fields}),
                    expectedManagedBlock=expected_block(store, run, issue['number']))
    return dict(identity=candidate(issue, boundary, now, actor, allow_closed=True, source_evidence=evidence), sourceEvidence=evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('save', 'verify'))
    parser.add_argument('--issue-json', required=True, type=Path)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--boundary', required=True)
    parser.add_argument('--workflow-id', type=int)
    parser.add_argument('--run-id')
    parser.add_argument('--mode', choices=('pr_review', 'full_delivery'))
    args = parser.parse_args()
    from control_store import GitHubStore
    try:
        issue = json.loads(args.issue_json.read_text())
        now = datetime.now(timezone.utc).isoformat()
        store = GitHubStore()
        if args.operation == 'save':
            from execution_control import apply
            apply(store, 'guard', run_id=args.run_id, mode=args.mode, now=now, issue=issue['number'])
            output = {'baselineBlobSHA': save_baseline(store, issue, args.actor, args.boundary, now)}
        else:
            output = verify_source(store, issue, args.actor, args.boundary, now, args.workflow_id)
        print(json.dumps(output, ensure_ascii=False))
        return 0
    except (ValueError, KeyError, TypeError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(json.dumps({'error': str(error), 'nextAction': 'stop_verify_source'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
