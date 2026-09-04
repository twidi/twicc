# Codex 0.151 Rollout Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade TwiCC from Codex `0.150.1` to `0.151.0`, migrate known legacy rollouts automatically, rebuild every line-derived TwiCC structure safely, and make all Codex readers canonical-only.

**Architecture:** A Codex-only coordinator stays in the main process. It classifies each stale session, runs the bundled Codex migration command when required, replaces raw history through the existing DB writer, then dispatches the existing read-only compute worker. A per-session gate serializes migration with watcher ingestion and TwiCC-owned resume/send. `compute_version` remains the durable visibility barrier until the canonical history, metadata, links, snapshot boundaries, and search state are consistent.

**Tech Stack:** Python 3.13, Django 6, SQLite/WAL, asyncio, multiprocessing, orjson, pytest/pytest-django, Vue 3, Pinia, node:test, Tantivy, bundled Codex CLI `0.151.0`.

**Spec:** [TEMP_CODEX_0_151_JSONL_MIGRATION_NOTES.txt](../../../TEMP_CODEX_0_151_JSONL_MIGRATION_NOTES.txt)

---

## Global constraints

- Keep Claude Code on the current provider-agnostic one-shot compute path.
- Keep the CPU compute subprocess read-only against TwiCC SQLite.
- Route every migration-owned TwiCC SQLite write through `src/twicc/providers/db_writer.py`.
- Perform rollout file reads and Codex subprocess management in the main process.
- Do not add a Django model or migration.
- Store temporary snapshot anchors in `Share.options` under the private key `_codex_rollout_migration_anchor`.
- Do not retain any legacy JSONL reader after the final task.
- Do not add fork or revert lineage support.
- Do not add migration polling, WebSocket refresh, or automatic refresh to public shares.
- Do not add migration-specific activity preservation. The normal recompute is sufficient.
- Do not add a migration execution timeout.
- On cancellation or shutdown, terminate and reap the active Codex migration process before releasing its session gate.
- Do not run migration tests against the developer's real `~/.codex` directory.
- Do not bump the runtime or `CODEX_COMPUTE_VERSION` until the final integration task.
- Do not commit during planning. During implementation, commit only after explicit implementation authorization.

## End-state flow

```text
stale Codex Session
        |
        v
source mode + DB mode decision
        |
        +-- paginated / paginated --> read-only metadata compute
        |
        +-- legacy / paginated ----> fail this run
        |
        +-- legacy / legacy -------+--> preflight --> Codex migrate
        |                          |
        +-- paginated / legacy ----+
                                   v
                         per-session migration gate
                                   |
                         snapshot anchor DB job
                                   |
                     full source read from byte zero
                                   |
                       atomic history replacement
                                   |
                           computed(session_id)
                                   |
                    atomic metadata + share remap apply
                                   |
                    applied(session_id, outcome)
                                   |
                         search reindex request
                                   |
                         release migration gate
```

## Files and ownership

### New backend modules

- `src/twicc/providers/codex/canonical.py` — pure canonical item readers and TwiCC-private canonical builders.
- `src/twicc/providers/codex/rollout_migration.py` — history mode, preflight, full-file preparation, Codex CLI report parsing, and DB-writer job types/apply functions.
- `src/twicc/providers/codex/migration_gate.py` — per-session gates and the scheduler wake event.
- `src/twicc/providers/codex/background_compute.py` — Codex-only long-lived coordinator.

### New frontend modules

- `frontend/src/providers/codex/canonical.js` — canonical item accessors shared by helpers and components.
- `frontend/src/composables/wsSessionItems.js` — testable owner WebSocket readiness gate.

### New focused test files

- `tests/test_codex_canonical.py`
- `tests/test_codex_rollout_migration.py`
- `tests/test_codex_migration_jobs.py`
- `tests/test_codex_migration_gate.py`
- `tests/test_codex_migration_scheduler.py`
- `frontend/src/providers/codex/canonical.test.js`
- `frontend/src/composables/wsSessionItems.test.js`

Existing behavior tests remain authoritative. Update their Codex fixtures from legacy display events to canonical completed items when they exercise source parsing.

---

### Task 1: Define canonical item contracts in Python and JavaScript

**Files:**

- Create: `src/twicc/providers/codex/canonical.py`
- Create: `tests/test_codex_canonical.py`
- Create: `frontend/src/providers/codex/canonical.js`
- Create: `frontend/src/providers/codex/canonical.test.js`

- [ ] **Step 1: Write failing Python tests for the accepted allow-list**

Cover these exact completed item types:

- `UserMessage`
- `AgentMessage`
- `FileChange`
- `McpToolCall`
- `SubAgentActivity`
- `ImageGeneration`
- `Extension` only when `kind == "image_gen.generation"`

Also assert that `Reasoning`, `Plan`, `FunctionCallOutput`, `CommandExecution`, `WebSearch`, `ContextCompaction`, `HookPrompt`, `skill`, and `mention` do not become new visible semantic rows.

Use canonical fixtures such as:

```python
def completed(item: dict, *, turn_id: str = "turn-1") -> dict:
    return {
        "timestamp": "2026-08-31T10:00:00.000Z",
        "type": "event_msg",
        "payload": {
            "type": "item_completed",
            "thread_id": "11111111-1111-1111-1111-111111111111",
            "turn_id": turn_id,
            "item": item,
            "completed_at_ms": 1_788_171_200_000,
        },
    }
```

