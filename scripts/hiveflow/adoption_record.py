"""Offline single-slot transition planner. Inputs are NOT authenticated authority.

Reads one strict JSON envelope from stdin; outputs a proposed record and next step.
Never opens repositories, calls APIs, executes commands, or writes runtime state.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import re
import sys
from urllib.parse import urlsplit

REPOSITORY = 'xiiiblue/hive-os'
BRANCH = 'chore/hiveflow-control'
PATH = '.hiveflow/control/slot.json'
STATES = {'pending', 'claimed', 'completed', 'blocked', 'cancelled', 'unknown'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def fields(value, names):
    require(type(value) is dict and set(value) == set(names.split()), 'unexpected or missing fields')


def token(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', value)


def stamp(value):
    require(type(value) is str and len(value) <= 40, 'timestamp required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.tzinfo is not None, 'timezone required')
    return parsed


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def config_sha(config):
    return hashlib.sha256(canonical(config).encode()).hexdigest()


def validate_config(config):
    fields(config, 'schemaVersion configId mode repository control issue maxIssues executionEnabled activationBoundary authorizedStart authorizedEnd scopeRef acceptanceRef environmentRef reviewRef recoveryRef')
    require(type(config['schemaVersion']) is int and config['schemaVersion'] == 1, 'schema')
    require(token(config['configId']) and config['mode'] in ('pr_review', 'full_delivery'), 'config identity/mode')
    require(config['repository'] == REPOSITORY, 'repository')
    fields(config['control'], 'repository branch path')
    require(config['control'] == dict(repository=REPOSITORY, branch=BRANCH, path=PATH), 'fixed control location')
    fields(config['issue'], 'number createdAt')
    require(type(config['issue']['number']) is int and config['issue']['number'] > 0 and type(config['issue']['createdAt']) is str, 'unique issue')
    stamp(config['issue']['createdAt'])
    require(type(config['maxIssues']) is int and config['maxIssues'] == 1, 'single issue only')
    require(type(config['executionEnabled']) is bool, 'executionEnabled boolean')
    require(stamp(config['authorizedStart']) <= stamp(config['activationBoundary']) < stamp(config['authorizedEnd']), 'window')
    for key in ('scopeRef', 'acceptanceRef', 'environmentRef', 'reviewRef', 'recoveryRef'):
        require(token(config[key]), 'approved context reference required')
    return config


def validate_result(result, stage):
    fields(result, 'stage evidenceUrls')
    require(result['stage'] == stage and type(result['evidenceUrls']) is list and len(result['evidenceUrls']) <= 10, 'result')
    for url in result['evidenceUrls']:
        require(type(url) is str and len(url) <= 512, 'evidence URL')
        parts = urlsplit(url)
        require(parts.scheme == 'https' and parts.hostname == 'github.com' and not parts.username and not parts.password and not parts.query, 'GitHub evidence only')
        require(parts.path.startswith('/' + REPOSITORY + '/'), 'evidence repository')
    if stage in ('pr_review', 'full_delivery'):
        require(bool(result['evidenceUrls']), 'completion evidence required')


def validate_observed(observed, config):
    fields(observed, 'repository number createdAt url state')
    identity = {key: observed[key] for key in ('repository', 'number', 'createdAt')}
    require(type(observed['number']) is int and identity == dict(repository=REPOSITORY, **config['issue']), 'actual issue mismatch')
    require(observed['url'] == 'https://github.com/' + REPOSITORY + '/issues/' + str(observed['number']), 'not canonical ordinary Issue URL')
    require(observed['state'] in ('open', 'closed'), 'actual issue state')
    return identity


def validate_record(record, config, observed):
    identity = validate_observed(observed, config)
    fields(record, 'schemaVersion configId configSHA decisionId decision issue decidedAt sourceRef state revision cancelRequested claim result')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 1, 'record schema')
    require(record['decision'] == 'adopted', 'only controlled adoption')
    require(record['configId'] == config['configId'] and record['configSHA'] == config_sha(config), 'config mismatch')
    require(record['issue'] == identity, 'record issue mismatch')
    require(type(record['sourceRef']) is str and re.fullmatch(r'dots-decision:[A-Za-z0-9_-]{8,128}', record['sourceRef']), 'opaque controlled source reference')
    decision_id = hashlib.sha256((config['configId'] + '\n' + canonical(identity) + '\n' + record['sourceRef']).encode()).hexdigest()
    require(record['decisionId'] == decision_id, 'stable decisionId')
    require(stamp(config['issue']['createdAt']) <= stamp(record['decidedAt']), 'decision predates Issue')
    require(stamp(config['activationBoundary']) < stamp(record['decidedAt']) <= stamp(config['authorizedEnd']), 'historical/out of window decision')
    require(record['state'] in STATES and type(record['revision']) is int and record['revision'] >= 0, 'state/revision')
    require(type(record['cancelRequested']) is bool, 'cancel flag')
    if record['claim'] is not None:
        fields(record['claim'], 'runId')
        require(token(record['claim']['runId']), 'runId')
    require(record['state'] != 'pending' or (record['claim'] is None and not record['cancelRequested'] and record['result'] is None), 'pending consistency')
    require(record['state'] not in ('claimed', 'completed', 'blocked', 'unknown') or record['claim'] is not None, 'claim required')
    require(record['state'] != 'cancelled' or record['cancelRequested'], 'cancellation required')
    require(record['state'] != 'claimed' or record['result'] is None, 'claimed result')
    if record['state'] in ('completed', 'blocked', 'unknown'):
        validate_result(record['result'], config['mode'] if record['state'] == 'completed' else record['state'])
    else:
        require(record['result'] is None, 'unexpected result')
    return record


def live(config, now, native_active):
    require(type(native_active) is bool and native_active and config['executionEnabled'], 'inactive configuration')
    require(stamp(config['activationBoundary']) < stamp(now) <= stamp(config['authorizedEnd']), 'not active window')


def validate_idle(record):
    fields(record, 'schemaVersion state executionEnabled revision')
    require(type(record['schemaVersion']) is int and record['schemaVersion'] == 1, 'idle schema')
    require(record['state'] == 'idle' and record['executionEnabled'] is False, 'idle must remain disabled')
    require(type(record['revision']) is int and record['revision'] == 0, 'initial idle revision')
    return record


def validate_snapshot(record, expected, observed):
    # Values must come from a fresh read of the SAME fixed ref/path.
    for sha in (expected, observed):
        require(sha is None or (type(sha) is str and re.fullmatch(r'[0-9a-f]{40}', sha)), 'blob SHA')
    require(expected == observed, 'conflict: reread, do not replay stale proposal')
    require((record is None) == (observed is None), 'missing record/SHA mismatch')


def plan(envelope):
    require(type(envelope) is dict, 'object required')
    if envelope.get('operation') in ('initialize', 'inspect_idle'):
        fields(envelope, 'operation record expectedBlobSHA observedBlobSHA')
        record = deepcopy(envelope['record'])
        validate_snapshot(record, envelope['expectedBlobSHA'], envelope['observedBlobSHA'])
        if record is not None:
            validate_idle(record)
            return dict(write=False, nextAction='idle_do_not_execute', record=record)
        require(envelope['operation'] == 'initialize', 'idle record absent')
        idle = dict(schemaVersion=1, state='idle', executionEnabled=False, revision=0)
        validate_idle(idle)
        return dict(write=True, nextAction='initialize_disabled_then_readback_only', record=idle)
    fields(envelope, 'operation config observedIssue now nativeActive record expectedBlobSHA observedBlobSHA runId decision result')
    config = validate_config(envelope['config'])
    observed = envelope['observedIssue']
    identity = validate_observed(observed, config)
    stamp(envelope['now'])
    require(type(envelope['nativeActive']) is bool, 'nativeActive boolean')
    op = envelope['operation']
    require(op in ('create', 'claim', 'guard', 'cancel', 'unknown', 'blocked', 'complete'), 'operation')
    record = deepcopy(envelope['record'])
    validate_snapshot(record, envelope['expectedBlobSHA'], envelope['observedBlobSHA'])
    if op != 'create':
        require(envelope['decision'] is None, 'unexpected decision')
    if op not in ('complete', 'unknown', 'blocked'):
        require(envelope['result'] is None, 'unexpected result')
    if op in ('create', 'cancel'):
        require(envelope['runId'] is None, 'producer cannot claim')
    else:
        require(token(envelope['runId']), 'consumer runId required')
    if op == 'create':
        fields(envelope['decision'], 'decidedAt sourceRef')
        decision = envelope['decision']
        require(type(decision['sourceRef']) is str and re.fullmatch(r'dots-decision:[A-Za-z0-9_-]{8,128}', decision['sourceRef']), 'opaque source reference')
        require(stamp(decision['decidedAt']) <= stamp(envelope['now']), 'future decision')
        proposed = dict(schemaVersion=1, configId=config['configId'], configSHA=config_sha(config),
                        decisionId=hashlib.sha256((config['configId'] + '\n' + canonical(identity) + '\n' + decision['sourceRef']).encode()).hexdigest(),
                        decision='adopted', issue=identity, decidedAt=decision['decidedAt'], sourceRef=decision['sourceRef'],
                        state='pending', revision=0, cancelRequested=False, claim=None, result=None)
        validate_record(proposed, config, observed)
        was_idle = type(record) is dict and record.get('state') == 'idle'
        if was_idle:
            validate_idle(record)
            proposed['revision'] = record['revision'] + 1
        elif record is not None:
            validate_record(record, config, observed)
            require(all(record[k] == proposed[k] for k in ('decisionId', 'decidedAt', 'sourceRef')), 'occupied slot: no replacement or historical replay')
            return dict(write=False, nextAction='reconcile_existing_record', record=record)
        live(config, envelope['now'], envelope['nativeActive'])
        require(observed['state'] == 'open', 'closed issue cannot be selected')
        validate_record(proposed, config, observed)
        return dict(write=True, nextAction='cas_write_then_readback_no_business_replay' if was_idle else 'write_then_readback_only', record=proposed)
    require(record is not None, 'record required')
    validate_record(record, config, observed)
    require(stamp(record['decidedAt']) <= stamp(envelope['now']), 'future record')
    if op == 'cancel':
        if record['state'] in ('completed', 'cancelled') or record['cancelRequested']:
            return dict(write=False, nextAction='report_existing_terminal_or_cancellation', record=record)
        record['cancelRequested'] = True
        if record['state'] == 'pending':
            record['state'] = 'cancelled'
    else:
        if op == 'claim':
            require(observed['state'] == 'open', 'closed issue cannot be claimed')
            live(config, envelope['now'], envelope['nativeActive'])
            require(not record['cancelRequested'], 'cancelled')
            if record['state'] != 'pending':
                return dict(write=False, nextAction='reconcile_existing_run_no_reexecution', record=record)
            record['state'] = 'claimed'
            record['claim'] = dict(runId=envelope['runId'])
        else:
            require(record['claim'] == dict(runId=envelope['runId']), 'different run cannot take over')
            require(record['state'] == 'claimed', 'terminal/unknown: manual reconciliation required')
            if op == 'guard':
                live(config, envelope['now'], envelope['nativeActive'])
                require(not record['cancelRequested'], 'stop at safe boundary')
                return dict(write=False, nextAction='existing_run_guard_passed_not_execution_authority', record=record)
            # Bookkeeping may record already observed facts after pause/expiry.
            # It never authorizes continuing business actions.
            if op == 'complete':
                require(not record['cancelRequested'], 'cancel requested: record blocked, not completion')
                stage = config['mode']
                record['state'] = 'completed'
            else:
                stage = op
                record['state'] = op
            validate_result(envelope['result'], stage)
            record['result'] = envelope['result']
    record['revision'] += 1
    validate_record(record, config, observed)
    return dict(write=True, nextAction='cas_write_then_readback_no_business_replay', record=record)


def strict_json(payload):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    return json.loads(payload, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


if __name__ == '__main__':
    try:
        payload = sys.stdin.read(65537)
        require(len(payload.encode('utf-8')) <= 65536, 'input too large')
        print(json.dumps(plan(strict_json(payload)), ensure_ascii=False, indent=2))
    except (ValueError, TypeError, KeyError) as error:
        print('Rejected: ' + str(error), file=sys.stderr)
        sys.exit(1)
