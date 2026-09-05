/**
 * Tool-rendering helpers for Codex sessions.
 *
 * Codex's ``exec_command`` tool is a single shell-running entry point —
 * the model packs ``cat foo``, ``rg PATTERN``, ``ls`` and arbitrary
 * scripts under the same name. The Rust runtime used to surface a
 * structured ``parsed_cmd`` (Read / ListFiles / Search / Unknown) on
 * ``event_msg.exec_command_end``, but the CLI no longer persists that
 * event. We rely on our local ``parseCommand`` classifier
 * (see ``parseCommand.js``) for the header label and summary line, and
 * we reconstruct the rich aggregated output by walking the chain of
 * ``function_call_output`` rows that the backend rebinds to a single
 * ``tool_use_id`` (the parent ``exec_command``'s ``call_id`` — the
 * write_stdin polling outputs hang off it via
 * ``CodexSessionCompute.remap_tool_result_id``).
 *
 * The shell still uses ``getExpectedResultCount`` for tools that have
 * a persisted ``*_end`` event (apply_patch, MCP, web_search,
 * image_generation) and switches to a status-based ``isToolRunning``
 * for the exec_command family.
 */

import { PROVIDER, PROCESS_STATE } from '../../constants'
import { BaseToolHelpers } from '../baseHelpers'
import { capitalize } from '../utils/format'
import { formatRelativePath, fileIconFor, resolveAbsolutePath } from '../utils/path'
import { parseCommand } from './parseCommand'
import { parseCodeModeOutput, parseCodeModeScript } from './parseCodeModeScript'
import {
    IMAGE_GEN_TOOL_NAME,
    describeWebRun,
    extractInputImageUrls,
    resolveCodeModeCall,
    summarizeCodeModeCalls,
} from './codeModeDisplay'
import { parseApplyPatchEnvelope } from './parsePatch'
import { getTodoDescription } from '../../utils/todoList'
import { formatToolNameForHeader, humanizeToolSegment } from '../../utils/toolNames'

import DescriptionSummary from '../../components/session/detail/items/summary/DescriptionSummary.vue'
import GrepSummary from '../../components/session/detail/items/summary/GrepSummary.vue'
import MultiFileSummary from '../../components/session/detail/items/summary/MultiFileSummary.vue'
import TodoSummary from '../../components/session/detail/items/summary/TodoSummary.vue'
import WebFetchSummary from '../../components/session/detail/items/summary/WebFetchSummary.vue'
import WebRunSummary from '../../components/session/detail/items/summary/WebRunSummary.vue'
import WebSearchSummary from '../../components/session/detail/items/summary/WebSearchSummary.vue'
import GoalUpdateSummary from '../../components/session/detail/items/summary/GoalUpdateSummary.vue'
import ExecResultContent from '../../components/session/detail/items/codex/ExecResultContent.vue'
import ReadResultContent from '../../components/session/detail/items/codex/ReadResultContent.vue'
import ApplyPatchContent from '../../components/session/detail/items/codex/ApplyPatchContent.vue'
import SpawnAgentResult from '../../components/session/detail/items/codex/SpawnAgentResult.vue'
import ViewImageResult from '../../components/session/detail/items/codex/ViewImageResult.vue'
import TodoContent from '../../components/session/detail/items/TodoContent.vue'

// Tool names that produce / consume a shell process and share the
// shell-card rendering path. Two facts at once for these tools:
//   - their result rows are paired by ``call_id`` and the spinner is
//     driven by :meth:`isToolRunning` reading ``extra.is_terminated``;
//   - their input carries a parseable shell command we can feed to the
//     local ``parseCommand`` parser to derive the header label
//     (Read / List files / Grep / Exec).
//
// Input shape varies by tool — three categories live here:
//   - ``exec_command`` (unified_exec): raw script as ``input.cmd``
//     (string).
//   - ``shell`` / ``shell_command`` / ``container.exec``:
//     ``Vec<String>`` argv as ``input.command``, typically
//     ``["bash", "-lc", "..."]``.
//   - ``local_shell_call``: native Responses-API shell tool (not a
//     ``function_call`` wire-level — it's the ``local_shell_call``
//     ``response_item`` sub_type; see ``ToolUse.vue`` for the unwrap).
//     Its input is ``payload.action`` with the same ``command`` argv
//     array, so it slots into the same parsing as ``shell`` et al.
//
// Output shape varies too — see ``aggregateExecCommandOutput`` and
// ``STRUCTURED_JSON_OUTPUT_TOOLS`` below: ``exec_command`` is the only
// tool here that emits a Codex unified-exec status trailer; ``shell``
// and ``local_shell_call`` emit a JSON-encoded payload; the others
// haven't been audited yet (we treat them as the trailer family until
// proven otherwise).
const FUNCTION_CALL_EXEC_TOOLS = new Set([
    'exec_command',
    'shell',
    'shell_command',
    'local_shell_call',
    'container.exec',
])

// Subset of :data:`FUNCTION_CALL_EXEC_TOOLS` whose
// ``function_call_output.output`` is a JSON-encoded string of shape
// ``{"output":"<body>","metadata":{"exit_code":N,"duration_seconds":N.N}}``
// (cf. ``format_exec_output_for_model_structured`` in
// ``codex-rs/core/src/tools/mod.rs``). ``aggregateExecCommandOutput``
// branches on this set to ``JSON.parse`` the wrapper and surface the
// inner ``output`` as the body.
//
// Members today:
//   - ``shell``: ``ShellHandler`` uses ``ToolEmitter::Shell { freeform: false }``
//     which routes to ``format_exec_output_for_model_structured``.
//   - ``local_shell_call``: ``LocalShellHandler`` uses the same emitter
//     variant via ``run_exec_like(freeform=false)`` (``local_shell.rs:116``).
//   - ``container.exec``: ``ContainerExecHandler`` calls
//     ``run_exec_like(freeform=false)`` too — same output shape, same
//     ``ShellToolCallParams`` arguments schema as ``shell``
//     (``shell/container_exec.rs:62``).
const STRUCTURED_JSON_OUTPUT_TOOLS = new Set([
    'shell',
    'local_shell_call',
    'container.exec',
])

// Subset of :data:`FUNCTION_CALL_EXEC_TOOLS` whose
// ``function_call_output.output`` is a freeform text trailer starting
// with ``Exit code: N`` (cf. ``format_exec_output_for_model_freeform``
// in ``codex-rs/core/src/tools/mod.rs``). Single member today:
// ``shell_command``, which uses ``ToolEmitter::Shell { freeform: true }``
// via ``run_exec_like(freeform=true)`` (``shell_command.rs:244``).
//
// The wire shape is plain text:
//
//     Exit code: N
//     Wall time: X.X seconds
//     [Total output lines: N]    ← optional, only when truncated
//     Output:
//     <body>
//
// ``aggregateExecCommandOutput`` branches on this set to read the exit
// code from the trailer and the body from after the ``Output:`` marker.
const FREEFORM_TEXT_OUTPUT_TOOLS = new Set([
    'shell_command',
])

// Tools that never produce a matched ``function_call_output`` or
// ``event_msg.*_end``. The tool_use card stands alone — no spinner, no
// expected result count, no chained outputs. Mirrors the backend
// :data:`twicc.providers.codex.compute._RESULTLESS_TOOL_SUB_TYPES`.
//
// Today: ``web_search_call``. Codex emits both a
// ``response_item.web_search_call`` and an ``event_msg.web_search_end``,
// but the call doesn't carry a serialised id so the two can't be paired
// from disk — we ignore the event_msg side entirely (it falls through
// to DEBUG_ONLY) and treat the call as a leaf node.
const RESULTLESS_TOOLS = new Set([
    'web_search_call',
])

// ``function_call`` tools with a canonical completed result item.
// ``apply_patch``'s JSON variant is here. Its Freeform variant lands as
// a ``custom_tool_call`` and is handled inline in getExpectedResultCount.
const FUNCTION_CALL_TOOLS_WITH_COMPLETED_ITEM = new Set([
    'apply_patch',
])

// MCP tools are dispatched as ``custom_tool_call`` and their names are
// always namespaced with this prefix (see ``ToolName::namespaced`` in
// the Codex source). We branch on the prefix because the actual name
// is dynamic (``mcp__<server>__<tool>``).
const MCP_TOOL_NAME_PREFIX = 'mcp__'

// Codex "code mode" (GPT-5.6+ ``tool_mode: code_mode_only`` models):
// every action is a ``custom_tool_call`` named ``exec`` whose ``input``
// is JavaScript calling nested tools (``tools.exec_command({...})``,
// ``tools.apply_patch("...")``, …) — the rollout never persists the
// nested calls, only the script. ``parseCodeModeScript`` statically
// recovers them so a script wrapping a single resolvable call renders
// like the direct call would (shell heuristics, patch diff, Todo, web,
// image, MCP); anything else degrades to the generic "Run code" card
// enriched with the detected call list. The companion ``wait``
// function_call (resumes a
// still-running cell) never reaches these helpers: the backend buckets
// it as SYSTEM and rebinds its output chunks to the owning ``exec``
// call, so they surface here through ``aggregateCodeModeOutput`` like
// write_stdin chunks do for ``exec_command``. Deliberately NOT a
// member of ``FUNCTION_CALL_EXEC_TOOLS`` — its input is JS, not a
// shell command, so it must never enter ``extractCommandPayload`` et
// al. Design: ``docs/plans/2026-07-10-codex-code-mode-display-design.md``.
const CODE_MODE_EXEC_TOOL_NAME = 'exec'

/**
 * Walk the result chain of a code-mode ``exec`` call (its own
 * ``custom_tool_call_output`` plus every ``wait`` chunk the backend
 * rebound to it) and stitch the bodies together — the code-mode
 * counterpart of ``aggregateExecCommandOutput``. The status header of
 * each chunk is stripped by ``parseCodeModeOutput``; ``isTerminated``
 * flips on the first final status (completed / failed / terminated).
 * ``exitCode`` is always ``null``: the script wrapper hides the nested
 * command's exit code (script-level failure surfaces through
 * ``ToolResultLink.error`` instead).
 */
function aggregateCodeModeOutput(toolId, options) {
    const results = options?.resultsArray
    if (!Array.isArray(results) || results.length === 0) return null
    const bodies = []
    let isTerminated = false
    for (const payload of results) {
        if (!payload || typeof payload !== 'object') continue
        if (payload.type !== 'function_call_output' && payload.type !== 'custom_tool_call_output') continue
        const parsed = parseCodeModeOutput(payload.output)
        if (parsed === null) {
            // Not a code-mode header — keep the raw string visible rather
            // than losing the chunk (defensive, format drift).
            if (typeof payload.output === 'string' && payload.output) bodies.push(payload.output)
            continue
        }
        if (parsed.status !== 'running') isTerminated = true
        if (parsed.body) bodies.push(parsed.body)
    }
    if (bodies.length === 0 && !isTerminated) return null
    return {
        aggregatedOutput: bodies.join(''),
        isTerminated,
        exitCode: null,
    }
}

