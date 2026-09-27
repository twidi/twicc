# Depth — Layered Soft Shadows and Typography — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (native execution, the user's choice per roadmap §5: the main session implements, one fresh whole-branch reviewer at the end) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace TwiCC's hard 2px shadow lines and small offset shadows with three levels of soft, layered shadows (plus a faint accent tint on user messages, raised secondary buttons, recessed fields), then tighten the typography — visual refresh step 2, in two commits.

**Architecture:** One new global stylesheet (`styles/depth.css`) holds the depth tokens, maps Web Awesome's `--wa-shadow-s/m/l` onto them, and carries the few global control rules; it is imported by the SPA, the share viewer and the artifact shell. Component files switch their ad-hoc shadows to the tokens. A `node:test` file pins the token invariants (valid lists, dark layer order, shadow reach, imports) because CSS has no other test harness here. Typography (commit 2) is a handful of scoped/global rules.

**Tech Stack:** Vue 3 SFC (scoped and global `<style>`), Web Awesome 3 (`::part()`, theme tokens in `@layer wa-theme`), Notivue theme variables, `node:test` + `node:fs` for the CSS invariant tests, Chrome MCP for browser checks.

**Spec:** `docs/plans/2026-09-26-depth-design.md` — read it first; every task argues from it (§ numbers below refer to it). Background: `docs/plans/2026-09-26-visual-refresh-roadmap.md`.

## Global Constraints

- All written artifacts (code, comments, names, docs) in English.
- Sizes that must follow the font size are in `rem`; px only for 1px hairlines and shadow geometry already in px in the spec.
- Never add `transform`, `filter`, `backdrop-filter`, `contain`, `will-change` or `container-type` to `.main-content`'s branches, `.session-layout`, `.center-slot`, `.dock-region`, `.layout-overlay` (spec §11).
- `.main-content` stays opaque. `--panel-shadow` unchanged; every panel shadow keeps a horizontal reach ≤ 4px (spec §3, §5.1).
- Chat spacing unchanged except the last-row reserve of §6.2.
- Every token value is a valid `box-shadow` list; never put `none` inside a list (spec §4).
- Global element rules in `depth.css` are wrapped in `:where(...)`; the awesome theme's controls are excluded (spec §4.2, §7).
- Existing `transition`s are not modified (spec §3).
- Frontend commands run from `frontend/` in the worktree. Prefix every shell command with `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && `. Never run `npm install`/`npm ci` or `migrate` by hand. Never restart servers other than this worktree's (`uv run ./devctl.py …` from the worktree only).
- Untracked files (this plan, the spec, new files) are edited with the Edit/Write tools, never by scripts.
- Commits: only when the user explicitly says "commit", after reviewing the result in the browser. Conventional Commit subject, descriptive body, `Co-Authored-By` trailer with the running model's exact name (the commands below use `Claude Opus 5.5 (1M context)`; replace it if another model commits). Stage files by explicit path. No CHANGELOG entry.
- No merge, no push, no PR (roadmap §1).
- Task 7 step 5 and Task 9 step 3 are deliberate **user gates**: stop there and wait for the user, whatever superpowers:executing-plans says about continuous execution. The skill's final whole-branch review runs at Task 9 step 4b (range `655381c2..HEAD`), before the roadmap records the step.

## Review Focus

1. **Awesome theme controls** (buttons, fields, composer) keep their hard look while menus/cards/messages turn soft — Task 5 step 5 and Task 7 check 9.
2. **Last chat card at the bottom of the list** keeps its whole shadow in the shoelace theme and at font sizes 12/24/32 — pinned by the `--depth-card-reach` test (Task 2) and Task 7 check 10.
3. **Joined tool-card runs** (same row and across rows) cast one shadow — Task 4 step 6 probe and Task 7 checks 2 and 14.
4. **`.wa-invert` contexts** (toasts): WA tokens resolve to the page scheme, toast buttons stay flat — Task 3 step 6 and Task 7 checks 3 and 5.
5. **Dark hover transition** on home cards fades instead of snapping — pinned by the dark layer-order test (Task 2) and Task 7 check 8.

---

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `frontend/src/styles/depth.css` | Create (T2, T5) | Depth tokens, WA shadow mapping, global button/field rules |
| `frontend/src/styles/depth.test.js` | Create (T2, T3) | CSS invariants: valid lists, dark layer order, reach, budget, imports |
| `frontend/src/main.js` | Modify (T2) | Import `depth.css` |
| `frontend/src/share-session/main.js` | Modify (T2) | Import `depth.css` |
| `frontend/src/artifact-shell/main.js` | Modify (T2) | Import `depth.css` |
| `frontend/src/styles/surfaces.css` | Modify (T3) | `--panel-overlay-shadow` |
| `frontend/src/components/session/layout/LayoutOverlay.vue` | Modify (T3) | Overlay shadow token |
| `frontend/src/App.vue` | Modify (T3) | Toast `--nv-shadow` |
| `frontend/src/components/app/UsageGraphDialog.vue` | Modify (T3) | Chart tooltip level 2 |
| `frontend/src/components/activity/ContributionSparklines.vue` | Modify (T3) | Sparkline tooltip level 2 |
| `frontend/src/components/session/detail/SessionHeader.vue` | Modify (T3, T8) | Overflow panel shadow; title typography |
| `frontend/src/components/project/ProjectDetailHeader.vue` | Modify (T3, T8) | Overflow panel shadow; title typography |
| `frontend/src/share-session/ShareSessionApp.vue` | Modify (T3) | Header level 2 |
| `frontend/src/styles/transcript-tokens.css` | Modify (T4, T8) | User card tint; tabular digits |
| `frontend/src/components/session/detail/SessionItem.vue` | Modify (T4) | Chat card and tool card shadows, last-row reserve |
| `frontend/src/components/message/MessageInput.vue` | Modify (T5) | Composer level 2 |
| `frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue` | Modify (T5) | Option cards level 1 |
| `frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue` | Modify (T5) | Option cards level 1 |
| `frontend/src/components/project/ProjectCard.vue` | Modify (T6) | Hover level 2 |
| `frontend/src/components/workspace/WorkspaceCard.vue` | Modify (T6) | Hover level 2 |
| `frontend/src/components/activity/ActivityDashboard.vue` | Modify (T6) | Pill badges, card hover |
| `frontend/src/components/ui/MarkdownContent.vue` | Modify (T8) | Proportional digits + `text-wrap: pretty` in prose |
| `frontend/src/views/HomeView.vue` | Modify (T8) | Title letter-spacing |
| `frontend/src/components/sidebar/SidebarListSeparator.vue` | Modify (T8) | Uppercase section labels |
| `docs/plans/2026-09-26-visual-refresh-roadmap.md` | Modify (T9) | Step 2 status and summary |

Commit 1 = Tasks 2–7 (+ the spec and this plan). Commit 2 = Task 8 (verified in Task 9). A third, docs-only commit records step 2 in the roadmap (Task 9 steps 5–6), since a commit cannot contain its own hash.

---

### Task 1: Baseline on the worktree instance

No code. Captures what "unchanged" is compared against (spec §12 preamble).

**Files:** none (numbers go to the ledger).

