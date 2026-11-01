# Monitor Tool Aggregation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the Claude Code `Monitor` tool result as a single aggregated card instead of the current noisy mix of an empty "Monitor started" tool_result, several `<task-notification>` user_messages, and a hidden terminal attachment.

**Architecture:** Mirror Codex's `exec_command` + `write_stdin` chained-result pattern. Add a session-scoped `task_id → tool_use_id` map on the Claude Code compute, rewrite the `<task-notification>` user_messages and the terminal attachment into synthetic `tool_result` rows via `transform_inline`, flag the terminal row through `compute_link_extra` with `is_terminated: true`, and on the frontend wire the existing `shouldAggregateExecOutput` / `getAggregatedExecOutput` / `isToolRunning` hooks plus a new `MonitorResultContent.vue` component.

**Tech Stack:** Python 3.13, Django 6, orjson, xmltodict (backend); Vue 3 (Composition API), Pinia (frontend).

**Notes:**
- This project uses **no tests, no linting** (per `CLAUDE.md`). Steps that would normally be TDD are replaced with manual verification against a known reference session.
- Reference session for verification: `97526ea3-bf81-480f-9bb0-d37332f53278` (project `-home-twidi-dev-sparkup-stt-worker`). Tool_use Monitor at line 1243, task `bzi1lpskj` chain at lines 1245/1255/1263/1270.

---

## Background

### Today's wire shape (observed)

For one Monitor invocation (task `bzi1lpskj` in the reference session):

| line_num | nature | identifiers in payload | content |
|---|---|---|---|
| 1243 | `assistant` / `tool_use` `Monitor` | `tool_use_id = toolu_01Vt1TWEjVttfDdg2AAwdBC7` | description + bash command |
| 1245 | `user` / `tool_result` of `Monitor` | `tool_use_id` matches; `toolUseResult.taskId = bzi1lpskj` | `"Monitor started (task bzi1lpskj, timeout 600000ms). …"` |
| 1252, 1259, 1260 | `queue-operation` (internal SDK echo) | none | XML notification copies — already classified `kind=system display_level=3` (DEBUG_ONLY), ignored by the pipeline |
| 1255 | `user` / user_message with `origin.kind=task-notification` | XML carries `<task-id>` only (no `<tool-use-id>`) | `<task-notification><task-id>bzi1lpskj</task-id><summary>…</summary><event>Lint+typecheck: pass\nTests: pass</event></task-notification>` |
| 1263 | same shape as 1255 | `<task-id>` only | `<event>Build docker: pass\n--- all checks done ---</event>` |
| 1270 | `attachment` with `attachment.type=queued_command` and `attachment.commandMode=task-notification` | XML carries `<task-id>` **and** `<tool-use-id>` | `<task-notification><task-id>bzi1lpskj</task-id><tool-use-id>toolu_01Vt1…</tool-use-id><status>completed</status><summary>… stream ended</summary></task-notification>` |

### Existing `<task-notification>` handling (not what we want)

`src/twicc/providers/claude_code/compute.py:404-450` already rewrites `<task-notification>` user_messages into `tool_result` rows — but only for the case where the XML carries `<tool-use-id>`. That path serves **background agents spawned via `Task`** (the `<tool-use-id>` lets the rewrite point at the spawning Task tool_use). Our Monitor task notifications **do not** carry `<tool-use-id>` — they carry only `<task-id>`, so we must resolve the `tool_use_id` via a session-scoped map populated when the Monitor's first `tool_result` arrived.

### Pattern to mirror — Codex `exec_command` / `write_stdin`

- Backend `compute_link_extra` returns `{"is_terminated": True}` on the **closing chunk** of an `exec_command` chain (`src/twicc/providers/codex/compute.py:2014/2025/2083`).
- Backend `remap_tool_result_id` rebinds each `write_stdin` `function_call_output` onto the parent `exec_command`'s `tool_use_id` (`src/twicc/providers/codex/compute.py:1033`).
- Frontend store aggregates: `toolState = { resultCount, completedAt, error, extra, toolResultLineNums }` where `extra` is `Max`-aggregated across links (so any closing chunk flips the whole tool to "done").
- Frontend `isToolRunning` for the exec family reads `JSON.parse(toolState.extra).is_terminated` instead of comparing `resultCount` to an expected count (count is unknown up-front, can be any number of chunks).
- Frontend `shouldAggregateExecOutput(name)` / `getAggregatedExecOutput(name, toolId, options)` are the generic hooks the shell exposes (`frontend/src/providers/baseHelpers.js:952/978`). The shell passes the result through `ctx.aggregatedExecOutput` to `getResultRendering`.

