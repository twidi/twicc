# Artifact Bookmarks — Design

**Status:** Design (approved in brainstorming, not yet planned/implemented)
**Date:** 2026-06-16

## 1. Motivation & concept

The Artifacts tab lets a user browse a session's artifacts directory
(`{artifacts_base_dir}/{session_id}/`) and renders images, HTML, Markdown,
Mermaid, PDF, audio and video in the file viewer. There is currently **no way
to collect artifacts across sessions** — to mark the ones worth coming back to.

This feature adds **artifact bookmarks**: while viewing a rendered artifact, the
user can bookmark it (give it a name, pick a visibility scope), and later browse
all bookmarked artifacts from a dedicated **Artifacts mode** in the sidebar,
scoped to the current project / workspace / all-projects context — reusing the
same three-scope model as session pinning.

A bookmark is a *"come back to this later"* marker, not a *favourite/preferred*
flag — hence the **bookmark** wording and the **bookmark (signet)** icon, kept
visually distinct from the session **pin (thumbtack)**.

### Key decisions (locked during brainstorming)

1. **Bookmark**, not favourite. Icon: `bookmark` (solid = bookmarked, outline =
   not). Internal model: `ArtifactBookmark`.
2. **Three scopes**, reusing `PinMode` (`project` / `workspace` / `all`). Scope
   controls cross-context visibility, exactly mirroring session pins. The
   all-projects ("global") view shows **`all`-scoped bookmarks only**.
3. **Only renderable artifacts can be bookmarked**: Markdown, SVG, HTML,
   Mermaid, images, PDF, audio, video. No source-code mode anywhere in this
   feature — everything is shown rendered. (For a folder with `index.html` +
   `app.js` + `style.css`, the user bookmarks the HTML; its relative assets are
   pulled by the rendered iframe.)
4. **Browse mode is URL-based**: an `/artifacts` segment is appended to the
   current scope URL; the workspace id is preserved as a query param.
5. **Default name is empty** and required (the user always types a name).
6. **Orphan detection is lazy, on click only** (see §9).
7. **Adding a bookmark must work on every device** (desktop and mobile) and for
   every renderable type — via a dedicated, always-present *artifact action bar*
   (see §6 and §10).
8. **Separate components** for the Artifacts-mode sidebar, not conditionals
   bolted onto the session sidebar (see §7).

## 2. Terminology

| Term | Meaning |
| --- | --- |
| Bookmark | A saved pointer to one rendered artifact file, with a user name and a scope. |
| Scope | `project` / `workspace` / `all` — where the bookmark surfaces. |
| Artifacts mode | The sidebar mode that lists bookmarks (URL segment `/artifacts`). |
| Artifact action bar | The always-present strip in the file viewer (artifact context) hosting the bookmark button + name. |
| Artifact view | The main-pane render of a selected bookmark in Artifacts mode. |

## 3. Data model (backend)

New model `ArtifactBookmark` in `src/twicc/core/models.py`:

| Field | Type | Notes |
| --- | --- | --- |
| `session` | FK → `Session` (CASCADE) | Owning session; the artifact lives under its artifacts dir. |
| `project` | FK → `Project` | **Denormalised** from `session.project` so scope queries/filters don't need a join; indexed. This is the raw `session.project_id`: for a worktree session it is the *worktree* project, **not** the main repo. The worktree→main-repo mapping happens at scope-resolution time (§5), exactly as for sessions — do not pre-map it here. |
| `relative_path` | `TextField` | Path **relative** to the session's artifacts dir (e.g. `demo/index.html`). Never absolute — survives a relocation of the artifacts base dir (`get_artifacts_dir()`). |
| `name` | `CharField` | User display name. Required (non-blank). |
| `scope` | `CharField(choices=PinMode.choices)` | One of `project` / `workspace` / `all`. **Non-null** here (unlike `Session.pinned`, where NULL = unpinned; here "not bookmarked" = no row). |
| `created_at` / `updated_at` | `DateTimeField` | `auto_now_add` / `auto_now`. |