// `spawn_agent` collects two ToolResultLinks per call: the immediate
// `function_call_output` ack carrying `{agent_id, nickname}` (rendered
// useless on its own), and a synthetic second link rebound from the
// `<subagent_notification>` user message Codex injects when the
// subagent finalises. The second one carries the actual subagent
// output via the AgentStatus enum (snake_case-tagged JSON):
//   - `{"completed": "msg" | null}` -> render `msg` as the result body
//   - `{"errored": "msg"}` -> same body; `ToolResultLink.error` already
//     drives the standard error callout
//   - `"shutdown"` / `"not_found"` -> short label, no body
// `transformDisplayResult` looks up that second link in `resultData`
// and produces a synthetic `{__spawnAgentResult: true, ...}` envelope
// consumed by `getResultRendering` to dispatch on `SpawnAgentResult.vue`.
const SPAWN_AGENT_TOOL_NAME = 'spawn_agent'
const SUBAGENT_NOTIFICATION_START = '<subagent_notification>'
const SUBAGENT_NOTIFICATION_END = '</subagent_notification>'

// Codex ships two generations of the multi-agent protocol, and the
// tool name differs between them: v1 spawns through a bare
// `spawn_agent`, v2 (`turn_context.multi_agent_version === "v2"`)
// qualifies every collaboration tool with a `collaboration` namespace,
// so `ToolUse.vue` composes `collaboration__spawn_agent` (same rule as
// the backend's `_qualified_function_call_name`). Both must light up
// the agent UI — old rollouts stay readable forever and a fresh install
// syncs them alongside new ones. Matched by explicit membership rather
// than a suffix test, so an unrelated MCP tool literally named
// `…__spawn_agent` can't hijack the agent card.
const COLLABORATION_NAMESPACE = 'collaboration'
const SPAWN_AGENT_TOOL_NAMES = new Set([
    SPAWN_AGENT_TOOL_NAME,
    `${COLLABORATION_NAMESPACE}__${SPAWN_AGENT_TOOL_NAME}`,
])

/**
 * Whether a resolved tool name is a `spawn_agent` call (multi-agent v1 or v2).
 *
 * @param {string} name - Tool name as resolved by `ToolUse.vue`.
 * @returns {boolean}
 */
function isSpawnAgentTool(name) {
    return SPAWN_AGENT_TOOL_NAMES.has(name)
}

// ``view_image`` loads a local image file and feeds it back to the model.
// Its ``function_call_output`` carries the bytes inline as ``input_image``
// part(s) (``{type:"input_image", image_url:"data:image/…;base64,…"}``).
// ``getResultRendering`` pulls those data URLs out (``extractInputImageUrls``
// in ``codeModeDisplay.js``) and hands them to ``ViewImageResult`` so the
// Result section shows the actual image instead of dumping the base64 blob
// through JsonHumanView. Everything else (the
// tool card, the ``{path, detail}`` input JSON) is the default tool path.
const VIEW_IMAGE_TOOL_NAME = 'view_image'

// Per-tool ``JsonHumanView`` overrides used when the Result/Input
// fallback rendering kicks in. Mirrors Claude Code's pattern: a tiny
// table keyed by tool name → ``{ key: { valueType, language } }``.
// Add new entries here as more Codex tools get tool-cards.
const INPUT_OVERRIDES = {
    exec_command: {
        // ``cmd`` is the raw shell script the model wants Codex to run.
        // Render it as a fenced bash block so it's syntax-coloured by
        // Shiki, the same way Claude Code's Bash ``command`` is shown.
        cmd: { valueType: 'string-code', language: 'bash' },
    },
    local_shell_call: {
        // ``command`` is an argv array (e.g. ``["bash", "-lc", "echo hi"]``)
        // passed verbatim from ``payload.action``. ``JsonHumanView``
        // auto-joins arrays with a single space when rendering them as
        // ``string-code``, so we get the same fenced bash block as
        // ``exec_command.cmd`` without having to flatten upstream — and
        // the array form remains intact for ``parseCommand`` which can
        // unwrap ``bash -lc <script>`` and classify Read / Grep / List.
        command: { valueType: 'string-code', language: 'bash' },
    },
    shell: {
        // Same argv-array shape as ``local_shell_call.command`` — schema
        // declared by ``create_shell_tool`` in
        // ``codex-rs/core/src/tools/handlers/shell_spec.rs``.
        command: { valueType: 'string-code', language: 'bash' },
    },
    shell_command: {
        // ``command`` here is a **string** (the raw shell script, not an
        // argv array). Schema declared by ``create_shell_command_tool``
        // in ``codex-rs/core/src/tools/handlers/shell_spec.rs``. The
        // ``string-code`` override renders it as a fenced bash block;
        // JsonHumanView passes the string through unchanged (no array
        // join needed, contrast with ``shell.command``).
        command: { valueType: 'string-code', language: 'bash' },
    },
    'container.exec': {
        // Legacy alias of ``shell`` — same ``ShellToolCallParams`` schema
        // (``command`` is an argv array). JsonHumanView auto-joins the
        // array when rendering as ``string-code``.
        command: { valueType: 'string-code', language: 'bash' },
    },
    exec: {
        // Codex's ``code_mode`` exec tool (``custom_tool_call name=exec``,
        // public name ``PUBLIC_TOOL_NAME = "exec"`` in
        // ``codex-rs/core/src/tools/code_mode/``). The ``input`` is raw
        // JavaScript source — the handler explicitly rejects anything
        // else (cf. ``execute_handler.rs:120``). Render it as a fenced
        // JS code-block. ``input`` here is the wrapper key set by
        // ``ToolUse.vue`` for ``custom_tool_call`` (``{ input: p.input }``).
        input: { valueType: 'string-code', language: 'javascript' },
        // Tier-1 exec_command scripts: ``getDisplayInputObject`` swaps the
        // JS wrapper for the extracted ``{cmd}`` so the card shows the
        // actual shell command (bash block) — the full JS source stays
        // reachable through the ``</>`` raw toggle.
        cmd: { valueType: 'string-code', language: 'bash' },
    },
    web_search_call: {
        // Multiple queries ship as ``queries`` (after the
        // ``getDisplayInputObject`` normalisation that collapses
        // single-item ``queries`` to a plain ``query`` string). Render
        // them one per line in the detail view so the user reads them
        // as a list rather than a comma-blurb.
        queries: { valueType: 'array-multiline' },
    },
}

// Per-tool whitelist of input keys to drop from the JSON fallback
// (kept out of the tool body but not from the raw JSON view, which is
// always reachable through the ``</>`` toggle). Stripped keys are
// usually internal knobs the user doesn't need to read on every call.
// Schema source for ``exec_command``: ``ExecCommandArgs`` in
// ``codex-rs/core/src/tools/handlers/unified_exec.rs``.
const STRIPPED_INPUT_KEYS_BY_TOOL = {
    exec_command: new Set([
        'workdir',
        // Internal: how long the runtime waits before yielding partial
        // output back to the model (default 10s). Implementation knob,
        // not interesting to readers.
        'yield_time_ms',
        // Internal: per-call truncation budget for the aggregated
        // output. Useful only when comparing it with the actual output
        // length, which we don't surface here either.
        'max_output_tokens',
        // Always present (defaults to false) but rarely meaningful.
        // We accept that the rare ``tty: true`` case is hidden — that
        // can come back as a dedicated badge later if needed.
        'tty',
        // Sandbox-approval mechanism (cf. ``create_approval_parameters``
        // in ``codex-rs/core/src/tools/handlers/shell_spec.rs``) — these
        // describe how Codex CLI authorised the call at runtime, not
        // what the call does. The user already saw / approved them
        // before the call ran; post-mortem they're noise.
        'sandbox_permissions',
        'justification',
        'prefix_rule',
        'additional_permissions',
    ]),
    // ``local_shell_call`` input is the unwrapped ``payload.action``.
    // Schema source: ``LocalShellExecAction`` in
    // ``codex-rs/protocol/src/models.rs``. Kept visible: ``command``
    // (the argv) and ``env`` (the model can inject ``LANG=C`` /
    // ``DEBUG=1`` / etc. which changes what the command does, so it
    // belongs in the body). Stripped:
    local_shell_call: new Set([
        // The action's enum tag — always ``"exec"`` today (single variant
        // ``LocalShellAction::Exec``), so it carries no information.
        'type',
        // Scheduling knob (max wall time before kill). Implementation
        // detail, not what the call *does*.
        'timeout_ms',
        // Where the command runs — mirrors what we strip for
        // ``exec_command.workdir``.
        'working_directory',
        // Account / privilege swap. Rare in practice, and when set it's
        // typically just a marker rather than an action — we accept that
        // the rare ``user: "root"`` case is hidden behind the ``</>``
        // toggle for now.
        'user',
    ]),
    // ``shell`` is a ``function_call`` whose ``arguments`` (JSON-parsed
    // upstream) shape is declared by ``create_shell_tool`` in
    // ``codex-rs/core/src/tools/handlers/shell_spec.rs``. Kept visible:
    // ``command`` (the argv). Stripped:
    shell: new Set([
        // Where the command runs — same rationale as ``exec_command.workdir``.
        'workdir',
        // Scheduling knob (max wall time before kill). Implementation
        // detail, not what the call *does*.
        'timeout_ms',
        // Sandbox-approval mechanism — same rationale and same fields as
        // for ``exec_command``. The model fills these only when it wants
        // to escalate out of the sandbox; the user already approved at
        // runtime, post-mortem they're noise.
        'sandbox_permissions',
        'justification',
        'prefix_rule',
        'additional_permissions',
    ]),
    // ``shell_command`` shape is declared by ``create_shell_command_tool``
    // in the same Rust file. Same approval mechanism as ``shell``; the
    // tool-specific knob ``login`` (login shell vs normal) is implementation
    // detail like ``exec_command.tty``. Kept visible: ``command``
    // (the shell script string). Stripped:
    shell_command: new Set([
        'workdir',
        'timeout_ms',
        // Login-shell semantics flag — appears only when the runtime
        // has ``allow_login_shell`` enabled. Implementation detail,
        // not what the script does.
        'login',
        'sandbox_permissions',
        'justification',
        'prefix_rule',
        'additional_permissions',
    ]),
    // ``container.exec`` shares ``shell``'s ``ShellToolCallParams``
    // schema and approval mechanism, so the stripped set mirrors
    // ``shell`` 1:1.
    'container.exec': new Set([
        'workdir',
        'timeout_ms',
        'sandbox_permissions',
        'justification',
        'prefix_rule',
        'additional_permissions',
    ]),
    // ``web_search_call`` input is the unwrapped ``payload.action``
    // (``WebSearchAction`` in ``codex-rs/protocol/src/models.rs:1163``).
    // Kept visible: ``query`` / ``queries`` (for ``type: search``),
    // ``url`` (for ``open_page`` / ``find_in_page``), ``pattern`` (for
    // ``find_in_page`` — even though the summary treats find_in_page
    // like an open_page, the pattern is still useful in the detail view).
    web_search_call: new Set([
        // The action's enum tag — already surfaced by the header label
        // (WebSearch vs WebFetch), so showing it again in the body is
        // pure duplication.
        'type',
    ]),
}

