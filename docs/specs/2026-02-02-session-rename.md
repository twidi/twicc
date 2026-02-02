# Session Rename

**Date:** 2026-02-02
**Status:** DRAFT

## Overview

Allow users to rename sessions from the UI. Since Claude CLI doesn't provide a direct rename API, we achieve this by writing a `custom-title` entry to the session's JSONL file - the same mechanism Claude CLI uses internally.

## Concept

When a user renames a session:
1. The title is immediately updated in the database (instant UI feedback)
2. A `custom-title` entry is appended to the JSONL file
3. The Watcher detects the change and syncs (no-op since DB already has the correct title)

This ensures consistency: if the user opens Claude CLI directly, they see the same title.

### The Challenge: Concurrent Writes

The JSONL file may be actively written to by Claude during `assistant_turn`. Writing to the file at the same time could corrupt it. We must only write when it's safe:

- **Safe states**: No process, `user_turn`, `dead`
- **Unsafe states**: `starting`, `assistant_turn`

When unsafe, we defer the write until the process transitions to a safe state.

## Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           FLUX DE RENOMMAGE                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  User renomme "Toto"                                                    │
│         │                                                               │
│         ▼                                                               │
│  ┌─────────────────────────┐                                            │
│  │  API: PATCH session     │                                            │
│  │  session.title = "Toto" │  ◄── Mise à jour DB immédiate              │
│  │  + Broadcast WS         │                                            │
│  └─────────────────────────┘                                            │
│         │                                                               │
│         ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  Process actif ?                                                │    │
│  │  ET state in (starting, assistant_turn) ?                       │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│         │                                                               │
│    NON  │                        OUI                                    │
│   (pas de process,               │                                      │
│    ou user_turn,                 │                                      │
│    ou dead)                      │                                      │
│         │                        │                                      │
│         ▼                        ▼                                      │
│  ┌──────────────────┐    ┌────────────────────────────┐                 │
│  │ Écrire JSONL     │    │ pending_titles[session_id] │                 │
│  │ immédiatement    │    │      = "Toto"              │                 │
│  └──────────────────┘    └────────────────────────────┘                 │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                    PROCESS MANAGER - STATE CHANGES                      │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  _on_state_change(session_id, new_state)                                │
│         │                                                               │
│         ▼                                                               │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │  new_state in (user_turn, dead) ?                               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│         │                                                               │
│    NON  │                        OUI                                    │
│   (starting,                     │                                      │
│    assistant_turn)               │                                      │
│         │                        │                                      │
│         ▼                        ▼                                      │
│      (rien)              ┌────────────────────────────┐                 │
│                          │ session_id in pending ?    │                 │
│                          └────────────────────────────┘                 │
│                                  │                                      │
│                             NON  │  OUI                                 │
│                                  │   │                                  │
│                                  ▼   ▼                                  │
│                              (rien)  ┌────────────────────────────┐     │
│                                      │ title = pending.pop(id)   │     │
│                                      │ Écrire JSONL              │     │
│                                      └────────────────────────────┘     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Cases Covered

| Situation | Comportement |
|-----------|--------------|
| Pas de process | Écriture JSONL immédiate |
| Process en `user_turn` | Écriture JSONL immédiate |
| Process en `dead` | Écriture JSONL immédiate |
| Process en `starting` | Stocke dans dict, attend |
| Process en `assistant_turn` | Stocke dans dict, attend |
| → puis passe en `user_turn` | ProcessManager écrit le JSONL |
| → puis passe en `dead` (fin normale) | ProcessManager écrit le JSONL |
| → user kill le process (→ dead) | ProcessManager écrit le JSONL |
| → crash du process (→ dead) | ProcessManager écrit le JSONL |
| Backend restart pendant attente | **Perdu** - titre OK en DB, pas dans JSONL (edge case rare, accepté) |

## API Choice: REST vs WebSocket

**REST API (PATCH)** is preferred because:
- Pattern already established for `project_detail` (PUT)
- Synchronous response with the updated title
- Simpler to implement and test
- WebSocket doesn't return a direct response