Required assertions:

- User text concatenates every `content[type=text].text` with no separator.
- Agent text concatenates every `content[type=Text].text` with no separator.
- User visibility accepts non-empty text, `image`, or `local_image`.
- Attachment-only user messages remain visible.
- `skill` and `mention` remain in raw content but do not add text or attachment count.
- A UserMessage containing only `skill` or `mention` is not visible.
- FileChange success means `status == "completed"` only.
- MCP failure reads `error.message` and also detects `result.isError == true`.
- Image generation normalizes native snake_case and Extension camelCase fields.

- [ ] **Step 2: Implement the Python API**

Use `NamedTuple` for normalized immutable results.

```python
class CanonicalImageGeneration(NamedTuple):
    id: str
    status: str
    revised_prompt: str | None
    result: str
    saved_path: str | None
    transparent_background: bool | None
    failure: object | None


def completed_item(record: dict) -> dict | None: ...
def user_message_text(record: dict) -> str | None: ...
def user_message_is_visible(record: dict) -> bool: ...
def user_message_attachment_count(record: dict) -> int: ...
def agent_message_text(record: dict) -> str | None: ...
def canonical_result_item(record: dict) -> dict | None: ...
def canonical_call_id(record: dict) -> str | None: ...
def image_generation(record: dict) -> CanonicalImageGeneration | None: ...
```

Add exact TwiCC-private builders:

```python
def build_twicc_user_message(
    record: dict,
    *,
    session_id: str,
    line_num: int,
    text: str,
) -> dict: ...


def build_twicc_agent_message(
    record: dict,
    *,
    session_id: str,
    line_num: int,
    text: str,
) -> dict: ...
```

Builder rules:

- Preserve the top-level `timestamp` and unrelated outer fields.
- Store the original raw `payload` under `twiccOriginalContent`.
- Emit `event_msg/item_completed` only.
- Use `thread_id=session_id`.
- Use stable `turn_id="twicc-line-<line_num>"` and `item.id="twicc-item-<line_num>"`.
- Omit `started_at_ms`.
- Set `completed_at_ms` from the top-level RFC3339 timestamp, or `0` when absent or invalid.
- Emit UserMessage content as `[{"type":"text","text":...,"text_elements":[]}]`.
- Emit AgentMessage content as `[{"type":"Text","text":...}]`.
- Omit optional `client_id`, `phase`, `memory_citation`, and `delivery` when unavailable.

- [ ] **Step 3: Write failing JavaScript tests for the mirrored contract**

Test these exports:

```js
completedItem(data)
userMessageText(data)
userMessageImages(data)
userMessageAttachmentCount(data)
agentMessageText(data)
fileChangeItem(data)
mcpToolCallItem(data)
imageGeneration(data)
buildOptimisticUserMessage(text, attachments)
```

`userMessageImages()` returns supported `image.image_url` and `local_image.path` entries in source order. It ignores audio, skill, and mention entries until TwiCC has an intentional renderer for them.

- [ ] **Step 4: Implement `canonical.js` and run focused tests**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && node --test src/providers/codex/canonical.test.js
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest tests/test_codex_canonical.py -q
```

---

### Task 2: Convert every backend Codex source reader to canonical items

**Files:**

- Modify: `src/twicc/providers/codex/compute.py`
- Modify: `src/twicc/providers/codex/helpers.py`
- Modify: `tests/test_codex_recompute_persistence.py`
- Modify: `tests/test_codex_code_mode.py`
- Modify: `tests/test_codex_subagent_links.py`
- Modify: `tests/test_plan_docs_providers.py`
- Modify: `tests/test_codex_hardcoded_commands.py`
- Modify: `tests/test_insert_screenshot.py`
- Modify: `tests/test_user_messages_endpoint.py`

- [ ] **Step 1: Add canonical regression fixtures to the existing behavior tests**

Cover all direct and indirect behaviors from the audit:

- Multiple user text chunks.
- Image-only, data-URL image, and local-image input.
- User messages with text plus skill/mention metadata.
- Multiple AgentMessage `Text` chunks.
- Goal, `/goal clear`, `/compact`, bare `/plan`, proposed plan, `/plan <prompt>`, and screenshot rewrites.
- Direct and nested FileChange pairing.
- Completed, failed, and declined FileChange states.
- File path, line statistics, git root, and plan-document extraction.
- Direct and nested McpToolCall pairing.
- MCP transport error and `result.isError == true`.
- Started SubAgentActivity in batch and live DB lookup paths.
- Later `FINAL_ANSWER` rebinding.
- Both image-generation canonical variants.
- De-duplication against preserved raw ResponseItems.

- [ ] **Step 2: Replace the six backend reader families**

Import the pure accessors from `canonical.py`.

Update these functions and methods:

```text
_event_msg_text
_event_msg_call_id
_mcp_end_qualified_name
_parse_sub_agent_activity_started
_patch_apply_error
_mcp_tool_call_end_error
_event_msg_payload_error
CodexSessionCompute.compute_item_kind
CodexSessionCompute.extract_user_message_text
CodexSessionCompute.analyze_content
CodexSessionCompute.is_tool_result_item
CodexSessionCompute.extract_tool_result_info
CodexSessionCompute.remap_tool_result_id
CodexSessionCompute.remap_tool_result_id_live
CodexSessionCompute.extract_paths_from_tool_uses
CodexSessionCompute.extract_doc_edit_events
CodexSessionCompute.compute_link_extra
CodexSessionCompute.transform_tool_result_with_cache
CodexHelpers.extract_indexable_text
CodexHelpers.get_tool_results
CodexHelpers.enrich_live_items_payload
```

Keep these sources unchanged:

- `session_meta`
- `turn_context`
- `event_msg.token_count`
- `event_msg.thread_goal_updated`
- raw function/custom calls and outputs
- raw reasoning
- raw `FINAL_ANSWER`
- top-level `compacted`

- [ ] **Step 3: Convert private DB rewrites to canonical builders**

Use `build_twicc_user_message()` and `build_twicc_agent_message()` in `_transform_inline_provider()`.

For already transformed legacy DB content:

1. Restore `twiccOriginalContent` to the original `response_item` payload.
2. Remove the private legacy wrapper.
3. Apply the canonical builder.

For `/plan <prompt>` restoration, modify the first canonical user text entry. For screenshot replacement, join AgentMessage text, replace tags, then store one schema-valid `Text` entry while preserving item metadata.

- [ ] **Step 4: Remove dormant legacy whitelists**

Delete `_PERSISTED_END_EVENT_TYPES` branches from backend parsing. Do not add canonical WebSearch to the result path. Keep image generation as a standalone IMAGE item.

- [ ] **Step 5: Run the backend semantic suite**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest \
  tests/test_codex_canonical.py \
  tests/test_user_messages_endpoint.py \
  tests/test_codex_code_mode.py \
  tests/test_codex_subagent_links.py \
  tests/test_plan_docs_providers.py \
  tests/test_codex_hardcoded_commands.py \
  tests/test_insert_screenshot.py \
  tests/test_codex_recompute_persistence.py -q
```

