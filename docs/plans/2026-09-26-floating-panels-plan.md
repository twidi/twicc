# Floating Panels on a Lit Canvas — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give TwiCC a tinted, softly lit canvas background with the sidebar sitting on it and the working zones as floating rounded cards — first one card for the whole content area (commit 1), then one card per dock region (commit 2).

**Architecture:** One new global stylesheet (`styles/surfaces.css`) owns every token and the `.panel-card` class. Commit 1 is CSS-only on the app shell. Commit 2 adds a small pure helper module (`utils/panelInsets.js`) that turns the layout resolver's edge-to-edge rects into rects inset by half a gap on their inner edges (as CSS `calc()` strings, so the gap stays in `rem`), and computes which corners of a pooled iframe must be rounded; the layout components and `FrameHost` consume it. The pure resolver (`utils/layoutResolver.js`) is not modified.

**Tech Stack:** Vue 3 (`<script setup>`, scoped CSS), Web Awesome 3 tokens, Pinia (`stores/framePool.js`), `@vueuse/core` (`useElementBounding`), `node:test` for unit tests.

**Spec:** `docs/plans/2026-09-26-floating-panels-design.md` — read it first; every task argues from it (§ numbers below refer to it).

## Global Constraints

- All written artifacts (code, comments, names, docs) in English.
- Layout sizes in `rem` or Web Awesome tokens. Px only where already px or sub-pixel tolerances: resolver rects, `--divider-size`, shadow offsets/blurs, ε = 0.5px, `FLUSH_TOLERANCE_PX = 6`.
- Never add `transform`, `filter`, `backdrop-filter`, `contain: paint|layout`, `will-change` or `container-type` to `.main-content`'s branches, `.session-layout`, `.center-slot`, `.dock-region` or `.layout-overlay` (spec §7).
- Never move an iframe in the DOM; never wrap `FrameHost` cells (spec §7).
- `utils/layoutResolver.js` is not modified; `frontend/src/utils/layoutResolver.test.js` must stay green.
- Panel surfaces stay `--wa-color-surface-default` (`getSurfaceColor()` users must keep matching).
- `--panel-shadow` horizontal reach must stay ≤ 4px (spec §4, shadow budget).
- Every card carries the `.panel-card` class — no copies of its values (spec §4).
- Frontend commands run from `frontend/` in the worktree. Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && `. Never run `npm install`/`npm ci` or `migrate` by hand; `devctl.py start` does it.
- Commits: only when the user explicitly says so (after reviewing each commit's result in the browser). Conventional Commit subject, descriptive body, `Co-Authored-By` trailer with the running model's exact name (the commands below use `Claude Opus 5.5 (1M context)`; replace it if another model runs the commit). Stage files by explicit path. No CHANGELOG entry unless the user asks.
- Web Awesome events/parts: custom events keep the `wa-` prefix; parts are styled through `::part()`.

## Review Focus

Failure modes the spec implies that no unit test exercises (each is pinned by a manual check in the task that owns it):

1. **`awesome` theme** (4px dividers, 1.5× radius): card borders, divider line, corner insets and iframe corners still look right — Task 3 and Task 6 checks.
2. **Extreme font sizes** (12 and 18+): gap/radius follow the setting; at 12 the side shadows are not cut — Task 3 and Task 5 checks.
3. **Narrow viewport (< 640px)**: page never scrolls, drawer opaque, cards inset on all sides — Task 3 and Task 5 checks.
4. **Fullscreen file preview** from a docked/overlay pane still covers the whole window (no containing block introduced) — Task 5 check.
5. **KeepAlive session switch** with a docked Browser pane: iframe corners still correct after coming back — Task 6 check.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/src/styles/surfaces.css` | Create (T2) | Canvas + panel tokens, canvas painting, `.panel-card` |
| `frontend/src/main.js` | Modify (T2) | Import `surfaces.css` |
| `frontend/src/App.vue` | Modify (T2) | Transparent `.app-container`, canvas on `.connecting-backdrop` |
| `frontend/src/views/LoginView.vue` | Modify (T2) | Canvas on `.login-backdrop` |
| `frontend/src/composables/useSplitDividerDragFlag.js` | Modify (T3) | Also return a reactive `dragging` ref |
| `frontend/src/views/ProjectView.vue` | Modify (T3, T5) | Sidebar on canvas, divider as gap, content card(s), toggle offset |
| `frontend/src/utils/panelInsets.js` | Create (T4) | Pure helpers: inner edges, inset style, visible rect, flush corners, frame clip-path |
| `frontend/src/utils/panelInsets.test.js` | Create (T4) | Unit tests |
| `frontend/src/views/SessionView.vue` | Modify (T5) | Clip, header separators, fallback cards, center tab strip insets |
| `frontend/src/components/session/layout/SessionLayout.vue` | Modify (T5) | Center card + insets, pass insets to regions/overlay, clip margin |
| `frontend/src/components/session/layout/DockRegion.vue` | Modify (T5) | Region card + insets, top bar inset |
| `frontend/src/components/session/layout/LayoutOverlay.vue` | Modify (T5) | Overlay card + insets, backdrop radius, top bar inset |
| `frontend/src/components/session/layout/DockGutter.vue` | Modify (T5) | Transparent rails |
| `frontend/src/stores/framePool.js` | Modify (T6) | `cardRect` field |
| `frontend/src/components/frames/PersistentFrame.vue` | Modify (T6) | Resolve + measure the containing card |
| `frontend/src/components/frames/FrameHost.vue` | Modify (T5, T6) | `overflow: clip` (T5); rounded corners (T6) |

Commit 1 = Tasks 2–3. Commit 2 = Tasks 4–7.

---

### Task 1: Dedicated dev instance for the worktree

**Files:** none (environment only).

- [ ] **Step 1: Check the cwd is the worktree**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git rev-parse --show-toplevel && git branch --show-current`
Expected: `/home/twidi/dev/twicc-poc/.worktrees/enhanced-ui` and `enhanced-ui`.

- [ ] **Step 2: Start the worktree's servers (existing DB copied on first setup)**

Run (in the background — it can take minutes on first setup): `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && uv run ./devctl.py start`
Expected: output ends with the frontend and backend URLs (ports are usually 5174 / 3501). A port-check timeout during initial sync is not a failure: confirm with `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && uv run ./devctl.py logs back --lines=40`.

- [ ] **Step 3: Give the user the frontend URL**

Report the frontend URL from devctl's output. Every later manual check uses this URL (never the main instance on 5173).

- [ ] **Step 4: Record the unit-test baseline**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test 2>&1 | grep -E '^# (tests|pass|fail)'`
Expected: `# fail 0`. Note the `# tests` count: Task 3 Step 8 compares against it.

---

### Task 2: Tokens, canvas painting and `.panel-card` (commit 1, part 1)

**Files:**
- Create: `frontend/src/styles/surfaces.css`
- Modify: `frontend/src/main.js` (after the `transcript-tokens.css` import)
- Modify: `frontend/src/App.vue` (`.connecting-backdrop`, `.app-container` in the unscoped `<style>`)
- Modify: `frontend/src/views/LoginView.vue` (`.login-backdrop`)

**Interfaces:**
- Produces (CSS custom properties on `:root`, used by every later task): `--canvas-color`, `--canvas-aura-start`, `--canvas-aura-end`, `--canvas-background`, `--panel-gap`, `--panel-half-gap`, `--panel-radius`, `--panel-inner-radius`, `--panel-corner-inset`, `--panel-border`, `--panel-shadow`; class `.panel-card`.

