# Auto-Deny of Approval Requests in Claude `bypassPermissions`

**Date:** 2026-10-08
**Status:** Adversarial spec review passed (6 rounds); awaiting user review before implementation.
**Scope:** Claude Code SDK agents in `bypassPermissions`, the process-state wire payload, the pending-request form, and the CLI/MCP `pending-requests` output.

## 1. Goal

In `bypassPermissions`, the user expects no interruption.
Claude Code still asks for approval for some commands that it considers dangerous (for example `rm -rf`).
Today TwiCC then waits for a human with no time limit.
The session stays blocked, also when no browser is open.

The Claude Code CLI denies such a request automatically after about two minutes.
The agent then continues and finds another way.
TwiCC must give the same behavior to the sessions that it drives through the SDK:

- The backend denies the request automatically after **120 seconds** without an answer.
- The deny carries an English message that tells the agent why, and that it must find another way.
- The backend owns the timer. It works when no frontend is connected.
- The pending-request form shows the remaining time.
- The CLI/MCP `pending-requests` output shows the deadline, so that an orchestrating parent knows that the request clears itself.

## 2. Scope decisions

### 2.1 Claude SDK agents: in scope

The rule applies to `ClaudeCodeAgent` (`src/twicc/providers/claude_code/agent/agent.py`).
Its `can_use_tool` callback (`_handle_pending_request`) waits on `BaseAgent._await_pending_request`.

### 2.2 Claude hybrid agents: out of scope

A hybrid session runs the real CLI in tmux.
The CLI shows its own dialog and applies its own timer.
TwiCC cannot read that timer, so the form shows no countdown for a hybrid request.

TwiCC already clears the pending request when the CLI resolves the prompt in the terminal:

1. A deny in the CLI writes an error `tool_result` to the JSONL.
2. `ClaudeCodeAgentManager.handle_hybrid_jsonl_signals` sees `signals.tool_results` and calls `HybridClaudeAgent.on_jsonl_progress`.
3. `on_jsonl_progress` calls `_clear_all_pendings("reap")` and broadcasts the new state. The form disappears.
4. A turn end (`on_jsonl_turn_end`) also clears every pending request.

No code change is necessary for hybrid.
The hybrid agent does not use `_await_pending_request`, so the new mechanism does not reach it.

**Not verified:** whether the CLI runs its native timer while the TwiCC `PermissionRequest` hook polls.
The implementation validation includes one manual hybrid check (section 9.3).

### 2.3 Codex: out of scope

A spike on 2026-10-08 used the real bundled binary (`codex-cli 0.161.0`) in a throwaway `CODEX_HOME`.

- With the TwiCC `yolo` policy (`_YOLO_APPROVAL` in `src/twicc/providers/codex/permission_modes.py`: Granular with `rules=False`, `sandbox_approval=False`), Codex sent **no** approval for `rm -rf`.
  Codex rejected the command itself. The model received `Rejected("rm -f style commands are not permitted. Use a safer approach")` and continued its turn.
- The development SDK logs (`~/.twicc/logs/sdk/codex/`) contain zero `item/commandExecution/requestApproval`, `item/fileChange/requestApproval` and `item/permissions/requestApproval` server requests.
  The requests that reach the user in these logs are questions (`item/tool/requestUserInput`) and MCP elicitations.

Codex `yolo` therefore already has the expected behavior.
A question must not be denied automatically.

For reference, the spike also proved a channel for a future need:
a `developer`-role message sent with `thread/inject_items` during a pending approval, before the `decline`, is accepted, lands after the tool output in the rollout, is read by the model, and is classified `DEBUG_ONLY` by the Codex compute.
This feature does not use it.

### 2.4 What is armed, and what always waits

The rule uses an **allow-list** of action tools: `AUTO_DENY_TOOLS = {"Bash", "PowerShell", "Monitor", "Write", "Edit", "MultiEdit", "NotebookEdit"}`.
These are the CLI's built-in shell tools (`Bash`, `PowerShell` on Windows, and `Monitor`, which also runs a shell command) and file-write tools: the tools whose prompt in `bypassPermissions` is a safety check on a potentially dangerous action.
`MultiEdit` is absent from the current CLI tool list; keeping it costs nothing and covers older CLI versions.

