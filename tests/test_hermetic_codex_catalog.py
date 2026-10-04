import copy
import threading

import orjson
import pytest

from twicc.providers.codex import hermetic_catalog as cat
from twicc.providers.hermetic import HermeticConfigError

ENTRY = {
    "slug": "gpt-6-luna", "display_name": "Luna", "base_instructions": "long original text",
    "model_messages": {"x": 1}, "include_apps_usage_instructions": True,
    "include_plugin_usage_instructions": True, "include_skills_usage_instructions": True,
    "shell_type": "shell_command", "tool_mode": "code", "apply_patch_tool_type": "freeform",
    "supports_search_tool": True, "experimental_supported_tools": ["a"], "multi_agent_version": "v2",
    "some_future_field": 42,
}


def test_transform_sets_every_overridden_field_and_keeps_the_rest():
    out = cat.transform_entry(ENTRY, variant="production")
    assert out["base_instructions"] == cat.PRODUCTION_BASE_INSTRUCTIONS
    assert out["model_messages"] is None
    assert out["include_apps_usage_instructions"] is False
    assert out["include_plugin_usage_instructions"] is False
    assert out["include_skills_usage_instructions"] is False
    assert out["shell_type"] == "disabled"
    assert out["tool_mode"] is None and out["multi_agent_version"] is None
    assert out["apply_patch_tool_type"] is None and out["supports_search_tool"] is False
    assert out["experimental_supported_tools"] == []
    assert out["some_future_field"] == 42 and out["display_name"] == "Luna"
    assert ENTRY["shell_type"] == "shell_command"  # the source is not mutated


def test_neutral_variant_differs_only_by_base_instructions():
    prod = cat.transform_entry(ENTRY, variant="production")
    neutral = cat.transform_entry(ENTRY, variant="neutral")
    assert neutral["base_instructions"] == "You are a helpful assistant."
    assert {k: v for k, v in neutral.items() if k != "base_instructions"} == \
           {k: v for k, v in prod.items() if k != "base_instructions"}


def test_nullable_keys_may_be_absent_in_the_source():
    entry = {k: v for k, v in ENTRY.items() if k not in cat.NULLABLE_KEYS}
    out = cat.transform_entry(entry, variant="production")
    assert out["tool_mode"] is None and out["multi_agent_version"] is None


def test_other_missing_field_raises_catalog():
    entry = {k: v for k, v in ENTRY.items() if k != "shell_type"}
    with pytest.raises(HermeticConfigError) as info:
        cat.transform_entry(entry, variant="production")
    assert info.value.reason == "catalog" and "shell_type" in str(info.value)


def _valid():
    return {"models": [cat.transform_entry(ENTRY, variant="production")]}


def test_validate_accepts_a_good_catalog_and_absent_nullable_keys():
    data = _valid()
    cat.validate_catalog(data, model="gpt-6-luna", variant="production")
    del data["models"][0]["tool_mode"]
    cat.validate_catalog(data, model="gpt-6-luna", variant="production")


@pytest.mark.parametrize("mutate", [
    lambda d: d["models"].append(copy.deepcopy(d["models"][0])),
    lambda d: d["models"][0].update(slug="other"),
    lambda d: d["models"][0].update(shell_type="shell_command"),
    lambda d: d["models"][0].update(tool_mode="code"),
    lambda d: d["models"][0].update(base_instructions="changed"),
    lambda d: d.update(models=[]),
])
def test_validate_rejects_a_bad_catalog(mutate):
    data = _valid()
    mutate(data)
    with pytest.raises(HermeticConfigError) as info:
        cat.validate_catalog(data, model="gpt-6-luna", variant="production")
    assert info.value.reason == "catalog"


