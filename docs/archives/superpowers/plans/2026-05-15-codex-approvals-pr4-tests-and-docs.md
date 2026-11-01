# Codex Approvals PR4 — Tests + Docs + Memories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock in the Codex approvals feature with unit tests on the pure helpers, fix one carryover Minor (M2 — background re-compute might overwrite `ToolResultLink.error`), add the missing debug log on the cancel-turn siblings loop, and ship docs (new memory + memory update + CHANGELOG entry).

**Architecture:** Three families of change, each independent.

1. **Unit tests** for the pure helpers introduced/touched by PR2a / PR2c: `permission_modes.py` (3 functions), `approvals.py` (4 functions), `codex/ws.py` response builders (5 functions). All synchronous, all small, all already shipping in production code — these are characterization tests that document current behaviour and guard against future regression. Uses the existing pytest harness (`tests/`, `pytest.ini` config in `pyproject.toml`, `tests/conftest.py`).

2. **One investigative task** (Background re-compute) that reads the compute path, determines whether `create_tool_result_link_live` overwrites a previously-set `ToolResultLink.error` when a re-compute (without a live agent) produces `error_text=None`, and ships a defensive fix if the bug exists. Plus one trivial debug log addition.

3. **Documentation**: new memory file describing the Codex approvals architecture (so future me / future sessions can recover the design without re-reading the spec), update of the existing `reference_codex_sdk_update_procedure.md` memory with new check items (the SandboxPolicy variant classes + `AsyncThread.turn` signature), and a user-facing entry in `CHANGELOG.md [Unreleased]`.

**Tech Stack:** Python 3.13, pytest + pytest-django (existing harness), orjson, Codex SDK enums (`SandboxMode`, `AskForApproval`, `SandboxPolicy`), Django ORM (`ToolResultLink`).

---

## Reference

- **Plan PR3** (most recent context): `docs/superpowers/plans/2026-05-14-codex-approvals-pr3-frontend-refactor-rich-render.md`
- **Carryover memo** (source of truth for PR4 scope): `docs/superpowers/plans/2026-05-14-codex-approvals-pr3-pr4-carryover-notes.md` — section "PR4 — Scope"
- **Spec design**: `docs/superpowers/specs/2026-05-14-codex-approvals-design.md` — §8 "Tests", §0.2 "deviation"
- **Existing test pattern**: `tests/test_pricing_parsing.py` (table-driven, `ProviderSpec`-style)
- **Existing memory pattern**: any of the files under `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/`

---

## Important — project context

CLAUDE.md says "no tests / no linting" is one of two shortcuts the project allows. PR4 is the deliberate exception: spec §8 calls for unit tests, the carryover memo plans them, and the user has explicitly OK'd PR4 = tests + docs + memories. The pytest harness IS set up (`tests/conftest.py`, `pyproject.toml` `[tool.pytest.ini_options]`, 247 existing tests collected). So writing tests here is not contradicting the rule; it's a focused investment in the feature's most critical surface.

Run-the-tests command (from the worktree root):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run --with pytest --with pytest-django pytest tests/ -v
```

`TWICC_DATA_DIR=$PWD` ensures the test DB resolves inside the worktree (CLAUDE.md "Running Python / Django code in a worktree without devctl").

---

## File Structure

### New test files

| File | Responsibility |
|------|----------------|
| `tests/test_codex_permission_modes.py` | Tests for `permission_modes._PRESET_MAP`, `resolve_codex_policy`, `_to_sandbox_policy`, `resolve_codex_turn_overrides`. |
| `tests/test_codex_approvals_helpers.py` | Tests for `agent/approvals.py:is_approval_method`, `derive_request_id`, `make_pending_request`, `default_response_for`. |
| `tests/test_codex_ws_responses.py` | Tests for `codex/ws.py` response builders: `_build_codex_response`, `_build_command_response`, `_build_file_response`, `_build_permissions_response`, `_safe_default_for`. |

### Modified backend files

| File | Action |
|------|--------|
| `src/twicc/providers/codex/agent/agent.py` | Add 1 `logger.debug` call inside the cancel-turn siblings loop (Minor M3). |
| `src/twicc/providers/compute_base.py` | Possibly defensive fix on `create_tool_result_link_live` (Minor M2). Only if Task 4 finds a real bug. |

### New + modified docs

| File | Action |
|------|--------|
| `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/project_codex_approvals.md` | **Create**: architecture overview of the Codex approvals subsystem. |
| `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/MEMORY.md` | Modify: add a 1-line index entry pointing to the new memory file. |
| `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/reference_codex_sdk_update_procedure.md` | Modify: add 3 new check items for the `SandboxPolicy` variant classes + `AsyncThread.turn` signature. |
| `CHANGELOG.md` | Modify `[Unreleased]` section: add an entry describing the Codex interactive approvals feature. |

---

## Tasks

### Task 1: Tests for `permission_modes.py`

**Files:**
- Create: `tests/test_codex_permission_modes.py`

The module exposes:

```python
# src/twicc/providers/codex/permission_modes.py
_PRESET_MAP: dict[str, tuple[SandboxMode, AskForApproval]]
DEFAULT_MODE: str  # "auto"

def resolve_codex_policy(mode: str | None) -> tuple[SandboxMode, AskForApproval]
def _to_sandbox_policy(sandbox_mode: SandboxMode) -> SandboxPolicy
def resolve_codex_turn_overrides(mode: str | None) -> tuple[SandboxPolicy, AskForApproval]
```

5 preset modes after PR3: `read_only`, `strict`, `auto`, `autonomous`, `yolo`.

Wire / preset table:

| Mode | sandbox_mode | approval_policy |
|------|--------------|-----------------|
| `read_only` | `SandboxMode.read_only` | `AskForApproval("on-request")` |
| `strict` | `SandboxMode.read_only` | `AskForApproval("never")` |
| `auto` | `SandboxMode.workspace_write` | `AskForApproval("on-request")` |
| `autonomous` | `SandboxMode.workspace_write` | `AskForApproval("never")` |
| `yolo` | `SandboxMode.danger_full_access` | `AskForApproval("never")` |

- [ ] **Step 1: Write the test file**

Create `tests/test_codex_permission_modes.py` with the following content:

```python
"""Unit tests for the Codex permission_mode preset mapping.

5 wire-value presets (since PR3) translate to ``(SandboxMode, AskForApproval)``
couples for ``thread_start`` / ``thread_resume`` and to ``(SandboxPolicy, AskForApproval)``
for per-turn overrides via ``thread.turn``. Both surfaces are exercised here so
any future regression in either path is caught immediately.
"""

from __future__ import annotations

import pytest

