# Codex Approvals — PR1 — Refactor BaseAgent pending requests

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the in-memory pending-request plumbing (dicts, property, helpers, `resolve_pending_request`) from `ClaudeCodeAgent`/`ClaudeCodeAgentManager` up to `BaseAgent`/`BaseAgentManager`, so future providers (Codex first) can reuse the same machinery. Pure refactor, zero feature change.

**Architecture:** `PendingRequest` and `AgentInfo.pending_requests` are already provider-neutral in `twicc/agent/states.py`. The dicts, the awaiter, the canceller and the routing function move from CC-specific paths to the base classes. Subclasses that need to enrich the snapshot still override `get_info()` and call `super().get_info()._replace(...)`.

**Tech Stack:** Python ≥ 3.13, Django 6, ruff (line-length=120). No new dependencies. Vanilla asyncio.

**Reference spec:** `docs/superpowers/specs/2026-05-14-codex-approvals-design.md` § 4 Étape 1 + §6 mapping table.

**PR1 acceptance criteria (from spec §7-Q13):** Claude sessions continue to work exactly as before (approval flow CC unchanged end-to-end). `BaseAgent` exposes the dicts/property/helpers of pending requests, usable by any provider. No Codex session impacted (bypass still in place).

---

## File Structure

### Files modified

| File | Why |
|------|-----|
| `src/twicc/agent/base_agent.py` | Add `_pending_requests`, `_pending_futures`, `pending_requests` property, `_await_pending_request`, `_cancel_all_pending_futures`, `resolve_pending_request`; inject `pending_requests` in default `get_info()`. |
| `src/twicc/agent/base_manager.py` | Add `resolve_pending_request(session_id, ...)`. Add `pending_requests` skip inside `_state_based_timeout`. |
| `src/twicc/providers/claude_code/agent/agent.py` | Remove the local copies of the two dicts, the property, `resolve_pending_request`, `_cancel_pending_request_future`. Update `_handle_pending_request` to use `super()._await_pending_request(...)`. Update `get_info()` override to stop injecting `pending_requests=...` (base does it now). |
| `src/twicc/providers/claude_code/agent/manager.py` | Remove the `resolve_pending_request` duplicate. Remove the `if agent.pending_requests: return None` skip in `_check_agent_timeout` (now part of `_state_based_timeout`). |

### Files NOT touched

- `src/twicc/agent/states.py` — `PendingRequest`, `AgentInfo`, `serialize_agent_info` already neutral.
- `src/twicc/providers/codex/**` — Codex stays in bypass for PR1; nothing here changes.
- Frontend — no WS protocol change in PR1.

---

## How to run / verify each step

This refactor has no automated tests (project convention: "no tests and no linting" — see CLAUDE.md). Verification is by **smoke testing the Claude approval flow end-to-end** through the worktree's dev servers. The flow that must still work after every task:

1. Open a Claude session in the worktree frontend.
2. Ask Claude to do something that triggers a tool approval (e.g. `Bash` running a non-allowlisted command).
3. The frontend shows the `PendingRequestForm` in the bottom banner with the tool input.
4. Click `Approve` (or `Deny`).
5. The session resumes and the tool executes (or is denied).

If this end-to-end loop works after a task, that task is verified.

For Python syntax verification between edits, use:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('PATH').read()); print('OK')"
```

---

## Task 1: Extend BaseAgent with the shared pending plumbing

**Files:**
- Modify: `src/twicc/agent/base_agent.py`

This task adds five things to `BaseAgent`: the two dicts in `__init__`, the `pending_requests` property, the three async/sync helpers, and the `pending_requests` injection in `get_info()`.

- [ ] **Step 1.1: Import `PendingRequest`**

Open `src/twicc/agent/base_agent.py` and update the import on line 21:

```python
from .states import AgentInfo, AgentState, PendingRequest, get_process_memory
```

(Was: `from .states import AgentInfo, AgentState, get_process_memory`.)

- [ ] **Step 1.2: Initialize the two dicts in `__init__`**

In the `__init__` of `BaseAgent`, immediately after `self._state_change_callback: StateChangeCallback | None = None` (currently line 80), add:

```python
        # Pending requests waiting on a user click (tool approval, ask user
        # question, …). Keyed by request_id (UUID). Provider subclasses populate
        # these via ``_await_pending_request``; the WS layer consumes them via
        # ``resolve_pending_request`` and the manager-level
        # ``BaseAgentManager.resolve_pending_request``.
        # ``_pending_futures`` is typed ``Any`` because each provider's SDK
        # returns its own decision type (Claude: PermissionResult{Allow,Deny};
        # Codex: raw dict). The caller is responsible for the cast.
        self._pending_requests: dict[str, PendingRequest] = {}
        self._pending_futures: dict[str, asyncio.Future[Any]] = {}
