"""Offline structural check only; never authenticates evidence or sends notifications."""
import json
import re
import sys
from urllib.parse import urlsplit


def validate(record):
    def require(condition):
        if not condition:
            raise ValueError('Invalid or incomplete delivery record')

    def fields(obj, keys):
        require(type(obj) is dict and set(obj) == set(keys.split()))

    def text(value):
        return type(value) is str and bool(value.strip()) and len(value) <= 3000

    def url(value):
        if not text(value):
            return False
        try:
            u = urlsplit(value)
            return u.scheme == 'https' and bool(u.hostname) and not u.username and not u.password
        except ValueError:
            return False

    def positive(value):
        return type(value) is int and 0 < value <= 9007199254740991

    fields(record, 'schemaVersion deliveryId repository issue pullRequest commit checks deployment acceptance publicSummary')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 1)
    require(type(record['deliveryId']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', record['deliveryId']))
    require(type(record['repository']) is str and re.fullmatch(r'[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*', record['repository']))
    require(positive(record['issue']) and (record['pullRequest'] is None or positive(record['pullRequest'])))
    require(type(record['commit']) is str and re.fullmatch(r'[0-9a-f]{40}', record['commit']))
    require(type(record['checks']) is list and 0 < len(record['checks']) <= 100)
    for check in record['checks']:
        fields(check, 'name status evidenceUrl')
        require(text(check['name']) and check['status'] == 'passed' and url(check['evidenceUrl']))
    deployment = record['deployment']
    fields(deployment, 'status commit evidenceUrl scope')
    require(deployment['status'] in ('passed', 'not_required') and deployment['commit'] == record['commit'])
    require(url(deployment['evidenceUrl']) and text(deployment['scope']))
    fields(record['acceptance'], 'status evidenceUrl')
    require(record['acceptance']['status'] == 'passed' and url(record['acceptance']['evidenceUrl']))
    require(text(record['publicSummary']) and len(record['publicSummary'].encode('utf-8')) <= 3000)
    return record


WAKEUP_MARKER = 'HiveFlow delivery v1'


def strict_json(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON field')
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=unique)


def parse_wakeup(payload, *, repository, issue, pull_request, record=None):
    """Context must come from verified event/association; never authenticate from body."""
    def require(condition):
        if not condition:
            raise ValueError('Invalid or mismatched delivery wakeup')

    require(type(repository) is str and re.fullmatch(r'[a-z0-9][a-z0-9_.-]*/[a-z0-9][a-z0-9_.-]*', repository))
    require(type(issue) is int and 0 < issue <= 9007199254740991)
    require(type(pull_request) is int and 0 < pull_request <= 9007199254740991)
    require(type(payload) is str and len(payload.encode('utf-8')) <= 16384)
    # One exact marker line, then one JSON object. No fences, preamble or extra prose.
    first, separator, body = payload.partition('\n')
    require(bool(separator) and first == WAKEUP_MARKER)
    wakeup = strict_json(body)
    require(type(wakeup) is dict and set(wakeup) == {
        'schemaVersion', 'deliveryId', 'repository', 'issue', 'pullRequest', 'issueRecordUrl'})
    require(type(wakeup['schemaVersion']) is int and wakeup['schemaVersion'] == 1)
    require(type(wakeup['deliveryId']) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}', wakeup['deliveryId']))
    require(wakeup['repository'] == repository)
    require(type(wakeup['issue']) is int and wakeup['issue'] == issue)
    require(type(wakeup['pullRequest']) is int and wakeup['pullRequest'] == pull_request)
    expected = f'https://github.com/{repository}/issues/{issue}#issuecomment-'
    require(type(wakeup['issueRecordUrl']) is str and re.fullmatch(re.escape(expected) + r'[1-9][0-9]*', wakeup['issueRecordUrl']))
    if record is not None:
        validate(record)  # Existing Issue record schema is unchanged.
        require(all(wakeup[key] == record[key] for key in
                    ['schemaVersion', 'deliveryId', 'repository', 'issue', 'pullRequest']))
    return wakeup


def read_data(path):
    with open(path, encoding='utf-8') as handle:
        payload = handle.read(65537)
    if len(payload.encode('utf-8')) > 65536:
        raise ValueError('Oversized record')
    return payload


def main():
    try:
        if sys.argv[1] == '--wakeup':
            # --wakeup comment.txt trusted-repository trusted-issue trusted-pr [record.json]
            if len(sys.argv) not in (6, 7):
                raise ValueError('Invalid arguments')
            record = strict_json(read_data(sys.argv[6])) if len(sys.argv) == 7 else None
            parse_wakeup(read_data(sys.argv[2]), repository=sys.argv[3],
                         issue=int(sys.argv[4]), pull_request=int(sys.argv[5]), record=record)
        else:
            if len(sys.argv) != 2:
                raise ValueError('Invalid arguments')
            validate(strict_json(read_data(sys.argv[1])))
    except (OSError, ValueError, TypeError, IndexError, KeyError):
        print('Delivery structure invalid, incomplete or mismatched; no external action performed.', file=sys.stderr)
        return 1
    print('Delivery structure valid; author, event type, evidence, authorization and audience still require independent verification.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
