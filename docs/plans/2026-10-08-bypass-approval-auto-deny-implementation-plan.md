# Auto-Deny of Bypass Approvals — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In Claude `bypassPermissions`, the backend denies an unanswered dangerous-action approval after 120 seconds with a reason for the agent; the approval form shows a countdown; the CLI/MCP output and the `session.concluded` event carry the deadline.

**Architecture:** `BaseAgent._await_pending_request` gains an opt-in wall-clock timer (`auto_deny_response`). `ClaudeCodeAgent._handle_pending_request` arms it for an allow-list of action tools in `bypassPermissions`, and mirrors a `setMode` answer into its in-memory mode. The process-state payload carries the remaining seconds; the frontend store turns them into a local deadline; `PendingRequestForm.vue` displays it and refuses answers in the last second.

**Tech Stack:** Django 6 ASGI, Python ≥ 3.13, `claude-agent-sdk`, pytest + pytest-django, Vue 3 (`<script setup>`), Web Awesome 3, node:test.

**Spec:** [Auto-Deny of Approval Requests in Claude `bypassPermissions`](2026-10-08-bypass-approval-auto-deny-spec.md). It is the source of truth. Read it completely before Task 1.

## Global Constraints

Every task includes these requirements. Values are copied from the spec.

- `AUTO_DENY_DELAY_SECONDS` = **120**. Wall-clock deadline `auto_deny_at = created_at + 120` (epoch seconds, `time.time()`).
- `AUTO_DENY_CHECK_INTERVAL_SECONDS` = **5.0**: the timer re-checks the wall clock at most this far apart (machine suspend).
- `AUTO_DENY_TOOLS` = `{"Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit"}`.
- Deny message, exact text: `The user did not answer this permission request within 2 minutes, so TwiCC denied it automatically. The action looked potentially dangerous. Do not retry it as is: find another way to reach your goal, or explain to the user what you need.` The `2` comes from the constant.
- Claude deny value: `PermissionResultDeny(message=AUTO_DENY_MESSAGE, interrupt=False)`.
- Arming rule: `request_type == "tool_approval"` and `tool_name in AUTO_DENY_TOOLS` and not `untrusted` and `agent_settings.permission_mode == "bypassPermissions"`. Nothing else is ever armed. Hybrid and Codex are not touched.
- Wire key (process state, per pending request): `auto_deny_in_seconds`, rounded to 0.1 s, floor `0`, emitted only when `auto_deny_at is not None` (never a truthiness test).
- CLI `out_of_scope` entry keys: `auto_deny_at` (ISO 8601, `created_at_iso`) and `auto_deny_in_seconds` (same value as the wire key). MCP event `data` key: `auto_deny_at` (ISO 8601).
- Frontend: lock margin **1000 ms** (answers refused when less is left; the display then reads `0:00`), keep tolerance **500 ms**, display `m:ss` rounded down, labels `Auto-deny in m:ss` and `Auto-denying…`, tooltip `Bypass permissions mode: this request is denied automatically when nobody answers within 2 minutes.`
- `setMode` mirror rule: a `PermissionResultAllow` whose `updated_permissions` carries `type == "setMode"`, a non-empty `mode`, and `destination` in `(None, "session")`.
- Plugin version: patch bump of `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (currently `0.108.1` → `0.108.2`; read the file first and bump whatever patch it holds). Never put the plugin version in a commit subject.
- Never edit any file under `docs/plans/` (the spec and this plan included) and never edit `docs/plans/2026-10-03-mcp-events-session-concluded-spec.md`: it is historical. Task 3 adapts the test that reads it instead.
- Work in `/home/twidi/dev/twicc-poc` on the current branch (`main`). No branch, no worktree. Every Bash command starts with `cd /home/twidi/dev/twicc-poc && `.
- Backend tests: `uv run pytest <paths> -q`. Frontend tests: `cd frontend && npm test`. Lint: `uvx ruff check <paths>` (ruff is not installed; never `uv run ruff`).
- Lint rule: the repo has no ruff baseline. New files must be clean. In an existing file, add no new finding and do not fix older findings: compare `uvx ruff check <file>` before and after the edit.
- Never run `migrate`, `npm install`, `npm ci`, `uv pip`, anything with `--active`. Never start or restart servers. No database model change, no migration, no CHANGELOG change, no new dependency.
- Backend JSON: `orjson` only. No cosmetic import aliases.
- Stage files by name (`git add <file> …`), never a directory. Leave the untracked `docs/plans/2026-07-18-shared-artifact-import-design.md` and `docs/plans/2026-07-18-telemetry-dashboard-design.md` alone.
- Commit format: `type(scope): summary` + a body that explains the change + the trailer `Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>`. Every commit heredoc of this plan contains that literal placeholder: **replace it with your exact model name before you run the command** (e.g. `Claude Opus 5.5 (1M context)`).

## Review Focus

Inputs the spec implies but its own test list does not pin. Each line names the task that owns the test.

1. **The agent is killed or the turn interrupted during the wait.** The cancelled wait must leave no scheduled check behind. (Task 1: `test_a_cancelled_wait_leaves_no_timer`.)
2. **The user answers in the same instant a check fires.** The late check must find the Future done and do nothing, without raising `InvalidStateError`. (Task 1: `test_a_check_after_an_answer_is_a_no_op`.)
3. **Two armed approvals in parallel** (Claude's concurrent tool calls). Each keeps its own deadline; answering one leaves the other armed. (Task 1: `test_parallel_requests_keep_their_own_timers`.)
4. **A `setMode` answer for a non-session destination** (`userSettings`, `projectSettings`, `localSettings`). It must not change the session's in-memory mode. (Task 2: `test_a_set_mode_for_another_destination_is_not_mirrored`.)
5. **A browser that connects after the deadline has passed** (snapshot with `auto_deny_in_seconds: 0`). The form must be locked at once and show `0:00`. (Task 4: `a reconnect after the deadline is locked at once`.)

---

### Task 1: Backend core — constants, `PendingRequest.auto_deny_at`, base timer, wire field

**Files:**
- Create: `src/twicc/agent/auto_deny.py`
- Modify: `src/twicc/agent/states.py` (`PendingRequest`, `serialize_agent_info`, imports)
- Modify: `src/twicc/agent/base_agent.py` (`__init__`, `_await_pending_request`, new `_check_auto_deny`, imports)
- Test: `tests/test_auto_deny.py` (new)

**Interfaces:**
- Produces:
  - `twicc.agent.auto_deny`: `AUTO_DENY_DELAY_SECONDS: int`, `AUTO_DENY_CHECK_INTERVAL_SECONDS: float`, `AUTO_DENY_MESSAGE: str`, `AUTO_DENY_TOOLS: frozenset[str]`, `auto_deny_remaining(pending: PendingRequest, now: float) -> float | None`.
  - `PendingRequest.auto_deny_at: float | None = None` (last field of the frozen dataclass).
  - `BaseAgent._await_pending_request(request: PendingRequest, *, auto_deny_response: Any = None) -> Any`.
  - `BaseAgent._auto_deny_timers: dict[str, asyncio.TimerHandle]` (empty when nothing is armed).
  - `BaseAgent._check_auto_deny(request: PendingRequest, future: asyncio.Future, response: Any) -> None`.
  - Process-state entries carry `auto_deny_in_seconds` when armed.
  - `base_agent.py` reads `AUTO_DENY_DELAY_SECONDS` and `AUTO_DENY_CHECK_INTERVAL_SECONDS` as module globals (tests monkeypatch `twicc.agent.base_agent.<NAME>`).

- [ ] **Step 0: Record the baseline**

Before any change, record which backend tests already fail, so Task 6 can tell a regression from an existing failure:

Run (in the background, it takes several minutes): `cd /home/twidi/dev/twicc-poc && uv run pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort > /tmp/auto-deny-baseline-failures.txt; wc -l < /tmp/auto-deny-baseline-failures.txt`
Expected: the file exists (it may be empty; on 2026-10-08 it listed 7 failures in `tests/test_session_projection.py` and `tests/test_composer_attachment_target.py`).
Steps 1 and 2 may run meanwhile (they only add a new test file). **Wait for the baseline to finish before Step 3**: a test that imports lazily could otherwise read edited sources.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_auto_deny.py`:

