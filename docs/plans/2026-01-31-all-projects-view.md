# All Projects View - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an "All Projects" mode to view sessions from all projects in one list, with easy switching between projects.

**Architecture:** Reuse `ProjectView.vue` with a special project ID `__all__` to indicate "all projects" mode. The plural `/projects` URL prefix distinguishes this mode from single-project view. Backend provides a new endpoint to fetch all sessions at once.

**Tech Stack:** Django (new API endpoint), Vue.js (router + store + components)

---

## Task 1: Backend - New API endpoint for all sessions

**Files:**
- Modify: `src/twicc/urls.py`
- Modify: `src/twicc/views.py`

**Step 1: Add route in urls.py**

Add before the project routes (line 8):

```python
path("api/sessions/", views.all_sessions),
```

**Step 2: Add view in views.py**

Add after `project_list` function (around line 23):

```python
def all_sessions(request):
    """GET /api/sessions/ - All sessions from all projects.

    Returns only regular sessions (not subagents).
    """
    sessions = Session.objects.filter(type=SessionType.SESSION)
    data = [serialize_session(s) for s in sessions]
    return JsonResponse(data, safe=False)
```

**Step 3: Commit**

```bash
git add src/twicc/urls.py src/twicc/views.py
git commit -m "$(cat <<'EOF'
feat(api): add endpoint to fetch all sessions

GET /api/sessions/ returns sessions from all projects.
Used by the "All Projects" view in the frontend.

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Store - Add constant and getter/action for all sessions

**Files:**
- Modify: `frontend/src/stores/data.js`

**Step 1: Add constant at top of file (after imports, around line 7)**

```javascript
// Special project ID for "All Projects" mode
export const ALL_PROJECTS_ID = '__all__'
```

**Step 2: Add getter after `getProjectSessions` (around line 58)**

```javascript
getAllSessions: (state) =>
    Object.values(state.sessions)
        .filter(s => !s.parent_session_id)
        .sort((a, b) => b.mtime - a.mtime),
```

**Step 3: Add action after `loadSessions` (around line 390)**

```javascript
/**
 * Load all sessions from all projects.
 * @param {Object} options
 * @param {boolean} options.isInitialLoading - If true, enables UI feedback
 * @returns {Promise<void>}
 */
async loadAllSessions({ isInitialLoading = false } = {}) {
    // Use a special project ID for "all projects" state
    const projectId = ALL_PROJECTS_ID

    // Initialize localState for this pseudo-project if needed
    if (!this.localState.projects[projectId]) {
        this.localState.projects[projectId] = {}
    }
    this.localState.projects[projectId].sessionsLoading = true

    try {
        const res = await fetch('/api/sessions/')
        if (!res.ok) {
            console.error('Failed to load all sessions:', res.status, res.statusText)
            if (isInitialLoading) {
                this.localState.projects[projectId].sessionsLoadingError = true
            }
            return
        }
        const freshSessions = await res.json()
        for (const fresh of freshSessions) {
            this.sessions[fresh.id] = fresh
        }
        // Mark as fetched and clear any previous error
        this.localState.projects[projectId].sessionsFetched = true
        this.localState.projects[projectId].sessionsLoadingError = false
    } catch (error) {
        console.error('Failed to load all sessions:', error)
        if (isInitialLoading) {
            this.localState.projects[projectId].sessionsLoadingError = true
        }
        throw error
    } finally {
        this.localState.projects[projectId].sessionsLoading = false
    }
},
```

**Step 4: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "$(cat <<'EOF'
feat(store): add ALL_PROJECTS_ID constant and loadAllSessions action

- Export ALL_PROJECTS_ID = '__all__' for "All Projects" mode
- Add getAllSessions getter (all sessions sorted by mtime)
- Add loadAllSessions() action to fetch from /api/sessions/

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Router - Add /projects routes

**Files:**
- Modify: `frontend/src/router.js`

**Step 1: Add new routes for "all projects" mode**

Add after the existing `/project/:projectId` route block (after line 26):

```javascript
// "All Projects" mode routes (note: plural "projects")
{
    path: '/projects',
    component: ProjectView,
    children: [
        { path: '', name: 'projects-all', component: null },
        {
            path: ':projectId/session/:sessionId',
            name: 'projects-session',
            component: SessionView,
            children: [
                {
                    path: 'subagent/:subagentId',
                    name: 'projects-session-subagent'
                }
            ]
        }
    ]
}
```

**Step 2: Commit**

```bash
git add frontend/src/router.js
git commit -m "$(cat <<'EOF'
feat(router): add /projects routes for All Projects mode

