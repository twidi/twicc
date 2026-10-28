// Sidebar recompute benchmark
// (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 7.2 and 10).
//
// Run: cd frontend && node scripts/benchmark_sidebar_recompute.mjs   (several minutes)
//
// Measures, in one process, the cost of one live-update batch on the sidebar
// derivations "before" (the frozen baseline copies of tests/helpers/sidebarBaseline/)
// and "after" (the shipped modules), and prints the ratio before ÷ after.
//
//   - One batch = one `session_updated` flush (a real tick of one session: `mtime`,
//     `last_line`, `last_new_content_at`), then one `project_updated` flush (the
//     project's `mtime`).
//   - Both implementations run on the same fixture (tests/helpers/sidebarFixture.js),
//     each on its own state, through the facades the tests use. "Before" runs the
//     frozen `computeSidebarSessionBlocks` in one retaining computed, like
//     SessionList.vue did; "after" runs the shipped wiring factory
//     `useSidebarSessionBlocks`.
//   - One `effect` with a scheduler subscribes, like a component: the blocks, the
//     unread index (one summary per project, the workspace summary, the total) and,
//     in tree mode, `buildSessionTree` + `flattenTree`.
//   - Median of 5 runs of 20 batches, after 10 warm-up batches; store sizes 3 000
//     and 9 000 (1 000 and 3 000 roots, 2 subagents each).
//
// The benchmark is evidence, not a gate: the exit code is 0 whatever the ratios.
// The deterministic tests of design 7.1 are the gate. A `checkPremises` failure is
// different: the fixture does not give a scenario its premises, so it is a harness
// bug; the script throws and exits non-zero. A threshold-group line is marked
// `(blocks differ)` when one of its rows printed `same blocks: NO`.

import { computed, effect, version as vueVersion } from 'vue'
import { ALL_PROJECTS_ID } from '../src/constants.js'
import { useSidebarSessionBlocks } from '../src/utils/sidebarSessions.js'
import { getProjectActivityIndex } from '../src/utils/projectProcessActivity.js'
import { buildSessionTree, flattenTree } from '../src/utils/sessionTree.js'
import { isSessionUnread } from '../src/utils/sessions.js'
import { extractWorkspaceId, isWorkspaceProjectId } from '../src/utils/workspaceIds.js'
import { computeSidebarSessionBlocks as baselineSessionBlocks } from '../tests/helpers/sidebarBaseline/sidebarSessions.js'
import { createBaselineActivityIndex } from '../tests/helpers/sidebarBaseline/projectInputs.js'
import { buildSessionTree as baselineBuildSessionTree, flattenTree as baselineFlattenTree }
    from '../tests/helpers/sidebarBaseline/sessionTree.js'
import { FIXTURE_IDS as ID, buildSidebarState, createBaselineData, createShippedData, tick }
    from '../tests/helpers/sidebarFixture.js'

const SUBAGENTS_PER_ROOT = 2
const ROOT_COUNTS = [1000, 3000]          // store sizes 3 000 and 9 000
// Pagination: the oldest roots are below the `__all__` cutoff. Some of them (cut-p0,
// cut-p2) are still in the scope lists of their single project.
const ROOTS_BELOW_CUTOFF = 50
const WARM_UP_BATCHES = 10
const RUNS = 5
const BATCHES_PER_RUN = 20

const IMPLEMENTATIONS = {
    before: {
        createData: createBaselineData,
        mountBlocks: (options) => computed(previous => baselineSessionBlocks(options, previous)),
        activityIndex: createBaselineActivityIndex,
        buildSessionTree: baselineBuildSessionTree,
        flattenTree: baselineFlattenTree,
    },
    after: {
        createData: createShippedData,
        mountBlocks: (options) => useSidebarSessionBlocks(() => options).blocks,
        activityIndex: getProjectActivityIndex,
        buildSessionTree,
        flattenTree,
    },
}