- **Uniqueness:** `unique_together = (session, relative_path)` — one bookmark per
  artifact file. Re-bookmarking the same file *edits* the existing row (name /
  scope), never duplicates.
- Reuses the existing `PinMode` enum
  (`src/twicc/core/models.py:56`) — no new enum.
- Needs a migration (`ArtifactBookmark` table + indexes on `project`, `session`).
  *User runs the migration on their own instance (per project rules).*

There is **no render-mode field** — everything renders (decision 3). There is
**no availability flag** — orphan detection is lazy on click (decision 6, §9), so
the list never stats the filesystem.

## 4. Backend API & WebSocket

Mirror the flat CRUD pattern of `project_list` / `project_detail`
(`src/twicc/views.py:190`, `:521`) — TwiCC serializers are plain functions, not
DRF classes.

### Endpoints (`src/twicc/urls.py`, `src/twicc/views.py`)

```
path("api/artifact-bookmarks/", views.artifact_bookmark_list)               # GET list, POST create
path("api/artifact-bookmarks/<int:bookmark_id>/", views.artifact_bookmark_detail)  # GET, PATCH, DELETE
```

- `GET /api/artifact-bookmarks/` → **all** bookmarks (they are few; filtering by
  scope happens client-side, mirroring `loadStickySessions` +
  `computeSidebarSessionBlocks`). No server-side scope param needed in v1.
- `POST` body: `{ session_id, relative_path, name, scope }` → validate
  `scope ∈ PinMode.values`, validate the **path is confined** to the session's
  artifacts dir and the **file exists** (reusing the standalone root/confinement
  logic from `standalone_file_raw`), then create and broadcast
  `artifact_bookmark_updated`.
  - **Renderable-type is NOT re-validated on the backend.** The renderable-type
    set lives only on the frontend (per-extension computeds in `FilePane.vue` +
    runtime image detection). The bookmark button is offered **only** for
    renderable types (§6), so renderability is enforced client-side and the
    backend stays the single authority for existence + confinement. This avoids
    a second, drift-prone copy of the extension list on the backend. (If a
    server-side gate is ever wanted, introduce one shared list — do not
    hand-duplicate it.)
- `PATCH /<id>/` body: `{ name?, scope? }` → update, broadcast
  `artifact_bookmark_updated`.
- `DELETE /<id>/` → delete, broadcast `artifact_bookmark_removed`.

All writes go under the DB write lock (`run_under_db_write_lock`, as in
`session_detail` — method dispatch at `src/twicc/views.py:852`, the lock call at
`:879`), then broadcast via Channels
`channel_layer.group_send("updates", {"type": "broadcast", "data": {...}})`.

### Serializer (`src/twicc/core/serializers.py`)

`def serialize_artifact_bookmark(bm)` — a pure function (no DB queries, no lazy
relationships, per the module contract). Returns:

```json
{
  "id": "...",
  "name": "...",
  "scope": "project|workspace|all",
  "session_id": "...",
  "project_id": "...",
  "relative_path": "demo/index.html",
  "root": "/abs/path/to/{artifacts_base_dir}/{session_id}",
  "file_ext": "html",
  "created_at": "...",
  "updated_at": "..."
}
```

- `session_id` / `project_id` come from the FK id columns (no query).
- `root` is computed purely via `get_session_artifacts_dir(session_id)`
  (`src/twicc/paths.py:146`) — the same helper `serialize_session` already uses
  (`src/twicc/core/serializers.py:134`) — so the frontend can build the render
  URL `/api/file-raw/<base64url(root)>/<relative_path>` (the existing standalone
  raw endpoint, `standalone_file_raw`, `src/twicc/views.py:1795`) without loading
  the full session into the store.
- `file_ext` feeds the list's type icon.

### WebSocket

