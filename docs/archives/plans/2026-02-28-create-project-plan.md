# Create Project — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow users to create a new project from the home page by providing a filesystem directory path, with optional name and color.

**Architecture:** Extend the existing `project_list` view to handle POST requests for project creation. Extend the existing `ProjectEditDialog.vue` to support a create mode (no `project` prop). Add a "New Project" button to `HomeView.vue`.

**Tech Stack:** Django (backend view), Vue 3 + Web Awesome (frontend dialog), Pinia (store), Django Channels (WebSocket broadcast)

---

### Task 1: Backend — Add POST handler to `project_list` view

**Files:**
- Modify: `src/twicc/views.py` (the `project_list` function, lines 69-73)

**Step 1: Add the POST branch to `project_list`**

In `src/twicc/views.py`, add `import re` at the top (after the existing `import os`), then replace the `project_list` function with:

```python
def project_list(request):
    """GET /api/projects/ - List all projects.
    POST /api/projects/ - Create a new project from a directory path.
    """
    if request.method == "POST":
        return _create_project(request)

    projects = Project.objects.all()
    data = [serialize_project(p) for p in projects]
    return JsonResponse(data, safe=False)
```

Then add the `_create_project` helper function right after `project_list`:

```python
def _create_project(request):
    """Create a new project from a directory path.

    Body: { "directory": "/absolute/path", "name": "optional", "color": "optional" }
    """
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    # 1. Extract and validate directory
    directory = data.get("directory")
    if not directory or not isinstance(directory, str):
        return JsonResponse({"error": "directory is required"}, status=400)

    resolved = os.path.realpath(directory)
    if not os.path.isabs(resolved):
        return JsonResponse({"error": "Directory must be an absolute path"}, status=400)
    if not os.path.isdir(resolved):
        return JsonResponse({"error": "Directory does not exist or is not a directory"}, status=400)

    # 2. Generate project ID: all non-alphanumeric chars become dashes
    project_id = re.sub(r'[^a-zA-Z0-9]', '-', resolved)

    # 3. Check project doesn't already exist
    if Project.objects.filter(id=project_id).exists():
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    # 4. Validate optional name
    name = data.get("name")
    if name is not None:
        name = name.strip()
        if not name:
            name = None
        elif len(name) > 25:
            return JsonResponse({"error": "Name must be 25 characters or less"}, status=400)
        elif Project.objects.filter(name=name).exists():
            return JsonResponse({"error": "A project with this name already exists"}, status=409)

    # 5. Validate optional color
    color = data.get("color")
    if color is not None and not isinstance(color, str):
        color = None

    # 6. Create project
    project = Project.objects.create(
        id=project_id,
        directory=resolved,
        name=name,
        color=color or None,
    )

    # 7. Broadcast via WebSocket
    from asgiref.sync import async_to_sync
    from channels.layers import get_channel_layer
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "project_added",
                "project": serialize_project(project),
            },
        },
    )

    return JsonResponse(serialize_project(project), status=201)
```

**Step 2: Verify the import is present**

Ensure `import re` is added near the top of the file, after `import os`.

**Step 3: Test manually with curl**

```bash
# Should return 201 with the created project
curl -X POST http://localhost:3500/api/projects/ \
  -H 'Content-Type: application/json' \
  -d '{"directory": "/tmp"}'

# Should return 409 (already exists)
curl -X POST http://localhost:3500/api/projects/ \
  -H 'Content-Type: application/json' \
  -d '{"directory": "/tmp"}'

# Should return 400 (doesn't exist)
curl -X POST http://localhost:3500/api/projects/ \
  -H 'Content-Type: application/json' \
  -d '{"directory": "/nonexistent/path"}'
```

**Step 4: Commit**

```bash
git add src/twicc/views.py
git commit -m "feat: add POST /api/projects/ endpoint for project creation"
```

---

### Task 2: Frontend — Extend `ProjectEditDialog.vue` for create mode

**Files:**
- Modify: `frontend/src/components/ProjectEditDialog.vue`

The dialog currently works only in edit mode (when `project` prop is provided). We extend it to also work in create mode (when `project` is null/absent).

**Step 1: Add create-mode state and computed properties**

