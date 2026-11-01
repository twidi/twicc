# Terminal lifecycle × dockable layout — analysis & options (2026-06-19)

Status: **Q2 implemented + live-verified (2026-06-19)** — the Main-terminal explicit-start (see §6) is
built and tested in the browser. The broader focus-model (Option C) and the reaper (Option D) remain for
later discussion. Written overnight per request, to seed the design discussion about how the terminal
(PTY/tmux) should behave under the dockable layout, and how that ties into the proposed "focus model"
(per-tab visibility/focus lifecycle signals).

All claims below were traced in the `layout` worktree and the key ones re-verified by hand (file:line).

---

## TL;DR

- **The terminal connects LAZILY, gated on an `active` prop — never on mount.** So the cases we worried
  about are mostly already fine:
  - A terminal in a **minimized dock (gutter)** → **not connected** (already the desired behavior).
  - The **inactive tab of a merged region** → **not connected**.
  - A **background (non-foreground) session** → **not connected**.
  - **Draft / archived** sessions → never tmux at all.
  - **Main-area terminal**: connects when the user makes it the active tab (the desired "open → tmux").
- **The real problem is narrow:** a terminal that is **docked AND the active tab of its region** flips
  `active = true` on render, with **no user gesture** → it spawns tmux. A *default layout that docks the
  terminal* (so it is region-active by default) would therefore spawn a `twicc-<session>` tmux session
  for **every foreground session the user merely views**.
- **tmux sessions persist past the WebSocket close** (a WS close only *detaches*), with **no idle reaper,
  no cap, no boot GC**. Only **archiving** (or an explicit disconnect/kill) destroys them. → A user who
  doesn't archive accumulates one tmux session per viewed session (plus one per extra terminal sub-tab).
- **The crux for the focus model:** today a single `active` boolean conflates *"visible"* with
  *"should be running"*, and can't distinguish *"passively shown by the layout"* from *"the user
  deliberately focused the terminal."* Both produce `active = true`. Fixing the spawn problem cleanly
  needs a richer per-tab signal — exactly the focus-model idea.

---

## 1. Current mechanics (verified)

