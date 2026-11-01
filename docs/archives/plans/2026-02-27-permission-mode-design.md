# Permission Mode — Design Document

## Goal

Make the Claude Agent SDK `permission_mode` configurable per session through the TwiCC UI. Currently hard-coded to `"bypassPermissions"`, this change allows users to choose the permission mode when creating or resuming a session, and persists the choice in the database for session continuity.

## Permission Modes

The SDK defines `PermissionMode` as a `Literal` with these values:

| Mode | SDK value | Description |
|------|-----------|-------------|
| Default | `"default"` | Prompts for permission on first use of each tool |
| Accept Edits | `"acceptEdits"` | Auto-accepts file edit permissions |
| Plan | `"plan"` | Read-only — Claude can analyze but not modify files |
| Don't Ask | `"dontAsk"` | Auto-denies tools unless pre-approved via permission rules |
| Bypass | `"bypassPermissions"` | Skips all permission prompts |

Note: `dontAsk` may not be in the installed SDK yet (only 4 modes in the current `PermissionMode` type). TwiCC will support all 5 in the UI and pass the value as-is to the SDK.

## Architecture

### 1. Backend — Session Model

Add a `permission_mode` field to `Session`:

```python
permission_mode = models.CharField(max_length=30, default="default")
```

- Default value: `"default"` (the SDK's standard behavior)
- No choices constraint — we store the raw string value to be forward-compatible with new SDK modes
- Requires a Django migration

### 2. Backend — Serializer

Add `permission_mode` to `serialize_session()` output so the frontend receives it.

### 3. Backend — Process Start

Replace the hard-coded `permission_mode="bypassPermissions"` in `ClaudeProcess.start()` with a parameter:

- Add `permission_mode: str` parameter to `ClaudeProcess.__init__()` (stored as instance attribute)
- Use `self.permission_mode` in `ClaudeAgentOptions` construction
- Thread the parameter through `ProcessManager._start_process()` → `create_session()` / `send_to_session()`

### 4. Backend — WebSocket Consumer

In `_handle_send_message()`:
- Extract `permission_mode` from the WS payload
- Pass it to `ProcessManager.create_session()` / `send_to_session()`
- On session creation: the session doesn't exist yet in DB — the watcher will create it. We need to update `permission_mode` after the session is created by the watcher, or set it directly in `_handle_send_message` when we know it's a new session.
- On session resume: update `session.permission_mode` in DB with the value from the payload

**Approach for new sessions:** Since the session doesn't exist in DB when `_handle_send_message` is called for new sessions, and the file watcher creates the `Session` row when the JSONL file appears, we have two options:
1. Store a "pending permission_mode" like we do for titles, then apply it when the session is created
2. Let the watcher create the session with default, then update immediately after

**Decision:** Option 2 is simpler. After calling `manager.create_session()`, we can do a DB update `Session.objects.filter(id=session_id).update(permission_mode=...)`. Since the watcher may not have created the row yet, we retry with a small delay, or we store the pending value and apply it in the watcher. Actually, the cleanest approach is to store it as a "pending" value (like `set_pending_title`) and have the watcher (or a post-creation hook) apply it.

**Revised decision:** Even simpler — we update the DB synchronously right after the session is created by the manager. If the session row doesn't exist yet (watcher hasn't processed it), the update silently does nothing (`rows_affected=0`), and we can retry once after a short delay. But this is fragile.

**Final decision:** Follow the existing `set_pending_title` pattern. Create `set_pending_permission_mode(session_id, mode)` and `get_pending_permission_mode(session_id)` functions. The watcher applies it when it creates the session row. For existing sessions (resume), we update directly in DB since the row already exists.

### 5. Backend — Permission Suggestion "setMode"

When a `PermissionUpdate` with `type="setMode"` is accepted by the user (via `updated_permissions` in the pending request response), the SDK applies it internally. But TwiCC also needs to persist the new mode in the database so that future resumes use the correct mode.

In `_handle_pending_request_response()` in `asgi.py`, after constructing the `PermissionResultAllow`:
- Check `updated_permissions` for any `setMode` entries
- If found, update `Session.permission_mode` in DB with the new mode value

### 6. Frontend — Settings Store

Add `defaultPermissionMode: 'default'` to `SETTINGS_SCHEMA` in `settings.js`. Add corresponding validator, getter, and setter.

### 7. Frontend — Settings UI

Add a `wa-select` dropdown in the "Sessions" section of `SettingsPopover.vue` for choosing the default permission mode for new sessions.

### 8. Frontend — MessageInput Component

Add a `wa-select` dropdown to the left of the Send/Cancel buttons in the toolbar:

- **Source of the selected value:**
  - New session (draft): pre-selected with `settings.defaultPermissionMode`
  - Existing session: pre-selected with `session.permission_mode` (from serializer/store)
- **Disabled state:** Disabled when the session has an active process (any state except `dead` or no process)
- **Enabled state:** Enabled when no process is running (new session, or existing session without active process)
- **On send:** The current dropdown value is included in the `send_message` WS payload as `permission_mode`

### 9. Frontend — Data Store

The `permission_mode` field from the serializer is already handled generically (session data is merged into the store). No special action needed beyond ensuring the `wa-select` reads from `session.permission_mode`.

## Data Flow

### New Session
1. User selects permission mode in MessageInput dropdown (defaults from Settings)
2. User clicks Send → WS payload includes `permission_mode`
3. Backend `_handle_send_message` stores pending permission mode, passes mode to ProcessManager
4. ProcessManager creates ClaudeProcess with the mode → SDK uses it
5. Watcher creates Session row → applies pending permission mode
6. Session serialized back to frontend with `permission_mode` field

### Resume Session
1. Session loads from DB with `permission_mode` → serialized to frontend
2. MessageInput dropdown pre-selected with `session.permission_mode`
3. User may change the dropdown value before sending
4. User clicks Send → WS payload includes `permission_mode`
5. Backend updates `session.permission_mode` in DB, passes mode to ProcessManager
6. ProcessManager creates ClaudeProcess with the mode → SDK uses it

### setMode Suggestion Accepted
1. Claude suggests `setMode` via `permission_suggestions`
2. User checks the suggestion and approves the pending request
3. Backend receives `updated_permissions` with `setMode` entry
4. Backend updates `session.permission_mode` in DB
5. Updated session serialized to frontend → dropdown reflects new mode
