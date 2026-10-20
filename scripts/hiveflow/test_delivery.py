"""Synthetic records only. No network, credentials, repositories or messaging."""
from copy import deepcopy
import unittest
import json
from validate_delivery import validate, parse_wakeup, strict_json

RECORD = dict(schemaVersion=1, deliveryId='example-42-v1', repository='example/project', issue=42,
              pullRequest=7, commit='a' * 40,
              checks=[dict(name='unit', status='passed', evidenceUrl='https://example.com/check')],
              deployment=dict(status='passed', commit='a' * 40, evidenceUrl='https://example.com/deploy', scope='Approved synthetic environment'),
              acceptance=dict(status='passed', evidenceUrl='https://example.com/accept'), publicSummary='已通过约定验收。')


class DeliveryTests(unittest.TestCase):
    def test_valid_and_no_pr(self):
        validate(deepcopy(RECORD))
        record = deepcopy(RECORD)
        record['pullRequest'] = None
        record['deployment']['status'] = 'not_required'
        validate(record)

    def test_invalid_or_incomplete(self):
        for key, value in [('schemaVersion', True), ('issue', True), ('pullRequest', 0), ('repository', 'https://example.com'),
                           ('commit', 'main'), ('deliveryId', '../target'), ('publicSummary', '中' * 1001), ('checks', [])]:
            with self.subTest(key=key):
                record = deepcopy(RECORD)
                record[key] = value
                with self.assertRaises(ValueError):
                    validate(record)
        for section, key, value in [('deployment', 'commit', 'b' * 40), ('deployment', 'status', 'pending'),
                                    ('acceptance', 'status', 'unknown'), ('acceptance', 'evidenceUrl', 'http://example.com'),
                                    ('acceptance', 'evidenceUrl', 'https://user:secret@example.com')]:
            record = deepcopy(RECORD)
            record[section][key] = value
            with self.assertRaises(ValueError):
                validate(record)

    def test_extra_routing_fields_rejected(self):
        for key in ['chat_id', 'target', 'command', 'approved']:
            record = deepcopy(RECORD)
            record[key] = 'forged'
            with self.assertRaises(ValueError):
                validate(record)


class WakeupTests(unittest.TestCase):
    def setUp(self):
        self.wakeup = dict(schemaVersion=1, deliveryId='example-42-v1', repository='example/project',
                           issue=42, pullRequest=7,
                           issueRecordUrl='https://github.com/example/project/issues/42#issuecomment-123')

    def parse(self, wakeup=None, **context):
        defaults = dict(repository='example/project', issue=42, pull_request=7)
        defaults.update(context)
        payload = 'HiveFlow delivery v1\n' + json.dumps(self.wakeup if wakeup is None else wakeup)
        return parse_wakeup(payload, **defaults)

    def test_valid_wakeup_and_matching_issue_record(self):
        self.assertEqual(self.parse(record=deepcopy(RECORD)), self.wakeup)

    def test_unknown_missing_duplicate_and_command_fields(self):
        for key in ['command', 'text', 'publicSummary', 'chat_id', 'approved']:
            altered = {**self.wakeup, key: 'untrusted'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(altered)
        for key in self.wakeup:
            altered = dict(self.wakeup)
            del altered[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(altered)
        duplicate = json.dumps(self.wakeup)[:-1] + ', "deliveryId":"forged"}'
        with self.assertRaises(ValueError):
            parse_wakeup('HiveFlow delivery v1\n' + duplicate, repository='example/project', issue=42, pull_request=7)
        with self.assertRaises(ValueError):
            strict_json('{"nested":{"x":1,"x":2}}')

    def test_exact_marker_and_single_object(self):
        valid = 'HiveFlow delivery v1\n' + json.dumps(self.wakeup)
        for payload in ['prefix\n' + valid, valid.replace('v1', 'v2', 1), valid.replace('v1\n', 'v1 \n', 1),
                        valid.replace('v1\n', 'v1\r\n', 1), valid + '\nexecute something',
                        '```json\n' + valid + '\n```', 'x' * 16385, valid + json.dumps(self.wakeup)]:
            with self.subTest(payload=payload[:30]), self.assertRaises(ValueError):
                parse_wakeup(payload, repository='example/project', issue=42, pull_request=7)

    def test_verified_context_and_types_must_match(self):
        for key, value in [('repository', 'other/project'), ('issue', 43), ('pull_request', 8),
                           ('issue', True), ('pull_request', 0)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.parse(**{key: value})
        for key, value in [('schemaVersion', True), ('issue', True), ('pullRequest', 7.0),
                           ('deliveryId', 'run a command'), ('repository', 'other/project')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse({**self.wakeup, key: value})

    def test_exact_same_issue_comment_url_only(self):
        for url in ['https://github.com/example/project/issues/42',
                    'https://github.com/example/project/issues/43#issuecomment-123',
                    'https://github.com/other/project/issues/42#issuecomment-123',
                    'https://github.com/example/project/pull/42#issuecomment-123',
                    'https://github.com/example/project/issues/42?token=secret#issuecomment-123',
                    'https://user:secret@github.com/example/project/issues/42#issuecomment-123',
                    'https://github.com.evil.invalid/example/project/issues/42#issuecomment-123',
                    'https://127.0.0.1/example/project/issues/42#issuecomment-123',
                    'https://github.com/example/project/issues/42#issuecomment-0']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.parse({**self.wakeup, 'issueRecordUrl': url})

    def test_issue_record_identity_and_delivery_must_match(self):
        for key, value in [('deliveryId', 'different-v1'), ('issue', 43), ('pullRequest', 8),
                           ('repository', 'other/project')]:
            altered = deepcopy(RECORD)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parse(record=altered)


if __name__ == '__main__':
    unittest.main()