- [ ] **Step 6: Prove no source legacy reader remains**

The remaining matches may exist only in migration tests, comments describing migration input, or the temporary audit notes.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && rg -n \
  'user_message|agent_message|patch_apply_end|mcp_tool_call_end|image_generation_end|sub_agent_activity' \
  src/twicc/providers/codex
```

Review every match manually. Production source readers must use canonical item names.

---

### Task 3: Convert frontend Codex rendering and helper logic

**Files:**

- Modify: `frontend/src/providers/codex/helpers.js`
- Modify: `frontend/src/providers/codex/toolHelpers.js`
- Modify: `frontend/src/components/session/detail/items/codex/Message.vue`
- Modify: `frontend/src/components/session/detail/items/codex/ApplyPatchContent.vue`
- Modify: `frontend/src/components/session/detail/items/codex/ImageGeneration.vue`
- Modify: `frontend/src/components/session/detail/items/codex/PlanImplementationBody.vue`
- Test: `frontend/src/providers/codex/canonical.test.js`

- [ ] **Step 1: Route message helpers through `canonical.js`**

- `buildOptimisticUserMessageContent()` emits a synthetic canonical UserMessage wrapper.
- `extractUserMessageText()` returns trimmed canonical text or `null`.
- `extractUserMessageAttachmentCount()` counts `image` and `local_image` only.
- `Message.vue` reads user/assistant text from shared accessors.
- `Message.vue` preserves canonical user image order.
- `PlanImplementationBody.vue` reads the latest assistant text through `agentMessageText()`.

- [ ] **Step 2: Route rich result readers through canonical item roots**

- Replace `findCodexEndEventPayload()` with canonical FileChange/McpToolCall selectors.
- Keep expected result count at two for patch and MCP calls.
- Read FileChange `id`, `changes`, `status`, `stdout`, and `stderr` directly.
- Read McpToolCall `result` and `error.message` directly.
- Preserve `result.isError` bodies for display.
- Keep raw call/output result rows available for existing generic rendering.

- [ ] **Step 3: Normalize both image-generation schemas**

`ImageGeneration.vue` consumes the normalized object returned by `imageGeneration()`.

Render:

- native `revised_prompt`, `saved_path`, and `result`;
- Extension `revisedPrompt`, `savedPath`, `transparentBackground`, `failure`, and `result`;
- no Extension-only UI when native fields are absent.

- [ ] **Step 4: Remove frontend legacy end-event names**

Delete `PERSISTED_END_EVENT_TYPES`. Do not introduce a generic completed-item renderer.

- [ ] **Step 5: Run frontend tests and the standalone bundle build**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && npm run build
```

---

### Task 4: Implement rollout mode detection, preflight, and Codex CLI execution

**Files:**

- Modify: `src/twicc/providers/codex/initial_sync.py`
- Modify: `src/twicc/providers/codex/bin.py`
- Create: `src/twicc/providers/codex/rollout_migration.py`
- Create: `tests/test_codex_rollout_migration.py`

- [ ] **Step 1: Write history-mode and decision-matrix tests**

Define:

```python
class HistoryMode(StrEnum):
    LEGACY = "legacy"
    PAGINATED = "paginated"


class MigrationPreparation(StrEnum):
    MIGRATE_AND_REPLACE = "migrate_and_replace"
    REPLACE_ONLY = "replace_only"
    COMPUTE_ONLY = "compute_only"
    INCONSISTENT = "inconsistent"
```

Test the exact matrix:

| Source | DB | Decision |
| --- | --- | --- |
| legacy | legacy | migrate and replace |
| paginated | legacy | replace from byte zero |
| paginated | paginated | metadata compute only |
| legacy | paginated | inconsistent failure |

`session_meta.payload.history_mode == "paginated"` is the only paginated value. Missing or legacy values mean legacy.

- [ ] **Step 2: Extend `SessionMeta` without changing existing filtering**

Add `history_mode: HistoryMode` to `initial_sync.SessionMeta`. Preserve Guardian and parent-session behavior.

Add a DB helper that parses the first stored `SessionItem` and returns its history mode. A missing or malformed first DB item is a real preparation failure, not a guessed mode.

- [ ] **Step 3: Write preflight tests**

Define:

```python
class RolloutPreflight(NamedTuple):
    complete_lines: int
    malformed_lines: int
    blank_lines: int
    retired_lines: int
    partial_trailing_line: bool
    oversized_line: int | None
    oversized_bytes: int | None
```

Rules:

- Stream bytes. Do not load the complete legacy file to detect oversize.
- Reject a complete record whose payload bytes exceed `16 * 1024 * 1024`.
- Log one-based source line and exact byte count.
- Count malformed, blank, retired, and partial trailing records for diagnostics.
- Count `event_msg.guardian_assessment`, `event_msg.thread_name_updated`,
  `event_msg.undo_completed`, and `response_item` with
  `payload.type == "ghost_snapshot"` as the known retired records.
- Do not reject those four accepted-loss categories.
- Run preflight before snapshot anchors or the Codex subprocess.

- [ ] **Step 4: Add a public bundled-command resolver**

Expose the existing runtime path and merged environment without reaching into `CodexConfig` internals:

```python
class CodexCommand(NamedTuple):
    binary: Path
    env: dict[str, str]


async def resolve_codex_command() -> CodexCommand: ...
```

It calls `ensure_codex_runtime()`, uses `codex_binary_path()`, and merges `os.environ` with the existing Codex env overlay.

- [ ] **Step 5: Implement and test exact JSON report parsing**

Run:

```text
<bundled-codex> migrate-rollouts --apply --thread <session-id> --json
```

Parse this exact report shape:

```json
{
  "outcomes": [{
    "thread_id": "...",
    "rollout_path": "...",
    "status": "migrated",
    "bytes_processed": 123,
    "message": null
  }]
}
```

Accepted statuses:

- `migrated` and `already_paginated` -> success.
- `skipped_busy` -> deferred.
- `failed`, `skipped_empty`, or unexpected `eligible` -> real failure.

Require exactly one matching outcome. Validate both `thread_id` and resolved `rollout_path`. Parse stdout even when the process exits non-zero, because Codex exits non-zero for a reported `failed` outcome.

- [ ] **Step 6: Implement cancellation-safe process ownership**

The runner keeps the active `asyncio.subprocess.Process` reference.

On task cancellation or provider shutdown:

1. call `terminate()`;
2. await process exit;
3. use the provider's existing bounded shutdown grace before `kill()` if required;
4. always await the final exit before returning.

Do not set a timeout on a normal migration run.

- [ ] **Step 7: Run focused tests**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest \
  tests/test_codex_rollout_migration.py \
  tests/test_codex_guardian_session_filtering.py -q
```

---

### Task 5: Add DB-writer jobs for snapshot anchors and full history replacement

**Files:**

- Modify: `src/twicc/providers/codex/rollout_migration.py`
- Modify: `src/twicc/providers/codex/helpers.py`
- Modify: `src/twicc/providers/compute_base.py`
- Modify: `src/twicc/providers/db_writer.py`
- Modify: `src/twicc/core/serializers.py`
- Create: `tests/test_codex_migration_jobs.py`

- [ ] **Step 1: Write failing transaction tests**

Cover:

- Snapshot anchor capture at the last valid timestamp on or before `frozen_at_line`.
- Capture failure when no valid timestamp exists.
- Idempotent reuse of an existing private anchor after a crash.
- Anchor cleanup after `skipped_busy`.
- Owner and public Share serializers never expose the private anchor.
- Explicit deletion of `ToolResultLink` and `AgentLink`.
- Complete `SessionItem` replacement with fresh line numbers from one.
- Replacement of `last_offset`, `last_line`, and `mtime`.
- `tasks={}` and `search_version=None` after structural replacement.
- `compute_version` remains stale.
- Transaction rollback leaves old history and links intact.
- Historical `original_files` disappears after replacement.
- Canonical `changes` still supports the normal hunk fallback.
- A successful metadata apply marks `search_version=None` in the same transaction.

- [ ] **Step 2: Define immutable DB job payloads**

```python
class CaptureSnapshotAnchorsJob(NamedTuple):
    provider: Provider
    session_id: str
    future: asyncio.Future


class ClearSnapshotAnchorsJob(NamedTuple):
    provider: Provider
    session_id: str
    future: asyncio.Future


class ReplaceCodexHistoryJob(NamedTuple):
    provider: Provider
    session_id: str
    items: list[tuple[int, str]]
    last_offset: int
    last_line: int
    mtime: float
    future: asyncio.Future