Every other request always waits for the human:

| Request | Why |
| --- | --- |
| `AskUserQuestion` (`request_type == "ask_user_question"`) | A question to the user, not a dangerous action. |
| `ExitPlanMode` | A plan review. The CLI can switch to plan mode by itself, and TwiCC's view of the mode can then still read `bypassPermissions`. |
| Interactive CLI tools and MCP tools that require user interaction (for example a setup offer or a role picker) | The prompt asks the user for a decision. It is not a safety check. |
| Any tool absent from the allow-list, including a future CLI tool | Unknown intent. Waiting for the human is the safe default. |
| MCP elicitations (`_handle_elicitation_request`) | A server asks the user for data. This path does not go through `_handle_pending_request`. |
| Every request in any other permission mode | The user chose to be asked. |

## 3. Behavior

1. Claude, in `bypassPermissions`, calls `can_use_tool` for a tool of `AUTO_DENY_TOOLS`.
2. The existing short-circuits run first, unchanged: TwiCC MCP tools (`mcp__twicc__*`) and the work-dir auto-approval.
3. TwiCC registers the pending request with a deadline: `auto_deny_at = created_at + 120`.
4. The form shows the request with the remaining time.
5. The user answers before the deadline: the normal path applies, and the timer stops.
6. Nobody answers before the deadline: the backend resolves the request with
   `PermissionResultDeny(message=AUTO_DENY_MESSAGE, interrupt=False)`.
   The CLI writes the error `tool_result` with this message. The turn continues.
7. The request leaves the process state. The form disappears on every connected client.
   The conversation shows the tool call with the deny message as its error result.

The permission mode is read **once**, when the request is created.
A mode change during the wait does not arm, disarm or move the timer.

### 3.1 Deny message

```text
The user did not answer this permission request within 2 minutes, so TwiCC denied it automatically. The action looked potentially dangerous. Do not retry it as is: find another way to reach your goal, or explain to the user what you need.
```

The duration in the text comes from the constant (section 4.1), so the two cannot diverge.

## 4. Backend design

### 4.1 New module `src/twicc/agent/auto_deny.py`

Provider-agnostic constants and one helper:

- `AUTO_DENY_DELAY_SECONDS = 120`
- `AUTO_DENY_MESSAGE`: the text of section 3.1, built from the constant.
- `AUTO_DENY_TOOLS`: the allow-list of section 2.4. It is a Claude tool list, but it lives here with the other constants of the rule.
- `auto_deny_remaining(pending, now) -> float | None`: `None` when `pending.auto_deny_at` is `None`; otherwise `max(0.0, round(auto_deny_at - now, 1))`.

### 4.2 `PendingRequest` (`src/twicc/agent/states.py`)

Add one optional field: `auto_deny_at: float | None = None` (epoch seconds, same clock as `created_at`).
The dataclass stays frozen. The field defaults to `None`, so every existing constructor keeps working.

### 4.3 `BaseAgent._await_pending_request` (`src/twicc/agent/base_agent.py`)

New keyword-only parameter: `auto_deny_response: Any = None`.

- When it is `None`: no change in behavior. The request is registered as given.
- When it is set:
  - Register `dataclasses.replace(request, auto_deny_at=request.created_at + AUTO_DENY_DELAY_SECONDS)`.
  - The deadline is a **wall-clock** instant (`time.time()`), like the value the payload shows.
    The event-loop clock (`time.monotonic()`) does not advance during a machine suspend on Linux, so one long `call_later` would fire up to 120 s late after a resume.
    The timer therefore checks the wall clock in steps of at most 5 seconds (`loop.call_later(min(5.0, remaining), _check)`), and denies at the first check where `time.time() >= auto_deny_at`.
  - The deny does nothing when the Future is already done. Otherwise it logs at INFO level (session id, request id, tool name) and calls `future.set_result(auto_deny_response)`.
  - The `finally` block cancels the pending timer handle in every case: user answer, auto-deny, cancellation by kill or interrupt.

