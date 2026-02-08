# Pending Requests: Tool Approval & Clarifying Questions

**Date:** 2026-02-08
**Status:** COMPLETED
**SDK Reference:** https://platform.claude.com/docs/en/agent-sdk/user-input

## Overview

Claude Code can request user input during execution in two scenarios:

1. **Tool approval** — Claude wants to use a tool (Bash, Write, Edit...) and needs permission
2. **Clarifying questions** — Claude needs the user to choose between options before proceeding (via the `AskUserQuestion` tool)

Both are handled through the same SDK mechanism: the `can_use_tool` callback. Today, TWICC bypasses this entirely with `permission_mode="bypassPermissions"`. This spec adds support for surfacing these requests to the frontend and collecting user responses.

## How the Claude Agent SDK Works

### The `can_use_tool` callback

When Claude wants to use a tool that isn't auto-approved by the current permission mode, the SDK invokes an async callback passed in `ClaudeAgentOptions`:

```python
async def can_use_tool(
    tool_name: str,
    input_data: dict,
    context: ToolPermissionContext
) -> PermissionResultAllow | PermissionResultDeny:
```

The SDK **blocks execution** until this callback returns. Claude is suspended — nothing happens until the callback resolves.

The callback fires in two distinct cases, distinguished by `tool_name`:

#### Case 1: Tool approval (`tool_name != "AskUserQuestion"`)

Claude wants to execute a tool. The callback receives:

- `tool_name`: the tool name (`"Bash"`, `"Write"`, `"Edit"`, `"Read"`, etc.)
- `input_data`: the tool's parameters, which vary by tool:

| Tool | Key input fields |
|------|-----------------|
| `Bash` | `command`, `description`, `timeout` |
| `Write` | `file_path`, `content` |
| `Edit` | `file_path`, `old_string`, `new_string` |
| `Read` | `file_path`, `offset`, `limit` |

The callback must return either:

- **Allow**: `PermissionResultAllow(updated_input=input_data)` — tool executes with original or modified input
- **Deny**: `PermissionResultDeny(message="reason")` — tool is blocked, Claude sees the message and may adapt

#### Case 2: Clarifying question (`tool_name == "AskUserQuestion"`)

Claude wants to ask the user a question. The `input_data` contains:

```json
{
    "questions": [
        {
            "question": "How should I format the output?",
            "header": "Format",
            "options": [
                {"label": "Summary", "description": "Brief overview"},
                {"label": "Detailed", "description": "Full explanation"}
            ],
            "multiSelect": false
        }
    ]
}
```

Constraints:
- 1 to 4 questions per call
- 2 to 4 options per question
- `multiSelect`: if `true`, user can select multiple options
- The user can also provide free text instead of choosing a predefined option

The callback must return `PermissionResultAllow` with the answers:

```python
return PermissionResultAllow(
    updated_input={
        "questions": input_data["questions"],   # pass through original questions
        "answers": {
            "How should I format the output?": "Summary",         # single select
            "Which sections?": "Introduction, Conclusion"         # multi select: join with ", "
        }
    }
)
```

Keys in `answers` are the `question` text. Values are the selected option's `label` (or free text).

### Python SDK constraints

The `can_use_tool` callback in Python requires two things:

1. **Streaming mode**: the prompt must be sent as an async generator, not a plain string
2. **A dummy `PreToolUse` hook**: a workaround that keeps the stream open for the callback to be invoked

```python
async def dummy_hook(input_data, tool_use_id, context):
    return {"continue_": True}

options = ClaudeAgentOptions(
    can_use_tool=my_callback,
    hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[dummy_hook])]},
)
```

### Permission modes

The `permission_mode` setting determines which tools trigger the callback:

| Mode | Behavior |
|------|----------|
| `default` | Nothing auto-approved; all tools trigger callback |
| `acceptEdits` | File operations auto-approved; Bash and others trigger callback |
| `bypassPermissions` | Everything auto-approved; callback never fires |
| `plan` | No tool execution at all |

TWICC will not implement its own auto-approve logic. If the user (or the system) sets `default`, every tool triggers the callback and the user gets asked every time. The permission mode is a configuration concern, not a TWICC concern.

**Current state:** Today, TWICC hardcodes `permission_mode="bypassPermissions"` because there is no UI to handle approval requests. This spec implements the approval/question infrastructure. Once it lands, the permission mode will initially be switched to a non-bypass mode (likely `default` or `acceptEdits`). Allowing users to choose their permission mode will be implemented in a future step.