from codex_app_server import AskForApproval, SandboxMode, SandboxPolicy
from codex_app_server.generated.v2_all import (
    DangerFullAccessSandboxPolicy,
    ReadOnlySandboxPolicy,
    WorkspaceWriteSandboxPolicy,
)

from twicc.providers.codex.permission_modes import (
    DEFAULT_MODE,
    _PRESET_MAP,
    _to_sandbox_policy,
    resolve_codex_policy,
    resolve_codex_turn_overrides,
)


# (wire_mode, sandbox_mode, approval_policy_string)
PRESET_TABLE: list[tuple[str, SandboxMode, str]] = [
    ("read_only", SandboxMode.read_only, "on-request"),
    ("strict", SandboxMode.read_only, "never"),
    ("auto", SandboxMode.workspace_write, "on-request"),
    ("autonomous", SandboxMode.workspace_write, "never"),
    ("yolo", SandboxMode.danger_full_access, "never"),
]


class TestPresetMap:
    def test_preset_map_has_exactly_5_entries(self):
        assert len(_PRESET_MAP) == 5

    def test_default_mode_is_auto(self):
        assert DEFAULT_MODE == "auto"

    def test_default_mode_is_present_in_preset_map(self):
        assert DEFAULT_MODE in _PRESET_MAP


class TestResolveCodexPolicy:
    @pytest.mark.parametrize("mode,sandbox,approval_str", PRESET_TABLE)
    def test_known_mode_returns_expected_couple(self, mode, sandbox, approval_str):
        result_sandbox, result_approval = resolve_codex_policy(mode)
        assert result_sandbox is sandbox
        assert isinstance(result_approval, AskForApproval)
        assert result_approval.root == approval_str

    def test_none_falls_back_to_default(self):
        result = resolve_codex_policy(None)
        expected = _PRESET_MAP[DEFAULT_MODE]
        assert result == expected

    def test_unknown_mode_falls_back_to_default(self):
        result = resolve_codex_policy("totally_made_up_mode")
        expected = _PRESET_MAP[DEFAULT_MODE]
        assert result == expected

    def test_empty_string_falls_back_to_default(self):
        # ``mode or DEFAULT_MODE`` makes empty string equivalent to None.
        result = resolve_codex_policy("")
        expected = _PRESET_MAP[DEFAULT_MODE]
        assert result == expected


class TestToSandboxPolicy:
    def test_read_only_returns_read_only_policy(self):
        result = _to_sandbox_policy(SandboxMode.read_only)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, ReadOnlySandboxPolicy)
        assert result.root.type == "readOnly"

    def test_workspace_write_returns_workspace_write_policy(self):
        result = _to_sandbox_policy(SandboxMode.workspace_write)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, WorkspaceWriteSandboxPolicy)
        assert result.root.type == "workspaceWrite"

    def test_danger_full_access_returns_danger_full_access_policy(self):
        result = _to_sandbox_policy(SandboxMode.danger_full_access)
        assert isinstance(result, SandboxPolicy)
        assert isinstance(result.root, DangerFullAccessSandboxPolicy)
        assert result.root.type == "dangerFullAccess"


class TestResolveCodexTurnOverrides:
    @pytest.mark.parametrize("mode,sandbox_mode,approval_str", PRESET_TABLE)
    def test_known_mode_returns_sandbox_policy_and_approval(
        self, mode, sandbox_mode, approval_str,
    ):
        sandbox_policy, approval = resolve_codex_turn_overrides(mode)
        assert isinstance(sandbox_policy, SandboxPolicy)
        # The inner SandboxPolicy variant matches the SandboxMode.
        type_map = {
            SandboxMode.read_only: ReadOnlySandboxPolicy,
            SandboxMode.workspace_write: WorkspaceWriteSandboxPolicy,
            SandboxMode.danger_full_access: DangerFullAccessSandboxPolicy,
        }
        assert isinstance(sandbox_policy.root, type_map[sandbox_mode])
        assert isinstance(approval, AskForApproval)
        assert approval.root == approval_str

    def test_none_falls_back_to_default(self):
        sandbox_policy, approval = resolve_codex_turn_overrides(None)
        # DEFAULT_MODE = "auto" → workspace_write + on-request
        assert isinstance(sandbox_policy.root, WorkspaceWriteSandboxPolicy)
        assert approval.root == "on-request"

    def test_unknown_mode_falls_back_to_default(self):
        sandbox_policy, approval = resolve_codex_turn_overrides("bogus")
        assert isinstance(sandbox_policy.root, WorkspaceWriteSandboxPolicy)
        assert approval.root == "on-request"
```

- [ ] **Step 2: Run the tests to verify they pass**

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run --with pytest --with pytest-django pytest tests/test_codex_permission_modes.py -v
```

Expected: all tests PASS. The functions are already shipped; these are characterization tests.

If any test FAILS, investigate — either the test is wrong (most likely), or there's a latent bug in the production code. Don't blindly modify the production code; understand why first.

- [ ] **Step 3: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add tests/test_codex_permission_modes.py
git commit -m "$(cat <<'EOF'
test(codex): cover permission_modes preset map + helpers

5 modes (read_only, strict, auto, autonomous, yolo) parametrised
across all three public surfaces:
- ``resolve_codex_policy(mode) -> (SandboxMode, AskForApproval)``
- ``_to_sandbox_policy(sandbox_mode) -> SandboxPolicy``
- ``resolve_codex_turn_overrides(mode) -> (SandboxPolicy, AskForApproval)``

Plus None / unknown / empty-string fallback to DEFAULT_MODE ("auto").
Locks the wire-value table in place so a future SDK rename of the
``SandboxMode`` / ``AskForApproval`` enum values would break the
tests rather than silently flipping live sessions to the wrong
sandbox.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Tests for `approvals.py`

**Files:**
- Create: `tests/test_codex_approvals_helpers.py`

The module at `src/twicc/providers/codex/agent/approvals.py` exposes 4 pure functions + 1 constant used by the agent's sync ↔ async bridge:

```python
APPROVAL_METHODS: dict[str, str]  # {wire_method: tool_name} — 3 entries

def is_approval_method(method: str) -> bool
def derive_request_id(params: dict | None) -> str
def make_pending_request(method: str, params: dict | None) -> PendingRequest
def default_response_for(method: str) -> dict
```

The 3 methods + their tool_name mapping:
- `"item/commandExecution/requestApproval"` → `"commandExecution"`
- `"item/fileChange/requestApproval"` → `"fileChange"`
- `"item/permissions/requestApproval"` → `"permissions"`

`default_response_for` builds the default dict **inline** per call (no module-level `DEFAULT_RESPONSES` constant exists):
- command / file methods → `{"decision": "decline"}`
- permissions method → `{"permissions": {}, "scope": "turn"}`

Each call returns a freshly-built dict, so mutation by callers cannot leak.