- [ ] **Step 1: Create `frontend/src/styles/surfaces.css`**

```css
/* Floating panels on a lit canvas — visual refresh step 1.
   Design: docs/plans/2026-09-26-floating-panels-design.md.
   SPA only: the share bundle imports transcript-tokens.css, not this file.

   Declaration order matters (all three blocks match <html>): light tokens on
   :root, dark overrides on .wa-dark (same specificity, later wins), then the
   narrow-viewport override, which MUST stay on :root — the derived tokens
   (--panel-half-gap, --panel-inner-radius, --panel-corner-inset) are computed
   where they are declared and inherited as values, so an override placed lower
   in the tree would leave them at their desktop values. */

:root {
    --canvas-color: color-mix(in oklab, var(--wa-color-brand-95) 30%, var(--wa-color-neutral-95));
    --canvas-aura-start: color-mix(in oklab, var(--wa-color-brand-90) 60%, transparent);
    --canvas-aura-end: oklch(from var(--wa-color-brand-90) l c calc(h + 70) / 0.45);
    /* Declared once: its var() references resolve on <html>, where .wa-dark also applies. */
    --canvas-background:
        radial-gradient(55rem 38rem at -5% -10%, var(--canvas-aura-start), transparent 65%),
        radial-gradient(50rem 36rem at 105% 110%, var(--canvas-aura-end), transparent 65%),
        var(--canvas-color);

    --panel-gap: var(--wa-space-xs);
    --panel-half-gap: calc(var(--panel-gap) / 2);
    --panel-radius: var(--wa-border-radius-l);
    /* Radius of a card's padding box: what overflow clipping and iframe corners follow. */
    --panel-inner-radius: calc(var(--panel-radius) - var(--divider-size));
    /* Inline inset of the tab bars, so top-corner controls are not cut by the radius. */
    --panel-corner-inset: calc(var(--panel-radius) / 3);
    --panel-border: var(--divider-size) solid var(--wa-color-surface-border);
    /* Shadow budget: zero x-offsets and a negative spread on the large layer keep the
       horizontal reach at 4px, below the desktop gap at every font size. Keep it so when
       tuning — every clip in the layout stops at the gap. */
    --panel-shadow:
        0 1px 2px oklch(0.25 0.02 275 / 0.06),
        0 6px 10px -6px oklch(0.25 0.02 275 / 0.14);
}

.wa-dark {
    --canvas-color: color-mix(in oklab, color-mix(in oklab, var(--wa-color-surface-default), black 30%) 92%, var(--wa-color-brand-20));
    --canvas-aura-start: color-mix(in oklab, var(--wa-color-brand-30) 35%, transparent);
    --canvas-aura-end: oklch(from var(--wa-color-brand-30) l c calc(h + 70) / 0.28);
    --panel-shadow:
        0 1px 2px oklch(0 0 0 / 0.4),
        0 6px 10px -6px oklch(0 0 0 / 0.55);
}

@media (width < 640px) {
    :root {
        --panel-gap: var(--wa-space-2xs);
        --panel-radius: var(--wa-border-radius-m);
    }
}

/* Unlayered, so it beats Web Awesome's `html { background-color }` in @layer wa-native.
   Covers overscroll areas. (index.html's inline html.loading rule wins on <html> while
   loading, but body::before paints the canvas over it as soon as this sheet is applied.) */
html {
    background-color: var(--canvas-color);
}

/* The auras. A fixed layer rather than background-attachment: fixed, which repaints on
   every scroll of the home page. */
body::before {
    content: "";
    position: fixed;
    inset: 0;
    z-index: -1;
    pointer-events: none;
    background: var(--canvas-background);
}

/* One floating card. Every card carries this class: it is also the marker the pooled
   iframes look for to round their corners (PersistentFrame). */
.panel-card {
    background: var(--wa-color-surface-default);
    border: var(--panel-border);
    border-radius: var(--panel-radius);
    box-shadow: var(--panel-shadow);
}
```

- [ ] **Step 2: Import it in `frontend/src/main.js`**

After the line `import './styles/transcript-tokens.css'`, add:

```js
// Canvas + floating-panel tokens (SPA only — the share bundle does not import it).
import './styles/surfaces.css'
```

- [ ] **Step 3: Make `.app-container` transparent (`frontend/src/App.vue`)**

Replace:

```css
.app-container {
    min-height: 100dvh;
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-normal);
}
```

with:

```css
/* Transparent: the canvas (styles/surfaces.css) shows through. */
.app-container {
    min-height: 100dvh;
    color: var(--wa-color-text-normal);
}
```

- [ ] **Step 4: Paint the canvas on the fixed backdrops**

In `frontend/src/App.vue`, `.connecting-backdrop`: replace `background: var(--wa-color-surface-default);` with:

```css
    /* Full canvas (auras included) — a fixed layer above everything would hide body::before. */
    background: var(--canvas-background);
```

In `frontend/src/views/LoginView.vue`, `.login-backdrop`: same replacement.

- [ ] **Step 5: Visual check in the worktree instance**

Open the Task 1 URL.
Expected: Home shows a slightly tinted background with a faint accent glow top-left and bottom-right; the workspace/project cards stand out as white (light) / raised (dark). Switch Settings › Color scheme light/dark and two accents: canvas follows.

The login backdrop cannot be seen on the worktree instance: its `.env` has no password and a worktree backend lets loopback requests past the password, so `/login` redirects to Home. Check `.login-backdrop` by reading the rule only (it must be `background: var(--canvas-background)` and stay `position: fixed`); it will be seen on the main instance after merge.

---

### Task 3: Sidebar on the canvas, divider as gap, content card, toggle (commit 1, part 2)

**Files:**
- Modify: `frontend/src/composables/useSplitDividerDragFlag.js`
- Modify: `frontend/src/views/ProjectView.vue` (script near line 85, template `<wa-split-panel>` and `<main>`, scoped styles)

**Interfaces:**
- Consumes: tokens from Task 2.
- Produces: `useSplitDividerDragFlag(splitPanelRef)` now returns `{ dragging }` (`Ref<boolean>`); existing callers ignoring the return value keep working.

- [ ] **Step 1: Expose the drag state from `useSplitDividerDragFlag.js`**

Replace the whole file body with:

```js
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useFramePoolStore } from '../stores/framePool'

/**
 * Flag divider drags of a <wa-split-panel> into the frame pool so FrameHost
 * can neutralize iframe pointer-events for the duration (an iframe would
 * otherwise capture pointermove and freeze the drag). The docking gutters
 * have their own wiring in SessionLayout.vue; this covers the three plain
 * wa-split-panels (project sidebar, FilesPanel tree/content, GitPanel
 * tree/content) whose drags over an iframe are broken today already.
 *
 * Also returns the drag state as a ref (`dragging`): wa-split-panel exposes
 * none, and the project sidebar divider shows its line while dragged.
 */
export function useSplitDividerDragFlag(splitPanelRef) {
    const pool = useFramePoolStore()
    const dragging = ref(false)

    function onPointerDown(event) {
        // THIS panel's own divider only (WA exposes it as the `divider` property). A
        // part="divider" test would also match a nested split panel's divider bubbling up
        // through this host (e.g. the Files/Git tree splitters inside the project content).
        const divider = splitPanelRef.value?.divider
        if (!divider || !event.composedPath().includes(divider)) return
        dragging.value = true
        pool.beginDividerDrag()
    }

    function onPointerEnd() {
        if (!dragging.value) return
        dragging.value = false
        pool.endDividerDrag()
    }

    onMounted(() => {
        splitPanelRef.value?.addEventListener('pointerdown', onPointerDown)
        window.addEventListener('pointerup', onPointerEnd, true)
        window.addEventListener('pointercancel', onPointerEnd, true)
    })
    onBeforeUnmount(() => {
        splitPanelRef.value?.removeEventListener('pointerdown', onPointerDown)
        window.removeEventListener('pointerup', onPointerEnd, true)
        window.removeEventListener('pointercancel', onPointerEnd, true)
        onPointerEnd() // never leave the depth stuck if unmounted mid-drag
    })

    return { dragging }
}
```