WebSocket is only used to **broadcast** the session update (via `session_updated`) so other clients see the change.

## Backend Implementation

### New Module: `src/twicc/titles.py`

This module handles reading/writing custom-title entries (consistency with existing code in `sync.py` that reads custom-title items).

```python
"""
Session title management - writing custom-title entries to JSONL files.

This module handles writing custom-title entries to session JSONL files.
Reading is handled by sync.py which parses CUSTOM_TITLE items.
"""

import json
from pathlib import Path

from django.conf import settings

from twicc.core.models import Session

# Global dict for pending titles
# Used when a process is in starting/assistant_turn
_pending_titles: dict[str, str] = {}


def get_session_jsonl_path(session: Session) -> Path:
    """Get the JSONL file path for a session."""
    return Path(settings.CLAUDE_PROJECTS_DIR) / session.project_id / f"{session.id}.jsonl"


def write_custom_title_to_jsonl(session_id: str, title: str) -> None:
    """Write a custom-title entry to the session's JSONL file.

    The entry format matches what Claude CLI uses:
    {"type": "custom-title", "customTitle": "..."}
    """
    from twicc.core.models import Session

    session = Session.objects.get(id=session_id)
    jsonl_path = get_session_jsonl_path(session)

    entry = {"type": "custom-title", "customTitle": title}

    with open(jsonl_path, 'a', encoding='utf-8') as f:
        f.write(json.dumps(entry) + '\n')


def set_pending_title(session_id: str, title: str) -> None:
    """Store a title to be written when the process becomes safe."""
    _pending_titles[session_id] = title


def pop_pending_title(session_id: str) -> str | None:
    """Get and remove a pending title for a session."""
    return _pending_titles.pop(session_id, None)


def flush_pending_title(session_id: str) -> None:
    """Write pending title to JSONL if one exists.

    Called by ProcessManager when process transitions to user_turn or dead.
    """
    title = pop_pending_title(session_id)
    if title:
        write_custom_title_to_jsonl(session_id, title)
```

### Modification: `src/twicc/agent/manager.py`

Add call to `flush_pending_title` in `_on_state_change`:

```python
# In _on_state_change, after broadcast and before dead process cleanup:

from twicc.titles import flush_pending_title

async def _on_state_change(self, process: ClaudeProcess) -> None:
    """Handle process state change..."""
    info = process.get_info()

    # ... existing broadcast code ...

    # Flush pending title when process becomes safe to write
    if process.state in (ProcessState.USER_TURN, ProcessState.DEAD):
        # Run sync function in thread pool since file I/O
        await asyncio.to_thread(flush_pending_title, process.session_id)

    # ... existing dead process cleanup code ...
```

### Modification: `src/twicc/views.py`

Add PATCH support to `session_detail`:

```python
from twicc.titles import (
    set_pending_title,
    write_custom_title_to_jsonl,
)
from twicc.agent.manager import get_process_manager
from twicc.agent.states import ProcessState


def session_detail(request, project_id, session_id, parent_session_id=None):
    """GET/PATCH /api/projects/<id>/sessions/<session_id>/ - Detail or rename session."""
    try:
        session = Session.objects.get(id=session_id, project_id=project_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    # Validate parent_session_id
    if parent_session_id is not None:
        if session.parent_session_id != parent_session_id:
            raise Http404("Subagent not found for this parent session")
    else:
        if session.parent_session_id is not None:
            raise Http404("Session not found")

    if request.method == "PATCH":
        # Reject subagents (cannot be renamed)
        if session.type == SessionType.SUBAGENT:
            return JsonResponse({"error": "Subagents cannot be renamed"}, status=400)

        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        if "title" not in data:
            return JsonResponse({"error": "title field required"}, status=400)

        title = data["title"]
        if title is not None:
            title = title.strip()
            if not title:
                return JsonResponse({"error": "Title cannot be empty"}, status=400)
            if len(title) > 200:
                return JsonResponse({"error": "Title must be 200 characters or less"}, status=400)

        # 1. Update DB immediately
        session.title = title
        session.save(update_fields=["title"])

        # 2. Write to JSONL (immediate or deferred)
        manager = get_process_manager()
        process_info = manager.get_process_info(session_id)

        if process_info and process_info.state in (ProcessState.STARTING, ProcessState.ASSISTANT_TURN):
            # Process is busy, defer write
            set_pending_title(session_id, title)
        else:
            # Safe to write immediately (no process, user_turn, or dead)
            write_custom_title_to_jsonl(session_id, title)

        # 3. Broadcast will happen via Watcher when JSONL changes
        # But we also broadcast immediately for instant UI update
        # (handled by the view returning the updated session)

    return JsonResponse(serialize_session(session))
```

