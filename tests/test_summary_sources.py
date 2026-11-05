"""Summary sources use native groups and immutable current-content snapshots."""

from types import SimpleNamespace
from uuid import uuid4

import orjson
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from tests import content_storage_helpers, test_timeline_messages
from twicc.core.models import LogicalTurn, Session, SessionItem

live_session = test_timeline_messages.live_session
content_row_factory = content_storage_helpers.content_row_factory
pytestmark = pytest.mark.django_db


def resolve(session, line=1):
    from twicc.summaries.sources import resolve_source
    return resolve_source({'session_id': session.id, 'line_num': line})


def test_complete_native_messages_and_timeline_share_source(live_session):
    from twicc.summaries.sources import resolve_source
    f = live_session
    f.append(f.message('answer', '**' + 'é' * 1000 + '**'))
    chat = resolve(f.session)
    snapshot = resolve_source({'event_id': f.events()[0].id})
    assert chat.source_key == snapshot.source_key
    assert chat.source_hash == snapshot.source_hash
    assert chat.canonical_text == '**' + 'é' * 1000 + '**'
    assert chat.content_chars == 1000
    assert chat.eligible and snapshot.eligible
    assert chat.first_line == 1 and chat.text_lines == (1,)
    with pytest.raises(AttributeError):
        chat.first_line = 3


def test_claude_group_keeps_order_duplicates_and_text_anchor(live_session):
    f = live_session
    if f.provider != 'claude_code':
        pytest.skip('Claude split-message records')
    thinking = f.message('thinking', '', message_id='group', final=None)
    thinking['message']['content'] = [{'type': 'thinking', 'thinking': 'private'}]
    first = f.message('first', 'A' * 500, message_id='group', final=None)
    second = f.message('second', 'B' * 500, message_id='group', final=False)
    tool = f.message('tool', '', message_id='group', final=False)
    tool['message']['content'] = [{'type': 'tool_use', 'id': 'call', 'name': 'Read', 'input': {}}]
    f.append(thinking, first, second, second, tool)
    result = resolve(f.session, 3)
    assert result.canonical_text == 'A' * 500 + '\n' + 'B' * 500
    assert result.first_line == 2 and result.text_lines == (2, 3)
    assert result.eligible
    assert resolve(f.session, 1) == result
    assert resolve(f.session, 4) == result
    assert f.events() == []  # Manual completeness does not change timeline admission.


def test_distinct_native_messages_do_not_merge(live_session):
    f = live_session
    f.append(f.message('first', 'A' * 499), f.message('second', 'B' * 500))
    first, second = resolve(f.session), resolve(f.session, 2)
    assert first.source_key != second.source_key
    assert first.content_chars == 499 and not first.eligible
    assert first.unavailable_reason == 'too_short'
    assert second.eligible


def test_complete_commentary_is_manually_eligible_without_timeline(live_session):
    f = live_session
    f.append(f.message('commentary', 'A' * 1000, final=False), capture=False)
    assert resolve(f.session).eligible
    assert f.events() == []


def test_unknown_historical_message_and_changed_snapshot(live_session):
    from twicc.summaries.sources import resolve_source
    f = live_session
    f.append(f.message('answer', 'A' * 1000))
    event = f.events()[0]
    before = resolve_source({'event_id': event.id})
    replacement = f.message('answer', 'B' * 1000, final=None)
    SessionItem.objects.filter(session=f.session, line_num=1).update(current_content_id=None, content=orjson.dumps(replacement).decode())
    after = resolve(f.session)
    saved = resolve_source({'event_id': event.id})
    assert after.eligible
    assert before.source_key == after.source_key == saved.source_key
    assert before.source_hash == saved.source_hash != after.source_hash
    assert saved.canonical_text == 'A' * 1000


def test_storage_and_history_replacement_change_identity(live_session):
    from twicc.summaries.sources import resolve_source
    f = live_session
    f.append(f.message('answer', 'A' * 1000))
    before = resolve(f.session)
    event = f.events()[0]
    Session.objects.filter(pk=f.session.pk).update(storage_incarnation=str(uuid4()), history_epoch=7)
    after = resolve(f.session)
    snapshot = resolve_source({'event_id': event.id})
    assert before.source_key != after.source_key
    assert snapshot.source_key == before.source_key
    assert snapshot.source_hash == before.source_hash == after.source_hash


def test_legacy_snapshot_validates_native_identity_and_exact_text(live_session):
    from twicc.core.models import TimelineEvent
    from twicc.summaries.sources import resolve_source
    f = live_session
    f.append(f.message('answer', 'A' * 1000))
    event = f.events()[0]
    legacy = {key: value for key, value in event.data.items()
              if key not in {'storage_incarnation', 'history_epoch'}}
    TimelineEvent.objects.filter(pk=event.pk).update(data=legacy)
    assert resolve_source({'event_id': event.id}).source_key == resolve(f.session).source_key
    legacy['text'] = 'old saved text ' * 100
    TimelineEvent.objects.filter(pk=event.pk).update(data=legacy)
    old = resolve_source({'event_id': event.id})
    assert old.canonical_text == legacy['text'] and old.eligible
    assert old.source_key != resolve(f.session).source_key
    assert old.first_line is None and old.text_lines == ()


