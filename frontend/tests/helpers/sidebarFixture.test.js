import test from 'node:test'
import assert from 'node:assert/strict'
import { isReactive, toRaw } from 'vue'
import { computeSidebarSessionBlocks } from './sidebarBaseline/sidebarSessions.js'
import { computeSidebarSessionBlocks as computeShippedBlocks } from '../../src/utils/sidebarSessions.js'
import { createBaselineActivityIndex } from './sidebarBaseline/projectProcessActivity.js'
import { hasUnreadContent, isSessionUnread } from '../../src/utils/sessions.js'
import { hasActiveStartupPhase } from '../../src/utils/startupPhase.js'
import { FIXTURE_IDS as ID, FIXTURE_SCOPES, FIXTURE_UPDATE_SEQUENCE, applyStep, buildSidebarState,
    createBaselineData, createShippedData, progressMessage, startupPhase, tick } from './sidebarFixture.js'

const blocksOf = (data, workspaces, effectiveProjectId, extra = {}, compute = computeSidebarSessionBlocks) => compute({
    data, workspaces, effectiveProjectId, activeWorkspaceId: null, sessionId: null, showArchived: false,
    showArchivedProjects: false, showActiveAcrossFilters: true, ...extra })
const ids = (sessions) => sessions.map(s => s.id)
const sorted = (sessions) => ids(sessions).sort()

test('baseline facade gives non-empty root-only blocks in every fixture scope', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    for (const effectiveProjectId of FIXTURE_SCOPES) {
        const blocks = computeSidebarSessionBlocks({ data, workspaces, effectiveProjectId, activeWorkspaceId: null,
            sessionId: null, showArchived: false, showArchivedProjects: false, showActiveAcrossFilters: true })
        assert.ok(blocks.natural.length > 0, String(effectiveProjectId))
        assert.ok(blocks.natural.every(s => !s.parent_session_id))
    }
})

test('the fixture is deterministic and holds the requested store size', () => {
    const first = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const second = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    assert.deepEqual(toRaw(first), toRaw(second))
    assert.ok(isReactive(first))
    assert.equal(Object.keys(first.sessions).length, 60 * 3 - 2 * 2)  // no subagents on the draft and the ephemeral root
    assert.deepEqual(Object.keys(first.processStates).sort(), [ID.processAssistant, ID.processCron, ID.processShell])
    assert.equal(first.settings.isShowArchivedProjects, false)
    for (const scopeId of ['__all__', 'p0', 'p2', 'ws:w', 'ws:w1']) {
        assert.equal(first.localState.projects[scopeId].hasMoreSessions, true, scopeId)
    }
    const cursors = Object.values(first.localState.projects).map(entry => entry.oldestSessionMtime)
    assert.equal(new Set(cursors).size, cursors.length)
    const noCutoff = buildSidebarState({ roots: 60 })
    assert.ok(Object.values(noCutoff.localState.projects).every(entry => !entry.hasMoreSessions))
})

test('Active elsewhere and Pinned elsewhere hold exactly the placed sessions', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    for (const scope of ['p0', 'ws:w', 'ws:w1']) {
        const blocks = blocksOf(data, workspaces, scope)
        assert.deepEqual(sorted(blocks.crossFilterActive), [ID.unread1, ID.unread2], scope)
        assert.deepEqual(sorted(blocks.crossFilterPinned), [ID.pinned1, ID.pinned2], scope)
    }
    const p2 = blocksOf(data, workspaces, 'p2')
    assert.deepEqual(sorted(p2.crossFilterActive),
        [ID.processAssistant, ID.processCron, ID.processShell, ID.unread1, ID.unread2].sort())
    assert.deepEqual(sorted(p2.crossFilterPinned), [ID.pinned1, ID.pinned2])
    for (const scope of ['__all__', null]) {
        const blocks = blocksOf(data, workspaces, scope)
        assert.deepEqual(blocks.crossFilterActive, [], String(scope))
        assert.deepEqual(blocks.crossFilterPinned, [], String(scope))
    }
})

test('cutoff placements make single-project and multi-project lists differ', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    const natural = (scope) => ids(blocksOf(data, workspaces, scope).natural)
    assert.ok(natural('p0').includes(ID.cutP0))
    for (const scope of ['ws:w', 'ws:w1', '__all__']) assert.ok(!natural(scope).includes(ID.cutP0), scope)
    assert.ok(!natural('p2').includes(ID.cutP2))
    assert.ok(!natural('p2').includes(ID.oldWorktree))
    assert.ok(natural('p2-wt1').includes(ID.oldWorktree))
    // The cursors are ordered as the fixture paragraph says.
    const cursor = (scope) => state.localState.projects[scope].oldestSessionMtime
    assert.ok(cursor('p0') < state.sessions[ID.cutP0].mtime && state.sessions[ID.cutP0].mtime < cursor('__all__'))
    assert.ok(cursor('p2') < state.sessions[ID.cutP2].mtime && state.sessions[ID.cutP2].mtime < cursor('__all__'))
    for (const id of [ID.processAssistant, ID.processShell, ID.processCron]) {
        assert.ok(state.sessions[id].mtime >= cursor('__all__') && state.sessions[id].mtime >= cursor('p0'), id)
        assert.ok(!isSessionUnread(state.sessions[id], state.processStates[id]), id)
    }
    for (const scope of ['p0', 'ws:w', 'ws:w1']) assert.ok(natural(scope).includes(ID.processAssistant), scope)
})

