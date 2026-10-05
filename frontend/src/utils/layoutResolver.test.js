// Run with: node --test src/utils/layoutResolver.test.js (from the frontend dir)
//
// Scenario assertions for the pure dockable-layout resolver — the executable spec that pins the
// rules described in docs/plans/2026-06-16-dockable-layout-design.md. Ported from the design
// prototype's playground (`tests.js`, plain data-in/data-out by construction) so the coverage
// finally lives with the code it guards.
//
// Each entry is one viewport/intention combination plus a predicate over the renderDescription.
// Keep them declarative: a scenario states an OUTCOME (this region is merged, that gutter spans
// only the column band), never an intermediate computation — the resolver is free to reach it
// however it likes. Add a scenario whenever a threshold in DEFAULT_CONFIG gains a new behaviour.

import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveLayout } from './layoutResolver.js'

const T = {
    chat: { id: 'chat', label: 'Chat', icon: 'comments', fixedCenter: true },
    files: { id: 'files', label: 'Files', icon: 'folder' },
    git: { id: 'git', label: 'Git', icon: 'code-branch' },
    terminal: { id: 'terminal', label: 'Terminal', icon: 'terminal' },
    logs: { id: 'logs', label: 'Logs', icon: 'scroll' },
    artifacts: { id: 'artifacts', label: 'Artifacts', icon: 'image', optional: true, hasContent: false },
}

const has = (desc, pred) => desc.regions.some(pred)
const kindShown = (desc, kind) => desc.regions.some((r) => r.kind === kind)
const gutterOn = (desc, edge) => desc.gutters.some((g) => g.edge === edge)

