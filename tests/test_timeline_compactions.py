"""Compactions in the timeline (``core/services/timeline_compactions.py``): requested, completed, never a message."""

from datetime import timedelta

import pytest
from django.utils import timezone

from tests.db_write_helpers import run_with_direct_writes
from tests.test_timeline_messages import live_session  # noqa: F401 (fixture)
from twicc.core.models import SessionItem, TimelineEvent
from twicc.core.services.timeline_compactions import record_compaction_request_sent
from twicc.core.enums import ItemKind
from twicc.providers.helpers import get_provider_helpers

# The send path writes from a worker thread (record_event): it needs real transactions.
pytestmark = pytest.mark.django_db(transaction=True)

CLAUDE_COMPACT = "<command-name>/compact</command-name>\n<command-message>compact</command-message>\n<command-args>{}</command-args>"


def compaction_events(session, kind):
    return list(TimelineEvent.objects.filter(session_id=session.id, kind=kind).order_by('id'))


def send(f, text):
    """Deliver ``text`` the way the send paths do (the lock-guarded write runs directly)."""
    run_with_direct_writes(lambda: record_compaction_request_sent(
        session_id=f.session.id, project_id=f.session.project_id, provider=f.provider, text=text))


def compact_line(f, prompt=''):
    text = CLAUDE_COMPACT.format(prompt) if f.provider == 'claude_code' else '/compact'
    return f.message('compact-request', text, user=True)


def summary_lines(f, *, trigger='auto', text='The summary.'):
    now = timezone.now().isoformat()
    if f.provider == 'claude_code':
        return (
            {'type': 'system', 'subtype': 'compact_boundary', 'uuid': 'boundary', 'timestamp': now,
             'content': 'Conversation compacted', 'compactMetadata': {'trigger': trigger, 'preTokens': 1000}},
            {'type': 'user', 'uuid': 'summary', 'isCompactSummary': True, 'timestamp': now,
             'message': {'role': 'user', 'content': text}},
        )
    return ({'type': 'compacted', 'timestamp': now,
             'payload': {'message': '', 'replacement_history': [{'type': 'compaction', 'encrypted_content': 'x'}]}},)


def test_a_compact_command_line_is_a_compaction_request_never_a_message(live_session):  # noqa: F811
    f = live_session
    f.append(compact_line(f, 'keep the **plan**'), f.message('u', 'Hello', user=True))
    assert f.texts() == ['Hello']
    [request] = compaction_events(f.session, 'compaction.requested')
    assert request.data == {'text': 'keep the **plan**' if f.provider == 'claude_code' else None, 'source': 'source'}
    assert request.actor == 'external'


def test_a_compact_line_after_a_sent_request_does_not_double_it(live_session):  # noqa: F811
    f = live_session
    send(f, '/compact')
    f.append(compact_line(f))
    [request] = compaction_events(f.session, 'compaction.requested')
    assert request.data['source'] == 'send'
    assert f.texts() == []


def test_a_sent_request_far_from_the_line_does_not_stand_for_it(live_session):  # noqa: F811
    f = live_session
    send(f, '/compact')
    TimelineEvent.objects.filter(kind='compaction.requested').update(occurred_at=timezone.now() - timedelta(hours=1))
    f.append(compact_line(f))
    assert [event.data['source'] for event in compaction_events(f.session, 'compaction.requested')] == ['send', 'source']


def test_sending_records_the_request_at_once_with_its_prompt_and_only_for_a_whole_compact_command(live_session):  # noqa: F811
    f = live_session
    for text in ('/compact   keep X  ', 'hello', '/compactor', 'please /compact', ''):
        send(f, text)
    [request] = compaction_events(f.session, 'compaction.requested')
    assert request.data == {'text': 'keep X' if f.provider == 'claude_code' else None, 'source': 'send'}


def test_a_compaction_summary_is_a_completed_compaction_with_its_trigger_and_readable_summary(live_session):  # noqa: F811
    f = live_session
    f.append(*summary_lines(f, trigger='auto', text='What happened so far.'))
    [completed] = compaction_events(f.session, 'compaction.completed')
    if f.provider == 'claude_code':
        assert completed.data == {'trigger': 'auto', 'text': 'What happened so far.'}
    else:
        # Codex encrypts its summary; no request is open: automatic.
        assert completed.data == {'trigger': 'auto', 'text': None}
    assert f.texts() == []


def test_an_open_request_makes_a_compaction_manual_when_the_provider_does_not_say(live_session):  # noqa: F811
    f = live_session
    send(f, '/compact')
    f.append(*summary_lines(f, trigger='manual'))
    assert [event.data['trigger'] for event in compaction_events(f.session, 'compaction.completed')] == ['manual']
    # The next one, with no request in between, is automatic (Claude says so, Codex has no open request).
    f.append(*[{**line, 'uuid': f"{line.get('uuid', '')}-2"} for line in summary_lines(f, trigger='auto')])
    assert [event.data['trigger'] for event in compaction_events(f.session, 'compaction.completed')] == ['manual', 'auto']


def test_nothing_is_recorded_outside_the_live_window(live_session):  # noqa: F811
    f = live_session
    f.append(compact_line(f), *summary_lines(f), since=timezone.now() + timedelta(days=1))
    assert not TimelineEvent.objects.filter(session_id=f.session.id, kind__startswith='compaction.').exists()


def test_a_compaction_request_is_not_searched(live_session):  # noqa: F811
    f = live_session
    f.append(compact_line(f, 'keep it'), f.message('u', 'Searchable words', user=True), capture=False)
    items = SessionItem.objects.filter(session=f.session, kind=ItemKind.USER_MESSAGE).order_by('line_num')
    indexed = get_provider_helpers(f.provider).get_indexable_messages(items)
    assert [message.text for message in indexed] == ['Searchable words']