/**
 * Pull the command payload from a tool_use ``input`` according to the
 * tool's input shape. Returns ``null`` when the tool isn't one we
 * parse, or when the expected field is missing.
 *
 * Two ``command`` shapes coexist across the shell family:
 *   - argv array (``shell`` / ``local_shell_call`` / ``container.exec``):
 *     ``parseCommand`` unwraps ``bash -lc <script>`` and classifies
 *     Read / Grep / List.
 *   - raw script string (``shell_command``): ``parseCommand`` runs
 *     ``parseShellScript`` directly on it.
 * ``exec_command`` is the special case shipping the script under ``cmd``
 * instead of ``command``.
 */
function extractCommandPayload(name, input) {
    if (!input) return null
    if (name === 'exec_command') {
        return typeof input.cmd === 'string' ? input.cmd : null
    }
    if (typeof input.command === 'string') return input.command
    if (Array.isArray(input.command)) return input.command
    return null
}

/**
 * Locate a canonical completed result item in this tool's result rows.
 */
function findCanonicalResultItem(toolId, options, itemType) {
    if (!toolId) return null
    // The backend unwraps item_completed and returns the item directly.
    // ToolResultLink is authoritative for nested code-mode rebinding.
    const results = options?.resultsArray
    if (!Array.isArray(results) || results.length === 0) return null
    for (const item of results) {
        if (!item || typeof item !== 'object' || item.type !== itemType) continue
        return item
    }
    return null
}

/**
 * Pull the rich body out of an MCP call's result rows for the Result
 * section — shared by direct MCP ``function_call``s and code-mode
 * ``exec``s wrapping a single MCP call (whose ``McpToolCall`` item the
 * backend rebound onto the exec's chain).
 *
 * The backend returns the canonical item directly. We unwrap:
 *  - `result.structuredContent` when the server
 *    provides a structured schema (most modern MCPs);
 *  - `result` as-is otherwise — at minimum it carries
 *    `content: [{type:"text", text:"..."}]` + `isError`;
 *  - `error.message` for transport failures.
 * Falls back to the whole row on any unexpected shape so we never drop
 * content silently, and to ``undefined`` (= shell default behaviour)
 * when no end event is in the rows yet.
 */
function mcpEndDisplayResult(resultData) {
    if (!Array.isArray(resultData)) return undefined
    const mcpEnd = resultData.find((row) => row?.type === 'McpToolCall')
    if (!mcpEnd) return undefined
    const result = mcpEnd.result
    if (result && typeof result === 'object') {
        return result.structuredContent ?? result
    }
    if (typeof mcpEnd.error?.message === 'string') return mcpEnd.error.message
    return mcpEnd
}

// Match the formatted trailer Codex emits on every exec_command /
// write_stdin ``function_call_output`` (see
// ``codex-rs/core/src/tools/context.rs``). Mirrors the backend's
// :func:`twicc.providers.codex.compute.parse_exec_command_status`
// — kept inline here so the front isn't tied to backend regex churn.
const EXEC_COMMAND_STATUS_RE = /^Process (?:running with session ID (?<run>-?\d+)|exited with code (?<exit>-?\d+))$/m

// The body of a Codex tool output starts with this marker. Anything
// before it (Chunk ID / Wall time / Process … / Original token count
// for ``exec_command``; Exit code / Wall time / Total output lines
// for ``shell_command``) is structured trailer; the actual stdout /
// stderr lives after.
const OUTPUT_BODY_PREFIX_RE = /^Output:\n?/m

// Match the ``Exit code: N`` line emitted by
// ``format_exec_output_for_model_freeform`` (today: ``shell_command``).
// Anchored at line start (multiline mode) so a stray occurrence inside
// the body can't fool us. Mirrors the backend's
// :data:`twicc.providers.codex.compute._FREEFORM_EXIT_CODE_RE`.
const FREEFORM_EXIT_CODE_RE = /^Exit code: (-?\d+)$/m

/**
 * Walk the chain of ``function_call_output`` rows attached to this
 * tool_use_id and concatenate every body in order. Used by the
 * exec_command family — the backend rebinds every ``write_stdin``
 * polling output to the parent ``exec_command``'s tool_use_id, so
 * ``toolState.toolResultLineNums`` lists every chunk in source order
 * and we just stitch them back together.
 *
 * Returns ``null`` when nothing usable is in the store yet, otherwise
 * an object ready to feed :class:`ExecResultContent` /
 * :class:`ReadResultContent`:
 *   - ``aggregatedOutput``: concatenated bodies (string).
 *   - ``isTerminated``: ``true`` once any chunk reported a
 *     ``Process exited`` line.
 *   - ``exitCode``: parsed code on the closing chunk (or ``null``).
 *
 * The ``name`` argument selects the output dialect:
 *   - Members of :data:`STRUCTURED_JSON_OUTPUT_TOOLS` (today: ``shell``
 *     and ``local_shell_call``): the
 *     ``function_call_output.output`` is a JSON-encoded **string**
 *     ``{"output":"<body>","metadata":{"exit_code":N,"duration_seconds":N.N}}``
 *     (see ``format_exec_output_for_model_structured`` in
 *     ``codex-rs/core/src/tools/mod.rs``, then ``function_tool_response``
 *     in ``codex-rs/core/src/tools/context.rs`` which collapses the
 *     single ``InputText`` item to a ``Text`` body). We ``JSON.parse``
 *     defensively and pull ``output`` / ``metadata.exit_code`` —
 *     falling back to the raw string on parse failure so a future
 *     format change doesn't black out the card.
 *   - Members of :data:`FREEFORM_TEXT_OUTPUT_TOOLS` (today:
 *     ``shell_command``): freeform text starting with ``Exit code: N``
 *     (see ``format_exec_output_for_model_freeform`` in the same Rust
 *     file). We match ``Exit code:`` for the code and ``Output:`` for
 *     the body marker, falling back to the raw text if neither pattern
 *     matches.
 *   - everything else in the exec_command family (``exec_command``,
 *     ``write_stdin``): Codex unified-exec status trailer
 *     (``Process exited with code N``) + ``Output:`` prefix, see the
 *     code below.
 */
function aggregateExecCommandOutput(name, toolId, options) {
    // ``options.resultsArray`` is the already-fetched tool_result payload
    // list for this tool_use (see ToolUseContent.vue's
    // ``aggregatedExecOutput`` computed). The backend returns one entry
    // per ToolResultLink in tool_result_line_num ASC order, with the
    // ``response_item`` wrapper unwrapped — every entry is the payload
    // directly (see ``codex/helpers.py:get_tool_results``). We use this
    // instead of walking the session items store, which only carries the
    // visible window and would yield placeholders for chunks outside it.
    const results = options?.resultsArray
    if (!Array.isArray(results) || results.length === 0) return null
    const isStructuredJson = STRUCTURED_JSON_OUTPUT_TOOLS.has(name)
    const isFreeformText = FREEFORM_TEXT_OUTPUT_TOOLS.has(name)
    const bodies = []
    let isTerminated = false
    let exitCode = null
    for (const payload of results) {
        if (!payload || typeof payload !== 'object') continue
        if (payload.type !== 'function_call_output' && payload.type !== 'custom_tool_call_output') continue
        const output = typeof payload.output === 'string' ? payload.output : ''
        if (!output) continue
        if (isStructuredJson) {
            // ``output`` is a JSON-encoded string — decode it and pull
            // the actual body + exit code out. See the function-level
            // doc above for the wire shape and references.
            let parsed = null
            try {
                parsed = JSON.parse(output)
            } catch {
                parsed = null
            }
            if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
                if (typeof parsed.output === 'string') bodies.push(parsed.output)
                const meta = parsed.metadata
                if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
                    const code = meta.exit_code
                    if (Number.isInteger(code)) exitCode = code
                }
                // A successful parse means the result has fully landed
                // (the backend's ``compute_link_extra`` already flagged
                // this on ``ToolResultLink.extra`` for the spinner; we
                // mirror that here so the aggregator's own
                // ``isTerminated`` is consistent and the
                // ``bodies.length === 0 && !isTerminated`` guard below
                // doesn't drop a zero-output success).
                isTerminated = true
            } else {
                // Unparseable — keep the raw string visible rather than
                // losing the output entirely (a malformed payload or a
                // future Codex format change shouldn't black out the
                // shell card).
                bodies.push(output)
            }
            continue
        }
        if (isFreeformText) {
            // Freeform text trailer — pull the exit code from the
            // ``Exit code: N`` line and the body from after the
            // ``Output:`` marker. See the function-level doc above for
            // the wire shape and references.
            let matchedAnything = false
            const exitMatch = FREEFORM_EXIT_CODE_RE.exec(output)
            if (exitMatch) {
                const code = parseInt(exitMatch[1], 10)
                if (Number.isFinite(code)) exitCode = code
                matchedAnything = true
            }
            const bodyMatch = OUTPUT_BODY_PREFIX_RE.exec(output)
            if (bodyMatch) {
                const body = output.slice(bodyMatch.index + bodyMatch[0].length)
                if (body) bodies.push(body)
                matchedAnything = true
            }
            if (matchedAnything) {
                // Same rationale as ``isStructuredJson`` above: a
                // successful match means the result has fully landed;
                // mirror the backend's ``extra.is_terminated`` so the
                // zero-output success isn't dropped by the guard below.
                isTerminated = true
            } else {
                // Defensive fallback — push the raw text so the card
                // doesn't go blank on a future format change.
                bodies.push(output)
            }
            continue
        }
        // Status trailer (running/exited).
        const statusMatch = EXEC_COMMAND_STATUS_RE.exec(output)
        if (statusMatch?.groups?.exit !== undefined) {
            isTerminated = true
            const code = parseInt(statusMatch.groups.exit, 10)
            if (Number.isFinite(code)) exitCode = code
        }
        // Body lives after ``Output:\n`` (which is always emitted, even
        // when the body is empty). Anything without that marker is
        // either malformed or not a unified-exec output — skip.
        const bodyMatch = OUTPUT_BODY_PREFIX_RE.exec(output)
        if (!bodyMatch) continue
        const body = output.slice(bodyMatch.index + bodyMatch[0].length)
        if (body) bodies.push(body)
    }
    if (bodies.length === 0 && !isTerminated) return null
    return {
        aggregatedOutput: bodies.join(''),
        isTerminated,
        exitCode,
    }
}

