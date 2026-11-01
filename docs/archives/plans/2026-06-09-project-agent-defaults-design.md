# Per-project agent settings defaults — design

Status: **design agreed, implementation pending.** A first version shipped as
option B (commit `aee7730c`) and is now known to be wrong (it affects running
sessions). This document is the corrected design (option A) and the plan to get
there. It is written to be self-contained: everything needed to implement is
here, independent of the chat history.

---

## 1. Goal

Let a **project** carry optional defaults that seed a **new** session:

- a default **provider** (`claude_code` / `codex` / …), and
- a per-provider **agent-settings bundle** (the 7-field closed bundle).

These are inherited **up the project hierarchy** (the project, then its parents,
then the global synced defaults) so a sub-project/worktree falls back to its
parent's defaults, ultimately the global defaults — exactly like the global
agent settings, but scoped per project.

**Hard requirement (the whole point of the redesign):** these defaults only
decide the settings **at session creation**. Once a session is launched, changing
a project (or global) default must have **no effect** on that running session.
The user can still change a running session's settings explicitly, and that is
applied. So a session is a **snapshot** taken at creation.

---

## 2. Background: how agent settings work today

### The closed bundle

Seven per-session fields, shared across providers (see CLAUDE.md “Agent Settings
— Closed Bundle”), stored as **nullable** columns on `Session`:

`permission_mode, selected_model, effort, thinking_enabled, claude_in_chrome,
fast_mode, context_max` (plus the hidden `question_widget`).

`NULL` means “no explicit choice → use the default”. The frontend `AgentSettings`
NamedTuple (`src/twicc/providers/helpers.py`) mirrors this; all fields default to
`None`.

### The single resolution function

`BaseProviderHelpers.resolve_agent_settings(source)` in
`src/twicc/providers/helpers.py` (~line 415). For each field: if `source.field`
is non-`None` use it; else look up the provider's default key in
`AGENT_SETTINGS_FIELDS_MAPPING` and read the global synced settings
(`read_synced_settings()` → `settings.json`), with the provider's
`SYNCED_SETTINGS_DEFAULTS` as last resort. Fields a provider doesn't map stay
`None`. Always followed by `enforce_agent_settings_consistency()` (capability
clamps + **retired-model upgrade**).

### Where it is called (the resolution sites)

`resolve_agent_settings` is invoked for **every** point where the agent needs
concrete values, not just creation:

- `core/services/session_creation.py` — new session (WS + CLI drop-file).
- `asgi.py` `_handle_send_message` — resume / send to an existing session.
- `core/services/send_message.py` — CLI send to an existing session.
- `core/services/session_update.py` (×2: hidden-constraints check + live propagation).
- `agent/system_prompt.py` (×2: per-turn mutable-context reconcile + fresh-start seed).
- `providers/claude_code/agent/manager.py` — `_maybe_apply…` on **USER_TURN**.
- `providers/claude_code/cron_restart.py` — cron restart.
- (plus several CLI-only display/preview sites, and `cli/info/presets.py`'s
  `__defaults__` which is intentionally project-independent.)

### The consequence (the bug)

Because a `NULL` field is re-resolved against the **current** default at every
turn, a session **follows** default changes. Change a default → on the session's
next turn, `_maybe_apply…` re-resolves, sees a diff, and applies it. For
model/context this calls `apply_live_settings()` → the Claude SDK `set_model()`,
which the SDK records as a **`/model` command in the transcript** — looking as if
the user typed it. **This is pre-existing for global defaults**; option B simply
extended it to project defaults and made it easy to trigger.

This is exactly what we must stop.

---

## 3. What was shipped (commit `aee7730c`) — option B, to be partly reverted

Option B = “the backend resolves `NULL` against the project chain at every
resolution site”. Shipped and committed but WRONG for the requirement above.

Backend (to be reverted):
- `Project.default_provider` (CharField, null) + `Project.default_agent_settings`
  (JSONField, null) — `{ "<provider>": { "<AgentSettings field>": value } }`,
  canonical wire field names; migration `0105_project_agent_defaults`. **KEEP.**
- `serialize_project` exposes both fields. **KEEP.**
- `views.project_detail` PUT validates + stores them via
  `_clean_project_agent_defaults` and broadcasts `project_updated`. **KEEP.**
- `src/twicc/project_hierarchy.py` — ancestor-chain walk + per-field resolution
  (`project_agent_defaults(project_id, provider)`). **REMOVE** (only the backend
  resolution uses it; the frontend has its own mirror).
- `resolve_agent_settings(source, project_defaults=None)` — added the param.
  **REVERT** to `resolve_agent_settings(source)`.
