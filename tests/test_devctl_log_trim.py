"""devctl's side of the backend log trim: the worktree opt-out and the shrunk-log port check."""

import importlib.util
from pathlib import Path

import pytest


def _load_devctl():
    path = Path(__file__).resolve().parents[1] / "devctl.py"
    spec = importlib.util.spec_from_file_location("devctl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def devctl(tmp_path, monkeypatch):
    module = _load_devctl()
    monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(module, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(module, "is_git_worktree", lambda: True)
    return module


def test_worktree_env_sets_the_ports_and_the_log_trim_opt_out(devctl):
    devctl.save_worktree_env(3501, 5174)
    env = devctl.load_env_file()
    assert env["TWICC_PORT"] == "3501"
    assert env["VITE_PORT"] == "5174"
    assert env["TWICC_NO_LOG_TRIM"] == "1"


def test_worktree_env_appends_to_the_existing_file(devctl):
    devctl.ENV_FILE.write_text("TWICC_PASSWORD_HASH=abc\n")
    devctl.save_worktree_env(3501, 5174)
    env = devctl.load_env_file()
    assert env["TWICC_PASSWORD_HASH"] == "abc"
    assert env["TWICC_NO_LOG_TRIM"] == "1"


def _backend(log: Path, port: int = 3501) -> dict:
    return {"back": {"name": "Backend", "port": port, "log": log}}


def test_verify_port_reads_a_shrunk_log_from_its_start(devctl, tmp_path):
    log = tmp_path / "backend.log"
    log.write_text("[2026-09-05 10:00:00,000 - INFO - uvicorn] Uvicorn running on http://0.0.0.0:3501\n")
    # The log was much longer before the backend trimmed it at startup.
    assert devctl.verify_port("back", log_start_pos=10_000, processes=_backend(log), timeout=1.0) is True


def test_verify_port_still_ignores_the_content_before_the_start(devctl, tmp_path):
    log = tmp_path / "backend.log"
    old = "[2026-09-04 10:00:00,000 - INFO - uvicorn] Uvicorn running on http://0.0.0.0:3501\n"
    log.write_text(old + "[2026-09-05 10:00:00,000 - INFO - twicc.run] TWICC starting...\n")
    assert devctl.verify_port("back", log_start_pos=len(old), processes=_backend(log), timeout=0.3) is False
