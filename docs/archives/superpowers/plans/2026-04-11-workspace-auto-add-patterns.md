# Workspace Auto-Add Project Patterns — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow workspaces to define path patterns that automatically add matching projects — via manual "Scan now" (frontend) or on new project detection (backend).

**Architecture:** Patterns are stored as `autoProjectPatterns: string[]` in the workspace JSON. Frontend handles "Scan now" (iterates all projects, matches patterns, adds to formData). Backend handles auto-add on new project detection (in sessions_watcher after project creation + directory resolution). An asyncio.Lock serializes workspace file writes.

**Tech Stack:** Python (re, asyncio.Lock), JavaScript (RegExp), Vue 3, existing DirectoryPickerPopup component.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/twicc/workspaces.py` | Modify | Add `match_pattern()`, `auto_add_project_to_workspaces()`, asyncio.Lock |
| `src/twicc/asgi.py` | Modify | Use workspace lock in `_handle_update_workspaces` |
| `src/twicc/sessions_watcher.py` | Modify | Call auto-add after new project + directory resolution |
| `frontend/src/utils/patternMatch.js` | Create | JS `matchPattern()` utility |
| `frontend/src/stores/workspaces.js` | Modify | Support `autoProjectPatterns` in create/update |
| `frontend/src/components/WorkspaceManageDialog.vue` | Modify | Patterns section UI, DirectoryPickerPopup, Scan now button |

---

### Task 1: Python pattern matching + locking (`workspaces.py`)

**Files:**
- Modify: `src/twicc/workspaces.py`

- [ ] **Step 1: Add `match_pattern` function**

```python
import re

def match_pattern(directory: str, pattern: str) -> bool:
    """Check if a directory path matches a pattern using * as wildcard."""
    effective = pattern if '*' in pattern else pattern.rstrip('/') + '/*'
    regex = re.compile(
        '^' + '.*'.join(re.escape(part) for part in effective.split('*')) + '$'
    )
    return regex.search(directory) is not None
```

- [ ] **Step 2: Add asyncio.Lock and auto-add function**

```python
import asyncio
from channels.layers import get_channel_layer
from asgiref.sync import sync_to_async

_workspaces_lock = asyncio.Lock()

async def auto_add_project_to_workspaces(project_id: str, directory: str) -> None:
    """Auto-add a project to workspaces whose patterns match its directory."""
    async with _workspaces_lock:
        data = await sync_to_async(read_workspaces)()
        workspaces = data.get("workspaces", [])
        modified = False
        for ws in workspaces:
            patterns = ws.get("autoProjectPatterns", [])
            if not patterns or project_id in ws.get("projectIds", []):
                continue
            if any(match_pattern(directory, p) for p in patterns):
                ws.setdefault("projectIds", []).append(project_id)
                modified = True
        if not modified:
            return
        await sync_to_async(write_workspaces)(data)
    # Broadcast outside lock
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "workspaces_updated", "workspaces": workspaces},
    })
```

---

### Task 2: Lock workspace writes in `asgi.py`

**Files:**
- Modify: `src/twicc/asgi.py` (lines 1217-1234, `_handle_update_workspaces`)

- [ ] **Step 1: Import and use lock**

Wrap the write in `_handle_update_workspaces` with `_workspaces_lock`:

```python
from twicc.workspaces import _workspaces_lock

async def _handle_update_workspaces(self, content: dict) -> None:
    workspaces = content.get("workspaces")
    if not isinstance(workspaces, list):
        return
    def _write():
        write_workspaces({"workspaces": workspaces})
    async with _workspaces_lock:
        await sync_to_async(_write)()
    # broadcast (outside lock)
    await self.channel_layer.group_send(...)
```

---

### Task 3: Auto-add hook in `sessions_watcher.py`

**Files:**
- Modify: `src/twicc/sessions_watcher.py` (end of `sync_and_broadcast`, ~line 530)

- [ ] **Step 1: Add auto-add call after new project processing**

At the end of `sync_and_broadcast`, after all processing:

```python
# Auto-add newly created project to workspaces matching patterns
if project_created and not is_subagent:
    # Reload project to get directory (set by ensure_project_directory during sync_session_items)
    project = await refresh_project(project)
    if project.directory:
        from twicc.workspaces import auto_add_project_to_workspaces
        await auto_add_project_to_workspaces(project.id, project.directory)
```

This is placed at the very end of `sync_and_broadcast`, before the final `return`. It only runs for newly created non-subagent projects that have a resolved directory.

---

### Task 4: JS pattern matching utility

**Files:**
- Create: `frontend/src/utils/patternMatch.js`

- [ ] **Step 1: Create matching function**

```javascript
/**
 * Check if a directory path matches a pattern using * as wildcard.
 * If pattern has no *, it's treated as a directory prefix (appends /*).
 */
export function matchPattern(directory, pattern) {
  const effective = pattern.includes('*') ? pattern : pattern.replace(/\/+$/, '') + '/*'
  const regex = new RegExp(
    '^' + effective.split('*').map(s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*') + '$'
  )
  return regex.test(directory)
}
```

---

### Task 5: Frontend store changes

**Files:**
- Modify: `frontend/src/stores/workspaces.js`

- [ ] **Step 1: Support `autoProjectPatterns` in create/update actions**

In `createWorkspace`: include `autoProjectPatterns` in the new workspace object.
In `updateWorkspace`: support updating `autoProjectPatterns`.

---

### Task 6: WorkspaceManageDialog UI

**Files:**
- Modify: `frontend/src/components/WorkspaceManageDialog.vue`

- [ ] **Step 1: Add `autoProjectPatterns` to formData and form logic**

Add `autoProjectPatterns: []` to formData. Add `patternInput` ref. Add `addPattern()`, `removePattern(index)`, `scanNow()` functions.

- [ ] **Step 2: Add DirectoryPickerPopup for pattern input**

Reuse DirectoryPickerPopup with a computed v-model that:
- get: returns patternInput if it has no `*`, else `''`
- set: updates patternInput with selected directory

- [ ] **Step 3: Build patterns section in template**

After the projects section, add:
- Section header "Auto-add project patterns"
- List of patterns with remove buttons
- Input row: text input + DirectoryPickerPopup + Add button
- "Scan now" button with feedback message

- [ ] **Step 4: Implement scanNow()**

```javascript
function scanNow() {
  const dataStore = useDataStore()
  const projects = dataStore.getProjects
  let added = 0
  for (const project of projects) {
    if (!project.directory || formData.projectIds.includes(project.id)) continue
    const matches = formData.autoProjectPatterns.some(p => matchPattern(project.directory, p))
    if (matches) {
      formData.projectIds.push(project.id)
      added++
    }
  }
  scanFeedback.value = added > 0
    ? `${added} project${added > 1 ? 's' : ''} added`
    : 'No new projects found'
  // Clear feedback after a few seconds
  setTimeout(() => { scanFeedback.value = '' }, 4000)
}
```

---

### Task 7: Commit

- [ ] `git commit -m "feat: add auto-add project patterns to workspaces"`
