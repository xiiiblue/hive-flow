"""企微通知只做离线验证；所有发送器均为内存替身。"""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import wecom_notify as notify


class NotifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='hive-wecom-notify-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root/'contacts.json'
        self.write(self.source, [{'name': '示例成员甲', 'userid': 'member-a'},
                                 {'name': '示例成员乙', 'userid': 'member-b'}])
        self.webhook = self.root/'webhook.json'
        self.write(self.webhook, {'transport': 'wecom_webhook', 'repository': 'example/project',
                                 'webhook': 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=example-key-0123456789'})
        self.profile = self.root/'profile.json'
        self.raw_config = {'repository': 'example/project', 'group': '示例工作群',
                           'webhook_config': str(self.webhook), 'directory': {'path': str(self.source)},
                           'ledger_dir': str(self.root/'ledger'), 'cache_dir': str(self.root/'cache')}
        self.write(self.profile, self.raw_config)
        self.config = notify.load_config(self.profile)

    def write(self, path, value):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))
        path.chmod(0o600)

    def plan(self, text='第一段\n\n第二段', names=('示例成员甲',), delivery='example:notice:1'):
        return notify.plan_notification(self.config, delivery, text, names)[0]

    def test_exact_payload_and_local_cache_avoid_second_source_lookup(self):
        plan = self.plan()
        self.assertEqual(json.loads(plan['request']), {'msgtype': 'text', 'text': {
            'content': '第一段\n\n第二段', 'mentioned_list': ['member-a']}})
        self.assertEqual(plan['cache'], 'source')
        self.source.unlink()
        again = self.plan()
        self.assertEqual(again['cache'], 'cache')
        self.assertEqual(again['requestSHA256'], plan['requestSHA256'])
        self.assertNotIn('member-a', json.dumps(notify.public_result(plan, 'not_found')))

    def test_duplicate_names_are_deduplicated_and_all_is_forbidden(self):
        plan = self.plan(names=('示例成员乙', '示例成员甲', '示例成员甲'))
        self.assertEqual(json.loads(plan['request'])['text']['mentioned_list'], ['member-a', 'member-b'])
        for name in ['@all', '@示例成员甲', '']:
            with self.assertRaises(notify.NoticeError): self.plan(names=(name,))
        self.write(self.source, [{'name': '示例成员甲', 'userid': '@all'}])
        with self.assertRaises(notify.NoticeError):
            notify.resolve_members(self.config, ['示例成员甲'], refresh=True)

    def test_ambiguous_or_unknown_names_refuse_before_any_message(self):
        self.write(self.source, [{'name': '示例成员甲', 'userid': 'a'}, {'name': '示例成员甲', 'userid': 'b'}])
        for name in ['示例成员甲', '不存在的示例成员']:
            with self.assertRaisesRegex(notify.NoticeError, 'MEMBER_NOT_UNIQUE'): self.plan(names=(name,))

    def test_no_mention_needs_no_directory(self):
        self.source.unlink()
        self.assertEqual(json.loads(self.plan(names=())['request']), {'msgtype': 'text', 'text': {'content': '第一段\n\n第二段'}})

    def test_cache_is_bound_to_group_webhook_and_source_and_expires(self):
        self.plan()
        for replacement in [{'group': '其他示例群'}, {'targetSHA256': '0'*64},
                            {'directory': {'path': str(self.root/'other.json')}}]:
            with self.assertRaisesRegex(notify.NoticeError, 'MEMBER_CACHE_MISS_OFFLINE'):
                notify.resolve_members({**self.config, **replacement}, ['示例成员甲'], offline=True)
        with self.assertRaisesRegex(notify.NoticeError, 'MEMBER_CACHE_MISS_OFFLINE'):
            notify.resolve_members(self.config, ['示例成员甲'], now=time.time()+86401, offline=True)
        self.write(self.source, [{'name': '示例成员甲', 'userid': 'member-new'}])
        selected, _ = notify.resolve_members(self.config, ['示例成员甲'], refresh=True)
        self.assertEqual(selected[0]['userid'], 'member-new')

    def test_unknown_and_rejected_and_success_never_resubmit(self):
        for status in ['wecom_ack', 'unknown', 'rejected']:
            plan = self.plan(delivery='example:'+status)
            calls = []
            def sender(url, body):
                calls.append(body)
                _, saved = notify.find_record(self.config, plan['deliveryId'])
                self.assertEqual(saved['status'], 'unknown')
                self.assertEqual(saved['requestSHA256'], notify.digest(body))
                return {'status': status, 'errcode': 0 if status == 'wecom_ack' else 1}
            self.assertTrue(notify.send_notification(self.config, plan, sender)['submitted'])
            self.assertFalse(notify.send_notification(self.config, plan, sender)['submitted'])
            self.assertEqual(len(calls), 1)
            with self.assertRaisesRegex(notify.NoticeError, 'DELIVERY_REQUEST_CONFLICT'):
                self.plan(text='新正文', delivery=plan['deliveryId'])
            with self.assertRaisesRegex(notify.NoticeError, 'DELIVERY_REQUEST_CONFLICT'):
                self.plan(names=('示例成员乙',), delivery=plan['deliveryId'])

    def test_timeout_exception_stays_unknown_and_hides_secrets(self):
        plan = self.plan()
        def failing(url, body): raise TimeoutError(url+' member-a')
        result = notify.send_notification(self.config, plan, failing)
        self.assertEqual(result['status'], 'unknown')
        self.assertNotIn('example-key-', json.dumps(result))
        self.assertNotIn('member-a', json.dumps(result))
        _, record = notify.find_record(self.config, plan['deliveryId'])
        self.assertEqual(record['status'], 'unknown')
        with patch.object(notify, 'post_webhook', side_effect=AssertionError('must not send')):
            self.assertFalse(notify.send_notification(self.config, plan)['submitted'])

    def test_concurrent_submit_invokes_sender_once(self):
        plan = self.plan()
        calls = []
        lock = threading.Lock()
        def sender(url, body):
            with lock: calls.append(body)
            time.sleep(0.04)
            return {'status': 'wecom_ack'}
        def send():
            try: return notify.send_notification(self.config, plan, sender)
            except notify.NoticeError: return {'status': 'blocked_while_first_record_is_written'}
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: send(), range(8)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(bool(r.get('submitted')) for r in results), 1)
        self.assertEqual(notify.find_record(self.config, plan['deliveryId'])[1]['status'], 'wecom_ack')

    def test_existing_manual_receipt_is_used_without_creating_new_ledger(self):
        plan = self.plan()
        record = {k: plan[k] for k in ['deliveryId', 'repository', 'targetSHA256', 'requestSHA256']}
        record['status'] = 'wecom_ack'
        self.write(plan['ledger_path'].parent/'older-human-readable-name.json', record)
        _, prior = notify.plan_notification(self.config, plan['deliveryId'], plan['text'], ['示例成员甲'])
        self.assertEqual(prior['status'], 'wecom_ack')
        with patch.object(notify, 'post_webhook', side_effect=AssertionError('must not send')):
            self.assertFalse(notify.send_notification(self.config, plan)['submitted'])
        self.assertFalse(plan['ledger_path'].exists())

    def test_status_does_not_resolve_members_or_call_webhook(self):
        plan = self.plan()
        notify.send_notification(self.config, plan, lambda *_: {'status': 'wecom_ack'})
        self.source.unlink()
        with patch.object(notify, 'resolve_members', side_effect=AssertionError('no lookup')):
            with patch.object(notify, 'post_webhook', side_effect=AssertionError('no HTTP')):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    status = notify.main(['status', '--config', str(self.profile), '--delivery-id', plan['deliveryId']])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'wecom_ack')

    def test_plan_is_offline_after_cache_and_never_sends(self):
        self.plan()
        text = self.root/'message.txt';text.write_text('已审核\n\n保留空行\n')
        with patch.object(notify, 'post_webhook', side_effect=AssertionError('no HTTP')):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                status = notify.main(['plan', '--config', str(self.profile), '--delivery-id', 'example:plan',
                                      '--text-file', str(text), '--mention', '示例成员甲', '--offline'])
        self.assertEqual(status, 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['text'], text.read_text())
        self.assertFalse(result['sends_message'])
        self.assertEqual(result['cache'], 'cache')
        self.assertFalse(notify.find_record(self.config, 'example:plan')[0].exists())

    @patch.object(notify, 'LIVE_TRANSPORTS_ENABLED', True)
    def test_changed_approved_hash_refuses_before_sending(self):
        text = self.root/'message.txt';text.write_text('变更后的正文')
        with patch.object(notify, 'post_webhook', side_effect=AssertionError('no HTTP')):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                status = notify.main(['send', '--config', str(self.profile), '--delivery-id', 'example:changed',
                                      '--text-file', str(text), '--expected-sha256', '0'*64])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(error.getvalue())['error'], 'APPROVED_REQUEST_CHANGED')

    def test_bad_permissions_symlinks_and_invalid_webhooks_are_rejected(self):
        self.profile.chmod(0o644)
        with self.assertRaises(notify.NoticeError): notify.load_config(self.profile)
        self.profile.chmod(0o600)
        link=self.root/'link.json';link.symlink_to(self.profile)
        with self.assertRaises(notify.NoticeError): notify.load_config(link)
        for url in ['http://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=example-key-0123456789',
                    'https://example.invalid/cgi-bin/webhook/send?key=example-key-0123456789',
                    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=example-key-0123456789&key=another-example-key']:
            self.write(self.webhook, {'transport': 'wecom_webhook', 'repository': 'example/project', 'webhook': url})
            with self.assertRaises(notify.NoticeError): notify.load_config(self.profile)

    def test_corrupt_or_duplicate_ledger_refuses_without_sending(self):
        plan=self.plan();self.write(plan['ledger_path'], {'deliveryId': plan['deliveryId'], 'status': 'unknown'})
        with self.assertRaisesRegex(notify.NoticeError, 'DELIVERY_REQUEST_CONFLICT'): self.plan()
        self.write(plan['ledger_path'].parent/'duplicate.json', {'deliveryId': plan['deliveryId']})
        with self.assertRaisesRegex(notify.NoticeError, 'DUPLICATE_LEDGER_RECORD'):
            notify.find_record(self.config, plan['deliveryId'])

    def test_double_escaped_newlines_and_oversized_text_refuse(self):
        for text in ['', 'x'*2049, '第一段\\n\\n第二段', 'x\0y']:
            with self.assertRaises(notify.NoticeError): self.plan(text=text)

    @patch.object(notify, 'LIVE_TRANSPORTS_ENABLED', True)
    def test_remote_directory_uses_fixed_program_and_only_requests_exact_names(self):
        directory={'ssh_host': 'example-host', 'path': '~/private/contacts.json'}
        result = type('Result', (), {'stdout': notify.encoded({'members': [{'name': '示例成员甲', 'userid': 'opaque-member-a'}], 'source_sha256': 'a'*64})})()
        with patch.object(notify.subprocess, 'run', return_value=result) as run:
            selected, _ = notify.fetch_remote_members(directory, ['示例成员甲'])
        self.assertEqual(selected[0]['userid'], 'opaque-member-a')
        args, kwargs=run.call_args
        self.assertEqual(args[0][:6], ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=8','example-host'])
        self.assertEqual(json.loads(kwargs['input']), {'path':directory['path'],'names':['示例成员甲']})
        self.assertNotIn('示例成员甲', args[0][-1])

    def test_prepared_payload_or_target_cannot_change_before_submission(self):
        for field, value in [('request', notify.encoded({'msgtype':'text','text':{'content':'替换正文'}})),
                             ('mentioned_list', ['different-member']), ('repository', 'other/project'),
                             ('group', '另一示例群'), ('requestSHA256', '0'*64)]:
            plan = self.plan()
            plan[field] = value
            with self.assertRaisesRegex(notify.NoticeError, 'PREPARED_REQUEST_CHANGED'):
                notify.send_notification(self.config, plan, lambda *_: self.fail('must not send'))
        plan = self.plan()
        with self.assertRaisesRegex(notify.NoticeError, 'PREPARED_REQUEST_CHANGED'):
            notify.send_notification({**self.config,'webhook':self.config['webhook']+'-changed'}, plan,
                                     lambda *_: self.fail('must not send'))

    def test_public_reference_refuses_live_send_before_loading_credentials(self):
        with patch.object(notify, 'LIVE_TRANSPORTS_ENABLED', False):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                status = notify.main(['send','--config',str(self.root/'absent.json'),'--delivery-id','example:public'])
        self.assertEqual(status, 2)
        self.assertEqual(json.loads(error.getvalue())['error'], 'PUBLIC_REFERENCE_OFFLINE_ONLY')

    @patch.object(notify, 'LIVE_TRANSPORTS_ENABLED', True)
    def test_explicit_http_error_is_rejected_but_redirect_stays_unknown(self):
        for status, code, expected in [(400,40058,'rejected'),(200,0,'wecom_ack'),(200,1,'rejected'),
                                       (302,1,'unknown'),(500,0,'unknown'),(400,False,'unknown')]:
            from urllib.error import HTTPError
            if status != 200:
                result = HTTPError('https://example.invalid',status,'safe',{},io.BytesIO(notify.encoded({'errcode':code})))
                opener = type('Opener', (), {'open':lambda self,*a,**k: (_ for _ in ()).throw(result)})()
            else:
                class Response(io.BytesIO):
                    pass
                result=Response(notify.encoded({'errcode':code}));result.status=status
                opener=type('Opener', (), {'open':lambda self,*a,**k:result})()
            with patch.object(notify.urllib.request,'build_opener',return_value=opener):
                self.assertEqual(notify.post_webhook(self.config['webhook'],b'{}')['status'], expected)

    def test_blank_or_invisible_member_ids_are_not_sent_or_trimmed(self):
        for value in [' ', ' @all', 'member-a ', 'member a', 'member\t-a', 'member\x7f-a', 'member\u200b-a']:
            self.write(self.source,[{'name':'示例成员甲','userid':value}])
            with self.assertRaisesRegex(notify.NoticeError, 'MEMBER_ID_INVALID'):
                notify.resolve_members(self.config,['示例成员甲'],refresh=True)

    def test_status_with_no_ledger_is_read_only(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            status=notify.main(['status','--config',str(self.profile),'--delivery-id','example:missing'])
        self.assertEqual(status,0)
        self.assertEqual(json.loads(output.getvalue())['status'],'not_found')
        self.assertFalse((self.root/'ledger').exists())
        self.assertFalse((self.root/'cache').exists())


if __name__ == '__main__':
    unittest.main()
