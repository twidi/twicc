# Project icons — design

2026-07-17. Status: validated, not implemented.

## Goal

A project's only visual identity today is the generated color dot
(`Project.color`, rendered in ~7 places). Allow a project to show an **icon
image** instead. The icon is optional: when absent, rendering falls back to
the color dot exactly as today.

Two ways to get an icon:

1. **Auto-discovery** — find a favicon/logo file inside the project's git
   repository and adopt it silently.
2. **Manual** — the user picks an image for a project (or for a whole repo).

Icons are **copied** into TwiCC's data dir, never referenced by their source
path: they survive deletion of the original file.

## Inheritance model

An icon is a **per-project value that cascades to descendants** — the same
inheritance chain used for agent defaults (worktree main repo first, else
nearest path-ancestor project, recursively). Setting an icon on a project
shows it on every descendant that doesn't override; the auto-discovered repo
icon is the base layer.

**Effective icon of a project P** (resolved by walking the chain from P
upward):

1. The **first project in the chain with an explicit choice** wins — its own
   manual override (an icon) → that icon; `"none"` → the color dot.
2. Else P's **auto-discovered repo icon** (the socle, keyed by git root).
3. Else the color dot.

So overriding A cascades to its sub-projects B, C…; overriding B affects only
B and its descendants. There is **no repo-level manual icon** and **no
"apply to all"** — setting a project's icon *is* the whole operation; the
chain does the rest. The auto layer stays keyed by git root (shared, resolved
live); only the manual layer cascades by the project chain.

### `Project.icon` — per-project state (closed set of values)

Non-null `CharField`, default `"inherit"`:

| Value              | Meaning                          |
|--------------------|----------------------------------|
| `"inherit"` (default) | follow the inheritance chain, else the auto repo icon |
| `"none"`           | this project (and inheriting descendants) show the color dot |
| `<token>` (e.g. `icon-ab12cd34.png`) | this project's own icon (cascades) — file under `proj-<hash>` |

`null` is deliberately **not** used: a real `"inherit"` default means an unset
row inherits naturally (existing rows after migration, new subprojects).

Auto-discovery **never writes `Project.icon`**: it only populates the auto
repo-icon layer. `Project.icon` is written only by explicit user action.

### `Project.icon_anchor` — the AUTO layer's git root

The auto-discovered repo icon is keyed by **git root**, independent of any
`Project` row (the project sitting exactly at the git root may not exist).
`icon_anchor` records which git root feeds a project's auto icon when it
differs from its own `git_root` (worktree → main repo; umbrella → the git
found by the downward scan). Null → use `Project.git_root`.
`Project.git_root` itself is **never** touched by this feature.

## Storage layout

New data-dir subtree, one `paths.py` helper (`get_project_icons_dir()` →
`<data_dir>/project-icons/`), following the `artifacts/` / `scratch/`
pattern:

```
<data_dir>/project-icons/
  repo-<sha256(realpath(git_root))[:16]>/
    manifest.json               # repo-level icon state (source of truth)
    icon-<sha8_of_content>.png  # normalized bytes — the REPO icon
  proj-<sha256(project_id)[:16]>/
    icon-<sha8_of_content>.png  # normalized bytes — a per-project override
```

- **Filenames are content-hashed** (`icon-<sha8>.png`): immutable, so a
  changed image yields a new filename → new URL → free cache-busting; the
  old file is removed on replace.
- The repo icon's bytes exist **once** (under `repo-<hash>`), never
  duplicated per subproject.
- Directory names are opaque hashes: the served URL never leaks the
  `git_root` path.

### `manifest.json` — the auto repo icon (per git root)

```json
{
  "scanned_at": "<iso>",
  "icon_token": "icon-ab12cd34.png",   // or null (nothing found)
  "source_path": "/repo/.../favicon.ico"  // provenance
}
```

The manifest is purely the **auto layer** (no user state — all user choices
live per-project on `Project.icon`). Stickiness:

- **token present** (found) → **sticky**, not re-scanned, persisted.
- **nothing found** → not persisted, so it **re-scans every sync**; a favicon
  added to the repo later is picked up.

Loaded at startup into an in-memory cache `repo-<hash> → icon_token` (like
`_project_git_roots` in `projects.py`), so the serializer's `repo_icon_url`
needs **zero disk I/O per project**. Updated in place on discovery.

## Icon anchor resolution

Each project's icon anchor (the git root it inherits from) is resolved
**once at discovery time** and persisted:

1. `Project.git_root` if set (upward walk — the normal case).
2. Else a **bounded downward scan** from the project directory: if it finds
   **exactly one** `.git`, that repo is the anchor (the "umbrella folder"
   case — a container dir whose versioned code lives in a subdirectory).
3. Else no anchor → no auto icon (manual still possible).

For worktrees, the anchor is the main repo's git root (via `worktree_of`),
set at discovery.

### Bounded downward scan (safety)

