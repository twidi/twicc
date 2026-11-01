# Codex Code Mode (GPT-5.6) — Tool-Call Display Design

Status: implemented (2026-07-10) — extractor mirrors `code_mode_script.py` / `parseCodeModeScript.js`, compute wiring in `compute.py` (CODEX_COMPUTE_VERSION 37), frontend routing in `toolHelpers.js`, tests in `tests/test_codex_code_mode.py`.
Scope: display/classification of Codex "code mode" tool calls in TwiCC. No approval changes, no live-stream changes.

## 1. Problem

GPT-5.6 Codex models (`gpt-5.6-sol/terra/luna`) run in **code mode only** (`tool_mode: "code_mode_only"`, served by the backend `/models` catalog). Classic direct tools (`shell`, `exec_command`, `apply_patch`, MCP tools) are no longer exposed to the model. Instead the model calls:

- **`exec`** — a `custom_tool_call` whose `input` is raw JavaScript source, executed in a V8 isolate by the CLI (`codex-rs/code-mode/`). The JS calls nested tools on a global `tools` object: `await tools.exec_command({cmd, workdir, ...})`, `await tools.apply_patch(patchString)`, `await tools.mcp__server__tool({...})`, and emits results via `text(...)` / `image(...)`.
- **`wait`** — a `function_call` (`{cell_id, yield_time_ms, max_tokens, terminate}`) that resumes a still-running script "cell" and returns its new output.

Every action goes through this wrapper — even a bare `ls` is a one-line JS script. TwiCC currently renders these as an opaque "Run code" card with the JS in a code block: no command classification (read/search/edit labels), no diff rendering for wrapped patches, raw unparsed output, and `wait` renders as a visible generic tool card.

### Why the CLI doesn't have this problem

The Codex TUI never parses or displays the JS. Each nested call re-enters the ordinary tool router (`call_nested_tool`, `codex-rs/core/src/tools/code_mode/mod.rs`) with a synthesized `call_id` (`exec-<uuid>`), and emits the same live events as a direct call (`ExecCommandBegin/End` with `parsed_cmd`, `PatchApply*`, item `commandExecution`/`fileChange`). The TUI renders those; the outer `exec`/`wait` produce no visible cell at all.

TwiCC syncs from the **rollout JSONL**, where almost none of that is persisted (`codex-rs/rollout/src/policy.rs`):

- persisted: the `exec` `custom_tool_call` (raw JS) + its `custom_tool_call_output`, the `wait` `function_call` + output, and `event_msg.patch_apply_end` (with structured `changes`);
- never persisted: `ExecCommandBegin/End` (End only in non-default Extended mode), `PatchApplyBegin`, approval requests, thread items.

So the only rollout-side source for "what did this script do" is **the JS itself** (plus `patch_apply_end` for edits).

### What is NOT affected

- **Approvals**: emitted by the nested handlers with structured payloads (`item/commandExecution/requestApproval` with `command`/`commandActions`/`cwd`, `item/fileChange/requestApproval` with `changes`), independent of the JS wrapper. Our approvals bridge (`providers/codex/agent/approvals.py`) already works, verified live.
- **The live agent path** (`agent/agent.py`): keys on semantic item types (`commandExecution`, `fileChange`), unaffected by the wire change.
- **Pre-5.6 sessions**: never emit `custom_tool_call name="exec"` nor `function_call name="wait"`. All detection below is **shape-based** (payload sub-type + tool name), never model-version-based; existing code paths stay untouched.

## 2. Observed script shapes

The tool description (`codex-rs/code-mode/src/description.rs`) allows arbitrary JS (loops, `store`/`load`, `setTimeout`, `notify`, `yield_control`, multiple calls per script). In practice the 5.6 system prompt still instructs the model in terms of `exec_command`/`apply_patch`, and observed scripts (real session + codex-rs test fixtures) are thin stereotyped wrappers:

```js
const r = await tools.exec_command({"cmd":"...","workdir":"...","yield_time_ms":10000,"max_output_tokens":2000});
text(r.output);
```

```js
const patch = "*** Begin Patch\n*** Add File: ...\n*** End Patch";
const r = await tools.apply_patch(patch);
text(typeof r === "string" ? r : JSON.stringify(r));
```

Known variants: first-line pragma `// @exec: {"yield_time_ms":...}`; JS object literals with unquoted keys (`{ cmd: "..." }`); `text(JSON.stringify(await tools.exec_command({...})))` (call nested in an expression); polling loops (`while ((await tools.exec_command({...})).output !== "ready") {}`).