The base class owns the timer, so the pending-request bookkeeping stays in one place.
The provider owns the deny value, because its type is provider-specific.
The existing `finally` logic (`last_pending_resolved_at`, state broadcast) applies to an auto-deny without change.

### 4.4 Claude arming rule (`ClaudeCodeAgent._handle_pending_request`)

Arm when all conditions are true:

- `request_type == "tool_approval"`;
- `tool_name in AUTO_DENY_TOOLS`;
- `untrusted` is `False` (the value the handler already resolves with `_resolve_untrusted_now()`);
- `self.agent_settings.permission_mode == "bypassPermissions"`.

When armed, pass `auto_deny_response=PermissionResultDeny(message=AUTO_DENY_MESSAGE, interrupt=False)`.

An untrusted project can never hold `bypassPermissions` in `agent_settings`. The `untrusted` check keeps the rule safe during a trust revocation that is not yet re-clamped: in that case the request waits for the human.

### 4.5 Keep the in-memory mode exact after a `setMode` answer

`agent_settings.permission_mode` must be the mode the CLI really applies. Today two paths write it: the trust clamp at launch and `set_permission_mode`.
A third path changes the CLI mode without updating it: an approval answer that carries a session-destination `setMode` permission (for example "allow, and switch to `default`").
`ws.py` persists that mode to the database, and the CLI applies it through `updated_permissions`, but the SDK agent's `agent_settings` keeps the old mode until the settings monitor reconciles at the next `USER_TURN`.
Without a fix, every later approval of the same turn is armed in a mode the user just left.

Fix: when `_handle_pending_request` returns a `PermissionResultAllow` whose `updated_permissions` carry a `setMode` with destination `session` (or no destination), it updates `agent_settings.permission_mode` to that mode.
`HybridClaudeAgent.resolve_pending_request` already does the same mirror; the SDK agent follows the same rule.
The settings monitor then sees no difference and does not call `set_permission_mode` a second time.

### 4.6 Process-state payload (`serialize_agent_info`)

For each pending request with a deadline, add `auto_deny_in_seconds` = `auto_deny_remaining(pending, time.time())`.
The condition is `pending.auto_deny_at is not None`, never a truthiness test: `0.0` is a valid value and must be emitted.
The key is absent when there is no deadline. The frontend tests that the key exists, not that it is truthy.

The payload carries the remaining time, not the epoch deadline.
Every caller of `serialize_agent_info` (`broadcast_process_state`, the connect snapshot in `asgi.py`, the share consumer) serializes at send time, so the value is fresh.
The browser can run on another machine (tunnel, LAN). A relative value removes any dependency on the clock offset between the browser and the server.

## 5. Frontend design

### 5.1 Local deadline

New pure module `frontend/src/utils/autoDeny.js`:

- `AUTO_DENY_DELAY_SECONDS = 120`: mirror of the backend constant, used only in the tooltip text. A comment names `src/twicc/agent/auto_deny.py` as the source of truth.
- `withAutoDenyDeadlines(pendingRequests, receivedAtMs, previousRequests)`: returns the list where each entry that has `auto_deny_in_seconds` is replaced by a copy **without** that wire key and **with** `autoDenyDeadlineMs = receivedAtMs + auto_deny_in_seconds * 1000`.
  When `previousRequests` holds the same `request_id` with a deadline less than 0.5 second away from the new one, the previous deadline is kept.
  Entries without the key are returned as they are.
- `formatAutoDenyRemaining(deadlineMs, nowMs)`: returns `m:ss`, rounded down, with a floor of `0:00`.

The store applies `withAutoDenyDeadlines(…, Date.now(), <current pending_requests of the session>)` at the two places that write `pending_requests` into a process state, in `frontend/src/stores/data.js`:

- `setProcessState` (the `process_state` WebSocket message);
- the active-processes snapshot loop (the list sent on connect).