// The rows of design section 10. `targets`: the ticked sessions, in turn; with two
// targets each tick gives the session an mtime above every other ("order changes").
// In the project and workspace scopes the fixture places two "Active elsewhere"
// members (unread-1 in p4, unread-2 in p5) and two "Pinned elsewhere" members
// (pinned-1 in p6, pinned-2 in p7); the all-projects scope has no cross-filter
// block. "Without process outside the scope" ticks unread-1, already unread, so the
// block keeps its two members. The tree scenarios have an extension: the p0
// `ancestor` of `child` sits below every cutoff.
const SCENARIOS = [
    { label: 'Project, active', group: 'flat', scope: 'p0', targets: [ID.processAssistant] },
    { label: 'Project, without process, order changes', group: 'flat', scope: 'p0',
        targets: [ID.p0Recent, ID.p0Second] },
    { label: 'Project, without process, "Active elsewhere"', group: 'flat', scope: 'p0', targets: [ID.unread1] },
    { label: 'All projects, active', group: 'all', scope: ALL_PROJECTS_ID, targets: [ID.processAssistant] },
    { label: 'All projects, without process, order changes', group: 'all', scope: ALL_PROJECTS_ID,
        targets: [ID.p0Recent, ID.p0Second] },
    { label: 'Workspace, active', group: 'flat', scope: 'ws:w', targets: [ID.processAssistant] },
    { label: 'Workspace, without process outside the workspace', group: 'flat', scope: 'ws:w',
        targets: [ID.unread1] },
    { label: 'Workspace, without process, order changes', group: 'flat', scope: 'ws:w',
        targets: [ID.p0Recent, ID.p1Recent] },
    { label: 'Tree, project, active, with extension', group: 'tree', scope: 'p0', tree: true,
        targets: [ID.processAssistant] },
    { label: 'Tree, project, without process, order changes, with extension', group: 'tree', scope: 'p0',
        tree: true, targets: [ID.p0Recent, ID.p0Second] },
]

// Expected ratios (before ÷ after) of design 7.2, at every store size.
const THRESHOLD_GROUPS = [
    { group: 'flat', label: 'single-project and workspace flat', min: 20 },
    { group: 'all', label: 'all-projects', min: 10 },
    { group: 'tree', label: 'tree', min: 10 },
]

/** Mount one implementation on its own state and subscribe its derivations like a component. */
function mount(impl, state, { scope, tree = false }) {
    const { data, workspaces } = impl.createData(state)
    const blocks = impl.mountBlocks({
        data, workspaces, effectiveProjectId: scope,
        activeWorkspaceId: isWorkspaceProjectId(scope) ? extractWorkspaceId(scope) : null,
        sessionId: null, showArchived: false, showArchivedProjects: false,
        showActiveAcrossFilters: true, showAsTree: tree,
    })
    const index = impl.activityIndex(data)
    const projectIds = Object.keys(state.projects)
    // Like SessionList.vue: the tree, then its rows (every node expanded).
    const sessionTree = tree
        ? computed(() => impl.buildSessionTree(blocks.value.natural, {
            lookup: id => state.sessions[id] ?? null,
            processStates: state.processStates,
            viewedId: null,
            isUnread: isSessionUnread,
        }))
        : null
    const treeRows = tree ? computed(() => impl.flattenTree(sessionTree.value, () => true)) : null

    let dirty = false
    const runner = effect(() => {
        blocks.value
        // The unread index: the project badges, the workspace badge, the favicon.
        for (const projectId of projectIds) data.getProjectActivitySummary([projectId])
        data.getProjectActivitySummary(workspaces.getVisibleProjectIds('w'))
        index.totalUnread()
        treeRows?.value
    }, { scheduler: () => { dirty = true } })
    return {
        blocks,
        treeRows,
        flush() { if (dirty) { dirty = false; runner() } },
        stop() { runner.effect.stop() },
    }
}

/** One batch: a `session_updated` flush, then a `project_updated` flush. */
function batch(state, view, scenario, i) {
    const { targets } = scenario
    const sessionId = targets[i % targets.length]
    tick(state, sessionId, { top: targets.length > 1 })
    view.flush()
    state.projects[state.sessions[sessionId].project_id].mtime += 1
    view.flush()
}

const median = (values) => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)]
const ids = (sessions) => sessions.map(s => s.id)

/** The block ids (and tree rows) of a view, to check that both implementations agree. */
function snapshot(view) {
    const { natural, crossFilterPinned, crossFilterActive, extra } = view.blocks.value
    return JSON.stringify({ extra: extra?.id ?? null, crossFilterPinned: ids(crossFilterPinned),
        crossFilterActive: ids(crossFilterActive), natural: ids(natural),
        rows: view.treeRows ? view.treeRows.value.map(row => row.id) : null })
}

