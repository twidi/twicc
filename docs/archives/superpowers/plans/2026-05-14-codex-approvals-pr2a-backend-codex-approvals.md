# Codex Approvals — PR2a — Backend Codex approvals + permission modes (bypass conservé)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the complete Codex approval pipeline (monkey-patched sync handler → async coroutine → `_await_pending_request` → WS validation → wire response) and the 4-preset `permission_mode` mapping, while keeping `DEFAULT_MODE = "yolo"` so user-visible behaviour is unchanged. PR2b later flips the default to `"auto"` and adds the frontend stub. PR3 adds the 5th preset (`strict`).

**Architecture:** Two new isolated modules (`permission_modes.py`, `approvals.py`) carry pure-Python helpers. `CodexAgent.__init__` monkey-patches the SDK's private sync handler at `codex._client._sync._approval_handler` and `start()` captures the running loop. The sync handler bridges to async via `asyncio.run_coroutine_threadsafe(...).result()`, catches `CancelledError` and returns a safe wire default. A side-table `_items_by_id` populated on `item/started` lets us inject the diff payload into `fileChange` `PendingRequest`s. `interrupt_or_kill` calls `_cancel_all_pending_futures()` BEFORE `codex.close()` to break the `_transport_lock` deadlock. The Codex WS handler validates the user's decision payload strictly. The manager replaces its hardcoded bypass with `resolve_codex_policy(settings.permission_mode)` — but since `DEFAULT_MODE = "yolo"` is `(danger_full_access, never)`, sessions without explicit mode behave exactly like before.

**Tech Stack:** Python ≥ 3.13, Django 6, ruff (line-length=120). No new dependencies. Vanilla asyncio. Codex SDK vendored at `src/codex_app_server/`.

**Reference spec:** `docs/superpowers/specs/2026-05-14-codex-approvals-design.md` — §4 Étape 2 (`CodexApprovalBridge`), §4 Étape 3 (WS handler), §4 Étape 4 (`_items_by_id`), §4 Étape 7 (permission modes), §7-Q13 (5-PR rollout), §9 (wire formats).

**PR2a acceptance criteria** (from spec §7-Q13 + acceptance criteria):
- All new code compiles and loads. Backend boots cleanly.
- A Codex session started **without** an explicit `permission_mode` (i.e. `null`) tunes to `DEFAULT_MODE = "yolo"` → `sandbox=danger_full_access` + `approval_policy=never`, strictly identical to today.
- No approval UI ever fires (it's PR2b that flips the default and adds the frontend).
- `_PRESET_MAP` exists with the 4 modes (`read_only` / `auto` / `autonomous` / `yolo`). The 5th (`strict`) is intentionally absent until PR3.
- The monkey-patch handler is installed. WS handler routes `codex:pending_request_response` and validates strictly, but is never solicited during normal use in PR2a.
- Claude sessions: **inchangées**.

---

## File Structure

### Files created

| File | Why |
|------|-----|
| `src/twicc/providers/codex/agent/approvals.py` | Codex-specific approval helpers: `APPROVAL_METHODS`, `is_approval_method`, `derive_request_id`, `make_pending_request`, `default_response_for`. Pure functions, importable in isolation. |
| `src/twicc/providers/codex/permission_modes.py` | Mapping from preset string (the value the frontend stores in `Session.permission_mode`) to the `(SandboxMode, AskForApproval)` couple the SDK expects. Single source of truth. |

### Files modified

| File | Why |
|------|-----|
| `src/twicc/providers/codex/agent/agent.py` | Add `_items_by_id` side-table + lifecycle (init/update/pop/clear), capture `_loop` in `start()`, capture `_sdk_default_approval_handler` and monkey-patch in `__init__`, add `_sync_approval_handler` + `_async_approval_handler` + `_enrich_params_with_item_payload`, cancel pending futures in `interrupt_or_kill` before `codex.close()`. |
| `src/twicc/providers/codex/agent/manager.py` | Replace the hardcoded `approval_policy="never"` / `sandbox=danger_full_access` bypass with a call to `resolve_codex_policy(settings.permission_mode)`. Update module docstring + `_create_agent` docstring (no more "approvals are bypassed at the server level"). |
| `src/twicc/providers/codex/ws.py` | Add `pending_request_response` route in `dispatch()`. Add `_handle_pending_request_response` + `_build_codex_response` (strict validation). |

### Files NOT touched

- `src/twicc/providers/codex/credentials.py` — keeps its local bypass on the throwaway auth call (internal, never user-facing). Just clarify in a comment if convenient.
- `src/twicc/providers/codex/title_suggest.py` — keeps its local bypass for title generation (internal turn, no tool calls).
- `src/codex_app_server/**` — vendored SDK, untouched. We monkey-patch at runtime, not in source.
- `frontend/**` — no frontend changes in PR2a. The dispatcher, stub `PendingRequestBody`, and `strict` mode wait for PR2b/PR3.
- `src/twicc/agent/**` — already factorized in PR1.
- `src/twicc/providers/claude_code/**` — out of scope.

---

## How to run / verify each step

This refactor has no automated tests (project convention: "no tests and no linting" — see `CLAUDE.md`). Per-step verification is by `ast.parse` (compilation check) + import sanity, and the final task is a user-assisted E2E smoke test.

For Python syntax verification between edits:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('PATH').read()); print('OK')"
```

For import sanity (catches missing references the parser tolerates):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "from twicc.providers.codex.agent import approvals; print(approvals.APPROVAL_METHODS)"
```

(Always prefix `TWICC_DATA_DIR=$PWD` for `python -c` invocations from a worktree — see `CLAUDE.md` "Running Python / Django code in a worktree without devctl".)

For final E2E smoke (Task 7): the user restarts the backend, opens an existing Codex session, sends a message, and checks the session still works exactly like before. **No approval banner should ever appear in PR2a** — the default mode is still `"yolo"`.

---

## Task 1: Create `src/twicc/providers/codex/permission_modes.py`

**Files:**
- Create: `src/twicc/providers/codex/permission_modes.py`

A small, isolated module — easier to land first because nothing else depends on it yet, and the spec § Étape 7 specifies the mapping completely. The module is the canonical translation from the preset string (`Session.permission_mode`) to the SDK pair `(SandboxMode, AskForApproval)`.

