# Dockable layout — persistence + named layouts (implementation plan)

**Date:** 2026-06-19 · **Branch:** `layout` · **Status:** **BUILT** — steps 1–4 + the menu's scope-default
rows + alphabetical listing are all implemented and live-verified. The §7 doc chore is **done**
(`layouts.json` + `Session.layout` / `Project.default_layout_id` are in `CLAUDE.md`/`AGENTS.md`). Only the
§6 v2 items remain deferred.

Companion to `2026-06-16-dockable-layout-design.md` (§ "Storage / persistence") and the impl handoff
`2026-06-17-dockable-layout-impl-handoff.md` (§ "What remains → Persistence"). This plan supersedes the
short persistence bullets there with the **named-layouts** model agreed in discussion on 2026-06-19.

The keystone gap today: the per-session layout intention is in-memory only (`store.localState.sessionLayout`,
ephemeral) — it resets on reload / KeepAlive eviction. And there is no way to save, name, reuse, or default
a layout. Persistence and the save/select/default machinery are **one feature** — you cannot ship one without
the other (you can't "reload a saved layout" if nothing is saved).

---

## 0. Decisions locked (do NOT re-litigate)

These were converged with the user across the 2026-06-19 discussion. Treat as fixed unless the user reopens.

1. **Three distinct notions**, never conflated:
   - **Catalog** = the named layouts (content). Stored in a synced user-global file `layouts.json`.
   - **Selection** = which layout is a scope's default (a *reference*, an id). Global → `settings.json`;
     project → `Project.default_layout_id` column.
   - **Session snapshot** = a session's live intention (a *copy*, no origin). Stored on `Session.layout`,
     synced via the DB, debounced.
2. **Single pane** ("no docks") = a **synthetic** layout, fixed id `"single-pane"`, injected in code, present
   in *every* selector, never editable / overwritable / deletable, **never written to `layouts.json`**. Its
   intention is the empty intention.
3. **Two different "empty" values — never merge them:**
   - **inherit** = absence of a choice → walk up the chain → DB `NULL` (nullable column / `None` / JS absent).
   - **forced single pane** = an explicit choice → the id string `"single-pane"` (stops the walk).
4. **Resolution mirrors agent settings**: project default → parent/worktree → … → global, first non-`NULL`
   wins; global is always concrete (defaults to `"single-pane"`). Resolved **once at session creation** and
   snapshotted onto `Session.layout`. (Difference vs agent settings: `Session.layout` is **mutable** after
   creation — the snapshot is only the seed; later edits persist normally.)
5. **References by id, display by name** → renaming a named layout is free (no cascade). Resolution is
   **dangling-tolerant**: an unknown id falls back to inherit → global → single pane (never crashes).
6. **Session = snapshot copy, no origin.** Loading a layout copies its intention into the session; later tweaks
   never touch the catalog until an explicit Save. Editing/overwriting a named layout does **not** retroactively
   change existing sessions (they hold copies); it affects only *future* sessions created where it is the default.
7. **Save dialog**: "Overwrite an existing layout" (picker of named layouts) **or** "New layout" (name field,
   prefilled `Default`). Optional checkbox **"Set as global default"**, checked by default on the very first save
   (when the global default is still `single-pane`). No origin tracking.
8. **Buttons in the main-area tab nav** (right cluster, order): `[ Select ▾ ] [ Save ] [ Maximize ]`.
   - **Select** is visible **always** (incl. single pane — the only way to *enter* a layout from single pane).
   - **Save** and **Maximize** are visible **only when not single pane** (gated on a new `hasDocks`, i.e. the
     render has ≥1 non-center region or a gutter — not merely `dockingRendered`).
9. **One catalog, user-global.** No per-project named layouts. No workspace tier.
10. **`maximized` is transient** — never persisted (already the case).
11. **No schema `version` field for now** — a tolerant merge with `EMPTY_INTENTION` (missing → default, unknown
    key → ignored) handles additive/removed fields for free. Add a `version` only when a *breaking* reshape lands.

---

## 1. Data shapes

### 1.1 The intention (already exists)
`EMPTY_INTENTION` (`frontend/src/composables/useSessionLayout.js`):
`{ assignment, collapsed, activeSide, activeResize, activeByGroup, resizeFractions, maximized }`.

- **Template subset** (what a named layout stores) = `{ assignment, collapsed, resizeFractions }` — the
  *structure*. `activeByGroup` / `activeSide` / `activeResize` are session-runtime; **not** part of a named
  layout. `maximized` is excluded everywhere.
- **Session subset** (what `Session.layout` stores) = the full intention **minus `maximized`**.

### 1.2 A named layout (catalog entry)
```jsonc
{ "id": "lay_<random>", "name": "Wide", "intention": { "assignment": {…}, "collapsed": [...], "resizeFractions": {…} } }
```
`id` generated with a reserved-safe prefix; the id `"single-pane"` is **reserved** (a named layout may never
take it). `layouts.json` shape mirrors `workspaces.json`: `{ "layouts": [ {…}, … ] }`.

### 1.3 The synthetic single pane
Not stored. Injected in the frontend catalog store as `{ id: "single-pane", name: "Single pane", synthetic: true }`.
Resolving it yields the empty intention.

### 1.4 The two sentinels recap
| Scope field | NULL / absent | `"single-pane"` | `<named id>` |
|---|---|---|---|
| `Project.default_layout_id` (nullable) | inherit (walk up) | forced single pane | that named layout |
| `settings.json.default_layout_id` (always set) | — (root, never absent) | single pane (the default) | that named layout |
| `Session.layout` | (it's the intention blob itself, not an id) | | |

---

## 2. Backend changes (file by file)

Mirror the **`workspaces.json`** pipeline for the catalog and the **pin** pipeline for `Session.layout`.

### 2.1 `src/twicc/paths.py`
- Add `get_layouts_path() -> Path` returning `get_data_dir() / "layouts.json"` (mirror `get_workspaces_path`, `:125`).
- Update the data-dir docstring/inventory in `CLAUDE.md` / `AGENTS.md` (synced-config file list) — keep both in sync.

### 2.2 `src/twicc/layouts.py` (NEW)
Mirror `src/twicc/workspaces.py`: `read_layouts() -> dict` (default `{"layouts": []}`), `write_layouts(data)`
under `file_lock(get_layouts_path())` + `atomic_write_json`. A module-level lock comment like workspaces'.
(Catalog mutations from the CLI later reuse this; out of scope now.)

### 2.3 `src/twicc/asgi.py` (UpdatesConsumer)
Mirror the workspaces WS sync exactly:
- **On connect push**: alongside the `workspaces_updated` block (`:500`), send `layouts_updated`
  `{ "type": "layouts_updated", "layouts": read_layouts().get("layouts", []) }`, gated by `_should_send`.
- **Client→server**: add `elif msg_type == "update_layouts": await self._handle_update_layouts(content)` in
  `receive_json` (next to `update_workspaces`, `:624`).
- **`_handle_update_layouts`** (mirror `_handle_update_workspaces`, `:1536`): validate `content["layouts"]` is a
  list, `file_lock(get_layouts_path())` + `atomic_write_json({"layouts": layouts})`, then broadcast
  `layouts_updated` to group `"updates"` (`:1560` pattern). (Optional optimistic `baseVersion` later if multi-device
  catalog edits clash — defer; last-write-wins is acceptable for a small catalog.)

### 2.4 `src/twicc/synced_settings.py` + `frontend/src/constants.js`
- Add `"defaultLayoutId": "single-pane"` to `_GENERIC_SYNCED_SETTINGS_DEFAULTS` (`synced_settings.py:38`, beside
  `defaultProvider`).
- Add `"defaultLayoutId"` to `SYNCED_SETTINGS_KEYS` (`constants.js:204`) so it round-trips through the existing
  synced-settings machinery (no bespoke endpoint needed — reuses `update_synced_settings`).

### 2.5 `src/twicc/core/models.py`
- **`Session.layout = models.JSONField(default=dict, blank=True)`** (mirror `Session.annotations`, `:454`). Holds
  the session subset intention (or `{}` = single pane). Migration required.
- **`Project.default_layout_id = models.CharField(max_length=64, null=True, blank=True, default=None)`** (mirror
  `Project.default_provider`, `:133`). `NULL` = inherit. Migration required.
- One migration for both columns.

### 2.6 `src/twicc/serializers.py`
- `serialize_session`: add `"layout": session.layout` (beside `pinned` / `annotations`).
- `serialize_project`: add `"default_layout_id": project.default_layout_id` (beside `default_provider`).

### 2.7 Session write path
- `core/services/session_update.py`: add `apply_session_layout_change(session, layout)` (mirror
  `apply_session_pinned_change`, `:525`) — validate/merge the blob (tolerant merge against the empty shape),
  write under `run_under_db_write_lock`, return the session. Caller broadcasts `session_updated`.
- `views.py` `session_detail` PATCH (`:916`): add a `layout` branch calling the service then the standard
  `session_updated` broadcast to `"updates"`. Frontend merges via the existing `session_updated → updateSession`
  path — **no new WS handler** needed.

### 2.8 Project write path
- `views.py` `project_detail` PUT (`:522`) already round-trips arbitrary project fields; accept
  `default_layout_id` and persist it. Add light validation in `core/services/project_mutation.py`
  (`clean_*` neighborhood): must be `None`, `"single-pane"`, or a string (dangling tolerated at resolution).
  `project_updated` broadcast already in place.

### 2.9 Resolution mirror (CLI / backend creation)
- New `src/twicc/project_layout_default.py` (mirror `project_agent_defaults.py`): `resolve_project_layout_default(
  project_id, directory=None) -> str` — same `ancestor_chain` walk (worktree_of → path ancestors), first
  non-`NULL` `default_layout_id` wins, fallback to the global `settings.json.defaultLayoutId`, ultimate fallback
  `"single-pane"`. Used at CLI session creation only (header comment: creation-only, never per-turn).
- `core/services/session_creation.py` `create_session_from_payload` (`:52`): accept an optional `layout` in the
  payload; if absent, resolve via the above. Stash pending (mirror `set_pending_session_attributes`, `:333`);
  the watcher freezes it onto `Session.layout` at row creation (`sessions_watcher.py` pop path, `:603`).

---

## 3. Frontend changes (file by file)

### 3.1 `frontend/src/stores/layouts.js` (NEW — mirror `stores/workspaces.js`)
State `{ layouts: [] }`. 
- `applyLayouts(layouts)` (from WS), `saveLayouts()` → `sendLayouts(this.layouts)` over WS (add `sendLayouts` to
  `useWebSocket.js`, mirror `sendWorkspaces`).
- Getters: `getAllLayouts`, `getLayoutById(id)`.
- **Synthetic single pane**: a getter `selectableLayouts` = `[{id:'single-pane', name:'Single pane', synthetic:true}, ...this.layouts]`
  used by every selector. `intentionForId(id)` → `id==='single-pane'` ⇒ `{}` (empty); named ⇒ deep-clone of its
  `intention`; unknown ⇒ `{}` (dangling-tolerant).
- CRUD by id: `upsertLayout({id?, name, intention})` (generate id if new; reserve `single-pane`),
  `renameLayout(id, name)`, `deleteLayout(id)` — each followed by `saveLayouts()`.

### 3.2 `frontend/src/composables/useWebSocket.js`
- Handle `case 'layouts_updated': layoutsStore.applyLayouts(msg.layouts)`.
- Add `sendLayouts(layouts)` (mirror `sendWorkspaces`, `:367`-area) emitting `{type:'update_layouts', layouts}`.

### 3.3 `frontend/src/utils/layoutDefaults.js` (NEW — mirror `utils/projectAgentDefaults.js`)
- `resolveProjectLayoutId(projectId, projectsById, globalDefaultId)` — same `ancestorChain` walk
  (`projectAgentDefaults.js:67`), first non-null `default_layout_id` wins, fallback `globalDefaultId`, ultimate
  `'single-pane'`. Returns an **id**.
- Consumers map id → intention via `layoutsStore.intentionForId(id)`.

### 3.4 `frontend/src/stores/data.js`
- **Seed from persisted `Session.layout`**: when a session is loaded/updated, hydrate
  `localState.sessionLayout[sessionId]` from `session.layout` if present (tolerant merge with the empty shape).
  Guard against echo (an apply-from-remote flag, like settings' `_isApplyingRemoteSettings`).
- **Persist on mutation (debounced)**: the existing `setTabDock` / `minimizeDock` / `setLayoutResizeFraction` / …
  actions, after mutating, call a new `persistSessionLayoutDebounced(sessionId)` (~500 ms; flush immediately on
  drag-end for fractions). It PATCHes `/api/projects/<id>/sessions/<id>/ {layout: <session subset>}` (mirror
  `setSessionPinMode`, `:4396`) — optimistic local already done. The `session_updated` broadcast echoes back; the
  apply-from-remote guard prevents a re-persist loop. **`maximized` is stripped** before persisting.
- **`loadLayoutIntoSession(sessionId, layoutId)`**: replace `sessionLayout[sessionId]` with a deep copy of
  `intentionForId(layoutId)` (resetting runtime: clear `activeByGroup`, default `activeSide/activeResize`,
  `maximized=null`), then persist. `single-pane` ⇒ empty.
- **Draft seeding at creation** (mirror `_resolveDraftAgentSettings`, `:1260` / `createDraftSession`, `:1310`):
  resolve `resolveProjectLayoutId(projectId, …, settings.defaultLayoutId)` → `intentionForId` → snapshot the
  session-subset into `draft.layout`; carry in the create payload so the backend freezes it.

### 3.5 `frontend/src/composables/useSessionLayout.js`
- No model change (it already reads `store.getSessionLayout(sessionId)`), but: ensure the read reflects the
  hydrated/persisted intention, and add a thin `loadLayout(layoutId)` passthrough to
  `store.loadLayoutIntoSession`. Keep `maximized` purely transient (already excluded from persistence).

### 3.6 `frontend/src/views/SessionView.vue`
- New computed `hasDocks` = `layout.render.value.regions.some(r => r.kind !== 'center') || layout.render.value.gutters.length > 0`.
- Tab-nav right cluster: add **Select** button (always; `slot="nav"`), gate **Save** + **Maximize** on `hasDocks`
  (Maximize currently `canMaximizeCenter` — fold `hasDocks` in). Keep `.center-maximize` placement; add
  `.layout-save` / `.layout-select` with the same `:first-of-type { margin-inline-start:auto }` right-alignment
  idiom so the leading button pushes the cluster right.
- Wire `LayoutSelectMenu` (open on Select) and `LayoutSaveDialog` (open on Save).

### 3.7 `frontend/src/components/session/layout/LayoutSelectMenu.vue` (NEW)
`wa-dropdown` with sectioned items (separators only when a section is non-empty):
1. **Single pane** (always; also the reset).
2. — sep — **Scope defaults** that resolve to a *real* layout (skip ones resolving to single-pane, to avoid the
   duplicate): `Worktree default (Name)` if the session's project is a worktree and the parent chain sets one;
   `Project default (Name)`; `Global default (Name)`.
3. — sep — **Named layouts** (the catalog).
4. — sep — **Manage layouts…** (opens the catalog manager, §3.10).
Selecting 1–3 ⇒ `layout.loadLayout(id)`.

### 3.8 `frontend/src/components/session/layout/LayoutSaveDialog.vue` (NEW)
Follow the `ProjectEditDialog.vue` dialog patterns (form + submit-outside-form + `@wa-after-show` focus + nested
`wa-*` bubbling guards). Body:
- Radio / segmented: **Overwrite existing** (a `wa-select` of named layouts; hidden/disabled if catalog empty)
  **|** **New layout** (text input, prefilled `Default`, trimmed, client-side uniqueness check).
- Checkbox **"Set as global default"** — checked by default iff `settings.defaultLayoutId === 'single-pane'`
  (first-save case), else unchecked.
- On submit: snapshot the session's **template subset** (`{assignment, collapsed, resizeFractions}`) →
  `layoutsStore.upsertLayout(...)` → if checked, set `settings.defaultLayoutId = id` (synced settings auto-persist).

### 3.9 Settings panel — "Layouts" section
- In the settings UI (`SettingsPopover.vue`, the surface that hosts `defaultProvider`), add a **"Layouts"** section
  with **two controls, kept light** (it's a popover):
  1. a **"Default layout"** `<select>` bound to `settings.defaultLayoutId`, options = `layoutsStore.selectableLayouts`
     (Single pane + named). No "Inherit" at global (it's the root).
  2. a **"Manage layouts…"** button that opens the shared `LayoutManagerDialog` (§3.10). No rename/delete inline —
     the popover stays minimal; all management is in the dialog.

### 3.10 `LayoutManagerDialog.vue` (NEW) — the single home for rename / delete
A dedicated dialog (follow `ProjectEditDialog.vue` dialog patterns), opened from **both** the Settings section's
"Manage layouts…" button **and** the Select menu's "Manage…" entry (§3.7) — one reusable surface, rename/delete
logic lives here only (DRY). Lists named layouts with **rename** (inline; free, id-based) and **delete**. On
delete, if the id is referenced by the global default or any `Project.default_layout_id` (queryable in-memory from
the stores), show a **reassignment** confirmation: "Used by global default and N projects — redirect them to:
[Single pane ▾ / other named]". Repoint `settings.defaultLayoutId` and the affected projects (PUT each).
Resolution is dangling-tolerant regardless, so a missed reference degrades to inherit→global→single-pane rather
than breaking.

### 3.11 `frontend/src/components/project/ProjectEditDialog.vue`
- Add a **"Default layout"** picker to the project form (beside the agent-defaults section), bound to
  `default_layout_id`, options = **Inherit** (value `null`) + Single pane + named. Persist via the existing
  `PUT /api/projects/<id>/` (include `default_layout_id` in the changed-fields payload). Worktree projects show a
  hint that "Inherit" resolves through the parent repo then global.

---

## 4. Edge cases & invariants

- **Dangling id** (deleted named layout still referenced): resolution returns inherit→global→single-pane. Never
  throws. The reassignment dialog is cosmetic cleanup, not correctness.
- **Reserved id**: `upsertLayout` must reject/avoid `"single-pane"` for new entries.
- **Echo loop**: persisting `Session.layout` triggers a `session_updated` broadcast that re-enters
  `updateSession`; the apply-from-remote guard must prevent re-persisting the just-applied value.
- **Debounce + drag**: resize fractions mutate continuously — debounce (~500 ms) and/or flush on drag-end; discrete
  actions (place/minimize/load) can persist promptly (still debounced to coalesce bursts).
- **Tolerant merge**: hydrating `Session.layout` or a catalog `intention` always merges against the current empty
  shape (fill missing, drop unknown) — this is the no-version migration strategy.
- **`maximized` never persisted**: strip on every write; never read from persistence.
- **Multi-device session edits**: last-write-wins via the standard broadcast is acceptable; the `baseVersion`
  optimistic idiom (from `_handle_update_synced_settings`) is a later hardening if needed.

---

## 5. Build sequence (each step independently testable)

1. **Session persistence (the keystone).** `Session.layout` column + serializer + PATCH/service/broadcast + the
   `data.js` hydrate + debounced persist + echo guard. *Outcome:* a session's layout survives reload and follows
   you across devices. (No catalog yet; new sessions still start single pane.)
2. **Catalog + Save/Select.** `layouts.json` (paths/module/WS) + `stores/layouts.js` + `useWebSocket` wiring +
   the synthetic single pane + the two buttons + `LayoutSaveDialog` + `LayoutSelectMenu` (catalog section only,
   no scope-default rows yet). *Outcome:* save/overwrite/load named layouts within a session.
3. **Selections + inheritance.** `settings.defaultLayoutId` + `Project.default_layout_id` (+ ProjectEditDialog &
   Settings pickers) + `resolveProjectLayoutId` (FE) and `project_layout_default.py` (CLI) + seed-at-creation in
   `createDraftSession` / `create_session_from_payload` / watcher freeze + the scope-default rows in the Select
   menu. *Outcome:* new sessions open with the resolved default; the "Set as global default" checkbox works.
4. **Catalog management.** Rename + delete with reassignment dialog (§3.10).

Steps 1–2 are the smallest *useful* unit (persistence is meaningless to the user without save/load).

---

## 6. Deferred (explicitly out of scope here)

- Per-device localStorage override of a synced session layout (design-doc v2 escape hatch).
- Per-project named layouts; workspace-tier default.
- Reset-to-{project,default,tabbed} as distinct affordances (the Select menu's "Single pane" + scope rows already
  cover the practical cases).
- A schema `version` field (add only on a breaking reshape).
- CLI commands to manage the catalog (`layouts.json` is reachable; expose later if asked — then update
  `SKILLS-AND-CLI.md`).

---

## 7. Doc/sync chores when this lands

- **DONE** — `CLAUDE.md` + `AGENTS.md`: `layouts.json` added to the synced-config inventory; `Session.layout` /
  `Project.default_layout_id` noted in the models section (both files kept in sync).
- The settings panel's shortcut/section docs if any new shortcut is added (none planned).
- Mark the impl handoff's "Persistence" item as in-progress/done as steps complete.
