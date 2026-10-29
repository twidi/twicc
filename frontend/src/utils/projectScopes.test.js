import test from 'node:test'
import assert from 'node:assert/strict'
import { effect, reactive, toRaw } from 'vue'
import { createWorkspaceMembership, getArchivedProjectIds, getWorktreeIdsByParent } from './projectScopes.js'
import { observeStageRuns } from './stageRuns.js'
import { buildSidebarState, createFixtureStores } from '../../tests/helpers/sidebarFixture.js'

// Activity-free project inputs (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md,
// 5.6 and 7.1 #3). The probes count reads of the project `mtime`: any activity sort
// reads it first, so a membership that sorted by activity would fail them.

/** Install a counting getter on `mtime` of every project record (present ones only). */
function countMtimeReads(state) {
    let reads = 0
    for (const project of Object.values(toRaw(state.projects))) {
        let value = project.mtime
        Object.defineProperty(project, 'mtime', {
            configurable: true, enumerable: true,
            get() { reads++; return value },
            set(next) { value = next },
        })
    }
    return { reads: () => reads, reset() { reads = 0 } }
}

/** Keep `read()` subscribed in an effect; count its evaluations and the stage runs per name. */
function subscribe(read) {
    let value
    let evaluations = 0
    const runs = {}
    const stopObserving = observeStageRuns(name => { runs[name] = (runs[name] || 0) + 1 })
    const runner = effect(() => {
        evaluations++
        value = read()
    })
    return {
        value: () => value,
        evaluations: () => evaluations,
        runs: (name) => runs[name] || 0,
        reset() {
            evaluations = 0
            for (const name of Object.keys(runs)) delete runs[name]
        },
        stop() { stopObserving(); runner.effect.stop() },
    }
}

/** A project record of the fixture shape. */
function project(id, mtime, worktreeOf = null) {
    return { id, name: `Project ${id}`, directory: `/fixture/${id}`, mtime, archived: false, worktree_of: worktreeOf }
}

/** A membership over a state and a workspace list (each call reads them live). */
function membershipOver(state, workspaces, isShowArchived) {
    return createWorkspaceMembership({
        getWorkspaceById: (id) => workspaces.find(w => w.id === id) || null,
        getProject: (id) => state.projects[id],
        getWorktreeIdsByParent: () => getWorktreeIdsByParent(state),
        isShowArchived,
    })
}

test('archivedProjectIds reads no mtime and keeps its instance on an activity change', () => {
    const state = buildSidebarState({ roots: 18 })
    // Own setup: two archived projects, a plain one and a worktree.
    state.projects.p1.archived = true
    state.projects['p2-wt1'].archived = true
    const mtimes = countMtimeReads(state)
    const probe = subscribe(() => getArchivedProjectIds(state))
    const archived = probe.value()
    assert.deepEqual([...archived].sort(), ['p1', 'p2-wt1'])
    assert.equal(mtimes.reads(), 0)
    assert.equal(probe.evaluations(), 1)

    state.projects.p0.mtime += 10_000
    state.projects['p2-wt1'].mtime += 10_000
    mtimes.reset()
    assert.strictEqual(getArchivedProjectIds(state), archived)
    assert.equal(probe.evaluations(), 1)
    assert.equal(mtimes.reads(), 0)

    // A new project that is not archived changes the keys, not the set: the subscriber does not rerun.
    state.projects.p10 = project('p10', 1)
    assert.strictEqual(getArchivedProjectIds(state), archived)
    assert.equal(probe.evaluations(), 1)

    // Positive control: an archive change gives a new set at once.
    state.projects.p3.archived = true
    assert.equal(probe.evaluations(), 2)
    assert.notStrictEqual(probe.value(), archived)
    assert.deepEqual([...probe.value()].sort(), ['p1', 'p2-wt1', 'p3'])
    assert.equal(mtimes.reads(), 0)
    probe.stop()
})

