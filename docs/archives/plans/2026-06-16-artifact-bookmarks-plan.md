# Artifact Bookmarks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users bookmark rendered artifacts (name + project/workspace/all scope) and browse them in a URL-driven Artifacts mode of the sidebar, scoped exactly like session pins.

**Architecture:** New `ArtifactBookmark` Django model + flat CRUD REST endpoints + WebSocket broadcasts (mirroring `project_*`). Frontend: a Pinia `bookmarks` map loaded at boot; an always-present artifact action bar in `FilePane` to add/edit a bookmark (works on mobile and for media); a route-driven Artifacts mode (`/…/artifacts/:bookmarkId?`) that swaps the sidebar to bookmark controls + list and renders the selected bookmark in a render-only `FilePane` wrapper.

**Tech Stack:** Django 6 ASGI · SQLite · Channels · Vue 3 (`<script setup>`) · Pinia · Web Awesome · vue-router.

---

## Pre-flight notes (read once)

- **No worktree / stay on `main`.** Per project rules, do not branch or create a worktree.
- **Tests are not mandatory** (project rule). This plan includes **backend** tests (pytest infra exists) for model/serializer/endpoints. There is **no frontend test runner** (no vitest/jest), so frontend tasks use **manual verification** steps instead of unit tests. Do not add a frontend test runner.
- **Migrations:** after editing models, generate the migration with
  `TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --name artifact_bookmark --settings=twicc.settings`.
  Do **not** run `migrate` yourself — devctl auto-applies it on backend restart; remind the user to restart.
- **Commits:** precise `git add <files>` (never `-A`/`-a`), conventional messages, footer
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- **Reference spec:** `docs/plans/2026-06-16-artifact-bookmarks-design.md` (read it; this plan implements it).
- **Backend test run:** `uv run pytest tests/test_artifact_bookmarks.py -v`.
- **Frontend manual check:** ask the user to restart dev servers via `devctl.py` when needed (do not restart yourself).

## File Structure

**Backend (modify):**
- `src/twicc/core/models.py` — add `ArtifactBookmark` model.
- `src/twicc/core/migrations/0108_artifactbookmark.py` — generated.
- `src/twicc/core/serializers.py` — add `serialize_artifact_bookmark`.
- `src/twicc/views.py` — add `artifact_bookmark_list`, `_create_artifact_bookmark`, `artifact_bookmark_detail`, `_broadcast_bookmark_updated`, `_broadcast_bookmark_removed`.
- `src/twicc/urls.py` — two `path()` entries.

**Backend (create):**
- `tests/test_artifact_bookmarks.py`.

**Frontend (create):**
- `frontend/src/utils/textFilter.js` — extracted `matchSessionQuery` / `matchSubsequence`.
- `frontend/src/utils/sidebarBookmarks.js` — `computeBookmarkList`.
- `frontend/src/components/session/SessionsSidebarControls.vue` — extracted session header row.
- `frontend/src/components/artifacts/BookmarksSidebarControls.vue`
- `frontend/src/components/artifacts/BookmarkList.vue`
- `frontend/src/components/artifacts/ArtifactActionBar.vue` — bookmark button + name + dialog trigger (used inside `FilePane`).
- `frontend/src/components/artifacts/ArtifactBookmarkDialog.vue` — create/edit dialog.
- `frontend/src/views/ArtifactsBrowserView.vue` — main-pane bookmark renderer.

**Frontend (modify):**
- `frontend/src/stores/data.js`, `frontend/src/App.vue`, `frontend/src/composables/useWebSocket.js`,
  `frontend/src/components/files/FilePane.vue`, `frontend/src/components/files/FilesPanel.vue`,
  `frontend/src/views/SessionView.vue`, `frontend/src/views/ProjectView.vue`, `frontend/src/router.js`,
  `frontend/src/components/session/list/SessionList.vue`.

**Docs (modify):** `CLAUDE.md`, `AGENTS.md`.

---

## Phase 1 — Backend

### Task 1: `ArtifactBookmark` model + migration

**Files:**
- Modify: `src/twicc/core/models.py`
- Create (generated): `src/twicc/core/migrations/0108_artifactbookmark.py`
- Test: `tests/test_artifact_bookmarks.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_artifact_bookmarks.py`:

```python
import pytest
from django.db import IntegrityError
from django.utils import timezone

from twicc.core.models import ArtifactBookmark, PinMode, Project, Session, SessionType


@pytest.fixture
def project(db):
    return Project.objects.create(id="-tmp-ab", directory="/tmp/ab")


@pytest.fixture
def session(project):
    now = timezone.now()
    return Session.objects.create(
        id="sess-ab", project=project, provider="claude_code",
        file_path="sess-ab.jsonl", type=SessionType.SESSION, title="sess-ab",
        created_at=now, last_new_content_at=now, user_message_count=1,
    )


def test_create_bookmark(session, project):
    bm = ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.PROJECT,
    )
    assert bm.id is not None
    assert bm.scope == "project"


def test_unique_per_session_and_path(session, project):
    ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope=PinMode.ALL,
    )
    with pytest.raises(IntegrityError):
        ArtifactBookmark.objects.create(
            session=session, project=project, relative_path="a.md", name="B", scope=PinMode.PROJECT,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_artifact_bookmarks.py -v`
Expected: FAIL — `ImportError: cannot import name 'ArtifactBookmark'`.

- [ ] **Step 3: Add the model**

In `src/twicc/core/models.py` (after the `Session` model; `PinMode` is already defined in this module):

