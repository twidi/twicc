"""Read/write the text-to-speech configs.

File: <data_dir>/tts.json — whole-file atomic overwrite, like message-snippets.json.
The OpenRouter key is never stored here: it lives in <data_dir>/.env, and only
its presence reaches the browser (:func:`tts_client_payload`).
"""

import math

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import OPENROUTER_API_KEY_ENV, get_env_path, get_env_secret, get_tts_config_path

TTS_RESPONSE_FORMATS = ("mp3", "pcm")
SPEECH_FIELDS = ("model", "voice", "response_format", "speed", "instructions")


def empty_tts_config() -> dict:
    """The config of a missing or invalid file. An empty ``test_text`` means the default sentence."""
    return {"active_id": None, "configs": [], "test_text": ""}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def positive_speed(value: object) -> float | None:
    """A finite positive number as a float, else None (``bool`` is not a number here)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        # The WebSocket JSON decoder (stdlib ``json``) keeps big ints: 10**400 does not fit a float.
        speed = float(value)
    except OverflowError:
        return None
    if not math.isfinite(speed) or speed <= 0:
        return None
    return speed


def _speech_fields(raw: dict) -> dict:
    """The five speech fields of *raw*, each normalized."""
    response_format = raw.get("response_format")
    return {
        "model": _string(raw.get("model")),
        "voice": _string(raw.get("voice")),
        "response_format": response_format if response_format in TTS_RESPONSE_FORMATS else "mp3",
        "speed": positive_speed(raw.get("speed")),
        "instructions": _string(raw.get("instructions")),
    }


def _validated(value: object) -> dict | None:
    """The speech-field snapshot of the last confirmation, or None when absent or incomplete."""
    if not isinstance(value, dict) or any(field not in value for field in SPEECH_FIELDS):
        return None
    return _speech_fields(value)


def _non_negative(value: object, kind: type) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = kind(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _last_test(value: object) -> dict | None:
    """The last test run of a config, written by the backend only; None when absent or unusable.

    ``cost`` (USD) comes some seconds after the run, through the generation lookup.
    """
    if not isinstance(value, dict) or not isinstance(value.get("at"), str):
        return None
    chars = _non_negative(value.get("chars"), int)
    generation_ms = _non_negative(value.get("generation_ms"), int)
    if chars is None or generation_ms is None:
        return None
    return {
        "at": value["at"],
        "generation_id": _string(value.get("generation_id")),
        "chars": chars,
        "generation_ms": generation_ms,
        "audio_seconds": _non_negative(value.get("audio_seconds"), float),
        "cost": _non_negative(value.get("cost"), float),
    }


def normalize_tts_config(raw: object) -> dict:
    """Repair *raw* into the stored shape.

    Drops the entries that are not dicts, have no non-empty string ``id``, or
    repeat an earlier ``id``. An ``active_id`` that matches no entry becomes None.
    The backend does not require the active config to be validated: the
    frontend availability rule checks it.
    """
    if not isinstance(raw, dict):
        return empty_tts_config()
    raw_configs = raw.get("configs")
    configs = []
    seen_ids = set()
    for entry in raw_configs if isinstance(raw_configs, list) else []:
        if not isinstance(entry, dict):
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id or entry_id in seen_ids:
            continue
        seen_ids.add(entry_id)
        configs.append({
            "id": entry_id,
            "name": _string(entry.get("name")),
            **_speech_fields(entry),
            "note": _string(entry.get("note")),
            "validated": _validated(entry.get("validated")),
            "last_test": _last_test(entry.get("last_test")),
        })
    active_id = raw.get("active_id")
    if not isinstance(active_id, str) or active_id not in seen_ids:
        active_id = None
    return {"active_id": active_id, "configs": configs, "test_text": _string(raw.get("test_text"))}


def read_tts_config() -> dict:
    """Read tts.json, normalized. Returns the empty config if the file doesn't exist or is invalid."""
    try:
        raw = orjson.loads(get_tts_config_path().read_bytes())
    except (FileNotFoundError, orjson.JSONDecodeError):
        return empty_tts_config()
    return normalize_tts_config(raw)


def write_tts_config(config: object) -> dict:
    """Normalize *config*, write it to tts.json atomically, and return the normalized config."""
    normalized = normalize_tts_config(config)
    atomic_write_json(get_tts_config_path(), normalized)
    return normalized


def openrouter_api_key() -> str | None:
    """The OpenRouter key from <data_dir>/.env; None when absent or empty."""
    return get_env_secret(OPENROUTER_API_KEY_ENV)


def tts_client_payload(config: dict) -> dict:
    """What the browser receives: the configs, whether the key is set, and where to set it."""
    return {
        "config": config,
        "key_present": openrouter_api_key() is not None,
        "env_path": str(get_env_path()),
    }


def keep_last_tests(config: dict, current: dict) -> dict:
    """*config* (a client's full write) with the ``last_test`` of *current* (the file):
    the backend alone writes it, so a client never erases or forges one."""
    tests = {entry["id"]: entry["last_test"] for entry in current["configs"]}
    return {
        **config,
        "configs": [{**entry, "last_test": tests.get(entry["id"])} for entry in config["configs"]],
    }


def write_client_tts_config(config: object) -> dict:
    """Write a client's full tts.json, keeping the backend-written ``last_test`` values."""
    return write_tts_config(keep_last_tests(normalize_tts_config(config), read_tts_config()))


def record_last_test(config_id: str, update: dict) -> dict | None:
    """Merge *update* into the ``last_test`` of *config_id* in tts.json; return the new config.

    A new run carries ``at`` and replaces the record. A later cost-only update applies only
    when its ``generation_id`` is still the recorded one (a newer test may have replaced it).
    None when nothing was written.
    """
    config = read_tts_config()
    entry = next((item for item in config["configs"] if item["id"] == config_id), None)
    if entry is None:
        return None
    if "at" in update:
        entry["last_test"] = dict(update)
    else:
        if (entry["last_test"] or {}).get("generation_id") != update.get("generation_id"):
            return None
        entry["last_test"] = {**entry["last_test"], **update}
    return write_tts_config(config)