### What is NOT covered

This spec covers only `can_use_tool` (tool approval + `AskUserQuestion`). The following SDK capabilities are out of scope:

- Hooks (PreToolUse, PostToolUse, etc.) beyond the required dummy hook
- Custom tools (MCP servers)
- Streaming input / interrupts
- Structured outputs
- File checkpointing / rewind
- User-configurable permission mode — this spec hardcodes the permission mode at startup. Allowing users to choose their mode (per session, per project, or globally) requires its own UI and settings infrastructure. This includes both the initial mode when starting/resuming a session and the ability to change it mid-session (the SDK supports dynamic changes via `set_permission_mode()`). This will be a separate spec once the pending request infrastructure is in place.

## Concept: Pending Request

A **pending request** is a request from Claude that is waiting for a user response. It lives as a nullable field on `ClaudeProcess`:

- `None` → no pending request, normal operation
- Not `None` → Claude is blocked waiting for user input

The pending request data contains everything needed to display the request in the frontend and to re-send it on WebSocket reconnection.

There can only be **one pending request at a time** per process (the SDK calls `can_use_tool` sequentially, never concurrently).

### States

The process states remain unchanged: `STARTING`, `ASSISTANT_TURN`, `USER_TURN`, `DEAD`.

A pending request happens **during `ASSISTANT_TURN`** — Claude is working, hits a tool call, and pauses to ask. The state stays `ASSISTANT_TURN` but the process additionally has a non-null `pending_request`.

### Process death during a pending request

If the process dies while a pending request is active (error, manual kill, timeout, shutdown), the normal death flow handles cleanup automatically:

1. Backend: `_set_state(ProcessState.DEAD)` → `_notify_state_change()` broadcasts `process_state` with `state: "dead"` and no `pending_request`
2. Frontend: the store updates the process state → Vue reactivity removes the form automatically

The orphaned Future should be cancelled explicitly in the death path (`_handle_error()`, `kill()`) to avoid asyncio warnings about unawaited Futures. But functionally, the frontend clears correctly without any special mechanism — the pending request disappears because the process state no longer carries it.

## Backend

### Changes to `process.py`

#### New instance variables on `ClaudeProcess`

```python
self._pending_request: PendingRequest | None = None
self._pending_request_future: asyncio.Future | None = None
```

#### New data class: `PendingRequest`

```python
@dataclass
class PendingRequest:
    request_id: str                     # UUID, unique per request
    request_type: str                   # "tool_approval" or "ask_user_question"
    tool_name: str                      # SDK tool name
    tool_input: dict                    # SDK tool input data
    created_at: float                   # time.time() when created
```

For `tool_approval`: `tool_name` is the actual tool (`"Bash"`, `"Write"`, etc.) and `tool_input` contains the tool parameters.

For `ask_user_question`: `tool_name` is `"AskUserQuestion"` and `tool_input` contains the `questions` array.

#### The `can_use_tool` callback

```python
async def _handle_pending_request(self, tool_name, input_data, context):
    request_id = str(uuid.uuid4())

    if tool_name == "AskUserQuestion":
        request_type = "ask_user_question"
    else:
        request_type = "tool_approval"

    self._pending_request = PendingRequest(
        request_id=request_id,
        request_type=request_type,
        tool_name=tool_name,
        tool_input=input_data,
        created_at=time.time(),
    )

    # Create future for the response
    self._pending_request_future = asyncio.get_event_loop().create_future()

    # Notify frontend via state change callback (broadcasts WebSocket message)
    await self._notify_state_change()

    # Block here until frontend responds
    response = await self._pending_request_future

    # Clear pending state
    self._pending_request = None
    self._pending_request_future = None

    # Notify that pending request is resolved
    await self._notify_state_change()

    return response
```

#### Resolving the pending request

Called by `ProcessManager` when a WebSocket response arrives:

```python
def resolve_pending_request(self, response: PermissionResultAllow | PermissionResultDeny) -> bool:
    """Resolve the pending request with the user's response.

    Returns True if resolved, False if no pending request or already resolved.
    """
    if self._pending_request_future is None or self._pending_request_future.done():
        return False
    self._pending_request_future.set_result(response)
    return True
```

#### Property for external access

```python
@property
def pending_request(self) -> PendingRequest | None:
    return self._pending_request
```

