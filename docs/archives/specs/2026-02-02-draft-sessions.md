# Draft Sessions

**Date:** 2026-02-02
**Status:** COMPLETED

## Overview

Allow users to create new Claude sessions from the frontend with a client-generated UUID. The session exists locally as a "draft" until the first message is sent, at which point it's created on the backend.

## Concept

A **draft session** is a regular session object in `store.sessions` with a `draft: true` flag. It's treated like any other session everywhere, with specific handling:

- No API fetch for its items (there are none)
- Tag "Draft" displayed before the title (sidebar + header)
- Simplified header (no meta line)
- Sidebar: no meta line
- "Cancel" button in MessageInput to delete the draft
- First message sends `send_message` (backend auto-detects new session)

When `session_added` WebSocket message arrives with the same ID, the session is updated (overwrites `draft`).

## Backend

### WebSocket Message: `send_message`

**Format (frontend → backend):**
```json
{
    "type": "send_message",
    "session_id": "uuid-generated-by-frontend-or-existing",
    "project_id": "proj-xyz",
    "text": "The message text"
}
```

The backend automatically detects whether to create a new session or resume an existing one by checking if `session_id` exists in the database.

### Files Modified

| File | Changes |
|------|---------|
| `src/twicc/asgi.py` | `_handle_send_message()` checks session existence and calls appropriate method |
| `src/twicc/agent/manager.py` | Methods `resume_session()` and `new_session()` + factorized `_start_process()` |
| `src/twicc/agent/process.py` | `start()` accepts `resume: bool` parameter |

### Implementation Details

**`asgi.py` - `_handle_send_message()`:**
- Validates `session_id`, `project_id`, `text`
- Gets project directory from database
- Checks if session exists in database via `session_exists()`
- If exists: calls `manager.resume_session(session_id, project_id, cwd, text)`
- If not exists: calls `manager.new_session(session_id, project_id, cwd, text)`

**`manager.py` - `resume_session()`:**
- Resumes an existing session
- Cleans up dead process if exists
- Calls `_start_process(session_id, project_id, cwd, text, resume=True)`

**`manager.py` - `new_session()`:**
- Creates a new session with the client-provided UUID
- Rejects if session already exists and is active
- Cleans up dead process if exists
- Calls `_start_process(session_id, project_id, cwd, text, resume=False)`

**`manager.py` - `_start_process()`:**
- Factorized from `resume_session` and `new_session`
- Creates `ClaudeProcess`, registers it, broadcasts starting state
- Calls `process.start(text, callback, resume=resume)`

**`process.py` - `start(prompt, on_state_change, resume=True)`:**
- If `resume=True`: uses `ClaudeAgentOptions(resume=session_id)`
- If `resume=False`: uses `ClaudeAgentOptions(extra_args={"session-id": session_id})`

The `--session-id <uuid>` CLI flag allows specifying a custom UUID for new sessions.

## Frontend

### Files to Modify

| File | Changes |
|------|---------|
| `stores/data.js` | Actions `createDraftSession()` and `deleteDraftSession()` |
| `composables/useWebSocket.js` | Function `newSession()` + handle `session_added` for drafts |
| `components/ProjectView.vue` | Floating button "New session" |
| `components/SessionList.vue` | Tag "Draft" + no meta line if `draft` |
| `views/SessionView.vue` | Skip item fetch if `draft` |
| `components/SessionHeader.vue` | Tag "Draft" + no meta line if `draft` |
| `components/MessageInput.vue` | "Cancel" button if `draft` + send `new_session` |

### Store (`data.js`)

**Action `createDraftSession(projectId)`:**
```javascript
createDraftSession(projectId) {
    const id = crypto.randomUUID()
    const now = Date.now() / 1000  // Unix timestamp in seconds
    this.sessions[id] = {
        id,
        project_id: projectId,
        title: 'New session',
        mtime: now,
        last_line: 0,
        draft: true,
    }
    return id
}
```

**Action `deleteDraftSession(sessionId)`:**
```javascript
deleteDraftSession(sessionId) {
    if (this.sessions[sessionId]?.draft) {
        delete this.sessions[sessionId]
    }
}
```

### WebSocket (`useWebSocket.js`)

**Handle `session_added` for drafts:**
```javascript
case 'session_added':
    const existingSession = store.sessions[msg.session.id]
    if (existingSession?.draft) {
        // Draft confirmed by backend - update with real data
        store.updateSession(msg.session)
    } else if (store.areProjectSessionsFetched(msg.session.project_id) || ...) {
        store.addSession(msg.session)
    }
    break
```

### ProjectView.vue - Floating Button

- Visible only when a specific `projectId` is selected (not "all projects" mode)
- On click:
```javascript
const sessionId = store.createDraftSession(projectId)
router.push({ name: 'session', params: { projectId, sessionId } })
```

### SessionList.vue

For each session:
- If `session.draft`: display tag "Draft" + title, NO meta line
- Otherwise: normal display (meta with message count, cost, time)

### SessionView.vue

- If `session.draft`: do NOT fetch items (none exist)
- Empty message list displays normally
- MessageInput is active and functional

### SessionHeader.vue

If `session.draft`:
- Tag "Draft" before the title
- Title = "New session"
- No meta line (skip session-meta entirely)

### MessageInput.vue

**Cancel button:**
- If `session.draft`: show "Cancel" button to the left of "Send"
- On click: `store.deleteDraftSession(sessionId)` + `router.push()` to project

**Message send:**
- Always call `sendWsMessage({ type: 'send_message', ... })`
- Backend automatically detects new vs existing session

## User Flow

1. User is on a project page
2. Clicks floating "New session" button
3. Frontend generates UUID, creates draft session in store
4. Navigates to `/projects/{projectId}/sessions/{sessionId}`
5. Page displays with:
   - Header: "Draft" tag + "New session" title, no meta
   - Empty message list
   - MessageInput with Cancel and Send buttons
6. User types message and clicks Send
7. Frontend sends `send_message` WebSocket message
8. Backend creates session, JSONL watcher detects file
9. Backend sends `session_added` WebSocket message
10. Frontend updates session (removes `draft` flag)
11. Session is now a normal session
