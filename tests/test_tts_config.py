"""Speech configs stored in ``<data_dir>/tts.json`` (``twicc.tts.config``)."""

import asyncio

import orjson
import pytest
from asgiref.sync import async_to_sync
from django.test import AsyncClient

from tests.test_hidden_sessions_snapshot import _close, _communicator, _connect_frames
from twicc.paths import get_env_path, get_tts_config_path
from twicc.tts.config import (
    SPEECH_FIELDS,
    empty_tts_config,
    normalize_tts_config,
    positive_speed,
    read_tts_config,
    script_config_validated,
    script_fields,
    stored_script_config,
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
        "note": "",
        "validated": None,
        "last_test": None,
    }
    entry.update(overrides)
    return entry


def test_missing_or_invalid_file_reads_as_empty(tmp_data_dir):
    assert read_tts_config() == empty_tts_config()
    get_tts_config_path().write_bytes(b"not json")
    assert read_tts_config() == empty_tts_config()


def test_empty_config_shape():
    assert empty_tts_config() == {"active_id": None, "configs": [], "test_text": "", "adapt": None}


def test_normalize_keeps_a_valid_entry_unchanged():
    entry = _entry(kind="openrouter")
    config = {"active_id": "a", "configs": [entry], "test_text": "Bonjour", "adapt": "luna"}
    assert normalize_tts_config(config) == config


def test_normalize_keeps_only_a_string_test_text():
    assert normalize_tts_config({"configs": []})["test_text"] == ""
    assert normalize_tts_config({"configs": [], "test_text": 3})["test_text"] == ""
    assert normalize_tts_config({"configs": [], "test_text": "Salut"})["test_text"] == "Salut"


@pytest.mark.parametrize("adapt", [None, "haiku", "luna", "off"])
def test_normalize_keeps_a_known_adapt(adapt):
    assert normalize_tts_config({"configs": [], "adapt": adapt})["adapt"] == adapt


@pytest.mark.parametrize("adapt", ["on", "", "Haiku", True, 1, ["haiku"]])
def test_normalize_drops_an_unknown_adapt(adapt):
    assert normalize_tts_config({"configs": [], "adapt": adapt})["adapt"] is None


def test_adapt_accepts_openrouter():
    assert normalize_tts_config({"configs": [], "adapt": "openrouter"})["adapt"] == "openrouter"


def test_adapt_drops_the_name_before_the_rename():
    # "qwen" was the stored value before the choice became "openrouter": unknown, so no choice.
    assert normalize_tts_config({"configs": [], "adapt": "qwen"})["adapt"] is None


def test_normalize_without_adapt_is_none():
    assert normalize_tts_config({"configs": []})["adapt"] is None


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
        "id": "a", "name": "", "kind": "openrouter", "model": "", "voice": "", "response_format": "mp3",
        "speed": None, "instructions": "", "note": "", "validated": None, "last_test": None,
    }], "test_text": "", "adapt": None}


def test_normalize_keeps_a_note_and_never_snapshots_it():
    out = normalize_tts_config({"configs": [
        _entry(note="Warm, a bit slow", validated={
            "model": "m", "voice": "v", "response_format": "mp3", "speed": None, "instructions": "", "note": "x",
        }),
        _entry(id="b", note=42),
    ]})
    assert [c["note"] for c in out["configs"]] == ["Warm, a bit slow", ""]
    assert "note" not in out["configs"][0]["validated"]


@pytest.mark.parametrize("raw", [None, "junk", [], 3, {"configs": "junk"}])
def test_normalize_non_dict_or_bad_configs_is_empty(raw):
    assert normalize_tts_config(raw) == empty_tts_config()


@pytest.mark.parametrize("speed", [0, -1, float("inf"), float("nan"), "1.0", True, pytest.param(10**400, id="huge-int")])
def test_normalize_rejects_a_bad_speed(speed):
    out = normalize_tts_config({"configs": [{"id": "a", "speed": speed}]})
    assert out["configs"][0]["speed"] is None


@pytest.mark.parametrize(
    "speed", [None, 0, -1, float("inf"), float("nan"), "1.0", True, pytest.param(10**400, id="huge-int")]
)
def test_positive_speed_rejects(speed):
    assert positive_speed(speed) is None


@pytest.mark.parametrize(("speed", "expected"), [(2, 2.0), (0.5, 0.5)])
def test_positive_speed_returns_a_float(speed, expected):
    result = positive_speed(speed)
    assert result == expected
    assert isinstance(result, float)


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
    config = {"active_id": "a", "configs": [_entry(kind="openrouter", validated={
        "model": "google/gemini-3.8-flash-lite-tts", "voice": "Kore", "response_format": "pcm",
        "speed": None, "instructions": "",
    })], "test_text": "A sentence of my own.", "adapt": "off"}
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


