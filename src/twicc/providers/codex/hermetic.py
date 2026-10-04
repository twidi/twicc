"""
Hermetic Codex calls: a minimal catalogue, process overrides and thread parameters
that leave the model no tool, plus a runtime guard (design §5.4, §5.5).
"""
import asyncio
import logging
import threading
from collections.abc import Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import NamedTuple

import orjson
from openai_codex import TextInput
from openai_codex.generated.v2_all import (
    AskForApproval,
    ConfigReadResponse,
    ErrorNotification,
    ReasoningEffort,
    SandboxMode,
)

from twicc.provider_homes import codex_home
from twicc.providers.codex import hermetic_catalog
from twicc.providers.codex.bin import make_codex_config
from twicc.providers.codex.runtime import codex_binary_path, ensure_codex_runtime
from twicc.providers.codex.sdk_wrappers import TwiccAsyncCodex
from twicc.providers.hermetic import HermeticConfigError, HermeticGuardViolation, hermetic_cwd

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


# Replaced by the unit tests.
_client_factory = TwiccAsyncCodex


class HermeticCodexPlan(NamedTuple):
    model: str
    config: object          # CodexConfig
    catalog_path: Path
    cwd: Path


class HermeticCodexResult(NamedTuple):
    text: str
    terminal_error: object | None   # ErrorNotification payload, in the shape the auth classifier receives
    input_tokens: int | None
    start: dict


async def _catalog_for(model: str, variant: str) -> Path:
    return await asyncio.to_thread(hermetic_catalog.ensure_catalog, codex_binary_path(), model, variant)


async def _prepare(model, *, catalog_variant, catalog_path, extra_config_overrides) -> HermeticCodexPlan:
    # Before anything runs the binary: downloads the runtime when the cache was pruned (as make_codex_config does).
    await ensure_codex_runtime()
    cwd = hermetic_cwd()
    if catalog_path is None:
        catalog_path = await _catalog_for(model, catalog_variant)
        # ensure_catalog validated it; a caller-supplied path is used as is (diagnostic only).
    config = await make_codex_config(
        cwd=str(cwd), config_overrides=process_overrides(catalog_path, extra_config_overrides),
    )
    return HermeticCodexPlan(model=model, config=config, catalog_path=Path(catalog_path), cwd=cwd)


async def prepare_hermetic_codex(model: str) -> HermeticCodexPlan:
    """Build the catalogue, the process overrides and the neutral directory check (what ``make_codex_config`` did)."""
    return await _prepare(model, catalog_variant="production", catalog_path=None, extra_config_overrides=())


async def _prepare_hermetic_codex_for_diagnostic(
    model: str,
    *,
    catalog_variant: str = "production",
    catalog_path: Path | None = None,
    extra_config_overrides: tuple[str, ...] = (),
) -> HermeticCodexPlan:
    """Diagnostic seam (§5.1): a neutral-instruction catalogue, a catalogue used as is, extra overrides."""
    return await _prepare(
        model, catalog_variant=catalog_variant, catalog_path=catalog_path, extra_config_overrides=extra_config_overrides,
    )


async def _list_models(codex):
    return await codex._client.model_list(include_hidden=True)


async def _read_mcp_server_names(codex, cwd: Path) -> tuple[str, ...]:
    inherited = await codex._client.request(
        "config/read", {"includeLayers": False, "cwd": str(cwd)}, response_model=ConfigReadResponse,
    )
    return tuple(inherited.config.model_dump().get("mcp_servers", {}) or {})


def _item_type(event) -> str | None:
    item = getattr(event.payload, "item", None)
    if item is None:
        return None
    data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return data.get("type") if isinstance(data, dict) else None


def _item_text(event) -> str:
    item = getattr(event.payload, "item", None)
    data = item.model_dump(mode="json") if hasattr(item, "model_dump") else item
    return data.get("text", "") if isinstance(data, dict) else ""


