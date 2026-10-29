// Sidebar block stages and their wiring factory
// (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md, 5.3, 5.4 and 5.7;
// tests of 7.1 #1, #3 to #7, #9 and #10 for the block part, and Review Focus 1, 3, 4 and 5).
//
// Unless a test names other values, the blocks use `showActiveAcrossFilters: true`
// and `showArchivedProjects: false`, and every test runs in scope `p0` and in
// scope `ws:w`, each on its own fixture. Each tick comment states whether the
// tick flips the unread state of its session.

import test from 'node:test'
import assert from 'node:assert/strict'
import { computed, effect, isReactive, toRaw } from 'vue'
import { computeSidebarNaturalMembership, computeSidebarSessionBlocks, orderSidebarNatural, useSidebarSessionBlocks }
    from './sidebarSessions.js'
import { resolveSessionScope } from './sessionLists.js'
import { observeStageRuns } from './stageRuns.js'
import { hasUnreadContent } from './sessions.js'
import { extractWorkspaceId, isWorkspaceProjectId } from './workspaceIds.js'
import { FIXTURE_IDS as ID, FIXTURE_SCOPES, FIXTURE_UPDATE_SEQUENCE, applyStep, buildSidebarState,
    createFixtureStores, progressMessage, reloadedState, startupPhase, tick } from '../../tests/helpers/sidebarFixture.js'

const ALL = '__all__'
const SCOPES = ['p0', 'ws:w']
const BLOCK_STAGES = ['blocks:natural', 'blocks:extension', 'blocks:crossFilter',
    'blocks:naturalOrder', 'blocks:pinnedOrder', 'blocks:activeOrder']
const NATURAL_STAGES = ['blocks:natural', 'blocks:extension', 'blocks:naturalOrder']
const ALL_STAGE_NAMES = ['rootSessions', 'scope:candidates', 'scope:members', 'scope:ordered',
    'memo:unread', 'memo:unreadContent', 'unread:counts', ...BLOCK_STAGES, 'workspace:visible', 'workspace:all']
const ids = (sessions) => sessions.map(s => s.id)
const stepNamed = (name) => FIXTURE_UPDATE_SEQUENCE.find(step => step.name === name)
const iso = (seconds) => new Date(seconds * 1000).toISOString()
/** The block stages a mode can run: flat mode never extends the tree. */
const blockStages = (tree) => tree ? BLOCK_STAGES : BLOCK_STAGES.filter(name => name !== 'blocks:extension')

/** A fixture with pagination cutoffs (40 of 60 roots above the `__all__` one) and its shipped facades. */
function setup({ roots = 60, cutoffRoots = 40, prepare = null } = {}) {
    const state = buildSidebarState({ roots, cutoffRoots })
    prepare?.(state)
    return { state, ...createFixtureStores(state) }
}

function blockOptions({ data, workspaces }, scope, overrides = {}) {
    return {
        data, workspaces, effectiveProjectId: scope,
        activeWorkspaceId: isWorkspaceProjectId(scope) ? extractWorkspaceId(scope) : null,
        sessionId: null, showArchived: false, showArchivedProjects: false,
        showActiveAcrossFilters: true, showAsTree: false, ...overrides,
    }
}

/** The shipped factory for one scope and mode. */
function mountView(context, scope, overrides = {}) {
    const options = blockOptions(context, scope, overrides)
    const { blocks, stages } = useSidebarSessionBlocks(() => options)
    return { scope, options, blocks, stages, label: `${scope ?? 'null'} ${options.showAsTree ? 'tree' : 'flat'}` }
}

/**
 * Keep the views subscribed like a component (an effect with a scheduler, so
 * the derivations rerun only at `flush()`), with the unread index summaries
 * when asked, and count the stage runs per name from now on.
 */
function watch({ data, workspaces }, views, { unread = false, extra = null } = {}) {
    let dirty = false
    const runner = effect(() => {
        for (const view of views) view.blocks.value
        if (unread) {
            data.getProjectActivitySummary(['p0'])
            data.getProjectActivitySummary(workspaces.getVisibleProjectIds('w'))
        }
        extra?.()
    }, { scheduler: () => { dirty = true } })
    const counts = new Map()
    const stopObserving = observeStageRuns(name => counts.set(name, (counts.get(name) || 0) + 1))
    return {
        flush() { if (dirty) { dirty = false; runner() } },
        runs: (name) => counts.get(name) || 0,
        seen: () => new Set(counts.keys()),
        reset() { counts.clear() },
        stop() { stopObserving(); runner.effect.stop() },
    }
}

function assertNoRuns(probe, names, label) {
    for (const name of names) assert.equal(probe.runs(name), 0, `${label}: ${name} must not run`)
}

/** A root without a process, with an mtime above every root: read unless `unread`. */
function addRoot(state, id, projectId, { mtime = topMtime(state), pinned = null, unread = false } = {}) {
    const content = iso(mtime)
    state.sessions[id] = {
        id, project_id: projectId, parent_session_id: null, provider: 'claude', title: `Session ${id}`,
        mtime, last_line: 10, last_new_content_at: content, last_viewed_at: unread ? iso(mtime - 5) : content,
        hidden: false, archived: false, pinned, mute_on_user_turn: false, spawned_by: null, tree_detached: false,
    }
}

function topMtime(state) {
    let top = 0
    for (const session of Object.values(toRaw(state.sessions))) top = Math.max(top, session.mtime)
    return top + 10
}

