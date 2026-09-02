"""
Claude Code agent: wraps a single SDK client instance for one TwiCC session.
"""

import asyncio
import logging
import re
import shlex
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage, StreamEvent, SystemMessage, ThinkingConfigAdaptive, ThinkingConfigDisabled,
    ToolPermissionContext, UserMessage,
)

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
import json_repair
import orjson

from twicc.agent import AgentInfo, AgentState, BaseAgent, PendingRequest, StateChangeCallback
from twicc.agent.plugin import get_plugin_dir
from twicc.context_injection import apply_goal_instruction, apply_pending_context
from twicc.core.enums import Provider
from twicc.core.models import Session
from twicc.pending_session_attributes import get_pending_session_attributes
from twicc.providers.helpers import AgentSettings

from .elicitation import attach_elicitation_handler, make_elicitation_pending_request
from .permissions import (
    extract_claude_tool_paths,
    maybe_update_plan_file,
    order_suggestion_fields,
    serialize_and_filter_suggestions,
)
from .sdk_logger import patch_client as patch_client_for_logging
from .tool_label_filter import filter_tool_input

logger = logging.getLogger(__name__)


# ripgrep's ``-r`` is ``--replace``, NOT ``--recursive`` (rg recurses by
# default). A glued ``-r<letter>`` such as ``-rn``/``-rln`` is silently parsed as
# ``--replace=<letter>`` and corrupts the output with no error — a frequent
# grep-reflex mistake the model makes. The PreToolUse hook denies such a Bash
# command so it gets re-run without the ``r``. Only the glued form is matched
# (a glued non-letter like ``-r$1`` is a deliberate replacement template), and
# only within actual ``rg`` segments. Validated by tests/test_rg_replace_trap.py.
_RG_GLUED_REPLACE_RE = re.compile(r"^-[A-Za-z]*r[A-Za-z]")
_RG_SHELL_OPS = frozenset({"|", "||", "&&", ";", "&", "|&"})
_RG_REPLACE_DENY_REASON = (
    "TwiCC blocked this command. In ripgrep (rg), `-r` means `--replace` (it "
    "substitutes matches in the printed output), NOT `--recursive`. That is the "
    "grep reflex — but ripgrep already searches the current directory recursively "
    "by default, so there is no recursive flag and none is needed. A glued "
    "`-r<letter>` such as `-rn` or `-rln` is parsed as `--replace=<letter>`, which "
    "silently rewrites your results and corrupts the output with no error. Re-run "
    "the same command with the `r` removed from that flag (e.g. `-rn` -> `-n`, "
    "`-rln` -> `-ln`)."
)

_MONITOR_STARTED_RE = re.compile(r"\bMonitor started \(task ([^,\s)]+),", re.IGNORECASE)


def _rg_replace_trap(command: str) -> bool:
    """True if ``command`` runs ``rg`` with a glued ``-r<letter>`` (the
    ``--replace``-mistaken-for-``--recursive`` trap).

    Scoped to actual ``rg`` segments so a ``-rn`` belonging to another command
    does not trigger. Heuristic, not a shell parser; every miss is fail-safe
    (a false negative just falls back to the status quo, never a wrong block).
    """
    try:
        # ``shlex.split`` does not tokenize a shell operator glued to the
        # preceding word (``echo x; rg`` -> token ``x;``): the separator vanishes,
        # the segment boundary is lost, and a following ``rg -rn`` gets swallowed
        # into the previous segment and never checked. ``punctuation_chars``
        # detaches ``; | & < > ( )`` even when glued, so operators become their own
        # tokens. ``commenters = ""`` mirrors ``shlex.split``'s ``comments=False``.
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";|&<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False  # unbalanced quotes etc. -> never block
    segment: list[str] = []
    for tok in [*tokens, "|"]:  # trailing sentinel flushes the last segment
        if tok in _RG_SHELL_OPS:
            if segment and Path(segment[0]).name == "rg":
                if any(_RG_GLUED_REPLACE_RE.match(arg) for arg in segment[1:]):
                    return True
            segment = []
        else:
            segment.append(tok)
    return False


def _capture_original_file(input_data: dict, tool_use_id: str) -> None:
    """Read and cache a file's content before it gets modified by Edit/Write."""
    from twicc.providers.claude_code.agent.original_file_cache import MAX_FILE_SIZE, cache_original_file

    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path")
    if not file_path:
        return

    session_id = input_data.get("session_id", "")
    if not session_id:
        return

    try:
        path = Path(file_path)
        if not path.is_file():
            return
        size = path.stat().st_size
        if size > MAX_FILE_SIZE:
            return
        content = path.read_text(encoding="utf-8")
        cache_original_file(session_id, tool_use_id, content)
    except Exception:
        logger.debug("Failed to cache original file %s for tool_use %s", file_path, tool_use_id, exc_info=True)


# Type aliases for cron change callbacks
CronCreatedCallback = Callable[
    [str, str, str, bool, str, "datetime", "datetime"],
    # session_id, cron_id, cron_expr, recurring, prompt, created_at, next_fire
    Coroutine[Any, Any, None],
]
CronDeletedCallback = Callable[
    [str, str],  # session_id, cron_id
    Coroutine[Any, Any, None],
]