```python
"""Auto-deny of unanswered approval requests: the base timer and the wire field.

Design: ``docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md`` (§4).
"""

import asyncio
from dataclasses import replace
import time
from types import SimpleNamespace

import pytest

from twicc.agent import base_agent as base_agent_module
from twicc.agent.auto_deny import (
    AUTO_DENY_DELAY_SECONDS,
    AUTO_DENY_MESSAGE,
    AUTO_DENY_TOOLS,
    auto_deny_remaining,
)
from twicc.agent.base_agent import BaseAgent
from twicc.agent.states import AgentInfo, AgentState, PendingRequest, serialize_agent_info
from twicc.core.enums import Provider
from twicc.providers.helpers import AgentSettings

# Stands for the provider's deny value: the base class never looks inside it.
DENY = object()


class _Agent(BaseAgent):
    """Minimal concrete agent — the timer lives on the base class."""

    provider = Provider.CLAUDE_CODE

    async def start(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def send_message(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *args, **kwargs):  # pragma: no cover - unused
        raise NotImplementedError


def _make_agent():
    return _Agent(session_id="s-1", project_id="-p", cwd="/tmp", agent_settings=AgentSettings())


def _request(request_id="r-1", *, created_at=None):
    return PendingRequest(
        request_id=request_id,
        request_type="tool_approval",
        tool_name="Bash",
        tool_input={"command": "rm -rf /tmp/x"},
        created_at=time.time() if created_at is None else created_at,
    )


@pytest.fixture
def fast_timer(monkeypatch):
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_DELAY_SECONDS", 0.2)
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_CHECK_INTERVAL_SECONDS", 0.02)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_the_message_names_the_delay_in_minutes():
    assert AUTO_DENY_DELAY_SECONDS == 120
    assert "within 2 minutes" in AUTO_DENY_MESSAGE
    assert "find another way" in AUTO_DENY_MESSAGE


def test_the_allow_list_holds_only_action_tools():
    assert AUTO_DENY_TOOLS == {"Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit"}


def test_remaining_is_none_when_unarmed_and_floored_at_zero():
    assert auto_deny_remaining(_request(), time.time()) is None
    armed = replace(_request(), auto_deny_at=1000.0)
    assert auto_deny_remaining(armed, 940.04) == 60.0
    assert auto_deny_remaining(armed, 1005.0) == 0.0


# ---------------------------------------------------------------------------
# The base timer
# ---------------------------------------------------------------------------


def test_an_unanswered_armed_request_resolves_with_the_deny_value(fast_timer):
    agent = _make_agent()

    result = asyncio.run(agent._await_pending_request(_request(), auto_deny_response=DENY))

    assert result is DENY
    assert agent.pending_requests == ()
    assert agent.last_pending_resolved_at > 0
    assert agent._auto_deny_timers == {}


def test_an_armed_request_carries_its_deadline_and_an_answer_wins(fast_timer):
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0)
        (pending,) = agent.pending_requests
        assert pending.auto_deny_at == pytest.approx(request.created_at + 0.2)
        assert request.request_id in agent._auto_deny_timers
        assert agent.resolve_pending_request(request.request_id, "allow")
        return await task

    assert asyncio.run(run()) == "allow"
    assert agent._auto_deny_timers == {}


def test_an_unarmed_request_has_no_deadline_and_no_timer():
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request))
        await asyncio.sleep(0)
        (pending,) = agent.pending_requests
        assert pending.auto_deny_at is None
        assert agent._auto_deny_timers == {}
        agent.resolve_pending_request(request.request_id, "allow")
        return await task

    assert asyncio.run(run()) == "allow"


def test_a_deadline_already_passed_denies_at_once():
    agent = _make_agent()
    request = _request(created_at=time.time() - AUTO_DENY_DELAY_SECONDS - 1)

    assert asyncio.run(agent._await_pending_request(request, auto_deny_response=DENY)) is DENY


def test_the_check_reschedules_until_the_deadline(fast_timer):
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0.05)
        assert not task.done()
        assert request.request_id in agent._auto_deny_timers
        return await asyncio.wait_for(task, 2)

    assert asyncio.run(run()) is DENY


def test_a_wall_clock_jump_denies_at_the_next_check(monkeypatch):
    # A machine suspend does not advance the event-loop clock: only the
    # wall-clock re-check can see that the deadline has passed.
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_DELAY_SECONDS", 100)
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_CHECK_INTERVAL_SECONDS", 0.02)
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0.05)
        assert not task.done()
        jumped = time.time() + 200
        monkeypatch.setattr(base_agent_module, "time", SimpleNamespace(time=lambda: jumped))
        return await asyncio.wait_for(task, 1)

    assert asyncio.run(run()) is DENY


def test_a_cancelled_wait_leaves_no_timer(fast_timer):
    agent = _make_agent()
    request = _request()

    async def run():
        task = asyncio.create_task(agent._await_pending_request(request, auto_deny_response=DENY))
        await asyncio.sleep(0)
        agent._cancel_all_pending_futures()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert agent._auto_deny_timers == {}
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert agent.pending_requests == ()


def test_a_check_after_an_answer_is_a_no_op(fast_timer):
    agent = _make_agent()

    async def run():
        future = asyncio.get_running_loop().create_future()
        future.set_result("allow")
        armed = replace(_request(), auto_deny_at=time.time() - 1)
        agent._check_auto_deny(armed, future, DENY)
        assert future.result() == "allow"
        assert agent._auto_deny_timers == {}

    asyncio.run(run())


def test_parallel_requests_keep_their_own_timers(fast_timer):
    agent = _make_agent()
    first, second = _request("r-1"), _request("r-2")

    async def run():
        first_task = asyncio.create_task(agent._await_pending_request(first, auto_deny_response=DENY))
        second_task = asyncio.create_task(agent._await_pending_request(second, auto_deny_response=DENY))
        await asyncio.sleep(0)
        assert set(agent._auto_deny_timers) == {"r-1", "r-2"}
        agent.resolve_pending_request("r-1", "allow")
        assert await first_task == "allow"
        assert set(agent._auto_deny_timers) == {"r-2"}
        return await asyncio.wait_for(second_task, 2)

    assert asyncio.run(run()) is DENY
    assert agent._auto_deny_timers == {}


# ---------------------------------------------------------------------------
# The process-state payload
# ---------------------------------------------------------------------------


def _info(*pending):
    now = time.time()
    return AgentInfo(
        session_id="s-1", project_id="-p", provider=Provider.CLAUDE_CODE,
        state=AgentState.ASSISTANT_TURN, previous_state=None,
        started_at=now, state_changed_at=now, last_activity=now,
        pending_requests=pending,
    )


def test_the_payload_carries_the_remaining_seconds_only_when_armed():
    armed = replace(_request("armed"), auto_deny_at=time.time() + 60)
    plain = _request("plain")

    entries = {e["request_id"]: e for e in serialize_agent_info(_info(armed, plain))["pending_requests"]}

    assert 59 <= entries["armed"]["auto_deny_in_seconds"] <= 60
    assert "auto_deny_in_seconds" not in entries["plain"]


def test_a_passed_deadline_is_sent_as_zero():
    late = replace(_request(), auto_deny_at=time.time() - 5)

    (entry,) = serialize_agent_info(_info(late))["pending_requests"]

    assert entry["auto_deny_in_seconds"] == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_auto_deny.py -q`
Expected: collection error `ModuleNotFoundError: No module named 'twicc.agent.auto_deny'`.

- [ ] **Step 3: Create `src/twicc/agent/auto_deny.py`**

```python
"""Auto-deny of unanswered approval requests.

Design: ``docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md``. In Claude
``bypassPermissions`` the CLI still prompts for some dangerous actions, and
nobody is expected to watch. TwiCC denies such a prompt after
``AUTO_DENY_DELAY_SECONDS`` without an answer, so the agent continues, like the
Claude Code CLI does. The timer lives in ``BaseAgent._await_pending_request``;
the provider decides what is armed and with which deny value.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .states import PendingRequest

AUTO_DENY_DELAY_SECONDS = 120

# ``loop.call_later`` runs on the monotonic clock, which does not advance
# during a machine suspend. The timer therefore re-checks the wall-clock
# deadline at most this far apart, bounding the delay after a resume.
AUTO_DENY_CHECK_INTERVAL_SECONDS = 5.0

AUTO_DENY_MESSAGE = (
    f"The user did not answer this permission request within {AUTO_DENY_DELAY_SECONDS // 60} minutes, "
    "so TwiCC denied it automatically. The action looked potentially dangerous. "
    "Do not retry it as is: find another way to reach your goal, or explain to the user what you need."
)

# Claude tools whose prompt in ``bypassPermissions`` is a safety check on a
# potentially dangerous action: the CLI's shell tools (``Monitor`` runs a shell
# command too) and file-write tools. Anything else — questions, plan reviews,
# interactive or MCP tools, a future unknown tool — keeps waiting for the human.
# ``MultiEdit`` is absent from current CLIs; keeping it covers older ones.
AUTO_DENY_TOOLS = frozenset({"Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit"})


def auto_deny_remaining(pending: PendingRequest, now: float) -> float | None:
    """Seconds left before ``pending`` is auto-denied, floored at 0.

    ``None`` when the request is not armed. ``now`` is a wall-clock instant
    (``time.time()``), the clock ``auto_deny_at`` uses.
    """
    if pending.auto_deny_at is None:
        return None
    return max(0.0, round(pending.auto_deny_at - now, 1))
```

- [ ] **Step 4: Add the field and the wire key in `src/twicc/agent/states.py`**

Add `import time` to the stdlib imports (after `from enum import StrEnum` is fine, keep the block sorted as it is: `dataclasses`, `enum`, `time`, `typing`):

```python
from dataclasses import dataclass
from enum import StrEnum
import time
from typing import NamedTuple
```

Add the import of the helper after `from twicc.core.enums import Provider`:

```python
from twicc.core.enums import Provider

from .auto_deny import auto_deny_remaining
```

Add the field as the last field of `PendingRequest` (after `permission_suggestions`), and extend the class docstring with one sentence:

```python
    created_at: float
    permission_suggestions: list[dict] | None = None
    # Wall-clock instant (same clock as ``created_at``) at which the backend
    # denies the request by itself; ``None`` when it is not armed. Set only by
    # ``BaseAgent._await_pending_request`` (see ``twicc.agent.auto_deny``).
    auto_deny_at: float | None = None
```

In `serialize_agent_info`, replace the pending-requests block:

```python
    if info.pending_requests:
        serialized = []
        for pr in info.pending_requests:
            entry = {
                "request_id": pr.request_id,
                "request_type": pr.request_type,
                "tool_name": pr.tool_name,
                "tool_input": pr.tool_input,
                "created_at": pr.created_at,
            }
            if pr.permission_suggestions:
                entry["permission_suggestions"] = pr.permission_suggestions
            serialized.append(entry)
        data["pending_requests"] = serialized
```

with:

