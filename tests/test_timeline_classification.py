"""Offline semantic request, bounded route, parser, and diagnostic contracts."""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import orjson
import pytest

from twicc import timeline_classification as classifier
from twicc.agent.semantic_types import Classification, DecisionRequest, SourceScope
from twicc.core.enums import Provider

FIXTURES = orjson.loads((Path(__file__).parent / 'fixtures/timeline_semantic_contract.json').read_bytes())


def request(payload=None, *, kind='message', route='provider'):
    return DecisionRequest('decision-id', kind, 'session-id', SourceScope('claude_code', 'source-id', 3),
                           payload or {'target': {'text': 'Full candidate'}, 'previous_messages': [],
                                       'source_boundary': 10, 'tool_evidence': []},
                           datetime(2030, 1, 1, tzinfo=timezone.utc), route)


@pytest.mark.parametrize('provider,choice,want', [
    (Provider.CLAUDE_CODE, 'provider', ('haiku', 'luna')),
    (Provider.CODEX, 'provider', ('luna', 'haiku')),
    (Provider.CLAUDE_CODE, 'haiku', ('haiku', 'luna')),
    (Provider.CODEX, 'haiku', ('haiku', 'luna')),
    (Provider.CLAUDE_CODE, 'luna', ('luna', 'haiku')),
    (Provider.CODEX, 'luna', ('luna', 'haiku')),
    (Provider.CLAUDE_CODE, 'openrouter', ('openrouter', 'haiku', 'luna')),
    (Provider.CODEX, 'openrouter', ('openrouter', 'luna', 'haiku')),
])
def test_route_matrix(monkeypatch, provider, choice, want):
    monkeypatch.setattr(classifier, 'openrouter_key_set', lambda: True)
    assert classifier.candidate_routes(choice, provider) == want


def test_missing_key_prefers_session_provider(monkeypatch):
    monkeypatch.setattr(classifier, 'openrouter_key_set', lambda: False)
    assert classifier.candidate_routes('openrouter', Provider.CODEX) == ('luna', 'haiku')


@pytest.mark.parametrize('text,verdict,reason', [
    ('YES\nDelivered.', 'YES', 'Delivered.'), ('\ufeff\n  yEs  \r\n\r\n good \t reason ', 'YES', 'good reason'),
    ('\r\nno\rStill running\rNO', 'NO', 'Still running NO'), ('YES', 'YES', ''),
    ('NO\nActually YES', 'NO', 'Actually YES'), ('\nNO\n' + 'word\n' * 200, 'NO', ('word ' * 200).strip()[:500]),
])
def test_parser_preserves_first_verdict(text, verdict, reason):
    assert classifier.parse_classification('ending', text) == Classification(verdict, reason)


@pytest.mark.parametrize('text', ['', '  ', 'YES.', '**YES**\nreason', 'Decision: YES',
                                  '{"verdict":"YES"}', 'reason\nYES', 'YES\n' + 'é' * 4096])
def test_parser_rejects_invalid_first_line_and_oversized_response(text):
    with pytest.raises(classifier.ClassificationError, match='invalid_response'):
        classifier.parse_classification('message', text)


@pytest.mark.parametrize('case', FIXTURES['cases'], ids=lambda case: case['name'])
def test_literal_payload_contract(case):
    captured = request(case['input'], kind=case['kind'])
    assert classifier.build_classifier_payload(captured) == case['expected']
    prepared = asyncio.run(classifier.build_decision_context(captured))
    assert classifier.build_classifier_payload(prepared) == case['expected']
    system, wire = classifier.classifier_messages(prepared)
    assert hashlib.sha256(system.encode()).hexdigest() == FIXTURES['prompt_sha256'][case['kind']]
    assert wire == orjson.dumps(case['expected']).decode()


def test_context_never_uses_newer_work():
    captured = request({'target': {'text': 'Unchanged'}, 'source_boundary': 10,
                        'previous_messages': [{'role': 'user', 'text': 'Old', 'line': 9},
                                              {'role': 'user', 'text': 'Future', 'line': 11}],
                        'tool_evidence': []})
    frozen = asyncio.run(classifier.build_decision_context(captured))
    captured.payload['target']['text'] = 'Mutated'
    assert classifier.build_classifier_payload(frozen) == {
        'kind': 'message', 'preceding_messages': [{'role': 'user', 'text': 'Old'}],
        'candidate': {'text': 'Unchanged'}}