```

The coordinator prepares `items` and tracking values from byte zero. The DB writer performs no file I/O.

The complete-file reader must:

- read bytes from offset zero;
- decode with the existing lenient UTF-8 policy;
- keep each non-blank JSONL record unchanged as raw content;
- assign fresh `line_num` values from one in physical record order;
- set `last_line` to the final assigned line number;
- set `last_offset` to the exact byte EOF captured by the same open file;
- set `mtime` from a stat taken after that read;
- fail preparation if the source disappears or stops being paginated.

- [ ] **Step 3: Implement provider-specific DB handlers**

Register the three jobs through `CodexHelpers.try_handle_async_job()` and `_settle_async_job()`.

The private option value is:

```json
{
  "timestamp": "2026-08-31T10:00:00+00:00"
}
```

Do not add the private key to the public share mutation allow-list.
Filter the private key out of both owner and public Share serialization.

The replacement transaction must:

1. lock/read the Session row;
2. delete old `ToolResultLink` rows;
3. delete old `AgentLink` rows;
4. delete old `SessionItem` rows;
5. bulk-create the prepared raw canonical items;
6. update `last_offset`, `last_line`, and `mtime`;
7. clear `tasks` and set `search_version=None`;
8. leave `compute_version` unchanged and stale.

- [ ] **Step 4: Return an explicit metadata apply outcome**

Replace the ambiguous `str | None` return from `BaseSessionCompute.apply_session_complete()` with:

```python
class ComputeApplyResult(NamedTuple):
    outcome: Literal["applied", "superseded", "missing"]
    folded_ancestor_id: str | None = None
```

- `applied` means the transaction committed.
- `superseded` means the `last_offset` revision guard rejected an older normal compute.
- `missing` means the Session row disappeared.

The DB writer converts exceptions to a separate `failed` applied signal.

- [ ] **Step 5: Remap snapshot shares inside final metadata apply**

Inside the same `transaction.atomic()` as item metadata, links, and `compute_version`:

1. find every snapshot Share containing `_codex_rollout_migration_anchor`;
2. parse its anchor timestamp;
3. choose the greatest new `line_num` with `SessionItem.timestamp <= anchor`;
4. use `0` only as a fail-closed fallback if no new row matches;
5. set `frozen_at_line`;
6. remove the private anchor;
7. save Share options before commit.

Also set `search_version=None` whenever a background metadata result advances `compute_version`. This covers the paginated/paginated compute-only recovery path.

Ordering by descending `line_num` includes every new row sharing the anchor timestamp.

- [ ] **Step 6: Extend DB-writer run signaling**

Add an optional `asyncio.Queue` to `_ComputeProviderState` and `arm_compute_completion()`.

Emit:

```python
class ComputeApplied(NamedTuple):
    session_id: str
    outcome: Literal["applied", "superseded", "missing", "failed"]
    error: str | None = None
```

Emit it after success, revision rejection, missing row, worker error, or apply exception. Keep the existing run-level completion Future for FIFO drain completion.

- [ ] **Step 7: Run focused tests**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest \
  tests/test_codex_migration_jobs.py \
  tests/test_codex_recompute_persistence.py -q
```

---

### Task 6: Add the per-session gate to migration, watcher, and resume/send

**Files:**

- Create: `src/twicc/providers/codex/migration_gate.py`
- Modify: `src/twicc/providers/sessions_watcher.py`
- Modify: `src/twicc/providers/codex/sessions_watcher.py`
- Modify: `src/twicc/providers/codex/agent/manager.py`
- Create: `tests/test_codex_migration_gate.py`

- [ ] **Step 1: Write concurrency tests before implementation**

Use asyncio Events. Prove:

- A watcher callback that starts first completes before migration enters.
- A watcher callback that starts second waits until migration completes.
- A cold resume that starts first establishes the live agent before migration rechecks.
- A cold resume that starts second waits, then resumes the canonical rollout.
- Unrelated session ids proceed concurrently.
- An active-agent precheck defers without acquiring and holding the gate.
- Cancellation releases the local gate only after the migration child is reaped.
- A `DEAD` transition wakes the scheduler.

- [ ] **Step 2: Implement the gate registry**

Provide:

```python
def gate_for(session_id: str) -> asyncio.Lock: ...
def wake_migration_scheduler() -> None: ...
async def wait_for_migration_wake(stop_event: asyncio.Event, timeout: float) -> None: ...
```

Use process-local state only. Remove an idle lock entry when it has no owner or waiter. Never inspect or delete Codex lock files.

- [ ] **Step 3: Add a provider hook around complete watcher callbacks**

Add a default no-op async context manager to `BaseSessionsWatcher`. Override it in `CodexSessionsWatcher` with `gate_for(parsed.session_id)`.

Hold it from after `parse_session_file()` through:

- initial title fetch;
- `sync_and_broadcast()`;
- Tantivy indexing or reindexing;
- `search_version` update;
- provider post-sync hooks.

This scope makes migration wait for a callback that began first. It also prevents an old callback from reintroducing stale search documents after replacement.

- [ ] **Step 4: Gate all existing-session Codex sends**

Wrap `CodexAgentManager.send_to_session()` with the session gate outside `self._lock`.

Lock order is always:

```text
session migration gate -> CodexAgentManager._lock
```

This includes hardcoded commands and cold resume. It does not gate `create_session()` for a new draft.

- [ ] **Step 5: Wake on DEAD**

After `super()._on_state_change(agent)`, call `wake_migration_scheduler()` when the resulting state is `DEAD`. Keep title reassertion restricted to `ASSISTANT_TURN`.

