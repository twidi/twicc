# AutoAttach parent terminals into children (tmux-only)

- **Date:** 2026-06-20
- **Status:** Approved (brainstorm) → implementing
- **Branch:** `attach-parent-terms`
- **Builds on:** `2026-06-20-attach-parent-terminals-design.md` (the manual attach
  feature, already shipped as commit `588b4e46`). Read it first — this design
  reuses its pool/move architecture, `ancestorScopes`, and attachment registry.

## Goal

The shipped feature lets a child panel **manually** attach a terminal from an
ancestor scope (worktree → project → workspace → global). This adds an opt-in
flag on an *ancestor* terminal — **AutoAttach in children** — so the terminal
appears automatically as an attached tab in **every** descendant panel, where it
**cannot be detached** (the parent owns the decision).

"Children" is the inverse of `ancestorScopes`: a panel auto-attaches a flagged
ancestor terminal iff that terminal's scope is one of the panel's ancestors. This
is fully recursive — a flagged **Project** terminal shows up in the project's
worktree panels, in the sessions of the project, and in the sessions of its
worktrees; a flagged **Global** terminal shows up everywhere.

## Decisions (locked during brainstorm)

1. **tmux-only.** The flag is persisted as a tmux user option on the session.
   Non-tmux terminals have **no** server-side existence (a raw `pty.fork()` dies
   on WS close, with nowhere to store metadata), so AutoAttach is **unavailable**
   for them: the toggle is simply not rendered. There is no in-memory fallback —
   a single code path.
2. **Persistence = tmux user option** `@twicc_autoattach`, the exact twin of the
   existing `@twicc_label` label mechanism. Survives browser reload and backend
   restart (as long as the tmux server lives).
3. **UI** = a toggle button in the terminal action bar, acting on the active own
   tab, shown only on attachable-ancestor scopes that use tmux.
4. **Child indicator** = the same `link` icon as a manual attachment; the only
   distinguishing affordance is the **absence of the Detach button**.

## Why tmux-only is the whole story

There is **no** backend store for terminal metadata — no DB table, no JSON file.
Terminal labels live entirely inside tmux as a session user option
(`@twicc_label`), read back in `list_tmux_terminals` via the `list-sessions`
format string and surfaced through the `terminal_list` discovery. In non-tmux
mode `list_tmux_terminals` returns `[]` and `_handle_list_terminals` reports an
empty list. So "persisted backend" can only mean "a tmux user option", which by
construction excludes non-tmux. AutoAttach therefore mirrors labels exactly:
tmux carries the state, the rest of the stack just transports it.

## Architecture

### 1. Backend — `src/twicc/terminal.py`

- New constant `_TMUX_AUTOATTACH_OPTION = "@twicc_autoattach"`.
- `TerminalInfo` (NamedTuple) gains `auto_attach: bool = False`.
- `list_tmux_terminals`: extend the `list-sessions` format string to
  `"#{session_name}\t#{@twicc_label}\t#{@twicc_autoattach}"`, split on `\t`, and
  set `auto_attach = (raw == "1")`. (An unset user option expands to the empty
  string, so absent → `False`.)
- `set_tmux_terminal_autoattach(terminal_context, terminal_index, enabled)`:
  twin of `set_tmux_terminal_label` — `tmux_set_option(..., "1")` when enabled,
  else unset the option. Returns `bool`.
- `_unset_tmux_terminal_autoattach`: twin of `_unset_tmux_terminal_label`.

### 2. Backend — `src/twicc/asgi.py`

- `_handle_list_terminals`: add to the `terminal_list` payload a sibling of
  `labels`:
  ```python
  "autoAttach": {str(t.index): t.auto_attach for t in terminals if t.auto_attach},
  ```
  (Only truthy entries are sent, like labels — absent key = `False`.)
- New handler `_handle_set_terminal_autoattach`, registered for message type
  `set_terminal_autoattach`, twin of `_handle_rename_terminal`:
  - validates `terminal_context` + `terminal_index` + `enabled` (bool);
  - calls `set_tmux_terminal_autoattach` off-thread;
  - broadcasts `terminal_autoattach_changed
    {terminal_context, terminal_index, enabled}` to the `updates` group for
    cross-device sync.
- Add the dispatch case in the message handler (next to `rename_terminal`).

### 3. Frontend store — `frontend/src/stores/terminalTabs.js`

- New state `autoAttach: {}` — `contextKey → { [index]: true }` (only truthy
  entries kept, matching `labels`).
- Actions:
  - `setAutoAttachMap(contextKey, map)` — replace the whole map for a context
    (from `terminal_list`); keep only truthy values.
  - `setAutoAttach(contextKey, index, enabled)` — set/clear one entry (from the
    broadcast and from the optimistic local write on toggle).
  - getter `isAutoAttach(contextKey, index)` → `boolean`.
- `removeIndex` also deletes `this.autoAttach[contextKey]?.[index]`.

### 4. Frontend WS — `frontend/src/composables/useWebSocket.js`

- `terminal_list` case: after `setLabels`, call
  `store.setAutoAttachMap(msg.terminal_context, msg.autoAttach || {})`.
- New `terminal_autoattach_changed` case →
  `useTerminalTabsStore().setAutoAttach(msg.terminal_context, msg.terminal_index, msg.enabled)`.

### 5. Frontend UI toggle — `frontend/src/components/terminal/TerminalPanel.vue`