- [ ] **Step 1: Confirm the worktree dev instance runs**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && uv run ./devctl.py status`
Expected: `Frontend (Vite): running … on port 5174` and `Backend (Django): running … on port 3501`. If not running: `uv run ./devctl.py start` (same prefix) and read its output.

- [ ] **Step 2: Pick a baseline session**

In Chrome MCP (new tab), open http://localhost:5174, open a session of this project that has: a user message followed by an assistant answer with several consecutive tool calls (a joined run), and scroll the chat to the bottom. It must be a session that is **not running** (not the executing session, no live process), so its last rows do not change between this baseline and Task 4. Note its URL in the ledger.

- [ ] **Step 3: Measure the chat gaps**

Run in the page (Chrome MCP `javascript_tool`):

```js
(() => {
  const rows = [...document.querySelectorAll('.session-items .virtual-scroller-item')].slice(-12)
  return rows.map(r => {
    const s = r.querySelector('.session-item, .group-toggle')
    if (!s) return null
    const cs = getComputedStyle(s)
    return { kind: s.dataset.kind, marginTop: cs.marginTop, marginBottom: cs.marginBottom,
             height: Math.round(r.getBoundingClientRect().height * 100) / 100 }
  }).filter(Boolean)
})()
```

Expected: a JSON list. Save it to the ledger as `Baseline gaps (light, default theme, 16px)`.

Then, same page, record the values later steps compare against:

```js
(() => {
  const part = (el) => el && getComputedStyle(el.shadowRoot.querySelector('[part~="base"]')).boxShadow
  const user = document.querySelector('.session-items .session-item[data-kind="user_message"]')
  return {
    userBg: user && getComputedStyle(user).backgroundColor,
    activeSessionRow: part(document.querySelector('wa-button.session-item[appearance="outlined"]')),
    accentButton: part(document.querySelector('wa-button[appearance="accent"]')),
  }
})()
```

Save it to the ledger as `Baseline values (light)`; run it again in dark and save as `Baseline values (dark)`.

- [ ] **Step 4: Screenshots**

Take light and dark screenshots (Chrome MCP `computer` screenshot) of: the chat bottom, the home page, a project's stats tab, a dialog with an outlined button (e.g. project edit). The images stay in the session transcript; note in the ledger which screenshot shows what, so later checks can refer to them.

---

### Task 2: Depth tokens, Web Awesome mapping and imports (TDD)

**Files:**
- Create: `frontend/src/styles/depth.test.js`
- Create: `frontend/src/styles/depth.css`
- Modify: `frontend/src/main.js:13-15`
- Modify: `frontend/src/share-session/main.js:29`
- Modify: `frontend/src/artifact-shell/main.js:12`

**Interfaces:**
- Produces: CSS custom properties `--depth-1`, `--depth-2`, `--depth-3`, `--depth-card`, `--depth-card-reach`, `--depth-highlight`, `--depth-edge`, `--depth-inset` (on `:root`, dark overrides on `.wa-dark`); `--wa-shadow-s/m/l` mapped on `:root, .wa-invert`. Test helpers `parseTopLevelBlocks`, `declarations`, `shadowLayers`, `reach` (local to the test file; Task 3 adds tests to the same file).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/styles/depth.test.js`:

```js
// CSS invariants of the depth tokens (visual refresh step 2,
// docs/plans/2026-09-26-depth-design.md). CSS has no other test harness here:
// these tests read the stylesheets as text and check what the design relies on.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const read = (relative) => readFileSync(join(here, relative), 'utf8')

/** Top-level rules (at-rules skipped) as { selector, body }, comments stripped,
 *  selector whitespace collapsed. */
function parseTopLevelBlocks(css) {
    const text = css.replace(/\/\*[\s\S]*?\*\//g, '')
    const blocks = []
    let i = 0
    while (i < text.length) {
        const open = text.indexOf('{', i)
        if (open === -1) break
        const selector = text.slice(i, open).trim().replace(/\s+/g, ' ')
        let depth = 1
        let j = open + 1
        while (j < text.length && depth > 0) {
            if (text[j] === '{') depth++
            else if (text[j] === '}') depth--
            j++
        }
        if (!selector.startsWith('@')) blocks.push({ selector, body: text.slice(open + 1, j - 1) })
        i = j
    }
    return blocks
}

/** Custom-property declarations of the first top-level block with this exact selector. */
function declarations(css, selector) {
    const block = parseTopLevelBlocks(css).find((b) => b.selector === selector)
    assert.ok(block, `no top-level block "${selector}"`)
    const result = {}
    for (const part of block.body.split(';')) {
        const match = part.match(/^\s*(--[\w-]+)\s*:\s*([\s\S]+?)\s*$/)
        if (match) result[match[1]] = match[2].replace(/\s+/g, ' ')
    }
    return result
}

const REM = 16 // browser default root size; reach checks are relative anyway
function toPx(length) {
    if (length.endsWith('rem')) return parseFloat(length) * REM
    return parseFloat(length) // px or unitless 0
}

/** A box-shadow list as layers { inset, x, y, blur, spread, lengthCount, color }. The
 *  colors used here (oklch(... / a), transparent) contain no comma, so a plain split is
 *  safe. */
function shadowLayers(value) {
    return value.split(',').map((raw) => {
        const layer = raw.trim()
        const inset = /^inset\s/.test(layer)
        const tokens = layer.replace(/^inset\s+/, '').split(/\s+/)
        const lengths = []
        let i = 0
        while (i < tokens.length && /^-?[\d.]+(px|rem)?$/.test(tokens[i])) lengths.push(toPx(tokens[i++]))
        const [x = 0, y = 0, blur = 0, spread = 0] = lengths
        return { inset, x, y, blur, spread, lengthCount: lengths.length, color: tokens.slice(i).join(' ') }
    })
}

/** How far the outer layers of a shadow reach beyond the box, in px at 16px root. */
function reach(value) {
    const outer = shadowLayers(value).filter((l) => !l.inset)
    const max = (fn) => Math.max(0, ...outer.map(fn))
    return {
        below: max((l) => l.y + l.spread + l.blur),
        above: max((l) => -l.y + l.spread + l.blur),
        side: max((l) => Math.abs(l.x) + l.spread + l.blur),
    }
}

const depthCss = read('depth.css')
const lightTokens = declarations(depthCss, ':root')
const darkTokens = declarations(depthCss, '.wa-dark')
const SHADOW_TOKENS = ['--depth-1', '--depth-2', '--depth-3', '--depth-card', '--depth-highlight', '--depth-edge', '--depth-inset']

test('every depth token is a non-empty box-shadow list without none', () => {
    for (const [scheme, tokens] of [['light', lightTokens], ['dark', darkTokens]]) {
        for (const name of SHADOW_TOKENS) {
            const value = tokens[name]
            assert.ok(value, `${scheme} ${name} missing`)
            assert.doesNotMatch(value, /\bnone\b/, `${scheme} ${name} contains none`)
            for (const layer of shadowLayers(value)) {
                assert.ok(layer.lengthCount >= 2 && layer.lengthCount <= 4,
                    `${scheme} ${name}: a layer needs 2 to 4 lengths`)
                assert.match(layer.color, /^(oklch\([^)]*\)|transparent)$/,
                    `${scheme} ${name}: a layer must end with one color`)
            }
        }
    }
})

test('dark levels 1-3 put their inset layer first, outer layers after (interpolable hover)', () => {
    for (const name of ['--depth-1', '--depth-2', '--depth-3']) {
        const [first, ...rest] = shadowLayers(darkTokens[name])
        assert.equal(first.inset, true, `dark ${name}: first layer must be inset`)
        assert.ok(rest.every((l) => !l.inset), `dark ${name}: only the first layer may be inset`)
        assert.ok(shadowLayers(lightTokens[name]).every((l) => !l.inset), `light ${name}: no inset layer`)
    }
})

test('--depth-card reaches exactly --depth-card-reach below, nothing above, ≤ 1px sideways', () => {
    const declaredReach = toPx(lightTokens['--depth-card-reach'])
    assert.equal(declaredReach, 3)
    for (const [scheme, tokens] of [['light', lightTokens], ['dark', darkTokens]]) {
        const r = reach(tokens['--depth-card'])
        assert.equal(r.below, declaredReach, `${scheme} --depth-card below`)
        assert.equal(r.above, 0, `${scheme} --depth-card above`)
        assert.ok(r.side <= 1, `${scheme} --depth-card side ${r.side}`)
    }
})

test('Web Awesome shadow tokens are mapped on :root and .wa-invert', () => {
    const mapping = declarations(depthCss, ':root, .wa-invert')
    assert.equal(mapping['--wa-shadow-s'], 'var(--depth-1)')
    assert.equal(mapping['--wa-shadow-m'], 'var(--depth-3)')
    assert.equal(mapping['--wa-shadow-l'], 'var(--depth-3)')
})

test('depth.css is imported by the SPA, the share viewer and the artifact shell', () => {
    const spa = read('../main.js')
    const tokensAt = spa.indexOf("import './styles/transcript-tokens.css'")
    const depthAt = spa.indexOf("import './styles/depth.css'")
    const surfacesAt = spa.indexOf("import './styles/surfaces.css'")
    assert.ok(tokensAt >= 0 && depthAt > tokensAt && surfacesAt > depthAt, 'SPA import order')
    assert.match(read('../share-session/main.js'), /import '\.\.\/styles\/depth\.css'/)
    assert.match(read('../artifact-shell/main.js'), /import '\.\.\/styles\/depth\.css'/)
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test src/styles/depth.test.js`
Expected: FAIL — `ENOENT: no such file or directory, open '…/src/styles/depth.css'` (module-level read).