/** Own setup: make a session unread without an `mtime` change. */
function makeUnread(state, sessionId) {
    const session = state.sessions[sessionId]
    session.last_new_content_at = new Date(Date.parse(session.last_viewed_at) + 1000).toISOString()
}

/** Own setup: a timestamp after every content time of the session. */
function laterContent(session) {
    return new Date(Math.max(Date.parse(session.last_new_content_at) || 0,
        Date.parse(session.last_viewed_at) || 0) + 60_000).toISOString()
}

/** A regular p0 root below every cutoff (the fixture's `r<n>` rotation places one there). */
function oldP0Root(state) {
    const cutoff = state.localState.projects.p0.oldestSessionMtime
    return Object.values(toRaw(state.sessions))
        .find(s => !s.parent_session_id && s.project_id === 'p0' && s.id.startsWith('r') && s.mtime < cutoff).id
}

let controlCount = 0

/**
 * Positive controls on the same fixture: root adds that change each block
 * membership. A read root pinned `all` in p8 (out of scope) enters "Pinned
 * elsewhere"; an unread root in p8 enters "Active elsewhere"; a read root in p0
 * enters the natural block (and, in tree mode, reruns the extension). Then every
 * stage in `names` has run at least once.
 */
function assertStagesCanRun({ state }, probe, names, label) {
    probe.reset()
    const n = ++controlCount
    addRoot(state, `control-pinned-${n}`, 'p8', { pinned: 'all' })
    probe.flush()
    addRoot(state, `control-unread-${n}`, 'p8', { unread: true })
    probe.flush()
    addRoot(state, `control-natural-${n}`, 'p0')
    probe.flush()
    for (const name of names) assert.ok(probe.runs(name) >= 1, `${label}: ${name} positive control`)
}

// ---------------------------------------------------------------------------
// Block comparison

/** Same blocks: the same ids, in the same order. */
function assertSameBlocks(actual, expected, label) {
    assert.equal(actual.extra?.id ?? null, expected.extra?.id ?? null, `${label}: extra`)
    for (const block of ['crossFilterPinned', 'crossFilterActive', 'natural']) {
        assert.deepEqual(ids(actual[block]), ids(expected[block]), `${label}: ${block}`)
    }
}

/**
 * The blocks a reload shows: fresh stores on a copy of the data, outside startup,
 * computed in one shot. No live update may leave the sidebar behind them.
 */
function reloadedBlocks(state, options) {
    return computeSidebarSessionBlocks({ ...options, ...createFixtureStores(reloadedState(state)) })
}

/** Assert that the live blocks of a view equal those of a reload. */
function assertSameAsReload(view, state, label) {
    assertSameBlocks(view.blocks.value, reloadedBlocks(state, view.options), label)
}

// ---------------------------------------------------------------------------
// Rerun graph

test('an active in-scope tick reruns no block stage, no unreadCounts and none of its memos', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view], { unread: true })
            const label = view.label
            const before = view.blocks.value
            // An open tick of the assistant_turn session: no flip (its process is not in user_turn).
            tick(context.state, ID.processAssistant, { open: true })
            probe.flush()
            assertNoRuns(probe, [...BLOCK_STAGES, 'unread:counts', 'memo:unread', 'memo:unreadContent'], label)
            assert.strictEqual(view.blocks.value, before, label)
            assertStagesCanRun(context, probe,
                [...blockStages(tree), 'unread:counts', 'memo:unread', 'memo:unreadContent'], label)
            probe.stop()
        }
    }
})

test('a project mtime change reruns no block stage', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            const view = mountView(context, scope, { showAsTree: tree, showArchivedProjects: false })
            const probe = watch(context, [view])
            const label = view.label
            const before = view.blocks.value
            const projects = context.data.getProjects
            state.projects.p3.mtime += 5000
            probe.flush()
            // The activity-sorted project list changes; the blocks do not depend on it.
            assert.notStrictEqual(context.data.getProjects, projects, `${label}: the facade getProjects follows activity`)
            assertNoRuns(probe, BLOCK_STAGES, label)
            assert.strictEqual(view.blocks.value, before, label)
            assertStagesCanRun(context, probe, blockStages(tree), label)
            probe.stop()
        }
    }
})

test('a natural reorder reruns the natural order stage only', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            const natural = ids(view.blocks.value.natural)
            assert.ok(natural.indexOf(ID.child) < natural.indexOf(ID.p0Recent), label)
            // p0-recent is a read natural member above both cutoffs: the tick flips it to unread.
            assert.equal(hasUnreadContent(state.sessions[ID.p0Recent]), false, label)
            tick(state, ID.p0Recent, { top: true })
            probe.flush()
            assert.equal(hasUnreadContent(state.sessions[ID.p0Recent]), true, `${label}: the tick flips`)
            const after = ids(view.blocks.value.natural)
            assert.ok(after.indexOf(ID.p0Recent) < after.indexOf(ID.child), `${label}: the order changed`)
            assert.ok(probe.runs('blocks:naturalOrder') >= 1, label)
            assertNoRuns(probe, BLOCK_STAGES.filter(name => name !== 'blocks:naturalOrder'), label)
            assertStagesCanRun(context, probe, blockStages(tree), label)
            probe.stop()
        }
    }
})