(The spec §5.2 describes setting the class from `handleSplitPanelPointerDown` plus a window listener; this composable already implements that pointerdown / window-pointerup pair, so reusing it avoids a second copy. What makes the reuse equivalent is the new divider filter — `composedPath().includes(splitPanel.divider)`, the same test as `handleSplitPanelPointerDown` — which also fixes the old part-attribute test counting nested split panels' dividers. `FilesPanel` and `GitPanel` keep working: each one's own divider still matches. The touch grip is slotted inside `.divider`, so it is in the path. The double-click reset (`handleSplitPanelPointerDown`) is untouched.)

- [ ] **Step 2: Bind the class in `ProjectView.vue`**

Replace:

```js
const projectSplitRef = ref(null)
useSplitDividerDragFlag(projectSplitRef)
```

with:

```js
const projectSplitRef = ref(null)
// `sidebarResizing` drives the divider line while the sidebar is being resized.
const { dragging: sidebarResizing } = useSplitDividerDragFlag(projectSplitRef)
```

In the template, on `<wa-split-panel ref="projectSplitRef" class="project-view" …>`, add `:class="{ 'sidebar-resizing': sidebarResizing }"`.

- [ ] **Step 3: Make `<main>` a card (template)**

Replace `<main slot="end" class="main-content" :class="{ 'main-content--preview-expanded': previewExpanded }">` with:

```html
<main slot="end" class="main-content panel-card" :class="{ 'main-content--preview-expanded': previewExpanded }">
```

- [ ] **Step 4: Scoped styles — split panel, divider, sidebar**

In `ProjectView.vue` scoped `<style>`:

a) In the existing `.project-view { … }` block, add as the first declarations:

```css
    /* The divider column IS the gap between the sidebar and the content card.
       Beats App.vue's global `wa-split-panel { --divider-width: … !important }`. */
    --divider-width: var(--panel-gap) !important;
```

b) Replace:

```css
wa-split-panel::part(divider) {
    /* same color/width as normal dividers */
    background-color: var(--wa-color-surface-border);
    width: var(--divider-size);
}
```

with:

```css
/* The divider spans the whole gap and is invisible at rest; its line is a centered
   background image (not a fill, not ::after — WA uses .divider::after as the hit area),
   shown at half strength on hover and full while dragging, like the layout splitters.
   Opacity lives in the color so the slotted touch grip stays fully visible. */
.project-view::part(divider) {
    --line: transparent;
    background: linear-gradient(var(--line), var(--line)) center / var(--divider-size) 100% no-repeat;
}
.project-view::part(divider):hover {
    --line: color-mix(in oklab, var(--wa-color-brand-fill-loud) 50%, transparent);
}
.project-view.sidebar-resizing::part(divider) {
    --line: var(--wa-color-brand-fill-loud);
}
```

c) In `.sidebar { … }`, replace `background: var(--wa-color-surface-default);` with `background: transparent; /* on the canvas */`.

- [ ] **Step 5: Scoped styles — the content card**

Replace the `.main-content { … }` block with:

```css
/* Commit 1: the whole content area is one floating card (.panel-card in the template
   gives background, border, radius, shadow). The left gap is the divider column. */
.main-content {
    flex: 1;
    min-width: 0;
    height: calc(100% - 2 * var(--panel-gap));
    margin-block: var(--panel-gap);
    margin-inline-end: var(--panel-gap);
    /* With the radius, also clips the pooled iframes (FrameHost lives inside). */
    overflow: hidden;
    z-index: 1;
    /* Containing block for the absolutely-positioned FrameHost, stable in both
       states (container-type below is dropped while a preview is expanded). */
    position: relative;
    container-type: inline-size;
    container-name: main-content;
}
```

- [ ] **Step 6: Scoped styles — the reopen toggle**

a) In the `.sidebar-toggle { … }` block, replace:

```css
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
```

with:

```css
    /* Offset from the footer's corner; --sidebar-toggle-shift adds the panel gap while the
       desktop sidebar is collapsed, so the floating toggle keeps its distance from the card. */
    --sidebar-toggle-offset: var(--wa-space-s);
    bottom: calc(var(--sidebar-toggle-offset) + var(--sidebar-toggle-shift, 0px));
    left: calc(var(--sidebar-toggle-offset) + var(--sidebar-toggle-shift, 0px));
```

b) In `@container sidebar (width <= 19rem)`, replace:

```css
    .sidebar-footer-buttons--with-inbox .sidebar-toggle {
        bottom: var(--wa-space-xs);
        left: var(--wa-space-xs);
    }
```

with:

```css
    .sidebar-footer-buttons--with-inbox .sidebar-toggle {
        --sidebar-toggle-offset: var(--wa-space-xs);
    }
```

c) After the rule `.project-view-wrapper:has(.sidebar-toggle-checkbox:checked) .project-view { … }` (desktop collapsed grid), add:

```css
/* Desktop collapsed sidebar: the toggle floats over the content card, which starts one gap
   in — shift it by that gap. Keyed on the checkbox (the fact that collapses the grid), not on
   body.sidebar-closed, which goes stale across the 640px breakpoint. */
@media (width >= 640px) {
    .project-view-wrapper:has(.sidebar-toggle-checkbox:checked) .sidebar-toggle {
        --sidebar-toggle-shift: var(--panel-gap);
    }
}
```

- [ ] **Step 7: Scoped styles — narrow viewport**

Inside `@media (width < 640px)`:

a) In the `.project-view { display: block; … }` rule, add:

```css
        /* The inset comes from padding here: a top margin on .main-content would collapse
           through this block host and the wrappers, and scroll the page. */
        padding: var(--panel-gap);
```

b) In the drawer `.sidebar { … }` rule, add `background: var(--canvas-color); /* the drawer overlays the content: opaque */`.

c) Add:

```css
    .main-content {
        margin: 0;
        height: 100%;
    }
```

- [ ] **Step 8: Run the unit suite (nothing should change)**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test 2>&1 | grep -E '^# (tests|pass|fail)'`
Expected: `# fail 0`, and the same `# tests` count as the baseline recorded in Task 1 Step 4.

- [ ] **Step 9: Manual check (spec §8, commit 1 scope)**

