# Archive Projects Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to archive projects, hiding them from the UI by default (home, selectors, session lists), with a toggle to reveal them.

**Architecture:** Adds `archived` boolean field to the Project model (mirroring Session.archived). Backend exposes PATCH endpoint. Frontend filters archived projects in all views and selectors, with a persisted `showArchivedProjects` setting toggle on the home page.

**Tech Stack:** Django models + migration, Django views (PATCH), Vue 3 (Composition API), Pinia stores, Web Awesome components.

**No tests or linting** per project policy.

---

### Task 1: Backend — Model field + migration

**Files:**
- Modify: `src/twicc/core/models.py:51-79` (Project model)

**Step 1: Add archived field to Project model**

In `src/twicc/core/models.py`, add the `archived` field to the `Project` model, after the `color` field (line 61) and before `total_cost` (line 62):

```python
    archived = models.BooleanField(default=False)  # User can archive projects to hide from default list
```

**Step 2: Create migration**

Run:
```bash
cd /home/twidi/dev/twicc-poc && uv run python -m django makemigrations core
```

**Step 3: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/migrations/
git commit -m "feat: add archived field to Project model"
```

---

### Task 2: Backend — Serializer

**Files:**
- Modify: `src/twicc/core/serializers.py:17-29` (serialize_project)

**Step 1: Add archived to serializer**

In `serialize_project()`, add `"archived"` to the returned dict, after `"color"`:

```python
        "archived": project.archived,
```

**Step 2: Commit**

```bash
git add src/twicc/core/serializers.py
git commit -m "feat: serialize project archived field"
```

---

### Task 3: Backend — PATCH endpoint for project

**Files:**
- Modify: `src/twicc/views.py:156-187` (project_detail view)

**Step 1: Add PATCH support to project_detail**

The current `project_detail` handles GET and PUT. Add PATCH handling for `archived`. The view should also broadcast `project_updated` via WebSocket (the current PUT handler does not broadcast — but PATCH for archived should, since it changes visibility).

Replace the `project_detail` function body. The new function should:

1. Keep the existing GET and PUT logic unchanged
2. Add an `elif request.method == "PATCH"` block that:
   - Parses JSON body
   - Validates `archived` is a boolean
   - Saves `project.archived` with `update_fields=["archived"]`
   - Broadcasts `project_updated` via WebSocket (same pattern as `session_detail` PATCH)
   - Returns `serialize_project(project)`

```python
def project_detail(request, project_id):
    """GET/PUT/PATCH /api/projects/<id>/ - Detail of a project, update name/color, or archive."""
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        raise Http404("Project not found")

    if request.method == "PUT":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        # Update allowed fields only
        if "name" in data:
            name = data["name"]
            if name is not None:
                name = name.strip()
                if not name:
                    # Empty after strip means no name
                    name = None
                elif len(name) > 25:
                    return JsonResponse({"error": "Name must be 25 characters or less"}, status=400)
                elif Project.objects.filter(name=name).exclude(id=project_id).exists():
                    return JsonResponse({"error": "A project with this name already exists"}, status=400)
            project.name = name
        if "color" in data:
            project.color = data["color"]

        project.save(update_fields=["name", "color"])

    elif request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        if "archived" in data:
            archived = data["archived"]
            if not isinstance(archived, bool):
                return JsonResponse({"error": "archived must be a boolean"}, status=400)
            project.archived = archived
            project.save(update_fields=["archived"])

            # Broadcast project_updated via WebSocket
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                "updates",
                {
                    "type": "broadcast",
                    "data": {
                        "type": "project_updated",
                        "project": serialize_project(project),
                    },
                },
            )

    return JsonResponse(serialize_project(project))
```

**Step 2: Commit**

```bash
git add src/twicc/views.py
git commit -m "feat: add PATCH endpoint for project archived field"
```

---

### Task 4: Frontend — Settings store (showArchivedProjects)

**Files:**
- Modify: `frontend/src/stores/settings.js`

**Step 1: Add setting to schema, validator, getter, action, and watch**

In `SETTINGS_SCHEMA` (around line 33, after `showArchivedSessions`), add:
```javascript
    showArchivedProjects: false,
```

In `SETTINGS_VALIDATORS` (around line 71, after `showArchivedSessions` validator), add:
```javascript
    showArchivedProjects: (v) => typeof v === 'boolean',
```

In the `getters` section (around line 160, after `isShowArchivedSessions`), add:
```javascript
        isShowArchivedProjects: (state) => state.showArchivedProjects,
```

In the `actions` section (after `setShowArchivedSessions` around line 341), add:
```javascript
        /**
         * Set show archived projects mode.
         * This setting is not exposed in the settings panel — it is only
         * toggled from the home page project list.
         * @param {boolean} enabled
         */
        setShowArchivedProjects(enabled) {
            if (SETTINGS_VALIDATORS.showArchivedProjects(enabled)) {
                this.showArchivedProjects = enabled
            }
        },
```

In the `watch()` block inside `initSettings()` (around line 499, after `showArchivedSessions`), add:
```javascript
            showArchivedProjects: store.showArchivedProjects,