const SCENARIOS = [
    {
        name: 'Large desktop → widescreen, both sides + bottom under center',
        input: {
            tabs: [T.chat, T.files, T.git, T.terminal, { ...T.artifacts, hasContent: true }],
            assignment: { files: 'left-top', git: 'left-bottom', terminal: 'bottom-left', artifacts: 'right-top' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => d.mode === 'widescreen' && kindShown(d, 'col-left') && kindShown(d, 'col-right')
            && has(d, (r) => r.kind === 'bottom' && r.x > 0),  // bottom under center, not full-width
    },
    {
        // 1060 = centerMinW (460) + 2 × sideMinW (300): the narrowest viewport that still fits both
        // columns, so the center lands under bottomComfortW and the bottom flips to full width.
        name: 'Squeezed center with two bottom siblings → classic flip (bottom full-width, split)',
        input: {
            tabs: [T.chat, T.files, T.git, T.terminal, T.logs],
            assignment: { files: 'left-top', git: 'right-top', terminal: 'bottom-left', logs: 'bottom-right' },
            viewport: { w: 1060, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => {
            const bottoms = d.regions.filter((r) => r.kind === 'bottom')
            const spansFull = bottoms.length === 2
                && Math.min(...bottoms.map((r) => r.x)) === 0
                && Math.abs(Math.max(...bottoms.map((r) => r.x + r.w)) - 1060) < 1
            return d.mode === 'classic' && spansFull
        },
    },
    {
        name: 'Single terminal + narrow center → bottom full width (classic), not sibling-dependent',
        input: {
            tabs: [T.chat, T.files, T.terminal],
            assignment: { files: 'left-top', terminal: 'bottom-left' },
            viewport: { w: 800, h: 900 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => {
            const bottoms = d.regions.filter((r) => r.kind === 'bottom')
            return d.mode === 'classic' && bottoms.length === 1 && bottoms[0].x === 0 && Math.abs(bottoms[0].w - 800) < 1
        },
    },
    {
        name: 'Single terminal + wide center → bottom stays under center (widescreen)',
        input: {
            tabs: [T.chat, T.files, T.terminal],
            assignment: { files: 'left-top', terminal: 'bottom-left' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => d.mode === 'widescreen' && d.regions.some((r) => r.kind === 'bottom' && r.x > 0),
    },
    {
        name: 'Width at 480 px → pure tabs (single center region)',
        input: {
            tabs: [T.chat, T.files, T.git, T.terminal],
            assignment: { files: 'left-top', git: 'right-top', terminal: 'bottom-left' },
            viewport: { w: 480, h: 900 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => d.mode === 'tabs' && d.regions.length === 1 && d.regions[0].slots[0].tabs.length === 4,
    },
    {
        name: 'Width above 480 px → docks remain available',
        input: {
            tabs: [T.chat, T.files, T.git, T.terminal],
            assignment: { files: 'left-top', git: 'right-top', terminal: 'bottom-left' },
            viewport: { w: 481, h: 900 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => d.mode !== 'tabs' && gutterOn(d, 'left') && gutterOn(d, 'right')
            && kindShown(d, 'bottom'),
    },
    {
        name: 'Two sides, medium width → mutual exclusion (left active, right gutter)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'right-top' },
            viewport: { w: 1000, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => kindShown(d, 'col-left') && !kindShown(d, 'col-right') && gutterOn(d, 'right'),
    },
    {
        name: 'Short height with a bottom → bottom collapses to its gutter',
        input: {
            tabs: [T.chat, T.terminal],
            assignment: { terminal: 'bottom-left' },
            viewport: { w: 1200, h: 300 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => gutterOn(d, 'bottom') && !kindShown(d, 'bottom'),
    },
    {
        // The flip threshold is centerMinH + bottomMinH (370). A mobile keyboard shrinking the layout
        // viewport must NOT cross it at ordinary phone heights: crossing it retargets the panel's
        // Teleport, which moves its DOM node and drops the focus — closing the keyboard, restoring
        // the height, and looping. Pins both sides of the boundary.
        name: 'Bottom dock survives a keyboard-sized height loss (flip at 370, not above)',
        input: {
            tabs: [T.chat, T.terminal],
            assignment: { terminal: 'bottom-left' },
            viewport: { w: 674, h: 380 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => {
            const shrunk = resolveLayout({ ...d.input, viewport: { w: 674, h: 360 } })
            return kindShown(d, 'bottom') && !gutterOn(d, 'bottom')
                && !kindShown(shrunk, 'bottom') && gutterOn(shrunk, 'bottom')
        },
    },
    {
        name: 'Two left siblings, low height → vertical merge (one tab bar)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'left-bottom' },
            viewport: { w: 1400, h: 280 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => has(d, (r) => r.kind === 'col-left' && r.merged === true),
    },
    {
        name: 'Two bottom siblings, too narrow to split → bottom merge (one tab bar)',
        input: {
            tabs: [T.chat, T.terminal, { ...T.artifacts, hasContent: true }],
            assignment: { terminal: 'bottom-left', artifacts: 'bottom-right' },
            viewport: { w: 540, h: 900 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => has(d, (r) => r.kind === 'bottom' && r.merged === true && r.x === 0),
    },
    {
        name: 'Widescreen: minimized bottom gutter sits under center only (not full width)',
        input: {
            tabs: [T.chat, T.files, T.git, T.terminal],
            assignment: { files: 'left-top', git: 'right-top', terminal: 'bottom-left' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: ['bottom-left'],
        },
        check: (d) => {
            const g = d.gutters.find((x) => x.edge === 'bottom')
            return d.mode === 'widescreen' && g && g.x > 0 && g.w < d.viewport.w
        },
    },
    {
        name: 'Classic: side gutter spans only the column band, above the full-width bottom',
        input: {
            tabs: [T.chat, T.files, T.terminal],
            assignment: { files: 'left-top', terminal: 'bottom-left' },
            viewport: { w: 555, h: 900 }, activeSide: 'left', collapsed: ['left-top'],
        },
        check: (d) => {
            const g = d.gutters.find((x) => x.edge === 'left')
            const b = d.regions.find((r) => r.kind === 'bottom')
            return d.mode === 'classic' && g && b && g.h < d.viewport.h && Math.abs(g.h - (d.viewport.h - b.h)) < 1
        },
    },
    {
        name: 'Gutter icons respect dock direction (left-top start, left-bottom end)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'left-bottom' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: ['left-top', 'left-bottom'],
        },
        check: (d) => {
            const g = d.gutters.find((x) => x.edge === 'left')
            if (!g) return false
            const a = Object.fromEntries(g.items.map((i) => [i.dockId, i.anchor]))
            return a['left-top'] === 'start' && a['left-bottom'] === 'end'
        },
    },
    {
        name: 'Sibling resize clamps to siblingMaxFrac (one sibling cannot exceed 80%)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'left-bottom' },
            viewport: { w: 1400, h: 1000 }, activeSide: 'left', collapsed: [],
            config: { leftSplitFrac: 0.95 },   // user dragged the splitter far down
        },
        check: (d) => {
            const top = d.regions.find((r) => r.id === 'left-top')
            const bot = d.regions.find((r) => r.id === 'left-bottom')
            return top && bot && Math.abs(top.h / (top.h + bot.h) - 0.8) < 0.001
        },
    },
    {
        name: 'Split mode exposes a draggable sibling splitter (not when merged)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'left-bottom' },
            viewport: { w: 1400, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => (d.splitters || []).some((s) => s.id === 'left-split' && s.configKey === 'leftSplitFrac'),
    },
    {
        name: 'Dock resize: dragging a column wide cannot push the center below centerResizeMinW (300)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'right-top' },
            viewport: { w: 1400, h: 1000 }, activeSide: 'left', collapsed: [],
            config: { leftColFrac: 0.9 },   // dragged the left column very wide
        },
        check: (d) => {
            const c = d.regions.find((r) => r.kind === 'center')
            return c && Math.abs(c.w - 300) < 1
        },
    },
    {
        name: 'Dock resize: dragging the RIGHT column wide pushes the LEFT to its min (symmetric)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'right-top' },
            viewport: { w: 1400, h: 1000 }, activeSide: 'left', activeResize: 'right', collapsed: [],
            config: { rightColFrac: 0.9 },
        },
        check: (d) => {
            const c = d.regions.find((r) => r.kind === 'center')
            const l = d.regions.find((r) => r.kind === 'col-left')
            return c && l && Math.abs(c.w - 300) < 1 && Math.abs(l.w - 150) < 1
        },
    },
    {
        name: 'Dock resize: growing the bottom keeps the chat at centerResizeMinH (150)',
        input: {
            tabs: [T.chat, T.terminal],
            assignment: { terminal: 'bottom-left' },
            viewport: { w: 800, h: 1000 }, activeSide: 'left', collapsed: [],
            config: { bottomFrac: 0.95 },   // dragged the bottom way up
        },
        check: (d) => {
            const c = d.regions.find((r) => r.kind === 'center')
            return c && Math.abs(c.h - 150) < 1
        },
    },
    {
        name: 'Dock resize: a shown side column exposes a dock splitter',
        input: {
            tabs: [T.chat, T.files],
            assignment: { files: 'left-top' },
            viewport: { w: 1400, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => (d.splitters || []).some((s) => s.id === 'left-dock' && s.configKey === 'leftColFrac'),
    },
    {
        name: 'Same dock, optional WITH content → shown as a real column',
        input: {
            tabs: [T.chat, { ...T.artifacts, hasContent: true }],
            assignment: { artifacts: 'right-top' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: [],
        },
        check: (d) => kindShown(d, 'col-right') && !gutterOn(d, 'right'),
    },
    {
        name: 'Maximize a dock → single full-bleed region, no gutters/splitters/overlays',
        input: {
            tabs: [T.chat, T.files, T.git, T.terminal],
            assignment: { files: 'left-top', git: 'right-top', terminal: 'bottom-left' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: [],
            maximized: ['left-top'],
        },
        check: (d) => d.mode === 'maximized' && d.regions.length === 1
            && d.regions[0].x === 0 && d.regions[0].y === 0
            && Math.abs(d.regions[0].w - 1680) < 1 && Math.abs(d.regions[0].h - 1000) < 1
            && d.gutters.length === 0 && (d.splitters || []).length === 0 && d.overlays.length === 0
            && d.regions[0].slots.length === 1 && d.regions[0].slots[0].dockId === 'left-top'
            && d.regions[0].slots[0].tabs.some((t) => t.id === 'files'),
    },
    {
        name: 'Maximize a merged region → both docks’ tabs in one region',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'left-bottom' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: [],
            maximized: ['left-top', 'left-bottom'],
        },
        check: (d) => d.mode === 'maximized' && d.regions.length === 1 && d.regions[0].merged === true
            && d.regions[0].slots.length === 2
            && d.regions[0].slots.flatMap((s) => s.tabs).length === 2,
    },
    {
        name: 'Maximize the central zone → center tabs fill the area, no docks',
        input: {
            tabs: [T.chat, T.files, T.terminal],
            assignment: { files: 'left-top', terminal: 'bottom-left' },
            viewport: { w: 1680, h: 1000 }, activeSide: 'left', collapsed: [],
            maximized: ['center'],
        },
        check: (d) => d.mode === 'maximized' && d.regions.length === 1
            && d.regions[0].slots[0].dockId === 'center'
            && d.regions[0].slots[0].tabs.some((t) => t.id === 'chat')
            && d.gutters.length === 0,
    },
    {
        name: 'Maximize wins over a layout that would otherwise gutter/overlay (medium width)',
        input: {
            tabs: [T.chat, T.files, T.git],
            assignment: { files: 'left-top', git: 'right-top' },
            viewport: { w: 1000, h: 1000 }, activeSide: 'left', collapsed: [],
            maximized: ['right-top'],
        },
        check: (d) => d.mode === 'maximized' && d.regions.length === 1
            && d.gutters.length === 0 && (d.splitters || []).length === 0
            && d.regions[0].slots[0].dockId === 'right-top',
    },
]

for (const scenario of SCENARIOS) {
    test(scenario.name, () => {
        const desc = resolveLayout(scenario.input)
        // A check may need to re-resolve a neighbouring viewport (threshold boundaries); hand it the
        // input back rather than making every scenario carry a second description.
        assert.ok(
            scenario.check({ ...desc, input: scenario.input }),
            `resolved mode=${desc.mode}`
            + `, regions=[${desc.regions.map((r) => r.id).join(',')}]`
            + `, gutters=[${desc.gutters.map((g) => g.edge).join(',')}]`
            + `, overlays=[${desc.overlays.map((o) => o.edge).join(',')}]`,
        )
    })
}