In the worktree instance, light and dark, two accents, themes `default` / `awesome` / `shoelace`, font sizes 12, 14, 18:
- sidebar on the canvas; content is one rounded card with a gap on top/right/bottom and toward the sidebar;
- hover the gap between sidebar and card: a thin accent line; drag it: full-strength line, sidebar resizes; double-click the divider still resets the width;
- collapse the sidebar (button and Alt+Shift+B): card keeps a left gap; the reopen toggle sits inside the card clear of its rounded corner — with and without the peer inbox configured;
- a Browser pane / HTML preview reaching a card corner: rounded by the card;
- fullscreen file preview: covers the whole window;
- narrow window (< 640px): page does not scroll, drawer opaque (canvas color), card inset on all four sides; close the drawer, widen past 640px, and the reverse: toggle in the right place;
- Home, project detail, artifacts browser;
- "Connecting to server..." overlay: seeing it needs the worktree backend stopped. **Ask the user first.** With their OK, use the safe sequence (a plain `stop back` can leave a zombie `run.py` that keeps serving, so the overlay never shows):

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && uv run ./devctl.py stop back
  W=/home/twidi/dev/twicc-poc/.worktrees/enhanced-ui
  for i in $(seq 1 30); do pgrep -f "$W/.venv/bin/python3 \./run\.py" >/dev/null || break; sleep 1; done
  PIDS=$(pgrep -f "$W/.venv/bin/python3 \./run\.py" || true); [ -n "$PIDS" ] && { kill $PIDS; sleep 3; }
  ```

  Reload the page (overlay with the canvas behind "Connecting…"), then restart and wait for the HTTP port (from devctl's output, usually 3501):

  ```bash
  cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && uv run ./devctl.py start back
  for i in $(seq 1 90); do curl -s -o /dev/null http://localhost:3501/api/projects/ && break; sleep 2; done
  ```

  Without the user's OK, check `.connecting-backdrop` by reading the rule only.

- [ ] **Step 10: Hand over commit 1 to the user**

Tell the user commit 1 is ready to look at (URL). Wait for their go. On "commit":

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git add frontend/src/styles/surfaces.css frontend/src/main.js frontend/src/App.vue frontend/src/views/LoginView.vue frontend/src/composables/useSplitDividerDragFlag.js frontend/src/views/ProjectView.vue docs/plans/2026-09-26-floating-panels-design.md docs/plans/2026-09-26-floating-panels-plan.md
git commit -m "feat(ui): lit canvas background with the content as a floating card" -m "The UI read flat: one surface everywhere, hairline dividers between zones. The page background becomes a canvas slightly tinted by the accent, with two faint accent auras (styles/surfaces.css holds every token and the .panel-card class). The sidebar sits directly on it; the sidebar divider becomes the gap between the sidebar and the content, with an accent line on hover and while dragging; the whole content area becomes one rounded card with a soft downward shadow. The reopen toggle, the mobile drawer and the login/connecting backdrops follow. First of two commits of docs/plans/2026-09-26-floating-panels-design.md." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Pure helpers `utils/panelInsets.js` (TDD)

**Files:**
- Create: `frontend/src/utils/panelInsets.js`
- Test: `frontend/src/utils/panelInsets.test.js`

**Interfaces:**
- Produces:
  - `innerEdges(rect: {x,y,w,h}, viewport: {w,h}) → { left, top, right, bottom }` (booleans)
  - `insetRectStyle(rect: {x,y,w,h}, edges) → { left, top, width, height }` (CSS strings using `var(--panel-half-gap)`)
  - `NO_INSETS` — frozen all-false edges object
  - `visibleRect(frameRect: {x,y,width,height}, clipRect|null) → {x,y,width,height}`
  - `frameFlushCorners(visible: {x,y,width,height}, cardRect: {x,y,width,height}) → { tl, tr, br, bl }`
  - `frameClipPath(frameRect, clipRect|null, corners|null) → string|null`
  - `FLUSH_TOLERANCE_PX = 6`, `CARD_CORNER_RADIUS = 'var(--panel-inner-radius)'`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/utils/panelInsets.test.js`:

```js
// Run with: node --test src/utils/panelInsets.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveLayout } from './layoutResolver.js'
import {
    CARD_CORNER_RADIUS,
    FLUSH_TOLERANCE_PX,
    NO_INSETS,
    frameClipPath,
    frameFlushCorners,
    innerEdges,
    insetRectStyle,
    visibleRect,
} from './panelInsets.js'

const TABS = [
    { id: 'main', label: 'Chat', fixedCenter: true },
    { id: 'files', label: 'Files' },
    { id: 'git', label: 'Git' },
    { id: 'terminal', label: 'Terminal' },
    { id: 'browser', label: 'Browser' },
]
const layout = (assignment, viewport, extra = {}) => resolveLayout({ tabs: TABS, assignment, viewport, ...extra })
const region = (render, id) => {
    const r = render.regions.find((x) => x.id === id)
    assert.ok(r, `region ${id} missing (got ${render.regions.map((x) => x.id).join(', ')})`)
    return r
}
const edgesOf = (render, id) => innerEdges(region(render, id), render.viewport)
const E = (left, top, right, bottom) => ({ left, top, right, bottom })

test('widescreen: left column, split right column, center, bottom under the center', () => {
    const r = layout({ files: 'left-top', git: 'right-top', terminal: 'right-bottom', browser: 'bottom-left' }, { w: 1600, h: 900 })
    assert.equal(r.mode, 'widescreen')
    assert.deepEqual(edgesOf(r, 'left-col'), E(false, false, true, false))
    assert.deepEqual(edgesOf(r, 'right-top'), E(true, false, false, true))
    assert.deepEqual(edgesOf(r, 'right-bottom'), E(true, true, false, false))
    assert.deepEqual(edgesOf(r, 'center'), E(true, false, true, true))
    assert.deepEqual(edgesOf(r, 'bottom'), E(true, true, true, false))
})

test('classic: full-width bottom under a center and a right column', () => {
    const r = layout({ files: 'right-top', terminal: 'bottom-left' }, { w: 850, h: 900 })
    assert.equal(r.mode, 'classic')
    assert.deepEqual(edgesOf(r, 'center'), E(false, false, true, true))
    assert.deepEqual(edgesOf(r, 'right-col'), E(true, false, false, true))
    assert.deepEqual(edgesOf(r, 'bottom'), E(false, true, false, false))
})

test('merged siblings: one right region spanning the full height', () => {
    const r = layout({ git: 'right-top', terminal: 'right-bottom' }, { w: 1600, h: 500 })
    const col = region(r, 'right-col')
    assert.equal(col.merged, true)
    assert.deepEqual(innerEdges(col, r.viewport), E(true, false, false, false))
})

test('split left column and split bottom', () => {
    const left = layout({ files: 'left-top', git: 'left-bottom' }, { w: 1600, h: 900 })
    assert.deepEqual(edgesOf(left, 'left-top'), E(false, false, true, true))
    assert.deepEqual(edgesOf(left, 'left-bottom'), E(false, true, true, false))
    const bottom = layout({ terminal: 'bottom-left', browser: 'bottom-right' }, { w: 1600, h: 900 })
    assert.deepEqual(edgesOf(bottom, 'bottom-left'), E(false, true, true, false))
    assert.deepEqual(edgesOf(bottom, 'bottom-right'), E(true, true, false, false))
    assert.deepEqual(edgesOf(bottom, 'center'), E(false, false, false, true))
})

test('a rail on each edge makes the facing center edge inner', () => {
    const left = layout({ files: 'left-top' }, { w: 1600, h: 900 }, { collapsed: ['left-top'] })
    assert.ok(left.gutters.some((g) => g.edge === 'left'))
    assert.deepEqual(edgesOf(left, 'center'), E(true, false, false, false))
    const right = layout({ files: 'right-top' }, { w: 1600, h: 900 }, { collapsed: ['right-top'] })
    assert.ok(right.gutters.some((g) => g.edge === 'right'))
    assert.deepEqual(edgesOf(right, 'center'), E(false, false, true, false))
    const bottom = layout({ terminal: 'bottom-left' }, { w: 1600, h: 900 }, { collapsed: ['bottom-left'] })
    assert.ok(bottom.gutters.some((g) => g.edge === 'bottom'))
    assert.deepEqual(edgesOf(bottom, 'center'), E(false, false, false, true))
})

test('bottom overlay: stops above the bottom rail', () => {
    // Height below centerMinH + bottomMinH (370) → the bottom becomes an overlay over a rail.
    const r = layout({ terminal: 'bottom-left' }, { w: 1600, h: 300 })
    const ov = r.overlays.find((o) => o.edge === 'bottom')
    assert.ok(ov, 'bottom overlay expected')
    assert.deepEqual(innerEdges(ov.rect, r.viewport), E(false, true, false, true))
    assert.deepEqual(edgesOf(r, 'center'), E(false, false, false, true))
})

test('overlay rect: inset from the rail and from the escape strip', () => {
    const r = layout({ files: 'right-top' }, { w: 700, h: 900 })
    const ov = r.overlays.find((o) => o.edge === 'right')
    assert.ok(ov, 'right overlay expected')
    assert.deepEqual(innerEdges(ov.rect, r.viewport), E(true, false, true, false))
    assert.deepEqual(edgesOf(r, 'center'), E(false, false, true, false))
})

test('maximized and tabs mode: one full region, no inner edge', () => {
    const max = layout({ files: 'right-top' }, { w: 1600, h: 900 }, { maximized: ['center'] })
    assert.deepEqual(innerEdges(max.regions[0], max.viewport), NO_INSETS)
    const tabs = layout({ files: 'right-top' }, { w: 500, h: 900 })
    assert.equal(tabs.mode, 'tabs')
    assert.deepEqual(innerEdges(tabs.regions[0], tabs.viewport), NO_INSETS)
})

test('innerEdges tolerates sub-pixel rects on the boundary', () => {
    assert.deepEqual(innerEdges({ x: 0.3, y: 0, w: 999.8, h: 600 }, { w: 1000, h: 600 }), NO_INSETS)
})

test('insetRectStyle moves only the inner edges, in CSS', () => {
    assert.deepEqual(insetRectStyle({ x: 10, y: 0, w: 100, h: 50 }, E(true, false, false, true)), {
        left: 'calc(10px + var(--panel-half-gap) * 1)',
        top: 'calc(0px + var(--panel-half-gap) * 0)',
        width: 'calc(100px - var(--panel-half-gap) * 1)',
        height: 'calc(50px - var(--panel-half-gap) * 1)',
    })
    assert.deepEqual(insetRectStyle({ x: 0, y: 0, w: 10, h: 10 }, E(true, true, true, true)).width,
        'calc(10px - var(--panel-half-gap) * 2)')
})

const CARD = { x: 100, y: 100, width: 400, height: 300 }
const C = (tl, tr, br, bl) => ({ tl, tr, br, bl })

test('frameFlushCorners: frame filling the card inside a 1px or 4px border', () => {
    assert.deepEqual(frameFlushCorners({ x: 101, y: 101, width: 398, height: 298 }, CARD), C(true, true, true, true))
    assert.deepEqual(frameFlushCorners({ x: 104, y: 104, width: 392, height: 292 }, CARD), C(true, true, true, true))
})

test('frameFlushCorners: frame under a toolbar reaches only the bottom corners', () => {
    assert.deepEqual(frameFlushCorners({ x: 101, y: 150, width: 398, height: 249 }, CARD), C(false, false, true, true))
})

test('frameFlushCorners: one corner, and the tolerance boundary', () => {
    assert.deepEqual(frameFlushCorners({ x: 101, y: 200, width: 200, height: 199 }, CARD), C(false, false, false, true))
    const at = FLUSH_TOLERANCE_PX
    assert.deepEqual(frameFlushCorners({ x: 100 + at, y: 100 + at, width: 400 - 2 * at, height: 300 - 2 * at }, CARD), C(true, true, true, true))
    const past = FLUSH_TOLERANCE_PX + 1
    assert.deepEqual(frameFlushCorners({ x: 100 + past, y: 100 + past, width: 400 - 2 * past, height: 300 - 2 * past }, CARD), C(false, false, false, false))
})

test('visibleRect: intersection with the clip container, or the frame itself', () => {
    const frame = { x: 101, y: 101, width: 398, height: 600 }
    assert.deepEqual(visibleRect(frame, null), frame)
    assert.deepEqual(visibleRect(frame, { x: 101, y: 101, width: 398, height: 298 }), { x: 101, y: 101, width: 398, height: 298 })
    // Judged on the visible rect, a frame scrolled past the card bottom still gets its bottom corners.
    assert.deepEqual(frameFlushCorners(visibleRect(frame, { x: 101, y: 101, width: 398, height: 298 }), CARD), C(true, true, true, true))
    assert.deepEqual(frameFlushCorners(frame, CARD), C(true, true, false, false))
})

test('frameClipPath: none, clip only, corners only, both', () => {
    const frame = { x: 0, y: 0, width: 100, height: 100 }
    assert.equal(frameClipPath(frame, null, null), null)
    assert.equal(frameClipPath(frame, null, C(false, false, false, false)), null)
    assert.equal(frameClipPath(frame, { x: 0, y: 10, width: 100, height: 80 }, null), 'inset(10px 0px 10px 0px)')
    assert.equal(frameClipPath(frame, null, C(false, false, true, true)),
        `inset(0px 0px 0px 0px round 0 0 ${CARD_CORNER_RADIUS} ${CARD_CORNER_RADIUS})`)
    assert.equal(frameClipPath(frame, { x: 5, y: 0, width: 95, height: 100 }, C(true, false, false, false)),
        `inset(0px 0px 0px 5px round ${CARD_CORNER_RADIUS} 0 0 0)`)
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test src/utils/panelInsets.test.js`
Expected: FAIL — `Cannot find module … panelInsets.js`.

- [ ] **Step 3: Implement `frontend/src/utils/panelInsets.js`**