Two new message types, dispatched in the `switch` of
`frontend/src/composables/useWebSocket.js` next to `session_updated`
(`:838`):

```js
case 'artifact_bookmark_updated':  store.upsertBookmark(msg.bookmark); break
case 'artifact_bookmark_removed':  store.removeBookmark(msg.bookmark_id); break
```

## 5. Scope resolution (worktree-aware)

Resolution mirrors `computeSidebarSessionBlocks`
(`frontend/src/utils/sidebarSessions.js:66`) and reuses
`workspaces.workspaceContainsProject` (`frontend/src/stores/workspaces.js:121`),
which already treats **git worktrees of a member project as implicit members**
(via `worktree_of`). This worktree-awareness is mandatory.

A bookmark with scope `S` owned by project `P` surfaces in:

| Sidebar scope context | Shows bookmark? |
| --- | --- |
| Single-project view whose **scope ids include `P`** | **Always**, regardless of `S`. |
| Single-project view whose scope ids do **not** include `P` | Never (a bookmark never leaks into an unrelated single project). |
| Workspace `W` | If `S ∈ {workspace, all}` **and** `W` contains `P` (worktree-aware). |
| All projects (global) | If `S == all` only. |

**Worktree-awareness (mandatory, and where a naive implementation breaks the
"faithful mirror"):** the single-project rows must NOT compare `P` against
`effectiveProjectId` directly. A bookmark owned by a worktree session has
`project = <worktree project>` (§3), yet viewing the worktree's **main repo**
must show it — exactly as session pins do. So the project match expands the
viewed project into its scope-id set via `data.getProjectScopeIds(effectiveProjectId)`
(`frontend/src/stores/data.js:587` — main-repo id → `[mainRepo, ...worktrees]`)
and tests `scopeIds.includes(bookmark.project_id)`. This mirrors
`sidebarSessions.js:92-98`. The workspace row reuses
`workspaces.workspaceContainsProject` (`frontend/src/stores/workspaces.js:121`),
which maps the project up via `getMainRepoProjectId` (as `sidebarSessions.js:119`
does) — already worktree-aware.

So, to mirror sessions: `project` stays local to its project's Artifacts view
(its main repo + worktrees); `workspace` additionally surfaces in workspace views
containing the project; `all` additionally surfaces in the global view. Higher
scope = strict superset.

A pure function `computeBookmarkList({ bookmarks, workspaces, effectiveProjectId,
activeWorkspaceId, projectScopeIds })` (new util, e.g.
`frontend/src/utils/sidebarBookmarks.js`) applies the rules above (with the
`getProjectScopeIds` expansion for the single-project case), then sorts **flat,
by recency** (`updated_at` desc).

## 6. Frontend — adding a bookmark (artifact action bar + dialog)

### Artifact action bar

A new, always-present strip rendered by `FilePane.vue`
(`frontend/src/components/files/FilePane.vue`) **whenever it is showing a
bookmarkable artifact** — i.e. *artifact context* AND *renderable type*. It must
**not** be hung off either of the two existing chrome elements, because neither
is reliably present for every renderable artifact on every device:

- the `.file-path-header` path bandeau is `v-if="displayPath"` and `displayPath`
  is null on mobile (`FilesPanel` passes
  `display-path = !isMobile ? selectedFile : null`,
  `frontend/src/components/files/FilesPanel.vue:841`) — so it is **absent on
  mobile for all types**;
- the `.header` toolbar is shown for HTML / Markdown / SVG / Mermaid
  (`showHeader = true`) but **`false` for binary media** — images, PDF, audio,
  video (`FilePane.vue:441-452`).

So across the renderable set, on desktop the toolbar covers HTML/MD/SVG/Mermaid
but not media; on mobile neither the bandeau nor (for media) the toolbar exists.
The action bar therefore renders **independently**, on **all devices and all
renderable types**, and hosts:

- the **bookmark button** (`wa-button` + `wa-icon name="bookmark"`; solid +
  `variant="brand"` when bookmarked, outline + `neutral` otherwise), and
