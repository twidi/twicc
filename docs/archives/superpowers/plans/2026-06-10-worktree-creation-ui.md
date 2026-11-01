# Worktree Creation from the UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a git worktree (and its auto-linked TwiCC project) from the "New session" dropdowns, then open a draft session in it.

**Architecture:** A compact button on git-project rows in both "New session" dropdowns opens a new `WorktreeCreateDialog` (branch + absolute path + optional start-from). The dialog POSTs to a new `POST /api/projects/{id}/worktrees/` endpoint, which runs `git worktree add` (new helper in `git.py`) then registers the directory via the existing `register_project()` with a new explicit `worktree_of_id` parameter — the row is born linked to its parent, and workspace auto-add runs as usual. A small `GET /api/projects/{id}/branches/` endpoint feeds branch autocompletion.

**Tech Stack:** Django 6 async views + orjson (backend), Vue 3 `<script setup>` + Web Awesome 3 (frontend).

**Spec:** `docs/superpowers/specs/2026-06-09-worktree-creation-ui-design.md` — read it first; it is the authority on behavior.

**Project policies that apply (from CLAUDE.md):**
- No tests, no linting — verification is manual by the user.
- All code/UI strings/comments in English.
- Do NOT restart the dev servers and do NOT run `uvx`/`uv-publish` — server restart is reserved to the user; remind them at the end.
- Git: never `git add -A`; list files explicitly.

---

### Task 1: git helpers — `create_worktree` and `get_worktree_branches`

**Files:**
- Modify: `src/twicc/git.py`

`src/twicc/git.py` centralizes every git subprocess call. Existing conventions to follow: `subprocess.run([...], capture_output=True, text=True, timeout=...)`, never raise on git failure, module-level `_GIT_TIMEOUT` for fast read commands. Look at `get_branches` (line ~180) as the style reference.

- [ ] **Step 1: Add `get_worktree_branches`** (place it near `get_branches`)

```python
def get_worktree_branches(git_directory: str) -> set[str]:
    """Return the set of local branch names currently checked out in any
    worktree of the repo (including the main checkout).

    Detached-HEAD worktrees emit a ``detached`` line instead of a
    ``branch`` line in the porcelain output and are skipped.
    """
    try:
        result = subprocess.run(
            ["git", "-C", git_directory, "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return set()
    if result.returncode != 0:
        return set()
    prefix = "branch refs/heads/"
    return {
        line[len(prefix):]
        for line in result.stdout.splitlines()
        if line.startswith(prefix)
    }
```

- [ ] **Step 2: Add `create_worktree`** (right after `get_worktree_branches`)

Note the dedicated, longer timeout: `git worktree add` materializes a full checkout, which can take far longer than the read-only commands covered by `_GIT_TIMEOUT`. Define `_WORKTREE_ADD_TIMEOUT = 60` next to `_GIT_TIMEOUT`.

```python
def create_worktree(
    repo_root: str,
    path: str,
    branch: str,
    start_point: str | None = None,
) -> tuple[bool, str]:
    """Create a git worktree at ``path``.

    If ``branch`` is an existing local branch it is checked out into the new
    worktree (``start_point`` is ignored); otherwise the branch is created
    with ``-b``, from ``start_point`` when given, else from the repo's HEAD.

    Returns ``(True, "")`` on success, ``(False, <git error>)`` on failure.
    Git's own stderr is relayed verbatim — git remains the authority on
    git-level validation (branch already checked out, non-empty target, ...).
    """
    if branch in get_branches(repo_root):
        args = ["git", "-C", repo_root, "worktree", "add", path, branch]
    else:
        args = ["git", "-C", repo_root, "worktree", "add", path, "-b", branch]
        if start_point:
            args.append(start_point)
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_WORKTREE_ADD_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, result.stderr.strip() or "git worktree add failed"
    return True, ""
```