async def _interrupt(handle) -> None:
    try:
        await handle.interrupt()
    except Exception:
        logger.debug("interrupt failed after a guard violation", exc_info=True)


class HermeticCodexThread:
    """An open hermetic thread: the verified ``thread/start`` response, the guard state and ``run_turn``."""

    def __init__(self, thread, start: dict, handler: RefusingApprovalHandler, disabled_mcp_servers: tuple[str, ...]):
        self._thread = thread
        self.start = start
        self.handler = handler
        self.disabled_mcp_servers = disabled_mcp_servers

    @property
    def refused_method(self) -> str | None:
        return self.handler.method

    async def _check_flag(self, handle) -> None:
        if self.handler.violated.is_set():
            await _interrupt(handle)
            raise HermeticGuardViolation(f"refused request: {self.handler.method}")

    async def run_turn(self, prompt: str, *, effort) -> HermeticCodexResult:
        """One turn. Raises ``HermeticGuardViolation`` on a forbidden item or a refused request."""
        effort = ReasoningEffort(effort) if isinstance(effort, str) else effort
        handle = await self._thread.turn_with_policy(TextInput(prompt), effort=effort)
        text: list[str] = []
        terminal_error = None
        tokens = None
        async for event in handle.stream():
            await self._check_flag(handle)
            if event.method == "thread/tokenUsage/updated":
                tokens = event.payload.model_dump(mode="json")["token_usage"]["last"]["input_tokens"]
            elif event.method in ("item/started", "item/completed"):
                type_name = _item_type(event)
                try:
                    classify_codex_item(type_name)
                except HermeticGuardViolation:
                    await _interrupt(handle)
                    raise
                if event.method == "item/completed" and type_name == "agentMessage":
                    text.append(_item_text(event))
            elif event.method == "error":
                payload = getattr(event, "payload", None)
                if isinstance(payload, ErrorNotification) and not payload.will_retry:
                    terminal_error = payload
        await self._check_flag(handle)
        return HermeticCodexResult("".join(text).strip(), terminal_error, tokens, self.start)


@asynccontextmanager
async def hermetic_codex(plan: HermeticCodexPlan):
    """Start the app-server, verify it, start the thread; close the app-server on exit (§5.4, §5.5)."""
    codex = _client_factory(config=plan.config)
    handler = RefusingApprovalHandler()
    # Before the first request, the way CodexAgent patches the client for user sessions.
    codex._client._sync._approval_handler = handler
    try:
        try:
            await codex._ensure_initialized()
            check_codex_model_list(await _list_models(codex), model=plan.model)
        except HermeticGuardViolation:
            raise
        except Exception as exc:
            raise HermeticConfigError("start", f"The Codex app-server did not start: {exc!r}") from exc
        try:
            names = await _read_mcp_server_names(codex, plan.cwd)
        except Exception as exc:
            raise HermeticConfigError("mcp-config", f"config/read failed: {exc!r}") from exc
        try:
            thread = await codex.thread_start_with_policy(
                model=plan.model,
                ephemeral=True,
                cwd=str(plan.cwd),
                sandbox=SandboxMode.read_only,
                approval_policy=AskForApproval.model_validate("never"),
                config=thread_config(names),
            )
        except Exception as exc:
            raise HermeticConfigError("start", f"thread/start failed: {exc!r}") from exc
        start = thread.start_response.model_dump(mode="json")
        check_codex_thread_start(start, model=plan.model, cwd=plan.cwd, codex_home=codex_home().path)
        yield HermeticCodexThread(thread, start, handler, names)
    finally:
        try:
            await codex.close()
        except Exception:
            logger.debug("codex.close() failed while unwinding a hermetic call", exc_info=True)


async def run_hermetic_codex(plan: HermeticCodexPlan, prompt: str, *, effort) -> HermeticCodexResult:
    async with hermetic_codex(plan) as thread:
        return await thread.run_turn(prompt, effort=effort)