- the bookmark **name** when bookmarked.

Placement: a dedicated element in the `FilePane` template, rendered whenever the
bookmarkable condition holds, independent of `displayPath`/`showHeader`. On
desktop, when the path bandeau is present, the bar sits adjacent to it (same
region, so it reads as "path + name + bookmark"); when the bandeau/toolbar are
absent (mobile, or media), it stands alone as a slim bar at the top of the pane,
above the rendered content and not overlapping the media player.

**Artifact context** is signalled to `FilePane` by the parent. The Artifacts tab
in `SessionView.vue` already mounts `FilesPanel` with
`root-restriction = artifactsDir`; we additionally pass the **session id** down
so `FilePane` can compute `relative_path = filePath − rootRestriction` and knows
which session owns the artifact. Renderable-type detection reuses the existing
computeds in `FilePane.vue` (`isMarkdownFile`, `isSvgFile`, `isHtmlFile`,
`isMermaidFile`, `isPdfFile`, `isAudioFile`, `isVideoFile`, + binary-image
detection).

### Bookmark dialog

A `wa-dialog` following the `ProjectEditDialog.vue` reference pattern
(form with `@submit.prevent`, submit button wired via `form` attribute, focus on
`@wa-after-show`, `trim()` on the name, danger `wa-callout` for errors, guarded
nested-event bubbling):

- **Not bookmarked → click opens the create dialog:** name input (empty,
  required) + scope selector (`wa-select`, options Project / Workspace / All
  projects, labels matching the pin dropdown). Save → `POST`.
- **Already bookmarked → click opens the edit dialog:** prefilled name + scope,
  plus a **Remove** button (`DELETE`).

## 7. Frontend — browse mode (routing, sidebar components, list)

### Routing

Add two top-level child routes (`frontend/src/router.js`), siblings of the
existing `files` / `git` / `terminal` children — **not** inside the
`session/:sessionId` subtree (so no collision with `session-artifacts`):

```js
// under /project/:projectId children:
{ path: 'artifacts/:bookmarkId?', name: 'project-artifacts', component: { render: () => null } }
// under /projects children:
{ path: 'artifacts/:bookmarkId?', name: 'projects-artifacts', component: { render: () => null } }
```

The route component is a `{ render: () => null }` stub, **matching the existing
project-level child routes** (`project-files`, `project-git`,
`project-terminal`, `router.js:36-38`/`:51-55`). Those panels are not rendered by
the `<router-view>`; they are rendered directly by `ProjectView.vue` based on the
route. `ArtifactsBrowserView` follows the same model (see §8) — it is **not**
mounted through the session `<router-view>` (which is keyed to `sessionId`).

Resulting URLs (workspace id preserved as `?workspace=`):

| Scope | Library URL | Selected bookmark |
| --- | --- | --- |
| Project | `/project/:id/artifacts` | `/project/:id/artifacts/:bookmarkId` |
| Workspace | `/projects/artifacts?workspace=W` | `/projects/artifacts/:bookmarkId?workspace=W` |
| All projects | `/projects/artifacts` | `/projects/artifacts/:bookmarkId` |

`ProjectView.vue` derives **mode** from the route name (the two exact names
`project-artifacts` / `projects-artifacts` → Artifacts mode, else Sessions mode;
see the exact-names guard below). Scope (`projectId`, `activeWorkspaceId`,
all-projects) is derived from the URL exactly as today
(`effectiveProjectId`) — no new store state.

### Separate sidebar components (no conditionals on the session sidebar)

The current sidebar header row 2 and list are inline in `ProjectView.vue`. We
extract and split them so Artifacts mode does not bolt `v-if`s onto
session-specific UI:

- **`SessionsSidebarControls`** (extracted from the current inline header row):
  the options gear (`show-archived`, `archive-older`, `compact-view`,
  `multi-select`, `show-active`), the session filter input, and the full-text
  search button. **New item added to its options menu: "Switch to artifacts"**
  (navigates to the `*-artifacts` route for the current scope).