test('a reorder inside Active elsewhere with two members reruns only that block sort', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            assert.deepEqual(ids(view.blocks.value.crossFilterActive), [ID.unread1, ID.unread2], label)
            const natural = view.blocks.value.natural
            // unread-2 is already unread: no flip.
            tick(state, ID.unread2, { top: true })
            probe.flush()
            assert.deepEqual(ids(view.blocks.value.crossFilterActive), [ID.unread2, ID.unread1], label)
            assert.strictEqual(view.blocks.value.natural, natural, label)
            assert.ok(probe.runs('blocks:activeOrder') >= 1, label)
            assertNoRuns(probe, BLOCK_STAGES.filter(name => name !== 'blocks:activeOrder'), label)
            assertStagesCanRun(context, probe, blockStages(tree), label)
            probe.stop()
        }
    }
})

test('a non-flipping timestamp change reruns no membership stage; a flip updates Active elsewhere', () => {
    for (const scope of SCOPES) {
        const context = setup()
        const { state } = context
        const view = mountView(context, scope)
        const probe = watch(context, [view], { unread: true })
        const label = view.label
        // unread-1 is an out-of-scope root without a process, already unread: no flip.
        const unread = state.sessions[ID.unread1]
        unread.last_new_content_at = laterContent(unread)
        probe.flush()
        assertNoRuns(probe, [...BLOCK_STAGES, 'unread:counts'], label)
        assert.ok(probe.runs('memo:unreadContent') >= 1, `${label}: the memo reran`)
        // p3-recent is an out-of-scope read root without a process: the change flips it to unread.
        probe.reset()
        const read = state.sessions[ID.p3Recent]
        read.last_new_content_at = laterContent(read)
        probe.flush()
        assert.ok(probe.runs('blocks:crossFilter') >= 1, label)
        assert.ok(ids(view.blocks.value.crossFilterActive).includes(ID.p3Recent), `${label}: Active elsewhere updated`)
        assertNoRuns(probe, NATURAL_STAGES, label)
        assertStagesCanRun(context, probe, [...blockStages(false), 'unread:counts'], label)
        probe.stop()
    }
})

test('an out-of-scope memo flip reruns the cross-filter membership but not the natural stages', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            const natural = view.blocks.value.natural
            // unread-1 (out of scope, no process) is marked as read: it flips to read.
            state.sessions[ID.unread1].last_viewed_at = laterContent(state.sessions[ID.unread1])
            probe.flush()
            assert.ok(probe.runs('blocks:crossFilter') >= 1, label)
            assert.deepEqual(ids(view.blocks.value.crossFilterActive), [ID.unread2], label)
            assert.strictEqual(view.blocks.value.natural, natural, label)
            // Only "Active elsewhere" changes: the pinned block is not sorted again.
            assertNoRuns(probe, [...NATURAL_STAGES, 'blocks:pinnedOrder'], label)
            assertStagesCanRun(context, probe, blockStages(tree), label)
            probe.stop()
        }
    }
})

// ---------------------------------------------------------------------------
// Startup phase (5.3)

/** Own setup of the startup tree tests: a second extension root, below every cutoff, spawned by `child`. */
function linkOldRoot(state) {
    const oldId = oldP0Root(state)
    state.sessions[oldId].spawned_by = ID.child
    return oldId
}

test('during startup a progress message reruns the natural stages but not the cross-filter membership', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            startupPhase(state, true)
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            if (tree) assert.ok(view.stages.natural.value.extension.length > 0, `${label}: an extension exists`)
            const naturalBefore = view.stages.natural.value
            progressMessage(state)
            probe.flush()
            assert.ok(probe.runs('blocks:natural') >= 1, label)
            // Nothing changed: the rerun keeps its previous output.
            assert.strictEqual(view.stages.natural.value, naturalBefore, label)
            if (tree) {
                assert.ok(probe.runs('blocks:extension') >= 1, label)
                assert.ok(probe.runs('blocks:naturalOrder') >= 1, label)
            }
            assertNoRuns(probe, ['blocks:crossFilter', 'blocks:pinnedOrder', 'blocks:activeOrder'], label)
            // Positive control: a pin during startup reruns the cross-filter membership and its order.
            probe.reset()
            state.sessions[ID.p3Recent].pinned = 'all'
            probe.flush()
            assert.ok(probe.runs('blocks:crossFilter') >= 1, `${label}: crossFilter positive control`)
            assert.ok(probe.runs('blocks:pinnedOrder') >= 1, `${label}: pinnedOrder positive control`)
            state.sessions[ID.unread2].last_viewed_at = laterContent(state.sessions[ID.unread2])
            probe.flush()
            assert.ok(probe.runs('blocks:activeOrder') >= 1, `${label}: activeOrder positive control`)
            probe.stop()
        }
    }
})

