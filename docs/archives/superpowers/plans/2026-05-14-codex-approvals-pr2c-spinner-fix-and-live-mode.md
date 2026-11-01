# Codex Approvals — PR2c — Spinner orphelin + live permission_mode update

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two issues surfaced by PR2b's E2E smoke test: (1) function-call tool spinners stay forever after a user clicks Deny or Cancel turn (the tool_result row never gets marked as errored); (2) changing `permission_mode` mid-session in the picker has no effect because the SDK was only consulted at `thread_start`.

**Architecture:** Two independent backend fixes living entirely inside the Codex provider.
1. **Spinner orphelin** — `CodexAgent` carries a small in-memory map `_denied_tool_ids: dict[str, str]` (item_id → reason). The agent's own `_async_approval_handler` knows when a user resolves a pending request with a refusal (`decline` / `cancel` / empty-permissions) and writes the item_id into the map before returning the wire response to the SDK. The Codex compute path consults this map in `extract_tool_result_info` and propagates `is_error=True` + `error_text` to the `ToolResultLink` when the matching `function_call_output` lands in the JSONL.
2. **Live permission_mode** — `_run_turn` already reads `self.agent_settings.effort` fresh on every turn. Add a sibling read for `permission_mode`, translate it via `resolve_codex_policy(...)`, and pass `approval_policy=` + `sandbox_policy=` to `self._thread.turn(...)`. The SDK's `AsyncThread.turn(...)` accepts both as per-turn overrides (`api.py:610-647`).

**Tech Stack:** Python ≥ 3.13. No new dependencies.

**Reference spec:** `docs/superpowers/specs/2026-05-14-codex-approvals-design.md`. PR2c specifically **deviates from §0.2** of that spec, which stated "Pas de mode runtime style Claude (changement live …)". User confirmed in PR2b smoke-test discussion that this exclusion was a design mistake — the agent_settings closed bundle (CLAUDE.md frontend section) is meant to be re-applied at every turn for every setting that supports it. Codex SDK supports per-turn `approval_policy` + `sandbox_policy` overrides (verified in `src/codex_app_server/api.py:610-647`), they were just never wired.

**Where this came from:**
- Spinner orphelin: PR2b smoke test revealed that `function_call_output` lines for declined / cancelled tools just carry the rejection text in `output` ("Rejected by user", "aborted by user") with no `is_error` flag. The Codex JSONL has no equivalent of Claude's `tool_result.is_error: true`, so the watcher had no way to know. Confirmed by Explore agent that Claude is purely JSONL-driven; Codex genuinely needs an auxiliary side-table.
- Live mode: PR2b smoke test confirmed that switching the picker to `read_only` mid-session was silent — the running agent kept the `auto` policy from its `thread_start`.