- /projects -> projects-all (list all sessions)
- /projects/:projectId/session/:sessionId -> projects-session
- Plural "projects" distinguishes from single project mode

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: ProjectView - Support All Projects mode

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

**Step 1: Import the constant and add computed for mode detection**

Update imports (around line 4):

```javascript
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
```

Add after `sessionId` computed (around line 16):

```javascript
// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() =>
    route.name === 'projects-all' ||
    route.name === 'projects-session' ||
    route.name === 'projects-session-subagent'
)

// Effective project ID for store operations
const effectiveProjectId = computed(() =>
    isAllProjectsMode.value ? ALL_PROJECTS_ID : projectId.value
)
```

**Step 2: Update the watch to handle both modes**

Replace the existing watch (lines 25-29):

```javascript
// Load sessions when project changes or mode changes
watch([effectiveProjectId, isAllProjectsMode], async ([newProjectId, isAllMode]) => {
    if (isAllMode) {
        await store.loadAllSessions({ isInitialLoading: true })
    } else if (newProjectId) {
        await store.loadSessions(newProjectId, { isInitialLoading: true })
    }
}, { immediate: true })
```

**Step 3: Update loading/error state computeds**

Replace lines 21-22:

```javascript
const areSessionsLoading = computed(() => store.areSessionsLoading(effectiveProjectId.value))
const didSessionsFailToLoad = computed(() => store.didSessionsFailToLoad(effectiveProjectId.value))
```

**Step 4: Update handleRetry**

Replace the function (lines 32-36):

```javascript
async function handleRetry() {
    if (isAllProjectsMode.value) {
        await store.loadAllSessions({ isInitialLoading: true })
    } else if (projectId.value) {
        await store.loadSessions(projectId.value, { isInitialLoading: true })
    }
}
```

**Step 5: Update handleProjectChange to support switching to/from All Projects**

Replace the function (lines 39-44):

```javascript
function handleProjectChange(event) {
    const newProjectId = event.target.value
    if (newProjectId === ALL_PROJECTS_ID) {
        router.push({ name: 'projects-all' })
    } else if (newProjectId && newProjectId !== projectId.value) {
        router.push({ name: 'project', params: { projectId: newProjectId } })
    }
}
```

**Step 6: Update handleSessionSelect to use correct route based on mode**

Replace the function (lines 47-52):

```javascript
function handleSessionSelect(session) {
    if (isAllProjectsMode.value) {
        router.push({
            name: 'projects-session',
            params: { projectId: session.project_id, sessionId: session.id }
        })
    } else {
        router.push({
            name: 'session',
            params: { projectId: projectId.value, sessionId: session.id }
        })
    }
}
```

**Step 7: Update template - selector with All Projects option**

Replace the `<wa-select>` block (lines 122-135):

```vue
<wa-select
    :value.attr="isAllProjectsMode ? ALL_PROJECTS_ID : projectId"
    @change="handleProjectChange"
    class="project-selector"
    size="small"
>
    <wa-option :value="ALL_PROJECTS_ID">
        All Projects
    </wa-option>
    <wa-divider></wa-divider>
    <wa-option
        v-for="p in allProjects"
        :key="p.id"
        :value="p.id"
    >
        {{ p.id }}
    </wa-option>
</wa-select>
```

**Step 8: Update SessionList props**

Replace the SessionList component (lines 157-162):

```vue
<SessionList
    v-else
    :project-id="effectiveProjectId"
    :session-id="sessionId"
    :show-project-name="isAllProjectsMode"
    @select="handleSessionSelect"
/>
```

**Step 9: Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "$(cat <<'EOF'
feat(ProjectView): support All Projects mode

- Detect mode from route name (projects-all, projects-session)
- Add "All Projects" option at top of selector with divider
- Switch between loadAllSessions() and loadSessions() based on mode
- Pass show-project-name prop to SessionList in All Projects mode

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: SessionList - Support showing project name and All Projects mode

**Files:**
- Modify: `frontend/src/components/SessionList.vue`

**Step 1: Update props and imports**

Add import (after line 3):

```javascript
import { ALL_PROJECTS_ID } from '../stores/data'
```