```

- [ ] **Step 1.3: Add the `pending_requests` property**

After the `wait_for_dead` method (currently ends around line 123), add a new section:

```python
    # ------------------------------------------------------------------
    # Pending requests (shared by every provider)
    # ------------------------------------------------------------------

    @property
    def pending_requests(self) -> tuple[PendingRequest, ...]:
        """Active pending requests waiting for user response, oldest first."""
        return tuple(
            sorted(self._pending_requests.values(), key=lambda r: r.created_at)
        )
```

- [ ] **Step 1.4: Add `_await_pending_request` helper**

Immediately after the property, add the awaiter:

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

        try:
            return await future
        finally:
            # Drop the entry whether we resolved or were cancelled.
            self._pending_requests.pop(request.request_id, None)
            self._pending_futures.pop(request.request_id, None)
            # Broadcast the cleared state to refresh the frontend.
            await self._notify_state_change()
```

- [ ] **Step 1.5: Add `_cancel_all_pending_futures`**

Right after, add the canceller:

```python
    def _cancel_all_pending_futures(self) -> None:
        """Cancel every in-flight pending Future.

        Used by provider ``interrupt_or_kill`` paths to unwind awaiters cleanly.
        The awaiter's ``finally`` clause does the dict cleanup; we just signal
        the cancellation here. Safe to call multiple times.
        """
        for future in self._pending_futures.values():
            if not future.done():
                future.cancel()
```

- [ ] **Step 1.6: Add `resolve_pending_request`**

Right after, add the resolver:

```python
    def resolve_pending_request(self, request_id: str, response: Any) -> bool:
        """Resolve a specific pending request with the user's response.

        Called by the manager when a WebSocket response arrives from the
        frontend. ``request_id`` disambiguates between concurrent pending
        requests on the same session (e.g. Claude's parallel Read + Glob).

        Returns ``True`` if the request was resolved, ``False`` if there was no
        matching in-flight Future (typically meaning the request was already
        resolved or the agent died in the meantime).
        """
        future = self._pending_futures.get(request_id)
        if future is None or future.done():
            logger.warning(
                "[session %s] resolve_pending_request: no in-flight Future "
                "for request_id=%s (known=%s)",
                self.session_id,
                request_id,
                list(self._pending_requests.keys()),
            )
            return False
        future.set_result(response)
        return True
```

- [ ] **Step 1.7: Update default `get_info()` to inject `pending_requests`**

Replace the existing `get_info()` body (currently lines ~152-173) so the snapshot already carries `pending_requests`:

```python
    def get_info(self) -> AgentInfo:
        """Build an immutable snapshot of the current agent state.

        Subclasses can override this to populate ``active_tools`` and
        ``last_started_tool_id`` by calling ``super()`` and ``_replace``-ing
        the result. ``pending_requests`` is always populated here.
        """
        # The subprocess no longer exists past DEAD — skip the memory lookup.
        memory_rss = None if self.state == AgentState.DEAD else self.get_memory_rss()
        return AgentInfo(
            session_id=self.session_id,
            project_id=self.project_id,
            provider=self.provider,
            state=self.state,
            previous_state=self.previous_state,
            started_at=self.started_at,
            state_changed_at=self.state_changed_at,
            last_activity=self.last_activity,
            error=self.error,
            memory_rss=memory_rss,
            kill_reason=self.kill_reason,
            pending_requests=self.pending_requests,
        )
```

- [ ] **Step 1.8: Sanity-check the file parses**