/**
 * Make ``path`` (as parsed out of a shell command) absolute against
 * the call's ``workdir`` (when present), then relative to the session
 * base dir. Required because ``parseCommand`` returns paths verbatim
 * from the command — typically already relative to the call's working
 * directory — so a literal ``formatRelativePath`` against the session
 * cwd misses the case where the model ran the tool from a sub-folder
 * (e.g. ``cd frontend && rg foo src/`` → path is ``src/`` relative to
 * ``frontend/``, not the session root). Absolute paths short-circuit
 * the ``workdir`` step. Falls back to ``baseDir`` when ``workdir``
 * isn't supplied so the legacy behaviour is preserved.
 */
function relPathFromWorkdir(path, input, baseDir) {
    if (!path) return path
    const workdir = (input && typeof input.workdir === 'string' && input.workdir) || baseDir
    const absPath = resolveAbsolutePath(path, workdir)
    return formatRelativePath(absPath, baseDir)
}

/**
 * Locate the matching canonical FileChange item for an apply_patch call.
 */
function findPatchApplyEndPayload(toolId, options) {
    return findCanonicalResultItem(toolId, options, 'FileChange')
}

/**
 * Resolve the file paths an ``apply_patch`` call touches, with
 * supersedence:
 *   1. ``FileChange.changes`` keys when loaded — the canonical,
 *      absolute paths the runtime actually applied to.
 *   2. Local v4a parser otherwise — what the model declared in its
 *      ``input``, available immediately on the tool_use line.
 * Returns ``[]`` when neither source yields anything.
 */
function resolveApplyPatchPaths(input, options) {
    const payload = findPatchApplyEndPayload(options?.toolId, options)
    if (payload && payload.changes && typeof payload.changes === 'object') {
        const keys = Object.keys(payload.changes)
        if (keys.length > 0) return keys
    }
    const parsed = parseApplyPatchEnvelope(typeof input === 'string' ? input : input?.input)
    return parsed.map((f) => f.path).filter(Boolean)
}

/**
 * Resolve the ``ParsedCommand[]`` to feed ``mergeStages`` /
 * ``pickPrimary``. Codex used to surface a tree-sitter-bash
 * ``parsed_cmd`` on the ``exec_command_end`` event but no longer
 * persists it (TUI flipped to ``persist_extended_history=false`` on
 * 2026-04-30), so the local ``parseCommand`` estimate is now the
 * canonical source. Returns ``null`` when the command shape isn't one
 * we know how to extract.
 */
function resolveParsedCommand(name, input) {
    const payload = extractCommandPayload(name, input)
    if (payload === null) return null
    return parseCommand(payload)
}

const HEADER_LABELS_BY_VARIANT = {
    read: 'Read',
    list_files: 'List files',
    // ``Grep`` reads better than ``Search`` for shell users: it
    // mirrors what the underlying tools (rg / grep / git grep) are.
    search: 'Grep',
}

// Priority used by ``pickPrimary``: more specific variants win.
// ``search`` carries a query (the most informative item), then ``read``
// (concrete file), then ``list_files`` (broader scope), then
// ``unknown`` (raw command). Ties are broken by "last wins" so the
// rightmost stage of an equal-priority sequence is the primary one.
const VARIANT_PRIORITY = { search: 3, read: 2, list_files: 1, unknown: 0 }

/**
 * Post-process a ``ParsedCommand[]`` to merge known combos into a
 * single richer stage. Applied between the parser (ours or Codex's
 * ``parsed_cmd``) and ``pickPrimary``, so the same rules drive both
 * sources.
 *
 * Current rule:
 *   - ``list_files`` / ``read`` immediately followed by ``search``
 *     without its own ``path`` → keep the upstream **type** (the
 *     operation is still a listing or a read; the trailing ``search``
 *     just filters its output) and graft the search's ``query`` onto
 *     the upstream entry. Captures pipelines like
 *     ``rg --files . | rg PATTERN`` (still a list of files, narrowed
 *     by name) or ``cat foo | grep bar`` (still a read, narrowed by
 *     content). The summary renderer treats a ``list_files`` / ``read``
 *     with a ``query`` field as a Grep-style display so the query is
 *     surfaced; the header label stays ``List files`` / ``Read``.
 *
 * The ``query`` field on ``list_files`` / ``read`` is a TwiCC-only
 * extension over the Codex ``ParsedCommand`` schema — fine, since
 * downstream code only consumes the merged structure.
 */
function mergeStages(parsed) {
    if (!Array.isArray(parsed) || parsed.length < 2) return parsed
    const out = []
    let i = 0
    while (i < parsed.length) {
        const cur = parsed[i]
        const next = parsed[i + 1]
        const isSourceForSearch = cur && (cur.type === 'list_files' || cur.type === 'read') && cur.path
        const isPathlessSearch = next && next.type === 'search' && !next.path
        if (isSourceForSearch && isPathlessSearch) {
            out.push({ ...cur, query: next.query })
            i += 2
        } else {
            out.push(cur)
            i += 1
        }
    }
    return out
}

/**
 * Pick the "most informative" stage from a parsed command sequence,
 * using ``VARIANT_PRIORITY``. Returns ``null`` for an empty input.
 * When everything is ``unknown`` we still return the last entry so
 * callers can render its raw ``cmd`` text.
 */
function pickPrimary(parsed) {
    if (!parsed || parsed.length === 0) return null
    let best = null
    let bestScore = -1
    for (const p of parsed) {
        const score = VARIANT_PRIORITY[p.type] ?? 0
        if (score >= bestScore) {  // ``>=`` → ties resolved by last wins
            best = p
            bestScore = score
        }
    }
    return best
}

/**
 * First line of ``cmd`` for the ``unknown`` summary variant. When the
 * source has more than one line we explicitly append ``…`` so the
 * truncation is visible even if the (possibly short) first line fits
 * on the row. When the source is a single line, we hand it back as-is
 * and rely on the surrounding CSS ``text-overflow: ellipsis`` to add
 * the ``…`` only when the text actually overflows the row width.
 */
function firstLine(cmd) {
    if (typeof cmd !== 'string') return ''
    const idx = cmd.indexOf('\n')
    return idx >= 0 ? cmd.slice(0, idx) + '…' : cmd
}

// ─── update_plan ────────────────────────────────────────────────────────
//
// Codex's ``update_plan`` is the moral equivalent of Claude Code's
// ``TodoWrite``: a list of plan items, each with a free-form text and
// one of the same three statuses (pending / in_progress / completed).
// We map it to the same renderers (``TodoContent`` / ``TodoSummary``)
// by normalising every entry to ``{ content, status }`` — Claude Code
// also has an ``activeForm`` field that Codex doesn't, so we leave it
// undefined and let the shared helpers fall back to ``content``.
// Source spec: ``codex-rs/core/src/tools/handlers/plan_spec.rs``.

function isValidPlan(plan) {
    if (!Array.isArray(plan) || plan.length === 0) return false
    return plan.every(p =>
        p != null && typeof p === 'object' &&
        typeof p.step === 'string' &&
        typeof p.status === 'string',
    )
}

function planToTodos(plan) {
    return plan.map(p => ({ content: p.step, status: p.status }))
}

// ─── Goal tools (Codex-only) ────────────────────────────────────────────
//
// Codex exposes three thread-Goal tools: ``create_goal`` (objective +
// token_budget), ``get_goal`` (read-only, filtered out upstream as
// DEBUG_ONLY), and ``update_goal`` (mutate status / objective / budget).
// Only the summary line and header label differ from the generic tool
// card — the detail (input) and result keep the default JSON rendering.

/**
 * Decompose an ``update_goal`` call's arguments for the summary line. The
 * keys present in the input ARE the fields being changed (Codex only sends
 * what it mutates). Returns ``{ status, changedKeys }`` — ``status`` is the
 * new status value when ``status`` was among the changed fields (else
 * ``null``), ``changedKeys`` lists the other changed field names. The
 * rendering (labels, the green ``complete`` check) lives in
 * :class:`GoalUpdateSummary`. Returns ``null`` when there's nothing to
 * show (empty / non-object input).
 */
function parseGoalUpdate(input) {
    if (!input || typeof input !== 'object') return null
    const keys = Object.keys(input)
    if (keys.length === 0) return null
    const hasStatus = Object.prototype.hasOwnProperty.call(input, 'status')
    return {
        status: hasStatus ? String(input.status) : null,
        changedKeys: keys.filter((k) => k !== 'status'),
    }
}

/**
 * Read the body of a `<subagent_notification>` user message ToolResultLink
 * row. Returns `{agentPath, status}` (where `status` is the raw enum
 * variant — dict for `Completed`/`Errored`, string for `Shutdown`/`NotFound`)
 * or `null` if the row is anything else.
 *
 * The `row` is the JSONL `payload` (not the `{type, payload}` wrapper) —
 * the backend's `get_tool_results` strips the wrapper before serialising.
 * For a subagent_notification this means `row.type === 'message'` and
 * `row.role === 'user'`.
 */
function extractSubagentNotificationBody(row) {
    if (!row || row.type !== 'message' || row.role !== 'user') return null
    const content = Array.isArray(row.content) ? row.content : null
    if (!content || content.length === 0) return null
    const first = content[0]
    if (!first || first.type !== 'input_text' || typeof first.text !== 'string') return null
    const text = first.text
    const start = text.indexOf(SUBAGENT_NOTIFICATION_START)
    if (start < 0) return null
    const end = text.indexOf(SUBAGENT_NOTIFICATION_END, start + SUBAGENT_NOTIFICATION_START.length)
    if (end < 0) return null
    const body = text.slice(start + SUBAGENT_NOTIFICATION_START.length, end).trim()
    if (!body) return null
    let parsed
    try {
        parsed = JSON.parse(body)
    } catch {
        return null
    }
    if (!parsed || typeof parsed !== 'object') return null
    const agentPath = typeof parsed.agent_path === 'string' ? parsed.agent_path : null
    if (!agentPath) return null
    return { agentPath, status: parsed.status }
}

/**
 * Read `{agent_id, nickname}` out of the `function_call_output` ack of a
 * successful `spawn_agent`. Returns `null` for any other shape (different
 * tool, freeform rejection text, malformed JSON…).
 *
 * Same wrapper convention as `extractSubagentNotificationBody`: `row` is
 * the payload, so `row.type === 'function_call_output'`.
 */
function extractSpawnAckBody(row) {
    if (!row || row.type !== 'function_call_output') return null
    const output = row.output
    if (typeof output !== 'string' || !output) return null
    let parsed
    try {
        parsed = JSON.parse(output)
    } catch {
        return null
    }
    if (!parsed || typeof parsed !== 'object') return null
    const agentId = typeof parsed.agent_id === 'string' ? parsed.agent_id : null
    if (!agentId) return null
    const nickname = typeof parsed.nickname === 'string' ? parsed.nickname : null
    return { agentId, nickname }
}