- [ ] **Step 3: Create `frontend/src/styles/depth.css`**

```css
/* Depth — layered soft shadows, visual refresh step 2.
   Design: docs/plans/2026-09-26-depth-design.md.
   Shared by the SPA (main.js), the share viewer (share-session/main.js) and the artifact
   shell (artifact-shell/main.js): nothing here may depend on another app stylesheet.

   Declaration order matters (both blocks match <html>): light tokens on :root, dark
   overrides on .wa-dark (same specificity, later wins). Unlayered, so they beat the
   theme's @layer wa-theme declarations. Every token is a valid box-shadow list (never
   `none`), so tokens combine with commas. */

:root {
    /* Level 1: resting on a card (buttons, stat/home cards, question options). */
    --depth-1:
        0 1px 1px oklch(0.25 0.02 275 / 0.04),
        0 1px 3px oklch(0.25 0.02 275 / 0.07);
    /* Level 2: stands out (message composer, hovered home card). */
    --depth-2:
        0 1px 2px oklch(0.25 0.02 275 / 0.05),
        0 4px 12px -2px oklch(0.25 0.02 275 / 0.08),
        0 16px 32px -12px oklch(0.25 0.02 275 / 0.12);
    /* Level 3: floats above the page (menus, dialogs, popovers, toasts, pickers). */
    --depth-3:
        0 2px 4px oklch(0.2 0.02 275 / 0.06),
        0 12px 28px -4px oklch(0.2 0.02 275 / 0.16),
        0 32px 64px -16px oklch(0.2 0.02 275 / 0.24);
    /* Downward level 1 for chat cards and tool cards: nothing above, a negligible
       0.0625rem on the sides (its faintest edge), 0.1875rem below (--depth-card-reach). */
    --depth-card:
        0 0.0625rem 0.125rem -0.0625rem oklch(0.25 0.02 275 / 0.10),
        0 0.125rem 0.1875rem -0.125rem oklch(0.25 0.02 275 / 0.12);
    /* How far --depth-card reaches below its box. Keep in sync with --depth-card (both
       schemes use the same geometry). */
    --depth-card-reach: 0.1875rem;
    /* Light top edge of a raised button. Dark: a fainter one, added to the edge line
       --depth-1 already carries there. */
    --depth-highlight: inset 0 1px 0 oklch(1 0 0 / 0.6);
    /* Hair-thin light top line on raised things in dark mode; nothing in light. */
    --depth-edge: 0 0 transparent;
    /* Recessed text field. */
    --depth-inset: inset 0 1px 2px oklch(0.25 0.02 275 / 0.06);
}

.wa-dark {
    /* Inset layer first: keeps level 1 → level 2 hover transitions interpolable (a
       box-shadow transition pairs layers by position and snaps on an inset/outer pair). */
    --depth-1:
        inset 0 1px 0 oklch(1 0 0 / 0.04),
        0 1px 2px oklch(0 0 0 / 0.35);
    --depth-2:
        inset 0 1px 0 oklch(1 0 0 / 0.05),
        0 1px 2px oklch(0 0 0 / 0.4),
        0 8px 24px -6px oklch(0 0 0 / 0.5);
    --depth-3:
        inset 0 1px 0 oklch(1 0 0 / 0.06),
        0 2px 6px oklch(0 0 0 / 0.45),
        0 24px 56px -12px oklch(0 0 0 / 0.65);
    --depth-card:
        0 0.0625rem 0.125rem -0.0625rem oklch(0 0 0 / 0.45),
        0 0.125rem 0.1875rem -0.125rem oklch(0 0 0 / 0.35);
    --depth-highlight: inset 0 1px 0 oklch(1 0 0 / 0.06);
    --depth-edge: inset 0 1px 0 oklch(1 0 0 / 0.04);
    --depth-inset: inset 0 1px 2px oklch(0 0 0 / 0.3);
}

/* Web Awesome's own shadows follow the levels: wa-card rests at level 1; every consumer
   of m (dropdown, submenu, select listbox, color picker) and of l (dialog, drawer,
   popover) is a floating layer, so both map to level 3. .wa-invert is listed because
   every theme re-declares --wa-shadow-* there (toasts, tooltips); var(--depth-*) then
   resolves to the page scheme, inherited from <html> — right for a shadow cast on the
   page. Placed after the .wa-dark block. */
:root,
.wa-invert {
    --wa-shadow-s: var(--depth-1);
    --wa-shadow-m: var(--depth-3);
    --wa-shadow-l: var(--depth-3);
}
```

- [ ] **Step 4: Add the imports**

`frontend/src/main.js` — replace:

```js
import './styles/transcript-tokens.css'
// Canvas + floating-panel tokens (SPA only — the share bundle does not import it).
import './styles/surfaces.css'
```

with:

```js
import './styles/transcript-tokens.css'
// Depth tokens (also imported by the share bundle and the artifact shell).
import './styles/depth.css'
// Canvas + floating-panel tokens (SPA only — the share bundle does not import it).
import './styles/surfaces.css'
```

