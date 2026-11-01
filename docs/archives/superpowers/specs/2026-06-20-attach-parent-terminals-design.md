# Attach parent-level terminals into a child panel

- **Date:** 2026-06-20
- **Status:** Approved (brainstorm) → implementing (unified pool/move architecture)
- **Branch:** `attach-parent-terms`

## Goal

A terminal panel lives at several levels — **session**, **project** (incl. a git
**worktree** project), **workspace**, and the global **all-projects** view. Today
each panel only shows the terminals of its own scope. We want, from a child
panel, to **attach** one or more terminals belonging to an *ancestor* scope (the
example: from a session, attach one of the project's terminals).

Attaching is always an explicit user action — never automatic.

## Key insight — move, don't mirror

The UI only ever shows **one** top-level context at a time: a session OR a
project OR a workspace OR the global view (the dockable layout is *inside* a
single session). So two contexts' terminal panels are never visible
simultaneously.

Therefore a shared terminal never needs to render in two places at once — it just
needs to **move** to whichever view is currently active. This sidesteps the only
hard constraint (a raw non-tmux PTY is single-consumer and cannot be mirrored)
**without any backend multiplexer**: there is exactly **one live instance** per
terminal, and it is relocated to the active claimant.

This unifies tmux and non-tmux: both go through the same pool/move mechanism; the
previously-built tmux "mirror via a 2nd WS" approach is removed.

## Levels & ancestor resolution

(unchanged) For a panel, the attachable ancestor scopes, most-local → most-global,
each shown only if it currently has ≥ 1 attachable terminal:

| Panel (context key) | Ancestors offered |
|---|---|
| Session `s:<sid>` whose project is a worktree | worktree `p:<worktreeId>` → project `p:<mainRepoId>` → workspace(s) `w:<wsId>` → global |
| Session `s:<sid>` in a plain project | project `p:<pid>` → workspace(s) → global |
| Worktree-project panel `p:<worktreeId>` | project `p:<mainRepoId>` → workspace(s) → global |
| Plain-project panel `p:<pid>` | workspace(s) → global |
| Workspace panel `w:<wsId>` | global |
| All-projects (global) panel | *(none → no button)* |

Visible labels: `Worktree: <name>`, `Project: <name>`, `Global: <name>`;
`Workspace: <name>` for a single workspace, else `Workspace (<ws name>): <name>`.
Attached tabs render before the panel's own tabs.

## Architecture — app-level pool + Vue Teleport

### `terminalPoolStore` (Pinia, app-level)

The single source of truth for live terminal instances and where each renders.
Keyed by `key = "${contextKey}#${index}"` (the terminal's **home** identity).

- `descriptors: Map<key, {contextKey, index, projectId, sessionId, cwd}>` — every
  terminal that must exist right now.
- `targets: Map<key, HTMLElement>` — the active panel's slot for this key (the
  teleport destination). Absent → the instance lives in a hidden holder.
- `activeFlags: Map<key, boolean>` — whether this key is the active tab of the
  panel currently displaying it (drives `start()`/focus).
- `apis: Map<key, api>` — each instance registers its toolbar/extra-keys API here
  (replaces the old panel-scoped `provide/inject` registry, which can't cross the
  Teleport boundary).
- `attachments: Map<panelContextKey, key[]>` — which ancestor terminals each panel
  has attached. **Persists across in-app navigation**, lost on page reload
  (ephemeral, per the original decision).

Actions: `setSlot(key, descriptor, el, isActiveTab)`, `clearSlot(key, ownerCtx)`,
`attach(panelCtx, key, descriptor)`, `detach(panelCtx, key)`,
`registerApi/unregisterApi`, plus a getter `liveKeysForContext(ctx)` (non-tmux
discovery = the keys whose `descriptor.contextKey === ctx`).

**Keep-alive rule:** a descriptor (hence its instance) exists while it has a
**target** (some active panel renders it), appears in some panel's
**attachments**, or is flagged **persist**. When none holds, the descriptor is
dropped → the instance unmounts → `useTerminal` cleanup closes the WS (tmux:
server session persists; non-tmux: shell ends).

- **`persist`** is set for non-tmux terminals of an *attachable ancestor scope*
  (project / worktree-project / workspace / global). They have no server-side
  session to re-attach to, so they must stay alive across navigation to remain
  *connected and attachable* from a child panel (the menu lists only currently
  connected non-tmux terminals). tmux terminals (re-attachable from the server)
  and **session** terminals (never attached) are torn down on navigation, exactly
  as today.
- On a real PTY **exit**, the pool drops the instance regardless of `persist`
  (`persist` survives navigation, never a dead shell) — except a MAIN terminal
  currently being displayed, which is kept so the panel shows its reconnect
  overlay.

### `TerminalPool.vue` (mounted once in `App.vue`)

`v-for` over `descriptors`; each entry is a
`<Teleport :to="targets.get(key)" :disabled="!targets.get(key)">` wrapping a
`<TerminalInstance>` built from the descriptor, with
`:active="!!targets.get(key) && activeFlags.get(key)"`. Disabled teleport renders
into a hidden in-pool holder (kept alive, off-screen). Teleport targets are
**element refs** (not selectors) so a panel docked anywhere — or itself teleported
into a dock — still works (nested teleports).

### `TerminalInstance.vue`

Now instantiated by the pool (not the panel). Registers its API in
`terminalPoolStore.apis[key]` on setup, unregisters on unmount. Otherwise
unchanged (xterm + `useTerminal`, foreign context handled by `getWsUrl()` as
before).

### `TerminalPanel.vue` (viewport)

Keeps managing its **own** tab list (`terminals`, `nextIndex`, route sync,
rename, create/kill, discovery) and the **attach menu**. Rendering changes:

- Renders a **slot `<div ref>` per tab** (own + attached); only the active tab's
  slot is visible (visibility toggling preserves the no-resize-flash behavior).
- When the panel is **active**, it calls `pool.setSlot(key, descriptor, el,
  isActiveTab)` for each slot; when inactive/unmounting, `pool.clearSlot`. The
  pool teleports the matching instance into each slot.
- Own tabs use `key = "${ownCtx}#${index}"`; attached tabs use the ancestor's key.
- `activeApi` = `pool.apis.get(activeKey)`; the toolbar/ExtraKeysBar read it.
- Attach menu sources: **tmux** scopes from the existing tmux discovery
  (`terminalTabsStore`, persistent, attachable even when not connected); **non-tmux**
  scopes from `pool.liveKeysForContext(ancestorCtx)` (only currently-connected
  terminals — matching "tout terminal connecté"). tmux vs non-tmux is the global
  `terminalUseTmux` setting, so a context is uniformly one or the other.
- Attach = `pool.attach(ownCtx, ancestorKey, descriptor)`; detach =
  `pool.detach(ownCtx, ancestorKey)` (never kills). Auto-remove an attached tab
  when its key leaves the pool (source killed / shell exited).

### Active-tab tracking & URLs

`activeIndex` stays the integer for own tabs (route-bound). `activeAttachedKey`
(string|null) tracks when an attached tab is active. The active **key** =
`activeAttachedKey ?? "${ownCtx}#${activeIndex}"`.

Attached tabs **do** participate in URL routing. The `:termIndex?` segment
(already a free string) carries either a plain own index or a scoped token for an
attached tab (`granularRoutes.js`):

- global → `/terminal/all:<idx>`
- workspace → `/terminal/w:<wsId>:<idx>`
- project / worktree → `/terminal/p:<projectId>:<idx>`

`parseRouteTermIndex` decodes the token to the pool key `${contextKey}#${idx}`;
`terminalRouteToken` re-encodes it. On reconciliation, an attached token that is
**not currently attached** is **auto-attached** from the matching ancestor scope
— but only if the target terminal actually **exists** (non-tmux: a live pool
instance; tmux: live or listed by discovery) so a stale URL never spawns a
phantom. Since attachments are in-memory, this makes browser back/forward (and
cross-navigation) re-attach automatically when you land on an attachment URL.
A retry watcher re-resolves the token once its inputs (ancestor scopes, the live
instance, or tmux discovery) arrive.

## Behavior changes & limits

- **Non-tmux project/workspace/global terminals now persist across in-app
  navigation** (kept alive in the pool so they stay connected and attachable),
  until disconnected/killed or the shell exits. Session terminals and all tmux
  terminals are unchanged. All pool/attachment state is lost on a full page reload.
- An un-attached persistent terminal you never return to lingers until you
  disconnect it — the price of "any connected terminal is attachable".
- A parked (off-screen) terminal is resized to the holder's size; a non-tmux TUI
  reflows when parked/un-parked (tmux is unaffected). Minor; could be optimized
  by skipping resize while parked.
- Non-tmux sharing is **intra-browser** (the instance is client-side) — expected,
  since we move an instance, not a server session. tmux remains cross-device for
  its own (non-attach) reconnect behavior.
- **Zero backend changes.**

## Non-goals / future

- Persisting attachments across reloads (and a future "attach by default to
  sessions" toggle when creating a project/workspace/global terminal). Note: an
  attachment URL already auto-(re)attaches on browser history navigation **when
  the target still exists**, but after a full reload a non-tmux source is gone so
  the URL resolves to "unavailable".
- Routing snippet "send to specific tab" / tab-nav shortcuts to attached tabs.