```python
    if info.pending_requests:
        serialized = []
        now = time.time()
        for pr in info.pending_requests:
            entry = {
                "request_id": pr.request_id,
                "request_type": pr.request_type,
                "tool_name": pr.tool_name,
                "tool_input": pr.tool_input,
                "created_at": pr.created_at,
            }
            if pr.permission_suggestions:
                entry["permission_suggestions"] = pr.permission_suggestions
            # The time left, not the deadline: the browser may run on another
            # machine whose clock differs. ``is not None``, never truthiness —
            # ``0.0`` (deadline passed, deny imminent) must reach the client.
            if pr.auto_deny_at is not None:
                entry["auto_deny_in_seconds"] = auto_deny_remaining(pr, now)
            serialized.append(entry)
        data["pending_requests"] = serialized
```

- [ ] **Step 5: Add the timer in `src/twicc/agent/base_agent.py`**

Imports — add `from dataclasses import replace` to the stdlib block and the constants next to the other `twicc.agent` imports:

```python
import asyncio
from dataclasses import replace
import logging
import time
```

```python
from twicc.agent.auto_deny import AUTO_DENY_CHECK_INTERVAL_SECONDS, AUTO_DENY_DELAY_SECONDS
from twicc.agent.shell_notice import OwnerFacts, ShellLookup, ShellNoticeState, ShellResolution
```

In `__init__`, right after `self._pending_futures: dict[str, asyncio.Future[Any]] = {}`:

```python
        # Auto-deny checks of armed pending requests, keyed by request_id: the
        # next scheduled wall-clock check (see ``_check_auto_deny``). Empty when
        # nothing is armed. Cancelled in ``_await_pending_request``'s finally.
        self._auto_deny_timers: dict[str, asyncio.TimerHandle] = {}
```

Replace the head of `_await_pending_request` (signature, docstring and the lines up to `await self._notify_state_change()`):

```python
    async def _await_pending_request(self, request: PendingRequest) -> Any:
        """Register a pending request, broadcast, wait for resolution, return raw response.

        Provider subclasses construct the ``PendingRequest`` (which knows the
        provider-specific ``tool_name`` / ``tool_input`` / suggestions) and
        delegate the bookkeeping here. The Future's ``set_result`` is invoked
        by ``resolve_pending_request`` when the WS layer routes a user decision
        back, or via ``_cancel_all_pending_futures`` on kill.

        The return type is ``Any`` because each provider's wire decision is its
        own type — the caller in the subclass casts.
        """
        self._pending_requests[request.request_id] = request
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_futures[request.request_id] = future

        # Tell the frontend a new pending request is in flight.
        await self._notify_state_change()
```

with:

```python
    async def _await_pending_request(
        self, request: PendingRequest, *, auto_deny_response: Any = None,
    ) -> Any:
        """Register a pending request, broadcast, wait for resolution, return raw response.

        Provider subclasses construct the ``PendingRequest`` (which knows the
        provider-specific ``tool_name`` / ``tool_input`` / suggestions) and
        delegate the bookkeeping here. The Future's ``set_result`` is invoked
        by ``resolve_pending_request`` when the WS layer routes a user decision
        back, or via ``_cancel_all_pending_futures`` on kill.

        ``auto_deny_response`` arms the auto-deny (``twicc.agent.auto_deny``):
        the request gets ``auto_deny_at = created_at + AUTO_DENY_DELAY_SECONDS``
        and the Future resolves with this provider-specific deny value when
        nobody answers by then. ``None`` (the default) leaves it unarmed.

        The return type is ``Any`` because each provider's wire decision is its
        own type — the caller in the subclass casts.
        """
        if auto_deny_response is not None:
            request = replace(request, auto_deny_at=request.created_at + AUTO_DENY_DELAY_SECONDS)
        self._pending_requests[request.request_id] = request
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending_futures[request.request_id] = future

        # Tell the frontend a new pending request is in flight.
        await self._notify_state_change()
```

Then replace the start of the `try:` block of the same method:

```python
        try:
            return await future
        finally:
            # Drop the entry whether we resolved or were cancelled.
```

with:

```python
        try:
            # Started inside the ``try`` so that its ``finally`` always cancels
            # the scheduled check, whatever ends the wait.
            if auto_deny_response is not None:
                self._check_auto_deny(request, future, auto_deny_response)
            return await future
        finally:
            # Stop the auto-deny checks whatever ended the wait: an answer,
            # the auto-deny itself, or a cancellation (kill / interrupt).
            timer = self._auto_deny_timers.pop(request.request_id, None)
            if timer is not None:
                timer.cancel()
            # Drop the entry whether we resolved or were cancelled.
```

Add the new method right after `_await_pending_request` (before `_cancel_all_pending_futures`):

```python
    def _check_auto_deny(
        self, request: PendingRequest, future: asyncio.Future[Any], response: Any,
    ) -> None:
        """Deny ``request`` once its wall-clock deadline has passed, else check again later.

        The deadline is wall-clock (``time.time()``), the value the frontend
        counts down. ``loop.call_later`` runs on the monotonic clock, which does
        not advance during a machine suspend, so one long delay could fire long
        after the deadline: the check re-runs at most
        ``AUTO_DENY_CHECK_INTERVAL_SECONDS`` apart instead. A Future already
        done (answered, cancelled) ends the checks without touching it.
        """
        self._auto_deny_timers.pop(request.request_id, None)
        if future.done():
            return
        remaining = request.auto_deny_at - time.time()
        if remaining <= 0:
            with provider_log_context(self.provider):
                self._logger.info(
                    "[session %s] Auto-denying unanswered %s request %s",
                    self.session_id, request.tool_name, request.request_id,
                )
            future.set_result(response)
            return
        self._auto_deny_timers[request.request_id] = asyncio.get_running_loop().call_later(
            min(AUTO_DENY_CHECK_INTERVAL_SECONDS, remaining),
            self._check_auto_deny, request, future, response,
        )
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_auto_deny.py -q`
Expected: all tests PASS.

- [ ] **Step 7: Run the neighbouring suites**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_background_work.py tests/test_agent_status_label.py tests/test_agent_hidden_broadcast_gate.py tests/test_pending_question.py tests/test_pending_question_services.py tests/test_claude_ws_approval_permissions.py tests/test_claude_ws_question_responses.py tests/test_codex_ws_responses.py -q`
Expected: all PASS (no behavior change for unarmed requests).

- [ ] **Step 8: Lint**

Run: `cd /home/twidi/dev/twicc-poc && uvx ruff check src/twicc/agent/auto_deny.py tests/test_auto_deny.py`
Expected: clean.

Run: `cd /home/twidi/dev/twicc-poc && for f in src/twicc/agent/states.py src/twicc/agent/base_agent.py; do echo "$f before=$(git show HEAD:$f | uvx ruff check --stdin-filename $f -q - | wc -l) after=$(uvx ruff check $f -q | wc -l)"; done`
(`--stdin-filename` keeps the repository's ruff config for the "before" count.)
Expected: `after` ≤ `before` for both files.

- [ ] **Step 9: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/agent/auto_deny.py src/twicc/agent/states.py src/twicc/agent/base_agent.py tests/test_auto_deny.py && git commit -F - <<'EOF'
feat(agent): add an opt-in auto-deny timer to pending requests

A provider can now pass auto_deny_response to _await_pending_request: the
request gets an auto_deny_at deadline (created_at + 120 s) and resolves with
that deny value when nobody answers. The deadline is wall-clock and checked
at most 5 s apart, so a machine suspend does not delay the deny by minutes.
Every way out of the wait cancels the pending check.

The process-state payload carries auto_deny_in_seconds, the time left at
send time, so the browser needs no clock in sync with the server.

Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>
EOF
```

---

### Task 2: Claude — arm the action tools in bypass, mirror a `setMode` answer

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/agent.py` (imports, `_handle_pending_request`, new `_mirror_session_set_mode`)
- Test: `tests/test_claude_auto_deny.py` (new)

**Interfaces:**
- Consumes: `AUTO_DENY_MESSAGE`, `AUTO_DENY_TOOLS` from `twicc.agent.auto_deny`; `_await_pending_request(request, *, auto_deny_response=...)`, `PendingRequest.auto_deny_at`, `twicc.agent.base_agent.AUTO_DENY_DELAY_SECONDS` / `AUTO_DENY_CHECK_INTERVAL_SECONDS` (Task 1).
- Produces: `ClaudeCodeAgent._mirror_session_set_mode(response: PermissionResultAllow) -> None`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_claude_auto_deny.py`:

```python
"""Claude arming rule for the auto-deny and the setMode mirror.

Design: ``docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md`` (§2.4, §4.4, §4.5).
"""

import asyncio
from unittest.mock import AsyncMock

from claude_agent_sdk import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
)
import pytest

from twicc.agent import base_agent as base_agent_module
from twicc.agent.auto_deny import AUTO_DENY_MESSAGE
from twicc.providers.claude_code.agent.agent import ClaudeCodeAgent
from twicc.providers.helpers import AgentSettings

DANGEROUS = {"command": "rm -rf /tmp/auto-deny-test"}


def _make_agent(monkeypatch, mode="bypassPermissions", *, untrusted=False) -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent(
        "session-id",
        "project-id",
        "/tmp",
        AgentSettings(selected_model="opus", permission_mode=mode),
        AsyncMock(return_value=None),
        AsyncMock(),
        AsyncMock(),
    )
    monkeypatch.setattr(agent, "_resolve_untrusted_now", AsyncMock(return_value=untrusted))
    monkeypatch.setattr(agent, "get_permission_suggestions", lambda *args, **kwargs: None)
    return agent


async def _ask(agent, tool_name, tool_input):
    """Start the can_use_tool callback and return it with its registered request."""
    task = asyncio.create_task(agent._handle_pending_request(tool_name, tool_input, ToolPermissionContext()))
    for _ in range(50):
        if agent.pending_requests:
            break
        await asyncio.sleep(0)
    (pending,) = agent.pending_requests
    return task, pending


@pytest.mark.parametrize("tool_name,tool_input", [
    ("Bash", DANGEROUS),
    ("Monitor", DANGEROUS),
    ("Write", {"file_path": "/etc/auto-deny-test", "content": "x"}),
    ("Edit", {"file_path": "/etc/auto-deny-test", "old_string": "a", "new_string": "b"}),
])
def test_an_action_tool_is_armed_in_bypass(monkeypatch, tool_name, tool_input):
    agent = _make_agent(monkeypatch)

    async def run():
        task, pending = await _ask(agent, tool_name, tool_input)
        assert pending.auto_deny_at is not None
        agent.resolve_pending_request(pending.request_id, PermissionResultAllow())
        await task

    asyncio.run(run())


@pytest.mark.parametrize("mode,tool_name,tool_input,untrusted", [
    ("bypassPermissions", "AskUserQuestion", {"questions": []}, False),
    ("bypassPermissions", "ExitPlanMode", {"plan": "the plan"}, False),
    ("bypassPermissions", "mcp__other__tool", {}, False),
    ("bypassPermissions", "WebFetch", {"url": "https://example.com"}, False),
    ("bypassPermissions", "Bash", DANGEROUS, True),
    ("default", "Bash", DANGEROUS, False),
    ("acceptEdits", "Write", {"file_path": "/etc/auto-deny-test", "content": "x"}, False),
    ("plan", "Bash", DANGEROUS, False),
])
def test_everything_else_is_never_armed(monkeypatch, mode, tool_name, tool_input, untrusted):
    agent = _make_agent(monkeypatch, mode, untrusted=untrusted)

    async def run():
        task, pending = await _ask(agent, tool_name, tool_input)
        assert pending.auto_deny_at is None
        agent.resolve_pending_request(pending.request_id, PermissionResultDeny(message="no"))
        await task

    asyncio.run(run())


def test_an_unanswered_prompt_is_denied_with_the_message(monkeypatch):
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_DELAY_SECONDS", 0.1)
    monkeypatch.setattr(base_agent_module, "AUTO_DENY_CHECK_INTERVAL_SECONDS", 0.02)
    agent = _make_agent(monkeypatch)

    # wait_for: without the arming, nothing ever answers and the test must
    # fail on a timeout instead of hanging.
    result = asyncio.run(asyncio.wait_for(
        agent._handle_pending_request("Bash", DANGEROUS, ToolPermissionContext()), 2,
    ))

    assert isinstance(result, PermissionResultDeny)
    assert result.message == AUTO_DENY_MESSAGE
    assert result.interrupt is False
    assert agent.pending_requests == ()


def _allow_with_mode(mode, destination="session"):
    return PermissionResultAllow(
        updated_permissions=[PermissionUpdate(type="setMode", mode=mode, destination=destination)],
    )


@pytest.mark.parametrize("destination", ["session", None])
def test_an_allow_that_leaves_bypass_disarms_the_next_prompts(monkeypatch, destination):
    agent = _make_agent(monkeypatch)

    async def run():
        task, pending = await _ask(agent, "Bash", DANGEROUS)
        agent.resolve_pending_request(pending.request_id, _allow_with_mode("default", destination))
        await task
        assert agent.agent_settings.permission_mode == "default"

        task, pending = await _ask(agent, "Bash", DANGEROUS)
        assert pending.auto_deny_at is None
        agent.resolve_pending_request(pending.request_id, PermissionResultDeny(message="no"))
        await task

    asyncio.run(run())


def test_an_allow_that_enters_bypass_arms_the_next_prompts(monkeypatch):
    agent = _make_agent(monkeypatch, "default")

    async def run():
        task, pending = await _ask(agent, "Bash", DANGEROUS)
        assert pending.auto_deny_at is None
        agent.resolve_pending_request(pending.request_id, _allow_with_mode("bypassPermissions"))
        await task
        assert agent.agent_settings.permission_mode == "bypassPermissions"

        task, pending = await _ask(agent, "Bash", DANGEROUS)
        assert pending.auto_deny_at is not None
        agent.resolve_pending_request(pending.request_id, PermissionResultDeny(message="no"))
        await task

    asyncio.run(run())


@pytest.mark.parametrize("destination", ["userSettings", "projectSettings", "localSettings"])
def test_a_set_mode_for_another_destination_is_not_mirrored(monkeypatch, destination):
    agent = _make_agent(monkeypatch)

    async def run():
        task, pending = await _ask(agent, "Bash", DANGEROUS)
        agent.resolve_pending_request(pending.request_id, _allow_with_mode("default", destination))
        await task

    asyncio.run(run())
    assert agent.agent_settings.permission_mode == "bypassPermissions"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_claude_auto_deny.py -q`
Expected: FAIL — `test_an_action_tool_is_armed_in_bypass` (`auto_deny_at` is `None`), `test_an_unanswered_prompt_is_denied_with_the_message` (`TimeoutError` after 2 s: nothing denies), and the two mirror tests (mode unchanged). `test_everything_else_is_never_armed` and `test_a_set_mode_for_another_destination_is_not_mirrored` already pass: they pin behavior that must not change.

- [ ] **Step 3: Implement in `src/twicc/providers/claude_code/agent/agent.py`**

Imports — add `PermissionUpdate` to the `claude_agent_sdk` import:

```python
from claude_agent_sdk import (
    AssistantMessage, ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ResultMessage, StreamEvent, SystemMessage, ThinkingConfigAdaptive, ThinkingConfigDisabled,
    ToolPermissionContext, ToolResultBlock, ToolUseBlock, UserMessage,
)
```

and, next to the other `twicc.agent` imports:

```python
from twicc.agent import AgentInfo, AgentState, BaseAgent, PendingRequest, StateChangeCallback
from twicc.agent.auto_deny import AUTO_DENY_MESSAGE, AUTO_DENY_TOOLS
from twicc.agent.plugin import get_plugin_dir
```

In `_handle_pending_request`, correct the stale comment of the `mcp__twicc__` short-circuit. Replace:

```python
        # has. This fires before any pending request is created, so no prompt
        # ever appears. In ``bypassPermissions`` the callback isn't invoked at
        # all; this covers the restrictive modes (default/plan/acceptEdits).
        # See MCP plan D9.
```

with:

```python
        # has. This fires before any pending request is created, so no prompt
        # ever appears. It matters mostly in the restrictive modes
        # (default/plan/acceptEdits): in ``bypassPermissions`` the CLI calls
        # this callback only for its own safety checks. See MCP plan D9.
```

Replace the block that builds the request and awaits it:

```python
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
```

with:

```python
        request = PendingRequest(
            request_id=request_id,
            request_type=request_type,
            tool_name=tool_name,
            tool_input=input_data,
            created_at=time.time(),
            permission_suggestions=permission_suggestions,
        )

        # Auto-deny (design docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md):
        # in bypassPermissions the CLI still prompts for some dangerous actions
        # and nobody is expected to watch, so an unanswered prompt is denied
        # after AUTO_DENY_DELAY_SECONDS, like the Claude Code CLI does. Only
        # the action tools are armed: questions, plan reviews and any other tool
        # keep waiting. The mode is the one TwiCC last applied, kept exact by
        # ``_mirror_session_set_mode``; an untrusted project never arms.
        auto_deny_response = None
        if (
            request_type == "tool_approval"
            and tool_name in AUTO_DENY_TOOLS
            and not untrusted
            and self.agent_settings.permission_mode == "bypassPermissions"
        ):
            auto_deny_response = PermissionResultDeny(message=AUTO_DENY_MESSAGE, interrupt=False)

        try:
            response = await self._await_pending_request(request, auto_deny_response=auto_deny_response)
```

At the end of the method, replace the final `return response` (after the `ExitPlanMode` block) with:

```python
        if isinstance(response, PermissionResultAllow):
            self._mirror_session_set_mode(response)

        return response
```

Add the helper right after `_handle_pending_request` (before `_handle_elicitation_request`):

```python
    def _mirror_session_set_mode(self, response: PermissionResultAllow) -> None:
        """Keep ``agent_settings.permission_mode`` equal to the mode the CLI applies.

        An approval answer may carry a ``setMode`` permission ("allow, and
        switch to default"). ``ws.py`` persists it and the CLI applies it
        through ``updated_permissions``, but nothing else updates this
        in-memory copy before the next send or USER_TURN — and the auto-deny
        arming reads it. Same rule as ``HybridClaudeAgent.resolve_pending_request``:
        only a session-destination (or destination-less) ``setMode`` counts.
        The settings monitor then sees no difference and does not re-apply it.
        """
        for permission in response.updated_permissions or ():
            data = permission.to_dict() if isinstance(permission, PermissionUpdate) else permission
            if (
                data.get("type") == "setMode"
                and data.get("mode")
                and data.get("destination") in (None, "session")
            ):
                self.agent_settings = self.agent_settings._replace(permission_mode=data["mode"])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_claude_auto_deny.py -q`
Expected: all PASS.