Output shape (`format_script_status` / `prepend_script_status`, `code_mode/mod.rs`): `custom_tool_call_output.output` is either a plain string or an array of `{type: "input_text", text}` segments. The **first** segment is always a status header:

```
<status>
Wall time <X.X> seconds
Output:
```

where `<status>` is exactly one of `Script completed`, `Script failed`, `Script terminated`, `Script running with cell ID <id>`. On failure, an extra segment `Script error:\n<message>` is appended. The remaining segments are the script's `text(...)`/`image(...)` output. A `Script running…` status means the real result arrives later through one or more `wait` calls (same header format on the wait's `function_call_output`).

## 3. Goals / non-goals

Goals:

1. A script wrapping a single resolvable `exec_command` renders like a shell command today: command shown, `parseCommand` heuristic label (Read / Grep / List files / Exec), output body, exit/status handling.
2. A script wrapping a single resolvable `apply_patch` renders like an edit today: `ApplyPatchContent` diff, "Edit" label, doc-edit/plan detection.
3. Multi-call or unresolvable scripts degrade gracefully: "Run code" card with the JS source, plus a summary of the detected nested tools.
4. `wait` calls are invisible (like the CLI) and their outputs chain onto the owning `exec` call (like `write_stdin` chains onto `exec_command` today).
5. Script status/output is parsed (status header, error detection) for both string and array output shapes.
6. Zero behavioral change for pre-code-mode sessions.

Non-goals (deferred):

