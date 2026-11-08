"""Browser presentation metadata survives core services without changing CLI values."""

import pickle
from types import SimpleNamespace

import orjson
import pytest

from twicc.agent.exceptions import SendDeliveryError
from twicc.core.models import Peer, PeerMessage, Project, Session, ToolResultLink
from twicc.core.services.attachments import committer, drop, inline, planner, staging
from twicc.core.services.session_creation import SessionCreationError, _validate_annotations
from twicc.core.services.session_visibility import _check_type_session
from twicc.core.services.share_mutation import _validate_session_options
from twicc.core.services.timeline_feed import FeedError, parse_request
from twicc.core.session_availability import unavailable_message
from twicc.core.session_queries import aggregate_tool_state, aggregate_tool_states
from twicc.core.ui_messages import UiMessageError, ui_message, ui_message_text
from twicc.drop_requests_watcher import _error_payload


def test_shared_errors_keep_original_cli_json_shape():
    error = _validate_annotations([])
    assert error == SessionCreationError('annotations', 'invalid_annotations', 'annotations must be a JSON object.')
    assert orjson.loads(orjson.dumps(_error_payload(error))) == {
        'field': 'annotations', 'code': 'invalid_annotations', 'message': 'annotations must be a JSON object.',
    }
    assert error.message.ui_message == ui_message('backend.sessions.annotations_object')


def test_attachment_validation_metadata_survives_exception_and_committer():
    with pytest.raises(staging.AttachmentError) as caught:
        staging.validate_ref({'bucket': 'draft', 'id': 'wrong'})
    error = caught.value
    assert str(error) == 'Attachment id must be a UUID'
    delivery = committer._delivery_error(error)
    assert delivery.ui_message == ui_message('backend.attachments.id_uuid')
    assert str(delivery).ui_message == delivery.ui_message


def test_attachment_names_keep_metadata_and_original_english():
    error = planner.AttachmentPlanError(
        planner.ERROR_REQUIRES_ARTIFACTS,
        ui_message_text('Ephemeral sessions only accept native attachments', 'backend.attachments.ephemeral_native_only'),
        names=('one.zip', 'two.wav'),
    )
    _, message, names = planner.describe_attachment_error(error)
    assert message == 'Ephemeral sessions only accept native attachments: one.zip, two.wav'
    assert message.ui_message == ui_message('backend.attachments.ephemeral_native_names', {'names': 'one.zip, two.wav'})
    assert names == ('one.zip', 'two.wav')
    assert drop.message_with_names(error).ui_message == message.ui_message


def test_explicit_exception_metadata_survives_session_creation_forwarding():
    error = SendDeliveryError('native diagnostic', ui_message=ui_message('backend.questions.stale'))
    assert drop.message_with_names(error) == 'native diagnostic'
    assert drop.message_with_names(error).ui_message == error.ui_message


def test_share_validation_does_not_add_namedtuple_fields():
    _, errors = _validate_session_options({'mode': 'wrong'})
    assert errors[0]._fields == ('field', 'code', 'message')
    assert errors[0].message == "mode must be 'snapshot' or 'live'"
    assert errors[0].message.ui_message == ui_message('backend.shares.invalid_mode')


def test_timeline_validation_has_localized_presentation_and_original_diagnostic():
    with pytest.raises(FeedError) as caught:
        parse_request({'limit': 'zero'})
    assert str(caught.value) == "limit must be an integer, got 'zero'."
    assert caught.value.ui_message == ui_message('backend.timeline.integer', {'name': 'limit', 'value': "'zero'"})


def test_unavailable_message_keeps_session_reason_and_cli_text():
    message = unavailable_message('session-id', 'missing_rollout')
    assert message == "Session 'session-id' is unavailable (missing_rollout): TwiCC cannot read its history or continue it."
    assert message.ui_message['params'] == {'session_id': "'session-id'", 'reason': 'missing_rollout'}


def test_hidden_session_guard_keeps_metadata():
    error = _check_type_session(SimpleNamespace(type='subagent'))[0]
    assert error.message.ui_message['key'] == 'backend.sessions.hide_top_level'


def test_peer_inline_budget_localizes_owned_hint_as_part_of_message():
    message = inline.too_large_message(51 * inline.MIB, inline.INLINE_MAX_BYTES, inline.PEER_TOO_LARGE_HINT)
    assert message.ui_message == ui_message('backend.attachments.peer_too_large', {'size': '51 MB', 'limit': '50 MB'})
    assert not hasattr(inline.too_large_message(1, 0, 'custom diagnostic'), 'ui_message')


@pytest.mark.django_db
def test_tool_error_metadata_tracks_selected_error_and_keeps_rich_extra():
    project = Project.objects.create(id='ui-core-project')
    session = Session.objects.create(id='ui-core-session', project=project)
    # Max(extra) selects z-rich; Max(error) selects z-error from the OTHER source row.
    ToolResultLink.objects.create(
        session=session, tool_use_line_num=1, tool_result_line_num=2, tool_use_id='tool',
        error='a-error', extra=orjson.dumps({'z-rich': True, 'error_ui_message': ui_message('wrong')}).decode(),
    )
    ToolResultLink.objects.create(
        session=session, tool_use_line_num=1, tool_result_line_num=3, tool_use_id='tool',
        error='z-error', extra=orjson.dumps({'a-small': True, 'error_ui_message': ui_message('chosen')}).decode(),
    )
    links = ToolResultLink.objects.filter(session=session)
    for result in (aggregate_tool_state(links), aggregate_tool_states(links)['tool']):
        assert result['error'] == 'z-error'
        assert orjson.loads(result['extra']) == {'z-rich': True, 'error_ui_message': ui_message('chosen')}
    # An external selected error has no metadata; stale metadata from another link must disappear.
    links.filter(error='z-error').update(extra=None)
    for result in (aggregate_tool_state(links), aggregate_tool_states(links)['tool']):
        assert orjson.loads(result['extra']) == {'z-rich': True}