def ending_payload(shells=None, observations=None):
    return {'target': {'shells': shells or [
        {'key': 'one', 'owner': 'root', 'command': 'serve app', 'description': None,
         'started_at': None, 'start_line': 1},
        {'key': 'two', 'owner': 'subagent', 'command': 'run tests', 'description': None,
         'started_at': None, 'start_line': 2}]},
        'previous_messages': [{'role': 'assistant', 'text': 'Delivered', 'line': 9}],
        'tool_evidence': observations or [], 'source_boundary': 10,
        'candidate': {'turn_id': 'PRIVATE', 'revision': 3, 'deadline': 45},
        'snapshot': {'private': 'NEVER SEND'}, 'shell_refs': [{'key': 'one', 'generation': 3}]}


def observation(line, command, result='ok', tool_id=None):
    return {'tool_id': tool_id, 'line': line, 'command': command, 'result': result}


def test_observation_threshold_ties_limits_exact_exclusion_and_health_checks():
    shell = {'key': 'one', 'owner': 'root', 'command': 'alpha bravo charlie delta echo foxtrot',
             'description': None, 'started_at': None, 'start_line': 1}
    evidence = [observation(1, shell['command']), observation(2, 'alpha'),
                observation(3, 'bravo'), observation(4, 'charlie'),
                observation(5, 'alpha unrelated otherword'), observation(6, 'health check'),
                observation(7, 'curl localhost'), observation(11, 'future health')]
    wire = classifier.build_classifier_payload(request(ending_payload([shell], evidence), kind='ending'))
    assert wire['related_tool_observations'] == [
        {'command': 'bravo', 'result': 'ok'}, {'command': 'charlie', 'result': 'ok'},
        {'command': 'health check', 'result': 'ok'}, {'command': 'curl localhost', 'result': 'ok'}]
    assert wire['candidate']['shells'][0]['command'] == shell['command']


def test_observation_markers_and_object_deduplication():
    evidence = [observation(1, 'serve readiness', 'a' * 1300, 'same'),
                observation(2, 'serve readiness', 'b' * 1300, 'same'),
                observation(3, 'health', 'c' * 1300), observation(4, 'health', 'c' * 1300)]
    wire = classifier.build_classifier_payload(request(ending_payload(observations=evidence), kind='ending'))
    assert wire['related_tool_observations'] == [
        {'command': 'serve readiness', 'result': 'a' * 600 + '\n[... ]\n' + 'a' * 600},
        {'command': 'health', 'result': 'c' * 600 + '\n[output excerpt]\n' + 'c' * 600}]


def test_utf8_budget_preserves_required_text_and_only_removes_optional_observations():
    payload = ending_payload(observations=[observation(i, 'serve ' + str(i), 'é' * 1200) for i in range(1, 8)])
    payload['previous_messages'][0]['text'] = 'é' * 9000
    wire = classifier.build_classifier_payload(request(payload, kind='ending'))
    assert len(orjson.dumps(wire)) <= 24000
    assert len(wire['related_tool_observations']) == 2
    assert wire['latest_assistant_response'] == 'é' * 9000
    payload['previous_messages'][0]['text'] = 'é' * 12000
    with pytest.raises(classifier.ClassificationError, match='context_incomplete'):
        classifier.build_classifier_payload(request(payload, kind='ending'))


@pytest.mark.parametrize('payload', [{'target': {'text': 'é' * 12000}, 'previous_messages': [], 'source_boundary': 1},
                                    {'target': {'text': 'ok'}, 'source_boundary': 1}])
def test_incomplete_required_context_is_not_classified(payload):
    with pytest.raises(classifier.ClassificationError, match='context_incomplete'):
        classifier.build_classifier_payload(request(payload))


def configure_routes(monkeypatch, *, enabled=True):
    monkeypatch.setattr(classifier, 'is_provider_running', lambda provider: enabled)
    monkeypatch.setattr(classifier, 'openrouter_key_set', lambda: True)


def run_classify(captured, provider=Provider.CLAUDE_CODE, seconds=1):
    async def scenario():
        return await classifier.classify(captured, session_provider=provider,
                                         deadline=asyncio.get_running_loop().time() + seconds)
    return asyncio.run(scenario())