test('during startup the cross-filter blocks react at once to a pin, an archive and an unread flip', () => {
    for (const scope of SCOPES) {
        const context = setup()
        const { state } = context
        startupPhase(state, true)
        const view = mountView(context, scope)
        const probe = watch(context, [view])
        const label = view.label
        // The cross-filter memos are first evaluated here, during startup.
        state.sessions[ID.p3Recent].pinned = 'all'
        probe.flush()
        assert.deepEqual(ids(view.blocks.value.crossFilterPinned), [ID.pinned1, ID.pinned2, ID.p3Recent], `${label}: pin`)
        state.sessions[ID.pinned1].archived = true
        probe.flush()
        assert.deepEqual(ids(view.blocks.value.crossFilterPinned), [ID.pinned2, ID.p3Recent], `${label}: archive`)
        // r7 (p7) is an out-of-scope read root without a process: the change flips it to unread.
        state.sessions.r7.last_new_content_at = laterContent(state.sessions.r7)
        probe.flush()
        assert.ok(ids(view.blocks.value.crossFilterActive).includes('r7'), `${label}: unread flip`)
        // A memo first evaluated during startup still updates after startup.
        startupPhase(state, false)
        probe.flush()
        state.sessions.r7.last_viewed_at = laterContent(state.sessions.r7)
        probe.flush()
        assert.ok(!ids(view.blocks.value.crossFilterActive).includes('r7'), `${label}: flip after startup`)
        probe.stop()
    }
})

test('during startup an archive, a hide, a cutoff crossing, a natural reorder and an extension order or pin change show up at the next progress message', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            let oldId
            const context = setup({ prepare: (state) => { if (tree) oldId = linkOldRoot(state) } })
            const { state } = context
            oldId ??= oldP0Root(state)
            startupPhase(state, true)
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            // After each progress message, the sidebar shows what a reload would.
            const check = (change) => {
                progressMessage(state)
                probe.flush()
                assertSameAsReload(view, state, `${label}: ${change}`)
            }
            const natural = () => ids(view.blocks.value.natural)
            if (tree) assert.deepEqual(ids(view.stages.natural.value.extension).sort(), [ID.ancestor, oldId].sort(), label)
            state.sessions[ID.p0Second].archived = true
            check('archive')
            assert.ok(!natural().includes(ID.p0Second), `${label}: archive shown`)
            state.sessions[ID.p0Recent].hidden = true
            check('hide')
            assert.ok(!natural().includes(ID.p0Recent), `${label}: hide shown`)
            // r0 (p0) is a read natural member: the tick flips it to unread.
            tick(state, 'r0', { top: true })
            check('natural reorder')
            assert.ok(natural().indexOf('r0') < natural().indexOf(ID.child), `${label}: natural reorder shown`)
            if (tree) {
                const before = ids(view.blocks.value.natural)
                assert.ok(before.indexOf(oldId) < before.indexOf(ID.ancestor), label)
                state.sessions[ID.ancestor].mtime = state.sessions[oldId].mtime + 1
                check('extension order')
                const after = ids(view.blocks.value.natural)
                assert.ok(after.indexOf(ID.ancestor) < after.indexOf(oldId), `${label}: extension order shown`)
                state.sessions[oldId].pinned = 'project'
                check('extension pin')
                assert.equal(view.blocks.value.natural[0].id, oldId, `${label}: extension pin shown`)
            }
            state.sessions[oldId].mtime = state.localState.projects[ALL].oldestSessionMtime + 1
            check('cutoff crossing')
            assert.ok(ids(view.stages.natural.value.list.members).includes(oldId), `${label}: cutoff crossing shown`)
            probe.stop()
        }
    }
})

test('block outputs hold raw natural records during startup and reactive records after it', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            startupPhase(state, true)
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            const during = view.blocks.value
            assert.ok(during.natural.length > 0, label)
            assert.ok(during.natural.every(s => !isReactive(s)), `${label}: raw natural during startup`)
            if (tree) {
                assert.ok(view.stages.natural.value.extension.length > 0, label)
                assert.ok(view.stages.natural.value.extension.every(s => !isReactive(s)), `${label}: raw extension`)
            }
            assert.ok([...during.crossFilterPinned, ...during.crossFilterActive].every(isReactive),
                `${label}: the cross-filter blocks keep the reactive records`)
            startupPhase(state, false)
            probe.flush()
            const after = view.blocks.value
            assert.ok(after.natural.every(isReactive), `${label}: reactive natural after startup`)
            assert.ok(view.stages.natural.value.extension.every(isReactive), `${label}: reactive extension after startup`)
            assert.deepEqual(ids(after.natural), ids(during.natural), label)
            probe.stop()
        }
    }
})

test('during startup, writes between two progress messages rerun no natural stage', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            let oldId
            const context = setup({ prepare: (state) => { oldId = linkOldRoot(state) } })
            const { state } = context
            startupPhase(state, true)
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            const label = view.label
            if (tree) assert.equal(view.stages.natural.value.extension.length, 2, label)
            const writes = [
                // Natural roots (p0, above both cutoffs).
                [ID.p0Second, 'archived', true], [ID.p0Recent, 'hidden', true], ['r0', 'mtime', topMtime(state)],
                ['r12', 'pinned', 'project'], ['r12', 'spawned_by', ID.p0Second], ['r12', 'tree_detached', true],
                // Extension roots (below every cutoff, linked to `child`).
                [ID.ancestor, 'mtime', state.sessions[oldId].mtime + 1], [ID.ancestor, 'pinned', 'project'],
                [ID.ancestor, 'tree_detached', true], [ID.ancestor, 'spawned_by', 'r0'],
                [oldId, 'archived', true], [oldId, 'hidden', true],
            ]
            for (const [id, field, value] of writes) {
                state.sessions[id][field] = value
                probe.flush()
                assertNoRuns(probe, NATURAL_STAGES, `${label}: ${id}.${field}`)
            }
            progressMessage(state)
            probe.flush()
            assert.ok(probe.runs('blocks:natural') >= 1, `${label}: progress message positive control`)
            // The writes changed the natural members, so the order stage reruns in both modes.
            assert.ok(probe.runs('blocks:naturalOrder') >= 1, `${label}: naturalOrder positive control`)
            if (tree) assert.ok(probe.runs('blocks:extension') >= 1, `${label}: extension positive control`)
            // The progress message shows every write at once, as a reload would.
            assertSameAsReload(view, state, label)
            const natural = ids(view.blocks.value.natural)
            for (const gone of [ID.p0Second, ID.p0Recent]) assert.ok(!natural.includes(gone), `${label}: ${gone} gone`)
            probe.stop()
        }
    }
})

