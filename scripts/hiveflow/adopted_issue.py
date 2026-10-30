"""通用采纳选单与逐单认领的离线提案；不访问网络、不执行研发。"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
import sys
import unicodedata

REPOSITORY = 'xiiiblue/hive-os'
BRANCH = 'chore/hiveflow-control'
POLICY = 'dots-adopted-issues-v1'
MODES = ('pr_review', 'full_delivery')
STATES = ('claimed', 'finalizing', 'pr_review', 'full_delivery', 'blocked', 'unknown', 'finalizing_blocked', 'finalizing_unknown')
SECTION_TITLES = {
    '## 问题或需求说明': ('问题或需求说明', '问题与需求说明', '问题/需求说明', '问题或需求',
                    '问题与需求', '问题/需求', '问题说明', '需求说明', '问题描述', '需求描述'),
    '## 期望 / 调整范围': ('期望/调整范围', '期望与调整范围', '期望或调整范围', '期望调整范围',
                    '期望与范围', '调整范围', '变更范围', '改动范围', '实施范围'),
    '## 验收要点': ('验收要点', '验收标准', '验收要求', '验收条件', '验收清单'),
}
NOTE_TITLES = ('备注', '附注', '补充说明', '初步分析', '初步分析与建议', '来源', '来源与静态核验依据')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def stamp(value):
    require(type(value) is str, 'timestamp required')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, 'timezone required')
    return result


def issue_identity(issue, boundary, now):
    require(issue['repository'] == REPOSITORY, 'wrong repository')
    number = issue['number']
    require(type(number) is int and number > 0, 'ordinary Issue number required')
    require(issue['url'] == f'https://github.com/{REPOSITORY}/issues/{number}', 'not ordinary Issue URL')
    require(issue['state'] in ('open', 'closed'), 'Issue state')
    require(stamp(boundary) <= stamp(issue['createdAt']) <= stamp(now), 'outside creation boundary')
    return dict(repository=REPOSITORY, issueNumber=number, issueCreatedAt=issue['createdAt'])


def outside_code_and_comments(body):
    """来源标记本身是完整单行注释；其它隐藏/引用内容不作来源证据。"""
    lines = body.splitlines()
    output, fence, comment, raw_tag = [''] * len(lines), None, False, None
    for index, line in enumerate(lines):
        if fence:
            if re.fullmatch(r' {0,3}' + re.escape(fence[0]) + '{' + str(len(fence)) + r',}\s*', line):
                fence = None
            continue
        if raw_tag:
            if re.search(r'</' + raw_tag + r'\s*>', line, re.I):
                raw_tag = None
            continue
        if not comment and re.match(r' {0,3}>| {4}|\t', line):
            continue
        opening = re.match(r' {0,3}(`{3,}|~{3,})', line)
        if opening and not comment:
            fence = opening.group(1)
            continue
        if not comment and re.fullmatch(r'<!-- wecom-intake:[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12} -->', line):
            output[index] = line
            continue
        # 一行可关闭旧注释再开启新注释，不能只检查第一个结束符。
        rest, commented = line, comment
        while True:
            if comment:
                end = rest.find('-->')
                if end < 0:
                    break
                rest, comment = rest[end + 3:], False
            start = rest.find('<!--')
            if start < 0:
                break
            rest, comment, commented = rest[start + 4:], True, True
        if commented:
            continue
        opening = re.search(r'<(pre|code|script|style|textarea)\b[^>]*>', line, re.I)
        if opening:
            tag = opening.group(1).lower()
            if not re.search(r'</' + tag + r'\s*>', line[opening.end():], re.I):
                raw_tag = tag
            continue
        output[index] = line
    return '\n'.join(output)


def normalized_title(title):
    """只兼容确定的标题别名与排版；正文和权限字段不作模糊匹配。"""
    title = unicodedata.normalize('NFKC', title).strip()
    # 编号可在加粗标记内或外，兼容“二、”“2.”和“（二）”。
    for _ in range(2):
        title = title.rstrip(':').strip()
        emphasis = re.fullmatch(r'(\*\*|__)(.+)\1', title)
        if emphasis:
            title = emphasis.group(2)
        title = re.sub(r'^(?:\((?:\d+|[一二三四五六七八九十]+)\)|(?:\d+|[一二三四五六七八九十]+)[.、):])\s*', '', title)
    return re.sub(r'\s+', '', title).rstrip(':')


def scope_sections(body):
    lines = body.splitlines()
    headings = []
    # 保留原行号与正文，用可见行识别真实标题，避免把代码/引文/注释当作章节。
    for index, line in enumerate(outside_code_and_comments(body).split('\n')):
        match = re.fullmatch(r' {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*', line)
        if match:
            title = normalized_title(match.group(2))
            canonical = next((name for name, aliases in SECTION_TITLES.items() if title in aliases), None)
            headings.append((index, len(match.group(1)), canonical, title in NOTE_TITLES))
    sections = []
    for canonical in SECTION_TITLES:
        matches = [heading for heading in headings if heading[2] == canonical]
        require(len(matches) == 1, f'missing or duplicated section: {canonical}')
        index, level, _, _ = matches[0]
        end = next((other for other, other_level, title, note in headings
                    if other > index and (other_level <= level or title is not None or note)), len(lines))
        content = '\n'.join(lines[index + 1:end]).strip()
        # 真实章节可用引用、示例代码表达要求；只排除没有正文的注释占位。
        require(bool(re.sub(r'<!--.*?-->', '', content, flags=re.S).strip()), f'empty section: {canonical}')
        # 旧标准标题保持原摘要；换成同义标题也不改变范围身份。
        sections.append([canonical, content])
    header = '\n'.join(lines[:headings[0][0]]) if headings else body
    return header, sections


def managed_body(body):
    """返回唯一受管块和剩余正文；只忽略同步入口增加的首尾空白。"""
    require(type(body) is str, 'body required')
    start, end = '<!-- HIVE-ISSUE-SYNC:START -->', '<!-- HIVE-ISSUE-SYNC:END -->'
    require(body.count(start) == body.count(end) and body.count(start) <= 1, 'managed block mismatch')
    if start not in body:
        return None, body.strip()
    require(body.index(start) < body.index(end), 'managed block order')
    first, last = body.index(start), body.index(end) + len(end)
    return body[first:last], (body[:first] + body[last:]).strip()


def managed_sync_source(issue, boundary, now, actor, evidence):
    """只校验真实读取证据的结构；GitHub身份、工作流与本地基线由宿主认证。"""
    require(type(evidence) is dict and set(evidence) in ({'baselineIssue', 'editor', 'workflowRun', 'expectedManagedBlock'},
        {'baselineRecord', 'editor', 'workflowRun', 'expectedManagedBlock'}), 'managed sync evidence required')
    if 'baselineRecord' in evidence:
        from source_evidence import check_baseline
        baseline = check_baseline(evidence['baselineRecord'], issue['number'], actor, boundary, now)
    else:
        baseline = evidence['baselineIssue']
        candidate(baseline, boundary, now, actor)
    for key in ('repository', 'number', 'url', 'createdAt', 'author'):
        require(issue[key] == baseline[key], 'managed sync identity changed')
    require(evidence['editor'] == {'login': 'github-actions', '__typename': 'Bot', 'databaseId': 41898282}
            and issue['editor'] == 'github-actions', 'managed sync editor identity')
    run = evidence['workflowRun']
    require(type(run) is dict and set(run) == {
        'id', 'repository', 'path', 'head_branch', 'head_sha', 'status', 'conclusion', 'event', 'html_url'},
        'managed sync workflow fields')
    require(type(run['id']) is int and run['id'] > 0
            and run['repository'] == REPOSITORY and run['path'] == '.github/workflows/issue-sync.yml'
            and run['head_branch'] == 'main' and run['event'] in ('push', 'workflow_dispatch')
            and run['status'] == 'completed' and run['conclusion'] == 'success'
            and type(run['head_sha']) is str and re.fullmatch('[0-9a-f]{40}', run['head_sha'])
            and run['html_url'] == f'https://github.com/{REPOSITORY}/actions/runs/{run["id"]}',
            'managed sync workflow mismatch')
    block, body = managed_body(issue['body'])
    require(block is not None and block == evidence['expectedManagedBlock'], 'managed sync block mismatch')
    if 'baselineRecord' in evidence:
        require(hashlib.sha256(body.encode()).hexdigest() == evidence['baselineRecord']['bodySHA256'], 'managed sync changed original body')
    else:
        require(body == managed_body(baseline['body'])[1], 'managed sync changed original body')


def candidate(issue, boundary, now, actor, allow_closed=False, source_evidence=None):
    """输入必须来自真实GitHub读取；函数不能认证调用者提供的事实。"""
    identity = issue_identity(issue, boundary, now)
    require(issue['state'] == 'open' or allow_closed, 'Issue is not open')
    require(type(actor) is str and bool(actor), 'configured actor required')
    require(issue['author'] == actor, 'untrusted author/editor')
    if issue['editor'] != actor:
        managed_sync_source(issue, boundary, now, actor, source_evidence)
    require(type(issue['body']) is str, 'body required')
    body = issue['body']
    # 唯一允许的受管进度块来自既有同步入口；不把它计入目标/验收摘要。
    start, end = '<!-- HIVE-ISSUE-SYNC:START -->', '<!-- HIVE-ISSUE-SYNC:END -->'
    require(body.count(start) == body.count(end) and body.count(start) <= 1, 'managed block mismatch')
    if start in body:
        require(body.index(start) < body.index(end), 'managed block order')
        body = body[:body.index(start)] + body[body.index(end) + len(end):]
    header, sections = scope_sections(body)
    header = header.splitlines()
    matches = [line for line in header if line.startswith('评审状态：')]
    require(matches == ['评审状态：已采纳（待实施授权）'], 'adoption field mismatch')
    require(not any(line.lstrip().startswith(('```', '~~~')) or '<' in line or '>' in line for line in header), 'hidden/quoted adoption')
    markers = re.findall(r'(?m)^<!-- wecom-intake:([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}) -->$', outside_code_and_comments(body))
    require(len(markers) == 1, 'one controlled intake marker required')
    scope = json.dumps(sections, ensure_ascii=False, separators=(',', ':'))
    result = dict(**identity, scopeSHA=hashlib.sha256(scope.encode()).hexdigest(), sourceMarker=markers[0])
    if source_evidence is not None and 'baselineRecord' in source_evidence:
        require(result == source_evidence['baselineRecord']['identity'], 'shared baseline scope mismatch')
    return result


def record_path(number):
    require(type(number) is int and number > 0, 'Issue number required')
    return f'.hiveflow/control/issues/{number}.json'


def validate_record(record, identity):
    keys = set('schemaVersion policyId repository issueNumber issueCreatedAt scopeSHA sourceMarker runId mode state revision evidenceUrls'.split())
    require(type(record) is dict and set(record) == keys, 'record fields')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 2, 'record schema')
    require(record['policyId'] == POLICY, 'policy mismatch')
    for key in ('repository', 'issueNumber', 'issueCreatedAt', 'sourceMarker'):
        require(record[key] == identity[key], 'record identity mismatch')
    require(type(record['issueNumber']) is int, 'record Issue number')
    require(type(record['scopeSHA']) is str and re.fullmatch('[0-9a-f]{64}', record['scopeSHA']), 'scope digest')
    require(type(record['runId']) is str and bool(record['runId'].strip()), 'real run identity required')
    require(record['mode'] in MODES and record['state'] in STATES, 'record mode/state')
    require(not record['state'].startswith('finalizing') or record['mode'] == 'full_delivery', 'finalizing mode')
    require(type(record['revision']) is int and record['revision'] >= 1, 'revision')
    urls = record['evidenceUrls']
    require(type(urls) is list and len(urls) <= 10, 'evidence list')
    for url in urls:
        require(type(url) is str and re.fullmatch(r'https://github\.com/' + REPOSITORY + r'/(?:pull/[1-9][0-9]*|commit/[0-9a-f]{40}|issues/[1-9][0-9]*)', url), 'evidence URL')
    require(record['state'] not in MODES or bool(urls), 'completion evidence required')
    require(record['state'] not in MODES or record['state'] == record['mode'], 'terminal mode mismatch')
    return record


def plan(data):
    required = set('operation issue boundary now actor runId mode record blobSHA result evidenceUrls'.split())
    require(type(data) is dict and required <= set(data)
            and set(data) <= required | {'sourceEvidence', 'resumeAuthorization', 'handoffAuthorization', 'executionRecord', 'resolutionEvidence'}, 'input fields')
    require(data['mode'] in MODES, 'mode')
    require(type(data['runId']) is str and bool(data['runId'].strip()), 'real run identity required')
    op = data['operation']
    require(op in ('claim', 'guard', 'finish', 'resume', 'handoff', 'notify', 'resolve'), 'operation')
    record = deepcopy(data['record'])
    require(data.get('resumeAuthorization') is None or op == 'resume', 'resume authorization only for resume')
    require(data.get('sourceEvidence') is None or record is not None, 'initial adoption requires trusted editor')
    require(data.get('handoffAuthorization') is None or op == 'handoff', 'handoff authorization only for handoff')
    require(data.get('resolutionEvidence') is None or op == 'resolve', 'resolution evidence only for resolve')
    require(data.get('executionRecord') is None or op in ('handoff', 'resolve'), 'execution evidence only for transfer/resolution')
    stopping = op == 'finish' and data['result'] in ('blocked', 'unknown')
    if stopping:
        require(type(record) is dict, 'no claim for stopped result')
        identity = dict(**issue_identity(data['issue'], data['boundary'], data['now']),
                        sourceMarker=record.get('sourceMarker'))
    else:
        identity = candidate(data['issue'], data['boundary'], data['now'], data['actor'], allow_closed=(op in ('finish', 'handoff', 'notify', 'resolve')),
                             source_evidence=data.get('sourceEvidence'))
    sha = data['blobSHA']
    require((record is None and sha is None) or (record is not None and type(sha) is str and re.fullmatch('[0-9a-f]{40}', sha)), 'record/blob mismatch')
    result = dict(branch=BRANCH, path=record_path(identity['issueNumber']), write=False, record=record)
    if record is not None:
        validate_record(record, identity)
    if op == 'claim':
        require(data['result'] is None and data['evidenceUrls'] == [], 'claim result must be empty')
        if record is not None:
            result['nextAction'] = 'existing_record_do_not_restart'
            return result
        record = dict(schemaVersion=2, policyId=POLICY, **identity, runId=data['runId'],
                      mode=data['mode'], state='claimed', revision=1, evidenceUrls=[])
        result.update(write=True, record=record, nextAction='create_without_sha_then_readback')
        return result
    if op == 'handoff':
        # 占用转移先在全仓执行记录完成；本单仍按旧SHA更新，途中失败保留占用。
        from execution_control import validate as validate_execution, handoff_authorization
        require(record is not None and data['result'] is None and data['evidenceUrls'] == [], 'handoff input')
        execution = validate_execution(data['executionRecord'])
        require(execution['state'] == 'held' and execution['owner'] == {
            'runId': data['runId'], 'mode': data['mode'], 'issueNumber': identity['issueNumber']}, 'handoff execution owner')
        handoff_authorization(data['handoffAuthorization'], old_run=record['runId'], new_run=data['runId'],
                              issue=identity['issueNumber'], mode=data['mode'], sha=sha, key='issueBlobSHA',
                              now=data['now'], since=record['issueCreatedAt'])
        require(record['scopeSHA'] == identity['scopeSHA'], 'handoff scope changed')
        if record['state'] == 'full_delivery':
            require(data['mode'] == 'full_delivery' and data['issue']['state'] == 'closed', 'completed handoff is notification only')
            action = 'notification_only'
        elif record['state'] == 'pr_review' and data['mode'] == 'pr_review':
            require(data['issue']['state'] == 'open', 'PR notification requires open Issue')
            action = 'notification_only'
        elif record['state'].startswith('finalizing') or data['issue']['state'] == 'closed':
            require(data['mode'] == 'full_delivery', 'finalization requires full delivery mode')
            record['state'] = 'finalizing'
            action = 'finalize_only'
        else:
            require(data['issue']['state'] == 'open' and record['state'] != 'finalizing', 'unfinished handoff requires open Issue')
            record['state'] = 'claimed'
            action = 'continue_verified_remaining_stages'
        record.update(runId=data['runId'], mode=data['mode'], revision=record['revision'] + 1)
        validate_record(record, identity)
        return dict(result, write=True, record=record, expectedBlobSHA=sha, nextAction=action)
    if op == 'resolve':
        from execution_control import validate as validate_execution
        require(record is not None and record['state'] in ('unknown', 'finalizing_unknown', 'finalizing_blocked') and record['runId'] == data['runId']
                and record['mode'] == data['mode'] and record['scopeSHA'] == identity['scopeSHA']
                and (data['issue']['state'] == 'open' or data['mode'] == 'full_delivery')
                and data['result'] is None, 'resolution ownership/scope')
        execution = validate_execution(data['executionRecord'])
        require(execution['state'] == 'held' and execution['owner'] == {
            'runId': data['runId'], 'mode': data['mode'], 'issueNumber': identity['issueNumber']}, 'resolution execution owner')
        proof = data['resolutionEvidence']
        require(type(proof) is dict and set(proof) == {'recordBlobSHA', 'verifiedAt'}
                and proof['recordBlobSHA'] == sha and stamp(identity['issueCreatedAt']) <= stamp(proof['verifiedAt']) <= stamp(data['now'])
                and bool(data['evidenceUrls']), 'verified resolution evidence required')
        record.update(state='finalizing' if record['state'].startswith('finalizing') or data['issue']['state'] == 'closed' else 'claimed', revision=record['revision'] + 1,
                      evidenceUrls=list(dict.fromkeys(record['evidenceUrls'] + data['evidenceUrls'])))
        validate_record(record, identity)
        return dict(result, write=True, record=record, expectedBlobSHA=sha, nextAction='finalize_only' if record['state'] == 'finalizing' else 'continue_verified_remaining_stages')
    require(data.get('resolutionEvidence') is None, 'resolution fields only for resolve')
    require(data.get('handoffAuthorization') is None and data.get('executionRecord') is None, 'handoff fields only for handoff')
    if op == 'notify':
        require(record is not None and record['state'] == record['mode'] == data['mode']
                and record['runId'] == data['runId'] and record['scopeSHA'] == identity['scopeSHA']
                and data['result'] is None and data['evidenceUrls'] == [], 'notification ownership/scope')
        require(data['issue']['state'] == ('closed' if data['mode'] == 'full_delivery' else 'open'), 'notification endpoint state')
        return dict(result, nextAction='notification_only')
    require(record is not None and (record['state'] == ('blocked' if op == 'resume' else 'claimed')
            or op == 'finish' and (record['state'] == 'finalizing'
                or stopping and record['state'].startswith('finalizing'))), 'no live claim')
    require(record['runId'] == data['runId'] and record['mode'] == data['mode'], 'another run/mode owns claim')
    require(stopping or record['scopeSHA'] == identity['scopeSHA'], 'scope changed; inspect before continuing')
    if op == 'resume':
        require(data['result'] is None and data['evidenceUrls'] == [] and data['issue']['state'] == 'open',
                'resume requires open unfinished Issue')
        authorization = data.get('resumeAuthorization')
        require(type(authorization) is dict and set(authorization) == {
            'actor', 'runId', 'issueNumber', 'mode', 'source', 'approvedAt',
            'blockedRevision', 'blockedBlobSHA', 'blockedAt'}, 'explicit resume authorization required')
        require(authorization['actor'] == data['actor'] and authorization['runId'] == data['runId']
                and authorization['issueNumber'] == identity['issueNumber'] and authorization['mode'] == data['mode']
                and authorization['source'] == 'trusted_user_channel'
                and type(authorization['blockedRevision']) is int
                and authorization['blockedRevision'] == record['revision']
                and authorization['blockedBlobSHA'] == sha
                and stamp(identity['issueCreatedAt']) <= stamp(authorization['blockedAt'])
                <= stamp(authorization['approvedAt']) <= stamp(data['now']),
                'resume authorization mismatch')
        record.update(state='claimed', revision=record['revision'] + 1)
        result.update(write=True, record=record, expectedBlobSHA=sha, nextAction='resume_same_run_with_sha_then_readback')
        return result
    if op == 'guard':
        require(data['result'] is None and data['evidenceUrls'] == [], 'guard result must be empty')
        result['nextAction'] = 'check_live_task_and_side_effects_before_action'
        return result
    require(data['result'] in (data['mode'], 'blocked', 'unknown'), 'finish stage')
    if data['result'] in MODES:
        expected_state = 'closed' if data['mode'] == 'full_delivery' else 'open'
        require(data['issue']['state'] == expected_state, 'Issue state does not match successful endpoint')
    next_state = 'finalizing_' + data['result'] if record['state'].startswith('finalizing') and data['result'] in ('blocked', 'unknown') else data['result']
    record.update(state=next_state, revision=record['revision'] + 1, evidenceUrls=data['evidenceUrls'])
    validate_record(record, identity)
    result.update(write=True, record=record, expectedBlobSHA=sha, nextAction='update_with_sha_then_readback')
    return result


def main():
    try:
        raw = json.load(sys.stdin)
        print(json.dumps(plan(raw), ensure_ascii=False, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps(dict(error=str(exc)), ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
