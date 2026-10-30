"""跨运行/机器共享的HiveFlow执行占用；只写既有控制分支，不调度研发。"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from pathlib import Path
from adopted_issue import REPOSITORY, MODES, require, stamp

PATH = '.hiveflow/control/execution.json'


def validate(record):
    require(type(record) is dict and set(record) == {
        'schemaVersion', 'repository', 'revision', 'state', 'owner', 'updatedAt'}, 'execution fields')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 1
            and record['repository'] == REPOSITORY, 'execution identity')
    require(type(record['revision']) is int and record['revision'] > 0, 'execution revision')
    stamp(record['updatedAt'])
    require(record['state'] in ('idle', 'held', 'unknown'), 'execution state')
    if record['state'] == 'idle':
        require(record['owner'] is None, 'idle owner')
    else:
        owner = record['owner']
        require(type(owner) is dict and set(owner) == {'runId', 'mode', 'issueNumber'}, 'owner fields')
        require(type(owner['runId']) is str and bool(owner['runId'].strip()) and owner['mode'] in MODES, 'owner identity')
        require(owner['issueNumber'] is None or type(owner['issueNumber']) is int
                and owner['issueNumber'] > 0, 'owner Issue')
    return record


def handoff_authorization(auth, *, old_run, new_run, issue, mode, sha, key, now, since):
    # 只检查结构与绑定。本人指令、旧运行结束和实际副作用由宿主独立核验。
    require(type(auth) is dict and set(auth) == {
        'source', 'approvedAt', 'previousRunId', 'nextRunId', 'previousRunEndedAt',
        'resultsVerifiedAt', 'lockBlobSHA', 'issueBlobSHA', 'issueNumber', 'mode',
        'previousIssueRunId', 'previousIssueRunEndedAt'}, 'handoff authorization fields')
    previous_key = 'previousRunId' if key == 'lockBlobSHA' else 'previousIssueRunId'
    ended_key = 'previousRunEndedAt' if key == 'lockBlobSHA' else 'previousIssueRunEndedAt'
    require(auth['source'] == 'trusted_user_channel' and auth[previous_key] == old_run
            and auth['nextRunId'] == new_run and old_run != new_run
            and auth['issueNumber'] == issue and auth['mode'] == mode
            and auth[key] == sha, 'handoff authorization mismatch')
    require(type(auth['lockBlobSHA']) is str and re.fullmatch('[0-9a-f]{40}', auth['lockBlobSHA']), 'handoff lock hash')
    require(auth['issueBlobSHA'] is None if issue is None else type(auth['issueBlobSHA']) is str
            and re.fullmatch('[0-9a-f]{40}', auth['issueBlobSHA']), 'handoff Issue hash')
    require(issue is None or type(auth['issueNumber']) is int, 'handoff Issue type')
    require(stamp(since) <= stamp(auth[ended_key]) <= stamp(auth['resultsVerifiedAt']) <= stamp(now)
            and stamp(auth[ended_key]) <= stamp(auth['approvedAt']) <= stamp(now), 'handoff chronology')


def plan(operation, record, sha, run_id, mode, now, issue=None, authorization=None, results_known=False):
    require(type(run_id) is str and bool(run_id.strip()) and mode in MODES, 'real run/mode required')
    stamp(now)
    require((record is None and sha is None) or record is not None
            and type(sha) is str and re.fullmatch('[0-9a-f]{40}', sha), 'execution record/SHA')
    if record is not None:
        validate(record)
        require(stamp(record['updatedAt']) <= stamp(now), 'execution from future')
    result = {'path': PATH, 'write': False, 'record': record, 'expectedBlobSHA': sha}
    if operation == 'status':
        return dict(result, nextAction='missing' if record is None else record['state'])
    if operation == 'acquire':
        require(issue is None and authorization is None, 'acquire before selection')
        if record is not None and record['state'] != 'idle':
            # 即使同run也不当成新启动；恢复必须先核验原执行位置。
            return dict(result, nextAction='busy_exit')
        next_record = dict(schemaVersion=1, repository=REPOSITORY,
                           revision=1 if record is None else record['revision'] + 1,
                           state='held', owner=dict(runId=run_id, mode=mode, issueNumber=None), updatedAt=now)
        return dict(result, write=True, record=next_record, nextAction='acquired_after_readback')
    require(record is not None and record['state'] in ('held', 'unknown'), 'no execution owner')
    if operation == 'handoff':
        handoff_authorization(authorization, old_run=record['owner']['runId'], new_run=run_id,
                              issue=record['owner']['issueNumber'], mode=mode, sha=sha,
                              key='lockBlobSHA', now=now, since=record['updatedAt'])
        # unknown只能在宿主确认所有外部结果、旧进程已结束后由本人显式接棒。
        updated = deepcopy(record)
        updated.update(state='held', revision=record['revision'] + 1, updatedAt=now)
        updated['owner'].update(runId=run_id, mode=mode)
        return dict(result, write=True, record=updated, nextAction='handoff_issue_before_business')
    require(authorization is None, 'unexpected handoff authorization')
    require(record['owner']['runId'] == run_id and record['owner']['mode'] == mode, 'another execution owns repository')
    if operation == 'guard':
        require(record['state'] == 'held' and record['owner']['issueNumber'] == issue, 'execution guard mismatch')
        return dict(result, nextAction='owned_check_issue_and_actual_results')
    require(operation in ('bind', 'release', 'unknown', 'resolve'), 'execution operation')
    require(record['state'] == ('unknown' if operation == 'resolve' else 'held'), 'unknown execution requires verified resolution')
    updated = deepcopy(record)
    updated.update(revision=record['revision'] + 1, updatedAt=now)
    if operation == 'resolve':
        require(results_known is True, 'resolve requires verified external results')
        updated['state'] = 'held'
    elif operation == 'bind':
        require(type(issue) is int and issue > 0 and record['owner']['issueNumber'] in (None, issue), 'one Issue per execution')
        updated['owner']['issueNumber'] = issue
    elif operation == 'release':
        require(results_known is True, 'release requires verified results and ended writers')
        # 调用方须已结束写操作/子进程、保存本单事实，且所有外部结果已知。
        updated.update(state='idle', owner=None)
    else:
        updated['state'] = 'unknown'
    validate(updated)
    return dict(result, write=True, record=updated, nextAction=operation + '_after_readback')


def apply(store, operation, **kwargs):
    record, sha = store.read(PATH)
    proposal = plan(operation, record, sha, **kwargs)
    if proposal['write']:
        proposal['blobSHA'] = store.write(PATH, proposal['record'], sha)
    return proposal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('status', 'acquire', 'guard', 'bind', 'release', 'unknown', 'resolve', 'handoff'))
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--mode', choices=MODES, required=True)
    parser.add_argument('--issue', type=int)
    parser.add_argument('--authorization', type=Path)
    parser.add_argument('--results-known', action='store_true')
    args = parser.parse_args()
    from control_store import GitHubStore
    try:
        auth = None if args.authorization is None else json.loads(args.authorization.read_text())
        output = apply(GitHubStore(), args.operation, run_id=args.run_id, mode=args.mode,
                       now=datetime.now(timezone.utc).isoformat(), issue=args.issue, authorization=auth, results_known=args.results_known)
        print(json.dumps(output, ensure_ascii=False))
        return 3 if output['nextAction'] == 'busy_exit' else 0
    except (ValueError, KeyError, TypeError, RuntimeError, OSError) as error:
        print(json.dumps({'error': str(error), 'nextAction': 'stop_verify_original_state'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