def test_shared_project_defaults_keep_original_diagnostic_and_metadata():
    from twicc.core.services.project_mutation import clean_project_agent_defaults

    _, _, error = clean_project_agent_defaults("unknown", None)
    assert error == "Invalid default_provider: 'unknown'"
    assert error.ui_message == ui_message('backend.projects.invalid_default_provider', {'provider': "'unknown'"})


def test_presentation_text_and_error_survive_pickle():
    message = ui_message_text('original text', 'backend.test', {'value': 3}, count=2)
    restored_text = pickle.loads(pickle.dumps(message))
    assert restored_text == message
    assert restored_text.ui_message == message.ui_message
    error = UiMessageError('original_code', 'backend.questions.invalid', {'value': 3})
    restored_error = pickle.loads(pickle.dumps(error))
    assert str(restored_error) == 'original_code'
    assert restored_error.ui_message == error.ui_message


@pytest.mark.django_db
def test_stored_peer_metadata_is_browser_opt_in_only():
    from twicc.core.serializers import serialize_peer_message

    peer = Peer.objects.create(id='peer_12345678', base_url='https://peer.example', state='active')
    descriptor = ui_message('backend.peer_messages.peer_rejected_token')
    message = PeerMessage.objects.create(
        peer=peer, direction='out', message_id='pm_1234567812345678', thread_id='pm_1234567812345678',
        status='failed', error='peer_rejected_token', error_ui_message=descriptor,
    )
    message.refresh_from_db()
    cli = serialize_peer_message(message)
    browser = serialize_peer_message(message, include_ui_messages=True)
    assert 'error_ui_message' not in cli
    assert browser == {**cli, 'error_ui_message': descriptor}
    assert cli['error'] == 'peer_rejected_token'


def test_standard_runtime_error_keeps_tagged_message_when_forwarded():
    text = ui_message_text('Session is already active', 'backend.sessions.already_active')
    error = RuntimeError(text)
    assert not hasattr(str(error), 'ui_message')
    forwarded = drop.message_with_names(error)
    assert forwarded == text
    assert forwarded.ui_message == text.ui_message


@pytest.mark.django_db
def test_tagged_process_error_records_and_only_browser_timeline_exposes_metadata():
    from django.utils import timezone

    from twicc.agent.states import AgentState
    from twicc.agent.timeline_hooks import ProcessSnapshot, record_process_ended, record_process_started
    from twicc.core.enums import Provider
    from twicc.core.models import TimelineEvent, TimelineKind
    from twicc.core.services.timeline_feed import fetch_click_boundary, fetch_feed, serialize_event
    from twicc.core.services.timeline_listing import fetch_timeline_page
    from twicc.core.timeline import validate_data

    project = Project.objects.create(id='timeline-ui-project')
    session = Session.objects.create(id='timeline-ui-session', project=project, provider='codex')
    descriptor = ui_message('backend.questions.stale')
    error = 'Original English failure'
    agent = SimpleNamespace(
        session_id=session.id, project_id=project.id, provider=Provider.CODEX, ephemeral=False,
        process_run=None, kill_reason='error', error=error, error_ui_message=descriptor,
    )
    boundary = timezone.now().isoformat()
    record_process_started(agent, resume=True)
    record_process_ended(agent, ProcessSnapshot(AgentState.USER_TURN.value, False, None))
    event = TimelineEvent.objects.get(session_id=session.id, kind=TimelineKind.PROCESS_ENDED)
    assert event.data['error'] == error
    assert event.data['error_ui_message'] == descriptor
    assert event.data['provider'] == 'codex' and event.data['cause'] == 'resume'
    assert validate_data(TimelineKind.PROCESS_ENDED, {'error': error, 'error_ui_message': descriptor})[
        'error_ui_message'
    ] == descriptor
    # Existing process events keep the exact stored data shape, without a new null field.
    old_payload = validate_data(TimelineKind.PROCESS_ENDED, {'error': error})
    assert old_payload == {
        'provider': None, 'cause': None, 'twicc_pid': None, 'hybrid': None,
        'error': error, 'upper_bound': None,
    }
    request = parse_request({'session': session.id})
    for body in (fetch_feed(request), fetch_click_boundary(request, boundary)):
        browser = next(row for row in body['events'] if row['id'] == event.id)
        assert browser['data'] == event.data
    cli_page = fetch_timeline_page(
        project_ids=None, session_id=session.id, selected_sessions=Session.objects.all(),
        require_session_metadata=False, include_instance=False, limit=20, before=None,
        known_projects={project.id: project}, known_sessions={session.id: session}, process_rows={},
    )
    cli = next(row for row in cli_page['items'] if row['id'] == event.id)
    assert cli['data'] == {name: value for name, value in event.data.items() if name != 'error_ui_message'}
    assert serialize_event(event) == cli
    assert event.data['error_ui_message'] == descriptor  # Serialization never mutates stored content.