def test_growing_source_uses_native_completion_and_active_floor(live_session, monkeypatch):
    from twicc.agent import registry
    f = live_session
    f.append(f.message('historical', 'A' * 1000, final=None), capture=False)
    f.append(f.message('growing', 'B' * 1000, final=None), capture=False)
    manager = registry.get_agent_manager_registry().get(f.provider)
    monkeypatch.setitem(manager._agents, f.session.id, SimpleNamespace(
        state='assistant_turn', _completion_source_floor=1,
        _completion_storage_incarnation=f.session.storage_incarnation,
        _completion_history_epoch=f.session.history_epoch,
        ephemeral=False, _timeline_message_refs={}, _completion_openings={},
    ))
    assert resolve(f.session).eligible  # An older unknown message remains usable.
    growing = resolve(f.session, 2)
    if f.provider == 'codex':
        assert growing.eligible  # item_completed is completion evidence, regardless of phase.
    else:
        assert not growing.eligible and growing.unavailable_reason == 'incomplete'
        unidentified = f.message('unidentified', 'unidentified assistant', final=None)
        unidentified['message'].pop('id')
        f.append(unidentified, capture=False)
        assert not resolve(f.session, 2).eligible
        f.append(f.message('terminal', 'C', message_id='growing'))
        assert resolve(f.session, 2).eligible


def test_open_logical_turn_blocks_only_its_unknown_source(live_session):
    f = live_session
    f.append(f.message('old', 'A' * 1000, final=None), f.message('live', 'B' * 1000, final=None), capture=False)
    LogicalTurn.objects.create(id=uuid4(), run_id=uuid4(), session_id=f.session.id, provider=f.provider,
        ordinal=1, position=1, source_floor=1, storage_incarnation=f.session.storage_incarnation,
        history_epoch=f.session.history_epoch, first_ref=None, opened_at=timezone.now())
    assert resolve(f.session).eligible
    assert resolve(f.session, 2).eligible is (f.provider == 'codex')


def test_current_compressed_content_is_authoritative(live_session, content_row_factory):
    f = live_session
    f.append(f.message('answer', 'stale'), capture=False)
    current = content_row_factory(orjson.dumps(f.message('answer', 'A' * 1000)).decode(), True, f.provider)
    SessionItem.objects.filter(session=f.session).update(current_content=current)
    assert resolve(f.session).canonical_text == 'A' * 1000


def test_counting_markdown_code_tables_labels_and_whitespace():
    from twicc.summaries.sources import content_length, content_text
    text = '# **Hé**\n\n[Label](https://secret.example/path)\n\n```py\nx = 1\n```\n\n| A | B |\n|---|---|\n| é | z |'
    assert content_text(text) == 'Hé Label x = 1 A B é z'
    assert content_length(' é\t' * 500) == 999
    assert content_length(' é\t' * 500 + 'x') == 1001
    assert content_length('é' * 1000) == 1000
    assert content_text('a**b**c `d` ![image label](private.png)') == 'abc d image label'


def test_artifact_publications_are_removed_without_reading_files(live_session, monkeypatch):
    from pathlib import Path
    from twicc.summaries.sources import content_length
    f = live_session
    tag = '<twicc:inline-artifact id="chart" src="inline-artifacts/chart/index.html" title="Secret"/>'
    f.append(f.message('artifact', tag), capture=False)
    monkeypatch.setattr(Path, 'read_text', lambda *args, **kwargs: pytest.fail('Artifact source file read'))
    assert content_length(tag) == 0
    result = resolve(f.session)
    assert result.input_text.strip() == '' and result.canonical_text == tag
    assert result.content_chars == 0 and not result.eligible
    assert result.unavailable_reason == 'empty'


def test_raw_canonical_fingerprint_survives_prose_normalization(live_session):
    f = live_session
    f.append(f.message('answer', '**' + 'A' * 1000 + '**'), capture=False)
    before = resolve(f.session)
    SessionItem.objects.filter(session=f.session).update(current_content_id=None, content=orjson.dumps(f.message('answer', 'A' * 1000)).decode())
    after = resolve(f.session)
    assert before.content_chars == after.content_chars == 1000
    assert before.source_key == after.source_key and before.source_hash != after.source_hash


def test_temporary_sources_have_no_saved_message_identity():
    from twicc.summaries.sources import resolve_source
    result = resolve_source({'text': 'Short text', 'purpose': 'selection'})
    assert result.eligible and result.session_id is None and result.source_key is None
    assert result.first_line is None and result.text_lines == ()
    assert resolve_source({'text': 'test', 'purpose': 'test'}).eligible
    assert resolve_source({'text': '---', 'purpose': 'selection'}).eligible