@pytest.mark.parametrize("subscribe", [None, "tts_config_updated"])
def test_ws_connect_sends_the_tts_payload(transactional_db, tmp_data_dir, monkeypatch, subscribe):
    monkeypatch.setattr("twicc.tts.config.get_env_secret", lambda key: "sk-or-secret")
    config = write_tts_config({"active_id": "a", "configs": [_entry()]})

    async def scenario():
        comm = _communicator(subscribe)
        connected, _ = await comm.connect()
        assert connected
        try:
            return await _connect_frames(comm)
        finally:
            await _close(comm)

    frames = asyncio.run(scenario())
    assert frames is not None
    tts_frames = [frame for frame in frames if frame.get("type") == "tts_config_updated"]
    assert tts_frames == [{
        "type": "tts_config_updated",
        "config": config,
        "key_present": True,
        "env_path": str(get_env_path()),
    }]
    assert "sk-or-secret" not in orjson.dumps(frames).decode()


def test_ws_connect_skips_the_tts_payload_outside_the_subscribe_filter(transactional_db, tmp_data_dir):
    async def scenario():
        comm = _communicator("active_processes")
        connected, _ = await comm.connect()
        assert connected
        try:
            return await _connect_frames(comm)
        finally:
            await _close(comm)

    frames = asyncio.run(scenario())
    assert frames is not None
    assert all(frame.get("type") != "tts_config_updated" for frame in frames)


LAST_TEST = {
    "at": "2026-10-30T10:42:00+00:00", "generation_id": "gen-1", "chars": 2000,
    "generation_ms": 2200, "first_audio_ms": 1160, "audio_seconds": 143.0, "cost": None, "parts": 3,
    "adapt_engine": "haiku", "adapted_by": "haiku", "adapt_reason": None, "adapt_first_ms": 640,
    "adapted_text": "Hello, said aloud.", "fallback_reason": None,
}
ADAPT_FIELDS = ("adapt_engine", "adapted_by", "adapt_reason", "adapt_first_ms", "adapted_text", "fallback_reason")


def test_last_test_is_normalized_or_dropped():
    out = normalize_tts_config({"configs": [
        _entry(last_test={**LAST_TEST, "chars": 2000.0, "cost": "x"}),
        _entry(id="b", last_test={"at": "now"}),
        _entry(id="c", last_test="junk"),
    ]})
    assert out["configs"][0]["last_test"] == {**LAST_TEST, "chars": 2000}
    assert [c["last_test"] for c in out["configs"][1:]] == [None, None]


def test_last_test_keeps_first_audio_ms():
    def first_audio_ms(last_test):
        return normalize_tts_config({"configs": [_entry(last_test=last_test)]})["configs"][0]["last_test"]

    assert first_audio_ms(LAST_TEST)["first_audio_ms"] == 1160
    assert first_audio_ms({**LAST_TEST, "first_audio_ms": -1})["first_audio_ms"] is None
    assert first_audio_ms({**LAST_TEST, "first_audio_ms": "x"})["first_audio_ms"] is None
    # A record written before the field existed stays valid.
    old_record = {key: value for key, value in LAST_TEST.items() if key != "first_audio_ms"}
    assert first_audio_ms(old_record) == {**LAST_TEST, "first_audio_ms": None}


def test_last_test_keeps_parts():
    def last_test(record):
        return normalize_tts_config({"configs": [_entry(last_test=record)]})["configs"][0]["last_test"]

    assert last_test(LAST_TEST)["parts"] == 3
    assert last_test({**LAST_TEST, "parts": -1})["parts"] is None
    assert last_test({**LAST_TEST, "parts": "x"})["parts"] is None
    # A record written before split texts: still valid, without a part count.
    old_record = {key: value for key, value in LAST_TEST.items() if key != "parts"}
    assert last_test(old_record) == {**LAST_TEST, "parts": None}


def _normalized_last_test(record):
    return normalize_tts_config({"configs": [_entry(last_test=record)]})["configs"][0]["last_test"]


def test_last_test_keeps_the_adaptation_fields():
    assert _normalized_last_test(LAST_TEST) == LAST_TEST
    not_adapted = {**LAST_TEST, "adapted_by": None, "adapt_reason": "stalled", "adapt_first_ms": None,
                   "adapted_text": None}
    assert _normalized_last_test(not_adapted) == not_adapted


@pytest.mark.parametrize("reason", ["off", "no_provider", "too_long", "failed", "stalled", "empty", "refused"])
def test_last_test_keeps_every_adapt_reason(reason):
    assert _normalized_last_test({**LAST_TEST, "adapt_reason": reason})["adapt_reason"] == reason