class ClaudeCodeAgent(BaseAgent):
    """Claude Code SDK agent for a single TwiCC session.

    Manages the lifecycle of a Claude Code SDK client (connection, message sending,
    state tracking). Designed to be completely isolated so that any errors in
    the SDK never propagate to crash the server.
    """

    provider = Provider.CLAUDE_CODE

    def __init__(
        self,
        session_id: str,
        project_id: str,
        cwd: str,
        settings: AgentSettings,
        get_session_slug: Callable[[str], Coroutine[Any, Any, str | None]],
        on_cron_created: CronCreatedCallback,
        on_cron_deleted: CronDeletedCallback,
    ) -> None:
        """Initialize a Claude Code agent wrapper.

        Args:
            session_id: The session to resume
            project_id: The TwiCC project this belongs to
            cwd: Working directory for Claude operations
            settings: Per-session agent settings (already resolved against
                synced defaults). Each field is unpacked onto ``self`` so
                the rest of the agent code keeps using the individual
                attributes; mutations via ``set_permission_mode`` /
                ``apply_live_settings`` update those attributes in place.
            get_session_slug: Async callback that retrieves the slug stored on the
                Session model. Takes a session_id and returns the slug string or None.
            on_cron_created: Async callback fired when a CronCreate PostToolUse event occurs.
                Receives (session_id, cron_id, cron_expr, recurring, prompt, created_at, next_fire).
            on_cron_deleted: Async callback fired when a CronDelete PostToolUse event occurs.
                Receives (session_id, cron_id).
        """
        super().__init__(session_id, project_id, cwd, agent_settings=settings)

        self._client: ClaudeSDKClient | None = None
        # Effective trust of the project, resolved once per process start (trust
        # design §13.4: clamp at build/resume, no live re-clamp on revocation).
        # While True, every permission-mode entry point clamps to the
        # untrusted-allowed set, project/local setting sources are dropped and
        # the bypassPermissions opt-in flag is withheld.
        self._untrusted = False
        self._interrupting = False  # Set when interrupt() is called, suppresses error toast
        # Set when soft_interrupt() is called: the loop treats the resulting
        # error ResultMessage as a clean turn end (→ USER_TURN, session kept
        # alive) instead of tearing down to DEAD like ``_interrupting`` does.
        self._soft_interrupting = False
        self._message_loop_task: asyncio.Task[None] | None = None
        self._active_tools: dict[str, dict[str, Any]] = {}
        self._last_started_tool_id: str | None = None
        # Live background subagents, task_id -> description. The CLI launches
        # agents asynchronously by default and ends the parent's turn as soon
        # as it stops talking, while the agents keep running; this set (fed by
        # the ``task_started`` / ``task_updated`` / ``task_notification``
        # system events in ``_update_live_background_tasks``) lets the
        # ResultMessage handler hold ASSISTANT_TURN instead of dropping to
        # USER_TURN while agents are still running. In-memory only: background
        # agents are children of the CLI process, so they never outlive this
        # object.
        self._live_background_tasks: dict[str, str] = {}
        # Monitors are asynchronous background commands that Claude can leave
        # running after its own turn ends. Unlike generic background Bash work,
        # a Monitor has a bounded lifecycle (it stops on its own timeout at the
        # latest) with a matching terminal task notification, so retain its task
        # ids and hold ASSISTANT_TURN until all of them finish. Fed by the
        # ``Monitor started`` tool_result, drained by the same system events as
        # ``_live_background_tasks`` (see ``_update_live_tasks``) or by a
        # ``TaskStop``. This is deliberately separate from the compute
        # pipeline's task_id -> tool_use_id map, which only correlates timeline
        # result fragments for rendering.
        self._live_monitor_tasks: set[str] = set()
        # True while a "waiting" process label is what the frontend shows —
        # either "waiting for N subagents" (live background agents),
        # "monitoring" (live Monitor tools), or "waiting for scheduled wakeup
        # (HH:MM)" (a pending ScheduleWakeup).
        # Broadcast when a turn ends held in ASSISTANT_TURN; refreshed as the
        # held state changes, cleared as soon as the parent streams its own
        # activity again. See _refresh_waiting_label.
        self._waiting_label_active = False
        # Epoch (seconds) the latest ScheduleWakeup is due to fire, or None.
        # Claude's ScheduleWakeup tool ends the turn, but the harness re-invokes
        # the agent once the delay elapses; like a live background agent, a
        # still-future wake-up holds the session in ASSISTANT_TURN instead of
        # dropping to USER_TURN — no "finished working" churn and no idle
        # auto-stop while it waits. A new ScheduleWakeup always supersedes the
        # previous deadline and nothing else clears it (a later message does NOT
        # cancel the wake-up), so the turn-end test is a pure "deadline > now".
        # In-memory only: the wake-up timer lives in the CLI process and never
        # outlives it. See the ResultMessage handler and _refresh_waiting_label.
        self._pending_wakeup_at: float | None = None
        self._get_session_slug = get_session_slug
        self._on_cron_created = on_cron_created
        self._on_cron_deleted = on_cron_deleted

        # ProcessRun lifecycle tracking
        self._first_turn_done_event = asyncio.Event()  # Set on first USER_TURN or DEAD
        self._first_user_turn_reached = False           # True only if USER_TURN was reached (not DEAD)
        self._old_runs_purged = False                   # True after old ProcessRuns are purged at first USER_TURN

        logger.debug(
            "ClaudeCodeAgent created for session %s, project %s, cwd=%s, settings=%s",
            session_id,
            project_id,
            cwd,
            settings,
        )

    def _log_stderr(self, line: str) -> None:
        """Log stderr output from the Claude Code CLI subprocess.

        This callback is passed to the SDK to capture stderr lines.
        """
        logger.warning(
            "Claude Code stderr for session %s: %s",
            self.session_id,
            line.rstrip(),
        )

    def get_pid(self) -> int | None:
        """Get the PID of the underlying Claude Code CLI subprocess.

        This accesses internal SDK attributes to extract the subprocess PID.
        May return None if the process is not started or has terminated.

        Returns:
            Process ID of the Claude Code CLI subprocess, or None if unavailable
        """
        try:
            if self._client is None:
                return None
            # Access internal SDK attributes: ClaudeSDKClient._transport._process.pid
            transport = getattr(self._client, "_transport", None)
            if transport is None:
                return None
            process = getattr(transport, "_process", None)
            if process is None:
                return None
            return getattr(process, "pid", None)
        except Exception:
            return None

    def get_expired_recurring_crons(self) -> list:
        """Return recurring SessionCron instances whose CLI auto-delete has occurred.

        Checks each recurring cron's expired_at (last_fire + jitter + margin) against
        the current time. Non-recurring crons are excluded.

        This is a synchronous method (DB access) — call via asyncio.to_thread.

        Returns:
            List of SessionCron instances that have expired.
        """
        from twicc.core.models import SessionCron

        now = datetime.now(tz=UTC)
        expired = []
        for cron in SessionCron.objects.filter(session_id=self.session_id, recurring=True):
            if cron.expired_at is not None and now >= cron.expired_at:
                expired.append(cron)
        return expired

    async def _handle_cron_tool_event(self, input_data: dict) -> None:
        """Process a CronCreate or CronDelete PostToolUse event.

        Parses the SDK hook data and delegates to the on_cron_created / on_cron_deleted
        callbacks for DB persistence. Called from the PostToolUse hook closure in start().

        For CronCreate, the next fire time is always computed via cronsim. If the
        expression cannot be parsed, or the computed fire time is already in the past
        for a one-shot cron, the cron is not persisted (it would never fire).

        Args:
            input_data: The PostToolUseHookInput dict from the SDK, containing
                tool_name, tool_input, and tool_response.
        """
        tool_name = input_data.get("tool_name")
        tool_input = input_data.get("tool_input", {})
        tool_response = input_data.get("tool_response")

        # logger.debug(f"CronCreate called with input = {tool_input} and response = {tool_response}")

        if tool_name == "CronCreate" and isinstance(tool_response, dict):
            job_id = tool_response.get("id")
            if not job_id:
                return

            cron_expr = tool_input.get("cron", "")
            recurring = tool_response.get("recurring", True)
            prompt = tool_input.get("prompt", "")

            from twicc.core.models import cron_occurrences

            created_at = datetime.now(tz=UTC)

            # Compute next fire time from the cron expression (always required)
            try:
                it = cron_occurrences(cron_expr, created_at)
                next_fire = next(it)
            except Exception:
                logger.warning(
                    "Failed to parse cron expression '%s' for session %s, skipping persistence",
                    cron_expr,
                    self.session_id,
                )
                return

            # For one-shot crons, skip if next_fire is already in the past
            if not recurring and next_fire <= created_at:
                logger.warning(
                    "One-shot cron '%s' for session %s has next_fire in the past (%s), skipping persistence",
                    cron_expr,
                    self.session_id,
                    next_fire,
                )
                return

            logger.info(
                "Cron job created for session %s: id=%s, cron=%s, recurring=%s, next_fire=%s",
                self.session_id,
                job_id,
                cron_expr,
                recurring,
                next_fire,
            )

            try:
                await self._on_cron_created(
                    self.session_id, job_id, cron_expr, recurring,
                    prompt, created_at, next_fire,
                )
            except Exception as e:
                logger.error("Error in on_cron_created callback for session %s: %s", self.session_id, e)

        elif tool_name == "CronDelete" and isinstance(tool_response, dict):
            job_id = tool_response.get("id") or tool_input.get("id")
            if not job_id:
                return

            logger.info("Cron job deleted for session %s: id=%s", self.session_id, job_id)

            try:
                await self._on_cron_deleted(self.session_id, job_id)
            except Exception as e:
                logger.error("Error in on_cron_deleted callback for session %s: %s", self.session_id, e)

    def _serialize_active_tools(self) -> list[dict]:
        """Return ``_active_tools`` as a list of dicts ready for transport."""
        return [
            {
                "id": tool_use_id,
                "name": entry["name"],
                "input": entry["input"],
                "streaming": entry.get("streaming", False),
            }
            for tool_use_id, entry in self._active_tools.items()
        ]

    def get_info(self) -> AgentInfo:
        """Get an immutable snapshot of the agent state.

        Extends the base snapshot with Claude-specific fields (active tools
        and the most recently started tool id). ``pending_requests`` is
        populated by the base implementation.
        """
        return super().get_info()._replace(
            active_tools=tuple(self._serialize_active_tools()),
            last_started_tool_id=self._last_started_tool_id,
        )

    async def discard_active_tool(self, tool_use_id: str) -> bool:
        """Remove an active-tool entry by id and broadcast if it existed.

        Used by the JSONL watcher as a safety net: PostToolUse/PostToolUseFailure
        do not fire when the CLI rejects a tool call before execution (e.g.
        Edit/Write on a not-yet-read file — the CLI synthesises an is_error
        tool_result without ever invoking the tool). Without this hook, those
        entries would leak into _active_tools and persist in the working
        status line.
        """
        if tool_use_id not in self._active_tools:
            return False
        self._active_tools.pop(tool_use_id, None)
        # logger.debug(
        #     "discard_active_tool session=%s tool=%s active_tools=%d",
        #     self.session_id,
        #     entry["name"] if entry else "?",
        #     len(self._active_tools),
        # )
        await self._broadcast_process_tools()
        return True

    def get_permission_suggestions(
        self, tool_name: str, input_data: dict, context: ToolPermissionContext, *, untrusted: bool
    ) -> list[dict] | None:
        """Extract, serialize and filter permission suggestions from the SDK context.

        The SDK may provide suggestions as ``PermissionUpdate`` dataclass instances
        or, in some cases, as plain dicts. This method normalises both forms into
        a list of JSON-serialisable dicts ready for transmission to the frontend.

        Filtering applied:
        - For ``addDirectories`` / ``removeDirectories`` suggestions, the project
          directory (``self.cwd``) is removed from the directories list (it is always
          implicitly allowed). If the directories list becomes empty after filtering,
          the suggestion is dropped entirely.

        Args:
            tool_name: The name of the tool requesting permission (used to generate
                MCP suggestions when the SDK provides none)
            input_data: The tool's input parameters (reserved for future filtering)
            context: SDK ``ToolPermissionContext`` containing the suggestions list
            untrusted: Live-resolved trust state of the project. When ``True``,
                modes outside the untrusted-allowed set (``bypassPermissions``)
                are surfaced as ``disabled`` options rather than hidden, so the
                user sees they exist but cannot pick them here.

        Returns:
            A list of serialised permission suggestion dicts, or ``None`` if there
            are none (or all were filtered out).
        """
        # Shared brick: serialize, cwd-filter, ungroup (kept identical for the
        # hybrid path). Then drop any setMode suggestion from the SDK — we
        # inject our own mode-picker at the end so the user always sees the
        # full set of mode options regardless of context (SDK-only policy:
        # the hybrid path keeps hook-native setMode suggestions verbatim).
        result = [
            s for s in serialize_and_filter_suggestions(context.suggestions, self.cwd)
            if s.get("type") != "setMode"
        ]

        # Inject wildcard MCP suggestions: for each rule targeting a specific MCP tool
        # (mcp__{name}__{tool}), add a wildcard suggestion (mcp__{name}__*) so the user
        # can choose to allow all tools from that MCP server at once.
        # Collect all existing toolNames to know what's already covered.
        existing_tool_names: set[str] = set()
        for s in result:
            for rule in s.get("rules") or ():
                tn = rule.get("toolName", "")
                if tn:
                    existing_tool_names.add(tn)

        # If no suggestion covers the current tool and it's an MCP tool, create one.
        # The wildcard loop below will then automatically add the server-wide variant.
        if tool_name not in existing_tool_names:
            parts = tool_name.split("__")
            if len(parts) == 3 and parts[0] == "mcp":
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': tool_name}],
                    'behavior': 'allow',
                    'destination': 'localSettings',
                })
                existing_tool_names.add(tool_name)

        # If the current tool is WebFetch, ensure both a domain-specific and a global suggestion exist.
        # Domain-specific: ruleContent = "domain:<host>" extracted from the URL in input_data.
        # Global: no ruleContent, allows WebFetch on any domain.
        if tool_name == "WebFetch":
            url = input_data.get("url")
            if url:
                host = urlparse(url if "://" in url else f"https://{url}").hostname
                if host:
                    domain_rule = f"domain:{host}"
                    has_domain = any(
                        rule.get("toolName") == "WebFetch" and rule.get("ruleContent") == domain_rule
                        for s in result for rule in s.get("rules") or ()
                    )
                    if not has_domain:
                        result.append({
                            'type': 'addRules',
                            'rules': [{'toolName': 'WebFetch', 'ruleContent': domain_rule}],
                            'behavior': 'allow',
                            'destination': 'session',
                        })
            # Global suggestion inherits the destination from the domain-specific one
            # (either from the SDK or the one we just created above).
            domain_suggestion = next(
                (s for s in result for rule in s.get("rules") or ()
                 if rule.get("toolName") == "WebFetch" and rule.get("ruleContent")),
                None,
            )
            domain_destination = domain_suggestion["destination"] if domain_suggestion else "session"
            has_global = any(
                rule.get("toolName") == "WebFetch" and not rule.get("ruleContent")
                for s in result for rule in s.get("rules") or ()
            )
            if not has_global:
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': 'WebFetch'}],
                    'behavior': 'allow',
                    'destination': domain_destination,
                })

        # If the current tool is WebSearch and no suggestion exists for it, create one.
        if tool_name == "WebSearch":
            has_web_search = any(
                rule.get("toolName") == "WebSearch"
                for s in result for rule in s.get("rules") or ()
            )
            if not has_web_search:
                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': 'WebSearch'}],
                    'behavior': 'allow',
                    'destination': 'session',
                })

        # If the current tool is Read, Edit, or Write, generate a suggestion with a
        # ruleContent select covering: exact file, then for each ancestor directory
        # (from deepest to shallowest) the extension glob and the catch-all glob,
        # and finally "allow all" (no ruleContent). Write maps to Edit rules.
        if tool_name in ("Read", "Edit", "Write"):
            file_path = input_data.get("file_path")
            if file_path and file_path.startswith("/"):
                suggestion_tool = "Read" if tool_name == "Read" else "Edit"
                path = PurePosixPath(file_path)
                ext = path.suffix  # e.g. ".py"

                # Build ruleContent options: exact file first
                options = [f"//{file_path.lstrip('/')}"]

                # For each ancestor directory, add extension glob then catch-all glob
                parent = path.parent
                while parent != PurePosixPath("/"):
                    prefix = f"//{str(parent).lstrip('/')}"
                    if ext:
                        options.append(f"{prefix}/*{ext}")
                    options.append(f"{prefix}/*")
                    if ext:
                        options.append(f"{prefix}/**/*{ext}")
                    options.append(f"{prefix}/**")
                    parent = parent.parent

                # Root-level extension glob (e.g. //**/*.py) — no //** since that equals "allow all"
                if ext:
                    options.append(f"//**/*{ext}")

                # Last option: allow all (empty ruleContent rendered as tool name only)
                options.append("")

                result.append({
                    'type': 'addRules',
                    'rules': [{'toolName': suggestion_tool, 'ruleContent': options[0]}],
                    'behavior': 'allow',
                    'destination': 'session',
                    '_ruleContentOptions': options,
                })

        # For each specific MCP tool suggestion, add a server-wide wildcard variant
        # (mcp__{name}__*) so the user can allow all tools from that server at once.
        seen_mcp_prefixes: set[str] = set()
        extra: list[dict] = []
        for s in result:
            for rule in s.get("rules") or ():
                tn = rule.get("toolName", "")
                # Match mcp__{name}__{tool} — exactly 3 parts separated by "__"
                parts = tn.split("__")
                if len(parts) != 3 or parts[0] != "mcp":
                    continue
                mcp_prefix = f"mcp__{parts[1]}"
                if mcp_prefix in seen_mcp_prefixes:
                    continue
                seen_mcp_prefixes.add(mcp_prefix)
                wildcard = f"{mcp_prefix}__*"
                # Only add if neither the bare prefix nor the wildcard already exist
                if mcp_prefix not in existing_tool_names and wildcard not in existing_tool_names:
                    extra.append({**s, "rules": [{"toolName": wildcard}]})

        result.extend(extra)

        # Inject a setMode suggestion so the user can switch the session's permission
        # mode directly from the approval screen. Skipped for ExitPlanMode, where the
        # CLI handles the plan→default transition automatically on approval. ``auto``
        # is filtered out when the session's model does not support it (Opus 4.5,
        # Sonnet 4.5, ...) — picking it would otherwise hit an SDK error.
        if tool_name != "ExitPlanMode":
            from twicc.providers.helpers import get_provider_helpers

            current_mode = self.agent_settings.permission_mode
            helpers = get_provider_helpers(Provider.CLAUDE_CODE)
            supports_auto = helpers.selected_model_supports_permission_auto(
                self.agent_settings.selected_model,
            )
            candidate_modes = ("default", "auto", "acceptEdits", "bypassPermissions")
            # Untrusted modes (``bypassPermissions``) are kept in the list but
            # flagged ``disabled`` so the approval screen shows them as
            # "(not available)" — matching the agent-settings popover — instead
            # of silently hiding them. The frontend prevents picking them and the
            # ws.py floor re-clamps regardless (forged/stale payloads).
            mode_options = [
                {"mode": m, "disabled": untrusted and m not in helpers.UNTRUSTED_PERMISSION_MODES}
                for m in candidate_modes
                if m != current_mode and (m != "auto" or supports_auto)
            ]
            if mode_options:
                result.append({
                    'type': 'setMode',
                    'mode': None,
                    'destination': 'session',
                    '_modeOptions': mode_options,
                    '_currentMode': current_mode,
                })

        return order_suggestion_fields(result)

    async def _handle_pending_request(
        self,
        tool_name: str,
        input_data: dict,
        context: ToolPermissionContext,
    ) -> PermissionResultAllow | PermissionResultDeny:
        """SDK can_use_tool callback: creates a pending request and waits for resolution.

        Called by the SDK when Claude wants to use a tool that requires permission,
        or when Claude asks a clarifying question via AskUserQuestion. This method
        blocks (via an asyncio Future) until the frontend resolves the request.

        Args:
            tool_name: The tool Claude wants to use (e.g., "Bash", "AskUserQuestion")
            input_data: The tool's input parameters
            context: SDK ToolPermissionContext with optional permission suggestions

        Returns:
            PermissionResultAllow or PermissionResultDeny from the user's response
        """
        # TwiCC's own MCP tools are a control plane (they drive sessions,
        # messages, bookmarks — never the project's code); auto-approve them in
        # every mode and trust, exactly like the skills+CLI the agent already
        # has. This fires before any pending request is created, so no prompt
        # ever appears. In ``bypassPermissions`` the callback isn't invoked at
        # all; this covers the restrictive modes (default/plan/acceptEdits).
        # See MCP plan D9.
        if tool_name.startswith("mcp__twicc__"):
            return PermissionResultAllow()

        request_id = str(uuid.uuid4())

        if tool_name == "AskUserQuestion":
            request_type = "ask_user_question"
        else:
            request_type = "tool_approval"

        untrusted = await self._resolve_untrusted_now()

        # System work-dir auto-approval: if this approval targets ONLY the
        # session's own artifacts/scratch dirs (and the orchestration root's
        # shared scratch), answer it silently instead of prompting the human.
        # Disabled in untrusted projects (trust stays human-only); never for
        # AskUserQuestion (not a tool approval).
        if not untrusted and request_type == "tool_approval":
            paths, fully_known = extract_claude_tool_paths(
                tool_name, input_data, getattr(context, "blocked_path", None),
            )
            if self._targets_only_work_dirs(paths, fully_known):
                logger.info(
                    "Auto-approving %s for session %s — targets only system "
                    "work dirs", tool_name, self.session_id,
                )
                return PermissionResultAllow()

        permission_suggestions = self.get_permission_suggestions(
            tool_name, input_data, context, untrusted=untrusted
        )

        request = PendingRequest(
            request_id=request_id,
            request_type=request_type,
            tool_name=tool_name,
            tool_input=input_data,
            created_at=time.time(),
            permission_suggestions=permission_suggestions,
        )

        try:
            response = await self._await_pending_request(request)
        except asyncio.CancelledError:
            logger.warning(
                "[session %s] [permission %s] Future cancelled while awaiting (tool=%r)",
                self.session_id, request_id, tool_name,
            )
            raise

        # For ExitPlanMode: persist a user-modified plan to the plan file
        # (shared brick — the CLI ignores the plan carried in updated_input).
        if tool_name == "ExitPlanMode" and isinstance(response, PermissionResultAllow):
            await maybe_update_plan_file(
                input_data,
                response.updated_input,
                slug_getter=self._get_session_slug,
                session_id=self.session_id,
            )

        return response

    async def _handle_elicitation_request(self, params: dict) -> dict:
        """Elicitation-bridge handler: create a pending request and wait for the user.

        Called (via :mod:`.elicitation`) when an MCP server elicits input
        mid-tool-call — the CLI forwards it as a ``subtype: "elicitation"``
        control request. Unlike ``can_use_tool``, this path is active in EVERY
        permission mode (including ``bypassPermissions``): an elicitation is a
        server asking the *user* for data, not a tool asking to run, so no
        auto-approve gate applies.

        The returned dict is the CLI wire response, already shape-validated by
        the WS layer (``ClaudeCodeWSHandler._build_elicitation_response``):
        ``{"action": ..., "content"?: {...}}``. A cancelled future
        (kill / turn interrupt) propagates ``CancelledError`` to the bridge,
        which drops the control request without a response — mirroring the
        SDK's own cancel semantics.
        """
        request = make_elicitation_pending_request(params)
        logger.info(
            "MCP elicitation for session %s: server=%r mode=%r",
            self.session_id, request.tool_input.get("serverName"),
            request.tool_input.get("mode"),
        )
        return await self._await_pending_request(request)

    async def _build_query_prompt(
        self,
        text: str,
        images: list[dict] | None,
        documents: list[dict] | None,
    ) -> AsyncIterator[dict]:
        """Build prompt for SDK query() as an async generator.

        Always returns an async generator yielding a single transport message,
        which is required for streaming mode (needed by the can_use_tool callback).

        Each yielded dict is a complete transport message with the structure:
            {"type": "user", "message": {"role": "user", "content": [...]}, ...}

        Args:
            text: The message text
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Returns:
            An async iterator yielding a single transport message dict.
        """
        # Reconcile the dynamic Context block, then fold any queued
        # <twicc:context> block into the user text. This is the single chokepoint
        # for Claude Code outgoing user messages — both start() and send() build
        # their prompt here — so an environment change the reconcile picks up, or
        # any queued injection, lands on whichever message goes out next.
        # One-shot and generic; a no-op when nothing changed. compute_base
        # scrubs the block from the stored copy. See twicc.context_injection.
        await self._reconcile_context()
        text = apply_pending_context(self.session_id, text)
        # And, when this message is a ``/goal <args>`` command, append the static
        # <twicc:instruction> block (a no-op otherwise). Same chokepoint, same
        # ingestion strip. See twicc.context_injection.apply_goal_instruction.
        text = apply_goal_instruction(text)

        content_blocks: list[dict] = []

        if images:
            content_blocks.extend(images)

        if documents:
            content_blocks.extend(documents)

        # Attachments alone are a valid message (the frontend allows sending
        # them with an empty composer on an existing session), and the CLI
        # writes the content array verbatim to the JSONL — so emit no text
        # block at all rather than an empty one. The empty block stays the
        # fallback for a message with no attachments either (callers upstream
        # refuse those, so this only guards against an empty content array).
        if text or not content_blocks:
            content_blocks.append({"type": "text", "text": text})

        async def _message_stream() -> AsyncIterator[dict]:
            yield {
                "type": "user",
                "message": {"role": "user", "content": content_blocks},
                "parent_tool_use_id": None,
            }

        return _message_stream()

    async def start(
        self,
        prompt: str,
        on_state_change: StateChangeCallback,
        resume: bool = True,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> None:
        """Start the process and send the first message.

        This connects to Claude and sends the initial prompt. A background task
        is started to consume messages and track state changes.

        Args:
            prompt: The initial message to send
            on_state_change: Async callback invoked when process state changes
            resume: If True (default), resume an existing session. If False,
                   create a new session with the session_id as the custom UUID.
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Raises:
            RuntimeError: If the process is already started
        """
        if self._client is not None:
            raise RuntimeError("Process already started")

        self._state_change_callback = on_state_change

        logger.debug(
            "Starting process for session %s (resume=%s)", self.session_id, resume
        )

        # If <claude home>/projects/ doesn't exist yet, the watcher is currently
        # in slow-poll mode (30s). The SDK is about to create that directory
        # for this session — boost the watcher to 5s so we pick up the new
        # JSONL file quickly.
        from twicc.providers.claude_code.sessions_watcher import get_watcher
        get_watcher().request_fast_poll()

        try:
            self._active_tools = {}
            self._last_started_tool_id = None

            # Trust clamp (security floor, trust design §13.4): resolve the
            # project's effective trust and clamp the permission mode before it
            # reaches the SDK. The frontend normally bakes a safe value at
            # creation; this catches forged payloads, legacy sessions and
            # post-revocation resumes. The stored Session value is left
            # untouched — the frontend derives the forced display itself.
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

            # Create options - either resume existing session or create new with custom ID
            # Build thinking config from boolean flag
            thinking_config = None
            if self.agent_settings.thinking_enabled is True:
                thinking_config = ThinkingConfigAdaptive(type="adaptive", display="summarized")
            elif self.agent_settings.thinking_enabled is False:
                thinking_config = ThinkingConfigDisabled(type="disabled")

            # PostToolUse hook for cron tracking — closure captures self so the
            # hook can persist cron changes via the on_cron_created/on_cron_deleted callbacks.
            async def _on_cron_tool(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
                await self._handle_cron_tool_event(input_data)
                return {"continue_": True}

            async def _pre_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
                tool_name = input_data.get("tool_name", "") or ""
                # Deny the recurring ``rg -r<letter>`` mistake (``-r`` is
                # ``--replace``, not ``--recursive``) so the call is re-run
                # without the glued ``r``. Applies to subagent Bash too.
                if tool_name == "Bash":
                    command = (input_data.get("tool_input") or {}).get("command", "") or ""
                    if _rg_replace_trap(command):
                        return {
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "permissionDecision": "deny",
                                "permissionDecisionReason": _RG_REPLACE_DENY_REASON,
                            }
                        }
                # Subagent tools carry an "agent_id" field; the parent session must
                # ignore them so its working-status feed stays scoped to its own work.
                is_subagent_tool = "agent_id" in input_data
                if tool_name in ("Edit", "Write") and not is_subagent_tool:
                    _capture_original_file(input_data, tool_use_id)
                # Streaming registered the tool with streaming=True so the frontend
                # always shows its summary while input chunks were arriving. By the
                # time PreToolUse fires, the input is final — flip the flag off so
                # the "lone latest tool → no parens" rule can apply again.
                if tool_use_id and tool_use_id in self._active_tools:
                    entry = self._active_tools[tool_use_id]
                    if entry.get("streaming"):
                        entry["streaming"] = False
                        await self._broadcast_process_tools()
                return {"continue_": True}

            async def _post_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
                if tool_use_id and tool_use_id in self._active_tools:
                    self._active_tools.pop(tool_use_id, None)
                    # event = input_data.get("hook_event_name", "PostToolUse")
                    # logger.debug(
                    #     "%s session=%s tool=%s active_tools=%d",
                    #     event,
                    #     self.session_id,
                    #     entry["name"] if entry else "?",
                    #     len(self._active_tools),
                    # )
                    await self._broadcast_process_tools()
                return {"continue_": True}

            extra_args: dict[str, str | None] = {
                ("chrome" if self.agent_settings.claude_in_chrome else "no-chrome"): None
            }
            # Always opt in to the ``bypassPermissions`` *capability* at launch.
            # This is a capability flag, not the mode itself: the effective mode
            # stays governed by the live trust floor (``set_permission_mode`` and
            # the ws.py approval path both re-resolve trust). Passing it
            # unconditionally lets a session that started untrusted escalate to
            # ``bypassPermissions`` once the project is trusted mid-run — without
            # it, the CLI could never honor the mode for the process lifetime.
            extra_args["allow-dangerously-skip-permissions"] = None

            # Always pass ``fastMode`` explicitly (true or false) via the
            # flag-settings layer so the per-session choice overrides any
            # ``/fast`` toggle persisted in the user's ``<claude home>/settings.json``.
            # Inline JSON is accepted by the SDK transport when the string
            # starts with ``{`` (see ``_build_settings_value`` in the SDK's
            # ``subprocess_cli``).
            fast_mode_settings = orjson.dumps(
                {"fastMode": bool(self.agent_settings.fast_mode)}
            ).decode()

            # ``question_widget=False`` → the agent must ask its questions as
            # plain text. We strip ``AskUserQuestion`` from the tools the SDK
            # exposes so the model cannot reach for the UI widget at all.
            # ``None`` / ``True`` leave the tool in place (default behaviour).
            disallowed_tools = (
                ["AskUserQuestion"]
                if self.agent_settings.question_widget is False
                else []
            )

            # TwiCC's system-prompt addendum: read the frozen value from
            # the Session row (resume case) or from the pending buffer
            # (brand-new sessions whose row is not yet created by the
            # watcher). The content was composed once by
            # ``core.services.session_creation`` and is byte-stable across
            # every subsequent start — required for the Anthropic prompt
            # cache to keep hitting on resumes. Pre-existing sessions
            # created before the column existed leave it as ``None``;
            # in that case we omit ``append`` entirely so the SDK behaves
            # exactly as it did before the addendum feature.
            def _read_system_prompt_addendum() -> str | None:
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

            system_prompt_append = await sync_to_async(_read_system_prompt_addendum)()

            system_prompt_option: dict[str, Any] = {
                "type": "preset",
                "preset": "claude_code",  # Use Claude Code's system prompt
            }
            if system_prompt_append is not None:
                system_prompt_option["append"] = system_prompt_append

            # Pre-create and collect this session's work dirs (its own
            # artifacts/<id> + scratch/<id>, plus the orchestration root's
            # shared scratch when spawned) and grant them via ``add_dirs`` so
            # the agent reads/writes them prompt-free in the modes that allow
            # writes. Passed in every mode: redundant under bypass, read-only
            # under plan — harmless either way. ``add_dirs`` needs existing
            # dirs (Claude silently ignores a missing target), hence the
            # pre-creation inside the helper.
            work_dirs = await self._resolve_and_create_work_dirs()

            # TwiCC's own MCP server (/mcp): pass its per-session config as a
            # FILE path (never the inline dict form, which the SDK serialises
            # onto the argv — the bearer token would show in ``ps``). The token
            # is deterministic, so the file self-heals across restarts. Gated on
            # the TWICC_NO_MCP kill switch. ``MCP_TOOL_TIMEOUT`` lets long
            # ``*_wait`` tools run without the SDK's default cutoff.
            #
            # Allow-list ``mcp__twicc`` (server-level) so our tools are permitted
            # in EVERY mode — the ``can_use_tool`` auto-approve below is not
            # enough on its own: ``dontAsk`` (read-only) denies a permissioned
            # tool BEFORE the callback is consulted, so without this allow-list
            # our control-plane tools would be blocked in read-only mode. The
            # allow-list is the Claude equivalent of Codex's approve mode (D9).
            from twicc.mcp import mcp_enabled
            from twicc.mcp.wiring import write_claude_mcp_config

            _mcp_on = mcp_enabled()
            mcp_servers_option = str(write_claude_mcp_config(self.session_id)) if _mcp_on else {}
            mcp_allowed_option = ["mcp__twicc"] if _mcp_on else []

            # Claude Code 2.1.233 retired the todo/task tools (TodoWrite, Task*) on
            # Opus 4.8, Sonnet 5, Fable 5 and every newer model. They are the only
            # source feeding ``Session.tasks`` on this provider, so force them back
            # on — without the flag the Tasks snapshot stays empty on current models.
            env_option = {"CLAUDE_CODE_ENABLE_TODO_TOOLS": "1"}
            if _mcp_on:
                env_option["MCP_TOOL_TIMEOUT"] = "600000"
            # Configured provider homes (CLAUDE_CONFIG_DIR & co): explicit even
            # though os.environ inheritance would carry them — the invariant
            # that a launched process never touches another home must not
            # depend on what a purge leaves behind.
            from twicc.provider_homes import provider_env_overlay

            env_option.update(provider_env_overlay())

            options = ClaudeAgentOptions(
                system_prompt=system_prompt_option,
                cwd=self.cwd,
                add_dirs=work_dirs,
                permission_mode=self.agent_settings.permission_mode,
                model=self.sdk_model,
                effort=self.agent_settings.effort,
                thinking=thinking_config,
                # Untrusted projects load only the user-level settings: project/
                # local settings, hooks, ``.mcp.json`` and project agents are
                # repo-controlled and must not shape the session (trust §13.4).
                setting_sources=["user"] if self._untrusted else ["user", "project", "local"],
                settings=fast_mode_settings,
                plugins=[{"type": "local", "path": str(get_plugin_dir())}],
                can_use_tool=self._handle_pending_request,
                allowed_tools=mcp_allowed_option,
                disallowed_tools=disallowed_tools,
                hooks={
                    "PreToolUse": [HookMatcher(matcher=None, hooks=[_pre_tool_use])],
                    "PostToolUse": [
                        HookMatcher(matcher=None, hooks=[_post_tool_use]),
                        HookMatcher(matcher="CronCreate|CronDelete", hooks=[_on_cron_tool]),
                    ],
                    # PostToolUseFailure fires instead of PostToolUse when a tool
                    # errors out (or is interrupted) — without it, the entry would
                    # stay in _active_tools and the working-status line would keep
                    # showing the dead tool indefinitely.
                    "PostToolUseFailure": [HookMatcher(matcher=None, hooks=[_post_tool_use])],
                },
                stderr=self._log_stderr,
                max_buffer_size=10 * 1024 * 1024,  # 10 MB — prevent crashes on large tool outputs
                extra_args=extra_args,
                include_partial_messages=True,
                mcp_servers=mcp_servers_option,
                env=env_option,
            )

            if resume:
                # Resume existing session
                options.resume = self.session_id
                # The frozen addendum reflects launch-time state and is replayed
                # verbatim on resume, so re-state the whole mutable Context block
                # on the first message. See twicc.context_injection.
                self._reset_context_baseline()
            else:
                # New session with custom session ID
                options.extra_args["session-id"] = self.session_id
                # Seed the reconciliation baseline from the same primitives that
                # built the addendum so the first turn re-states nothing
                # (best-effort; logs and continues on failure).
                await self._seed_context_baseline()

            self._client = ClaudeSDKClient(options=options)
            patch_client_for_logging(self._client, self.session_id)

            # Connect without prompt to enter streaming mode, then send the message
            # via query(). The SDK's connect(prompt) with a string puts the transport
            # in "print mode" which closes stdin immediately, but ClaudeSDKClient
            # always uses streaming mode for the control protocol. We must connect()
            # first, then query() to send messages.
            await self._client.connect()

            # Route MCP elicitations (control requests the Python SDK does not
            # know) to our pending-request pipeline. Must come after connect()
            # — the Query object only exists from there — and always before
            # any turn runs (elicitations only fire mid-turn).
            attach_elicitation_handler(self._client, self._handle_elicitation_request)

            # Build query prompt as async generator (streaming mode)
            query_prompt = await self._build_query_prompt(prompt, images, documents)
            await self._client.query(query_prompt)

            logger.debug(
                "Connection established for session %s",
                self.session_id,
            )

            # Transition to assistant turn
            self._set_state(AgentState.ASSISTANT_TURN)
            self.last_activity = time.time()
            await self._notify_state_change()

            # Start background message loop
            self._message_loop_task = asyncio.create_task(
                self._run_message_loop(),
                name=f"claude_code-process-{self.session_id}",
            )

            logger.debug(
                "Message loop task created for session %s",
                self.session_id,
            )

        except Exception as e:
            # Handle error and transition to DEAD state. Do not re-raise as the
            # spec requires process errors to be logged and reported, never propagated.
            # The error is communicated to the frontend via WebSocket broadcast.
            await self._handle_error(f"Failed to start process: {e}", exc=e)

    async def _resolve_untrusted_now(self) -> bool:
        """Re-resolve the project's trust state live (fresh DB read).

        ``self._untrusted`` is a launch-time snapshot used only for the
        build-time decisions (initial clamp, ``setting_sources``). The runtime
        permission floors instead re-resolve trust on every access so that
        trusting/untrusting a project mid-run takes effect immediately, in both
        directions (relax after trust, re-clamp after revocation).
        """
        from twicc.core.services.trust import project_is_untrusted

        return await sync_to_async(project_is_untrusted)(self.project_id)

    async def set_permission_mode(self, mode: str) -> None:
        """Change permission mode on the live SDK client.

        Calls the SDK's set_permission_mode() method to update the permission mode
        on the running Claude process, then updates the local attribute to keep
        the in-memory state in sync.

        Args:
            mode: The permission mode to set (e.g., "default", "acceptEdits", "bypassPermissions")

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        if await self._resolve_untrusted_now():
            # Security floor: no escalation past the untrusted-allowed set,
            # whatever the source of the request (trust design §13.4). Trust is
            # re-resolved live (not the launch-time snapshot) so a project
            # trusted mid-run can escalate, and one revoked mid-run is re-clamped.
            from twicc.core.services.trust import clamp_permission_mode_for_untrusted

            mode = await sync_to_async(clamp_permission_mode_for_untrusted)(
                Provider.CLAUDE_CODE, mode,
            )

        logger.debug(
            "Setting permission mode to '%s' for session %s",
            mode,
            self.session_id,
        )
        await self._client.set_permission_mode(mode)
        self.agent_settings = self.agent_settings._replace(permission_mode=mode)

    async def stop_subagent(self, subagent_id: str) -> None:
        """Stop a running subagent (Task) via the SDK.

        Calls the SDK's ``stop_task()`` method to gracefully stop a
        specific subagent running within this session's process. The
        CLI will emit a ``task_notification`` with ``status='stopped'``
        in the JSONL stream.

        Args:
            subagent_id: The subagent identifier (same as the SDK's task_id)

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.info(
            "Stopping subagent %s in session %s",
            subagent_id,
            self.session_id,
        )
        await self._client.stop_task(subagent_id)
        # The CLI's terminal task_notification also clears this entry; the
        # eager pop just makes sure a stopped agent can never keep the parent
        # pinned in ASSISTANT_TURN if that notification goes missing.
        if self._live_background_tasks.pop(subagent_id, None) is not None and self._waiting_label_active:
            await self._refresh_waiting_label()

    async def interrupt(self) -> None:
        """Send an interrupt signal to the CLI (equivalent to Ctrl+C).

        Sets the _interrupting flag so the message loop handles the resulting
        error response gracefully (clean DEAD state, no error toast).

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        self._interrupting = True
        logger.info("Interrupting session %s", self.session_id)
        await self._client.interrupt()

    async def soft_interrupt(self) -> bool:
        """Interrupt the current turn but keep the session alive.

        Sends the SDK's interrupt control-request (the same signal ESC fires in
        the interactive CLI) and flags the message loop to treat the resulting
        error ``ResultMessage`` as a clean turn end — dropping back to
        ``USER_TURN`` with the SDK client kept connected, ready for the next
        message. Contrast with :meth:`interrupt` (used by
        :meth:`interrupt_or_kill`), which flags the loop to tear the session
        down to DEAD.

        Raises:
            RuntimeError: If the process is not started.
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        self._soft_interrupting = True
        logger.info("Soft-interrupting session %s", self.session_id)
        await self._client.interrupt()
        return True

    async def interrupt_or_kill(self, reason: str) -> None:
        """Try to interrupt the process gracefully, fall back to kill.

        When the process is actively working (ASSISTANT_TURN, STARTING), sends
        an interrupt signal so the CLI can finalize JSONL writes. When idle
        (USER_TURN), kills directly since there's nothing to interrupt.

        Args:
            reason: Reason for stopping (e.g., "manual", "apply-settings")
        """
        # Hard kill: skip the polite interrupt + grace window entirely.
        if (
            not self._force_kill.is_set()
            and self.state in (AgentState.ASSISTANT_TURN, AgentState.STARTING)
        ):
            try:
                self.kill_reason = reason
                await self.interrupt()
                # Wait for the clean DEAD, but bail early on a force-kill.
                if await self._race_force(self.wait_for_dead(), timeout=30.0):
                    return
                logger.debug(
                    "Interrupt timeout/forced for session %s, falling back to kill",
                    self.session_id,
                )
            except Exception:
                logger.debug(
                    "Interrupt failed for session %s, falling back to kill",
                    self.session_id,
                )
        await self.kill(reason=reason)

    async def set_model(self, sdk_model: str | None) -> None:
        """Change the AI model on the live SDK client.

        Calls the SDK's set_model() method with the full SDK model string
        (including "[1m]" suffix for extended context if applicable).

        Note: this does NOT update ``self.agent_settings``. The caller
        (apply_live_settings) is responsible for that.

        Raises:
            RuntimeError: If the process is not started
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        logger.debug(
            "Setting SDK model to '%s' for session %s",
            sdk_model,
            self.session_id,
        )
        await self._client.set_model(sdk_model)

    def _build_sdk_model(self, selected_model: str | None = None, context_max: int | None = None) -> str | None:
        """Build the full SDK model string from model shorthand and context_max.

        Uses the model registry to resolve versioned models to their full SDK
        name, and appends "[1m]" only when the model supports extended context.
        """
        from twicc.providers.helpers import get_provider_helpers

        model = selected_model if selected_model is not None else self.agent_settings.selected_model
        ctx = context_max if context_max is not None else self.agent_settings.context_max
        return get_provider_helpers(Provider.CLAUDE_CODE).resolve_sdk_model(model, ctx)

    @property
    def sdk_model(self) -> str | None:
        """The current full SDK model string (including context suffix)."""
        return self._build_sdk_model()

    @staticmethod
    def _is_permission_mode_change_ack(msg: object) -> bool:
        """Check if a message is the CLI ack for a set_permission_mode control request.

        The CLI emits: SystemMessage(subtype="status", data={..., "permissionMode": "...", "status": None})
        """
        return (
            isinstance(msg, SystemMessage)
            and msg.subtype == "status"
            and "permissionMode" in msg.data
        )

    @staticmethod
    def _is_model_change_ack(msg: object) -> bool:
        """Check if a message is the CLI ack for a set_model control request.

        The CLI emits: UserMessage(content="<local-command-stdout>Set model to ...</local-command-stdout>")
        """
        return (
            isinstance(msg, UserMessage)
            and isinstance(msg.content, str)
            and msg.content.startswith("<local-command-stdout>Set model to ")
        )

    @staticmethod
    def _is_compacting_status(msg: object) -> bool:
        """Check if a message signals that the CLI is compacting the conversation context.

        The CLI emits: SystemMessage(subtype="status", data={..., "status": "compacting"})
        """
        return (
            isinstance(msg, SystemMessage)
            and msg.subtype == "status"
            and msg.data.get("status") == "compacting"
        )

    @staticmethod
    def _is_settings_change_ack(msg: object) -> bool:
        """Check if a message is an ack from a settings control request (not real assistant activity)."""
        return ClaudeCodeAgent._is_permission_mode_change_ack(msg) or ClaudeCodeAgent._is_model_change_ack(msg)

    @staticmethod
    def _is_turn_activity(msg: object) -> bool:
        """Check if a message means the agent is producing a turn again.

        Only conversation traffic qualifies: the model's own output
        (``AssistantMessage``, ``StreamEvent``) and what is fed back to it
        (``UserMessage`` — tool results, injected content). Everything else,
        ``SystemMessage`` first of all, is a lifecycle marker the CLI may emit
        at any moment, including seconds *after* the final ``ResultMessage``:
        ``commands_changed`` (a skill or command file changed on disk, and it
        reaches every live CLI process, not just the one that edited it),
        ``mcp_status``, hook events, … Treating one of those as activity pins a
        finished session in ASSISTANT_TURN forever — no further ``ResultMessage``
        will ever come to release it. A whitelist keeps future subtypes harmless.
        The ``set_model`` ack is a ``UserMessage``, so it still needs its own
        exclusion.
        """
        return (
            isinstance(msg, (AssistantMessage, UserMessage, StreamEvent))
            and not ClaudeCodeAgent._is_settings_change_ack(msg)
        )

    async def _update_live_tasks(self, msg: SystemMessage) -> None:
        """Track live background subagents and drain Monitors, from CLI events.

        The CLI streams ``task_started`` when a background task launches,
        ``task_updated`` patches (``{"status": "completed", ...}``) and a
        terminal ``task_notification`` each time the task stops. Only
        ``task_type == "local_agent"`` entries are *tracked* here: agents are
        conversations with a bounded lifetime and a guaranteed terminal
        notification, whereas other task types (e.g. a ``run_in_background``
        Bash command) may legitimately outlive the whole conversation (dev
        servers, tails) and must not pin the session in ASSISTANT_TURN.

        A terminal event *releases* both collections, whatever the task type:
        a Monitor is a ``local_bash`` task, tracked from its own tool_result in
        ``_update_live_monitor_tasks``, and this system event is the only signal
        the CLI emits when it ends on its own (stream over, timeout reached).
        Missing it pins the session in ASSISTANT_TURN forever.
        """
        data = msg.data if isinstance(msg.data, dict) else {}
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return

        if msg.subtype == "task_started":
            if data.get("task_type") == "local_agent":
                self._live_background_tasks[task_id] = data.get("description") or ""
                logger.debug(
                    "Session %s: background agent %s started (%d live)",
                    self.session_id, task_id, len(self._live_background_tasks),
                )
            return

        # ``task_notification`` fires on every stop (completed, failed,
        # killed); ``task_updated`` with a terminal status arrives slightly
        # earlier — honour both, idempotently.
        terminal = msg.subtype == "task_notification"
        if not terminal and msg.subtype == "task_updated":
            patch = data.get("patch")
            status = patch.get("status") if isinstance(patch, dict) else None
            terminal = isinstance(status, str) and status not in ("pending", "queued", "running", "in_progress")
        if not terminal:
            return

        released = self._live_background_tasks.pop(task_id, None) is not None
        if released:
            logger.debug(
                "Session %s: background agent %s stopped (%d live)",
                self.session_id, task_id, len(self._live_background_tasks),
            )
        if task_id in self._live_monitor_tasks:
            self._live_monitor_tasks.discard(task_id)
            released = True
            logger.debug(
                "Session %s: Monitor %s stopped (%d live)",
                self.session_id, task_id, len(self._live_monitor_tasks),
            )
        if released and self._waiting_label_active:
            await self._refresh_waiting_label()

    @staticmethod
    def _message_content_text(content: Any) -> str:
        """Flatten SDK user-message content enough to identify Monitor signals."""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for block in content:
            block_content = block.get("content") if isinstance(block, dict) else getattr(block, "content", None)
            if isinstance(block_content, str):
                parts.append(block_content)
        return "\n".join(parts)

    async def _update_live_monitor_tasks(self, msg: UserMessage) -> None:
        """Track Monitor starts and explicit stops from the SDK.

        A Monitor acknowledgement carries ``tool_use_result.taskId`` and the
        literal ``Monitor started`` text. A successful ``TaskStop`` releases it
        right away: its result has ``task_id`` either in the structured tool
        result or in its JSON text payload.

        Every other ending — stream over, timeout reached, failure — only
        reaches us as a ``task_notification`` *system* event, handled by
        ``_update_live_tasks``. The CLI does write a ``<task-notification>`` XML
        block in its own JSONL transcript (which the compute pipeline reads),
        but never injects it into the SDK message stream, so there is nothing
        to parse here.
        """
        text = self._message_content_text(msg.content)
        tool_use_result = msg.tool_use_result
        if isinstance(tool_use_result, dict):
            task_id = tool_use_result.get("taskId")
            if isinstance(task_id, str) and _MONITOR_STARTED_RE.search(text):
                self._live_monitor_tasks.add(task_id)
                logger.debug(
                    "Session %s: Monitor %s started (%d live)",
                    self.session_id, task_id, len(self._live_monitor_tasks),
                )
                return

        stopped_task_id = tool_use_result.get("task_id") if isinstance(tool_use_result, dict) else None
        if not isinstance(stopped_task_id, str):
            try:
                tool_result_payload = orjson.loads(text)
            except orjson.JSONDecodeError:
                tool_result_payload = None
            if isinstance(tool_result_payload, dict):
                stopped_task_id = tool_result_payload.get("task_id")

        if isinstance(stopped_task_id, str) and stopped_task_id in self._live_monitor_tasks:
            self._live_monitor_tasks.remove(stopped_task_id)
            logger.debug(
                "Session %s: Monitor %s stopped by TaskStop (%d live)",
                self.session_id, stopped_task_id, len(self._live_monitor_tasks),
            )
            if self._waiting_label_active:
                await self._refresh_waiting_label()

    def _waiting_label_text(self) -> str | None:
        """Compose the "waiting" label from what holds the turn right now.

        Live background agents win ("waiting for N subagents"), then live
        Monitors ("monitoring"); with neither left but a pending
        ScheduleWakeup it becomes "waiting for scheduled wakeup (HH:MM)".
        ``None`` when nothing holds.

        Pure and stateless on purpose: both the broadcast path
        (:meth:`_refresh_waiting_label`) and the snapshot path
        (:meth:`current_status_label`) call it, so a client that connects
        late reads the same count a client that was there sees.
        """
        count = len(self._live_background_tasks)
        if count > 0:
            return f"waiting for {count} subagent{'s' if count > 1 else ''}"
        if self._live_monitor_tasks:
            return "monitoring"
        if self._has_pending_wakeup():
            return f"waiting for scheduled wakeup ({self._format_wakeup_time()})"
        return None

    def current_status_label(self) -> str | None:
        # Only while a turn is actually held: the moment the agent works
        # again, its own activity is the truth and the label must not
        # override it (``_clear_waiting_label`` drops the flag there).
        if not self._waiting_label_active:
            return None
        return self._waiting_label_text()

    async def _refresh_waiting_label(self) -> None:
        """Broadcast/refresh the "waiting" process label held in ASSISTANT_TURN.

        Shown by ``WorkingAssistantMessage`` instead of the bare "thinking"
        while a turn ended held in ASSISTANT_TURN (see the ResultMessage
        handler). Falls back to clearing when nothing holds — the activity
        that follows repaints the real status.
        """
        label = self._waiting_label_text()
        if label is None:
            await self._clear_waiting_label()
            return
        self._waiting_label_active = True
        await self._broadcast_process_label(label)

    async def _clear_waiting_label(self) -> None:
        """Drop the waiting label if it is currently shown (no-op otherwise)."""
        if not self._waiting_label_active:
            return
        self._waiting_label_active = False
        await self._broadcast_process_label("")

    def _note_schedule_wakeup(self, tool_use_result: dict) -> None:
        """Track the deadline of the latest ScheduleWakeup from its tool_result.

        ``tool_use_result`` is the tool_result payload of *any* tool; only a
        ScheduleWakeup carries ``scheduledFor`` (epoch ms) / ``stopped``, so any
        other tool's result is ignored (the existing deadline is untouched).

        Two ScheduleWakeup shapes:
        - a scheduling call carries ``scheduledFor`` (epoch ms). That value is
          authoritative — the harness rounds the requested ``delaySeconds`` up
          to the next whole minute, so recomputing from the delay lands ~a
          minute early. The deadline is what the ResultMessage handler compares
          against the clock to decide whether to hold ASSISTANT_TURN.
        - a ``stop=true`` call cancels any pending wake-up; its result reports
          ``stopped: true`` (and ``scheduledFor: 0``). We clear the deadline
          rather than stamping the bogus epoch-0 one that would otherwise read
          as "holding until 01:00" and never actually hold.

        A new scheduling call always overwrites the previous deadline.
        """
        # A stop cancels any pending wake-up. Handle it first, and off the
        # ``stopped`` flag alone, so it works even if a future SDK omits
        # ``scheduledFor`` from the stop result.
        if tool_use_result.get("stopped"):
            if self._pending_wakeup_at is not None:
                logger.debug(
                    "Session %s: ScheduleWakeup stopped — pending wake-up cleared",
                    self.session_id,
                )
            self._pending_wakeup_at = None
            return
        # Inner key is camelCase in the JSONL; tolerate snake_case defensively.
        scheduled_for_ms = tool_use_result.get("scheduledFor")
        if scheduled_for_ms is None:
            scheduled_for_ms = tool_use_result.get("scheduled_for")
        if not isinstance(scheduled_for_ms, (int, float)) or isinstance(scheduled_for_ms, bool):
            return
        # A non-positive deadline is a cancel / no-op, never a real fire time.
        if scheduled_for_ms <= 0:
            self._pending_wakeup_at = None
            return
        self._pending_wakeup_at = scheduled_for_ms / 1000.0
        logger.debug(
            "Session %s: ScheduleWakeup noted — holding until %s",
            self.session_id, self._format_wakeup_time(),
        )

    def _has_pending_wakeup(self) -> bool:
        """True while the last-seen ScheduleWakeup is still due to fire."""
        return self._pending_wakeup_at is not None and self._pending_wakeup_at > time.time()

    def _format_wakeup_time(self) -> str:
        """Local ``HH:MM`` of the pending wake-up deadline (for the label)."""
        # The label is deliberately local, per the docstring.
        return datetime.fromtimestamp(self._pending_wakeup_at).strftime("%H:%M")  # noqa: DTZ006

    async def apply_live_settings(self, settings: AgentSettings) -> None:
        """Apply live and idle setting changes to the live SDK client.

        Of the bundled :class:`AgentSettings`, only the fields that
        Claude Code classifies as live or idle are consulted here:
        ``permission_mode`` (live, applicable in any state), and
        ``selected_model`` + ``context_max`` (idle, applicable during
        USER_TURN). Other fields are ignored — they're either
        startup-only (handled by a restart elsewhere) or not consumed
        by the SDK on a live client.

        The SDK methods are called only when the requested value
        differs from the current one. Context_max is folded into the
        ``sdk_model`` string when set_model is called.
        """
        if settings.permission_mode != self.agent_settings.permission_mode:
            await self.set_permission_mode(settings.permission_mode)

        if self.state == AgentState.USER_TURN:
            new_sdk_model = self._build_sdk_model(settings.selected_model, settings.context_max)
            if new_sdk_model != self.sdk_model:
                await self.set_model(new_sdk_model)
                self.agent_settings = self.agent_settings._replace(
                    selected_model=settings.selected_model,
                    context_max=settings.context_max,
                )

    async def send(
        self,
        text: str,
        *,
        images: list[dict] | None = None,
        documents: list[dict] | None = None,
    ) -> bool:
        """Send a follow-up message to the process.

        Args:
            text: The message text to send
            images: Optional list of SDK ImageBlockParam objects
            documents: Optional list of SDK DocumentBlockParam objects

        Returns:
            ``True`` once the message was handed to the SDK (accepted for
            delivery); ``False`` when ``query()`` failed and the error was
            swallowed into the DEAD-state broadcast.

        Raises:
            RuntimeError: If the process is not running or not ready for input
        """
        if self._client is None:
            raise RuntimeError("Process not started")

        if self.state not in (AgentState.USER_TURN, AgentState.ASSISTANT_TURN):
            raise RuntimeError(f"Cannot send message in state {self.state}")

        logger.debug("Sending message to session %s", self.session_id)

        try:
            # Only transition and notify if we're not already in ASSISTANT_TURN
            # (if this case, Claude will queue the message, we stay in ASSISTANT_TURN)
            if self.state != AgentState.ASSISTANT_TURN:
                self._set_state(AgentState.ASSISTANT_TURN)
                self.last_activity = time.time()
                await self._notify_state_change()
            # A user message starts a fresh turn: the "waiting for N
            # subagents" label no longer describes what the agent is doing
            # (the hold after that turn's result re-establishes it if needed).
            await self._clear_waiting_label()

            # Build query prompt as async generator (streaming mode)
            query_prompt = await self._build_query_prompt(text, images, documents)
            await self._client.query(query_prompt)
            return True

        except Exception as e:
            # Handle error and transition to DEAD state. Do not re-raise as the
            # spec requires process errors to be logged and reported, never propagated.
            # The error is communicated to the frontend via WebSocket broadcast.
            # The caller learns delivery failed from the False return (no ack).
            await self._handle_error(f"Failed to send message: {e}", exc=e)
            return False

    async def kill(self, reason: str = "manual") -> None:
        """Terminate the process gracefully.

        This cancels the message loop and kills the underlying Claude CLI process.
        Safe to call multiple times or on an already dead process.

        Args:
            reason: Reason for killing the process (e.g., "manual", "shutdown")
        """
        logger.debug(
            "Kill requested for session %s (reason: %s)", self.session_id, reason
        )

        if self.state == AgentState.DEAD:
            logger.debug("Session %s already dead, skipping kill", self.session_id)
            return

        self.kill_reason = reason

        # Cancel any pending request Future to avoid asyncio warnings
        self._cancel_all_pending_futures()

        # Cancel the message loop first so it doesn't interpret the imminent
        # transport close as a real error.
        if self._message_loop_task is not None:
            self._message_loop_task.cancel()
            try:
                await self._message_loop_task
            except asyncio.CancelledError:
                pass
            self._message_loop_task = None

        await self._shutdown_sdk_client()

        # Update state
        self.last_activity = time.time()
        self._first_turn_done_event.set()  # Unblock any waiters (cron restart)
        await self._transition_to_dead()

    async def _run_message_loop(self) -> None:
        """Background task that consumes messages and tracks state.

        This loop processes messages from Claude. We don't use the message content
        (the JSONL watcher handles that), but we need to consume them to detect
        when Claude is done responding (ResultMessage).
        """
        if self._client is None:
            return

        logger.debug("Entering message loop for session %s", self.session_id)

        # State variables when streaming is about assistant messages and thinking
        current_stream_message_id: str | None = None
        current_stream_block_index: int | None = None
        current_stream_block_type: str | None = None
        # Fallback: if AssistantMessage arrives after content_block_stop,
        # the current_stream_block_* vars are already cleared. We keep the
        # last finished block info so we can still send the END event.
        last_finished_block_index: int | None = None
        last_finished_block_type: str | None = None

        # State variables when streaming is about tools
        current_stream_tool_id: str | None = None
        current_stream_tool_name: str | None = None
        current_stream_tool_input_raw: str = ""
        current_stream_tool_input_cleaned: dict | None = None

        try:
            async for msg in self._client.receive_messages():
                self.last_activity = time.time()

                # ``parse_message`` returns None for unknown / forward-compat
                # message types (e.g. ``command_lifecycle``). The unpatched SDK
                # and the logging patch both drop these, but guard the loop
                # regardless: a None carries no state, and letting it fall
                # through to the ASSISTANT_TURN catch-all below would misread a
                # lifecycle marker trailing the final ResultMessage as activity
                # and pin a finished session in ASSISTANT_TURN forever.
                if msg is None:
                    continue

                # Keep the live-background-agents set and the live-Monitors set
                # current on every task lifecycle event — consumed by the
                # ResultMessage branch below to hold ASSISTANT_TURN while either
                # still runs.
                if isinstance(msg, SystemMessage):
                    await self._update_live_tasks(msg)

                # A ScheduleWakeup's tool_result carries the authoritative fire
                # time (``scheduledFor``, epoch ms). The harness rounds the
                # requested delay up to the next whole minute, so we read this
                # rather than recompute from ``delaySeconds`` (which lands ~a
                # minute early). Recorded so the ResultMessage branch can hold
                # ASSISTANT_TURN until it fires.
                if isinstance(msg, UserMessage) and isinstance(msg.tool_use_result, dict):
                    self._note_schedule_wakeup(msg.tool_use_result)

                if isinstance(msg, UserMessage):
                    await self._update_live_monitor_tasks(msg)

                if isinstance(msg, StreamEvent):
                    # Stream events only carry the parent's own model output
                    # (subagent traffic arrives as full messages), so the
                    # parent is visibly active again: drop the waiting label.
                    await self._clear_waiting_label()

                    event = msg.event
                    event_type = event.get("type")
                    match event_type:
                        # HANDLE SIMPLE MESSAGES AND THINKING
                        case "message_start" if (
                            isinstance(message := event.get("message"), dict)
                            and message.get("type") == "message"
                            and message.get("role") == "assistant"
                            and (message_id := message.get("id"))
                        ):
                            # entering a new streaming message with id
                            current_stream_message_id = message_id
                        case "content_block_start" if (
                            current_stream_message_id
                            and (block_index := event.get("index")) is not None
                            and isinstance(block := event.get("content_block"), dict)
                            and (block_type := block.get("type")) in ("text", "thinking")
                        ):
                            current_stream_block_index = block_index
                            current_stream_block_type = block_type
                            last_finished_block_index = None
                            last_finished_block_type = None
                            # logger.debug(f"[Stream {current_stream_block_type} msg_id={current_stream_message_id} index={current_stream_block_index}] => START")
                            await self._broadcast_stream_event({
                                "type": "stream_block_start",
                                "session_id": self.session_id,
                                "message_id": current_stream_message_id,
                                "block_index": current_stream_block_index,
                                "block_type": current_stream_block_type,
                            })
                        case "content_block_delta" if (
                            current_stream_block_type
                            and isinstance(delta := event.get("delta"), dict)
                            and delta.get("type") == f"{current_stream_block_type}_delta"
                            and (delta_text := delta.get(current_stream_block_type))
                        ):
                            # logger.debug(f"[Stream {current_stream_block_type} msg_id={current_stream_message_id} index={current_stream_block_index}] `{delta_text}`")
                            await self._broadcast_stream_event({
                                "type": "stream_block_delta",
                                "session_id": self.session_id,
                                "message_id": current_stream_message_id,
                                "block_index": current_stream_block_index,
                                "block_type": current_stream_block_type,
                                "text": delta_text,
                            })
                        case "content_block_stop" if current_stream_block_type:
                            # logger.debug(f"[Stream {current_stream_block_type} msg_id={current_stream_message_id} index={current_stream_block_index}] => STOP")
                            await self._broadcast_stream_event({
                                "type": "stream_block_stop",
                                "session_id": self.session_id,
                                "message_id": current_stream_message_id,
                                "block_index": current_stream_block_index,
                                "block_type": current_stream_block_type,
                            })
                            last_finished_block_index = current_stream_block_index
                            last_finished_block_type = current_stream_block_type
                            current_stream_block_index = None
                            current_stream_block_type = None
                        case "message_stop" if current_stream_message_id:
                            current_stream_message_id = None
                            last_finished_block_index = None
                            last_finished_block_type = None

                        # HANDLE TOOL CALLS
                        case "content_block_start" if (
                            (block_index := event.get("index")) is not None
                            and isinstance(block := event.get("content_block"), dict)
                            and (block_type := block.get("type")) == "tool_use"
                            and (current_stream_tool_name := block.get("name"))
                            and (current_stream_tool_id := block.get("id"))
                        ):
                            current_stream_tool_input_raw = ""
                            current_stream_tool_input_cleaned = None
                            # Track the tool from its very first event so the delta
                            # handler's "skip if not tracked" guard can distinguish
                            # late zombie deltas from this tool's normal stream.
                            self._active_tools[current_stream_tool_id] = {
                                "name": current_stream_tool_name,
                                "input": {},
                                "streaming": True,
                            }
                            self._last_started_tool_id = current_stream_tool_id
                            # logger.debug(
                            #     "[ToolUse block_start index=%s tool=%s id=%s] => START",
                            #     block_index, current_stream_tool_name, current_stream_tool_id,
                            # )
                            await self._broadcast_process_tools()
                        case "content_block_delta" if (
                            current_stream_tool_id
                            and current_stream_tool_id in self._active_tools
                            and isinstance(delta := event.get("delta"), dict)
                            and delta.get("type") == "input_json_delta"
                            and (chunk := delta.get("partial_json"))
                        ):
                            current_stream_tool_input_raw += chunk
                            try:
                                parsed_input = json_repair.loads(current_stream_tool_input_raw)
                            except Exception as exc:
                                logger.exception(f"Failed to parse tool input: {exc}")
                            else:
                                if isinstance(parsed_input, dict):
                                    cleaned = filter_tool_input(current_stream_tool_name, parsed_input)
                                    if cleaned != current_stream_tool_input_cleaned:
                                        current_stream_tool_input_cleaned = cleaned
                                        self._active_tools[current_stream_tool_id]["input"] = cleaned
                                        # logger.debug(
                                        #     "[ToolUse content_block_delta tool=%s id=%s active_tools=%d] => input=%s",
                                        #     current_stream_tool_name, current_stream_tool_id,
                                        #     len(self._active_tools), cleaned,
                                        # )
                                        await self._broadcast_process_tools()

                        case "content_block_stop" if current_stream_tool_id:
                            # logger.debug(
                            #     "[ToolUse block_stop tool=%s id=%s] => STOP",
                            #     current_stream_tool_name, current_stream_tool_id,
                            # )
                            # Flip streaming=False as soon as the block ends — input is
                            # final, no more deltas. This is the earliest reliable point;
                            # PreToolUse-based flip stays as a redundant safety net.
                            if current_stream_tool_id in self._active_tools:
                                entry = self._active_tools[current_stream_tool_id]
                                if entry.get("streaming"):
                                    entry["streaming"] = False
                                    await self._broadcast_process_tools()
                            current_stream_tool_id = None
                            current_stream_tool_name = None
                            current_stream_tool_input_raw = ""
                            current_stream_tool_input_cleaned = None

                elif (
                    isinstance(msg, AssistantMessage)
                    and msg.message_id == current_stream_message_id
                    and (current_stream_block_type or last_finished_block_type)
                ):
                    # AssistantMessage usually arrives before content_block_stop (current_stream_block_*
                    # still set), but may arrive after (fallback to last_finished_block_*).
                    end_block_index = current_stream_block_index if current_stream_block_type else last_finished_block_index
                    end_block_type = current_stream_block_type or last_finished_block_type

                    # logger.debug(f"[Stream {end_block_type} msg_id={current_stream_message_id} index={end_block_index}] => END: uuid = {msg.uuid}")
                    await self._broadcast_stream_event({
                        "type": "stream_block_end",
                        "session_id": self.session_id,
                        "message_id": current_stream_message_id,
                        "block_index": end_block_index,
                        "block_type": end_block_type,
                        "uuid": msg.uuid,
                    })
                    # Clear last_finished so we don't re-match on a subsequent AssistantMessage
                    last_finished_block_index = None
                    last_finished_block_type = None

                elif isinstance(msg, AssistantMessage) and msg.error == "authentication_failed":
                    logger.error(
                        "Authentication failed for session %s — Claude Code CLI is not logged in",
                        self.session_id,
                    )
                    self._cancel_all_pending_futures()
                    self.kill_reason = "auth_required"
                    self.last_activity = time.time()
                    self._first_turn_done_event.set()
                    await self._transition_to_dead()
                    # Flip the global auth state immediately. The credentials file
                    # may still exist on disk, but the SDK has just told us the
                    # token is no longer accepted — that's the authoritative signal.
                    from twicc.providers.claude_code.auth import mark_unauthenticated_and_broadcast
                    await mark_unauthenticated_and_broadcast()
                    await self._shutdown_sdk_client()
                    return

                if isinstance(msg, ResultMessage):
                    # Claude finished responding, ready for user input
                    was_soft_interrupt = False
                    if msg.is_error:
                        if self._interrupting:
                            # Expected error after interrupt — clean shutdown.
                            # kill_reason is already set by interrupt_or_kill().
                            # No self.error = avoids the error toast on the frontend.
                            logger.info(
                                "Session %s interrupted cleanly (subtype=%s)",
                                self.session_id,
                                msg.subtype,
                            )
                            self._cancel_all_pending_futures()
                            self.last_activity = time.time()
                            self._first_turn_done_event.set()
                            await self._transition_to_dead()
                            await self._shutdown_sdk_client()
                            return

                        if self._soft_interrupting:
                            # User interrupted the current turn but asked to keep
                            # the session alive. Treat this expected error result
                            # as a clean turn end: reset the flag, drop any
                            # pending permission asks from the aborted turn, and
                            # fall through to the normal USER_TURN handling below
                            # — no shutdown, the loop keeps running for the next
                            # message. ``_interrupting`` (kill) is checked first,
                            # so an interrupt-to-kill still wins over a soft one.
                            self._soft_interrupting = False
                            was_soft_interrupt = True
                            logger.info(
                                "Session %s soft-interrupted; back to USER_TURN (subtype=%s)",
                                self.session_id,
                                msg.subtype,
                            )
                            self._cancel_all_pending_futures()
                        else:
                            # Log full ResultMessage details for debugging
                            logger.error(
                                "Claude Code error for session %s: result=%r, subtype=%s, "
                                "num_turns=%s, duration_ms=%s",
                                self.session_id,
                                msg.result,
                                msg.subtype,
                                msg.num_turns,
                                msg.duration_ms,
                            )
                            await self._handle_error(
                                f"Claude reported error: {msg.result or 'Unknown error'}"
                            )
                            return

                    # Signal first USER_TURN for ProcessRun lifecycle
                    if not self._first_user_turn_reached:
                        self._first_user_turn_reached = True
                        self._first_turn_done_event.set()

                    # Turn ended — every tool of this turn is finished, so any
                    # remaining _active_tools entry is stale (typically resurrected
                    # by a late content_block_delta after its PostToolUse fired).
                    if self._active_tools:
                        self._active_tools.clear()
                        await self._broadcast_process_tools()

                    hold_for_background = bool(self._live_background_tasks)
                    hold_for_monitor = bool(self._live_monitor_tasks)
                    hold_for_wakeup = self._has_pending_wakeup()
                    if (hold_for_background or hold_for_monitor or hold_for_wakeup) and not was_soft_interrupt:
                        # The async-by-default CLI ends its turn as soon as the
                        # parent stops talking — while background agents it
                        # spawned keep running, and/or a ScheduleWakeup it set is
                        # still pending; either way the harness re-invokes it for
                        # a follow-up turn (a terminal task-notification, or the
                        # wake-up firing). Holding ASSISTANT_TURN here is the
                        # single choke point that keeps every USER_TURN consumer
                        # quiet — process-state broadcasts, external "finished
                        # working" pushes, browser notifications, the idle
                        # auto-stop — until a turn ends with nothing left to wait
                        # on. A deliberate soft interrupt bypasses the hold: the
                        # user asked for control, show the session as theirs.
                        if hold_for_background:
                            logger.info(
                                "Session %s: turn ended with %d background agent(s) still "
                                "running — holding ASSISTANT_TURN (%s)",
                                self.session_id,
                                len(self._live_background_tasks),
                                ", ".join(
                                    f"{tid} ({desc})" if desc else tid
                                    for tid, desc in self._live_background_tasks.items()
                                ),
                            )
                        elif hold_for_monitor:
                            logger.info(
                                "Session %s: turn ended with %d live Monitor task(s) — "
                                "holding ASSISTANT_TURN (%s)",
                                self.session_id,
                                len(self._live_monitor_tasks),
                                ", ".join(sorted(self._live_monitor_tasks)),
                            )
                        else:
                            logger.info(
                                "Session %s: turn ended with a pending scheduled wake-up "
                                "(~%ds out) — holding ASSISTANT_TURN",
                                self.session_id,
                                int(self._pending_wakeup_at - time.time()),
                            )
                        if self.state != AgentState.ASSISTANT_TURN:
                            self._set_state(AgentState.ASSISTANT_TURN)
                            await self._notify_state_change()
                        await self._refresh_waiting_label()
                    else:
                        # The process_state broadcast that follows drops any
                        # label on the frontend; just reset the local flag.
                        self._waiting_label_active = False
                        self._set_state(AgentState.USER_TURN)
                        await self._notify_state_change()

                elif self._is_compacting_status(msg):
                    # CLI is compacting context — notify frontend to show "compacting" label
                    await self._broadcast_process_label("compacting")

                elif self.state != AgentState.ASSISTANT_TURN and self._is_turn_activity(msg):
                    # Enforce assistant state if real conversation traffic came
                    # after the ResultMessage (a turn the CLI started on its own:
                    # a cron firing, a background task notification, …).
                    self._set_state(AgentState.ASSISTANT_TURN)
                    await self._notify_state_change()

        except asyncio.CancelledError:
            # Normal cancellation during shutdown
            raise
        except (ClaudeSDKError, Exception) as e:
            # If the process was already killed intentionally (apply-settings, manual, shutdown),
            # the message loop error is expected (CLI exiting) — don't treat it as an error.
            if self.state == AgentState.DEAD and self.kill_reason and self.kill_reason != "error":
                logger.debug(
                    "Message loop ended after intentional kill for session %s (kill_reason=%s): %s",
                    self.session_id, self.kill_reason, e,
                )
                return
            prefix = "SDK error" if isinstance(e, ClaudeSDKError) else "Unexpected error in message loop"
            await self._handle_error(f"{prefix}: {e}", exc=e)

    async def _handle_error(self, error_message: str, exc: Exception | None = None) -> None:
        """Handle an error by transitioning to DEAD state.

        Args:
            error_message: Description of what went wrong
            exc: Optional exception that caused the error (logged with full traceback)
        """
        logger.error(
            "Process %s for session %s died: %s",
            self.project_id,
            self.session_id,
            error_message,
            exc_info=exc,
        )

        # Cancel any pending request Future to avoid asyncio warnings
        self._cancel_all_pending_futures()

        self.error = error_message
        self.kill_reason = "error"
        self.last_activity = time.time()
        self._first_turn_done_event.set()  # Unblock any waiters (cron restart)

        await self._transition_to_dead()

        await self._shutdown_sdk_client()

    async def _shutdown_sdk_client(self) -> None:
        """Release the SDK client and its CLI subprocess.

        Calls ``ClaudeSDKClient.disconnect()`` — the SDK's intended cleanup
        path — so it can cancel its detached read / control-handler tasks,
        close the message stream, and shut the transport (which kills the
        Claude CLI subprocess). The call is bounded by a 5 s timeout and
        shielded against caller cancellation so a hanging or misbehaving
        disconnect can't propagate cancellation up to our task.

        Earlier SDK versions held those detached tasks inside an anyio
        ``TaskGroup`` whose ``CancelScope`` had task affinity — closing the
        transport from a task other than the one that opened it raised
        ``RuntimeError: Attempted to exit cancel scope in a different task
        than it was entered in``. The SDK now spawns them via plain
        ``loop.create_task`` (see ``claude_agent_sdk/_internal/_task_compat``),
        so ``disconnect()`` is safe to call from any task in the version
        range we pin.

        If disconnect fails or times out, falls back to killing the
        subprocess directly via psutil so we never leave the CLI running.
        We deliberately skip the psutil kill on the success path to avoid a
        narrow PID-reuse window: between transport close and our kill, the
        OS may have already recycled the pid for an unrelated process.

        Safe to call when ``_client`` is already ``None`` — no-op.
        """
        client = self._client
        if client is None:
            return
        pid = self.get_pid()
        self._client = None
        try:
            await asyncio.wait_for(
                asyncio.shield(client.disconnect()),
                timeout=5.0,
            )
            return
        except TimeoutError:
            logger.warning(
                "SDK disconnect() timed out for session %s — falling back to psutil kill",
                self.session_id,
            )
        except Exception:
            logger.exception(
                "SDK disconnect() failed for session %s — falling back to psutil kill",
                self.session_id,
            )
        if pid is not None:
            await self._kill_system_process(pid)

    async def _broadcast_process_tools(self) -> None:
        """Broadcast the current list of in-progress tools for the status display."""
        if await self._is_session_hidden():
            return
        channel_layer = get_channel_layer()
        await channel_layer.group_send(
            "updates",
            {"type": "broadcast", "data": {
                "type": "process_tools",
                "session_id": self.session_id,
                "tools": self._serialize_active_tools(),
                "last_started_id": self._last_started_tool_id,
            }},
        )