class FakeRun:
    def __init__(self, version="codex-cli 0.160.0", models=None, code=0, out=None):
        self.calls = []
        self.version, self.models, self.code, self.out = version, models or [ENTRY], code, out

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        from types import SimpleNamespace
        if "--version" in argv:
            return SimpleNamespace(returncode=0, stdout=self.version + "\n", stderr="")
        stdout = self.out if self.out is not None else orjson.dumps({"models": self.models}).decode()
        return SimpleNamespace(returncode=self.code, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def _clear_memo():
    cat._reset_memo()


@pytest.fixture
def hermetic_logs():
    import logging

    logger = logging.getLogger("twicc.providers.hermetic")
    records: list[str] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler, was_disabled, level = _Collect(), logger.disabled, logger.level
    logger.disabled = False
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    yield records
    logger.removeHandler(handler)
    logger.setLevel(level)
    logger.disabled = was_disabled


def test_ensure_catalog_writes_once_and_names_the_file_from_its_inputs(tmp_path, monkeypatch):
    run = FakeRun()
    monkeypatch.setattr(cat.subprocess, "run", run)
    path = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert path.parent == tmp_path and "0.160.0" in path.name and "gpt-6-luna" in path.name
    assert path.name.endswith(f"-v{cat.TRANSFORM_VERSION}.json")
    cat.validate_catalog(orjson.loads(path.read_bytes()), model="gpt-6-luna", variant="production")
    cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert sum("--bundled" in c for c in run.calls) == 1  # memoised per process and binary


def test_a_changed_entry_changes_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    first = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    cat._reset_memo()
    monkeypatch.setattr(cat.subprocess, "run", FakeRun(models=[{**ENTRY, "some_future_field": 43}]))
    second = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert first != second


def test_a_changed_cli_version_changes_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    first = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    cat._reset_memo()
    monkeypatch.setattr(cat.subprocess, "run", FakeRun(version="codex-cli 0.161.0"))
    assert cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path) != first


def test_a_changed_transformation_constant_changes_the_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    first = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    monkeypatch.setattr(cat, "TRANSFORM_VERSION", cat.TRANSFORM_VERSION + 1)
    assert cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path) != first


def test_unknown_slug_raises_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "nope", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


@pytest.mark.parametrize("run", [FakeRun(code=1), FakeRun(out="not json")])
def test_subprocess_failure_or_garbage_raises_catalog(tmp_path, monkeypatch, run):
    monkeypatch.setattr(cat.subprocess, "run", run)
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


def test_subprocess_timeout_raises_catalog(tmp_path, monkeypatch):
    def boom(argv, **kw):
        raise cat.subprocess.TimeoutExpired(argv, 20)
    monkeypatch.setattr(cat.subprocess, "run", boom)
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


def test_concurrent_generation_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    paths, errors = [], []

    def go():
        try:
            paths.append(cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert not errors and len(set(paths)) == 1
    cat.validate_catalog(orjson.loads(paths[0].read_bytes()), model="gpt-6-luna", variant="production")
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_regenerating_a_damaged_cache_file_logs_nothing(tmp_path, monkeypatch, hermetic_logs):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    path = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    path.write_text("{}")
    cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert hermetic_logs == []


def test_a_cached_file_that_fails_validation_is_regenerated(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    path = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    path.write_text("{}")
    again = cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert again == path
    cat.validate_catalog_file(again, "gpt-6-luna", "production")  # raises if still damaged


def test_unwritable_cache_dir_raises_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=blocker / "cache")
    assert info.value.reason == "catalog"


def test_failed_temp_file_creation_raises_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(cat.subprocess, "run", FakeRun())

    def boom(**_kw):
        raise PermissionError("read-only")

    monkeypatch.setattr(cat.tempfile, "mkstemp", boom)
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert info.value.reason == "catalog"


def test_non_dict_model_entry_raises_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(cat, "bundled_catalog", lambda binary: ("codex-cli 1.0", {"models": ["oops", None]}))
    with pytest.raises(HermeticConfigError) as info:
        cat.ensure_catalog(tmp_path / "codex", "gpt-6-luna", cache_dir=tmp_path)
    assert info.value.reason == "catalog"
