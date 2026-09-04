"""
Compute pipeline for Codex sessions.

Each Codex JSONL line is wrapped in ``{timestamp, type, payload}``; this
pass turns the wrapper into a TwiCC :class:`~twicc.core.enums.ItemKind`
and, for tool calls, lets the inherited base orchestration build the
``ToolResultLink`` rows that pair a call with its result.

Classification rules (any change MUST bump CODEX_COMPUTE_VERSION):

Codex persists chat messages and tool outcomes as canonical completed
items — ``event_msg.item_completed`` lines whose ``payload.item.type`` is a
PascalCase ``TurnItem`` — since 0.151 (paginated history; legacy rollouts
are migrated to the same shape). The readers live in :mod:`.canonical`.

- ``item_completed`` / ``UserMessage`` → ``USER_MESSAGE`` (text joined
  from the ``text`` entries; ``image`` / ``local_image`` entries make an
  attachment-only prompt visible too)
- ``item_completed`` / ``AgentMessage`` → ``ASSISTANT_MESSAGE``
- The first ``response_item.message`` (role=user) carrying a
  ``<codex_internal_context source="goal">`` block after a goal set/update is
  rewritten by :meth:`_transform_inline_provider` into a TwiCC-private
  canonical ``UserMessage`` of text ``/goal <objective>``. Later continuation
  prompts for the same goal stay ``SYSTEM`` / ``DEBUG_ONLY`` so the command is
  not repeated between every assistant response. The original payload of the
  visible boundary is kept under ``twiccOriginalContent``.
- A ``response_item.message`` (role=user) that is a TwiCC-injected command
  (via ``thread/inject_items`` — ``/goal clear`` and ``/compact``, for which
  Codex writes no "the user asked" rollout line of its own) is likewise
  rewritten into a private canonical ``UserMessage`` carrying that command.
  Same ``twiccOriginalContent`` preservation.
- A ``response_item.message`` carrying TwiCC's terminal provider-error marker
  is rewritten into ``twicc_provider_error`` → ``API_ERROR`` (→ ``ALWAYS``).
  Codex only emits these errors on its live app-server stream, so the agent
  injects the marker before teardown to make the recovery block durable.
- A ``UserMessage`` starting with ``<twicc-resume>`` is TwiCC's hidden
  mid-turn recovery instruction → ``SYSTEM`` (→ ``DEBUG_ONLY``).
- ``item_completed`` / ``FileChange`` and ``McpToolCall`` → kind stays
  ``None``; routed to ``DEBUG_ONLY`` via :meth:`is_tool_result_item`. Pairs
  with the matching ``function_call`` / ``custom_tool_call`` by the item
  ``id`` (the call_id). These items carry the structured outcome of the
  tool (``changes`` map, ``CallToolResult``, …) and coexist as a second
  :class:`ToolResultLink` row alongside the LLM-facing
  ``function_call_output`` for the same tool_use_id. ``WebSearch`` is
  intentionally not a result — see the ``response_item.web_search_call``
  rule below. Every other completed item (``Reasoning``,
  ``CommandExecution``, ``Plan``, ``FunctionCallOutput``, …) duplicates a
  raw ``response_item`` TwiCC already reads and stays ``SYSTEM``.
- ``item_completed`` / ``ImageGeneration`` (native hosted call) or
  ``Extension`` with ``kind == "image_gen.generation"`` (what the migration
  emits) → ``IMAGE`` (-> ``ALWAYS``). Codex writes it right after
  generating an image; the item carries
  the ``revised_prompt`` (the actual prompt the image generator received
  after the model rewrote the user's request), the base64-encoded PNG
  ``result``, and the on-disk ``saved_path`` (typically under
  ``<codex home>/generated_images/<session>/<call_id>.png``). The matching
  ``response_item.image_generation_call`` duplicates ``revised_prompt``
  and ``result`` (no ``saved_path``), so we ignore it — it falls through
  to ``SYSTEM`` / ``DEBUG_ONLY`` like any other unhandled response_item.
  No tool_use → tool_result pairing: the event alone carries everything
  the frontend needs to render the image, the prompt and the path inline,
  and the image is already fully baked when the line lands (no streaming,
  no spinner). The matching frontend component is
  ``items/codex/ImageGeneration.vue``.
- ``event_msg.exec_command_end`` is intentionally **not** in the list:
  Codex CLI no longer persists it (TUI sets
  ``persist_extended_history=false`` since 2026-04-30) so we
  reconstruct the same surface from the chain of
  ``function_call_output`` lines instead — the original ``exec_command``
  output plus every ``write_stdin`` polling output sharing the same
  unified-exec process id (called ``session_id`` by Codex,
  ``exec_command_id`` here).
- ``response_item.function_call`` / ``custom_tool_call`` /
  ``local_shell_call`` / ``web_search_call`` → ``TOOL_USE`` (->
  ``COLLAPSIBLE``), except ``function_call name=write_stdin`` and the
  read-only Goal probe ``function_call name=get_goal`` which are
  bucketed as ``SYSTEM`` (no tool card). ``write_stdin``'s
  ``function_call_output`` is rebound to the parent ``exec_command``'s
  ``call_id`` via :meth:`CodexSessionCompute.remap_tool_result_id`;
  ``get_goal``'s output is already DEBUG_ONLY via ``is_tool_result_item``.
  ``local_shell_call``
  doesn't carry a ``name`` field — its tool name is the sub_type itself
  (``"local_shell_call"``), supplied via
  :data:`_NATIVE_TOOL_NAME_BY_SUB_TYPE` in
  :meth:`extract_tool_use_entries` / :meth:`analyze_content`. Its result
  is a single ``function_call_output`` paired by ``call_id`` (no chained
  ``write_stdin`` polls, and unlike ``exec_command`` it does **not**
  emit a Codex unified-exec status trailer — instead its ``output`` is
  a JSON-encoded string ``{"output":"<body>","metadata":{"exit_code":N,
  "duration_seconds":N.N}}`` produced by
  ``format_exec_output_for_model_structured`` in
  ``codex-rs/core/src/tools/mod.rs``). The exit-code surface for
  :class:`ToolResultLink.is_error` therefore goes through
  :func:`_structured_exec_output_error` (JSON decode + ``metadata.exit_code``
  test), and :meth:`compute_link_extra` flags the matching result row as
  terminated on arrival so the frontend stops the spinner.
  ``web_search_call`` is a **resultless** tool (see
  :data:`_RESULTLESS_TOOL_SUB_TYPES`): no ``call_id`` is serialised
  on the call, so the matching canonical ``WebSearch`` item can't be
  paired from the JSONL and is intentionally ignored (not a result item
  for :func:`canonical.canonical_result_item`). The tool_use card stands alone
  — no ``ToolResultLink``, no spinner; ``analyze_content`` emits a
  visible-but-unpaired :class:`ContentAnalysis` for it.
- ``response_item.{function_call_output, custom_tool_call_output}`` →
  kind stays ``None`` (-> ``DEBUG_ONLY``). Pairs as a tool_result.
  For exec_command long-running shells the chain accumulates one row
  per polling write_stdin; for everything else there's a single row
  (plus the matching event_msg.*_end when applicable).
- Code mode (GPT-5.6+): ``custom_tool_call name=exec`` (JS script) →
  ``TOOL_USE``, except a single resolved nested ``write_stdin`` → ``SYSTEM``
  and rebound to its nested ``exec_command`` parent; ``function_call name=wait`` → ``SYSTEM`` (via
  :data:`_NON_TOOL_FUNCTION_NAMES`), its output chunks rebound to the
  owning ``exec`` through the cell map. A single resolved nested
  ``update_plan`` also refreshes :attr:`Session.tasks` without changing the
  outer wrapper's tool identity — see
  :data:`_CODE_MODE_EXEC_TOOL` and ``code_mode_script.py``.
- A successful Goal-tool result carrying ``{goal: {status: ...}}`` is also a
  goal-lifecycle event. This is the completion signal emitted by GPT-5.6 code
  mode when no final ``thread_goal_updated`` line is persisted.
- top-level ``compacted`` → ``COMPACT_SUMMARY`` (lands at ``ALWAYS``).
  Codex CLI writes this line on auto-compaction; the payload carries
  a ``replacement_history`` of the messages that were summarized plus
  an encrypted summary in
  ``replacement_history[-1].encrypted_content``. We pick this wrapper
  over the redundant ``event_msg.context_compacted`` event because
  the encrypted field gives us a future-proof landing spot if Codex
  ever ships a readable summary. The matching
  ``event_msg.context_compacted`` line stays bucketed as ``SYSTEM``.
- everything else (``session_meta``, ``turn_context``, other
  ``response_item`` subtypes, other ``event_msg`` subtypes without
  ``call_id`` including ``event_msg.context_compacted``) → ``SYSTEM``
  (lands at ``DEBUG_ONLY``).

The ``call_id`` carried by every line above is the pairing key,
stored as-is in ``ToolResultLink.tool_use_id`` (analogous to Claude's
``tool_use_id``).

Token counts and costs are computed by
:meth:`CodexSessionCompute.compute_item_cost_and_usage` from
``event_msg.token_count`` events: ``last_token_usage`` is mapped to
the cross-provider :class:`TokenUsage` via :func:`to_token_usage` and
priced with the model carried by the running ``turn_context``;
``info.total_token_usage.total_tokens`` acts as a monotonic clock to
filter non-billable events (bootstrap snapshot, inter-turn
re-emission, compaction-zero) — see the method docstring for details.

Subagent linkage is wired for both multi-agent generations: the
``(spawn_agent call_id, subagent thread id)`` pair comes from the spawn
ack on v1 and from the canonical ``SubAgentActivity`` item on v2,
and the completion signal rebound as the spawn's second
``ToolResultLink`` is the ``<subagent_notification>`` user message on
v1, the ``FINAL_ANSWER`` inter-agent message on v2 — see
:data:`_SPAWN_AGENT_FUNCTION_NAME` and its neighbours. Custom titles
and session-start detection remain out of scope at this stage.
Runtime environment fields are partially
wired: ``cwd`` and ``cwd_git_branch`` come from the opening
``session_meta`` line, ``cwd`` plus ``model`` come from each
``turn_context`` line, and ``context_max`` comes from the
``event_msg.task_started.model_context_window`` emitted at every turn
start (the base orchestrator's "last non-null wins" rule means a
mid-session ``cd`` / model swap / window change is reflected on
``Session.cwd`` / ``Session.model`` / ``Session.context_max``).
``slug`` carries a subagent's ``agent_nickname`` when the opening
``session_meta`` declares one (top-level sessions have none).
File-change stats are
wired for ``apply_patch`` (aggregated ``+`` / ``-`` from the
``FileChange.changes`` map). Canonical ``FileChange`` items
are also enriched in-place with an ``original_files`` map
(``{abs_path: pre_patch_content}``) when the matching capture is in
cache — see :meth:`transform_tool_result_with_cache` and the
``agent/original_files_cache.py`` module. Other hooks return empty /
no-op values so the inherited base machinery (group state, batch
compute, title extraction) still runs cleanly.
"""

from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime
from typing import ClassVar, NamedTuple

import orjson
from django.db.models import Q

from twicc.core.enums import ItemKind, Provider
from twicc.core.models import SessionItem
from twicc.paths import get_artifacts_dir
from twicc.pricing import calculate_line_context_usage
from twicc.providers.goals import GOAL_STATE_ACTIVE, GOAL_STATE_COMPLETED, GoalEvent
from twicc.providers.plan_docs import DocEditEvent, extract_shell_write_targets, is_plan_doc_path
from twicc.providers.compute_base import (
    _EMPTY_ANALYSIS,
    _EMPTY_FILE_PATHS,
    _EMPTY_TASK_TOOL_USES,
    _EMPTY_TOOL_USE_ENTRIES,
    BaseSessionCompute,
    ContentAnalysis,
    INSERT_SCREENSHOT_TAG_RE,
    ToolResultInfo,
    ToolUseEntry,
    parse_timestamp_to_datetime,
    substitute_insert_screenshot_tags,
)

from .agent.original_files_cache import pop_original_files
from .canonical import (
    agent_message_text,
    build_twicc_agent_message,
    build_twicc_user_message,
    canonical_call_id,
    canonical_result_item,
    completed_item,
    image_generation,
    user_message_is_visible,
    user_message_text,
)
from .code_mode_script import parse_code_mode_output, parse_code_mode_script
from .pricing import extract_model_info, to_token_usage
from .provider_errors import (
    PROVIDER_ERROR_MARKER,
    CodexProviderError,
    parse_provider_error_marker,
)

logger = logging.getLogger(__name__)


# Keys at the wrapper level. Every Codex JSONL line is
# ``{"timestamp": ..., "type": ..., "payload": {...}}`` so we always
# go through ``payload`` to reach Codex-specific fields.
_TYPE_EVENT_MSG = "event_msg"
_TYPE_RESPONSE_ITEM = "response_item"
_TYPE_TWICC_PROVIDER_ERROR = "twicc_provider_error"
# ``session_meta`` is the opening line of a Codex JSONL (one per
# session) — carries the initial cwd + native git branch. ``turn_context``
# is emitted on every turn — carries the current cwd and model. Both
# feed :meth:`CodexSessionCompute.extract_runtime_fields`.
_TYPE_SESSION_META = "session_meta"
_TYPE_TURN_CONTEXT = "turn_context"
# ``compacted`` is the top-level wrapper Codex CLI writes when it auto-
# compacts the rolling context. The payload carries a ``replacement_history``
# of the messages that were summarized plus a trailing
# ``{"type":"compaction","encrypted_content":"..."}`` entry — the
# summary itself is encrypted, so we can't surface a body, only mark
# the item as a ``COMPACT_SUMMARY`` so the UI shows the standard
# divider. The matching ``event_msg.context_compacted`` event is
# redundant for our purposes and stays bucketed as SYSTEM.
_TYPE_COMPACTED = "compacted"
_PAYLOAD_AGENT_MESSAGE = "agent_message"
# ``turn_context.payload.collaboration_mode.mode`` value for Codex's Plan
# collaboration mode (entered via TwiCC's ``/plan`` hardcoded command). Every
# turn_context carries the effective mode explicitly (``"default"``
# otherwise), and the mode is sticky: all turns after a ``/plan`` report it
# too — which is why the prefix restoration below keys on TRANSITIONS, not on
# the mode itself.
_PLAN_COLLABORATION_MODE = "plan"
# ``event_msg.thread_goal_updated`` carries the current goal snapshot
# (``payload.goal.status``). Used as the live signal that a goal continuation
# stopped — see :meth:`CodexSessionCompute.is_goal_continuation_stopped`.
_PAYLOAD_THREAD_GOAL_UPDATED = "thread_goal_updated"
_GOAL_STATUS_ACTIVE = "active"
_GOAL_STATUS_COMPLETE = "complete"
# ``event_msg.token_count`` is the only Codex line that carries usage
# counters (``info.last_token_usage`` for the last LLM call,
# ``info.total_token_usage`` for the cumulative session totals). Read
# by :meth:`CodexSessionCompute.compute_item_cost_and_usage`.
_PAYLOAD_TOKEN_COUNT = "token_count"
# ``event_msg.task_started`` is emitted at the start of every turn and
# carries the active model's context window in ``model_context_window``.
# That value is **not** the nominal input window of the model: Codex
# CLI publishes its internal compaction threshold instead — 95% of the
# nominal input window, the rest left as headroom for the auto-compact
# logic. The nominal input window is per-model (mirrored in
# ``CodexModelExtra.context_window``): 272K for the pre-5.6 models
# (advertised 400K total = 272K input + 128K output reserved, so the
# JSONL reports 272_000 × 0.95 = 258_400) and 372K for the GPT-5.6
# tiers (reported as 353_400). We divide back by the factor below to
# recover the nominal window the user expects to see in the UI (and
# to keep the ring meaningful across the auto-compact step). Read by
# :meth:`CodexSessionCompute.extract_runtime_fields` to populate
# ``Session.context_max`` for sessions imported from JSONL.
_PAYLOAD_TASK_STARTED = "task_started"
# The matching turn-end event. Together they bracket one turn of a
# thread — the running / idle signal :meth:`subagent_turn_boundary`
# maps onto a subagent's ``Session.last_stopped_at``.
_PAYLOAD_TASK_COMPLETE = "task_complete"
# Compaction headroom Codex CLI reserves on top of the model's nominal
# input window, expressed as the ratio of "published" to "nominal".
# Used to recover the nominal window from
# ``task_started.model_context_window``. If Codex changes the
# headroom in a future release this constant will need adjusting (or
# the math replaced by an explicit per-model lookup).
_TASK_STARTED_WINDOW_HEADROOM_FACTOR = 0.95

# response_item payload sub-types that represent a tool call. Each is its
# own JSONL line (mono-block), unlike Claude where tool_uses live inside a
# message.content array. ``function_call`` is the standard OpenAI form;
# ``custom_tool_call`` is the freeform variant used for tools whose input
# isn't JSON (apply_patch ships its patch as raw Lark-grammar text);
# ``local_shell_call`` is the native shell tool exposed directly by the
# Responses API — it doesn't carry a ``name`` field (the sub_type IS the
# tool name) and ships its argv via ``payload.action`` instead of a JSON
# ``arguments`` string; ``web_search_call`` is the native web-search tool
# (also nameless, also payload.action-based) — see
# :data:`_NATIVE_TOOL_NAME_BY_SUB_TYPE` and :data:`_RESULTLESS_TOOL_SUB_TYPES`.
_TOOL_CALL_PAYLOAD_TYPES = frozenset({
    "function_call",
    "custom_tool_call",
    "local_shell_call",
    "web_search_call",
})

# Sub-types whose canonical tool name is the sub_type itself — used as a
# fallback in :meth:`extract_tool_use_entries` / :meth:`analyze_content`
# when the payload doesn't carry a ``name`` field. The frontend reads
# this name verbatim (no rewriting) so the value here is what the tool
# card / helpers (label, summary, INPUT_OVERRIDES, …) key off.
_NATIVE_TOOL_NAME_BY_SUB_TYPE = {
    "local_shell_call": "local_shell_call",
    "web_search_call": "web_search_call",
}

# Sub-types of :data:`_TOOL_CALL_PAYLOAD_TYPES` that never produce a
# paired ``function_call_output`` (or equivalent) — the tool_use card
# stands alone with no result to wait for, so the frontend's spinner
# stays off from the start.
#
# Today: ``web_search_call``. Codex emits a ``response_item.web_search_call``
# alongside an ``event_msg.web_search_end``, but the call doesn't carry
# a ``call_id`` or any serialised id (``id`` is ``skip_serializing``
# on ``WebSearchCall``), so the event can't be paired with the call from
# the JSONL — and the call has nothing else to wait for. ``analyze_content``
# therefore short-circuits to a visible-but-unpaired ContentAnalysis for
# these sub-types (no ``call_id`` requirement, empty ``tool_use_entries``).
# Frontend mirrors via the ``RESULTLESS_TOOLS`` set in toolHelpers.js.
_RESULTLESS_TOOL_SUB_TYPES = frozenset({"web_search_call"})

# Shell-family tools that share the shell-card rendering path on the
# frontend. Membership here drives :meth:`compute_link_extra`'s
# ``extra.is_terminated`` logic — see :data:`_EXEC_COMMAND_TOOLS` for
# the chained subset.
#
# The rule of thumb: any new shell-like tool we want to surface should
# go in this set, and is treated as **atomic** (single
# ``function_call_output`` per call, terminated on arrival) by default.
# Only ``exec_command`` and ``write_stdin`` (the unified-exec pair
# already in :data:`_EXEC_COMMAND_TOOLS`) can chain multiple result
# rows for the same call_id — Codex CLI only spawns ``write_stdin``
# polls against an ``exec_command`` parent, never against ``shell`` /
# ``shell_command`` / ``local_shell_call`` whose output is always a
# complete one-shot payload (cf. their handlers — they don't expose a
# unified-exec process id to poll).
#
_SHELL_FAMILY_TOOLS = frozenset({
    "exec_command",
    "write_stdin",
    "shell",
    "shell_command",
    "local_shell_call",
    # ``container.exec`` is a legacy alias of ``shell`` — same wire shape
    # (function_call, ``ShellToolCallParams`` arguments) and same output
    # path (``run_exec_like(freeform=false)`` ->
    # ``format_exec_output_for_model_structured``). Hosted by
    # ``ContainerExecHandler`` (``codex-rs/core/src/tools/handlers/shell/container_exec.rs``).
    "container.exec",
})

# ``function_call`` shell tools inspected for plan-doc detection, mapped to
# the ``arguments`` key carrying their command (``exec_command`` uses ``cmd``,
# not ``command``). ``write_stdin`` is deliberately absent (stdin to a running
# process, not a command); ``local_shell_call`` is handled separately (argv in
# ``payload.action.command``, no ``arguments``).
_DOC_EDIT_SHELL_COMMAND_KEYS = {
    "exec_command": "cmd",
    "shell": "command",
    "container.exec": "command",
    "shell_command": "command",
}

# Code-mode tool names (GPT-5.6+ "tool_mode: code_mode_only" models).
# ``exec`` is a ``custom_tool_call`` whose ``input`` is raw JavaScript
# executed in a V8 isolate by the CLI; every real action (shell command,
# patch, MCP call) is a *nested* call made from that JS and never
# persisted to the rollout — the script source is statically mined by
# :func:`parse_code_mode_script` instead. ``wait`` is the ``function_call``
# that resumes a still-running script "cell" (the code-mode analog of
# ``write_stdin`` polling an ``exec_command`` process): its output chunks
# are rebound to the owning ``exec`` call via the ``cell_id`` announced
# by the parent's ``Script running with cell ID <id>`` status header.
# Detection is shape-based only (payload sub-type + these names) — never
# model-version-based — so pre-5.6 sessions are untouched. The bare name
# ``exec`` is unambiguous among custom_tool_calls (MCP tools are
# ``mcp__``-prefixed and the only historical custom_tool_call is
# ``apply_patch``); no historical function_call is named ``wait``
# (``wait_agent`` is distinct). Design:
# ``docs/plans/2026-07-10-codex-code-mode-display-design.md``.
_CODE_MODE_EXEC_TOOL = "exec"
_CODE_MODE_WAIT_TOOL = "wait"

# Function-call ``name`` values whose tool_use is bucketed as SYSTEM (no
# tool card rendered) because the relevant exchange is captured elsewhere.
# ``write_stdin`` (direct or a single resolved code-mode wrapper) belongs to
# a previously-spawned ``exec_command`` session;
# its ``function_call_output`` is rebound to the parent exec_command's
# ``call_id`` via :meth:`CodexSessionCompute.remap_tool_result_id` so the
# polled chunks all land on the same ``ToolResultLink`` chain. ``wait``
# is the code-mode equivalent (rebound to the owning ``exec`` call via
# the cell map — see :data:`_CODE_MODE_WAIT_TOOL`).
#
# NOTE: this list governs UI rendering only (``compute_item_kind`` returns
# ``SYSTEM`` for these). For ``write_stdin`` and ``wait`` the pairing path
# (``extract_tool_use_entries``, ``analyze_content``) STILL records the
# call_id in ``tool_use_map`` so the remap hook can resolve their
# function_call_output to the parent call. For
# :data:`_IGNORED_FUNCTION_NAMES` (``wait_agent``) the pairing is
# dropped entirely — see that constant's docstring.
_NON_TOOL_FUNCTION_NAMES = frozenset({"write_stdin", "wait_agent", _CODE_MODE_WAIT_TOOL})

# Function-call ``name`` values that ARE real tool calls but carry nothing
# worth a visible card, so their ``function_call`` is bucketed as SYSTEM
# (-> DEBUG_ONLY). Unlike :data:`_NON_TOOL_FUNCTION_NAMES` there's no remap
# or pairing subtlety: the matching ``function_call_output`` already lands
# at DEBUG_ONLY via :meth:`is_tool_result_item`, so both ends stay hidden
# from the normal flow and reappear together only in debug mode.
#
# ``get_goal`` (Codex-only) is a read-only probe of the thread Goal — the
# whole goal state is redundant with the surrounding ``create_goal`` /
# ``update_goal`` cards, so the lone read adds noise without information.
_DEBUG_ONLY_FUNCTION_NAMES = frozenset({"get_goal"})