### URL Route

The route already exists (`session_detail`), we just add PATCH support in the view.

## Frontend Implementation

### New Component: `SessionRenameDialog.vue`

```vue
<script setup>
import { ref, watch, nextTick } from 'vue'
import { useDataStore } from '../stores/data'

const props = defineProps({
    session: {
        type: Object,
        default: null,
    },
})

const emit = defineEmits(['saved'])

const store = useDataStore()

const dialogRef = ref(null)
const titleInputRef = ref(null)

const localTitle = ref('')
const isSaving = ref(false)
const errorMessage = ref('')

// Sync form values when session changes
watch(
    () => props.session,
    (newSession) => {
        if (newSession) {
            localTitle.value = newSession.title || ''
        }
    },
    { immediate: true }
)

function syncFormState() {
    nextTick(() => {
        if (titleInputRef.value) {
            titleInputRef.value.value = localTitle.value
        }
    })
}

function focusTitleInput() {
    const input = titleInputRef.value
    if (!input) return
    input.focus()
    const len = input.value?.length || 0
    input.setSelectionRange(len, len)
}

function open() {
    errorMessage.value = ''
    syncFormState()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
}

function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
}

function onTitleInput(event) {
    localTitle.value = event.target.value
}

async function handleSave() {
    if (!props.session) return

    const trimmedTitle = localTitle.value.trim()

    if (!trimmedTitle) {
        errorMessage.value = 'Title cannot be empty'
        return
    }

    if (trimmedTitle.length > 200) {
        errorMessage.value = 'Title must be 200 characters or less'
        return
    }

    isSaving.value = true
    errorMessage.value = ''

    try {
        await store.renameSession(
            props.session.project_id,
            props.session.id,
            trimmedTitle
        )
        emit('saved')
        close()
    } catch (error) {
        errorMessage.value = error.message || 'Failed to rename session'
    } finally {
        isSaving.value = false
    }
}

defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        label="Rename Session"
        class="session-rename-dialog"
        @wa-show="syncFormState"
        @wa-after-show="focusTitleInput"
    >
        <form v-if="session" id="session-rename-form" class="dialog-content" @submit.prevent="handleSave">
            <div class="form-group">
                <label class="form-label">Title</label>
                <wa-input
                    ref="titleInputRef"
                    :value.prop="localTitle"
                    @input="onTitleInput"
                    placeholder="Session title"
                    maxlength="200"
                ></wa-input>
                <div class="form-hint">Max 200 characters</div>
            </div>

            <wa-callout v-if="errorMessage" variant="danger" size="small">
                {{ errorMessage }}
            </wa-callout>
        </form>

        <div slot="footer" class="dialog-footer">
            <wa-button variant="neutral" appearance="outlined" @click="close" :disabled="isSaving">
                Cancel
            </wa-button>
            <wa-button type="submit" form="session-rename-form" variant="brand" :disabled="isSaving">
                <wa-spinner v-if="isSaving" slot="prefix"></wa-spinner>
                Save
            </wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.session-rename-dialog {
    --width: min(500px, calc(100vw - 2rem));
}

.dialog-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.form-group {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.form-label {
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
}

.form-hint {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.dialog-footer {
    display: flex;
    gap: var(--wa-space-s);
    justify-content: flex-end;
}
</style>
```