Run:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/agent/base_agent.py').read()); print('OK')"
```

Expected output: `OK`. Anything else means a syntax error in the inserted code — fix it before the next step.

- [ ] **Step 1.9: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/agent/base_agent.py
git commit -m "$(cat <<'EOF'
refactor(agent): host pending-request plumbing on BaseAgent

Adds the dicts (_pending_requests, _pending_futures), the pending_requests
property, _await_pending_request / _cancel_all_pending_futures /
resolve_pending_request helpers, and injects pending_requests into the default
get_info() snapshot. Sets up shared machinery so future providers (Codex)
can reuse the same routing without duplicating bookkeeping.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Extend BaseAgentManager with the shared manager-level helpers

**Files:**
- Modify: `src/twicc/agent/base_manager.py`

This task adds `resolve_pending_request` at the manager level and moves the `pending_requests` timeout-skip into the shared `_state_based_timeout`.

- [ ] **Step 2.1: Add `resolve_pending_request` to `BaseAgentManager`**

Open `src/twicc/agent/base_manager.py`. Add a new method right after `stop_subagent` (currently ends around line 119), in the same "Public API — generic for every provider" section:

```python
    async def resolve_pending_request(
        self,
        session_id: str,
        request_id: str,
        response: Any,
    ) -> bool:
        """Resolve a specific pending request on an agent.

        Routes the user's response to the correct agent (by ``session_id``)
        and the correct in-flight Future on that agent (by ``request_id``).
        ``response`` is provider-specific; the caller (typically the WS
        handler) is responsible for shaping it correctly for the SDK that
        will receive it.

        Returns ``True`` if the request was resolved, ``False`` if no agent
        or no matching pending request was found.
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return agent.resolve_pending_request(request_id, response)
```

- [ ] **Step 2.2: Add `pending_requests` skip in `_state_based_timeout`**

In the same file, modify `_state_based_timeout` (currently around line 490). Add this guard at the very top of the method body, immediately after the docstring and the `from django.conf import settings` import:

```python
        # Never time out an agent waiting on a user click. The countdown
        # resumes once the pending request resolves and last_activity is
        # touched again.
        if agent.pending_requests:
            return None
```

So the head of the method becomes:

```python
    def _state_based_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Shared per-state timeout policy reused by every provider.
        ...
        """
        from django.conf import settings

        # Never time out an agent waiting on a user click. The countdown
        # resumes once the pending request resolves and last_activity is
        # touched again.
        if agent.pending_requests:
            return None

        if agent.state == AgentState.STARTING:
            ...
```

- [ ] **Step 2.3: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/agent/base_manager.py').read()); print('OK')"
```

Expected output: `OK`.

- [ ] **Step 2.4: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/agent/base_manager.py
git commit -m "$(cat <<'EOF'
refactor(agent): host resolve_pending_request and pending-skip on BaseAgentManager

Adds BaseAgentManager.resolve_pending_request to route a frontend response to
the right agent's right Future. Moves the "skip timeouts while pending" guard
into _state_based_timeout so every provider that calls into it inherits the
behaviour, without each having to remember the check.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Simplify `ClaudeCodeAgent` to use the inherited plumbing

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/agent.py`

This task removes the now-duplicated state and methods, and rewires `_handle_pending_request` to use `super()._await_pending_request(...)`.

- [ ] **Step 3.1: Drop the two duplicated dict initializations in `__init__`**

In `ClaudeCodeAgent.__init__` (lines 122-126), delete:

```python
        # Concurrent pending requests: the CLI can run multiple concurrency-safe tools
        # in parallel within the same assistant turn (e.g., Read + Glob), each with its
        # own can_use_tool callback. Both dicts are keyed by request_id (UUID).
        self._pending_requests: dict[str, PendingRequest] = {}
        self._pending_futures: dict[str, asyncio.Future[PermissionResultAllow | PermissionResultDeny]] = {}
```

These attributes now live on `BaseAgent` and have already been initialized by `super().__init__(...)` two lines up.

- [ ] **Step 3.2: Drop the `pending_requests` property**

Delete the property (currently lines ~180-185):

```python
    @property
    def pending_requests(self) -> tuple[PendingRequest, ...]:
        """Active pending requests waiting for user response, oldest first."""
        return tuple(
            sorted(self._pending_requests.values(), key=lambda r: r.created_at)
        )
```

It's now inherited from `BaseAgent`.

- [ ] **Step 3.3: Remove the `pending_requests=` line from `get_info()` override**

In `ClaudeCodeAgent.get_info()` (currently lines 304-314), the override should now look like:

```python
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
```

The base now injects `pending_requests`, so the `_replace` no longer needs that field.

- [ ] **Step 3.4: Rewire `_handle_pending_request` to use `_await_pending_request`**

Locate `_handle_pending_request` (currently lines 541-634) and replace its body (starting at line 568 — keep the signature and docstring) with:

```python
        request_id = str(uuid.uuid4())

        if tool_name == "AskUserQuestion":
            request_type = "ask_user_question"
        else:
            request_type = "tool_approval"

        permission_suggestions = self.get_permission_suggestions(tool_name, input_data, context)

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

        # For ExitPlanMode: detect if the user modified the plan content.
        # Because of a "bug" in claude-agent-sdk / claude-code, the plan passed
        # via the response is not taken into account, so we update it ourselves
        # in the plan file (via ``_update_plan``).
        if (
            tool_name == "ExitPlanMode"
            and isinstance(response, PermissionResultAllow)
            and response.updated_input is not None
            and response.updated_input.get("plan") != input_data.get("plan")
        ):
            await self._update_plan(response.updated_input["plan"])

        return response