test('the worktree index ignores activity and lists worktrees in id order', () => {
    const state = buildSidebarState({ roots: 18 })
    // Own setup: the p2 worktrees in activity order are p2-wt2, p2-wt1 (the fixture
    // makes p2-wt2 the more recent); a worktree of p0 holds a second parent entry.
    state.projects['p0-wt1'] = project('p0-wt1', 1, 'p0')
    const { data } = createFixtureStores(state)
    assert.deepEqual(data.getWorktreesOf('p2').map(p => p.id), ['p2-wt2', 'p2-wt1'])

    const mtimes = countMtimeReads(state)
    const probe = subscribe(() => getWorktreeIdsByParent(state))
    const index = probe.value()
    assert.deepEqual([...index.entries()].sort(), [['p0', ['p0-wt1']], ['p2', ['p2-wt1', 'p2-wt2']]])
    assert.equal(mtimes.reads(), 0)

    // p2-wt1 becomes the most recent worktree: the activity order flips, the index does not change.
    state.projects['p2-wt1'].mtime += 10_000
    state.projects.p0.mtime += 10_000
    mtimes.reset()
    assert.strictEqual(getWorktreeIdsByParent(state), index)
    assert.equal(probe.evaluations(), 1)
    assert.equal(mtimes.reads(), 0)

    // Positive control: a new p2 worktree (id before the others) joins in id order at once;
    // the unchanged p0 array keeps its identity.
    const p0Worktrees = index.get('p0')
    state.projects['p2-wt0'] = project('p2-wt0', 2, 'p2')
    assert.equal(probe.evaluations(), 2)
    assert.notStrictEqual(probe.value(), index)
    assert.deepEqual(probe.value().get('p2'), ['p2-wt0', 'p2-wt1', 'p2-wt2'])
    assert.strictEqual(probe.value().get('p0'), p0Worktrees)
    assert.equal(mtimes.reads(), 0)
    probe.stop()
})

test('visible and all modes: members in workspace order, each followed by its worktrees in id order', () => {
    const state = buildSidebarState({ roots: 18 })
    // Own setup: p1 is an archived member, p2-wt1 an archived worktree of the member p2
    // (whose worktree ids are not in activity order), ghost an unknown member, p2-wt2 a
    // worktree that is also a member, p9 a member that is also listed twice.
    state.projects.p1.archived = true
    state.projects['p2-wt1'].archived = true
    const workspaces = [
        { id: 'x', projectIds: ['p3', 'ghost', 'p2', 'p1', 'p2-wt2', 'p9', 'p9'] },
        { id: 'only-worktree', projectIds: ['p2-wt1', 'p2'] },
        { id: 'empty', projectIds: [] },
    ]
    const settings = reactive({ showArchived: false })
    const membership = membershipOver(state, workspaces, () => settings.showArchived)
    // `all` lists every member and worktree once, whatever exists or is archived: it never changes here.
    const all = { x: ['p3', 'ghost', 'p2', 'p2-wt1', 'p2-wt2', 'p1', 'p9'], 'only-worktree': ['p2-wt1', 'p2', 'p2-wt2'],
        empty: [], unknown: [] }
    // `visible` skips unknown projects, and archived ones unless shown; an archived member takes its worktrees along.
    const check = (label, visible) => {
        for (const wsId of ['x', 'only-worktree', 'empty', 'unknown']) {
            assert.deepEqual(membership.visible(wsId), visible[wsId] ?? [], `${label} ${wsId} visible`)
            assert.deepEqual(membership.all(wsId), all[wsId], `${label} ${wsId} all`)
        }
    }

    check('show archived off', { x: ['p3', 'p2', 'p2-wt2', 'p9'], 'only-worktree': ['p2', 'p2-wt2'] })
    settings.showArchived = true
    const shown = { x: ['p3', 'p2', 'p2-wt1', 'p2-wt2', 'p1', 'p9'], 'only-worktree': ['p2-wt1', 'p2', 'p2-wt2'] }
    check('show archived on', shown)
    // An archived member with worktrees: off, its worktrees go too; on, they come back.
    state.projects.p2.archived = true
    check('archived parent, show archived on', shown)
    settings.showArchived = false
    check('archived parent, show archived off', { x: ['p3', 'p2-wt2', 'p9'], 'only-worktree': [] })
})