def test_last_test_keeps_the_fallback_reason():
    assert _normalized_last_test({**LAST_TEST, "fallback_reason": "stalled"})["fallback_reason"] == "stalled"
    assert _normalized_last_test({**LAST_TEST, "fallback_reason": "refused"})["fallback_reason"] == "refused"
    assert _normalized_last_test({**LAST_TEST, "fallback_reason": "bogus"})["fallback_reason"] is None
    # A record written before the OpenRouter adaptation.
    old_record = {key: value for key, value in LAST_TEST.items() if key != "fallback_reason"}
    assert _normalized_last_test(old_record)["fallback_reason"] is None


def test_last_test_keeps_the_engine_of_the_last_attempt():
    for engine in ("haiku", "luna", "openrouter"):
        assert _normalized_last_test({**LAST_TEST, "adapt_engine": engine})["adapt_engine"] == engine
    assert _normalized_last_test({**LAST_TEST, "adapt_engine": "opus"})["adapt_engine"] is None
    assert _normalized_last_test({**LAST_TEST, "adapt_engine": None})["adapt_engine"] is None
    # A record written before the field existed.
    old_record = {key: value for key, value in LAST_TEST.items() if key != "adapt_engine"}
    assert _normalized_last_test(old_record) == {**LAST_TEST, "adapt_engine": None}


def test_last_test_keeps_an_openrouter_adaptation():
    assert _normalized_last_test({**LAST_TEST, "adapted_by": "openrouter"})["adapted_by"] == "openrouter"


@pytest.mark.parametrize("field, value", [
    ("adapted_by", "off"), ("adapted_by", "claude"), ("adapted_by", True),
    ("adapt_reason", "other"), ("adapt_reason", ""), ("adapt_reason", 3),
    ("adapt_first_ms", -1), ("adapt_first_ms", "x"), ("adapt_first_ms", True), ("adapt_first_ms", float("inf")),
    ("adapted_text", 3), ("adapted_text", ["a"]),
])
def test_last_test_drops_a_bad_adaptation_field(field, value):
    out = _normalized_last_test({**LAST_TEST, field: value})
    assert out == {**LAST_TEST, field: None}


def test_last_test_keeps_an_int_adapt_first_ms():
    out = _normalized_last_test({**LAST_TEST, "adapt_first_ms": 640.0})
    assert out["adapt_first_ms"] == 640
    assert isinstance(out["adapt_first_ms"], int)


def test_last_test_keeps_an_empty_adapted_text():
    assert _normalized_last_test({**LAST_TEST, "adapted_text": ""})["adapted_text"] == ""


def test_last_test_without_adaptation_fields_stays_valid():
    # A record written before the adaptation existed.
    old_record = {key: value for key, value in LAST_TEST.items() if key not in ADAPT_FIELDS}
    assert _normalized_last_test(old_record) == {**old_record, **dict.fromkeys(ADAPT_FIELDS)}


def test_a_client_write_keeps_the_adapt_setting(tmp_data_dir):
    from twicc.tts.config import write_client_tts_config
    write_tts_config({"configs": [_entry()]})
    assert write_client_tts_config({"configs": [_entry()], "adapt": "luna"})["adapt"] == "luna"
    assert read_tts_config()["adapt"] == "luna"
    assert write_client_tts_config({"configs": [_entry()], "adapt": "junk"})["adapt"] is None
    assert read_tts_config()["adapt"] is None


def test_a_client_write_keeps_the_recorded_last_test(tmp_data_dir):
    from twicc.tts.config import write_client_tts_config
    write_tts_config({"configs": [_entry(last_test=LAST_TEST)]})
    # The client renames the config and sends a forged (or missing) last test.
    out = write_client_tts_config({"configs": [_entry(name="Renamed", last_test={**LAST_TEST, "cost": 9.0}), _entry(id="new")]})
    assert out["configs"][0]["name"] == "Renamed"
    assert out["configs"][0]["last_test"] == LAST_TEST
    assert out["configs"][1]["last_test"] is None


def test_record_last_test_and_its_cost(tmp_data_dir):
    from twicc.tts.config import record_last_test
    write_tts_config({"configs": [_entry()]})
    assert record_last_test("missing", LAST_TEST) is None
    assert record_last_test("a", LAST_TEST)["configs"][0]["last_test"] == LAST_TEST
    # The cost of an older run does not land on a newer one.
    assert record_last_test("a", {"generation_id": "gen-0", "cost": 0.5}) is None
    out = record_last_test("a", {"generation_id": "gen-1", "cost": 0.008})
    assert out["configs"][0]["last_test"] == {**LAST_TEST, "cost": 0.008}
    assert read_tts_config()["configs"][0]["last_test"]["cost"] == 0.008


# --- script configs -------------------------------------------------------------


def test_an_entry_without_kind_is_openrouter():
    assert normalize_tts_config({"configs": [_entry()]})["configs"][0]["kind"] == "openrouter"