In the `<script setup>` section, add:

- A `localDirectory` ref for the directory input (create mode only)
- A `directoryInputRef` ref for DOM access
- A computed `isCreateMode` based on whether `project` is null
- Modify `syncFormState` to also handle the directory input and the form ID + button label
- Modify `focusNameInput` → `focusFirstInput` to focus directory input in create mode, name input in edit mode

**Step 2: Modify `handleSave` to branch on mode**

In create mode:
- Validate directory is non-empty (after trim)
- POST to `/api/projects/` with `{ directory, name, color }`
- On success: `store.addProject(createdProject)`, emit `'saved'`, close, then navigate to the project

For the navigation after creation, use a lazy import of the router:
```javascript
const { router } = await import('../router')
router.push({ name: 'project', params: { projectId: createdProject.id } })
```

In edit mode: keep existing PUT logic unchanged.

**Step 3: Update the template**

- Dialog label: `isCreateMode ? 'New Project' : 'Edit Project'`
- In create mode: show a `wa-input` for directory instead of the read-only ID/Directory info blocks
- Submit button text: `isCreateMode ? 'Create' : 'Save'`
- Form `id`: use `'project-create-form'` in create mode, `'project-edit-form'` in edit mode (to avoid conflicts if both were ever open)
- In create mode, the `v-if="project"` on the form must be changed to always show the form

**Step 4: Add `open()` method reset for create mode**

When `open()` is called and we're in create mode, reset `localDirectory`, `localName`, `localColor`, and `errorMessage`.

**Step 5: Commit**

```bash
git add frontend/src/components/ProjectEditDialog.vue
git commit -m "feat: extend ProjectEditDialog to support create mode"
```

---

### Task 3: Frontend — Add "New Project" button to `HomeView.vue`

**Files:**
- Modify: `frontend/src/views/HomeView.vue`

**Step 1: Import and set up the dialog**

Add the import for `ProjectEditDialog` and a template ref:

```javascript
import ProjectEditDialog from '../components/ProjectEditDialog.vue'

const createDialogRef = ref(null)

function openCreateDialog() {
    createDialogRef.value?.open()
}

function handleProjectCreated(project) {
    router.push({ name: 'project', params: { projectId: project.id } })
}
```

**Step 2: Add the button and dialog to the template**

In the `.home-header`, add a "New Project" button before the "All sessions" button. Both should be pushed right together. The "New Project" button should have `margin-left: auto` and the "All sessions" button loses its `margin-left: auto`.

```html
<wa-button class="new-project-button" variant="neutral" appearance="outlined" size="small" @click="openCreateDialog">
    <wa-icon slot="prefix" name="plus-lg"></wa-icon>
    New project
</wa-button>
```

Add the dialog component at the end of the template (no `project` prop = create mode):

```html
<ProjectEditDialog ref="createDialogRef" @saved="handleProjectCreated" />
```

**Step 3: Update CSS**

Move `margin-left: auto` from `.view-all-button` to `.new-project-button`:

```css
.new-project-button {
    margin-left: auto;
}

.view-all-button {
    /* margin-left: auto removed */
}
```

**Step 4: Commit**

```bash
git add frontend/src/views/HomeView.vue
git commit -m "feat: add New Project button to home page"
```

---

### Task 4: Integration test — Manual verification

**Step 1: Restart the backend** (remind user)

The backend needs a restart to pick up the new view code.

**Step 2: Verify end-to-end flow**

1. Open the home page
2. Click "New Project" → dialog opens with "New Project" title, directory input, name, color
3. Enter a valid directory path (e.g. `/tmp`) → click "Create"
4. Verify: redirected to the new project page
5. Go back to home → verify the project appears in the list
6. Try creating with the same directory → verify 409 error displayed
7. Try a non-existent directory → verify 400 error displayed
8. Try creating with a name that's already taken → verify error

**Step 3: Verify WebSocket broadcast**

Open the app in two browser tabs. Create a project in one tab. Verify it appears in the other tab's project list.

**Step 4: Final commit (if any fixes needed)**

```bash
git add <fixed-files>
git commit -m "fix: address issues found during integration testing"
```