# Function-call ``name`` values whose result is fully redundant with
# another signal we already capture, so we drop them entirely from the
# pairing path: no ``tool_use_map`` entry, no ``ToolResultLink`` row, no
# ``tool_state`` broadcast. The matching ``function_call`` row is still
# bucketed as SYSTEM via :data:`_NON_TOOL_FUNCTION_NAMES`, and the
# matching ``function_call_output`` falls through to SYSTEM as well
# because there is no parent it can pair with.
#
# Today this only contains ``wait_agent``: it polls subagents already
# tracked by ``spawn_agent``, and its output is redundant with the
# completion signal we do capture — a ``<subagent_notification>`` user
# message emitted at the exact same instant on multi-agent v1 (see
# ``codex-rs/core/src/agent/control.rs``), a ``FINAL_ANSWER`` agent
# message on v2. Keeping wait_agent as its own tool would just clone
# the ack/done signal twice, and its own output identifies no agent
# (``{"message": "Wait completed.", "timed_out": false}``) so it could
# not stand in for that signal anyway. Future agent-control tools
# (``close_agent``, ``send_input``, ``resume_agent``) will likely join
# this set as we wire them up.
_IGNORED_FUNCTION_NAMES = frozenset({"wait_agent"})

# ``function_call.name`` for the SDK tool that spawns a subagent thread.
# Two protocol generations coexist on disk, discriminated by the shape of
# the ``function_call_output`` ack (never by a version field — old
# rollouts stay readable forever, and ``turn_context.multi_agent_version``
# is absent from v1 lines):
#
# - **v1** (no ``multi_agent_version``): bare ``spawn_agent`` name, ack
#   ``{"agent_id": "...", "nickname": "..."}`` carrying the subagent's
#   thread id, terminated by a ``<subagent_notification>`` user message.
# - **v2** (``multi_agent_version == "v2"``): name qualified with the
#   ``collaboration`` namespace, ack ``{"task_name": "/root/<task>"}``
#   carrying an *agent path* instead of a thread id. See
#   :data:`_SUB_AGENT_ACTIVITY_PAYLOAD_TYPE` for where the thread id and
#   the termination signal moved.
#
# On failure both generations return a freeform rejection string (e.g.
# fork-context constraint violations like ``"Full-history forked agents
# inherit the parent agent type, model, and reasoning effort; ..."``)
# through the same ``output`` field — the success / failure split is done
# by attempting an :func:`orjson.loads` and looking for ``agent_id``
# (v1) or ``task_name`` (v2).
#
# Used by :meth:`CodexSessionCompute.extract_task_tool_uses`,
# :meth:`extract_task_tool_use_prompts`,
# :meth:`extract_agent_info_from_tool_result`,
# :meth:`compute_link_error_override`, and :meth:`analyze_content` (batch
# path) to keep the success / failure detection in a single place.
_SPAWN_AGENT_FUNCTION_NAME = "spawn_agent"

# ``function_call.namespace`` of the multi-agent v2 collaboration tools
# (``spawn_agent``, ``send_message``, ``wait_agent``, ``list_agents``,
# ``followup_task``). v1 had no namespace at all, so the name resolved by
# :func:`_qualified_function_call_name` — the one the pairing hooks and
# the frontend tool card see — is ``spawn_agent`` on v1 rollouts and
# ``collaboration__spawn_agent`` on v2 ones. Raw ``payload.name`` checks
# are unaffected: Codex kept the bare name there in both generations.
_COLLABORATION_NAMESPACE = "collaboration"

# Every qualified name a ``spawn_agent`` call can carry (v1 + v2). Used by
# the hooks that receive the name already resolved through
# :func:`_qualified_function_call_name` (:meth:`compute_link_error_override`).
_SPAWN_AGENT_TOOL_NAMES = frozenset({
    _SPAWN_AGENT_FUNCTION_NAME,
    f"{_COLLABORATION_NAMESPACE}__{_SPAWN_AGENT_FUNCTION_NAME}",
})

# ``event_msg`` sub-type carrying multi-agent v2 subagent activity, and
# the only ``kind`` value that marks a spawn. The v2 ack no longer
# carries the subagent's thread id, so this event takes over the role v1
# gave to the ack: its ``event_id`` IS the ``call_id`` of the tool that
# touched the subagent (the ``spawn_agent`` for ``kind == "started"``),
# and its ``agent_thread_id`` is the subagent session id — the exact
# ``(tool_use_id, agent_id)`` pair :class:`~twicc.core.models.AgentLink`
# needs. The other kinds (``interacted`` on a ``send_message``,
# ``interrupted``) carry a different call_id and must never create a
# link. ``agent_path`` (e.g. ``/root/task1_review``) is the stable
# handle the collaboration tools use, and the key the v2 termination
# signal is addressed by.
_SUB_AGENT_ACTIVITY_ITEM_TYPE = "SubAgentActivity"
_SUB_AGENT_ACTIVITY_STARTED_KIND = "started"

# Envelope of an inter-agent message, as persisted in the *receiving*
# thread (``response_item.agent_message``, multi-agent v2). The first
# content block is plain text::
#
#     Message Type: <TYPE>
#     Task name: <receiver agent path>
#     Sender: <sender agent path>
#     Payload:
#     <payload>
#
# ``<payload>`` is a Fernet ciphertext (``gAAAAA…``, in a sibling
# ``encrypted_content`` block) for ``NEW_TASK`` and ``MESSAGE``, but
# **plaintext, inline** for ``FINAL_ANSWER`` — the subagent's answer
# handed back to its parent. That makes ``FINAL_ANSWER`` the v2
# equivalent of v1's ``<subagent_notification>``: the canonical
# "spawn_agent terminated" signal, addressed by the sender's agent path.
# We mine that one only; the encrypted kinds carry nothing we could use.
_AGENT_MESSAGE_TYPE_PREFIX = "Message Type: "
_AGENT_MESSAGE_FINAL_ANSWER_TYPE = "FINAL_ANSWER"
# The task a parent hands to a subagent — the child's opening prompt,
# promoted to a ``USER_MESSAGE`` by :meth:`compute_item_kind` (the parent
# is the "user" of that thread, exactly like Claude Code's sidechains).
_AGENT_MESSAGE_NEW_TASK_TYPE = "NEW_TASK"
_AGENT_MESSAGE_SENDER_PREFIX = "Sender: "
_AGENT_MESSAGE_TASK_NAME_PREFIX = "Task name: "
_AGENT_MESSAGE_PAYLOAD_MARKER = "Payload:"
# Content-block type of an encrypted payload. Its presence IS the
# "unreadable body" signal — cheaper and more durable than recognising a
# Fernet token, and it degrades gracefully if Codex ever ships some of
# these envelopes in the clear.
_ENCRYPTED_CONTENT_BLOCK_TYPE = "encrypted_content"

# Marker tags that delimit the JSON body of a Codex
# ``<subagent_notification>`` user message (multi-agent **v1** only —
# v2 replaced it with the ``FINAL_ANSWER`` agent message, see
# :data:`_AGENT_MESSAGE_FINAL_ANSWER_TYPE`). Codex injects this message
# in the parent thread (via ``inject_user_message_without_turn``,
# cf. ``codex-rs/core/src/agent/control.rs``) every time a spawned
# subagent reaches a final ``AgentStatus`` (Completed / Errored /
# Shutdown / NotFound) — independently of whether the parent ever
# called ``wait_agent``. The body is a JSON object
# ``{"agent_path": "<id>", "status": <AgentStatus>}`` (see
# ``codex-rs/core/src/context/subagent_notification.rs``). We treat
# this user message as the canonical "spawn_agent terminated" signal
# and rebind it as a synthetic second ``ToolResultLink`` of the
# matching ``spawn_agent`` ``function_call`` — the first link being
# the spawn ack ``function_call_output`` carrying ``{agent_id, ...}``.
# The ``wait_agent`` tool is intentionally NOT rebound; its
# ``function_call_output`` is redundant with this notification (both
# emitted at the same instant when the subagent finalises) and
# carrying both would only duplicate work.
_SUBAGENT_NOTIFICATION_START = "<subagent_notification>"
_SUBAGENT_NOTIFICATION_END = "</subagent_notification>"

# Tool-result payload sub-types from ``response_item`` lines (the
# LLM-facing string returned to the model). Paired with the calls above
# by ``call_id`` and routed to DEBUG_ONLY via :meth:`is_tool_result_item`.
_TOOL_RESULT_PAYLOAD_TYPES = frozenset({"function_call_output", "custom_tool_call_output"})

# function_call ``name`` values that produce / consume a unified-exec
# process. ``exec_command`` spawns the process; ``write_stdin`` polls
# (and optionally writes to) it. Their ``function_call_output`` lines
# carry the structured ``Chunk ID / Wall time / Process … / Output:``
# trailer parsed by :func:`parse_exec_command_status`.
#
# Also the **chained** subset of :data:`_SHELL_FAMILY_TOOLS`: these are
# the only shell-family tools whose output can chain across multiple
# ``function_call_output`` rows for the same call_id (the parent
# ``exec_command``'s own row plus one row per ``write_stdin`` poll,
# all rebinded by :meth:`remap_tool_result_id`). Anything else in the
# family is atomic by definition.
_EXEC_COMMAND_TOOLS = frozenset({"exec_command", "write_stdin"})

class ExecCommandStatus(NamedTuple):
    """Parsed status of a Codex ``function_call_output`` for an exec tool.

    Codex formats its exec_command / write_stdin tool outputs as a flat
    string with a structured trailer; we parse it once with
    :func:`parse_exec_command_status` and surface the bits we need.

    Fields:

    - ``exec_command_id``: the unified-exec process id (called ``session_id``
      by Codex itself, but we name it ``exec_command_id`` here to avoid
      colliding with TwiCC's own ``Session`` notion). Only set when the
      output reports a process *running* — the *exited* shape doesn't
      include the id, so callers resolve it via the
      ``_exec_command_maps`` cache instead.
    - ``is_terminated``: ``True`` iff a ``Process exited with code N``
      line was matched.
    - ``exit_code``: the integer code; meaningful only when
      ``is_terminated`` is ``True``.
    """
    exec_command_id: int | None
    is_terminated: bool
    exit_code: int | None


# Single-pass regex with alternation, anchored at line start (multiline
# mode). Either a "running" line or an "exited" line matches per output
# (they are mutually exclusive in Codex's formatter, see
# ``codex-rs/core/src/tools/context.rs``).
_EXEC_COMMAND_STATUS_RE = re.compile(
    r"^Process (?:running with session ID (?P<run>-?\d+)"
    r"|exited with code (?P<exit>-?\d+))$",
    re.MULTILINE,
)

# ``shell_command`` (and any other tool using
# ``format_exec_output_for_model_freeform`` in
# ``codex-rs/core/src/tools/mod.rs``) emits a freeform text trailer that
# starts with this line — anchored at line start so we never match a
# stray occurrence inside the body. The pattern is intentionally distinct
# from the ``exec_command`` trailer (``Process exited with code N``) so
# :func:`_freeform_exec_output_error` and :func:`_exit_code_error_from_output`
# can both be tried defensively without ever cross-matching.
_FREEFORM_EXIT_CODE_RE = re.compile(r"^Exit code: (-?\d+)$", re.MULTILINE)


def parse_exec_command_status(output: str) -> ExecCommandStatus:
    """Extract the status trailer from an exec_command/write_stdin output.

    Returns a default :class:`ExecCommandStatus` (``None`` / ``False`` /
    ``None``) when neither pattern is present (defensive — Codex always
    emits one when the output is well-formed).
    """
    if not isinstance(output, str) or not output:
        return ExecCommandStatus(None, False, None)
    match = _EXEC_COMMAND_STATUS_RE.search(output)
    if match is None:
        return ExecCommandStatus(None, False, None)
    if match.group("run") is not None:
        return ExecCommandStatus(int(match.group("run")), False, None)
    return ExecCommandStatus(None, True, int(match.group("exit")))


def _exit_code_error_from_output(output: str) -> str | None:
    """Render ``"Exit code N"`` for a non-zero exit, else ``None``.

    Replaces the legacy ``_exit_code_error`` helper that read
    ``payload.exit_code`` off the disappeared ``exec_command_end`` event.
    The exit code now lives in the formatted trailer of the matching
    ``function_call_output`` (parsed via :func:`parse_exec_command_status`).
    Returns ``None`` while the process is still running (``is_terminated``
    is ``False``) and on a clean exit (code ``0``).
    """
    status = parse_exec_command_status(output)
    if not status.is_terminated or status.exit_code is None or status.exit_code == 0:
        return None
    return f"Exit code {status.exit_code}"


def _freeform_exec_output_error(output: str) -> str | None:
    """Render ``"Exit code N"`` from a freeform-text shell tool output.

    Applies to every Codex tool whose ``function_call_output.output`` is
    produced by ``format_exec_output_for_model_freeform``
    (``codex-rs/core/src/tools/mod.rs``) — today: ``shell_command``. The
    wire shape is plain text starting with ::

        Exit code: N
        Wall time: X.X seconds
        [Total output lines: N]
        Output:
        <body>

    We match the first line with :data:`_FREEFORM_EXIT_CODE_RE` (anchored
    at line start, so a stray occurrence inside the body can't fool us)
    and surface ``"Exit code N"`` for a non-zero exit.

    Defensive: returns ``None`` when no match, when the captured code
    doesn't parse, or when it's zero — so the caller can chain it with
    :func:`_structured_exec_output_error` and
    :func:`_exit_code_error_from_output` and let the matching output
    shape win.
    """
    if not isinstance(output, str) or not output:
        return None
    match = _FREEFORM_EXIT_CODE_RE.search(output)
    if match is None:
        return None
    try:
        code = int(match.group(1))
    except ValueError:
        return None
    if code == 0:
        return None
    return f"Exit code {code}"


def _structured_exec_output_error(output: str) -> str | None:
    """Render ``"Exit code N"`` from a structured-JSON shell tool output.

    Applies to every Codex tool whose ``function_call_output.output`` is
    produced by ``format_exec_output_for_model_structured``
    (``codex-rs/core/src/tools/mod.rs``) — today: ``local_shell_call``
    and ``shell``. The wire shape is a JSON string
    ``{"output":"<body>","metadata":{"exit_code":N,"duration_seconds":N.N}}``,
    collapsed by ``function_tool_response`` to ``FunctionCallOutputBody::Text``
    when the inner ``InputText`` is a single item. We orjson-decode and
    pull ``metadata.exit_code`` to surface a non-zero exit the same way
    :func:`_exit_code_error_from_output` does for ``exec_command``.

    Defensive: returns ``None`` on parse failure, shape mismatch, or
    ``exit_code == 0`` so the caller can fall back to other detection
    paths (notably the legacy exec_command trailer) without crashing.
    The function is intentionally self-detecting — it doesn't take the
    parent tool name, so :meth:`extract_tool_result_info` can chain it
    with :func:`_exit_code_error_from_output` and let the matching
    output shape win.
    """
    if not isinstance(output, str) or not output:
        return None
    try:
        parsed = orjson.loads(output)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    metadata = parsed.get("metadata")
    if not isinstance(metadata, dict):
        return None
    exit_code = metadata.get("exit_code")
    if not isinstance(exit_code, int) or exit_code == 0:
        return None
    return f"Exit code {exit_code}"


def _code_mode_output_error(output: object) -> str | None:
    """Surface a script-level failure from a code-mode ``exec``/``wait`` output.

    A ``Script failed`` status header flips the link to error state, with
    the ``Script error:`` segment body as the message when present. Every
    other status (and any non-code-mode output — :func:`parse_code_mode_output`
    returns ``None`` for those) yields ``None``. Note: a *nested* command
    exiting non-zero does NOT fail the script; the script-level status is
    all the rollout lets us claim.
    """
    parsed = parse_code_mode_output(output)
    if parsed is None or parsed.status != "failed":
        return None
    return parsed.error_text or "Script failed"


# Declared targets of a v4a patch envelope (``*** Add File: <path>`` /
# ``*** Update File: <path>`` / ``*** Delete File: <path>``). Used to match
# an orphan ``FileChange`` back to the code-mode ``exec`` whose script
# declared a patch on the same files — see
# :meth:`CodexSessionCompute._remap_orphan_end_event`.
_PATCH_ENVELOPE_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


class CodeModeScriptTargets(NamedTuple):
    """What a code-mode script declares that later orphan end events can match.

    ``has_patch`` / ``patch_paths`` feed the ``FileChange`` pairing
    (paths possibly empty when the envelope isn't statically resolvable —
    the matcher then falls back to recency); ``mcp_tools`` holds the
    fully-qualified ``mcp__<server>__<tool>`` names of every nested MCP
    call and feeds the ``McpToolCall`` pairing the same way.
    """

    has_patch: bool
    patch_paths: frozenset[str]
    mcp_tools: frozenset[str]


def _script_targets(script_input: object) -> CodeModeScriptTargets:
    """Inspect a code-mode script for nested calls orphan end events pair with.

    Covers the two nested tool families whose handler emits a persisted
    ``event_msg.*_end`` under a synthesized ``exec-<uuid>`` call_id:
    ``apply_patch`` (→ ``FileChange``) and ``mcp__*`` (→
    ``McpToolCall``, whose ``invocation`` field carries the exact
    server/tool pair to match against ``mcp_tools``).
    """
    script = parse_code_mode_script(script_input)
    has_patch = False
    paths: set[str] = set()
    mcp_tools: set[str] = set()
    for call in script.calls:
        if call.name.startswith("mcp__"):
            mcp_tools.add(call.name)
            continue
        if call.name != "apply_patch":
            continue
        has_patch = True
        if call.resolved and isinstance(call.arg, str):
            for match in _PATCH_ENVELOPE_PATH_RE.finditer(call.arg):
                path = match.group(1).strip()
                if path:
                    paths.add(path)
    return CodeModeScriptTargets(has_patch, frozenset(paths), frozenset(mcp_tools))


def _normalize_code_mode_identifier(name: str) -> str:
    """Mirror of codex-rs ``normalize_code_mode_identifier`` (description.rs).

    Code mode exposes nested tools as JavaScript identifiers: every
    character outside ``[A-Za-z0-9_$]`` (digits excluded at index 0) is
    rewritten to ``_``. Applied to the event-side qualified name so it
    compares against the identifiers the script actually uses — e.g.
    server ``chrome-devtools`` is addressed as
    ``tools.mcp__chrome_devtools__<tool>``.
    """
    chars = []
    for index, ch in enumerate(name):
        if ch == "_" or ch == "$" or (ch.isascii() and (ch.isalpha() or (index > 0 and ch.isdigit()))):
            chars.append(ch)
        else:
            chars.append("_")
    return "".join(chars) if chars else "_"


def _mcp_end_qualified_name(payload: dict) -> str | None:
    """Qualified ``mcp__<server>__<tool>`` name of an ``McpToolCall``.

    Built from the event's ``invocation`` field and normalised like the
    JS identifier the code-mode script uses to address the tool
    (``tools.mcp__server__tool``), so it compares directly against
    :attr:`CodeModeScriptTargets.mcp_tools`. Returns ``None`` when the
    invocation is missing or malformed (the matcher then falls back to
    recency).
    """
    if payload.get("type") != "McpToolCall":
        return None
    server = payload.get("server")
    tool = payload.get("tool")
    if not isinstance(server, str) or not server or not isinstance(tool, str) or not tool:
        return None
    return _normalize_code_mode_identifier(f"mcp__{server}__{tool}")


def _changes_match_declared(change_paths: list[str], declared: frozenset[str]) -> bool:
    """True when a ``FileChange.changes`` path matches a declared one.

    ``changes`` keys are absolute; envelope declarations may be relative
    (the patch grammar allows both), so a suffix match on a path-segment
    boundary is used instead of resolving against a cwd.
    """
    for declared_path in declared:
        for change_path in change_paths:
            if change_path == declared_path or change_path.endswith("/" + declared_path):
                return True
    return False


def _wait_cell_id_from_payload(payload: dict | None) -> str | None:
    """Read ``arguments.cell_id`` from a code-mode ``wait`` function_call payload.

    The cell id is the token the parent ``exec`` output announced in its
    ``Script running with cell ID <id>`` header. Codex serialises it as a
    JSON string in the wait's arguments; an integer is tolerated and
    normalised to its string form so it matches the header-side capture.
    """
    if payload is None:
        return None
    raw_args = payload.get("arguments")
    if not isinstance(raw_args, str):
        return None
    try:
        args = orjson.loads(raw_args)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(args, dict):
        return None
    cell_id = args.get("cell_id")
    if isinstance(cell_id, str) and cell_id:
        return cell_id
    if isinstance(cell_id, int):
        return str(cell_id)
    return None


def _resolved_code_mode_nested_args(payload: dict | None, nested_name: str) -> dict | None:
    """Return one resolved nested tool's object arguments from an ``exec``.

    Only the unambiguous tier-1 shape qualifies: a code-mode
    ``custom_tool_call name=exec`` whose script contains exactly one resolved
    ``tools.<nested_name>({...})`` call. Multi-tool or dynamically-built
    scripts keep their ordinary ``exec`` semantics.
    """
    if (
        payload is None
        or payload.get("type") != "custom_tool_call"
        or payload.get("name") != _CODE_MODE_EXEC_TOOL
    ):
        return None
    calls = parse_code_mode_script(payload.get("input")).calls
    if len(calls) != 1:
        return None
    call = calls[0]
    if call.name != nested_name or not call.resolved or not isinstance(call.arg, dict):
        return None
    return call.arg


def _write_stdin_exec_command_id_from_payload(payload: dict | None) -> int | None:
    """Read the unified-exec id from direct or code-mode ``write_stdin``.

    Pre-5.6 direct calls store a JSON string in ``payload.arguments``. GPT-5.6
    code mode stores the same object inside the outer ``exec`` JavaScript.
    """
    if payload is None:
        return None
    if payload.get("type") == "function_call" and payload.get("name") == "write_stdin":
        raw_args = payload.get("arguments")
        if not isinstance(raw_args, str):
            return None
        try:
            args = orjson.loads(raw_args)
        except orjson.JSONDecodeError:
            return None
        if not isinstance(args, dict):
            return None
    else:
        args = _resolved_code_mode_nested_args(payload, "write_stdin")
        if args is None:
            return None
    session_id = args.get("session_id")
    return session_id if isinstance(session_id, int) else None


def _extract_write_stdin_exec_command_id(parsed_json: dict) -> int | None:
    """Read the unified-exec id from a direct or code-mode ``write_stdin``.

    The ``session_id`` field is named ``exec_command_id`` everywhere on
    TwiCC's side. Returns ``None`` for malformed or ambiguous wrappers.
    """
    return _write_stdin_exec_command_id_from_payload(_payload(parsed_json))


# Codex's ``update_plan`` is the moral equivalent of Claude Code's ``TodoWrite``
# (a full-list replacement). Source spec: ``codex-rs/core/src/tools/handlers/
# plan_spec.rs``.
_UPDATE_PLAN_FUNCTION_NAME = "update_plan"


def _update_plan_args_from_payload(payload: dict) -> dict | None:
    """Return resolved ``update_plan`` arguments from direct or code mode calls.

    Pre-5.6 Codex persists ``update_plan`` as a native ``function_call`` with
    JSON-encoded ``arguments``. GPT-5.6 code mode instead persists only the
    outer ``custom_tool_call name=exec`` JavaScript, which may invoke
    ``tools.update_plan({...})`` alongside unrelated nested tools. A single
    statically resolved update is unambiguous enough to recover; dynamic or
    repeated updates are ignored because the scanner cannot prove which
    control-flow branch ran.
    """
    sub_type = payload.get("type")
    if sub_type == "function_call":
        if payload.get("name") != _UPDATE_PLAN_FUNCTION_NAME:
            return None
        raw_args = payload.get("arguments")
        if not isinstance(raw_args, str):
            return None
        try:
            args = orjson.loads(raw_args)
        except orjson.JSONDecodeError:
            return None
        return args if isinstance(args, dict) else None

    if sub_type != "custom_tool_call" or payload.get("name") != _CODE_MODE_EXEC_TOOL:
        return None
    plan_calls = [
        call
        for call in parse_code_mode_script(payload.get("input")).calls
        if call.name == _UPDATE_PLAN_FUNCTION_NAME
    ]
    if len(plan_calls) != 1:
        return None
    call = plan_calls[0]
    if not call.resolved or not isinstance(call.arg, dict):
        return None
    return call.arg