export class CodexToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CODEX

    getExpectedResultCount(name, _input, options) {
        // Resultless tools never produce a paired output — short-circuit
        // before any wrapper-specific branch.
        if (RESULTLESS_TOOLS.has(name)) return 0

        // MCP tools land as ``function_call`` with a fully-qualified
        // ``name`` starting with ``mcp__`` (assembled from
        // ``payload.namespace + "__" + payload.name`` by ``ToolUse.vue``
        // and the backend's ``_qualified_function_call_name``). They
        // always emit two ToolResultLinks: the LLM-facing
        // ``function_call_output`` and the richer
        // canonical ``McpToolCall`` item paired by ``call_id``. We keep
        // both in the store; ``transformDisplayResult`` picks the
        // McpToolCall item for the Result section.
        if (typeof name === 'string' && name.startsWith(MCP_TOOL_NAME_PREFIX)) return 2

        const wrapperType = options?.wrapperType
        if (wrapperType === 'function_call') {
            // Shell-family tools (``FUNCTION_CALL_EXEC_TOOLS``): the
            // spinner is driven by :meth:`isToolRunning` reading
            // ``extra.is_terminated`` rather than the expected count.
            // ``exec_command`` chains a variable number of
            // ``function_call_output`` rows (one per ``write_stdin``
            // poll); ``shell`` and the others emit a single output
            // — the backend's ``compute_link_extra`` flags the
            // matching result row as terminated on arrival either way.
            if (FUNCTION_CALL_EXEC_TOOLS.has(name)) return 1
            // ``spawn_agent`` collects two paired rows: the immediate
            // ack ``function_call_output`` carrying ``{agent_id,
            // nickname}`` and the synthetic second link rebound from
            // the ``<subagent_notification>`` user message Codex
            // injects when the subagent finalises (see the
            // ``SPAWN_AGENT_TOOL_NAME`` block at the top of this
            // module for the full shape). Counted here so the shell's
            // ``isToolRunning`` flips to done only once the
            // notification arrives, matching the agent-running
            // semantics the View-Agent UI relies on.
            if (isSpawnAgentTool(name)) return 2
            return FUNCTION_CALL_TOOLS_WITH_COMPLETED_ITEM.has(name) ? 2 : 1
        }
        if (wrapperType === 'custom_tool_call') {
            // apply_patch (Freeform variant) is the only custom_tool_call
            // that pairs with a persisted ``*_end`` event today.
            // The code_mode ``exec`` tool falls through to 1: like
            // ``exec_command`` it can chain a variable number of result
            // rows (one per rebound ``wait`` chunk), so the spinner is
            // driven by :meth:`isToolRunning` reading
            // ``extra.is_terminated`` rather than the expected count.
            if (name === 'apply_patch') return 2
            return 1
        }
        if (wrapperType === 'local_shell_call') {
            // Native shell tool: a single ``function_call_output`` paired
            // by ``call_id`` (no ``*_end`` event, no chained ``write_stdin``
            // polls). The backend sets ``extra.is_terminated`` on arrival
            // (see ``CodexSessionCompute.compute_link_extra``) so the
            // spinner flips to done as soon as the result lands.
            return 1
        }
        // image_generation_call: the ``*_end`` event is the only result
        // (no separate ``*_call_output`` payload). Single ToolResultLink,
        // single result.
        return 1
    }

    getRequiredResultCountForDisplay(name, input, options) {
        // Shell tools — and code-mode ``exec`` — render progressively
        // from a single chunk (the aggregation helpers concatenate
        // whatever is in the store), so 1 is enough; everything else
        // mirrors ``getExpectedResultCount``.
        if (FUNCTION_CALL_EXEC_TOOLS.has(name) || name === CODE_MODE_EXEC_TOOL_NAME) return 1
        return this.getExpectedResultCount(name, input, options)
    }

    isToolRunning(name, input, options) {
        // Resultless tools never wait for a result — they're "done" from
        // the moment they land in the store.
        if (RESULTLESS_TOOLS.has(name)) return false
        // Any errored link → tool is dead, no more results coming.
        // Catches Codex aborting before the full expected result set
        // lands (Deny / Cancel turn on apply_patch only emits the
        // ``custom_tool_call_output`` row; the ``FileChange`` row
        // never comes because Codex never applies the patch). The
        // aggregated ``toolState.error`` is ``Max('error')`` across
        // every link, so any non-null marks the tool as terminated.
        if (options?.toolState?.error) return false
        // Shell tools — and code-mode ``exec``, whose ``wait`` chunks
        // chain exactly like write_stdin polls: status comes from the
        // chain's last chunk via the ``is_terminated`` flag the backend
        // set on ``ToolResultLink.extra``. ``Max``-aggregated across
        // links so any closing chunk flips the whole tool to "done".
        if (FUNCTION_CALL_EXEC_TOOLS.has(name) || name === CODE_MODE_EXEC_TOOL_NAME) {
            const extra = options?.toolState?.extra
            if (extra) {
                // ``extra`` is the JSON string set by
                // :meth:`compute_link_extra` — parse defensively so the
                // shell never crashes on unexpected shapes (live race,
                // malformed payload).
                try {
                    const parsed = typeof extra === 'string' ? JSON.parse(extra) : extra
                    if (parsed?.is_terminated) return false
                } catch {
                    // Malformed extra → fall through to the liveness gate.
                }
            }
            // No ``is_terminated`` signal yet. A chained ``exec_command``
            // only advances while the agent is working: each ``write_stdin``
            // poll runs inside the same ASSISTANT_TURN. Once the session is
            // back to USER_TURN — turn finished or soft-interrupted — no
            // closing ``Process exited`` / ``aborted by user`` chunk will
            // ever come (an interrupt mid-chain leaves the last chunk
            // reporting ``Process running``), so the tool can't be running
            // anymore; without this gate the spinner spins forever. We only
            // trust an explicit USER_TURN — null/unknown (process not yet
            // synced, or dead/historical, which ``isStaleToolUse`` already
            // handles) keeps the prior "assume running" behaviour.
            if (options?.processState === PROCESS_STATE.USER_TURN) return false
            return true
        }
        return super.isToolRunning(name, input, options)
    }

    shouldAggregateExecOutput(name) {
        return FUNCTION_CALL_EXEC_TOOLS.has(name) || name === CODE_MODE_EXEC_TOOL_NAME
    }

    getAggregatedExecOutput(name, toolId, options) {
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            return aggregateCodeModeOutput(toolId, options)
        }
        return aggregateExecCommandOutput(name, toolId, options)
    }

    getHeaderLabel(name, input, options) {
        // ``apply_patch`` is the model's verb, not the user-facing
        // operation. Mirror Claude Code's ``Edit`` header so users see
        // the same word regardless of provider.
        if (name === 'apply_patch') return 'Edit'
        // Same idea for ``update_plan`` → ``Todo``: the tool plays the
        // role of Claude Code's ``TodoWrite``, so users see the same
        // header word across providers.
        if (name === 'update_plan') return 'Todo'
        // ``web_search_call`` splits into two user-facing surfaces
        // depending on the action variant: a web search for ``search``
        // (one or more queries) vs a web fetch for ``open_page`` /
        // ``find_in_page`` (URL retrieval). Sentence case to match the
        // shared formatter, which now renders Claude Code's ``WebSearch`` /
        // ``WebFetch`` tools as ``Web search`` / ``Web fetch`` — so the user
        // gets the same words across providers. Not removable: dropping the
        // override would surface the raw ``Web search call`` and lose the
        // search-vs-fetch distinction (which lives in ``input.type``).
        if (name === 'web_search_call') {
            return input?.type === 'search' ? 'Web search' : 'Web fetch'
        }
        if (name === 'web__run') {
            const web = describeWebRun(input)
            if (web?.kind === 'search') return 'Web search'
            if (web?.kind === 'fetch') return 'Web fetch'
            return web ? 'Web' : null
        }
        // ``exec`` is Codex's ``code_mode`` tool — runs a JavaScript
        // snippet as a sandboxed code cell. When the script wraps a
        // single resolvable nested call, label it like the direct call
        // would be (shell heuristics for exec_command, ``Edit`` for
        // apply_patch); otherwise "Run code" reads better than the
        // bare ``exec`` for users.
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            const nested = resolveCodeModeCall(input)
            if (nested?.name === 'exec_command') {
                // Delegate to the direct exec_command path — the nested
                // arg has the exact same shape ({cmd, workdir, …}).
                return this.getHeaderLabel('exec_command', nested.arg, options) ?? 'Shell'
            }
            if (nested?.name === 'apply_patch') return 'Edit'
            if (nested?.name === VIEW_IMAGE_TOOL_NAME) return 'Image'
            if (nested?.name === IMAGE_GEN_TOOL_NAME) return 'Image generation'
            if (nested?.name === 'update_plan') return 'Todo'
            if (nested?.name === 'web__run') {
                const web = describeWebRun(nested.arg)
                if (web?.kind === 'search') return 'Web search'
                if (web?.kind === 'fetch') return 'Web fetch'
                return 'Web'
            }
            if (nested?.name?.startsWith(MCP_TOOL_NAME_PREFIX)) {
                // Same words a direct MCP call would show: direct calls
                // have no headerLabel, so the shell formats the raw
                // ``mcp__server__tool`` name — do the same here (the
                // shell's fallback would format ``exec`` instead).
                return formatToolNameForHeader(nested.name)
            }
            return 'Run code'
        }
        // ``view_image`` loads a local image for the model — show the
        // clean "Image" header instead of the raw ``view_image`` name.
        if (name === VIEW_IMAGE_TOOL_NAME) return 'Image'
        // GPT-5.6 hosted image generation: the raw namespaced name would
        // format as "Image gen : Imagegen". The generated picture itself is
        // a separate ``image`` item (``ImageGeneration.vue``); this card is
        // the call that produced it.
        if (name === IMAGE_GEN_TOOL_NAME) return 'Image generation'
        // Only SEMANTIC remaps live here (Edit, Todo, WebSearch, Image,
        // Run code) — where the header word differs from the tool's raw
        // name. Pure snake_case → clean-label cases (``create_goal``,
        // ``update_goal``, ``request_user_input``, and any future
        // first-party tool) are intentionally NOT listed: the shared
        // ``formatToolNameForHeader`` fallback already sentence-cases them
        // ("Create goal", "Request user input"), so an entry here would be
        // dead duplication. ``get_goal`` never reaches the header — the
        // backend buckets it as SYSTEM (DEBUG_ONLY).
        if (!FUNCTION_CALL_EXEC_TOOLS.has(name)) return null
        const parsed = resolveParsedCommand(name, input)
        if (!parsed) return null
        const primary = pickPrimary(mergeStages(parsed))
        if (!primary) return null
        return HEADER_LABELS_BY_VARIANT[primary.type] ?? 'Shell'
    }

    getSummaryRendering(name, input, baseDir, options) {
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            // Tier 1: delegate to the direct tool's summary path with the
            // extracted argument (same shapes — {cmd, workdir, …} for
            // exec_command, { input: <envelope> } for apply_patch).
            const nested = resolveCodeModeCall(input)
            if (nested?.name === 'exec_command') {
                return this.getSummaryRendering('exec_command', nested.arg, baseDir, options)
            }
            if (nested?.name === 'apply_patch') {
                return this.getSummaryRendering('apply_patch', { input: nested.arg }, baseDir, options)
            }
            if (nested?.name === VIEW_IMAGE_TOOL_NAME) {
                return this.getSummaryRendering(VIEW_IMAGE_TOOL_NAME, nested.arg, baseDir, options)
            }
            if (nested?.name === IMAGE_GEN_TOOL_NAME) {
                return this.getSummaryRendering(IMAGE_GEN_TOOL_NAME, nested.arg, baseDir, options)
            }
            if (nested?.name === 'update_plan') {
                return this.getSummaryRendering('update_plan', nested.arg, baseDir, options)
            }
            if (nested?.name === 'web__run') {
                const web = describeWebRun(nested.arg)
                if (!web || web.summaryItems.length === 0) return null
                if (web.kind === 'search') {
                    return {
                        component: WebSearchSummary,
                        props: {
                            query: web.summaryItems.length === 1
                                ? web.summaryItems[0]
                                : web.summaryItems,
                        },
                    }
                }
                if (
                    web.kind === 'fetch' && web.summaryItems.length === 1
                    && /^https?:\/\/[^\s·]+$/i.test(web.summaryItems[0])
                ) {
                    return {
                        component: WebFetchSummary,
                        props: { url: web.summaryItems[0] },
                    }
                }
                return {
                    component: WebRunSummary,
                    props: { items: web.summaryItems },
                }
            }
            // Tier-1 MCP: a direct MCP call shows no summary (the
            // formatted name is already the header) — mirror that.
            if (nested?.name?.startsWith(MCP_TOOL_NAME_PREFIX)) return null
            // Tier 2: list the detected nested tools so the collapsed
            // card says what the script does. Tier 3 (nothing detected)
            // keeps the bare "Run code" header, no summary.
            const detected = summarizeCodeModeCalls(
                input,
                (nestedName, nestedInput) => this.getHeaderLabel(nestedName, nestedInput, options),
            )
            if (!detected) return null
            return {
                component: DescriptionSummary,
                props: { description: detected, fileIconSrc: null, truncate: true },
            }
        }
        if (isSpawnAgentTool(name)) {
            // Same spot Claude Code shows the Task ``description``: after
            // the em-dash. Two independent bits land there:
            //
            // - the ``task_name`` the parent chose for this delegation
            //   (multi-agent v2 only), sentence-cased like every other
            //   machine identifier we surface. It is the ONLY readable
            //   trace of what the subagent was asked to do — the prompt
            //   itself travels encrypted.
            // - the subagent's nickname in parens (Codex's
            //   ``agent_nickname``, persisted as ``Session.slug``, wired
            //   into ``helperOptions.agentSlug`` by the shell). Parens
            //   keep it visibly a name and not part of the task.
            //
            // Either can be missing: v1 spawns carry no task name, and the
            // nickname is only known once the subagent's own transcript has
            // been parsed. Nothing to show at all → no summary row.
            const rawTaskName = typeof input?.task_name === 'string' ? input.task_name.trim() : ''
            const taskName = rawTaskName ? humanizeToolSegment(rawTaskName) : ''
            const slug = options?.agentSlug
            const description = [taskName, slug ? `(${slug})` : ''].filter(Boolean).join(' ')
            if (!description) return null
            return {
                component: DescriptionSummary,
                props: { description, fileIconSrc: null, truncate: true },
            }
        }
        if (name === 'update_plan' && isValidPlan(input?.plan)) {
            return {
                component: TodoSummary,
                props: { parts: getTodoDescription(planToTodos(input.plan)) },
            }
        }
        // Image generation: the prompt is the only thing worth a glance
        // (size / quality knobs stay in the detail view).
        if (name === IMAGE_GEN_TOOL_NAME) {
            const prompt = typeof input?.prompt === 'string' ? input.prompt.trim() : ''
            if (!prompt) return null
            return {
                component: DescriptionSummary,
                props: { description: prompt, fileIconSrc: null, truncate: true },
            }
        }
        // Codex Goal tools. ``create_goal`` shows the (truncated) objective;
        // the token_budget is intentionally omitted from the summary (the
        // detail view still shows it). ``update_goal`` shows the changed
        // fields via :class:`GoalUpdateSummary` (see :func:`parseGoalUpdate`).
        if (name === 'create_goal') {
            const objective = typeof input?.objective === 'string' ? input.objective.trim() : ''
            if (!objective) return null
            return {
                component: DescriptionSummary,
                props: { description: objective, fileIconSrc: null, truncate: true },
            }
        }
        if (name === 'update_goal') {
            const parsed = parseGoalUpdate(input)
            if (!parsed) return null
            return {
                component: GoalUpdateSummary,
                props: { status: parsed.status, changedKeys: parsed.changedKeys },
            }
        }
        if (name === 'web_search_call') {
            // ``payload.action`` shape:
            //   - ``{type:"search", query?, queries?}`` — when ``queries``
            //     is set (one or more items), it's the canonical source
            //     and we render the comma-separated list; otherwise fall
            //     back to the single ``query`` string. The summary
            //     component (:class:`WebSearchSummary`) accepts both.
            //   - ``{type:"open_page", url}`` / ``{type:"find_in_page",
            //     url, pattern}`` — render the URL via the same link
            //     summary as Claude Code's WebFetch. We intentionally
            //     ignore ``pattern`` here so find_in_page reads like a
            //     plain page fetch in the summary line (it stays
            //     visible in the body via the JSON view).
            const actionType = input?.type
            if (actionType === 'search') {
                if (Array.isArray(input.queries) && input.queries.length > 0) {
                    return {
                        component: WebSearchSummary,
                        props: { query: input.queries },
                    }
                }
                if (typeof input.query === 'string' && input.query) {
                    return {
                        component: WebSearchSummary,
                        props: { query: input.query },
                    }
                }
                return null
            }
            if (actionType === 'open_page' || actionType === 'find_in_page') {
                if (typeof input.url === 'string' && input.url) {
                    return {
                        component: WebFetchSummary,
                        props: { url: input.url },
                    }
                }
                return null
            }
            return null
        }
        if (name === 'apply_patch') {
            const paths = resolveApplyPatchPaths(input, options)
            if (paths.length === 0) return null
            if (paths.length === 1) {
                return {
                    component: DescriptionSummary,
                    props: {
                        description: formatRelativePath(paths[0], baseDir),
                        fileIconSrc: fileIconFor(paths[0]),
                    },
                }
            }
            // Multi-file: each file gets its own icon + relative path,
            // separated by commas. No truncation — the summary line is
            // free to wrap if needed (like ``WorkingAssistantMessage``
            // does for long status lines).
            return {
                component: MultiFileSummary,
                props: {
                    files: paths.map((p) => ({
                        path: formatRelativePath(p, baseDir),
                        fileIconSrc: fileIconFor(p),
                    })),
                },
            }
        }
        if (!FUNCTION_CALL_EXEC_TOOLS.has(name)) return null
        const parsed = resolveParsedCommand(name, input)
        if (!parsed) return null
        const primary = pickPrimary(mergeStages(parsed))
        if (!primary) return null

        if (primary.type === 'read') {
            const relPath = relPathFromWorkdir(primary.path, input, baseDir)
            // ``query`` is set when ``mergeStages`` paired this read
            // with a downstream search filter (``cat foo | grep bar``).
            // Show the query alongside the path via GrepSummary.
            if (primary.query) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern: primary.query,
                        fileType: null,
                        path: relPath,
                        pathIconSrc: fileIconFor(primary.path),
                    },
                }
            }
            return {
                component: DescriptionSummary,
                props: {
                    description: relPath,
                    fileIconSrc: fileIconFor(primary.path),
                },
            }
        }

        if (primary.type === 'list_files') {
            // No icon for list_files: directories don't have a file-icon
            // mapping and the generic ``default-file`` glyph would be
            // misleading (we'd be claiming the path is a file).
            const relPath = relPathFromWorkdir(primary.path, input, baseDir)
            if (!relPath) return null
            // ``query`` is set when ``mergeStages`` paired this listing
            // with a downstream search filter on file names
            // (``rg --files . | rg PATTERN``). Surface the query
            // through the GrepSummary layout.
            if (primary.query) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern: primary.query,
                        fileType: null,
                        path: relPath,
                        pathIconSrc: null,
                    },
                }
            }
            return {
                component: DescriptionSummary,
                props: { description: relPath, fileIconSrc: null },
            }
        }

        if (primary.type === 'search') {
            const relPath = primary.path ? relPathFromWorkdir(primary.path, input, baseDir) : null
            return {
                component: GrepSummary,
                props: {
                    pattern: primary.query ?? null,
                    fileType: null,
                    path: relPath,
                    pathIconSrc: primary.path ? fileIconFor(primary.path) : null,
                },
            }
        }

        // Unknown / fallback: show the first line of the raw command,
        // forced to a single ellipsis-truncated row (the script may be
        // arbitrarily long and shouldn't wrap into the next line).
        const inline = firstLine(primary.cmd)
        if (!inline) return null
        return {
            component: DescriptionSummary,
            props: { description: inline, fileIconSrc: null, truncate: true },
        }
    }

    getInputRendering(name, input, ctx) {
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            // Tier-1 apply_patch: the extracted envelope feeds the same
            // diff renderer as a direct apply_patch call. Tier-1
            // exec_command and tiers 2/3 return null — their body goes
            // through ``getDisplayInputObject`` (extracted ``{cmd}`` as a
            // bash block, or the raw JS source).
            const nested = resolveCodeModeCall(input)
            if (nested?.name === 'apply_patch') {
                return this.getInputRendering('apply_patch', { input: nested.arg }, ctx)
            }
            if (nested?.name === VIEW_IMAGE_TOOL_NAME) {
                return this.getInputRendering(VIEW_IMAGE_TOOL_NAME, nested.arg, ctx)
            }
            if (nested?.name === 'update_plan') {
                return this.getInputRendering('update_plan', nested.arg, ctx)
            }
            return null
        }
        if (name === 'apply_patch') {
            const raw = typeof input === 'string' ? input : input?.input
            if (typeof raw !== 'string' || !raw) return null
            return {
                component: ApplyPatchContent,
                props: {
                    input: raw,
                    sessionId: ctx?.sessionId ?? '',
                    toolId: ctx?.toolId ?? '',
                    isSubagent: !!ctx?.isSubagent,
                },
            }
        }
        if (name === 'update_plan' && isValidPlan(input?.plan)) {
            return {
                component: TodoContent,
                props: {
                    todos: planToTodos(input.plan),
                    explanation: typeof input.explanation === 'string' && input.explanation
                        ? input.explanation
                        : null,
                },
            }
        }
        return null
    }

    getResultRendering(name, result, input, options) {
        // ``spawn_agent`` is rendered from the synthetic envelope
        // produced by :meth:`transformDisplayResult` above. We dispatch
        // on the AgentStatus variant to surface the best body we have:
        //   - ``Completed(msg)`` / ``Errored(msg)`` -> the message body
        //     (markdown via ``SpawnAgentResult``);
        //   - status-only variants (``shutdown`` / ``not_found``) ->
        //     a short label, no body.
        if (isSpawnAgentTool(name) && result?.__spawnAgentResult) {
            const status = result.status
            let message = null
            let statusLabel = null
            if (status && typeof status === 'object') {
                if (typeof status.completed === 'string') {
                    message = status.completed
                } else if (typeof status.errored === 'string') {
                    message = status.errored
                }
            } else if (status === 'shutdown') {
                statusLabel = 'Subagent was shut down before producing a final message.'
            } else if (status === 'not_found') {
                statusLabel = 'Subagent reference was not found.'
            }
            if (!message && !statusLabel) return null
            return {
                component: SpawnAgentResult,
                props: { message, statusLabel, nickname: result.nickname },
            }
        }
        // ``view_image``: render the image(s) the tool fed back to the
        // model (carried inline as base64 ``input_image`` data URLs)
        // instead of the raw ``function_call_output`` JSON.
        if (name === VIEW_IMAGE_TOOL_NAME) {
            const images = extractInputImageUrls(result)
            if (images.length === 0) return null
            // Title for the preview dialog — the input path basename.
            let imageName = 'Image'
            const path = input?.path
            if (typeof path === 'string' && path) {
                const idx = path.lastIndexOf('/')
                imageName = idx >= 0 ? path.slice(idx + 1) : path
            }
            return {
                component: ViewImageResult,
                props: { images, name: imageName },
            }
        }
        // ``image_gen__imagegen``: same wire shape as ``view_image`` — the
        // output is a list of parts: one or more ``input_image`` data URLs
        // surrounded by ``input_text`` parts (the leading script status header
        // when wrapped in code mode, the trailing "saved to <path>" notice). Render the
        // picture rather than the megabytes of base64 JsonHumanView would
        // print; the saved path is already on the adjacent ``image`` card.
        // Kept behind the Result disclosure (not inline): the generated
        // image has its own ``image`` card right after this one.
        if (name === IMAGE_GEN_TOOL_NAME) {
            const images = extractInputImageUrls(result)
            if (images.length === 0) return null
            return {
                component: ViewImageResult,
                props: { images, name: 'Generated image' },
            }
        }
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            // Tier-1 exec_command: delegate to the direct path so a
            // ``cat``-classified script gets the same ReadResultContent
            // treatment (the aggregate in ``options`` was already built
            // by ``aggregateCodeModeOutput``, same shape).
            const nested = resolveCodeModeCall(input)
            if (nested?.name === 'exec_command') {
                return this.getResultRendering('exec_command', result, nested.arg, options)
            }
            // Tier-1 view_image: the outer custom_tool_call_output carries
            // the same ``input_image`` segments as the direct tool's
            // function_call_output. Delegate before the generic code-mode
            // aggregation path, which only understands text segments and
            // would otherwise render an empty ExecResultContent.
            if (nested?.name === VIEW_IMAGE_TOOL_NAME) {
                return this.getResultRendering(VIEW_IMAGE_TOOL_NAME, result, nested.arg, options)
            }
            // Tier-1 image generation: same ``input_image`` parts in the
            // outer custom_tool_call_output — same delegation. No image yet
            // (``Script running`` window, ``Script failed``): fall through to
            // the aggregated script output below rather than the raw rows.
            if (nested?.name === IMAGE_GEN_TOOL_NAME) {
                const rendering = this.getResultRendering(IMAGE_GEN_TOOL_NAME, result, nested.arg, options)
                if (rendering) return rendering
            }
            // Tier-1 MCP with its rebound end event in the chain:
            // ``transformDisplayResult`` already unwrapped the payload
            // into ``result`` — return null so the default JsonHumanView
            // renders it, exactly like a direct MCP call. Without the
            // event (still running, script died before calling, stale
            // pre-38 compute) fall through to the aggregated script
            // output below.
            if (
                nested?.name?.startsWith(MCP_TOOL_NAME_PREFIX)
                && Array.isArray(options?.resultsArray)
                && options.resultsArray.some((row) => row?.type === 'McpToolCall')
            ) {
                return null
            }
            const aggregated = options?.aggregatedExecOutput
            if (!aggregated || typeof aggregated.aggregatedOutput !== 'string') return null
            if (!aggregated.aggregatedOutput && !aggregated.isTerminated) return null
            return {
                component: ExecResultContent,
                props: { result: { aggregated_output: aggregated.aggregatedOutput } },
            }
        }
        if (!FUNCTION_CALL_EXEC_TOOLS.has(name)) return null
        // The shell precomputed the chain aggregate when
        // :meth:`shouldAggregateExecOutput` returned ``true``; reach
        // for it through ``options.aggregatedExecOutput``. ``_result``
        // (the raw row from ``displayResult``) is unused here — for
        // long-running shells it's just one chunk among many, and for
        // synchronous one-shots the aggregator already collapsed it.
        const aggregated = options?.aggregatedExecOutput
        if (!aggregated || typeof aggregated.aggregatedOutput !== 'string') return null
        if (!aggregated.aggregatedOutput && !aggregated.isTerminated) return null

        // ``read``-classified calls get the same treatment as Claude
        // Code's Read tool: try to extract ``cat -n`` / ``nl -ba``
        // line numbers, surface them as a "Lines X–Y" header, and
        // colour the code by the file's extension. The
        // tree-sitter-bash ``parsed_cmd`` Codex used to surface on
        // ``exec_command_end`` is gone, so the local ``parseCommand``
        // estimate is now the only source.
        const stages = parseCommand(extractCommandPayload(name, input))
        const primary = pickPrimary(mergeStages(stages || []))
        if (primary?.type === 'read' && primary.path) {
            return {
                component: ReadResultContent,
                props: {
                    aggregatedOutput: aggregated.aggregatedOutput,
                    path: primary.path,
                },
            }
        }

        // ``ExecResultContent`` originally received the raw
        // ``exec_command_end`` payload as ``result``; we hand it a
        // synthetic object with the same ``aggregated_output`` shape so
        // the component itself doesn't need to know the source changed.
        return {
            component: ExecResultContent,
            props: { result: { aggregated_output: aggregated.aggregatedOutput } },
        }
    }

    showsResultOnError(name) {
        // Same rationale as Claude Code's Bash: the error callout only
        // surfaces a short label (``Exit code N`` / ``Patch failed``),
        // so the rich event payload (stdout, stderr, applied changes)
        // is still useful and should stay visible.
        if (name === 'apply_patch') return true
        // ``update_plan`` errors out when called in Plan mode — the
        // attempted plan is still informative, keep the body shown.
        if (name === 'update_plan') return true
        // MCP tools surface a generic ``Tool error`` label that doesn't
        // tell the user anything actionable — keep the rich result body
        // visible so they can see what the server actually returned.
        if (typeof name === 'string' && name.startsWith(MCP_TOOL_NAME_PREFIX)) return true
        // Code-mode ``exec``: a ``Script failed`` error label says nothing
        // about what the script printed before dying — keep the body.
        if (name === CODE_MODE_EXEC_TOOL_NAME) return true
        return FUNCTION_CALL_EXEC_TOOLS.has(name)
    }

    /**
     * Pull the rich body out of an MCP call's result rows.
     *
     * Codex emits two ToolResultLinks per MCP call: the LLM-facing
     * `function_call_output` (text trailer) and the structured
     * canonical `McpToolCall` item carrying the actual server payload.
     * The latter is strictly richer, so we surface it instead of the
     * raw two-row dump JsonHumanView would otherwise produce — see
     * :func:`mcpEndDisplayResult` for the unwrap rules. Applies both to
     * direct MCP `function_call`s and to code-mode ``exec``s wrapping a
     * single MCP call.
     */
    transformDisplayResult(name, resultData, options) {
        // ``spawn_agent`` collects up to two ToolResultLinks:
        //   1. the immediate ``function_call_output`` ack
        //      ``{agent_id, nickname}`` (always present on a successful
        //      spawn, replaced by a freeform rejection string on a
        //      failed spawn);
        //   2. a synthetic link rebound from the
        //      ``<subagent_notification>`` user message Codex injects
        //      when the subagent finalises (only present on success,
        //      and only after the subagent has actually finished).
        // We render the second when available — that's where the
        // subagent's actual output lives. The ack is only useful to
        // pick up the nickname for the meta line. When no notification
        // is in yet (still running) or on a failed spawn (only the
        // rejection text), we return ``undefined`` so the default
        // ``JsonHumanView`` renders the raw row — the standard error
        // callout already surfaces the failure message.
        if (isSpawnAgentTool(name)) {
            if (!Array.isArray(resultData)) return undefined
            let notif = null
            let ack = null
            for (const row of resultData) {
                if (notif === null) {
                    const candidate = extractSubagentNotificationBody(row)
                    if (candidate) notif = candidate
                }
                if (ack === null) {
                    const candidate = extractSpawnAckBody(row)
                    if (candidate) ack = candidate
                }
            }
            if (!notif) return undefined
            return {
                __spawnAgentResult: true,
                status: notif.status,
                nickname: ack?.nickname ?? null,
                agentId: ack?.agentId ?? notif.agentPath,
            }
        }
        // ``create_goal`` / ``update_goal`` (Codex Goal tools): the
        // ``function_call_output.output`` is itself a JSON document (the
        // goal state ``{goal, remainingTokens, completionBudgetReport}``).
        // Surface its parsed content so the default JsonHumanView renders
        // the goal payload directly instead of the ``{type, call_id,
        // output}`` envelope with ``output`` shown as an opaque JSON
        // string. ``get_goal`` never reaches here — it's DEBUG_ONLY upstream.
        if (name === 'create_goal' || name === 'update_goal') {
            if (!Array.isArray(resultData)) return undefined
            const row = resultData.find((r) => r?.type === 'function_call_output')
            const out = row?.output
            // Already an object (defensive — a future Codex build could
            // ship real JSON instead of a JSON-encoded string).
            if (out && typeof out === 'object') return out
            if (typeof out !== 'string' || !out) return undefined
            try {
                const parsed = JSON.parse(out)
                if (parsed && typeof parsed === 'object') return parsed
            } catch {
                // Not JSON after all — fall back to the default rendering
                // (raw row, ``output`` shown as a plain string).
            }
            return undefined
        }
        // Code-mode ``exec`` wrapping a single MCP call: the backend
        // rebound the nested ``McpToolCall`` (synthesized
        // ``exec-<uuid>`` call_id) onto this exec's chain — surface it
        // exactly like a direct MCP call would (the script's own output
        // only repeats the same payload as an opaque JSON string). When
        // the event hasn't arrived (still running, or the script died
        // before calling), fall through to the default so the shell
        // keeps the aggregated script-output path.
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            const nested = resolveCodeModeCall(options?.input)
            if (nested?.name?.startsWith(MCP_TOOL_NAME_PREFIX)) {
                return mcpEndDisplayResult(resultData)
            }
            return undefined
        }
        if (typeof name !== 'string' || !name.startsWith(MCP_TOOL_NAME_PREFIX)) {
            return undefined
        }
        return mcpEndDisplayResult(resultData)
    }

    getInputOverrides(name, input) {
        // Tier-1 MCP through code-mode ``exec``: the displayed object is
        // the MCP call's arguments, so the exec overrides (``cmd`` →
        // bash block, ``input`` → JS block) must not capture same-named
        // MCP argument keys.
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            const nestedName = resolveCodeModeCall(input)?.name
            if (
                nestedName?.startsWith(MCP_TOOL_NAME_PREFIX)
                || nestedName === 'web__run'
                || nestedName === IMAGE_GEN_TOOL_NAME
            ) {
                return {}
            }
        }
        return INPUT_OVERRIDES[name] ?? {}
    }

    getDisplayInputObject(name, input) {
        if (!input || Object.keys(input).length === 0) return null
        // ``apply_patch`` only has the raw v4a envelope as input; the
        // ``ApplyPatchContent`` renderer takes over the full body, so
        // there's nothing useful left for the JSON fallback.
        if (name === 'apply_patch') return null
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            const nested = resolveCodeModeCall(input)
            // Tier-1 apply_patch: ApplyPatchContent (via getInputRendering)
            // renders the whole body — same rule as direct apply_patch.
            if (nested?.name === 'apply_patch') return null
            // Tier-1 exec_command: swap the JS wrapper for the extracted
            // command so the card body shows a bash block (see the
            // ``cmd`` entry in INPUT_OVERRIDES.exec). The internal knobs
            // (yield_time_ms, …) and the full JS source stay reachable
            // through the ``</>`` raw toggle.
            if (nested?.name === 'exec_command') return { cmd: nested.arg.cmd }
            // Tier-1 view_image: replace the JavaScript wrapper with the
            // semantic tool arguments, matching the direct tool card.
            if (nested?.name === VIEW_IMAGE_TOOL_NAME) return nested.arg
            // Tier-1 update_plan: TodoContent owns the complete input body.
            if (nested?.name === 'update_plan') return null
            // Tier-1 web__run: show the semantic web arguments instead of
            // the JavaScript transport wrapper. The response still renders
            // through the normal code-mode result aggregation path.
            if (nested?.name === 'web__run') return nested.arg
            // Tier-1 image generation: the prompt and its knobs, like the
            // direct call's input.
            if (nested?.name === IMAGE_GEN_TOOL_NAME) return nested.arg
            // Tier-1 MCP: show the call's arguments object like a direct
            // MCP call does (nothing when the call takes none — the full
            // JS source stays reachable through the raw toggle).
            if (nested?.name?.startsWith(MCP_TOOL_NAME_PREFIX)) {
                const arg = nested.arg
                return arg && Object.keys(arg).length > 0 ? arg : null
            }
            // Tiers 2/3 fall through: ``{ input: <JS source> }`` rendered
            // as a fenced JS block by INPUT_OVERRIDES.exec.input.
        }
        // ``update_plan`` is fully rendered by ``TodoContent`` (plan +
        // explanation), so the JSON fallback would only duplicate it.
        if (name === 'update_plan') return null
        const stripped = STRIPPED_INPUT_KEYS_BY_TOOL[name]
        const out = stripped && stripped.size > 0 ? {} : { ...input }
        if (stripped && stripped.size > 0) {
            for (const k of Object.keys(input)) {
                if (!stripped.has(k)) out[k] = input[k]
            }
        }
        // ``web_search_call`` special case: Codex sometimes ships both
        // ``query`` (string) and ``queries`` (array) carrying the same
        // single value — showing both is pure duplication. Normalise to
        // a single field based on how many distinct queries the call
        // actually issued: a single query (or none) collapses to
        // ``query`` (string); multiple queries keep ``queries`` and drop
        // the redundant string. The summary helper still privileges
        // ``queries`` so the array form drives the rendered list.
        if (name === 'web_search_call') {
            const queries = Array.isArray(out.queries)
                ? out.queries.filter((q) => typeof q === 'string' && q)
                : null
            if (queries) {
                if (queries.length >= 2) {
                    out.queries = queries
                    delete out.query
                } else {
                    if (queries.length === 1 && (typeof out.query !== 'string' || !out.query)) {
                        out.query = queries[0]
                    }
                    delete out.queries
                }
            }
        }
        return Object.keys(out).length > 0 ? out : null
    }

    rendersResultInline(name, input) {
        // ``view_image``'s result is the image itself — show it directly in
        // the card body (in place of the "Result" disclosure) and fetch it
        // as soon as the card opens, rather than hiding it behind an extra
        // click. The ``{path, detail}`` input JSON still renders above.
        if (name === VIEW_IMAGE_TOOL_NAME) return true
        return name === CODE_MODE_EXEC_TOOL_NAME
            && resolveCodeModeCall(input)?.name === VIEW_IMAGE_TOOL_NAME
    }

    isFileChangeTool(name) {
        return name === 'apply_patch'
    }

    isAgentTool(name) {
        // Activates the shared agent-spawn UI on the tool card: a
        // spinner before the spawn ack lands, then a ``View Agent``
        // button (with a pulsing robot while the subagent is still
        // running). The Stop button on the same card is gated by the
        // provider-level ``CodexHelpers.canStopSubagent`` (defined in
        // ``../helpers.js``) since the stop plumbing belongs to the
        // provider, not to a specific tool name.
        return isSpawnAgentTool(name)
    }

    agentRunEndsOnSubagentIdle() {
        // Multi-agent v2 has no reliable "the subagent finished" signal in the
        // parent thread: the `FINAL_ANSWER` message that pairs as the spawn's
        // second result only exists when the subagent ends its turn with a
        // final answer. One that reports through `send_message` and stays
        // alive (Codex still lists it as `running` — an agent is "running"
        // until closed) would otherwise pulse forever. Its own transcript is
        // authoritative instead: `task_complete` ends the turn, which the
        // backend maps onto `Session.last_stopped_at`.
        return true
    }

    getDisplayName(name, input) {
        // Header label for the spawn_agent tool card. The user-facing
        // pattern, agreed with the user:
        //   - missing or ``"default"`` agent_type → ``Agent`` (a
        //     bare ``"Default"`` would be confusing — the role name
        //     leaks an implementation detail with no semantic value)
        //   - any other value (built-in ``explorer`` / ``worker`` or
        //     a user-defined role from ``[agents]`` / ``agents/*.toml``)
        //     → the value itself, capitalised.
        if (!isSpawnAgentTool(name)) return null
        const rawType = typeof input?.agent_type === 'string' ? input.agent_type.trim() : ''
        if (!rawType || rawType === 'default') {
            return { name: 'Agent', namespace: null }
        }
        return { name: capitalize(rawType), namespace: null }
    }

    computeToolSummary(name, input, _baseDir) {
        // The shell renders ``displayName.name`` in the card header
        // when ``isTask && displayName`` (see ToolUseContent.vue) —
        // which is exactly the path activated for spawn_agent via
        // ``isAgentTool``. Other Codex tools fall back to the inline
        // ``headerLabel`` / ``getSummaryRendering`` machinery and have
        // no use for a structured ``displayName``, so we keep the
        // base stub for everything else.
        const displayName = this.getDisplayName(name, input || {})
        return { displayName, inline: null }
    }

    getFilePath(name, input) {
        // Code-mode script wrapping a single-file apply_patch: surface the
        // path so the shell shows the same ``View in Files tab`` button as
        // a direct apply_patch.
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            const nested = resolveCodeModeCall(input)
            if (nested?.name !== 'apply_patch') return null
            return this.getFilePath('apply_patch', { input: nested.arg })
        }
        if (name !== 'apply_patch') return null
        const parsed = parseApplyPatchEnvelope(typeof input === 'string' ? input : input?.input)
        // Single-file: surface the path so the shell shows a
        // ``View in Files tab`` button next to the summary, like Edit
        // / Write. Multi-file: bail — a single button can only point
        // to one path, so each per-file header inside the body adds
        // its own button instead.
        if (parsed.length !== 1) return null
        return parsed[0]?.path ?? null
    }

    shouldAutoOpenLive(name, input) {
        // Same UX as Claude Code's Edit / Write: when the user has
        // ``showDiffs`` enabled, an apply_patch tool_use that arrives
        // live is auto-expanded so the diff is visible without a click.
        // A code-mode script wrapping a single apply_patch is the same
        // user-facing operation, so it auto-opens too.
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            return resolveCodeModeCall(input)?.name === 'apply_patch'
        }
        return name === 'apply_patch'
    }

    computeFileChangeStats(name, input, toolState, _isSubagent) {
        // Nested code-mode patch: the stats can't travel through
        // ``ToolResultLink.extra`` (that slot drives the exec spinner's
        // ``is_terminated`` and the tool_state aggregates a single
        // value), so derive them client-side from the extracted
        // envelope — the same counts the v4a parser feeds the card body.
        if (name === CODE_MODE_EXEC_TOOL_NAME) {
            const nested = resolveCodeModeCall(input)
            if (nested?.name !== 'apply_patch') return null
            const files = parseApplyPatchEnvelope(nested.arg)
            if (files.length === 0) return null
            let linesAdded = 0
            let linesRemoved = 0
            const perFile = []
            for (const file of files) {
                const added = file.linesAdded ?? 0
                const removed = file.linesRemoved ?? 0
                linesAdded += added
                linesRemoved += removed
                perFile.push({ path: file.path, lines_added: added, lines_removed: removed })
            }
            return { lines_added: linesAdded, lines_removed: linesRemoved, files: perFile }
        }
        if (name !== 'apply_patch') return null
        if (!toolState?.extra) return null
        try {
            return JSON.parse(toolState.extra)
        } catch {
            return null
        }
    }
}

export const codexToolHelpers = new CodexToolHelpers()