```python
class ArtifactBookmark(models.Model):
    """A user-saved pointer to one rendered artifact file, with a display name
    and a visibility scope. Scope reuses PinMode and mirrors session pinning:
    'project' stays local, 'workspace' surfaces in workspace views, 'all' in the
    global view. "Not bookmarked" = no row."""

    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="artifact_bookmarks",
    )
    # Denormalised from session.project so scope filtering needs no join.
    # Intentionally the RAW project: for a worktree session this is the worktree
    # project, NOT the main repo. The worktree -> main-repo mapping happens at
    # scope-resolution time on the frontend (see the design doc, §5).
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="artifact_bookmarks",
    )
    # Path relative to the session's artifacts dir, e.g. "demo/index.html".
    relative_path = models.TextField()
    name = models.CharField(max_length=255)
    scope = models.CharField(max_length=16, choices=PinMode.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "relative_path"],
                name="uniq_artifact_bookmark_session_path",
            ),
        ]
        indexes = [
            models.Index(fields=["project"], name="idx_artifactbookmark_project"),
        ]
```

- [ ] **Step 4: Generate the migration**

Run: `TWICC_DATA_DIR=$PWD uv run python -m django makemigrations core --name artifact_bookmark --settings=twicc.settings`
Expected: creates `src/twicc/core/migrations/0108_artifactbookmark.py`. (Tests use `settings_test` and auto-build the schema, so they don't need a manual `migrate`.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_artifact_bookmarks.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add src/twicc/core/models.py src/twicc/core/migrations/0108_artifactbookmark.py tests/test_artifact_bookmarks.py
git commit -m "feat(artifacts): add ArtifactBookmark model"
```

> After this task, remind the user to restart the backend via `devctl.py` so the migration is applied to their instance.

---

### Task 2: `serialize_artifact_bookmark`

**Files:**
- Modify: `src/twicc/core/serializers.py`
- Test: `tests/test_artifact_bookmarks.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_artifact_bookmarks.py`:

```python
def test_serialize_bookmark(session, project):
    from twicc.core.serializers import serialize_artifact_bookmark
    bm = ArtifactBookmark.objects.create(
        session=session, project=project,
        relative_path="demo/index.html", name="Demo", scope=PinMode.WORKSPACE,
    )
    d = serialize_artifact_bookmark(bm)
    assert d["name"] == "Demo"
    assert d["scope"] == "workspace"
    assert d["session_id"] == "sess-ab"
    assert d["project_id"] == "-tmp-ab"
    assert d["relative_path"] == "demo/index.html"
    assert d["file_ext"] == "html"
    assert d["root"].endswith("/sess-ab")
    assert d["created_at"] and d["updated_at"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_artifact_bookmarks.py::test_serialize_bookmark -v`
Expected: FAIL — `ImportError: cannot import name 'serialize_artifact_bookmark'`.

- [ ] **Step 3: Implement the serializer**

In `src/twicc/core/serializers.py` (plain function, no DB queries — FK ids via `_id`, mirrors `serialize_project`):

```python
def serialize_artifact_bookmark(bookmark):
    """Serialize an ArtifactBookmark to a dict. Pure: no DB queries — FK ids via
    *_id, root computed from the session id (no relationship access)."""
    import os
    from twicc.paths import get_session_artifacts_dir

    rel = bookmark.relative_path
    return {
        "id": bookmark.id,
        "name": bookmark.name,
        "scope": bookmark.scope,
        "session_id": bookmark.session_id,
        "project_id": bookmark.project_id,
        "relative_path": rel,
        "root": str(get_session_artifacts_dir(bookmark.session_id)),
        "file_ext": os.path.splitext(rel)[1].lstrip(".").lower(),
        "created_at": bookmark.created_at.isoformat() if bookmark.created_at else None,
        "updated_at": bookmark.updated_at.isoformat() if bookmark.updated_at else None,
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_artifact_bookmarks.py::test_serialize_bookmark -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/twicc/core/serializers.py tests/test_artifact_bookmarks.py
git commit -m "feat(artifacts): serialize_artifact_bookmark"
```

---

### Task 3: REST endpoints + broadcasts

**Files:**
- Modify: `src/twicc/views.py`, `src/twicc/urls.py`
- Test: `tests/test_artifact_bookmarks.py`

- [ ] **Step 1: Add failing endpoint tests**

Append to `tests/test_artifact_bookmarks.py` (mirrors `tests/test_session_artifacts.py`: `AsyncClient` driven from sync tests; `artifacts_root` fixture writes real files. Reuse that file's `artifacts_root` / `_write_artifact` fixtures — copy them in or import the helper):

```python
import asyncio
import orjson
from django.test import AsyncClient


@pytest.fixture
def client(settings):
    settings.TWICC_PASSWORD_HASH = ""
    return AsyncClient()


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# NOTE: copy the `artifacts_root` fixture + `_write_artifact` helper from
# tests/test_session_artifacts.py VERBATIM, AND their module-level imports
# (`from pathlib import Path`, and the `twicc.paths` import they monkeypatch).
# That fixture works by patching `paths.get_data_dir` (the parent of
# get_session_artifacts_dir) at a tmp dir — so artifacts land under
# <tmp>/artifacts/<session_id>/. A literal copy of just the fixture body fails
# with NameError without those imports.

def test_create_and_list_bookmark(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "demo/index.html", b"<html></html>")
    body = {"session_id": "sess-ab", "relative_path": "demo/index.html",
            "name": "Demo", "scope": "all"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 201
    created = orjson.loads(res.content)
    assert created["name"] == "Demo"

    res = _run(client.get("/api/artifact-bookmarks/"))
    assert res.status_code == 200
    assert len(orjson.loads(res.content)["bookmarks"]) == 1


def test_create_rejects_missing_file(client, session, project, artifacts_root):
    body = {"session_id": "sess-ab", "relative_path": "nope.md",
            "name": "X", "scope": "project"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 404


def test_create_rejects_path_escape(client, session, project, artifacts_root):
    body = {"session_id": "sess-ab", "relative_path": "../escape.md",
            "name": "X", "scope": "project"}
    res = _run(client.post("/api/artifact-bookmarks/", data=orjson.dumps(body),
                           content_type="application/json"))
    assert res.status_code == 400


def test_patch_and_delete_bookmark(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# hi")
    bm = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="project")
    res = _run(client.patch(f"/api/artifact-bookmarks/{bm.id}/",
                            data=orjson.dumps({"name": "B", "scope": "all"}),
                            content_type="application/json"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["name"] == "B"
    res = _run(client.delete(f"/api/artifact-bookmarks/{bm.id}/"))
    assert res.status_code == 200
    assert not ArtifactBookmark.objects.filter(id=bm.id).exists()


def test_detail_reports_availability(client, session, project, artifacts_root):
    _write_artifact(artifacts_root, "sess-ab", "a.md", b"# hi")
    present = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="a.md", name="A", scope="all")
    res = _run(client.get(f"/api/artifact-bookmarks/{present.id}/"))
    assert res.status_code == 200
    assert orjson.loads(res.content)["available"] is True
    # A bookmark whose file is missing (created directly, bypassing POST validation):
    missing = ArtifactBookmark.objects.create(
        session=session, project=project, relative_path="gone.md", name="Gone", scope="all")
    res = _run(client.get(f"/api/artifact-bookmarks/{missing.id}/"))
    assert orjson.loads(res.content)["available"] is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_artifact_bookmarks.py -v -k "create or patch or list"`
Expected: FAIL — 404/Resolver404 (URLs not wired).

- [ ] **Step 3: Add the views**

In `src/twicc/views.py` (imports already present: `orjson`, `JsonResponse`, `Http404`, `sync_to_async`, `get_channel_layer`, `run_under_db_write_lock`; add `import os`, and import `ArtifactBookmark`, `PinMode`, `Session` from `twicc.core.models`, and `serialize_artifact_bookmark` from `twicc.core.serializers` alongside the existing model/serializer imports, and `get_session_artifacts_dir` from `twicc.paths`):

```python
def _confined_artifact_path(session_id, relative_path):
    """Resolve relative_path inside the session's artifacts dir. Returns the
    realpath if it stays confined to the artifacts dir, else None."""
    root_real = os.path.realpath(str(get_session_artifacts_dir(session_id)))
    abs_path = os.path.realpath(os.path.join(root_real, relative_path))
    if abs_path != root_real and not abs_path.startswith(root_real + os.sep):
        return None
    return abs_path


async def artifact_bookmark_list(request):
    """GET /api/artifact-bookmarks/ — list all bookmarks.
    POST /api/artifact-bookmarks/ — create (or upsert) a bookmark."""
    if request.method == "POST":
        return await _create_artifact_bookmark(request)
    bookmarks = await sync_to_async(list)(ArtifactBookmark.objects.all())
    return JsonResponse({"bookmarks": [serialize_artifact_bookmark(b) for b in bookmarks]})


async def _create_artifact_bookmark(request):
    try:
        data = orjson.loads(request.body)
    except orjson.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    session_id = (data.get("session_id") or "").strip()
    relative_path = (data.get("relative_path") or "").strip()
    name = (data.get("name") or "").strip()
    scope = data.get("scope")
    if not session_id or not relative_path or not name:
        return JsonResponse({"error": "session_id, relative_path and name are required"}, status=400)
    if scope not in PinMode.values:
        return JsonResponse({"error": "Invalid scope"}, status=400)

    try:
        session = await Session.objects.aget(id=session_id)
    except Session.DoesNotExist:
        raise Http404("Session not found")

    # Confinement + existence (renderable-type is enforced client-side, design §4).
    abs_path = _confined_artifact_path(session_id, relative_path)
    if abs_path is None:
        return JsonResponse({"error": "Path escapes the artifacts directory"}, status=400)
    if not await sync_to_async(os.path.isfile)(abs_path):
        return JsonResponse({"error": "Artifact file not found"}, status=404)

    # IMPORTANT: run_under_db_write_lock takes a *coroutine factory* (it awaits
    # the lambda's result). Use the ASYNC ORM method, never sync update_or_create.
    bookmark, _created = await run_under_db_write_lock(
        lambda: ArtifactBookmark.objects.aupdate_or_create(
            session=session, relative_path=relative_path,
            defaults={"project_id": session.project_id, "name": name, "scope": scope},
        )
    )
    await _broadcast_bookmark_updated(bookmark)
    return JsonResponse(serialize_artifact_bookmark(bookmark), status=201)


async def artifact_bookmark_detail(request, bookmark_id):
    try:
        bookmark = await ArtifactBookmark.objects.aget(id=bookmark_id)
    except ArtifactBookmark.DoesNotExist:
        raise Http404("Bookmark not found")

    if request.method == "DELETE":
        await run_under_db_write_lock(lambda: bookmark.adelete())
        await _broadcast_bookmark_removed(bookmark_id)
        return JsonResponse({"ok": True})

    if request.method == "PATCH":
        try:
            data = orjson.loads(request.body)
        except orjson.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        update_fields = []
        if "name" in data:
            name = (data.get("name") or "").strip()
            if not name:
                return JsonResponse({"error": "name cannot be empty"}, status=400)
            bookmark.name = name
            update_fields.append("name")
        if "scope" in data:
            if data["scope"] not in PinMode.values:
                return JsonResponse({"error": "Invalid scope"}, status=400)
            bookmark.scope = data["scope"]
            update_fields.append("scope")
        if update_fields:
            update_fields.append("updated_at")
            await run_under_db_write_lock(lambda: bookmark.asave(update_fields=update_fields))
            await _broadcast_bookmark_updated(bookmark)

    payload = serialize_artifact_bookmark(bookmark)
    if request.method == "GET":
        # Lazy availability check, on open (design §8/§9): stat the file now.
        # Kept OUT of the pure serializer; only the single-bookmark GET does I/O.
        abs_path = _confined_artifact_path(bookmark.session_id, bookmark.relative_path)
        payload["available"] = bool(abs_path and await sync_to_async(os.path.isfile)(abs_path))
    return JsonResponse(payload)


async def _broadcast_bookmark_updated(bookmark):
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "artifact_bookmark_updated",
                 "bookmark": serialize_artifact_bookmark(bookmark)},
    })


async def _broadcast_bookmark_removed(bookmark_id):
    channel_layer = get_channel_layer()
    await channel_layer.group_send("updates", {
        "type": "broadcast",
        "data": {"type": "artifact_bookmark_removed", "bookmark_id": bookmark_id},
    })
```

- [ ] **Step 4: Wire the URLs**

In `src/twicc/urls.py`, after the `api/projects/` lines:

```python
    path("api/artifact-bookmarks/", views.artifact_bookmark_list),
    path("api/artifact-bookmarks/<int:bookmark_id>/", views.artifact_bookmark_detail),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_artifact_bookmarks.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/twicc/views.py src/twicc/urls.py tests/test_artifact_bookmarks.py
git commit -m "feat(artifacts): artifact-bookmarks REST endpoints + broadcasts"
```

---

## Phase 2 — Frontend store, boot, WebSocket

### Task 4: Pinia store — bookmarks map + actions + getter

**Files:** Modify `frontend/src/stores/data.js`

- [ ] **Step 1: Add `bookmarks` to state**

In the `state()` return (next to `sessions: {}`):

```js
    bookmarks: {},      // { id: { id, name, scope, session_id, project_id, relative_path, root, file_ext, ... } }
```

- [ ] **Step 2: Add a getter to find a bookmark by (session, path)**

In `getters`:

```js
    bookmarkFor: (state) => (sessionId, relativePath) =>
        Object.values(state.bookmarks).find(
            b => b.session_id === sessionId && b.relative_path === relativePath,
        ) || null,
```

- [ ] **Step 3: Add actions** (mirror `loadStickySessions` / `loadSessionById`)

In `actions`:

```js
    async loadArtifactBookmarks() {
        try {
            const res = await apiFetch('/api/artifact-bookmarks/')
            if (!res.ok) {
                console.error('Failed to load artifact bookmarks:', res.status, res.statusText)
                return
            }
            const data = await res.json()
            for (const b of data.bookmarks) this.bookmarks[b.id] = b
        } catch (error) {
            console.error('Failed to load artifact bookmarks:', error)
        }
    },
    async fetchBookmarkDetail(id) {
        // Always GET the detail (fresh server-side `available` flag), upsert
        // metadata, and return the full payload incl. `available`, or null on 404.
        try {
            const res = await apiFetch(`/api/artifact-bookmarks/${id}/`)
            if (res.status === 404) { delete this.bookmarks[id]; return null }
            if (!res.ok) throw new Error(`Failed to load bookmark: ${res.status}`)
            const b = await res.json()
            this.bookmarks[b.id] = b
            return b
        } catch (error) {
            console.error(`Failed to fetch bookmark ${id}:`, error)
            return null
        }
    },
    upsertBookmark(bookmark) { this.bookmarks[bookmark.id] = bookmark },
    removeBookmark(id) { delete this.bookmarks[id] },
    async createBookmark({ sessionId, relativePath, name, scope }) {
        const res = await apiFetch('/api/artifact-bookmarks/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: sessionId, relative_path: relativePath, name, scope }),
        })
        if (!res.ok) throw new Error((await res.json().catch(() => ({})))?.error || 'Failed to create bookmark')
        const b = await res.json()
        this.bookmarks[b.id] = b
        return b
    },
    async updateBookmark(id, patch) {
        const res = await apiFetch(`/api/artifact-bookmarks/${id}/`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(patch),
        })
        if (!res.ok) throw new Error('Failed to update bookmark')
        const b = await res.json()
        this.bookmarks[b.id] = b
        return b
    },
    async deleteBookmark(id) {
        const res = await apiFetch(`/api/artifact-bookmarks/${id}/`, { method: 'DELETE' })
        if (!res.ok) throw new Error('Failed to delete bookmark')
        delete this.bookmarks[id]
    },
```

- [ ] **Step 4: Manual verification**

In the browser devtools console after restart: `const s = window.__pinia ?? null` is not exposed; instead verify via the Vue devtools that the data store has a `bookmarks` object, or temporarily call `useDataStore().loadArtifactBookmarks()` from a component. Minimal check: no console errors at boot (Task 5 wires the call).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/stores/data.js
git commit -m "feat(artifacts): bookmarks store state, getter and actions"
```

---

### Task 5: Boot load + WebSocket cases

**Files:** Modify `frontend/src/App.vue`, `frontend/src/composables/useWebSocket.js`

- [ ] **Step 1: Load bookmarks at boot**

In `frontend/src/App.vue`, add to the `Promise.all([...])` in the `isAppReady` watcher:

```js
            dataStore.loadArtifactBookmarks(),
```

- [ ] **Step 2: Add WS cases**

In `frontend/src/composables/useWebSocket.js`, after the `case 'artifacts_available': { ... }` block:

```js
                case 'artifact_bookmark_updated': {
                    store.upsertBookmark(msg.bookmark)
                    break
                }
                case 'artifact_bookmark_removed': {
                    store.removeBookmark(msg.bookmark_id)
                    break
                }
```

- [ ] **Step 3: Manual verification**

Restart servers (ask user). Open the app; confirm no console errors at boot and the `GET /api/artifact-bookmarks/` request appears in the Network tab (200, `{bookmarks: []}`).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.vue frontend/src/composables/useWebSocket.js
git commit -m "feat(artifacts): load bookmarks at boot + WS upsert/remove"
```

---

## Phase 3 — Add-bookmark UI

### Task 6: Extract the filter matcher into a shared util

**Files:** Create `frontend/src/utils/textFilter.js`; Modify `frontend/src/components/session/list/SessionList.vue`

- [ ] **Step 1: Create the util** (move `matchSubsequence` + `matchSessionQuery` verbatim from `SessionList.vue`)

```js
// frontend/src/utils/textFilter.js
// Shared filter matching used by the session list and the artifact bookmark
// list. `matchQuery`: subsequence match by default; a leading " or ' switches
// to a case-insensitive literal-substring match of the quoted phrase.

export function matchSubsequence(query, text) {
    // ... move the exact body from SessionList.vue ...
}

export function matchQuery(query, text) {
    const first = query[0]
    if (first === '"' || first === "'") {
        let needle = query.slice(1)
        if (needle.endsWith(first)) needle = needle.slice(0, -1)
        if (!needle) return true
        return text.toLowerCase().includes(needle.toLowerCase())
    }
    return matchSubsequence(query, text)
}
```

- [ ] **Step 2: Use it in `SessionList.vue`**

Remove the local `matchSubsequence` / `matchSessionQuery` definitions; `import { matchQuery } from '../../../utils/textFilter'` and replace the call `matchSessionQuery(query, displayName)` with `matchQuery(query, displayName)`.

- [ ] **Step 3: Manual verification**

In the session sidebar, confirm filtering still works: typing a fuzzy substring matches; typing `"exact phrase` does a case-insensitive literal match. No behavior change.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/utils/textFilter.js frontend/src/components/session/list/SessionList.vue
git commit -m "refactor(filter): extract session filter matcher to shared util"
```

---

### Task 7: `FilePane` render-only prop (threaded through `FilesPanel`)

**Files:** Modify `frontend/src/components/files/FilePane.vue`, `frontend/src/components/files/FilesPanel.vue`

- [ ] **Step 1: Add the prop to `FilePane`**

In `FilePane.vue` `defineProps`, add `renderOnly: { type: Boolean, default: false }`.

- [ ] **Step 2: Hide the source toggle + Edit switch when `renderOnly`**

- The Edit `<wa-switch>` block (`:904-909`): add `v-if` condition so it does not render when `props.renderOnly` (combine with the existing `(!diffMode || !diffReadOnly) && isWritable`).
- The eye/preview-toggle buttons for md/svg/html/mermaid (in `.header-right`): add `&& !renderOnly` to their `v-if` so the toggle disappears (preview stays on because `preview-by-default` is also set by the caller).
- Ensure preview is locked on: when `renderOnly`, the `filePath` watcher must set the preview flags true (reuse the `previewByDefault` path) and never allow toggling.

- [ ] **Step 3: Thread through `FilesPanel`**

In `FilesPanel.vue` `defineProps` add `renderOnly: { type: Boolean, default: false }`, and pass it to the child: `<FilePane :render-only="renderOnly" ... />`.

- [ ] **Step 4: Manual verification** (after Task 12 wires a consumer; for now)

Temporarily pass `:render-only="true"` to the Artifacts-tab `FilesPanel` and confirm md/html render with no Edit switch and no eye toggle; then revert the temporary prop.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/files/FilePane.vue frontend/src/components/files/FilesPanel.vue
git commit -m "feat(files): render-only mode for FilePane"
```

---

### Task 8: Artifact action bar + bookmark dialog

**Files:** Create `frontend/src/components/artifacts/ArtifactActionBar.vue`, `frontend/src/components/artifacts/ArtifactBookmarkDialog.vue`; Modify `frontend/src/components/files/FilePane.vue`, `frontend/src/components/files/FilesPanel.vue`, `frontend/src/views/SessionView.vue`

- [ ] **Step 1: Create `ArtifactBookmarkDialog.vue`** (mirror `ProjectEditDialog.vue`)

`<script setup>` props: `{ sessionId, relativePath, defaultName }`. Holds a `wa-dialog` ref; exposes `open(existingBookmark | null)`. Form (`<form :id @submit.prevent="handleSave">`) with: a `wa-input` for **name** (required, `trim()`, empty default per design), a `wa-select` for **scope** (`:value.prop="scope" @change="scope = $event.target.value"`, options `project`/`workspace`/`all` with labels "Project" / "Workspace" / "All projects"). Footer: Cancel; **Save** (`type="submit"`, `form` set via `setAttribute` in the `@wa-show` sync handler); in edit mode also a **Remove** button. Guard `@wa-show`/`@wa-after-show` against bubbled child events (`if (e.target !== dialogRef.value) return`); focus the name input on `@wa-after-show`. Use `wa-callout variant="danger"` for errors.

Save logic:
```js
// create mode:
await dataStore.createBookmark({ sessionId, relativePath, name: name.value.trim(), scope: scope.value })
// edit mode (existing.id):
await dataStore.updateBookmark(existing.id, { name: name.value.trim(), scope: scope.value })
// remove:
await dataStore.deleteBookmark(existing.id)
```
Emit `saved` / `removed` and close on success.

- [ ] **Step 2: Create `ArtifactActionBar.vue`**

Props `{ sessionId, relativePath }`. Computed `bookmark = dataStore.bookmarkFor(sessionId, relativePath)`. Renders a slim bar with:
- a `wa-button` carrying `<wa-icon name="bookmark">` — `variant="brand"` + solid when `bookmark`, neutral + outline otherwise;
- when bookmarked, the bookmark **name** text next to it.
On click → `dialogRef.open(bookmark)`. Mount `<ArtifactBookmarkDialog ref="dialogRef" :session-id :relative-path />`.

```vue
<template>
  <div class="artifact-action-bar">
    <wa-button size="small" :variant="bookmark ? 'brand' : 'neutral'" @click="openDialog">
      <wa-icon slot="start" name="bookmark"></wa-icon>
      {{ bookmark ? 'Bookmarked' : 'Bookmark' }}
    </wa-button>
    <span v-if="bookmark" class="artifact-action-bar__name">{{ bookmark.name }}</span>
    <ArtifactBookmarkDialog ref="dialogRef" :session-id="sessionId" :relative-path="relativePath" />
  </div>
</template>
```

- [ ] **Step 3: Render the bar from `FilePane`**

In `FilePane.vue`: add prop `bookmarkSessionId: { type: String, default: null }`. Add computed `isRenderableArtifact` = `isMarkdownFile || isSvgFile || isHtmlFile || isMermaidFile || isPdfFile || isAudioFile || isVideoFile || <binary-image flag>`. Add computed `relativeArtifactPath` = strip `props.rootRestriction + '/'` prefix from `props.filePath`. Render the bar **independently of `displayPath`/`showHeader`**, e.g. just above the rendered content:

```vue
<ArtifactActionBar
  v-if="bookmarkSessionId && isRenderableArtifact && relativeArtifactPath"
  :session-id="bookmarkSessionId"
  :relative-path="relativeArtifactPath"
/>
```

- [ ] **Step 4: Thread `bookmarkSessionId` through `FilesPanel`**

Add `bookmarkSessionId: { type: String, default: null }` to `FilesPanel.defineProps` and pass `:bookmark-session-id="bookmarkSessionId"` to `<FilePane>`.

- [ ] **Step 5: Pass it from the Artifacts tab**

In `SessionView.vue`, on the **Artifacts-tab** `<FilesPanel>` (the one with `root-restriction="artifactsDir"`), add `:bookmark-session-id="session?.id"`. (Leave the Files tab `FilesPanel` unchanged — no bookmarking of repo source files.)

- [ ] **Step 6: Register components**

`ArtifactActionBar` / `ArtifactBookmarkDialog` are Vue SFCs (no `main.js` registration). Confirm `wa-dialog`, `wa-select`, `wa-option`, `wa-callout`, `wa-input`, `wa-button`, `wa-icon` are already imported in `main.js` (they are). `bookmark` resolves via the icon CDN.

- [ ] **Step 7: Manual verification**

Open a session's Artifacts tab, open a rendered artifact (md/html/image/pdf — incl. on a narrow/mobile viewport). The action bar shows with a "Bookmark" button. Click → dialog; enter a name, pick a scope, Save → button turns brand and shows the name. Reopen → edit dialog with Remove. Verify it also appears for PDF/image (where the toolbar is absent) and on mobile width (where the path bandeau is absent).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/artifacts/ArtifactActionBar.vue frontend/src/components/artifacts/ArtifactBookmarkDialog.vue frontend/src/components/files/FilePane.vue frontend/src/components/files/FilesPanel.vue frontend/src/views/SessionView.vue
git commit -m "feat(artifacts): bookmark action bar + create/edit dialog in the file viewer"
```

---

## Phase 4 — Browse mode

### Task 9: Routes + scope resolution util

**Files:** Modify `frontend/src/router.js`; Create `frontend/src/utils/sidebarBookmarks.js`

- [ ] **Step 1: Add the routes**

In `frontend/src/router.js`, add to the `/project/:projectId` children array:

```js
            { path: 'artifacts/:bookmarkId?', name: 'project-artifacts', component: { render: () => null } },
```

and to the `/projects` children array:

```js
            { path: 'artifacts/:bookmarkId?', name: 'projects-artifacts', component: { render: () => null } },
```

(These are siblings of `files`/`git`/`terminal`, NOT under `session/:sessionId`.)

- [ ] **Step 2: Create `computeBookmarkList`**

```js
// frontend/src/utils/sidebarBookmarks.js
// NOTE: these come from two DIFFERENT modules — copy the exact imports from the
// top of frontend/src/utils/sidebarSessions.js (ALL_PROJECTS_ID is exported by
// the data store, isWorkspaceProjectId by workspaceIds):
import { ALL_PROJECTS_ID } from '../stores/data'
import { isWorkspaceProjectId } from './workspaceIds'

// Mirrors computeSidebarSessionBlocks scope rules for bookmarks.
// `projectScopeIds` = data.getProjectScopeIds(effectiveProjectId) (main repo + worktrees).
export function computeBookmarkList({ bookmarks, workspaces, effectiveProjectId, activeWorkspaceId, projectScopeIds }) {
    const list = Object.values(bookmarks).filter(b => {
        if (effectiveProjectId === ALL_PROJECTS_ID) return b.scope === 'all'
        if (isWorkspaceProjectId(effectiveProjectId)) {
            if (b.scope === 'project') return false
            return workspaces.workspaceContainsProject(activeWorkspaceId, b.project_id)
        }
        return projectScopeIds.includes(b.project_id)  // single project (worktree-aware)
    })
    return list.sort((a, b) =>
        a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0)  // recency desc
}
```

> Cross-check the two import lines against the top of `frontend/src/utils/sidebarSessions.js` (it imports the same two symbols from those same two modules).

- [ ] **Step 3: Manual verification**

Routes only — navigating to `/project/<id>/artifacts` should not 404 (blank main pane until Task 12). Commit after Task 10–12 wire the UI, or commit routes+util now:

```bash
git add frontend/src/router.js frontend/src/utils/sidebarBookmarks.js
git commit -m "feat(artifacts): artifacts-mode routes + bookmark scope resolution"
```

---

### Task 10: Extract `SessionsSidebarControls`, add `BookmarksSidebarControls`, wire the mode switch

**Files:** Create `frontend/src/components/session/SessionsSidebarControls.vue`, `frontend/src/components/artifacts/BookmarksSidebarControls.vue`; Modify `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: Extract `SessionsSidebarControls.vue`**

Move the second `.sidebar-header-row` (options dropdown + filter input + advanced-search button, `ProjectView.vue:1690-1774`) into a new component. Props in: the option states (`showArchivedSessions`, `compactView`, `multiSelectActive`, `showActiveAcrossFilters`) and `searchQuery`. Emits/handlers out: `update:searchQuery`, `optionSelect`, `searchKeydown`, `openAdvancedSearch`, and a new `switchToArtifacts`. Add a `wa-dropdown-item value="switch-artifacts"` ("Switch to artifacts") to its options menu. Keep `ProjectView` behavior identical otherwise (pass the existing refs/handlers through).

- [ ] **Step 2: Create `BookmarksSidebarControls.vue`**

A trimmed mirror: options dropdown with only **"Switch to sessions"** (`value="switch-sessions"`) and **"Compact view"** (`compact-view`); a filter input (`placeholder="Filter artifacts..."`, `v-model` → `update:searchQuery`); **no** advanced-search button. Emits `switchToSessions`, `optionSelect`, `update:searchQuery`.

- [ ] **Step 3: Wire mode + switching in `ProjectView.vue`**

```js
const isArtifactsMode = computed(() =>
    route.name === 'project-artifacts' || route.name === 'projects-artifacts')
```

In the sidebar template, render `<SessionsSidebarControls v-if="!isArtifactsMode" ... />` and `<BookmarksSidebarControls v-else ... />`.

Switch handlers (preserve scope + workspace query):
```js
function switchToArtifacts() {
    if (isAllProjectsMode.value) router.push({ name: 'projects-artifacts', query: queryWithWorkspace() })
    else router.push({ name: 'project-artifacts', params: { projectId: projectId.value } })
}
function switchToSessions() {
    if (isAllProjectsMode.value) router.push({ name: 'projects-all', query: queryWithWorkspace() })
    else router.push({ name: 'project', params: { projectId: projectId.value } })
}
```
Add this **small local helper** (the existing selector code in `ProjectView.vue` inlines the same pattern at `:824`/`:837`/`:843`, so factor it once and reuse):
```js
const queryWithWorkspace = () => (activeWorkspaceId.value ? { workspace: activeWorkspaceId.value } : {})
```

- [ ] **Step 4: Make the scope selector mode-aware**

In the project/workspace/all selector's select handler (`handleSelectorSelect`), when `isArtifactsMode.value` is true, push to the `*-artifacts` route variant for the chosen scope instead of the session route — so changing scope stays in Artifacts mode.

- [ ] **Step 5: Manual verification**

Sessions mode unchanged. Options menu now has "Switch to artifacts" → URL becomes `/…/artifacts`, sidebar shows the trimmed bookmark controls (filter says "Filter artifacts...", no full-text button). "Switch to sessions" returns. Changing project/workspace in the selector while in Artifacts mode keeps you in Artifacts mode.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/session/SessionsSidebarControls.vue frontend/src/components/artifacts/BookmarksSidebarControls.vue frontend/src/views/ProjectView.vue
git commit -m "feat(artifacts): sidebar mode switch + dedicated bookmark controls"
```

---

### Task 11: `BookmarkList` component

**Files:** Create `frontend/src/components/artifacts/BookmarkList.vue`; Modify `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: Create `BookmarkList.vue`**

Props: `{ effectiveProjectId, activeWorkspaceId, searchQuery }`. Compute the list:
```js
const list = computed(() => {
    const projectScopeIds = dataStore.getProjectScopeIds(props.effectiveProjectId)
    let rows = computeBookmarkList({
        bookmarks: dataStore.bookmarks,
        workspaces: workspacesStore,
        effectiveProjectId: props.effectiveProjectId,
        activeWorkspaceId: props.activeWorkspaceId,
        projectScopeIds,
    })
    const q = props.searchQuery.trim()
    if (q) rows = rows.filter(b => matchQuery(q, b.name) || matchQuery(q, b.relative_path))
    return rows
})
```
Each row: a type icon derived from `file_ext` (e.g. an icon map; default a generic file icon), the **name** (primary), a secondary line with project + session source (use `dataStore.getProject(b.project_id)?.name` / the session title if loaded, else the ids; `relative_path` in a tooltip), and a small scope indicator. Clicking a row emits `select(bookmark)`. Empty state: "No bookmarked artifacts yet — bookmark an artifact from a session's Artifacts tab."

- [ ] **Step 2: Render it in the sidebar**

In `ProjectView.vue`, in the `.sidebar-sessions` area, render `<SessionList v-if="!isArtifactsMode" ... />` and `<BookmarkList v-else :effective-project-id="effectiveProjectId" :active-workspace-id="activeWorkspaceId" :search-query="searchQuery" @select="onBookmarkSelect" />`. `onBookmarkSelect(b)` pushes to the selected-bookmark route:
```js
function onBookmarkSelect(b) {
    if (isAllProjectsMode.value)
        router.push({ name: 'projects-artifacts', params: { bookmarkId: String(b.id) }, query: queryWithWorkspace() })
    else
        router.push({ name: 'project-artifacts', params: { projectId: projectId.value, bookmarkId: String(b.id) } })
}
```

- [ ] **Step 3: Manual verification**

Bookmark a couple of artifacts in different scopes/projects. In a project's Artifacts mode, only that project's bookmarks (worktree-aware) show; in all-projects mode only `all`-scoped show; in a workspace, `workspace`/`all` of member projects show. Filtering by name and by `"exact` works. Sorted by recency.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/artifacts/BookmarkList.vue frontend/src/views/ProjectView.vue
git commit -m "feat(artifacts): bookmark list in the sidebar (scoped, filtered, recency-sorted)"
```

---

### Task 12: `ArtifactsBrowserView` (main pane) + third main-pane region

**Files:** Create `frontend/src/views/ArtifactsBrowserView.vue`; Modify `frontend/src/views/ProjectView.vue`

- [ ] **Step 1: Create `ArtifactsBrowserView.vue`**

Props: `{ bookmarkId, effectiveProjectId }` (the view reads scope from props/route). Logic:
- If no `bookmarkId` → empty state ("Select a bookmark").
- Else, **on open / when `bookmarkId` changes**: set `loading = true`, then
  `const detail = await dataStore.fetchBookmarkDetail(bookmarkId)` (always GETs
  the detail, fresh, including the `available` flag; upserts metadata into the
  store). Then: `detail === null` → **not-found** state; `detail.available ===
  false` → **missing-file callout** (below); otherwise render. Use `detail` (=
  `dataStore.bookmarks[bookmarkId]`) for name/scope/root/relative_path.
  - *Optional polish:* if the bookmark is already in `dataStore.bookmarks`
    (boot/list load), render its cached metadata + the `FilePane` immediately and
    run `fetchBookmarkDetail` in the background, only swapping to the
    missing-file callout if `available === false` comes back — avoids a loading
    flash when navigating from the list.
- Header: bookmark **name** (title); a scope indicator/button → opens `ArtifactBookmarkDialog` in edit mode (reuse the same dialog component, passing `sessionId`/`relativePath` from the bookmark); a **Remove** button (`dataStore.deleteBookmark`, then navigate back to the list route); an **Open in session** button (Step 2).
- Body: a render-only `FilePane` wrapper:
```vue
<FilePane
  :file-path="`${bookmark.root}/${bookmark.relative_path}`"
  :root-restriction="bookmark.root"
  :preview-by-default="true"
  :render-only="true"
  :active="true"
  api-prefix="/api"
/>
```
- **Missing file:** when `detail.available === false` (computed server-side on
  the detail GET by stat-ing the file — uniform across ALL types, including
  binary media that bypass FilePane's content fetch), render a `wa-callout`
  **instead of** the `FilePane` wrapper, with **Remove bookmark** (`dataStore.deleteBookmark` → back to the list route) + **Open the session that created it** (Step 2). No `FilePane` change is needed for this — do not add a load-failed emit.

- [ ] **Step 2: "Open in session"**

Navigate to the source session's Artifacts tab with the file revealed, mirroring `SessionView.onArtifactsNavigate` (`SessionView.vue:433`). Build the route params with the **`buildFilesRouteParams` helper** (`import { buildFilesRouteParams } from '../utils/granularRoutes'` — it applies `encodePath` internally, so pass the **raw** relative path, do NOT call `encodePath` yourself). The artifacts `rootKey` is the literal string `'artifacts'` (see `SessionView.vue:136`):

```js
const params = buildFilesRouteParams({ rootKey: 'artifacts', filePath: bookmark.relative_path })
const name = isAllProjectsMode ? 'projects-session-artifacts' : 'session-artifacts'
router.push({ name, params: { projectId: bookmark.project_id, sessionId: bookmark.session_id, ...params }, query: queryWithWorkspace() })
```

> Verify `buildFilesRouteParams`'s exact return shape in `frontend/src/utils/granularRoutes.js` and that `'artifacts'` is the rootKey SessionView uses, before finalizing.

- [ ] **Step 3: Add the third main-pane region in `ProjectView.vue`**

Gate the two existing regions and add the third:
```html
<main slot="end" class="main-content">
    <div v-show="!isArtifactsMode && sessionId" class="session-content">
        <router-view v-slot="{ Component }">
            <KeepAlive :max="settingsStore.getMaxCachedSessions">
                <component :is="Component" :key="route.params.sessionId" />
            </KeepAlive>
        </router-view>
    </div>
    <div v-show="!isArtifactsMode && !sessionId" class="project-detail-content">
        <KeepAlive>
            <ProjectDetailPanel :project-id="effectiveProjectId" :active="!sessionId" :key="effectiveProjectId" />
        </KeepAlive>
    </div>
    <div v-show="isArtifactsMode" class="artifacts-browser-content">
        <KeepAlive>
            <ArtifactsBrowserView
                :bookmark-id="route.params.bookmarkId || null"
                :effective-project-id="effectiveProjectId"
                :key="effectiveProjectId"
            />
        </KeepAlive>
    </div>
</main>
```

- [ ] **Step 4: Manual verification**

Click a bookmark in the list → it renders in the main pane in render mode (md/html/image/pdf), with the name as title and Remove / Open-in-session buttons. "Open in session" opens that session's Artifacts tab on the right file. Delete an artifact file on disk, click its bookmark → the missing-file callout shows with Remove + Open-session. Deep-link directly to `/projects/artifacts/<id>` in a fresh tab → loading then render (no spurious not-found).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ArtifactsBrowserView.vue frontend/src/views/ProjectView.vue
git commit -m "feat(artifacts): artifact browser view (render, manage, open-in-session, missing-file)"
```

---

## Phase 5 — Docs

### Task 13: Update `CLAUDE.md` and `AGENTS.md`

**Files:** Modify `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: `CLAUDE.md`** — in the **Database Models** section, add a bullet:
  `**ArtifactBookmark`** — user bookmark of one rendered artifact (`session` FK, denormalised `project`, `relative_path`, `name`, `scope` reusing `PinMode`; unique `(session, relative_path)`). Scope mirrors session pinning; "not bookmarked" = no row.`

- [ ] **Step 2: `AGENTS.md`** — mirror the same bullet (condensed) in its models section.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md AGENTS.md
git commit -m "docs: document ArtifactBookmark model"
```

---

## Final verification checklist

- [ ] `uv run pytest tests/test_artifact_bookmarks.py -v` — all pass.
- [ ] Bookmark add/edit/remove works in the Artifacts tab on desktop **and** mobile width, for md/html/svg/mermaid/image/pdf/audio/video.
- [ ] Sidebar mode switch (Sessions ⇄ Artifacts) is URL-driven and preserves scope + workspace id.
- [ ] Scope resolution matches session pins, including worktree → main-repo surfacing.
- [ ] Artifact browser renders, manages, opens-in-session, and shows the missing-file callout on click.
- [ ] No console errors; sessions filtering unchanged after the matcher extraction.
- [ ] Remind the user to restart the backend (migration) and dev servers via `devctl.py`.