Why the wire key is dropped and a close deadline is kept: `_patchProcessState` replaces a field only when `jsonValuesEqual` says it changed.
`auto_deny_in_seconds` changes on every payload. Kept as is, it would replace the whole `pending_requests` array on every `process_state`, and re-render everything derived from a large `tool_input`.
With this rule, an unchanged request compares equal.
The backend deadline itself never moves (it is wall-clock, section 4.3). A local deadline changes by more than 0.5 second only when a stamp was taken late (for example a first payload handled on a busy page load): the next payload then corrects it.
The keep tolerance (0.5 s) stays below the answer lock margin (1 s, section 5.2), so the keep rule cannot use the whole margin.

The pending-request draft storage (`frontend/src/utils/pendingRequestDraftStorage.js`) hashes the whole request object, and a draft restores only on an exact hash match.
The deadline differs between two page loads, so `hashPendingRequest` excludes `autoDenyDeadlineMs` and `auto_deny_in_seconds`.
Without this, a draft (a deny reason, an edited input) would never restore after a reload.

### 5.2 Countdown display (`PendingRequestForm.vue`)

The shared shell serves both providers. The countdown therefore needs no change in the provider bodies.

- When `pendingRequest.autoDenyDeadlineMs` exists, show a compact label: `Auto-deny in 1:42`.
- Place it in the normal header, after the title, and in the `#trailing` slot of the minimized `CollapsedBar`.
- When the display reaches `0:00` (less than 1 second left, because the display rounds down), show `Auto-denying…` until the broadcast removes the request.
  While the display shows `0:00`, the form refuses new answers: the shell passes the existing `is-responding` guard as true (every provider body already checks it before it submits), and the shell's own submit handler refuses the answer too.
  The lock is computed from the current time and the current deadline. It is not a one-way flag, so a corrected deadline (section 5.1) unlocks the form again.
  The shell's submit handler is the only place that sends an answer. It compares `Date.now()` with the deadline when it runs, never the one-second `now` ref, so the 1-second margin holds exactly. The ref only drives the display and the `is-responding` value given to the bodies, which can therefore lag by up to one tick.
  Reusing `is-responding` has two accepted side effects, and the provider bodies do not change:
  - The request's draft is discarded when the lock starts (`usePendingRequestDraft` discards on `isResponding`). The request is about to be denied, so its draft has no value. In the rare unlock case, the draft is saved again at the user's next edit.
  - The action buttons show their spinner. Next to `Auto-denying…`, it reads as the resolution in progress, which is what happens.
  The local deadline is later than the backend deadline by the stamping delay (serialization to handler run). The answer then needs its own travel time back to the backend.
  The 1-second margin covers the sum of the two, minus the 0.5-second keep tolerance in the worst case.
  So a click that the backend would discard is not sent, except when these delays exceed the margin (section 7).
- A tooltip explains: `Bypass permissions mode: this request is denied automatically when nobody answers within 2 minutes.` The duration comes from `AUTO_DENY_DELAY_SECONDS` in `autoDeny.js`.
- Every frontend watcher on a pending request keys on `request_id` (`PendingRequestForm.vue`, `SessionItemsList.vue`, `usePendingRequestDraft.js`, the provider bodies). A re-stamped deadline therefore does not re-run focus, draft restore or the footer accordion.
- A one-second interval updates a local `now` ref. The interval runs only while the displayed request has a deadline, and stops on unmount.
- The form shows the first pending request only (existing behavior). Each queued request keeps its own deadline and shows it when it reaches the form.

No new Web Awesome component is used, so `main.js` needs no new import.

## 6. CLI and MCP

The skill-covered `session pending-requests` command is also the MCP tool `session_pending_requests`.
A tool approval cannot be answered from the CLI: it is an `out_of_scope` entry (`out_of_scope_entry` in `src/twicc/providers/pending_question.py`).

### 6.1 Output change

When the pending request has a deadline, `out_of_scope_entry` adds two keys:

