"""Hybrid Claude Code agent: drives the interactive CLI (TUI) in tmux.

Implements the :class:`BaseAgent` contract without an SDK message loop.
State transitions are pushed from the outside:

- the sessions watcher's JSONL bridge calls :meth:`on_jsonl_user_message` /
  :meth:`on_jsonl_progress` / :meth:`on_jsonl_turn_end` as lines land;
- the hybrid hooks watcher delivers the single injected ``PermissionRequest``
  hook via :meth:`on_permission_request`;
- a light liveness monitor polls the tmux pane and transitions to DEAD when
  claude exits.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import replace
from datetime import datetime, UTC
from typing import Any

from asgiref.sync import sync_to_async
from claude_agent_sdk import PermissionResultAllow, PermissionUpdate

from twicc.agent.base_agent import BaseAgent, StateChangeCallback
from twicc.agent.exceptions import SendDeliveryError
from twicc.agent.states import AgentInfo, AgentState, PendingRequest
from twicc.core.enums import Provider
from twicc.paths import get_session_hybrid_dir
from twicc.providers.claude_code.agent.permissions import (
    extract_claude_tool_paths,
    maybe_update_plan_file,
    order_suggestion_fields,
    serialize_and_filter_suggestions,
)

from . import tmux as hybrid_tmux
from .launch import HYBRID_HOOK_TIMEOUT_SECONDS, build_argv, write_addendum_file
from .responses import (
    DUMMY_REAP_STATUS,
    drop_path_for,
    status_path_for,
    to_hook_output,
    write_status,
)

logger = logging.getLogger(__name__)

# The trust dialog swallows pasted text entirely (verified empirically:
# pasting while it is up leaves the composer empty after the dialog is
# answered). The first paste therefore waits until no trust dialog is on
# screen. The pattern matches both the question and the confirm option of
# the current dialog wording, with older variants covered by "do you trust".
_TRUST_DIALOG_RE = re.compile(r"trust this folder|do you trust", re.IGNORECASE)


class HybridClaudeAgent(BaseAgent):
    """Claude Code session driven by the interactive CLI in tmux."""

    provider = Provider.CLAUDE_CODE
    # Discriminator used by the manager / WS layer to branch hybrid-specific
    # behavior without isinstance checks across modules.
    is_hybrid = True

    # First-paste readiness: poll until the TUI shows its empty composer
    # (tmux.composer_ready). Covers the warm-up (nothing drawn yet) and the
    # trust dialog (which swallows pastes — verified; it should not appear
    # in real TwiCC flows, where project trust is settled in the CLI's global
    # config file (~/.claude.json or its configured-home equivalent)
    # before launch). The bounded wait can be long: the user answers the
    # dialog inside the embedded terminal.
    READY_POLL_INTERVAL = 1.0
    READY_TIMEOUT = 600.0
    LIVENESS_INTERVAL = 5.0
    # While the terminal is flagged blocked, poll the composer at this cadence
    # to clear the flag as soon as it frees up (the user resolved the dialog /
    # cleared their TUI draft) without waiting for the next paste attempt.
    BLOCK_POLL_INTERVAL = 2.5

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        agent_settings: Any,
    ) -> None:
        super().__init__(session_id, project_id, cwd, agent_settings)
        self.agent_pid: int | None = None
        self._untrusted = False
        self._first_paste_task: asyncio.Task[None] | None = None
        self._liveness_task: asyncio.Task[None] | None = None
        # Background retry tasks for auto-pasted slash commands, keyed by
        # purpose ("rename", "settings") — a new task replaces (cancels) the
        # previous one of the same purpose, so only the latest title/settings
        # value is ever pasted.
        self._auto_paste_tasks: dict[str, asyncio.Task[None]] = {}
        # Serializes ready-check + paste pairs: two concurrent pastes would
        # race on the shared tmux buffer and on the composer-free check.
        self._paste_lock = asyncio.Lock()
        # GUI-answer expiry timers, one per registered pending request
        # (keyed by hook nonce): when the CLI-side hook timeout reaps the
        # polling hook, the widget degrades to the badge-only marker.
        self._gui_expiry_tasks: dict[str, asyncio.Task[None]] = {}
        # Read by ClaudeCodeAgentManager._on_state_change for every Claude
        # Code agent (old-ProcessRun purge at first USER_TURN).
        self._old_runs_purged = False
        # True while the current ASSISTANT_TURN was started by a local
        # slash-command echo (not a real prompt): only then does the
        # command's <local-command-stdout> ack end the turn. Mid-real-turn
        # acks (e.g. the auto /rename's) are ignored — one once reaped a
        # live permission prompt, denying it behind the user's back.
        self._local_cmd_turn = False
        # True when TwiCC tried to paste (a user send or an auto command) and
        # the composer wasn't free — a TUI dialog is open or the user has text
        # typed in the terminal. Surfaced verbatim in get_info().extra so any
        # client (live, late-opened, reloaded) can steer the user to the
        # terminal. A poll clears it as soon as the composer frees up.
        self._terminal_blocked = False
        self._block_poll_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Manager-contract shims (SDK-agent methods reached on every Claude
    # Code agent regardless of flavor)
    # ------------------------------------------------------------------

    def get_pid(self) -> int | None:
        return self.agent_pid

    def get_info(self) -> AgentInfo:
        # Attach the hybrid-specific live state bag (broadcast verbatim to the
        # front, live and in the initial snapshot). The flag is raw — the
        # front decides whether/when to surface it.
        return super().get_info()._replace(
            extra={"mode": "hybrid", "terminal_blocked": self._terminal_blocked},
        )

    def get_expired_recurring_crons(self) -> list:
        # Crons on hybrid sessions are out of scope (V1): the manager's cron
        # expiry monitor polls every USER_TURN agent, so answer "none".
        return []

    async def discard_active_tool(self, tool_use_id: str) -> bool:
        # No live active-tools feed in hybrid mode (no PreToolUse hook in V1).
        return False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        text: str,
        on_state_change: StateChangeCallback,
        resume: bool = True,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
        adopt: bool = False,
    ) -> None:
        """Create the tmux session and schedule the first paste.

        IMPORTANT: this is awaited under the MANAGER-WIDE lock
        (``_register_and_start``) — it must return fast. Only the tmux
        creation happens inline; the first paste (with its TUI warm-up
        delay and possible trust-dialog wait) runs in a background task.

        ``adopt=True`` attaches to an ALREADY-RUNNING tmux session that
        survived a TwiCC restart: nothing is created and nothing is pasted —
        the agent simply binds to the live pane, reports USER_TURN (the
        JSONL bridge corrects it on the next ingested lines) and starts the
        liveness monitor. ``text`` is ignored in that case.
        """
        self._state_change_callback = on_state_change

        if adopt:
            self.agent_pid, _ = await asyncio.to_thread(
                hybrid_tmux.pane_status, self.session_id,
            )
            logger.info(
                "Adopted surviving hybrid CLI for session %s (pid=%s)",
                self.session_id, self.agent_pid,
            )
            # The CLI keeps its launch-time provider homes until it exits; a
            # .env change since then would make it write elsewhere. Warn.
            if self.agent_pid is not None:
                from twicc.provider_homes import provider_env_overlay, provider_home_mismatches

                env = await asyncio.to_thread(hybrid_tmux.read_process_environ, self.agent_pid)
                if env is not None:
                    overlay = provider_env_overlay()
                    for key in provider_home_mismatches(env):
                        logger.warning(
                            "Adopted hybrid CLI for session %s (pid=%s) runs with %s=%r, "
                            "this instance is configured with %r — it keeps its launch-time "
                            "home until it exits",
                            self.session_id, self.agent_pid, key, env.get(key), overlay.get(key),
                        )
            self._set_state(AgentState.USER_TURN)
            await self._notify_state_change()
            self._start_liveness_monitor()
            return

        # The JSONL appears only when the first message is submitted; make
        # sure the watcher picks it up quickly (same as the SDK agent).
        from twicc.providers.claude_code.sessions_watcher import get_watcher
        get_watcher().request_fast_poll()

        # Trust clamp (security floor, trust design §13.4) — identical to the
        # SDK agent: resolve the project's effective trust and clamp the
        # permission mode before it reaches the CLI flags.
        def _resolve_trust_clamp() -> tuple[bool, str]:
            from twicc.core.services.trust import (
                clamp_permission_mode_for_untrusted,
                project_is_untrusted,
            )

            untrusted = project_is_untrusted(self.project_id)
            mode = self.agent_settings.permission_mode
            if untrusted:
                mode = clamp_permission_mode_for_untrusted(Provider.CLAUDE_CODE, mode)
            return untrusted, mode

        self._untrusted, clamped_mode = await sync_to_async(_resolve_trust_clamp)()
        if clamped_mode != self.agent_settings.permission_mode:
            self.agent_settings = self.agent_settings._replace(permission_mode=clamped_mode)

        addendum = await sync_to_async(self._read_system_prompt_addendum)()
        addendum_path = await asyncio.to_thread(
            write_addendum_file, self.session_id, addendum,
        )
        temp_title = await self._resolve_temp_title(text)
        # Same work dirs as the SDK agent (artifacts/scratch, shared
        # orchestration scratch) + the hybrid dir (attachments) — all granted
        # prompt-free via --add-dir.
        work_dirs = await self._resolve_and_create_work_dirs()
        hybrid_dir = await asyncio.to_thread(get_session_hybrid_dir, self.session_id)
        argv = build_argv(
            session_id=self.session_id,
            settings=self.agent_settings,
            resume=resume,
            temp_title=temp_title,
            addendum_path=addendum_path,
            add_dirs=[*work_dirs, str(hybrid_dir)],
            untrusted=self._untrusted,
        )

        def _create() -> tuple[int | None, bool]:
            # A leftover tmux session here can only hold a DEAD pane (live
            # ones are adopted at boot, not restarted): clear it so
            # new-session does not fail on the name collision.
            if hybrid_tmux.session_exists(self.session_id):
                hybrid_tmux.kill_session(self.session_id)
            hybrid_tmux.create_session(self.session_id, self.cwd, argv)
            return hybrid_tmux.pane_status(self.session_id)

        self.agent_pid, _ = await asyncio.to_thread(_create)
        logger.info(
            "Hybrid CLI launched for session %s (pid=%s, resume=%s)",
            self.session_id, self.agent_pid, resume,
        )
        self._first_paste_task = asyncio.create_task(
            self._first_paste(text, images, documents),
            name=f"hybrid-first-paste-{self.session_id}",
        )
        self._start_liveness_monitor()

    async def _first_paste(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> None:
        """Background task: wait for the TUI (and any trust dialog), then paste."""
        try:
            full_text = await self._materialize_attachments(text, images, documents)
            await self._wait_until_composer_ready()
            if self.state == AgentState.DEAD:
                return
            await asyncio.to_thread(hybrid_tmux.paste_text, self.session_id, full_text)
            if self.state == AgentState.DEAD:
                return
            # Optimistic transition; the JSONL bridge corrects it within ms
            # if the submit did not take.
            self._set_state(AgentState.ASSISTANT_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(
                "First paste failed for hybrid session %s", self.session_id,
            )
            if self.state != AgentState.DEAD:
                self.error = f"First paste failed: {e}"
                self.kill_reason = "startup-failed"
                await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
                await self._transition_to_dead()

    async def _wait_until_composer_ready(self) -> None:
        """Block until the TUI shows its empty composer (bounded wait).

        ``composer_ready`` is the single readiness signal: it stays False
        through the warm-up render, the trust dialog (which swallows pasted
        text — verified) and any other blocking first screen, and flips
        True exactly when a paste can land. On timeout we paste anyway
        (worst case the paste is lost and the user re-sends — better than
        text silently never arriving while the agent looks alive forever).
        """
        deadline = time.monotonic() + self.READY_TIMEOUT
        trust_logged = False
        while time.monotonic() < deadline:
            if self.state == AgentState.DEAD:
                return
            if await asyncio.to_thread(hybrid_tmux.composer_ready, self.session_id):
                return
            if not trust_logged:
                screen = await asyncio.to_thread(
                    hybrid_tmux.capture_pane, self.session_id,
                )
                if screen and _TRUST_DIALOG_RE.search(screen):
                    trust_logged = True
                    logger.info(
                        "Trust dialog detected for hybrid session %s — waiting "
                        "for the user to answer it in the terminal",
                        self.session_id,
                    )
            await asyncio.sleep(self.READY_POLL_INTERVAL)
        logger.warning(
            "TUI composer still not ready after %.0fs for hybrid session %s — "
            "pasting anyway",
            self.READY_TIMEOUT, self.session_id,
        )

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> bool:
        # Pre-paste guard: with a TUI dialog open the paste is swallowed and
        # its trailing Enter would VALIDATE the highlighted option (verified
        # empirically); with text already typed in the TUI composer it would
        # append to and submit the user's draft. Fail fast instead — the
        # frontend restores the message into the TwiCC composer.
        full_text = await self._materialize_attachments(text, images, documents)
        if not await self._checked_paste(full_text):
            raise SendDeliveryError(
                "The Claude CLI is showing a dialog or has text typed in its "
                "composer — resolve it in the Claude CLI terminal, then "
                "send again.",
                code="hybrid_composer_busy",
            )
        self.last_activity = time.time()
        return True

    async def _checked_paste(self, text: str) -> bool:
        """Atomically verify the composer is free, then paste.

        The lock serializes user sends and auto-pasted commands, so two
        pastes never race on the shared tmux paste buffer and the
        composer-free check is still valid when the paste lands.
        """
        async with self._paste_lock:
            ready = await asyncio.to_thread(
                hybrid_tmux.composer_ready, self.session_id,
            )
            if ready:
                await asyncio.to_thread(
                    hybrid_tmux.paste_text, self.session_id, text,
                )
        # Update + broadcast the blocked flag OUTSIDE the lock (broadcasts are
        # never held under a lock): not-ready means the composer was busy.
        await self._set_terminal_blocked(not ready)
        return ready

    # ── Terminal-blocked live state ──────────────────────────────────────────
    async def _set_terminal_blocked(self, blocked: bool) -> None:
        """Update the blocked flag, manage the unblock poll, and broadcast.

        No-op when unchanged. Going blocked starts a poll that clears the flag
        as soon as the composer frees up; going free cancels it. Broadcasts the
        fresh ``get_info().extra`` so every client (live or late) sees it.
        """
        if self._terminal_blocked == blocked:
            return
        self._terminal_blocked = blocked
        if blocked:
            self._start_block_poll()
        else:
            self._cancel_block_poll()
        await self._notify_state_change()

    def _start_block_poll(self) -> None:
        if self._block_poll_task and not self._block_poll_task.done():
            return
        self._block_poll_task = asyncio.create_task(
            self._poll_until_unblocked(), name=f"hybrid-block-poll-{self.session_id}",
        )

    def _cancel_block_poll(self) -> None:
        task = self._block_poll_task
        self._block_poll_task = None
        # Never cancel from inside the poll task itself (it returns on its own).
        if task and task is not asyncio.current_task():
            task.cancel()

    async def _poll_until_unblocked(self) -> None:
        """While blocked, watch the composer and clear the flag when it frees."""
        try:
            while self._terminal_blocked and self.state != AgentState.DEAD:
                await asyncio.sleep(self.BLOCK_POLL_INTERVAL)
                if self.state == AgentState.DEAD:
                    return
                if await asyncio.to_thread(hybrid_tmux.composer_ready, self.session_id):
                    self._terminal_blocked = False
                    self._block_poll_task = None
                    await self._notify_state_change()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Terminal-blocked poll failed for hybrid session %s", self.session_id,
            )

    async def _materialize_attachments(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> str:
        """Save attachments to the hybrid dir and prepend ``@path`` mentions."""
        if not images and not documents:
            return text

        import mimetypes
        import secrets

        def _write_all() -> list[str]:
            hybrid_dir = get_session_hybrid_dir(self.session_id)
            paths: list[str] = []
            for block in [*(images or []), *(documents or [])]:
                source = block.get("source") or {}
                if source.get("type") != "base64" or not source.get("data"):
                    logger.warning(
                        "Skipping non-base64 attachment for hybrid session %s",
                        self.session_id,
                    )
                    continue
                import base64

                media_type = source.get("media_type") or "application/octet-stream"
                ext = mimetypes.guess_extension(media_type) or ".bin"
                # Randomized name on purpose (verified pitfall: the model can
                # answer from a meaningful filename without reading the file).
                path = hybrid_dir / f"att_{secrets.token_hex(6)}{ext}"
                path.write_bytes(base64.b64decode(source["data"]))
                paths.append(str(path))
            return paths

        paths = await asyncio.to_thread(_write_all)
        if not paths:
            return text
        mentions = "\n".join(f"@{p}" for p in paths)
        return f"{mentions}\n{text}"

    # ------------------------------------------------------------------
    # Signal-driven transitions (hooks watcher + JSONL bridge, via manager)
    # ------------------------------------------------------------------

    async def on_permission_request(self, payload: dict, nonce: str) -> bool:
        """The injected hook fired: a TUI prompt is up — register a real,
        GUI-answerable pending request keyed on the hook's nonce.

        Returns ``True`` when registered (the hooks watcher leaves the drop
        file to this agent — deleted at resolution/clear/death), ``False``
        when declined (a stale drop re-fed after a TwiCC restart whose prompt
        was already answered — the watcher deletes it).
        """
        if nonce in self._pending_requests:
            # Idempotent boot re-feed: already registered, drop stays owned.
            return True
        if await self._is_stale_drop(nonce):
            logger.info(
                "Stale PermissionRequest drop for hybrid session %s "
                "(transcript moved after the hook fired) — not registered",
                self.session_id,
            )
            return False
        tool_name = payload.get("tool_name") or ""

        # TwiCC MCP tools are a control plane (drive sessions, not the project's
        # code); auto-approve them silently in every mode, matching the SDK path's
        # can_use_tool short-circuit so D9 (auto-approve on both providers) holds
        # for hybrid too. Fires only if the CLI actually prompts for the tool.
        if tool_name.startswith("mcp__twicc__"):
            await self._write_allow_status(nonce)
            return False

        # System work-dir auto-approval: answer silently (no GUI card) when the
        # request targets ONLY this session's own artifacts/scratch dirs (and the
        # orchestration root's shared scratch). Disabled in untrusted projects
        # (trust stays human-only); never for AskUserQuestion. Containment is
        # checked first so the trust DB read only happens for an in-scope request.
        if tool_name != "AskUserQuestion":
            paths, fully_known = extract_claude_tool_paths(
                tool_name, payload.get("tool_input") or {}, payload.get("blocked_path"),
            )
            if self._targets_only_work_dirs(paths, fully_known):
                from twicc.core.services.trust import project_is_untrusted

                if not await sync_to_async(project_is_untrusted)(self.project_id):
                    logger.info(
                        "Auto-approving hybrid %s for session %s — targets only "
                        "system work dirs", tool_name, self.session_id,
                    )
                    await self._write_allow_status(nonce)
                    # HANDLED: the status file drives the CLI hook and the watcher
                    # deletes the drop; no pending is registered (no GUI card).
                    return False

        self._pending_requests[nonce] = PendingRequest(
            request_id=nonce,
            request_type="ask_user_question" if tool_name == "AskUserQuestion" else "tool_approval",
            tool_name=tool_name,
            tool_input=payload.get("tool_input") or {},
            created_at=time.time(),
            # Hook-native suggestions only, through the shared filter (NO
            # SDK-style setMode mode-picker injection, NO synthesized
            # suggestions — design doc 2026-06-12 §3).
            permission_suggestions=order_suggestion_fields(
                serialize_and_filter_suggestions(payload.get("permission_suggestions"), self.cwd)
            ),
        )
        self._schedule_gui_expiry(nonce)
        self.last_activity = time.time()
        await self._notify_state_change()
        return True

    async def _write_allow_status(self, nonce: str) -> None:
        """Write an ``allow`` status for a silent auto-approval (work dirs, MCP).

        No pending is registered and no GUI card is shown: the CLI's still-polling
        hook reads this ``.status.json`` and proceeds, exactly as it would for a
        human GUI answer (mirrors ``_finalize_gui_answer`` minus the pending
        bookkeeping). The caller returns ``False`` (HANDLED) so the hooks watcher
        deletes the drop file.
        """
        status = status_path_for(self.session_id, nonce)
        await asyncio.to_thread(
            write_status, status, to_hook_output(PermissionResultAllow()),
        )
        self.last_activity = time.time()

    async def _is_stale_drop(self, nonce: str) -> bool:
        """A re-fed drop is stale when the transcript moved after the fire.

        While a permission prompt is pending the CLI writes nothing to the
        JSONL (verified — that is why the hook exists), so ANY item whose
        embedded message timestamp postdates the hook fire proves the prompt
        was resolved. The nonce embeds the fire wall-clock
        (``<pid>-<nanoseconds>``, same machine as the CLI's timestamps).
        """
        try:
            fired_ns = int(nonce.rsplit("-", 1)[-1])
            fired_at = datetime.fromtimestamp(fired_ns / 1e9, tz=UTC)
        except (ValueError, OverflowError, OSError):
            # Unparseable nonce: register rather than silently drop.
            return False

        @sync_to_async
        def _transcript_moved() -> bool:
            from twicc.core.models import SessionItem

            return SessionItem.objects.filter(
                session_id=self.session_id, timestamp__gt=fired_at,
            ).exists()

        return await _transcript_moved()

    async def on_jsonl_user_message(self) -> None:
        """A real user prompt landed in the JSONL → a turn is running."""
        self._local_cmd_turn = False
        self._clear_all_pendings("reap")
        if self.state != AgentState.ASSISTANT_TURN:
            self._set_state(AgentState.ASSISTANT_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    async def on_jsonl_command_message(self) -> None:
        """A local slash-command echo landed (its stdout ack will follow).

        Outside a turn, show the command as activity (spinner) and remember
        that the ack — not a turn_duration — will end it. During a real
        assistant turn the command executed inline: state, pendings and the
        ack-tracking flag are all left untouched.
        """
        self.last_activity = time.time()
        if self.state == AgentState.ASSISTANT_TURN:
            return
        self._local_cmd_turn = True
        self._set_state(AgentState.ASSISTANT_TURN)
        await self._notify_state_change()

    async def on_jsonl_local_command_ack(self) -> None:
        """A ``<local-command-stdout>`` ack landed.

        Ends the command-started "turn" it belongs to. Any other ack —
        typically one landing mid real turn (auto-pasted /rename, a command
        typed in the TUI while Claude works) — is plain noise: ending the
        turn there once reaped a live permission prompt (the dummy reap
        status denied the approval behind the user's back).
        """
        self.last_activity = time.time()
        if not self._local_cmd_turn:
            return
        self._local_cmd_turn = False
        if self.state != AgentState.USER_TURN:
            self._set_state(AgentState.USER_TURN)
        await self._notify_state_change()

    async def on_jsonl_progress(self) -> None:
        """New tool_result lines landed.

        The PermissionRequest payload carries no tool_use_id (verified), so
        clearing is unconditional: any tool_result written AFTER a pending
        was registered proves the prompt was answered (approve → result,
        deny → error result).
        """
        self.last_activity = time.time()
        if self._pending_requests:
            self._clear_all_pendings("reap")
            await self._notify_state_change()

    async def on_jsonl_turn_end(self) -> None:
        """A turn ended: ``turn_duration`` line, interruption marker, or
        compaction."""
        self._local_cmd_turn = False
        self._clear_all_pendings("reap")
        if self.state != AgentState.USER_TURN:
            self._set_state(AgentState.USER_TURN)
        self.last_activity = time.time()
        await self._notify_state_change()

    # ------------------------------------------------------------------
    # GUI answer delivery (manager → resolve_pending_request → status file)
    # ------------------------------------------------------------------

    def resolve_pending_request(self, request_id: str, response: Any) -> bool:
        """Deliver a GUI answer by writing the hook's ``.status.json``.

        Overrides the Future-based SDK contract: hybrid pendings have no
        awaiter — the polling hook is the consumer. The base contract is
        sync, so the file I/O finishes in a task; the pending is popped
        immediately (the next ``process_state`` broadcast clears the widget,
        same as the SDK path).
        """
        pending = self._pending_requests.pop(request_id, None)
        if pending is None:
            logger.warning(
                "[session %s] resolve_pending_request: no hybrid pending for "
                "request_id=%s (already cleared?)",
                self.session_id, request_id,
            )
            return False
        self._cancel_gui_expiry(request_id)
        if not self._pending_requests:
            # Same stamp as the SDK path: the time spent waiting on the user
            # must not count against the turn timeouts.
            self.last_pending_resolved_at = time.time()
        # A session-destination setMode rides back to the CLI, which applies
        # it itself ("Allowed by PermissionRequest hook" + mode switch) while
        # ws.py persists it on the Session row. Mirror it into the in-memory
        # snapshot too: permission_mode is a STARTUP setting for hybrid, so a
        # stale snapshot would make the settings monitor read the DB change
        # as pending and kill the CLI right after the answer (observed in the
        # E2E sweep).
        if isinstance(response, PermissionResultAllow):
            for perm in response.updated_permissions or ():
                p = perm.to_dict() if isinstance(perm, PermissionUpdate) else perm
                if p.get("type") == "setMode" and p.get("mode") and p.get("destination") in (None, "session"):
                    self.agent_settings = self.agent_settings._replace(permission_mode=p["mode"])
        asyncio.create_task(
            self._finalize_gui_answer(pending, response),
            name=f"hybrid-gui-answer-{self.session_id}",
        )
        return True

    async def _finalize_gui_answer(self, pending: PendingRequest, response: Any) -> None:
        try:
            if pending.tool_name == "ExitPlanMode" and isinstance(response, PermissionResultAllow):
                # Shared brick: persist a user-edited plan BEFORE the CLI
                # proceeds (``planFilePath`` rides in the hook payload's
                # tool_input; no slug fallback needed in hybrid).
                await maybe_update_plan_file(pending.tool_input, response.updated_input)
            status = status_path_for(self.session_id, pending.request_id)
            await asyncio.to_thread(write_status, status, to_hook_output(response))
            await asyncio.to_thread(
                drop_path_for(self.session_id, pending.request_id).unlink, missing_ok=True,
            )
        except Exception:
            # The pending is already popped: the widget is gone but the TUI
            # dialog is still up and answerable — log loudly and move on.
            logger.exception(
                "[session %s] failed to deliver the GUI answer for request %s",
                self.session_id, pending.request_id,
            )
        self.last_activity = time.time()
        await self._notify_state_change()

    # ------------------------------------------------------------------
    # Pending clearing janitor + GUI-channel expiry
    # ------------------------------------------------------------------

    def _clear_all_pendings(self, mode: str) -> None:
        """Clear every registered pending and clean its files.

        ``mode`` picks the file janitor:
        - ``"reap"`` — the prompt was resolved inside the TUI: write a dummy
          status so the still-polling hook consumes it and exits (the CLI
          provably ignores late hook output), delete the drop, then
          delayed-delete the dummy for the hook-already-dead case (TUI Esc
          kills the hook before it can consume anything).
        - ``"delete"`` — the CLI (and its hooks) are dead: delete both files.
        - ``"detach"`` — TwiCC shutdown: the tmux kill is best-effort, so the
          CLI MAY survive with a still-pending prompt; keep the drop on disk
          (boot re-feeds a survivor) and the hook polling (GUI answering
          resumes after restart). A killed session's leftover files are swept
          as orphans at the next boot.
        """
        if not self._pending_requests:
            return
        cleared = list(self._pending_requests.values())
        self._pending_requests.clear()
        for task in self._gui_expiry_tasks.values():
            task.cancel()
        self._gui_expiry_tasks.clear()
        # Same stamp as _await_pending_request's finally: the time spent
        # waiting on the user must not count against the turn timeouts.
        self.last_pending_resolved_at = time.time()
        if mode != "detach":
            asyncio.ensure_future(self._janitor_files(cleared, reap=(mode == "reap")))

    async def _janitor_files(self, cleared: list[PendingRequest], *, reap: bool) -> None:
        for pending in cleared:
            drop = drop_path_for(self.session_id, pending.request_id)
            status = status_path_for(self.session_id, pending.request_id)
            try:
                if reap:
                    await asyncio.to_thread(write_status, status, DUMMY_REAP_STATUS)
                    await asyncio.to_thread(drop.unlink, missing_ok=True)
                    await asyncio.sleep(5)
                    await asyncio.to_thread(status.unlink, missing_ok=True)
                else:
                    await asyncio.to_thread(drop.unlink, missing_ok=True)
                    await asyncio.to_thread(status.unlink, missing_ok=True)
            except Exception:
                logger.exception(
                    "[session %s] pending-files janitor failed for %s",
                    self.session_id, pending.request_id,
                )

    def _schedule_gui_expiry(self, nonce: str) -> None:
        async def _expire() -> None:
            await asyncio.sleep(HYBRID_HOOK_TIMEOUT_SECONDS)
            self._gui_expiry_tasks.pop(nonce, None)
            pending = self._pending_requests.get(nonce)
            if pending is None:
                return
            # The CLI reaped the polling hook at its timeout: the GUI channel
            # for this prompt is gone. Keep the pending but degrade it to the
            # V1 badge-only marker — the TUI dialog is still answerable.
            self._pending_requests[nonce] = replace(pending, request_type="hybrid_terminal")
            await self._notify_state_change()

        self._gui_expiry_tasks[nonce] = asyncio.create_task(
            _expire(), name=f"hybrid-gui-expiry-{self.session_id}",
        )

    def _cancel_gui_expiry(self, nonce: str) -> None:
        task = self._gui_expiry_tasks.pop(nonce, None)
        if task is not None:
            task.cancel()

    # ------------------------------------------------------------------
    # Liveness
    # ------------------------------------------------------------------

    def _start_liveness_monitor(self) -> None:
        if self._liveness_task is not None and not self._liveness_task.done():
            return
        self._liveness_task = asyncio.create_task(
            self._liveness_loop(),
            name=f"hybrid-liveness-{self.session_id}",
        )

    async def _liveness_loop(self) -> None:
        """Poll the tmux pane; transition to DEAD when claude exits.

        This is the ONLY death-detection path (no SessionEnd hook in V1's
        minimal hook set).
        """
        while True:
            await asyncio.sleep(self.LIVENESS_INTERVAL)
            if self.state == AgentState.DEAD:
                return
            try:
                pid, dead = await asyncio.to_thread(
                    hybrid_tmux.pane_status, self.session_id,
                )
            except Exception:
                logger.exception(
                    "Liveness check failed for hybrid session %s", self.session_id,
                )
                continue
            if pid is not None:
                self.agent_pid = pid
            if dead or pid is None:
                if self.state == AgentState.DEAD:
                    return
                logger.info(
                    "Hybrid CLI exited for session %s (pane_dead=%s)",
                    self.session_id, dead,
                )
                self.kill_reason = self.kill_reason or "cli-exit"
                self._clear_all_pendings("delete")
                if self._first_paste_task is not None:
                    self._first_paste_task.cancel()
                # Remove the dead-pane tmux session so the next send can
                # recreate one under the same name.
                await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
                await self._transition_to_dead()
                return

    # ------------------------------------------------------------------
    # Kill / interrupt
    # ------------------------------------------------------------------

    async def _graceful_cli_exit(self) -> bool:
        """Best-effort clean shutdown of the embedded CLI via ``/exit``.

        Mirrors the SDK agents' graceful interrupt: free the composer (Escape
        stops any in-flight turn so it returns to an empty composer), submit
        ``/exit`` so the CLI finalizes its transcript and quits on its own, then
        wait (bounded) for the process to go. Returns ``True`` if it exited,
        ``False`` to fall through to the forced teardown. Never raises.
        """
        deadline = time.monotonic() + self.HYBRID_GRACEFUL_EXIT_TIMEOUT
        # Stop any running turn so the composer is free to accept /exit.
        try:
            await asyncio.to_thread(hybrid_tmux.interrupt_turn, self.session_id)
        except Exception:
            logger.debug("Escape send failed for hybrid %s", self.session_id, exc_info=True)
        # Submit /exit once the composer is free. ``_checked_paste`` only pastes
        # onto an empty composer, so it never clobbers user-typed text — it just
        # keeps returning False until the turn is interrupted, then we fall
        # through to the forced kill if that never happens within the budget.
        submitted = False
        while time.monotonic() < deadline and not self._force_kill.is_set():
            try:
                if await self._checked_paste("/exit"):
                    submitted = True
                    break
            except Exception:
                logger.debug("/exit paste failed for hybrid %s", self.session_id, exc_info=True)
                break
            await asyncio.sleep(0.25)
        if not submitted:
            return False
        # Wait for the CLI to actually quit (pane gone or marked dead).
        while time.monotonic() < deadline and not self._force_kill.is_set():
            try:
                pane_pid, pane_dead = await asyncio.to_thread(
                    hybrid_tmux.pane_status, self.session_id,
                )
            except Exception:
                return False
            if pane_pid is None or pane_dead:
                return True
            await asyncio.sleep(0.2)
        return False

    async def kill(self, reason: str = "manual") -> None:
        if self.state == AgentState.DEAD:
            return
        self.kill_reason = reason
        if self._first_paste_task is not None:
            self._first_paste_task.cancel()
        if self._liveness_task is not None:
            self._liveness_task.cancel()
        self._cancel_block_poll()
        # Pending files on shutdown: KEEP them ("detach"). The tmux kill below
        # is best-effort, so if it doesn't land (or TwiCC was hard-killed before
        # reaching here) the CLI survives — boot adoption + the hooks-watcher
        # scan then restore its GUI-answerable pendings. If the kill DID land,
        # the leftover files are swept as orphans at the next boot. Every other
        # reason kills the tmux synchronously, taking the hooks with it — delete.
        self._clear_all_pendings("detach" if reason == "shutdown" else "delete")
        # Graceful → forced, the same intent as the SDK agents. First let the
        # CLI quit cleanly via ``/exit`` so it finalizes its transcript and
        # shuts down on its own. Skipped on shutdown (we want speed there, and a
        # survivor is re-adopted at the next boot). If it doesn't land in time,
        # SIGTERM → (2s) → SIGKILL the claude process tree via the LIVE pane pid
        # (claude runs as its child, so a stale stored pid can't make us miss).
        exited = False
        if reason != "shutdown" and not self._force_kill.is_set():
            exited = await self._graceful_cli_exit()
        if not exited:
            pane_pid, _ = await asyncio.to_thread(hybrid_tmux.pane_status, self.session_id)
            if pane_pid is not None:
                await self._kill_system_process(pane_pid)
        # Drop the tmux session itself either way (best-effort cleanup).
        await asyncio.to_thread(hybrid_tmux.kill_session, self.session_id)
        await self._transition_to_dead()

    async def interrupt_or_kill(self, reason: str) -> None:
        # Timeouts, manual stops and TwiCC shutdown all kill: the tmux session
        # must go away to free claude's memory (shutdown best-effort, see
        # ``kill``). A non-destructive mid-turn interrupt (keep the session
        # alive) is a separate affordance — see :meth:`soft_interrupt`.
        await self.kill(reason)

    # Soft-interrupt key cadence: when a TUI dialog blocks the composer, press
    # Escape, wait, and re-check on this interval until it frees up, bounded by
    # the timeout so a wedged dialog can't spin forever.
    HYBRID_INTERRUPT_POLL_INTERVAL = 1.0
    HYBRID_INTERRUPT_TIMEOUT = 15.0

    async def soft_interrupt(self) -> bool:
        """Interrupt the current turn but keep the session alive (→ USER_TURN).

        Presses Escape in the TUI — the Claude CLI treats it as "stop the
        current turn" and returns to an empty composer, then writes the
        "[Request interrupted by user…]" marker. The watcher surfaces that as a
        ``turn_end`` signal which flips the agent back to USER_TURN, so this
        method only ever sends keys; it never touches state itself.

        Two cases (see :func:`tmux.composer_ready`):
        - Composer active — the normal in-turn state (spinner + empty composer):
          a single Escape interrupts the turn.
        - Composer blocked — a TUI dialog is up (permission prompt, plan
          approval, a bare ``/effort``/``/fast`` picker…): Escape to dismiss it,
          then poll until the composer frees up and send a final Escape to
          interrupt the turn. Bounded by ``HYBRID_INTERRUPT_TIMEOUT``.

        Returns ``True`` once an interrupt Escape reached an active composer,
        ``False`` if the agent is dead or the composer never freed up in time.
        """
        if self.state == AgentState.DEAD:
            return False

        sid = self.session_id

        # Fast path: composer already active → one Escape stops the turn.
        if await asyncio.to_thread(hybrid_tmux.composer_ready, sid):
            await asyncio.to_thread(hybrid_tmux.interrupt_turn, sid)
            logger.info("Soft-interrupting hybrid session %s (composer active)", sid)
            return True

        # A dialog is blocking the composer: Escape to dismiss, poll until it
        # frees up, then a final Escape interrupts the turn.
        logger.info(
            "Soft-interrupting hybrid session %s: composer busy, escaping until free", sid,
        )
        deadline = time.monotonic() + self.HYBRID_INTERRUPT_TIMEOUT
        while time.monotonic() < deadline and self.state != AgentState.DEAD:
            await asyncio.to_thread(hybrid_tmux.interrupt_turn, sid)
            await asyncio.sleep(self.HYBRID_INTERRUPT_POLL_INTERVAL)
            if await asyncio.to_thread(hybrid_tmux.composer_ready, sid):
                await asyncio.to_thread(hybrid_tmux.interrupt_turn, sid)
                logger.info("Hybrid session %s composer freed — interrupt sent", sid)
                return True

        logger.warning(
            "Hybrid soft interrupt gave up for session %s: composer never freed within %.0fs",
            sid, self.HYBRID_INTERRUPT_TIMEOUT,
        )
        return False

    # ------------------------------------------------------------------
    # Settings / title application
    # ------------------------------------------------------------------

    AUTO_PASTE_RETRY_INTERVAL = 3.0
    AUTO_PASTE_RETRY_TIMEOUT = 300.0

    # Total budget for the graceful ``/exit`` shutdown attempt on stop (freeing
    # the composer, submitting /exit, and waiting for the CLI to quit) before
    # falling through to the forced process-tree kill. Mirrors Claude Code SDK's
    # ``wait_for_dead`` window so every mode gives the CLI the same grace to
    # finalize on a stop. See ``_graceful_cli_exit``.
    HYBRID_GRACEFUL_EXIT_TIMEOUT = 30.0

    def _spawn_auto_paste(self, purpose: str, commands: list[str]) -> None:
        """Run ``commands`` in a background retry task (latest-wins per purpose).

        Auto-pasted slash commands are system-initiated and idempotent:
        nobody is waiting on them, so instead of failing fast like user
        sends they poll until the TUI composer is free. The task must NOT
        be awaited by callers — rename/settings application sit on the WS
        handler and state-transition paths, which a minutes-long retry
        loop would stall.
        """
        previous = self._auto_paste_tasks.get(purpose)
        if previous is not None and not previous.done():
            previous.cancel()
        self._auto_paste_tasks[purpose] = asyncio.create_task(
            self._run_auto_paste(commands),
            name=f"hybrid-auto-paste-{purpose}-{self.session_id}",
        )

    async def _run_auto_paste(self, commands: list[str]) -> None:
        try:
            for index, command in enumerate(commands):
                if index:
                    # Let the TUI finish processing the previous local
                    # command before the next paste lands in its composer.
                    await asyncio.sleep(0.5)
                if not await self._paste_command_when_ready(command):
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Auto paste failed for hybrid session %s", self.session_id,
            )

    async def _paste_command_when_ready(self, command: str) -> bool:
        """Paste ``command`` once the empty-composer pattern shows up.

        Gives up (with a warning) after AUTO_PASTE_RETRY_TIMEOUT or when
        the agent dies — the worst case is CLI-side drift (TwiCC's stored
        value stays authoritative and is re-passed at the next launch).
        """
        deadline = time.monotonic() + self.AUTO_PASTE_RETRY_TIMEOUT
        while self.state != AgentState.DEAD:
            if await self._checked_paste(command):
                logger.info(
                    "Applied %s to hybrid session %s", command, self.session_id,
                )
                return True
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(self.AUTO_PASTE_RETRY_INTERVAL)
        logger.warning(
            "Gave up pasting %r to hybrid session %s (TUI composer never free)",
            command, self.session_id,
        )
        return False

    async def rename(self, title: str) -> None:
        """Queue ``/rename <title>`` (verified: instant, no dialog, writes
        custom-title JSONL lines). Pasted when the TUI composer is free."""
        clean = " ".join(title.split())
        if not clean:
            return
        self._spawn_auto_paste("rename", [f"/rename {clean}"])

    async def apply_live_settings(self, settings: Any) -> None:
        """IDLE application via pasted slash commands.

        Model/context map to ``/model <name>``, effort to
        ``/effort <level>`` and fast mode to ``/fast on|off`` — all three
        apply instantly in their ARGUMENT form (verified on 2.1.172); the
        bare forms open interactive dialogs and must never be pasted.
        ``/model`` and ``/effort`` also save the value as the user's
        global default for new CLI sessions — accepted: TwiCC re-passes
        both at every launch (``/fast`` has no such side effect).
        Everything else stays STARTUP (the manager kills and relaunches).
        """
        from twicc.providers.helpers import get_provider_helpers

        helpers = get_provider_helpers(Provider.CLAUDE_CODE)
        old_model = helpers.resolve_sdk_model(
            self.agent_settings.selected_model, self.agent_settings.context_max,
        )
        new_model = helpers.resolve_sdk_model(
            settings.selected_model, settings.context_max,
        )
        commands: list[str] = []
        if new_model and new_model != old_model:
            commands.append(f"/model {new_model}")
        if settings.effort and settings.effort != self.agent_settings.effort:
            commands.append(f"/effort {settings.effort}")
        if settings.fast_mode is not None and bool(settings.fast_mode) != bool(self.agent_settings.fast_mode):
            commands.append("/fast on" if settings.fast_mode else "/fast off")
        if commands:
            # Fast path: apply inline while the composer is free, so settings
            # land BEFORE a message sent in the same action (the manager
            # awaits this method right before agent.send, and a /model pasted
            # mid-turn has no guaranteed semantics). Fall back to the
            # background retry task only for the commands the busy TUI
            # refused — re-applying from there is idempotent.
            for index, command in enumerate(commands):
                if index:
                    # Let the TUI finish processing the previous local
                    # command before the next paste lands in its composer.
                    await asyncio.sleep(0.5)
                if not await self._checked_paste(command):
                    self._spawn_auto_paste("settings", commands[index:])
                    break
                logger.info(
                    "Applied %s to hybrid session %s", command, self.session_id,
                )
        self.agent_settings = settings

    # ------------------------------------------------------------------
    # Launch-time reads
    # ------------------------------------------------------------------

    def _read_system_prompt_addendum(self) -> str | None:
        """Same dual read as the SDK agent: Session row, else pending buffer."""
        from twicc.core.models import Session
        from twicc.pending_session_attributes import get_pending_session_attributes

        row = (
            Session.objects
            .filter(id=self.session_id)
            .only("system_prompt_addendum")
            .first()
        )
        if row is not None:
            return row.system_prompt_addendum
        pending = get_pending_session_attributes(self.session_id)
        return pending.system_prompt_addendum if pending else None

    async def _resolve_temp_title(self, text: str) -> str:
        """Title for ``-n``: pending/stored title, else the prompt prefix.

        Passing ``-n`` at every launch permanently suppresses the CLI's own
        ai-title generation (verified), so TwiCC's title pipeline stays
        authoritative.
        """
        from twicc.pending_titles import get_pending_title

        title = get_pending_title(self.session_id)
        if not title:
            from twicc.core.models import Session

            title = await sync_to_async(
                lambda: Session.objects.filter(id=self.session_id)
                .values_list("title", flat=True).first()
            )()
        if not title:
            title = " ".join(text.split())
        return title[:100]