Descending is far costlier than walking up, so it is tightly bounded:

- **max depth 2**; stop descending a branch as soon as a `.git` is found.
- skip heavy/irrelevant dirs (`node_modules`, `.venv`, `dist`, `.git`
  internals, etc.) and cap the number of visited directories (~200) — if
  exceeded, give up (no anchor) rather than churn.
- **exactly one** `.git` → adopt; two or more sibling repos → ambiguous →
  no anchor.
- prefer a `.git` **directory** (real repo) over a gitfile; prefer the
  shallowest.
- runs only for projects with no upward `git_root`, only once at discovery.

This is a **heuristic**: a lone `.git` could be a vendored/cloned dependency,
which would then lend its favicon to the umbrella. Accepted because the icon
is auto + silent + overridable — a wrong guess is low-stakes (the user
overrides or sets `"none"`).

## Auto-discovery

- Anchored on the icon anchor git root (above).
- **No location assumptions.** Projects organize assets a thousand ways
  (`<root>/public/`, `frontend/public/`, a Django app's `.../static/...`,
  the repo root itself). Rather than a whitelist of paths, we **search the
  repo recursively, breadth-first, for a whitelist of icon file names**, and
  the **shallowest match wins** (this survey of `~/dev` set the depth: a real
  project keeps its logo as deep as 6 levels). Names (case-insensitive, with
  optional `-<variant>` suffixes like `favicon-32x32`): `apple-touch-icon`,
  `favicon`, `icon`, `logo` in `svg`/`png`/`ico`/`webp`/`jpg`/`gif`.
- **Ties at equal depth**: role (`apple-touch-icon` > `favicon` > `icon` >
  `logo` — a square app icon beats a wide wordmark for a small badge), then
  format (`svg` > `png` > `ico`), then larger declared size.
- **Bounded and cheap.** Breadth-first means a repo whose icon is shallow
  stops at that depth (it never walks the whole tree); only an icon-less repo
  walks deep. Caps: **max depth 8** and a **visited-directory budget (~2000)**;
  skips `SCAN_SKIP_DIRS` + hidden dirs + nested git repos. Measured on `~/dev`:
  ~0.02 s across all repos; median repo ~14 dirs, only the largest monorepos
  approach the budget.
- **Known caveat**: a repo shipping its framework's default favicon (Vite/CRA)
  adopts a generic mark; detecting that (hashing against known defaults) is out
  of scope for v1 — silent + overridable makes a wrong guess low-stakes.
- On match: **normalize with Pillow** (already a dependency;
  reuse the resize helper in `cli/_drop_request/attachments.py`) — `.ico`
  and other raster formats → `.png`, bounded to a small max size; `.svg`
  is validated (parseable) and kept as-is. Copy into `repo-<hash>/`, write
  `manifest.json` (`origin: auto`), update the in-memory cache, broadcast
  `project_updated` for the affected projects.

### When discovery runs

A single chokepoint: the **project-registration path** in `projects.py`,
alongside `ensure_project_color` / `ensure_project_git_root` /
`ensure_worktree_link` — a new sibling `ensure_project_icon`. This covers,
with no extra wiring:

- **initial sync** (every start/restart re-registers all projects → existing
  users get icons, **without a data migration** — no app-code/filesystem
  work in migrations);
- **every project-creation site** (single chokepoint);
- **a new subproject of an already-scanned repo** — `ensure_project_icon`
  finds the existing `manifest.json` and reuses it without re-scanning.

The per-git-root manifest is what makes re-running at every sync idempotent
and cheap.

## Serving

New async view `project_icon`, cloned from `session_artifact`
(`views.py:3326`):

- `FileResponse(fp, content_type=..., as_attachment=False)`.
- extension allowlist + filename-shape check (reuse
  `_classify_artifact_filename` / `ALLOWED_ARTIFACT_EXTENSIONS`).
- symlink/escape confinement (reuse the `safe_open_artifact` pattern).
- route mounted **outside `/api/`**, e.g.
  `path("project-icons/<str:bucket>/<str:file_name>", views.project_icon)`
  where `bucket` is `repo-<hash>` or `proj-<hash>`; added to the SPA
  catch-all exclusion (`urls.py`) and to the `PasswordAuthMiddleware`
  protected prefixes (auth parity with artifacts — served through Django,
  **not** BlackNoise, which bypasses auth).

### SVG safety

Icons are rendered by the frontend through `<img src=...>`, never inlined as
`<svg>` markup. An SVG loaded via `<img>` cannot execute scripts, so serving
user SVG is safe. `FileResponse` carries `X-Content-Type-Options: nosniff`
and the correct content type.

## Effective resolution — client-side, by chain

Because a manual override cascades along the project chain (needing
sibling/ancestor rows), the serializer stays **query-free** and exposes two
bricks per project — the client resolves the effective icon:

- `icon` — the state (`"inherit"` / `"none"` / token).
- `icon_override_url` — this project's OWN manual icon URL, or `null`
  (`/project-icons/proj-<hash(id)>/<token>`).
- `repo_icon_url` — the auto-discovered repo icon for its anchor, or `null`
  (`/project-icons/repo-<hash(anchor)>/<token>`; in-memory cache, no disk I/O).

`utils/projectIcon.js` `resolveProjectIconUrl(id, projectsById)` walks the
chain (reusing `ancestorChain` from `projectAgentDefaults.js`): first node
with a token → its `icon_override_url`; first `"none"` → color; else the
project's own `repo_icon_url`; else color. The data store memoizes it as a
`{id → url}` map (recomputed only when `projects` changes) so per-render
lookups are O(1) and cascade updates are reactive.

## Frontend

The color dot was duplicated across ~10 sites (`ProjectBadge`,
`WorktreeBadge`, `SessionListItem`, `SessionSwitcher`, `MessageSnippetsDialog`,
`TerminalSnippetsDialog`, `SearchOverlay`, `ArtifactBookmarkList`,
`CommandPalette` via `staticCommands.js`), each re-implementing the
`--dot-color` circle. To avoid tripling that with icon-or-color logic:

- **`ProjectMark.vue`** presentational component: renders the icon (square,
  the rounded-corner was dropped per feedback) when a URL is present, else the
  color dot. Size via `--project-mark-size` (default = the 8px dot).
- **`useProjectMark`** composable / the store's `resolvedProjectIcons` map
  feed each site the resolved URL + dot color (worktree→parent fallback).
- The two snippet-scope indicators (project/workspace/all) stay plain dots —
  they encode a scope *type*, not project identity.

## UI operations (`ProjectEditDialog`)

Where name + color already live. An icon is a per-project value that cascades,
so the surface is minimal — no repo-level or "apply to all" action:

- **Set icon…** → uploads an image → `Project.icon = <token>` (this project's
  own icon; cascades to inheriting descendants).
- **Use color instead** → `Project.icon = "none"` (this project + inheriting
  descendants show the dot).
- **Follow inherited icon** (shown when overridden) → `Project.icon =
  "inherit"` (re-inherit from the chain / auto).
- **Scan repository…** → `action: "scan"` (read-only): a bounded walk that,
  unlike auto-discovery, does NOT stop at the first match — it collects every
  icon file found (ranked shallowest-first by depth then role/format, deduped
  by content, capped), and returns each as a **normalized `data:` URI preview**
  (`scan_repo_icons`). The dialog shows them as a thumbnail gallery (with the
  relative path each came from); picking one applies it as **this project's**
  icon by reusing the `set` path (the preview IS the normalized image, sent
  back verbatim — no arbitrary server-side file read). Lets the user override
  the auto discovery's single-best choice.

## Migration

- Add `Project.icon` (non-null `CharField`, default `"inherit"`) and
  `Project.icon_anchor` (nullable `CharField`). No data backfill: the
  `"inherit"` default makes every existing row inherit; anchors and repo
  icons are populated by the first post-deploy initial sync. No filesystem
  work in the migration.
- `serialize_project` gains `icon_url`.

## Out of scope (YAGNI)

- No multiple size variants / responsive thumbnails — one normalized image.
- No fetching favicons from remote URLs (only files already on disk).
- No animated-icon handling beyond passing a discovered `.gif` through the
  allowlist.
- No CLI/MCP surface for icons in v1 (human-only, like sharing) — revisit if
  needed.

## Tests

- Anchor resolution: upward git root; umbrella downward scan (exactly-one,
  zero, ambiguous, depth-2 boundary, heavy-dir skip, visit cap); worktree →
  main repo.
- State machine: `inherit` / `none` / `<token>` resolution incl. repo
  manifest `auto` / `manual` / `cleared`; auto-discovery never clobbers a
  manual/none/cleared choice; re-scan idempotence.
- Normalization: `.ico` → `.png`, oversized raster bounded, `.svg` kept,
  undecodable input rejected.
- Serving: allowlist, filename-shape, symlink-escape confinement, auth
  required, content type + `nosniff`.
- `serialize_project.icon_url` for each state, including subproject
  inheritance and the "apply to all subprojects" reset.

## Touch points

- `core/models.py` (Project fields), migration, `core/serializers.py`
  (`icon_url`).
- `paths.py` (`get_project_icons_dir`), new discovery/normalize/scan module,
  `projects.py` (`ensure_project_icon` + in-memory repo-icon cache load).
- `views.py` (`project_icon`) + `urls.py` (route, SPA-exclusion) +
  `PasswordAuthMiddleware` prefix.
- Frontend: `useProjectMark`, `ProjectMark.vue`, the ~7 dot sites,
  `staticCommands.js`, `ProjectEditDialog.vue`.
- Docs: `SKILLS-AND-CLI.md` unaffected (no CLI surface); `CHANGELOG`
  `[Unreleased]` on implementation.
