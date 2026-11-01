# Terminal AutoAttach (tmux-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an ancestor-scope tmux terminal carry an opt-in "AutoAttach in children" flag so it appears (non-detachable) as an attached tab in every descendant panel.

**Architecture:** The flag is a tmux session user option (`@twicc_autoattach`), the exact twin of the existing `@twicc_label`. The backend transports it through `list_terminals` discovery + a `set_terminal_autoattach` WS message + a `terminal_autoattach_changed` broadcast; the frontend mirrors it in `terminalTabsStore`. Each child panel **derives** its forced tabs from the flags of its `ancestorScopes` (never stored in the attachment registry), so toggling the parent flag makes the child tab appear/disappear symmetrically.

**Tech Stack:** Python (Django ASGI consumer, tmux via subprocess), Vue 3 `<script setup>`, Pinia, Web Awesome, xterm.js pool/teleport.

**Spec:** `docs/superpowers/specs/2026-06-20-terminal-autoattach-design.md`

---

## Project conventions (override the skill's defaults)

- **No mandatory tests** (per `CLAUDE.md`: "Only allowed shortcuts: no mandatory tests or linting"). This codebase has no frontend test harness and the backend bits need a live tmux server. So this plan replaces TDD steps with **verification steps**: SFC compile-checks, backend log checks after a worktree `devctl restart`, and a manual smoke test. This matches how the parent feature (commit `588b4e46`) was validated.
- **Single commit at the end, only when the user explicitly asks.** Do NOT commit between tasks. The plan ends with one commit task gated on the user's request.
- **Worktree discipline:** every Bash command is prefixed with `cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && …`. Never run `npm install` / `migrate` by hand. Restart only via `devctl.py` (user has standing approval for this worktree's servers).
- **Compile-check command** (worktree now has its own `node_modules`):
  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && node -e "const {parse,compileScript}=require('./frontend/node_modules/@vue/compiler-sfc');const fs=require('fs');const f=process.argv[1];const {descriptor}=parse(fs.readFileSync(f,'utf8'),{filename:f});compileScript(descriptor,{id:'x'});console.log('OK',f)" <file.vue>
  ```

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/twicc/terminal.py` | tmux user-option read/write; `TerminalInfo` | Modify |
| `src/twicc/asgi.py` | WS discovery payload + `set_terminal_autoattach` handler + broadcast | Modify |
| `frontend/src/stores/terminalTabs.js` | autoAttach flag state (per context/index) | Modify |
| `frontend/src/composables/useWebSocket.js` | route `terminal_list` + `terminal_autoattach_changed` into the store | Modify |
| `frontend/src/components/terminal/TerminalPanel.vue` | toggle button (owner) + derived forced tabs (child) + routing | Modify |
| `frontend/src/components/terminal/AttachTerminalMenu.vue` | (read-only) forced items already render disabled via `attached` flag | No change expected |
| `CHANGELOG.md` | Unreleased entry | Modify |

---

## Task 1: Backend — tmux user option (`terminal.py`)

**Files:**
- Modify: `src/twicc/terminal.py` (near `_TMUX_LABEL_OPTION` ~line 81; `TerminalInfo` ~line 90; `list_tmux_terminals` ~line 444; after `set_tmux_terminal_label`/`_unset_tmux_terminal_label` ~line 545-579)

- [ ] **Step 1: Add the user-option constant**

Next to `_TMUX_LABEL_OPTION = "@twicc_label"`:

```python
# tmux user option name for storing the "auto-attach into children" flag
_TMUX_AUTOATTACH_OPTION = "@twicc_autoattach"
```

- [ ] **Step 2: Add `auto_attach` to `TerminalInfo`**

```python
class TerminalInfo(NamedTuple):
    """A terminal's index and metadata read from tmux."""
    index: int
    label: str  # empty string if no custom label set
    auto_attach: bool = False  # mirrors the @twicc_autoattach user option
```

- [ ] **Step 3: Read the flag in `list_tmux_terminals`**

Extend the format string and parse the new column. The `list-sessions -F` line becomes:

```python
"-F", "#{session_name}\t#{@twicc_label}\t#{@twicc_autoattach}"],
```

And the parse loop. **Replace** the existing `name, _, label = line.partition("\t")` line (terminal.py:476) — `partition` only splits on the first tab, so it can't read a third column; switch to `split("\t")` as below. (An unset option expands to empty string → `False`; the value we write is `"1"`.)

```python
prefix = tmux_session_name(terminal_context, 0)  # "twicc-<normalized_context>"
terminals: list[TerminalInfo] = []
for line in result.stdout.strip().split("\n"):
    if not line:
        continue
    parts = line.split("\t")
    name = parts[0]
    label = parts[1] if len(parts) > 1 else ""
    auto_attach = len(parts) > 2 and parts[2] == "1"
    if name == prefix:
        terminals.append(TerminalInfo(index=0, label=label, auto_attach=auto_attach))
    elif name.startswith(prefix + "__"):
        suffix = name[len(prefix) + 2:]
        try:
            terminals.append(TerminalInfo(index=int(suffix), label=label, auto_attach=auto_attach))
        except ValueError:
            continue
return sorted(terminals, key=lambda t: t.index)
```

- [ ] **Step 4: Add the setter (twin of `set_tmux_terminal_label`)**

After `_unset_tmux_terminal_label`:

```python
def set_tmux_terminal_autoattach(terminal_context: str, terminal_index: int, enabled: bool) -> bool:
    """Set/clear the "auto-attach into children" flag on a tmux terminal session.

    Stored as a tmux user option (``@twicc_autoattach`` = ``"1"``) on the session,
    read back via ``list_tmux_terminals``. Twin of ``set_tmux_terminal_label``.

    Returns True on success, False on failure (tmux not installed, no session…).
    """
    if not enabled:
        return _unset_tmux_terminal_autoattach(terminal_context, terminal_index)
    return tmux_set_option(terminal_context, _TMUX_AUTOATTACH_OPTION, "1", terminal_index)


def _unset_tmux_terminal_autoattach(terminal_context: str, terminal_index: int) -> bool:
    """Remove the auto-attach user option from a tmux terminal session."""
    tmux_path = get_tmux_path()
    if tmux_path is None:
        return False
    name = tmux_session_name(terminal_context, terminal_index)
    try:
        result = subprocess.run(
            [tmux_path, "-L", tmux_socket_for(terminal_context), "set-option", "-t", name, "-u", _TMUX_AUTOATTACH_OPTION],
            capture_output=True, timeout=5,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False
```

> **Note:** check `_unset_tmux_terminal_label`'s exact body and mirror it (the `-u` "unset option" flag is the tmux idiom). If the label unsetter uses a different shape, copy that shape verbatim for consistency.

- [ ] **Step 5: Verify import/parse**

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && TWICC_DATA_DIR=$PWD uv run python -c "from twicc.terminal import TerminalInfo, set_tmux_terminal_autoattach, list_tmux_terminals; print(TerminalInfo(0,'',True))"
```
Expected: prints `TerminalInfo(index=0, label='', auto_attach=True)`, no ImportError.

---

## Task 2: Backend — WS handler, discovery payload, broadcast (`asgi.py`)

**Files:**
- Modify: `src/twicc/asgi.py` (dispatch block ~line 664-672; `_handle_list_terminals` ~line 1857; add handler after `_handle_rename_terminal` ~line 1940)

- [ ] **Step 1: Add `autoAttach` to the discovery payload**

In `_handle_list_terminals`, the `send_json` becomes:

```python
await self.send_json({
    "type": "terminal_list",
    "terminal_context": terminal_context,
    "terminals": [t.index for t in terminals],
    "labels": {str(t.index): t.label for t in terminals if t.label},
    "autoAttach": {str(t.index): True for t in terminals if t.auto_attach},
})
```

- [ ] **Step 2: Add the dispatch case**

Next to the `rename_terminal` case (~line 670):

```python
elif msg_type == "set_terminal_autoattach":
    await self._handle_set_terminal_autoattach(content)
```
(Use the same argument variable the neighbouring cases use — `content`.)

- [ ] **Step 3: Add the handler (twin of `_handle_rename_terminal`)**

After `_handle_rename_terminal`:

```python
async def _handle_set_terminal_autoattach(self, data):
    """Handle set_terminal_autoattach: set/clear the auto-attach-into-children flag.

    The flag is stored as a tmux user option (persists across reconnections) and
    broadcast to all clients for cross-device sync. tmux-only by construction.
    """
    terminal_context = data.get("terminal_context")
    terminal_index = data.get("terminal_index")
    enabled = bool(data.get("enabled"))
    if not terminal_context or terminal_index is None:
        await self.send_json({"type": "error", "message": "Missing terminal_context or terminal_index"})
        return

    from twicc.terminal import set_tmux_terminal_autoattach

    await asyncio.to_thread(set_tmux_terminal_autoattach, terminal_context, terminal_index, enabled)

    await self.channel_layer.group_send(
        "updates",
        {
            "type": "broadcast",
            "data": {
                "type": "terminal_autoattach_changed",
                "terminal_context": terminal_context,
                "terminal_index": terminal_index,
                "enabled": enabled,
            },
        },
    )
```

- [ ] **Step 4: Verify**

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && TWICC_DATA_DIR=$PWD uv run python -c "import twicc.asgi" && echo OK
```
Expected: `OK`, no SyntaxError/ImportError.

---

## Task 3: Frontend store (`terminalTabs.js`)

**Files:**
- Modify: `frontend/src/stores/terminalTabs.js`

- [ ] **Step 1: Add `autoAttach` state + actions + getter**

State: add `autoAttach: {}` (contextKey → `{ [index]: true }`).

Actions (mirror the `labels` ones; keep only truthy entries):

```javascript
setAutoAttachMap(contextKey, map) {
    this.autoAttach[contextKey] = {}
    for (const [index, enabled] of Object.entries(map || {})) {
        if (enabled) this.autoAttach[contextKey][Number(index)] = true
    }
},
setAutoAttach(contextKey, index, enabled) {
    if (!this.autoAttach[contextKey]) this.autoAttach[contextKey] = {}
    if (enabled) this.autoAttach[contextKey][index] = true
    else delete this.autoAttach[contextKey][index]
},
isAutoAttach(contextKey, index) {
    return !!this.autoAttach[contextKey]?.[index]
},
```

- [ ] **Step 2: Purge the flag in `removeIndex`**

In `removeIndex`, after the label cleanup:

```javascript
if (this.autoAttach[contextKey]) {
    delete this.autoAttach[contextKey][index]
}
```

> **Note:** `isAutoAttach` is declared as an action (not a getter) so it can take args, exactly like `getLabel` in this store.

---

## Task 4: Frontend WS routing (`useWebSocket.js`)

**Files:**
- Modify: `frontend/src/composables/useWebSocket.js` (`terminal_list` case ~line 1254; add a new case near `terminal_renamed` ~line 1273)

- [ ] **Step 1: Feed the flags from discovery**

In the `terminal_list` case, after the `setLabels` call:

```javascript
store.setAutoAttachMap(msg.terminal_context, msg.autoAttach || {})
```
(Use the same `store` reference the case already resolved for `setIndices`/`setLabels`.)

- [ ] **Step 2: Handle the broadcast**

Add a case next to `terminal_renamed`:

```javascript
case 'terminal_autoattach_changed':
    if (msg.terminal_context != null && msg.terminal_index != null) {
        useTerminalTabsStore().setAutoAttach(msg.terminal_context, msg.terminal_index, !!msg.enabled)
    }
    break
```
(Match the existing import/use pattern for `useTerminalTabsStore` in this file.)

- [ ] **Step 3: Verify the WS file parses**

Run (Vite will HMR; just sanity-check syntax):
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && node --check frontend/src/composables/useWebSocket.js && echo OK
```
Expected: `OK` (note: `node --check` may choke on ESM `import` — if so, rely on the devctl HMR log check in Task 8 instead).

---

## Task 5: Frontend — the owner toggle (`TerminalPanel.vue`)

**Files:**
- Modify: `frontend/src/components/terminal/TerminalPanel.vue` (script: near the attach helpers ~line 917; template: in `.terminal-actions`, next to the Rename button ~line 1389)

- [ ] **Step 1: Script — current state + toggle action**

Add (in the "Attach parent-scope terminals" section):

```javascript
// Whether THIS panel's own scope can be an ancestor of other panels AND a live
// tmux session exists for the active tab (the flag is a tmux user option on the
// session — nothing to pin for a never-started Main or an index without a tmux
// session). Sessions are never ancestors.
const canBroadcast = computed(() =>
    isAncestorScope(props.contextKey)
    && usesTmux.value
    && (terminalTabsStore.indices[props.contextKey] || []).includes(activeIndex.value))

// AutoAttach flag of the active OWN tab (the toggle target).
const activeOwnAutoAttach = computed(() =>
    terminalTabsStore.isAutoAttach(props.contextKey, activeIndex.value))

function toggleAutoAttach() {
    if (isActiveAttached.value) return
    const next = !activeOwnAutoAttach.value
    // Optimistic local write; the broadcast will confirm cross-device.
    terminalTabsStore.setAutoAttach(props.contextKey, activeIndex.value, next)
    sendWsMessage({
        type: 'set_terminal_autoattach',
        terminal_context: props.contextKey,
        terminal_index: activeIndex.value,
        enabled: next,
    })
}
```

- [ ] **Step 2: Template — the button**

In `.terminal-actions`, before the Rename `<template v-if="!isActiveAttached">` block:

```html
<!-- AutoAttach into children — owner toggle (tmux ancestor scopes only) -->
<template v-if="canBroadcast && !isActiveAttached">
    <wa-button
        id="terminal-autoattach-button"
        variant="neutral"
        :appearance="activeOwnAutoAttach ? 'filled' : 'plain'"
        size="small"
        class="autoattach-button reduced-height"
        @click="toggleAutoAttach"
    >
        <wa-icon name="thumbtack" :label="activeOwnAutoAttach ? 'Disable auto-attach in children' : 'Auto-attach in children'"></wa-icon>
    </wa-button>
    <AppTooltip for="terminal-autoattach-button">{{ activeOwnAutoAttach ? 'Auto-attached in children — click to stop' : 'Auto-attach this terminal in children' }}</AppTooltip>
</template>
```

- [ ] **Step 3: Verify the icon is Font Awesome Free**

`thumbtack` is expected to be Free (solid). Confirm: open the running app, and if the icon renders, it's fine. If it 403s (Pro-only — see the project's FA-Free caveat), fall back to a confirmed-free glyph: try `bullhorn`, else reuse `link`. Update the `name="…"` accordingly.

- [ ] **Step 4: Compile-check**

Run the compile-check command on `TerminalPanel.vue`. Expected: `OK`.

---

## Task 6: Frontend — derived forced tabs in children (`TerminalPanel.vue`)

This is the core. Forced tabs are **derived** from the flags of `ancestorScopes`, never stored in `attachments`.

**Files:**
- Modify: `frontend/src/components/terminal/TerminalPanel.vue` (script: `forcedKeys` goes in the **pool-slots section right after `ancestorScopes` ~line 237** — see Step 1's TDZ note, NOT near `attachedTabs`; `attachedTabs` ~line 885, `attachSections` ~line 849, `applyRouteTermIndex`/`attachKeyFromRoute` ~line 616-664, the validity watcher ~line 950-957, the slot-publishing `watchEffect` ~line 972, discovery activation; template: Detach button ~line 1405)

- [ ] **Step 1: `forcedKeys` computed**

**Placement (critical — TDZ):** declare this in the pool-slots section right
after `ancestorScopes` (~line 237), **before** the route-reconciliation watcher.
Step 7 inserts `forcedKeys.value` into `applyRouteTermIndex` (~line 653), which is
invoked synchronously during setup by the `{ immediate: true }` watcher (~line
705-712) when the panel mounts on a deep-linked attached-terminal URL (string
`routeTermIndex`). A `const` declared later than that call site throws
`ReferenceError: Cannot access 'forcedKeys' before initialization`. This is the
same discipline already documented in the file for `ancestorScopes`/
`ancestorsUseTmux` (comments ~line 184-185 / 841-843); `attachKeyFromRoute`
survives today only because it's a hoisted `function`, which a `const` computed is
not. `forcedKeys` depends only on `ancestorsUseTmux`, `ancestorScopes`,
`terminalTabsStore`, `poolStore` — all available at that early point.

```javascript
// Keys of ancestor terminals whose owner flagged them AutoAttach-in-children.
// Derived from the store (fed by discovery + the autoattach broadcast); the
// source of truth for non-detachable tabs. tmux-only (non-tmux scopes have no
// backend flag), so this is empty unless ancestors use tmux.
// Declared HERE (with ancestorScopes, before route reconciliation) to avoid a
// TDZ: applyRouteTermIndex reads forcedKeys.value during the immediate route watch.
const forcedKeys = computed(() => {
    if (!ancestorsUseTmux.value) return []
    const keys = []
    for (const scope of ancestorScopes.value) {
        for (const index of terminalTabsStore.indices[scope.contextKey] || []) {
            if (terminalTabsStore.isAutoAttach(scope.contextKey, index)) {
                keys.push(poolStore.keyFor(scope.contextKey, index))
            }
        }
    }
    return keys
})
const isForced = (key) => forcedKeys.value.includes(key)
```

- [ ] **Step 2: Combine forced + manual into the rendered attached-tab list**

Replace `attachedTabs` so it iterates the **union** (forced first, in ancestor order, then manual extras), tagging each with `forced` and carrying the descriptor fields needed for materialization:

```javascript
const attachedTabs = computed(() => {
    const manual = poolStore.attachmentsFor(props.contextKey)
    const orderedKeys = [...forcedKeys.value, ...manual.filter((k) => !forcedKeys.value.includes(k))]
    return orderedKeys
        .map((key) => {
            const forced = isForced(key)
            // Descriptor: prefer the live pool descriptor; for a forced key whose
            // owner panel was never opened, synthesize from the ancestor scope.
            let d = poolStore.descriptors[key]
            const hash = key.lastIndexOf('#')
            const contextKey = key.slice(0, hash)
            const index = Number.parseInt(key.slice(hash + 1), 10)
            const scope = ancestorScopes.value.find((s) => s.contextKey === contextKey)
            if (!d && !scope) return null  // unknown ancestor (data not loaded) → skip for now
            const projectId = d?.projectId ?? scope?.projectId ?? null
            const cwd = d?.cwd ?? scope?.cwd ?? null
            const scopeLabel = scope ? scope.label : contextKey
            // Label source MUST match the original (TerminalPanel.vue:892-894):
            // tmux → store label (cross-device, from discovery); non-tmux → the
            // live descriptor's client-side label. `terminalTabsStore.labels` is
            // fed ONLY by the tmux-only terminal_list/terminal_renamed messages,
            // so getLabel() is always '' in non-tmux mode — dropping the d?.label
            // branch would regress a renamed non-tmux MANUAL attachment to its
            // default name. Forced tabs are tmux-only, so they take the tmux path.
            const foreignLabel = (ancestorsUseTmux.value
                ? terminalTabsStore.getLabel(contextKey, index)
                : (d?.label || terminalTabsStore.getLabel(contextKey, index))) || defaultLabel(index)
            return {
                key, contextKey, index, projectId, cwd, forced, scopeLabel,
                displayLabel: `${scopeLabel}: ${foreignLabel}`,
            }
        })
        .filter(Boolean)
})
```

> Note: this preserves the original tmux-vs-non-tmux label branch. Forced tabs are
> tmux-only (so `getLabel`); manual non-tmux attachments keep their client-side
> `d.label`. Verify both in the smoke test (Task 8 covers a non-tmux manual-attach
> label check).

- [ ] **Step 3: Mark forced items in the attach menu**

In `attachSections`, an item is `attached` if it's manually attached OR forced:

```javascript
attached: myAttached.includes(key) || forcedKeys.value.includes(key),
```
(So a forced terminal shows the disabled "✓ attached" state in the menu — it can't be toggled off from the child.)

- [ ] **Step 4: Materialize forced descriptors in the slot-publishing `watchEffect`**

In the `watchEffect` that publishes slots, change the attached-tab loop to create the descriptor for forced keys (`setSlot`), and only relocate (`setSlotTarget`) when the descriptor already exists:

```javascript
for (const tab of attachedTabs.value) {
    wanted.add(tab.key)
    const isActive = activeAttachedKey.value === tab.key
    if (tab.forced && !poolStore.descriptors[tab.key]) {
        // Owner panel never opened this session — create the instance here.
        // persist:false → tmux is re-attachable, so a GC on navigation is fine
        // (recreated from the flag on return).
        poolStore.setSlot(tab.key, {
            contextKey: tab.contextKey,
            index: tab.index,
            projectId: tab.projectId,
            sessionId: null,
            cwd: tab.cwd,
            startMode: 'auto',
            label: tab.displayLabel,
            persist: false,
        }, slotEls[tab.key], isActive)
    } else {
        poolStore.setSlotTarget(tab.key, slotEls[tab.key], isActive)
    }
}
```

> **Critical (from spec review):** `setSlotTarget` early-returns when no descriptor exists — using it on an absent forced descriptor renders a blank tab. Forced-absent MUST go through `setSlot`.

- [ ] **Step 5: Block detach for forced tabs**

Template — the Detach button block (`<template v-if="isActiveAttached">`) becomes conditional on not-forced:

```html
<template v-if="isActiveAttached && !isForced(activeAttachedKey)">
```

Script — guard `handleDetach` / `detachTerminal` defensively:

```javascript
function handleDetach() {
    if (activeAttachedKey.value !== null && !isForced(activeAttachedKey.value)) {
        detachTerminal(activeAttachedKey.value)
    }
}
```

> When a forced tab is active, neither Detach nor Rename shows (Rename is already hidden for any attached tab). That empty-action state is correct — the link icon + missing Detach conveys "forced".

- [ ] **Step 5b: Keep an active forced tab from being yanked away**

The existing validity watcher (TerminalPanel.vue ~line 950-957) resets
`activeAttachedKey` whenever the active key is **not** in
`poolStore.attachmentsFor(...)`. Forced keys are deliberately kept OUT of
`attachments`, so an active forced tab would be dropped the moment any unrelated
manual attach/detach mutates that list. Extend the guard to treat a forced key as
valid:

```javascript
watch(
    () => poolStore.attachmentsFor(props.contextKey).slice(),
    (keys) => {
        if (activeAttachedKey.value !== null
            && !keys.includes(activeAttachedKey.value)
            && !forcedKeys.value.includes(activeAttachedKey.value)) {
            activeAttachedKey.value = keys.length ? keys[keys.length - 1] : null
        }
    },
)
```

> Also sanity-check the other consumer of "is this attached key still valid": the
> auto-removal path when a source terminal exits. A forced tmux source that is
> killed disappears from discovery → `forcedKeys` drops it → its tab vanishes,
> which is the desired behavior; no extra code needed there.

- [ ] **Step 6: Proactive ancestor discovery on activation**

Today `requestAncestorDiscovery()` runs only on menu open. Add a watcher so a freshly-activated panel learns its ancestors' flags without opening the menu — only for tmux scopes still unknown in the store:

```javascript
watch(
    [() => props.active, () => dataStore.wsConnected],
    () => {
        if (!props.active || !ancestorsUseTmux.value || !dataStore.wsConnected) return
        for (const scope of ancestorScopes.value) {
            if (terminalTabsStore.indices[scope.contextKey] === undefined) {
                sendWsMessage({ type: 'list_terminals', terminal_context: scope.contextKey })
            }
        }
    },
    { immediate: true },
)
```

- [ ] **Step 7: Route resolution accepts a forced key**

In `applyRouteTermIndex` (string branch), treat a forced key as resolvable without an `attachments` entry:

```javascript
if (poolStore.attachmentsFor(props.contextKey).includes(target)
    || forcedKeys.value.includes(target)
    || attachKeyFromRoute(target)) {
```

The existing retry watcher (keyed on ancestor discovery + live instance) already re-fires when discovery lands, which now also carries the flags — a deep-linked forced tab resolves once its scope is discovered.

- [ ] **Step 8: Compile-check**

Run the compile-check on `TerminalPanel.vue`. Expected: `OK`.

---

## Task 7: CHANGELOG

**Files:**
- Modify: `CHANGELOG.md` (under `[Unreleased] → Added`)

- [ ] **Step 1: Add the entry**

```markdown
- **Auto-attach parent terminals** — a tmux terminal at a parent scope (worktree, project, workspace, or global) can be flagged "auto-attach in children" from its action bar; it then appears as a non-detachable attached tab in every descendant panel. tmux-only.
```

---

## Task 8: Integration verification (no automated tests)

- [ ] **Step 1: Restart the worktree servers**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && uv run ./devctl.py restart all
```
Then read the tail of `logs/backend.log` and `logs/frontend.log` — expect a clean Vite HMR/startup, no errors, no full-reload loop.

- [ ] **Step 2: Smoke test (manual, or ask the user to verify in the browser)**

With `terminalUseTmux` **on** (the toggle is tmux-only):
1. Open a project (or global/workspace) terminal → the AutoAttach (thumbtack) button shows; click it → button goes "filled".
2. Open a session of that project → the project terminal appears as an attached tab (`link` icon, label `Project: <name>`), with **no Detach button**.
3. Turn the flag off on the project panel → the forced tab disappears from the session.
4. Reload the page → the flag persists (re-discovered from tmux); reopening a child re-attaches it.
5. Confirm a manual attachment still has its Detach button and still works.

Then, with `terminalUseTmux` **off** (non-tmux), regression-check the label path:
6. The AutoAttach button must be **absent** on every panel (tmux-only).
7. Manually attach a parent terminal that was **renamed**, into a child → the attached tab must show the custom name (`Scope: <custom>`), NOT the default `Main`/`Term N`. (Guards the non-tmux `d?.label` branch.)

> Per the project rule, do NOT drive Chrome yourself unless the user asks — report "done" and let the user verify, or test via Chrome only on explicit request.

- [ ] **Step 3: Commit — ONLY when the user explicitly asks**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/attach-parent-terms && git add \
  src/twicc/terminal.py src/twicc/asgi.py \
  frontend/src/stores/terminalTabs.js \
  frontend/src/composables/useWebSocket.js \
  frontend/src/components/terminal/TerminalPanel.vue \
  CHANGELOG.md \
  docs/superpowers/specs/2026-06-20-terminal-autoattach-design.md \
  docs/superpowers/plans/2026-06-20-terminal-autoattach.md
git commit -m "$(cat <<'EOF'
feat(terminal): auto-attach parent terminals into children (tmux)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```
(Stage files explicitly — never `git add -A`. Confirm the working tree only contains intended changes first.)

---

## Notes for the implementer

- **Reactivity:** `terminalTabsStore.autoAttach` is plain Pinia state — reading it inside `computed`/`watch` is reactive. The `isAutoAttach` action reads state, so it tracks too.
- **No new keep-alive rule:** forced tmux descriptors are transient (`persist:false`); the `watchEffect` rebuilds them from `forcedKeys` on every active render, and tmux re-attaches server-side.
- **Order of edits:** Task 1→2 (backend) can be verified independently before touching the frontend. Task 6 depends on Tasks 3-4 (store + WS) being in place.
- **Web Awesome:** no new component is imported (reusing `wa-button`/`wa-icon`/`AppTooltip` already imported). If you somehow add a new `wa-*`, register it in `frontend/src/main.js`.