def test_counting_excludes_control_markup_and_keeps_visible_labels():
    from twicc.summaries.sources import content_text
    assert content_text(':: Sender\n\n::: **Note**\nBody\n:::') == 'Sender Note Body'
    assert content_text('<!-- hidden -->\nVisible\n<!-- hidden -->') == 'Visible'
    assert content_text('<twicc:context>private context</twicc:context>\nVisible') == 'Visible'
    assert content_text('Visible <twicc:instruction>private instruction</twicc:instruction>') == 'Visible'
    assert content_text('`<twicc:inline-artifact id="chart"/>`') == '<twicc:inline-artifact id="chart"/>'


@pytest.mark.parametrize('wrapper', ['```xml\n{}\n```', '~~~xml\n{}\n~~~', '`{}`', '``{}``', '    {}',
                                   ':: `{}`', '::: `{}`\n:::'])
@pytest.mark.parametrize('tag', ['instruction', 'context'])
def test_literal_control_tags_in_code_remain_in_counts_and_generation_input(wrapper, tag):
    from twicc.summaries.sources import content_text, resolve_source

    code = f'<twicc:{tag}>' + 'x' * 1000 + f'</twicc:{tag}>'
    text = wrapper.format(code)
    result = resolve_source({'text': text, 'purpose': 'selection'})
    assert result.canonical_text == result.input_text == text
    assert content_text(text) == code
    assert result.content_chars == len(code)


def test_real_control_blocks_are_removed_around_literal_code():
    from twicc.summaries.sources import content_text, resolve_source

    literal = '<twicc:instruction>' + 'x' * 1000 + '</twicc:instruction>'
    text = ('<twicc:context>private context</twicc:context>\n\n'
            f'```xml\n{literal}\n```\n\n'
            'Visible <twicc:instruction>private `code` instruction</twicc:instruction>')
    result = resolve_source({'text': text, 'purpose': 'selection'})
    assert result.canonical_text == text
    assert literal in result.input_text
    assert 'private' not in result.input_text
    assert content_text(text) == literal + ' Visible'


def test_literal_opening_tag_does_not_hide_a_later_real_instruction():
    from twicc.summaries.sources import resolve_source

    text = '`<twicc:instruction>` Visible <twicc:instruction>private</twicc:instruction>'
    assert resolve_source({'text': text, 'purpose': 'selection'}).input_text == '`<twicc:instruction>` Visible'


def test_control_removal_preserves_fence_and_prose_line_boundaries():
    from twicc.summaries.sources import content_text, resolve_source

    text = '```xml\nliteral code\n```\n\n<twicc:instruction>private</twicc:instruction>\n\nVisible'
    result = resolve_source({'text': text, 'purpose': 'selection'})
    assert '```\n' in result.input_text
    assert content_text(text) == 'literal code Visible'


def test_stored_code_message_keeps_summary_eligibility(live_session):
    f = live_session
    text = '```xml\n<twicc:instruction>' + 'x' * 1000 + '</twicc:instruction>\n```'
    # Store the provider record directly: this test targets the summary boundary.
    SessionItem.objects.create(session=f.session, line_num=1, kind='assistant_message',
        message_id='code' if f.provider == 'claude_code' else None,
        content=orjson.dumps(f.message('code', text)).decode())
    result = resolve(f.session)
    assert result.eligible and result.content_chars > 1000
    assert result.input_text == result.canonical_text == text


@pytest.mark.parametrize('source', [{}, {'text': '', 'purpose': 'selection'}, {'text': 'x'},
    {'session_id': 'x', 'line_num': True}, {'event_id': 0}, {'event_id': 1, 'text': 'x', 'purpose': 'test'}])
def test_invalid_sources_are_rejected(source):
    from twicc.summaries.sources import resolve_source
    with pytest.raises(ValueError):
        resolve_source(source)


def test_successful_summary_uniqueness_and_session_cascade(live_session):
    from twicc.core.models import MessageSummary
    f = live_session
    other = Session.objects.create(id='other', provider=f.provider, project=f.session.project)
    fields = {'source_key': 'identity', 'source_hash': 'a' * 64, 'policy_key': 'policy',
        'requested_model': 'auto', 'engine': 'luna', 'text': 'Saved summary', 'metadata': {'result_hash': 'b' * 64},
        'completed_at': timezone.now()}
    MessageSummary.objects.create(session=f.session, **fields)
    retained = MessageSummary.objects.create(session=other, **fields)
    with pytest.raises(IntegrityError), transaction.atomic():
        MessageSummary.objects.create(session=f.session, **fields)
    MessageSummary.objects.create(session=f.session, **{**fields, 'policy_key': 'new-policy'})
    f.session.delete()
    assert list(MessageSummary.objects.values_list('id', flat=True)) == [retained.id]


def test_the_minimum_counts_the_source_text_markdown_included(live_session):
    f = live_session
    f.append(f.message('answer', '**' + 'A' * 496 + '**'))
    source = resolve(f.session)
    assert source.content_chars == 496 and source.eligible