- `project_defaults` wired into the **9 sites** in §2. **REVERT all 9.**

Frontend (mostly kept):
- `frontend/src/utils/projectAgentDefaults.js` — mirror of the resolution
  (`ancestorChain`, `resolveProjectAgentDefaults`, `resolveProjectDefaultProvider`).
  **KEEP** (it becomes the single resolver, used for display + draft pre-fill).
- `useSessionAgentSettings.js` — `resolvedDefaults` (project chain → global),
  `globalDefaults`, the **reset stack** (`resetStack`, `applyResetTarget`,
  `resolveFromChainSlice`). **KEEP** (see §6 for the reset-target tweak).
- `stores/data.js` — `createDraftSession` / `hydrateDraftSessions` pre-select the
  project's resolved default **provider**. **KEEP**, and EXTEND to pre-fill the
  agent-settings too (§5).
- `ProjectAgentDefaultsSection.vue` — the edit UI (per-provider tabs incl.
  disabled providers, provider icons, default-provider select, “load from”
  picker = ancestor projects / global / presets). **KEEP.**
- `ProjectEditDialog.vue` — two main tabs (“Project” / “Agent settings”), path
  moved under the dialog title, nested-tab event `.stop`, nav hidden in create
  mode. **KEEP.**
- `AgentSettingsPopover.vue` — renders the reset stack. **KEEP.**

---

## 4. The corrected design (option A): resolve at draft creation, snapshot

### Principle

Resolution happens **once, at draft creation, on the frontend**. The draft is
**pre-filled** with the resolved concrete values (project → parents → global),
proposed to the user as the session's settings. The user can tweak any field.
On launch, the concrete values are sent and stored. The session is then a
**snapshot** — the backend never re-resolves project defaults for it.

The backend goes back to its pre-feature behavior (`NULL → global`), which now
only serves **legacy** sessions (new sessions have no `NULL`). The project
defaults are a pure-frontend concern (display + pre-fill). The backend only
**stores** the project-defaults config (for the edit UI to read/write).

### Decisions (all agreed)

1. **Snapshot all fields at creation.** Not just project-set fields — *all* of
   them (project values where set, global default elsewhere). A new session has
   no `NULL` for any provider-supported field, so nothing about it follows any
   default afterwards.
2. **Resolution at draft creation (frontend), pre-filled as the proposed
   settings.** `createDraftSession` resolves project → global and stores the
   concrete bundle on the draft (and IndexedDB). The popover shows it; the user
   adjusts; launch freezes it.
3. **Model stored as the alias.** The resolved default model is the alias
   (`"opus"`), not a pinned version. Storing the alias preserves **version
   auto-upgrade** (the SDK maps `"opus"` → latest opus at run time). A user who
   explicitly picks a versioned model (`"opus-4.7"`) stores that concrete value
   (it is a non-default override) and stays pinned. ⇒ “alias unless the user
   chose a version” falls out for free.
4. **Pure-frontend resolution; CLI deferred.** The backend stops resolving the
   project chain entirely. A session created via the **CLI** (no draft) does
   **not** inherit project defaults for now — to be added later (with the CLI
   work) by materializing at the single backend creation point. This reverses
   the earlier “CLI inherits automatically” that option B gave for free; it is
   an accepted, explicit trade.
5. **Legacy sessions untouched.** Sessions created before this change keep their
   `NULL` fields and the pre-feature behavior (`NULL → global` at run time, no
   project). We do not (cannot reliably) backfill them — we don't know the value
   that was the default at their creation.
6. **Reset = re-apply the *current* resolved defaults.** Resetting is an explicit
   user action, so it (re)reads today's defaults. In the all-concrete model the
   reset targets set **concrete** values (no `NULL`), see §6.
7. **Diff-marking baseline = the project-resolved default, for ALL sessions.**
   The popover marks a field as “changed/forced” relative to the **project-
   resolved** default (the full chain: project → parents → global), **not** the
   bare global default — for launched sessions too. This is what makes the nice
   “your defaults at launch were X, the defaults are now Y” readout possible.
   So `resolvedDefaults` is **not** conditioned on draft; the project chain stays
   the baseline everywhere.

### What each field needs (why §decision 1 + 3 are safe)

| field | needs | snapshot at creation? |
| --- | --- | --- |
| `selected_model` | freeze the *family*, auto-upgrade the version | yes — store the **alias** |
| `context_max` | a fixed value | yes |
| `effort` / `thinking_enabled` / `permission_mode` / `claude_in_chrome` / `fast_mode` | a fixed value | yes |