def _plan_to_todos(plan) -> list[dict] | None:
    """Map a Codex ``update_plan.plan`` array to the cross-provider task shape.

    All-or-nothing (mirrors the frontend ``isValidPlan`` + ``planToTodos``):
    every entry must carry a string ``step`` and a string ``status``; a single
    bad entry invalidates the whole list (``None``). ``step`` maps to
    ``content`` (Codex has no ``activeForm``).
    """
    if not isinstance(plan, list) or not plan:
        return None
    normalized: list[dict] = []
    for entry in plan:
        if not isinstance(entry, dict):
            return None
        step = entry.get("step")
        status = entry.get("status")
        if not isinstance(step, str) or not isinstance(status, str):
            return None
        normalized.append({"content": step, "status": status})
    return normalized


def _event_msg_call_id(parsed_json: dict) -> str | None:
    """Return ``payload.call_id`` for a persisted Codex ``event_msg`` line.

    Codex persists canonical completed items that carry the structured
    outcome of a tool call (``changes`` map for ``FileChange``,
    ``CallToolResult`` for ``McpToolCall``, …). Each one is paired with
    the originating ``function_call`` / ``custom_tool_call`` by its item
    ``id``, which is the call_id.

    Only ``FileChange`` and ``McpToolCall`` qualify (see
    :func:`canonical.canonical_result_item`); ``CommandExecution`` is
    excluded because shell transcripts are rebuilt from the
    ``function_call_output`` chain. ``response_item`` lines are filtered
    out at the wrapper level. Returns the call_id for a matching item,
    else ``None``.
    """
    return canonical_call_id(parsed_json)


def _payload(parsed_json: dict) -> dict | None:
    """Return ``parsed_json["payload"]`` if it's a dict, else ``None``."""
    payload = parsed_json.get("payload")
    return payload if isinstance(payload, dict) else None


def _restore_private_source(parsed_json: dict) -> dict:
    """Restore the raw payload before replacing an older private rewrite."""
    original = parsed_json.get("twiccOriginalContent")
    if not isinstance(original, dict):
        return parsed_json
    restored = dict(parsed_json)
    restored["type"] = _TYPE_RESPONSE_ITEM
    restored["payload"] = original
    restored.pop("twiccOriginalContent", None)
    return restored


def _qualified_function_call_name(payload: dict) -> str:
    """Return the fully-qualified tool name for a ``function_call`` payload.

    For most tools the canonical name is just ``payload.name``. MCP tools
    additionally carry a ``payload.namespace`` (e.g.
    ``"mcp__codex_apps__github"``) — without it, the bare ``name`` (often
    starting with an underscore like ``"_search_repositories"``) is
    ambiguous and indistinguishable from any other function_call. We
    prepend the namespace with ``__`` so the resulting name keeps the
    same ``mcp__server__app__tool`` shape Claude Code's MCP tools use,
    and so ``startsWith("mcp__")`` becomes a reliable detection point in
    both backend and frontend. The frontend formatter strips leading /
    trailing ``_`` from each segment when splitting on ``__`` for
    display, so the bare-name leading underscore stays out of the
    header label.

    Returns the empty string when ``payload.name`` is missing or
    not a string — same fallback as the previous logic, so a malformed
    payload doesn't blow up the pipeline.
    """
    name = payload.get("name")
    if not isinstance(name, str):
        name = ""
    namespace = payload.get("namespace")
    if isinstance(namespace, str) and namespace:
        return f"{namespace}__{name}"
    return name


def _tool_use_name(payload: dict) -> str:
    """Return the effective tool name used by pairing and rendering.

    A tier-1 code-mode wrapper around ``write_stdin`` deliberately adopts the
    nested name. That puts it on the same invisible/remapped path as the
    direct pre-5.6 call while every other code-mode script remains ``exec``.
    """
    sub_type = payload.get("type")
    native_name = _NATIVE_TOOL_NAME_BY_SUB_TYPE.get(sub_type)
    if native_name is not None:
        return native_name
    if _write_stdin_exec_command_id_from_payload(payload) is not None:
        return "write_stdin"
    return _qualified_function_call_name(payload)


_CODE_MODE_EXEC_COMMAND_ID_RE = re.compile(r"(?:^|\n)SESSION_ID=(\d+)(?=\n|$)")


def _code_mode_exec_command_id_from_output(output: object) -> int | None:
    """Extract the nested unified-exec id printed by the canonical wrapper.

    The GPT-5.6 wrapper prints ``SESSION_ID=<id>`` when nested
    ``exec_command`` returned a background process. The line lives in the
    code-mode output body, after the ``Script ...`` header.
    """
    parsed = parse_code_mode_output(output)
    if parsed is None:
        return None
    matches = list(_CODE_MODE_EXEC_COMMAND_ID_RE.finditer(parsed.body))
    if not matches:
        return None
    try:
        return int(matches[-1].group(1))
    except ValueError:
        return None


def _subagent_notification_text(parsed_json: dict) -> str | None:
    """Return the inner ``input_text`` string of a Codex ``<subagent_notification>``.

    The matching JSONL shape is::

        {
          "type": "response_item",
          "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "<subagent_notification>...\\n"}]
          }
        }

    Returns the full text (including the surrounding markers) when the
    line matches; ``None`` on any other shape — including ordinary
    user messages, multi-block messages, or messages whose first block
    is not an ``input_text`` carrying the start marker.
    """
    if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "input_text":
        return None
    text = first.get("text")
    if not isinstance(text, str):
        return None
    if _SUBAGENT_NOTIFICATION_START not in text:
        return None
    return text


# Opening tag of the synthetic context message Codex injects to drive a goal
# continuation. It is a ``response_item.message`` (role=user) whose first
# ``input_text`` block wraps the objective in an ``<objective>`` tag. Matching
# this exact opening tag identifies the line (other ``codex_internal_context``
# sources, if any, are left untouched).
_GOAL_CONTEXT_MARKER = '<codex_internal_context source="goal">'
# The continuation prompt wraps the objective in ``<objective>``; the mid-run
# objective-edit steer (a ``/goal`` set on an already-active goal) uses
# ``<untrusted_objective>``. Match either (back-reference keeps open/close tags
# paired) so both render as ``/goal <objective>``.
_GOAL_OBJECTIVE_RE = re.compile(
    r"<(untrusted_objective|objective)>\s*(.*?)\s*</\1>", re.DOTALL,
)


def _goal_context_payload(parsed_json: dict) -> dict | None:
    """Return the native goal-context message payload, raw or rewritten.

    Older compute passes rewrote every continuation prompt into an
    canonical ``UserMessage`` item and preserved the native ``response_item``
    payload under ``twiccOriginalContent``. Recognising both shapes lets a
    compute-version bump demote those already-persisted duplicates again.
    """
    if parsed_json.get("type") == _TYPE_RESPONSE_ITEM:
        payload = _payload(parsed_json)
        if payload is not None and payload.get("type") == "message" and payload.get("role") == "user":
            return payload
        return None
    if parsed_json.get("type") != _TYPE_EVENT_MSG:
        return None
    payload = _payload(parsed_json)
    item = completed_item(parsed_json)
    is_private_user_rewrite = (
        item is not None and item.get("type") == "UserMessage"
    ) or (
        payload is not None and payload.get("type") == "user_message"
    )
    original = parsed_json.get("twiccOriginalContent")
    if (
        is_private_user_rewrite
        and isinstance(original, dict)
        and original.get("type") == "message"
        and original.get("role") == "user"
    ):
        return original
    return None


def _goal_context_objective(parsed_json: dict) -> str | None:
    """Return the objective of a Codex goal-continuation context message.

    When a thread has an active goal, Codex injects a ``response_item.message``
    (role=user) whose first ``input_text`` block is a
    ``<codex_internal_context source="goal">`` block wrapping the objective —
    the continuation prompt uses an ``<objective>`` tag, the mid-run
    objective-edit steer (a ``/goal`` set on an already-active goal) uses
    ``<untrusted_objective>``::

        {"type": "response_item", "payload": {"type": "message", "role": "user",
          "content": [{"type": "input_text",
            "text": "<codex_internal_context source=\\"goal\\">…<objective>\\nDo X\\n</objective>…"}]}}

    Returns the objective body (XML-entity-unescaped) when the line matches that
    shape, else ``None``. Used to rewrite the line into a human-looking ``/goal
    <objective>`` user message — see :meth:`_transform_inline_provider`. (Codex
    budget/usage-limit steers reuse the same ``source="goal"`` + ``<objective>``
    shape; for TwiCC's budget-free ``/goal`` that case never arises.)
    """
    payload = _goal_context_payload(parsed_json)
    if payload is None:
        return None
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "input_text":
        return None
    text = first.get("text")
    if not isinstance(text, str) or not text.lstrip().startswith(_GOAL_CONTEXT_MARKER):
        return None
    match = _GOAL_OBJECTIVE_RE.search(text)
    if match is None:
        return None
    objective = html.unescape(match.group(2)).strip()
    return objective or None


def _goal_snapshot_from_tool_result(parsed_json: dict) -> dict | None:
    """Return a structured Goal snapshot from a successful tool result.

    Native calls serialise their result as one JSON string. GPT-5.6 code mode
    commonly stores an array whose first text block is the script status and
    whose second block is ``text(JSON.stringify(result))``. Accept both forms,
    plus a combined status+body string, and require the Goal's stable
    ``threadId`` / ``status`` fields before treating it as lifecycle evidence.
    """
    if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
        return None
    output = payload.get("output")
    texts: list[str] = []
    if isinstance(output, str):
        texts.append(output)
    elif isinstance(output, list):
        for part in output:
            if not isinstance(part, dict) or part.get("type") not in {"input_text", "output_text"}:
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)

    for text in reversed(texts):
        candidates = [text.strip()]
        parsed_output = parse_code_mode_output(text)
        if parsed_output is not None and parsed_output.status == "completed" and parsed_output.body:
            candidates.insert(0, parsed_output.body.strip())
        for candidate in candidates:
            if not candidate or '"goal"' not in candidate:
                continue
            try:
                result = orjson.loads(candidate)
            except orjson.JSONDecodeError:
                continue
            if not isinstance(result, dict):
                continue
            goal = result.get("goal")
            if (
                isinstance(goal, dict)
                and isinstance(goal.get("threadId"), str)
                and isinstance(goal.get("status"), str)
            ):
                return goal
    return None


# TwiCC-injected commands (via ``thread/inject_items``) for which Codex writes
# no rollout line for on its own, so TwiCC injects one to keep the command
# visible in the transcript:
#   - ``/goal clear`` — its RPC emits a wire-only notification, nothing to disk.
#   - ``/compact`` — its RPC writes only the ``compacted`` summary (the divider),
#     never a "the user asked to compact" line, so without this the command
#     survives only as a transient optimistic bubble (retired on completion).
#   - ``/plan`` (bare) — the collaboration-mode ``thread/settings/update`` RPC
#     writes nothing to the rollout. Only the bare form injects; ``/plan
#     <prompt>`` opens a real turn whose user_message needs no marker.
# They land as a ``response_item.message`` (role=user) carrying the literal
# command; the transform relabels them as real user messages. Real user input
# never takes this shape (it is an canonical ``UserMessage`` item), so an exact
# match is unambiguous.
_INJECTED_COMMANDS = frozenset({"/goal clear", "/compact", "/plan"})


def _injected_provider_error(parsed_json: dict) -> CodexProviderError | None:
    """Return an error carried by a TwiCC-injected rollout item."""
    if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "input_text":
        return None
    text = first.get("text")
    if not isinstance(text, str) or PROVIDER_ERROR_MARKER not in text:
        return None
    return parse_provider_error_marker(text)


def _is_internal_resume_message(parsed_json: dict) -> bool:
    """Whether an event is TwiCC's hidden instruction for a failed turn."""
    message = user_message_text(parsed_json)
    return isinstance(message, str) and message.lstrip().startswith("<twicc-resume>")


def _injected_command_text(parsed_json: dict) -> str | None:
    """Return the command of a TwiCC-injected message, or ``None``.

    Matches a ``response_item.message`` (role=user) whose sole ``input_text`` is
    exactly one of :data:`_INJECTED_COMMANDS`. Used to rewrite the injected
    line into a real ``user_message`` — see :meth:`_transform_inline_provider`.
    """
    if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content")
    if not isinstance(content, list) or len(content) != 1:
        return None
    first = content[0]
    if not isinstance(first, dict) or first.get("type") != "input_text":
        return None
    text = first.get("text")
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    return stripped if stripped in _INJECTED_COMMANDS else None


# Opening tag of a Plan-mode final answer, on its own line — the shape is a
# stable contract from Codex's built-in Plan-mode instructions (exact tag,
# never translated, own line). Mirrors the frontend detection in
# ``codex/AssistantMessage.vue``.
_PROPOSED_PLAN_OPEN_RE = re.compile(r"(?:^|\n)[ \t]*<proposed_plan>[ \t]*(?:\r?\n|$)")


def _proposed_plan_message_text(parsed_json: dict) -> str | None:
    """Return the text of a Plan-mode final answer ``response_item``, or ``None``.

    A Plan collaboration-mode turn delivers its final answer as a ``Plan``
    turn item, NOT an ``agentMessage`` — so Codex writes no
    canonical ``AgentMessage`` item for it (and ``task_complete`` carries
    ``last_agent_message: null``). The only rollout line with the plan text
    is the model-history ``response_item.message`` (role=assistant), which
    normally classifies as SYSTEM because it duplicates the agent_message…
    except here, where there is nothing to duplicate. Matches an assistant
    ``response_item.message`` whose ``output_text`` content carries a
    ``<proposed_plan>`` opening tag on its own line; the caller relabels it
    into a canonical canonical ``AgentMessage`` item so the plan actually shows.
    """
    if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") != "message" or payload.get("role") != "assistant":
        return None
    content = payload.get("content")
    if not isinstance(content, list):
        return None
    texts = [
        entry.get("text")
        for entry in content
        if isinstance(entry, dict) and entry.get("type") == "output_text"
        and isinstance(entry.get("text"), str)
    ]
    if not texts:
        return None
    text = "\n\n".join(texts)
    if not _PROPOSED_PLAN_OPEN_RE.search(text):
        return None
    return text


def _turn_context_collaboration_mode(parsed_json: dict) -> str | None:
    """Return a ``turn_context`` line's collaboration mode, or ``None``.

    ``None`` for any other line shape. A ``turn_context`` without a
    ``collaboration_mode`` (rollouts predating the field) counts as
    ``"default"`` — for transition tracking, the field's absence IS the
    default mode.
    """
    if parsed_json.get("type") != _TYPE_TURN_CONTEXT:
        return None
    payload = _payload(parsed_json)
    if payload is None:
        return None
    collaboration = payload.get("collaboration_mode")
    if isinstance(collaboration, dict):
        mode = collaboration.get("mode")
        if isinstance(mode, str) and mode:
            return mode
    return "default"


class _PlanPrefixState:
    """Per-session scan state for the ``/plan <prompt>`` display restoration.

    A ``/plan <prompt>`` command runs ``<prompt>`` as a normal turn (the
    literal ``/plan`` never reaches the model), so the durable
    ``user_message`` line loses the prefix the user typed. The compute
    restores it on the stored copy, deterministically from the file: the
    command's turn is the one whose ``turn_context`` TRANSITIONS the
    collaboration mode to ``plan`` with no injected ``/plan`` marker in
    between (the bare form injects its marker before the next turn, and
    the sticky mode makes every later plan turn a non-transition).

    Mutable on purpose (unlike the repo's usual NamedTuple pattern): the
    fields evolve line by line during a sequential scan.
    """

    __slots__ = ("armed", "last_mode", "marker_seen")

    def __init__(self, last_mode: str | None = None) -> None:
        # Collaboration mode of the last ``turn_context`` seen. ``None``
        # means "not seen yet in this process" — the live path then seeds
        # it from the DB on demand (batch mode starts at ``"default"``: a
        # full replay that has seen no turn yet is in the default mode).
        self.last_mode = last_mode
        # An injected bare-``/plan`` marker was seen since the last
        # ``turn_context``: the upcoming default→plan transition is the
        # bare form's, whose next turn message is ordinary input.
        self.marker_seen = False
        # The next native user_message is a ``/plan <prompt>`` inline
        # prompt and must be re-prefixed.
        self.armed = False


class _GoalContextState:
    """Per-session scan state deciding which Goal context is user-visible.

    Codex injects the same internal user-role prompt before every autonomous
    continuation turn. Only the first one after a goal activation/update is a
    transcript boundary representing the user's ``/goal`` command; the rest
    are provider control traffic. Mutable because the decision evolves during
    the sequential compute scan.
    """

    __slots__ = ("initialized", "last_objective", "seen_context", "show_next")

    def __init__(self, *, initialized: bool = False) -> None:
        self.initialized = initialized
        self.seen_context = False
        self.last_objective: str | None = None
        self.show_next = False


def _parse_subagent_notification(parsed_json: dict) -> tuple[str, dict] | None:
    """Decode a ``<subagent_notification>`` user message.

    Returns ``(agent_path, status_dict)`` when the message body parses
    as the expected JSON object, where ``status_dict`` is the raw
    ``AgentStatus`` shape (e.g. ``{"completed": "msg"}``,
    ``{"errored": "msg"}``, ``"shutdown"``, ``"not_found"``). Returns
    ``None`` if the line is not a subagent notification or the body is
    malformed (defensive — the SDK currently always serialises a valid
    payload, but a future schema change shouldn't crash the pipeline).
    """
    text = _subagent_notification_text(parsed_json)
    if text is None:
        return None
    start = text.find(_SUBAGENT_NOTIFICATION_START)
    end = text.find(_SUBAGENT_NOTIFICATION_END, start + len(_SUBAGENT_NOTIFICATION_START))
    if start < 0 or end < 0:
        return None
    body = text[start + len(_SUBAGENT_NOTIFICATION_START):end].strip()
    if not body:
        return None
    try:
        parsed = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    agent_path = parsed.get("agent_path")
    if not isinstance(agent_path, str) or not agent_path:
        return None
    status = parsed.get("status")
    return agent_path, status if isinstance(status, (dict, str)) else None


def _status_error_text(status) -> str | None:
    """Translate a Codex ``AgentStatus`` into a ``ToolResultLink.error`` string.

    Mirrors the Rust ``AgentStatus`` enum (snake_case-tagged JSON):

    - ``{"completed": <msg | null>}``: success, no error.
    - ``{"errored": <msg>}``: surface the SDK message verbatim.
    - ``"shutdown"``: subagent was shut down before producing a final
      message — treat as an error so the tool flips to error state in
      the UI.
    - ``"not_found"``: subagent reference no longer exists (race or
      cleanup) — same treatment.
    - non-final variants (``"pending_init"``, ``"running"``,
      ``"interrupted"``) shouldn't reach us through a notification
      (Codex only emits the message on a final status), but if they do
      we return ``None`` so the tool stays running.

    Returns ``None`` on success / non-final / malformed status.
    """
    if isinstance(status, dict):
        if "completed" in status:
            return None
        errored = status.get("errored")
        if isinstance(errored, str) and errored:
            return errored
        return None
    if status == "shutdown":
        return "Subagent shut down"
    if status == "not_found":
        return "Subagent not found"
    return None


class _SubAgentSpawn(NamedTuple):
    """One multi-agent v2 spawn, as decoded from a ``SubAgentActivity``.

    - ``call_id``: the ``spawn_agent`` call this event acknowledges.
    - ``agent_id``: the subagent's thread id (a TwiCC ``Session.id``).
    - ``agent_path``: the collaboration handle (``/root/<task_name>``),
      the key the ``FINAL_ANSWER`` termination message is addressed by.
    """

    call_id: str
    agent_id: str
    agent_path: str


def _parse_sub_agent_activity_started(parsed_json: dict) -> _SubAgentSpawn | None:
    """Decode the multi-agent **v2** spawn event.

    canonical ``SubAgentActivity`` item is emitted in the parent thread
    every time a collaboration tool touches a subagent. Only
    ``kind == "started"`` marks a spawn, and only then does ``event_id``
    hold the ``call_id`` of the originating ``spawn_agent``: the
    ``interacted`` / ``interrupted`` kinds carry the call_id of whatever
    other tool touched the agent (a ``send_message``, …), which must
    never produce an :class:`~twicc.core.models.AgentLink`.

    Returns ``(spawn_call_id, agent_thread_id, agent_path)``, or ``None``
    for any other line shape / kind / malformed payload.
    """
    payload = completed_item(parsed_json)
    if payload is None or payload.get("type") != "SubAgentActivity":
        return None
    if payload.get("kind") != _SUB_AGENT_ACTIVITY_STARTED_KIND:
        return None
    call_id = payload.get("id")
    agent_id = payload.get("agent_thread_id")
    agent_path = payload.get("agent_path")
    if not isinstance(call_id, str) or not call_id:
        return None
    if not isinstance(agent_id, str) or not agent_id:
        return None
    if not isinstance(agent_path, str) or not agent_path:
        return None
    return _SubAgentSpawn(call_id, agent_id, agent_path)


class _InterAgentMessage(NamedTuple):
    """One decoded multi-agent v2 inter-agent envelope.

    - ``message_type``: ``NEW_TASK`` / ``MESSAGE`` / ``FINAL_ANSWER``…
    - ``sender``: the sending agent's path (``/root``, ``/root/impl``…).
      Never used as a *filter* — a nested subagent's parent is not
      ``/root`` — only surfaced as "who asked".
    - ``task_path``: the receiver's own agent path, as the envelope's
      ``Task name:`` line spells it (``/root/tweak_display_test``).
    - ``payload``: the inline body, empty when the payload travelled
      encrypted.
    - ``encrypted``: an ``encrypted_content`` block sits next to the
      header. That block's *type* is the signal — no ciphertext
      sniffing, and a future plaintext ``NEW_TASK`` would read normally.
    """

    message_type: str
    sender: str | None
    task_path: str | None
    payload: str
    encrypted: bool

    @property
    def task_name(self) -> str | None:
        """The receiver's task name — the last segment of its agent path."""
        if not self.task_path:
            return None
        return self.task_path.rsplit("/", 1)[-1] or None


def _parse_inter_agent_message(parsed_json: dict) -> _InterAgentMessage | None:
    """Decode a multi-agent **v2** inter-agent message envelope.

    Shape (see :data:`_AGENT_MESSAGE_TYPE_PREFIX`): a
    ``response_item.agent_message`` whose first content block is plain
    text opening with ``Message Type: <TYPE>``, followed by the routing
    header and the payload. Codex persists it in the *receiving* thread,
    so the same shape carries a task handed down to a subagent
    (``NEW_TASK``), a mid-flight message either way (``MESSAGE``) and the
    answer handed back up (``FINAL_ANSWER``).

    Returns ``None`` for any other line shape or a malformed envelope.
    """
    if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
        return None
    payload = _payload(parsed_json)
    if payload is None or payload.get("type") != _PAYLOAD_AGENT_MESSAGE:
        return None
    content = payload.get("content")
    if not isinstance(content, list) or not content:
        return None
    first = content[0]
    if not isinstance(first, dict):
        return None
    text = first.get("text")
    if not isinstance(text, str) or not text.startswith(_AGENT_MESSAGE_TYPE_PREFIX):
        return None

    message_type = ""
    sender: str | None = None
    task_path: str | None = None
    payload_lines: list[str] | None = None
    for line in text.split("\n"):
        if payload_lines is not None:
            payload_lines.append(line)
            continue
        if line.startswith(_AGENT_MESSAGE_TYPE_PREFIX):
            message_type = line[len(_AGENT_MESSAGE_TYPE_PREFIX):].strip()
            continue
        if line.startswith(_AGENT_MESSAGE_SENDER_PREFIX):
            sender = line[len(_AGENT_MESSAGE_SENDER_PREFIX):].strip() or None
            continue
        if line.startswith(_AGENT_MESSAGE_TASK_NAME_PREFIX):
            task_path = line[len(_AGENT_MESSAGE_TASK_NAME_PREFIX):].strip() or None
            continue
        if line.rstrip() == _AGENT_MESSAGE_PAYLOAD_MARKER:
            payload_lines = []
    if not message_type:
        return None
    encrypted = any(
        isinstance(block, dict) and block.get("type") == _ENCRYPTED_CONTENT_BLOCK_TYPE
        for block in content
    )
    return _InterAgentMessage(
        message_type=message_type,
        sender=sender,
        task_path=task_path,
        payload="\n".join(payload_lines).strip() if payload_lines else "",
        encrypted=encrypted,
    )


