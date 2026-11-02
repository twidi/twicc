"""Read/write the text-to-speech configs.

File: <data_dir>/tts.json — whole-file atomic overwrite, like message-snippets.json.
A config has a ``kind``: ``openrouter`` (the default) or ``script`` (a user shell command
gives the audio). Each kind has its own fields.
The top-level ``adapt`` is the global text adaptation choice (see :data:`ADAPT_CHOICES`).
The OpenRouter key is never stored here: it lives in <data_dir>/.env, and only
its presence reaches the browser (:func:`tts_client_payload`).
"""

import math

import orjson

from twicc.atomic_json import atomic_write_json
from twicc.paths import OPENROUTER_API_KEY_ENV, get_env_path, get_env_secret, get_tts_config_path
from twicc.tts.openrouter import MAX_PCM_CHANNELS, MAX_PCM_RATE

CONFIG_KINDS = ("openrouter", "script")
TTS_RESPONSE_FORMATS = ("mp3", "pcm")
SPEECH_FIELDS = ("model", "voice", "response_format", "speed", "instructions")
SCRIPT_FIELDS = ("command", "output", "audio_format", "pcm_rate", "pcm_channels", "max_chars")
SCRIPT_OUTPUTS = ("stream", "whole", "path")
SCRIPT_FORMATS = ("mp3", "wav", "pcm")
# The small models that adapt a text for speech (``openrouter`` needs no provider, only the key).
ADAPT_ENGINES = ("haiku", "luna", "openrouter")
# The stored ``adapt`` values; None (no stored choice) means on, with the first running provider.
ADAPT_CHOICES = (*ADAPT_ENGINES, "off")
# Why a reading was not adapted, or why OpenRouter failed before an agent took over (in the log
# line and the last test). ``refused``: OpenRouter refused the call (no key, a wrong key, no
# credits).
ADAPT_REASONS = ("off", "no_provider", "too_long", "failed", "stalled", "empty", "refused")


def empty_tts_config() -> dict:
    """The config of a missing or invalid file. An empty ``test_text`` means the default sentence."""
    return {"active_id": None, "configs": [], "test_text": "", "adapt": None}


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _one_of(value: object, choices: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in choices else None


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


def _int_in(value: object, low: int, high: int | None = None) -> int | None:
    """*value* when it is an int (not a ``bool``) with ``low <= value <= high``, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < low or (high is not None and value > high):
        return None
    return value


def script_fields(raw: dict) -> dict:
    """The six fields of a script config in *raw*, each normalized."""
    output = raw.get("output")
    audio_format = raw.get("audio_format")
    pcm_channels = _int_in(raw.get("pcm_channels"), 1, MAX_PCM_CHANNELS)
    return {
        "command": _string(raw.get("command")),
        "output": output if output in SCRIPT_OUTPUTS else "stream",
        "audio_format": audio_format if audio_format in SCRIPT_FORMATS else "mp3",
        "pcm_rate": _int_in(raw.get("pcm_rate"), 1, MAX_PCM_RATE),
        "pcm_channels": 1 if pcm_channels is None else pcm_channels,
        "max_chars": _int_in(raw.get("max_chars"), 1),
    }


_KIND_FIELDS = {
    "openrouter": (SPEECH_FIELDS, _speech_fields),
    "script": (SCRIPT_FIELDS, script_fields),
}


def _kind(value: object) -> str:
    return value if value in CONFIG_KINDS else "openrouter"


def _validated(value: object, kind: str) -> dict | None:
    """The snapshot of the fields of *kind* at the last confirmation, or None when absent or
    incomplete. Other keys of the snapshot (a ``kind``, a ``note``) are dropped."""
    fields, normalize = _KIND_FIELDS[kind]
    if not isinstance(value, dict) or any(field not in value for field in fields):
        return None
    return normalize(value)


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
    ``parts`` is the number of requests of the text; None in a record written before split texts.
    The adaptation fields are None in a record written before the text adaptation: ``adapt_engine``
    (the engine of the last attempt, None when no engine ran), ``adapted_by`` (the engine, None when
    not adapted), ``adapt_reason`` (why not), ``adapt_first_ms`` (the wait for the first adapted
    text), ``adapted_text`` and ``fallback_reason`` (why OpenRouter failed before an agent took
    over, else None).
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
        "first_audio_ms": _non_negative(value.get("first_audio_ms"), int),
        "audio_seconds": _non_negative(value.get("audio_seconds"), float),
        "cost": _non_negative(value.get("cost"), float),
        "parts": _non_negative(value.get("parts"), int),
        "adapt_engine": _one_of(value.get("adapt_engine"), ADAPT_ENGINES),
        "adapted_by": _one_of(value.get("adapted_by"), ADAPT_ENGINES),
        "adapt_reason": _one_of(value.get("adapt_reason"), ADAPT_REASONS),
        "adapt_first_ms": _non_negative(value.get("adapt_first_ms"), int),
        "adapted_text": value["adapted_text"] if isinstance(value.get("adapted_text"), str) else None,
        "fallback_reason": _one_of(value.get("fallback_reason"), ADAPT_REASONS),
    }


def normalize_tts_config(raw: object) -> dict:
    """Repair *raw* into the stored shape.

    Drops the entries that are not dicts, have no non-empty string ``id``, or
    repeat an earlier ``id``. An ``active_id`` that matches no entry becomes None, and so
    does an unknown ``adapt``.
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
        kind = _kind(entry.get("kind"))
        _fields, normalize_fields = _KIND_FIELDS[kind]
        # Not a speech field: no part of the audio, nor of the validation snapshot.
        delete_file = entry.get("delete_file")
        options = {"delete_file": delete_file if isinstance(delete_file, bool) else True} if kind == "script" else {}
        configs.append({
            "id": entry_id,
            "name": _string(entry.get("name")),
            "kind": kind,
            **normalize_fields(entry),
            **options,
            "note": _string(entry.get("note")),
            "validated": _validated(entry.get("validated"), kind),
            "last_test": _last_test(entry.get("last_test")),
        })
    active_id = raw.get("active_id")
    if not isinstance(active_id, str) or active_id not in seen_ids:
        active_id = None
    return {
        "active_id": active_id,
        "configs": configs,
        "test_text": _string(raw.get("test_text")),
        "adapt": _one_of(raw.get("adapt"), ADAPT_CHOICES),
    }


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


def stored_script_config(config_id: str) -> dict | None:
    """The normalized entry of *config_id* in tts.json when it is a script config, else None.

    The command that runs is always this stored one, never one from a request.
    """
    entry = next((item for item in read_tts_config()["configs"] if item["id"] == config_id), None)
    return entry if entry is not None and entry["kind"] == "script" else None


def script_config_validated(entry: dict) -> bool:
    """Whether the ``validated`` snapshot of the script config *entry* matches its fields."""
    return entry.get("validated") == script_fields(entry)


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