#### Changes to `ClaudeAgentOptions` construction

In `start()`, the options change from:

```python
options = ClaudeAgentOptions(
    cwd=self.cwd,
    permission_mode="bypassPermissions",
    ...
)
```

To:

```python
options = ClaudeAgentOptions(
    cwd=self.cwd,
    permission_mode="default",       # or configurable
    can_use_tool=self._handle_pending_request,
    hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_dummy_hook])]},
    ...
)
```

With the module-level dummy hook:

```python
async def _dummy_hook(input_data, tool_use_id, context):
    return {"continue_": True}
```

#### Changes to `_build_query_prompt`

Must always return an async generator (streaming mode required for `can_use_tool`):

```python
def _build_query_prompt(self, text, images, documents) -> AsyncIterator[dict]:
    content_blocks = []

    if images:
        content_blocks.extend(images)
    if documents:
        content_blocks.extend(documents)

    content_blocks.append({"type": "text", "text": text})

    async def _message_stream():
        yield {
            "type": "user",
            "message": {"role": "user", "content": content_blocks},
            "parent_tool_use_id": None,
        }

    return _message_stream()
```

No more plain string path — always async generator.

### Changes to `states.py`

#### `PendingRequest` in `ProcessInfo`

Add to `ProcessInfo`:

```python
class ProcessInfo(NamedTuple):
    ...
    pending_request: PendingRequest | None = None
```

Add to `serialize_process_info()`:

```python
if info.pending_request is not None:
    data["pending_request"] = {
        "request_id": info.pending_request.request_id,
        "request_type": info.pending_request.request_type,
        "tool_name": info.pending_request.tool_name,
        "tool_input": info.pending_request.tool_input,
        "created_at": info.pending_request.created_at,
    }
```

This means pending request data is included in `process_state` WebSocket messages and in `active_processes` sent on connection.

### Changes to `manager.py`

#### Timeout exemption

In `check_and_stop_timed_out_processes()`, skip processes with a pending request:

```python
for session_id, process in processes_snapshot:
    # Don't timeout processes waiting for user input
    if process.pending_request is not None:
        continue
    ...
```

#### New method: `resolve_pending_request()`

```python
async def resolve_pending_request(self, session_id: str, response_data: dict) -> bool:
    """Resolve a pending request on a process.

    Args:
        session_id: The session to resolve
        response_data: The user's response data

    Returns:
        True if resolved, False if no matching process/request
    """
    process = self._processes.get(session_id)
    if process is None:
        return False
    return process.resolve_pending_request(response_data)
```

### Changes to `asgi.py`

#### New WebSocket message type (frontend → backend)

A single message type for all pending request responses, mirroring the unified `pending_request` field in `process_state`:

```python
elif msg_type == "pending_request_response":
    await self._handle_pending_request_response(content)
```

#### Handler: `_handle_pending_request_response()`

```python
async def _handle_pending_request_response(self, content: dict) -> None:
    """Handle a pending request response from the user.

    Expected content for tool approval:
    {
        "type": "pending_request_response",
        "session_id": "...",
        "request_id": "...",
        "request_type": "tool_approval",
        "decision": "allow" | "deny",
        "message": "optional reason for deny",
        "updated_input": { ... }  // optional, for approve with modifications
    }

    Expected content for ask_user_question:
    {
        "type": "pending_request_response",
        "session_id": "...",
        "request_id": "...",
        "request_type": "ask_user_question",
        "answers": {
            "question text": "selected label or free text",
            ...
        }
    }
    """
    session_id = content.get("session_id")
    request_id = content.get("request_id")
    request_type = content.get("request_type")

    manager = get_process_manager()

    if request_type == "tool_approval":
        decision = content.get("decision")
        if decision == "allow":
            updated_input = content.get("updated_input")
            response = PermissionResultAllow(updated_input=updated_input)
        else:
            message = content.get("message", "User denied this action")
            response = PermissionResultDeny(message=message)

    elif request_type == "ask_user_question":
        answers = content.get("answers", {})
        # The original questions must be passed back along with answers
        process = manager._processes.get(session_id)
        if process and process.pending_request:
            original_input = process.pending_request.tool_input
            response = PermissionResultAllow(
                updated_input={
                    "questions": original_input.get("questions", []),
                    "answers": answers,
                }
            )
        else:
            return  # No matching process/request

    else:
        return  # Unknown request type

    await manager.resolve_pending_request(session_id, response)
```

