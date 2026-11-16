"""One Gemini CLI session driven over ACP (``gemini --acp``).

Lifecycle: :meth:`GeminiAgent.open` spawns the process, ``initialize``s and
opens the session (``session/new`` mints the canonical id, ``session/load``
resumes one). :meth:`start` / :meth:`send` run prompt turns in a background
task; the conversation itself reaches the UI through the session file and the
watcher, the ACP stream only feeds the live streaming blocks and the tool
approval requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import time
import uuid
from typing import Any, ClassVar

from twicc.agent.base_agent import BaseAgent, StateChangeCallback
from twicc.agent.exceptions import SendDeliveryError
from twicc.agent.states import AgentState, PendingRequest
from twicc.context_injection import INSTRUCTION_TAG_NAME, apply_pending_context, inject_context
from twicc.core.enums import Provider
from twicc.core.services.attachments.manifest import build_manifest
from twicc.core.services.attachments.types import AttachmentContent
from twicc.core.ui_messages import ui_message
from twicc.providers.helpers import AgentSettings

from ..acp import PROTOCOL_VERSION, AcpConnection, AcpConnectionClosed, AcpError
from ..constants import ACP_MODE_IDS

# Override of the ``gemini`` executable looked up on ``PATH``.
GEMINI_BIN_ENV = "TWICC_GEMINI_BIN"

# Option kinds of ``session/request_permission`` (ACP).
ALLOW_ONCE = "allow_once"
REJECT_KINDS = ("reject_once", "reject_always")

_STOP_TIMEOUT = 3.0


def resolve_gemini_bin() -> str | None:
    """The Gemini CLI executable: ``$TWICC_GEMINI_BIN`` or ``gemini`` on ``PATH``."""
    configured = os.environ.get(GEMINI_BIN_ENV, "").strip()
    if configured:
        return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
    return shutil.which("gemini")


# Name of TwiCC's MCP server in the Gemini session. The CLI names its tools
# ``twicc_<tool>`` (``mcp_`` prefix in policy contexts) and call ids
# ``<tool name>__call_<n>``.
TWICC_MCP_SERVER = "twicc"


def twicc_mcp_servers(session_id: str) -> list[dict]:
    """The ACP ``mcpServers`` entry for TwiCC's MCP endpoint (empty when MCP is off)."""
    from twicc.mcp import mcp_base_url, mcp_enabled
    from twicc.mcp.identity import mint_session_token

    if not mcp_enabled():
        return []
    return [{
        "type": "http",
        "name": TWICC_MCP_SERVER,
        "url": mcp_base_url(),
        "headers": [{"name": "Authorization", "value": f"Bearer {mint_session_token(session_id)}"}],
    }]


def is_twicc_mcp_call(tool_call_id: str) -> bool:
    """Whether a tool call targets TwiCC's own MCP server."""
    return tool_call_id.startswith((f"{TWICC_MCP_SERVER}_", f"mcp_{TWICC_MCP_SERVER}_"))


def _option_of_kind(options: list[dict], kind: str) -> str | None:
    for option in options:
        if option.get("kind") == kind:
            return option.get("optionId")
    return None


def _reject_option(options: list[dict]) -> str | None:
    for option in options:
        if option.get("kind") in REJECT_KINDS:
            return option.get("optionId")
    return None