- [ ] **Step 3: Sanity-check by hand in a scratch repo** (read-only for the project; do NOT touch the TwiCC repo's own worktrees)

```bash
cd /tmp && rm -rf wt-test && git init -q wt-test && cd wt-test && git commit -q --allow-empty -m init
cd /home/twidi/dev/twicc-poc && uv run python -c "
from twicc.git import create_worktree, get_worktree_branches
print(create_worktree('/tmp/wt-test', '/tmp/wt-test-feature', 'feature/x'))
print(get_worktree_branches('/tmp/wt-test'))
print(create_worktree('/tmp/wt-test', '/tmp/wt-test-dup', 'feature/x'))
"
rm -rf /tmp/wt-test /tmp/wt-test-feature
```

Expected: `(True, '')`, a set containing `master` (or `main`) and `feature/x`, then `(False, "fatal: 'feature/x' is already used by worktree ...")`.

- [ ] **Step 4: Commit**

```bash
git add src/twicc/git.py
git commit -m "feat(git): add create_worktree and get_worktree_branches helpers"
```

---

### Task 2: backend endpoints — branches list and worktree creation

**Files:**
- Modify: `src/twicc/projects.py`
- Modify: `src/twicc/views.py`
- Modify: `src/twicc/urls.py`

Reference handler: `_create_project` in `src/twicc/views.py` (lines ~201-299) — same body parsing (`orjson.loads`), error shape (`JsonResponse({"error": ...}, status=4xx)`), `run_under_db_write_lock(lambda: register_project(...))` usage, and `serialize_project` 201 response. `run_under_db_write_lock` takes a **zero-arg factory returning a coroutine**, not a coroutine. Mirror the project-lookup pattern used by the existing per-project views (e.g. `project_detail`).

- [ ] **Step 1: Thread an explicit `worktree_of_id` through project registration** (`src/twicc/projects.py`)

The endpoint knows the parent project, so the link is set at row creation instead of relying on filesystem detection (spec §2). Add an optional `worktree_of_id: str | None = None` keyword to the three layers:

- `_create_or_get_project` (line ~203): add the parameter; in the `defaults` build, add

  ```python
  if worktree_of_id is not None:
      defaults["worktree_of_id"] = worktree_of_id
  ```

- `register_project_db_only` (line ~255): add the parameter, pass it to `_create_or_get_project`, and after the adoption branch handle the pre-existing-row case (lost race where a directory-less row already existed — `get_or_create` defaults don't apply then):

  ```python
  if worktree_of_id is not None and not created and project.worktree_of_id != worktree_of_id:
      project.worktree_of_id = worktree_of_id
      project.save(update_fields=["worktree_of"])
  ```

  Mention the parameter in the docstring (one sentence: explicit worktree link set at creation, callers that know the parent — e.g. the worktree-creation endpoint — use it instead of detection).

- `register_project` (line ~291): add the parameter, forward it to `register_project_db_only`, and skip detection when the link is explicit:

  ```python
  if (created or adopted) and project.directory:
      await auto_add_project_to_workspaces(project.id, project.directory)
      if worktree_of_id is None:
          await ensure_worktree_link(project.id, project.directory)
  ```

Because the row is born linked, the existing `project_added` broadcast (which fires with the just-created `project` instance) already carries `worktree_of` — `serialize_project` includes the field. Every existing caller is unaffected (`worktree_of_id` defaults to `None` → behavior identical, detection still runs).

While in there, refresh the comment above `Project.worktree_of` in `src/twicc/core/models.py` (~line 85): it still says the field is set by `ensure_worktree_link` only, "new projects only, no backfill" — now it is also set explicitly (worktree-creation endpoint) and by the backfill command.

- [ ] **Step 2: Add the two views in `src/twicc/views.py`**

Import note: `views.py` imports `twicc.git` **lazily inside each view function** (see e.g. lines ~1761, ~1862) — follow that convention for `get_branches`, `get_worktree_branches`, `create_worktree`. The rest (`orjson`, `os`, `sync_to_async`, `IntegrityError`, `path_to_project_id`, `register_project`, `run_under_db_write_lock`, `serialize_project`, `get_channel_layer`) is already imported at module level for the existing views; verify and complete.

```python
async def project_branches(request, project_id):
    """GET: local branches of the project's repo, with worktree-checkout state."""
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return JsonResponse({"error": "Project not found"}, status=404)
    if not project.git_root:
        return JsonResponse({"error": "Project is not a git repository"}, status=400)
    branches = await sync_to_async(get_branches)(project.git_root)
    checked_out = await sync_to_async(get_worktree_branches)(project.git_root)
    return JsonResponse(
        {"branches": [{"name": b, "checked_out": b in checked_out} for b in branches]}
    )


async def project_worktrees(request, project_id):
    """POST: create a git worktree and register it as a project linked to its parent."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    project = await Project.objects.filter(id=project_id).afirst()
    if project is None:
        return JsonResponse({"error": "Project not found"}, status=404)
    if not project.git_root:
        return JsonResponse({"error": "Project is not a git repository"}, status=400)

    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    path = (data.get("path") or "").strip()
    branch = (data.get("branch") or "").strip()
    start_from = (data.get("start_from") or "").strip() or None

    if not path or not os.path.isabs(path):
        return JsonResponse({"error": "Path must be an absolute path"}, status=400)
    if not branch:
        return JsonResponse({"error": "Branch is required"}, status=400)

    resolved = os.path.realpath(path)
    new_project_id = path_to_project_id(resolved)
    if await Project.objects.filter(id=new_project_id).aexists():
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    local_branches = await sync_to_async(get_branches)(project.git_root)
    if branch in local_branches:
        start_from = None  # meaningless for an existing-branch checkout
    elif start_from and start_from not in local_branches:
        return JsonResponse({"error": "Start-from branch does not exist"}, status=400)

    ok, git_error = await sync_to_async(create_worktree)(project.git_root, resolved, branch, start_from)
    if not ok:
        return JsonResponse({"error": git_error}, status=400)

    try:
        new_project, _created = await run_under_db_write_lock(
            lambda: register_project(new_project_id, directory=resolved, worktree_of_id=project.id)
        )
    except IntegrityError:
        return JsonResponse({"error": "A project already exists for this directory"}, status=409)

    return JsonResponse(serialize_project(new_project), status=201)
```

Implementation notes:
- The `worktree_of_id=project.id` keyword (added in Step 1) sets the link at row creation: the `project_added` broadcast and the 201 body both carry `worktree_of` with no extra fetch or broadcast, and `ensure_worktree_link` detection is skipped for this flow. No self-link is possible: if `resolved` mapped to the parent's own id, the `aexists` check above already returned 409.
- `register_project` still performs the workspace auto-add and the `project_added` broadcast — no duplicated side-effect code here.
- `_created` is deliberately ignored (unlike `_create_project`, which 409s on `created=False`): the early `aexists` check covers the normal duplicate case, and once `git worktree add` has succeeded, adopting a pre-existing directory-less row in a lost race is benign — returning 409 at that point would orphan a freshly created worktree. Do not reintroduce the 409-on-race here.
- Adjust the project-lookup lines to whatever pattern the neighbouring per-project views actually use (e.g. helper function or `aget` + `DoesNotExist`); the 404/400 semantics above are what matters.

- [ ] **Step 3: Register the routes in `src/twicc/urls.py`** (next to the other per-project routes, lines ~33-63)

```python
path("api/projects/<str:project_id>/branches/", views.project_branches),
path("api/projects/<str:project_id>/worktrees/", views.project_worktrees),
```

- [ ] **Step 4: Syntax sanity check**

`views.py` imports Django models at module level, so a bare `import twicc.views` raises `AppRegistryNotReady`; Django must be set up first:

```bash
uv run python -c "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'twicc.settings'); django.setup(); import twicc.views, twicc.urls; print('ok')"
```

Expected: `ok` (note: full endpoint behavior is verified manually in Task 5, after the user restarts the backend).

- [ ] **Step 5: Commit**

```bash
git add src/twicc/projects.py src/twicc/views.py src/twicc/urls.py
git commit -m "feat(api): endpoints to list branches and create worktrees per project"
```

---

### Task 3: `WorktreeCreateDialog.vue`

**Files:**
- Create: `frontend/src/components/project/WorktreeCreateDialog.vue`

Reference implementation for every dialog pattern: `frontend/src/components/project/ProjectEditDialog.vue` — `wa-dialog` with `@wa-show`/`@wa-after-show` (with the `e.target !== dialogRef.value` guard), `useId()`-based form id, footer submit button bound via `setAttribute('form', formId)` in a `nextTick` sync function, `wa-callout variant="danger"` for errors, `apiFetch` from `utils/api`, responsive `--width`. The path field reuses the exact directory-input pattern: `wa-input` + `<DirectoryPickerPopup v-model="..." />` side by side (see `ProjectEditDialog.vue` line ~545).

- [ ] **Step 1: Create the component**

Behavior contract (see spec §3):

- `defineExpose({ open, close })`; `open(project)` receives the **parent project object** (`{ id, git_root, ... }`), resets all fields and errors, shows the dialog, and fires the branches fetch. (Deliberate deviation from spec §3, which words this as a prop: a single mounted instance serves every project row, so the parent is passed at `open()` time and kept in a local ref. Behavior is otherwise identical.)
- Emits `created(project)` after a successful creation.
- On open: `GET /api/projects/{project.id}/branches/` via `apiFetch`; store `branches` (`[{name, checked_out}]`). A fetch failure shows the error callout but leaves the form usable (autocomplete just stays empty).
- **Branch field** (first, focused via `@wa-after-show`): `wa-input`, free text. Below it, a suggestion list (simple scrollable `<div>` with max-height, NOT a wa-dropdown — we are already inside a dialog): branches whose name contains the typed text (case-insensitive), hidden when the input exactly matches a branch or is empty. Each available suggestion is clickable and fills the field; branches with `checked_out: true` are rendered disabled with an "in use" hint and are not clickable (if the user types one manually anyway, git's error will surface via the backend).
- `branchIsNew = computed(() => trimmedBranch && !branches.some(b => b.name === trimmedBranch))`.
- **Path field**: `wa-input` (placeholder `e.g. ${parentGitRoot}/.worktrees/<branch>` — build the literal string, never auto-fill the value) + `DirectoryPickerPopup v-model`.
- **Start from** (only rendered when `branchIsNew`): `wa-select` with a first option "Current HEAD" (value `""`, default) then every branch name. Web Awesome selects need `:value.prop` binding or `@input` handlers — follow how `ProjectEditDialog` handles its `wa-select`s.
- Submit (`Create worktree` footer button, loading state via `loading` attribute while the request is in flight):
  - client-side checks: branch non-empty; path non-empty and starting with `/` (absolute) — otherwise set the error message locally, no request;
  - payload: `{ path, branch, start_from: branchIsNew && startFrom ? startFrom : null }` — `start_from` is **always `null`** when the field is hidden, a previously selected value must not leak;
  - `apiFetch('/api/projects/${project.id}/worktrees/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })`;
  - non-2xx → `errorMessage = data.error || 'Failed to create worktree'` (danger callout);
  - 201 → `store.addProject(data)` **before** `emit('created', data)` (required: the follow-up flow reads the store — trust gate, draft settings resolution), then close the dialog.
- Dialog label: `New worktree`; a one-line hint mentioning the parent project name (e.g. `Create a git worktree of <name>`).
- All strings in English.

- [ ] **Step 2: Check Web Awesome imports**

Every `wa-*` component used must be imported in `frontend/src/main.js`. The dialog only uses components already imported elsewhere (`wa-dialog`, `wa-input`, `wa-select`, `wa-option`, `wa-button`, `wa-callout`, `wa-icon`); verify each with a quick grep and add any missing import:

```bash
grep -o "components/[a-z-]*/" frontend/src/main.js | sort -u
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/project/WorktreeCreateDialog.vue
git commit -m "feat(frontend): worktree creation dialog"
```

(Include `frontend/src/main.js` in the `git add` if Step 2 added imports.)

---

### Task 4: dropdown integration in `ProjectView.vue`

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`

The two "New session" dropdowns live here: single-project split-button (~line 1705) and all-projects dropdown (~line 1836). Project rows are `wa-dropdown-item :value="p.id"` wrapping a `ProjectBadge`, repeated in several template loops (prioritized/named/tree/other sections) in BOTH dropdowns. `handleNewSessionSelect` (~line 961) already `e.preventDefault()`s the `worktrees-toggle:` pseudo-values; `handleProjectCreated` (~line 976) is the post-creation flow to mimic; the create-project dialog is mounted at the bottom of the template (~line 2168).

- [ ] **Step 1: Add the button to every main project row**

In **each** template loop that renders a main project row (`wa-dropdown-item` with a `ProjectBadge`) inside the two "New session" dropdowns — and **only** there; do not touch `WorktreePickerRows.vue` (no worktree-of-worktree) — append after the badge:

```html
<span
    v-if="p.git_root"
    class="new-worktree-button"
    title="New worktree"
    @click.stop="openWorktreeDialog(p)"
>
    <wa-icon name="code-branch"></wa-icon><wa-icon name="plus"></wa-icon>
</span>
```

(`p` = the loop's project variable; adjust per loop.) Native `title` is deliberate: the rows are loops (no stable ids for `AppTooltip for=...`) and `wa-tooltip` stacking inside a dropdown overlay is fragile — the spec sanctions the `title` fallback.

Make the row a flex container so the button sits at the far right. Scoped styles, following the dropdown styles already present in this file:

```css
.new-worktree-button {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 2px;
    padding: 2px 6px;
    border-radius: var(--wa-border-radius-s, 4px);
    opacity: 0.55;
    font-size: 0.85em;
    cursor: pointer;
}
.new-worktree-button:hover {
    opacity: 1;
    background: var(--wa-color-neutral-fill-normal);
}
```

`wa-dropdown-item`'s default slot may not be a flex row; if `margin-left: auto` has no effect, wrap the row content or style the item's part — check the rendered DOM and adapt (the requirement is: badge left, button far right).

- [ ] **Step 2: Wire the dialog**

Script additions:

```js
import WorktreeCreateDialog from '../components/project/WorktreeCreateDialog.vue'

const worktreeCreateDialogRef = ref(null)
const newSessionSplitDropdownRef = ref(null)   // ref on the single-project wa-dropdown
const newSessionAllDropdownRef = ref(null)     // ref on the all-projects wa-dropdown

function openWorktreeDialog(project) {
    // Close whichever "New session" dropdown is open: the button click is
    // stopPropagation'd, so the dropdown would otherwise stay visible.
    for (const dd of [newSessionSplitDropdownRef.value, newSessionAllDropdownRef.value]) {
        if (dd) dd.open = false
    }
    worktreeCreateDialogRef.value?.open(project)
}

function handleWorktreeCreated(project) {
    handleNewSession(project.id)
}
```

Template additions: `ref="newSessionSplitDropdownRef"` / `ref="newSessionAllDropdownRef"` on the two `wa-dropdown` elements, and next to the existing `ProjectEditDialog` mounts (~line 2168):

```html
<WorktreeCreateDialog ref="worktreeCreateDialogRef" @created="handleWorktreeCreated" />
```

(If setting `.open = false` doesn't close the dropdown, use its `hide()` method — check the Web Awesome dropdown docs in `frontend/node_modules/@awesome.me/webawesome/dist/llms.txt`.)

- [ ] **Step 3: Frontend builds clean**

The dev server (if running) hot-reloads; otherwise:

```bash
cd frontend && npx vite build --logLevel error
```

Expected: build completes without errors. Do not commit build output.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/ProjectView.vue
git commit -m "feat(frontend): new-worktree button on git project rows in New session dropdowns"
```

---

### Task 5: manual verification (with the user)

No tests per project policy. The backend changes require a server restart, which is **reserved to the user** — ask them to restart via `devctl.py`, then walk through:

- [ ] 1. In both "New session" dropdowns, git projects show the code-branch+plus button at the right of their row; non-git projects don't; worktree sub-rows don't. Clicking the row itself still creates a session (button click does not).
- [ ] 2. Button click closes the dropdown and opens the dialog; branch field is focused; autocomplete lists local branches, with already-checked-out ones marked "in use" and unclickable.
- [ ] 3. Creating a worktree with a **new** branch name (+ optional start-from) at an absolute path: 201, dialog closes, draft session opens in the new project, and the new project appears under the parent's "Worktrees" group with the worktree icon (i.e. `worktree_of` was linked).
- [ ] 4. Creating with an **existing, unused** branch: works; "Start from" select is hidden in that case.
- [ ] 5. Error paths show the danger callout with git's message: branch already checked out elsewhere; target path existing and non-empty; relative path rejected client-side; existing-project directory → 409 message.
- [ ] 6. `git worktree list` in the parent repo confirms the new worktrees.

After verification, remind the user this feature is done and committed (no migration involved, no new packages).