- [ ] **Step 1: Read the existing `approvals.py` to understand its contract**

Read `src/twicc/providers/codex/agent/approvals.py` in full. Note:
- The exact wire shape of each `DEFAULT_RESPONSES` entry (which the tests will pin down)
- The order of fallbacks in `derive_request_id` (the docstring says `approvalId > itemId > UUID4`)
- Whether `make_pending_request` raises on unknown method or silently passes through (the docstring says ValueError)

- [ ] **Step 2: Write the test file**

Create `tests/test_codex_approvals_helpers.py`:

```python
"""Unit tests for the Codex sync ↔ async approval bridge helpers.

The 3 server-side approval methods Codex emits
(``item/commandExecution/requestApproval``,
``item/fileChange/requestApproval``,
``item/permissions/requestApproval``) flow through this module on
the way to ``BaseAgent._await_pending_request``. The 4 helpers
covered here are pure, synchronous, and easy to lock down.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from twicc.providers.codex.agent.approvals import (
    APPROVAL_METHODS,
    default_response_for,
    derive_request_id,
    is_approval_method,
    make_pending_request,
)


# Single source of truth for the 3 known approval methods (mirrors
# APPROVAL_METHODS keys; duplicated here so a future rename in the
# production module fails the table-driven assertions immediately).
KNOWN_METHODS = [
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
]


class TestIsApprovalMethod:
    @pytest.mark.parametrize("method", KNOWN_METHODS)
    def test_true_for_known_methods(self, method):
        assert is_approval_method(method) is True

    def test_false_for_unknown_method(self):
        assert is_approval_method("item/tool/call") is False

    def test_false_for_empty_string(self):
        assert is_approval_method("") is False

    def test_false_for_arbitrary_string(self):
        assert is_approval_method("nope") is False


class TestApprovalMethodsConstant:
    def test_constant_keys_match_known_methods(self):
        # APPROVAL_METHODS is a dict[wire_method, tool_name]. The keys
        # are the wire methods; the values are the human-readable
        # tool_names exposed in PendingRequest.tool_name.
        assert set(APPROVAL_METHODS.keys()) == set(KNOWN_METHODS)

    def test_tool_name_values_match_spec(self):
        # Source of truth for the (method → tool_name) mapping per spec
        # §1.1.{a,b,c}.
        assert APPROVAL_METHODS["item/commandExecution/requestApproval"] == "commandExecution"
        assert APPROVAL_METHODS["item/fileChange/requestApproval"] == "fileChange"
        assert APPROVAL_METHODS["item/permissions/requestApproval"] == "permissions"


class TestDeriveRequestId:
    def test_approval_id_takes_priority(self):
        params = {"approvalId": "appr-1", "itemId": "item-1"}
        assert derive_request_id(params) == "appr-1"

    def test_item_id_when_no_approval_id(self):
        params = {"itemId": "call_xyz"}
        assert derive_request_id(params) == "call_xyz"

    def test_uuid4_fallback_when_no_id_fields(self):
        result = derive_request_id({})
        # Should be a valid UUID4 string.
        parsed = UUID(result)
        assert parsed.version == 4

    def test_uuid4_fallback_when_none_params(self):
        result = derive_request_id(None)
        parsed = UUID(result)
        assert parsed.version == 4

    def test_empty_string_fields_fall_through_to_uuid(self):
        params = {"approvalId": "", "itemId": ""}
        result = derive_request_id(params)
        parsed = UUID(result)
        assert parsed.version == 4

    def test_non_string_id_fields_fall_through_to_uuid(self):
        # If the wire ever sends an int (defensive), we don't crash.
        params = {"approvalId": 123, "itemId": 456}
        result = derive_request_id(params)
        parsed = UUID(result)
        assert parsed.version == 4


class TestMakePendingRequest:
    @pytest.mark.parametrize("method", KNOWN_METHODS)
    def test_returns_pending_request_for_known_methods(self, method):
        params = {"itemId": "call_abc"}
        pr = make_pending_request(method, params)
        # PendingRequest is a NamedTuple from twicc.agent.states.
        assert pr.request_id == "call_abc"
        # tool_name is looked up via APPROVAL_METHODS (the production
        # source of truth) — assert through that map rather than
        # re-deriving from the method string.
        assert pr.tool_name == APPROVAL_METHODS[method]
        # tool_input is a dict copy of the original params.
        assert isinstance(pr.tool_input, dict)
        assert pr.tool_input == params
        # request_type is always "tool_approval" for Codex (no ask_user_question).
        assert pr.request_type == "tool_approval"
        # permission_suggestions is unused by Codex.
        assert pr.permission_suggestions is None

    def test_empty_params_yield_empty_dict_tool_input(self):
        pr = make_pending_request(KNOWN_METHODS[0], None)
        assert pr.tool_input == {}

    def test_unknown_method_raises_value_error(self):
        with pytest.raises(ValueError):
            make_pending_request("item/unknown/requestApproval", {"itemId": "x"})

    def test_uuid_fallback_request_id_when_no_id_fields(self):
        pr = make_pending_request(KNOWN_METHODS[0], {})
        parsed = UUID(pr.request_id)
        assert parsed.version == 4


class TestDefaultResponseFor:
    def test_command_method_returns_decline(self):
        result = default_response_for("item/commandExecution/requestApproval")
        assert result == {"decision": "decline"}

    def test_file_method_returns_decline(self):
        result = default_response_for("item/fileChange/requestApproval")
        assert result == {"decision": "decline"}

    def test_permissions_method_returns_empty_grant(self):
        result = default_response_for("item/permissions/requestApproval")
        assert result == {"permissions": {}, "scope": "turn"}

    def test_unknown_method_raises_value_error(self):
        with pytest.raises(ValueError):
            default_response_for("item/unknown/requestApproval")

    @pytest.mark.parametrize("method", KNOWN_METHODS)
    def test_returns_fresh_dict_per_call(self, method):
        # Defensive copy: mutating one return value must not leak into
        # the next call (no module-level cached dict).
        first = default_response_for(method)
        sentinel = object()
        key = next(iter(first))
        first[key] = sentinel
        second = default_response_for(method)
        assert second[key] is not sentinel
```