- [ ] **Step 6: Run focused tests**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest \
  tests/test_codex_migration_gate.py \
  tests/test_codex_ws_responses.py \
  tests/test_codex_runtime.py -q
```

---

### Task 7: Implement the long-lived Codex compute coordinator

**Files:**

- Modify: `src/twicc/providers/background_compute_task.py`
- Create: `src/twicc/providers/codex/background_compute.py`
- Modify: `src/twicc/providers/codex/orchestrator.py`
- Modify: `src/twicc/providers/db_writer.py`
- Create: `tests/test_codex_migration_scheduler.py`

- [ ] **Step 1: Write scheduler tests with injected fakes**

Do not spawn a real Codex migration or touch the real Codex home.

Cover:

- newest ready session selected first;
- exactly one worker command in flight;
- no prefetch before `computed(session_id)`;
- next dispatch after `computed`, without waiting for prior SQL apply;
- migration-required dispatch waits for history replacement Future;
- migration gate remains held through `applied`;
- normal paginated compute `superseded` is successful;
- migrated compute `superseded` is an invariant failure;
- active agent defers immediately;
- `skipped_busy` clears attempt-created anchors and defers;
- a real failure after anchor capture retains anchors for restart recovery;
- timed wake retries external busy writers;
- `DEAD` wakes immediately;
- one real failure attempt per session per TwiCC start;
- other sessions continue after a failure;
- failed sessions retry after a new coordinator instance;
- worker stops when only failed sessions remain stale;
- worker remains idle when only deferred sessions remain;
- initial pass completes when every startup id is applied, deferred, or failed;
- deferred work does not hold global search startup for hours;
- final log summary contains session id, phase, and error;
- cancellation terminates and reaps migration before gate release.
- Codex `failed` for a raw `ResponseItem::Other` leaves the legacy rollout and
  TwiCC history unchanged, then adds the session to `failed_this_run`.

- [ ] **Step 2: Add a direct worker status channel**

Extend `ComputeContext` with a per-run multiprocessing status queue. Keep it separate from the DB-writer result queue.

After `compute_session_metadata()` places its normal payload on the result queue, emit:

```json
{"type":"computed","session_id":"..."}
```

On compute exception, emit the existing DB-writer `error` message and a direct failed status. The main coordinator drains this queue with `asyncio.to_thread()`.

Claude Code can ignore the optional status channel and retain the current bulk enqueue behavior.

- [ ] **Step 3: Implement preparation as an explicit state machine**

Use a result type such as:

```python
class PreparedCandidate(NamedTuple):
    session_id: str
    migration_lease: object | None
    migrated_history: bool


class DeferredCandidate(NamedTuple):
    session_id: str
    reason: Literal["active", "skipped_busy"]


class FailedCandidate(NamedTuple):
    session_id: str
    phase: str
    error: str
```

Preparation order for migration-required sessions:

1. precheck active TwiCC agent;
2. acquire session gate;
3. recheck source mode, DB mode, and active agent;
4. run oversize preflight;
5. capture snapshot anchors through DB writer;
6. run Codex CLI only for legacy source;
7. on `skipped_busy`, clear anchors, release, defer;
8. read the full paginated file from byte zero;
9. submit and await `ReplaceCodexHistoryJob`;
10. invalidate old search documents when search is initialized;
11. return an eligible command while retaining the lease.

On a real failure after step 5, keep the private anchors, release the gate,
and add the session to `failed_this_run`. A later TwiCC start reuses those
anchors. Preflight failures occur before step 5 and therefore create none.

- [ ] **Step 4: Implement candidate bookkeeping**

Track these sets/maps in memory:

- `submitted`
- `computed_not_applied`
- `deferred`
- `failed_this_run`
- migration leases keyed by session id
- failure details keyed by session id

Rescan stale sessions in descending `mtime` after every preparation outcome, direct computed signal, applied signal, DEAD wake, or 30-second external-writer timeout.

Never resubmit a session present in `submitted`, `computed_not_applied`, or `failed_this_run`.

- [ ] **Step 5: Separate initial pass completion from run completion**

The fixed initial progress total contains top-level stale sessions at coordinator start.

Count each initial session once when it becomes:

- successfully applied;
- deferred;
- failed.

Set `CodexOrchestrator.compute_done` after this initial classification pass. Keep the coordinator task and worker alive for deferred sessions.

Disable DB-writer-owned startup progress for this Codex run to avoid double counting. Keep it unchanged for Claude Code.

- [ ] **Step 6: Implement stop conditions**

Send the existing `None` command only when:

- no ready or deferred candidate remains;
- no command is in flight;
- no metadata application is pending.

Sessions in `failed_this_run` do not keep the worker alive. Await the DB-writer run Future after `None` so FIFO results and aggregates are drained.

- [ ] **Step 7: Integrate the Codex orchestrator lifecycle**

Replace `start_background_compute_task()` with the Codex-specific coordinator only in `CodexOrchestrator`.

Shutdown order:

1. stop candidate scheduling;
2. terminate/reap active migration child;
3. release any migration lease;
4. abandon the compute run;
5. stop/reap the CPU worker;
6. cancel the coordinator task;
7. continue existing watcher and agent-manager shutdown.

- [ ] **Step 8: Run scheduler and lifecycle tests**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest \
  tests/test_codex_migration_scheduler.py \
  tests/test_codex_migration_gate.py \
  tests/test_codex_migration_jobs.py \
  tests/test_codex_recompute_persistence.py -q
```

