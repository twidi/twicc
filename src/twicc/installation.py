"""Detect the running installation and describe its update procedure."""

import shlex
import sys
from importlib.metadata import PackageNotFoundError, distribution
from importlib.util import find_spec
from pathlib import Path
from typing import NamedTuple


def _is_uv_managed(segment: str) -> bool:
    # Do not resolve symlinks: the venv path identifies its manager, while
    # its Python symlink can point outside that environment.
    parts = Path(sys.executable).parts
    return any(part == "uv" and parts[i + 1] == segment for i, part in enumerate(parts[:-1]))


def detect_installation(package_dir: Path) -> str:
    if package_dir.parent.name == "src":
        return "source"
    if _is_uv_managed("archive-v0"):
        return "uvx"
    if _is_uv_managed("tools"):
        return "uv_tool"

    # pipx uses pip internally, but owns the environment and its upgrades.
    parts = Path(sys.executable).parts
    if any(part == "pipx" and parts[i + 1] == "venvs" for i, part in enumerate(parts[:-1])):
        return "unknown"
    try:
        package = distribution("twicc")
        installer = (package.read_text("INSTALLER") or "").strip()
        # A Git/local/URL installation must retain its original update source.
        if installer == "pip" and not package.read_text("direct_url.json") and find_spec("pip") is not None:
            return "pip"
    except (PackageNotFoundError, OSError, ValueError):
        pass
    return "unknown"


class UpdateInstructions(NamedTuple):
    mode: str
    before: str
    command: str | None
    after: str


def resolve_update_instructions(mode: str) -> UpdateInstructions:
    if mode == "uvx":
        return UpdateInstructions(mode, "Stop TwiCC, then run:", "uvx twicc@latest", "")
    if mode == "uv_tool":
        return UpdateInstructions(mode, "Stop TwiCC, run:", "uv tool upgrade twicc", "Then restart TwiCC.")
    if mode == "pip":
        command = f"{shlex.quote(sys.executable)} -m pip install --upgrade twicc"
        return UpdateInstructions(mode, "Stop TwiCC, run:", command, "Then restart TwiCC.")
    if mode == "source":
        return UpdateInstructions(mode, "Stop TwiCC, update your source checkout, then restart TwiCC.", None, "")
    return UpdateInstructions(
        "unknown", "Stop TwiCC, update it with the package manager used to install it, then restart.", None, ""
    )


def prepare_update_instructions_for_client(instructions: dict) -> dict:
    """Add browser presentation metadata to the existing installation instructions."""
    from twicc.core.ui_messages import ui_message

    result = dict(instructions)
    mode = instructions.get("mode")
    if mode == "uvx":
        before_key = "backend.updates.stop_then_run"
    elif mode in ("uv_tool", "pip"):
        before_key = "backend.updates.stop_run"
    elif mode == "source":
        before_key = "backend.updates.source_checkout"
    else:
        before_key = "backend.updates.unknown_installation"
    result["before_ui_message"] = ui_message(before_key)
    if mode in ("uv_tool", "pip"):
        result["after_ui_message"] = ui_message("backend.updates.restart")
    return result