def test_an_unknown_kind_is_openrouter():
    assert normalize_tts_config({"configs": [_entry(kind="other")]})["configs"][0]["kind"] == "openrouter"


def test_a_script_entry_keeps_only_its_fields():
    raw = {"id": "s", "kind": "script", "name": "Piper", "command": "piper -q", "output": "path",
           "audio_format": "pcm", "pcm_rate": 22050, "pcm_channels": 1, "max_chars": 800,
           "model": "x", "voice": "y", "note": "n", "validated": None}
    entry = normalize_tts_config({"configs": [raw]})["configs"][0]
    assert entry == {"id": "s", "name": "Piper", "kind": "script", "command": "piper -q", "output": "path",
                     "audio_format": "pcm", "pcm_rate": 22050, "pcm_channels": 1, "max_chars": 800,
                     "delete_file": True, "note": "n", "validated": None, "last_test": None}


def test_a_bare_script_entry_gets_the_defaults():
    entry = normalize_tts_config({"configs": [{"id": "s", "kind": "script"}]})["configs"][0]
    assert entry == {"id": "s", "name": "", "kind": "script", "command": "", "output": "stream",
                     "audio_format": "mp3", "pcm_rate": None, "pcm_channels": 1, "max_chars": None,
                     "delete_file": True, "note": "", "validated": None, "last_test": None}


@pytest.mark.parametrize("field, value, expected", [
    ("output", "other", "stream"), ("audio_format", "ogg", "mp3"), ("pcm_rate", 0, None),
    ("pcm_rate", 384001, None), ("pcm_rate", True, None), ("pcm_rate", 22050.0, None), ("pcm_channels", 9, 1),
    ("pcm_channels", 0, 1), ("pcm_channels", True, 1), ("max_chars", 0, None), ("max_chars", 1.5, None),
    ("max_chars", True, None), ("command", 3, ""),
])
def test_script_fields_are_normalized(field, value, expected):
    result = script_fields({field: value})[field]
    assert result == expected and type(result) is type(expected)


@pytest.mark.parametrize("field, value", [
    ("output", "whole"), ("audio_format", "wav"), ("pcm_rate", 384000), ("pcm_channels", 8), ("max_chars", 1),
])
def test_script_fields_keep_a_valid_value(field, value):
    assert script_fields({field: value})[field] == value


def test_a_script_validation_snapshot_holds_the_script_fields():
    fields = script_fields({"command": "say", "audio_format": "wav"})
    raw = {"id": "s", "kind": "script", **fields, "validated": {**fields, "kind": "script"}}
    entry = normalize_tts_config({"configs": [raw]})["configs"][0]
    assert entry["validated"] == fields
    assert script_config_validated(entry)
    assert not script_config_validated({**entry, "command": "other"})


def test_an_incomplete_script_snapshot_is_dropped():
    fields = script_fields({"command": "say"})
    missing = {key: value for key, value in fields.items() if key != "max_chars"}
    raw = {"id": "s", "kind": "script", **fields, "validated": missing}
    entry = normalize_tts_config({"configs": [raw]})["configs"][0]
    assert entry["validated"] is None
    assert not script_config_validated(entry)


@pytest.mark.parametrize("value, expected", [(None, True), (True, True), (False, False), ("no", True), (0, True)])
def test_delete_file_is_a_bool_on_by_default(value, expected):
    raw = {"id": "s", "kind": "script"} if value is None else {"id": "s", "kind": "script", "delete_file": value}
    assert normalize_tts_config({"configs": [raw]})["configs"][0]["delete_file"] is expected


def test_delete_file_is_not_a_speech_field():
    fields = script_fields({"command": "say", "output": "path"})
    raw = {"id": "s", "kind": "script", **fields, "delete_file": False, "validated": {**fields, "delete_file": True}}
    entry = normalize_tts_config({"configs": [raw]})["configs"][0]
    assert entry["validated"] == fields  # the snapshot never holds it
    assert "delete_file" not in script_fields(raw)
    assert script_config_validated(entry)
    assert script_config_validated({**entry, "delete_file": True})


def test_an_openrouter_entry_has_no_delete_file():
    entry = normalize_tts_config({"configs": [_entry(delete_file=False)]})["configs"][0]
    assert "delete_file" not in entry


def test_an_openrouter_snapshot_without_kind_stays_valid():
    # A snapshot written before this change: no kind, the five fields.
    speech = {key: _entry()[key] for key in SPEECH_FIELDS}
    entry = normalize_tts_config({"configs": [_entry(validated=speech)]})["configs"][0]
    assert entry["validated"] == speech


def test_stored_script_config(tmp_data_dir):
    write_tts_config({"configs": [_entry(id="o"), {"id": "s", "kind": "script", "command": "say"}]})
    assert stored_script_config("s")["command"] == "say"
    assert stored_script_config("o") is None
    assert stored_script_config("missing") is None