```js
// panelInsets.js — PURE helpers for the floating-panel cards. No DOM, no Vue.
// Design: docs/plans/2026-09-26-floating-panels-design.md (§6.3 region insets, §6.4 iframe
// corners). The layout resolver returns edge-to-edge rects; each card is inset by half the
// panel gap on the edges that face another region, so neighbours end up one gap apart while
// the outer edges stay flush with the layout box. The inset is emitted as CSS calc() over
// --panel-half-gap so it stays in rem and follows the font-size setting with no measurement.

// The resolver works in fractional px: an edge within this distance of the layout boundary
// is on the boundary.
const EDGE_EPSILON = 0.5

// A pooled iframe corner is "flush" with its card's corner when within this distance on both
// axes: the largest card border (4px, awesome theme) plus rounding.
export const FLUSH_TOLERANCE_PX = 6

// Radius given to a flush iframe corner: the card's padding-box radius, resolved by the
// browser from the inherited tokens (theme, breakpoint and font size apply with no JS).
export const CARD_CORNER_RADIUS = 'var(--panel-inner-radius)'

export const NO_INSETS = Object.freeze({ left: false, top: false, right: false, bottom: false })

/** Edges of `rect` ({x,y,w,h}) that face another region rather than the layout boundary. */
export function innerEdges(rect, viewport) {
    return {
        left: rect.x > EDGE_EPSILON,
        top: rect.y > EDGE_EPSILON,
        right: rect.x + rect.w < viewport.w - EDGE_EPSILON,
        bottom: rect.y + rect.h < viewport.h - EDGE_EPSILON,
    }
}

/** Absolute-position style for `rect`, inset by half the panel gap on its inner `edges`. */
export function insetRectStyle(rect, edges) {
    const l = edges.left ? 1 : 0
    const t = edges.top ? 1 : 0
    const r = edges.right ? 1 : 0
    const b = edges.bottom ? 1 : 0
    const half = 'var(--panel-half-gap)'
    return {
        left: `calc(${rect.x}px + ${half} * ${l})`,
        top: `calc(${rect.y}px + ${half} * ${t})`,
        width: `calc(${rect.w}px - ${half} * ${l + r})`,
        height: `calc(${rect.h}px - ${half} * ${t + b})`,
    }
}

/** The part of a frame rect ({x,y,width,height}) left visible by its clip container. */
export function visibleRect(frameRect, clipRect) {
    if (!clipRect) return frameRect
    const x = Math.max(frameRect.x, clipRect.x)
    const y = Math.max(frameRect.y, clipRect.y)
    const right = Math.min(frameRect.x + frameRect.width, clipRect.x + clipRect.width)
    const bottom = Math.min(frameRect.y + frameRect.height, clipRect.y + clipRect.height)
    return { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) }
}

/** Which corners of the visible frame rect sit in the matching corner of its card. */
export function frameFlushCorners(visible, cardRect) {
    const near = (a, b) => Math.abs(a - b) <= FLUSH_TOLERANCE_PX
    const left = near(visible.x, cardRect.x)
    const top = near(visible.y, cardRect.y)
    const right = near(visible.x + visible.width, cardRect.x + cardRect.width)
    const bottom = near(visible.y + visible.height, cardRect.y + cardRect.height)
    return { tl: top && left, tr: top && right, br: bottom && right, bl: bottom && left }
}

/**
 * clip-path for a pooled frame cell: the clip container's insets (parts scrolled out of the
 * owner's scroll container) plus rounded corners where the frame is flush with its card.
 * Null when nothing needs clipping.
 */
export function frameClipPath(frameRect, clipRect, corners) {
    const { x, y, width, height } = frameRect
    let top = 0, right = 0, bottom = 0, left = 0
    if (clipRect) {
        top = Math.max(0, clipRect.y - y)
        left = Math.max(0, clipRect.x - x)
        right = Math.max(0, x + width - (clipRect.x + clipRect.width))
        bottom = Math.max(0, y + height - (clipRect.y + clipRect.height))
    }
    const rounded = !!corners && (corners.tl || corners.tr || corners.br || corners.bl)
    if (!(top || right || bottom || left) && !rounded) return null
    const box = `${top}px ${right}px ${bottom}px ${left}px`
    if (!rounded) return `inset(${box})`
    const r = (on) => (on ? CARD_CORNER_RADIUS : '0')
    return `inset(${box} round ${r(corners.tl)} ${r(corners.tr)} ${r(corners.br)} ${r(corners.bl)})`
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test src/utils/panelInsets.test.js && node --test src/utils/layoutResolver.test.js`
Expected: PASS for both. If a layout fixture's region id differs (the helper prints the ids it got), fix the **fixture's expectation** against the resolver's actual output — never the resolver.

---

### Task 5: One card per region (commit 2, layout)

**Files:**
- Modify: `frontend/src/views/ProjectView.vue`
- Modify: `frontend/src/views/SessionView.vue`
- Modify: `frontend/src/components/session/layout/SessionLayout.vue`
- Modify: `frontend/src/components/session/layout/DockRegion.vue`
- Modify: `frontend/src/components/session/layout/LayoutOverlay.vue`
- Modify: `frontend/src/components/session/layout/DockGutter.vue`
- Modify: `frontend/src/components/frames/FrameHost.vue` (style only)

**Interfaces:**
- Consumes: `innerEdges`, `insetRectStyle`, `NO_INSETS` (Task 4); tokens and `.panel-card` (Task 2).
- Produces: `DockRegion` and `LayoutOverlay` accept a new prop `insets` (`{left,top,right,bottom}` booleans, default `NO_INSETS`).

- [ ] **Step 1: `ProjectView.vue` — content area transparent, branches as cards**

Template: remove `panel-card` from `<main …class="main-content panel-card"…>` (back to `class="main-content"`); add `panel-card` to the two non-session branches:

```html
<div v-show="!isArtifactsMode && !sessionId" class="project-detail-content panel-card">
…
<div v-show="isArtifactsMode" class="artifacts-browser-content panel-card">
```

Scoped styles: replace the commit-1 `.main-content { … }` block **together with its two-line `Commit 1:` comment** with:

```css
/* Transparent: the session regions, the project detail and the artifacts browser are the
   cards. The outer inset is padding (not margin) so cards' shadows paint into it; clip
   (not hidden: never a scroll container) with a gap-sized margin lets the shadows of cards
   flush with the left edge paint into the divider column. */
.main-content {
    flex: 1;
    min-width: 0;
    height: 100%;
    padding-block: var(--panel-gap);
    padding-inline-end: var(--panel-gap);
    overflow: clip;
    overflow-clip-margin: var(--panel-gap);
    z-index: 1;
    /* Containing block for the absolutely-positioned FrameHost, stable in both
       states (container-type below is dropped while a preview is expanded). */
    position: relative;
    container-type: inline-size;
    container-name: main-content;
}
```

Replace:

```css
.session-content,
.project-detail-content,
.artifacts-browser-content {
    height: 100%;
}
```

with:

```css
.session-content,
.project-detail-content,
.artifacts-browser-content {
    height: 100%;
}
/* Cards: clip (both axes) follows the rounded corners and is not a scroll container. */
.project-detail-content,
.artifacts-browser-content {
    overflow: clip;
}
```

Inside `@media (width < 640px)`: remove the commit-1 `padding: var(--panel-gap);` from `.project-view` **together with its two-line comment** ("The inset comes from padding here…"), and replace the commit-1 `.main-content { margin: 0; height: 100%; }` with:

```css
    .main-content {
        margin: 0;
        height: 100%;
        /* No divider column on mobile: inset on all four sides. */
        padding: var(--panel-gap);
    }
```

- [ ] **Step 2: `SessionView.vue` — clip, header separators, fallback cards**

Template:
- On the ephemeral branch, `<SessionItemsList v-if="isLaunchedEphemeral(session)" …>`, add `class="panel-card"`.
- On the three `<div … class="empty-state">` (not-found, error, loading), change to `class="empty-state panel-card"`.

Scoped styles:

a) Replace the `.session-view { … }` block with:

```css
.session-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    /* clip, not hidden: nothing scrolls this flex column, and a gap-sized clip margin lets the
       outer cards' shadows paint into .main-content's padding (see SessionLayout). */
    overflow: clip;
    overflow-clip-margin: var(--panel-gap);
    position: relative;
}

/* The header sits on the canvas: its separators would draw a hairline just above the cards.
   Invisible but still taking their space (the divider carries the header's bottom spacing). */
.session-view > .session-header :deep(wa-divider) {
    visibility: hidden;
}
@media (max-height: 900px) {
    .session-view > .session-header.compact-collapsed {
        border-bottom-color: transparent;
    }
}
```

b) Replace the `.empty-state { … }` block (the one with `height: 200px`) with:

```css
.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    /* A card filling the session view (no header in these states). */
    flex: 1;
    min-height: 0;
    overflow: clip;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
```