/** Throw when the fixture does not give the scenario its premises (a benchmark bug, not a result). */
function checkPremises(state, view, scenario, size) {
    const where = `${scenario.label} (store ${size})`
    const { natural, crossFilterPinned, crossFilterActive } = view.blocks.value
    if (scenario.scope !== ALL_PROJECTS_ID && (crossFilterPinned.length !== 2 || crossFilterActive.length !== 2)) {
        throw new Error(`${where}: expected 2 "Pinned elsewhere" and 2 "Active elsewhere" members, `
            + `got ${crossFilterPinned.length} and ${crossFilterActive.length}`)
    }
    if (scenario.tree && !natural.some(s => s.id === ID.ancestor)) {
        throw new Error(`${where}: the tree extension does not hold ${ID.ancestor}`)
    }
    if (scenario.targets.length > 1) {
        // The last ticked session is the first natural session without a process, a pin or an ephemeral run.
        const lastTicked = scenario.targets[(WARM_UP_BATCHES + RUNS * BATCHES_PER_RUN - 1) % scenario.targets.length]
        const firstQuiet = natural.find(s => !s.pinned && !s.ephemeral && state.processStates[s.id] == null)
        if (firstQuiet?.id !== lastTicked) {
            throw new Error(`${where}: ${lastTicked} did not take the top (top is ${firstQuiet?.id})`)
        }
    }
}

/** Time one implementation on one scenario: median ms per batch, and the final blocks. */
function measure(impl, scenario, roots) {
    const state = buildSidebarState({ roots, subagentsPerRoot: SUBAGENTS_PER_ROOT,
        cutoffRoots: roots - ROOTS_BELOW_CUTOFF })
    const view = mount(impl, state, scenario)
    let i = 0
    for (let w = 0; w < WARM_UP_BATCHES; w++) batch(state, view, scenario, i++)
    const perBatch = []
    for (let r = 0; r < RUNS; r++) {
        const start = performance.now()
        for (let b = 0; b < BATCHES_PER_RUN; b++) batch(state, view, scenario, i++)
        perBatch.push((performance.now() - start) / BATCHES_PER_RUN)
    }
    const result = { ms: median(perBatch), state, view, snapshot: snapshot(view) }
    view.stop()
    return result
}

const pad = (value, width) => String(value).padStart(width)
const LABEL_WIDTH = Math.max(...SCENARIOS.map(s => s.label.length)) + 2

console.log('Sidebar recompute benchmark: cost of one batch (one session_updated flush, then one project_updated flush)')
console.log(`Median of ${RUNS} runs of ${BATCHES_PER_RUN} batches after ${WARM_UP_BATCHES} warm-up batches; `
    + `${SUBAGENTS_PER_ROOT} subagents per root; Node ${process.version}, Vue ${vueVersion}`)
console.log('Store 3000 = 1000 roots, store 9000 = 3000 roots (the draft and the ephemeral root have no subagents)')
console.log()
console.log('Scenario'.padEnd(LABEL_WIDTH) + 'store'.padStart(6) + 'before ms'.padStart(11) + 'after ms'.padStart(10)
    + 'ratio'.padStart(9) + '  same blocks')

const results = []
for (const roots of ROOT_COUNTS) {
    const size = roots * (1 + SUBAGENTS_PER_ROOT)
    for (const scenario of SCENARIOS) {
        const before = measure(IMPLEMENTATIONS.before, scenario, roots)
        const after = measure(IMPLEMENTATIONS.after, scenario, roots)
        checkPremises(before.state, before.view, scenario, size)
        checkPremises(after.state, after.view, scenario, size)
        const ratio = before.ms / after.ms
        const same = before.snapshot === after.snapshot
        results.push({ scenario, size, ratio, same })
        console.log(scenario.label.padEnd(LABEL_WIDTH) + pad(size, 6) + pad(before.ms.toFixed(2), 11)
            + pad(after.ms.toFixed(2), 10) + pad(`${ratio.toFixed(1)}x`, 9) + `  ${same ? 'yes' : 'NO'}`)
    }
}

console.log()
console.log('Threshold groups (design 7.2: minimum ratio over every scenario of the group, at every store size)')
for (const { group, label, min } of THRESHOLD_GROUPS) {
    const ratios = results.filter(r => r.scenario.group === group)
    const lowest = ratios.reduce((low, r) => (r.ratio < low.ratio ? r : low))
    const verdict = ratios.every(r => r.ratio >= min) ? 'ok' : 'below'
    const blocksDiffer = ratios.some(r => !r.same) ? '   (blocks differ)' : ''
    console.log(`${label.padEnd(36)} expected >= ${pad(min, 2)}x   lowest ${pad(lowest.ratio.toFixed(1), 6)}x `
        + `(${lowest.scenario.label}, store ${lowest.size})   ${verdict}${blocksDiffer}`)
}