test('in tree mode an mtime tick without a process never reruns extendTreeCandidates', () => {
    for (const scope of SCOPES) {
        // Own setup: p0-second is made unread (no mtime change), so its ticks do not flip.
        const context = setup({ prepare: (state) => makeUnread(state, ID.p0Second) })
        const { state } = context
        const view = mountView(context, scope, { showAsTree: true })
        const probe = watch(context, [view])
        const label = view.label
        assert.ok(view.stages.natural.value.extension.length > 0, label)
        const order = () => ids(view.blocks.value.natural)
        const ticks = [
            [ID.p0Second, {}, false, 'no flip, no order change'],
            [ID.p0Second, { top: true }, true, 'no flip, order change'],
            // p0-recent and r0 are read natural members above both cutoffs: their ticks flip.
            [ID.p0Recent, {}, false, 'flip, no order change'],
            ['r0', { top: true }, true, 'flip, order change'],
        ]
        for (const [id, options, reorders, change] of ticks) {
            const before = order()
            tick(state, id, options)
            probe.flush()
            assert.equal(order().join() !== before.join(), reorders, `${label}: ${change}`)
            assertNoRuns(probe, ['blocks:extension', 'blocks:natural'], `${label}: ${change}`)
        }
        assertStagesCanRun(context, probe, blockStages(true), label)
        probe.stop()
    }
})

test('a subagent add or removal reruns no block stage; an unreadContent flip reads no subagent field', () => {
    for (const scope of SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view], { unread: true })
            const label = view.label
            applyStep(state, stepNamed('subagent add'))
            probe.flush()
            applyStep(state, stepNamed('subagent removal'))
            probe.flush()
            assert.ok(probe.runs('rootSessions') >= 2, `${label}: the root index reran`)
            assertNoRuns(probe, [...BLOCK_STAGES, 'unread:counts'], label)
            // Counting getters on the fields an unread check would read first for a subagent.
            let reads = 0
            for (const session of Object.values(toRaw(state.sessions))) {
                if (!session.parent_session_id) continue
                for (const field of ['parent_session_id', 'hidden', 'pinned']) {
                    let value = session[field]
                    Object.defineProperty(session, field, {
                        configurable: true, enumerable: true,
                        get() { reads++; return value },
                        set(next) { value = next },
                    })
                }
            }
            probe.reset()
            // p3-recent is an out-of-scope read root without a process: the change flips it to unread.
            state.sessions[ID.p3Recent].last_new_content_at = laterContent(state.sessions[ID.p3Recent])
            probe.flush()
            assert.ok(probe.runs('blocks:crossFilter') >= 1, label)
            assert.ok(ids(view.blocks.value.crossFilterActive).includes(ID.p3Recent), label)
            assert.equal(reads, 0, `${label}: no subagent field read`)
            assertStagesCanRun(context, probe, [...blockStages(tree), 'unread:counts'], label)
            probe.stop()
        }
    }
})

test('every fixed stage name runs at least once along the shared update sequence', () => {
    const context = setup()
    const { state, workspaces } = context
    const views = SCOPES.flatMap(scope => [false, true].map(tree => mountView(context, scope, { showAsTree: tree })))
    const allProjectIds = computed(() => workspaces.getAllProjectIds('w'))
    const probe = watch(context, views, { unread: true, extra: () => allProjectIds.value })
    for (const step of FIXTURE_UPDATE_SEQUENCE) {
        applyStep(state, step)
        probe.flush()
    }
    const seen = probe.seen()
    for (const name of ALL_STAGE_NAMES) assert.ok(seen.has(name), `${name} ran`)
    assert.deepEqual([...seen].filter(name => !ALL_STAGE_NAMES.includes(name)), [], 'no unknown stage name')
    probe.stop()
})

test('the natural order never holds a list member twice, even if the extension held one', () => {
    for (const scope of SCOPES) {
        const context = setup()
        const options = blockOptions(context, scope, { showAsTree: true })
        const { list, natural, naturalIds, extension } = computeSidebarNaturalMembership(options)
        assert.ok(extension.length > 0, scope)
        const expected = new Set([...ids(natural), ...ids(extension)])
        // The invariant holds today: the extension holds no member of the list.
        assert.ok(!ids(extension).some(id => ids(list.ordered).includes(id)), scope)
        assert.deepEqual(new Set(ids(orderSidebarNatural(options, list, naturalIds, extension))), expected, scope)
        // An extension that broke it (a natural member added to it) still gives each session once.
        const order = ids(orderSidebarNatural(options, list, naturalIds, [...extension, natural[0]]))
        assert.equal(order.length, expected.size, scope)
        assert.deepEqual(new Set(order), expected, scope)
    }
})

