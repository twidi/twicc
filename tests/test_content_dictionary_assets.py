"""Bundled public dictionary identity and seed contracts."""

import hashlib
from pathlib import Path

import orjson
import pytest

from twicc.core import models as core

ASSETS = Path(__file__).resolve().parents[1] / "src/twicc/core/content_storage/dictionaries"


def test_bundled_dictionaries_match_manifest():
    assert (ASSETS / "manifest.json").is_file()
    entries = orjson.loads((ASSETS / "manifest.json").read_bytes())["dictionaries"]
    assert {e["provider"] for e in entries} == {"claude_code", "codex"}
    for entry in entries:
        payload = (ASSETS / entry["file"]).read_bytes()
        assert entry["version"] == 1
        assert entry["format"] == "raw"
        assert 0 < len(payload) <= 16384
        assert len(payload) == entry["byte_length"]
        assert hashlib.sha256(payload).hexdigest() == entry["sha256"]
        assert len(entry["policy_sha256"]) == 64
        assert entry["seed"] == 20261014


@pytest.mark.django_db
def test_fixed_assets_seed_dictionary_rows():
    dictionary = getattr(core, "ContentDictionary", None)
    assert dictionary is not None
    for entry in orjson.loads((ASSETS / "manifest.json").read_bytes())["dictionaries"]:
        row = dictionary.objects.get(provider=entry["provider"], version=1)
        assert bytes(row.payload) == (ASSETS / entry["file"]).read_bytes()
        assert hashlib.sha256(row.payload).digest() == row.sha256


def test_generator_finalizes_only_sanitized_release_assets(tmp_path):
    import sqlite3
    import subprocess
    import sys

    database = tmp_path / "source.sqlite"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE core_session (id TEXT, provider TEXT, project_id TEXT)")
        db.execute("CREATE TABLE core_sessionitem (id INTEGER PRIMARY KEY, session_id TEXT, content TEXT)")
        for provider in ("claude_code", "codex"):
            for index in range(20):
                sid = f"{provider}-{index}"
                db.execute("INSERT INTO core_session VALUES (?, ?, ?)", (sid, provider, sid))
                text = orjson.dumps({"type": "assistant" if provider == "claude_code" else "message",
                                     "role": "assistant", "cwd": "PRIVATE_PATH", "uuid": str(index),
                                     "content": [{"type": "text", "text": "PRIVATE_PROSE"}]}).decode()
                db.execute("INSERT INTO core_sessionitem(session_id, content) VALUES (?, ?)", (sid, text))
    release = tmp_path / "release"
    result = subprocess.run([sys.executable, "scripts/benchmark_public_schema_dictionaries.py", "--db", str(database),
                             "--output", str(tmp_path / "candidates"), "--finalize", str(release)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert {p.name for p in release.iterdir()} == {"manifest.json", "claude_code_v1.dict", "codex_v1.dict"}
    assert all(b"PRIVATE" not in p.read_bytes() for p in release.iterdir())
