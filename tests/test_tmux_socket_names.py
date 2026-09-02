"""Per-data-dir tmux socket names (``paths.tmux_socket_suffix`` + the devctl mirror).

Design: docs/plans/2026-09-02-provider-home-dirs-design.md (section 8).
"""

import hashlib
import importlib.util
from pathlib import Path

import pytest

from twicc import paths


def _load_devctl():
    path = Path(__file__).resolve().parents[1] / "devctl.py"
    spec = importlib.util.spec_from_file_location("devctl", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def devctl():
    return _load_devctl()


def test_default_data_dir_keeps_the_bare_names(monkeypatch):
    monkeypatch.delenv("TWICC_DATA_DIR", raising=False)
    assert paths.get_data_dir() == paths.DEFAULT_DATA_DIR
    assert paths.tmux_socket_suffix() == ""


def test_symlink_to_the_default_data_dir_is_the_default(monkeypatch, tmp_path):
    link = tmp_path / "twicc-link"
    link.symlink_to(paths.DEFAULT_DATA_DIR)
    monkeypatch.setenv("TWICC_DATA_DIR", str(link))
    assert paths.tmux_socket_suffix() == ""


def test_other_data_dir_gets_a_stable_hash_suffix(monkeypatch, tmp_path):
    monkeypatch.setenv("TWICC_DATA_DIR", str(tmp_path))
    expected = "-" + hashlib.sha256(str(tmp_path.resolve()).encode()).hexdigest()[:8]
    assert paths.tmux_socket_suffix() == expected
    assert paths.tmux_socket_suffix() == expected
    assert len(expected) == 9


def test_devctl_mirror_matches_the_backend(monkeypatch, tmp_path, devctl):
    monkeypatch.setenv("TWICC_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(devctl, "DATA_DIR", tmp_path)
    assert devctl.tmux_socket_suffix() == paths.tmux_socket_suffix()
    suffix = paths.tmux_socket_suffix()
    assert devctl.tmux_socket_names() == ("twicc" + suffix, "twicc-hybrid" + suffix)

    monkeypatch.delenv("TWICC_DATA_DIR")
    monkeypatch.setattr(devctl, "DATA_DIR", devctl.DEFAULT_DATA_DIR)
    assert devctl.tmux_socket_suffix() == paths.tmux_socket_suffix() == ""
    assert devctl.tmux_socket_names() == ("twicc", "twicc-hybrid")


def test_terminal_constants_derive_from_the_suffix():
    from twicc import terminal

    suffix = paths.tmux_socket_suffix()
    assert terminal.TMUX_SOCKET_NAME == "twicc" + suffix
    assert terminal.HYBRID_TMUX_SOCKET_NAME == "twicc-hybrid" + suffix