- [ ] **Step 3: Run the tests, fix what's wrong**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run --with pytest --with pytest-django pytest tests/test_codex_approvals_helpers.py -v
```

If any assertion FAILS, the test is wrong (not the production code). Adjust the assertion to match what the code actually produces — these are characterization tests.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add tests/test_codex_approvals_helpers.py
git commit -m "$(cat <<'EOF'
test(codex): cover approval helpers (is_approval_method, derive_request_id, make_pending_request, default_response_for)

The four pure helpers in ``codex/agent/approvals.py`` are the
sync→async bridge's contract surface: deciding whether to handle
a server request, picking a stable request_id from the params,
shaping the ``PendingRequest`` we hand to ``_await_pending_request``,
and producing a wire-safe fallback when the future is cancelled.

Tests parametrise across the 3 known approval methods
(commandExecution / fileChange / permissions), exercise the
approvalId > itemId > UUID4 fallback chain, and verify the
ValueError contract on unknown methods. Also enforces that
``default_response_for`` returns a fresh dict per call
(mutation-safety guard for the sync bridge).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Tests for `codex/ws.py` response builders

**Files:**
- Create: `tests/test_codex_ws_responses.py`

The module at `src/twicc/providers/codex/ws.py` exposes 5 response-building helpers as **methods on the `CodexWSHandler` class** (NOT module-level functions). The class is instantiated per WS connection with a `consumer` argument, but the builders never touch `self.consumer` — so for unit tests we can instantiate `CodexWSHandler(consumer=None)` and call the methods on the resulting instance.

```python
class CodexWSHandler:
    _SIMPLE_STRING_DECISIONS: set[str] = {"accept", "acceptForSession", "decline", "cancel"}
    _COMMAND_DICT_VARIANTS: dict[str, tuple[str, type]] = {
        "acceptWithExecpolicyAmendment": ("execpolicy_amendment", list),
        "applyNetworkPolicyAmendment":   ("network_policy_amendment", dict),
    }

    def __init__(self, consumer): ...

    def _build_codex_response(self, tool_name: str, content: dict) -> dict | None: ...
    def _build_command_response(self, decision: object) -> dict | None: ...
    def _build_file_response(self, decision: object) -> dict | None: ...
    def _build_permissions_response(self, content: dict) -> dict | None: ...
    def _safe_default_for(self, tool_name: str) -> dict: ...
```

**Contract**: builders return `dict` on valid input, `None` on invalid input (they do NOT raise `ValueError`). Use `assert result is None` in the failure-case tests.

- [ ] **Step 1: Read `codex/ws.py` to lock the exact return shapes**

Read `src/twicc/providers/codex/ws.py` lines 110-260 (the 5 builders). For each one, note:

- The SDK-compliant dict shape returned on the HAPPY path (what `{"decision": "accept"}` becomes in wire output)
- The EXACT validation failures that return `None` (NOT `ValueError`):
  - `_build_command_response`: unknown string → `None`; dict with no recognised key → `None`; amendment with wrong-type inner → `None`
  - `_build_file_response`: unknown string → `None`; any dict input → `None` (fileChange has no amendments)
  - `_build_permissions_response`: missing `permissions` / `scope` → `None`; invalid `scope` → `None`; non-dict `permissions` → `None`
  - `_build_codex_response`: unknown tool_name → returns `_safe_default_for(tool_name)` (which is itself defensive — confirm the actual fallback shape)
- `_safe_default_for` for the 3 known tool_names: build inline (look at code, it likely mirrors `default_response_for` from `approvals.py`)

These are the exact wire shapes the test must lock in. Don't paraphrase — assert against the real output.

- [ ] **Step 2: Write the test file**

Create `tests/test_codex_ws_responses.py`:

```python
"""Unit tests for the Codex WS response builders.

The 5 builders on ``CodexWSHandler`` translate the frontend's
payload (``{tool_name, decision, ...}`` or
``{tool_name, permissions, scope}``) into the SDK-compliant dicts
that ``_async_approval_handler`` returns to Codex via the bridge.

Contract: return ``dict`` on valid input, ``None`` on invalid input.
``None`` triggers the upstream ``_safe_default_for`` fallback so the
SDK never receives a malformed wire response.
"""

from __future__ import annotations

import pytest

from twicc.providers.codex.ws import CodexWSHandler


@pytest.fixture
def handler():
    # The builders don't touch ``self.consumer`` — passing None is safe
    # for pure unit testing of the response builders.
    return CodexWSHandler(consumer=None)


class TestBuildCommandResponse:
    @pytest.mark.parametrize("decision", [
        "accept", "acceptForSession", "decline", "cancel",
    ])
    def test_simple_string_decisions_return_decision_key(self, handler, decision):
        result = handler._build_command_response(decision)
        # Lock the wire shape pinned by reading ws.py — adjust if the
        # actual return shape differs.
        assert result == {"decision": decision}

    def test_accept_with_execpolicy_amendment_happy(self, handler):
        amendment = [{"rule": "git status"}]
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment},
        })
        assert isinstance(result, dict)
        # The SDK wire shape — confirm by reading ws.py and replace if needed.
        assert result == {
            "decision": {
                "acceptWithExecpolicyAmendment": {"execpolicy_amendment": amendment},
            },
        }

    def test_apply_network_policy_amendment_happy(self, handler):
        amendment = {"host": "example.com"}
        result = handler._build_command_response({
            "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment},
        })
        assert isinstance(result, dict)
        assert result == {
            "decision": {
                "applyNetworkPolicyAmendment": {"network_policy_amendment": amendment},
            },
        }

    def test_unknown_string_returns_none(self, handler):
        assert handler._build_command_response("totally_made_up") is None

    def test_amendment_with_non_list_returns_none(self, handler):
        result = handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": "not a list"},
        })
        assert result is None

    def test_amendment_with_non_dict_returns_none(self, handler):
        result = handler._build_command_response({
            "applyNetworkPolicyAmendment": {"network_policy_amendment": "not a dict"},
        })
        assert result is None

    def test_dict_with_no_recognized_key_returns_none(self, handler):
        assert handler._build_command_response({"randomKey": {}}) is None

    def test_dict_with_multiple_keys_returns_none(self, handler):
        # Spec: dict variant must have EXACTLY one key.
        assert handler._build_command_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": []},
            "applyNetworkPolicyAmendment": {"network_policy_amendment": {}},
        }) is None


class TestBuildFileResponse:
    @pytest.mark.parametrize("decision", [
        "accept", "acceptForSession", "decline", "cancel",
    ])
    def test_simple_string_decisions(self, handler, decision):
        result = handler._build_file_response(decision)
        assert result == {"decision": decision}

    def test_dict_input_returns_none(self, handler):
        # fileChange has no amendment variants — dict input is invalid.
        assert handler._build_file_response({
            "acceptWithExecpolicyAmendment": {"execpolicy_amendment": []},
        }) is None

    def test_unknown_string_returns_none(self, handler):
        assert handler._build_file_response("nonsense") is None