Update props (replace lines 6-15):

```javascript
const props = defineProps({
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        default: null
    },
    showProjectName: {
        type: Boolean,
        default: false
    }
})
```

**Step 2: Update sessions computed to handle All Projects mode**

Replace the sessions computed (lines 20-22):

```javascript
const sessions = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID) {
        return store.getAllSessions
    }
    return store.getProjectSessions(props.projectId)
})
```

**Step 3: Add template for project name display**

Add after `.session-name` div (line 53), before `.session-meta`:

```vue
<div v-if="showProjectName" class="session-project">{{ session.project_id }}</div>
```

**Step 4: Add CSS for project name**

Add after `.session-name` styles (after line 94):

```css
.session-project {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    /* Truncate with ellipsis */
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    margin-top: var(--wa-space-3xs);
}
```

**Step 5: Commit**

```bash
git add frontend/src/components/SessionList.vue
git commit -m "$(cat <<'EOF'
feat(SessionList): support All Projects mode

- Add showProjectName prop to display project ID between name and meta
- Use getAllSessions getter when projectId is ALL_PROJECTS_ID
- Style project name similar to session meta

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: SessionView - Support /projects routes

**Files:**
- Modify: `frontend/src/views/SessionView.vue`

**Step 1: Add computed for All Projects mode detection**

Add after `subagentId` computed (around line 17):

```javascript
// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() =>
    route.name === 'projects-session' ||
    route.name === 'projects-session-subagent'
)
```

**Step 2: Update navigation functions to use correct route names**

Replace `onTabShow` function (lines 48-76):

```javascript
function onTabShow(event) {
    const panel = event.detail?.name
    if (!panel) return

    // Ignore if already on this tab (avoid infinite loop)
    if (panel === activeTabId.value) return

    if (panel === 'main') {
        // Navigate to session without subagent
        router.push({
            name: isAllProjectsMode.value ? 'projects-session' : 'session',
            params: {
                projectId: projectId.value,
                sessionId: sessionId.value
            }
        })
    } else if (panel.startsWith('agent-')) {
        // Navigate to subagent
        const agentId = panel.replace('agent-', '')
        router.push({
            name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
            params: {
                projectId: projectId.value,
                sessionId: sessionId.value,
                subagentId: agentId
            }
        })
    }
}
```

Replace navigation in `closeTab` function. The two `router.push` calls (around lines 95-102 and 104-111) should become:

```javascript
// First router.push (going to previous subagent tab):
router.push({
    name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
    params: {
        projectId: projectId.value,
        sessionId: sessionId.value,
        subagentId: prevTab.agentId
    }
})

// Second router.push (going to main):
router.push({
    name: isAllProjectsMode.value ? 'projects-session' : 'session',
    params: {
        projectId: projectId.value,
        sessionId: sessionId.value
    }
})
```

**Step 3: Commit**

```bash
git add frontend/src/views/SessionView.vue
git commit -m "$(cat <<'EOF'
feat(SessionView): support /projects routes for All Projects mode

- Detect mode from route name
- Use projects-session/projects-session-subagent routes when in All Projects mode
- Maintain correct navigation when switching tabs

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Final verification and cleanup

**Step 1: Start dev servers and test**

```bash
uv run ./devctl.py start all
```

**Step 2: Manual testing checklist**

- [ ] Navigate to `/projects` - should show all sessions from all projects
- [ ] Selector shows "All Projects" selected with divider below
- [ ] Each session shows its project name between title and meta
- [ ] Click a session - URL becomes `/projects/:projectId/session/:sessionId`
- [ ] Switch to a specific project in selector - URL becomes `/project/:projectId`
- [ ] Sessions list now shows only that project's sessions
- [ ] Project name no longer shown in session items
- [ ] Refresh page on `/projects` - stays on All Projects view
- [ ] Refresh page on `/projects/:projectId/session/:sessionId` - stays on that session in All Projects mode

**Step 3: Final commit if any fixes needed**

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Backend API endpoint | urls.py, views.py |
| 2 | Store constant + getter + action | stores/data.js |
| 3 | Router routes | router.js |
| 4 | ProjectView All Projects support | ProjectView.vue |
| 5 | SessionList project name display | SessionList.vue |
| 6 | SessionView route handling | SessionView.vue |
| 7 | Testing and verification | - |