### WebSocket messages (backend → frontend)

No new dedicated broadcast messages needed. The pending request data flows through the **existing** `process_state` message:

```json
{
    "type": "process_state",
    "session_id": "...",
    "project_id": "...",
    "state": "assistant_turn",
    "started_at": 1234567890.0,
    "state_changed_at": 1234567891.0,
    "pending_request": {
        "request_id": "uuid-xxx",
        "request_type": "tool_approval",
        "tool_name": "Bash",
        "tool_input": {
            "command": "rm -rf /tmp/test",
            "description": "Delete test directory"
        },
        "created_at": 1234567892.0
    }
}
```

When the pending request is resolved, another `process_state` is broadcast with `"pending_request"` absent (or null), which clears the UI for all connected clients.

On WebSocket connect, `active_processes` already includes serialized `ProcessInfo` — the pending request data comes along automatically.

### Reconnection behavior

Since pending request data is part of `ProcessInfo`:

1. **WebSocket reconnect**: `active_processes` message sent on `connect()` includes all pending requests → frontend restores UI
2. **Page reload / session load**: frontend fetches process state which includes pending request → UI shows form immediately

No additional mechanism needed — it piggybacks on the existing process state infrastructure.

## Frontend

### Changes to `useWebSocket.js`

The `process_state` handler already exists. No new message type to add. The store action `setProcessState` will receive the `pending_request` field as part of the existing message.

When a pending request is resolved (broadcast with no `pending_request`), the same `setProcessState` clears it.

### Changes to `data.js` (Pinia store)

#### State

The `pending_request` data is stored within `processStates`:

```javascript
// processStates[sessionId] already has: state, project_id, started_at, state_changed_at, memory, error, kill_reason
// Add: pending_request (null or object)
```

#### Actions

Update `setProcessState` to include `pending_request`:

```javascript
setProcessState(sessionId, projectId, state, metadata) {
    this.processStates[sessionId] = {
        state,
        project_id: projectId,
        ...metadata,
        pending_request: metadata.pending_request || null,
    }
}
```

Add response action:

```javascript
respondToPendingRequest(sessionId, requestId, responseData) {
    sendWsMessage({
        type: 'pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        ...responseData,
    })
}
```

Where `responseData` depends on the request type:

- **Tool approval**: `{ request_type: 'tool_approval', decision: 'allow' | 'deny', message?, updated_input? }`
- **Ask user question**: `{ request_type: 'ask_user_question', answers: { ... } }`

Add a getter:

```javascript
getPendingRequest(sessionId) {
    return this.processStates[sessionId]?.pending_request || null
}
```

### Changes to `useWebSocket.js` — global send function

Add one exported function:

```javascript
export function respondToPendingRequest(sessionId, requestId, responseData) {
    sendWsMessage({
        type: 'pending_request_response',
        session_id: sessionId,
        request_id: requestId,
        ...responseData,
    })
}
```

### Session list indicator

In the session list component, when a session's process has a `pending_request`, display a visual indicator (icon, badge, color) so the user immediately sees that Claude is waiting for input on that session.

### Pending request UI in `SessionItemsList.vue`

When `pending_request` is set for the current session, display a form **in place of the MessageInput** area. Two variants:

#### Tool approval form

Displayed when `pending_request.request_type === "tool_approval"`.

Shows:
- Tool name (e.g., "Bash", "Write")
- Tool parameters formatted for readability:
  - Bash: the command and description
  - Write/Edit: the file path (content can be collapsed/expandable)
  - Other tools: raw JSON of input
- **Approve** button
- **Deny** button with optional text input for reason

On approve: calls `respondToPendingRequest(sessionId, requestId, { request_type: 'tool_approval', decision: 'allow', updated_input: originalInput })`.
On deny: calls `respondToPendingRequest(sessionId, requestId, { request_type: 'tool_approval', decision: 'deny', message: reason })`.

#### Ask user question form

Displayed when `pending_request.request_type === "ask_user_question"`.

For each question in `pending_request.tool_input.questions`:
- Display the header and question text
- Display options as selectable buttons/chips:
  - If `multiSelect === false`: radio-like behavior (single selection)
  - If `multiSelect === true`: checkbox-like behavior (multiple selection)
- Each option shows its `label` and `description`
- An additional "Other" text input for free-text answers

