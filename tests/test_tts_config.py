"""Speech configs stored in ``<data_dir>/tts.json`` (``twicc.tts.config``)."""

import orjson
import pytest
from asgiref.sync import async_to_sync
from django.test import AsyncClient

from twicc.paths import get_env_path, get_tts_config_path
from twicc.tts.config import (
    empty_tts_config,
    normalize_tts_config,
    read_tts_config,
    tts_client_payload,
    write_tts_config,
)


@pytest.fixture
def tmp_data_dir():
    """The isolated test data dir, without a ``tts.json`` before and after the test."""
    path = get_tts_config_path()
    path.unlink(missing_ok=True)
    yield path.parent
    path.unlink(missing_ok=True)


def _entry(**overrides) -> dict:
    entry = {
        "id": "a",
        "name": "Gemini",
        "model": "google/gemini-3.8-flash-lite-tts",
        "voice": "Kore",
        "response_format": "pcm",
        "speed": None,
        "instructions": "",
        "validated": None,
    }
    entry.update(overrides)
    return entry


def test_missing_or_invalid_file_reads_as_empty(tmp_data_dir):
    assert read_tts_config() == empty_tts_config()
    get_tts_config_path().write_bytes(b"not json")
    assert read_tts_config() == empty_tts_config()


def test_empty_config_shape():
    assert empty_tts_config() == {"active_id": None, "configs": []}


def test_normalize_keeps_a_valid_entry_unchanged():
    entry = _entry()
    assert normalize_tts_config({"active_id": "a", "configs": [entry]}) == {"active_id": "a", "configs": [entry]}


def test_normalize_repairs_fields():
    out = normalize_tts_config({"active_id": "zz", "configs": [
        {"id": "a", "response_format": "ogg", "speed": -1, "name": 3},
        {"id": "a"}, {"name": "no id"}, "junk",
    ]})
    assert out["active_id"] is None
    assert [c["id"] for c in out["configs"]] == ["a"]
    assert out["configs"][0]["response_format"] == "mp3"
    assert out["configs"][0]["speed"] is None
    assert out["configs"][0]["name"] == ""


def test_normalize_fills_defaults_for_a_bare_entry():
    out = normalize_tts_config({"configs": [{"id": "a"}]})
    assert out == {"active_id": None, "configs": [{
        "id": "a", "name": "", "model": "", "voice": "", "response_format": "mp3",
        "speed": None, "instructions": "", "validated": None,
    }]}


@pytest.mark.parametrize("raw", [None, "junk", [], 3, {"configs": "junk"}])
def test_normalize_non_dict_or_bad_configs_is_empty(raw):
    assert normalize_tts_config(raw) == empty_tts_config()


@pytest.mark.parametrize("speed", [0, -1, float("inf"), float("nan"), "1.0", True, pytest.param(10**400, id="huge-int")])
def test_normalize_rejects_a_bad_speed(speed):
    out = normalize_tts_config({"configs": [{"id": "a", "speed": speed}]})
    assert out["configs"][0]["speed"] is None


def test_normalize_keeps_a_positive_speed_as_float():
    out = normalize_tts_config({"configs": [{"id": "a", "speed": 2}]})
    assert out["configs"][0]["speed"] == 2.0
    assert isinstance(out["configs"][0]["speed"], float)


@pytest.mark.parametrize("active_id", [["a"], {"a": 1}, 3, ""])
def test_normalize_drops_a_non_matching_active_id(active_id):
    assert normalize_tts_config({"active_id": active_id, "configs": [{"id": "a"}]})["active_id"] is None


def test_normalize_drops_an_empty_or_non_string_id():
    out = normalize_tts_config({"configs": [{"id": ""}, {"id": 3}, {"id": "b"}]})
    assert [c["id"] for c in out["configs"]] == ["b"]