---

### Task 8: Enforce readiness in owner UI, public shares, and search

**Files:**

- Create: `frontend/src/composables/wsSessionItems.js`
- Create: `frontend/src/composables/wsSessionItems.test.js`
- Modify: `frontend/src/composables/useWebSocket.js`
- Modify: `src/twicc/share/session_views.py`
- Modify: `src/twicc/share/consumer.py`
- Modify: `src/twicc/core/serializers.py`
- Modify: `frontend/src/share-session/ShareSessionApp.vue`
- Modify: `src/twicc/search.py`
- Modify: `src/twicc/search_indexing_task.py`
- Modify: `src/twicc/cli/run.py`
- Modify: `tests/test_share_public_routes.py`
- Modify: `tests/test_share_consumer.py`
- Add or modify: search indexing tests covering obsolete sessions

- [ ] **Step 1: Extract and test the owner WebSocket mutation**

```js
export function applySessionItemsAdded(store, message) {
    const session = store.getSession(message.session_id)
    if (session?.compute_version_up_to_date === false) return false
    if (message.items?.length) {
        store.markItemsLive(message.session_id, message.items.map(item => item.line_num))
    }
    if (store.areSessionItemsFetched(message.session_id)) {
        store.addSessionItems(message.session_id, message.items, message.updated_metadata)
    }
    return true
}
```

Test that an obsolete session calls neither `markItemsLive` nor `addSessionItems`. Keep current behavior for ready and not-yet-known sessions.

In `useWebSocket.js`, skip the subsequent viewed notification when the helper returns `false`.

- [ ] **Step 2: Add public share readiness helpers**

Use the provider helper's `current_compute_version`. Do not hard-code 48 in share code.

Rules:

- The share page remains HTTP 200 and renders a generic preparation message.
- The message tells the viewer to refresh later.
- The page does not call item APIs.
- The page does not poll.
- The page does not open the share WebSocket.
- Root and subagent content APIs return `409` with `{"error":"session_not_ready"}`.
- The share consumer refuses an obsolete root at connection.
- The share consumer suppresses events for an obsolete descendant.
- Snapshot token and options remain unchanged apart from the internal remap.

Expose `ready: false` in public page metadata only. Do not expose the private snapshot anchor.

- [ ] **Step 3: Prevent stale search documents from becoming visible**

This closes a readiness gap found during plan review. A stale Tantivy document contains both old content and invalid old line numbers.

Before `search_index_ready.set()` in the global search lifecycle:

1. query sessions whose stored compute version differs from their provider's current version;
2. delete all Tantivy documents for those session ids;
3. commit once.

Also change `search.reindex_session()` and `_index_session()` to delete and skip a session while its compute version is obsolete. Do not mark its `search_version` current.

Add `request_session_reindex(session_id)` to `search_indexing_task.py`. It owns one process-local pending-id set and one task. It coalesces duplicate ids and uses the existing indexing run lock. Many migrated sessions must not create many queued full sweeps.

After `applied` or a normal compute `superseded` outcome, request that session's full reindex. Run the request even when a live watcher already marked `search_version` current. Startup removed the old complete document set, while the watcher indexed only its new suffix.

- [ ] **Step 4: Test owner, share, and search readiness**

Required assertions:

- Owner stale event leaves live-line state unchanged.
- Owner false-to-true readiness transition uses the existing REST load path.
- No migration-specific `unloadSession()` call exists.
- Public stale page includes the manual refresh instruction.
- Every stale root/subagent API returns no content.
- Stale share WebSocket does not forward items or tool state.
- Search startup removes old stale documents.
- A concurrent indexing pass cannot re-add a stale session.
- A later successful apply requests and completes a canonical reindex.
- Reindex requests for many completed sessions coalesce behind one task.

- [ ] **Step 5: Run tests and rebuild standalone share assets**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && npm run build
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest \
  tests/test_share_public_routes.py \
  tests/test_share_consumer.py -q
```

---

### Task 9: Activate Codex 0.151 and complete end-to-end verification

**Files:**

- Modify: `src/twicc/providers/codex/runtime.py`
- Modify: `src/twicc/settings.py`
- Modify: `pyproject.toml`
- Modify: `docs/codex-vendoring.md`
- Modify: `src/openai_codex/generated/v2_all.py` only if the retained local patch comment needs the new version reference
- Modify: `CHANGELOG.md`
- Add end-to-end fixture coverage to: `tests/test_codex_migration_scheduler.py`

- [ ] **Step 1: Confirm the vendored SDK delta before changing it**

The local source comparison currently shows no upstream Python SDK change between the two tags:

```bash
git -C /home/twidi/dev/codex diff --name-status rust-v0.150.1..rust-v0.151.0 -- sdk/python
```

If it remains empty, do not replace `src/openai_codex/`. Update only the documented source tag and retain the local `SubAgentActivityKind.completed` patch.

If it is no longer empty, follow `docs/codex-vendoring.md` completely. Reapply only local patches still absent upstream. Then run the SDK compatibility checklist in that document.

- [ ] **Step 2: Pin and verify the 0.151 runtime**

Set:

```python
CODEX_VERSION = "0.151.0"
CODEX_RELEASE_TAG = "rust-v0.151.0"
```

Download the four release wheels and replace every SHA-256 in `_WHEELS`:

```bash
TAG=rust-v0.151.0
V=0.151.0
for w in manylinux_2_17_x86_64 manylinux_2_17_aarch64 macosx_11_0_arm64 macosx_10_9_x86_64; do
  f=openai_codex_cli_bin-$V-py3-none-$w.whl
  printf '%s  ' "$f"
  curl -sL "https://github.com/openai/codex/releases/download/$TAG/$f" | sha256sum