def _parse_agent_final_answer(parsed_json: dict) -> tuple[str, str] | None:
    """Return ``(sender_agent_path, answer_text)`` for a ``FINAL_ANSWER``.

    The v2 "spawn_agent terminated" signal: a subagent finishing its work
    hands its answer back to its parent, in the clear. Returns ``None``
    for every other envelope (``NEW_TASK`` / ``MESSAGE``, whose payloads
    are encrypted anyway) and for a sender-less one, which nothing could
    be paired with.
    """
    message = _parse_inter_agent_message(parsed_json)
    if message is None or message.message_type != _AGENT_MESSAGE_FINAL_ANSWER_TYPE:
        return None
    if message.sender is None:
        return None
    return message.sender, message.payload


def _humanize_identifier(raw: str) -> str:
    """Sentence-case a machine identifier: ``tweak_display_test`` → ``Tweak display test``.

    Mirrors the frontend's ``humanizeToolSegment`` so a task name reads
    the same in the session title (written here) and on the parent's
    spawn card (rendered there).
    """
    spaced = raw.replace("_", " ").replace("-", " ").strip()
    return spaced[:1].upper() + spaced[1:] if spaced else ""


def _parse_agent_new_task(parsed_json: dict) -> _InterAgentMessage | None:
    """Return the ``NEW_TASK`` envelope handed to *this* thread, if any.

    This is a subagent's opening prompt — what a human's first message is
    to a top-level session — so :meth:`CodexSessionCompute.compute_item_kind`
    promotes it to a ``USER_MESSAGE``. Filtering is on the message type
    alone, never on the sender: a nested subagent receives its task from
    another agent (``/root/impl``), not from ``/root``, and must be
    treated identically. ``followup_task`` sends further ``NEW_TASK``
    envelopes to a live agent, and each is a genuine new instruction —
    so this is not restricted to the first one either.
    """
    message = _parse_inter_agent_message(parsed_json)
    if message is None or message.message_type != _AGENT_MESSAGE_NEW_TASK_TYPE:
        return None
    return message


def _patch_apply_error(payload: dict) -> str | None:
    """Synthesise an error string from a ``FileChange`` payload.

    Codex emits a structured ``success`` boolean alongside ``status``
    (``completed`` / ``failed`` / ``declined``) and a ``stderr`` line
    describing the failure (e.g. ``"Failed to delete file …"`` or
    ``"patch rejected by user"``). We surface that text verbatim when
    available so the front-end's error callout shows the actual
    parser/IO error, falling back to a generic label when it isn't.

    Returns ``None`` on success or when the payload isn't a
    ``FileChange``.
    """
    if payload.get("type") != "FileChange":
        return None
    if payload.get("status") == "completed":
        return None
    stderr = payload.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        return stderr.strip()
    if payload.get("status") == "declined":
        return "Patch declined"
    return "Patch failed"


def _mcp_tool_call_end_error(payload: dict) -> str | None:
    """Synthesise an error string from a ``McpToolCall`` payload.

    The wire shape of ``payload.result`` mirrors the Rust
    ``Result<CallToolResult, String>`` (cf. ``codex-rs/protocol/src/protocol.rs``)
    so two distinct error cases exist:

    - ``{"Err": "<message>"}`` — the invocation itself failed (transport,
      MCP server unreachable, …). The string carries a usable error
      label.
    - ``{"Ok": {"isError": true, "content": [...], ...}}`` — the
      invocation reached the server but the tool returned an error
      (``CallToolResult.is_error`` in Rust, serialised as ``isError``
      in camelCase per ``mcp.rs:138-151``). The content may carry a
      message but extracting it reliably across MCP servers is
      brittle, so we surface a generic ``"Tool error"`` label for now
      — adjust later if we see consistent shapes worth parsing.

    Returns ``None`` when the payload isn't an ``McpToolCall`` or
    when no error is reported.
    """
    if payload.get("type") != "McpToolCall":
        return None
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        return "Tool error"
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    if result.get("isError") is True:
        return "Tool error"
    return None


def _event_msg_payload_error(payload: dict) -> str | None:
    """Dispatch a canonical result item to the matching error helper.

    ``FileChange`` and ``McpToolCall`` expose a usable error signal;
    any other item yields ``None``. Errors for the exec_command family
    are derived from the ``function_call_output`` text via
    :func:`_exit_code_error_from_output` instead. Image-generation items
    are classified as ``IMAGE`` (not a tool result) and never reach this
    helper.
    """
    err = _patch_apply_error(payload)
    if err is not None:
        return err
    return _mcp_tool_call_end_error(payload)


def _count_diff_lines(unified_diff: str) -> tuple[int, int]:
    """Count ``+`` / ``-`` body lines in a unified-diff string.

    Header lines (``--- a/foo``, ``+++ b/foo``) and hunk markers
    (``@@ ...``) are ignored — only payload mutations are counted.
    Returns ``(added, removed)``.
    """
    added = 0
    removed = 0
    for line in unified_diff.splitlines():
        if not line:
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def _has_summary_text(reasoning_payload: dict) -> bool:
    """Return ``True`` when a ``response_item.reasoning`` payload has visible summary text.

    OpenAI publishes a summary at the model's discretion: most reasoning
    blocks come back with an empty ``summary: []`` array (no useful text
    to render), and occasionally one carries one or more
    ``{"type": "summary_text", "text": "..."}`` entries. Only the latter
    are worth rendering — the former would amount to an empty collapsible
    card and is better hidden behind DEBUG_ONLY.
    """
    summary = reasoning_payload.get("summary")
    if not isinstance(summary, list):
        return False
    for entry in summary:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") != "summary_text":
            continue
        text = entry.get("text")
        if isinstance(text, str) and text.strip():
            return True
    return False


def _user_terminated_tool_reason(session_id: str, call_id: str) -> str | None:
    """Lookup the live agent's ``_user_terminated_tool_ids`` map for a record.

    A "user-terminated" tool is one the agent knows the user ended out of
    band — an approval Deny / Cancel / refused permissions, or a turn
    interruption (:meth:`CodexAgent.soft_interrupt`). In every case Codex
    emits a ``function_call_output`` carrying only rejection / "aborted by
    user" text with no ``is_error`` flag and no ``Process exited`` trailer,
    so the signal recorded here is the only way the compute can tell the
    tool is finished.

    ``Provider`` is already imported at module top (used elsewhere in
    this file). Only ``get_agent_manager_registry`` is lazily imported
    to avoid a static cycle between ``compute`` and the agent package.
    Returns ``None`` cleanly if anything is missing (no live agent, no
    entry, no manager registered).
    """
    try:
        from twicc.agent.registry import get_agent_manager_registry
    except ImportError:
        return None
    try:
        manager = get_agent_manager_registry().get(Provider.CODEX)
    except KeyError:
        # Provider not yet registered (extremely early startup, before the
        # registry has wired up every provider — should be impossible at
        # actual compute time, but cheap to guard).
        return None
    if manager is None:
        return None
    # The accessor is defensive: returns None if no live agent for the session.
    return manager.get_user_terminated_tool_reason(session_id, call_id)


def _data_url_image(value: object) -> tuple[str, str] | None:
    """Split an ``input_image`` data URL into ``(media_type, base64_data)``.

    Codex tool results carry images as ``image_url`` data URLs
    (``data:image/png;base64,<data>``), unlike Claude's structured
    ``{media_type, data}`` source dicts. Returns ``None`` for anything
    that isn't a base64 ``image/*`` data URL (http(s) references, other
    media families, percent-encoded non-base64 payloads).
    """
    if not isinstance(value, str) or not value.startswith("data:image/"):
        return None
    header, sep, data = value.partition(",")
    if not sep or not data or not header.endswith(";base64"):
        return None
    media_type = header[len("data:"):-len(";base64")]
    return media_type, data


