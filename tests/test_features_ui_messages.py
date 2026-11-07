"""Feature HTTP, stream and durable errors carry browser presentation metadata."""

import asyncio
from unittest.mock import patch

import orjson
import pytest
from django.test import RequestFactory, override_settings

from twicc.auth import views as auth_views
from twicc.core.services.peer_mutation import PeerError
from twicc.core.services.share_mutation import ShareError
from twicc.core.ui_messages import UiMessageError, ui_message, ui_message_text
from twicc.mcp.oauth.protection import Protection
from twicc.peer import outbound, owner_views as peer_views
from twicc.share import owner_views as share_views
from twicc.summaries import service as summaries
from twicc.tts import service, streams, views as tts_views
from twicc.tts.openrouter import SpeechError, upstream_error_message
from twicc.tts.script_audio import OutputCheck
from twicc.uploads import store


def payload(response):
    return orjson.loads(response.content)


@override_settings(TWICC_PASSWORD_HASH='configured')
def test_login_rate_limit_keeps_english_status_and_wait_parameter():
    request = RequestFactory().post('/api/auth/login/', data='{}', content_type='application/json')
    with patch.object(auth_views, '_check_rate_limit', return_value=42):
        response = asyncio.run(auth_views.login(request))
    assert response.status_code == 429
    assert payload(response) == {
        'error': 'Too many attempts. Try again in 42s.',
        'ui_message': ui_message('backend.auth.rateLimited', {'seconds': 42}),
    }


def test_speech_validation_survives_service_and_http_error_boundaries():
    async def run():
        with pytest.raises(service.SpeechError) as failure:
            await service.open_speech({'text': ''})
        error = failure.value
        assert error.status == 400
        assert error.message == "'text' must be a non-empty string"
        return tts_views._error(error.message, error.status, ui_message=error.ui_message)
    assert payload(asyncio.run(run())) == {
        'error': "'text' must be a non-empty string",
        'ui_message': ui_message('backend.tts.textRequired'),
    }


def test_dynamic_validation_keeps_field_name_as_parameter():
    with pytest.raises(ValueError) as failure:
        service.parse_speech_request({'text': 'hello', 'model': 'model', 'response_format': 'mp3', 'voice': 3})
    assert str(failure.value) == "'voice' must be a string"
    assert failure.value.args[0].ui_message == ui_message('backend.tts.fieldString', {'field': 'voice'})


def test_stream_failure_after_audio_keeps_metadata_and_original_diagnostic():
    async def run():
        streams._reset_for_tests()
        stream = streams.AudioStream({'model': 'model', 'voice': 'voice', 'format': 'mp3', 'chars': 5})
        streams._register(stream)
        stream.set_headers('audio/mpeg', None)
        stream.append(b'audio')
        stream.fail(SpeechError(ui_message_text('The text adaptation stopped before the end', 'backend.tts.adaptationStopped')))
        assert bytes(stream.data) == b'audio'
        assert streams.ending_of(stream.id) == {
            'state': 'failed', 'error': 'The text adaptation stopped before the end',
            'ui_message': ui_message('backend.tts.adaptationStopped'),
        }
        streams._reset_for_tests()
    asyncio.run(run())


def test_raw_provider_diagnostic_has_no_presentation_metadata():
    raw = 'Provider-specific detail: invalid language code xx-YY'
    message = upstream_error_message(400, orjson.dumps({'error': {'message': raw}}))
    error = SpeechError(message)
    assert error.message == raw
    assert error.ui_message is None
    assert upstream_error_message(503, b'not json').ui_message == ui_message('backend.tts.openrouterStatus', {'status': 503})


def test_script_audio_validation_retains_metadata_without_running_a_script():
    check = OutputCheck('mp3', None, 1)
    with pytest.raises(SpeechError) as failure:
        check.feed(b'not an mp3 file')
    assert failure.value.message == 'The script output is not MP3'
    assert failure.value.ui_message == ui_message('backend.tts.notMp3')


def test_summary_resolve_preserves_each_validation_descriptor(monkeypatch):
    def prepare(source):
        raise UiMessageError('Invalid source', 'backend.summaries.invalidSource')
    monkeypatch.setattr(summaries, '_prepare', prepare)
    assert summaries.resolve_sources([{}]) == [
        {'error': 'Invalid source', 'ui_message': ui_message('backend.summaries.invalidSource')},
    ]


@pytest.mark.parametrize('module,error', [
    (peer_views, PeerError('name', 'invalid', ui_message_text('Invalid name', 'backend.test.invalidName'))),
    (share_views, ShareError('name', 'invalid', ui_message_text('Invalid name', 'backend.test.invalidName'))),
])
def test_browser_named_tuple_errors_add_metadata_without_changing_rpc_shape(module, error):
    assert module._ui_errors([error]) == [{
        'field': 'name', 'code': 'invalid', 'message': 'Invalid name',
        'ui_message': ui_message('backend.test.invalidName'),
    }]
    assert orjson.loads(orjson.dumps(error._asdict())) == {'field': 'name', 'code': 'invalid', 'message': 'Invalid name'}


def test_peer_remote_fallback_retains_its_own_metadata():
    fallback = ui_message_text('Peer request failed.', 'backend.peer.requestFailed')
    assert outbound.response_error_message({'error': 'unknown_remote_code'}, fallback) is fallback
    known = outbound.response_error_message({'error': 'bad_state'}, fallback)
    assert known == 'The remote instance cannot apply this request in its current state.'
    assert known.ui_message == ui_message('backend.peer.remote.bad_state')


def test_upload_error_metadata_survives_disk_and_clears_with_state(tmp_path, monkeypatch):
    monkeypatch.setenv('TWICC_DATA_DIR', str(tmp_path))
    upload_id = store.new_upload_id()
    store.part_path(upload_id).touch()
    store.create_metadata(upload_id, client_id='tab:abc', size=4, filename='file.txt', target_dir='/tmp/target',
                          scope={'kind': 'standalone', 'root': None}, origin={'panel': 'files', 'key': ''}, fingerprint='fp')
    store.update_metadata(upload_id, error=ui_message_text('The target directory no longer exists', 'backend.uploads.targetGone'))
    loaded = store.read_metadata(upload_id)
    assert loaded['error'] == 'The target directory no longer exists'
    assert loaded['error_ui_message'] == ui_message('backend.uploads.targetGone')
    assert store.build_record(loaded)['ui_message'] == ui_message('backend.uploads.targetGone')
    cleared = store.update_metadata(upload_id, state=store.STATE_CANCELLED)
    assert cleared['error'] is None
    assert 'ui_message' not in store.build_record(cleared)


def test_mcp_protection_snapshot_adds_descriptor_at_browser_boundary():
    protection = Protection()
    protection.reject('source', capacity=True)
    snapshot = protection.snapshot()
    assert snapshot['incident']['reason'] == 'OAuth admission capacity reached'
    assert snapshot['incident']['reason_ui_message'] == ui_message('backend.mcp.admissionCapacity')
    assert 'reason_ui_message' not in protection.incident


@pytest.mark.parametrize("reason", ["off", "refused"])
def test_summary_engine_reason_metadata_keeps_closed_reason_code(reason, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(summaries, "resolve_engine", lambda *_: (None, reason))
    async def scenario():
        run = summaries._SummaryRun(SimpleNamespace(source_key=None, input_text="Selection"), "auto", "policy")
        await run.generate()
        snapshot = run.snapshot()
        assert snapshot["state"] == "failed"
        assert snapshot["error"] == reason
        assert snapshot["ui_message"] == ui_message(f"backend.summaries.reason.{reason}")
    asyncio.run(scenario())
