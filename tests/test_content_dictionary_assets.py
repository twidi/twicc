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


def test_generator_samples_decoded_effective_current_content(tmp_path):
    import importlib.util
    import sqlite3
    import zstandard

    spec = importlib.util.spec_from_file_location('public_dictionary_generator',
                                                 'scripts/benchmark_public_schema_dictionaries.py')
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE core_session (id TEXT, provider TEXT, project_id TEXT);
        CREATE TABLE core_sessionitem (id INTEGER PRIMARY KEY, session_id TEXT, content TEXT,
                                       current_content_id INTEGER, original_content_id INTEGER);
        CREATE TABLE core_sessionitemcontent (id INTEGER PRIMARY KEY, payload BLOB, codec TEXT,
                                              dictionary_id INTEGER, raw_size INTEGER, sha256 BLOB);
        CREATE TABLE core_contentdictionary (id INTEGER PRIMARY KEY, payload BLOB, format TEXT, sha256 BLOB);
    ''')
    db.execute("INSERT INTO core_session VALUES ('s', 'codex', 'project')")
    dictionary = b'public schema dictionary'
    db.execute("INSERT INTO core_contentdictionary VALUES (1, ?, 'raw', ?)",
               (dictionary, hashlib.sha256(dictionary).digest()))
    expected = [b'legacy', 'current é'.encode(), b'compressed current' * 100, b'']
    for index, raw in enumerate(expected, 1):
        if index == 1:
            db.execute("INSERT INTO core_sessionitem VALUES (1, 's', 'legacy', NULL, NULL)")
            continue
        payload = raw
        codec = 'plain'
        dictionary_id = None
        if index == 3:
            data = zstandard.ZstdCompressionDict(dictionary, dict_type=zstandard.DICT_TYPE_RAWCONTENT)
            payload = zstandard.ZstdCompressor(level=3, dict_data=data).compress(raw)
            codec, dictionary_id = 'zstd', 1
        db.execute('INSERT INTO core_sessionitemcontent VALUES (?, ?, ?, ?, ?, ?)',
                   (index, payload, codec, dictionary_id, len(raw), hashlib.sha256(raw).digest()))
        db.execute("INSERT INTO core_sessionitem VALUES (?, 's', 'STALE LEGACY', ?, 99)", (index, index))
    assert [row[3] for row in generator.load_sampled_current_contents(db, [1, 2, 3, 4])] == expected
    db.close()


def test_wheel_packages_assets_and_seeds_fresh_database(tmp_path):
    import os
    import subprocess
    import sys
    import zipfile

    wheel_dir = tmp_path / 'wheels'
    result = subprocess.run(['uv', 'build', '--wheel', '--out-dir', str(wheel_dir)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    wheel, = wheel_dir.glob('*.whl')
    installed = tmp_path / 'installed'
    with zipfile.ZipFile(wheel) as archive:
        entries = orjson.loads((ASSETS / 'manifest.json').read_bytes())['dictionaries']
        prefix = 'twicc/core/content_storage/dictionaries/'
        assert {prefix + 'manifest.json', *(prefix + entry['file'] for entry in entries)} <= set(archive.namelist())
        archive.extractall(installed)
    script = '''
import hashlib
from importlib.resources import files
from pathlib import Path
import django
import orjson
import twicc
assert Path(twicc.__file__).resolve().is_relative_to(Path.cwd() / 'installed')
django.setup()
from django.core.management import call_command
from django.conf import settings
assert settings.DATABASES['default']['NAME'] == ':memory:'
call_command('migrate', verbosity=0)
from twicc.core.models import ContentDictionary
assets = files('twicc.core.content_storage').joinpath('dictionaries')
for entry in orjson.loads(assets.joinpath('manifest.json').read_bytes())['dictionaries']:
    row = ContentDictionary.objects.get(provider=entry['provider'], version=1)
    payload = assets.joinpath(entry['file']).read_bytes()
    assert bytes(row.payload) == payload
    assert bytes(row.sha256) == hashlib.sha256(payload).digest()
print('Installed wheel dictionaries seed the fresh isolated database')
'''
    env = dict(os.environ, PYTHONPATH=str(installed), TWICC_DATA_DIR=str(tmp_path),
               DJANGO_SETTINGS_MODULE='twicc.settings_test')
    result = subprocess.run([sys.executable, '-c', script], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'Installed wheel dictionaries seed' in result.stdout