On submit: calls `respondToPendingRequest(sessionId, requestId, { request_type: 'ask_user_question', answers })` where `answers` maps each question's `question` text to the selected label(s) or free text.

#### Multi-tab behavior

When the user clicks a response button:
1. The button becomes disabled with a spinner
2. The WebSocket message is sent
3. The backend resolves the future and broadcasts a `process_state` without `pending_request`
4. All tabs receive this broadcast → the form disappears everywhere
5. The tab that sent the response sees the spinner briefly, then the form disappears when the broadcast arrives

This means: the response confirmation comes from the broadcast, not from the send. The sending tab doesn't get special treatment.

## Files impacted

| File | Changes |
|------|---------|
| `src/twicc/agent/process.py` | `can_use_tool` callback, pending request management, Future mechanism, always-streaming `_build_query_prompt`, dummy hook, `permission_mode` change |
| `src/twicc/agent/states.py` | `PendingRequest` dataclass, add to `ProcessInfo`, serialize |
| `src/twicc/agent/manager.py` | `resolve_pending_request()` method, timeout exemption for pending requests |
| `src/twicc/asgi.py` | New WebSocket message handler (`pending_request_response`) |
| `frontend/src/composables/useWebSocket.js` | Two new global send functions |
| `frontend/src/stores/data.js` | `pending_request` in process state, response actions, getter |
| `frontend/src/components/SessionItemsList.vue` | Pending request form display |
| `frontend/src/components/MessageInput.vue` | Hide when pending request active |
| Session list component | Visual indicator for pending requests |

## Implementation tasks

Sequential tasks to implement this feature. Each task is self-contained and can be tested before moving to the next.

### Task 1: PendingRequest dataclass and ProcessInfo serialization

Create the data structures and make them flow through the existing process state infrastructure. After this task, pending request data _could_ be serialized and broadcast — nothing creates one yet.

**Files:**
- `src/twicc/agent/states.py`:
  - Add `PendingRequest` dataclass (`request_id`, `request_type`, `tool_name`, `tool_input`, `created_at`)
  - Add `pending_request: PendingRequest | None = None` field to `ProcessInfo`
  - Update `serialize_process_info()` to include `pending_request` when present

**Verify:** Unit-level check that `serialize_process_info()` includes pending request data when set, and omits it when `None`.

---

### Task 2: `can_use_tool` callback and Future mechanism in ClaudeProcess

Wire up the SDK callback that creates a pending request, broadcasts it, and waits for a response. This is the core async rendez-vous mechanism.

**Files:**
- `src/twicc/agent/process.py`:
  - Add `_pending_request` and `_pending_request_future` instance variables to `__init__`
  - Add `_handle_pending_request()` async method (the `can_use_tool` callback): creates `PendingRequest`, sets the Future, calls `_notify_state_change()`, awaits the Future, clears state, calls `_notify_state_change()` again, returns the response
  - Add `resolve_pending_request()` method: sets the Future result, returns bool
  - Add `pending_request` property for external access
  - Add Future cancellation in `_handle_error()` and `kill()` death paths to avoid asyncio warnings
  - Update `get_info()` to pass `self._pending_request` to `ProcessInfo`

**Verify:** The callback can be called, creates a pending request, and `resolve_pending_request()` unblocks it. Death during pending request cancels the Future cleanly.

---

### Task 3: Always-streaming `_build_query_prompt` and dummy hook

The `can_use_tool` callback requires streaming mode in Python. Make `_build_query_prompt` always return an async generator and add the required dummy hook.

**Files:**
- `src/twicc/agent/process.py`:
  - Add module-level `_dummy_hook()` async function returning `{"continue_": True}`
  - Change `_build_query_prompt()`: remove the plain string path, always return an async generator (even for text-only messages)
  - Update `ClaudeAgentOptions` construction in `start()`: set `can_use_tool=self._handle_pending_request`, add `hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[_dummy_hook])]}`, change `permission_mode` from `"bypassPermissions"` to `"default"` (or configurable)

**Verify:** Process starts successfully with the new options. A text-only message still works through the async generator path. Multimodal messages still work.

---

### Task 4: ProcessManager — resolve method and timeout exemption

Give the manager the ability to route responses to the right process, and prevent timeout kills during pending requests.

**Files:**
- `src/twicc/agent/manager.py`:
  - Add `resolve_pending_request(session_id, response)` method: finds the process and calls `process.resolve_pending_request(response)`
  - Update `check_and_stop_timed_out_processes()`: skip processes where `process.pending_request is not None`

