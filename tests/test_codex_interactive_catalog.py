"""Interactive Codex catalogues remove async questions without changing other capabilities."""

import asyncio
import fcntl
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import orjson
import pytest

from twicc.providers.codex import bin, interactive_catalog


SOURCE = {
    "models": [
        {
            "slug": "gpt-sol",
            "base_instructions": "Keep these instructions.",
            "model_messages": {"future": {"instruction": "Keep this too."}},
            "context_window": 272000,
            "experimental_supported_tools": [
                "request_user_input_async", "request_user_input", "send_user_message_async", "future_tool",
            ],
        },
        {"slug": "other-model", "experimental_supported_tools": []},
    ],
    "future_catalog_field": {"keep": True},
}


def test_filter_removes_both_async_names_and_preserves_everything_else():
    source = deepcopy(SOURCE)
    result = interactive_catalog.transform_catalog(source)
    expected = deepcopy(SOURCE)
    expected["models"][0]["experimental_supported_tools"] = ["request_user_input", "future_tool"]
    assert result == expected
    assert source == SOURCE


@pytest.mark.parametrize("source", [
    {}, {"models": []}, {"models": [None]},
    {"models": [{"slug": "model"}]},
    {"models": [{"experimental_supported_tools": "request_user_input_async"}]},
])
def test_schema_changes_fail_instead_of_silently_restoring_async_questions(source):
    with pytest.raises(ValueError):
        interactive_catalog.transform_catalog(source)


def test_cache_repairs_corruption_and_changes_with_source_and_version(tmp_path, monkeypatch):
    source = deepcopy(SOURCE)
    version = "codex-cli 0.161.0"
    monkeypatch.setattr(interactive_catalog, "bundled_catalog", lambda binary: (version, source))
    first = interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path)
    assert first.name.startswith("interactive-codex-catalog-0.161.0-")
    assert orjson.loads(first.read_bytes())["models"][0]["experimental_supported_tools"] == [
        "request_user_input", "future_tool",
    ]
    assert interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path) == first
    first.write_bytes(b"corrupt")
    assert interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path) == first
    assert orjson.loads(first.read_bytes())["future_catalog_field"] == {"keep": True}
    source["models"][0]["base_instructions"] = "Updated upstream instructions."
    second = interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path)
    assert second != first
    version = "codex-cli 0.162.0"
    assert interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path) != second
    assert not list(tmp_path.glob("*.tmp"))


def test_config_override_is_opt_in_and_preserves_other_overrides(tmp_path, monkeypatch):
    async def ready():
        pass

    monkeypatch.setattr(bin, "ensure_codex_runtime", ready)
    monkeypatch.setattr(bin, "codex_binary_path", lambda: tmp_path / "codex")
    monkeypatch.setattr(bin, "_codex_env", dict)
    monkeypatch.setattr("twicc.provider_homes.ensure_codex_home", lambda: None)
    monkeypatch.setattr(interactive_catalog, "bundled_catalog", lambda binary: ("codex-cli 0.161.0", SOURCE))
    monkeypatch.setattr("twicc.paths.get_data_dir", lambda: tmp_path)

    plain = asyncio.run(bin.make_codex_config(cwd=str(tmp_path)))
    assert not plain.config_overrides
    filtered = asyncio.run(bin.make_codex_config(
        cwd=str(tmp_path), disable_async_questions=True, config_overrides=("features.plugins=false",),
    ))
    assert filtered.config_overrides[0] == "features.plugins=false"
    key, value = filtered.config_overrides[-1].split("=", 1)
    assert key == "model_catalog_json"
    path = Path(orjson.loads(value))
    assert path.parent == tmp_path / "cache"
    assert orjson.loads(path.read_bytes())["models"][0]["experimental_supported_tools"] == [
        "request_user_input", "future_tool",
    ]


def test_cleanup_removes_only_unused_interactive_catalogues(tmp_path, monkeypatch):
    monkeypatch.setattr(interactive_catalog, "bundled_catalog", lambda binary: ("codex-cli 0.162.0", SOURCE))
    obsolete = tmp_path / "interactive-codex-catalog-codex-cli_0.161.0-old-v1.json"
    obsolete.write_text("old")
    protected = tmp_path / "interactive-codex-catalog-codex-cli_0.160.0-active-v1.json"
    protected.write_text("active")
    unrelated = tmp_path / "hermetic-codex-catalog-old.json"
    unrelated.write_text("keep")
    orphan_lease = tmp_path / "interactive-codex-catalog-codex-cli_0.159.0-lost-v1.lease"
    orphan_lease.touch()
    with protected.with_suffix(".lease").open("a+b") as lease:
        fcntl.flock(lease, fcntl.LOCK_SH)
        current = interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path)
        assert not obsolete.exists()
        assert not orphan_lease.exists()
        assert protected.read_text() == "active"
        assert unrelated.read_text() == "keep"
        assert current.exists()
    interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path)
    assert not protected.exists()
    assert not protected.with_suffix(".lease").exists()
    assert current.exists()


def test_cleanup_keeps_catalogues_retained_by_this_backend(tmp_path, monkeypatch):
    version = "codex-cli 0.161.0"
    monkeypatch.setattr(interactive_catalog, "bundled_catalog", lambda binary: (version, SOURCE))
    active = interactive_catalog.ensure_catalog(tmp_path / "old-codex", cache_dir=tmp_path)
    version = "codex-cli 0.162.0"
    current = interactive_catalog.ensure_catalog(tmp_path / "new-codex", cache_dir=tmp_path)
    assert active.exists()
    assert current.exists()


def test_cleanup_waits_for_another_backend_to_exit(tmp_path, monkeypatch):
    script = """
import sys
from pathlib import Path
from twicc.providers.codex import interactive_catalog as catalog
catalog.bundled_catalog = lambda binary: (
    "codex-cli 0.161.0", {"models": [{"experimental_supported_tools": []}]},
)
path = catalog.ensure_catalog(Path("old-codex"), cache_dir=Path(sys.argv[1]))
print(path.name, flush=True)
sys.stdin.readline()
"""
    monkeypatch.setattr(interactive_catalog, "bundled_catalog", lambda binary: ("codex-cli 0.162.0", SOURCE))
    with subprocess.Popen(
        [sys.executable, "-u", "-c", script, str(tmp_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    ) as process:
        try:
            filename = process.stdout.readline().strip()
            assert filename.startswith("interactive-codex-catalog-0.161.0-")
            old = tmp_path / filename
            current = interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path)
            assert old.exists()
            assert current.exists()
        finally:
            process.communicate(input="\n", timeout=10)
        assert process.returncode == 0
    interactive_catalog.ensure_catalog(tmp_path / "codex", cache_dir=tmp_path)
    assert not old.exists()
    assert not old.with_suffix(".lease").exists()
    assert current.exists()