@pytest.mark.parametrize('route', ['haiku', 'luna', 'openrouter'])
def test_transport_preserves_exact_request(monkeypatch, route):
    configure_routes(monkeypatch)
    captured = request(route=route)
    expected_system, expected_json = classifier.classifier_messages(captured)
    calls = []

    async def claude(prompt, *, model):
        calls.append((prompt, model))
        return SimpleNamespace(text='YES\nDelivered', is_error=False, assistant_error=None)

    async def prepare(model):
        calls.append(('prepare', model))
        return 'plan'

    async def codex(plan, prompt, *, effort):
        calls.append((plan, prompt, effort.value))
        return SimpleNamespace(text='YES\nDelivered', terminal_error=None)

    async def openrouter(model, prompt, *, system_prompt, timeout_seconds):
        from twicc.openrouter_text import _build_payload
        calls.append(_build_payload(model, prompt, system_prompt, stream=False))
        assert 0 < timeout_seconds <= 1
        return 'YES\nDelivered'

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    monkeypatch.setattr(classifier, 'prepare_hermetic_codex', prepare)
    monkeypatch.setattr(classifier, 'run_hermetic_codex', codex)
    monkeypatch.setattr(classifier, 'complete_openrouter_text', openrouter)
    assert run_classify(captured) == (Classification('YES', 'Delivered'), route)
    if route == 'haiku':
        assert calls == [(expected_system + '\n' + expected_json, 'haiku')]
    elif route == 'luna':
        assert calls == [('prepare', 'gpt-6-luna'), ('plan', expected_system + '\n' + expected_json, 'low')]
    else:
        assert calls == [{'model': 'qwen/qwen3.8-27b', 'messages': [
            {'role': 'system', 'content': expected_system}, {'role': 'user', 'content': expected_json}],
            'stream': False, 'provider': {'order': ['Cerebras'], 'allow_fallbacks': False},
            'usage': {'include': True}, 'reasoning': {'effort': 'none'}, 'max_tokens': 256, 'temperature': 0}]


def test_preparation_counts_toward_deadline(monkeypatch):
    configure_routes(monkeypatch)
    canceled = []

    async def prepare(model):
        try:
            await asyncio.Event().wait()
        finally:
            canceled.append(True)

    monkeypatch.setattr(classifier, 'prepare_hermetic_codex', prepare)
    with pytest.raises(classifier.ClassificationError, match='timeout'):
        run_classify(request(route='luna'), seconds=0.01)
    assert canceled == [True]


def test_disabled_providers_never_launch(monkeypatch):
    configure_routes(monkeypatch, enabled=False)
    with pytest.raises(classifier.ClassificationError, match='no_provider_available'):
        run_classify(request())


def test_openrouter_failure_starts_fallback_from_session_provider(monkeypatch):
    configure_routes(monkeypatch)
    order = []

    async def failed(*args, **kwargs):
        order.append('openrouter')
        raise RuntimeError('unavailable')

    async def prepare(model):
        order.append('luna')
        return 'plan'

    async def codex(*args, **kwargs):
        return SimpleNamespace(text='NO\nWaiting', terminal_error=None)

    monkeypatch.setattr(classifier, 'complete_openrouter_text', failed)
    monkeypatch.setattr(classifier, 'prepare_hermetic_codex', prepare)
    monkeypatch.setattr(classifier, 'run_hermetic_codex', codex)
    assert run_classify(request(route='openrouter'), Provider.CODEX) == (Classification('NO', 'Waiting'), 'luna')
    assert order == ['openrouter', 'luna']