done
```

Update the tag references in `pyproject.toml` and `docs/codex-vendoring.md`.

- [ ] **Step 3: Bump only the Codex compute version**

Set `CODEX_COMPUTE_VERSION = 48` with a concise comment describing canonical paginated history and automatic legacy rollout migration.

Do not bump `CURRENT_SEARCH_VERSION`. The search schema does not change; per-session `search_version=None` and stale-document deletion drive rebuilds.

- [ ] **Step 4: Add a fake-binary end-to-end recovery test**

The fake binary must:

1. receive the exact command line;
2. atomically rewrite a legacy fixture to paginated canonical JSONL;
3. output the exact Codex JSON report;
4. let the coordinator replace history;
5. let the worker compute canonical metadata;
6. let the DB writer atomically apply links, share remap, and compute version;
7. verify canonical search reindex request;
8. verify gate release.

Add crash checkpoints:

- before Codex publish: legacy source + legacy DB;
- after Codex publish: paginated source + legacy DB;
- after history replacement: paginated source + paginated DB + stale compute version;
- after final apply: current compute version and no private anchor.

Each restart must select the correct row of the source/DB decision matrix.

Add a separate fake-binary failure case for `ResponseItem::Other`. Assert that
Codex publishes no partial canonical file, TwiCC replaces no history, and the
session stays stale for the rest of that TwiCC start.

- [ ] **Step 5: Add the Unreleased changelog entry**

Re-read the top of `CHANGELOG.md` first. Edit only `## [Unreleased]`.

Update the existing Codex runtime line from `0.150.1` to `0.151.0`. Add a Changed entry explaining that TwiCC migrates old Codex session history automatically and keeps sessions unavailable until their rebuild finishes.

- [ ] **Step 6: Run targeted lint and complete test suites**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && uvx ruff check \
  src/twicc/providers/background_compute_task.py \
  src/twicc/providers/codex \
  src/twicc/providers/db_writer.py \
  src/twicc/providers/compute_base.py \
  src/twicc/providers/sessions_watcher.py \
  src/twicc/share \
  src/twicc/search.py \
  src/twicc/search_indexing_task.py \
  tests/test_codex_canonical.py \
  tests/test_codex_rollout_migration.py \
  tests/test_codex_migration_jobs.py \
  tests/test_codex_migration_gate.py \
  tests/test_codex_migration_scheduler.py

cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && TWICC_DATA_DIR=$PWD uv run pytest
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && npm test
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format/frontend && npm run build
```

- [ ] **Step 7: Run final static invariants**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && rg -n \
  'payload\.message|patch_apply_end|mcp_tool_call_end|image_generation_end|sub_agent_activity' \
  src/twicc/providers/codex frontend/src/providers/codex \
  frontend/src/components/session/detail/items/codex

cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && rg -n \
  '_codex_rollout_migration_anchor|CODEX_COMPUTE_VERSION|CODEX_VERSION|CODEX_RELEASE_TAG' \
  src tests docs pyproject.toml
```

Review every legacy-name match. Only migration input code, migration tests, historical comments, and the audit document may retain legacy names.

- [ ] **Step 8: Build the release artifact**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/codex-new-format && ./scripts/build-release.sh
```

Confirm the wheel contains `src/openai_codex`, the built frontend assets, and the updated runtime metadata.

Do not start a real migrated instance as an automated verification step. The first real startup mutates the developer's Codex rollouts and remains a user-controlled release test.

---

## Final acceptance checklist

- [ ] New Codex sessions use canonical paginated history.
- [ ] Known legacy sessions migrate automatically at TwiCC startup.
- [ ] Obsolete sessions never load transcript items in owner or public viewers.
- [ ] Obsolete sessions expose no stale search snippets.
- [ ] The six accepted canonical semantic roles preserve current TwiCC behavior.
- [ ] Unsupported canonical items do not create new UI behavior.
- [ ] No legacy source reader remains.
- [ ] Private TwiCC rewrites are schema-valid canonical completed items.
- [ ] `skill` and `mention` stay raw and invisible.
- [ ] Every structural rebuild starts at byte zero.
- [ ] Snapshot shares remap by timestamp without token changes or owner action.
- [ ] Historical `original_files` loss falls back to canonical patch hunks.
- [ ] Oversized legacy records fail before any source or TwiCC DB mutation.
- [ ] Malformed, blank, partial, retired, and empty-assistant loss follows the accepted policy.
- [ ] Busy sessions defer; failed sessions wait for the next TwiCC start.
- [ ] One session's migration never blocks unrelated sessions.
- [ ] Shutdown leaves no migration child or CPU worker behind.
- [ ] Claude Code compute behavior remains unchanged.
- [ ] Runtime, vendored SDK documentation, compute version, changelog, tests, and release artifact all agree on Codex `0.151.0`.