**Verify:** A process with a pending request is not killed by the timeout monitor. `resolve_pending_request` correctly routes to the process.

---

### Task 5: WebSocket handler for `pending_request_response`

Accept responses from the frontend and route them to the process manager.

**Files:**
- `src/twicc/asgi.py`:
  - Add `"pending_request_response"` case in `receive_json()`
  - Add `_handle_pending_request_response()` method: reads `request_type`, builds the appropriate `PermissionResultAllow` or `PermissionResultDeny`, calls `manager.resolve_pending_request()`

**Verify:** Sending a `pending_request_response` WebSocket message resolves the pending request on the process. The process continues execution after resolution.

---

### Task 6: Frontend store — pending request in process state

Make the frontend aware of pending requests through the existing process state flow.

**Files:**
- `frontend/src/stores/data.js`:
  - Update `setProcessState` to include `pending_request` from metadata (null if absent)
  - Add `respondToPendingRequest(sessionId, requestId, responseData)` action
  - Add `getPendingRequest(sessionId)` getter
- `frontend/src/composables/useWebSocket.js`:
  - Add exported `respondToPendingRequest()` function that sends the `pending_request_response` WebSocket message

**Verify:** When a `process_state` message arrives with `pending_request`, the store holds it. When a subsequent `process_state` arrives without it, it's cleared. The send function correctly builds and sends the WebSocket message.

---

### Task 7: Tool approval form UI

Display the approval form when Claude requests permission to use a tool.

**Files:**
- New component (e.g., `frontend/src/components/PendingRequestForm.vue`) or integrated in existing:
  - Tool approval variant: shows tool name, formatted parameters (command for Bash, file path for Write/Edit, raw JSON for others), Approve and Deny buttons, optional deny reason text input
  - Disable buttons with spinner after click, until `pending_request` clears from store
- `frontend/src/components/SessionItemsList.vue` (or parent):
  - Show `PendingRequestForm` when `getPendingRequest(sessionId)` is non-null with `request_type === "tool_approval"`
- `frontend/src/components/MessageInput.vue`:
  - Hide when a `pending_request` is active for the current session

**Verify:** When a tool approval pending request is active, the approval form appears instead of the message input. Clicking Approve/Deny sends the correct WebSocket message, buttons disable with spinner, and the form disappears when the process state updates.

---

### Task 8: Ask user question form UI

Display the question form when Claude asks clarifying questions.

**Files:**
- Same component as Task 7 (`PendingRequestForm.vue`):
  - Ask user question variant: for each question, display header + question text, options as selectable chips/buttons (radio for single, checkbox for multi), "Other" text input for free text, Submit button
  - Same disable-with-spinner pattern as tool approval
- `frontend/src/components/SessionItemsList.vue` (or parent):
  - Show `PendingRequestForm` when `request_type === "ask_user_question"`

**Verify:** When an ask-user-question pending request is active, the question form appears. Selecting options and submitting sends the correct `answers` mapping. Multi-select joins labels with `", "`. Free text is sent directly as the answer value.

---

### Task 9: Session list indicator

Show a visual indicator on sessions that have a pending request, so the user sees it immediately without opening the session.

**Files:**
- Session list component (sidebar):
  - When `processStates[sessionId]?.pending_request` is non-null, display an indicator (icon, badge, dot, color change...) on the session entry

**Verify:** A session with a pending request shows the indicator. The indicator disappears when the pending request is resolved or the process dies.

---

### Task 10: End-to-end testing

Test the full flow with a real Claude session.

- Start a session with `permission_mode="default"`
- Claude tries to use a tool → approval form appears
- Approve → Claude continues
- Claude tries another tool → deny → Claude adapts
- Claude asks a clarifying question → question form appears → answer → Claude continues
- Kill a process during a pending request → form disappears
- Reconnect WebSocket during a pending request → form reappears
- Open two tabs → answer from one → form disappears in both

## Task tracking

- [x] Task 1: PendingRequest dataclass and ProcessInfo serialization
- [x] Task 2: `can_use_tool` callback and Future mechanism in ClaudeProcess
- [x] Task 3: Always-streaming `_build_query_prompt` and dummy hook
- [x] Task 4: ProcessManager — resolve method and timeout exemption
- [x] Task 5: WebSocket handler for `pending_request_response`
- [x] Task 6: Frontend store — pending request in process state
- [x] Task 7: Tool approval form UI
- [x] Task 8: Ask user question form UI
- [x] Task 9: Session list indicator
- [ ] Task 10: End-to-end testing