```

**Step 2: Commit**

```bash
git add frontend/src/stores/settings.js
git commit -m "feat: add showArchivedProjects setting"
```

---

### Task 5: Frontend — Data store action (setProjectArchived)

**Files:**
- Modify: `frontend/src/stores/data.js`

**Step 1: Add setProjectArchived action**

Add this action in `data.js`, right after the `updateProject` action (around line 432). It follows the exact same pattern as `setSessionArchived` (lines 1642-1699):

```javascript
        /**
         * Set the archived state of a project.
         * @param {string} projectId - The project ID
         * @param {boolean} archived - Whether to archive or unarchive
         * @throws {Error} If the update fails
         */
        async setProjectArchived(projectId, archived) {
            // Optimistic update
            const project = this.projects[projectId]
            const oldArchived = project?.archived

            if (project) {
                project.archived = archived
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ archived })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update project')
                }

                const updatedProject = await response.json()
                this.$patch({ projects: { [projectId]: updatedProject } })

            } catch (error) {
                // Rollback on error
                if (project && oldArchived !== undefined) {
                    project.archived = oldArchived
                }
                throw error
            }
        },
```

**Step 2: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat: add setProjectArchived action to data store"
```

---

### Task 6: Frontend — ProjectList.vue (filtering + archive button + toggle)

**Files:**
- Modify: `frontend/src/components/ProjectList.vue`

**Step 1: Add filtering logic and archive button**

In the `<script setup>`:

1. The `settingsStore` is already imported (line 17). Add a computed for `showArchivedProjects`:

```javascript
// Show archived projects setting
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)
```

2. Modify `namedProjects` computed (line 42-43) to filter archived:

```javascript
const namedProjects = computed(() =>
    store.getProjects.filter(p => p.name !== null && (showArchivedProjects.value || !p.archived))
)
```

3. Modify `treeRoots` computed (line 47-50) to filter archived:

```javascript
const treeRoots = computed(() => {
    const unnamed = store.getProjects.filter(p => p.name === null && (showArchivedProjects.value || !p.archived))
    return buildProjectTree(unnamed)
})
```

4. Add a `handleArchiveClick` function:

```javascript
function handleArchiveClick(event, project) {
    event.stopPropagation()
    store.setProjectArchived(project.id, !project.archived)
}
```

5. Add a `handleToggleShowArchived` function:

```javascript
function handleToggleShowArchived(event) {
    settingsStore.setShowArchivedProjects(event.target.checked)
}
```

6. Add a new `handleTreeArchiveClick` function (similar to `handleTreeEditClick`):

```javascript
function handleTreeArchiveClick(project) {
    store.setProjectArchived(project.id, !project.archived)
}
```

In the `<template>`:

1. Add an "Arch." tag in the named project cards, inside `.project-title-row` after `ProjectProcessIndicator`:

```vue
<wa-tag v-if="project.archived" variant="neutral" size="small" class="archived-tag">Arch.</wa-tag>
```

2. Add an archive button next to the edit button in named project cards. Inside the `<wa-card>` for named projects, after the edit button and its tooltip (lines 93-103):

```vue
                    <wa-button
                        :id="`archive-button-${project.id}`"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="archive-button"
                        @click="(e) => handleArchiveClick(e, project)"
                    >
                        <wa-icon :name="project.archived ? 'arrow-counterclockwise' : 'archive'"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="`archive-button-${project.id}`">{{ project.archived ? 'Unarchive project' : 'Archive project' }}</AppTooltip>
```

3. Add the "Show archived projects" toggle before the project list sections. Right after `<div class="project-list">` opening tag:

```vue
        <wa-switch
            size="small"
            class="show-archived-toggle"
            :checked="showArchivedProjects"
            @wa-change="handleToggleShowArchived"
        >
            Show archived projects
        </wa-switch>
```

4. Pass the `archive` event from `ProjectTreeNode`:

```vue
            <ProjectTreeNode
                v-for="root in treeRoots"
                :key="root.project ? root.project.id : root.segment"
                :node="root"
                @select="handleSelect"
                @edit="handleTreeEditClick"
                @archive="handleTreeArchiveClick"
            />
```

In the `<style scoped>`:

Add styles for the archive button and toggle:

```css
.archive-button {
    position: absolute;
    top: calc(var(--spacing) / 2);
    right: calc(var(--spacing) / 2 + 2em);
}

.archived-tag {
    flex-shrink: 0;
}

.show-archived-toggle {
    align-self: flex-end;
    font-size: var(--wa-font-size-s);
}
```

**Step 2: Commit**

```bash
git add frontend/src/components/ProjectList.vue
git commit -m "feat: archive button, toggle, and filtering on home page"
```

---

### Task 7: Frontend — ProjectTreeNode.vue (archive button)

**Files:**
- Modify: `frontend/src/components/ProjectTreeNode.vue`

**Step 1: Add archive button and event handling**

In `<script setup>`:

1. Update `defineEmits` (line 31) to include `archive`:

```javascript
const emit = defineEmits(['select', 'edit', 'archive'])
```

2. Add handler functions:

```javascript
function handleArchiveClick(event, proj) {
    event.stopPropagation()
    emit('archive', proj)
}

function onChildArchive(proj) {
    emit('archive', proj)
}
```

In `<template>`:

1. Add "Arch." tag in `.project-title-row` after `ProjectProcessIndicator` (line 113):

```vue
<wa-tag v-if="project.archived" variant="neutral" size="small" class="archived-tag">Arch.</wa-tag>
```

2. Add archive button after the edit button and its tooltip (lines 115-125):

```vue
                <wa-button
                    :id="`archive-button-${project.id}`"
                    variant="neutral"
                    appearance="plain"
                    size="small"
                    class="archive-button"
                    @click="(e) => handleArchiveClick(e, project)"
                >
                    <wa-icon :name="project.archived ? 'arrow-counterclockwise' : 'archive'"></wa-icon>
                </wa-button>
                <AppTooltip :for="`archive-button-${project.id}`">{{ project.archived ? 'Unarchive project' : 'Archive project' }}</AppTooltip>
```

3. On the recursive `<ProjectTreeNode>` (line 155-161), add `@archive="onChildArchive"`:

```vue
            <ProjectTreeNode
                v-for="child in node.children"
                :key="child.project ? child.project.id : `folder-${child.segment}`"
                :node="child"
                @select="onChildSelect"
                @edit="onChildEdit"
                @archive="onChildArchive"
            />
```

In `<style scoped>`:

Add:
```css
.archive-button {
    position: absolute;
    top: calc(var(--spacing) / 2);
    right: calc(var(--spacing) / 2 + 2em);
}

.archived-tag {
    flex-shrink: 0;
}
```

And update `.project-title-row` padding-right to accommodate both buttons:
```css
.project-title-row {
    /* ... existing styles ... */
    padding-right: calc(var(--wa-space-s) + 3.5em);
}
```

Also update in `ProjectList.vue` the `.project-title-row` padding-right to the same value.

**Step 2: Commit**

```bash
git add frontend/src/components/ProjectTreeNode.vue
git commit -m "feat: archive button on project tree nodes"
```

---

### Task 8: Frontend — ProjectView.vue (selector filtering + session filtering)

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

**Step 1: Add archived project filtering in selectors**

1. Add a computed for the setting (near line 208, after `showArchivedSessions`):

```javascript
const showArchivedProjects = computed(() => settingsStore.isShowArchivedProjects)
```

2. Modify `allProjects` computed (line 163) to filter archived:

```javascript
const allProjects = computed(() =>
    store.getProjects.filter(p => showArchivedProjects.value || !p.archived)
)
```

3. Modify `nonStaleProjects` computed (line 173) to **always** exclude archived:

```javascript
const nonStaleProjects = computed(() => allProjects.value.filter(p => !p.stale && !p.archived))
```

Note: `nonStaleProjects` feeds the "new session" dropdowns. Archived projects should **always** be excluded from these, regardless of the toggle.

**Step 2: Add session filtering for archived projects in all-projects mode**

The `SessionList` component already receives `:show-archived="showArchivedSessions"` as a prop (line 774) and filters `session.archived`. For filtering sessions of archived **projects**, we need to also filter in the session list.

The cleanest approach: modify `SessionList.vue`'s `allSessions` computed to also accept and use a project-level filter. Add a new prop `showArchivedProjects`:

In `frontend/src/components/SessionList.vue`:

1. Add the prop (after `showArchived` around line 33):

```javascript
    showArchivedProjects: {
        type: Boolean,
        default: true
    }
```

2. Modify the `allSessions` computed (lines 60-68) to filter sessions of archived projects:

```javascript
const allSessions = computed(() => {
    const baseSessions = props.projectId === ALL_PROJECTS_ID
        ? store.getAllSessions
        : store.getProjectSessions(props.projectId)

    return baseSessions.filter(s =>
        (props.showArchived || !s.archived || s.id === props.sessionId) &&
        (props.showArchivedProjects || !store.getProject(s.project_id)?.archived)
    )
})
```

In `frontend/src/views/ProjectView.vue`, pass the prop to `SessionList` (around line 774):

```vue
                    :show-archived-projects="showArchivedProjects"
```

**Step 3: Commit**

```bash
git add frontend/src/views/ProjectView.vue frontend/src/components/SessionList.vue
git commit -m "feat: filter archived projects from selectors and session lists"
```

---

### Task 9: Frontend — Import wa-switch in main.js (if needed)

**Files:**
- Modify: `frontend/src/main.js` (only if `wa-switch` is not already imported)

**Step 1: Check if wa-switch is imported**

Search for `switch` import in `frontend/src/main.js`. If not present, add:

```javascript
import '@awesome.me/webawesome/dist/components/switch/switch.js'
```

**Step 2: Commit (if change needed)**

```bash
git add frontend/src/main.js
git commit -m "feat: import wa-switch component"
```

---

### Task 10: Remind user to run migration + restart

After all tasks are complete, remind the user to:

1. Run the Django migration: `uv run python -m django migrate`
2. Restart the dev servers: `uv run ./devctl.py restart`

---