- **`BookmarksSidebarControls`** (new): a **trimmed** options gear and an
  artifact filter input, **no full-text button**. Options menu contents:
  - **"Switch to sessions"** (back to the sessions route for the current scope),
  - `compact-view` (carried over — applies to the bookmark list density),
  - dropped as irrelevant: `show-archived`, `archive-older`, `show-active`,
    `multi-select` (bulk-remove deferred to v2, §12).
- The **filter matcher is shared**: `matchSessionQuery` + `matchSubsequence` are
  currently **inline** in `SessionList.vue:185`/`:161`. Extract them verbatim
  into a shared util (e.g. `frontend/src/utils/textFilter.js`); `SessionList`
  imports it, and `BookmarkList` reuses the **identical** rules (subsequence
  match; leading `"`/`'` ⇒ case-insensitive literal-phrase match) applied to the
  bookmark **name** (and `relative_path`).
- **`BookmarkList`** (new, replaces `SessionList` in Artifacts mode): flat list
  sorted by recency. It resolves `projectScopeIds =
  data.getProjectScopeIds(effectiveProjectId)` itself and passes it — together
  with the bookmarks map, `workspaces`, `effectiveProjectId` and
  `activeWorkspaceId` — to `computeBookmarkList` (§5). (This is the one
  divergence from the `computeSidebarSessionBlocks` mirror, which takes the whole
  `data` store and resolves scope ids internally; `computeBookmarkList` keeps a
  pure, explicit-args signature, so the caller does the resolution.) Each row =
  type icon (from `file_ext`) + **name** (primary) + secondary line (project +
  session source; `relative_path` in tooltip) + a scope indicator where multiple
  scopes can coexist (project view). Reuses the filter input; explicit empty
  state ("No bookmarked artifacts yet — bookmark an artifact from a session's
  Artifacts tab."). The floating **New session** buttons live with `SessionList`,
  so they naturally disappear in Artifacts mode — no condition needed.

`ProjectView.vue` simply chooses the pair (`SessionsSidebarControls` +
`SessionList`) vs (`BookmarksSidebarControls` + `BookmarkList`) by mode. Derive
the mode with the **exact route names**, NOT `endsWith('-artifacts')` — the
latter would also match the session-level `session-artifacts` /
`projects-session-artifacts` tab routes and wrongly flip the sidebar inside an
open session:
`const isArtifactsMode = computed(() => route.name === 'project-artifacts' || route.name === 'projects-artifacts')`
(alongside the existing `isAllProjectsMode = route.name?.startsWith('projects-')`, `ProjectView.vue:398`).

The **project/workspace/all scope selector** at the top of the sidebar keeps
working in Artifacts mode. Crucially, **in Artifacts mode the selector must
navigate to the `*-artifacts` route variants** (e.g. `project-artifacts` /
`projects-artifacts` with the right `projectId` / `?workspace=`), not the session
routes — otherwise changing scope would silently drop back to Sessions mode.
Switching scope therefore stays in Artifacts mode and just re-filters.

### Store

- `loadArtifactBookmarks()` — mirror of `loadStickySessions`
  (`frontend/src/stores/data.js:1886`): `GET /api/artifact-bookmarks/`, store
  into a reactive map. Runs in the `isAppReady` boot sequence (`App.vue`).
- `fetchBookmarkDetail(id)` — always `GET /api/artifact-bookmarks/<id>/` on open
  (fresh `available` flag, §8), upserts metadata into the store, returns the
  payload or null (unknown id). This both resolves deep-links (id not yet in the
  store) and drives the missing-file callout.
- `upsertBookmark(bm)` / `removeBookmark(id)` — mutations driven by the WS
  handlers and by optimistic create/edit/delete from the dialog and the artifact
  view.

## 8. Frontend — artifact view (main pane)

**Main-pane wiring (important — the existing mechanism does not give this for
free).** Today `ProjectView.vue` (`:2271-2284`) renders the main pane as **two
sibling `v-show` divs**, with the lone `<router-view>` *inside* the
`v-show="sessionId"` div and `ProjectDetailPanel` inside `v-show="!sessionId"`.
On a `*-artifacts` route there is no `sessionId`, so as-is the router-view region
is hidden and `ProjectDetailPanel` would show instead. `ArtifactsBrowserView`
must therefore be wired explicitly, not assumed to render through the existing
router-view:

- Add an `isArtifactsMode` flag (route-name derived, §7).
- Gate the two existing regions so they don't show in Artifacts mode:
  `v-show="!isArtifactsMode && sessionId"` and
  `v-show="!isArtifactsMode && !sessionId"`.
- Add a **third region** `v-show="isArtifactsMode"` that renders
  `ArtifactsBrowserView` **directly** (like `ProjectDetailPanel` is rendered
  directly), passing `:bookmark-id="route.params.bookmarkId"` and the scope, under
  its own `KeepAlive` keyed by `effectiveProjectId`. (The artifacts route's
  component stays `{ render: () => null }`, §7.)

Inside `ArtifactsBrowserView`:

- **No `:bookmarkId`** → empty state ("Select a bookmark").
- **With `:bookmarkId`**:
  - on open, call `fetchBookmarkDetail(id)` (always GET, §7 store) showing a
    **loading** state while in flight; this resolves deep-links (id not yet in
    the store) and returns the fresh `available` flag. Null → **not-found**
    state; `available === false` → **missing-file callout**;
  - render the artifact via a **thin wrapper around `FilePane`**, with
    `root-restriction = bookmark.root` and the relative path. Each bookmark
    resolves its own session's artifacts dir as the render root, so bookmarks
    from different sessions render correctly.
- **Render-only mode is a NEW `FilePane` capability, not an existing prop.**
  `preview-by-default` (`FilePane.vue:54`) only *auto-enables* preview on open;
  the eye/source toggle (`:704-726`) and the `<wa-switch>Edit</wa-switch>`
  (`:904-909`) both remain. So this needs a new prop (e.g. `render-only` /
  `preview-locked`) on `FilePane` that hides the source toggle and the Edit
  switch and locks preview on. Listed as a `FilePane` modification in §11.
- **Header**: the bookmark **name** (title) + scope indicator (click → edit
  dialog) + **Remove** + **Open in session**. "Open in session" navigates to the
  source session's Artifacts tab with the file revealed, reusing
  `viewFileInFilesTab` (`frontend/src/views/SessionView.vue:130`), which already
  routes artifacts-dir paths to the `'artifacts'` tab (`:134-139`) and then calls
  the **`FilesPanel` method** `revealFile` (`revealFile` is defined on
  `FilesPanel`, only *called* from `SessionView` at `:138`/`:150` — not a
  `SessionView` function). Source project/session + `relative_path` shown
  discreetly.

### Missing-file handling (lazy, on open)

When opening a bookmark, the artifact view fetches the bookmark detail
(`GET /api/artifact-bookmarks/<id>/`), which includes an `available` flag
computed **server-side by stat-ing the file** — lazy, only on open, and uniform
across all types (including binary media, which bypass the file-content fetch).
If `available` is false (file deleted/renamed), the view shows a **callout**
instead of the render: a **Remove bookmark** button + an **Open the session that
created it** link. No proactive detection, no filesystem stat at list time (the
`available` flag is NOT in the pure list serializer — only the single-bookmark
GET does the I/O).

## 9. Lifecycle

- **Orphans:** detected only when a bookmark is opened (§8). The list shows all
  bookmarks unconditionally.
- **Regenerated at the same path:** the bookmark keeps pointing at it (desired —
  "the report" updates in place).
- **Renamed/moved:** the bookmark breaks → handled by the missing-file callout.
- **Session archived:** does **not** hide its bookmarks (artifacts persist on
  disk). Bookmarks are independent of session archive/pin state.
- **Session deletion:** out of scope — TwiCC never deletes sessions or JSONL.

## 10. Mobile

Bookmarking must work on mobile (and others use mobile too). The **artifact
action bar** (§6) is the mechanism: because it is decoupled from the
desktop-only path bandeau and from the media-hidden toolbar, the bookmark button
is reachable on mobile and for every renderable type. The bookmark dialog
(`wa-dialog`) works on mobile as-is. Browse mode and the artifact view work on
mobile like any other route.

## 11. Component & file inventory

**Backend (new/modified):**
- `src/twicc/core/models.py` — new `ArtifactBookmark` model (+ migration).
- `src/twicc/core/serializers.py` — `serialize_artifact_bookmark`.
- `src/twicc/views.py` — `artifact_bookmark_list`, `artifact_bookmark_detail`.
- `src/twicc/urls.py` — two URL patterns.
- WebSocket broadcasts via existing Channels group `"updates"`.

**Frontend (new):**
- `frontend/src/utils/textFilter.js` — extracted `matchSessionQuery` / `matchSubsequence`.
- `frontend/src/utils/sidebarBookmarks.js` — `computeBookmarkList` (scope resolution + recency sort).
- `frontend/src/components/.../BookmarksSidebarControls.vue`
- `frontend/src/components/.../BookmarkList.vue`
- `frontend/src/components/.../ArtifactBookmarkDialog.vue`
- `frontend/src/views/ArtifactsBrowserView.vue` (+ thin render wrapper around `FilePane`).

**Frontend (modified):**
- `frontend/src/components/files/FilePane.vue` — (a) artifact action bar +
  bookmark button + name (independent of `displayPath`/`showHeader`, §6);
  (b) accept artifact-context props (owning session id; relative path derived as
  `filePath − rootRestriction`); (c) **new `render-only` / `preview-locked`
  prop** that hides the source toggle and the Edit switch and locks preview on
  (§8 M2).
- `frontend/src/components/files/FilesPanel.vue` — forward the artifact-context /
  session-id props through to `FilePane`.
- `frontend/src/views/SessionView.vue` — pass session id / artifact context to
  the Artifacts-tab `FilesPanel`.
- `frontend/src/views/ProjectView.vue` — extract `SessionsSidebarControls`;
  add `isArtifactsMode` (route-name derived); switch the control/list pair by
  mode; add "Switch to artifacts/sessions" items; **gate the two existing
  main-pane `v-show` regions on `!isArtifactsMode` and add the explicit third
  region rendering `ArtifactsBrowserView` directly** (§8 B1); make the scope
  selector navigate to `*-artifacts` variants while in Artifacts mode (§7 m6).
- `frontend/src/router.js` — two child routes (`{ render: () => null }` stubs).
- `frontend/src/stores/data.js` — `loadArtifactBookmarks` (boot load, all),
  `fetchBookmarkDetail` (always-GET on open → `available` flag + deep-link),
  `upsertBookmark`, `removeBookmark`, plus create/update/delete actions.
- `frontend/src/composables/useWebSocket.js` — two new `case`s.
- `frontend/src/main.js` — register any new `wa-*` components used (if not already imported).

## 12. Out of scope (v2 candidates)

- CLI commands / drop-requests for bookmarks; an agent skill to let an agent
  bookmark an artifact it just produced.
- Bulk multi-select remove in the bookmark list.
- Image thumbnails in the list (currently a type icon; thumbnails are an easy
  follow-up since the raw URL is available).
- Manual ordering / alternate sort options.

## 13. Docs to update at implementation

- `CLAUDE.md` — add `ArtifactBookmark` to the **Database Models** section.
- `AGENTS.md` — mirror the CLAUDE.md change (condensed).
- No `SKILLS-AND-CLI.md` change in v1 (no CLI/skill surface).
