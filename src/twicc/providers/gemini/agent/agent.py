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
from twicc.core.enums import Provider
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
        self._queued: list[str] = []
        self._loading = False
        self._applied_mode: str | None = None
        self._applied_model: str | None = model
        # Live streaming block: (message_id, block_type) or None.
        self._block: tuple[str, str] | None = None
        self._closing = False
        self._watch_task: asyncio.Task | None = None

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
        await self._connection.start(argv, cwd=self.cwd, env=self._build_env())
        try:
            await self._connection.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {},
            })
            if resume:
                self._loading = True
                try:
                    await self._connection.request("session/load", {
                        "sessionId": self.session_id, "cwd": self.cwd, "mcpServers": [],
                    })
                finally:
                    self._loading = False
            else:
                result = await self._connection.request("session/new", {"cwd": self.cwd, "mcpServers": []})
                session_id = result.get("sessionId") if isinstance(result, dict) else None
                if not isinstance(session_id, str) or not session_id:
                    raise AcpError("session/new returned no session id")
                self.session_id = session_id
            await self._apply_mode(self.agent_settings.permission_mode)
        except BaseException:
            await self._connection.close(timeout=1.0)
            raise
        self._watch_task = asyncio.create_task(self._watch_process(), name=f"gemini-watch-{self.session_id}")

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

    async def start(self, text: str, on_state_change: StateChangeCallback, resume: bool, **kwargs: Any) -> None:
        self._state_change_callback = on_state_change
        self._note_external_send()
        self._turn_task = asyncio.create_task(self._run_turns(text), name=f"gemini-turn-{self.session_id}")

    async def send(self, text: str, **kwargs: Any) -> bool:
        if self.state == AgentState.DEAD:
            raise SendDeliveryError("Cannot send message: agent is dead", code="agent_dead",
                                    ui_message=ui_message("backend.gemini.agentDead"))
        self._note_external_send()
        if self._turn_task is not None and not self._turn_task.done():
            # Delivered right after the current turn, as its own prompt.
            self._queued.append(text)
            return True
        self._turn_task = asyncio.create_task(self._run_turns(text), name=f"gemini-turn-{self.session_id}")
        return True

    async def _run_turns(self, text: str) -> None:
        while True:
            if not await self._run_one_turn(text):
                return
            if not self._queued:
                break
            text = "\n\n".join(self._queued)
            self._queued.clear()
        self._set_state(AgentState.USER_TURN)
        await self._notify_state_change()

    async def _run_one_turn(self, text: str) -> bool:
        """Run one prompt. ``False`` when the agent died meanwhile."""
        self.last_activity = time.time()
        if self.state != AgentState.ASSISTANT_TURN:
            self._set_state(AgentState.ASSISTANT_TURN)
            await self._notify_state_change()
        try:
            await self._connection.request("session/prompt", {
                "sessionId": self.session_id,
                "prompt": [{"type": "text", "text": text}],
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
        elif kind in ("tool_call", "tool_call_update"):
            # A tool call closes the current model output; the next chunks
            # belong to a new record.
            await self._end_block()

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
        self._queued.clear()
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
        self._queued.clear()
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