In PR2a we ship **4** modes. PR3 adds `"strict"` (it'd be a 1-line `_PRESET_MAP` insertion).

- [ ] **Step 1.1: Create the new file with the mapping and helper**

Write `src/twicc/providers/codex/permission_modes.py`:

```python
"""
Map the user-facing ``Session.permission_mode`` preset (single string) to the
``(SandboxMode, AskForApproval)`` couple that the Codex SDK expects at
``thread_start`` / ``thread_resume``.

The four modes here are intentionally the same set the frontend exposes today
(``frontend/src/providers/codex/constants.js``). The 5th mode ``strict`` is
added in a later PR.

Wire / preset table (kept in sync with the spec ``§4 Étape 7``):

+-------------+-------------------+--------------------+-----------+----------------+
| Mode (wire) | sandbox_mode      | approval_policy    | Prompts?  | Can write?     |
+=============+===================+====================+===========+================+
| read_only   | read-only         | on-request         | yes       | no             |
| auto        | workspace-write   | on-request         | yes       | workspace only |
| autonomous  | workspace-write   | never              | no        | workspace only |
| yolo        | danger-full-access| never              | no        | anywhere       |
+-------------+-------------------+--------------------+-----------+----------------+

``DEFAULT_MODE`` is the value used when ``Session.permission_mode`` is unset
(``None``) or unknown. In PR2a we ship ``"yolo"`` to preserve the current
behaviour: every Codex session that doesn't have an explicit mode keeps
running with the bypass. PR2b flips this to ``"auto"`` once the frontend
banner is wired.
"""

from __future__ import annotations

from codex_app_server import AskForApproval, SandboxMode

# Preset wire value → (SandboxMode enum, AskForApproval enum)
#
# AskForApproval is a Pydantic RootModel union; the wire strings live in
# ``codex_app_server.generated.v2_all``. We use AskForApproval(value) for
# the explicit string variants ("on-request", "never") — that constructor
# accepts a raw string and round-trips through validation.
_PRESET_MAP: dict[str, tuple[SandboxMode, AskForApproval]] = {
    "read_only":  (SandboxMode.read_only,           AskForApproval("on-request")),
    "auto":       (SandboxMode.workspace_write,     AskForApproval("on-request")),
    "autonomous": (SandboxMode.workspace_write,     AskForApproval("never")),
    "yolo":       (SandboxMode.danger_full_access,  AskForApproval("never")),
}

# PR2a ships this as ``"yolo"`` so existing Codex sessions keep behaving like
# the current bypass. PR2b flips it to ``"auto"`` (workspace-write +
# on-request).
DEFAULT_MODE = "yolo"


def resolve_codex_policy(mode: str | None) -> tuple[SandboxMode, AskForApproval]:
    """Return the ``(sandbox, approval_policy)`` for a preset.

    Unknown / missing mode falls back to ``DEFAULT_MODE``. The two callers
    that matter are :meth:`CodexAgentManager._create_agent` (thread_start /
    thread_resume) and any future place that needs to query the active
    policy for telemetry.
    """
    return _PRESET_MAP.get(mode or DEFAULT_MODE, _PRESET_MAP[DEFAULT_MODE])
```

- [ ] **Step 1.2: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/permission_modes.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 1.3: Sanity-check imports resolve and the map looks right**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.permission_modes import _PRESET_MAP, DEFAULT_MODE, resolve_codex_policy
assert set(_PRESET_MAP.keys()) == {'read_only', 'auto', 'autonomous', 'yolo'}, _PRESET_MAP.keys()
assert DEFAULT_MODE == 'yolo'
sandbox, policy = resolve_codex_policy(None)
assert sandbox.value == 'danger-full-access', sandbox
print('OK', sandbox, policy)
"
```

Expected: `OK SandboxMode.danger_full_access AskForApproval(root='never')` (or similar — the exact repr depends on Pydantic version; what matters is no crash and `sandbox.value == 'danger-full-access'`).

- [ ] **Step 1.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/permission_modes.py
git commit -m "$(cat <<'EOF'
feat(codex): add permission_mode preset → SDK policy mapping

Single source of truth for translating the frontend's permission_mode
preset string (the value stored in Session.permission_mode) into the
(SandboxMode, AskForApproval) couple thread_start / thread_resume
expects. Ships PR2a with 4 modes (read_only / auto / autonomous / yolo).
DEFAULT_MODE = "yolo" preserves the current bypass for existing sessions
without an explicit mode; PR2b flips it to "auto".

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Create `src/twicc/providers/codex/agent/approvals.py`

**Files:**
- Create: `src/twicc/providers/codex/agent/approvals.py`

The pure helpers for the approval flow. Three responsibilities:
1. Whitelist of Codex server-request methods we recognise as approvals (vs. delegated to the SDK default).
2. Convert the wire payload into a `PendingRequest` (provider-neutral type from PR1).
3. Produce a safe wire default when the future is cancelled (kill / shutdown / transport error).

No SDK state here — that lives on `CodexAgent`. The reason `approvals.py` is separate is twofold: (a) the constants & helpers are pure-functional and easy to read in isolation; (b) it keeps `agent.py` lighter.

- [ ] **Step 2.1: Create the new file**

Write `src/twicc/providers/codex/agent/approvals.py`:

```python
"""
Codex approval helpers — pure functions translating between the Codex
JSON-RPC wire format and TwiCC's provider-neutral ``PendingRequest``.

The 3 approval methods Codex sends as ``server requests`` (i.e. with an
``id``, requiring a synchronous response):

- ``item/commandExecution/requestApproval`` — shell exec / sub-exec / network
- ``item/fileChange/requestApproval`` — ApplyPatch (one or more file changes)
- ``item/permissions/requestApproval`` — model asks for extra filesystem / network permissions

Other server requests (``item/tool/call``, ``account/chatgptAuthTokens/refresh``,
``item/tool/requestUserInput``, ``mcpServer/elicitation/request``) are NOT
ours to handle in PR2a — the wiring in :class:`CodexAgent` delegates them to
the SDK's default sync handler (captured before we monkey-patch). See spec
``§1.6`` and ``§7-Q9``.

Wire details: see spec ``§1.1.{a,b,c}``. Decision types: ``§9`` (annex).
"""

from __future__ import annotations

import time
import uuid

from twicc.agent.states import PendingRequest

# Method (wire) → human-readable tool_name we expose in PendingRequest.
# The tool_name is what the frontend dispatches on (in a later PR) to pick
# the right body component. Keeping it short and hyphen-free.
APPROVAL_METHODS: dict[str, str] = {
    "item/commandExecution/requestApproval": "commandExecution",
    "item/fileChange/requestApproval":       "fileChange",
    "item/permissions/requestApproval":      "permissions",
}

# Wire response used by the sync handler when the future is cancelled
# (typical: kill while waiting on a user click). Sending ``decline``
# instead of ``cancel`` keeps the turn alive — the model can recover or
# pick another approach instead of being aborted whole-cloth.
_DEFAULT_KILL_RESPONSE_COMMAND_OR_FILE: dict = {"decision": "decline"}

# Permissions has a different wire shape — see spec ``§1.1.c``.
_DEFAULT_KILL_RESPONSE_PERMISSIONS: dict = {
    "permissions": {},  # empty granted profile = nothing accorded
    "scope": "turn",
}


def is_approval_method(method: str) -> bool:
    """Return True if ``method`` is one of the 3 approval RPCs we own."""
    return method in APPROVAL_METHODS


def derive_request_id(params: dict | None) -> str:
    """Build a stable key to route the user response back to the right future.

    Codex sometimes fans a single ``itemId`` (e.g. an ``ExecCommandBegin``)
    into several sub-exec approvals, each carrying its own ``approvalId``
    (vérifié dans the schema description, ``ServerRequest.json:345-442``).
    Prefer ``approvalId`` when present, fall back to ``itemId``, and as a
    last-ditch produce a UUID so we never collide on empty payloads.
    """
    if not params:
        return str(uuid.uuid4())
    candidate = params.get("approvalId") or params.get("itemId")
    return candidate if isinstance(candidate, str) and candidate else str(uuid.uuid4())


def make_pending_request(method: str, params: dict | None) -> PendingRequest:
    """Translate a Codex server-request into the provider-neutral PendingRequest.

    Callers are expected to enrich ``params`` upstream if they want to
    attach side-band info — e.g. the streamed item payload for
    ``fileChange`` (which carries the diff). See
    :meth:`CodexAgent._enrich_params_with_item_payload`.
    """
    tool_name = APPROVAL_METHODS[method]
    return PendingRequest(
        request_id=derive_request_id(params),
        request_type="tool_approval",  # Codex never uses ``ask_user_question``
        tool_name=tool_name,
        tool_input=dict(params) if params else {},
        created_at=time.time(),
        permission_suggestions=None,
    )


def default_response_for(method: str) -> dict:
    """Wire response we send to Codex when we cannot route the request to a user.

    Triggered by the sync handler's ``CancelledError`` branch on kill /
    transport teardown — NOT by user-initiated ``Cancel turn`` (that goes
    through ``resolve_pending_request`` with the real wire decision).

    Returns a shape that's valid for the requested ``method``:
    - command / file: ``{"decision": "decline"}``
    - permissions:    ``{"permissions": {}, "scope": "turn"}``
    """
    if method == "item/permissions/requestApproval":
        return dict(_DEFAULT_KILL_RESPONSE_PERMISSIONS)
    return dict(_DEFAULT_KILL_RESPONSE_COMMAND_OR_FILE)
```

- [ ] **Step 2.2: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/agent/approvals.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 2.3: Sanity-check the imports resolve and helpers behave**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.agent.approvals import (
    APPROVAL_METHODS, is_approval_method, derive_request_id,
    make_pending_request, default_response_for,
)
assert set(APPROVAL_METHODS.keys()) == {
    'item/commandExecution/requestApproval',
    'item/fileChange/requestApproval',
    'item/permissions/requestApproval',
}
assert is_approval_method('item/commandExecution/requestApproval') is True
assert is_approval_method('item/tool/call') is False
assert derive_request_id({'approvalId': 'A1'}) == 'A1'
assert derive_request_id({'itemId': 'I1'}) == 'I1'
assert derive_request_id({'approvalId': 'A1', 'itemId': 'I1'}) == 'A1'  # approvalId wins
assert derive_request_id(None).count('-') == 4  # UUID4
pr = make_pending_request('item/commandExecution/requestApproval', {'itemId': 'I9', 'command': 'ls'})
assert pr.tool_name == 'commandExecution'
assert pr.request_type == 'tool_approval'
assert pr.tool_input == {'itemId': 'I9', 'command': 'ls'}
assert default_response_for('item/commandExecution/requestApproval') == {'decision': 'decline'}
assert default_response_for('item/permissions/requestApproval') == {'permissions': {}, 'scope': 'turn'}
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 2.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/approvals.py
git commit -m "$(cat <<'EOF'
feat(codex): add pure helpers for the 3 Codex approval methods

approvals.py owns the wire ↔ PendingRequest translation:
- APPROVAL_METHODS: the 3 methods we route to a user (command, file, permissions)
- is_approval_method: gate for the sync handler's whitelist
- derive_request_id: approvalId > itemId > uuid fallback
- make_pending_request: build the provider-neutral type from raw params
- default_response_for: safe wire fallback when a future is cancelled

Pure functions, no SDK state — bridge/wiring is in agent.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Side-table `_items_by_id` in `CodexAgent`

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py`

The `fileChange` approval payload does NOT carry the diff (verified, spec §1.1.b). To make it available to the frontend (in PR2b/PR3), the backend must index the streamed `item/started` payload by `itemId` and join it back when the approval arrives.

This task wires the side-table only — it does **not** yet read from it. The reads come in Task 4 (`_async_approval_handler` enrichment).

- [ ] **Step 3.1: Add `_items_by_id` initialization in `__init__`**

In `CodexAgent.__init__` (currently lines 82-104), after `self._reasoning_summary_indices: dict[str, set[int]] = {}` (currently line 104), add:

```python
        # Side-table for ``item/started`` payloads, indexed by ``itemId``.
        # Used to inject the diff into ``fileChange`` PendingRequests (the
        # approval payload itself doesn't carry it — see spec §1.1.b).
        # Populated on ``item/started``, popped on ``item/completed``,
        # cleared on ``interrupt_or_kill``.
        self._items_by_id: dict[str, dict] = {}
```

- [ ] **Step 3.2: Populate on `item/started`**

In `_handle_stream_event`, the `if method == "item/started":` branch is currently lines 421-432. It only handles the `agentMessage` case and `return`s early for everything else.

We need to keep that early return for `agentMessage` (it does its job and returns), but **also** capture the raw payload for *any* item kind into the side-table. Reorganise the branch so capture happens BEFORE the agent-message-specific handling:

Replace:

```python
        if method == "item/started":
            agent_msg = _agent_message_item(payload)
            if agent_msg is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_start",
                "session_id": self.session_id,
                "message_id": agent_msg.id,
                "block_index": 0,
                "block_type": "text",
            })
            return
```

with:

```python
        if method == "item/started":
            # Capture the raw inner payload first so any ``itemId`` is indexed,
            # regardless of item kind. ``fileChange`` approvals later in the
            # turn read this side-table to grab the diff.
            item = getattr(payload, "item", None)
            if item is not None:
                inner = getattr(item, "root", item)
                item_id = getattr(inner, "id", None)
                if item_id:
                    self._items_by_id[item_id] = inner.model_dump(
                        mode="json", by_alias=True,
                    )

            # Existing agent-message streaming logic — only this kind paints
            # a live ``stream_block_start`` event today; other kinds flow
            # through the JSONL → watcher path.
            agent_msg = _agent_message_item(payload)
            if agent_msg is None:
                return
            await self._broadcast_stream_event({
                "type": "stream_block_start",
                "session_id": self.session_id,
                "message_id": agent_msg.id,
                "block_index": 0,
                "block_type": "text",
            })
            return
```

Note: `inner.model_dump(mode="json", by_alias=True)` works because every `ThreadItem` variant in the SDK is a Pydantic model. `by_alias=True` matches the wire format expected if the dict ever leaves the backend; `mode="json"` keeps datetimes, enums, etc. wire-compatible.

- [ ] **Step 3.3: Pop on `item/completed`**

In `_handle_stream_event`, the `if method == "item/completed":` branch is currently lines 499-559. It currently handles `agentMessage` and `reasoning`. For any other item kind it implicitly falls through (no `return`, no `pop`).

After computing `item_type` (currently line 504: `item_type = getattr(inner, "type", None)`), add a single `pop` line. This runs **regardless of the kind** — completed items are done, we don't need their payload anymore. Then the existing kind-specific blocks continue.

Replace:

```python
        if method == "item/completed":
            item = getattr(payload, "item", None)
            if item is None:
                return
            inner = getattr(item, "root", item)
            item_type = getattr(inner, "type", None)

            if item_type == "agentMessage":
```

with:

```python
        if method == "item/completed":
            item = getattr(payload, "item", None)
            if item is None:
                return
            inner = getattr(item, "root", item)
            item_type = getattr(inner, "type", None)
            # The side-table entry is no longer needed once the item is
            # finalized (see ``_items_by_id`` in __init__). Pop is
            # idempotent — items we never saw started don't show up here.
            item_id_for_cleanup = getattr(inner, "id", None)
            if item_id_for_cleanup:
                self._items_by_id.pop(item_id_for_cleanup, None)

            if item_type == "agentMessage":
```

- [ ] **Step 3.4: Clear in `interrupt_or_kill`**

In `interrupt_or_kill` (currently lines 284-344), after the existing `get_streamed_item_registry().clear_session(self.session_id)` call (currently line 339), add one line:

```python
        # Drop the side-table — no more turns will read it on this agent.
        self._items_by_id.clear()
```

- [ ] **Step 3.5: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/agent/agent.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 3.6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/agent.py
git commit -m "$(cat <<'EOF'
feat(codex): index streamed item payloads by id (side-table for fileChange diff)

The Codex v2 approval payload for fileChange doesn't carry the diff;
only the itemId that correlates to a prior item/started ApplyPatch event.
This indexes those events by itemId so the (upcoming) async approval
handler can join the diff into the PendingRequest payload.

Update on item/started, pop on item/completed (any kind, since the table
is shared), clear on interrupt_or_kill. No callers yet — wired in the
next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Wire `CodexAgent` — monkey-patch + sync/async approval handlers + cancel on kill

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py`

This is the heart of PR2a. The wiring has 5 pieces, all in `CodexAgent`:

1. Capture the SDK's default sync handler in `__init__` (before we monkey-patch).
2. Monkey-patch `codex._client._sync._approval_handler` to point at `self._sync_approval_handler`.
3. Capture the running event loop in `start()`.
4. Implement `_sync_approval_handler` (runs in SDK worker thread) and `_async_approval_handler` (runs in main loop) + the `_enrich_params_with_item_payload` helper.
5. In `interrupt_or_kill`, cancel pending futures **before** `codex.close()` (otherwise `_transport_lock` deadlocks).

The first 4 are dormant under PR2a's `DEFAULT_MODE = "yolo"` — Codex never sends approvals when `approval_policy="never"`. The plumbing is in place for PR2b's behavioural switch.

- [ ] **Step 4.1: Add the necessary imports**

At the top of `src/twicc/providers/codex/agent/agent.py` (currently lines 12-37), the existing imports are:

```python
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, ClassVar
...
from twicc.agent import AgentState, BaseAgent, StateChangeCallback
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings

from ..streaming_registry import get_streamed_item_registry
```

Add after `from ..streaming_registry import get_streamed_item_registry`:

```python
from .approvals import (
    APPROVAL_METHODS,
    default_response_for,
    is_approval_method,
    make_pending_request,
)
```

- [ ] **Step 4.2: Capture loop slot + monkey-patch in `__init__`**

In `CodexAgent.__init__`, immediately after the new `self._items_by_id: dict[str, dict] = {}` line from Task 3, add:

```python
        # Captured lazily in ``start()`` — that's the first place we're
        # guaranteed to be inside a running asyncio loop. The SDK's worker
        # threads dispatch approval callbacks back to this loop via
        # ``asyncio.run_coroutine_threadsafe``.
        self._loop: asyncio.AbstractEventLoop | None = None

        # Capture the SDK's *default* sync approval handler BEFORE we
        # monkey-patch our own in. The default auto-accepts the 2 methods
        # it recognises and returns ``{}`` for others (see vendored
        # ``codex_app_server/client.py:480-485``). We delegate to it for
        # server requests we don't own (item/tool/call,
        # account/chatgptAuthTokens/refresh, …) — see spec §1.6, §7-Q9.
        # PRIVATE SDK API — see memory ``reference_codex_sdk_update_procedure.md``
        # for the upgrade checklist (this attribute path must hold).
        self._sdk_default_approval_handler = (
            self._codex._client._sync._approval_handler
        )
        # Replace the SDK's stub with our bridge. Must happen here, BEFORE
        # any ``thread_start`` / ``thread_resume`` runs (Codex could ship
        # the first approval immediately).
        self._codex._client._sync._approval_handler = self._sync_approval_handler
```

The ordering matters: this must come AFTER `self._codex = codex` is set (currently line 92), and BEFORE the manager calls `start()`. Since `__init__` is short and `self._codex = codex` is one of the first lines, this is naturally satisfied.

- [ ] **Step 4.3: Capture the running loop in `start()`**

`start()` is currently lines 126-150. The body starts with:

```python
        self._state_change_callback = on_state_change

        # Flip to ASSISTANT_TURN immediately so the UI gates the input as
        # "working" — the actual turn runs in the background task below.
        self._set_state(AgentState.ASSISTANT_TURN)
```

Insert ONE line right after `self._state_change_callback = on_state_change`:

```python
        # First place we're guaranteed to be inside a running loop. Captured
        # so the SDK's worker threads can resume our coroutines back here
        # via ``asyncio.run_coroutine_threadsafe`` (see ``_sync_approval_handler``).
        self._loop = asyncio.get_running_loop()
```

- [ ] **Step 4.4: Add the bridge methods**

`CodexAgent` ends with `_broadcast_stream_event` at line 561+. Insert a new section right before `_broadcast_stream_event` (and after the `_handle_stream_event` method that ends around line 559):

```python
    # ------------------------------------------------------------------
    # Approval handlers (sync ↔ async bridge)
    # ------------------------------------------------------------------

    def _sync_approval_handler(self, method: str, params: dict | None) -> dict:
        """Called by the SDK from a worker thread (via ``asyncio.to_thread``).

        Bridges the SDK's blocking expectation (``Callable -> dict``) to our
        async ``_await_pending_request``. Approvals we don't own (MCP, OAuth
        refresh, ...) delegate to the captured SDK default. Cancellation —
        typically from ``_cancel_all_pending_futures()`` on kill — is
        converted into a safe wire default so the SDK's read loop doesn't
        hang.

        See spec §2.4 + §5.1 for the full call chain.
        """
        if not is_approval_method(method):
            # Defensive fallback: log + delegate. The SDK default returns
            # ``{}`` for unknown methods which might break Codex; for the 2
            # approval methods it knows it returns ``{"decision": "accept"}``,
            # which is safer than crashing the read loop. PR2a does not
            # naturally exercise this path — the warning is here to flag
            # an unsupported server request the day it shows up.
            logger.warning(
                "Unhandled Codex server request method=%r (delegating to SDK default)",
                method,
            )
            return self._sdk_default_approval_handler(method, params)

        if self._loop is None or self._loop.is_closed():
            # Approval before ``start()`` ran, or after the loop was torn
            # down. Either way we can't bridge to async; return a safe
            # wire default so the SDK doesn't hang.
            logger.error(
                "Codex approval received before loop init or after close: method=%r",
                method,
            )
            return default_response_for(method)

        coro = self._async_approval_handler(method, params)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, self._loop)
            return future.result()
        except asyncio.CancelledError:
            # Pending future was cancelled (kill, transport teardown). The
            # awaiter's ``finally`` already dropped the entry; we just have
            # to give the SDK something to send back to Codex so the JSON-RPC
            # response is well-formed and the read loop unblocks.
            return default_response_for(method)
        except Exception as exc:
            # Any other failure of the bridge — log loudly and fall back to
            # a safe default. Re-raising would leak the exception into the
            # SDK's worker thread which would then crash the entire read
            # loop.
            logger.error(
                "Codex approval bridge failed for method=%r: %s",
                method, exc, exc_info=True,
            )
            return default_response_for(method)

    async def _async_approval_handler(
        self, method: str, params: dict | None,
    ) -> dict:
        """Main-loop side of the bridge.

        Build a ``PendingRequest`` (enriched with the streamed item payload
        for ``fileChange``), broadcast it via ``_await_pending_request``,
        and return the dict the frontend sent back through
        ``manager.resolve_pending_request``.

        The WS layer is responsible for shape-validating the response into
        a Codex-compliant dict (``CodexWSHandler._build_codex_response``)
        — at this point we just pass it through.
        """
        enriched_params = self._enrich_params_with_item_payload(method, params)
        request = make_pending_request(method, enriched_params)
        response = await self._await_pending_request(request)
        return response

    def _enrich_params_with_item_payload(
        self, method: str, params: dict | None,
    ) -> dict | None:
        """For ``fileChange``, attach the streamed item payload (the diff).

        Other methods pass through unchanged. We do this BEFORE constructing
        the PendingRequest so ``tool_input`` carries the join data (under
        ``_item_payload``) and the frontend doesn't have to do a side fetch.

        The underscore prefix on ``_item_payload`` signals it's a synthetic
        side-band field, not from the Codex schema.
        """
        if method != "item/fileChange/requestApproval":
            return params
        if not params:
            return params
        item_id = params.get("itemId")
        if not item_id:
            return params
        payload = self._items_by_id.get(item_id)
        if payload is None:
            return params
        return {**params, "_item_payload": payload}