`frontend/src/share-session/main.js` — after `import '../styles/transcript-tokens.css'` add:

```js
import '../styles/depth.css'
```

`frontend/src/artifact-shell/main.js` — after `import '@awesome.me/webawesome/dist/styles/themes/default.css'` add:

```js
// Depth tokens: the consent prompt dialog looks the same here as in the SPA preview.
import '../styles/depth.css'
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test src/styles/depth.test.js`
Expected: PASS, 5 tests.

- [ ] **Step 6: Run the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t2.log 2>&1; tail -8 /tmp/depth-t2.log`
Expected: `# fail 0`, `# pass` = previous count (449) + 5.

- [ ] **Step 7: Quick browser probe**

On http://localhost:5174 (HMR applies), run in the page:

```js
(() => { const cs = getComputedStyle(document.documentElement);
  return ['--depth-1','--wa-shadow-s','--wa-shadow-l'].map(n => [n, cs.getPropertyValue(n).trim()]) })()
```

Expected: `--wa-shadow-s` equals `--depth-1`'s value; `--wa-shadow-l` equals `--depth-3`'s (dark values when the page is dark).

---

### Task 3: Floating layers and the layout overlay

**Files:**
- Modify: `frontend/src/styles/depth.test.js` (append)
- Modify: `frontend/src/styles/surfaces.css` (`:root` and `.wa-dark` blocks)
- Modify: `frontend/src/components/session/layout/LayoutOverlay.vue:89-92`
- Modify: `frontend/src/App.vue` (`toastTheme` computed, ~line 767)
- Modify: `frontend/src/components/app/UsageGraphDialog.vue:1429`
- Modify: `frontend/src/components/activity/ContributionSparklines.vue:737`
- Modify: `frontend/src/components/session/detail/SessionHeader.vue:1437`
- Modify: `frontend/src/components/project/ProjectDetailHeader.vue:528`
- Modify: `frontend/src/share-session/ShareSessionApp.vue:242`

**Interfaces:**
- Consumes: `--depth-2`, `--depth-3` (Task 2); test helpers `declarations`, `reach`, `read` (Task 2).
- Produces: `--panel-overlay-shadow` (SPA only, `surfaces.css`).

- [ ] **Step 1: Write the failing test** — append to `frontend/src/styles/depth.test.js`:

```js
test('panel shadows keep the step-1 horizontal budget (≤ 4px)', () => {
    const surfacesCss = read('surfaces.css')
    for (const selector of [':root', '.wa-dark']) {
        const tokens = declarations(surfacesCss, selector)
        for (const name of ['--panel-shadow', '--panel-overlay-shadow']) {
            assert.ok(tokens[name], `${selector} ${name} missing`)
            const r = reach(tokens[name])
            assert.ok(r.side <= 4, `${selector} ${name} side reach ${r.side}px`)
        }
    }
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test src/styles/depth.test.js`
Expected: FAIL — `:root --panel-overlay-shadow missing` (the dark `--panel-shadow` also exists already; only the new token is missing).

- [ ] **Step 3: Add `--panel-overlay-shadow` to `surfaces.css`**

In the `:root` block, right after the `--panel-shadow` declaration, add:

```css
    /* The layout overlay (and the headers' overflow panels) float above the other cards:
       deeper than --panel-shadow, same horizontal budget (≤ 4px per layer: blur + spread). */
    --panel-overlay-shadow:
        0 2px 4px oklch(0.2 0.02 275 / 0.08),
        0 14px 18px -14px oklch(0.2 0.02 275 / 0.28);
```

In the `.wa-dark` block, right after its `--panel-shadow` declaration, add:

```css
    --panel-overlay-shadow:
        0 2px 4px oklch(0 0 0 / 0.45),
        0 14px 18px -14px oklch(0 0 0 / 0.7);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && node --test src/styles/depth.test.js`
Expected: PASS, 6 tests.

- [ ] **Step 5: Point the consumers at the tokens**

`LayoutOverlay.vue` — replace:

```css
    /* Background, full border and radius come from .panel-card; the stronger shadow overrides
       the card's: the overlay floats above the other cards. */
    box-shadow: var(--wa-shadow-l, 0 10px 40px rgba(0, 0, 0, 0.35));
```

with:

```css
    /* Background, full border and radius come from .panel-card; the stronger shadow overrides
       the card's: the overlay floats above the other cards. Not --wa-shadow-l (level 3): its
       48px side reach would be cut flat by the layout's clip in the gap. */
    box-shadow: var(--panel-overlay-shadow);
```

`App.vue` — in `toastTheme`, replace:

```js
    return {
        ...(isDark ? lightTheme : slateTheme),
        '--nv-width': '100%',
        '--nv-min-width': '30rem',
    }
```

with:

```js
    return {
        ...(isDark ? lightTheme : slateTheme),
        '--nv-width': '100%',
        '--nv-min-width': '30rem',
        // Level 3, cast on the page: resolves with the page scheme even inside the
        // .wa-invert box (slateTheme has no shadow of its own, lightTheme a faint one).
        '--nv-shadow': 'var(--depth-3)',
    }
```

`UsageGraphDialog.vue` (`.usage-chart-tooltip`) and `ContributionSparklines.vue` (`.sparkline-tooltip`) — in each, replace `    box-shadow: var(--wa-shadow-s);` (the one inside the tooltip rule) with:

```css
    box-shadow: var(--depth-2);
```

`SessionHeader.vue` (`.session-collapsible-rows` inside `@media (max-height: 900px)`, ~line 1437) — replace `        box-shadow: var(--wa-shadow-s);` with:

```css
        /* Spans the whole width of .session-view, clipped at the gap: keep the panel budget. */
        box-shadow: var(--panel-overlay-shadow);
```

`ProjectDetailHeader.vue` (`.detail-collapsible-rows`, ~line 528) — replace `        box-shadow: var(--wa-shadow-s);` with:

```css
        /* Same token as the session header's overflow panel, so both headers look alike. */
        box-shadow: var(--panel-overlay-shadow);
```

`ShareSessionApp.vue` (`.share-header::before`) — replace `    box-shadow: var(--wa-shadow-m);` with:

```css
    box-shadow: var(--depth-2);
```

Each CSS file above (not `App.vue`) has exactly one occurrence at the given place; `grep -n "var(--wa-shadow" <file>` before (one hit) and after (no hit) confirms it — grep `var(--wa-shadow`, not `wa-shadow`: the new `LayoutOverlay.vue` comment mentions `--wa-shadow-l`.

- [ ] **Step 6: Browser probes (light and dark)**