No field wants to “follow the current default” once running. The only legitimate
per-turn dynamics are `enforce_agent_settings_consistency` (capability clamp +
retired-model upgrade, works on concrete values) and the user's explicit changes.
Neither needs the `NULL → default` follow.

---

## 5. Resolution + hierarchy (the algorithm, frontend)

Lives in `frontend/src/utils/projectAgentDefaults.js` (already implemented; it is
the canonical resolver in the new design).

**Ancestor chain** (`ancestorChain(projectId, projectsById)`): ordered
`[self, parent, grandparent, …]`. At each node the parent is its `worktree_of`
main repo if set, else its **nearest path ancestor** (longest registered project
directory that is a strict path-prefix, segment-by-segment so `/a/b` is not an
ancestor of `/a/bc`). Cycle-guarded. **Unfiltered** (every ancestor is a step,
even if it sets nothing — a different field may be set higher).

**Per-field resolution** (`resolveProjectAgentDefaults(projectId, provider,
projectsById)`): walk the chain nearest-first; for each field, first non-`null`
value in `node.default_agent_settings[provider]` wins; missing → fall to the
global default (provider store `defaultXxx`). Field keys are the canonical wire
names.

**Default provider** (`resolveProjectDefaultProvider`): first non-null
`default_provider` up the chain, else the global `defaultProvider`.

This mirrors what the backend `project_hierarchy.py` did; after this change the
backend version is removed and the frontend one is the only resolver.

---

## 6. Frontend behavior (popover, draft pre-fill, reset, edit UI)

### Draft pre-fill (the core new piece)

`stores/data.js`:
- `createDraftSession(projectId)`: in addition to the provider preselect, resolve
  the **agent-settings** bundle (project chain → global, alias for model) and
  store the 7 concrete fields on the draft session object + persist to IndexedDB
  (`draftStorage.js`). The provider's global defaults come from
  `getProviderStore(provider)`; the chain from `resolveProjectAgentDefaults`.
- `hydrateDraftSessions`: restore those fields from IndexedDB on startup.
- `draftStorage.js`: extend the persisted draft shape to carry the 7 fields.

Result: when the popover opens for a fresh draft, `useSessionAgentSettings`
loads `selectedXxx` from the (now pre-filled) draft fields → the user sees the
proposed concrete settings, editable. On launch the concrete values are sent and
stored → snapshot.

