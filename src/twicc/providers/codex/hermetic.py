"""
Hermetic Codex calls: a minimal catalogue, process overrides and thread parameters
that leave the model no tool, plus a runtime guard (design §5.4, §5.5).
"""
import logging
import threading
from collections.abc import Iterable
from pathlib import Path

import orjson

from twicc.providers.hermetic import HermeticGuardViolation

logger = logging.getLogger(__name__)

# ``features.*`` names switched off at process level. Unknown names are silently
# ignored by Codex, so the manual diagnostic (O4) checks they still exist.
FEATURES_OFF: tuple[str, ...] = (
    "hooks", "plugins", "apps", "browser_use", "browser_use_external", "computer_use", "in_app_browser",
    "image_generation", "goals", "memories", "shell_tool", "unified_exec", "multi_agent", "view_image",
    "skill_search", "tool_suggest", "sleep_tool",
)

ALLOWED_ITEM_TYPES = frozenset({"userMessage", "agentMessage", "reasoning"})


def process_overrides(catalog_path: Path, extra: Iterable[str] = ()) -> tuple[str, ...]:
    """The ``--config key=value`` overrides of the app-server process (§5.4 (b))."""
    # A JSON string is a valid TOML basic string (quotes and backslashes escaped).
    return (
        f"model_catalog_json={orjson.dumps(str(catalog_path)).decode()}",
        "project_doc_max_bytes=0",
        "skills.max_context_tokens=1",   # 0 is rejected at startup
        'web_search="disabled"',
        "notify=[]",
        *(f"features.{name}=false" for name in FEATURES_OFF),
        *extra,
    )


def thread_config(mcp_server_names: Iterable[str]) -> dict:
    """The thread-level ``config`` patch (§5.4 (c)).

    ``mcp_servers`` must be a nested table (it merges with each server's own
    transport settings, and keeps names with dots literal). ``features`` and
    ``tools`` must be DOTTED keys: a nested ``features`` table at thread level
    replaces the whole table of the lower layers and restores every tool.
    """
    return {
        "mcp_servers": {name: {"enabled": False} for name in mcp_server_names},
        "features.default_mode_request_user_input": False,
        "tools.experimental_request_user_input.enabled": False,
        "suppress_unstable_features_warning": True,
    }


def _field(source, *names):
    """First present, non-None value among ``names`` in a dict, else ``None``."""
    for name in names:
        value = source.get(name)
        if value is not None:
            return value
    return None


def _field_keep_empty(source, *names):
    """Like ``_field`` but an empty list/dict counts as present."""
    for name in names:
        if name in source and source[name] is not None:
            return source[name]
    return None


def _model_slug(model_obj) -> str | None:
    return getattr(model_obj, "model", None) or getattr(model_obj, "id", None)


def check_codex_model_list(response, *, model: str) -> None:
    """``model/list`` (hidden included) must return exactly the requested model: proof the catalogue was loaded."""
    data = getattr(response, "data", None)
    if data is None:
        raise HermeticGuardViolation("model/list returned no data")
    if getattr(response, "next_cursor", None):
        raise HermeticGuardViolation("model/list has a next page: the catalogue override was not loaded")
    slugs = [_model_slug(m) for m in data]
    if slugs != [model]:
        raise HermeticGuardViolation(f"model/list returned {slugs!r}, expected {[model]!r}")


def check_codex_thread_start(start: dict, *, model: str, cwd: Path, codex_home: Path) -> None:
    """Verify the ``thread/start`` response (available before any model call)."""
    got_model = _field(start, "model")
    got_cwd = _field(start, "cwd")
    sandbox = _field(start, "sandbox")
    approval = _field(start, "approval_policy", "approvalPolicy")
    if got_model is None or got_cwd is None or sandbox is None or approval is None:
        raise HermeticGuardViolation("thread/start response lacks model, cwd, sandbox or approval policy")
    sources = _field_keep_empty(start, "instruction_sources", "instructionSources")
    if sources is None:
        raise HermeticGuardViolation("thread/start response lacks instruction_sources")
    if got_model != model:
        raise HermeticGuardViolation(f"thread/start reports model {got_model!r}")
    if Path(got_cwd).resolve() != Path(cwd).resolve():
        raise HermeticGuardViolation(f"thread/start reports cwd {got_cwd!r}")
    if sandbox.get("type") != "readOnly" or sandbox.get("network_access", sandbox.get("networkAccess")):
        raise HermeticGuardViolation(f"thread/start reports sandbox {sandbox!r}")
    if approval != "never":
        raise HermeticGuardViolation(f"thread/start reports approval policy {approval!r}")
    home = Path(codex_home).resolve()
    for source in sources:
        # Resolve the directory only: ``~/.codex/AGENTS.md`` is often a symlink into a dotfiles repository.
        path = Path(source)
        if path.parent.resolve() != home or path.name not in ("AGENTS.md", "AGENTS.override.md"):
            raise HermeticGuardViolation(f"unexpected instruction source {source!r}")


def classify_codex_item(type_name: str | None) -> None:
    """Fail on any stream item that is not a plain message or reasoning (§5.5 (iii))."""
    if type_name not in ALLOWED_ITEM_TYPES:
        raise HermeticGuardViolation(f"unexpected stream item type {type_name!r}")


_REFUSALS: dict[str, dict] = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "cancel"},
    "item/tool/requestUserInput": {"answers": {}},
}


class RefusingApprovalHandler:
    """Refuses every server request and records the first one.

    Runs in the SDK's reader thread, hence the ``threading.Event``. The reply
    shapes are the ones TwiCC already sends when it cannot reach a user.
    """

    def __init__(self) -> None:
        self.violated = threading.Event()
        self.method: str | None = None

    def __call__(self, method: str, params: dict | None) -> dict:
        if not self.violated.is_set():
            self.method = method
            self.violated.set()
        # Fresh dict on every call. The shapes equal ``approvals.default_response_for``; they are inlined
        # so this module does not import the Codex agent package (a heavy import and a cycle risk).
        reply = _REFUSALS.get(method)
        return dict(reply) if reply is not None else {}
