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


def main():
    try:
        with open(sys.argv[1], encoding='utf-8') as handle:
            payload = handle.read(65537)
        if len(payload.encode('utf-8')) > 65536:
            raise ValueError('Oversized record')
        validate(json.loads(payload))
    except (OSError, ValueError, TypeError, IndexError, KeyError):
        print('Delivery structure invalid or incomplete; no external action performed.', file=sys.stderr)
        return 1
    print('Delivery structure valid; author, evidence, authorization and audience still require independent verification.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