Note the model is stored as the **alias** (the resolved default's value), never a
pinned version, unless the user explicitly chose a version.

### `resolvedDefaults` (baseline) — keep project chain for ALL sessions

`useSessionAgentSettings.js` already computes `resolvedDefaults` = per field
`projectChain(field) ?? providerStore.defaultXxx`. **Keep it for every session
(drafts and launched), do NOT condition on draft.** It is the display baseline:
- effective value rendering,
- diff-marking (a field differs ⇒ “changed/forced” relative to the project-
  resolved default),
- the “Default (X)” label shown per field.

This gives the desired “your launch-time defaults vs the current defaults”
comparison for running sessions.

### Reset stack

`useSessionAgentSettings.resetStack` (already implemented) lists: **Project
defaults**, one entry per **ancestor** project that defines its own bundle, and
**Global defaults** (collapses to a single “Reset to defaults” when no project in
the chain sets anything). `AgentSettingsPopover.vue` renders it under
“Reset to…”.

Tweak for the all-concrete model: in option B the “Project defaults” target was a
`NULL`/follow reset (`applyResetTarget({follow:true})` → `resetAllToDefaults`).
In the snapshot model there is no follow — **every reset target sets concrete
values** (re-apply the resolved bundle from that level). So “Project defaults”
becomes a concrete re-apply of the current project-resolved bundle, like the
ancestor/global targets. (`resolveFromChainSlice` already produces these
concrete bundles; just route “Project defaults” through it instead of the follow
branch.)

### Edit UI (`ProjectAgentDefaultsSection.vue` + `ProjectEditDialog.vue`)

No change required from option B — keep as shipped:
- Project edit dialog: two main tabs **Project** / **Agent settings**; path under
  the title; create mode hides the tab nav (single tab); nested per-provider
  `wa-tab-group` has `@wa-tab-show.stop @wa-tab-hide.stop` so it never leaks to
  the main tab-group; the main tab is controlled (`:active` + sync) and reset to
  “Project” on open.
- Agent settings tab: default-provider select (with provider icons + “Inherit”),
  per-provider tabs over **all registered** providers (incl. disabled, badged),
  each editing that provider's bundle via the shared field hooks
  (`getFieldLabel`/`getFieldChoices`/`getModelSelectGroups`), with “Inherit”
  sentinel per field, and a “Load from…” picker = ancestor projects' bundles /
  global defaults / named presets.
- Saved via the project PUT (`getChangedFields()` folded into the existing
  body); only changed keys are sent.

---

## 7. Backend after the change

- Revert `resolve_agent_settings` to `(self, source)` — drop `project_defaults`.
- Revert the 9 call sites to plain `resolve_agent_settings(...)`.
- Delete `src/twicc/project_hierarchy.py`.
- Keep: `Project.default_provider`, `Project.default_agent_settings`, migration
  `0105`, `serialize_project` exposure, `project_detail` PUT +
  `_clean_project_agent_defaults` (validation: provider keys ∈ Provider values,
  field names ∈ AgentSettings fields, hidden fields stripped, empty bundles
  dropped) + `project_updated` broadcast.

Net backend role: **store** the per-project defaults config and serve it to the
frontend. No runtime resolution of the project chain anywhere. `NULL → global`
stays in `resolve_agent_settings` as the legacy fallback (new sessions have no
`NULL`, so they never hit it).

---

## 8. Implementation plan (file-by-file)

Backend (revert + keep):
1. `providers/helpers.py`: `resolve_agent_settings(self, source)` — remove the
   `project_defaults` param + the project lookup line; keep the docstring's
   global-fallback description.
2. Revert the 9 sites (remove the `project_agent_defaults(...)` load + the
   `project_defaults=` kwarg): `session_creation.py`, `asgi.py`,
   `system_prompt.py` (×2), `session_update.py` (×2, plus the shared
   `project_defaults` it computed after the lookup), `send_message.py`,
   `claude_code/agent/manager.py`, `claude_code/cron_restart.py`.
3. Delete `src/twicc/project_hierarchy.py`.
4. Leave models/migration/serializer/views untouched (already correct).

Frontend (add pre-fill, small reset tweak, keep the rest):
5. `stores/data.js` + `utils/draftStorage.js`: pre-fill the draft's 7 agent
   settings at `createDraftSession` (resolve project→global, alias for model),
   persist + rehydrate.
6. `composables/useSessionAgentSettings.js`: route the reset stack's “Project
   defaults” target through the concrete bundle (drop the follow/`NULL` branch);
   keep `resolvedDefaults` project-chain for all sessions (do NOT add a draft
   condition).
7. Keep `projectAgentDefaults.js`, `ProjectAgentDefaultsSection.vue`,
   `ProjectEditDialog.vue`, `AgentSettingsPopover.vue` as committed.

Reserved ops (user runs): no new migration (0105 already applied); restart not
needed for pure-frontend edits, but backend reverts (steps 1–3) need a backend
restart.

---

## 9. Known issues / deferred

- **CLI inheritance deferred.** CLI-created sessions won't pick up project
  defaults until we add backend materialization at the single creation point
  (the CLI work lot). Documented as an explicit trade.
- **Legacy `NULL` display edge.** For a legacy session with a `NULL` field, the
  popover shows the **project-resolved** baseline (per §decision 7) while the
  backend actually resolves that `NULL` to the **global** default (no project).
  They differ only when a project overrides a field to a value ≠ global, for a
  field the legacy session left `NULL` — rare, and legacy is “don't touch”.
  Acceptable; revisit only if it bites.
- **`/model` phantom command.** Confirmed root cause: a settings re-resolution
  that changes model/context calls `apply_live_settings()` → SDK `set_model()`,
  recorded as a `/model` command in the transcript. The snapshot design removes
  the re-resolution trigger, so this disappears for new sessions.
- **Draft snapshot timing.** Pre-fill freezes at **draft creation**. If a default
  changes between draft creation and launch, the draft keeps its creation-time
  values (it does not follow). If “follow until launch” is ever wanted, switch to
  “keep `selectedXxx=null` + materialize at send” — same end state, snapshot at
  send instead of at draft creation.

---

## 10. Why B → A (rationale, for the record)

Option B (“backend resolves `NULL` per turn against the project chain”) was
chosen first for clean `NULL`-means-follow semantics and free CLI inheritance.
But “follow” is precisely the behavior the requirement forbids: a running session
must not move when a default changes. The supposed need to keep `NULL` for model
**version** auto-upgrade dissolves once you realize the default model is an
**alias** (`"opus"`), and the alias→version mapping happens at the SDK level —
so storing the alias concretely keeps auto-upgrade without `NULL`. With that, the
only correct model is **snapshot at creation** (option A): resolve once, store
concrete (alias for model), never re-resolve project at run time.