// ---------------------------------------------------------------------------
// What each update shows, and live equals reload (7.1 #9)

/** The first natural session without a process, a pin or an ephemeral run: the top of the date-sorted part. */
const firstQuiet = (view, state) => view.blocks.value.natural
    .find(s => !s.pinned && !s.ephemeral && state.processStates[s.id] == null)?.id

/**
 * What each step of the update sequence shows in the flat views p0, ws:w and all
 * projects, mounted together on one state from the start (`showActiveAcrossFilters`
 * on, archived hidden). `see({ p0, w, all }, label)` gets the three views'
 * `{ natural, pinned, active, quiet }`: the natural ids, the "Pinned elsewhere" and
 * "Active elsewhere" ids, and `firstQuiet`.
 */
const STEP_EFFECTS = {
    'in-scope tick without order change': (v, before) => {
        for (const scope of ['p0', 'w', 'all']) assert.deepEqual(v[scope].natural, before[scope].natural, scope)
    },
    'in-scope tick with order change': (v) => {
        for (const scope of ['p0', 'w', 'all']) assert.equal(v[scope].quiet, ID.p0Recent, scope)
    },
    'out-of-scope tick without order change': (v, before) => {
        for (const scope of ['p0', 'w']) assert.deepEqual(v[scope].active, [ID.unread1, ID.unread2], scope)
        assert.deepEqual(v.all.natural, before.all.natural)
    },
    'out-of-scope tick with order change': (v) => {
        for (const scope of ['p0', 'w']) assert.deepEqual(v[scope].active, [ID.unread2, ID.unread1], scope)
        assert.equal(v.all.quiet, ID.unread2)
    },
    'unread flip to unread on a user_turn session with background work': (v, before) => {
        for (const scope of ['p0', 'w', 'all']) assert.deepEqual(v[scope], before[scope], scope)
    },
    'unread flip to read': (v) => {
        for (const scope of ['p0', 'w']) assert.deepEqual(v[scope].active, [ID.unread2], scope)
    },
    'pin change': (v) => {
        for (const scope of ['p0', 'w']) assert.deepEqual(v[scope].pinned, [ID.pinned1, ID.pinned2, ID.p3Recent], scope)
        // All projects: no cross-filter block; the pinned roots lead the natural block, after the ephemeral one.
        assert.deepEqual(v.all.natural.slice(0, 4), [ID.ephemeral, ID.pinned1, ID.pinned2, ID.p3Recent])
    },
    'archive': (v) => {
        for (const scope of ['p0', 'w', 'all']) assert.ok(!v[scope].natural.includes(ID.p0Second), scope)
    },
    'hide': (v) => {
        for (const scope of ['w', 'all']) assert.ok(!v[scope].natural.includes(ID.p1Recent), scope)
    },
    'cutoff crossing': (v) => {
        // old-p2-wt1 crosses the all-projects cutoff with a real batch: it turns unread.
        assert.ok(v.all.natural.includes(ID.oldWorktree))
        for (const scope of ['p0', 'w']) assert.ok(v[scope].active.includes(ID.oldWorktree), scope)
    },
    'root add': (v) => {
        for (const scope of ['p0', 'w', 'all']) assert.equal(v[scope].quiet, 'added-root', scope)
    },
    'root removal': (v) => {
        for (const scope of ['p0', 'w', 'all']) assert.ok(!v[scope].natural.includes(ID.p0Recent), scope)
    },
    'subagent add': (v, before) => {
        for (const scope of ['p0', 'w', 'all']) assert.deepEqual(v[scope], before[scope], scope)
    },
    'subagent removal': (v, before) => {
        for (const scope of ['p0', 'w', 'all']) assert.deepEqual(v[scope], before[scope], scope)
    },
    'workspace member archived': (v, before, state) => {
        const inP1 = (id) => state.sessions[id]?.project_id === 'p1'
        assert.ok(before.w.natural.some(inP1), 'w showed p1 sessions')
        for (const scope of ['w', 'all']) assert.ok(!v[scope].natural.some(inP1), scope)
        assert.deepEqual(v.p0, before.p0)
    },
    'worktree added to p2': (v) => {
        assert.ok(v.all.natural.includes('p2-wt3-root'))
        for (const scope of ['p0', 'w']) assert.ok(!v[scope].natural.includes('p2-wt3-root'), scope)
    },
    'worktree added to p0': (v, before) => {
        for (const scope of ['p0', 'w', 'all']) assert.ok(v[scope].natural.includes('p0-wt1-root'), scope)
        // p0 is now a main repository with a worktree: its view uses the all-projects cursor, so cut-p0 leaves it.
        assert.ok(before.p0.natural.includes(ID.cutP0) && !v.p0.natural.includes(ID.cutP0))
    },
    'workspace gains a project': (v, before) => {
        assert.ok(v.w.natural.includes('p2-wt3-root'), 'w shows the p2 worktree sessions')
        assert.ok(v.w.natural.includes(ID.cutP2) === false, 'cut-p2 stays under the all-projects cutoff')
        assert.deepEqual(v.p0, before.p0)
    },
    'workspace loses a project': (v, before, state) => {
        const inP2 = (id) => state.sessions[id]?.project_id.startsWith('p2')
        assert.ok(!v.w.natural.some(inP2))
    },
    'process start': (v, before, state) => {
        // unread-1 runs: still "Active elsewhere" (a process), and in the process part of all projects.
        for (const scope of ['p0', 'w']) assert.ok(v[scope].active.includes(ID.unread1), scope)
        assert.ok(v.all.natural.indexOf(ID.unread1) < v.all.natural.indexOf(v.all.quiet))
        assert.ok(state.processStates[ID.unread1])
    },
    'process stop': (v) => {
        for (const scope of ['p0', 'w']) assert.deepEqual(v[scope].natural.slice(0, 2), [ID.processAssistant, ID.processShell], scope)
        for (const scope of ['p0', 'w']) assert.notEqual(v[scope].natural[2], ID.processCron, scope)
    },
    // The startup write hides cut-p0, which none of these views shows any more (see the p0 worktree step);
    // the startup tests above cover what a progress message shows.
    'startup phase with one untracked write': (v, before) => assert.deepEqual(v, before),
    'progress message': (v, before) => assert.deepEqual(v, before),
    'end of startup': (v, before) => assert.deepEqual(v, before),
}

