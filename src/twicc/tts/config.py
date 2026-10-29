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
    """The config of a missing or invalid file."""
    return {"active_id": None, "configs": []}


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
            "validated": _validated(entry.get("validated")),
        })
    active_id = raw.get("active_id")
    if not isinstance(active_id, str) or active_id not in seen_ids:
        active_id = None
    return {"active_id": active_id, "configs": configs}


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