- `auto_deny_at`: the deadline in ISO 8601 (same helper as `created_at`);
- `auto_deny_in_seconds`: the remaining seconds at read time, from `auto_deny_remaining` (section 4.1): rounded to 0.1 s, floor at `0`. Same value as the WebSocket payload.

Both keys are absent when there is no deadline.
`question_entry` does not change: a question is never armed.

### 6.2 Documentation

The rule for an agent that reads such an entry:
`wait-reply` returns `awaiting_user_input` at once while a request is pending, so an immediate new wait only spins.
The agent lets `auto_deny_in_seconds` pass without calling `wait-reply` in a loop, then waits again. In a batch, it can meanwhile wait on the other sessions only.
The doc names no delay mechanism: a Codex agent can `sleep` in its shell, while the Claude Code Bash tool refuses a foreground `sleep` (it offers a background command or `Monitor`).

- `src/twicc/agent/plugin/twicc/skills/twicc-session/questions.md`: list the two optional keys of an out-of-scope entry, and state the rule above.
- `src/twicc/agent/plugin/twicc/skills/twicc-orchestration/control-cookbook.md`: in the `awaiting_user_input` paragraph, add the same rule in one clause.
- `SKILLS-AND-CLI.md` (repository root): the `pending-requests` entry lists the two optional keys.
- `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: bump the patch version (user-visible change to an existing output).

The `wait-reply` semantics do not change: a pending request still ends a wait with `awaiting_user_input`.

### 6.3 MCP Events

The `session.concluded` event (webhook) fires with `outcome == "awaiting_user_input"` for an armed request too.
Its `data` already carries `request_type` (`build_data` in `src/twicc/mcp/events/delivery.py`).
When the pending request has a deadline, `data` also carries `auto_deny_at` (ISO 8601), so an external consumer knows that the request clears itself.
The published event schema (`payloadSchema` of `session.concluded` in `src/twicc/mcp/events/catalog.py`) declares it as an optional string: "ISO 8601 deadline; present only for an auto-deniable `awaiting_user_input`".

## 7. Edge cases

| Case | Behavior |
| --- | --- |
| The user answers in the same instant as the timer | The first `set_result` wins. `resolve_pending_request` returns `False` for the late answer and logs its existing warning. The form disappears with the next broadcast. |
| A late "allow" carries a `setMode` suggestion | The form refuses answers from `0:00` (section 5.2), so this happens only when the stamping delay plus the answer's travel time exceed the margin of section 5.2. In that window, `ws.py` persists the `setMode` before it calls `resolve_pending_request`; this order already applies to any late answer. No change. |
| The user answers "allow, and switch to `default`" | The SDK agent mirrors the new mode at once (section 4.5). The next approvals of the same turn are not armed. |
| Several parallel approvals | Each request has its own deadline and its own timer. |
| Agent kill or turn interrupt | `_cancel_all_pending_futures` cancels the Future. The `finally` block cancels the timer handle. |
| Backend restart | An SDK agent dies with the backend. No pending request survives, so no timer must be restored. |
| Machine suspend | The deadline is wall-clock. The timer checks it at most 5 seconds apart (section 4.3), so the deny fires at most 5 seconds after resume when the deadline has passed. The form and the CLI show the same wall-clock deadline. |
| No browser connected | The backend timer fires. The deny does not depend on any client. |
| Mode change during the wait | No effect (section 3). |
| The CLI switches to plan mode by itself (`EnterPlanMode`) | Accepted limitation. TwiCC does not track this switch, so `agent_settings` still reads `bypassPermissions`. An action-tool prompt in that state is armed. `ExitPlanMode` itself is never armed (section 2.4). |
| Subagent (`Agent` tool) request | Subagent approvals go through the same `can_use_tool` callback. The same rule applies. |
| Browser notification "needs approval" | Unchanged. It still fires when the request appears. |

## 8. Files touched

| File | Change |
| --- | --- |
| `src/twicc/agent/auto_deny.py` | New: constants, allow-list and `auto_deny_remaining`. |
| `src/twicc/agent/states.py` | `PendingRequest.auto_deny_at`; `serialize_agent_info` adds `auto_deny_in_seconds`. |
| `src/twicc/agent/base_agent.py` | `_await_pending_request(…, auto_deny_response=None)` with the wall-clock timer. |
| `src/twicc/providers/claude_code/agent/agent.py` | Arming rule and `setMode` mirror in `_handle_pending_request`. The comment of its `mcp__twicc__` short-circuit says that the callback is never invoked in `bypassPermissions`; correct it. |
| `src/twicc/providers/pending_question.py` | `out_of_scope_entry` adds the two keys. |
| `src/twicc/mcp/events/delivery.py` | `build_data` adds `auto_deny_at`. |
| `src/twicc/mcp/events/catalog.py` | `session.concluded` `payloadSchema` declares `auto_deny_at`. |
| `frontend/src/utils/autoDeny.js` (+ `autoDeny.test.js`) | New pure helpers. |
| `frontend/src/stores/data.js` | Stamp deadlines at the two ingestion points. |
| `frontend/src/utils/pendingRequestDraftStorage.js` | `hashPendingRequest` excludes the two deadline keys. |
| `frontend/src/components/message/PendingRequestForm.vue` | Countdown label, tooltip, answers refused at `0:00`. |
| Skill docs, `SKILLS-AND-CLI.md`, `plugin.json` | Section 6.2. |

No database model change and no migration.

## 9. Testing

### 9.1 Backend (pytest)

- `BaseAgent._await_pending_request` with `auto_deny_response` and a patched short delay: the Future resolves with the deny value; the request leaves `pending_requests`; `last_pending_resolved_at` is set.
- An answer before the deadline wins, and the timer handle is cancelled (no second `set_result`, no error).
- A cancellation (kill path) cancels the timer handle.
- Without `auto_deny_response`: `auto_deny_at` stays `None` and no timer is scheduled.
- The wall-clock check: a deadline already passed at a check fires the deny; a deadline not yet reached re-schedules the check.
- `ClaudeCodeAgent._handle_pending_request` arms only for `AUTO_DENY_TOOLS` in `bypassPermissions` for a trusted project. It never arms `AskUserQuestion`, `ExitPlanMode`, a tool outside the list, or any other mode.
- An allow with a session `setMode` updates `agent_settings.permission_mode`; the next approval in the new mode is not armed.
- `serialize_agent_info` emits `auto_deny_in_seconds` only for a request with a deadline, with a floor of `0`.
- `out_of_scope_entry` emits `auto_deny_at` and `auto_deny_in_seconds` only for a request with a deadline, with a floor of `0`.
- `build_data` emits `auto_deny_at` only for a request with a deadline.

### 9.2 Frontend (node:test)

- `withAutoDenyDeadlines`: stamps only the entries with the key and drops that key; keeps a previous deadline less than 0.5 second away; takes a new deadline further away; does not change entries without the key.
- `formatAutoDenyRemaining`: `m:ss` format, rounded down, floor at `0:00`.
- `hashPendingRequest`: two objects that differ only by the deadline keys give the same hash.

### 9.3 Manual validation

1. A Claude SDK session in `bypassPermissions` triggers a dangerous-command approval. The form shows `Auto-deny in 1:59` (or `2:00`) and counts down. Also check whether a dangerous `Monitor` command prompts in `bypassPermissions`, and whether its prompt gets the countdown.
2. After 120 seconds, the form disappears. The tool result shows the deny message. The agent continues its turn.
3. The same test with the browser closed during the wait: the agent continues; reopening shows the completed turn.
4. An answer before the deadline behaves as today.
5. The same prompt in `default` mode shows no countdown and waits.
6. `session pending-requests` on the waiting session lists `auto_deny_at` and `auto_deny_in_seconds`.
7. Hybrid check: a hybrid session in `bypassPermissions` triggers the same approval. Nobody answers. Record whether the CLI denies by itself, and confirm that the form disappears when it does.