def test_mixed_shells_use_one_aggregate_no(monkeypatch):
    configure_routes(monkeypatch)
    calls = []

    async def claude(prompt, *, model):
        calls.append(prompt)
        return SimpleNamespace(text='NO\nTests remain active', is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    assert run_classify(request(ending_payload(), kind='ending')) == (Classification('NO', 'Tests remain active'), 'haiku')
    assert len(calls) == 1
    assert 'serve app' in calls[0] and 'run tests' in calls[0]


def semantic_records(caplog):
    return [orjson.loads(record.getMessage().split('semantic_decision ', 1)[1])
            for record in caplog.records if 'semantic_decision ' in record.getMessage()]


def test_model_result_log_has_route_model_verdict_and_reason(monkeypatch, caplog):
    configure_routes(monkeypatch)
    caplog.set_level(logging.DEBUG, logger='twicc.timeline_classification')

    async def claude(*args, **kwargs):
        return SimpleNamespace(text='NO\n' + 'reason\n' * 150, is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    result, _ = run_classify(request())
    record, = semantic_records(caplog)
    assert record == {'event': 'model_result', 'session_id': 'session-id', 'decision_id': 'decision-id',
                      'kind': 'message', 'source_scope': {'provider': 'claude_code', 'storage_incarnation': 'source-id',
                                                        'history_epoch': 3},
                      'message_refs': [], 'shell_refs': [], 'requested_route': 'provider', 'effective_route': 'haiku',
                      'model': 'haiku', 'verdict': 'NO', 'outcome': None, 'reason': ('reason ' * 150).strip()[:500]}
    assert result.reason == record['reason']


def test_debug_log_excludes_context_payload(monkeypatch, caplog):
    configure_routes(monkeypatch)
    caplog.set_level(logging.DEBUG, logger='twicc.timeline_classification')

    async def claude(*args, **kwargs):
        return SimpleNamespace(text='YES\nDelivered', is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    run_classify(request(ending_payload(), kind='ending'))
    assert len(semantic_records(caplog)) == 1
    assert all(secret not in caplog.text for secret in ['serve app', 'run tests', 'NEVER SEND', 'Delivered\\"'])


def test_semantic_debug_records_are_absent_at_info_level(monkeypatch, caplog):
    configure_routes(monkeypatch)
    caplog.set_level(logging.INFO, logger='twicc.timeline_classification')

    async def claude(*args, **kwargs):
        return SimpleNamespace(text='YES\nDelivered', is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    run_classify(request())
    assert semantic_records(caplog) == []


def test_context_preparation_timeout_uses_stable_failure_code(monkeypatch):
    configure_routes(monkeypatch)

    async def blocked(request):
        await asyncio.Event().wait()

    monkeypatch.setattr(classifier, 'build_decision_context', blocked)
    with pytest.raises(classifier.ClassificationError, match='timeout'):
        run_classify(request(), seconds=0.01)


def test_first_route_uses_30_seconds_and_later_route_uses_15(monkeypatch):
    configure_routes(monkeypatch)
    budgets = []

    async def attempt(route, system_text, user_json, *, deadline):
        budgets.append((route, round(deadline - asyncio.get_running_loop().time())))
        if route == 'haiku':
            raise RuntimeError('first route fails')
        return 'YES\nDelivered'

    monkeypatch.setattr(classifier, '_attempt', attempt)
    assert run_classify(request(), seconds=100)[1] == 'luna'
    assert budgets == [('haiku', 30), ('luna', 15)]


def test_disabled_fallback_provider_is_checked_after_first_failure(monkeypatch):
    configure_routes(monkeypatch)
    enabled = {Provider.CLAUDE_CODE: True, Provider.CODEX: True}
    monkeypatch.setattr(classifier, 'is_provider_running', lambda provider: enabled[provider])

    async def claude(*args, **kwargs):
        enabled[Provider.CODEX] = False
        return SimpleNamespace(text='{"verdict":"YES"}', is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    with pytest.raises(classifier.ClassificationError, match='invalid_response'):
        run_classify(request())


def test_invalid_first_route_can_use_next_route(monkeypatch):
    configure_routes(monkeypatch)

    async def claude(*args, **kwargs):
        return SimpleNamespace(text='YES.', is_error=False, assistant_error=None)

    async def prepare(model):
        return 'plan'

    async def codex(*args, **kwargs):
        return SimpleNamespace(text='NO\nStill pending', terminal_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    monkeypatch.setattr(classifier, 'prepare_hermetic_codex', prepare)
    monkeypatch.setattr(classifier, 'run_hermetic_codex', codex)
    assert run_classify(request()) == (Classification('NO', 'Still pending'), 'luna')


def test_openrouter_works_without_any_running_agent(monkeypatch):
    configure_routes(monkeypatch, enabled=False)

    async def openrouter(*args, **kwargs):
        return 'YES\nDelivered'

    monkeypatch.setattr(classifier, 'complete_openrouter_text', openrouter)
    assert run_classify(request(route='openrouter')) == (Classification('YES', 'Delivered'), 'openrouter')


def test_cancellation_runs_runner_cleanup_without_trying_another_route(monkeypatch):
    configure_routes(monkeypatch)
    cleaned = []

    async def claude(*args, **kwargs):
        try:
            raise asyncio.CancelledError
        finally:
            cleaned.append(True)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    with pytest.raises(asyncio.CancelledError):
        run_classify(request())
    assert cleaned == [True]


def test_threshold_includes_exactly_point_15():
    shell = {'key': 'one', 'owner': 'root', 'command':
             'alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango',
             'description': None, 'started_at': None, 'start_line': 1}
    evidence = [observation(1, 'alpha bravo charlie'), observation(2, 'alpha bravo'),
                observation(3, 'health probe'), observation(4, 'latest probe')]
    wire = classifier.build_classifier_payload(request(ending_payload([shell], evidence), kind='ending'))
    assert wire['related_tool_observations'] == [
        {'command': 'alpha bravo charlie', 'result': 'ok'},
        {'command': 'health probe', 'result': 'ok'}, {'command': 'latest probe', 'result': 'ok'}]


def test_command_excerpt_is_2000_characters_and_full_shell_is_preserved():
    payload = ending_payload(observations=[observation(1, 'é' * 2100, 'x' * 1200)])
    payload['target']['shells'][0]['command'] = 'serve ' + 'é' * 2100
    wire = classifier.build_classifier_payload(request(payload, kind='ending'))
    assert wire['related_tool_observations'] == [{'command': 'é' * 2000, 'result': 'x' * 1200}]
    assert wire['candidate']['shells'][0]['command'] == 'serve ' + 'é' * 2100


@pytest.mark.django_db(transaction=True)
def test_isolated_calls_create_no_session_transcript_or_timeline_event(monkeypatch):
    from twicc.core.models import Session, SessionItem, TimelineEvent

    configure_routes(monkeypatch)
    counts = (Session.objects.count(), SessionItem.objects.count(), TimelineEvent.objects.count())

    async def claude(*args, **kwargs):
        return SimpleNamespace(text='YES\nDelivered', is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    assert run_classify(request())[0].verdict == 'YES'
    assert (Session.objects.count(), SessionItem.objects.count(), TimelineEvent.objects.count()) == counts


def test_ending_requires_complete_assistant_and_shell_command():
    payload = ending_payload()
    payload['previous_messages'] = []
    with pytest.raises(classifier.ClassificationError, match='context_incomplete'):
        classifier.build_classifier_payload(request(payload, kind='ending'))
    payload = ending_payload()
    payload['target']['shells'][1]['command'] = None
    with pytest.raises(classifier.ClassificationError, match='context_incomplete'):
        classifier.build_classifier_payload(request(payload, kind='ending'))


def test_internal_and_tool_entries_do_not_enter_message_window():
    payload = {'target': {'text': 'Full candidate'}, 'previous_messages': [
        {'role': 'user', 'text': 'Request'}, {'role': 'tool', 'text': 'Private tool result'},
        {'role': 'internal_context', 'text': 'Private internal context'},
        {'role': 'assistant', 'text': 'Prior response'}]}
    assert classifier.build_classifier_payload(request(payload)) == {
        'kind': 'message', 'preceding_messages': [{'role': 'user', 'text': 'Request'},
                                                {'role': 'assistant', 'text': 'Prior response'}],
        'candidate': {'text': 'Full candidate'}}


def test_parser_accepts_exact_response_byte_ceiling():
    result = classifier.parse_classification('message', 'YES\n' + 'x' * 8188)
    assert result == Classification('YES', 'x' * 500)


def test_model_logger_keeps_only_safe_captured_references(monkeypatch, caplog):
    configure_routes(monkeypatch)
    caplog.set_level(logging.DEBUG, logger='twicc.timeline_classification')
    payload = ending_payload()
    payload['message_refs'] = [{'kind': 'claude_message', 'native_id': 'answer', 'turn_id': None,
                               'text': 'SECRET candidate'}]
    payload['shell_refs'] = [{'key': 'one', 'generation': 3, 'started_at': 'captured',
                             'owner_ref': {'kind': 'claude_message', 'native_id': 'owner', 'turn_id': None,
                                           'text': 'SECRET owner'},
                             'command': 'SECRET command', 'output_path': 'SECRET output'}]

    async def claude(*args, **kwargs):
        return SimpleNamespace(text='YES\nDelivered', is_error=False, assistant_error=None)

    monkeypatch.setattr(classifier, 'run_hermetic_claude', claude)
    run_classify(request(payload, kind='ending'))
    record, = semantic_records(caplog)
    assert record['message_refs'] == [{'kind': 'claude_message', 'native_id': 'answer', 'turn_id': None}]
    assert record['shell_refs'] == [{'key': 'one', 'generation': 3, 'started_at': 'captured',
                                     'owner_ref': {'kind': 'claude_message', 'native_id': 'owner', 'turn_id': None}}]
    assert 'SECRET' not in caplog.text