test('each step of the update sequence shows its effect in the p0, ws:w and all-projects views', () => {
    assert.deepEqual(Object.keys(STEP_EFFECTS), FIXTURE_UPDATE_SEQUENCE.map(step => step.name))
    const context = setup()
    const { state } = context
    const views = { p0: mountView(context, 'p0'), w: mountView(context, 'ws:w'), all: mountView(context, ALL) }
    const probe = watch(context, Object.values(views))
    const see = () => Object.fromEntries(Object.entries(views).map(([scope, view]) => [scope, {
        natural: ids(view.blocks.value.natural), pinned: ids(view.blocks.value.crossFilterPinned),
        active: ids(view.blocks.value.crossFilterActive), quiet: firstQuiet(view, state),
    }]))
    for (const step of FIXTURE_UPDATE_SEQUENCE) {
        const before = see()
        applyStep(state, step)
        probe.flush()
        STEP_EFFECTS[step.name](see(), before, state)
    }
    probe.stop()
})

/**
 * Own setup of the workspace-pin variant: two read roots pinned `workspace`,
 * one in p1 (a member of w) and one in p8 (outside w). They are added here, not
 * in the shared fixture, so the counts of the other tests do not change.
 */
const WORKSPACE_PINNED = { member: 'ws-pinned-p1', outside: 'ws-pinned-p8' }
function addWorkspacePinnedRoots(state) {
    addRoot(state, WORKSPACE_PINNED.member, 'p1', { pinned: 'workspace' })
    addRoot(state, WORKSPACE_PINNED.outside, 'p8', { pinned: 'workspace' })
}

const VIEW_VARIANTS = [
    { name: 'default', options: { sessionId: null, showArchivedProjects: false, showArchived: false } },
    { name: 'selected, archived shown', options: { sessionId: ID.p3Recent, showArchivedProjects: true, showArchived: true } },
    // The active workspace w in every scope, and no "Active elsewhere" block.
    { name: 'workspace pins', prepare: addWorkspacePinnedRoots, options: { sessionId: null, showArchivedProjects: false,
        showArchived: false, activeWorkspaceId: 'w', showActiveAcrossFilters: false } },
]

test('the live blocks equal those of a reload in every fixture scope, flat and tree, along the update sequence', () => {
    for (const scope of FIXTURE_SCOPES) {
        for (const tree of [false, true]) {
            for (const variant of VIEW_VARIANTS) {
                const context = setup({ prepare: (state) => {
                    state.settings.isShowArchivedProjects = variant.options.showArchivedProjects
                    variant.prepare?.(state)
                } })
                const { state } = context
                const view = mountView(context, scope, { showAsTree: tree, ...variant.options })
                const probe = watch(context, [view])
                const compare = (step) => {
                    const label = `${view.label} ${variant.name}: ${step.name}`
                    assertSameAsReload(view, state, label)
                    // The one-shot wrapper gives the same blocks.
                    assertSameBlocks(computeSidebarSessionBlocks(view.options), view.blocks.value, `${label} (one-shot)`)
                }
                if (variant.prepare && ['p0', 'p2', 'ws:w1'].includes(scope)) {
                    // The variant reaches the workspace-pin path: only the member's pin is "Pinned elsewhere".
                    const pinned = ids(view.blocks.value.crossFilterPinned)
                    assert.ok(pinned.includes(WORKSPACE_PINNED.member), `${view.label} ${variant.name}: member pin`)
                    assert.ok(!pinned.includes(WORKSPACE_PINNED.outside), `${view.label} ${variant.name}: outside pin`)
                    assert.deepEqual(view.blocks.value.crossFilterActive, [], `${view.label} ${variant.name}: no active block`)
                }
                compare({ name: 'initial', startup: null })
                for (const step of FIXTURE_UPDATE_SEQUENCE) {
                    applyStep(state, step)
                    probe.flush()
                    // Between a startup write and the next progress message, the sidebar may lag (5.3).
                    if (step.startup !== 'write') compare(step)
                }
                probe.stop()
            }
        }
    }
})

test('during startup each progress message shows what a reload would, and so does the end of startup', () => {
    for (const scope of FIXTURE_SCOPES) {
        for (const tree of [false, true]) {
            const context = setup()
            const { state } = context
            startupPhase(state, true)
            const view = mountView(context, scope, { showAsTree: tree })
            const probe = watch(context, [view])
            for (const step of FIXTURE_UPDATE_SEQUENCE.filter(step => !step.startup)) {
                applyStep(state, step)
                progressMessage(state)
                probe.flush()
                assertSameAsReload(view, state, `${view.label}: ${step.name}`)
            }
            startupPhase(state, false)
            probe.flush()
            assertSameAsReload(view, state, `${view.label}: end`)
            probe.stop()
        }
    }
})