```

The `_await_pending_request` helper now owns:
- Dict insertion / removal in its `finally`,
- State-change broadcasts before and after,
- The `await future` itself.

We retain the `except CancelledError` block solely to log the cancellation with provider-context (tool name, request id) before re-raising. The cast from `Any` to `PermissionResultAllow | PermissionResultDeny` is implicit: Python won't enforce it, but the SDK and the caller's branches already assume that shape.

- [ ] **Step 3.5: Delete the old `resolve_pending_request` override**

Delete the entire method (currently lines 636-674):

```python
    def resolve_pending_request(
        self,
        request_id: str,
        response: PermissionResultAllow | PermissionResultDeny,
    ) -> bool:
        """Resolve a specific pending request with the user's response.
        ...
        """
        # request = self._pending_requests.get(request_id)
        future = self._pending_futures.get(request_id)
        if future is None or future.done():
            logger.warning(...)
            return False
        future.set_result(response)
        return True
```

It's now inherited.

- [ ] **Step 3.6: Delete the old `_cancel_pending_request_future` method**

Delete the entire method (currently lines 676-686):

```python
    def _cancel_pending_request_future(self) -> None:
        """Cancel any active pending request Futures to avoid asyncio warnings.
        ...
        """
        for future in self._pending_futures.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()
        self._pending_futures.clear()
```

- [ ] **Step 3.7: Find call sites of `_cancel_pending_request_future`**

Search for any remaining references in `ClaudeCodeAgent`:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -n "_cancel_pending_request_future\|_cancel_all_pending_futures" src/twicc/providers/claude_code/agent/agent.py
```