**PR2c acceptance criteria:**
- Click Deny on a Codex tool approval → the tool card stops spinning and shows an error state (red banner / "Denied by user" / however the existing compute path renders `ToolResultLink.error`).
- Click Cancel turn → the spinning tool card shows error state, control returns to user, no other tool in `_items_by_id` keeps spinning.
- Change the `permission_mode` picker mid-session → the change takes effect on the **next** turn (current turn is unaffected; that's the SDK semantics).
- Claude sessions: behaviour unchanged.
- Kill mid-approval: still clean (PR2a's `_cancel_all_pending_futures` ordering still works).

---

## File Structure

### Files modified

| File | Why |
|------|-----|
| `src/twicc/providers/codex/permission_modes.py` | Add `_to_sandbox_policy(sandbox_mode)` helper that maps `SandboxMode` enum → `SandboxPolicy` RootModel for per-turn overrides. Optionally also add `resolve_codex_turn_overrides(mode)` returning the SDK-ready `(SandboxPolicy, AskForApproval)` pair. |
| `src/twicc/providers/codex/agent/agent.py` | (a) Add `_denied_tool_ids: dict[str, str]` to `__init__`. (b) Add `_record_decision_outcome(method, params, response)` called by `_async_approval_handler` after resolution. (c) Modify `_run_turn` to read `self.agent_settings.permission_mode` per turn and pass overrides to `self._thread.turn(...)`. |
| `src/twicc/providers/codex/agent/manager.py` | Add a tiny accessor (or method) that lets `CodexSessionCompute` look up the live agent for a session and read its `_denied_tool_ids` without poking private attrs. Naming TBD in Task 4. |
| `src/twicc/providers/codex/compute.py` | In `extract_tool_result_info`, before returning the `ToolResultInfo`, consult the agent's `_denied_tool_ids` (via the manager) and if the `call_id` is registered there, override `is_error=True` and use the registered reason as `error_text`. |

### Files NOT touched

- Frontend (`frontend/**`) — PR2c is backend-only. The frontend renders whatever the backend tells it via the existing `ToolResultLink.error` field; nothing on the frontend needs to change.
- Spec doc — per the "never edit historical docs" memory, the spec stays as-is. This plan documents the §0.2 deviation explicitly.
- Tests — covered in PR4 per the spec roadmap.

---

## How to run / verify each step

This refactor has no automated tests (project policy). Per-step verification is:
- **Python**: `uv run python -c "import ast; ast.parse(open('PATH').read()); print('OK')"` for syntax, then a minimal import sanity-check.
- **End-to-end**: user-assisted smoke test in Task 5.

For Python invocations from the worktree:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "..."
```

---

## Task 1: Add `SandboxMode` → `SandboxPolicy` mapping in `permission_modes.py`

**Files:**
- Modify: `src/twicc/providers/codex/permission_modes.py`

The SDK's per-turn override at `AsyncThread.turn(sandbox_policy=...)` takes a `SandboxPolicy` (a `RootModel` union of 4 variants: `DangerFullAccessSandboxPolicy`, `ReadOnlySandboxPolicy`, `ExternalSandboxSandboxPolicy`, `WorkspaceWriteSandboxPolicy`). Our existing `_PRESET_MAP` returns the simpler `SandboxMode` enum (used by `thread_start(sandbox=...)`). Add the converter so we can call `turn(sandbox_policy=...)`.

- [ ] **Step 1.1: Add the converter function**

In `src/twicc/providers/codex/permission_modes.py`, add the new helper plus an updated `resolve_codex_turn_overrides()` companion that returns the SDK-ready pair for per-turn calls. Add these imports at the top (alongside the existing `from codex_app_server import AskForApproval, SandboxMode`):

```python
from codex_app_server import SandboxPolicy
from codex_app_server.generated.v2_all import (
    DangerFullAccessSandboxPolicy,
    ReadOnlySandboxPolicy,
    WorkspaceWriteSandboxPolicy,
)
```

Then append at the bottom of the module (after `resolve_codex_policy`):

```python
def _to_sandbox_policy(sandbox_mode: SandboxMode) -> SandboxPolicy:
    """Convert a ``SandboxMode`` enum (used by ``thread_start(sandbox=...)``)
    to a ``SandboxPolicy`` ``RootModel`` (used by ``thread.turn(sandbox_policy=...)``).

    The SDK has two parallel types: ``SandboxMode`` for thread bootstrap,
    ``SandboxPolicy`` for per-turn override. The mapping is mechanical
    because both encode the same 3 modes we use; the ``RootModel`` form
    just carries extra config fields (network_access, writable_roots, …)
    that we leave on their defaults.
    """
    if sandbox_mode is SandboxMode.read_only:
        return SandboxPolicy(root=ReadOnlySandboxPolicy(type="readOnly"))
    if sandbox_mode is SandboxMode.workspace_write:
        return SandboxPolicy(root=WorkspaceWriteSandboxPolicy(type="workspaceWrite"))
    if sandbox_mode is SandboxMode.danger_full_access:
        return SandboxPolicy(root=DangerFullAccessSandboxPolicy(type="dangerFullAccess"))
    # SandboxMode is an enum with exactly the 3 values above; unreachable.
    raise ValueError(f"Unsupported SandboxMode: {sandbox_mode!r}")


def resolve_codex_turn_overrides(
    mode: str | None,
) -> tuple[SandboxPolicy, AskForApproval]:
    """Return the ``(SandboxPolicy, AskForApproval)`` pair for a per-turn override.

    Wraps :func:`resolve_codex_policy` and converts the sandbox to the
    ``RootModel`` shape ``thread.turn`` requires. Use this in
    ``CodexAgent._run_turn`` to translate the live ``agent_settings.permission_mode``
    into the SDK kwargs.
    """
    sandbox_mode, approval_policy = resolve_codex_policy(mode)
    return _to_sandbox_policy(sandbox_mode), approval_policy
```

- [ ] **Step 1.2: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/permission_modes.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 1.3: Sanity-check imports and conversion behaviour**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.permission_modes import resolve_codex_turn_overrides
for mode in ['read_only', 'auto', 'autonomous', 'yolo', None, 'unknown']:
    sandbox_policy, approval_policy = resolve_codex_turn_overrides(mode)
    # SandboxPolicy is a RootModel — .root is the inner variant
    inner_type = type(sandbox_policy.root).__name__
    print(f'{mode!r}: sandbox={inner_type}, approval={approval_policy}')
print('OK')
"
```

Expected output (or similar — variant order may differ):
```
'read_only': sandbox=ReadOnlySandboxPolicy, approval=root=on-request
'auto': sandbox=WorkspaceWriteSandboxPolicy, approval=root=on-request
'autonomous': sandbox=WorkspaceWriteSandboxPolicy, approval=root=never
'yolo': sandbox=DangerFullAccessSandboxPolicy, approval=root=never
None: sandbox=WorkspaceWriteSandboxPolicy, approval=root=on-request
'unknown': sandbox=WorkspaceWriteSandboxPolicy, approval=root=on-request
OK
```

The key invariants:
- `None` and `'unknown'` fall back to the `DEFAULT_MODE` (`'auto'`) → `WorkspaceWriteSandboxPolicy`.
- `'yolo'` → `DangerFullAccessSandboxPolicy`.
- `'read_only'` → `ReadOnlySandboxPolicy`.

- [ ] **Step 1.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/permission_modes.py
git commit -m "$(cat <<'EOF'
feat(codex): add SandboxMode → SandboxPolicy converter for per-turn overrides

The SDK has two parallel types: SandboxMode for thread_start(sandbox=...)
(a simple enum, what _PRESET_MAP returns today) and SandboxPolicy for
thread.turn(sandbox_policy=...) (a RootModel union with 4 variants).

Add _to_sandbox_policy() to bridge the two, and resolve_codex_turn_overrides()
as the per-turn equivalent of resolve_codex_policy() that returns the
SDK-ready pair directly.

No callers yet — wired in the next commit (live mode update in _run_turn).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Wire live `permission_mode` in `CodexAgent._run_turn`

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py`

The agent reads `self.agent_settings.effort` fresh on every turn (around `agent.py:235`). Add a sibling read for `permission_mode`, convert via `resolve_codex_turn_overrides`, and pass to `self._thread.turn(...)`.

The agent_settings bundle is refreshed by `CodexAgentManager.send_to_session` (`manager.py:123` — `agent.agent_settings = settings`) right before the new turn is scheduled. So by the time `_run_turn` reads `self.agent_settings.permission_mode`, it reflects the latest value sent from the frontend.

- [ ] **Step 2.1: Add the import**

In `src/twicc/providers/codex/agent/agent.py`, locate the existing import block near the top. Add after the existing relative imports:

```python
from ..permission_modes import resolve_codex_turn_overrides
```

- [ ] **Step 2.2: Update `_run_turn`**

`_run_turn` body starts around `agent.py:261` (the method opens earlier but the relevant block is the `effort = ...` / `turn_input = ...` / `try: turn_handle = await ...` sequence). Find this block:

```python
        effort = self._sdk_effort(self.agent_settings.effort)
        turn_input = self._build_turn_input(text, images)
        try:
            turn_handle = await self._thread.turn(turn_input, effort=effort)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Failed to open turn: {e}", exc=e)
            return
```

Replace with:

```python
        # Read live agent_settings — the bundle was refreshed by
        # ``send_to_session`` immediately before this turn was scheduled,
        # so ``permission_mode`` here is whatever the frontend has set.
        # ``thread.turn(approval_policy=..., sandbox_policy=...)`` accepts
        # both as per-turn overrides — the SDK forwards them as
        # ``TurnStartParams`` on top of the values that were bound at
        # ``thread_start``. Mid-session mode changes therefore take effect
        # at the start of the next turn (current turn unaffected).
        effort = self._sdk_effort(self.agent_settings.effort)
        sandbox_policy, approval_policy = resolve_codex_turn_overrides(
            self.agent_settings.permission_mode,
        )
        turn_input = self._build_turn_input(text, images)
        try:
            turn_handle = await self._thread.turn(
                turn_input,
                effort=effort,
                approval_policy=approval_policy,
                sandbox_policy=sandbox_policy,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await self._handle_error(f"Failed to open turn: {e}", exc=e)
            return
```

- [ ] **Step 2.3: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/codex/agent/agent.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 2.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/agent.py
git commit -m "$(cat <<'EOF'
feat(codex): apply live permission_mode at every turn

_run_turn now reads self.agent_settings.permission_mode (refreshed by
send_to_session immediately before the turn is scheduled), converts it
via resolve_codex_turn_overrides, and passes approval_policy +
sandbox_policy as per-turn overrides to thread.turn(...).

Brings Codex in line with the agent_settings "closed bundle re-applied
at every turn" contract that effort already obeys (cf. CLAUDE.md
"Agent Settings — Closed Bundle"). Mid-session picker changes take
effect on the next turn.

Note: this deviates from spec §0.2 ("Pas de mode runtime style Claude")
which was a design mistake at brainstorm time; user confirmed in PR2b
smoke test that live updates are the desired behaviour for all settings
in the bundle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Add `_denied_tool_ids` map + populate from `_async_approval_handler`

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py`

When the user clicks Deny / Cancel turn (or for permissions, sends an empty grant), the WS handler calls `manager.resolve_pending_request(...)`, which sets the future's result. The agent's `_await_pending_request` returns, control flows back into `_async_approval_handler`. At that point the agent knows: the method, the original params (which contain the `itemId`), and the response (which carries the user's decision). This is the natural place to record the refusal.

The map's key is the Codex `itemId` (= the `call_id` of the resulting `function_call_output` in the JSONL). The value is a human-readable reason that will become `ToolResultLink.error_text`.

For Cancel turn specifically: the user requested "all in-flight tools should be marked." We iterate `_items_by_id` (PR2a's side-table for streamed item payloads) and mark every entry — including non-function_call items like `agentMessage`. Dead entries (item ids that never produce a `function_call_output`) are harmless: nothing ever consults them.

- [ ] **Step 3.1: Initialize the map in `__init__`**

In `CodexAgent.__init__`, immediately after the existing `self._items_by_id: dict[str, dict] = {}` line (Task 3 of PR2a, around `agent.py:120`), add:

```python
        # Map of itemId → human-readable reason for tools that the user
        # refused (Deny, Cancel turn, empty permissions grant). Codex's
        # ``function_call_output`` JSONL line has no ``is_error`` flag —
        # only an output string like "exec_command failed for ...
        # Rejected(...)" — so the Codex compute path
        # (``CodexSessionCompute.extract_tool_result_info``) consults this
        # side-table to know whether to mark the resulting
        # ``ToolResultLink`` as errored. See spec §1.1 + PR2c plan.
        # Lifetime: agent lifetime. Cleared by ``interrupt_or_kill`` (with
        # the rest of the side-tables) or by re-creating the agent on a
        # fresh session.
        self._denied_tool_ids: dict[str, str] = {}
```

- [ ] **Step 3.2: Add `_record_decision_outcome` helper**

Add this method on `CodexAgent`, alongside the other approval-bridge helpers (right after `_enrich_params_with_item_payload`, around `agent.py:720`):

```python
    # Item types from ``_items_by_id`` that produce a ``function_call_output``
    # in the JSONL (and therefore can be matched by ``_denied_tool_ids``).
    # We keep this set tight to avoid marking dead entries on cancel turn —
    # the lookup is harmless if we over-include, but the explicit list
    # documents which kinds we expect to surface as ``ToolResultLink``.
    # The SDK item-types stream as camelCase per ``model_dump(by_alias=True)``;
    # values here match what ``_items_by_id`` will hold.
    _CANCELLABLE_ITEM_TYPES: ClassVar[frozenset[str]] = frozenset({
        "commandExecution",
        "fileChange",
    })

    def _record_decision_outcome(
        self,
        method: str,
        params: dict | None,
        response: dict,
    ) -> None:
        """If the user refused the request, mark the matching itemId(s).

        Called from ``_async_approval_handler`` immediately after
        ``_await_pending_request`` returns. Three refusal shapes:

        - ``commandExecution`` / ``fileChange`` with ``decision == "decline"``:
          mark just the current itemId.
        - ``commandExecution`` / ``fileChange`` with ``decision == "cancel"``:
          mark the current itemId AND every in-flight item in
          ``_items_by_id`` whose type is in :attr:`_CANCELLABLE_ITEM_TYPES`
          (Codex will abort the whole turn — each in-flight tool gets
          an "aborted by user" output line).
        - ``permissions`` with empty granted profile:
          mark just the current itemId.

        ``response`` is the dict the frontend sent through
        ``resolve_pending_request``; ``params`` are the original Codex
        request params that contain ``itemId``. No-op if either is missing
        an itemId we can route from.
        """
        if not params:
            return
        item_id = params.get("itemId")
        if not isinstance(item_id, str) or not item_id:
            return

        if method == "item/permissions/requestApproval":
            granted = response.get("permissions")
            if not granted:
                # Empty granted profile = user refused permissions.
                self._denied_tool_ids[item_id] = "Permissions denied by user"
            return

        # command / file
        decision = response.get("decision")
        if decision == "decline":
            self._denied_tool_ids[item_id] = "Denied by user"
            return
        if decision == "cancel":
            self._denied_tool_ids[item_id] = "Turn cancelled by user"
            # Also mark every other in-flight function-call item. The user
            # asked for "tous les tools qui n'ont pas été terminés doivent
            # être marqués" — we iterate _items_by_id which holds every
            # item that emitted item/started but not item/completed yet.
            for other_id, payload in self._items_by_id.items():
                if other_id == item_id:
                    continue
                if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:
                    self._denied_tool_ids[other_id] = "Turn cancelled by user"
```

- [ ] **Step 3.3: Call `_record_decision_outcome` from `_async_approval_handler`**

Locate `_async_approval_handler` (around `agent.py:706`). Its current body:

```python
    async def _async_approval_handler(
        self, method: str, params: dict | None,
    ) -> dict:
        """Main-loop side of the bridge..."""
        enriched_params = self._enrich_params_with_item_payload(method, params)
        request = make_pending_request(method, enriched_params)
        response = await self._await_pending_request(request)
        return response
```

Add the recording call between `await self._await_pending_request(...)` and `return response`:

```python
    async def _async_approval_handler(
        self, method: str, params: dict | None,
    ) -> dict:
        """Main-loop side of the bridge..."""
        enriched_params = self._enrich_params_with_item_payload(method, params)
        request = make_pending_request(method, enriched_params)
        response = await self._await_pending_request(request)
        # Record refusals in _denied_tool_ids so the Codex compute can
        # surface them as ToolResultLink.error when the matching
        # function_call_output lands in the JSONL.
        self._record_decision_outcome(method, params, response)
        return response
```

(Pass the **original** `params` to `_record_decision_outcome`, not `enriched_params` — both have the same `itemId` but `params` is cleaner / less coupled.)

- [ ] **Step 3.4: Clear the map in `interrupt_or_kill`**

In `interrupt_or_kill` (around `agent.py:284-344`), after the existing `self._items_by_id.clear()` line (added in PR2a Task 3), add:

```python
        self._denied_tool_ids.clear()
```

(Same rationale: agent dying, no more reads will happen.)

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
feat(codex): record denied/cancelled tool itemIds for the watcher to surface

Codex's function_call_output JSONL line has no is_error flag — the user's
Deny / Cancel turn / empty-permissions choice is invisible to the
watcher otherwise (only an opaque output string like "exec_command
failed for ... Rejected(...)").

Add _denied_tool_ids: dict[str, str] on CodexAgent (itemId → reason text).
Populated by _record_decision_outcome called from _async_approval_handler
right after the future resolves; cleared on kill alongside the other
side-tables.

Cancel turn marks every in-flight function-call item in addition to
the one that triggered the cancel (per user smoke-test verdict: "tous
les tools qui n'ont pas été terminés doivent être marqués comme tels").

No reader yet — the Codex compute consult lands next.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: `CodexSessionCompute` consults `_denied_tool_ids`

**Files:**
- Modify: `src/twicc/providers/codex/agent/manager.py` (small accessor)
- Modify: `src/twicc/providers/codex/compute.py` (`extract_tool_result_info`)

`extract_tool_result_info` (`compute.py:1397-1459`) is where Codex computes `is_error` + `error_text` for a tool result. Today three sources of error feed it (structured exec output, freeform exit-code trailer, event_msg payload). PR2c adds a 4th: the live agent's `_denied_tool_ids` map. When the `call_id` (= Codex's `itemId`) appears in the agent's map, we override the result regardless of what the output text says.

The compute method has a `session_id` parameter already (currently marked `noqa: ARG002` — unused). We use it now.

The compute path is in-process with the agent manager (both live in the Django ASGI process). The lookup is a synchronous dict access via the registry. Trade-off: a post-restart **background re-computation** (when the agent is gone) won't see the map. In practice, the `ToolResultLink.error` set by the live path during the original session is already in the DB; the background compute either preserves it or doesn't overwrite. Acceptable: the failure mode is a stale tool that's already in the past — minor UX glitch on a corner case, no data loss.

- [ ] **Step 4.1: Add `get_denied_tool_reason` accessor on `CodexAgentManager`**

In `src/twicc/providers/codex/agent/manager.py`, add this method on the `CodexAgentManager` class (after the existing public API methods like `send_to_session` / `create_session`, before the private `_create_agent`):

```python
    def get_denied_tool_reason(
        self, session_id: str, item_id: str,
    ) -> str | None:
        """Return the recorded refusal reason for ``(session_id, item_id)``, or None.

        Called by :class:`twicc.providers.codex.compute.CodexSessionCompute`
        to surface user-initiated refusals (Deny / Cancel turn / empty
        permissions) as ``ToolResultLink.error`` when the matching
        ``function_call_output`` arrives in the JSONL.

        Returns ``None`` if there is no live agent for the session (e.g.
        the agent died and was GC'd, or this is a background re-compute
        on a session from a previous backend run) or if the item_id was
        never refused.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return None
        # ``CodexAgent`` owns ``_denied_tool_ids`` — see the comment on the
        # map in ``CodexAgent.__init__``.
        return agent._denied_tool_ids.get(item_id)
```

(We poke `agent._denied_tool_ids` through the manager rather than the compute reaching past the manager itself — keeps the compute → agent coupling routed through one chokepoint.)

- [ ] **Step 4.2: Modify `extract_tool_result_info` in `compute.py`**

In `src/twicc/providers/codex/compute.py:1397-1459`, the current body builds `error_text` from the JSONL output text via three helper functions and then returns a `ToolResultInfo`. We add a 4th lookup that **takes precedence** over the JSONL-derived value when the agent has recorded a refusal.

Replace the response-item branch and the final `ToolResultInfo` construction with:

```python
    def extract_tool_result_info(
        self,
        parsed_json: dict,
        *,
        session_id: str,
        tool_use_map: dict | None = None,  # noqa: ARG002
    ) -> ToolResultInfo | None:
        # ... same prelude ...
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
                )
            else:
                error_text = None
        elif wrapper_type == _TYPE_EVENT_MSG:
            call_id = _event_msg_call_id(parsed_json)
            error_text = _event_msg_payload_error(payload)
        else:
            return None
        if not isinstance(call_id, str) or not call_id:
            return None

        # 4th error source: the live agent's _denied_tool_ids map.
        # Codex's function_call_output line carries the rejection text in
        # ``output`` ("exec_command failed for ... Rejected(...)" /
        # "aborted by user after X.Xs") but no is_error flag. We don't
        # pattern-match the text — we consult the agent-side map populated
        # at WS-response time by ``CodexAgent._record_decision_outcome``.
        # If the user refused, the recorded reason supersedes any
        # exit-code text that ``_*_error`` helpers might have produced.
        denied_reason = _denied_tool_reason(session_id, call_id)
        if denied_reason is not None:
            error_text = denied_reason

        return ToolResultInfo(
            tool_use_id=call_id,
            is_error=error_text is not None,
            error_text=error_text,
        )
```

(Note: removed the `noqa: ARG002` from `session_id` since it's now used.)

Add the `_denied_tool_reason` lookup helper near the top of the file (or close to the other private helpers). It must avoid creating an import cycle with the manager — use a lazy import inside the function:

```python
def _denied_tool_reason(session_id: str, call_id: str) -> str | None:
    """Lookup the live agent's ``_denied_tool_ids`` map for a refusal record.

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
    except Exception:
        # Registry not yet initialized (early startup, background compute
        # before the live process boots, ...).
        return None
    if manager is None:
        return None
    # The accessor is defensive: returns None if no live agent for the session.
    return manager.get_denied_tool_reason(session_id, call_id)
```

(The two-level `try` makes the helper robust against startup ordering issues — background compute may run before the agent registry is wired in some scenarios.)

- [ ] **Step 4.3: Sanity-check both files parse**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "
import ast
for p in [
    'src/twicc/providers/codex/agent/manager.py',
    'src/twicc/providers/codex/compute.py',
]:
    ast.parse(open(p).read())
    print('OK', p)
"
```

Expected: two `OK` lines.

- [ ] **Step 4.4: Sanity-check the import + accessor work**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run python -c "
import django
django.setup()
from twicc.providers.codex.agent.manager import CodexAgentManager
from twicc.providers.codex.compute import CodexSessionCompute, _denied_tool_reason
# accessor returns None gracefully when no agent / no manager
assert _denied_tool_reason('nonexistent-session', 'nonexistent-call') is None
# verify CodexAgentManager has the new accessor
assert hasattr(CodexAgentManager, 'get_denied_tool_reason')
print('OK')
"
```

Expected: `OK`.

- [ ] **Step 4.5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/manager.py src/twicc/providers/codex/compute.py
git commit -m "$(cat <<'EOF'
feat(codex): surface user-refused tools as ToolResultLink.error

Add CodexAgentManager.get_denied_tool_reason(session_id, item_id) as the
read-side of CodexAgent._denied_tool_ids (populated in the previous
commit).

CodexSessionCompute.extract_tool_result_info now consults this accessor
(via a lazy private helper to avoid an import cycle) as a 4th error
source on top of the 3 existing exit-code parsers. When the user
declined / cancelled / denied permissions, the recorded reason takes
precedence over any exit-code text and lands in
ToolResultLink.error_text — the existing UI rendering picks it up and
the tool card stops spinning.

Background re-compute (agent already GC'd) returns None gracefully —
the original error is already persisted from the live computation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: End-to-end smoke test + wrap-up

User-assisted verification. The goal: confirm the spinner no longer orphans on Deny / Cancel turn, and the picker change applies on the next turn.

- [ ] **Step 5.1: User restarts backend**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py restart back
```

(User-only operation per CLAUDE.md.)

Confirm `uv run ./devctl.py status` shows backend running.

- [ ] **Step 5.2: Test Deny → tool card stops spinning**

User opens a Codex session, switches the picker to `read_only` (so a write triggers an approval reliably), sends a prompt that creates a file. Banner appears. **Click Deny**.

Expected:
- The Codex banner disappears.
- The shell exec / fileChange tool card on the timeline shows an **error state** (red banner, `ToolResultLink.error` rendered — the same UI Claude shows when a tool errors).
- Codex continues the turn (the model probably tries another approach or stops).
- `tail -50 logs/backend.log` shows no `Codex approval bridge failed` warnings, no asyncio errors.

If the tool card still spins, check:
- That `_record_decision_outcome` actually ran (`logger.debug`-add temporary if needed).
- That `extract_tool_result_info` ran and saw the call_id in the map (a stale agent or session_id mismatch).

- [ ] **Step 5.3: Test Cancel turn → all in-flight tools stop spinning**

User triggers another approval, **click Cancel turn**.

Expected:
- The banner disappears.
- The tool card shows error state with reason "Turn cancelled by user".
- Control returns to the user (USER_TURN).
- If multiple tools were in-flight (rare today since we don't support concurrent turn input), all should be marked. Skip if not testable.

- [ ] **Step 5.4: Test permissions Deny (if testable)**

Permissions approvals are rare — they only fire when Codex hits a `request_permissions` tool call. May not be reliably triggerable. Optional.

- [ ] **Step 5.5: Test live mode change**

User starts the session in `auto` mode, sends a message, Codex completes the turn. Then **switch the picker to `read_only`** while still in user_turn. Send another message that would normally not trigger an approval in `auto` (e.g. a workspace write).

Expected: the new turn DOES trigger an approval banner (because the new turn was opened with `approval_policy=on-request` + `sandbox_policy=read-only`).

Also test the reverse: switch from `read_only` back to `auto`, send a message that previously triggered approval — it should no longer trigger.

Note: changes don't affect the CURRENT turn (Codex SDK semantics). They only affect the NEXT turn.

- [ ] **Step 5.6: Claude unchanged**

Quick check: open a Claude session, trigger an approval, Approve and Deny both work. No regression.

- [ ] **Step 5.7: Kill mid-approval still clean**

Trigger a Codex approval, don't click any button, click Stop. Expected: agent dies cleanly, no `Task was destroyed`, no asyncio leaks.

- [ ] **Step 5.8: Report verification result**

Report to the user:
- Backend boot: ✅ / ❌
- Deny → no spinner (5.2): ✅ / ❌
- Cancel turn → no spinner (5.3): ✅ / ❌
- Permissions Deny (5.4, optional): ✅ / N/A / ❌
- Live mode change → next turn (5.5): ✅ / ❌
- Claude unchanged (5.6): ✅ / ❌
- Kill mid-approval still clean (5.7): ✅ / ❌

If any failure, surface the failing step's log excerpt — don't silently retry.

- [ ] **Step 5.9: Verify git log**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git log --oneline 4831f2c6..HEAD
```

Expected (newest first):
```
<sha> feat(codex): surface user-refused tools as ToolResultLink.error
<sha> feat(codex): record denied/cancelled tool itemIds for the watcher to surface
<sha> feat(codex): apply live permission_mode at every turn
<sha> feat(codex): add SandboxMode → SandboxPolicy converter for per-turn overrides
```

Four commits, one per Task 1-4. Task 5 is verification only, no commit.

- [ ] **Step 5.10: Decide on the next step with the user**

PR2c is done. Two backend fixes shipped, both user-visible:
- Tool spinner properly stops on user refusal.
- Picker changes apply on the next turn.

Next: **PR3 revisée** (factorisation correcte du shell vs body in `PendingRequestForm.vue`, rich rendering per `tool_name` for Codex, split-button Approve menus, 5th `strict` mode), or pause if user wants a break.

---

## Open considerations (not blocking PR2c)

- **Background re-compute fidelity.** A `ToolResultLink.error` set by the live path is persisted in the DB. If the backend restarts and the same JSONL line is re-computed (e.g. compute_version bump), the in-memory map is gone — `_denied_tool_reason` returns `None`, the 3 exit-code helpers will all return `None` (the output text isn't an exit-code error), and the new `ToolResultInfo` has `is_error=False`. **Whether this overwrites the existing DB value depends on `BaseSessionCompute.create_tool_result_link_live`'s semantics** — verify in PR4 (which will add tests) whether a re-compute can erase a previously-set `error` field. If it can, persist the refusal reason elsewhere (e.g. an extra column on `SessionItem` for the tool_use, set at WS-resolve time) so the background path can read it back.
- **Spec §0.2 outdated.** PR2c deviates from the spec's "no runtime mode changes" assertion. Leaving the spec as-is per the "no historical doc edits" memory; documented in this plan + in the Task 2 commit message. PR4 will record this as a known historical deviation if a memory update makes sense.
- **`AskUserQuestion` analogue.** Spec §0.2 also excludes a Codex equivalent of Claude's `ask_user_question`. Not in scope for PR2c either — separate concern.
- **PR3 still has factorisation work + rich rendering + `strict` mode + tooltips.** PR2c only fixes the two backend bugs surfaced by PR2b's smoke test. UI quality remains rough until PR3.