We **do not** need `remap_tool_result_id` for Monitor: our synthetic tool_results already carry the right `tool_use_id` (we wrote it ourselves during `transform_inline`).

### Why we can rely on a session-scoped instance map

- JSONL is append-only and read in line order. The Monitor's tool_result (which carries `toolUseResult.taskId`) **always** appears before its task-notifications, in both batch (`compute_session_metadata`) and live (watcher) paths.
- `transform_inline` is invoked in both paths (batch: `src/twicc/providers/compute_base.py:1701`, live: `src/twicc/providers/compute_base.py:2292`).
- `begin_session_compute` / `end_session_compute` are invoked **only** in batch (`src/twicc/providers/compute_base.py:1687/1878`). In live, the map persists across sessions, so we index by `session_id` (mirrors Codex's `_exec_command_maps: dict[str, dict[int, str]]`).
- We purge each `task_id` entry from the map when we synthesize its terminal tool_result, so memory does not grow unbounded.

---

## File Structure

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/twicc/providers/claude_code/compute.py` | New `_monitor_task_to_tool_use_id` session-scoped map, `begin_session_compute` / `end_session_compute` hooks, two new branches in `transform_inline` (Rule 1 = user_message fragment, Rule 2 = attachment terminal), Monitor case in `compute_link_extra`, bump of `CLAUDE_CODE_COMPUTE_VERSION` |
| Modify | `frontend/src/providers/claude_code/toolHelpers.js` | New `Monitor` cases in `getExpectedResultCount`, `getRequiredResultCountForDisplay`; new override of `isToolRunning`, `shouldAggregateExecOutput`, `getAggregatedExecOutput`; new `aggregateMonitorOutput` helper; extension of `getResultRendering` |
| Create | `frontend/src/components/session/detail/items/MonitorResultContent.vue` | Render the aggregated Monitor output (path confirmed in task 4.1) |

### Closed questions resolved during research

| # | Question | Answer |
|---|---|---|
| A | Will `is_error=True` on our synthetic terminal propagate to the link's `error` column and render automatically? | Yes. `extract_tool_result_info` (`claude_code/compute.py:702`) reads `tool_result.get('is_error')`; when set, it falls through to `error_text = stripped or 'Unknown error'` for non-prefixed strings. A status like `"failed"` lands as-is in `ToolResultLink.error` and is rendered by the generic error path. |
| B | How do we keep the map alive in the live path that doesn't call `begin/end_session_compute`? | Index the map by `session_id` (mirror of Codex's `_exec_command_maps`). Eager purge per `task_id` on terminal arrival prevents unbounded growth. |
| C | Is there a risk that some `remap_tool_result_id` rebinds our synthetic Monitor results incorrectly? | No — Claude Code does not override `remap_tool_result_id` / `remap_tool_result_id_live`, so it stays identity. |
| D | How do we tell `compute_link_extra` that "this is the terminal row" vs an intermediate event row? | Set `parsed_json['twiccMonitorTerminal'] = True` at the moment we synthesize the terminal tool_result in `transform_inline`. `compute_link_extra` reads this top-level flag (it lives in the persisted content, not just memory). |
| E | How does the frontend dispatch and aggregate per tool? | `ToolUseContent.vue:415-419` calls `helpers.shouldAggregateExecOutput(name)` then `helpers.getAggregatedExecOutput(name, toolId, options)`, passes the result as `ctx.aggregatedExecOutput` to `helpers.getResultRendering(name, displayResult, input, ctx)`. All three hooks are provider-agnostic in `baseHelpers.js` — we just override on the Claude Code side. |
| F | Is the order of `toolResultLineNums` reliable so index 0 is always the original Monitor tool_result? | Yes — documented "ordered ASC" (`frontend/src/stores/data.js:2397`) and populated from `line_num`. The Monitor's "Monitor started" line is always the lowest. |

### Open question to resolve while implementing task 3.2

**Per-link `extra` access from the frontend.** The frontend `toolState.extra` is `Max`-aggregated across all links of a `tool_use_id`. We need to know **per-row** whether a given `toolResultLineNum` is the terminal (so the aggregator can skip it from the concatenated body, even though it still flips `isTerminated`). Two options exist if a per-link `extra` is not already plumbed:
1. Read the `kind`/payload directly from the SessionItem content (the terminal tool_result has `is_error` set when status≠completed; the "completed" terminal has `is_error=False` so this is not enough). Heuristic on content shape ("≤1 word, no newline") would work but is fragile.
2. **Preferred fallback:** add a small explicit marker. When we synthesize the terminal in task 2, set `message.content[0]['twiccMonitorTerminal'] = True` (a TwiCC-only key inside the tool_result block — survives DB round-trip). The frontend aggregator skips any chunk whose tool_result block carries this marker.

Decision deferred to task 3.2 — we read the live data path first; if per-link `extra` is not surfaced through the WS payload, we adopt the marker approach without further discussion.

---

## Task 1 — Backend: session-scoped map + Rule 1 (user_message → tool_result fragment)

**Files:**
- Modify: `src/twicc/providers/claude_code/compute.py`

### Steps

- [ ] **1.1 — Add the Monitor constant.**

Near the top of the file, beside `AGENT_TOOL_NAMES`, add:

```python
MONITOR_TOOL_NAME = 'Monitor'
```

- [ ] **1.2 — Initialise the session-scoped map on the compute instance.**

Locate the existing `ClaudeCodeCompute.__init__` (or the closest equivalent — check whether an explicit `__init__` exists; if not, add one calling `super().__init__()`). Add:

```python
self._monitor_task_to_tool_use_id: dict[str, dict[str, str]] = {}
```

- [ ] **1.3 — Add `begin_session_compute` / `end_session_compute` overrides.**

```python
def begin_session_compute(self, session_id: str) -> None:
    self._monitor_task_to_tool_use_id[session_id] = {}

def end_session_compute(self, session_id: str) -> None:
    self._monitor_task_to_tool_use_id.pop(session_id, None)
```

These are no-ops in the base class — we are not overriding any existing behaviour, just adding the per-session bookkeeping. The base lifecycle is wired in `compute_base.py:1687` and `compute_base.py:1878`.

- [ ] **1.4 — In `transform_inline`, populate the map on Monitor `tool_result` arrival.**

Insert this block **before** the existing `# --- task-notification XML` block (`compute.py:404`):

```python
# --- Monitor tool_result side-effect: index its taskId so later
# task-notification user_messages can be rewritten as tool_results
# attached to the original tool_use_id. No content rewrite here —
# only the map is populated.
if entry_type == 'user':
    session_id = parsed_json.get('sessionId')
    if isinstance(session_id, str) and session_id:
        tool_use_result = parsed_json.get('toolUseResult')
        if isinstance(tool_use_result, dict):
            task_id = tool_use_result.get('taskId')
            if isinstance(task_id, str) and task_id:
                content = get_message_content_list(parsed_json, 'user')
                if content:
                    for block in content:
                        if (
                            isinstance(block, dict)
                            and block.get('type') == 'tool_result'
                            and isinstance(block.get('tool_use_id'), str)
                        ):
                            self._monitor_task_to_tool_use_id.setdefault(
                                session_id, {}
                            )[task_id] = block['tool_use_id']
                            break
```

Reuses the existing `get_message_content_list` helper. Returns nothing — falls through to the rest of `transform_inline`.

- [ ] **1.5 — Branch the existing `<task-notification>` handling on `<tool-use-id>` presence.**

Locate the existing branch (`compute.py:404-450`). Today it returns early when `tool_use_id` is extracted from the XML. Change the structure to:

```python
# --- task-notification XML (two flavours: background agent results
# carry <tool-use-id> directly; Monitor task notifications carry only
# <task-id> + <event>, and we resolve the tool_use_id via the
# session-scoped map populated above) ---
if entry_type == 'user':
    message = parsed_json.get('message')
    if isinstance(message, dict):
        content = message.get('content')
        if isinstance(content, str):
            stripped = content.lstrip()
            if stripped.startswith(_TASK_NOTIFICATION_TAG):
                close_idx = stripped.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
                if close_idx != -1:
                    xml_str = stripped[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]
                    try:
                        notification = xmltodict.parse(xml_str)['task-notification']
                        tool_use_id = notification.get('tool-use-id')
                        task_id = notification.get('task-id')
                        result_text = (
                            notification.get('result', '')
                            or notification.get('summary', '')
                        )
                        event_text = notification.get('event')
                    except Exception:
                        logger.info(
                            "xmltodict failed for task-notification, "
                            "falling back to manual extraction"
                        )
                        tool_use_id, task_id, result_text = (
                            _extract_task_notification_fields(xml_str)
                        )
                        event_text = None  # manual fallback only used by the
                                           # subagent branch; Monitor path
                                           # tolerates absence (no event match
                                           # → no rewrite, see below).

                    # --- Branch A: background agent result (existing behaviour) ---
                    if tool_use_id:
                        parsed_json['twiccOriginalContent'] = content
                        message['content'] = [{
                            'type': 'tool_result',
                            'tool_use_id': tool_use_id,
                            'content': result_text,
                        }]
                        if task_id:
                            parsed_json['toolUseResult'] = {'agentId': task_id}
                        return orjson.dumps(parsed_json).decode('utf-8')

                    # --- Branch B: Monitor task notification fragment ---
                    # No <tool-use-id> in the XML but <event> present and
                    # <task-id> resolvable through the session-scoped map.
                    session_id = parsed_json.get('sessionId')
                    if (
                        isinstance(session_id, str)
                        and isinstance(task_id, str)
                        and isinstance(event_text, str)
                        and event_text
                    ):
                        mapped = (
                            self._monitor_task_to_tool_use_id
                            .get(session_id, {})
                            .get(task_id)
                        )
                        if mapped:
                            parsed_json['twiccOriginalContent'] = content
                            message['content'] = [{
                                'type': 'tool_result',
                                'tool_use_id': mapped,
                                'content': event_text,
                            }]
                            return orjson.dumps(parsed_json).decode('utf-8')

                    # Fall through — no rewrite applied.
```

Notes:
- `xmltodict.parse` returns dicts; `notification.get('event')` returns the text inside `<event>…</event>` (or `None` if absent). Multi-line text inside CDATA-free XML round-trips fine.
- The manual fallback `_extract_task_notification_fields` is the existing helper at `compute.py:142`. It does not currently extract `<event>` — Branch B's input will be `None` in that case and we fall through (no rewrite). Acceptable: the manual fallback is for malformed XML; failing to rewrite is no worse than today.
- We don't purge the map here — the terminal in task 2 is the purge point.

- [ ] **1.6 — Manual verification (no automated test).**

Restart the backend (per `CLAUDE.md`, ask the user to run `uv run ./devctl.py restart back` — never restart servers yourself). Then run:

```bash
sqlite3 ~/.twicc/db/data.sqlite "SELECT line_num, kind, display_level FROM core_sessionitem WHERE session_id = '97526ea3-bf81-480f-9bb0-d37332f53278' AND line_num IN (1245, 1255, 1263) ORDER BY line_num;"
```

Expected after recompute:
- 1245: unchanged (still `content_items` / DEBUG_ONLY) — Monitor `tool_result` original.
- 1255 and 1263: kind changes from `user_message` to whatever the pipeline classifies `tool_result` rows as in Claude Code (probably also `content_items`), display_level changes from ALWAYS (1) to DEBUG_ONLY (3).

If the recompute does not run automatically (sessions not bumped), proceed to task 2 — the `CLAUDE_CODE_COMPUTE_VERSION` bump at the end of task 2 will trigger it for all sessions.

- [ ] **1.7 — Commit.**

```bash
git add src/twicc/providers/claude_code/compute.py
git commit -m "feat(claude_code): rewrite Monitor task-notification fragments as tool_result rows"
```

---

## Task 2 — Backend: Rule 2 (terminal attachment → terminal tool_result) + `compute_link_extra` Monitor

**Files:**
- Modify: `src/twicc/providers/claude_code/compute.py`

### Steps

- [ ] **2.1 — Add a new branch in `transform_inline` for terminal attachments.**

Append, **after** Branch B (still inside `transform_inline`, before the `local-command-stdout/stderr` block):

```python
# --- attachment queued_command terminal task-notification ---
# Monitor stream end signalled by an attachment carrying both
# <task-id> and <tool-use-id> + <status>. Rewrite as a synthetic
# terminal tool_result that compute_link_extra will flag with
# is_terminated:true; non-"completed" statuses surface as
# ToolResultLink.error through extract_tool_result_info.
if entry_type == 'attachment':
    attachment = parsed_json.get('attachment')
    if (
        isinstance(attachment, dict)
        and attachment.get('type') == 'queued_command'
        and attachment.get('commandMode') == 'task-notification'
    ):
        prompt_text = attachment.get('prompt')
        if isinstance(prompt_text, str):
            stripped = prompt_text.lstrip()
            if stripped.startswith(_TASK_NOTIFICATION_TAG):
                close_idx = stripped.rfind(_TASK_NOTIFICATION_CLOSE_TAG)
                if close_idx != -1:
                    xml_str = stripped[:close_idx + len(_TASK_NOTIFICATION_CLOSE_TAG)]
                    try:
                        notification = xmltodict.parse(xml_str)['task-notification']
                        terminal_tool_use_id = notification.get('tool-use-id')
                        terminal_task_id = notification.get('task-id')
                        terminal_status = notification.get('status')
                    except Exception:
                        logger.info(
                            "xmltodict failed for terminal task-notification "
                            "attachment, falling back to manual extraction"
                        )
                        terminal_tool_use_id, terminal_task_id, _ = (
                            _extract_task_notification_fields(xml_str)
                        )
                        terminal_status = None  # extractor doesn't carry status

                    if (
                        isinstance(terminal_tool_use_id, str)
                        and isinstance(terminal_status, str)
                    ):
                        original_content = orjson.dumps(parsed_json).decode('utf-8')
                        is_error = terminal_status != 'completed'
                        # Rewrite top-level shape into a synthetic user/tool_result
                        # entry compatible with extract_tool_result_info.
                        parsed_json['type'] = 'user'
                        parsed_json['message'] = {
                            'role': 'user',
                            'content': [{
                                'type': 'tool_result',
                                'tool_use_id': terminal_tool_use_id,
                                'content': terminal_status,
                                'is_error': is_error,
                            }],
                        }
                        parsed_json['twiccMonitorTerminal'] = True
                        parsed_json['twiccOriginalContent'] = original_content
                        # Drop attachment-specific keys that no longer
                        # describe the rewritten shape — keep things tidy
                        # for downstream consumers that may inspect the
                        # parsed dict (kind/display_level computation).
                        parsed_json.pop('attachment', None)

                        # Purge the map: this Monitor's stream is complete.
                        session_id = parsed_json.get('sessionId')
                        if isinstance(session_id, str) and isinstance(terminal_task_id, str):
                            self._monitor_task_to_tool_use_id.get(session_id, {}).pop(
                                terminal_task_id, None
                            )

                        return orjson.dumps(parsed_json).decode('utf-8')
```

Notes:
- `extract_tool_result_info` (`compute.py:702`) walks `message.content` (via `get_message_content_list(parsed_json, "user")`), so `type='user'` is the right top-level shape.
- We drop the `attachment` key once we've consumed it. The rest of the entry (`uuid`, `timestamp`, `sessionId`, `cwd`, `gitBranch`, `parentUuid`, `userType`, `version`) remains intact and feeds the normal metadata pipeline.
- `terminal_task_id` may be `None` from the manual fallback — defensive guard, no purge in that case (small leak per orphaned terminal; acceptable).

- [ ] **2.2 — Extend `compute_link_extra` with a Monitor case.**

Locate `compute_link_extra` (`compute.py:831-879`). Insert, **before** the existing `if tool_name not in ('Edit', 'Write'):` guard:

```python
if tool_name == MONITOR_TOOL_NAME:
    if parsed_json.get('twiccMonitorTerminal'):
        return orjson.dumps({'is_terminated': True}).decode()
    return None
```

The existing Edit/Write logic remains intact below.

- [ ] **2.3 — Verify `is_tool_result_item` accepts the synthetic terminal.**

Read `is_tool_result_item` in `claude_code/compute.py` (or `compute_base.py` if inherited). It must return `True` for a `parsed_json` with `type='user'` + a `tool_result` block in `message.content`. If it does, `transform_tool_result_with_cache` will run on the synthetic — verify it is a no-op for content without a PreToolUse cache hit. (Expected: yes; the helper looks up `~/.claude/projects/.../tasks/` files by tool_use_id and skips silently when none match.) If for some reason the helper raises on the synthetic shape, narrow the check (e.g. skip when `twiccMonitorTerminal=True`).

- [ ] **2.4 — Bump `CLAUDE_CODE_COMPUTE_VERSION`.**

The constant lives in `src/twicc/settings.py:205` (currently `93`). Increment by 1. This forces the background compute worker to re-run on every Claude Code session at next startup, applying the new rewrites to historical data.

There is a sibling `CLAUDE_CODE_COMPUTE_VERSION` defined in `src/twicc/settings_test.py:22` (test overrides). Do **not** touch it — only `settings.py` matters for the runtime path.

- [ ] **2.5 — Manual verification.**

Ask the user to restart the backend (do not restart yourself). After the background compute finishes (a few seconds to a few minutes depending on session count), verify in the reference session:

```bash
sqlite3 ~/.twicc/db/data.sqlite "SELECT line_num, kind, display_level FROM core_sessionitem WHERE session_id = '97526ea3-bf81-480f-9bb0-d37332f53278' AND line_num = 1270;"
```

Expected: kind moves from `system` to something like `content_items` (the synthetic tool_result), display_level stays DEBUG_ONLY.

```bash
sqlite3 ~/.twicc/db/data.sqlite "SELECT tool_result_line_num, error, extra FROM core_toolresultlink WHERE session_id = '97526ea3-bf81-480f-9bb0-d37332f53278' AND tool_use_line_num = 1243 ORDER BY tool_result_line_num;"
```

Expected: four `ToolResultLink` rows pointing at the Monitor tool_use (lines 1245, 1255, 1263, 1270). The 1270 row has `extra = '{"is_terminated": true}'`. Since status is `completed`, the `error` column is `NULL`.

- [ ] **2.6 — Commit.**

```bash
git add src/twicc/providers/claude_code/compute.py src/twicc/settings.py
git commit -m "feat(claude_code): synthesize Monitor terminal tool_result and flag is_terminated"
```

---

## Task 3 — Frontend: toolHelpers hooks for `Monitor`

**Files:**
- Modify: `frontend/src/providers/claude_code/toolHelpers.js`

### Steps

- [ ] **3.1 — Add the Monitor constant.**

Near the top of `claude_code/toolHelpers.js`, alongside existing tool-name constants if any:

```js
const MONITOR_TOOL_NAME = 'Monitor'
```

- [ ] **3.2 — Resolve the per-link `extra` question and implement `aggregateMonitorOutput`.**

First investigate (read `frontend/src/stores/data.js` around the `toolStates` population code, including the `setToolState` action and any WS handler that pushes per-link rows) whether the frontend has per-link `extra` available, or only the `Max`-aggregated `toolState.extra`. Approach:

- Search for `tool_result_link` or `toolResultLink` payload handling in `data.js` and `useWebSocket.js`.
- Search for `linkExtra` or any field shape carrying per-link data.

**If per-link `extra` is exposed:** the aggregator looks up each `line_num` → matching link → reads its `extra.is_terminated` to skip the terminal.

**If only `Max`-aggregated `toolState.extra` is exposed:** add an explicit marker in task 2's terminal rewrite. Specifically, in the synthetic terminal's tool_result block, add a key the aggregator can read directly:

```python
# In task 2.1, replace the message.content block with:
parsed_json['message'] = {
    'role': 'user',
    'content': [{
        'type': 'tool_result',
        'tool_use_id': terminal_tool_use_id,
        'content': terminal_status,
        'is_error': is_error,
        'twiccMonitorTerminal': True,
    }],
}
```

The frontend aggregator can then check `getParsedContent(item).message.content[0].twiccMonitorTerminal === true` and skip that row.

**Apply this fix retroactively to task 2** if needed (small back-patch to `compute.py` then re-commit with `--amend` of task 2's commit if not yet pushed — or as a separate fixup commit if cleaner).

Now write the aggregator:

```js
function aggregateMonitorOutput(toolId, options) {
    if (!toolId) return null
    const toolState = options?.getToolState?.(toolId)
    const lineNums = toolState?.toolResultLineNums
    if (!Array.isArray(lineNums) || lineNums.length === 0) return null
    const getSessionItem = options?.getSessionItem
    if (typeof getSessionItem !== 'function') return null

    const bodies = []
    let isTerminated = false
    for (let idx = 0; idx < lineNums.length; idx++) {
        // Index 0 is always the original Monitor "Monitor started …" row.
        // Its content is not user-facing; we only consumed its taskId on
        // the backend to populate the map.
        if (idx === 0) continue

        const ln = lineNums[idx]
        if (!Number.isInteger(ln) || ln < 1) continue
        const item = getSessionItem(ln)
        if (!item) continue
        const parsed = getParsedContent(item)
        if (!parsed || parsed.type !== 'user') continue
        const content = parsed.message?.content
        if (!Array.isArray(content) || content.length === 0) continue
        const block = content[0]
        if (!block || block.type !== 'tool_result') continue
        // Skip the terminal row — its body is just the status string,
        // handled by the generic error path when is_error is set.
        if (block.twiccMonitorTerminal === true) {
            isTerminated = true
            continue
        }
        const body = typeof block.content === 'string' ? block.content : ''
        if (body) bodies.push(body)
    }
    return {
        aggregatedOutput: bodies.join('\n'),
        isTerminated,
    }
}
```

Add the necessary import at the top of `toolHelpers.js`:

```js
import { getParsedContent } from '../../utils/parsedContent'
```

(Confirm the relative path — `toolHelpers.js` lives in `frontend/src/providers/claude_code/`, and `parsedContent.js` is at `frontend/src/utils/parsedContent.js`. So `../../utils/parsedContent`.)

- [ ] **3.3 — Override `shouldAggregateExecOutput`.**

```js
shouldAggregateExecOutput(name) {
    return name === MONITOR_TOOL_NAME
}
```

(If the helper is already overridden for any other tool in Claude Code today, extend the check with `|| existingCheck`. At time of writing, it isn't.)

- [ ] **3.4 — Override `getAggregatedExecOutput`.**

```js
getAggregatedExecOutput(name, toolId, options) {
    if (name !== MONITOR_TOOL_NAME) return null
    return aggregateMonitorOutput(toolId, options)
}
```

- [ ] **3.5 — Extend `getExpectedResultCount` and `getRequiredResultCountForDisplay`.**

Locate `getExpectedResultCount` (around `claude_code/toolHelpers.js:475`). Add the Monitor case at the top of the method body:

```js
if (name === MONITOR_TOOL_NAME) return 1
```

Count is not predictive for Monitor (a stream emits any number of fragments); the spinner is driven by `is_terminated` instead.

Similarly extend or add `getRequiredResultCountForDisplay`:

```js
getRequiredResultCountForDisplay(name, input, options) {
    if (name === MONITOR_TOOL_NAME) return 1
    return this.getExpectedResultCount(name, input, options)
}
```

- [ ] **3.6 — Add an `isToolRunning` override.**

The base default (`baseHelpers.js:932`) compares `resultCount` to `getExpectedResultCount`, which is wrong for Monitor. Add to `ClaudeCodeToolHelpers`:

```js
isToolRunning(name, input, options) {
    if (name === MONITOR_TOOL_NAME) {
        if (options?.toolState?.error) return false
        const extra = options?.toolState?.extra
        if (!extra) return true
        try {
            const parsed = typeof extra === 'string' ? JSON.parse(extra) : extra
            return !parsed?.is_terminated
        } catch {
            return true
        }
    }
    return super.isToolRunning(name, input, options)
}
```

- [ ] **3.7 — Manual verification (partial — full visual check happens in task 4).**

Open the reference session in the running frontend. Inspect the Monitor tool_use at line 1243 with the browser devtools: confirm `toolState.extra` parses to `{ is_terminated: true }` for that tool. The spinner should not be running. The result body will still look broken (no dedicated component yet) — that's task 4.

- [ ] **3.8 — Commit.**

```bash
git add frontend/src/providers/claude_code/toolHelpers.js
git commit -m "feat(frontend/claude_code): wire Monitor tool to chained-result aggregation hooks"
```

---

## Task 4 — Frontend: `MonitorResultContent.vue` + dispatch

**Files:**
- Create: `frontend/src/components/session/detail/items/MonitorResultContent.vue` (path confirmed below)
- Modify: `frontend/src/providers/claude_code/toolHelpers.js`

### Steps

- [ ] **4.1 — Choose the component path.**

Inspect `frontend/src/components/session/detail/items/`:

```bash
ls frontend/src/components/session/detail/items/
```

If `BashResultContent.vue`, `EditContent.vue`, etc. live directly under `items/`, place `MonitorResultContent.vue` alongside them. If a `claude_code/` subfolder already exists (mirroring the `codex/` subfolder), use it. Decide once and stick with it.

- [ ] **4.2 — Create `MonitorResultContent.vue`.**

Minimal v1, modelled on `BashResultContent.vue` (read it for reference: how it renders a terminal-style block, what props it receives). Suggested shape:

```vue
<script setup>
defineProps({
    aggregatedOutput: {
        type: String,
        required: true,
    },
})
</script>

<template>
    <pre class="monitor-output"><code>{{ aggregatedOutput }}</code></pre>
</template>

<style scoped>
.monitor-output {
    background: var(--wa-color-surface-lowered, #1e1e1e);
    color: var(--wa-color-text-default, #e8e8e8);
    padding: 0.75rem 1rem;
    border-radius: 6px;
    font-family: var(--wa-font-mono, ui-monospace, "Cascadia Mono", "Menlo", monospace);
    font-size: 0.85rem;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
    overflow-x: auto;
}
.monitor-output code {
    background: transparent;
    color: inherit;
    font: inherit;
    padding: 0;
}
</style>
```

Read `BashResultContent.vue` first to align with whatever theme variables / scrollbar utilities the project actually uses — the snippet above is a placeholder.

- [ ] **4.3 — Extend `getResultRendering` in `claude_code/toolHelpers.js`.**

At the top of the file, import the new component:

```js
import MonitorResultContent from '@/components/session/detail/items/MonitorResultContent.vue'
```

(Adjust path to match the alias / relative convention used by the other component imports in this file.)

In `getResultRendering`, add the Monitor branch before the final `return null`:

```js
if (name === MONITOR_TOOL_NAME) {
    const agg = ctx?.aggregatedExecOutput
    const output = (agg && typeof agg.aggregatedOutput === 'string') ? agg.aggregatedOutput : ''
    return { component: MonitorResultContent, props: { aggregatedOutput: output } }
}
```

- [ ] **4.4 — Manual end-to-end verification.**

Open the reference session in the frontend. Scroll to the Monitor tool_use at line 1243. Verify:

1. The aggregated output shows only the two `<event>` bodies, joined by a newline. No "Monitor started …", no `"completed"` status.
2. Lines 1255 and 1263 do not appear separately in the timeline (they are now bound to the Monitor tool via `ToolResultLink`).
3. The tool's spinner is not running (`isTerminated` true).
4. No error indicator (status was "completed").
5. Bonus, if a session with a failed Monitor is available: status `"failed"` appears via the generic error rendering. If not available, this can be deferred to a follow-up sanity check.

- [ ] **4.5 — Commit.**

```bash
git add frontend/src/components/session/detail/items/MonitorResultContent.vue frontend/src/providers/claude_code/toolHelpers.js
git commit -m "feat(frontend/claude_code): render aggregated Monitor output via dedicated component"
```

---

## Risks and out-of-scope

### Risks tracked above

- **Task 3.2 — per-link `extra` access:** investigated first thing in task 3; resolved with the `twiccMonitorTerminal` marker fallback if needed.
- **Task 2.3 — `is_tool_result_item` synthetic compatibility:** verified before the second commit lands.
- **Compute-version bump:** task 2.4 ensures all historical sessions are recomputed. If a user has a very large `~/.claude/projects/` tree, recompute time grows linearly — acceptable, documented in `CLAUDE.md` startup flow.

### Out of scope

- No support for the inverse direction (Claude triggering a Monitor and us re-emitting events). We only consume what the SDK writes.
- We do not attempt to surface the `<summary>` text — for v1, only the `<event>` body is part of the aggregated output. Adding the summaries (one per event) is a follow-up.
- Failed Monitor (`status != "completed"`) is supported by the `is_error` propagation, but we have no failing example in current sessions. The behaviour is implemented but only validated theoretically against `extract_tool_result_info`'s parsing logic.
- No UI affordance to expand back to the raw user_messages / attachment for debugging — those rows are still in the DB (`twiccOriginalContent` preserves the original JSON), but no UI exposes that today.

---

## Estimated effort

| Task | Effort |
|---|---|
| 1 — Backend map + Rule 1 | ~30 min |
| 2 — Backend Rule 2 + compute_link_extra + version bump | ~30 min |
| 3 — Frontend hooks (including the 3.2 investigation) | ~45 min |
| 4 — Frontend component + dispatch | ~20 min |
| **Total** | **~2h** |