```

- [ ] **Step 4.5: Cancel pending futures BEFORE `codex.close()` in `interrupt_or_kill`**

`interrupt_or_kill` is currently lines 284-344. Inside it, after `self.kill_reason = reason` (currently line 292), but BEFORE the `turn_handle.interrupt()` block (currently line 299+), add the cancel-pending call.

The reason this is critical: `codex.close()` itself takes the SDK's `_transport_lock`. If an approval is in-flight, the worker thread is blocked on `future.result()` while holding the same lock (via the surrounding `_call_sync`). `close()` would deadlock on the lock until our future resolves. Cancelling first unblocks the worker, which lets it release the lock, which lets `close()` proceed. See spec §5.1.

After:

```python
        if self.state == AgentState.DEAD:
            return

        self.kill_reason = reason
```

add:

```python
        # Cancel any in-flight approval BEFORE closing the transport.
        # Cascade per pending approval:
        #   future.cancel() → ``_await_pending_request`` raises CancelledError
        #                  → its ``finally`` clears the dict + broadcasts
        #                  → ``run_coroutine_threadsafe`` re-raises in the
        #                    SDK worker thread
        #                  → our ``_sync_approval_handler`` catches it and
        #                    returns ``default_response_for(method)``
        #                  → worker writes the wire response, releases
        #                    ``_transport_lock``
        # Now ``codex.close()`` can acquire the lock and tear down cleanly.
        # See spec §2.4 + §5.1.
        self._cancel_all_pending_futures()  # inherited from BaseAgent (PR1)