// ---------------------------------------------------------------------------
// Review Focus

test('a workspace whose member gets archived switches to the new scope list', () => {
    const context = setup()
    const { state, data, workspaces } = context
    const view = mountView(context, 'ws:w')
    const probe = watch(context, [view])
    const before = view.stages.natural.value.list
    assert.ok(view.blocks.value.natural.some(s => s.project_id === 'p1'))
    state.projects.p1.archived = true
    probe.flush()
    const list = view.stages.natural.value.list
    assert.notStrictEqual(list, before, 'the natural stage switched lists')
    const scope = resolveSessionScope({ effectiveProjectId: 'ws:w',
        getProjectScopeIds: data.getProjectScopeIds, getVisibleProjectIds: workspaces.getVisibleProjectIds })
    assert.deepEqual(scope.projectIds, ['p0'])
    assert.strictEqual(list, data.getScopeSessionList(scope))
    assert.ok(!view.blocks.value.natural.some(s => s.project_id === 'p1'))
    assertSameAsReload(view, state, 'ws:w')
    probe.stop()
})

test('a worktree added to a workspace member brings its sessions into the ws:w natural block', () => {
    const context = setup()
    const { state } = context
    const view = mountView(context, 'ws:w')
    const probe = watch(context, [view])
    const label = view.label
    // Read roots of the future worktree, not hidden, above the `__all__` cutoff.
    addRoot(state, 'wt-root-1', 'p0-wt')
    addRoot(state, 'wt-root-2', 'p0-wt')
    probe.flush()
    assert.ok(state.sessions['wt-root-2'].mtime > state.localState.projects[ALL].oldestSessionMtime)
    assert.ok(!view.blocks.value.natural.some(s => s.project_id === 'p0-wt'), `${label}: not in scope yet`)
    state.projects['p0-wt'] = { id: 'p0-wt', name: 'Project p0-wt', directory: '/fixture/p0-wt',
        mtime: topMtime(state), archived: false, worktree_of: 'p0' }
    probe.flush()
    assert.deepEqual(ids(view.blocks.value.natural.filter(s => s.project_id === 'p0-wt')), ['wt-root-2', 'wt-root-1'], label)
    assertSameAsReload(view, state, label)
    probe.stop()
})

test('a removed session leaves Active elsewhere at once', () => {
    for (const scope of SCOPES) {
        const context = setup()
        const { state } = context
        const view = mountView(context, scope)
        const probe = watch(context, [view])
        assert.deepEqual(ids(view.blocks.value.crossFilterActive), [ID.unread1, ID.unread2], view.label)
        delete state.sessions[ID.unread1]
        probe.flush()
        assert.deepEqual(ids(view.blocks.value.crossFilterActive), [ID.unread2], view.label)
        probe.stop()
    }
})

test('a selected subagent is never extra', () => {
    for (const scope of SCOPES) {
        const context = setup()
        const { state } = context
        for (const sessionId of [`${ID.p3Recent}-sa0`, `${ID.p0Recent}-sa0`]) {
            assert.ok(state.sessions[sessionId].parent_session_id, sessionId)
            const view = mountView(context, scope, { sessionId })
            assert.equal(view.blocks.value.extra, null, `${view.label}: ${sessionId}`)
        }
        // Positive control: the out-of-scope root itself is extra.
        const view = mountView(context, scope, { sessionId: ID.p3Recent })
        assert.equal(view.blocks.value.extra?.id, ID.p3Recent, view.label)
    }
})

test('drafts and ephemeral roots keep their place and stay out of Active elsewhere', () => {
    const prepare = (state) => {
        // Own setup: unread content on the draft and the ephemeral root (both p3), and a process on the ephemeral root.
        state.sessions[ID.draft].last_new_content_at = iso(topMtime(state))
        state.sessions[ID.ephemeral].last_new_content_at = iso(topMtime(state))
        state.processStates[ID.ephemeral] = { session_id: ID.ephemeral, project_id: 'p3', state: 'assistant_turn',
            started_at: 500, pending_requests: [] }
    }
    for (const tree of [false, true]) {
        const context = setup({ prepare })
        const { state } = context
        assert.ok(hasUnreadContent(state.sessions[ID.draft]))
        assert.ok(hasUnreadContent(state.sessions[ID.ephemeral]))
        const p3 = mountView(context, 'p3', { showAsTree: tree })
        const natural = ids(p3.blocks.value.natural)
        // The launched ephemeral root leads; the draft keeps its mtime place (the second most recent root).
        assert.deepEqual(natural.slice(0, 3), [ID.ephemeral, ID.draft, ID.p3Recent], p3.label)
        for (const scope of SCOPES) {
            const view = mountView(context, scope, { showAsTree: tree })
            const active = ids(view.blocks.value.crossFilterActive)
            assert.ok(!active.includes(ID.draft) && !active.includes(ID.ephemeral), view.label)
            // The rest of "Active elsewhere" is unchanged: the two unread roots of the fixture.
            assert.deepEqual(active, [ID.unread1, ID.unread2], view.label)
        }
    }
})