## Decisions made during implementation

### Task 1
- **Used `@dataclass(frozen=True)` for `PendingRequest`**: The spec says `@dataclass` and CLAUDE.md prefers `NamedTuple` for immutable data. Used `frozen=True` to get immutability (matching the project's preference for immutable data containers) while still using `@dataclass` as the spec prescribes. This is appropriate because `PendingRequest` holds a `dict` field (`tool_input`) which `NamedTuple` would not protect from mutation either, and `@dataclass` provides clearer semantics for a data structure that is created, passed around, and discarded (not used as a dict key or in sets).

### Task 2
- **Extracted `_cancel_pending_request_future()` as a helper method**: Both `_handle_error()` and `kill()` need to cancel any active pending request Future. Rather than duplicating the cancel-and-clear logic, a small private method `_cancel_pending_request_future()` handles it. This method cancels the Future (if active and not already done), then clears both `_pending_request` and `_pending_request_future` to `None`.
- **Tests for ClaudeProcess pending request mechanism**: Added 18 tests covering `_handle_pending_request()`, `resolve_pending_request()`, `_cancel_pending_request_future()`, `kill()`, `_handle_error()`, `get_info()`, and the `pending_request` property. Since `pytest-asyncio` is not available in the project, async tests use `asyncio.run()` wrapping an inner `async def run()` coroutine. `AsyncMock` from `unittest.mock` is used for the state change callback where exact call tracking is not needed. Tests verify both the happy path and edge cases (no pending request, already-resolved Future, already-cancelled Future).

### Task 3
- **No decisions needed**: The implementation follows the spec exactly. `_build_query_prompt()` always returns an async generator, the `_dummy_hook` is a module-level async function, and `ClaudeAgentOptions` now uses `permission_mode="default"` with `can_use_tool` and `hooks` set. Added 6 tests covering `_dummy_hook` and the various `_build_query_prompt` paths (text-only, images, documents, both, and empty lists).

### Task 4
- **`resolve_pending_request()` is async but calls a sync method**: The spec defines the manager's `resolve_pending_request()` as `async` (the ASGI handler calls it with `await`), but the underlying `process.resolve_pending_request()` is synchronous. Kept it `async` as specified to match the expected usage from the ASGI handler and maintain consistency with other manager methods.
- **Timeout exemption checks `pending_request` before state**: The pending request check is placed at the top of the loop iteration, before any state-based logic. This means any process with a pending request is exempt regardless of its state, which is correct since a pending request indicates the SDK is blocked waiting for user input.

### Task 5
- **Used `manager.get_process_info()` instead of `manager._processes.get()`**: The spec accesses `manager._processes.get(session_id)` directly to retrieve the original questions for `ask_user_question` responses. Instead, used the existing public method `manager.get_process_info(session_id)` which returns a `ProcessInfo` containing the `pending_request` field. This avoids accessing a private attribute from outside the class.
- **Added validation for missing `session_id` and `request_type`**: The handler returns early with a warning log if either required field is missing, matching the validation pattern used by other handlers in `asgi.py` (e.g., `_handle_kill_process` validates `session_id`).
- **Tests use a `_FakeConsumer` pattern**: Since `UpdatesConsumer` requires Django Channels infrastructure (channel layer, scope, etc.) to instantiate, tests use a lightweight `_FakeConsumer` class that binds the real `_handle_pending_request_response` method via descriptor protocol. This exercises the actual handler code without needing a full WebSocket consumer setup.

### Task 6
- **Import alias to avoid name collision**: The store imports `respondToPendingRequest` from `useWebSocket.js` as `sendPendingRequestResponse` to avoid a naming collision with the store's own `respondToPendingRequest` action. The store action delegates to the WebSocket function as specified.
- **`setActiveProcesses` also includes `pending_request`**: The `setActiveProcesses` action (used on WebSocket connection to sync with backend) was updated to include `pending_request` from each process entry, matching the `setProcessState` changes. This ensures pending requests are restored on reconnection.
- **No frontend tests**: The project has no frontend test infrastructure (no vitest, no test files), and CLAUDE.md explicitly states "no tests and no linting" as an allowed shortcut.

### Task 7
- **Created a separate `PendingRequestForm.vue` component**: Rather than integrating the form directly into `SessionItemsList.vue`, created a dedicated component at `frontend/src/components/PendingRequestForm.vue`. This keeps `SessionItemsList.vue` (already large at ~1000 lines) focused on its current responsibilities, and the pending request form is self-contained with its own state management (responding flag, deny reason).
- **Two-step deny flow**: Clicking "Deny" first reveals an optional reason text input (with Cancel to go back), then clicking "Deny" again sends the denial. This avoids accidental denials and gives the user a chance to explain why, while keeping the default flow fast (no reason needed). The deny reason input supports Enter to submit and Escape to cancel.
- **Specialized formatting per tool type**: Bash shows `command` and `description` directly. Write shows `file_path` with collapsible content (showing line count). Edit shows `file_path` with collapsible diff-like view (old/new strings with colored borders). All other tools show raw JSON in a collapsible details section. This mirrors the spec's recommendation for readability.
- **`hasPendingRequest` computed hides MessageInput for any pending request type**: Although Task 7 only handles `tool_approval`, the `MessageInput` is hidden whenever any `pending_request` is present (including future `ask_user_question` from Task 8). This prevents showing the message input when Claude is waiting for a question answer, even before the question form UI is implemented.
- **No new Web Awesome imports needed**: All components used (`wa-button`, `wa-icon`, `wa-spinner`, `wa-details`, `wa-input`, `wa-badge`, `wa-divider`) are already imported in `main.js` or used without explicit import (like `wa-badge` in `MessageInput.vue`).

### Task 8
- **Unified `hasPendingRequest` computed replaces `hasToolApprovalRequest`**: Task 7 introduced `hasToolApprovalRequest` (checking for `request_type === 'tool_approval'`) and a separate `hasPendingRequest` (checking for non-null). With Task 8, the `PendingRequestForm` component handles both variants internally via `requestType` conditional rendering. The `v-if` in `SessionItemsList.vue` now uses `hasPendingRequest` directly, and the `hasToolApprovalRequest` computed was removed as it is no longer needed.
- **"Other" is mutually exclusive with predefined options for single-select, replaces all for multi-select**: For single-select questions, selecting "Other" clears the predefined selection, and selecting a predefined option deactivates "Other". For multi-select questions, activating "Other" clears all predefined selections (since mixing predefined options and free text would produce ambiguous answer values). The answer value is always either the selected label(s) or the free text, never a mix.
- **Reactive objects (`reactive({})`) for per-question state**: Used `reactive({})` for `questionSelections`, `otherTexts`, and `otherActive` instead of a single reactive array, since the number of questions is dynamic and the state for each question is independent. Keys are question indices (0-based). State is cleared by deleting all keys when the pending request changes.
- **Submit requires all questions answered**: The submit button is disabled until every question has an answer (either a predefined option selected or "Other" text typed). This prevents submitting incomplete responses to Claude.
- **Native `<button>` elements for option chips**: Used plain HTML `<button>` elements styled as chips rather than `wa-button` components. This provides more layout flexibility (each chip can display label + description stacked vertically) and avoids the complexity of customizing Web Awesome button internals. The chips use the same design token variables for consistency.

### Task 9
- **Placed indicator next to `ProcessIndicator` in the process info row**: The pending request indicator (a pulsing "hand" icon in warning/orange color) is placed immediately to the left of the existing `ProcessIndicator` in the third column of the process info grid row. This placement is natural because pending requests are a process-level concept, and the process info row is already the visual anchor for process state. A wrapper `<span class="process-indicator-cell">` groups both icons with a small gap.
- **Used `store.getPendingRequest(session.id)` for reactivity**: The indicator uses the store's `getPendingRequest` getter (which reads from `processStates[sessionId]?.pending_request`) to conditionally render. When the pending request is resolved or the process dies (clearing `processStates`), the indicator disappears reactively with no additional cleanup needed.
- **Pulsing animation for attention**: The hand icon uses a 1.5-second ease-in-out pulse animation (opacity oscillating between 1 and 0.3) to draw the user's eye without being overly distracting. This is similar to but distinct from the `ProcessIndicator`'s own pulse animation (which uses a 1-second cycle and different opacity range).
- **Tooltip describes the action needed**: The tooltip reads "Waiting for your response" to clearly communicate that user input is required, without over-specifying the type of pending request (tool approval vs. clarifying question).

## Resolved questions and doubts

(none so far)
