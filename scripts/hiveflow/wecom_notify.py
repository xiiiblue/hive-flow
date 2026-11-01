#!/usr/bin/env python3
"""固定受众企微通知：plan预览，send提交一次，status只读本地收据。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata

LIVE_TRANSPORTS_ENABLED = False
DEFAULT_CONFIG = '~/.config/hive-os/wecom-notify.json'


class NoticeError(Exception):
    """异常只携带固定错误码，避免输出Webhook、成员ID或底层网络原文。"""


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def private_dir(path, create=True):
    path = Path(path).expanduser()
    if create:
        path.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise NoticeError('PRIVATE_DIRECTORY_INVALID')
    return path


def private_json(path):
    path = Path(path).expanduser()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as file:
            info = os.fstat(file.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
                raise NoticeError('PRIVATE_FILE_INVALID')
            return json.loads(file.read())
    except (OSError, ValueError):
        raise NoticeError('PRIVATE_FILE_UNREADABLE') from None


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_new(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as file:
        file.write(encoded(value))
        file.flush()
        os.fsync(file.fileno())
    sync_dir(path.parent)


def replace_json(path, value):
    temporary = path.with_name(path.name + '.' + os.urandom(8).hex() + '.tmp')
    write_new(temporary, value)
    os.replace(temporary, path)
    sync_dir(path.parent)


def load_config(path=DEFAULT_CONFIG):
    config = private_json(path)
    required = {'repository', 'group', 'webhook_config', 'directory', 'ledger_dir', 'cache_dir'}
    if not isinstance(config, dict) or set(config) - required - {'cache_ttl_seconds'} or not required <= config.keys():
        raise NoticeError('PROFILE_INVALID')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', config['repository']) or not config['group'].strip():
        raise NoticeError('PROFILE_TARGET_INVALID')
    source = private_json(config['webhook_config'])
    if source.get('transport') != 'wecom_webhook' or source.get('repository') != config['repository']:
        raise NoticeError('WEBHOOK_PROJECT_MISMATCH')
    webhook = source.get('webhook', '')
    try:
        url = urllib.parse.urlsplit(webhook)
        query = urllib.parse.parse_qs(url.query, strict_parsing=True)
        valid = (url.scheme == 'https' and url.netloc == 'qyapi.weixin.qq.com'
                 and url.path == '/cgi-bin/webhook/send' and not url.fragment
                 and list(query) == ['key'] and len(query['key']) == 1
                 and re.fullmatch(r'[A-Za-z0-9-]{16,128}', query['key'][0]))
    except ValueError:
        valid = False
    if not valid:
        raise NoticeError('WEBHOOK_TARGET_INVALID')
    directory = config['directory']
    if (not isinstance(directory, dict) or set(directory) - {'path', 'ssh_host'} or not directory.get('path')
            or not directory['path'].startswith(('/', '~/'))):
        raise NoticeError('MEMBER_SOURCE_INVALID')
    if 'ssh_host' in directory and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@-]{0,199}', directory['ssh_host']):
        raise NoticeError('MEMBER_SSH_HOST_INVALID')
    ttl = config.get('cache_ttl_seconds', 86400)
    if type(ttl) is not int or not 1 <= ttl <= 604800:
        raise NoticeError('MEMBER_CACHE_TTL_INVALID')
    return {**config, 'webhook': webhook, 'targetSHA256': digest(webhook), 'cache_ttl_seconds': ttl}


def valid_member_id(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and not value.startswith('@')
            and not any(c.isspace() or unicodedata.category(c).startswith('C') for c in value))


def match_members(rows, names):
    if not isinstance(rows, list):
        raise NoticeError('MEMBER_DIRECTORY_INVALID')
    selected = []
    for name in names:
        found = [row for row in rows if isinstance(row, dict) and row.get('name') == name]
        if len(found) != 1:
            raise NoticeError('MEMBER_NOT_UNIQUE')
        userid = found[0].get('userid')
        if not valid_member_id(userid):
            raise NoticeError('MEMBER_ID_INVALID')
        selected.append({'name': name, 'userid': userid})
    return selected


def fetch_remote_members(directory, names):
    if not LIVE_TRANSPORTS_ENABLED:
        raise NoticeError('PUBLIC_REFERENCE_OFFLINE_ONLY')
    # 固定远端程序仅输出请求姓名的匹配行，不下载整份通讯录，不执行配置中的命令。
    program = '''import sys,json,hashlib
from pathlib import Path
q=json.load(sys.stdin)
b=Path(q['path']).expanduser().read_bytes();rows=json.loads(b)
assert isinstance(rows,list)
result=[]
for name in q['names']:
 matches=[{'name':r.get('name'),'userid':r.get('userid')} for r in rows if isinstance(r,dict) and r.get('name')==name]
 result.extend(matches)
print(json.dumps({'members':result,'source_sha256':hashlib.sha256(b).hexdigest()},ensure_ascii=False))
'''
    try:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8',
                                 directory['ssh_host'], 'python3 -c ' + shlex.quote(program)],
                                input=encoded({'path': directory['path'], 'names': names}),
                                capture_output=True, timeout=15, check=True)
        if len(result.stdout) > 65536:
            raise NoticeError('MEMBER_SOURCE_TOO_LARGE')
        parsed = json.loads(result.stdout)
        return match_members(parsed['members'], names), parsed['source_sha256']
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        raise NoticeError('MEMBER_SOURCE_UNAVAILABLE') from None


def resolve_members(config, names, refresh=False, offline=False, now=None):
    names = sorted(set(names))
    if len(names) > 100 or any(not isinstance(name, str) or not name.strip() or name.startswith('@') or len(name) > 128 for name in names):
        raise NoticeError('MEMBER_NAME_INVALID')
    if not names:
        return [], 'not_needed'
    now = time.time() if now is None else now
    root = private_dir(config['cache_dir'])
    route = digest(encoded({'repository': config['repository'], 'group': config['group'],
                            'target': config['targetSHA256'], 'directory': config['directory']}))
    cached, missing = {}, []
    for name in names:
        file = root / (digest(route + '\n' + name) + '.json')
        if file.exists() or file.is_symlink():
            item = private_json(file)
            if (not refresh and item.get('route') == route and item.get('name') == name
                    and re.fullmatch(r'[a-f0-9]{64}', str(item.get('source_sha256', '')))
                    and type(item.get('cached_at')) in (int, float)
                    and 0 <= now - item['cached_at'] < config['cache_ttl_seconds']):
                cached[name] = match_members([item], [name])[0]
                continue
        missing.append(name)
    if not missing:
        return [cached[name] for name in names], 'cache'
    if offline:
        raise NoticeError('MEMBER_CACHE_MISS_OFFLINE')
    if config['directory'].get('ssh_host'):
        selected, source_sha = fetch_remote_members(config['directory'], missing)
    else:
        source = Path(config['directory']['path']).expanduser()
        try:
            raw = source.read_bytes()
            selected, source_sha = match_members(json.loads(raw), missing), digest(raw)
        except (OSError, ValueError):
            raise NoticeError('MEMBER_SOURCE_UNAVAILABLE') from None
    if not re.fullmatch(r'[a-f0-9]{64}', source_sha):
        raise NoticeError('MEMBER_SOURCE_DIGEST_INVALID')
    for item in selected:
        cache = {**item, 'route': route, 'source_sha256': source_sha, 'cached_at': now}
        replace_json(root / (digest(route + '\n' + item['name']) + '.json'), cache)
        cached[item['name']] = item
    return [cached[name] for name in names], 'source'


def validate_delivery_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9:_-]{1,200}', value):
        raise NoticeError('DELIVERY_ID_INVALID')


def find_record(config, delivery_id):
    validate_delivery_id(delivery_id)
    root = Path(config['ledger_dir']).expanduser()
    if not root.exists() and not root.is_symlink():
        return root / (digest(delivery_id) + '.json'), None
    root = private_dir(root, create=False)
    matches = []
    # 同时识别既有人工通知账，不因换成新工具而另建空账重放。
    for path in root.glob('*.json'):
        record = private_json(path)
        if not isinstance(record, dict):
            raise NoticeError('LEDGER_INVALID')
        if record.get('deliveryId', record.get('delivery_id')) == delivery_id:
            matches.append((path, record))
    if len(matches) > 1:
        raise NoticeError('DUPLICATE_LEDGER_RECORD')
    return matches[0] if matches else (root / (digest(delivery_id) + '.json'), None)


def plan_notification(config, delivery_id, text, names=(), refresh=False, offline=False):
    validate_delivery_id(delivery_id)
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 2048:
        raise NoticeError('TEXT_SIZE_INVALID')
    if '\\n' in text or '\x00' in text:
        raise NoticeError('TEXT_ESCAPING_INVALID')
    members, cache = resolve_members(config, names, refresh=refresh, offline=offline)
    details = {'content': text}
    if members:
        details['mentioned_list'] = sorted({member['userid'] for member in members})
    request = encoded({'msgtype': 'text', 'text': details})
    path, prior = find_record(config, delivery_id)
    plan = {'deliveryId': delivery_id, 'repository': config['repository'], 'group': config['group'],
            'targetSHA256': config['targetSHA256'], 'textSHA256': digest(text),
            'requestSHA256': digest(request), 'recipient_names': sorted({m['name'] for m in members}),
            'mentioned_list': details.get('mentioned_list', []), 'cache': cache, 'text': text,
            'request': request, 'ledger_path': path}
    if prior:
        if (prior.get('repository') != config['repository'] or prior.get('targetSHA256') != config['targetSHA256']
                or prior.get('requestSHA256') != plan['requestSHA256']):
            raise NoticeError('DELIVERY_REQUEST_CONFLICT')
        if prior.get('status') not in {'wecom_ack', 'unknown', 'rejected'}:
            raise NoticeError('LEDGER_STATUS_INVALID')
    return plan, prior


def post_webhook(url, body):
    if not LIVE_TRANSPORTS_ENABLED:
        raise NoticeError('PUBLIC_REFERENCE_OFFLINE_ONLY')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': 'application/json'})
    try:
        response = opener.open(request, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        raw = response.read(4097)
        if len(raw) > 4096:
            return {'status': 'unknown'}
        reply = json.loads(raw)
        code = reply.get('errcode')
        status = response.status
        if type(code) is not int:
            return {'status': 'unknown'}
        accepted = status == 200 and code == 0
        rejected = code != 0 and (status == 200 or 400 <= status <= 599)
        return {'status': 'wecom_ack' if accepted else 'rejected' if rejected else 'unknown',
                'http_status': status, 'errcode': code}


def validate_plan(config, plan):
    try:
        request = plan['request']
        payload = json.loads(request)
        text = payload['text']
        valid = (isinstance(request, bytes) and set(payload) == {'msgtype', 'text'}
                 and payload['msgtype'] == 'text' and set(text) <= {'content', 'mentioned_list'}
                 and isinstance(text['content'], str) and 0 < len(text['content'].encode()) <= 2048
                 and text['content'] == plan['text'] and digest(text['content']) == plan['textSHA256']
                 and digest(request) == plan['requestSHA256']
                 and text.get('mentioned_list', []) == plan['mentioned_list']
                 and isinstance(plan['mentioned_list'], list)
                 and all(valid_member_id(item) for item in plan['mentioned_list'])
                 and plan['repository'] == config['repository'] and plan['group'] == config['group']
                 and plan['targetSHA256'] == config['targetSHA256'] == digest(config['webhook']))
    except (KeyError, TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise NoticeError('PREPARED_REQUEST_CHANGED')


def send_notification(config, plan, sender=None):
    validate_plan(config, plan)
    path, prior = find_record(config, plan['deliveryId'])
    if prior:
        if (prior.get('repository') != plan['repository'] or prior.get('targetSHA256') != plan['targetSHA256']
                or prior.get('requestSHA256') != plan['requestSHA256']):
            raise NoticeError('DELIVERY_REQUEST_CONFLICT')
        return {**public_result(plan, prior['status']), 'submitted': False}
    record = {k: v for k, v in plan.items() if k not in {'request', 'ledger_path', 'cache', 'text'}}
    record.update(status='unknown', submitted_at=time.time(), visible_mention_verified=False,
                  recipient_receipt_verified=False)
    private_dir(config['ledger_dir'])
    try:
        write_new(path, record)
    except FileExistsError:
        return send_notification(config, plan, sender=sender)
    try:
        result = (sender or post_webhook)(config['webhook'], plan['request'])
        if result.get('status') not in {'wecom_ack', 'rejected', 'unknown'}:
            result = {'status': 'unknown'}
        record.update({key: result[key] for key in ['status', 'http_status', 'errcode'] if key in result})
    except Exception:
        # 记录unknown后不重试，也不输出可能含Webhook的异常。
        record['status'] = 'unknown'
    record['finished_at'] = time.time()
    replace_json(path, record)
    return {**public_result(plan, record['status']), 'submitted': True,
            **{key: record[key] for key in ['http_status', 'errcode'] if key in record}}


def public_result(plan, status):
    return {key: plan[key] for key in ['deliveryId', 'repository', 'group', 'requestSHA256', 'recipient_names']} | {'status': status}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['plan', 'send', 'status'])
    parser.add_argument('--config', default=DEFAULT_CONFIG)
    parser.add_argument('--delivery-id', required=True)
    parser.add_argument('--text-file', type=Path)
    parser.add_argument('--mention', action='append', default=[])
    parser.add_argument('--refresh-members', action='store_true')
    parser.add_argument('--offline', action='store_true', help='只用已有缓存，不发起目录SSH读取')
    parser.add_argument('--expected-sha256', help='可选：固定已审核的完整请求摘要')
    args = parser.parse_args(argv)
    try:
        if args.operation == 'send' and not LIVE_TRANSPORTS_ENABLED:
            raise NoticeError('PUBLIC_REFERENCE_OFFLINE_ONLY')
        config = load_config(args.config)
        if args.operation == 'status':
            _, prior = find_record(config, args.delivery_id)
            if prior and (prior.get('repository') != config['repository'] or prior.get('targetSHA256') != config['targetSHA256']):
                raise NoticeError('DELIVERY_REQUEST_CONFLICT')
            if prior and prior.get('status') not in {'wecom_ack', 'unknown', 'rejected'}:
                raise NoticeError('LEDGER_STATUS_INVALID')
            print(json.dumps({'deliveryId': args.delivery_id, 'status': prior['status'] if prior else 'not_found',
                              'evidence': 'local_ledger_only'}, ensure_ascii=False))
            return 0
        if not args.text_file:
            raise NoticeError('TEXT_FILE_REQUIRED')
        text = args.text_file.read_bytes().decode('utf-8', errors='strict')
        plan, prior = plan_notification(config, args.delivery_id, text, args.mention,
                                        refresh=args.refresh_members, offline=args.offline)
        if args.expected_sha256 and args.expected_sha256 != plan['requestSHA256']:
            raise NoticeError('APPROVED_REQUEST_CHANGED')
        if args.operation == 'plan':
            result = public_result(plan, prior['status'] if prior else 'not_found')
            result.update(text=text, cache=plan['cache'], text_bytes=len(text.encode()), sends_message=False)
        else:
            result = send_notification(config, plan)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result['status'] in {'wecom_ack', 'not_found'} or args.operation == 'plan' else 2
    except NoticeError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({'error': 'NOTICE_INPUT_OR_STORAGE_INVALID'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