class TestBuildPermissionsResponse:
    def test_happy_turn_scope(self, handler):
        content = {"permissions": {"network": True}, "scope": "turn"}
        result = handler._build_permissions_response(content)
        assert isinstance(result, dict)
        # Lock wire shape — refine after reading ws.py.
        assert result["permissions"] == {"network": True}
        assert result["scope"] == "turn"

    def test_happy_session_scope(self, handler):
        content = {"permissions": {"fileSystem": []}, "scope": "session"}
        result = handler._build_permissions_response(content)
        assert isinstance(result, dict)
        assert result["scope"] == "session"

    def test_missing_permissions_returns_none(self, handler):
        assert handler._build_permissions_response({"scope": "turn"}) is None

    def test_missing_scope_returns_none(self, handler):
        assert handler._build_permissions_response({"permissions": {}}) is None

    def test_invalid_scope_returns_none(self, handler):
        assert handler._build_permissions_response({
            "permissions": {}, "scope": "forever",
        }) is None

    def test_non_dict_permissions_returns_none(self, handler):
        assert handler._build_permissions_response({
            "permissions": "not a dict", "scope": "turn",
        }) is None

    def test_strict_auto_review_optional_bool_accepted(self, handler):
        content = {
            "permissions": {},
            "scope": "turn",
            "strictAutoReview": True,
        }
        result = handler._build_permissions_response(content)
        assert isinstance(result, dict)


class TestBuildCodexResponse:
    def test_command_execution_dispatches_to_command_builder(self, handler):
        result = handler._build_codex_response("commandExecution", {"decision": "accept"})
        assert result == {"decision": "accept"}

    def test_file_change_dispatches_to_file_builder(self, handler):
        result = handler._build_codex_response("fileChange", {"decision": "decline"})
        assert result == {"decision": "decline"}

    def test_permissions_dispatches_to_permissions_builder(self, handler):
        content = {"permissions": {}, "scope": "turn"}
        result = handler._build_codex_response("permissions", content)
        assert isinstance(result, dict)
        assert result["scope"] == "turn"

    def test_unknown_tool_name_falls_back_to_safe_default(self, handler):
        # _build_codex_response routes unknown tool_names through
        # _safe_default_for. The fallback shape mirrors the safe default
        # for that tool (or returns the generic default if even that is
        # unknown). Confirm by reading the dispatcher in ws.py.
        result = handler._build_codex_response("totally_made_up", {})
        # Tighten the assertion once you've read the source.
        assert isinstance(result, dict)


class TestSafeDefaultFor:
    def test_command_execution_returns_decline(self, handler):
        result = handler._safe_default_for("commandExecution")
        assert result == {"decision": "decline"}

    def test_file_change_returns_decline(self, handler):
        result = handler._safe_default_for("fileChange")
        assert result == {"decision": "decline"}

    def test_permissions_returns_empty_grant(self, handler):
        result = handler._safe_default_for("permissions")
        # Spec: empty permissions + turn scope (same as
        # ``approvals.default_response_for``).
        assert result == {"permissions": {}, "scope": "turn"}

    def test_unknown_tool_returns_safe_fallback(self, handler):
        # Reading ws.py: the function probably returns a defensive
        # ``{"decision": "decline"}`` for unknown tool_names (the safest
        # interpretation for the SDK). Confirm and tighten.
        result = handler._safe_default_for("nonsense")
        assert isinstance(result, dict)
```

- [ ] **Step 3: Read the source and tighten the loose assertions**

A few places in the test above use `assert isinstance(result, dict)` as a placeholder. After reading `codex/ws.py`, replace each with an exact-shape assertion. The two that need tightening:

1. `TestBuildCodexResponse.test_unknown_tool_name_falls_back_to_safe_default` — assert on the exact fallback dict
2. `TestSafeDefaultFor.test_unknown_tool_returns_safe_fallback` — assert on the exact fallback dict
3. `TestBuildPermissionsResponse.test_strict_auto_review_optional_bool_accepted` — assert on whether `strictAutoReview` is present in the result and what its type is

If `_build_codex_response("totally_made_up", {})` raises (vs. returning a dict from `_safe_default_for`), switch to `pytest.raises`. The source is authoritative.

- [ ] **Step 4: Run the tests**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run --with pytest --with pytest-django pytest tests/test_codex_ws_responses.py -v
```

All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add tests/test_codex_ws_responses.py
git commit -m "$(cat <<'EOF'
test(codex): cover WS response builders for the 3 approval methods

Five pure helpers in ``codex/ws.py`` translate the frontend payload
into SDK-compliant dicts:

- ``_build_command_response``: 4 simple strings + 2 dict variants
  (acceptWithExecpolicyAmendment, applyNetworkPolicyAmendment), each
  with their inner-shape validation
- ``_build_file_response``: 4 simple strings only (no amendments)
- ``_build_permissions_response``: scope + permissions dict +
  optional strictAutoReview boolean
- ``_build_codex_response``: dispatcher routing on tool_name
- ``_safe_default_for``: wire-safe fallbacks per tool_name

Tests parametrise across the 4 string decisions, exercise both
dict variants (happy + ValueError on bad inner type), and pin the
permissions wire shape down. Locks the WS contract so a frontend
shape drift surfaces as a backend test failure rather than a
runtime SDK rejection.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Background re-compute verification (Minor M2)

**Files:**
- Read: `src/twicc/providers/compute_base.py` (lines around `create_tool_result_link_live`, ~1100-1240)
- Possibly modify: same file

The `_denied_tool_ids` side-table on `CodexAgent` is in-memory only. After a backend restart, a background re-compute on a session's JSONL has no agent to consult — `_denied_tool_reason` returns `None` — and the 3 exit-code helpers return `None` for "aborted by user" / "Rejected by user" trailers (no exit code). So a re-compute's `ToolResultInfo` has `is_error=False`.

The question is: does `create_tool_result_link_live` (or whichever code path persists `ToolResultLink.error`) **overwrite** an existing `error` value with `None` when the new value is `None`?

If yes → the live-path error gets silently erased on re-compute. Need fix.

If no → no action; just document the result.

- [ ] **Step 1: Inspect the code path**

Read `src/twicc/providers/compute_base.py` around `create_tool_result_link_live` (around line 1130-1240 per the file structure). Look at the `ToolResultLink.objects.get_or_create` + any update calls. Determine:

1. On a re-compute pass (existing row in DB), does the code call `.save()` / `.update()` with the new `error` even if `None`?
2. Is there any guard like `if new_error is not None or existing_error is None`?

- [ ] **Step 2: Decide based on findings**

**Option A**: No bug — the code path doesn't update existing rows on re-compute. Document and skip the fix.

**Option B**: Bug confirmed — the existing error IS overwritten. Apply a defensive fix:

```python
# In create_tool_result_link_live, where the ToolResultLink is upserted:
# Skip overwriting `error` and `extra` when the new value is None and
# the existing value is non-null. This protects the live-path-recorded
# refusal reason (from CodexAgent._denied_tool_ids) when a background
# re-compute later produces a None error for the same JSONL line.
defaults_for_create = {
    'tool_name': tool_name,
    'tool_result_at': item.timestamp,
    'extra': extra,
    'error': error,
}
link, created = ToolResultLink.objects.get_or_create(
    session_id=session_id,
    tool_use_line_num=candidate.line_num,
    tool_result_line_num=item.line_num,
    tool_use_id=tool_use_id,
    defaults=defaults_for_create,
)
if not created:
    # Preserve previously-recorded error / extra if the new compute
    # produced None for them — happens when the live path's
    # _denied_tool_ids signal is no longer available (agent restart).
    update_fields = []
    if error is not None and link.error != error:
        link.error = error
        update_fields.append('error')
    if extra is not None and link.extra != extra:
        link.extra = extra
        update_fields.append('extra')
    # ... other fields that should always be refreshed
    if update_fields:
        link.save(update_fields=update_fields)
```

(The actual diff depends on the current code shape — adapt to what's there.)

- [ ] **Step 3: If a fix is applied, add a small test or assertion**

If Option B was implemented, add a unit test (or a Django integration test) at `tests/test_codex_recompute_persistence.py` that:
1. Creates a `ToolResultLink` row with `error="User denied this action"` (simulating live path)
2. Re-runs the create_tool_result_link_live path with `error=None` for the same JSONL line
3. Asserts the `error` is still `"User denied this action"` (not overwritten)

This test requires the `db` fixture (Django test DB) — see `tests/conftest.py:db_setup` for the pattern.

- [ ] **Step 4: Commit**

If Option A (no bug):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git commit --allow-empty -m "$(cat <<'EOF'
docs(codex): verify no overwrite of ToolResultLink.error on re-compute

Investigated whether ``BaseSessionCompute.create_tool_result_link_live``
could erase a previously-recorded refusal reason when a background
re-compute runs without a live agent (i.e. ``_denied_tool_reason``
returns None for a Codex JSONL line that was originally marked
errored via ``CodexAgent._denied_tool_ids``).

Result: no overwrite. The code path [explain why — e.g.
``get_or_create`` doesn't update existing rows / the update branch
guards against None / etc.]. Live-path errors are persisted.

No code change; commit kept for traceability of the investigation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

If Option B (bug fixed):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/compute_base.py tests/test_codex_recompute_persistence.py
git commit -m "$(cat <<'EOF'
fix(compute): preserve existing ToolResultLink error/extra on re-compute

Background re-compute on a Codex session's JSONL line that was
originally marked errored via the live path (``CodexAgent._denied_tool_ids``
→ ``compute.py:_denied_tool_reason`` → ``ToolResultLink.error``) used
to silently erase the error: re-compute has no live agent, so the
helper returns None and the upsert overwrites the live-path value.

Defensive fix in ``create_tool_result_link_live``: on update (row
already exists), only overwrite ``error`` / ``extra`` when the new
value is non-None. The live path's recorded reason wins because it
arrived first and was real, while a stale re-compute can't recover
that signal.

Includes a Django-DB integration test that simulates the
overwrite path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Debug log on cancel-turn siblings (Minor M3)

**Files:**
- Modify: `src/twicc/providers/codex/agent/agent.py` (around `_record_decision_outcome`'s cancel branch, ~lines 860-880)

The carryover memo asked for a debug log inside the cancel-turn siblings iteration so future smoke tests can see which sibling itemIds get marked. Today there's an outer log "outcome=cancel reason='User cancelled this turn' siblings_marked=[...]" but the per-iteration trace isn't there.

- [ ] **Step 1: Inspect the current cancel branch**

Read `src/twicc/providers/codex/agent/agent.py` around lines 850-880 to find the cancel branch:

```python
if decision == "cancel":
    self._denied_tool_ids[item_id] = "User cancelled this turn"
    siblings_marked: list[str] = []
    for other_id, payload in self._items_by_id.items():
        if other_id == item_id:
            continue
        if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:
            self._denied_tool_ids[other_id] = "User cancelled this turn"
            siblings_marked.append(other_id)
    logger.debug(
        "Codex decision recorded: session=%s itemId=%s "
        "outcome=cancel reason=%r siblings_marked=%s",
        ...
    )
```

- [ ] **Step 2: Add the per-iteration log**

Inside the `for other_id, payload in self._items_by_id.items():` loop, AFTER the `siblings_marked.append(other_id)` line, add:

```python
logger.debug(
    "Codex cancel: marking sibling session=%s itemId=%r type=%r",
    self.session_id, other_id, payload.get("type"),
)
```

This fires once per sibling actually marked (the `continue` and the type-filter both skip non-marks).

- [ ] **Step 3: Verify by inspection**

Read the function after the edit to confirm:
- The new log is inside the `if payload.get("type") in self._CANCELLABLE_ITEM_TYPES:` block (so it doesn't fire for items that don't qualify)
- It uses `self.session_id` (consistent with the other logs in the same function)
- The outer summary log still fires once with the aggregated `siblings_marked` list

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/codex/agent/agent.py
git commit -m "$(cat <<'EOF'
chore(codex): per-sibling debug log on cancel-turn marking

The outer ``outcome=cancel siblings_marked=[...]`` log shows the
final list but not the per-item type. Adding a debug line inside
the sibling iteration makes multi-tool cancel scenarios visible
in the logs (which itemId of which type was marked).

Carryover from the PR2c Task 3 code review (Minor M3). No
behaviour change.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: New memory `project_codex_approvals.md`

**Files:**
- Create: `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/project_codex_approvals.md`
- Modify: `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/MEMORY.md` (add 1-line index entry)

The full memory directory is at:
`/home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/`

- [ ] **Step 1: Create the memory file**

Path: `/home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/project_codex_approvals.md`

Use the standard memory frontmatter format (see the auto-memory section of the system instructions — `name`, `description`, `type`, then content).

Content (copy-paste verbatim):

```markdown
---
name: Codex approvals architecture
description: Sync↔async bridge + permission_mode presets + side-tables on CodexAgent — refer when touching the Codex approval pipeline
type: project
---

# Codex approvals — architecture summary

Shipped across 4 PRs (PR1–PR4) on `feature/multi-provider` between 2026-05-13
and 2026-05-15. Replaces the prior Codex bypass (always-allow, `yolo` mode)
with a full Claude-shaped interactive approval flow over the existing
`PendingRequest` plumbing.

## Architecture in one diagram

```
[Codex SDK worker thread]
   ↓ (sync) _sync_approval_handler(method, params)
[CodexAgent — sync↔async bridge]
   ↓ asyncio.run_coroutine_threadsafe(coro, self._loop).result()
[CodexAgent main loop] _async_approval_handler(method, params)
   ↓ make_pending_request(method, params)
[BaseAgent._await_pending_request] → broadcast PendingRequest over WS
   ↓ awaits Future
[Frontend] PendingRequestForm.vue (shell) → codex/PendingRequestBody.vue (body)
   ↓ user clicks Approve/Deny/Cancel
[WS] codex:pending_request_response → manager.resolve_pending_request → Future.set_result
   ↓ Future resolves
[CodexAgent main loop continues]
   ↓ _record_decision_outcome(method, params, response) — populates _denied_tool_ids
[returns response dict to sync handler]
   ↓ worker thread returns to SDK
[Codex SDK forwards to Rust core]
```

## Key files

- `src/twicc/providers/codex/agent/agent.py` — `CodexAgent` itself. The monkey-patch onto `self._codex._client._sync._approval_handler` happens in `__init__`. The bridge functions are `_sync_approval_handler` and `_async_approval_handler`.
- `src/twicc/providers/codex/agent/approvals.py` — pure helpers: `is_approval_method`, `derive_request_id`, `make_pending_request`, `default_response_for`.
- `src/twicc/providers/codex/agent/manager.py` — `CodexAgentManager.send_to_session` refreshes `agent.agent_settings` per call so live picker changes (`permission_mode`, `effort`) take effect on the next turn.
- `src/twicc/providers/codex/permission_modes.py` — 5-mode preset table, `resolve_codex_policy` for `thread_start`/`thread_resume`, `resolve_codex_turn_overrides` for per-turn `thread.turn` overrides.
- `src/twicc/providers/codex/ws.py` — `_build_codex_response` family that turns the frontend payload into the SDK-compliant response.
- `src/twicc/providers/codex/compute.py` — `_denied_tool_reason` is the watcher-side consumer of `_denied_tool_ids`; it stamps `ToolResultLink.error` (and via `compute_link_extra`, `extra.is_terminated`) when the user refused a tool.
- `frontend/src/components/message/PendingRequestForm.vue` — thin shell (header + body slot + dispatch on `@submit`).
- `frontend/src/components/session/detail/items/codex/PendingRequestBody.vue` — per-tool-name rich rendering (commandExecution / fileChange / permissions) + split-button Approve menu + tooltips.

## Permission_mode preset table (5 modes since PR3)

| Mode | sandbox_mode | approval_policy | Prompts? | Can write? |
|------|--------------|-----------------|----------|------------|
| `read_only` | read-only | on-request | yes | no |
| `strict` | read-only | never | no | no |
| `auto` *(default)* | workspace-write | on-request | yes | workspace only |
| `autonomous` | workspace-write | never | no | workspace only |
| `yolo` | danger-full-access | never | no | anywhere |

The agent re-reads `agent_settings.permission_mode` (AND `effort`) on **every turn** in `_run_turn`. `thread.turn(approval_policy=, sandbox_policy=)` takes per-turn overrides that the SDK applies on top of the values bound at `thread_start`. Picker change mid-session → next turn picks up the new mode.

## Side-tables on `CodexAgent`

Two in-memory dicts:

- **`_items_by_id`** (PR2a Task 3): `{itemId: item_payload_dump}`. Populated on `item/started`, popped on `item/completed`, cleared on `interrupt_or_kill`. Used by `_enrich_params_with_item_payload` to inject the diff into `fileChange` `PendingRequest`s — the approval payload itself doesn't carry it (spec §1.1.b).

- **`_denied_tool_ids`** (PR2c Task 3): `{itemId: reason_string}`. Populated by `_record_decision_outcome` when the user refused a tool (Deny / Cancel turn / empty permissions). Consumed by `compute.py:_denied_tool_reason` at watcher time to stamp `ToolResultLink.error` and the `extra.is_terminated` flag (PR2c bug fix on `exec_command` spinner orphan).

Both are agent-lifetime, cleared on `interrupt_or_kill`. After a backend restart, the side-tables are gone — see "Background re-compute caveat" below.

## sync↔async bridge — `asyncio.CancelledError` vs `concurrent.futures.CancelledError`

CAUGHT BY PR2A REVIEW. These are DIFFERENT classes since Python 3.8. The `_sync_approval_handler` calls `asyncio.run_coroutine_threadsafe(coro, self._loop).result()` from a worker thread:
- The asyncio coroutine raises `asyncio.CancelledError` (BaseException subclass)
- `run_coroutine_threadsafe(...).result()` re-raises it as `concurrent.futures.CancelledError` (Exception subclass) on the worker-thread side

The bridge MUST catch both:

```python
except (asyncio.CancelledError, concurrent.futures.CancelledError):
    return default_response_for(method)
```

If you ever bridge another async→sync surface, this gotcha applies.

## Spec §0.2 deviation — live permission_mode update

Spec §0.2 originally said "Pas de mode runtime style Claude (changement live ... en cours de session)". This was wrong — user confirmed at PR2c smoke test that live update is the intended behaviour (agent_settings is a "closed bundle" applied every turn, per CLAUDE.md frontend section). PR2c (`14ed2e4c`) wired it. Spec file itself was not amended (per the "no historical doc edits" rule); deviation documented in:
- Plan PR2c
- Commit body
- Code comment in `_run_turn`

## Background re-compute caveat

`_denied_tool_ids` is in-memory only. After backend restart, a background re-compute of a session's JSONL has no agent to consult; `_denied_tool_reason` returns None; the exit-code helpers return None for "aborted by user" / "Rejected by user" trailers. So a re-compute's `ToolResultInfo.is_error == False`.

PR4 Task 4 verified whether this overwrites the live-path-persisted `ToolResultLink.error`. The verdict + fix (if any) is logged in commits around 2026-05-15. If the bug exists, the fix lives in `create_tool_result_link_live` defensively skipping the overwrite when the new value is None.

## Frontend factorisation

The shell `PendingRequestForm.vue` owns ONLY: card wrapper, `<wa-divider>`, shared header (icon + title + count badge + expand toggle), `isResponding` guard, dispatch via `respondToPendingRequest` on the body's `@submit` event.

Per-provider bodies live in `frontend/src/components/session/detail/items/<provider>/PendingRequestBody.vue`. They emit a single `'submit'` event carrying the provider-shaped payload that `respondToPendingRequest` expects as its `responseData` argument. Bodies never call `respondToPendingRequest` directly — only the shell does.

Routing in the shell uses `<component :is="bodyComponent" />` (dynamic component), NOT a chain of `<template v-if>` branches (PR2b commit `4831f2c6` showed the Vue SFC compiler trips on nested `<template v-else-if>`).

## How to trigger each approval type in dev

- **commandExecution**: ask Codex (any mode except `strict` / `autonomous` / `yolo`) to run a shell command outside the read-only / workspace-write scope (e.g. `touch /tmp/file.txt` from a session in `read_only` mode, or any network access).
- **fileChange**: ask Codex (read_only mode) to create / modify / delete a file in the project — emits an `apply_patch` style approval.
- **permissions**: rare. Typically emitted by an MCP server requesting custom permissions. Not easily reproducible without an MCP setup. The code path is exercised by unit tests on `_build_permissions_response` instead.
```

- [ ] **Step 2: Add an index entry to `MEMORY.md`**

Edit `/home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/MEMORY.md`. Append a line in the appropriate section (after the existing project_* entries, alphabetised):

```markdown
- [Codex approvals architecture](project_codex_approvals.md) — sync↔async bridge + presets + side-tables, refer when touching Codex approval pipeline
```

Keep the line under ~150 characters per the auto-memory guidelines.

- [ ] **Step 3: Verify by reading both files**

```bash
ls /home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/project_codex_approvals.md
grep -n 'project_codex_approvals' /home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/MEMORY.md
```

Expected: file exists, MEMORY.md has 1 hit.

- [ ] **Step 4: Commit**

**Note**: the memory directory lives OUTSIDE the worktree (at `~/.claude/projects/...`). It's not tracked by the `feature/multi-provider` git repo. Therefore there's no commit for these files in the codebase — they're personal-machine artifacts. Skip the git commit step for this task.

(If the user wants them committed to a dotfiles repo elsewhere, that's a separate concern.)

---

### Task 7: Update memory `reference_codex_sdk_update_procedure.md`

**Files:**
- Modify: `~/.claude/projects/-home-twidi-dev-twicc-poc/memory/reference_codex_sdk_update_procedure.md`

The carryover memo lists 3 new check items to add to the SDK update procedure:

- [ ] **Step 1: Read the current memory file**

```bash
cat /home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/reference_codex_sdk_update_procedure.md
```

Note the existing structure (sections, bullet style, formatting).

- [ ] **Step 2: Add the 3 new check items**

Append (in the most appropriate section — probably alongside other "verify these still exist" items) the following bullets:

```markdown
- **`SandboxPolicy` variant classes** still exist at `codex_app_server.generated.v2_all`:
  - `ReadOnlySandboxPolicy`, `WorkspaceWriteSandboxPolicy`, `DangerFullAccessSandboxPolicy`
  - Their `type` Literal values are still the same wire strings: `"readOnly"`, `"workspaceWrite"`, `"dangerFullAccess"` (discriminator on the union — a rename here would silently flip live sessions to the wrong sandbox)
- **`AsyncThread.turn` signature** still accepts `approval_policy=` and `sandbox_policy=` kwargs for per-turn overrides. This is what TwiCC uses to apply live picker changes mid-session — `CodexAgent._run_turn` at `src/twicc/providers/codex/agent/agent.py` calls it on every turn.
- **`AskForApproval` is still a `RootModel` union** accepting the string constructor (`AskForApproval("never")`, `AskForApproval("on-request")`) and that those two wire strings are still valid Literals.
```

If the existing memory has a structured "checklist" section, slot these in as new entries. If it's free-form prose, append a new "## Post-PR3 additions" section.

- [ ] **Step 3: No commit** (same as Task 6 — memory files outside the worktree).

---

### Task 8: CHANGELOG.md entry

**Files:**
- Modify: `CHANGELOG.md` (at the repo root)

- [ ] **Step 1: Read the existing CHANGELOG.md**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
head -30 CHANGELOG.md
```

Note the format. There should be an `## [Unreleased]` section at the top.

- [ ] **Step 2: Add the entry under `[Unreleased]`**

In `## [Unreleased]`, under a sub-heading like `### Added` (create the sub-heading if it doesn't exist yet for this release window), add:

```markdown
### Added

- Codex sessions now expose interactive approvals (Approve / Deny / Cancel turn) for shell commands, file changes, and permission requests, replacing the prior always-allow bypass.
- 5 permission modes available in the session picker: Read-only, Strict, Auto, Autonomous, YOLO. Switching the picker mid-session takes effect on the next turn.
- The Approve button is a split-button: Once / For this session / Add allow rule (when Codex proposes an execpolicy amendment) / Allow network access (when Codex proposes a network policy amendment).
```

If the `[Unreleased]` section already has entries, preserve them and add the new ones in a coherent place.

- [ ] **Step 3: Verify**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
head -25 CHANGELOG.md
```

The new lines should be visible under `[Unreleased]`.

- [ ] **Step 4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add CHANGELOG.md
git commit -m "$(cat <<'EOF'
docs: CHANGELOG entry for Codex interactive approvals

User-facing summary of the 4-PR Codex approvals rollout:
interactive approvals for the 3 Codex server-side methods
(commandExecution, fileChange, permissions), 5 permission modes
in the session picker, live mid-session mode updates, and the
split-button Approve menu with conditional amendment items.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Final smoke test (user-assisted)

- [ ] **Step 1: Run the full test suite**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
TWICC_DATA_DIR=$PWD uv run --with pytest --with pytest-django pytest tests/ -v
```

Expected: 247 prior tests + new tests added by Tasks 1, 2, 3 all PASS. Total ~280-310 tests.

- [ ] **Step 2: Verify the cancel-siblings log fires (Task 5)**

Restart back, trigger a cancel turn on a multi-tool session, observe the new per-iteration log:

```bash
tail -F /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log | grep 'Codex cancel: marking sibling'
```

- [ ] **Step 3: User opens the new memory file and checks it reads well**

```bash
less /home/twidi/.claude/projects/-home-twidi-dev-twicc-poc/memory/project_codex_approvals.md
```

The memory should serve as a future-proof reference. Any unclear sentence or outdated reference → fix in a follow-up.

---

## Out of scope

The following are explicitly OUT of PR4 (would balloon the scope without obvious value):

- Integration tests for the sync↔async bridge with a real Codex transport (would require a mock SDK + a test harness that doesn't exist today)
- Frontend unit tests on the body components (Vue Testing Library is not set up in TwiCC)
- E2E tests via Playwright / Cypress (not set up)
- Tests on `_record_decision_outcome` and `_denied_tool_reason` directly (tightly coupled to `CodexAgent` / agent registry state — would need extensive mocking)

If any of these become high-value later, they're separate plans.

---

## Acceptance criteria

- [x] All new unit test files exist and PASS via the standard pytest invocation
- [x] If Background re-compute Task 4 found a bug, the defensive fix is in place + a regression test covers it
- [x] The per-sibling cancel log appears in the backend log on a multi-tool cancel
- [x] `project_codex_approvals.md` exists in the memory directory and reads as a coherent reference
- [x] `reference_codex_sdk_update_procedure.md` includes the 3 new check items
- [x] `CHANGELOG.md [Unreleased]` describes the Codex interactive approvals feature in user-facing terms
- [x] Full test suite passes (prior + new)