c) Center tab strip insets. In `.layout-nav-cluster { … }` replace `inset-inline-end: 0;` with `inset-inline-end: var(--panel-corner-inset);`. Replace:

```css
.session-tabs::part(nav) {
    margin-inline-end: var(--layout-nav-cluster-w, 0px);
}
```

with:

```css
/* Margins, not padding: with overflowing tabs WA sets its own padding on this element and
   puts the start chevron at its inline start — a margin moves the whole container.
   measureCenterNav() measures the cluster only, so the reservation adds the corner inset. */
.session-tabs::part(nav) {
    margin-inline-start: var(--panel-corner-inset);
    margin-inline-end: calc(var(--layout-nav-cluster-w, 0px) + var(--panel-corner-inset));
}
```

- [ ] **Step 3: `SessionLayout.vue` — center card, insets, clip margin**

Script: add the import next to the others:

```js
import { innerEdges, insetRectStyle } from '../../../utils/panelInsets'
```

Replace the `centerStyle` computed with:

```js
const centerStyle = computed(() => {
    // When the center is maximized it fills the whole area (the resolver's region rect is the viewport).
    const r = (isCenterMaximized.value && maximizedRegion.value) || (docking.value && centerRegion.value)
    if (!r) return {}
    // Floating cards: inset by half a gap on the edges facing another region (panelInsets.js).
    return insetRectStyle(r, innerEdges(r, render.value.viewport))
})
```

Template:
- `<div class="center-slot" :style="centerStyle" v-show="centerVisible">` → `<div class="center-slot panel-card" :style="centerStyle" v-show="centerVisible">`.
- On both `<DockRegion …>` usages add `:insets="innerEdges(maximizedDockRegion, render.viewport)"` (maximized one) and `:insets="innerEdges(r, render.viewport)"` (the `v-for="r in dockRegions"` one).
- On `<LayoutOverlay …>` add `:insets="innerEdges(overlay.rect, render.viewport)"`.

Scoped styles: in `.session-layout { … }`, after `overflow: clip;`, add:

```css
    /* Lets the outer cards' shadows paint into the gap around the layout; only the gap, so the
       gutters' invisible measurement mirrors stay inside .main-content's padding box. */
    overflow-clip-margin: var(--panel-gap);
```

- [ ] **Step 4: `DockRegion.vue` — region card**

Script: add `import { insetRectStyle, NO_INSETS } from '../../../utils/panelInsets'`; add the prop:

```js
    // Inner edges of this region's rect (SessionLayout, panelInsets.innerEdges): the card is
    // inset by half a panel gap on them.
    insets: { type: Object, default: () => NO_INSETS },
```

Replace the `style` computed with:

```js
const style = computed(() => insetRectStyle(props.region, props.insets))
```

Template: `<div class="dock-region" …>` → `<div class="dock-region panel-card" …>`.

Styles: in `.dock-region { … }` remove `background: var(--wa-color-surface-default, transparent);` and the `--dock-border: …;` line (the card draws background and full border). Delete the whole inner-edge border block:

```css
/* Borders sit only on inner edges (…) */
.dock-region.col-left { border-right: var(--dock-border); }
.dock-region.col-right { border-left: var(--dock-border); }
.dock-region.bottom { border-top: var(--dock-border); }
.dock-region[data-rid="left-bottom"],
.dock-region[data-rid="right-bottom"] { border-top: var(--dock-border); }
.dock-region[data-rid="bottom-right"] { border-left: var(--dock-border); }
```

In `.dock-topbar { … }` add:

```css
    /* Keep the first tab and the window buttons clear of the card's rounded corners. */
    padding-inline: var(--panel-corner-inset);
```

- [ ] **Step 5: `LayoutOverlay.vue` — overlay card, backdrop corners**

Script: add `import { insetRectStyle, NO_INSETS } from '../../../utils/panelInsets'`; add the prop `insets: { type: Object, default: () => NO_INSETS },` (same comment as DockRegion); replace the `style` computed with:

```js
const style = computed(() => insetRectStyle(props.overlay.rect, props.insets))
```

Template: `<div class="layout-overlay" …>` → `<div class="layout-overlay panel-card" …>`.

Styles:
- `.overlay-backdrop { … }` add `border-radius: var(--panel-radius); /* follow the outer cards' corners */`.
- In `.layout-overlay { … }` remove `background: …;` and `--overlay-border: …;`; keep `box-shadow: var(--wa-shadow-l, …)` (it overrides the card's shadow: the overlay floats higher).
- Delete the inner-edge border block (`/* The overlay peeks from one edge; … */` and the three `.layout-overlay.left/.right/.bottom` rules).
- In `.overlay-topbar { … }` add `padding-inline: var(--panel-corner-inset);`.

- [ ] **Step 6: `DockGutter.vue` — rails on the canvas**

In `.dock-gutter { … }` replace `background: var(--wa-color-surface-default, transparent); /* match .dock-region */` with `background: transparent; /* on the canvas, like the sidebar */` and remove the `--gutter-border: …;` line. Remove `border-right: var(--gutter-border);` from `.dock-gutter.left`, `border-left: var(--gutter-border);` from `.dock-gutter.right`, `border-top: var(--gutter-border);` from `.dock-gutter.bottom`, and update the comment above them to `/* On the canvas: no border (the gap separates the rail from the cards). */`.

- [ ] **Step 7: `FrameHost.vue` — clip the host**

In `.frame-host { … }` add:

```css
    /* A cell is sized to its placeholder and trimmed only by clip-path; in a Browser pane's
       responsive mode it can extend far past the viewport. .main-content now clips with a
       margin, so the host clips at .main-content's padding box itself. Fixed (fullscreen)
       cells escape: this is not their containing block. */
    overflow: clip;
```

- [ ] **Step 8: Run the unit suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test`
Expected: all pass.

- [ ] **Step 9: Manual check (spec §8, layout part)**

Worktree instance, light/dark, two accents, three themes, font sizes 12 / 14 / 18:
- session with left, right-top, right-bottom and bottom docks: each is a separate card, one gap apart; header on the canvas, no hairline above the cards (normal and `max-height: 900px` compact modes);
- maximized dock, maximized center, tabs mode (narrow window): one card;
- rails on each edge (minimize a dock): transparent, on the canvas; open a side overlay while a rail shows: overlay is a card, inset from the rail; backdrop rounded, rail dimmed with the rest, its chips clickable;
- resize every splitter: the accent line shows in the gap; tab drag between docks still works;
- top-corner controls (first tab, nav cluster, dock window buttons): hover fill and focus ring not cut; center tabs overflowing: scroll chevrons do not cover a tab;
- shadows: whole on every side at font size 12, including cards flush with the sidebar side; nothing visibly spilling onto a neighbour card;
- terminal docked: background matches its card;
- fullscreen file preview from a docked pane and from an overlay: covers the whole window;
- Browser pane in responsive mode with a stage larger than the pane, in a right and in a bottom dock: no page scrollbar, also while another preview is fullscreen;
- Firefox: Artifacts tab in a side overlay with its file list open and a file selected, close, reopen, resize the window: no sideways shift;
- ephemeral session, session "not found", loading: one card filling the view;
- project detail and artifacts browser: one card each;
- narrow viewport (< 640px): page does not scroll, cards inset on all four sides, shadows not visibly cut.

---

### Task 6: Rounded corners for pooled iframes (commit 2, frames)

**Files:**
- Modify: `frontend/src/stores/framePool.js`
- Modify: `frontend/src/components/frames/PersistentFrame.vue`
- Modify: `frontend/src/components/frames/FrameHost.vue`

**Interfaces:**
- Consumes: `visibleRect`, `frameFlushCorners`, `frameClipPath` (Task 4).
- Produces: pool descriptor field `cardRect` (`{x,y,width,height}|null`, viewport coordinates of the `.panel-card` containing the placeholder).

- [ ] **Step 1: `framePool.js` — the new field**

In the descriptor comment, after the `clipRect` entry, add:

```js
        //   cardRect ({x,y,width,height}|null) — viewport rect of the .panel-card
        //     containing the placeholder; FrameHost rounds the frame corners that are
        //     flush with it (utils/panelInsets.js),
```

In `register()`, after `clipRect: null,` add `cardRect: null,`.

- [ ] **Step 2: `PersistentFrame.vue` — resolve and measure the card**

Change the vue import to include `shallowRef`:

```js
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, shallowRef, watch } from 'vue'
```

After `const clipBounding = useElementBounding(() => props.clipEl)`, add:

```js
// The floating card (.panel-card) containing the placeholder, so FrameHost can round the
// frame corners that sit in the card's corners. Re-resolved, never cached for the component's
// life: a pane moved to another dock keeps this instance (Teleport) but lands in another card.
const cardEl = shallowRef(null)
const cardBounding = useElementBounding(cardEl)
function resolveCard() {
    const el = placeholderEl.value?.closest('.panel-card') || null
    if (el !== cardEl.value) cardEl.value = el
    cardBounding.update()
}
```

In `onActivated(() => { … })`, after `clipBounding.update()`, add `resolveCard()`.

Inside the `if (pooled) { … }` block:

a) Replace the placeholder-rect watcher:

```js
    watch(
        [bounding.x, bounding.y, bounding.width, bounding.height],
        ([x, y, width, height]) => pool.setRect(props.frameId, { x, y, width, height })
    )