def test_validated_snapshot_is_normalized_or_dropped():
    snapshot = {"model": "m", "voice": "v", "response_format": "ogg", "speed": -2, "instructions": 5}
    out = normalize_tts_config({"configs": [_entry(validated=snapshot)]})
    assert out["configs"][0]["validated"] == {
        "model": "m", "voice": "v", "response_format": "mp3", "speed": None, "instructions": "",
    }

    good = {"model": "m", "voice": "v", "response_format": "pcm", "speed": 1.5, "instructions": "calm"}
    out = normalize_tts_config({"configs": [_entry(validated={**good, "name": "extra"})]})
    assert out["configs"][0]["validated"] == good

    missing = {k: v for k, v in good.items() if k != "speed"}
    out = normalize_tts_config({"configs": [_entry(validated=missing), _entry(id="b", validated="junk")]})
    assert [c["validated"] for c in out["configs"]] == [None, None]


def test_write_then_read_round_trips(tmp_data_dir):
    config = {"active_id": "a", "configs": [_entry(validated={
        "model": "google/gemini-3.8-flash-lite-tts", "voice": "Kore", "response_format": "pcm",
        "speed": None, "instructions": "",
    })]}
    assert write_tts_config(config) == config
    assert read_tts_config() == config
    assert orjson.loads(get_tts_config_path().read_bytes()) == config


def test_write_normalizes(tmp_data_dir):
    written = write_tts_config({"active_id": "zz", "configs": [{"id": "a"}, "junk"]})
    assert written["active_id"] is None
    assert [c["id"] for c in written["configs"]] == ["a"]
    assert read_tts_config() == written


def test_client_payload_reports_key_presence_without_the_key(monkeypatch):
    monkeypatch.setattr("twicc.tts.config.get_env_secret", lambda key: "sk-or-secret")
    payload = tts_client_payload(empty_tts_config())
    assert payload["key_present"] is True
    assert payload["config"] == empty_tts_config()
    assert "sk-or-secret" not in orjson.dumps(payload).decode()
    assert payload["env_path"].endswith(".env")


def test_client_payload_reports_a_missing_key(monkeypatch):
    monkeypatch.setattr("twicc.tts.config.get_env_secret", lambda key: None)
    assert tts_client_payload(empty_tts_config())["key_present"] is False


# --- wiring: WebSocket update handler and bootstrap ---------------------------


@pytest.fixture
def ws_consumer():
    """A consumer whose channel layer records ``group_send`` calls."""
    from twicc.asgi import WSConsumer

    sent: list[tuple[str, dict]] = []

    class Layer:
        async def group_send(self, group, message):
            sent.append((group, message))

    consumer = WSConsumer()
    consumer.channel_layer = Layer()
    return consumer, sent


def test_ws_update_writes_normalized_and_broadcasts(tmp_data_dir, ws_consumer, monkeypatch):
    monkeypatch.setattr("twicc.tts.config.get_env_secret", lambda key: "sk-or-secret")
    consumer, sent = ws_consumer
    async_to_sync(consumer._handle_update_tts_config)({
        "type": "update_tts_config",
        "config": {"active_id": "zz", "configs": [{"id": "a", "speed": -1}, "junk"]},
    })
    normalized = normalize_tts_config({"configs": [{"id": "a"}]})
    assert read_tts_config() == normalized
    assert sent == [("updates", {"type": "broadcast", "data": {
        "type": "tts_config_updated",
        "config": normalized,
        "key_present": True,
        "env_path": str(get_env_path()),
    }})]
    assert "sk-or-secret" not in orjson.dumps(sent).decode()


@pytest.mark.parametrize("config", [None, "junk", ["a"]])
def test_ws_update_rejects_a_non_dict_config(tmp_data_dir, ws_consumer, config):
    consumer, sent = ws_consumer
    async_to_sync(consumer._handle_update_tts_config)({"type": "update_tts_config", "config": config})
    assert not get_tts_config_path().exists()
    assert sent == []


@pytest.mark.django_db
def test_bootstrap_carries_the_tts_payload(tmp_data_dir, settings, monkeypatch):
    monkeypatch.setattr("twicc.tts.config.get_env_secret", lambda key: None)
    settings.TWICC_PASSWORD_HASH = ""
    config = write_tts_config({"active_id": "a", "configs": [_entry()]})
    response = async_to_sync(AsyncClient().get)("/api/bootstrap/")
    assert response.status_code == 200
    assert orjson.loads(response.content)["tts"] == {
        "config": config,
        "key_present": False,
        "env_path": str(get_env_path()),
    }