- Consuming the live `commandExecution`/`fileChange` item stream to enrich TwiCC-driven sessions (nice-to-have later; doesn't cover imported sessions or recompute, so it cannot be the base layer).
- Executing the JS (no JS engine server-side, ever).
- Rendering `store`/`load`/`notify`/`setTimeout` semantics.

## 4. Detection

- **Code-mode script**: payload sub-type `custom_tool_call` AND `payload.name == "exec"`. MCP tools are always `mcp__`-prefixed, and the only historical `custom_tool_call` is `apply_patch`, so the bare name `exec` is unambiguous.
- **Code-mode wait**: sub-type `function_call` AND `payload.name == "wait"`. No historical Codex tool uses this name (`wait_agent` is distinct).

No version/model gating anywhere.

## 5. The static script extractor (shared heuristic)

New mirrored module, one per side, same contract — following the existing `parse_command.rs` ↔ `parseCommand.js` precedent:

- `frontend/src/providers/codex/parseCodeModeScript.js`
- `src/twicc/providers/codex/code_mode_script.py`

### 5.1 Contract

```
parseCodeModeScript(source: string) -> {
  calls: [ { name: string,            // nested tool name, e.g. "exec_command"
             arg: any | null,          // statically resolved argument value
             resolved: boolean } ],    // arg extraction succeeded
  pragma: object | null,               // parsed // @exec: {...} first line, if any
}
```

`calls` preserves source order. The consumer classifies:

- **tier 1 — single resolved call**: `calls.length === 1 && calls[0].resolved` → route to the dedicated rendering for that nested tool;
- **tier 2 — detected but not fully resolved** (multiple calls, or unresolved args): generic "Run code" rendering enriched with the call list;
- **tier 3 — nothing detected**: current behavior (JS code block).

### 5.2 Extraction rules

Not a regex over the whole source — a small scanner with string-awareness:

1. Strip an optional first-line pragma `// @exec: {...}` (JSON-parse the payload; keep for display).
2. Scan for `tools.<identifier>(` occurrences **outside string literals** (the scanner tracks `'`, `"`, backtick strings with escape handling, and line/block comments). An optional preceding `await` is irrelevant to extraction.
3. For each occurrence, capture the argument span by balanced-parenthesis scan (same string-awareness).
4. Resolve the argument to a value:
   - **string literal** (any quote style; template literal only if it contains no `${`) → the string value;
   - **literal object/array** → parsed by a small recursive-descent literal parser: strings, numbers, booleans, `null`, arrays, nested objects, identifier keys (unquoted), trailing commas. Any non-literal token (identifier value, call, `${}`, spread) → unresolved;
   - **single identifier** → resolved through a one-pass `const <id> = <string-literal-or-concat>` table built from the source (string literals joined by `+` allowed). Only `const` string bindings; anything else → unresolved;
   - anything else → unresolved (`arg: null, resolved: false`).

Failure of any step degrades to tier 2/3, never throws. The extractor is pure and cheap (single pass + bounded rescans), safe for the compute hot path.

### 5.3 Nested tools with dedicated handling

- `exec_command` — arg object with `cmd` (string), optional `workdir`. `cmd` feeds the existing shell heuristics.
- `apply_patch` — arg is the patch envelope string (v4a `*** Begin Patch` grammar), feeds the existing patch parsing.
- `update_plan` — arg object feeds the existing Todo summary and detail renderers; the backend independently recovers the same call for `Session.tasks`.
- `web__run` — arg object is classified as search-only, navigation-only, or compound/hosted-data and rendered as Web search, Web fetch, or Web while preserving the returned content.
- `mcp__*` — recognized and listed in summaries (tier 2); dedicated MCP rendering for tier 1 is optional polish, not required initially.
- `write_stdin` — a single resolved wrapper with an integer `session_id` is invisible and rebound to the owning `exec_command`, including a transitive `wait` when the wrapper itself outlives its code cell.
- everything else (`view_image`, unresolved/multi-tool `write_stdin` scripts, …) — listed by name only.

## 6. Backend changes (`src/twicc/providers/codex/compute.py`)

### 6.1 Output normalization + status parsing

New helper `parse_code_mode_output(output) -> CodeModeOutputStatus` handling both shapes:

- string → header is the leading lines;
- array of `{type: "input_text", text}` → header is the first segment; body is the remaining text segments joined (non-text segments preserved for display counts).

Parsed fields: `status` (`completed` / `failed` / `terminated` / `running`), `cell_id` (from `Script running with cell ID <id>`), `wall_time_seconds`, `error_text` (from a `Script error:\n…` segment), `body`.

Wired into `extract_tool_result_info`: the current `else: error_text = None` branch for non-string outputs (~line 2113) learns the array shape; `Script failed` → error with `error_text`. Note: a nested command that exits non-zero does **not** fail the script — script-level status is all we can claim from the wrapper; per-command exit codes are only recoverable when the canonical wrapper echoed the nested output verbatim (best-effort, see Open questions).

### 6.2 `wait` chaining (mirror of the `write_stdin` machinery)

`wait` becomes the code-mode analog of `write_stdin`:

- add `"wait"` to `_NON_TOOL_FUNCTION_NAMES` (SYSTEM bucket — no visible card) while keeping it in the pairing path;
- new map `_code_cell_maps: {session_id: {cell_id: exec_call_id}}` populated by `analyze_content` when an `exec` output reports `Script running with cell ID <id>`;
- `remap_tool_result_id` (+ `remap_tool_result_id_live` with its DB-backed equivalent) rebinds a `wait` `function_call_output` to the owning `exec` call via the wait's `arguments.cell_id`; eviction when the chained output reports a final status (`completed`/`failed`/`terminated`);
- `compute_link_extra` gains the code-mode branch: `is_terminated` false while the last chained output is `running`, true on a final status — the existing spinner logic then works unchanged.

This exactly reuses the chain design already proven for `exec_command`/`write_stdin`; `exec` joins a new `_CODE_MODE_TOOLS`-style constant rather than `_SHELL_FAMILY_TOOLS` (its input is JS, not a shell command — keeping the sets separate avoids accidental reuse of shell-only paths).

The same chain also collapses a canonical JavaScript `write_stdin` wrapper onto the JavaScript `exec_command` that printed `SESSION_ID=<id>`. If the `write_stdin` wrapper reports `Script running with cell ID <id>`, the later native `wait` output is rebound transitively to that original shell card rather than to the invisible intermediate wrapper.

### 6.3 Derived task and plan-document metadata

`extract_tasks_payload` also runs the script extractor for code-mode `exec`
calls. When exactly one `tools.update_plan({...})` call has statically resolved
object arguments, its plan becomes the same last-wins `Session.tasks` snapshot
as a native pre-5.6 `function_call`. The surrounding script may contain other
nested tools (the common GPT-5.6 shape); repeated, dynamic, or malformed
`update_plan` calls are ignored because static analysis cannot establish the
executed state safely. This metadata extraction does not change the wrapper's
display tier or tool-result pairing.

`extract_doc_edit_events` gains an `exec` branch: run the extractor on `payload.input`;

- resolved `exec_command` call → feed `arg.cmd` through the same command-based detection as `_DOC_EDIT_SHELL_COMMAND_KEYS` tools;
- resolved `apply_patch` call → parse the patch envelope for target paths (same as the direct `custom_tool_call` apply_patch path).

`extract_paths_from_tool_uses` needs no change: it already reads `patch_apply_end.changes` directly, pairing-independent — and `patch_apply_end` is still persisted for JS-wrapped patches (verified in the real session), so edits are covered even when script extraction fails.

### 6.4 Kind / display-level

- `exec` calls stay TOOL_USE (visible card) — no change to `compute_item_kind` for them.
- `wait` calls → SYSTEM via §6.2.
- Recompute: bump `CURRENT_COMPUTE_VERSION` (chaining, error info and doc-edit events must be recomputed for existing 5.6 sessions).

### 6.5 Orphan `patch_apply_end` — heuristic pairing (decision reversed 2026-07-10)

For JS-wrapped patches, `patch_apply_end.call_id` is the nested `exec-<uuid>`, which matches **no** rollout `call_id`. The event is the richest edit artifact we have (structured `changes`, live-captured `original_files` splice — the capture/splice chain still works in code mode: the live `fileChange` item id IS the nested `exec-<uuid>`, verified on the test session) — leaving it orphaned costs the full-file diff and canonical paths on the card. Initial decision was to not pair; reversed on user request for full 5.5 display parity.

Pairing is heuristic, via `_remap_orphan_patch_apply_end` (batch) / `_lookup_patch_exec_call_id` (live), gated on the `exec-` call_id prefix + event shape:

1. `analyze_content` registers every code-mode `exec` whose script declares a nested `apply_patch` (`_patch_exec_maps`: call_id + envelope-declared paths, suffix-matched since envelope paths may be relative);
2. an orphan event binds to the most recent registered exec whose declared paths match the event's `changes`, else to the most recent registered exec (unresolvable-envelope fallback).

The paired link rides the exec's chain with `extra=None` (`compute_link_extra` only reacts to response_item rows for `exec`, so the spinner's `is_terminated` aggregation is untouched); patch failure text flows through `_event_msg_payload_error` like a direct apply_patch. Frontend: `ApplyPatchContent` accepts the `exec-`-prefixed `call_id` on the paired event (the lookup is already scoped to the tool's own link chain) and derives per-file +/- counts from the change entries when the backend stats extra is absent; the header badge comes from `computeFileChangeStats`'s envelope-based branch in `toolHelpers.js`.

## 7. Frontend changes

### 7.1 Routing in `toolHelpers.js`

All `name === 'exec'` branches call the extractor once (memoized per item, same pattern as other parsed content):

- **tier 1, `exec_command`**: header label via `parseCommand(arg.cmd)` exactly like `exec_command` today; `getSummaryRendering` shows the summarized command; `getInputRendering` shows the command (bash block) with the JS source available as a collapsed/secondary block; result rendering uses the code-mode output parsing (§7.2). `arg.workdir` shown like `cwd` is today.
- **tier 1, `apply_patch`**: header "Edit"; `getInputRendering` routes to `ApplyPatchContent` with the extracted patch string (it already accepts the raw envelope via `props.input` / `parseApplyPatchEnvelope`); summary path mirrors the direct `apply_patch` custom_tool_call.
- **tier 1, `update_plan`**: header "Todo"; summary and detail delegate to the native `update_plan` path (`TodoSummary` / `TodoContent`), which also suppresses the redundant success result.
- **tier 1, `web__run`**: search-only and navigation-only calls render as "Web search" / "Web fetch" with query or target summaries; compound and hosted-data calls render as neutral "Web". The nested arguments replace the JavaScript body while the code-mode output remains available as the result.
- **tier 2**: header "Run code"; summary line lists detected calls using the normal tool-card naming chain (`Shell ×2, Edit, MCP : Chrome devtools : List pages` — provider label first, then the shared MCP/general formatter); body = JS code block (current `INPUT_OVERRIDES.exec`) preceded by the resolved calls rendered individually when available.
- **tier 3**: current behavior unchanged.

`getHeaderLabel`, `getSummaryRendering`, `getInputRendering`, `getResultRendering`, `transformDisplayResult`, `getExpectedResultCount` each gain the `exec` branch; none of the existing sets (`FUNCTION_CALL_EXEC_TOOLS`, …) change membership.

### 7.2 Output rendering

New aggregation for code-mode outputs (mirroring `aggregateExecCommandOutput`): strip the status header, join text segments, surface status + wall time as metadata, chain `wait` outputs (they arrive as additional ToolResultLinks on the `exec` call thanks to §6.2, like `write_stdin` chunks today). `Script failed` renders the error state; `Script error:` segment shown as the error body.

### 7.3 `wait`

With §6.2 the `wait` call/output rows are SYSTEM (debug-only), and their content resurfaces as chained results on the `exec` card — nothing else to do frontend-side beyond the chaining-aware expected-result count (reuse the `exec_command` non-terminated logic keyed on the parsed status).

## 8. Compatibility & versioning

- All new logic is behind the two shape checks of §4; no existing constant/set changes semantics for other tools. 5.5-and-earlier sessions recompute to byte-identical results (worth a regression assertion in tests).
- `CURRENT_COMPUTE_VERSION` bump for the backend changes.
- No CLI/skill surface change → no plugin bump, no SKILLS-AND-CLI.md change.

## 9. Test plan

- **Extractor unit tests (both mirrors, same fixture list)**: canonical exec_command wrapper (JSON-style arg), unquoted-key object arg, pragma line, const-patch apply_patch wrapper, string-concat const, nested-in-expression call (`text(JSON.stringify(await tools.exec_command({...})))`), polling loop (→ tier 2: one call, resolved, loop ignored), multiple calls, `${}` template (→ unresolved), MCP call, no calls.
- **Output parsing**: string shape, array shape, all four statuses, `Script error` segment, missing header (defensive).
- **Compute integration**: fixtures lifted from the real session `019f4d27-01dd-7612-8ec4-f9659e71a7ac` (exec_command script, apply_patch script + orphan `patch_apply_end` + `wait` chain); assert kinds, chaining (`wait` output rebound to `exec`), `is_terminated` transitions, doc-edit events, error detection; plus a pre-5.6 regression fixture asserting unchanged output.
- **Frontend**: toolHelpers routing tests per tier (header/summary/input/result), ApplyPatchContent fed with an extracted patch.

## 10. Reference material

Analysis artifacts (no need to re-explore):

- **Test session** `019f4d27-01dd-7612-8ec4-f9659e71a7ac` (gpt-5.6-terra, project twicc-poc): rollout at `~/.codex/sessions/2026/07/10/rollout-2026-07-10T19-50-29-….jsonl` (49 lines — exec_command script l.15/16, apply_patch script l.32/33 + orphan `patch_apply_end` l.35 with `call_id: exec-<uuid>`, `wait` chain l.38/39, second exec l.43/44). Live app-server capture at `~/.twicc/logs/sdk/codex/019f4d27-01dd-7612-8ec4-f9659e71a7ac.jsonl` (`commandExecution` items with Codex-computed `commandActions`, `fileChange` + `item/fileChange/requestApproval`) — reference for the deferred live enrichment.
- **codex-rs** (checkout at `/home/twidi/dev/codex`): runtime `code-mode/src/runtime/` (V8) + `core/src/tools/code_mode/{mod,execute_handler,wait_handler}.rs` (`call_nested_tool`, `format_script_status`); tool description `code-mode/src/description.rs`; persistence `rollout/src/policy.rs`; `ToolMode` gate `protocol/src/openai_models.rs` + `core/src/tools/spec_plan.rs:403-452`; approval payloads `protocol/src/approvals.rs`.
- **TwiCC anchors**: backend `providers/codex/compute.py` (tool sets ~l.249-460, `parse_exec_command_status`, `extract_tool_result_info` non-string gap ~l.2113, `extract_doc_edit_events`, `remap_tool_result_id`); frontend `providers/codex/toolHelpers.js` (`INPUT_OVERRIDES.exec`, `getHeaderLabel` "Run code", per-tool sets), `parseCommand.js`, `ApplyPatchContent.vue` (`props.input` = raw envelope).
- 5.6 models report `tool_mode: "code_mode_only"` in the `/models` catalog (`~/.codex/models_cache.json`); 5.5/5.4 have none → Direct. `model_catalog_json` (config.toml) is the only client-side override (switches to `StaticModelsManager`, frozen catalog) — investigated and rejected as a workaround.

## 11. Open questions

1. **Per-command exit codes in tier 1**: the script-level status hides the nested command's exit code. The canonical wrapper emits `r.output` only (no trailer), so exit codes are generally unrecoverable from the rollout. Accepted loss for now; the live item stream (deferred enrichment) carries `exitCode` if we ever want it.
2. ~~**MCP tier-1 rendering**~~ — implemented (2026-07-11). A nested MCP call emits the same persisted `mcp_tool_call_end` as a direct call, under the synthesized `exec-<uuid>` call_id and with an `invocation: {server, tool, arguments}` field (verified on session `019f4fcb-5ddb-71b0-ac0d-660c41ee5fd6`). The orphan event is rebound to its exec like `patch_apply_end` — matched on the exact `mcp__<server>__<tool>` name declared by the script (stronger than the patch path heuristic), recency fallback (compute v38). A tier-1 MCP script then renders exactly like a direct MCP call: formatted tool name as header, arguments object as input, unwrapped `CallToolResult` (`Ok.structuredContent` / `Ok` / `Err`) as result, MCP errors surfacing on the exec card.
3. **`world_state` / `turn_context`**: new 5.6 rollout item types, currently falling through generic classification (SYSTEM / DEBUG_ONLY — confirmed harmless). Out of scope here; worth a quick audit separately.
