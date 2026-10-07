"""Update instructions must target the running distribution's installation."""

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_installation_detection(monkeypatch):
    from twicc import installation

    monkeypatch.setattr(installation.sys, "executable", "/home/user/.cache/uv/archive-v0/hash/bin/python")
    assert installation.detect_installation(Path("/installed/twicc")) == "uvx"
    monkeypatch.setattr(installation.sys, "executable", "/home/user/.local/share/uv/tools/twicc/bin/python")
    assert installation.detect_installation(Path("/installed/twicc")) == "uv_tool"
    assert installation.detect_installation(Path("/repo/src/twicc")) == "source"


@pytest.mark.parametrize("installer,pip_available,expected", [
    ("pip\n", True, "pip"), ("pip\n", False, "unknown"), ("uv\n", True, "unknown"), (None, True, "unknown"),
])
def test_other_installations_require_confirmed_pip(monkeypatch, installer, pip_available, expected):
    from twicc import installation

    monkeypatch.setattr(installation.sys, "executable", "/custom env/bin/python")
    monkeypatch.setattr(installation, "distribution", lambda name: SimpleNamespace(read_text=lambda file: installer if file == "INSTALLER" else None))
    monkeypatch.setattr(installation, "find_spec", lambda name: object() if pip_available else None)
    assert installation.detect_installation(Path("/installed/twicc")) == expected


def test_pipx_is_not_mistaken_for_a_direct_pip_install(monkeypatch):
    from twicc import installation

    monkeypatch.setattr(installation.sys, "executable", "/home/user/.local/share/pipx/venvs/twicc/bin/python")
    monkeypatch.setattr(installation, "distribution", lambda name: SimpleNamespace(read_text=lambda file: "pip" if file == "INSTALLER" else None))
    monkeypatch.setattr(installation, "find_spec", lambda name: object())
    assert installation.detect_installation(Path("/installed/twicc")) == "unknown"


def test_missing_metadata_uses_generic_instructions(monkeypatch):
    from twicc import installation

    monkeypatch.setattr(installation.sys, "executable", "/custom/bin/python")
    def missing(name):
        raise installation.PackageNotFoundError(name)
    monkeypatch.setattr(installation, "distribution", missing)
    assert installation.detect_installation(Path("/installed/twicc")) == "unknown"


@pytest.mark.parametrize("mode,command,after", [
    ("uvx", "uvx twicc@latest", ""),
    ("uv_tool", "uv tool upgrade twicc", "Then restart TwiCC."),
    ("pip", "'/custom env/bin/python' -m pip install --upgrade twicc", "Then restart TwiCC."),
    ("source", None, ""), ("unknown", None, ""),
])
def test_update_commands_target_the_detected_installation(monkeypatch, mode, command, after):
    from twicc import installation

    monkeypatch.setattr(installation.sys, "executable", "/custom env/bin/python")
    instructions = installation.resolve_update_instructions(mode)
    assert instructions.command == command
    assert instructions.after == after
    assert instructions.mode == mode
    assert instructions.before.startswith("Stop TwiCC,")


def test_direct_url_installation_keeps_generic_instructions(monkeypatch):
    from twicc import installation

    monkeypatch.setattr(installation.sys, "executable", "/custom/bin/python")
    metadata = {"INSTALLER": "pip", "direct_url.json": '{"url": "https://example.com/custom.whl"}'}
    monkeypatch.setattr(installation, "distribution", lambda name: SimpleNamespace(read_text=metadata.get))
    monkeypatch.setattr(installation, "find_spec", lambda name: object())
    assert installation.detect_installation(Path("/installed/twicc")) == "unknown"


@pytest.mark.parametrize("mode,uvx_mode,expected", [
    ("uvx", True, "uvx twicc"), ("uv_tool", False, "twicc"),
])
def test_login_launch_prefix_keeps_existing_managed_commands(monkeypatch, mode, uvx_mode, expected):
    from twicc import settings

    monkeypatch.setattr(settings, "INSTALLATION_MODE", mode)
    monkeypatch.setattr(settings, "UVX_MODE", uvx_mode)
    monkeypatch.setattr(settings.shutil, "which", lambda name: "/tools/bin/twicc")
    assert settings._resolve_twicc_launch_prefix() == expected


def test_login_launch_prefix_keeps_source_checkout_directory(monkeypatch, tmp_path):
    from twicc import settings

    script = tmp_path / "run.py"
    script.touch()
    monkeypatch.setattr(settings, "INSTALLATION_MODE", "source")
    monkeypatch.setattr(settings, "UVX_MODE", False)
    monkeypatch.setattr(settings, "DEV_MODE", True)
    monkeypatch.setattr(settings.sys, "argv", [str(script)])
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    assert settings._resolve_twicc_launch_prefix() == f"uv run --directory {tmp_path} run.py"