- Visibility: `isAncestorScope(props.contextKey) && usesTmux && !isActiveAttached`
  **and a live tmux session exists for the active tab**
  (`terminalTabsStore.indices[contextKey].includes(activeIndex)`). The session
  check is essential: the flag is a tmux user option *on the session*, so there
  is nothing to pin for a Main that was never started (its "Start terminal"
  state) or any index without a live tmux session. The toggle appears once the
  session exists (started/discovered), connected or not. Session panels (`s:`)
  never show it (a session is never an ancestor).
- State: `terminalTabsStore.isAutoAttach(props.contextKey, activeIndex)`.
- Click handler `toggleAutoAttach()`:
  - compute `next = !current`;
  - optimistic local write: `terminalTabsStore.setAutoAttach(contextKey, activeIndex, next)`;
  - `sendWsMessage({ type: 'set_terminal_autoattach', terminal_context, terminal_index: activeIndex, enabled: next })`.
- Rendering: a `wa-button` next to Rename, `appearance="filled"` when active /
  `"plain"` when not, with an `AppTooltip`. Icon: `thumbtack` (pending a
  Font-Awesome-Free availability check during implementation; fall back to a
  confirmed-free glyph such as `link`/`bullhorn` if `thumbtack` is Pro-only).

### 6. Frontend — derived auto-attach on the child side (`TerminalPanel.vue`)

The flag, not the attachment list, is the source of truth. Auto-attachment is
**derived**, never stored in `attachments`, so toggling the parent flag makes the
child tab appear/disappear symmetrically with zero desync.

- **Proactive ancestor discovery.** Today `requestAncestorDiscovery()` only runs
  when the attach menu opens. Add a trigger on panel activation (when
  `props.active` and the WS is connected) that lists every ancestor scope whose
  indices are still **unknown** in the store (`terminalTabsStore.indices[ctx] ===
  undefined`), so the flags are known without opening the menu. Maintained
  afterwards by the `terminal_autoattach_changed` / `terminal_renamed` /
  `terminal_killed` broadcasts. (The store is global, so a scope another panel
  already discovered costs nothing.) No `autoAttach` field is needed on a
  `terminal_created` broadcast: the toggle acts on an existing tab, so a
  brand-new terminal can never already carry the flag — its first flag arrives
  via `terminal_autoattach_changed`.
- **`forcedKeys`** (computed): for each `ancestorScope`, each index `i` with
  `isAutoAttach(scope.contextKey, i)` → pool key `scope.contextKey#i`, carrying
  the scope's descriptor fields (projectId, cwd, label).
- **Effective attached tabs** = manual `attachmentsFor(ctx)` ∪ `forcedKeys`,
  deduplicated, rendered before own tabs (forced + manual, then own). Order:
  keep ancestor order (worktree → project → workspace → global) for forced; manual
  ones keep their existing order; a key present in both is rendered once as forced.
- **Materialization.** A forced key that has no pool descriptor yet is created on
  the fly for rendering (the same descriptor shape `attachKeyFromRoute` builds),
  but **not** pushed into `attachments`. This matters for which pool action the
  slot-publishing `watchEffect` calls: `setSlotTarget` **early-returns when the
  descriptor is absent** (`terminalPool.js`), whereas `setSlot` creates/updates
  it. A forced key is an ancestor-owned descriptor that — unlike a manual
  attachment — is deliberately kept out of `attachments`, so for a tmux ancestor
  the child panel may be the only viewer and nothing else guarantees the
  descriptor exists. Therefore: use **`setSlot`** (with the materialized
  descriptor) for a forced key whose descriptor is absent; `setSlotTarget` only
  once it exists. Using `setSlotTarget` on an absent descriptor is a silent
  no-op (the tab renders blank) — the easy way to build this wrong. For tmux a
  dropped descriptor is recreated from the flag on the next visit (re-attachable
  server-side), so no client persistence is needed.
- **Detach blocked.** `isForced(key) = forcedKeys.includes(key)`. The Detach
  button is hidden when the active attached tab is forced; `handleDetach` ignores
  forced keys defensively. In the attach menu, a forced item is shown disabled
  with the existing "attached" check.

### 7. URL routing

A forced tab participates in routing exactly like a manual attachment (its route
token is the pool key, encoded by `terminalRouteToken`). `applyRouteTermIndex` /
`attachKeyFromRoute` must treat a forced key as resolvable: when the requested
key is in `forcedKeys` (or becomes so once discovery lands), activate it without
requiring a `attachments` entry. The existing retry watcher already re-resolves
once ancestor discovery arrives, which now also carries the flags.

## Behavior changes & limits

- **tmux-only.** The toggle is absent in non-tmux mode; the feature does nothing
  there. Accepted trade-off (single code path, true backend persistence).
- A forced tab cannot be detached from a child; the only way to remove it is to
  turn the flag off on the owning (ancestor) panel — which removes it from every
  child at once.
- A key both manually attached and force-attached is treated as forced
  (non-detachable). Turning the flag off reverts it to its manual state if it was
  ever manually attached, otherwise it disappears.
- Per-terminal granularity: the flag is per terminal index (the Main and each
  secondary independently), like manual attachment.
- Cross-device: the flag syncs through the same broadcast path as labels.
- No new keep-alive semantics: forced tmux terminals are re-attachable
  server-side; their pool descriptor is transient and rebuilt from the flag.

## Non-goals / future

- AutoAttach for non-tmux terminals (would need an in-memory or new backend
  store; explicitly out of scope per the brainstorm).
- A global/project default ("new terminals here are auto-attached by default").
- Any change to the manual attach UX beyond hiding Detach on forced tabs.