test('the tree extension holds the old p0 ancestor in p0 and both workspaces', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    for (const scope of ['p0', 'ws:w', 'ws:w1']) {
        assert.ok(!ids(blocksOf(data, workspaces, scope).natural).includes(ID.ancestor), scope)
        const tree = blocksOf(data, workspaces, scope, { showAsTree: true })
        assert.ok(ids(tree.natural).includes(ID.ancestor), scope)
    }
    assert.ok(!hasUnreadContent(state.sessions[ID.ancestor]))
})

test('the draft and the launched ephemeral root sit in p3', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    const p3 = ids(blocksOf(data, workspaces, 'p3').natural)
    assert.equal(p3[0], ID.ephemeral)
    assert.ok(p3.includes(ID.draft))
    assert.equal(state.sessions[ID.draft].draft, true)
    assert.equal(state.sessions[ID.ephemeral].ephemeral, true)
})

test('the baseline unread index counts the two placed unread roots', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const index = createBaselineActivityIndex(createBaselineData(state).data)
    assert.equal(index.totalUnread(), 2)
    assert.equal(index.summary(['p0']).unreadCount, 0)
    // The two p0 user_turn sessions carry a shell and a cron once they read as unread.
    for (const id of [ID.processShell, ID.processCron]) state.sessions[id].last_viewed_at = null
    const p0 = index.summary(['p0'])
    assert.deepEqual([p0.unreadCount, p0.unreadBackgroundCount, p0.unreadShellCount], [2, 2, 1])
})

test('a tick flips a read session to unread, an open tick does not, and top moves it first', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    const session = state.sessions[ID.p0Second]
    const before = { mtime: session.mtime, lastLine: session.last_line }
    tick(state, ID.p0Second, { open: true })
    assert.equal(session.mtime, before.mtime + 1)
    assert.equal(session.last_line, before.lastLine + 1)
    assert.ok(!hasUnreadContent(session))
    tick(state, ID.p0Second)
    assert.ok(hasUnreadContent(session))
    tick(state, ID.p1Recent, { top: true })
    const maxOther = Math.max(...Object.values(state.sessions).filter(s => s.id !== ID.p1Recent).map(s => s.mtime))
    assert.ok(state.sessions[ID.p1Recent].mtime > maxOther)
    assert.equal(ids(blocksOf(data, workspaces, 'p1').natural)[0], ID.p1Recent)
})

test('startupPhase and progressMessage write a real phase shape and replace the object', () => {
    const state = buildSidebarState({ roots: 20 })
    assert.equal(hasActiveStartupPhase(state.startupProgress), false)
    startupPhase(state, true)
    const started = state.startupProgress
    assert.equal(hasActiveStartupPhase(started), true)
    progressMessage(state)
    assert.notStrictEqual(state.startupProgress, started)
    assert.equal(hasActiveStartupPhase(state.startupProgress), true)
    assert.equal(state.startupProgress.background_compute.claude.current, 2)
    startupPhase(state, false)
    assert.equal(hasActiveStartupPhase(state.startupProgress), false)
    assert.equal(hasActiveStartupPhase(undefined), false)
})

test('the update sequence applies in order and ends outside startup', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const { data, workspaces } = createBaselineData(state)
    const seen = []
    for (const step of FIXTURE_UPDATE_SEQUENCE) {
        applyStep(state, step)
        seen.push(step.name)
        if (step.startup === 'write' || step.startup === 'progress') assert.ok(data.isStartupInProgress, step.name)
        else assert.ok(!data.isStartupInProgress, step.name)
    }
    assert.equal(new Set(seen).size, seen.length)
    assert.deepEqual(FIXTURE_UPDATE_SEQUENCE.filter(step => step.startup).map(step => step.startup),
        ['write', 'progress', 'end'])
    assert.ok(!state.sessions[ID.p0Recent])
    assert.equal(state.projects['p0-wt1'].worktree_of, 'p0')
    assert.equal(state.projects.p1.archived, true)
    assert.deepEqual(workspaces.getVisibleProjectIds('w'), ['p0', 'p0-wt1'])
})

test('both facades expose cached getters and the activity-sorted project inputs', () => {
    const state = buildSidebarState({ roots: 20 })
    for (const { data, workspaces } of [createBaselineData(state), createShippedData(state)]) {
        assert.strictEqual(data.getProjects, data.getProjects)
        assert.strictEqual(data.getAllSessions, data.getAllSessions)
        const projects = data.getProjects
        state.projects.p9.mtime += 10_000  // above every other project, on each pass
        assert.notStrictEqual(data.getProjects, projects)
        assert.equal(data.getProjects[0].id, 'p9')
        assert.deepEqual(data.getWorktreesOf('p2').map(p => p.id), ['p2-wt2', 'p2-wt1'])
        assert.deepEqual(workspaces.getAllProjectIds('w'), ['p0', 'p1'])
        assert.deepEqual(data.getProjectScopeIds('p2'), ['p2', 'p2-wt1', 'p2-wt2'])
    }
})

test('the shipped facade gives the same blocks as the baseline facade', () => {
    const state = buildSidebarState({ roots: 60, cutoffRoots: 50 })
    const baseline = createBaselineData(state)
    const shipped = createShippedData(state)
    for (const scope of FIXTURE_SCOPES) {
        for (const showAsTree of [false, true]) {
            const expected = blocksOf(baseline.data, baseline.workspaces, scope, { showAsTree })
            const actual = blocksOf(shipped.data, shipped.workspaces, scope, { showAsTree }, computeShippedBlocks)
            for (const key of ['crossFilterPinned', 'crossFilterActive', 'natural']) {
                assert.deepEqual(ids(actual[key]), ids(expected[key]), `${scope} ${showAsTree} ${key}`)
            }
        }
    }
})