- [ ] **Step 5: Run the neighbouring Claude suites**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_claude_ws_approval_permissions.py tests/test_claude_ws_question_responses.py tests/test_claude_ephemeral_scenarios.py tests/test_claude_attachment_delivery.py tests/test_auto_deny.py -q`
Expected: all PASS.

- [ ] **Step 6: Lint**

Run: `cd /home/twidi/dev/twicc-poc && uvx ruff check tests/test_claude_auto_deny.py`
Expected: clean.

Run: `cd /home/twidi/dev/twicc-poc && f=src/twicc/providers/claude_code/agent/agent.py; echo "before=$(git show HEAD:$f | uvx ruff check --stdin-filename $f -q - | wc -l) after=$(uvx ruff check $f -q | wc -l)"`
Expected: `after` ≤ `before`.

- [ ] **Step 7: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/providers/claude_code/agent/agent.py tests/test_claude_auto_deny.py && git commit -F - <<'EOF'
feat(claude-code): auto-deny unanswered bypass approvals of action tools

In bypassPermissions the CLI still asks approval for some dangerous
actions. A prompt for a shell or file-write tool (Bash, PowerShell, Monitor,
Write, Edit, MultiEdit, NotebookEdit) is now denied after 120 s without an
answer, with a message telling the agent to find another way. Questions,
plan reviews, other tools, other modes and untrusted projects keep waiting.

An approval answer that switches the mode now updates the SDK agent's
in-memory mode at once, like the hybrid agent already does, so the next
approvals of the same turn follow the new mode.

Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>
EOF
```

---

### Task 3: CLI/MCP output, MCP event, agent docs

**Files:**
- Modify: `src/twicc/providers/pending_question.py` (`out_of_scope_entry`)
- Modify: `src/twicc/mcp/events/delivery.py` (`build_data`)
- Modify: `src/twicc/mcp/events/catalog.py` (`session.concluded` `payloadSchema`)
- Modify: `tests/test_pending_question.py`, `tests/test_mcp_events_payload.py`, `tests/test_mcp_events_catalog.py`
- Modify: `src/twicc/agent/plugin/twicc/skills/twicc-session/questions.md`, `src/twicc/agent/plugin/twicc/skills/twicc-orchestration/control-cookbook.md`, `SKILLS-AND-CLI.md`, `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`

**Interfaces:**
- Consumes: `PendingRequest.auto_deny_at`, `auto_deny_remaining` (Task 1).
- Produces: `out_of_scope` entries with `auto_deny_at` / `auto_deny_in_seconds`; `session.concluded` `data.auto_deny_at`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_pending_question.py`, in the class that holds `test_an_out_of_scope_entry_carries_no_age`, add after that test:

```python
    def test_an_auto_deniable_entry_says_when_it_clears(self):
        pending = claude_question(request_type="tool_approval", tool_name="Bash",
                                  tool_input={"command": "rm -rf /tmp/x"},
                                  auto_deny_at=time.time() + 60)
        entry = claude_pq.normalize_pending_request(pending)
        assert entry["kind"] == "out_of_scope"
        assert entry["auto_deny_at"].endswith("+00:00")
        assert 59 <= entry["auto_deny_in_seconds"] <= 60
        assert entry["actions"] == []

    def test_a_passed_deadline_reads_zero_not_negative(self):
        pending = claude_question(request_type="tool_approval", tool_name="Bash",
                                  tool_input={"command": "rm -rf /tmp/x"},
                                  auto_deny_at=time.time() - 3)
        entry = claude_pq.normalize_pending_request(pending)
        assert entry["auto_deny_in_seconds"] == 0.0
```

In `tests/test_mcp_events_payload.py`, give the stand-in request the new field (line 14):

```python
REQUEST = SimpleNamespace(request_id="request-1", request_type="tool_approval",
                          created_at=TICK.timestamp() - 60, auto_deny_at=None)
```

In `test_exact_data_schema_and_text_presence`, after `assert ("request_type" in data) == (outcome == "awaiting_user_input")`, add:

```python
    assert "auto_deny_at" not in data
```

and add a new test after it:

```python
def test_an_auto_deniable_request_carries_its_deadline():
    armed = SimpleNamespace(**{**vars(REQUEST), "auto_deny_at": TICK.timestamp() + 60})
    reply = {"outcome": "awaiting_user_input", "line_num": None, "is_final": None,
             "since_line_num": 3, "waited_seconds": 1.0}
    event = build_occurrence("sub_a", "session-a", None, reply, numbering=2, last_line=8,
                             tick_started_at=TICK, pending_request=armed)
    validate(event["data"], PAYLOAD_SCHEMA)
    assert event["data"]["auto_deny_at"] == (TICK + timedelta(seconds=60)).isoformat()
```

In `tests/test_mcp_events_catalog.py`, `test_complete_catalog_matches_authoritative_spec_json`: the historical spec stays as written, so the test adds the new key explicitly. Replace:

```python
    expected = orjson.loads(listing)["events"][0]
    expected["payloadSchema"] = orjson.loads(payload)
```

with:

```python
    expected = orjson.loads(listing)["events"][0]
    expected["payloadSchema"] = orjson.loads(payload)
    # Added after that spec by docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md
    # (§6.3); the historical spec is never edited, so the addition is declared here.
    expected["payloadSchema"]["properties"]["auto_deny_at"] = AUTO_DENY_AT_SCHEMA