### 1.1 Connection trigger — lazy, gated on `active`
- `TerminalInstance.vue:98-107` — the connection trigger:
  ```js
  // Lazy init: start the terminal only when the tab becomes active for the first time
  watch(() => props.active, (active) => { if (active && !started.value) start() }, { immediate: true })
  ```
  `start()` is one-shot (`useTerminal.js` `start()` returns early if `started`). It runs
  `initTerminal() → connectWs()` which opens the WebSocket; there is **no `onMounted`** that connects
  (the composable's only lifecycle hook is `onUnmounted → cleanup()`).
- `SessionView.vue:1592` — `:active="isActive && isToolTabShown('terminal')"`. The Terminal `<Teleport>`
  has **no `v-if`** (`SessionView.vue:1583`), so the panel is **always mounted** (in the hidden host when
  not shown) — but mounting does not connect; only `active` does.
  - `isActive` = this session view is the **foreground/active** session (not a backgrounded KeepAlive one).
  - `isToolTabShown('terminal')` → for the center, `centerActiveTab === 'terminal'`; otherwise
    `layout.isToolPanelVisible('terminal')`, which for a **dock region** returns
    `regionActiveTabId(region) === 'terminal'` (`useSessionLayout.js` `isToolPanelVisible`).
- `TerminalPanel.vue` passes `:active="active && activeIndex === term.index"` to each sub-tab's
  `TerminalInstance`, so only the **active sub-tab** connects.
- `routeOwner` / `applyRouteTermIndex` do **not** gate the connection — they only decide *which sub-tab
  the URL points at* (a non-owner must not sync-from-route, to avoid resetting to Main at blur).

### 1.2 tmux vs plain PTY
- `useTerminal.js` `shouldUseTmux()`: tmux iff the **global** synced setting `isTerminalUseTmux` is on,
  **force-disabled** for `draft`/`archived`, **force-enabled** for hybrid (`h:`) contexts. `getWsUrl()`
  adds `?tmux=1` accordingly. There is **no per-session tmux flag** (only the draft/archived guard).
- Backend `terminal.py`: `spawn_tmux_pty()` (`tmux new-session -A -s <name>`, attach-or-create —
  idempotent per name) vs `spawn_pty()`. Falls back to raw shell if tmux is missing; for archived
  sessions it only attaches to an existing tmux session, never creates one.
- **Naming = one tmux session per (context × sub-tab index):** `twicc-<session_id>` for index 0,
  `twicc-<session_id>__N` for sub-tab N. Dedicated socket `twicc` (`twicc-hybrid` for `h:`).

### 1.3 Teardown
- **WS close / component unmount = DETACH only** for tmux (the killed `child_pid` is the tmux *client*;
  the server-side session persists — documented in `terminal.py`). For a *raw* PTY, killing the child
  ends the shell.
- **Explicit kills:** the "Disconnect" button sends Ctrl+D (ends the pane); killing a secondary sub-tab
  runs `tmux kill-session`; **archiving a session** kills all its tmux sessions
  (`kill_all_tmux_terminals("s:<id>")`, both single and bulk archive paths).
- **No idle/time-based reaper, no cap, no boot-time GC** of orphaned `twicc-*` tmux sessions was found.

### 1.4 What is already correct (don't "fix" these)
- Minimized-gutter terminal, inactive merged-region tab, background session, draft/archived → **no
  connect / no tmux**. Main-area open → connects. These already match the intent.

---

## 2. The problem, precisely

**Trigger condition:** foreground session **+** terminal docked **+** terminal is its region's active tab,
evaluated **on render** → `active=true` with no user action → `start()` → WS `?tmux=1` →
`tmux new-session -A` for `twicc-<session_id>`.

- A **default layout that docks the terminal** (alone in a dock, so it is region-active) makes this fire
  for **every foreground session the user opens** — even if they never touch the terminal.
- Because tmux sessions **persist** and nothing reaps them, a non-archiver accumulates one
  `twicc-<session_id>` per viewed session over time. (Whether this "explodes" the process count needs a
  real measurement — each tmux session keeps a server-side session + a login shell alive on the `twicc`
  socket — but it is a genuine slow leak with no upper bound today.)
- **Secondary accumulation:** the queued-command flow (provider login, snippet `openInNewTab`) creates a
  **new sub-tab every invocation** (`TerminalPanel.vue:417-464` → `createTerminal()` → route → connect),
  i.e. a new `__N` tmux session each time. Repeated logins stack sessions.

This is *enabled* by the layout (a docked region-active terminal connects on view) and would be made
*pervasive* by docking the terminal in the default layout. Pre-layout, the center terminal connected only
when you navigated to `/terminal` (made it active) — the same lazy rule, just rarely satisfied on render.

---

## 3. How this ties to the focus model

Today the terminal's "should I run?" decision is a single derived boolean `active = foreground && shown`,
with **one connection edge** (false→true) and **no disconnect on hide** (tmux is meant to persist). It
**conflates visibility with intent** and cannot tell apart:

| State | `active` today | What we'd want |
|---|---|---|
| Minimized (gutter) | false → no connect | no connect ✅ (already) |
| Inactive tab of a merged region | false → no connect | no connect ✅ (already) |
| **Docked, region-active, never focused** | **true → connect** | **no auto-spawn** ❗ |
| User clicked the terminal tab / routed to it (main area or dock) | true → connect | connect ✅ |
| Hidden after having been used | stays connected (detach on unmount) | keep tmux alive (cheap) |

The focus-model idea — *give each tab its own visibility/focus state and let the tab decide* — is the
right shape. The terminal would connect on **deliberate focus**, not on **passive layout visibility**. The
same signal lets Git stop polling `git status` when it is not the shown tab, etc. The hard part is purely
distinguishing **"region-active because the layout put it there"** from **"the user focused it"** — both
currently collapse to `active=true`.

---

## 4. Options (what could change)

**A — Gate the terminal connect on FOCUS (route-ownership), not visibility.**
Connect only when the terminal is the route-active/focused tab (`activeTabId === 'terminal'` /
`ownsRoute('terminal')`), not merely region-visible. A default-docked terminal then shows its chrome but
does not connect until focused; main-area click still connects (it becomes route-active); minimized still
no connect. Smallest change. Trade-off: a docked terminal you want needs one focus/click to spin up, and a
shown-but-unconnected terminal needs a clear placeholder ("press to start").

**B — Explicit "start" affordance for a passively-shown docked terminal.**
A docked terminal that is shown but never focused renders a lightweight placeholder instead of
auto-connecting; first interaction (focus/click/keystroke) connects. Superset of A with an explicit UI.

**C — Build the general per-tab lifecycle signal; the terminal opts into "connect on focus".**
Implement the focus-model hooks generally (became-visible / became-hidden / became-focused); the terminal
subscribes (`onFocus → ensure started`), and other panels reuse it (Git polling, etc.). Most work, but the
clean general solution the user described — and it unblocks the other panels too.

**D — Keep eager connect but bound accumulation (reaper / cap).**
Idle-timeout or LRU cap on tmux sessions, and/or a boot-time GC of clientless `twicc-*` sessions older
than X. Doesn't stop the needless *spawn*; only bounds the *leak*. Complements A/B/C and is arguably worth
doing regardless, since the unbounded-tmux situation is a latent leak even today.

**Decided (see §6):** Option **B, scoped to the Main (index-0) sub-tab** — the default session terminal
shows a "Start" callout instead of auto-creating its PTY/tmux; **new sub-tabs (index > 0) auto-connect**
as today, so the login-command and snippet flows are untouched. Fold into the general focus-model
lifecycle (C) later; the reaper (D) is deferred (Q2 largely removes the accumulation case).

---

## 5. Must keep working (hard constraints)

- **Main-area terminal open → launches tmux.** (Focus-gated connect preserves this — opening = focusing.)
- **Provider login command → run in terminal** (`terminalCommand.request('global', cmd)` → new sub-tab →
  connect → inject). Keep — but note it creates a **new sub-tab every call**; consider reusing a single
  login/aux tab to avoid stacking `__N` tmux sessions.
- **Terminal snippets `openInNewTab` → new sub-tab + connect + inject.** Keep.
- **Command-palette / shortcut "switch to terminal"** → navigate only (lazy connect on focus). Fine under
  focus-gating.
- **Hybrid CLI terminal (`h:` context)** → a separate surface; attach-only to the agent's own tmux. Not
  affected by any of this.

---

## 6. Decisions (from review — 2026-06-19)

- **Q1 — treat it as real.** A bottom-docked terminal (VS Code / PyCharm style) is expected to be a
  *frequent* default. So the eager-create problem is worth solving, not a moot edge case.
- **Q2 — explicit "Start" for the DEFAULT terminal only.** The session terminal's **Main sub-tab
  (index 0)** must NOT auto-create its PTY/tmux on display; it shows a connect callout (reuse the existing
  "terminal disconnected → reconnect" callout UI) and connects on the button press. **Any other sub-tab
  (index > 0) — user-created, or login/snippet-created — auto-connects as today.** Two refinements still
  open (below).
- **Q3 — no.** Don't do hidden-after-use detach/reattach for now: it would have to cover every sub-tab,
  tmux and non-tmux differ (no clean detach/reattach for a raw PTY), and the payoff is unclear. Skip.
- **Q4 — no change.** Keep running the provider-login command in a **new** sub-tab — deliberate: a fresh
  terminal is the only place we can be sure the shell prompt is clean/ready (the Main tab may be busy).
- **Q5 — defer.** tmux persistence is the whole point (resume a session and find your state). A reaper is
  only for *never-used* tmux sessions and is **later**: with Q2's manual-start the Main terminal is no
  longer auto-created, so the accumulation case mostly disappears on its own.

### Q2 — resolved (the full spec)
The **Main (index-0)** session terminal:
- **never auto-CREATES** its PTY/tmux on display;
- **auto-ATTACHES** when a tmux session already exists for it (so no Start prompt on a return visit) —
  you press Start at most **once per session, ever**;
- shows the **Start callout** (reusing the disconnected → reconnect UI) **only when there is nothing to
  attach to** — **same behavior in tmux and non-tmux** (decided): in non-tmux nothing ever persists, so a
  non-tmux Main always shows Start. One extra click, but consistent across modes and it avoids spawning a
  TTY for a terminal you never use;
- applies **everywhere** (docked AND main-area — opening the main-area Main no longer auto-*launches*; it
  attaches if a session exists, else shows Start);
- **new sub-tabs (index > 0)** auto-create/connect immediately, as today (login + snippet flows untouched).

**Implementation approach / feasibility:**
- "Does a tmux session already exist for index 0?" is answerable **without opening a PTY**: the backend's
  `list_tmux_terminals(context)` is surfaced over the **main app WS** (`_handle_list_terminals` → the
  `terminalTabs` store's indices/labels). The Main terminal's connect decision keys on whether index 0 is
  in that set: present → `start()` (attach); absent → render the Start callout instead of `start()`.
- Localized change: gate the index-0 `TerminalInstance`'s auto-`start()` (currently the unconditional
  `watch(active …) → start()` at `TerminalInstance.vue:98-107`) on *(an existing tmux session for index 0)*,
  and wire the Start button to call `start()`. Index > 0 keeps the immediate auto-start. There is a brief
  "checking" state until the list resolves (→ attach or callout).

**Implemented + live-verified (2026-06-19).** `TerminalInstance` gained a `startMode` prop
(`auto` / `manual` / `pending`); its connect watch only `start()`s on `auto`, and a `manual` Main renders a
"Start terminal" callout (reusing the disconnect-overlay UI) whose button calls `start()`. `TerminalPanel`
computes `startModeFor(index)` from `terminalTabs.indices[contextKey]` (index 0: `pending` until discovery
resolves, then `auto` if index 0 exists else `manual`; index > 0: always `auto`), with a 4s safety net so a
dropped discovery falls back to `manual`. Verified in Chrome **in both modes**: no-session → Start callout
(no auto-create); press Start → connect; **non-tmux** → always Start (no persistence); and **real tmux**
(Settings → Terminal enabled) → first visit Start → tmux session created → **reload auto-attaches** (the
session persists, `indices=[0]`, no callout); no console errors. Files: `TerminalInstance.vue`,
`TerminalPanel.vue`.

---

## Key file:line references (verified)
- Connect trigger: `frontend/src/components/terminal/TerminalInstance.vue:98-107`.
- Active wiring: `frontend/src/views/SessionView.vue:1583` (always-mounted Teleport), `:1592` (`:active`).
- Visibility resolution: `frontend/src/composables/useSessionLayout.js` `isToolPanelVisible` / `targetKeyForTab`.
- tmux decision: `frontend/src/composables/useTerminal.js` `shouldUseTmux` / `getWsUrl`.
- Backend spawn/teardown/naming: `src/twicc/terminal.py` (`spawn_tmux_pty`, `cleanup_pty`,
  `tmux_session_name`, `kill_all_tmux_terminals`); archive kill in `session_update.py` + `views.py`.
- Queued-command (login/snippet) forces a new sub-tab + connect: `TerminalPanel.vue:417-464`.
- Sub-tab ↔ URL: routes `terminal/:termIndex?` in `router.js`; `applyRouteTermIndex` /
  `emit('navigate')` in `TerminalPanel.vue`; parse/build in `utils/granularRoutes.js`.