On 5174:
- open any dropdown menu (e.g. a session's "⋮" menu) → the panel shadow is soft and wide;
- trigger a toast (e.g. copy a message) → soft shadow, in light **and** dark; run
  `getComputedStyle(document.querySelector('.Notivue__notification')).boxShadow` → contains three layers plus Notivue's own `inset` tip layer;
- open the Artifacts tab as an overlay → deeper shadow than the docks, nothing cut at its left/right sides.

Expected: as described. Screenshot each, light and dark.

- [ ] **Step 7: Run the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t3.log 2>&1; tail -8 /tmp/depth-t3.log`
Expected: `# fail 0`.

---

### Task 4: Chat messages and tool cards

**Files:**
- Modify: `frontend/src/styles/transcript-tokens.css:5-18`
- Modify: `frontend/src/components/session/detail/SessionItem.vue` (user card ~499, assistant rows ~573/593/613, first row ~620, last row ~655, joined details ~681 and ~706, `wa-details.item-details` ~740)

**Interfaces:**
- Consumes: `--depth-card`, `--depth-card-reach`, `--depth-edge` (Task 2).

- [ ] **Step 1: User card tint** — in `transcript-tokens.css`, replace:

```css
    --user-card-base-color: oklch(from var(--base-user-assistant-card-color) calc(l + 0.015) c h);
```

with:

```css
    /* User messages carry a faint accent tint, so they read apart from the agent's cards. */
    --user-card-base-color: color-mix(in oklab, var(--wa-color-brand-fill-quiet) 70%, white);
```

and in the `.wa-dark` block of the same file, after `--base-user-assistant-card-color: var(--wa-color-surface-raised);`, add:

```css
    --user-card-base-color: color-mix(in oklab, var(--wa-color-brand-fill-quiet) 55%, var(--wa-color-surface-raised));
```

- [ ] **Step 2: User card shadow** — in `SessionItem.vue`, replace:

```css
    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) var(--wa-shadow-blur-s) var(--wa-shadow-spread-s) var(--user-card-border-color);
```

with:

```css
    box-shadow: var(--depth-card), var(--depth-edge);
```

- [ ] **Step 3: Assistant rows** — in `SessionItem.vue`:

Replace:

```css
        /* by default no shadow because default style is only for "inner" (not last) rows */
        --assistant-card-shadow: none;
```

with:

```css
        /* by default no shadow because default style is only for "inner" (not last) rows.
           A transparent layer, not `none`: the row's box-shadow is a list (shadow, edge). */
        --assistant-card-shadow: 0 0 transparent;
        /* Dark-mode top edge: only on the first row (set with the top radius below). */
        --assistant-card-edge: 0 0 transparent;
```

Replace:

```css
            --assistant-card-default-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) var(--wa-shadow-blur-s) var(--wa-shadow-spread-s) var(--assistant-card-border-color);
```

with:

```css
            /* Downward only: a side reach would be cut flat at the top of the last row. */
            --assistant-card-default-shadow: var(--depth-card);
```

Replace:

```css
            box-shadow: var(--assistant-card-shadow);
```

with:

```css
            box-shadow: var(--assistant-card-shadow), var(--assistant-card-edge);
```

In the `.is-block-start` rule, after `--assistant-card-top-spacing: var(--assistant-card-spacing);`, add:

```css
                --assistant-card-edge: var(--depth-edge);
```

In the `.is-block-end` rule, replace:

```css
                margin-bottom: calc(var(--main-shadow-size) + 1px); /* For the shadow to appear on the last element with virtual scroller "cropping" if we don't have this */;
```

with:

```css
                /* For the shadow to appear on the last element with virtual scroller "cropping" if
                   we don't have this. max(): --depth-card reaches further than the theme's shadow
                   offset in the default and shoelace themes. */
                margin-bottom: calc(max(var(--main-shadow-size), var(--depth-card-reach)) + 1px);
```

- [ ] **Step 4: Tool cards** — in `SessionItem.vue`:

In the generic joined rule, after the closing of `wa-details { &:has(+wa-details) {…} & + wa-details {…} }` (the block under `/* Handle many wa-details one after the other */`), add a sibling rule:

```css
/* A tool card joined to the next one casts no shadow: only the last card of a run does.
   Needs .item-details to beat `wa-details.item-details::part(base)` below (0,1,3 > 0,1,2). */
wa-details.item-details:has(+ wa-details)::part(base) {
    box-shadow: 0 0 transparent;
}
```

In the cross-row rule (`.session-items .virtual-scroller-item:has(wa-details.item-details:last-child)`), inside `wa-details.item-details:last-child { … &::part(base) { … } }`, after `border-bottom-width: 0;`, add:

```css
                    box-shadow: 0 0 transparent;
```

In the `wa-details.item-details { … }` rule, after `padding-bottom: var(--spacing-bottom);`, add:

```css
    /* Downward only, like chat cards: the next card of a joined run touches its top edge.
       No dark top edge (it would draw a seam at each join). */
    &::part(base) {
        box-shadow: var(--depth-card);
    }
```

- [ ] **Step 5: Run the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t4.log 2>&1; tail -8 /tmp/depth-t4.log`
Expected: `# fail 0`.

- [ ] **Step 6: Browser probes (light and dark), on the Task 1 session**

Run in the page:

```js
(() => {
  const user = document.querySelector('.session-items .session-item[data-kind="user_message"]')
  const details = [...document.querySelectorAll('.session-items wa-details.item-details')]
  const shadowOf = (el) => getComputedStyle(el.shadowRoot.querySelector('[part~="base"]')).boxShadow
  return {
    user: user && getComputedStyle(user).boxShadow,
    userBg: user && getComputedStyle(user).backgroundColor,
    tools: details.slice(-6).map((d) => shadowOf(d)),
  }
})()
```

Expected: the user shadow is three entries — the two soft `--depth-card` layers, then the `--depth-edge` entry (`rgba(0, 0, 0, 0) 0px 0px 0px 0px` in light, an `inset` light line in dark); the user background has an accent hue (differs from the Task 1 `userBg` baseline value); in a joined run, every tool card but the last reports `rgba(0, 0, 0, 0) 0px 0px 0px 0px` (transparent), the last one the two `--depth-card` layers. Then re-run the Task 1 step 3 gap probe: every value identical except, on block-end rows, `marginBottom` and the row `height`, both larger by 1px (default theme, 16px; `.virtual-scroller-item` is `display: flow-root`, so the margin counts in the row height). Scroll to the very bottom: the last card's shadow is whole.

---

### Task 5: Controls and question widget options

**Files:**
- Modify: `frontend/src/styles/depth.css` (append)
- Modify: `frontend/src/components/message/MessageInput.vue` (scoped style, after `.message-input wa-textarea::part(textarea)`)
- Modify: `frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue:1352,1503`
- Modify: `frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue:467`

**Interfaces:**
- Consumes: `--depth-1`, `--depth-2`, `--depth-highlight`, `--depth-inset` (Task 2).

- [ ] **Step 1: Global control rules** — append to `frontend/src/styles/depth.css`:

```css
/* Global rules below are wrapped in :where() (specificity 0 + ::part), so any unlayered
   component rule that sets its own box-shadow on the same part wins. Being unlayered they
   beat every layered Web Awesome rule; the only layered ones that shadow these parts are
   the awesome theme's button and field rules, hence the awesome exclusion (that theme's
   controls keep their hard shadows: its press effect depends on them). */

/* Secondary buttons: gently raised. Not: accent (the default appearance, step 6), plain,
   joined buttons of a group, buttons of an inverted box (toasts — the page-scheme highlight
   would draw a bright line), the sidebar rows (session, artifact bookmark; the selected row
   is step 6), and the two plain↔framed toggles (raised only when on would read backwards).
   The wa-button:is(…):not(…) compound stays on one line: a line break between :is() and
   :not() would be a descendant combinator and target what is inside the buttons. */
:where(
    :root:not(.wa-theme-awesome) wa-button:is([appearance*='outlined'], [appearance*='filled']):not(wa-button-group wa-button, .wa-invert wa-button, .session-item, .bookmark-item, .filters-toggle, .autoattach-button)
)::part(base) {
    box-shadow: var(--depth-1), var(--depth-highlight);
}

/* Text fields: slightly recessed. The focus ring is an outline, so it is unaffected. */
:where(:root:not(.wa-theme-awesome) :is(wa-input, wa-textarea))::part(base),
:where(:root:not(.wa-theme-awesome) wa-select)::part(combobox) {
    box-shadow: var(--depth-inset);
}
```

- [ ] **Step 2: Composer** — in `MessageInput.vue`, after the rule:

```css
.message-input wa-textarea::part(textarea) {
    /* Limit height to 40% of visual viewport (accounts for mobile keyboard) */
    max-height: 40dvh;
    /* Allow scrolling when content exceeds max-height */
    overflow-y: auto;
}
```

add:

```css

/* The composer stands out (level 2) instead of the recessed look of other fields. The
   footer (.session-footer, a scroll container) cuts the faint tail of this shadow 4px
   above and at the side padding. The awesome theme keeps its own field look. */
:root:not(.wa-theme-awesome) .message-input wa-textarea::part(base) {
    box-shadow: var(--depth-2);
}
```

- [ ] **Step 3: Question widget options** — in `PendingRequestBody.vue`, both occurrences (on `.permission-suggestion-card` and on `.option-card`), and in `RequestUserInputBody.vue` (on `.option-card`), replace:

```css
    box-shadow: var(--wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) 0 0 var(--border-color);
```

with:

```css
    box-shadow: var(--depth-1);
```

Confirm: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && grep -rn "wa-shadow-offset-x-s) var(--wa-shadow-offset-y-s) 0 0" frontend/src` → no output.

- [ ] **Step 4: Run the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t5.log 2>&1; tail -8 /tmp/depth-t5.log`
Expected: `# fail 0`.

- [ ] **Step 5: Browser probes (default theme, then awesome theme)**

Run in the page, with a dialog open that has an outlined button (e.g. project edit):

```js
(() => {
  const part = (el) => el && getComputedStyle(el.shadowRoot.querySelector('[part~="base"]')).boxShadow
  const outlined = document.querySelector('wa-dialog[open] wa-button[appearance="outlined"]')
  const session = document.querySelector('wa-button.session-item[appearance="outlined"]')
  const accent = document.querySelector('wa-button[appearance="accent"]')
  const input = document.querySelector('wa-input')
  const composer = document.querySelector('.message-input wa-textarea')
  return { outlined: part(outlined), session: part(session), accent: part(accent),
           input: part(input), composer: part(composer) }
})()
```

Expected, default theme: `outlined` = two soft layers + `inset` highlight; `session` and `accent` = identical to the Task 1 `activeSessionRow` / `accentButton` baseline values; `input` = one `inset` layer; `composer` = three soft layers. Awesome theme (Settings → theme): `outlined` = the hard `… 0px 0px` offset shadow of the theme; `input`/`composer` = the theme's hard inset; menus and cards soft. Switch back to the default theme.

---

### Task 6: Home and stats

**Files:**
- Modify: `frontend/src/components/project/ProjectCard.vue:150-153`
- Modify: `frontend/src/components/workspace/WorkspaceCard.vue:200-203`
- Modify: `frontend/src/components/activity/ActivityDashboard.vue:258,267,296,306,328,338,378`

**Interfaces:**
- Consumes: `--depth-2` (Task 2).

- [ ] **Step 1: Home card hover** — in `ProjectCard.vue` (`.project-card:hover`) and `WorkspaceCard.vue` (`.workspace-card:hover`), replace `    box-shadow: var(--wa-shadow-m);` with:

```css
    box-shadow: var(--depth-2);
```

(`WorkspaceCard.vue`'s `.workspace-card.disabled:hover { box-shadow: none; }` stays: a lone `none` is valid.)

- [ ] **Step 2: Pill badges** — in `ActivityDashboard.vue`, on each of the six `<wa-tag>` elements carrying `appearance="outlined"` (lines 258, 267, 296, 306, 328, 338), replace `appearance="outlined"` with `appearance="filled" pill`.

Confirm: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && grep -c 'appearance="filled" pill' frontend/src/components/activity/ActivityDashboard.vue` → `6`, and `grep -c 'appearance="outlined"' …` → `0`.

- [ ] **Step 3: Stats card hover** — in `ActivityDashboard.vue`, replace the top-level:

```css
wa-card {
    min-width: 16rem;
}
```

with:

```css
wa-card {
    min-width: 16rem;
    /* Lifts to level 2 on hover (rests at level 1 through --wa-shadow-s). No translate:
       the cards are not clickable. */
    &:hover {
        box-shadow: var(--depth-2);
    }
}
```

(The two `wa-card` rules inside `@container project-detail` blocks are not touched.)

- [ ] **Step 4: Run the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t6.log 2>&1; tail -8 /tmp/depth-t6.log`
Expected: `# fail 0`.

- [ ] **Step 5: Browser check** — home page and a project's stats tab, light and dark: cards soft at rest, deeper on hover; home card hover fades smoothly in both schemes; trend and N/A badges are rounded filled pills.

---

### Task 7: Commit 1 — full verification and hand-over

**Files:** none new (verification), then the commit.

**Amendments (user review, 2026-09-27):** the user's visual review changed buttons, user
and assistant cards, split buttons, the sidebar chrome and the home page's floating buttons
— see spec §14. Those changes were applied at step 5 of this task, each probed in the
browser and followed by a green suite (`depth.test.js` gained `--depth-button`); the files
they touch are in the step 6 `git add` list.

- [ ] **Step 1: Build and bundle checks**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm run build > /tmp/depth-build.log 2>&1; tail -5 /tmp/depth-build.log`
Expected: the five builds complete, no error.

Run:
```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && S=src/twicc/static/share-session/share-session.css; H=src/twicc/static/artifact-shell/shell.css; \
grep -c -- '--depth-card:' $S; grep -c -- '--depth-card-reach' $S; \
grep -o -- '--user-card-base-color:[^;]*' $S | grep -c 'wa-color-brand-fill-quiet'; \
grep -o 'share-header:before{[^}]*}' $S | grep -c -- '--depth-2'; \
grep -c -- '--depth-3:' $H; grep -o -- '--wa-shadow-l:[^;]*' $H | grep -c 'depth-3'
```
Expected: every count ≥ 1.

- [ ] **Step 2: Tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t7.log 2>&1; tail -8 /tmp/depth-t7.log`
Expected: `# fail 0`, pass = 449 + 6.

- [ ] **Step 3: Browser checks — spec §12 items 1 to 14**

Run each item of spec §12 (1–14) on http://localhost:5174, light and dark, default theme at 16px unless the item says otherwise, comparing with the Task 1 baseline. For item 10 change the theme and the font size in Settings, then restore them. For item 13 resize the Chrome window to a height ≤ 900px (`resize_window`), then restore. Record each item's result in the ledger (pass / what was seen). Any failure: fix it (with its own probe re-run) before going on.

- [ ] **Step 4: Invariant grep**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git diff --stat && { git diff -U0 | grep -E '^\+'; cat frontend/src/styles/depth.css; } | grep -nE '(^|[^-\w])(transform|filter|backdrop-filter|contain|will-change|container-type)\s*:' || echo "no forbidden property added"`
Expected: `no forbidden property added`. Only added lines are scanned (`-U0` + `^\+`: context lines such as the home cards' existing `transform: translateY(-2px)` are not); `git diff` does not show the new untracked `depth.css`, hence the `cat`.

- [ ] **Step 5: Hand over to the user and STOP**

Tell the user (French, UI terms) what changed and where to look on http://localhost:5174 (chat, buttons, fields, composer, menus, toasts, overlay, home, stats; light/dark; awesome theme), list anything not verified, and offer the two deferred tries (message radius `0.875rem`, white assistant cards — spec §2). Wait for their review. Apply any requested change, re-run Steps 1–2.

- [ ] **Step 6: Commit — only after the user says "commit"**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git add \
  docs/plans/2026-09-26-depth-design.md docs/plans/2026-09-26-depth-plan.md \
  frontend/src/styles/depth.css frontend/src/styles/depth.test.js \
  frontend/src/main.js frontend/src/share-session/main.js frontend/src/artifact-shell/main.js \
  frontend/src/styles/surfaces.css frontend/src/styles/transcript-tokens.css \
  frontend/src/components/session/layout/LayoutOverlay.vue frontend/src/App.vue \
  frontend/src/components/app/UsageGraphDialog.vue frontend/src/components/activity/ContributionSparklines.vue \
  frontend/src/components/session/detail/SessionHeader.vue frontend/src/components/project/ProjectDetailHeader.vue \
  frontend/src/share-session/ShareSessionApp.vue frontend/src/components/session/detail/SessionItem.vue \
  frontend/src/components/message/MessageInput.vue \
  frontend/src/components/session/detail/items/claude_code/PendingRequestBody.vue \
  frontend/src/components/session/detail/items/codex/RequestUserInputBody.vue \
  frontend/src/components/project/ProjectCard.vue frontend/src/components/workspace/WorkspaceCard.vue \
  frontend/src/components/activity/ActivityDashboard.vue \
  frontend/src/views/ProjectView.vue frontend/src/views/HomeView.vue \
  frontend/src/components/session/SessionsSidebarControls.vue \
  frontend/src/components/artifacts/ArtifactBookmarksSidebarControls.vue \
  frontend/src/components/sidebar/SidebarViewSwitch.vue frontend/src/components/sidebar/SidebarListSeparator.vue \
  frontend/src/components/app/CommandPaletteButton.vue frontend/src/components/app/SettingsPopover.vue \
  frontend/src/components/peer/PeerInboxButton.vue \
&& git commit -F - <<'EOF'
feat(ui): layered soft shadows on three depth levels

Surfaces read flat: chat cards sat on a hard 2px line of their border color,
and Web Awesome's small offset shadows barely separated floating layers from
the page. styles/depth.css defines three soft, layered levels (resting,
standing out, floating), a downward-only variant for chat and tool cards, a
button highlight, a dark-mode top edge and a recessed-field inset, and maps
Web Awesome's --wa-shadow-s/m/l onto them, so cards, menus, selects, dialogs,
drawers and popovers follow in every theme. The file is shared by the SPA,
the share viewer and the artifact shell.

User messages get a faint accent tint (quotes and colon blocks inside them
start their alternation on the plain surface); light agent cards turn white;
chat and tool cards cast a soft downward shadow (only the last card of a
joined run); buttons are gently raised (solid ones with a light line drawn
from their own color), fields slightly recessed, the composer stands out;
split buttons lose Web Awesome's see-through gap between segments; toasts,
chart tooltips and the share header move to their level; home and stats
cards deepen on hover and trend badges become filled pills. The layout
overlay and the headers' overflow panels get their own shadow that keeps the
step-1 horizontal budget. The awesome theme keeps its hard button and field
shadows.

On the sidebar, its own buttons switch to the accent outline of the
back-home button, the project selector and filter fields are painted with
the canvas (tinted, still opaque when they widen), and its separators take
an accent-based color. On the home page, the floating Inbox and Settings
buttons are solid so nothing shows through them.

A node test pins the token invariants (valid lists, dark layer order for
smooth hover transitions, shadow reach, imports).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

Expected: one commit; `git status` clean except files of later tasks (none yet).

---

### Task 8: Typography (commit 2)

**Files:**
- Modify: `frontend/src/styles/transcript-tokens.css`
- Modify: `frontend/src/components/ui/MarkdownContent.vue` (global `<style>`, `.markdown-body` rule ~868)
- Modify: `frontend/src/components/session/detail/SessionHeader.vue:1016-1019`
- Modify: `frontend/src/components/project/ProjectDetailHeader.vue:359-362`
- Modify: `frontend/src/views/HomeView.vue:190-195`
- Modify: `frontend/src/components/sidebar/SidebarListSeparator.vue` (`.sls-label`)

**Interfaces:** none.

- [ ] **Step 0: Typography baseline (after commit 1)**

Commit 1 changed the stats badges, so the commit-2 visual baseline is taken now, before
any typography change: light screenshots (Chrome MCP) of the sidebar session list (costs
and badges visible), a session header (title, context badge, cost), a project's stats
tab (big numbers and pills) and the home page title. Note in the ledger which screenshot
shows what. (The data on these pages is live, so the numeric fit check of Task 9 step 2
is an A/B toggle on one page at one moment, not a comparison with this baseline.)

- [ ] **Step 1: Tabular digits** — in `transcript-tokens.css`, after the `.wa-dark { … }` block, add:

```css
/* Fixed-width digits everywhere, so counters, costs and durations do not shift while they
   update. Prose opts back out (MarkdownContent.vue, .markdown-body). */
body {
    font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 2: Prose** — in `MarkdownContent.vue`'s global `<style>`, replace:

```css
.markdown-body {
    background: transparent;
    /* Override github-markdown-css fixed 16px to inherit from :root */
    font-size: 1rem;
}
```

with:

```css
.markdown-body {
    background: transparent;
    /* Override github-markdown-css fixed 16px to inherit from :root */
    font-size: 1rem;
    /* Prose keeps proportional digits (the app sets tabular-nums on body). */
    font-variant-numeric: normal;
}

/* No lonely last word at the end of a paragraph. */
.markdown-body :is(p, li) {
    text-wrap: pretty;
}
```

- [ ] **Step 3: Titles**

`SessionHeader.vue` `.session-title h2`: replace `    font-weight: 600;` (inside that rule) with:

```css
    font-weight: 650;
    letter-spacing: -0.015em;
```

`ProjectDetailHeader.vue`: replace

```css
.detail-title {
    font-weight: 600;
```

with

```css
.detail-title {
    font-weight: 650;
    letter-spacing: -0.015em;
```

`HomeView.vue` `.home-header h1`: after `    font-weight: 700;` add:

```css
    letter-spacing: -0.02em;
```

- [ ] **Step 4: Section labels** — in `SidebarListSeparator.vue`, replace:

```css
.sls-label {
    flex: 0 0 auto;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
```

with:

```css
.sls-label {
    flex: 0 0 auto;
    font-size: 0.6875rem;
    font-weight: var(--wa-font-weight-semibold);
    text-transform: uppercase;
    letter-spacing: 0.07em;
```

- [ ] **Step 5: Run the whole suite**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm test > /tmp/depth-t8.log 2>&1; tail -8 /tmp/depth-t8.log`
Expected: `# fail 0`.

- [ ] **Step 6: Browser probe**

```js
(() => ({
  body: getComputedStyle(document.body).fontVariantNumeric,
  prose: (el => el && getComputedStyle(el).fontVariantNumeric)(document.querySelector('.markdown-body')),
  label: (el => el && getComputedStyle(el).textTransform)(document.querySelector('.sls-label')),
}))()
```

Expected: `tabular-nums`, `normal`, `uppercase`.

---

### Task 9: Commit 2 — verification, hand-over, roadmap

**Files:**
- Modify: `docs/plans/2026-09-26-visual-refresh-roadmap.md` (§1 status row 2; a short "Step 2 — done" section after §6)

- [ ] **Step 1: Build and tests**

Run: `cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui/frontend && npm run build > /tmp/depth-build2.log 2>&1; tail -5 /tmp/depth-build2.log; npm test > /tmp/depth-t9.log 2>&1; tail -8 /tmp/depth-t9.log`
Expected: builds complete; `# fail 0`.

- [ ] **Step 2: Browser checks — spec §12 items 15 and 16.** Visual: compare with the Task 8 step 0 screenshots (titles, labels). Numeric fit (dense numbers: no new truncation, wrap or overflow): the data is live, so run an A/B toggle of the only change that affects these elements (`tabular-nums` on `body`; none of them sets its own `font-variant-numeric`) on the same page at the same moment — once on a **session page** (sidebar + header), once on a **project's stats tab**:

```js
(() => {
  const probe = () => {
    const dense = '.session-meta, .session-cost, .session-header .meta-item, .detail-meta, .dashboard-grid > wa-card, .dashboard-grid wa-tag, .dashboard-grid .wa-heading-2xl, .dashboard-grid .wa-heading-l'
    const all = [...document.querySelectorAll(dense)]
    return {
      checked: all.length,
      // Truncation where the box clips (the sidebar .session-meta grid, overflow: hidden).
      overflowing: all.filter((e) => e.scrollWidth > e.clientWidth).length,
      // Wraps: most of these boxes size to their content or wrap, so a wider digit shows
      // as a taller wrapping container (header metas, a stats card whose tag drops to a
      // new line) or an extra stats row, not as an overflow.
      heights: all.map((e) => Math.round(e.getBoundingClientRect().height)).join(','),
      statRows: new Set([...document.querySelectorAll('.dashboard-grid > wa-card')].map((c) => c.offsetTop)).size,
      // The header's context ring: its "NN%" label is absolutely positioned and never
      // changes a measured box, so compare the label width with the ring width.
      ring: [...document.querySelectorAll('.context-usage-ring')].map((r) =>
        Math.round(r.querySelector('span').getBoundingClientRect().width * 10) / 10 + '/' + Math.round(r.getBoundingClientRect().width)).join(','),
    }
  }
  document.body.style.fontVariantNumeric = 'normal'
  const before = probe()
  document.body.style.fontVariantNumeric = ''
  const after = probe()
  return { before, after }
})()
```

Expected on both pages: `after.checked === before.checked`, `after.heights === before.heights`, `after.overflowing <= before.overflowing`, `after.statRows <= before.statRows`; on the session page, for each ring (`label/ring` pairs), the label width after ≤ max(its width before, ring width − 9px) — 9px is twice the widest indicator stroke (`--indicator-width` grows up to 1.5 × the 3px track at ≥ 40% usage, `SessionHeader.vue`), so the label stays inside the clear circle. The compact-mode duplicate ring is hidden outside compact mode and reports `0/0`: expected. Use a session whose header shows the context ring (ideally ≥ 40% usage, the tightest case). Record the outputs in the ledger.

- [ ] **Step 3: Hand over to the user and STOP** (French, UI terms). Wait for review; apply requested changes.

- [ ] **Step 4: Commit 2 — only after the user says "commit"**

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git add \
  frontend/src/styles/transcript-tokens.css frontend/src/components/ui/MarkdownContent.vue \
  frontend/src/components/session/detail/SessionHeader.vue frontend/src/components/project/ProjectDetailHeader.vue \
  frontend/src/views/HomeView.vue frontend/src/components/sidebar/SidebarListSeparator.vue \
&& git commit -F - <<'EOF'
feat(ui): steadier numbers, tighter titles and small uppercase section labels

Digits are fixed-width across the interface, so counters, costs and durations
stop shifting while they update; message prose keeps proportional digits and
avoids a lonely last word. Session, project and home titles are slightly
tighter (and bolder where they were semibold); sidebar section labels become
small uppercase labels with letter spacing.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

Expected: one commit; `git status` clean.

- [ ] **Step 4b: Final whole-branch review** (superpowers:executing-plans "Final Review")

Run the skill's review package on the range `655381c2..HEAD` (step 2 commits only; `655381c2` is the step-1 roadmap commit), dispatch a fresh reviewer on the most capable model with the package, this plan, the spec, the Review Focus section verbatim and the ledger's `Ruling:` lines. Every Critical/Important finding gets a fix verified by a failing-then-passing check (the node test, or a browser probe for CSS-only effects) and a green suite; every Minor is fixed too (user rule: every review finding gets fixed). Order: fix → show the user → commit on their "commit" (`fix(ui): …`, files staged by path) → re-run the reviewer on the new `655381c2..HEAD` package (the package is built from commits only, so a re-review before the commit would not see the fixes) → repeat until clean. Record the outcome in the ledger. Keep the workspace (ledger) until step 6 is committed — step 5 reads it; delete it after step 6.

- [ ] **Step 5: Update the roadmap** (Edit tool; the file is tracked), now that every commit hash exists (`git log --oneline 655381c2..HEAD`): in §1, row 2 → `**Done** — commits <c1>, <c2>[, fix commits] on branch \`enhanced-ui\``; add a `## 6b. Step 2 — depth + typography (done)` section after §6 with: spec/plan paths, review rounds (spec 7 rounds, PASS; plan rounds as run; final whole-branch review result), what it does (tokens in `depth.css`, WA mapping, levels per element), the user decisions of spec §2, lessons (layer order for transitions; `:where()` vs layered theme rules; the one-line `:is():not()` compound), and anything left unverified. Also update the memory file `project_visual_refresh_floating_panels.md` (step 2 done, commits).

- [ ] **Step 6: Commit the roadmap — only after the user says "commit"** (same precedent as step 1's roadmap commit `655381c2`)

```bash
cd /home/twidi/dev/twicc-poc/.worktrees/enhanced-ui && git add docs/plans/2026-09-26-visual-refresh-roadmap.md \
&& git commit -F - <<'EOF'
docs(plans): record step 2 (depth and typography) in the visual refresh roadmap

Marks step 2 done with its commits, and records what it does, the
user's decisions for it and the lessons learned (shadow layer order for
smooth transitions, :where() against layered theme rules, the one-line
:is():not() button selector).

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
EOF
```

Expected: one commit; `git status` clean.