```

with:

```js
    watch(
        [bounding.x, bounding.y, bounding.width, bounding.height],
        ([x, y, width, height]) => {
            pool.setRect(props.frameId, { x, y, width, height })
            // The reliable trigger for a card change: on a KeepAlive return the panel is
            // Teleported into the recreated dock only after onActivated, and the geometry
            // epoch may fire first — but the placeholder rect always changes when it lands.
            resolveCard()
        }
    )
    watch(
        [cardEl, cardBounding.x, cardBounding.y, cardBounding.width, cardBounding.height],
        ([el, x, y, width, height]) => {
            pool.patch(props.frameId, {
                cardRect: el && width > 0.5 && height > 0.5 ? { x, y, width, height } : null,
            })
        }
    )
```

b) In the `geometryEpoch` watcher, after `clipBounding.update()`, add `resolveCard()`.

In `onMounted(() => { … })`, after `bounding.update()`, add `resolveCard()`.

- [ ] **Step 3: `FrameHost.vue` — rounded clip-path**

Script: add `import { frameClipPath, frameFlushCorners, visibleRect } from '../../utils/panelInsets'`. In `cellStyle(frame)`, replace the whole clip block (from the comment `// Clip away the parts that scrolled out …` through the closing `}` of `if (clip) { … }`) with:

```js
    // Clip away the parts that scrolled out of the owner's clip container
    // (Browser pane responsive mode): the frame paints above pane content,
    // so an unclipped overhang would cover the pane's own chrome. Both rects
    // are viewport-based, so the deltas hold for either positioning branch —
    // clip-path resolves against the element's own border box.
    // Plus rounded corners where the visible frame sits in its card's corners
    // (floating panels): the card's radius does not clip pooled frames, which
    // are not its DOM descendants. Never for fullscreen frames.
    const corners = frame.zTier !== 'fullscreen' && frame.cardRect
        ? frameFlushCorners(visibleRect(frame.rect, frame.clipRect), frame.cardRect)
        : null
    const clipPath = frameClipPath(frame.rect, frame.clipRect, corners)
    if (clipPath) style.clipPath = clipPath
```

- [ ] **Step 4: Run the unit suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test`
Expected: all pass.

- [ ] **Step 5: Manual check (spec §8, frames part)**

Worktree instance, default and awesome themes:
- Browser pane and HTML artifact preview docked in a corner region (e.g. right-bottom): the frame's bottom corners are rounded with the card;
- move that tab to a non-corner region and back: corners follow;
- switch to another session and back (KeepAlive return): corners still right;
- open it in a side overlay: overlay corners followed;
- Browser responsive mode, scroll the stage: the visible part's corners are judged correctly (bottom corners rounded when the visible part reaches the card bottom);
- HTML artifact preview in the artifacts browser and in the project detail Files tab: corners follow the card;
- fullscreen preview: square, covers the whole window.

---

### Task 7: Final verification and hand-over of commit 2

**Files:** none new.

- [ ] **Step 1: Full unit suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test`
Expected: all pass.

- [ ] **Step 2: Invariant scan**

Run (the diff covers modified files; the second grep covers the two new, untracked files):

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && P='(transform|filter:|backdrop-filter|contain:|will-change|container-type)' && hits=$({ git diff -U0 -- frontend/src | grep -nE "^\+.*$P"; grep -nE "$P" frontend/src/styles/surfaces.css frontend/src/utils/panelInsets.js; }) ; [ -z "$hits" ] && echo "no forbidden property added" || printf '%s\n' "$hits"
```

Expected: `no forbidden property added` (spec §7). Any hit must be justified against the spec or removed. (Whether commit 1 was already committed or not, the direct grep covers `surfaces.css`; if commit 1 is not committed yet, the diff also shows its changes, which must pass the same scan.)

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git diff --stat -- frontend/src/utils/layoutResolver.js`
Expected: empty (the resolver is untouched).

- [ ] **Step 3: Hand over commit 2 to the user**

Tell the user commit 2 is ready to look at (URL, the Task 5 and Task 6 checklists passed). Wait for their go. On "commit":

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git add frontend/src/utils/panelInsets.js frontend/src/utils/panelInsets.test.js frontend/src/views/ProjectView.vue frontend/src/views/SessionView.vue frontend/src/components/session/layout/SessionLayout.vue frontend/src/components/session/layout/DockRegion.vue frontend/src/components/session/layout/LayoutOverlay.vue frontend/src/components/session/layout/DockGutter.vue frontend/src/components/frames/FrameHost.vue frontend/src/components/frames/PersistentFrame.vue frontend/src/stores/framePool.js
git commit -m "feat(ui): one floating card per session layout region" -m "Each session layout region (center, every dock, the overlay) becomes its own card, one gap apart, with the session header and the dock rails on the canvas. The layout resolver is untouched: a pure helper (utils/panelInsets.js) insets each resolver rect by half a gap on the edges facing another region, as CSS calc() so the gap follows the font size. The project detail, the artifacts browser and the session fallback views are cards too. Clipping moves from hidden to clip with gap-sized clip margins so shadows are not cut and nothing becomes scrollable; tab bars keep their corner controls clear of the radius; pooled iframes get rounded corners where they sit in a card's corner. Second of two commits of docs/plans/2026-09-26-floating-panels-design.md." -m "Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>"
```

- [ ] **Step 4: Remind the user**

Remind: the worktree's dev servers are still running (`uv run ./devctl.py stop all` + `kill-tmux` when the worktree is removed). Propose (do not write) a CHANGELOG `[Unreleased]` entry.