```

and add, near the top of the file after the imports:

```python
AUTO_DENY_AT_SCHEMA = {
    "type": "string",
    "description": "ISO 8601 deadline; present only for an auto-deniable awaiting_user_input.",
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_pending_question.py tests/test_mcp_events_payload.py tests/test_mcp_events_catalog.py -q`
Expected: FAIL — `KeyError: 'auto_deny_at'` in `test_an_auto_deniable_entry_says_when_it_clears` and in the new event test, `KeyError: 'auto_deny_in_seconds'` in `test_a_passed_deadline_reads_zero_not_negative`; the catalog test fails on the missing `auto_deny_at` property.

- [ ] **Step 3: Implement `out_of_scope_entry`** (`src/twicc/providers/pending_question.py`)

Add `import time` to the stdlib imports:

```python
from datetime import datetime, UTC
import time
```

Replace the function:

```python
def out_of_scope_entry(pending, *, raw: bool = False) -> dict:
    """The minimal entry for something waiting that this command cannot answer.

    It exists to say *"something is waiting and it is not mine"*, so it stops at
    what that sentence needs. No ``age_seconds``, no questions, no payload.
    """
    return _with_raw({
        "request_id": pending.request_id,
        "created_at": created_at_iso(pending.created_at),
        "kind": "out_of_scope",
        "reason": out_of_scope_reason(pending),
        "tool_name": pending.tool_name,
        "actions": [],
    }, pending, raw=raw)
```

with:

```python
def out_of_scope_entry(pending, *, raw: bool = False) -> dict:
    """The minimal entry for something waiting that this command cannot answer.

    It exists to say *"something is waiting and it is not mine"*, so it stops at
    what that sentence needs. No ``age_seconds``, no questions, no payload — but
    an auto-deniable request (``twicc.agent.auto_deny``) also says when it
    clears itself, so a waiting agent does not ask a human for nothing.
    """
    entry = {
        "request_id": pending.request_id,
        "created_at": created_at_iso(pending.created_at),
        "kind": "out_of_scope",
        "reason": out_of_scope_reason(pending),
        "tool_name": pending.tool_name,
    }
    if pending.auto_deny_at is not None:
        # Imported lazily: a module-level import would load the whole
        # ``twicc.agent`` package (its ``__init__``) with this light module.
        from twicc.agent.auto_deny import auto_deny_remaining

        entry["auto_deny_at"] = created_at_iso(pending.auto_deny_at)
        entry["auto_deny_in_seconds"] = auto_deny_remaining(pending, time.time())
    entry["actions"] = []
    return _with_raw(entry, pending, raw=raw)
```

- [ ] **Step 4: Implement `build_data`** (`src/twicc/mcp/events/delivery.py`)

Replace:

```python
    if reply["outcome"] == "awaiting_user_input":
        data["request_type"] = pending_request.request_type
    return data
```

with:

```python
    if reply["outcome"] == "awaiting_user_input":
        data["request_type"] = pending_request.request_type
        # An auto-deniable request clears itself at this instant
        # (docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md §6.3).
        if pending_request.auto_deny_at is not None:
            data["auto_deny_at"] = created_at_iso(pending_request.auto_deny_at)
    return data
```

- [ ] **Step 5: Declare the key in the schema** (`src/twicc/mcp/events/catalog.py`)

In `payloadSchema["properties"]`, right after the `"request_type"` property, add:

```python
            "auto_deny_at": {
                "type": "string",
                "description": "ISO 8601 deadline; present only for an auto-deniable awaiting_user_input.",
            },
```

The description must be the exact string of `AUTO_DENY_AT_SCHEMA` in the test. It is the spec §6.3 text, without the Markdown backticks (the sibling descriptions in this schema use none).

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest tests/test_pending_question.py tests/test_pending_question_services.py tests/test_mcp_events_payload.py tests/test_mcp_events_catalog.py tests/test_mcp_events_delivery.py tests/test_mcp_events_acceptance.py -q`
Expected: all PASS.

- [ ] **Step 7: Update the agent docs**

`src/twicc/agent/plugin/twicc/skills/twicc-session/questions.md` — replace the `kind` bullet:

```markdown
- `kind` — `question`, or `out_of_scope`: something is waiting and it is not yours. An out-of-scope entry has only `request_id`, `created_at`, `kind`, `reason` and `tool_name`, and an empty `actions`. `reason` is `tool_approval`, `mcp_tool_approval`, `elicitation`, `terminal_only` or `choice`.
```

with:

```markdown
- `kind` — `question`, or `out_of_scope`: something is waiting and it is not yours. An out-of-scope entry has `request_id`, `created_at`, `kind`, `reason`, `tool_name` and an empty `actions` — plus, when TwiCC denies it by itself, the two keys below. `reason` is `tool_approval`, `mcp_tool_approval`, `elicitation`, `terminal_only` or `choice`.
- `auto_deny_at`, `auto_deny_in_seconds` — only on an out-of-scope tool approval that TwiCC denies by itself (a dangerous action in Claude `bypassPermissions`): the instant (ISO 8601) and the seconds left. Do not ask a human. Do not call `wait-reply` in a loop meanwhile: it returns `awaiting_user_input` at once while the request is pending. Let `auto_deny_in_seconds` pass, then wait again.
```

`src/twicc/agent/plugin/twicc/skills/twicc-orchestration/control-cookbook.md` — replace:

```markdown
answer that session and wait again, or wait again naming only the other ids —
the same call returns the blocked session at once. The default cursor returns
```

with:

```markdown
answer that session and wait again, or wait again naming only the other ids —
the same call returns the blocked session at once. A blocked session whose
`pending-requests` entry has `auto_deny_at` clears itself then: wait on the
other ids meanwhile, and include it again once `auto_deny_in_seconds` has
passed. The default cursor returns
```

`SKILLS-AND-CLI.md` (repository root, the `pending-requests` entry) — replace:

```markdown
`kind: "out_of_scope"` carries a `reason` (`tool_approval`, `mcp_tool_approval`, `elicitation`, `terminal_only`, `choice`) and an empty `actions`.
```

with:

```markdown
`kind: "out_of_scope"` carries a `reason` (`tool_approval`, `mcp_tool_approval`, `elicitation`, `terminal_only`, `choice`) and an empty `actions`, plus `auto_deny_at` and `auto_deny_in_seconds` when TwiCC denies the request by itself (a dangerous action in Claude `bypassPermissions`).
```

`src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` — bump the patch number of `"version"` (`0.108.1` → `0.108.2` if unchanged since this plan was written).

- [ ] **Step 8: Lint**

Run: `cd /home/twidi/dev/twicc-poc && for f in src/twicc/providers/pending_question.py src/twicc/mcp/events/delivery.py src/twicc/mcp/events/catalog.py tests/test_pending_question.py tests/test_mcp_events_payload.py tests/test_mcp_events_catalog.py; do echo "$f before=$(git show HEAD:$f | uvx ruff check --stdin-filename $f -q - | wc -l) after=$(uvx ruff check $f -q | wc -l)"; done`
Expected: `after` ≤ `before` for every file.

- [ ] **Step 9: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add src/twicc/providers/pending_question.py src/twicc/mcp/events/delivery.py src/twicc/mcp/events/catalog.py tests/test_pending_question.py tests/test_mcp_events_payload.py tests/test_mcp_events_catalog.py src/twicc/agent/plugin/twicc/skills/twicc-session/questions.md src/twicc/agent/plugin/twicc/skills/twicc-orchestration/control-cookbook.md SKILLS-AND-CLI.md src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json && git commit -F - <<'EOF'
feat(cli): expose the auto-deny deadline of a pending approval

session pending-requests (CLI and MCP) now gives an auto-deniable tool
approval its auto_deny_at and auto_deny_in_seconds, and the
session.concluded event carries auto_deny_at. An orchestrating agent knows
that the request clears itself and waits instead of asking a human; the
skills tell it not to poll wait-reply meanwhile, since it returns at once.

The catalog test keeps reading the historical MCP Events spec and declares
the added schema key itself.

Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>
EOF
```

---

### Task 4: Frontend — local deadline in the store, stable draft hash

**Files:**
- Create: `frontend/src/utils/autoDeny.js`
- Create: `frontend/src/utils/autoDeny.test.js`
- Modify: `frontend/src/stores/data.js` (import; `setProcessState`; `setActiveProcesses`)
- Modify: `frontend/src/utils/pendingRequestDraftStorage.js` (`hashPendingRequest`)
- Modify: `frontend/src/stores/processSnapshots.test.js`, `frontend/src/stores/nestedAgentState.test.js` (they extract `data.js` action sources and inject every free identifier by hand)
- Create: `frontend/src/utils/pendingRequestDraftHash.test.js`

**Interfaces:**
- Consumes: wire key `auto_deny_in_seconds` (Task 1).
- Produces (`frontend/src/utils/autoDeny.js`):
  - `AUTO_DENY_DELAY_SECONDS = 120`, `AUTO_DENY_LOCK_MARGIN_MS = 1000`, `AUTO_DENY_KEEP_TOLERANCE_MS = 500`
  - `withAutoDenyDeadlines(pendingRequests: Array, receivedAtMs: number, previousRequests?: Array) -> Array` — entries with the wire key become copies without it and with `autoDenyDeadlineMs: number`.
  - `isAutoDenyLocked(deadlineMs: number, nowMs: number) -> boolean`
  - `formatAutoDenyRemaining(deadlineMs: number, nowMs: number) -> string` (`m:ss`)
  - `withoutAutoDenyKeys(pendingRequest: Object) -> Object`
- Store: `processStates[sid].pending_requests[i].autoDenyDeadlineMs` when armed; the wire key never reaches the store.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/utils/autoDeny.test.js`:

```js
import assert from 'node:assert/strict'
import test from 'node:test'

import {
    AUTO_DENY_DELAY_SECONDS,
    formatAutoDenyRemaining,
    isAutoDenyLocked,
    withAutoDenyDeadlines,
    withoutAutoDenyKeys,
} from './autoDeny.js'

const armed = (id, seconds) => ({ request_id: id, tool_name: 'Bash', auto_deny_in_seconds: seconds })

test('the delay mirrors the backend constant', () => {
    assert.equal(AUTO_DENY_DELAY_SECONDS, 120)
})

test('an armed entry gets a local deadline and loses the wire key', () => {
    const [entry] = withAutoDenyDeadlines([armed('r-1', 42.5)], 10_000)
    assert.equal(entry.autoDenyDeadlineMs, 52_500)
    assert.equal(Object.hasOwn(entry, 'auto_deny_in_seconds'), false)
    assert.equal(entry.tool_name, 'Bash')
})

test('an unarmed entry is returned unchanged', () => {
    const plain = { request_id: 'r-1', tool_name: 'AskUserQuestion' }
    const [entry] = withAutoDenyDeadlines([plain], 10_000)
    assert.equal(entry, plain)
})

test('a close re-stamp keeps the previous deadline', () => {
    const [first] = withAutoDenyDeadlines([armed('r-1', 60)], 10_000)
    const [second] = withAutoDenyDeadlines([armed('r-1', 55)], 15_300, [first])
    assert.equal(second.autoDenyDeadlineMs, first.autoDenyDeadlineMs)
    assert.deepEqual(second, first)
})

test('a far re-stamp takes the new deadline', () => {
    const [first] = withAutoDenyDeadlines([armed('r-1', 60)], 10_000)
    const [second] = withAutoDenyDeadlines([armed('r-1', 60)], 12_000, [first])
    assert.equal(second.autoDenyDeadlineMs, 72_000)
})

test('the previous deadline is matched by request id', () => {
    const [first] = withAutoDenyDeadlines([armed('r-1', 60)], 10_000)
    const [other] = withAutoDenyDeadlines([armed('r-2', 30)], 10_100, [first])
    assert.equal(other.autoDenyDeadlineMs, 40_100)
})

test('the lock starts when less than one second is left', () => {
    assert.equal(isAutoDenyLocked(10_000, 8_999), false)
    assert.equal(isAutoDenyLocked(10_000, 9_001), true)
    assert.equal(isAutoDenyLocked(10_000, 12_000), true)
})

test('the remaining time reads m:ss, rounded down, floored at 0:00', () => {
    assert.equal(formatAutoDenyRemaining(130_000, 10_000), '2:00')
    assert.equal(formatAutoDenyRemaining(112_400, 10_000), '1:42')
    assert.equal(formatAutoDenyRemaining(19_999, 10_000), '0:09')
    assert.equal(formatAutoDenyRemaining(10_900, 10_000), '0:00')
    assert.equal(formatAutoDenyRemaining(5_000, 10_000), '0:00')
})

test('a reconnect after the deadline is locked at once', () => {
    const [entry] = withAutoDenyDeadlines([armed('r-1', 0)], 10_000)
    assert.equal(isAutoDenyLocked(entry.autoDenyDeadlineMs, 10_000), true)
    assert.equal(formatAutoDenyRemaining(entry.autoDenyDeadlineMs, 10_000), '0:00')
})

test('the draft hash input drops both deadline keys', () => {
    const base = { request_id: 'r-1', tool_name: 'Bash', created_at: 1 }
    assert.deepEqual(withoutAutoDenyKeys({ ...base, autoDenyDeadlineMs: 5 }), base)
    assert.deepEqual(withoutAutoDenyKeys({ ...base, auto_deny_in_seconds: 5 }), base)
    assert.equal(withoutAutoDenyKeys(base), base)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/autoDeny.test.js`
Expected: FAIL — `Cannot find module '.../autoDeny.js'`.

- [ ] **Step 3: Create `frontend/src/utils/autoDeny.js`**

```js
// frontend/src/utils/autoDeny.js
// Countdown of an auto-deniable pending request (design
// docs/plans/2026-10-08-bypass-approval-auto-deny-spec.md §5).
//
// The backend sends `auto_deny_in_seconds`, the time left when it serialized
// the payload, not a deadline: the browser may run on another machine whose
// clock differs. The store turns it into a local deadline at reception and
// drops the wire key, so an unchanged request keeps comparing equal.

// Mirror of AUTO_DENY_DELAY_SECONDS in src/twicc/agent/auto_deny.py (the
// source of truth). Used only in the tooltip text.
export const AUTO_DENY_DELAY_SECONDS = 120

// The form refuses answers when less than this is left (the display then
// reads 0:00): the local deadline lags the backend one by the delivery delay,
// and the answer needs its own trip back.
export const AUTO_DENY_LOCK_MARGIN_MS = 1000

// A new stamp closer than this to the previous one keeps the previous one, so
// `jsonValuesEqual` sees no change and the store does not replace the whole
// `pending_requests` array on every process_state. Kept below the lock margin.
export const AUTO_DENY_KEEP_TOLERANCE_MS = 500

const WIRE_KEY = 'auto_deny_in_seconds'
const DEADLINE_KEY = 'autoDenyDeadlineMs'

/**
 * Turn the wire's relative auto-deny time into a local deadline.
 *
 * @param {Array<Object>} pendingRequests - Wire pending requests
 * @param {number} receivedAtMs - Local reception instant (Date.now())
 * @param {Array<Object>} [previousRequests] - The session's current store entries
 * @returns {Array<Object>} Entries with the wire key replaced by `autoDenyDeadlineMs`
 */
export function withAutoDenyDeadlines(pendingRequests, receivedAtMs, previousRequests = []) {
    const previousDeadlines = new Map()
    for (const request of previousRequests || []) {
        if (request?.[DEADLINE_KEY] != null) previousDeadlines.set(request.request_id, request[DEADLINE_KEY])
    }
    return pendingRequests.map(request => {
        if (!Object.hasOwn(request, WIRE_KEY)) return request
        const { [WIRE_KEY]: remainingSeconds, ...rest } = request
        let deadlineMs = receivedAtMs + remainingSeconds * 1000
        const previous = previousDeadlines.get(request.request_id)
        if (previous !== undefined && Math.abs(previous - deadlineMs) < AUTO_DENY_KEEP_TOLERANCE_MS) {
            deadlineMs = previous
        }
        return { ...rest, [DEADLINE_KEY]: deadlineMs }
    })
}

/**
 * True when the form must refuse answers: less than the lock margin is left.
 * @param {number} deadlineMs
 * @param {number} nowMs
 * @returns {boolean}
 */
export function isAutoDenyLocked(deadlineMs, nowMs) {
    return deadlineMs - nowMs < AUTO_DENY_LOCK_MARGIN_MS
}

/**
 * Remaining time as `m:ss`, rounded down, floored at `0:00` — so `0:00` is
 * shown exactly while `isAutoDenyLocked` is true.
 * @param {number} deadlineMs
 * @param {number} nowMs
 * @returns {string}
 */
export function formatAutoDenyRemaining(deadlineMs, nowMs) {
    const totalSeconds = Math.max(0, Math.floor((deadlineMs - nowMs) / 1000))
    const minutes = Math.floor(totalSeconds / 60)
    const seconds = totalSeconds % 60
    return `${minutes}:${String(seconds).padStart(2, '0')}`
}

/**
 * The request without its deadline keys, for a hash that must not change
 * between two page loads (the local deadline differs on each load).
 * @param {Object} pendingRequest
 * @returns {Object}
 */
export function withoutAutoDenyKeys(pendingRequest) {
    if (!pendingRequest || (!Object.hasOwn(pendingRequest, WIRE_KEY) && !Object.hasOwn(pendingRequest, DEADLINE_KEY))) {
        return pendingRequest
    }
    const { [WIRE_KEY]: _wire, [DEADLINE_KEY]: _deadline, ...rest } = pendingRequest
    return rest
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/autoDeny.test.js`
Expected: all PASS.

- [ ] **Step 5: Stamp the deadlines in the store** (`frontend/src/stores/data.js`)

Add the import next to the other `../utils/` imports (after the `pendingRequestDraftStorage` import):

```js
import { liveDraftKey, sweepPendingRequestDrafts } from '../utils/pendingRequestDraftStorage'
import { withAutoDenyDeadlines } from '../utils/autoDeny'
```

In `setProcessState`, inside the `this._patchProcessState(sessionId, { ... })` call, replace:

```js
                    pending_requests: extra.pending_requests || [],
```

with:

```js
                    pending_requests: withAutoDenyDeadlines(
                        extra.pending_requests || [],
                        Date.now(),
                        this.processStates[sessionId]?.pending_requests,
                    ),
```

In `setActiveProcesses`, inside the `this._patchProcessState(p.session_id, { ... })` call of the `for (const p of processes)` loop, replace:

```js
                        pending_requests: p.pending_requests || [],
```

with:

```js
                        pending_requests: withAutoDenyDeadlines(
                            p.pending_requests || [],
                            snapshotAt,
                            this.processStates[p.session_id]?.pending_requests,
                        ),
```

(The draft sweep below that loop reads `p.pending_requests` from the wire snapshot; it only uses `request_id`, so it stays as it is.)

- [ ] **Step 5b: Inject the new identifier in the two store tests**

`processSnapshots.test.js` and `nestedAgentState.test.js` run the `data.js` action sources through `new Function(...Object.keys(deps), …)`: every free identifier comes from their `deps` object. Without this step, `npm test` fails with `ReferenceError: withAutoDenyDeadlines is not defined`.

`frontend/src/stores/processSnapshots.test.js` — add the import after `import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'`:

```js
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import { withAutoDenyDeadlines } from '../utils/autoDeny.js'
```

and replace:

```js
const deps = { PROCESS_STATE, backgroundWorkStatusKey, jsonValuesEqual,
```

with:

```js
const deps = { PROCESS_STATE, backgroundWorkStatusKey, jsonValuesEqual, withAutoDenyDeadlines,
```

`frontend/src/stores/nestedAgentState.test.js` — add the import after `import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'`:

```js
import { jsonValuesEqual } from '../utils/jsonValuesEqual.js'
import { withAutoDenyDeadlines } from '../utils/autoDeny.js'
```

and replace:

```js
        sweepPendingRequestDrafts: async () => {}, liveDraftKey: (a, b) => `${a}:${b}`, getToolHelpers: () => null,
```

with:

```js
        sweepPendingRequestDrafts: async () => {}, liveDraftKey: (a, b) => `${a}:${b}`, getToolHelpers: () => null,
        withAutoDenyDeadlines,
```

- [ ] **Step 6: Make the draft hash ignore the deadline** (`frontend/src/utils/pendingRequestDraftStorage.js`)

Add the import after the existing ones:

```js
import { getDb, PENDING_REQUEST_DRAFTS_STORE } from './draftStorage'
import { hashString } from './hash'
import { withoutAutoDenyKeys } from './autoDeny'
```

Replace the function and its doc comment:

```js
/**
 * Hash of a pending request as the client received it, used to confirm on
 * restore that the stored draft belongs to this exact request.
 *
 * The whole wire object is hashed as-is: `request_id` and `created_at` are
 * fixed for the lifetime of a request, so including them costs nothing and
 * keeps the call trivial.
 *
 * @param {Object} pendingRequest - The wire pending request object
 * @returns {string} base36 hash
 */
export function hashPendingRequest(pendingRequest) {
    return hashString(JSON.stringify(pendingRequest))
}
```

with:

```js
/**
 * Hash of a pending request as the client received it, used to confirm on
 * restore that the stored draft belongs to this exact request.
 *
 * The whole object is hashed except the auto-deny deadline keys: the local
 * deadline is computed at reception, so it differs between two page loads
 * and would make every stored draft unrestorable. `request_id` and
 * `created_at` are fixed for the lifetime of a request, so including them
 * costs nothing.
 *
 * @param {Object} pendingRequest - The pending request object from the store
 * @returns {string} base36 hash
 */
export function hashPendingRequest(pendingRequest) {
    return hashString(JSON.stringify(withoutAutoDenyKeys(pendingRequest)))
}
```

- [ ] **Step 6b: Test the hash wiring**

`pendingRequestDraftStorage.js` cannot load under plain `node --test`: its extensionless `./draftStorage` import (resolved by Vite) gives `ERR_MODULE_NOT_FOUND`. The test therefore extracts the function source, like the store tests do, and checks that the hash really goes through `withoutAutoDenyKeys`.

Create `frontend/src/utils/pendingRequestDraftHash.test.js`:

```js
// `hashPendingRequest` must ignore the auto-deny deadline keys: the local
// deadline differs between two page loads. The module cannot load under plain
// node (extensionless Vite imports), so the function source is extracted and
// run with its two dependencies injected.

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import { withoutAutoDenyKeys } from './autoDeny.js'
import { hashString } from './hash.js'

const source = readFileSync(new URL('./pendingRequestDraftStorage.js', import.meta.url), 'utf8')
const start = source.indexOf('export function hashPendingRequest(')
const end = source.indexOf('\n}\n', start) + 2
const hashPendingRequest = new Function(
    'hashString', 'withoutAutoDenyKeys',
    `${source.slice(start, end).replace('export ', '')}\nreturn hashPendingRequest`,
)(hashString, withoutAutoDenyKeys)

test('the draft hash ignores the auto-deny deadline keys', () => {
    const base = { request_id: 'r-1', tool_name: 'Bash', created_at: 1, tool_input: { command: 'ls' } }
    const hash = hashPendingRequest(base)
    assert.equal(hashPendingRequest({ ...base, autoDenyDeadlineMs: 123 }), hash)
    assert.equal(hashPendingRequest({ ...base, autoDenyDeadlineMs: 456 }), hash)
    assert.equal(hashPendingRequest({ ...base, auto_deny_in_seconds: 7 }), hash)
})

test('the draft hash still sees a real change', () => {
    const base = { request_id: 'r-1', tool_name: 'Bash', created_at: 1, tool_input: { command: 'ls' } }
    assert.notEqual(hashPendingRequest({ ...base, tool_input: { command: 'pwd' } }), hashPendingRequest(base))
})
```

(`./hash.js` has no import and loads under plain node: checked on 2026-10-08.)

Run: `cd /home/twidi/dev/twicc-poc/frontend && node --test src/utils/pendingRequestDraftHash.test.js`
Expected: PASS (after Step 6). Before Step 6, the first test fails: the hash changes with the deadline.

- [ ] **Step 7: Run the whole frontend suite**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: all PASS, including `src/stores/processSnapshots.test.js` and `src/stores/nestedAgentState.test.js`.

- [ ] **Step 8: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/utils/autoDeny.js frontend/src/utils/autoDeny.test.js frontend/src/utils/pendingRequestDraftHash.test.js frontend/src/stores/data.js frontend/src/stores/processSnapshots.test.js frontend/src/stores/nestedAgentState.test.js frontend/src/utils/pendingRequestDraftStorage.js && git commit -F - <<'EOF'
feat(frontend): keep a local auto-deny deadline per pending request

The store turns the backend's auto_deny_in_seconds into a local deadline at
reception and drops the wire key. A re-stamp within 0.5 s keeps the previous
deadline, so an unchanged request still compares equal and the store does
not replace the pending_requests array on every process_state.

The pending-request draft hash ignores the deadline keys: the local deadline
differs on each page load and would otherwise block every draft restore.

Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>
EOF
```

---

### Task 5: Frontend — countdown and last-second lock in `PendingRequestForm.vue`

**Files:**
- Modify: `frontend/src/components/message/PendingRequestForm.vue`

**Interfaces:**
- Consumes: `pendingRequest.autoDenyDeadlineMs` (Task 4); `AUTO_DENY_DELAY_SECONDS`, `formatAutoDenyRemaining`, `isAutoDenyLocked` from `frontend/src/utils/autoDeny.js` (Task 4).
- Produces: nothing for other tasks. The bodies keep their props; the shell passes `is-responding` true while locked.

This component has no unit test harness (the repo tests only pure modules with node:test). The logic it uses is tested in Task 4; this task is verified by the build and by the manual validation of Task 6.

- [ ] **Step 1: Script — imports**

Replace:

```js
import { ref, computed, watch, useId, nextTick } from 'vue'
```

with:

```js
import { ref, computed, watch, useId, nextTick, onBeforeUnmount } from 'vue'
```

and add after `import { useFooterBlockMotion } from '../../composables/useFooterMotion.js'`:

```js
import { AUTO_DENY_DELAY_SECONDS, formatAutoDenyRemaining, isAutoDenyLocked } from '../../utils/autoDeny'
```

- [ ] **Step 2: Script — countdown state**

Add right after the `const headerTitle = computed(...)` block:

```js
// ── Auto-deny countdown (bypassPermissions) ──────────────────────────────────
// The store gives an armed request a local `autoDenyDeadlineMs` (utils/autoDeny.js).
// A one-second tick drives the label; it runs only while the shown request has
// a deadline. The lock (less than 1 s left, label "Auto-denying…") reuses the
// `is-responding` guard of the bodies; onBodySubmit re-checks with Date.now(),
// never the tick, so the margin holds exactly. Accepted side effects of that
// reuse (spec §5.2): the draft is discarded, the buttons show their spinner.
const autoDenyDeadlineMs = computed(() => props.pendingRequest.autoDenyDeadlineMs ?? null)
const autoDenyNowMs = ref(Date.now())
const autoDenyBadgeId = useId()
let autoDenyTicker = null

function stopAutoDenyTicker() {
    if (autoDenyTicker !== null) {
        clearInterval(autoDenyTicker)
        autoDenyTicker = null
    }
}

watch(autoDenyDeadlineMs, (deadline) => {
    stopAutoDenyTicker()
    if (deadline === null) return
    autoDenyNowMs.value = Date.now()
    autoDenyTicker = setInterval(() => { autoDenyNowMs.value = Date.now() }, 1000)
}, { immediate: true })

onBeforeUnmount(stopAutoDenyTicker)

const autoDenyLocked = computed(() => autoDenyDeadlineMs.value !== null
    && isAutoDenyLocked(autoDenyDeadlineMs.value, autoDenyNowMs.value))

const autoDenyLabel = computed(() => {
    if (autoDenyDeadlineMs.value === null) return null
    if (autoDenyLocked.value) return 'Auto-denying…'
    return `Auto-deny in ${formatAutoDenyRemaining(autoDenyDeadlineMs.value, autoDenyNowMs.value)}`
})

const autoDenyTooltip = `Bypass permissions mode: this request is denied automatically when nobody answers within ${AUTO_DENY_DELAY_SECONDS / 60} minutes.`
```

- [ ] **Step 3: Script — refuse a late answer**

Replace:

```js
function onBodySubmit(payload) {
    if (isResponding.value) return
    isResponding.value = true
```

with:

```js
function onBodySubmit(payload) {
    if (isResponding.value) return
    // The backend denies the request in the next instant: an answer sent now
    // would be discarded (and a late setMode persisted for nothing).
    if (autoDenyDeadlineMs.value !== null && isAutoDenyLocked(autoDenyDeadlineMs.value, Date.now())) return
    isResponding.value = true
```

- [ ] **Step 4: Template — badge in the minimized bar**

In the `<CollapsedBar>` `#trailing` template, insert before the existing `pending-count-badge` span:

```html
            <template #trailing>
                <span
                    v-if="autoDenyLabel"
                    class="auto-deny-badge"
                    :id="autoDenyBadgeId"
                    role="timer"
                >{{ autoDenyLabel }}</span>
                <AppTooltip v-if="autoDenyLabel" :for="autoDenyBadgeId">{{ autoDenyTooltip }}</AppTooltip>
                <span
                    v-if="extraPendingCount > 0"
```

- [ ] **Step 5: Template — badge in the normal header**

In `<div v-else class="pending-request-header">`, insert right after `<span class="pending-request-title">{{ headerTitle }}</span>`:

```html
            <span class="pending-request-title">{{ headerTitle }}</span>
            <span
                v-if="autoDenyLabel"
                class="auto-deny-badge"
                :id="autoDenyBadgeId"
                role="timer"
            >{{ autoDenyLabel }}</span>
            <AppTooltip v-if="autoDenyLabel" :for="autoDenyBadgeId">{{ autoDenyTooltip }}</AppTooltip>
```

(The minimized bar and the normal header are `v-if` / `v-else`, so the shared id is never rendered twice.)

- [ ] **Step 6: Template — lock the bodies**

In the `<component :is="bodyComponent" …>` element, replace:

```html
            :is-responding="isResponding"
```

with:

```html
            :is-responding="isResponding || autoDenyLocked"
```

- [ ] **Step 7: Style**

In the `<style scoped>` block, add after the `.pending-count-badge` rule:

```css
.auto-deny-badge {
    display: inline-flex;
    align-items: center;
    background: var(--wa-color-warning-fill-quiet);
    color: var(--wa-color-warning-on-quiet);
    font-size: var(--wa-font-size-xs);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    padding: 2px var(--wa-space-xs);
    border-radius: var(--wa-border-radius-pill);
    line-height: 1;
    white-space: nowrap;
}
```

- [ ] **Step 8: Verify the build and the suite**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test && npx vite build --outDir /tmp/twicc-auto-deny-build --emptyOutDir`
Expected: tests PASS; the build ends without error (the output goes to `/tmp`, so the repository's `src/twicc/static/frontend` is not touched).

- [ ] **Step 9: Commit**

```bash
cd /home/twidi/dev/twicc-poc && git add frontend/src/components/message/PendingRequestForm.vue && git commit -F - <<'EOF'
feat(frontend): show the auto-deny countdown on bypass approvals

An armed approval shows "Auto-deny in m:ss" in the form header and in the
minimized bar, with a tooltip explaining the bypass-mode rule. With less
than one second left it reads "Auto-denying…" and refuses answers: the
backend is about to deny the request, so a late click would be discarded.

Co-Authored-By: Claude <exact running model name> <noreply@anthropic.com>
EOF
```

---

### Task 6: Full verification and handoff to the user

**Files:** none modified.

- [ ] **Step 1: Run the whole backend suite**

Run: `cd /home/twidi/dev/twicc-poc && uv run pytest -q 2>&1 | grep -E "^(FAILED|ERROR)" | sort > /tmp/auto-deny-after-failures.txt; comm -13 <(cut -d' ' -f2 /tmp/auto-deny-baseline-failures.txt | sort -u) <(cut -d' ' -f2 /tmp/auto-deny-after-failures.txt | sort -u)`
(Only the test ids are compared: a failure message that varies between runs is not a regression.)
Expected: empty output (no failure that the Task 1 baseline did not have). A listed failure is a regression: fix it in the task that caused it, or report it to the user if its cause is outside this plan.

- [ ] **Step 2: Run the whole frontend suite**

Run: `cd /home/twidi/dev/twicc-poc/frontend && npm test`
Expected: all PASS.

- [ ] **Step 3: Hand off to the user**

Tell the user (do not do it yourself):
- Restart the dev servers with `uv run ./devctl.py restart` from `/home/twidi/dev/twicc-poc` (backend and frontend changed). No migration.
- Run the manual validation of spec §9.3:
  1. A Claude SDK session in `bypassPermissions` triggers a dangerous-command approval (for example a `rm -rf` on a throwaway directory). The form shows `Auto-deny in 1:59` (or `2:00`) and counts down. Check whether a dangerous `Monitor` command prompts too, and gets the countdown.
  2. After 120 seconds, the form disappears; the tool result shows the deny message; the agent continues its turn.
  3. The same with the browser closed during the wait: the agent continues; reopening shows the completed turn.
  4. An answer before the deadline behaves as today; "allow, and switch to `default`" then lets the next prompt of the turn wait without a countdown.
  5. The same prompt in `default` mode shows no countdown and waits.
  6. `session pending-requests` on the waiting session lists `auto_deny_at` and `auto_deny_in_seconds`.
  7. Hybrid: a hybrid session in `bypassPermissions` triggers the same approval; nobody answers; record whether the CLI denies by itself, and confirm the form disappears when it does.
