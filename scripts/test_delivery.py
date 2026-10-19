"""Synthetic records only. No network, credentials, repositories or messaging."""
from copy import deepcopy
import unittest
from validate_delivery import validate

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


if __name__ == '__main__':
    unittest.main()
