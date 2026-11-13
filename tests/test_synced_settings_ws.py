"""WebSocket result frames for correlated synced-settings writes."""

from asgiref.sync import async_to_sync

from twicc.asgi import WSConsumer
from twicc.core.services import settings_mutation
from twicc.core.services.settings_mutation import SettingsDropError, SettingsUpdateResult


def _frames(monkeypatch, result, *, request_id="origin-write", value="HTTPS://PEER.EXAMPLE:443/"):
    async def fake_update(_patch, *, base_version):
        assert base_version == 4
        return result

    frames = []

    async def send_json(frame):
        frames.append(frame)

    monkeypatch.setattr(settings_mutation, "update_synced_settings", fake_update)
    consumer = WSConsumer()
    consumer.send_json = send_json
    payload = {
        "settings": {"peerBaseUrl": value},
        "baseVersion": 4,
    }
    if request_id is not None:
        payload["request_id"] = request_id
    async_to_sync(consumer._handle_update_synced_settings)(payload)
    return frames


def test_correlated_acceptance_returns_the_authoritative_submitted_value(monkeypatch):
    result = SettingsUpdateResult(
        "accepted", 5, {"peerBaseUrl": "https://peer.example"},
        {"peerBaseUrl": "https://peer.example"},
    )
    assert _frames(monkeypatch, result) == [{
        "type": "synced_settings_result",
        "request_id": "origin-write",
        "status": "accepted",
        "settings": {"peerBaseUrl": "https://peer.example"},
        "version": 5,
        "errors": [],
    }]


def test_correlated_acceptance_without_correction_still_returns_one_result(monkeypatch):
    result = SettingsUpdateResult(
        "accepted", 5, {}, {"peerBaseUrl": "https://peer.example"},
    )
    assert _frames(monkeypatch, result, value="https://peer.example") == [{
        "type": "synced_settings_result",
        "request_id": "origin-write",
        "status": "accepted",
        "settings": {"peerBaseUrl": "https://peer.example"},
        "version": 5,
        "errors": [],
    }]


def test_correlated_rejection_sends_resync_then_the_matching_error_result(monkeypatch):
    error = SettingsDropError(
        "peerBaseUrl",
        "origin_conflict_ambiguous_authority",
        "The Peer and External addresses must be the same origin or use different authorities.",
    )
    clean = {"peerBaseUrl": "https://stored.example"}
    result = SettingsUpdateResult("rejected", 4, {}, clean, (error,))
    assert _frames(monkeypatch, result) == [
        {"type": "synced_settings_updated", "settings": clean, "version": 4},
        {
            "type": "synced_settings_result",
            "request_id": "origin-write",
            "status": "rejected",
            "settings": {"peerBaseUrl": "https://stored.example"},
            "version": 4,
            "errors": [error._asdict()],
        },
    ]


def test_correlated_stale_rejection_has_an_empty_error_list(monkeypatch):
    clean = {"peerBaseUrl": "https://remote.example"}
    result = SettingsUpdateResult("rejected", 8, {}, clean)
    assert _frames(monkeypatch, result) == [
        {"type": "synced_settings_updated", "settings": clean, "version": 8},
        {
            "type": "synced_settings_result",
            "request_id": "origin-write",
            "status": "rejected",
            "settings": {"peerBaseUrl": "https://remote.example"},
            "version": 8,
            "errors": [],
        },
    ]


def test_idless_rejection_keeps_the_legacy_error_frame(monkeypatch):
    error = SettingsDropError("peerBaseUrl", "invalid_origin_host", "Invalid address.")
    result = SettingsUpdateResult("rejected", 4, {}, {"peerBaseUrl": ""}, (error,))
    frames = _frames(monkeypatch, result, request_id=None)
    assert [frame["type"] for frame in frames] == ["synced_settings_updated", "error"]
    assert frames[1]["code"] == "invalid_synced_settings"


def test_summary_settings_use_the_ordinary_merge_and_broadcast(monkeypatch, tmp_path):
    import twicc.synced_settings as synced

    path = tmp_path / "settings.json"
    monkeypatch.setattr(synced, "get_synced_settings_path", lambda: path)
    synced._cache.clear()
    path.write_text('{"disabledProviders":[]}')
    frames = []

    class Layer:
        async def group_send(self, group, frame):
            assert group == "updates"
            frames.append(frame["data"])

    # Keep unrelated provider orchestration idle while exercising the real settings merge.
    monkeypatch.setattr(settings_mutation, "get_channel_layer", lambda: Layer())
    try:
        defaults = synced.read_synced_settings()
        assert defaults["summaryModel"] == "auto"
        assert defaults["timelineSummaryEnabled"] is True
        result = async_to_sync(settings_mutation.update_synced_settings)({
            "summaryModel": "openrouter", "timelineSummaryEnabled": False,
        })
        assert result.status == "accepted"
        stored = synced.read_synced_settings()
        assert stored["summaryModel"] == "openrouter"
        assert stored["timelineSummaryEnabled"] is False
        assert frames[-1] == {
            "type": "synced_settings_updated",
            "settings": {"summaryModel": "openrouter", "timelineSummaryEnabled": False},
            "version": result.version,
        }
    finally:
        synced._cache.clear()


def test_timeline_final_only_uses_ordinary_merge_and_broadcast(monkeypatch, tmp_path):
    import twicc.synced_settings as synced

    path = tmp_path / "settings.json"
    monkeypatch.setattr(synced, "get_synced_settings_path", lambda: path)
    synced._cache.clear()
    path.write_text('{"disabledProviders":[]}')
    frames = []

    class Layer:
        async def group_send(self, group, frame):
            assert group == "updates"
            frames.append(frame["data"])

    monkeypatch.setattr(settings_mutation, "get_channel_layer", lambda: Layer())
    try:
        assert synced.read_synced_settings()["timelineSpeechFinalOnly"] is True
        result = async_to_sync(settings_mutation.update_synced_settings)({"timelineSpeechFinalOnly": False})
        assert result.status == "accepted"
        assert synced.read_synced_settings()["timelineSpeechFinalOnly"] is False
        assert frames[-1] == {
            "type": "synced_settings_updated",
            "settings": {"timelineSpeechFinalOnly": False},
            "version": result.version,
        }
    finally:
        synced._cache.clear()