test('a project mtime change reruns no membership evaluation', () => {
    const state = buildSidebarState({ roots: 18 })
    // Own setup: w = {p0, p1} gets a worktree of p0, which an activity sort would order by mtime.
    state.projects['p0-wt1'] = project('p0-wt1', 1, 'p0')
    const { workspaces } = createFixtureStores(state)
    const mtimes = countMtimeReads(state)
    const probe = subscribe(() => [workspaces.getVisibleProjectIds('w'), workspaces.getAllProjectIds('w')])
    const [visible, all] = probe.value()
    assert.deepEqual(visible, ['p0', 'p0-wt1', 'p1'])
    assert.deepEqual(all, ['p0', 'p0-wt1', 'p1'])
    assert.equal(mtimes.reads(), 0)
    probe.reset()

    for (const id of ['p0', 'p0-wt1', 'p1', 'p9']) state.projects[id].mtime += 10_000
    mtimes.reset()
    assert.equal(probe.evaluations(), 0)
    assert.equal(probe.runs('workspace:visible'), 0)
    assert.equal(probe.runs('workspace:all'), 0)
    assert.strictEqual(workspaces.getVisibleProjectIds('w'), visible)
    assert.strictEqual(workspaces.getAllProjectIds('w'), all)
    assert.equal(mtimes.reads(), 0)

    // Positive control: a member archive reruns the visible stage (not the all stage).
    state.projects.p1.archived = true
    assert.equal(probe.evaluations(), 1)
    assert.equal(probe.runs('workspace:visible'), 1)
    assert.equal(probe.runs('workspace:all'), 0)
    assert.deepEqual(probe.value()[0], ['p0', 'p0-wt1'])
    assert.strictEqual(probe.value()[1], all)
    // Positive control of the zero 'workspace:all' runs above: a new worktree of a member reruns it.
    state.projects['p0-wt2'] = project('p0-wt2', 1, 'p0')
    assert.deepEqual(probe.value()[1], ['p0', 'p0-wt1', 'p0-wt2', 'p1'])
    assert.ok(probe.runs('workspace:all') >= 1)
    probe.stop()
})

test('archiving a member changes the visible ids at once', () => {
    const state = buildSidebarState({ roots: 18 })
    const { workspaces } = createFixtureStores(state)
    const probe = subscribe(() => workspaces.getVisibleProjectIds('w'))
    assert.deepEqual(probe.value(), ['p0', 'p1'])
    const visible = probe.value()
    probe.reset()

    // No member is archived: the setting reruns the stage, the array stays.
    state.settings.isShowArchivedProjects = true
    assert.equal(probe.runs('workspace:visible'), 1)
    assert.equal(probe.evaluations(), 0)
    assert.strictEqual(workspaces.getVisibleProjectIds('w'), visible)
    state.settings.isShowArchivedProjects = false
    probe.reset()

    state.projects.p1.archived = true
    assert.deepEqual(probe.value(), ['p0'])
    assert.equal(probe.evaluations(), 1)
    assert.equal(probe.runs('workspace:visible'), 1)
    state.settings.isShowArchivedProjects = true
    assert.deepEqual(probe.value(), ['p0', 'p1'])
    state.settings.isShowArchivedProjects = false
    assert.deepEqual(probe.value(), ['p0'])
    state.projects.p1.archived = false
    assert.deepEqual(probe.value(), ['p0', 'p1'])
    assert.equal(probe.evaluations(), 4)
    probe.stop()
})

test('a worktree added to a member appears in the visible ids at once', () => {
    const state = buildSidebarState({ roots: 18 })
    const { workspaces } = createFixtureStores(state)
    const probe = subscribe(() => [workspaces.getVisibleProjectIds('w'), workspaces.getAllProjectIds('w')])
    assert.deepEqual(probe.value(), [['p0', 'p1'], ['p0', 'p1']])
    const [visible, all] = probe.value()
    probe.reset()

    // A worktree of a non-member reruns both stages (the index changed) but keeps both
    // arrays, so the subscriber does not rerun.
    state.projects['p2-wt3'] = project('p2-wt3', 1, 'p2')
    assert.equal(probe.runs('workspace:visible'), 1)
    assert.equal(probe.runs('workspace:all'), 1)
    assert.equal(probe.evaluations(), 0)
    assert.strictEqual(workspaces.getVisibleProjectIds('w'), visible)
    assert.strictEqual(workspaces.getAllProjectIds('w'), all)
    probe.reset()

    state.projects['p0-wt1'] = project('p0-wt1', 1, 'p0')
    assert.deepEqual(probe.value(), [['p0', 'p0-wt1', 'p1'], ['p0', 'p0-wt1', 'p1']])
    assert.equal(probe.evaluations(), 1)
    assert.equal(probe.runs('workspace:visible'), 1)
    assert.equal(probe.runs('workspace:all'), 1)
    probe.stop()
})