class GeminiAgent(BaseAgent):
    """A live ``gemini --acp`` process bound to one session."""

    provider: ClassVar[Provider] = Provider.GEMINI

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        agent_settings: AgentSettings,
        *,
        model: str | None,
        trusted: bool,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings)
        self._model = model
        self._trusted = trusted
        self._connection = AcpConnection(on_notification=self._on_notification, on_request=self._on_request)
        self._turn_task: asyncio.Task | None = None
        self._loading = False
        self._applied_mode: str | None = None
        self._applied_model: str | None = model
        # Live streaming block: (message_id, block_type) or None.
        self._block: tuple[str, str] | None = None
        self._closing = False
        self._watch_task: asyncio.Task | None = None
        # The draft id a new session was created under (the canonical id comes
        # from ``session/new``): pending attributes and the MCP token use it.
        self._draft_id = session_id
        # TwiCC's system-prompt addendum, sent once with the first message of a
        # brand-new session (the CLI keeps it in the conversation it resumes).
        self._addendum: str | None = None
        # Committed composer attachments of a new session's first message.
        self._initial_content: AttachmentContent | None = None
        # Messages received during a turn: delivered one by one afterwards.
        self._queued_items: list[tuple[str, AttachmentContent | None]] = []
        self._background_tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Process
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        return self._connection.pid

    def _build_env(self) -> dict[str, str]:
        from twicc.provider_homes import provider_env_overlay
        from twicc.providers.helpers import get_provider_helpers_registry

        env = dict(os.environ)
        get_provider_helpers_registry().purge_env_vars(env)
        env.update(provider_env_overlay())
        if self._trusted:
            # TwiCC already holds the user's trust decision for this project;
            # without it the CLI applies its own folder-trust restrictions.
            env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
        return env

    async def open(self, *, resume: bool) -> None:
        """Spawn the CLI and open (or load) the session. Raises on failure."""
        binary = resolve_gemini_bin()
        if binary is None:
            raise SendDeliveryError(
                "Gemini CLI not found: install it or set TWICC_GEMINI_BIN",
                code="agent_start_failed",
                ui_message=ui_message("backend.gemini.cliNotFound"),
            )
        argv = [binary, "--acp"]
        if self._model:
            argv += ["--model", self._model]
        # The CLI only reads and writes inside its workspace: grant the
        # session's artifacts and scratch dirs (attachments live there). A new
        # session's id is minted by ``session/new``, after the spawn, so it
        # gets their parent directories instead.
        for directory in await self._workspace_directories(resume=resume):
            # Both spellings: the CLI compares paths without resolving links,
            # and TwiCC names files by either (a linked data dir in worktrees).
            for spelling in dict.fromkeys((directory, os.path.realpath(directory))):
                argv += ["--include-directories", spelling]
        await self._connection.start(argv, cwd=self.cwd, env=self._build_env())
        # TwiCC's MCP server. A brand-new session's token names the draft id;
        # the draft→canonical alias is registered once ``session/new`` returns.
        mcp_servers = twicc_mcp_servers(self.session_id)
        try:
            await self._connection.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {},
            })
            if resume:
                self._loading = True
                try:
                    await self._connection.request("session/load", {
                        "sessionId": self.session_id, "cwd": self.cwd, "mcpServers": mcp_servers,
                    })
                finally:
                    self._loading = False
            else:
                result = await self._connection.request(
                    "session/new", {"cwd": self.cwd, "mcpServers": mcp_servers},
                )
                session_id = result.get("sessionId") if isinstance(result, dict) else None
                if not isinstance(session_id, str) or not session_id:
                    raise AcpError("session/new returned no session id")
                self.session_id = session_id
            await self._apply_mode(self.agent_settings.permission_mode)
            await self._prepare_twicc_context(resume=resume)
        except AcpError as exc:
            await self._connection.close(timeout=1.0)
            if not self._trusted and "API key" in str(exc):
                # The CLI reads no ``.env`` file in a folder it does not trust.
                raise SendDeliveryError(
                    "Gemini CLI found no API key: in an untrusted project it reads no .env file; "
                    "trust the project or set GEMINI_API_KEY in TwiCC's environment",
                    code="agent_start_failed",
                    ui_message=ui_message("backend.gemini.apiKeyMissingUntrusted"),
                ) from exc
            raise
        except BaseException:
            await self._connection.close(timeout=1.0)
            raise
        self._watch_task = asyncio.create_task(self._watch_process(), name=f"gemini-watch-{self.session_id}")

    async def _workspace_directories(self, *, resume: bool) -> list[str]:
        if resume:
            return await self._resolve_and_create_work_dirs()
        from twicc.paths import get_artifacts_dir, get_scratch_dir

        directories = []
        for root in (get_artifacts_dir(), get_scratch_dir()):
            try:
                root.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            directories.append(str(root))
        return directories

    async def _prepare_twicc_context(self, *, resume: bool) -> None:
        """Work dirs, MCP identity, addendum and context baseline (as Claude Code / Codex do)."""
        from asgiref.sync import sync_to_async

        if resume:
            # Work dirs were resolved before the spawn (``_workspace_directories``).
            # The addendum is in the resumed conversation already; the whole
            # mutable Context is re-stated on the next message.
            self._reset_context_baseline()
            return
        draft_id = self._draft_id
        await self._resolve_and_create_work_dirs(pending_id=draft_id)
        if draft_id != self.session_id:
            from twicc.mcp.identity import register_draft_alias

            register_draft_alias(draft_id, self.session_id)
        # The addendum was composed before the CLI minted the id.
        inject_context(self.session_id, session_id=self.session_id)

        def _read_addendum() -> str | None:
            from twicc.pending_session_attributes import get_pending_session_attributes

            pending = get_pending_session_attributes(draft_id)
            return pending.system_prompt_addendum if pending else None

        self._addendum = await sync_to_async(_read_addendum)()
        await self._seed_context_baseline(pending_id=draft_id)

    async def _build_prompt(self, text: str, content: AttachmentContent | None) -> list[dict]:
        """The ACP prompt blocks of one user message.

        Order: the attachments manifest (where the ingestion looks for it,
        then stores and strips it), then the user text, with the folded
        ``<twicc:context>`` block (anchored at its start) and, on the first
        message of a new session, the TwiCC instructions in a
        ``<twicc:instruction>`` block. Both blocks are stripped from the stored
        copy by the ingestion. A slash command is sent untouched.
        """
        blocks: list[dict] = []
        if content is not None:
            if content.native_parts:
                raise SendDeliveryError("Inline attachments are not available for Gemini: attach them as files",
                                        code="attachments_unsupported",
                                        ui_message=ui_message("backend.gemini.attachmentsUnsupported"))
            blocks.append({"type": "text", "text": build_manifest(content.manifest)})
            text = content.user_text
        if text.lstrip().startswith("/") and content is None:
            blocks.append({"type": "text", "text": text})
            return blocks
        await self._reconcile_context()
        text = apply_pending_context(self.session_id, text)
        if self._addendum:
            instruction = f"<{INSTRUCTION_TAG_NAME}>\n{self._addendum.strip()}\n</{INSTRUCTION_TAG_NAME}>"
            head, separator, rest = text.partition("</twicc:context>")
            if separator:
                text = f"{head}{separator}\n\n{instruction}\n\n{rest.lstrip()}"
            else:
                text = f"{instruction}\n\n{text}"
            self._addendum = None
        if text:
            blocks.append({"type": "text", "text": text})
        return blocks

    async def _watch_process(self) -> None:
        """Turn an unexpected process exit into DEAD."""
        await self._connection.closed.wait()
        if self._closing or self.state == AgentState.DEAD:
            return
        tail = self._connection.stderr_tail().strip().splitlines()
        self.error = tail[-1] if tail else "Gemini CLI exited"
        self.kill_reason = "error"
        self._cancel_all_pending_futures()
        await self._transition_to_dead()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def _apply_mode(self, permission_mode: str | None) -> None:
        mode_id = ACP_MODE_IDS.get(permission_mode or "default")
        if mode_id is None or mode_id == self._applied_mode:
            return
        await self._connection.request("session/set_mode", {"sessionId": self.session_id, "modeId": mode_id})
        self._applied_mode = mode_id

    async def apply_agent_settings(self, settings: AgentSettings, *, model: str | None) -> None:
        """Apply a new settings bundle between turns (model and approval mode)."""
        self.agent_settings = settings
        if model and model != self._applied_model:
            await self._connection.request("session/set_model", {"sessionId": self.session_id, "modelId": model})
            self._applied_model = model
        await self._apply_mode(settings.permission_mode)

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    async def start(
        self, text: str, on_state_change: StateChangeCallback, resume: bool, *,
        content: AttachmentContent | None = None, **kwargs: Any,
    ) -> None:
        self._state_change_callback = on_state_change
        self._note_external_send()
        if self._initial_content is not None:
            content, self._initial_content = self._initial_content, None
        self._turn_task = asyncio.create_task(
            self._run_turns(text, content), name=f"gemini-turn-{self.session_id}",
        )

    async def send(self, text: str, *, content: AttachmentContent | None = None, **kwargs: Any) -> bool:
        if self.state == AgentState.DEAD:
            raise SendDeliveryError("Cannot send message: agent is dead", code="agent_dead",
                                    ui_message=ui_message("backend.gemini.agentDead"))
        self._note_external_send()
        if self._turn_task is not None and not self._turn_task.done():
            # Delivered right after the current turn, as its own prompt.
            self._queued_items.append((text, content))
            return True
        self._turn_task = asyncio.create_task(
            self._run_turns(text, content), name=f"gemini-turn-{self.session_id}",
        )
        return True

    async def _run_turns(self, text: str, content: AttachmentContent | None) -> None:
        while True:
            if not await self._run_one_turn(text, content):
                return
            if not self._queued_items:
                break
            text, content = self._queued_items.pop(0)
        self._set_state(AgentState.USER_TURN)
        await self._notify_state_change()

    async def _run_one_turn(self, text: str, content: AttachmentContent | None = None) -> bool:
        """Run one prompt. ``False`` when the agent died meanwhile."""
        self.last_activity = time.time()
        if self.state != AgentState.ASSISTANT_TURN:
            self._set_state(AgentState.ASSISTANT_TURN)
            await self._notify_state_change()
        try:
            prompt = await self._build_prompt(text, content)
            await self._connection.request("session/prompt", {
                "sessionId": self.session_id,
                "prompt": prompt,
            })
        except AcpConnectionClosed:
            # ``_watch_process`` (or ``interrupt_or_kill``) owns the DEAD transition.
            return False
        except AcpError as exc:
            # A failed turn (quota, network, invalid model) leaves the process
            # usable but the user must see why: report it and end the agent.
            await self._end_block()
            self.error = str(exc)
            self.kill_reason = "error"
            await self._shutdown_process()
            await self._transition_to_dead()
            return False
        finally:
            await self._end_block()
        self.last_activity = time.time()
        return self.state != AgentState.DEAD

    # ------------------------------------------------------------------
    # ACP agent → client traffic
    # ------------------------------------------------------------------

    async def _on_notification(self, method: str, params: dict) -> None:
        if method != "session/update" or self._loading:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            return
        self.last_activity = time.time()
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            content = update.get("content")
            text = content.get("text") if isinstance(content, dict) else None
            if isinstance(text, str) and text:
                await self._stream(text, "text" if kind == "agent_message_chunk" else "thinking")
        elif kind == "available_commands_update":
            commands = update.get("availableCommands")
            if isinstance(commands, list):
                task = asyncio.create_task(self._sync_commands(commands), name=f"gemini-commands-{self.session_id}")
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
        elif kind in ("tool_call", "tool_call_update"):
            # A tool call closes the current model output; the next chunks
            # belong to a new record.
            await self._end_block()

    async def _sync_commands(self, commands: list) -> None:
        from ..commands import sync_project_commands

        try:
            await sync_project_commands(self.project_id, commands)
        except Exception:
            self._logger.warning("Gemini command sync failed for session %s", self.session_id, exc_info=True)

    async def _on_request(self, method: str, params: dict) -> Any:
        if method == "session/request_permission":
            return await self._request_permission(params)
        raise AcpError(f"Method not supported by TwiCC: {method}", code=-32601)

    async def _request_permission(self, params: dict) -> dict:
        await self._end_block()
        tool_call = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        options = [option for option in params.get("options") or [] if isinstance(option, dict)]
        locations = [
            location.get("path") for location in tool_call.get("locations") or []
            if isinstance(location, dict) and isinstance(location.get("path"), str)
        ]
        request = PendingRequest(
            request_id=str(tool_call.get("toolCallId") or uuid.uuid4()),
            request_type="tool_approval",
            tool_name=str(tool_call.get("kind") or "other"),
            tool_input={
                "title": tool_call.get("title") or "",
                "kind": tool_call.get("kind") or "other",
                "locations": locations,
                "content": tool_call.get("content") or [],
                "options": [
                    {"optionId": option.get("optionId"), "name": option.get("name"), "kind": option.get("kind")}
                    for option in options
                ],
            },
            created_at=time.time(),
        )
        reject = _reject_option(options)
        allow_once = _option_of_kind(options, ALLOW_ONCE)
        # TwiCC's MCP server is a control plane, orthogonal to the approval
        # mode (as for Claude Code and Codex): its tools never ask.
        if allow_once is not None and is_twicc_mcp_call(request.request_id):
            return {"outcome": {"outcome": "selected", "optionId": allow_once}}
        # Writes confined to the session's own artifacts / scratch dirs pass
        # without asking, like for Claude Code and Codex (trusted projects only).
        if (
            allow_once is not None and self._trusted and locations
            and tool_call.get("kind") in ("edit", "delete", "move", "read")
            and self._targets_only_work_dirs(locations, True)
        ):
            return {"outcome": {"outcome": "selected", "optionId": allow_once}}
        try:
            response = await self._await_pending_request(
                request, auto_deny_response={"option_id": reject} if reject else None,
            )
        except asyncio.CancelledError:
            return {"outcome": {"outcome": "cancelled"}}
        option_id = response.get("option_id") if isinstance(response, dict) else None
        if option_id not in {option.get("optionId") for option in options}:
            option_id = reject
        if option_id is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option_id}}

    # ------------------------------------------------------------------
    # Streaming blocks (same wire as the other providers)
    # ------------------------------------------------------------------

    async def _stream(self, text: str, block_type: str) -> None:
        if self._block is not None and self._block[1] != block_type:
            await self._end_block()
        if self._block is None:
            self._block = (f"gemini-{uuid.uuid4()}", block_type)
            await self._broadcast_stream_event({
                "type": "stream_block_start", "session_id": self.session_id,
                "message_id": self._block[0], "block_index": 0, "block_type": block_type,
            })
        await self._broadcast_stream_event({
            "type": "stream_block_delta", "session_id": self.session_id,
            "message_id": self._block[0], "block_index": 0, "block_type": block_type, "text": text,
        })

    async def _end_block(self) -> None:
        block, self._block = self._block, None
        if block is None:
            return
        message_id, block_type = block
        with contextlib.suppress(Exception):
            await self._broadcast_stream_event({
                "type": "stream_block_stop", "session_id": self.session_id,
                "message_id": message_id, "block_index": 0, "block_type": block_type,
            })
            # The persisted record id is not known from the stream: the
            # frontend matches the final item by content.
            await self._broadcast_stream_event({
                "type": "stream_block_end", "session_id": self.session_id,
                "message_id": message_id, "block_index": 0, "block_type": block_type, "uuid": None,
            })

    # ------------------------------------------------------------------
    # Stopping
    # ------------------------------------------------------------------

    async def soft_interrupt(self) -> bool:
        if self.state != AgentState.ASSISTANT_TURN:
            return False
        self._queued_items.clear()
        self._cancel_all_pending_futures()
        with contextlib.suppress(AcpConnectionClosed):
            await self._connection.notify("session/cancel", {"sessionId": self.session_id})
        return True

    async def _shutdown_process(self) -> None:
        self._closing = True
        pid = self.get_pid()
        try:
            await asyncio.wait_for(self._connection.close(timeout=_STOP_TIMEOUT), timeout=_STOP_TIMEOUT * 3)
        except TimeoutError:
            pass
        if pid is not None and self._connection.returncode is None:
            await self._kill_system_process(pid)

    async def interrupt_or_kill(self, reason: str) -> None:
        if self.state == AgentState.DEAD:
            return
        self.kill_reason = reason
        self._queued_items.clear()
        self._cancel_all_pending_futures()
        if self.state == AgentState.ASSISTANT_TURN and not self._force_kill.is_set():
            with contextlib.suppress(Exception):
                await self._connection.notify("session/cancel", {"sessionId": self.session_id})
        await self._end_block()
        await self._shutdown_process()
        current = asyncio.current_task()
        for task in (self._turn_task, self._watch_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if self.state != AgentState.DEAD:
            await self._transition_to_dead()
