"""Consumer contracts remain stable across legacy, plain, and compressed storage."""
import base64

import orjson
import pytest
from django.db.models import Q

from twicc.core.content_storage.readers import effective_current, iter_matching_items, load_current
from twicc.core.models import Project, Session, SessionItem
from twicc.core.serializers import serialize_session_item, serialize_session_item_metadata
from twicc.providers.claude_code.compute import ClaudeCodeSessionCompute
from twicc.providers.history_facts import iter_history_items, iter_resolver_items
from twicc.views import _hybrid_attachment_image

pytestmark = pytest.mark.django_db


@pytest.fixture
def owner():
    project = Project.objects.create(id='consumer', directory='/consumer')
    return Session.objects.create(id='consumer', project=project, provider='claude_code', file_path='/missing',
                                  created_at='2026-01-01T00:00:00Z', user_message_count=1)


def put(owner, line, raw, factory, codec):
    return SessionItem.objects.create(
        session=owner, line_num=line, content=raw if codec == 'legacy' else 'stale',
        current_content=None if codec == 'legacy' else factory(raw, codec == 'zstd', 'claude_code'),
    )


@pytest.mark.parametrize('codec', ['legacy', 'plain', 'zstd'])
def test_raw_serializer_and_cli_shapes_stay_unchanged(owner, content_row_factory, codec, monkeypatch):
    raw = '{ "type": "user", "message": {"content": "exact"} }'
    item = put(owner, 1, raw, content_row_factory, codec)
    load_current([item])
    assert serialize_session_item(item)['content'] == raw
    from twicc.rpc.invoker import invoke

    monkeypatch.setattr('twicc.cli._twicc_info.resolve_live_twicc', lambda: None)
    cli = invoke(['session', owner.id, 'content'])
    assert cli.exit_code == 0, cli.error
    payload = cli.result if isinstance(cli.result, list) else cli.result['items']
    assert payload[0]['content'] == orjson.loads(raw)


def test_metadata_serializer_never_loads_payload(owner, content_row_factory, django_assert_num_queries):
    item = put(owner, 1, '{"type":"user"}', content_row_factory, 'zstd')
    item = SessionItem.objects.defer('content').get(pk=item.pk)
    with django_assert_num_queries(0):
        assert 'content' not in serialize_session_item_metadata(item)


def test_late_image_after_nonmatching_rows_survives_prefilter_limit(owner, content_row_factory):
    raw = orjson.dumps({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 'call',
        'content': [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'aGVsbG8='}}]}]}}).decode()
    put(owner, 1, raw, content_row_factory, 'zstd')
    for line in range(2, 20):
        put(owner, line, '{"noise":' + str(line) + '}', content_row_factory, 'plain')
    provider = ClaudeCodeSessionCompute()
    hits = list(provider.iter_images_backward(session_id=owner.id, before_line_num=20, images_needed=1))
    assert [hit.source_line_num for hit in hits] == [1]
    memory = [(19, None, orjson.loads(raw))]
    hits = list(provider.iter_images_backward(session_id=owner.id, before_line_num=20, images_needed=1,
                                             in_memory_items=memory))
    assert [hit.source_line_num for hit in hits] == [19]


def test_attachment_first_five_matching_rows_within_500_lines(owner, content_row_factory):
    ref = 'att_123456789abc.png'
    for line in range(2, 6):
        put(owner, line, orjson.dumps({'mention': '/' + ref, 'line': line}).decode(), content_row_factory, 'zstd' if line == 2 else 'plain')
    raw = orjson.dumps({'attachment': {'type': 'file', 'filename': '/' + ref,
        'content': {'type': 'image', 'file': {'type': 'image/png', 'base64': base64.b64encode(b'image').decode()}}}}).decode()
    put(owner, 6, raw, content_row_factory, 'zstd')
    assert _hybrid_attachment_image(owner, 1, ref) == ('image/png', b'image')
    put(owner, 1, orjson.dumps({'mention': '/' + ref, 'early': True}).decode(), content_row_factory, 'plain')
    assert _hybrid_attachment_image(owner, 0, ref) is None


def test_stale_history_facts_decode_raw_fallback(owner, content_row_factory):
    raw = '{"spawn":"complete"}'
    put(owner, 1, raw, content_row_factory, 'zstd')
    assert list(iter_history_items(owner.id, before_line=2)) == [(1, raw)]
    assert [effective_current(i) for i in iter_resolver_items(owner.id, None, before_line=2)] == [raw]


@pytest.mark.parametrize('terms', [
    ['alpha%', 'beta_'], ['ALPHA%', 'βETA_'], ['%', '_'], ['hidden', ''], ['alpha%\u0000suffix', 'beta_'],
])
def test_or_candidates_preserve_sqlite_order_and_limit(owner, content_row_factory, terms):
    texts = ['Alpha%', 'beta_', 'nothing', 'ALPHA%', 'βETA_', 'beta_\u0000hidden']
    for line, text in enumerate(texts, 1):
        put(owner, line, text, content_row_factory, 'zstd' if line % 2 else 'plain')
    oracle = SessionItem.objects.filter(session=owner).update(content='')
    assert oracle == len(texts)
    expected = []
    for line, text in enumerate(texts, 1):
        SessionItem.objects.filter(session=owner, line_num=line).update(content=text)
    qs = SessionItem.objects.filter(session=owner).order_by('-line_num')
    expected = list(qs.filter(Q(content__icontains=terms[0]) | Q(content__icontains=terms[1])).values_list('line_num', flat=True)[:3])
    assert [i.line_num for i in iter_matching_items(qs, terms, limit=3, match_all=False)] == expected
    required = list(qs.filter(Q(content__icontains=terms[0]) | Q(content__icontains=terms[1]),
                             content__icontains='a').values_list('line_num', flat=True)[:2])
    assert [i.line_num for i in iter_matching_items(qs, terms, limit=2, match_all=False, required_terms=['a'])] == required


def test_cold_workflow_links_and_synthesis_survive_provider_file_removal(owner, content_row_factory, tmp_path):
    from twicc.providers.claude_code.workflow_synthesis import _launch_args
    from twicc.views import _derive_workflow_links

    records = [
        {'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 'launch', 'name': 'Workflow',
            'input': {'args': {'prompt': 'durable'}}}]}},
        {'type': 'user', 'toolUseResult': {'runId': 'run-durable'}, 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 'launch', 'content': 'started'}]}},
    ]
    source = tmp_path / 'provider.jsonl'
    source.write_bytes(b'\n'.join(orjson.dumps(record) for record in records))
    owner.file_path = str(source)
    owner.archived = True
    owner.save()
    for line, record in enumerate(records, 1):
        put(owner, line, orjson.dumps(record).decode(), content_row_factory, 'zstd')
    source.unlink()
    assert _derive_workflow_links(owner.id) == [{'tool_use_id': 'launch', 'run_id': 'run-durable'}]
    assert _launch_args(owner.id, 'run-durable') == {'prompt': 'durable'}