class CodexSessionCompute(BaseSessionCompute):
    """Concrete :class:`BaseSessionCompute` for Codex sessions.

    Classifies user/assistant messages and tool_use lines, plus pairs
    each tool_use with its output via the inherited ``ToolResultLink``
    machinery. Everything else is ``SYSTEM``.

    Carries a small per-session cache (``_exec_command_maps``) used to
    rebind ``write_stdin`` polling outputs to the parent ``exec_command``
    they belong to, since Codex CLI no longer persists the
    ``exec_command_end`` event that previously tied the chain together.
    The cache is keyed by ``Session.id`` so the singleton stays safe
    even if the watcher interleaves multiple sessions.
    :func:`get_compute` returns a per-process singleton.
    """

    provider: ClassVar[Provider] = Provider.CODEX

    def __init__(self) -> None:
        super().__init__()
        # {session_id: {exec_command_id: exec_command_call_id}}.
        # Populated by :meth:`analyze_content` (batch) and
        # :meth:`extract_tool_result_info` (live) when they see a Codex
        # ``function_call_output`` for an ``exec_command`` whose trailer
        # reports a still-running unified-exec process. Read by the
        # remap hooks to resolve a ``write_stdin`` polling output back to
        # the parent ``exec_command``'s ``call_id``. Entries are cleared
        # both eagerly (when a "Process exited" status is observed) and
        # lazily (in :meth:`end_session_compute`).
        self._exec_command_maps: dict[str, dict[int, str]] = {}
        # {session_id: {cell_id: exec_call_id}}. Code-mode analog of
        # ``_exec_command_maps``: populated by :meth:`analyze_content`
        # (via :meth:`_maintain_code_cell_map`) when an ``exec``
        # custom_tool_call_output reports ``Script running with cell ID
        # <id>``; read by :meth:`remap_tool_result_id` to rebind a
        # ``wait`` function_call_output to the owning ``exec`` call.
        # Entries are evicted when the chain reports a final status
        # (completed / failed / terminated) — on the exec's own output
        # in :meth:`_maintain_code_cell_map`, on a wait chunk in
        # :meth:`remap_tool_result_id` (after the pairing read, like
        # write_stdin) — and lazily in :meth:`end_session_compute`.
        self._code_cell_maps: dict[str, dict[str, str]] = {}
        # {session_id: [(exec_call_id, targets), ...]} in line order.
        # One entry per code-mode ``exec`` whose script declares at
        # least one nested call that later emits an orphan end event
        # (``apply_patch`` → ``FileChange``, ``mcp__*`` →
        # ``McpToolCall``). Populated by :meth:`analyze_content`;
        # read by :meth:`_remap_orphan_end_event` to rebind the orphan
        # event (whose ``call_id`` is the nested ``exec-<uuid>``) to the
        # outer ``exec`` call — declared-target match first (patch paths
        # / MCP tool name), recency as fallback. Bounded (last 50
        # entries) and freed in :meth:`end_session_compute`.
        self._code_exec_targets: dict[str, list[tuple[str, CodeModeScriptTargets]]] = {}
        # {session_id: {completion key: spawn_agent_call_id}}. Batch-only
        # side-table letting the subagent's completion line rebind onto
        # the originating ``spawn_agent`` ``ToolResultLink`` chain
        # without a DB lookup. The key is whatever that completion line
        # addresses the agent by, which differs per protocol generation
        # (the two key spaces can't collide — a UUID never starts with
        # ``/``):
        #
        # - **v1**: the subagent's ``agent_id``, recorded when
        #   ``analyze_content`` sees the spawn ack (its output JSON
        #   carries ``agent_id``); the ``<subagent_notification>`` user
        #   message repeats it in its ``agent_path`` field.
        # - **v2**: the subagent's ``agent_path``, recorded when
        #   ``analyze_content`` sees the ``SubAgentActivity`` spawn
        #   event; the ``FINAL_ANSWER`` message names it as ``Sender``.
        #
        # Live mode ignores this map: v1 falls back to
        # ``AgentLink.objects`` (the row is already persisted from the
        # prior sync that processed the spawn ack) and v2 to
        # :meth:`_lookup_spawn_call_id_for_agent_path` (no model column
        # carries an agent path).
        # Initialised by :meth:`begin_session_compute`, freed by
        # :meth:`end_session_compute`. Lazily created on first access in
        # :meth:`_agent_id_map` to tolerate the live path that never
        # calls ``begin_session_compute``.
        self._agent_id_to_spawn_call_id: dict[str, dict[str, str]] = {}
        # {session_id: last seen ``info.total_token_usage.total_tokens``}.
        # Updated by :meth:`compute_item_cost_and_usage` on every
        # billable token_count event. The cumulative total advances only
        # when the LLM call actually consumed tokens, so a token_count
        # whose total matches the previous one carries no new activity:
        # it's the bootstrap (``info: null``), an inter-turn re-emission
        # (Codex republishes the previous totals at the start of a new
        # turn), or the zero-snapshot emitted alongside a compaction.
        # All three paths are filtered with a single equality check
        # against this map. Initialised by :meth:`begin_session_compute`
        # in batch mode and lazily seeded from the DB
        # (:meth:`_lookup_prev_total_tokens`) in live mode.
        self._prev_total_tokens: dict[str, int] = {}
        # {session_id: _PlanPrefixState}. Sequential-scan state restoring
        # the ``/plan `` prefix on the user message of a ``/plan <prompt>``
        # command turn — see :meth:`_note_turn_context_mode` /
        # :meth:`_restore_plan_prefix`. Initialised by
        # :meth:`begin_session_compute` in batch mode; the live path
        # lazily seeds ``last_mode`` from the DB
        # (:meth:`_lookup_prev_plan_context`).
        self._plan_prefix_states: dict[str, _PlanPrefixState] = {}
        # {session_id: _GoalContextState}. Sequential-scan state deciding
        # whether a provider-injected Goal context is the visible command
        # boundary or a hidden autonomous continuation prompt. Batch mode
        # starts clean; live mode lazily seeds from prior persisted goal lines.
        self._goal_context_states: dict[str, _GoalContextState] = {}

    def _proc_map(self, session_id: str) -> dict[int, str]:
        """Return the per-session ``{exec_command_id: call_id}`` map.

        Lazily creates the map on first access — the live path may call
        the extraction hooks before any explicit :meth:`begin_session_compute`,
        so we tolerate a missing entry instead of treating it as a bug.
        """
        return self._exec_command_maps.setdefault(session_id, {})

    def _cell_map(self, session_id: str) -> dict[str, str]:
        """Return the per-session ``{cell_id: exec_call_id}`` map.

        Lazily creates the map on first access for the same reason as
        :meth:`_proc_map`.
        """
        return self._code_cell_maps.setdefault(session_id, {})

    def _code_exec_target_map(self, session_id: str) -> list[tuple[str, CodeModeScriptTargets]]:
        """Return the per-session list of end-event-emitting ``exec`` records.

        Lazily creates the list on first access for the same reason as
        :meth:`_proc_map`.
        """
        return self._code_exec_targets.setdefault(session_id, [])

    def _agent_id_map(self, session_id: str) -> dict[str, str]:
        """Return the per-session ``{completion key: spawn_agent_call_id}`` map.

        Lazily creates the map on first access for the same reason as
        :meth:`_proc_map`. The live path doesn't actually consult this
        map (it queries ``AgentLink`` instead), so a missing entry there
        is harmless.
        """
        return self._agent_id_to_spawn_call_id.setdefault(session_id, {})

    def _plan_prefix_state(self, session_id: str) -> _PlanPrefixState:
        """Return the per-session ``/plan``-prefix scan state.

        Lazily creates it on first access for the same reason as
        :meth:`_proc_map`; the ``None`` ``last_mode`` of a lazy creation
        is the live path's "seed from DB when it matters" sentinel.
        """
        return self._plan_prefix_states.setdefault(session_id, _PlanPrefixState())

    def _goal_context_state(self, session_id: str, line_num: int) -> _GoalContextState:
        """Return goal-context scan state, seeding a fresh live process from DB."""
        state = self._goal_context_states.setdefault(session_id, _GoalContextState())
        if not state.initialized:
            state = self._lookup_prev_goal_context_state(session_id, line_num)
            self._goal_context_states[session_id] = state
        return state

    def begin_session_compute(self, session_id: str) -> None:
        # Reset per-session state at the start of a batch compute so a
        # previous run's leftover values can never leak into the new
        # pass. Batch always reprocesses every line of the session, so
        # starting the running total at zero is correct.
        self._exec_command_maps[session_id] = {}
        self._code_cell_maps[session_id] = {}
        self._code_exec_targets[session_id] = []
        self._agent_id_to_spawn_call_id[session_id] = {}
        self._prev_total_tokens[session_id] = 0
        self._plan_prefix_states[session_id] = _PlanPrefixState(last_mode="default")
        self._goal_context_states[session_id] = _GoalContextState(initialized=True)

    def end_session_compute(self, session_id: str) -> None:
        # Free the per-session caches after a batch compute finishes.
        # Live mode never calls this, which is fine: the exec_command
        # map is bounded by concurrent unified-exec processes (usually
        # 0–2) and entries get evicted on "Process exited"; the
        # agent_id map mirrors AgentLink rows already in DB; the
        # token-count map carries one int per active session.
        self._exec_command_maps.pop(session_id, None)
        self._code_cell_maps.pop(session_id, None)
        self._code_exec_targets.pop(session_id, None)
        self._agent_id_to_spawn_call_id.pop(session_id, None)
        self._prev_total_tokens.pop(session_id, None)
        self._plan_prefix_states.pop(session_id, None)
        self._goal_context_states.pop(session_id, None)

    def _release_exec_command_for_call(
        self, session_id: str, call_id: str
    ) -> None:
        """Drop any map entry that points at ``call_id``.

        Used after observing a terminating ``Process exited`` line in
        the function_call_output chain (either the exec_command's own
        output or one of its write_stdin polls). We don't always know
        the ``exec_command_id`` (the "exited" trailer doesn't include
        it), so we scan by value — the map stays small in practice.
        """
        proc_map = self._exec_command_maps.get(session_id)
        if not proc_map:
            return
        for exec_command_id, mapped_call_id in list(proc_map.items()):
            if mapped_call_id == call_id:
                proc_map.pop(exec_command_id, None)

    def _release_code_cell_for_call(self, session_id: str, call_id: str) -> None:
        """Drop any code-cell map entry that points at ``call_id``.

        Code-mode counterpart of :meth:`_release_exec_command_for_call`,
        used when an ``exec`` output reports a final script status. The
        final chunk doesn't repeat the cell id, so we scan by value —
        the map stays tiny (concurrent background cells).
        """
        cell_map = self._code_cell_maps.get(session_id)
        if not cell_map:
            return
        for cell_id, mapped_call_id in list(cell_map.items()):
            if mapped_call_id == call_id:
                cell_map.pop(cell_id, None)

    def remap_tool_result_id(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> str:
        """Rebind a write_stdin / wait function_call_output OR a subagent notification.

        Three unrelated chains converge here:

        - ``write_stdin`` ``function_call_output``: rebound to the
          parent ``exec_command`` via ``self._exec_command_maps``,
          populated by :meth:`analyze_content` when it saw the parent's
          first ``Process running with session ID N`` line. Falls back
          to identity when the chain can't be resolved.
        - ``wait`` ``function_call_output`` (code mode): rebound to the
          owning ``exec`` custom_tool_call via ``self._code_cell_maps``,
          populated by :meth:`analyze_content` when it saw the parent's
          ``Script running with cell ID <id>`` status header. Same
          identity fallback.
        - the subagent completion line — a ``<subagent_notification>``
          user message on multi-agent v1, a ``FINAL_ANSWER`` agent
          message on v2: rebound to the originating ``spawn_agent`` via
          ``self._agent_id_to_spawn_call_id``, populated by
          :meth:`analyze_content` when it saw the spawn ack output
          ``{"agent_id": ...}`` (v1) / the ``SubAgentActivity`` spawn
          event (v2). Falls back to identity when no mapping is
          registered (e.g. a completion whose spawn happened in an
          earlier, unsynced part of the thread).

        Also handles eviction for the write_stdin and wait sides: when
        this poll's output reports a terminating status (``Process
        exited`` / a final script status), the entry is removed from the
        map AFTER we resolved the parent_call_id, so analyze_content's
        reading order stays correct (it had already populated / read the
        map by the time we got here).
        """
        if (
            _subagent_notification_text(parsed_json) is not None
            or _parse_agent_final_answer(parsed_json) is not None
        ):
            agent_map = self._agent_id_to_spawn_call_id.get(session_id)
            if not agent_map:
                return naive_tool_use_id
            return agent_map.get(naive_tool_use_id, naive_tool_use_id)
        parent = tool_use_map.get(naive_tool_use_id)
        if parent is None:
            # A ``FileChange`` / ``McpToolCall`` from a
            # code-mode nested call carries the synthesized
            # ``exec-<uuid>`` call_id, matching no rollout tool_use —
            # rebind it to the owning ``exec``.
            return self._remap_orphan_end_event(
                parsed_json, naive_tool_use_id, session_id=session_id,
            )
        if parent.tool_name == _CODE_MODE_WAIT_TOOL:
            return self._remap_wait_result_id(
                parsed_json, naive_tool_use_id, session_id=session_id, parent=parent
            )
        if parent.tool_name != "write_stdin":
            return naive_tool_use_id
        exec_command_id = _extract_write_stdin_exec_command_id(parent.parsed_json)
        if exec_command_id is None:
            return naive_tool_use_id
        proc_map = self._exec_command_maps.get(session_id)
        if not proc_map:
            return naive_tool_use_id
        parent_call_id = proc_map.get(exec_command_id, naive_tool_use_id)
        # Evict the entry on a terminating poll so any stray future
        # write_stdin against the same id doesn't latch onto a stale
        # call_id (Codex would never reissue, but defensive cleanup is
        # cheap).
        payload = _payload(parsed_json)
        if payload is not None:
            output = payload.get("output", "")
            if (
                isinstance(output, str)
                and parse_exec_command_status(output).is_terminated
            ):
                proc_map.pop(exec_command_id, None)
        return parent_call_id

    def _remap_orphan_end_event(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
    ) -> str:
        """Rebind a code-mode nested end event to its ``exec`` call.

        When a code-mode script applies a patch or calls an MCP tool, the
        nested handler emits the same persisted end event as a direct
        call — ``FileChange`` with all its riches (structured
        ``changes``, the live-captured ``original_files`` splice),
        ``McpToolCall`` with the structured ``CallToolResult`` —
        but under a synthesized ``exec-<uuid>`` call_id that pairs with
        nothing. Rebinding it to the outer ``exec`` call restores 5.5
        display parity (full-file diff / MCP result body, error surfacing).

        No exact key exists, so the match is heuristic over the
        registered end-event-emitting execs (see ``_code_exec_targets``):

        1. most recent exec whose statically-extracted script declares a
           matching target — a patch path matching the event's
           ``changes`` (suffix match, the envelope may use relative
           paths) for ``FileChange``, the exact
           ``mcp__<server>__<tool>`` name from the event's ``invocation``
           for ``McpToolCall``;
        2. else the most recent exec declaring a call of the same family
           (covers the unresolvable-script case; the canonical wrappers
           run their nested calls synchronously, so recency is right in
           practice).

        Falls back to identity when the line isn't such an event, the
        call_id doesn't carry the nested ``exec-`` prefix, or nothing is
        registered.
        """
        if not naive_tool_use_id.startswith("exec-"):
            return naive_tool_use_id
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return naive_tool_use_id
        payload = completed_item(parsed_json)
        if payload is None:
            return naive_tool_use_id
        records = self._code_exec_targets.get(session_id)
        if not records:
            return naive_tool_use_id
        event_type = payload.get("type")
        if event_type == "FileChange":
            changes = payload.get("changes")
            change_paths = (
                [p for p in changes if isinstance(p, str)] if isinstance(changes, dict) else []
            )
            fallback: str | None = None
            for exec_call_id, targets in reversed(records):
                if not targets.has_patch:
                    continue
                if targets.patch_paths and _changes_match_declared(change_paths, targets.patch_paths):
                    return exec_call_id
                if fallback is None:
                    fallback = exec_call_id
            return fallback if fallback is not None else naive_tool_use_id
        if event_type == "McpToolCall":
            qualified = _mcp_end_qualified_name(payload)
            fallback = None
            for exec_call_id, targets in reversed(records):
                if not targets.mcp_tools:
                    continue
                if qualified is not None and qualified in targets.mcp_tools:
                    return exec_call_id
                if fallback is None:
                    fallback = exec_call_id
            return fallback if fallback is not None else naive_tool_use_id
        return naive_tool_use_id

    def _remap_wait_result_id(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
        parent: ToolUseEntry,
    ) -> str:
        """Rebind a code-mode ``wait`` output to the owning ``exec`` call.

        The wait's own arguments carry the ``cell_id``; the cell map
        (populated when the exec's output announced ``Script running
        with cell ID <id>``) resolves it to the exec's call_id. Eviction
        happens here when this chunk reports a final script status —
        AFTER the pairing read, mirroring the write_stdin flow.
        """
        cell_id = _wait_cell_id_from_payload(_payload(parent.parsed_json))
        if cell_id is None:
            return naive_tool_use_id
        cell_map = self._code_cell_maps.get(session_id)
        if not cell_map:
            return naive_tool_use_id
        parent_call_id = cell_map.get(cell_id, naive_tool_use_id)
        payload = _payload(parsed_json)
        if payload is not None:
            parsed = parse_code_mode_output(payload.get("output"))
            if parsed is not None and parsed.status != "running":
                cell_map.pop(cell_id, None)
        return parent_call_id

    def remap_tool_result_id_live(
        self,
        parsed_json: dict,
        naive_tool_use_id: str,
        *,
        session_id: str,
        item: SessionItem,
    ) -> str:
        """Live equivalent of :meth:`remap_tool_result_id` (no in-memory map).

        Three unrelated chains converge here, mirroring the batch hook:

        - ``<subagent_notification>`` user message (multi-agent v1):
          rebound to the originating ``spawn_agent`` via a single
          ``AgentLink`` DB query keyed on
          ``(session_id, agent_id=naive_tool_use_id)``.
          The AgentLink row was persisted by an earlier sync that
          processed the spawn ack ``function_call_output``, so the
          lookup always succeeds in normal operation. Falls back to
          identity if the row is missing (e.g. truly out-of-order
          syncs — defensive).
        - ``FINAL_ANSWER`` agent message (multi-agent v2): same rebind,
          but the naive id is an agent *path* no model column carries,
          so it goes through
          :meth:`_lookup_spawn_call_id_for_agent_path` (newest
          ``SubAgentActivity`` announcing that path).
        - ``write_stdin`` ``function_call_output``: resolves the parent
          ``exec_command`` through two DB lookups (write_stdin
          arguments → exec_command_id → function_call_output that
          announced it). The cost is incurred only on a write_stdin
          result line, which is rare per session.
        - ``wait`` ``function_call_output`` (code mode): same two-lookup
          shape (wait arguments → cell_id → the ``exec``
          custom_tool_call_output that announced ``Script running with
          cell ID <id>``).
        - nested ``FileChange`` / ``McpToolCall`` (code mode,
          ``call_id`` prefixed ``exec-``): rebound to the owning ``exec``
          custom_tool_call via :meth:`_lookup_orphan_end_exec_call_id`
          (declared-target match on the statically-extracted script —
          patch paths / MCP tool name — recency fallback).

        Falls back to identity at every step that can't be resolved so
        other tools' result rows are unaffected.
        """
        if _subagent_notification_text(parsed_json) is not None:
            from twicc.core.models import AgentLink
            link = AgentLink.objects.filter(
                session_id=session_id, agent_id=naive_tool_use_id,
            ).only("tool_use_id").first()
            if link is None:
                return naive_tool_use_id
            return link.tool_use_id
        if _parse_agent_final_answer(parsed_json) is not None:
            # v2: the naive id is an agent *path*, which no model column
            # carries — resolve it through the ``SubAgentActivity``
            # line that announced the spawn (it holds both the path and
            # the spawning call_id).
            return self._lookup_spawn_call_id_for_agent_path(
                session_id, item.line_num, naive_tool_use_id,
            )
        if parsed_json.get("type") == _TYPE_EVENT_MSG:
            payload = completed_item(parsed_json)
            if payload is not None and naive_tool_use_id.startswith("exec-"):
                if payload.get("type") == "FileChange":
                    return self._lookup_orphan_end_exec_call_id(
                        session_id, item.line_num, naive_tool_use_id,
                        event_type="FileChange",
                        changes=payload.get("changes"),
                    )
                if payload.get("type") == "McpToolCall":
                    return self._lookup_orphan_end_exec_call_id(
                        session_id, item.line_num, naive_tool_use_id,
                        event_type="McpToolCall",
                        mcp_qualified=_mcp_end_qualified_name(payload),
                    )
            # Direct apply_patch / MCP end events keep their own call_id.
            return naive_tool_use_id
        parent_payload = self._lookup_tool_call_payload(
            session_id, item.line_num, naive_tool_use_id
        )
        if parent_payload is None:
            return naive_tool_use_id
        parent_name = _tool_use_name(parent_payload)
        if parent_name == _CODE_MODE_WAIT_TOOL:
            cell_id = _wait_cell_id_from_payload(parent_payload)
            if cell_id is None:
                return naive_tool_use_id
            owner_call_id = self._lookup_code_cell_call_id(
                session_id, item.line_num, cell_id, naive_tool_use_id
            )
            # The owning cell can itself be an invisible exec wrapper around
            # write_stdin. Collapse that intermediate hop to the original
            # exec_command so the wait's final chunk reaches the visible card.
            owner_payload = self._lookup_tool_call_payload(
                session_id, item.line_num, owner_call_id
            )
            exec_command_id = _write_stdin_exec_command_id_from_payload(owner_payload)
            if exec_command_id is None:
                return owner_call_id
            return self._lookup_exec_command_call_id(
                session_id, item.line_num, exec_command_id, owner_call_id
            )
        if parent_name != "write_stdin":
            return naive_tool_use_id
        exec_command_id = _write_stdin_exec_command_id_from_payload(parent_payload)
        if exec_command_id is None:
            return naive_tool_use_id
        return self._lookup_exec_command_call_id(
            session_id, item.line_num, exec_command_id, naive_tool_use_id
        )

    def _lookup_tool_call_payload(
        self, session_id: str, max_line_num: int, naive_tool_use_id: str
    ) -> dict | None:
        """Find the tool-call payload owning ``naive_tool_use_id``.

        Direct ``function_call`` and code-mode ``custom_tool_call`` shapes
        qualify; text merely containing the id is rejected.
        """
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
            content__contains=naive_tool_use_id,
        ).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_RESPONSE_ITEM:
                continue
            payload = _payload(parsed)
            if payload is None:
                continue
            if payload.get("type") not in _TOOL_CALL_PAYLOAD_TYPES:
                continue
            if payload.get("call_id") != naive_tool_use_id:
                continue
            return payload
        return None

    def _lookup_spawn_call_id_for_agent_path(
        self,
        session_id: str,
        max_line_num: int,
        agent_path: str,
    ) -> str:
        """Resolve a multi-agent v2 agent path to its ``spawn_agent`` call_id.

        Live counterpart of the batch ``_agent_id_to_spawn_call_id``
        side-table: walks back to the canonical ``SubAgentActivity`` item
        line that announced the spawn (newest first — an agent path can
        be reused by a later spawn once the previous holder is gone) and
        returns the ``event_id`` it carries. Returns ``agent_path``
        unchanged when nothing matches, so the caller still creates a
        link (just under the naive id) instead of dropping the result.
        """
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
            content__contains=_SUB_AGENT_ACTIVITY_ITEM_TYPE,
        ).filter(content__contains=agent_path).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            spawn = _parse_sub_agent_activity_started(parsed)
            if spawn is not None and spawn.agent_path == agent_path:
                return spawn.call_id
        return agent_path

    def _lookup_orphan_end_exec_call_id(
        self,
        session_id: str,
        max_line_num: int,
        fallback: str,
        *,
        event_type: str,
        changes: object = None,
        mcp_qualified: str | None = None,
    ) -> str:
        """Live equivalent of :meth:`_remap_orphan_end_event`.

        Walks the preceding code-mode ``exec`` custom_tool_calls (newest
        first, textual pre-filter on ``"name":"exec"``), re-extracts each
        script, and returns the first whose declared targets match the
        event — patch paths against ``changes`` for ``FileChange``,
        the exact ``mcp_qualified`` name for ``McpToolCall``; the
        newest exec declaring a call of the same family is kept as the
        recency fallback. Returns ``fallback`` when nothing qualifies,
        so the live link is still created (just under the naive id).
        """
        is_patch = event_type == "FileChange"
        change_paths = (
            [p for p in changes if isinstance(p, str)] if isinstance(changes, dict) else []
        )
        recency_fallback: str | None = None
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
            content__contains='"name":"exec"',
        ).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_RESPONSE_ITEM:
                continue
            payload = _payload(parsed)
            if payload is None or payload.get("type") != "custom_tool_call":
                continue
            if payload.get("name") != _CODE_MODE_EXEC_TOOL:
                continue
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            targets = _script_targets(payload.get("input"))
            if is_patch:
                if not targets.has_patch:
                    continue
                if targets.patch_paths and _changes_match_declared(change_paths, targets.patch_paths):
                    return call_id
            else:
                if not targets.mcp_tools:
                    continue
                if mcp_qualified is not None and mcp_qualified in targets.mcp_tools:
                    return call_id
            if recency_fallback is None:
                recency_fallback = call_id
        return recency_fallback if recency_fallback is not None else fallback

    def _lookup_code_cell_call_id(
        self,
        session_id: str,
        max_line_num: int,
        cell_id: str,
        fallback: str,
    ) -> str:
        """Resolve the ``exec`` call_id that owns code-mode cell ``cell_id``.

        Code-mode counterpart of :meth:`_lookup_exec_command_call_id`:
        searches for the ``custom_tool_call_output`` line whose status
        header announced ``Script running with cell ID <cell_id>`` —
        that line's ``call_id`` IS the owning ``exec``'s call_id. The
        textual pre-filter can over-match (``cell ID 2`` is a prefix of
        ``cell ID 23``, and a still-running ``wait`` output repeats the
        same header on a ``function_call_output``), so each candidate is
        re-verified by parsing its output and comparing the exact cell
        id. Returns ``fallback`` when nothing matches, so the live link
        is still created (just under the naive id).
        """
        marker = f"Script running with cell ID {cell_id}"
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
            content__contains=marker,
        ).order_by('line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_RESPONSE_ITEM:
                continue
            payload = _payload(parsed)
            if payload is None:
                continue
            if payload.get("type") != "custom_tool_call_output":
                continue
            output_status = parse_code_mode_output(payload.get("output"))
            if output_status is None or output_status.cell_id != cell_id:
                continue
            call_id = payload.get("call_id")
            if isinstance(call_id, str) and call_id:
                return call_id
        return fallback

    def _lookup_exec_command_call_id(
        self,
        session_id: str,
        max_line_num: int,
        exec_command_id: int,
        fallback: str,
    ) -> str:
        """Resolve the exec_command call_id that owns ``exec_command_id``.

        Searches either a direct output's ``Process running with session ID
        <id>`` marker or a code-mode output's canonical ``SESSION_ID=<id>``
        line. The latter is accepted only when its call_id belongs to a
        tier-1 ``exec`` wrapper around ``exec_command``.
        Returns ``fallback`` when nothing is found, so the live link is
        still created (just under the naive id).
        """
        direct_marker = f"Process running with session ID {exec_command_id}"
        code_mode_marker = f"SESSION_ID={exec_command_id}"
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=max_line_num,
        ).filter(
            Q(content__contains=direct_marker) | Q(content__contains=code_mode_marker)
        ).order_by('line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_RESPONSE_ITEM:
                continue
            payload = _payload(parsed)
            if payload is None:
                continue
            if payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                continue
            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                continue
            output = payload.get("output")
            if isinstance(output, str) and direct_marker in output:
                return call_id
            if _code_mode_exec_command_id_from_output(output) != exec_command_id:
                continue
            owner_payload = self._lookup_tool_call_payload(
                session_id, candidate.line_num, call_id
            )
            owner_call_id = call_id
            if owner_payload is not None and _tool_use_name(owner_payload) == _CODE_MODE_WAIT_TOOL:
                cell_id = _wait_cell_id_from_payload(owner_payload)
                if cell_id is not None:
                    owner_call_id = self._lookup_code_cell_call_id(
                        session_id, candidate.line_num, cell_id, call_id
                    )
                    owner_payload = self._lookup_tool_call_payload(
                        session_id, candidate.line_num, owner_call_id
                    )
            nested_args = _resolved_code_mode_nested_args(owner_payload, "exec_command")
            if nested_args is not None and isinstance(nested_args.get("cmd"), str):
                return owner_call_id
        return fallback

    def _maintain_exec_command_map(
        self,
        session_id: str,
        call_id: str,
        payload: dict,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> str | None:
        """Update the per-session exec_command map and surface an error string.

        Called from :meth:`analyze_content` for every Codex
        ``function_call_output`` / ``custom_tool_call_output``. Looks up
        the parent tool_use in ``tool_use_map`` to identify
        direct exec_command / write_stdin lines plus their canonical code-mode
        wrappers, and updates the map for the ``exec_command`` side only:

        - On an exec_command output reporting ``Process running with
          session ID N``, register ``map[N] = call_id`` so future
          write_stdin children can be remapped to this exec_command.
        - On an exec_command output reporting ``Process exited``,
          evict any entry that points to this call_id (covers both the
          synchronous one-shot — no entry to evict — and the long-running
          parent's own final poll).
        - On a code-mode exec_command wrapper (or its wait) printing
          ``SESSION_ID=N``, register the outer exec call_id under the same map.

        write_stdin's contribution to the map is handled in
        :meth:`remap_tool_result_id` instead, so the eviction happens
        AFTER the orchestrator has read the parent_call_id from the map
        for the pairing.

        Returns the synthesised ``"Exit code N"`` error string for a
        non-zero exit (or ``None`` otherwise) so the caller can stuff it
        into ``ContentAnalysis.tool_result_error``.
        """
        parent = tool_use_map.get(call_id)
        if parent is None:
            return None

        # GPT-5.6's canonical nested exec_command wrapper prints the
        # background process id as ``SESSION_ID=N`` in the outer code-mode
        # output. Register it against the outer exec call_id so a later
        # JavaScript-wrapped write_stdin can reuse the native remap path.
        parent_call_id = call_id
        parent_payload = _payload(parent.parsed_json)
        if parent.tool_name == _CODE_MODE_WAIT_TOOL:
            cell_id = _wait_cell_id_from_payload(parent_payload)
            owner_call_id = self._cell_map(session_id).get(cell_id) if cell_id is not None else None
            owner = tool_use_map.get(owner_call_id) if owner_call_id is not None else None
            if owner is not None:
                parent_call_id = owner_call_id
                parent_payload = _payload(owner.parsed_json)
        nested_exec_args = _resolved_code_mode_nested_args(parent_payload, "exec_command")
        if nested_exec_args is not None and isinstance(nested_exec_args.get("cmd"), str):
            exec_command_id = _code_mode_exec_command_id_from_output(payload.get("output"))
            if exec_command_id is not None:
                self._proc_map(session_id)[exec_command_id] = parent_call_id
            return None

        if parent.tool_name not in _EXEC_COMMAND_TOOLS:
            return None
        output = payload.get("output", "")
        if not isinstance(output, str):
            output = ""
        if parent.tool_name == "exec_command":
            status = parse_exec_command_status(output)
            proc_map = self._proc_map(session_id)
            if status.exec_command_id is not None and not status.is_terminated:
                proc_map[status.exec_command_id] = call_id
            elif status.is_terminated:
                self._release_exec_command_for_call(session_id, call_id)
        return _exit_code_error_from_output(output)

    def _maintain_code_cell_map(
        self,
        session_id: str,
        call_id: str,
        payload: dict,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> str | None:
        """Code-mode counterpart of :meth:`_maintain_exec_command_map`.

        Called from :meth:`analyze_content` for every Codex tool result.
        Only acts when the payload owning the result is a code-mode ``exec``
        (including one effectively named ``write_stdin``) or ``wait``;
        updates the cell map for the ``exec`` side only:

        - On an exec output reporting ``Script running with cell ID
          <id>``, register ``map[<id>] = call_id`` so future ``wait``
          chunks can be remapped to this exec.
        - On an exec output reporting a final status, evict any entry
          that points to this call_id.

        ``wait``'s contribution (eviction on a final chunk) is handled
        in :meth:`_remap_wait_result_id` instead, AFTER the pairing read
        — same ordering contract as write_stdin.

        Returns the ``Script failed`` error string (or the ``Script
        error:`` body when present) so the caller can stuff it into
        ``ContentAnalysis.tool_result_error``.
        """
        parent = tool_use_map.get(call_id)
        if parent is None:
            return None
        parent_payload = _payload(parent.parsed_json)
        is_code_mode_exec = (
            parent_payload is not None
            and parent_payload.get("type") == "custom_tool_call"
            and parent_payload.get("name") == _CODE_MODE_EXEC_TOOL
        )
        if not is_code_mode_exec and parent.tool_name != _CODE_MODE_WAIT_TOOL:
            return None
        parsed = parse_code_mode_output(payload.get("output"))
        if parsed is None:
            return None
        if is_code_mode_exec:
            if parsed.status == "running" and parsed.cell_id is not None:
                # A wrapped write_stdin may itself outlive the code cell's
                # yield window. Point its later wait chunks straight at the
                # original exec_command rather than at the invisible wrapper.
                target_call_id = call_id
                exec_command_id = _write_stdin_exec_command_id_from_payload(parent_payload)
                if exec_command_id is not None:
                    target_call_id = self._proc_map(session_id).get(exec_command_id, call_id)
                self._cell_map(session_id)[parsed.cell_id] = target_call_id
            elif parsed.status != "running":
                self._release_code_cell_for_call(session_id, call_id)
        if parsed.status == "failed":
            return parsed.error_text or "Script failed"
        return None

    # ------------------------------------------------------------------
    # Extraction — content classification
    # ------------------------------------------------------------------

    def _transform_inline_provider(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        line_num: int,
        in_memory_items: list[tuple[int, datetime | None, dict]] | None = None,
    ) -> str | None:
        # ``turn_context`` → track collaboration-mode transitions. A
        # default→plan transition not announced by an injected ``/plan``
        # marker is a ``/plan <prompt>`` command's turn: arm the prefix
        # restoration for its user message (see ``_restore_plan_prefix``).
        # Never a rewrite by itself.
        tc_mode = _turn_context_collaboration_mode(parsed_json)
        if tc_mode is not None:
            self._note_turn_context_mode(session_id, tc_mode, line_num)
            return None

        # Goal lifecycle events arm/disarm visibility of the next internal
        # Goal context. The app-server emits the same user-role control prompt
        # before every continuation turn; only a prompt after an active goal
        # set/update represents a new transcript boundary.
        payload = _payload(parsed_json)
        if (
            parsed_json.get("type") == _TYPE_EVENT_MSG
            and payload is not None
            and payload.get("type") == _PAYLOAD_THREAD_GOAL_UPDATED
        ):
            goal = payload.get("goal")
            if isinstance(goal, dict):
                self._note_goal_status(session_id, line_num, goal.get("status"))

        # Terminal provider error → canonical visible API-error item. Codex
        # only emits the error on its live notification stream, so the agent
        # persists this private ``thread/inject_items`` marker before teardown.
        # Keep the native injected payload for debugging while exposing one
        # provider-neutral shape to the frontend.
        provider_error = _injected_provider_error(parsed_json)
        if provider_error is not None:
            parsed_json["twiccOriginalContent"] = parsed_json.get("payload")
            parsed_json["type"] = _TYPE_TWICC_PROVIDER_ERROR
            parsed_json["provider"] = Provider.CODEX.value
            parsed_json["isApiErrorMessage"] = True
            parsed_json["turnId"] = provider_error.turn_id
            parsed_json["error"] = {
                "type": provider_error.error_type,
                "message": provider_error.message,
            }
            parsed_json.pop("payload", None)
            return orjson.dumps(parsed_json).decode("utf-8")

        # Goal context → expose one command boundary, hide later continuations.
        # Codex injects the same user-role control prompt before every
        # autonomous turn. The first one after a goal set/update stands in for
        # the user's hardcoded command; subsequent prompts are provider control
        # traffic and must stay SYSTEM/DEBUG_ONLY. Already-rewritten duplicates
        # from an older compute pass are restored to their native response_item
        # shape here, allowing a compute-version bump to repair stored sessions.
        objective = _goal_context_objective(parsed_json)
        if objective is not None:
            show = self._consume_goal_context(session_id, line_num, objective)
            if show:
                source = _restore_private_source(parsed_json)
                if source.get("type") == _TYPE_RESPONSE_ITEM:
                    built = build_twicc_user_message(
                        source,
                        session_id=session_id,
                        line_num=line_num,
                        text=f"/goal {objective}",
                    )
                    return orjson.dumps(built).decode("utf-8")
                return None
            if parsed_json.get("type") == _TYPE_EVENT_MSG:
                original = parsed_json.get("twiccOriginalContent")
                if isinstance(original, dict):
                    parsed_json["type"] = _TYPE_RESPONSE_ITEM
                    parsed_json["payload"] = original
                    parsed_json.pop("twiccOriginalContent", None)
                    return orjson.dumps(parsed_json).decode("utf-8")
            return None

        # TwiCC-injected command (``/goal clear``, ``/compact``) → user message.
        # Injected via ``thread/inject_items`` for commands Codex writes no
        # rollout line for; relabel the injected ``response_item.message`` as the
        # canonical canonical ``UserMessage`` item so it counts + renders like the
        # command the user issued. Original kept under ``twiccOriginalContent``.
        injected_command = _injected_command_text(parsed_json)
        if injected_command is not None:
            if injected_command == "/plan":
                # A bare ``/plan``'s marker: the default→plan transition on
                # the next ``turn_context`` is not an inline-prompt command —
                # see ``_note_turn_context_mode``.
                self._plan_prefix_state(session_id).marker_seen = True
            source = _restore_private_source(parsed_json)
            built = build_twicc_user_message(
                source,
                session_id=session_id,
                line_num=line_num,
                text=injected_command,
            )
            return orjson.dumps(built).decode("utf-8")

        # ``/plan <prompt>`` display restoration: the command's turn carries
        # only ``<prompt>`` as its user message (the literal ``/plan`` never
        # reaches the model), so re-prefix the stored copy — the transcript
        # then shows what the user actually typed, and the optimistic bubble
        # converges to the same text.
        restored = self._restore_plan_prefix(parsed_json, session_id)
        if restored is not None:
            return restored

        # Plan-mode final answer → visible assistant message. The turn's
        # ``<proposed_plan>`` answer is a ``Plan`` item, not an
        # ``agentMessage``, so no canonical ``AgentMessage`` item exists for it
        # and the plan text would stay buried in a SYSTEM ``response_item``.
        # Relabel it as the canonical agent_message (original kept under
        # ``twiccOriginalContent``); the frontend then renders the
        # ``<proposed_plan>`` block as a dedicated plan panel.
        plan_text = _proposed_plan_message_text(parsed_json)
        if plan_text is not None:
            source = _restore_private_source(parsed_json)
            built = build_twicc_agent_message(
                source,
                session_id=session_id,
                line_num=line_num,
                text=plan_text,
            )
            return orjson.dumps(built).decode("utf-8")

        # The other Codex rewrite is the cross-provider screenshot tag
        # substitution: ``<twicc:insert-screenshot />`` markers placed
        # by the agent in an canonical ``AgentMessage`` item payload are
        # replaced inline with a markdown image link (images looked up
        # via :meth:`iter_tool_result_image_refs`), or with the
        # missing-screenshot placeholder when no image is available.
        # The Codex JSONL itself is already in its canonical shape — no
        # legacy XML or normalisation work to do here.
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return None
        item = completed_item(parsed_json)
        if item is None or item.get("type") != "AgentMessage":
            return None
        message = agent_message_text(parsed_json)
        if not isinstance(message, str) or not message:
            return None
        if not INSERT_SCREENSHOT_TAG_RE.search(message):
            return None
        new_message = substitute_insert_screenshot_tags(
            message,
            session_id=session_id,
            images_provider=lambda needed: self.iter_images_backward(
                session_id=session_id,
                before_line_num=line_num,
                images_needed=needed,
                in_memory_items=in_memory_items,
            ),
            artifacts_dir=get_artifacts_dir(),
        )
        if new_message == message:
            return None
        item["content"] = [{"type": "Text", "text": new_message}]
        return orjson.dumps(parsed_json).decode("utf-8")

    def _note_turn_context_mode(self, session_id: str, mode: str, line_num: int) -> None:
        """Update the ``/plan``-prefix scan state from a ``turn_context`` line.

        Arms the prefix restoration when the collaboration mode transitions
        into ``plan`` with no injected bare-``/plan`` marker since the
        previous turn — the deterministic signature of a ``/plan <prompt>``
        command (the bare form injects its marker before the next turn;
        sticky plan→plan turns are not transitions). Any ``turn_context``
        also disarms a leftover armed flag from a turn whose user message
        never landed.
        """
        state = self._plan_prefix_state(session_id)
        last_mode = state.last_mode
        marker_seen = state.marker_seen
        if last_mode is None:
            # Live path on a session this process never scanned: recover the
            # previous turn's mode (and any pending bare-``/plan`` marker)
            # from the already-persisted items. Only pay for the lookup when
            # it can matter, i.e. the incoming turn is in plan mode.
            if mode == _PLAN_COLLABORATION_MODE:
                last_mode, db_marker = self._lookup_prev_plan_context(
                    session_id, line_num,
                )
                marker_seen = marker_seen or db_marker
            else:
                last_mode = mode  # value unused: a non-plan turn never arms
        state.armed = (
            mode == _PLAN_COLLABORATION_MODE
            and last_mode != _PLAN_COLLABORATION_MODE
            and not marker_seen
        )
        state.last_mode = mode
        state.marker_seen = False

    def _restore_plan_prefix(self, parsed_json: dict, session_id: str) -> str | None:
        """Re-prefix a ``/plan <prompt>`` turn's user message, if armed.

        Fires at most once per armed transition, on the turn's first native
        canonical ``UserMessage`` item — the inline prompt, written at turn
        start right after the arming ``turn_context`` (steered messages come
        later and find the state disarmed). The rewrite tags the line with
        ``twiccPlanCommand``: a later batch re-compute of already-rewritten
        content replays the arming identically from the file, and the flag
        stops a second prefix. Internal ``<twicc-resume>`` instructions are
        skipped without consuming the armed state.
        """
        state = self._plan_prefix_states.get(session_id)
        if state is None or not state.armed:
            return None
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return None
        item = completed_item(parsed_json)
        if item is None or item.get("type") != "UserMessage":
            return None
        if _is_internal_resume_message(parsed_json):
            return None
        state.armed = False
        if parsed_json.get("twiccPlanCommand"):
            return None
        parsed_json["twiccPlanCommand"] = True
        content = item.get("content")
        if not isinstance(content, list):
            content = []
            item["content"] = content
        # Prefix the first text entry only; an attachment-only prompt gets
        # a bare ``/plan`` text entry in front of its attachments.
        for entry in content:
            if (
                isinstance(entry, dict)
                and entry.get("type") == "text"
                and isinstance(entry.get("text"), str)
            ):
                entry["text"] = f"/plan {entry['text']}"
                break
        else:
            content.insert(0, {"type": "text", "text": "/plan", "text_elements": []})
        return orjson.dumps(parsed_json).decode("utf-8")

    def _lookup_prev_plan_context(
        self, session_id: str, current_line_num: int,
    ) -> tuple[str, bool]:
        """Return (previous turn's collaboration mode, pending bare-``/plan`` marker).

        Live-path seeding for :meth:`_note_turn_context_mode`, mirroring
        :meth:`_lookup_prev_total_tokens`: walk the already-persisted items
        below ``current_line_num`` for the latest ``turn_context`` (mode
        defaults to ``"default"`` when the session has none — a first turn
        entering plan mode IS a transition), then check whether an injected
        ``/plan`` marker landed after it (a bare ``/plan`` sent to a cold
        session, whose wake turn this is). Both scans stop at the first
        hit, so a healthy session costs at most a couple of row reads.
        """
        prev_mode = "default"
        prev_tc_line = 0
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=current_line_num,
            content__contains='"type":"turn_context"',
        ).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            mode = _turn_context_collaboration_mode(parsed)
            if mode is None:
                continue
            prev_mode = mode
            prev_tc_line = candidate.line_num
            break
        marker_candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__gt=prev_tc_line,
            line_num__lt=current_line_num,
            # The relabelled marker serialises a canonical text entry
            # ``{"type":"text","text":"/plan","text_elements":[]}`` (plus
            # the raw source under ``twiccOriginalContent``). A prefixed
            # inline prompt ("/plan foo") never contains the closed
            # string; candidates are still parse-verified below.
            content__contains='"text":"/plan"',
        ).order_by('-line_num')
        for candidate in marker_candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if user_message_text(parsed) == "/plan":
                return prev_mode, True
        return prev_mode, False

    def _note_goal_status(self, session_id: str, line_num: int, status) -> None:
        """Arm the next Goal context after activation; disarm on termination."""
        if not isinstance(status, str):
            return
        state = self._goal_context_state(session_id, line_num)
        state.show_next = status == _GOAL_STATUS_ACTIVE

    def _consume_goal_context(
        self, session_id: str, line_num: int, objective: str,
    ) -> bool:
        """Return whether this internal context should represent ``/goal``.

        A new activation/update arms the boundary explicitly. The first context
        in a session and an objective change are defensive fallbacks for
        provider histories that omit the corresponding status event.
        """
        state = self._goal_context_state(session_id, line_num)
        show = (
            state.show_next
            or not state.seen_context
            or objective != state.last_objective
        )
        state.initialized = True
        state.seen_context = True
        state.last_objective = objective
        state.show_next = False
        return show

    def _lookup_prev_goal_context_state(
        self, session_id: str, current_line_num: int,
    ) -> _GoalContextState:
        """Seed live goal-context visibility from the latest persisted lines.

        Only two facts matter: the most recent internal context (if any), and
        whether a newer ``thread_goal_updated`` line activated another goal
        boundary. Both scans parse-verify their cheap text-filter candidates.
        """
        state = _GoalContextState(initialized=True)
        context_line = 0
        context_candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=current_line_num,
            content__contains="codex_internal_context",
        ).order_by("-line_num")
        for candidate in context_candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            objective = _goal_context_objective(parsed)
            if objective is None:
                continue
            state.seen_context = True
            state.last_objective = objective
            context_line = candidate.line_num
            break

        update_candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__gt=context_line,
            line_num__lt=current_line_num,
            content__contains='"type":"thread_goal_updated"',
        ).order_by("-line_num")
        for candidate in update_candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_EVENT_MSG:
                continue
            payload = _payload(parsed)
            if payload is None or payload.get("type") != _PAYLOAD_THREAD_GOAL_UPDATED:
                continue
            goal = payload.get("goal")
            if not isinstance(goal, dict):
                continue
            state.show_next = goal.get("status") == _GOAL_STATUS_ACTIVE
            break
        return state

    def extract_tasks_payload(self, parsed_json: dict) -> dict | None:
        """Latest plan state on a Codex ``update_plan`` line, in the common shape.

        Before GPT-5.6, ``update_plan`` is a native ``function_call`` whose
        ``arguments`` is a JSON-encoded string. In GPT-5.6 code mode it may be
        one statically resolved nested call inside an ``exec`` JavaScript that
        also invokes unrelated tools. Both carry ``{plan: [{step, status},
        ...], explanation?}`` as a full replacement. Returns ``None`` for any
        other line, an ambiguous wrapper, or a malformed / empty plan.
        """
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return None
        payload = _payload(parsed_json)
        if payload is None:
            return None
        args = _update_plan_args_from_payload(payload)
        if args is None:
            return None
        items = _plan_to_todos(args.get("plan"))
        if items is None:
            return None
        explanation = args.get("explanation")
        return {
            "source": _UPDATE_PLAN_FUNCTION_NAME,
            "items": items,
            "explanation": explanation if isinstance(explanation, str) else None,
        }

    def is_goal_continuation_stopped(self, parsed_json: dict) -> bool:
        """True when persisted evidence says the Goal left ``active``.

        The primary signal is ``event_msg.thread_goal_updated``. GPT-5.6 code
        mode may omit that event after ``update_goal`` while still persisting
        the successful tool result, so its structured Goal snapshot is an
        equivalent stop signal. The live watcher relays either one while a
        continuation is parked so the agent returns to USER_TURN.

        A ``thread_goal_cleared`` event is intentionally NOT a stop signal: a
        ``/goal`` replace clears the old goal mid-set before installing the new
        one, which must not look like the continuation ending.
        """
        goal = _goal_snapshot_from_tool_result(parsed_json)
        if goal is not None:
            return goal.get("status") != _GOAL_STATUS_ACTIVE
        if parsed_json.get("type") == _TYPE_EVENT_MSG:
            payload = _payload(parsed_json)
            if payload is not None and payload.get("type") == _PAYLOAD_THREAD_GOAL_UPDATED:
                goal = payload.get("goal")
                if isinstance(goal, dict):
                    return goal.get("status") != _GOAL_STATUS_ACTIVE
        return False

    def extract_goal_event(self, parsed_json: dict) -> GoalEvent | None:
        """Goal lifecycle from status events, Goal results, or ``/goal clear``.

        ``thread_goal_updated`` and successful Goal-tool results carry the same
        objective/status snapshot. The objective acts as a (re)definition only
        while active: that stops a repeated completion from forking a new goal
        and lets an in-place objective edit land as an addendum. ``/goal clear``
        writes no status event, so TwiCC's injected user message is used.
        """
        goal = _goal_snapshot_from_tool_result(parsed_json)
        if goal is None:
            payload = _payload(parsed_json)
            if payload is None:
                return None
            ptype = payload.get("type")
            if ptype == _PAYLOAD_THREAD_GOAL_UPDATED:
                candidate = payload.get("goal")
                goal = candidate if isinstance(candidate, dict) else None
            else:
                message = user_message_text(parsed_json)
                if message is not None and message.strip().lower() == "/goal clear":
                    return GoalEvent(cleared=True)
                return None
        if goal is not None:
            status = goal.get("status")
            objective = goal.get("objective")
            active = status == _GOAL_STATUS_ACTIVE
            return GoalEvent(
                objective=objective if (active and isinstance(objective, str) and objective) else None,
                state=GOAL_STATE_COMPLETED if status == _GOAL_STATUS_COMPLETE else GOAL_STATE_ACTIVE,
                raw_state=status if isinstance(status, str) else None,
            )
        return None

    def compute_item_kind(self, parsed_json: dict) -> ItemKind | None:
        # NOTE: any change to this classification MUST bump
        # CODEX_COMPUTE_VERSION so existing sessions are recomputed.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)

        if wrapper_type == _TYPE_TWICC_PROVIDER_ERROR:
            return ItemKind.API_ERROR

        # ``compacted`` is the top-level wrapper Codex CLI writes when
        # auto-compacting the rolling context. We pick this one (rather
        # than the redundant ``event_msg.context_compacted`` event)
        # because the payload carries a future-proof ``encrypted_content``
        # — if Codex ever ships a readable summary in there, the item is
        # already at the right kind/display level to surface it. Today
        # the content is opaque so the frontend renders a placeholder.
        if wrapper_type == _TYPE_COMPACTED:
            return ItemKind.COMPACT_SUMMARY

        if wrapper_type == _TYPE_EVENT_MSG and payload is not None:
            item = completed_item(parsed_json)
            item_type = item.get("type") if item is not None else None
            if item_type == "UserMessage" and user_message_is_visible(parsed_json):
                if _is_internal_resume_message(parsed_json):
                    return ItemKind.SYSTEM
                return ItemKind.USER_MESSAGE
            if item_type == "AgentMessage":
                # Even an empty one: the frontend renders the "empty
                # response" notice for it, as it did for the legacy event.
                return ItemKind.ASSISTANT_MESSAGE
            # A canonical image-generation item (native ``ImageGeneration``
            # or the ``image_gen.generation`` Extension the migration
            # emits) is a standalone visible row, not a tool_result. It
            # carries the base64 PNG, the revised prompt and the on-disk
            # path — everything the frontend needs to render the image
            # inline. The matching ``response_item.image_generation_call``
            # duplicates the same data (minus saved_path) and falls
            # through to SYSTEM below.
            if image_generation(parsed_json) is not None:
                return ItemKind.IMAGE
            # Canonical ``FileChange`` / ``McpToolCall`` items are
            # tool_results. Kind stays ``None`` so the base falls into the
            # ``is_tool_result_item`` branch (-> DEBUG_ONLY). Every other
            # completed item (Reasoning, CommandExecution, Plan, WebSearch,
            # …) duplicates a raw ``response_item`` TwiCC already reads
            # and stays SYSTEM.
            if _event_msg_call_id(parsed_json) is not None:
                return None

        if wrapper_type == _TYPE_RESPONSE_ITEM and payload is not None:
            sub_type = payload.get("type")
            # The task handed down to a subagent (multi-agent v2). Codex
            # models it as an inter-agent message rather than a user
            # message, but in the receiving thread it plays exactly the
            # role a human's prompt plays in a top-level session — it IS
            # what the agent was asked to do — so it renders as one (and
            # counts as one: ``user_message_count``, the session title).
            # Every other envelope stays SYSTEM: the mid-flight
            # ``MESSAGE`` exchanges are already visible through the
            # ``send_message`` tool cards on both sides.
            if sub_type == _PAYLOAD_AGENT_MESSAGE and _parse_agent_new_task(parsed_json):
                return ItemKind.USER_MESSAGE
            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                # A tier-1 code-mode wrapper around write_stdin is the same
                # polling operation as the direct function_call: no separate
                # card, and its results are rebound to the exec_command parent.
                if _write_stdin_exec_command_id_from_payload(payload) is not None:
                    return ItemKind.SYSTEM
                # ``write_stdin`` doesn't get its own tool card —
                # its result chunks are rebound to the parent
                # ``exec_command``'s ``ToolResultLink`` chain by
                # :meth:`remap_tool_result_id`.
                if (
                    sub_type == "function_call"
                    and payload.get("name") in _NON_TOOL_FUNCTION_NAMES
                ):
                    return ItemKind.SYSTEM
                # Real tool calls with nothing worth a card (``get_goal``):
                # bucket as SYSTEM so they land at DEBUG_ONLY. Their
                # ``function_call_output`` is already DEBUG_ONLY via
                # ``is_tool_result_item``.
                if (
                    sub_type == "function_call"
                    and payload.get("name") in _DEBUG_ONLY_FUNCTION_NAMES
                ):
                    return ItemKind.SYSTEM
                return ItemKind.TOOL_USE
            # Tool-result-bearing response_item lines: kind stays None
            # so the base routes via ``is_tool_result_item`` to
            # DEBUG_ONLY without also tagging them as plain SYSTEM.
            if sub_type in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            # Reasoning lines are rendered only when the model produced an
            # actual summary block — the encrypted_content is opaque to us
            # so a reasoning whose ``summary`` is empty has nothing visible
            # to show. We bucket the empty case back to SYSTEM (-> DEBUG_ONLY)
            # via the fall-through below; the non-empty case becomes its own
            # COLLAPSIBLE kind so it joins tool_use et al. in the group
            # machinery and gets a dedicated frontend renderer.
            if sub_type == "reasoning" and _has_summary_text(payload):
                return ItemKind.REASONING

        # Everything else (session_meta, turn_context, other response_item
        # subtypes — message/reasoning-without-summary/…, other event_msg
        # subtypes without call_id including ``event_msg.context_compacted``,
        # malformed lines) is bucketed as SYSTEM and ends up at
        # DEBUG_ONLY display level.
        return ItemKind.SYSTEM

    # compute_item_display_level + compute_item_metadata: inherited from base.
    # USER_MESSAGE/ASSISTANT_MESSAGE → ALWAYS, SYSTEM → DEBUG_ONLY,
    # TOOL_USE → COLLAPSIBLE (default fall-through), tool-result lines
    # whose kind is None → DEBUG_ONLY via :meth:`is_tool_result_item`.

    def extract_item_timestamp(self, parsed_json: dict) -> datetime | None:
        # Every Codex JSONL line carries a top-level ISO 8601 ``timestamp``.
        timestamp = parsed_json.get("timestamp")
        if isinstance(timestamp, str):
            return parse_timestamp_to_datetime(timestamp)
        return None

    # extract_title_from_user_message: inherited from base
    # (calls extract_user_message_text, then strip_markdown + truncate).

    def extract_user_message_text(self, parsed_json: dict) -> str | None:
        # Title extraction reads the first user_message's plain text.
        # event_msg:user_message stores the human input as a flat
        # string, optionally with images alongside (irrelevant for the
        # title).
        #
        # A subagent's opening prompt is a ``NEW_TASK`` inter-agent
        # message instead (see :meth:`compute_item_kind`). Its body is
        # encrypted, so the readable part — the task name the parent
        # chose — stands in: it is the only description of the job that
        # ever reaches us, and it makes a far better session title than
        # nothing at all.
        task = _parse_agent_new_task(parsed_json)
        if task is not None:
            if task.payload:
                return task.payload
            return _humanize_identifier(task.task_name) if task.task_name else None
        return user_message_text(parsed_json)

    # ------------------------------------------------------------------
    # Extraction — out-of-scope hooks (V1 stubs)
    # ------------------------------------------------------------------
    #
    # These hooks all return empty / no-op values so the inherited
    # machinery (group state, batch orchestration, watcher live sync)
    # still runs without errors. Each one will get a real implementation
    # when the matching Codex feature lands (tools, costs, runtime env, ...).

    def extract_runtime_fields(self, parsed_json: dict) -> dict:
        # ``slug`` is only set for subagent rollouts: Codex tags them
        # with an ``agent_nickname`` (e.g. ``"Chandrasekhar"``) inside
        # ``session_meta.payload.source.subagent.thread_spawn`` — top-level
        # sessions have no equivalent, so ``slug`` stays ``None`` for them.
        # Three line shapes contribute to runtime fields:
        #
        # - ``session_meta`` (opening line, one per session) carries the
        #   initial ``payload.cwd`` and ``payload.git.branch``. The latter
        #   is captured as a stable historical fallback for
        #   ``cwd_git_branch`` — filesystem-based resolution can drift
        #   (worktree gone, branch renamed since) (cf. the matching
        #   ``Session.cwd_git_branch`` comment). Subagent rollouts also
        #   contain a SECOND ``session_meta`` line right after the first
        #   one (a clone of the parent's metadata, used by the SDK to
        #   replay context); that clone has no ``source.subagent`` field
        #   so it returns ``slug=None`` here and the "last non-null
        #   wins" rule preserves the nickname captured from the first
        #   line. Same applies to ``cwd`` / ``cwd_git_branch``: parent
        #   and subagent share them.
        # - ``turn_context`` (emitted on every turn) carries
        #   ``payload.cwd`` and ``payload.model``. The base orchestrator's
        #   "last non-null wins" rule means a mid-session ``cd`` updates
        #   ``Session.cwd`` and a model swap updates ``Session.model``.
        #   ``turn_context`` does NOT carry git info — ``cwd_git_branch``
        #   keeps its initial value from ``session_meta``; the resolved
        #   ``Session.git_directory`` / ``Session.git_branch`` get
        #   re-derived from the new ``cwd`` downstream by the base.
        # - ``event_msg.task_started`` (emitted alongside every new turn)
        #   carries ``payload.model_context_window`` — Codex's published
        #   compaction threshold, equal to 95% of the model's nominal
        #   input window. We divide it back by
        #   :data:`_TASK_STARTED_WINDOW_HEADROOM_FACTOR` and snap to the
        #   nearest 1000 to recover the nominal window — per-model, see
        #   ``CodexModelExtra.context_window`` (272K pre-5.6, 372K for
        #   the GPT-5.6 tiers) — then surface it as ``context_max`` so
        #   the base loop can write it onto ``Session.context_max``.
        #   This gives us a real window value for sessions imported
        #   from JSONL (and a tracking value if the user switches to a
        #   model with a different window mid-session).
        cwd: str | None = None
        cwd_git_branch: str | None = None
        model: str | None = None
        slug: str | None = None
        context_max: int | None = None
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is not None:
            if wrapper_type == _TYPE_SESSION_META:
                value = payload.get("cwd")
                if isinstance(value, str) and value:
                    cwd = value
                git_info = payload.get("git")
                if isinstance(git_info, dict):
                    branch = git_info.get("branch")
                    if isinstance(branch, str) and branch:
                        cwd_git_branch = branch
                source = payload.get("source")
                if isinstance(source, dict):
                    subagent = source.get("subagent")
                    if isinstance(subagent, dict):
                        thread_spawn = subagent.get("thread_spawn")
                        if isinstance(thread_spawn, dict):
                            candidate = thread_spawn.get("agent_nickname")
                            if isinstance(candidate, str) and candidate:
                                slug = candidate
            elif wrapper_type == _TYPE_TURN_CONTEXT:
                value = payload.get("cwd")
                if isinstance(value, str) and value:
                    cwd = value
                value = payload.get("model")
                if isinstance(value, str) and value:
                    model = value
            elif (
                wrapper_type == _TYPE_EVENT_MSG
                and payload.get("type") == _PAYLOAD_TASK_STARTED
            ):
                window = payload.get("model_context_window")
                if isinstance(window, int) and window > 0:
                    # Recover the nominal window from Codex's published
                    # 95%-of-nominal value, then snap to the nearest
                    # 1000 so we get the round numbers a user expects
                    # in the UI (272_000 instead of 271_999, etc.) and
                    # tolerate small drift if Codex changes its rounding.
                    nominal = window / _TASK_STARTED_WINDOW_HEADROOM_FACTOR
                    context_max = round(nominal / 1000) * 1000
        return {
            "cwd": cwd,
            "cwd_git_branch": cwd_git_branch,
            "model": model,
            "slug": slug,
            "context_max": context_max,
        }

    def compute_item_cost_and_usage(
        self,
        item: SessionItem,
        parsed_json: dict,
        seen_message_ids: set[str],  # noqa: ARG002 (Codex dedups via total_tokens, not message_id)
        current_model: str | None,
    ) -> None:
        """Assign ``cost`` and ``context_usage`` for Codex billing items.

        Only ``event_msg.token_count`` lines carry usage data — every
        other JSONL shape returns immediately. For matching lines the
        algorithm is:

        1. Skip lines whose ``info`` is null (the bootstrap snapshot
           emitted before the first LLM call) or malformed.
        2. Read ``info.total_token_usage.total_tokens`` and compare to
           the previous value tracked in ``self._prev_total_tokens``.
           When the cumulative total hasn't moved, this token_count is
           non-billable:

           - inter-turn re-emission (Codex republishes the previous
             totals at the start of a new turn so its UI has the latest
             snapshot before any new call lands);
           - compaction-zero (a ``last_token_usage`` of ``0/0/0``
             emitted alongside the ``compacted`` event).

           Both are filtered by the same equality check.
        3. Otherwise, advance the running total, convert
           ``last_token_usage`` to the cross-provider :class:`TokenUsage`
           via :func:`to_token_usage`, and assign ``context_usage`` plus
           (when a current model and a timestamp are known) ``cost``.

        The cumulative ``total_token_usage`` itself is **never** read
        for billing — every call to :func:`calculate_line_cost` works
        off the per-event ``last_token_usage``. The total only acts as
        a monotonic clock for the dedup test above.

        Live mode never calls :meth:`begin_session_compute`, so the
        first time a session shows up here we lazy-seed
        ``self._prev_total_tokens[session_id]`` from the DB via
        :meth:`_lookup_prev_total_tokens` to avoid double-counting an
        inter-turn re-emission that happens to be the first line of the
        live batch.
        """
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != _PAYLOAD_TOKEN_COUNT:
            return
        info = payload.get("info")
        if not isinstance(info, dict):
            return  # bootstrap snapshot (info: null), no billable activity
        total_usage = info.get("total_token_usage")
        if not isinstance(total_usage, dict):
            return
        cur_total = total_usage.get("total_tokens", 0) or 0

        session_id = item.session_id
        if session_id not in self._prev_total_tokens:
            # Live mode: seed the running total from the most recent
            # already-processed token_count in the DB so dedup works on
            # the first batch line.
            self._prev_total_tokens[session_id] = self._lookup_prev_total_tokens(
                session_id, item.line_num,
            )

        if cur_total == self._prev_total_tokens[session_id]:
            return  # no new billable activity (re-emission / compaction-zero)
        self._prev_total_tokens[session_id] = cur_total

        last_usage = info.get("last_token_usage")
        if not isinstance(last_usage, dict):
            return

        token_usage = to_token_usage(last_usage)
        item.context_usage = calculate_line_context_usage(token_usage)

        if not current_model or item.timestamp is None:
            return  # cost requires both an active model and a date
        if extract_model_info(current_model) is None:
            return  # unrecognised model name — no fallback bucket
        from twicc.providers.helpers import get_provider_helpers
        helpers = get_provider_helpers(Provider.CODEX)
        model_id = f"{helpers.OPENROUTER_MODEL_PREFIX}{current_model}"
        item.cost = helpers.calculate_line_cost(
            token_usage, model_id, item.timestamp.date(),
        )

    def _lookup_prev_total_tokens(
        self, session_id: str, current_line_num: int,
    ) -> int:
        """Return the latest already-processed ``total_tokens`` for the session.

        Walks ``SessionItem`` rows of ``session_id`` whose ``line_num``
        is below ``current_line_num``, in reverse, and returns the
        ``info.total_token_usage.total_tokens`` of the first parseable
        ``event_msg.token_count`` found. Returns ``0`` when the session
        has no prior token_count (genuinely first event of a fresh
        session) — then any subsequent token_count with a non-zero
        total advances the dedup cursor as expected.

        Used only by the live path; batch mode resets to ``0`` via
        :meth:`begin_session_compute`. The scan stops at the first hit,
        so it costs at most one row read on a healthy session.
        """
        candidates = SessionItem.objects.filter(
            session_id=session_id,
            line_num__lt=current_line_num,
            content__contains='"type":"token_count"',
        ).order_by('-line_num')
        for candidate in candidates.iterator(chunk_size=10):
            try:
                parsed = orjson.loads(candidate.content)
            except orjson.JSONDecodeError:
                continue
            if parsed.get("type") != _TYPE_EVENT_MSG:
                continue
            payload = _payload(parsed)
            if payload is None or payload.get("type") != _PAYLOAD_TOKEN_COUNT:
                continue
            info = payload.get("info")
            if not isinstance(info, dict):
                continue
            total_usage = info.get("total_token_usage")
            if not isinstance(total_usage, dict):
                continue
            return total_usage.get("total_tokens", 0) or 0
        return 0

    def is_tool_result_item(self, parsed_json: dict) -> bool:
        # Three line shapes carry a tool_result for Codex:
        # - ``response_item`` with a ``*_call_output`` payload (the LLM-facing
        #   string returned from the function call). For exec_command
        #   shells this is the chunked transcript; for write_stdin it's
        #   one chunk of the parent exec_command's transcript (rebound
        #   via :meth:`remap_tool_result_id`).
        # - ``event_msg.item_completed`` carrying a canonical ``FileChange``
        #   or ``McpToolCall`` item. They carry the structured outcome of
        #   the tool call and are paired with the originating function_call
        #   by the item ``id`` (the call_id). ``WebSearch`` and the
        #   image-generation items are intentionally not results (see the
        #   module docstring).
        # - ``response_item.message role=user`` whose content opens with
        #   ``<subagent_notification>``: this synthetic user message is
        #   injected by Codex when a spawned subagent reaches a final
        #   status, and we treat it as the second tool_result of the
        #   originating ``spawn_agent`` (rebind via :meth:`remap_tool_result_id`
        #   / :meth:`remap_tool_result_id_live`). It's the canonical
        #   "spawn_agent terminated" signal — ``wait_agent`` outputs
        #   are intentionally NOT rebound (redundant, see the
        #   ``_SUBAGENT_NOTIFICATION_*`` constants docstring).
        # - ``response_item.agent_message`` opening with
        #   ``Message Type: FINAL_ANSWER``: the multi-agent **v2**
        #   replacement for ``<subagent_notification>`` — the subagent's
        #   answer handed back to its parent, rebound to the originating
        #   ``spawn_agent`` the same way (naive id = the sender's agent
        #   path).
        # - canonical ``SubAgentActivity`` item with ``kind == "started"``:
        #   the v2 spawn event. It carries no tool *result* (the ack
        #   ``function_call_output`` already pairs with the call by
        #   ``call_id``, and :meth:`extract_tool_result_info` returns
        #   ``None`` here), but the live path only offers
        #   :meth:`create_agent_link_from_tool_result` the lines it
        #   considers tool_result-ish — this is the gate that lets the
        #   v2 ``(call_id, agent_id)`` pair through to the AgentLink.
        # All of them are routed to DEBUG_ONLY; the front uses the tool's
        # ``isToolRunning`` hook to know when the chain is complete.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return False
        if wrapper_type == _TYPE_RESPONSE_ITEM:
            if payload.get("type") in _TOOL_RESULT_PAYLOAD_TYPES:
                return True
            return (
                _subagent_notification_text(parsed_json) is not None
                or _parse_agent_final_answer(parsed_json) is not None
            )
        if wrapper_type == _TYPE_EVENT_MSG:
            if _parse_sub_agent_activity_started(parsed_json) is not None:
                return True
            return canonical_result_item(parsed_json) is not None
        return False

    def extract_tool_use_entries(
        self,
        parsed_json: dict,
        *,
        session_id: str,  # noqa: ARG002 (kept for signature compatibility; future remap may use it)
    ) -> dict[str, str]:
        # One tool_use per JSONL line in Codex (no nesting like Claude),
        # so the returned mapping has at most one entry. Keyed by the
        # OpenAI ``call_id`` — that's what the matching output also carries.
        # Direct and single-resolved code-mode ``write_stdin`` calls are
        # included here even though their
        # :meth:`compute_item_kind` returns ``SYSTEM`` (no tool card):
        # we still need its call_id in ``tool_use_map`` so
        # :meth:`remap_tool_result_id` can recognise its
        # ``function_call_output`` and rebind it to the parent
        # ``exec_command``'s call_id. Names in
        # :data:`_IGNORED_FUNCTION_NAMES` (``wait_agent``) are excluded
        # entirely — no pairing, no ToolResultLink, no tool_state
        # broadcast for their output (which falls through to SYSTEM).
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return _EMPTY_TOOL_USE_ENTRIES
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_TOOL_USE_ENTRIES
        sub_type = payload.get("type")
        if sub_type not in _TOOL_CALL_PAYLOAD_TYPES:
            return _EMPTY_TOOL_USE_ENTRIES
        if sub_type == "function_call" and payload.get("name") in _IGNORED_FUNCTION_NAMES:
            return _EMPTY_TOOL_USE_ENTRIES
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return _EMPTY_TOOL_USE_ENTRIES
        return {call_id: _tool_use_name(payload)}

    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict | None = None,  # noqa: ARG002
    ) -> ToolResultInfo | None:
        # ``<subagent_notification>`` user messages are surfaced first
        # (cheap shape check). They carry the canonical end-of-spawn_agent
        # signal, with the agent_path as the naive tool_use_id — the
        # remap hook resolves it to the originating ``spawn_agent``
        # call_id. Error text is derived from the JSON ``status``
        # variant: ``errored`` / ``shutdown`` / ``not_found`` flip the
        # ``ToolResultLink.error`` field; ``completed`` keeps it ``None``.
        notif = _parse_subagent_notification(parsed_json)
        if notif is not None:
            agent_path, status = notif
            error_text = _status_error_text(status)
            return ToolResultInfo(
                tool_use_id=agent_path,
                is_error=error_text is not None,
                error_text=error_text,
            )

        # ``FINAL_ANSWER`` agent messages (multi-agent v2) play the same
        # role for the same reason, with the sender's agent path as the
        # naive tool_use_id (same remap side-table as the v1
        # notification, keyed by path instead of thread id). No error
        # variant exists on this envelope: v2 reports nothing about
        # *how* the subagent ended, so the link never flips to error
        # here — a failed spawn is still caught upstream by
        # :meth:`compute_link_error_override` on the ack itself.
        final_answer = _parse_agent_final_answer(parsed_json)
        if final_answer is not None:
            return ToolResultInfo(
                tool_use_id=final_answer[0],
                is_error=False,
                error_text=None,
            )

        # Mirror of ``extract_tool_use_entries`` for the matching result
        # line. Two shapes contribute:
        # - response_item.{function_call_output, custom_tool_call_output}
        #   — the LLM-facing output string. Three error-detection paths
        #   coexist here, all guarded by their own shape so they're
        #   mutually exclusive in practice:
        #     * ``local_shell_call`` / ``shell`` outputs are a JSON
        #       string carrying ``{"output":..., "metadata":{"exit_code":N,
        #       ...}}`` (cf. ``format_exec_output_for_model_structured``
        #       in ``codex-rs/core/src/tools/mod.rs``) —
        #       :func:`_structured_exec_output_error` decodes it and
        #       surfaces ``"Exit code N"`` for a non-zero exit.
        #     * ``shell_command`` outputs carry a freeform text trailer
        #       starting with ``Exit code: N`` (cf.
        #       ``format_exec_output_for_model_freeform``) —
        #       :func:`_freeform_exec_output_error` handles it.
        #     * ``exec_command`` / ``write_stdin`` outputs carry a Codex
        #       formatted trailer with a ``Process exited with code N``
        #       line — :func:`_exit_code_error_from_output` handles it.
        #     * everything else has no exit signal here, so all three
        #       helpers return ``None`` and ``error_text`` stays ``None``.
        # - ``event_msg.item_completed`` carrying a canonical ``FileChange``
        #   or ``McpToolCall`` item. Both shapes coexist as separate
        #   ``ToolResultLink`` rows for the same call_id (no dedup);
        #   the front knows whether to wait for both via
        #   ``getExpectedResultCount``.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return None
        if wrapper_type == _TYPE_RESPONSE_ITEM:
            if payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            call_id = payload.get("call_id")
            output = payload.get("output", "")
            if isinstance(output, str):
                error_text = (
                    _structured_exec_output_error(output)
                    or _freeform_exec_output_error(output)
                    or _exit_code_error_from_output(output)
                    or _code_mode_output_error(output)
                )
            else:
                # Code-mode ``exec`` / ``wait`` outputs may be an array
                # of ``{type: "input_text", text}`` segments — the only
                # non-string output shape carrying an error signal
                # (the ``Script failed`` status header).
                error_text = _code_mode_output_error(output)
        elif wrapper_type == _TYPE_EVENT_MSG:
            call_id = _event_msg_call_id(parsed_json)
            item = canonical_result_item(parsed_json)
            error_text = _event_msg_payload_error(item or {})
        else:
            return None
        if not isinstance(call_id, str) or not call_id:
            return None

        # 4th error source: the live agent's _user_terminated_tool_ids map.
        # Codex's function_call_output line carries the rejection text in
        # ``output`` ("exec_command failed for ... Rejected(...)" /
        # "aborted by user after X.Xs") but no is_error flag. We don't
        # pattern-match the text — we consult the agent-side map populated
        # when the user ends the tool out of band: at WS-response time by
        # ``CodexAgent._record_decision_outcome`` (Deny / Cancel / refused
        # permissions) or on a turn interruption by
        # ``CodexAgent.soft_interrupt``. The recorded reason supersedes any
        # exit-code text that ``_*_error`` helpers might have produced.
        # Caveat: this map is in-memory only; a backend restart followed
        # by a background re-compute on the same JSONL has no way to
        # recover the reason (the helpers above will return ``None`` for
        # an "aborted by user" trailer that has no exit code). The live
        # path's already-persisted ``ToolResultLink.error`` is the
        # authoritative source — verify in PR4 that background re-compute
        # doesn't overwrite it.
        termination_reason = _user_terminated_tool_reason(session_id, call_id)
        if termination_reason is not None:
            logger.debug(
                "Codex compute: marking tool result as user-terminated: "
                "session=%s call_id=%s reason=%r (overriding error_text=%r)",
                session_id, call_id, termination_reason, error_text,
            )
            error_text = termination_reason

        return ToolResultInfo(
            tool_use_id=call_id,
            is_error=error_text is not None,
            error_text=error_text,
        )

    def iter_tool_result_image_refs(self, parsed_json):
        # Codex tool results carry images as ``input_image`` segments in
        # the ``output`` list of a ``function_call_output`` (direct tools:
        # pre-5.6 MCP, view_image, ...) or ``custom_tool_call_output``
        # (5.6 code-mode ``exec`` cells): ``{type: "input_image",
        # image_url: "data:image/png;base64,<data>"}``. The paired
        # ``McpToolCall`` event usually duplicates the same bytes
        # (raw base64 in ``result.Ok.content``) — intentionally NOT
        # harvested here: the aggregated output is the canonical
        # model-visible result, and walking both sources would surface
        # the same screenshot at two consecutive offsets. User messages
        # also carry ``input_image`` segments (attachments) but are not
        # tool results, so ``response_item.message`` is excluded — same
        # scope as the Claude Code override.
        # Segments are walked in REVERSE document order so that, in a
        # multi-image output (e.g. a code-mode cell taking two
        # screenshots), the chronologically later image is yielded first
        # — matching the "offset=0 = most recent" contract of the base
        # hook.
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
            return
        output = payload.get("output")
        if not isinstance(output, list):
            return
        call_id = payload.get("call_id") or ""
        for segment in reversed(output):
            if not isinstance(segment, dict) or segment.get("type") != "input_image":
                continue
            detected = _data_url_image(segment.get("image_url"))
            if detected is None:
                continue
            media_type, data = detected
            yield (call_id, media_type, data)

    def image_candidate_queryset(self, session_id, before_line_num):
        # ``'"type":"input_image"'`` narrows to lines carrying an image
        # segment (both codex-rs and our orjson rewrites serialise
        # compactly); the payload-type marker excludes user messages with
        # attached images, which would otherwise consume slots in the
        # walker's ``[:images_needed]`` slice without ever yielding a
        # hit. Cheap LIKE pre-filters; iter_tool_result_image_refs does
        # the final shape check.
        return SessionItem.objects.filter(
            Q(content__contains='"type":"function_call_output"')
            | Q(content__contains='"type":"custom_tool_call_output"'),
            session_id=session_id,
            line_num__lt=before_line_num,
            content__contains='"type":"input_image"',
        ).order_by('-line_num')

    def extract_agent_info_from_tool_result(
        self, parsed_json: dict
    ) -> tuple[str, str, bool] | None:
        """Return ``(call_id, agent_id, is_async)`` for a successful spawn.

        Two line shapes carry the pair, one per protocol generation:

        - **v1**: the ``spawn_agent`` ``function_call_output`` itself,
          a JSON string ``{"agent_id": "...", "nickname": "..."}``.
        - **v2**: the canonical ``SubAgentActivity`` item line with
          ``kind == "started"`` — the v2 ack (``{"task_name": ...}``)
          has no thread id, so the event carries the pair instead
          (``event_id`` = the spawning call_id, ``agent_thread_id`` =
          the subagent session). See
          :func:`_parse_sub_agent_activity_started`.

        Returns ``None`` for any other shape (different tool, freeform
        rejection text, non-spawn activity kinds, missing fields,
        malformed JSON…). ``is_async`` is always True: Codex's
        ``spawn_agent`` runs the subagent asynchronously in both
        generations (see :meth:`extract_task_tool_uses`).
        """
        spawn = _parse_sub_agent_activity_started(parsed_json)
        if spawn is not None:
            return spawn.call_id, spawn.agent_id, True
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return None
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != "function_call_output":
            return None
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return None
        output = payload.get("output")
        if not isinstance(output, str) or not output:
            return None
        try:
            decoded = orjson.loads(output)
        except orjson.JSONDecodeError:
            return None
        if not isinstance(decoded, dict):
            return None
        agent_id = decoded.get("agent_id")
        if not isinstance(agent_id, str) or not agent_id:
            return None
        return call_id, agent_id, True

    def extract_task_tool_uses(self, parsed_json: dict) -> list[tuple[str, bool]]:
        """Return ``[(call_id, is_background)]`` for ``spawn_agent`` calls.

        Codex's ``spawn_agent`` always runs the subagent asynchronously:
        the parent receives an immediate ack and, when the subagent
        finishes, a second signal — a ``<subagent_notification>`` user
        message on v1, a ``FINAL_ANSWER`` agent message on v2. Both are
        rebound as the spawn's second ``ToolResultLink``, so we always
        model the call as ``is_background=True`` (two expected results).

        Matches on the raw ``payload.name``, which stayed ``spawn_agent``
        in both generations — the ``collaboration`` namespace v2 adds
        only shows up in the *qualified* name (see
        :data:`_SPAWN_AGENT_TOOL_NAMES`).
        """
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return _EMPTY_TASK_TOOL_USES
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != "function_call":
            return _EMPTY_TASK_TOOL_USES
        if payload.get("name") != _SPAWN_AGENT_FUNCTION_NAME:
            return _EMPTY_TASK_TOOL_USES
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return _EMPTY_TASK_TOOL_USES
        return [(call_id, True)]

    def extract_task_tool_use_prompts(
        self, parsed_json: dict
    ) -> list[tuple[str, str, bool]]:
        """Return ``[(call_id, prompt, is_background)]`` for ``spawn_agent`` calls.

        The prompt is the ``message`` field inside ``arguments`` (itself a
        JSON string). Codex doesn't actually need the prompt-matching
        path — the call_id ↔ agent_id link is direct via the
        ``function_call_output`` — but the hook is wired generically by
        the base ``create_agent_link_from_subagent`` /
        ``create_agent_link_from_tool_use`` flows.
        """
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return []
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != "function_call":
            return []
        if payload.get("name") != _SPAWN_AGENT_FUNCTION_NAME:
            return []
        call_id = payload.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            return []
        arguments = payload.get("arguments")
        if not isinstance(arguments, str) or not arguments:
            return []
        try:
            decoded = orjson.loads(arguments)
        except orjson.JSONDecodeError:
            return []
        if not isinstance(decoded, dict):
            return []
        message = decoded.get("message")
        if not isinstance(message, str) or not message:
            return []
        return [(call_id, message, True)]

    def compute_link_error_override(
        self,
        parsed_json: dict,
        tool_name: str,
        *,
        session_id: str | None = None,  # noqa: ARG002
    ) -> str | None:
        """For ``spawn_agent``: surface the SDK's rejection text as the link error.

        On success the ``output`` parses as ``{"agent_id": ..., ...}``
        (v1) or ``{"task_name": "/root/<task>"}`` (v2) and we return
        ``None`` (no override — the spawn worked). On failure the SDK
        returns a freeform string (e.g. fork-context constraint
        violations); we expose it verbatim as the error so the
        ``ToolResultLink`` flips to error state with a meaningful message.

        ``tool_name`` arrives fully qualified, so it matches against
        :data:`_SPAWN_AGENT_TOOL_NAMES` (v2 prefixes the ``collaboration``
        namespace) rather than the bare function name.
        """
        if tool_name not in _SPAWN_AGENT_TOOL_NAMES:
            return None
        if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
            return None
        payload = _payload(parsed_json)
        if payload is None or payload.get("type") != "function_call_output":
            return None
        output = payload.get("output")
        if not isinstance(output, str) or not output:
            return None
        try:
            decoded = orjson.loads(output)
        except orjson.JSONDecodeError:
            return output
        if isinstance(decoded, dict) and (
            isinstance(decoded.get("agent_id"), str)
            or isinstance(decoded.get("task_name"), str)
        ):
            return None
        return output

    def agent_tool_candidates_query(self, parent_session_id: str):
        # Pre-filter on the textual marker of a ``spawn_agent`` function_call to
        # avoid scanning every item of the parent session.
        return SessionItem.objects.filter(
            session_id=parent_session_id,
            content__contains='"name":"spawn_agent"',
        ).order_by('-line_num')

    def extract_paths_from_tool_uses(self, parsed_json: dict) -> list[str]:
        # Codex only exposes absolute file paths through
        # ``event_msg.patch_apply_end.changes`` (a ``{abs_path: change_entry}``
        # map). The matching ``custom_tool_call name=apply_patch`` ships
        # its patch as raw Lark grammar with paths that may be relative,
        # and ``exec_command`` / ``write_stdin`` carry arbitrary shell
        # text — neither is a reliable source for git resolution. So
        # only ``FileChange`` rows contribute paths here, and any
        # session that doesn't apply a patch falls back on the cwd-based
        # git resolution in the orchestrator (see ``compute_base``).
        payload = completed_item(parsed_json)
        if payload is None or payload.get("type") != "FileChange":
            return _EMPTY_FILE_PATHS
        changes = payload.get("changes")
        if not isinstance(changes, dict):
            return _EMPTY_FILE_PATHS
        return [p for p in changes if isinstance(p, str) and p.startswith("/")]

    def extract_doc_edit_events(self, parsed_json: dict, *, cwd: str | None) -> list[DocEditEvent]:
        # Three sources of plan-doc writes/deletes:
        # 1. canonical ``FileChange`` item — the canonical apply_patch result
        #    (absolute paths + per-file add/update/delete type), regardless of
        #    how the patch was invoked (custom_tool_call, shell-wrapped, or
        #    nested in a code-mode script — the event is persisted in all
        #    three cases). Only successful applies count: the ``changes`` map
        #    is present on failed/declined patches too.
        # 2. Shell tool calls, through the shared shell-write heuristic. The
        #    input shape diverges per tool: ``exec_command`` ships its script
        #    under ``cmd``, ``shell``/``container.exec`` a ``command`` argv,
        #    ``shell_command`` a ``command`` string, and ``local_shell_call``
        #    has no ``arguments`` at all (argv in ``payload.action.command``).
        # 3. Code-mode ``exec`` scripts (custom_tool_call): every statically
        #    resolved nested ``exec_command`` call feeds its ``cmd`` through
        #    the same shell-write heuristic. Nested ``apply_patch`` calls are
        #    deliberately NOT mined here — ``FileChange`` (source 1)
        #    already covers them, exactly like the direct apply_patch
        #    custom_tool_call which has no branch here either.
        line_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return []

        events: list[DocEditEvent] = []
        if line_type == _TYPE_EVENT_MSG:
            payload = completed_item(parsed_json)
            if payload is None or payload.get("type") != "FileChange":
                return []
            if _patch_apply_error(payload) is not None:
                return []
            changes = payload.get("changes")
            if not isinstance(changes, dict):
                return []
            for path, entry in changes.items():
                if not isinstance(path, str) or not os.path.isabs(path) or not is_plan_doc_path(path):
                    continue
                change_type = entry.get("type") if isinstance(entry, dict) else None
                events.append(DocEditEvent(path, 'delete' if change_type == 'delete' else 'write'))
            return events

        if line_type != _TYPE_RESPONSE_ITEM:
            return []
        sub_type = payload.get("type")
        if (
            sub_type == "custom_tool_call"
            and payload.get("name") == _CODE_MODE_EXEC_TOOL
        ):
            script = parse_code_mode_script(payload.get("input"))
            for nested_call in script.calls:
                if nested_call.name != "exec_command" or not isinstance(nested_call.arg, dict):
                    continue
                nested_command = nested_call.arg.get("cmd")
                if not isinstance(nested_command, str) or not nested_command:
                    continue
                nested_workdir = nested_call.arg.get("workdir")
                base_dir = nested_workdir if isinstance(nested_workdir, str) and nested_workdir else cwd
                for target, target_action in extract_shell_write_targets(nested_command):
                    path = target if os.path.isabs(target) else (os.path.join(base_dir, target) if base_dir else None)
                    if path and is_plan_doc_path(path):
                        events.append(DocEditEvent(path, target_action))
            return events
        command = None
        workdir = None
        if sub_type == "function_call":
            command_key = _DOC_EDIT_SHELL_COMMAND_KEYS.get(payload.get("name"))
            if command_key is None:
                return []
            try:
                arguments = orjson.loads(payload.get("arguments") or "{}")
            except orjson.JSONDecodeError:
                return []
            if not isinstance(arguments, dict):
                return []
            command = arguments.get(command_key)
            workdir = arguments.get("workdir")
        elif sub_type == "local_shell_call":
            action = payload.get("action")
            if not isinstance(action, dict):
                return []
            command = action.get("command")
            workdir = action.get("working_directory")
        else:
            return []
        if not command:
            return []

        base_dir = workdir if isinstance(workdir, str) and workdir else cwd
        for target, target_action in extract_shell_write_targets(command):
            path = target if os.path.isabs(target) else (os.path.join(base_dir, target) if base_dir else None)
            if path and is_plan_doc_path(path):
                events.append(DocEditEvent(path, target_action))
        return events

    def compute_link_extra(
        self,
        parsed_json: dict,
        tool_name: str,
        *,
        session_id: str | None = None,
    ) -> str | None:
        """Return the JSON ``ToolResultLink.extra`` payload for this result.

        Three shapes contribute today:

        - ``exec_command`` / ``write_stdin`` ``function_call_output``
          rows whose trailer reports ``Process exited`` produce
          ``{"is_terminated": true}``. Other rows in the same chain
          (still-running polls, the synchronous one-shot's own running
          status, the parent's first chunk) return ``None`` so the
          tool_state's ``Max``-aggregated ``extra`` only flips to
          terminated once we've seen the closing chunk.
        - Code-mode ``exec`` result rows (the exec's own output plus
          rebound ``wait`` chunks) follow the same chained logic, keyed
          on the script status header instead of the unified-exec
          trailer.
        - ``apply_patch`` canonical ``FileChange`` item rows produce
          ``{"lines_added": N, "lines_removed": M, "files": [...]}``
          so the front can show the per-tool badge.

        Returns ``None`` everywhere else (most rows don't need an
        ``extra`` payload).

        Output JSON shapes (``orjson.dumps`` of the dict):

        For exec_command / write_stdin completion::

            {"is_terminated": true}

        For apply_patch::

            {
                # Aggregated totals across every entry in ``changes``.
                "lines_added":   <int>,    # always present
                "lines_removed": <int>,    # always present (0 when only adds)

                # Per-file breakdown, in the order ``changes.items()``
                # iterates (i.e. insertion order from the Codex JSONL).
                # ``path`` is the absolute path Codex applied the patch
                # to. Always present, even for a single-file call.
                "files": [
                    {
                        "path":          <str>,
                        "lines_added":   <int>,
                        "lines_removed": <int>,
                    },
                    ...
                ],
            }

        Per-entry counting rules:

        - ``update``: ``+`` / ``-`` body lines of ``unified_diff``
          (header / hunk-marker lines are excluded by
          :func:`_count_diff_lines`).
        - ``add``: every line of ``content`` counts as ``+1``.
        - ``delete``: every line of ``content`` counts as ``-1``.

        The frontend reads ``lines_added`` / ``lines_removed`` for the
        per-tool ``+N -M`` summary badge; the per-file breakdown is
        provided for future surfaces (it is not consumed yet today).
        """
        # ``spawn_agent``: ``is_terminated`` is flagged either on the
        # ``<subagent_notification>`` user message (the canonical
        # end-of-spawn signal — emitted whether the subagent
        # completed, errored, was shut down, or was not found), or on
        # a failed spawn ack (``function_call_output`` whose output is
        # not a JSON object carrying ``agent_id``). The successful ack
        # itself ``{"agent_id": "..."}`` returns ``None`` so the tool
        # stays running until the notification arrives. ``Max``-
        # aggregation across the spawn ack + notification links flips
        # the tool to terminated as soon as either signal lands.
        if tool_name == _SPAWN_AGENT_FUNCTION_NAME:
            if _subagent_notification_text(parsed_json) is not None:
                return orjson.dumps({"is_terminated": True}).decode()
            if parsed_json.get("type") == _TYPE_RESPONSE_ITEM:
                payload = _payload(parsed_json)
                if (
                    payload is not None
                    and payload.get("type") == "function_call_output"
                    and self.extract_agent_info_from_tool_result(parsed_json) is None
                ):
                    # Failed spawn ack (rejection text instead of JSON)
                    # — the tool will never receive a follow-up, so
                    # terminate it on this link directly.
                    return orjson.dumps({"is_terminated": True}).decode()
            return None

        # Shell family: ``is_terminated`` flagged on the closing chunk
        # for chained tools (``exec_command`` / ``write_stdin``), and
        # immediately on arrival for atomic tools (everything else in
        # :data:`_SHELL_FAMILY_TOOLS`). Listing only the chained set
        # explicitly means every new shell-like tool added to the
        # family defaults to atomic — see the comments on those two
        # frozensets above.
        if tool_name in _SHELL_FAMILY_TOOLS:
            if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
                return None
            payload = _payload(parsed_json)
            if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            if tool_name in _EXEC_COMMAND_TOOLS:
                # Chained exec tools (``exec_command`` / ``write_stdin``)
                # emit one ``function_call_output`` per write_stdin poll
                # — only the closing chunk reports a ``Process exited``
                # status trailer, and the spinner relies on that closing
                # chunk to flip ``extra.is_terminated``. Two termination
                # signals, checked in priority order:
                #
                #   1. User termination (Deny / Cancel turn, or a turn
                #      interruption via ``CodexAgent.soft_interrupt``).
                #      Recorded in the agent-side map; signal-based, no
                #      text pattern-match. If we see the call_id in the
                #      map, the tool is over — Codex never sends a closing
                #      ``Process exited`` chunk in this case (just an
                #      "aborted by user" output), so without this check
                #      the spinner would spin forever.
                #   2. Natural completion. The unified-exec status
                #      trailer reports ``Process exited with code N``
                #      on the closing chunk.
                #
                # Anything else is a still-running poll → return ``None``
                # so the ``Max``-aggregated extra stays unset and the
                # spinner keeps spinning.
                call_id = payload.get("call_id")
                user_terminated = (
                    isinstance(call_id, str)
                    and session_id is not None
                    and _user_terminated_tool_reason(session_id, call_id) is not None
                )
                if user_terminated:
                    logger.debug(
                        "Codex compute: terminating user-ended exec_command "
                        "via user-terminated signal: session=%s call_id=%s",
                        session_id, call_id,
                    )
                else:
                    output = payload.get("output", "")
                    if not isinstance(output, str):
                        return None
                    if not parse_exec_command_status(output).is_terminated:
                        return None
            # Atomic result row, the closing chunk of a chained sequence,
            # or a user-terminated chained call — flag it so the card stops spinning.
            return orjson.dumps({"is_terminated": True}).decode()

        # Code-mode ``exec``: chained like exec_command (one row for the
        # exec's own output plus one per rebound ``wait`` chunk). Only a
        # final script status (completed / failed / terminated) — or a
        # user termination, same signal-based check as exec_command —
        # flips ``is_terminated``; a ``Script running with cell ID <id>``
        # header keeps the spinner on until a wait chunk closes the cell.
        if tool_name == _CODE_MODE_EXEC_TOOL:
            if parsed_json.get("type") != _TYPE_RESPONSE_ITEM:
                return None
            payload = _payload(parsed_json)
            if payload is None or payload.get("type") not in _TOOL_RESULT_PAYLOAD_TYPES:
                return None
            call_id = payload.get("call_id")
            user_terminated = (
                isinstance(call_id, str)
                and session_id is not None
                and _user_terminated_tool_reason(session_id, call_id) is not None
            )
            if not user_terminated:
                parsed = parse_code_mode_output(payload.get("output"))
                if parsed is None or parsed.status == "running":
                    return None
            return orjson.dumps({"is_terminated": True}).decode()

        if tool_name != "apply_patch":
            return None
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return None
        payload = completed_item(parsed_json)
        if payload is None or payload.get("type") != "FileChange":
            return None
        changes = payload.get("changes")
        if not isinstance(changes, dict) or not changes:
            return None

        lines_added = 0
        lines_removed = 0
        files: list[dict] = []
        for path, entry in changes.items():
            if not isinstance(entry, dict) or not isinstance(path, str):
                continue
            file_added = 0
            file_removed = 0
            change_type = entry.get("type")
            if change_type == "update":
                unified_diff = entry.get("unified_diff")
                if isinstance(unified_diff, str):
                    file_added, file_removed = _count_diff_lines(unified_diff)
            elif change_type == "add":
                content = entry.get("content")
                if isinstance(content, str) and content:
                    file_added = content.count("\n") + (
                        0 if content.endswith("\n") else 1
                    )
            elif change_type == "delete":
                content = entry.get("content")
                if isinstance(content, str) and content:
                    file_removed = content.count("\n") + (
                        0 if content.endswith("\n") else 1
                    )

            files.append({
                "path": path,
                "lines_added": file_added,
                "lines_removed": file_removed,
            })
            lines_added += file_added
            lines_removed += file_removed

        if not files:
            return None
        return orjson.dumps({
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "files": files,
        }).decode()

    def detect_prefix_suffix(
        self, parsed_json: dict, kind: ItemKind | None
    ) -> tuple[bool, bool]:
        # Codex user_message / agent_message events carry their text in
        # a single flat ``message`` string (no mixed content blocks),
        # so they never have a collapsible prefix or suffix.
        return False, False

    def is_session_start_marker(self, parsed_json: dict) -> bool:
        return False

    def subagent_turn_boundary(self, parsed_json: dict) -> bool | None:
        """Map Codex's own turn events to the subagent's running / idle state.

        ``event_msg.task_started`` / ``task_complete`` bracket every turn a
        thread runs, subagent threads included — the one signal that says
        whether the child is still working. Codex's own ``list_agents``
        does NOT: it reports an agent as ``running`` until it is closed,
        long after it went idle (a subagent that answered its parent and
        sits there waiting for a follow-up is still ``running`` there).
        """
        if parsed_json.get("type") != _TYPE_EVENT_MSG:
            return None
        payload = _payload(parsed_json)
        if payload is None:
            return None
        sub_type = payload.get("type")
        if sub_type == _PAYLOAD_TASK_COMPLETE:
            return True
        if sub_type == _PAYLOAD_TASK_STARTED:
            return False
        return None

    def extract_custom_title(self, parsed_json: dict) -> tuple[str, str] | None:
        return None

    def transform_tool_result_with_cache(
        self, parsed_json: dict, session_id: str, line_num: int
    ) -> str | None:
        # CodexAgent captures pre-patch file contents when it sees a
        # ``FileChangeThreadItem`` arrive on ``item/started`` (the SDK's
        # equivalent of Claude's PreToolUse hook). When the matching
        # canonical ``FileChange`` item lands here, we pop the captured
        # contents and splice them into the item under
        # ``original_files`` so the frontend can render a full-file diff
        # (``EditContent.vue``-style) instead of only the ``unified_diff``
        # hunks Codex persists.
        call_id = _event_msg_call_id(parsed_json)
        if call_id is None:
            return None
        payload = completed_item(parsed_json)
        if payload is None or payload.get("type") != "FileChange":
            return None

        # Always pop from the cache (consume the entry whether we use it or not).
        cached = pop_original_files(session_id, call_id)
        if not cached:
            return None

        # Already enriched (defensive: re-compute pass on a JSONL line that
        # already carries the splice). Leave the persisted shape intact.
        if payload.get("original_files") is not None:
            return None

        payload["original_files"] = cached
        logger.debug(
            "Injected cached original_files into FileChange item "
            "(session=%s, line=%d, call_id=%s, files=%d)",
            session_id, line_num, call_id, len(cached),
        )
        return orjson.dumps(parsed_json).decode("utf-8")

    # ------------------------------------------------------------------
    # Batch compute
    # ------------------------------------------------------------------

    def analyze_content(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict[str, ToolUseEntry],
    ) -> ContentAnalysis:
        # Line shapes that contribute to content analysis in Codex:
        # - canonical ``UserMessage`` / ``AgentMessage`` items carry
        #   plain text.
        # - canonical ``FileChange`` / ``McpToolCall`` items are
        #   tool_results paired by their id with the originating
        #   function_call.
        # - ``response_item.function_call`` / ``custom_tool_call`` declares
        #   a tool_use. ``write_stdin`` lands in ``tool_use_map`` here so
        #   :meth:`remap_tool_result_id` can later rebind its output to
        #   the parent ``exec_command``; it stays bucketed as ``SYSTEM``
        #   for rendering via :meth:`compute_item_kind`.
        # - ``response_item.{function_call_output, custom_tool_call_output}``
        #   is a tool_result. For exec_command / write_stdin lines we
        #   also (a) parse the trailer to derive an error string from
        #   the formatted ``Process exited with code N`` line, and
        #   (b) maintain ``self._exec_command_maps[session_id]`` —
        #   adding an entry on ``Process running with session ID N`` and
        #   evicting on a terminating exit so the remap hook can resolve
        #   write_stdin children to their parent exec_command.
        # Every other line falls through to the empty analysis.
        wrapper_type = parsed_json.get("type")
        payload = _payload(parsed_json)
        if payload is None:
            return _EMPTY_ANALYSIS

        # Top-level ``compacted`` wrapper: classified as COMPACT_SUMMARY
        # by :meth:`compute_item_kind`. The encrypted summary is opaque
        # so we don't surface a ``text_content`` body, but the row is
        # still visible (the frontend renders a placeholder).
        if wrapper_type == _TYPE_COMPACTED:
            return ContentAnalysis(
                has_visible_content=True,
                text_content=None,
                is_system_xml=False,
                has_tool_result=False,
                tool_result_id=None,
                tool_result_error=None,
                tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                task_tool_uses=_EMPTY_TASK_TOOL_USES,
                file_paths=_EMPTY_FILE_PATHS,
                has_prefix=False,
                has_suffix=False,
                tool_result_agent_info=None,
            )

        if wrapper_type == _TYPE_EVENT_MSG:
            item = completed_item(parsed_json)
            item_type = item.get("type") if item is not None else None
            if item_type in {"UserMessage", "AgentMessage"}:
                text = (
                    user_message_text(parsed_json)
                    if item_type == "UserMessage"
                    else agent_message_text(parsed_json)
                )
                text = text.strip() if text else None
                return ContentAnalysis(
                    has_visible_content=(
                        user_message_is_visible(parsed_json)
                        if item_type == "UserMessage"
                        else bool(text)
                    ),
                    text_content=text,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            # ``SubAgentActivity`` (multi-agent v2): the spawn event
            # carries the ``(spawn call_id, subagent thread id)`` pair
            # the v2 ack dropped, so it feeds ``tool_result_agent_info``
            # — the base batch loop turns that into the AgentLink as
            # soon as the matching ``spawn_agent`` sits in
            # ``task_tool_use_map`` (it does: same turn, lower line).
            # It carries no tool *result* (``has_tool_result=False``):
            # the ack ``function_call_output`` already pairs with the
            # call by ``call_id``. We also record ``agent_path ->
            # spawn call_id`` so :meth:`remap_tool_result_id` can rebind
            # the later ``FINAL_ANSWER`` message — the v2 termination
            # signal — onto the same ToolResultLink chain.
            if item_type == "SubAgentActivity":
                spawn = _parse_sub_agent_activity_started(parsed_json)
                if spawn is None:
                    return _EMPTY_ANALYSIS
                self._agent_id_map(session_id)[spawn.agent_path] = spawn.call_id
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=(spawn.call_id, spawn.agent_id, True),
                )

            # ``image_generation_end`` is a standalone visible row (see
            # :meth:`compute_item_kind`). No tool_result pairing, no text
            # content surfaced here — the frontend pulls ``revised_prompt``,
            # ``result`` and ``saved_path`` straight from the payload via
            # the ImageGeneration component.
            if image_generation(parsed_json) is not None:
                return ContentAnalysis(
                    has_visible_content=True,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            event_call_id = _event_msg_call_id(parsed_json)
            if event_call_id is not None:
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=True,
                    tool_result_id=event_call_id,
                    tool_result_error=_event_msg_payload_error(item or {}),
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            return _EMPTY_ANALYSIS

        if wrapper_type == _TYPE_RESPONSE_ITEM:
            sub_type = payload.get("type")

            # ``<subagent_notification>`` user messages: synthetic
            # tool_result for the originating ``spawn_agent``. Detected
            # FIRST because it sits on a ``message`` payload that has no
            # ``call_id`` (so the call_id-based branches below would
            # short-circuit to ``_EMPTY_ANALYSIS``). The naive
            # tool_result_id is the agent_path (an agent_id); the remap
            # hook resolves it to the spawn_agent's call_id via the
            # side-table populated when the spawn ack is processed.
            # Error text comes from the status enum (``errored`` /
            # ``shutdown`` / ``not_found`` surface as errors;
            # ``completed`` keeps it ``None``).
            if sub_type == "message" and payload.get("role") == "user":
                notif = _parse_subagent_notification(parsed_json)
                if notif is not None:
                    agent_path, status = notif
                    return ContentAnalysis(
                        has_visible_content=False,
                        text_content=None,
                        is_system_xml=False,
                        has_tool_result=True,
                        tool_result_id=agent_path,
                        tool_result_error=_status_error_text(status),
                        tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                        task_tool_uses=_EMPTY_TASK_TOOL_USES,
                        file_paths=_EMPTY_FILE_PATHS,
                        has_prefix=False,
                        has_suffix=False,
                        tool_result_agent_info=None,
                    )

            # ``FINAL_ANSWER`` inter-agent message (multi-agent v2): the
            # same synthetic tool_result, one generation later. Detected
            # on an ``agent_message`` payload (no ``call_id`` either), with
            # the sender's agent path as the naive tool_result_id — the
            # remap hook resolves it to the spawning call_id through the
            # side-table the ``SubAgentActivity`` branch populated.
            # ``NEW_TASK`` / ``MESSAGE`` envelopes are deliberately left
            # alone: they're mid-flight traffic, not a completion, and
            # their payloads are encrypted.
            if sub_type == _PAYLOAD_AGENT_MESSAGE:
                final_answer = _parse_agent_final_answer(parsed_json)
                if final_answer is not None:
                    return ContentAnalysis(
                        has_visible_content=False,
                        text_content=None,
                        is_system_xml=False,
                        has_tool_result=True,
                        tool_result_id=final_answer[0],
                        tool_result_error=None,
                        tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                        task_tool_uses=_EMPTY_TASK_TOOL_USES,
                        file_paths=_EMPTY_FILE_PATHS,
                        has_prefix=False,
                        has_suffix=False,
                        tool_result_agent_info=None,
                    )

                # A ``NEW_TASK`` is the subagent's opening prompt: a
                # visible row (:meth:`compute_item_kind` makes it a
                # USER_MESSAGE) whose searchable text is whatever the
                # envelope leaves readable — the task name when the body
                # travelled encrypted, which is the usual case.
                task = _parse_agent_new_task(parsed_json)
                if task is not None:
                    return ContentAnalysis(
                        has_visible_content=True,
                        text_content=self.extract_user_message_text(parsed_json),
                        is_system_xml=False,
                        has_tool_result=False,
                        tool_result_id=None,
                        tool_result_error=None,
                        tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                        task_tool_uses=_EMPTY_TASK_TOOL_USES,
                        file_paths=_EMPTY_FILE_PATHS,
                        has_prefix=False,
                        has_suffix=False,
                        tool_result_agent_info=None,
                    )

            # Resultless tool calls (``web_search_call``) have no
            # ``call_id`` and never pair with anything — short-circuit
            # to a visible TOOL_USE row with no tool_use_entries so the
            # frontend renders the card without waiting for a result.
            if sub_type in _RESULTLESS_TOOL_SUB_TYPES:
                return ContentAnalysis(
                    has_visible_content=True,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            call_id = payload.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return _EMPTY_ANALYSIS

            if sub_type in _TOOL_CALL_PAYLOAD_TYPES:
                # Names in :data:`_IGNORED_FUNCTION_NAMES` (``wait_agent``)
                # carry no useful pairing — drop them entirely so no
                # ToolResultLink ever materialises for the matching
                # output. The function_call line itself is bucketed as
                # SYSTEM by :meth:`compute_item_kind`.
                if (
                    sub_type == "function_call"
                    and payload.get("name") in _IGNORED_FUNCTION_NAMES
                ):
                    return _EMPTY_ANALYSIS
                name = _tool_use_name(payload)
                tool_use_entries = {call_id: name}
                # Register code-mode execs whose script declares a nested
                # apply_patch or MCP call, so the later orphan
                # ``FileChange`` / ``McpToolCall`` can be
                # rebound to them (see _remap_orphan_end_event). Bounded
                # to the last 50 records per session.
                if sub_type == "custom_tool_call" and name == _CODE_MODE_EXEC_TOOL:
                    targets = _script_targets(payload.get("input"))
                    if targets.has_patch or targets.mcp_tools:
                        records = self._code_exec_target_map(session_id)
                        records.append((call_id, targets))
                        del records[:-50]
                # ``spawn_agent`` is the only agent-spawning tool today.
                # Always background — see :meth:`extract_task_tool_uses`.
                # ``name`` is the *qualified* one, so it matches the v2
                # ``collaboration__`` form too (:data:`_SPAWN_AGENT_TOOL_NAMES`).
                if sub_type == "function_call" and name in _SPAWN_AGENT_TOOL_NAMES:
                    task_tool_uses = [(call_id, True)]
                else:
                    task_tool_uses = _EMPTY_TASK_TOOL_USES
                return ContentAnalysis(
                    has_visible_content=True,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=False,
                    tool_result_id=None,
                    tool_result_error=None,
                    tool_use_entries=tool_use_entries,
                    task_tool_uses=task_tool_uses,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=None,
                )

            if sub_type in _TOOL_RESULT_PAYLOAD_TYPES:
                # For exec_command / write_stdin outputs, parse the
                # formatted trailer to (a) maintain the per-session
                # ``exec_command_id`` map and (b) surface a
                # ``"Exit code N"`` error string so the front lights up
                # the same way it would for any other failed shell.
                # Code-mode ``exec`` / ``wait`` outputs go through the
                # same dance with their own map (cell_id → exec call_id)
                # and error signal (``Script failed`` status header) —
                # the two maintainers are mutually exclusive by parent
                # tool name, so chaining on ``None`` is safe.
                tool_result_error = self._maintain_exec_command_map(
                    session_id, call_id, payload, tool_use_map
                )
                if tool_result_error is None:
                    tool_result_error = self._maintain_code_cell_map(
                        session_id, call_id, payload, tool_use_map
                    )
                # ``spawn_agent`` ack: the JSON ``{"agent_id": ...}`` lets
                # the batch path link parent ↔ subagent without waiting
                # for the prompt-matching fallback. The matching parent
                # ``function_call`` is in ``tool_use_map`` by now (same
                # session, lower line_num) — but we don't filter by tool
                # name here because :meth:`extract_agent_info_from_tool_result`
                # parses the output once and the base
                # ``compute_session_metadata`` only honours
                # ``tool_result_agent_info`` when the matching tool_use
                # is in ``task_tool_use_map``, which itself is populated
                # exclusively from ``analyze_content``'s ``task_tool_uses``
                # for ``spawn_agent``. So the gating is implicit.
                tool_result_agent_info = self.extract_agent_info_from_tool_result(parsed_json)
                # Mirror the (agent_id -> spawn_agent call_id) mapping in
                # the per-session side-table so ``remap_tool_result_id``
                # can rebind the later ``<subagent_notification>`` user
                # message to the same ToolResultLink chain. The live
                # path skips this and queries ``AgentLink`` instead.
                if tool_result_agent_info is not None:
                    spawn_call_id, agent_id, _is_async = tool_result_agent_info
                    self._agent_id_map(session_id)[agent_id] = spawn_call_id
                return ContentAnalysis(
                    has_visible_content=False,
                    text_content=None,
                    is_system_xml=False,
                    has_tool_result=True,
                    tool_result_id=call_id,
                    tool_result_error=tool_result_error,
                    tool_use_entries=_EMPTY_TOOL_USE_ENTRIES,
                    task_tool_uses=_EMPTY_TASK_TOOL_USES,
                    file_paths=_EMPTY_FILE_PATHS,
                    has_prefix=False,
                    has_suffix=False,
                    tool_result_agent_info=tool_result_agent_info,
                )

        return _EMPTY_ANALYSIS

    # compute_session_metadata + apply_session_complete: inherited from base.
    # The base orchestrates DB I/O and dispatches every parsing hook
    # declared above. ``sync_session_items_from_file`` (also inherited) is
    # driven by ``CodexSessionsWatcher`` for live updates.


# =============================================================================
# Singleton accessor
# =============================================================================


_compute_instance: CodexSessionCompute | None = None


def get_compute() -> CodexSessionCompute:
    """Return the process-local :class:`CodexSessionCompute` singleton."""
    global _compute_instance
    if _compute_instance is None:
        _compute_instance = CodexSessionCompute()
    return _compute_instance