```

- [ ] **Step 4.6: Update the module docstring**

The module docstring (lines 1-10) currently says:

```
Approvals are bypassed at the server level by the manager via
``sandbox=danger_full_access`` + ``approval_policy="never"``, so the agent
itself never has to mediate one.
```

Replace that paragraph (lines 6-10 verbatim) with:

```
Approvals: the agent installs a sync ↔ async bridge on the SDK's private
``_client._sync._approval_handler`` slot and routes the 3 Codex approval
methods (commandExecution, fileChange, permissions) through the shared
``BaseAgent._await_pending_request`` plumbing. In PR2a the manager still
defaults sessions to ``yolo`` (= ``danger_full_access`` + ``never``), so
the bridge is installed-but-dormant — Codex won't actually emit approvals
under that policy.
```

- [ ] **Step 4.7: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/agent/agent.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4.8: Sanity-check imports / wiring resolves**

This won't actually exercise an approval (no Codex subprocess), but it confirms the file loads end-to-end without import cycles or attribute typos.

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.agent.agent import CodexAgent
from twicc.providers.codex.agent.approvals import APPROVAL_METHODS
# Verify the agent class still has the SDK plumbing methods we expect.
for name in (
    '_sync_approval_handler', '_async_approval_handler',
    '_enrich_params_with_item_payload',
    '_await_pending_request', '_cancel_all_pending_futures',  # inherited from BaseAgent
    '_handle_stream_event', 'interrupt_or_kill', 'start',
):
    assert hasattr(CodexAgent, name), name
# Static attribute on the class (instance-level) is harder to check without
# building one — that needs the SDK subprocess. The instance-only attrs
# (_loop, _items_by_id, _sdk_default_approval_handler) are exercised in
# Task 7's smoke test.
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 4.9: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/agent.py
git commit -m "$(cat <<'EOF'
feat(codex): wire the sync ↔ async approval bridge on CodexAgent

- Capture SDK's default approval_handler before monkey-patching ours in.
- Capture the running event loop in start() so SDK worker threads can
  schedule our async handler via run_coroutine_threadsafe.
- _sync_approval_handler: bridge sync (SDK worker thread) to async (main
  loop). Catches CancelledError → returns safe wire default so the SDK
  read loop never hangs. Delegates non-approval server requests to the
  captured SDK default.
- _async_approval_handler: build a PendingRequest, broadcast via the
  shared BaseAgent.await_pending_request, return the dict the frontend
  sent back.
- _enrich_params_with_item_payload: for fileChange, attach the streamed
  item payload (= the diff) from _items_by_id under tool_input._item_payload.
- interrupt_or_kill: cancel pending futures BEFORE codex.close() to break
  the _transport_lock deadlock (spec §5.1).

Bridge is installed-but-dormant in PR2a (DEFAULT_MODE=yolo means Codex
never emits approvals); becomes live in PR2b.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Codex WS handler — approval response routing

**Files:**
- Modify: `src/twicc/providers/codex/ws.py`

The WS layer turns a frontend message into the SDK-shaped response dict that the agent's pending future will be resolved with. PR2a includes the full validation (strict, per spec §7-Q11) even though no frontend ever sends one in PR2a — the wiring is in place so PR2b just wires the frontend.

- [ ] **Step 5.1: Add imports**

At the top of `src/twicc/providers/codex/ws.py` (currently lines 11-21):

```python
from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from twicc.core.enums import Provider
from twicc.providers.codex.statuspage_task import get_statuspage_message_for_connection
from twicc.usage_task import get_usage_message_for_connection