### Modification: `SessionHeader.vue`

Add the pencil button and dialog:

```vue
<script setup>
// ... existing imports ...
import SessionRenameDialog from './SessionRenameDialog.vue'

// ... existing code ...

const renameDialogRef = ref(null)

function openRenameDialog() {
    renameDialogRef.value?.open()
}
</script>

<template>
    <header class="session-header" v-if="session">
        <div v-if="mode === 'session'" class="session-title">
            <wa-tag v-if="session.draft" size="small" variant="warning" class="draft-tag">Draft</wa-tag>
            <h2 id="session-header-title">{{ displayName }}</h2>
            <wa-tooltip for="session-header-title">{{ displayName }}</wa-tooltip>

            <!-- Rename button (not for drafts or subagents) -->
            <wa-button
                v-if="!session.draft && mode === 'session'"
                id="session-header-rename-button"
                variant="neutral"
                appearance="plain"
                size="small"
                class="rename-button"
                @click="openRenameDialog"
            >
                <wa-icon name="pencil" label="Rename"></wa-icon>
            </wa-button>
            <wa-tooltip for="session-header-rename-button">Rename session</wa-tooltip>

            <ProjectBadge v-if="session.project_id" :project-id="session.project_id" class="session-project" />
        </div>

        <!-- ... rest of template ... -->

        <!-- Rename dialog -->
        <SessionRenameDialog
            ref="renameDialogRef"
            :session="session"
        />
    </header>
    <!-- ... -->
</template>

<style scoped>
/* ... existing styles ... */

.rename-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.rename-button:hover {
    opacity: 1;
}
</style>
```

### Modification: `stores/data.js`

Add the `renameSession` action:

```javascript
// In actions:

/**
 * Rename a session.
 * @param {string} projectId - The project ID
 * @param {string} sessionId - The session ID
 * @param {string} newTitle - The new title
 * @throws {Error} If the rename fails
 */
async renameSession(projectId, sessionId, newTitle) {
    // Optimistic update
    const session = this.sessions[sessionId]
    const oldTitle = session?.title

    if (session) {
        session.title = newTitle
    }

    try {
        const response = await fetch(
            `/api/projects/${projectId}/sessions/${sessionId}/`,
            {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ title: newTitle })
            }
        )

        if (!response.ok) {
            const data = await response.json()
            throw new Error(data.error || 'Failed to rename session')
        }

        const updatedSession = await response.json()
        this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

    } catch (error) {
        // Rollback on error
        if (session && oldTitle !== undefined) {
            session.title = oldTitle
        }
        throw error
    }
}
```

## Files Summary

| File | Action |
|------|--------|
| `src/twicc/titles.py` | **Create** - Pending titles management and JSONL writing |
| `src/twicc/views.py` | **Modify** - Add PATCH support in `session_detail` |
| `src/twicc/agent/manager.py` | **Modify** - Call `flush_pending_title` in `_on_state_change` |
| `frontend/src/components/SessionRenameDialog.vue` | **Create** - Rename dialog |
| `frontend/src/components/SessionHeader.vue` | **Modify** - Add pencil button + dialog |
| `frontend/src/stores/data.js` | **Modify** - Add `renameSession` action |

## Key Points

1. **Safe state to write**: `user_turn` or `dead` (or no process)
2. **Unsafe state**: `starting` or `assistant_turn` → store in `_pending_titles`
3. **Automatic flush**: ProcessManager calls `flush_pending_title` when it detects `user_turn` or `dead`
4. **Kill process**: Transitions to `dead` → flush happens automatically
5. **No subagents**: Button is hidden for subagents, and backend rejects PATCH
6. **No drafts**: Button is hidden for drafts (title will be the first message)
7. **Validation**: trim + non-empty + max 200 characters
8. **Accepted edge case**: If backend restarts during `starting/assistant_turn`, the title is lost in JSONL but remains correct in DB