For each call site `self._cancel_pending_request_future()`, replace it with `self._cancel_all_pending_futures()`. The semantics are identical: cancel every in-flight Future. The base helper does NOT clear the dicts (the awaiters' `finally` does that), which is a deliberate behavioural difference — the dicts will be cleared by the awaiters as they unwind from `CancelledError`. This is fine for kill paths because the awaiters are awaited (or cancelled) elsewhere in the kill flow.

- [ ] **Step 3.8: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/claude_code/agent/agent.py').read()); print('OK')"
```

Expected output: `OK`.

- [ ] **Step 3.9: Smoke-test Claude end-to-end (mandatory before commit)**

Don't commit yet. Verify the Claude flow works in the worktree:

1. Ask the user to start the worktree dev servers if not already running.
2. Open the frontend at the worktree's port (see `devctl.py status`).
3. Start a new Claude session.
4. Send a message that will trigger a `Bash` approval (e.g. "run `git log` in this project").
5. The `PendingRequestForm` must appear in the bottom banner.
6. Click `Approve`. The command must execute and the session resume.
7. Repeat with `Deny` on the next prompt — Claude must take a different action.

If any step fails, fix the regression before committing.

- [ ] **Step 3.10: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/claude_code/agent/agent.py
git commit -m "$(cat <<'EOF'
refactor(claude-code): consume pending-request plumbing from BaseAgent

Removes the local copies of _pending_requests, _pending_futures, the
pending_requests property, resolve_pending_request and
_cancel_pending_request_future. _handle_pending_request now delegates the
in-flight bookkeeping to BaseAgent._await_pending_request; the kill path
calls the inherited _cancel_all_pending_futures.

Behaviour is unchanged end-to-end (PendingRequestForm flow verified).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Simplify `ClaudeCodeAgentManager`

**Files:**
- Modify: `src/twicc/providers/claude_code/agent/manager.py`

Two removals: the duplicate `resolve_pending_request` and the now-redundant `pending_requests` skip in `_check_agent_timeout`.

- [ ] **Step 4.1: Delete the duplicate `resolve_pending_request`**

In `src/twicc/providers/claude_code/agent/manager.py`, delete the method (currently lines 319-343):

```python
    async def resolve_pending_request(
        self,
        session_id: str,
        request_id: str,
        response: PermissionResultAllow | PermissionResultDeny,
    ) -> bool:
        """Resolve a specific pending request on an agent.
        ...
        """
        agent = self._agents.get(session_id)
        if agent is None:
            return False
        return agent.resolve_pending_request(request_id, response)
```

It's now inherited from `BaseAgentManager`. Callers (the WS handler) keep working with the inherited method — its signature is identical (`response: Any` in the base, narrower in the subclass; Python doesn't enforce the narrowing, so the existing call sites with Claude's response type still compile).

- [ ] **Step 4.2: Remove the `pending_requests` skip from `_check_agent_timeout`**

In the same file, locate `_check_agent_timeout` (currently lines 414-442). Delete only the `pending_requests` early-return; **keep** the active-crons skip (Claude-specific):

```python
    async def _check_agent_timeout(
        self, agent: BaseAgent, current_time: float,
    ) -> tuple[str, float, int] | None:
        """Apply Claude Code-specific skips, then the shared per-state policy.

        Skips agents that have active crons (the CLI has scheduled work that
        would be lost if we kill the agent). The ``pending_requests`` skip
        lives in :meth:`BaseAgentManager._state_based_timeout` and is shared
        with every provider that calls into it.
        """
        # Don't timeout agents with active cron jobs.
        try:
            from twicc.core.models import SessionCron
            has_crons = await asyncio.to_thread(
                lambda sid=agent.session_id: SessionCron.has_active_for_session(sid, Provider.CLAUDE_CODE)
            )
            if has_crons:
                return None
        except Exception as e:
            logger.error(
                "Error checking active crons for session %s: %s",
                agent.session_id, e,
            )

        return self._state_based_timeout(agent, current_time)
```

- [ ] **Step 4.3: Sanity-check the file parses**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run python -c "import ast; ast.parse(open('src/twicc/providers/claude_code/agent/manager.py').read()); print('OK')"
```

Expected output: `OK`.

- [ ] **Step 4.4: Verify nothing else in the project imports the deleted method**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
grep -rn "manager.resolve_pending_request\|_cancel_pending_request_future" src/twicc/ frontend/src/
```

The Python results must show the inherited method invocation only (`base_manager.py:resolve_pending_request` if anywhere, plus `agent.resolve_pending_request` inside the base implementation). No frontend hits. If there's a forgotten Python reference, update it.

- [ ] **Step 4.5: Handle the legacy `tests/test_pending_request.py` file**

The repo has a leftover test file at `tests/test_pending_request.py` from before the project adopted its current "no tests" policy. It contains a dedicated class (lines 514-578) and a parallel test (around line 1548) that test `_cancel_pending_request_future` directly, plus assertions that it "clears both dicts". After this PR these tests reference a method that no longer exists and assert behaviour the refactor intentionally drops (clearing happens in the awaiter's `finally`, not in the canceller).

Since the project doesn't run tests in CI (per `CLAUDE.md`), the broken file is not a runtime issue, but it's misleading for anyone who runs `pytest` locally. Pick one:

- **Option A — Delete the file** (recommended given project policy):

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
  git rm tests/test_pending_request.py
  ```

  Then include the deletion in the Task 4 commit.

- **Option B — Leave it as-is.** Note in the commit message body that `tests/test_pending_request.py` references deleted methods and the project's "no tests" policy means CI doesn't catch it.

Default: pick A unless the user has previously indicated they want to preserve / migrate the tests.

- [ ] **Step 4.6: Commit**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git add src/twicc/providers/claude_code/agent/manager.py
git commit -m "$(cat <<'EOF'
refactor(claude-code): consume resolve_pending_request and pending-skip from base

Removes the duplicate resolve_pending_request method (now inherited from
BaseAgentManager) and the pending_requests early-return in
_check_agent_timeout (now part of the shared _state_based_timeout). The
active-crons skip stays — it's Claude-specific.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Final end-to-end smoke test + verification

This is a quality gate before declaring PR1 done. Invoke @superpowers:verification-before-completion semantics: produce concrete evidence that nothing regressed.

- [ ] **Step 5.1: Restart the worktree's backend dev server**

Ask the user to restart the backend with `uv run ./devctl.py restart back` (do not run this yourself — it's a user-reserved operation per project CLAUDE.md). The Python changes need a process restart to be picked up.

- [ ] **Step 5.2: Backend startup check**

After restart, ask the user to confirm:

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
uv run ./devctl.py status
```

Backend must show as `running` on its assigned port. No crash on startup.

- [ ] **Step 5.3: Open a Claude session in the browser**

User opens a fresh Claude session in the worktree's frontend.

- [ ] **Step 5.4: Trigger a Bash approval**

User sends a message asking Claude to run a non-allowlisted command (e.g. `run "git log --oneline -5" please`). The frontend must:

1. Show the `PendingRequestForm` in the bottom banner.
2. Display the tool name (`Bash`) and the input data.
3. Be in a state where the user can click Approve / Deny.

- [ ] **Step 5.5: Approve, then deny**

User clicks Approve → the session resumes, the command runs, the output appears in the conversation. User then triggers another approval and clicks Deny → Claude reports the denial and continues with a different approach.

- [ ] **Step 5.6: Verify `AskUserQuestion` still works (if Claude triggers it)**

If Claude ever asks a clarifying question via `AskUserQuestion`, the same banner appears with the multi-choice form. User answers → the session resumes.

(Skip this step if the test session doesn't naturally produce one; the pathway is the same as `tool_approval` minus the suggestion checkboxes, so step 5.5 is sufficient evidence.)

- [ ] **Step 5.7: Verify timeout monitor doesn't fire on a pending request**

User lets a pending request sit unresolved for over `PROCESS_TIMEOUT_USER_TURN` (default 15 minutes — settable lower in `.env` via `PROCESS_TIMEOUT_USER_TURN=120` for a quick check, then revert). The agent must NOT be auto-stopped while the request is pending. This validates the moved-up `pending_requests` skip in `_state_based_timeout`.

(Skippable for the impatient. The behaviour is the same as before refactor, just enforced from a different location; if step 5.5 passes, this almost certainly does too.)

- [ ] **Step 5.8: Kill the agent while a pending request is in flight**

User triggers an approval (don't answer it), then clicks Stop. The agent must die cleanly: no `asyncio` warnings in `<worktree>/logs/backend.log`, no zombie Future. Sample command to inspect tail of the log:

```bash
tail -50 /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider/logs/backend.log
```

Look for `_cancel_all_pending_futures` calls or `CancelledError` traces. No errors expected — just clean unwind.

- [ ] **Step 5.9: Report verification result**

Report to the user, before any final action:

- ✅ All sanity checks (3.8, 4.3) passed.
- ✅ Claude approval flow end-to-end verified (5.5).
- ✅ Backend logs clean (5.8).
- (5.6, 5.7 status as applicable.)

If any verification failed, stop and surface to the user with the failing step + log excerpt. Do NOT proceed to the next step.

---

## Task 6: Wrap-up — PR1 complete

- [ ] **Step 6.1: Verify git log shows the four refactor commits**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/feature-multi-provider
git log --oneline -6
```

Expected (top to bottom):

```
<sha> refactor(claude-code): consume resolve_pending_request and pending-skip from base
<sha> refactor(claude-code): consume pending-request plumbing from BaseAgent
<sha> refactor(agent): host resolve_pending_request and pending-skip on BaseAgentManager
<sha> refactor(agent): host pending-request plumbing on BaseAgent
<sha> docs: address spec review feedback for Codex approvals design
<sha> docs: add Codex approvals integration design spec
```

- [ ] **Step 6.2: Decide on the next step with the user**

PR1 is done — the worktree branch `feature/multi-provider` now contains the base-agent plumbing. The 4 next PRs (PR2a, PR2b, PR3, PR4) build on this. Ask the user whether to:

- A — Open a draft PR for PR1 now (push branch + `gh pr create`).
- B — Continue locally into PR2a without opening a PR yet (everything stays on the worktree branch).
- C — Pause here and let the user decide later.

Do NOT push / open the PR yourself unless the user explicitly says A. The worktree branch has been local-only so far per project conventions.

---

## Open considerations (not blocking PR1)

These do not change the implementation but the next PR (PR2a) will assume them:

- `_pending_futures: dict[str, asyncio.Future[Any]]` is intentionally widened to `Any` at the base level. PR2a's Codex bridge will store raw dicts, PR1's Claude code stores `PermissionResult{Allow,Deny}`. There is no provider-side runtime check enforcing the type — each provider must cast itself.
- The `_await_pending_request` finally clause broadcasts a second `state_change` on dict cleanup. This was already the behaviour of the old Claude implementation; PR2a will inherit it for Codex.
- `_cancel_all_pending_futures` does NOT clear the dicts (the old Claude `_cancel_pending_request_future` did). This is by design: the awaiter's `finally` is the canonical place to clear, and calling it in two places risks double-broadcast or out-of-order state changes.