from .auth import check_and_broadcast, get_auth_message_for_connection
```

After the imports block, add:

```python
from twicc.agent.registry import get_agent_manager_registry
```

- [ ] **Step 5.2: Route the new action in `dispatch`**

The current `dispatch` (lines 47-54) is:

```python
    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Codex-prefixed message."""
        if action == "check_auth":
            # Forced re-check of Codex CLI auth state, broadcast to every client.
            await check_and_broadcast(force=True)
            return True

        return False
```

Replace with:

```python
    async def dispatch(self, action: str, content: dict) -> bool:
        """Dispatch a Codex-prefixed message."""
        if action == "pending_request_response":
            await self._handle_pending_request_response(content)
            return True

        if action == "check_auth":
            # Forced re-check of Codex CLI auth state, broadcast to every client.
            await check_and_broadcast(force=True)
            return True

        return False
```

- [ ] **Step 5.3: Add the handler and the response builder**

After the `dispatch` method (after the new `return False` line), add two methods. They use the strict validation contract specified in spec §7-Q11.

```python
    async def _handle_pending_request_response(self, content: dict) -> None:
        """Route the user's decision to the right agent's right future.

        Wire shape (frontend → backend, spec §9.3, §9.5):

            {
                "type": "codex:pending_request_response",
                "session_id": "...",
                "request_id": "...",
                "tool_name": "commandExecution" | "fileChange" | "permissions",
                "decision": <string-or-dict-variant>,  # see _build_codex_response
                "permissions": {...},   // permissions only
                "scope": "turn" | "session",  // permissions only
            }

        Invalid / unroutable messages are logged and dropped; we never raise
        through the WS layer (that would tear down the consumer).
        """
        session_id = content.get("session_id")
        request_id = content.get("request_id")
        tool_name = content.get("tool_name")

        if not session_id or not request_id or not tool_name:
            logger.warning(
                "codex:pending_request_response missing required fields "
                "(session_id=%r, request_id=%r, tool_name=%r)",
                session_id, request_id, tool_name,
            )
            return

        response = self._build_codex_response(tool_name, content)
        if response is None:
            # Validation failed; _build_codex_response already logged.
            # Resolve with a safe default so the SDK isn't left hanging.
            response = self._safe_default_for(tool_name)

        manager = get_agent_manager_registry().get(Provider.CODEX)
        resolved = await manager.resolve_pending_request(
            session_id, request_id, response,
        )
        if not resolved:
            logger.warning(
                "codex:pending_request_response: failed to resolve %r for session %r "
                "(no matching pending request, or already resolved)",
                request_id, session_id,
            )

    # ------------------------------------------------------------------
    # Validation + response builders (spec §7-Q11: strict)
    # ------------------------------------------------------------------

    # Decisions a string-decision approval (command + file) may carry.
    _SIMPLE_STRING_DECISIONS: set[str] = {
        "accept", "acceptForSession", "decline", "cancel",
    }
    # Object-variant keys for command (network and execpolicy amendments).
    _COMMAND_DICT_VARIANTS: set[str] = {
        "acceptWithExecpolicyAmendment",
        "applyNetworkPolicyAmendment",
    }
    _PERMISSIONS_SCOPES: set[str] = {"turn", "session"}

    def _build_codex_response(self, tool_name: str, content: dict) -> dict | None:
        """Convert the frontend payload to the SDK-wire response dict.

        Returns ``None`` on any validation failure (caller substitutes a
        safe default). Validation rules:
        - command: ``decision`` is either in :attr:`_SIMPLE_STRING_DECISIONS`
          or a dict with exactly one key from :attr:`_COMMAND_DICT_VARIANTS`.
        - file: ``decision`` is in :attr:`_SIMPLE_STRING_DECISIONS` minus
          dict variants (no amendments for file changes — see spec §1.1.b).
        - permissions: ``scope`` ∈ :attr:`_PERMISSIONS_SCOPES`, ``permissions``
          is a dict (may be empty).
        """
        decision = content.get("decision")

        if tool_name == "commandExecution":
            return self._build_command_response(decision)

        if tool_name == "fileChange":
            return self._build_file_response(decision)

        if tool_name == "permissions":
            return self._build_permissions_response(content)

        logger.error(
            "codex:pending_request_response: unknown tool_name=%r in %r",
            tool_name, content,
        )
        return None

    def _build_command_response(self, decision: object) -> dict | None:
        if isinstance(decision, str):
            if decision in self._SIMPLE_STRING_DECISIONS:
                return {"decision": decision}
            logger.error(
                "codex commandExecution: invalid string decision=%r", decision,
            )
            return None
        if isinstance(decision, dict):
            keys = set(decision.keys())
            if len(keys) == 1 and keys.issubset(self._COMMAND_DICT_VARIANTS):
                # Wrap verbatim — Codex expects {"decision": {<variant>: {...}}}.
                return {"decision": decision}
            logger.error(
                "codex commandExecution: invalid dict decision=%r "
                "(expected one of %r)", decision, self._COMMAND_DICT_VARIANTS,
            )
            return None
        logger.error("codex commandExecution: invalid decision type=%r", type(decision))
        return None

    def _build_file_response(self, decision: object) -> dict | None:
        if isinstance(decision, str) and decision in self._SIMPLE_STRING_DECISIONS:
            return {"decision": decision}
        logger.error(
            "codex fileChange: invalid decision=%r (must be one of %r — "
            "no amendments allowed for file changes)",
            decision, self._SIMPLE_STRING_DECISIONS,
        )
        return None

    def _build_permissions_response(self, content: dict) -> dict | None:
        scope = content.get("scope")
        permissions = content.get("permissions")
        if scope not in self._PERMISSIONS_SCOPES:
            logger.error(
                "codex permissions: invalid scope=%r (expected %r)",
                scope, self._PERMISSIONS_SCOPES,
            )
            return None
        if not isinstance(permissions, dict):
            logger.error(
                "codex permissions: invalid permissions type=%r (expected dict)",
                type(permissions),
            )
            return None
        # ``strictAutoReview`` is optional + boolean per spec §1.1.c.
        strict_auto_review = content.get("strictAutoReview")
        response: dict = {"permissions": permissions, "scope": scope}
        if isinstance(strict_auto_review, bool):
            response["strictAutoReview"] = strict_auto_review
        return response

    def _safe_default_for(self, tool_name: str) -> dict:
        """Wire-safe fallback when the frontend response failed validation."""
        if tool_name == "permissions":
            return {"permissions": {}, "scope": "turn"}
        return {"decision": "decline"}
```

- [ ] **Step 5.4: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/ws.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 5.5: Sanity-check imports resolve**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.ws import CodexWSHandler
# Build a stub consumer and test the validation paths.
class Stub: pass
h = CodexWSHandler(Stub())
# command — string ok
assert h._build_command_response('accept') == {'decision': 'accept'}
# command — invalid string
assert h._build_command_response('allow') is None
# command — dict variant
v = {'acceptWithExecpolicyAmendment': {'execpolicy_amendment': ['ls']}}
assert h._build_command_response(v) == {'decision': v}
# command — invalid dict (extra key)
assert h._build_command_response({'acceptForSession': {}, 'extra': 1}) is None
# file — only strings
assert h._build_file_response('decline') == {'decision': 'decline'}
assert h._build_file_response({'acceptWithExecpolicyAmendment': {}}) is None
# permissions — valid
r = h._build_permissions_response({'scope': 'turn', 'permissions': {}})
assert r == {'permissions': {}, 'scope': 'turn'}
# permissions — bad scope
assert h._build_permissions_response({'scope': 'forever', 'permissions': {}}) is None
# permissions — strictAutoReview included
r = h._build_permissions_response({'scope': 'session', 'permissions': {}, 'strictAutoReview': True})
assert r == {'permissions': {}, 'scope': 'session', 'strictAutoReview': True}
# fallbacks
assert h._safe_default_for('commandExecution') == {'decision': 'decline'}
assert h._safe_default_for('permissions') == {'permissions': {}, 'scope': 'turn'}
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 5.6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/ws.py
git commit -m "$(cat <<'EOF'
feat(codex): WS handler for codex:pending_request_response

Route the inbound action to a new _handle_pending_request_response that:
- validates required fields (session_id, request_id, tool_name)
- builds an SDK-wire response dict via _build_codex_response with strict
  per-tool validation (spec §7-Q11):
  - commandExecution: string in {accept, acceptForSession, decline, cancel}
    OR dict with exactly one of {acceptWithExecpolicyAmendment,
    applyNetworkPolicyAmendment}
  - fileChange: string only (no amendments per spec §1.1.b)
  - permissions: {permissions: dict, scope: turn|session, strictAutoReview?: bool}
- on validation failure, logs error and substitutes a safe default
  (decline for command/file, empty permissions for permissions) so the
  pending future always resolves and the SDK never hangs
- routes through the agent registry to manager.resolve_pending_request

PR2a wires the handler; no frontend ever sends this action until PR2b.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Lift the bypass in `CodexAgentManager._create_agent`

**Files:**
- Modify: `src/twicc/providers/codex/agent/manager.py`

Replace the hardcoded `sandbox=danger_full_access` + `approval_policy="never"` with a call to `resolve_codex_policy(settings.permission_mode)`. Because `DEFAULT_MODE = "yolo"` resolves to that same pair, sessions WITHOUT an explicit `permission_mode` (= all today) keep the exact same wire behaviour.

- [ ] **Step 6.1: Add the import**

At the top of `src/twicc/providers/codex/agent/manager.py` (currently lines 17-29):

```python
from codex_app_server import (
    AppServerConfig,
    AskForApproval,
    AsyncCodex,
    SandboxMode,
)

from twicc.agent import AgentState, BaseAgent, BaseAgentManager
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings, get_provider_helpers

from ..bin import resolve_bundled_binary
from .agent import CodexAgent
```

We don't need `AskForApproval` / `SandboxMode` here anymore (the mapping module imports them). But we DO use them in the bypass code we're about to replace. After the replacement, both imports become orphans **only** if no other callsite uses them — the implementer should `grep` and drop them at the end of Task 6 if so.

Add the new import right above `from .agent import CodexAgent`:

```python
from ..permission_modes import resolve_codex_policy
```

- [ ] **Step 6.2: Replace the bypass in `_create_agent`**

The relevant block is currently lines 226-266 (inside the `try:` of `_create_agent`). The current code looks like:

```python
        try:
            approval_policy = AskForApproval.model_validate("never")
            sandbox = SandboxMode.danger_full_access
            # Per-thread config overrides. ``config`` on thread_start /
            # thread_resume reaches the server as a fresh ``ConfigToml`` patch
            # ...
            thread_config: dict[str, Any] = {
                "model_reasoning_summary": "detailed",
            }
            if resume:
                thread = await codex.thread_resume(
                    session_id,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                    config=thread_config,
                )
            else:
                helpers = get_provider_helpers(Provider.CODEX)
                sdk_model = helpers.resolve_sdk_model(settings.selected_model)
                thread = await codex.thread_start(
                    model=sdk_model,
                    sandbox=sandbox,
                    approval_policy=approval_policy,
                    config=thread_config,
                )
```

Replace the assignment of `approval_policy` + `sandbox` (the two lines after `try:`) with the call to `resolve_codex_policy`:

```python
        try:
            # Translate the user's preset (Session.permission_mode) into the
            # SDK couple. ``DEFAULT_MODE = "yolo"`` (= the previous bypass)
            # applies when permission_mode is unset, so existing sessions
            # without an explicit mode keep the same wire behaviour.
            sandbox, approval_policy = resolve_codex_policy(
                settings.permission_mode,
            )
            # Per-thread config overrides. ``config`` on thread_start /
            # thread_resume reaches the server as a fresh ``ConfigToml`` patch
            # scoped to this thread, which is more reliable than ``-c`` CLI
            # overrides (those bind at app-server boot and can be ignored by
            # the per-thread request layer). We force ``detailed`` reasoning
            # summaries so the JSONL captures the model's thinking text —
            # needed for the TwiCC "thinking" stream support we're wiring
            # next. Every model in the catalog already has
            # ``supports_reasoning_summaries=true``; the only knob that
            # actually moves the needle is the summary verbosity itself.
            thread_config: dict[str, Any] = {
                "model_reasoning_summary": "detailed",
            }
```

The rest of the `try:` block (the `if resume:` / `else:` branch using `sandbox` and `approval_policy`) stays unchanged.

- [ ] **Step 6.3: Update the `_create_agent` docstring**

The docstring (currently lines 197-216) has this paragraph:

```
        Approvals are bypassed at the server level for v1:
        ``sandbox=danger_full_access`` removes file/exec restrictions, and
        ``approval_policy="never"`` tells the server not to ask. Combined,
        the default sync approval_handler in ``AppServerClient`` (which
        accepts cmd+file approvals automatically) should never be reached;
        the residual risk is an exotic approval type that falls into
        ``return {}`` — accepted for v1.
```

Replace those 7 lines with:

```
        Sandbox + approval policy come from the user's preset (the
        ``permission_mode`` field on the bundle), translated by
        :func:`resolve_codex_policy`. In PR2a the default is still
        ``"yolo"`` (= ``danger_full_access`` + ``never``) so sessions
        without an explicit mode behave like the previous bypass. PR2b
        flips that default to ``"auto"`` once the frontend can ack
        approvals.
```

- [ ] **Step 6.4: Update the module docstring**

The module docstring (lines 1-10) currently says:

```
Minimal v1: no live settings (Codex doesn't expose a hot path for permission
or model changes on a running thread the way Claude Code does) and no
subagents. Images are forwarded to the Codex SDK as ``ImageInput`` data
URLs; documents (PDF / TXT) have no Codex protocol equivalent and are
silently dropped with a warning. Approvals are bypassed at the server
level via ``sandbox=danger_full_access`` and ``approval_policy="never"``.
```

Replace the last sentence (`Approvals are bypassed ...`) with:

```
Approvals are routed through ``CodexAgent``'s sync ↔ async bridge to the
shared ``PendingRequest`` plumbing; the sandbox + approval policy come
from the user's ``permission_mode`` preset via
:func:`resolve_codex_policy` (see ``permission_modes.py``).
```

- [ ] **Step 6.5: Check whether `AskForApproval` / `SandboxMode` are now orphan imports**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n "AskForApproval\|SandboxMode" src/twicc/providers/codex/agent/manager.py
```

If the only remaining hits are the import line and (now) nothing else, drop the two unused symbols:

```python
from codex_app_server import (
    AppServerConfig,
    AsyncCodex,
)
```

(Keep them if any of the helpers / type hints still references one of them.)

- [ ] **Step 6.6: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/agent/manager.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 6.7: Sanity-check the manager loads + resolves**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.permission_modes import resolve_codex_policy
# Verify the unset case still maps to the old bypass values.
sandbox, policy = resolve_codex_policy(None)
assert sandbox.value == 'danger-full-access'
print('OK', sandbox, policy)
"
```

Expected: `OK SandboxMode.danger_full_access AskForApproval(...)` (the exact `policy.root` repr is `never`).

- [ ] **Step 6.8: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/manager.py
git commit -m "$(cat <<'EOF'
feat(codex): wire permission_mode preset into _create_agent

Replace the hardcoded sandbox=danger_full_access + approval_policy="never"
bypass with resolve_codex_policy(settings.permission_mode). The mapping's
DEFAULT_MODE = "yolo" resolves to the same (SandboxMode, AskForApproval)
pair, so sessions without an explicit permission_mode keep identical wire
behaviour. PR2b will flip the default to "auto" once the frontend handles
the resulting approval banner.

Docstrings updated to drop the "approvals are bypassed" framing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: End-to-end smoke test + wrap-up

User-assisted verification. The PR's behavioural promise is: **a Codex session without an explicit `permission_mode` keeps running exactly like before**, no approval banners, no spurious warnings in the backend log.

- [ ] **Step 7.1: Ask user to restart backend**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py restart back
```

(User-only operation per CLAUDE.md.)

- [ ] **Step 7.2: Backend startup check (user)**

After restart, ask the user to confirm:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py status
```

Backend must show as `running` on its assigned port, no crash on startup. If backend won't boot, surface the log tail to investigate:

```bash
tail -100 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log
```

- [ ] **Step 7.3: Open an existing Codex session and send a turn**

User opens an existing Codex session in the frontend at the worktree's port (`uv run ./devctl.py status` shows the port). Sends a benign prompt that triggers shell exec, e.g. `list the files in this directory`.

Expected:
- The session resumes / continues normally.
- The shell command executes and the output appears.
- **No approval banner appears** (the default mode is still `yolo` → `approval_policy="never"`).
- No warnings of the form "Unhandled Codex server request" or "Codex approval received before loop init" in the backend log.

```bash
tail -50 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log
```

If an approval banner DOES appear, that's a regression: check that the session's `permission_mode` is genuinely null in the DB (or has been set to one of the non-`yolo` modes by accident).

- [ ] **Step 7.4: Create a brand-new Codex session**

User clicks "New session", chooses Codex provider, sends a benign prompt.

Expected: same as 7.3 — session works normally, no banner. This exercises the `thread_start` path of `_create_agent` (vs. `thread_resume` for 7.3).

- [ ] **Step 7.5: Kill an active Codex session**

User triggers a turn (e.g. ask Codex something that takes a few seconds), then clicks Stop while the turn is running.

Expected:
- The agent dies cleanly.
- No `Task was destroyed but it is pending` or `coroutine was never awaited` warnings in the backend log.
- No deadlock — Stop responds within ~5 seconds.

```bash
tail -50 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log
```

A `Future cancelled while awaiting` log from `_handle_pending_request` (Claude wording) does NOT appear for Codex — that's the Claude-specific log line. For Codex, expect just the normal kill cascade (state transitions to DEAD, `codex.close()` completes, `_items_by_id` cleared implicitly).

- [ ] **Step 7.6: Optional — switch a session to mode `read_only` and confirm a prompt fires**

This step PROVES the wiring works end-to-end, even though PR2a has no frontend stub.

Optionally, the user opens DBeaver / `sqlite3` on `db/data.sqlite` in the worktree, picks a Codex session, sets `permission_mode = 'read_only'`, then **kills that session in the UI** (or restarts the backend) so the next message creates a fresh agent that picks up the new mode at `_create_agent` time. Then sends a message that triggers a shell exec. Expected: the backend log shows the approval coming through, and since the frontend doesn't yet dispatch a response, the agent is stuck waiting on a `PendingRequest` (visible via `manager.get_agent_info(session_id).pending_requests` or via the WS `process_state` payload in browser devtools).

Note: just sending a new turn on an **existing** live agent is NOT enough — the agent was already built with the old policy at `_create_agent` time, and the mode is sticky to the live `AsyncCodex` thread server-side.

Skippable if the user doesn't want to dig into SQLite. The wiring is exercised in Task 4's import check anyway; this is just visual confirmation that approvals can actually flow.

If the user does this, they should reset `permission_mode` back to `null` afterwards (or kill the stuck session) to leave the DB in the pre-PR2a state.

- [ ] **Step 7.7: Report verification result**

Report to the user:

- Backend booted cleanly: ✅ / ❌ (log excerpt if ❌)
- Existing Codex session (7.3): works, no banner: ✅ / ❌
- New Codex session (7.4): works, no banner: ✅ / ❌
- Kill during a turn (7.5): clean, no warnings: ✅ / ❌
- Optional permission_mode = read_only confirmation (7.6): N/A or ✅ / ❌
- Claude sessions still working (quick sanity): ✅ / ❌

If anything failed, stop and surface to the user — do NOT silently retry or "fix" in PR2a scope. PR2a is supposed to be a no-op behaviourally.

- [ ] **Step 7.8: Verify git log**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git log --oneline 858c4686..HEAD
```

Expected (top to bottom, after Task 6's commit):

```
<sha> feat(codex): wire permission_mode preset into _create_agent
<sha> feat(codex): WS handler for codex:pending_request_response
<sha> feat(codex): wire the sync ↔ async approval bridge on CodexAgent
<sha> feat(codex): index streamed item payloads by id (side-table for fileChange diff)
<sha> feat(codex): add pure helpers for the 3 Codex approval methods
<sha> feat(codex): add permission_mode preset → SDK policy mapping
```

Six commits, one per task (Tasks 1-6). Task 7 is verification, no commit.

- [ ] **Step 7.9: Decide on the next step with the user**

PR2a is done. The worktree branch carries the full Codex approval pipeline + the 4-preset permission_mode mapping, but `DEFAULT_MODE = "yolo"` keeps the bypass alive. Ask the user whether to:

- A — Write the PR2b plan now (flip default to `auto` + frontend stub: 3-button banner, provider-agnostic dispatcher, Codex `PendingRequestBody` placeholder).
- B — Pause here. The spec + this plan + the resulting commits are the durable artefact; a fresh conversation can resume from `docs/superpowers/specs/2026-05-14-codex-approvals-design.md` + the latest commits.
- C — Pause and switch to something else entirely.

Do NOT push / open a PR yourself (the user keeps everything local-only per project conventions).

---

## Open considerations (not blocking PR2a)

These don't change the implementation; they're forward references the next PRs will pick up.

- **`CodexAgentManager._check_agent_timeout` (manager.py:293-313)** still has its own `if agent.pending_requests: return None` guard from before PR1 lifted that check to `BaseAgentManager._state_based_timeout`. The Codex subclass override is now redundant — the same skip runs twice per timeout tick. PR1's review for Claude flagged this kind of duplication; Codex was left untouched by PR1 to keep the diff narrow. **Not breaking** — both checks return the same answer; just one extra property access. Clean up in PR4 (or any follow-up).
- **Pydantic `AskForApproval` constructor signature**. The mapping uses `AskForApproval("on-request")` and `AskForApproval("never")` which depends on Pydantic accepting the raw root string. The vendored SDK does (`generated/v2_all.py:220-258` defines `AskForApproval = RootModel[Union[...]]`). If a future SDK update changes this, the import sanity check in Step 1.3 catches it before any agent runs.
- **`AsyncCodex._client._sync._approval_handler` is private API.** Two underscores deep. Documented in memory `reference_codex_sdk_update_procedure.md`. If a future SDK version reorganises this attribute path, the smoke test in Task 7.5 will reveal it (kill path deadlocks → backend hangs on shutdown).
- **`_sdk_default_approval_handler` capture timing.** It's grabbed BEFORE we monkey-patch — so it's truly the SDK's default, not our own handler. The delegation path in `_sync_approval_handler` (for unknown methods) thus never recurses.
- **`fileChange` `_item_payload` injection.** The frontend doesn't read it yet (PR2b uses raw `params`). The side-band is in place ahead of time so PR3's rich rendering doesn't need a backend round-trip.
- **No tests in PR2a.** Project convention. PR4 adds unit tests for the pure helpers (`_build_codex_response`, `make_pending_request`, `default_response_for`, `resolve_codex_policy`, `derive_request_id`).
