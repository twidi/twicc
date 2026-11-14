// Per-scope stable session lists (design: docs/plans/2026-10-28-sidebar-recompute-cost-design.md,
// 5.2 and 5.3; tests of 7.1 #1, #2, #6 and #10 for the scope part, and Review Focus 2).
//
// Every tick here uses a session that the test first made unread (own setup), so
// no tick flips an unread state; no tick is an open tick.

import test from 'node:test'
import assert from 'node:assert/strict'
import { effect, isReactive, toRaw } from 'vue'
import { getScopeSessionList, getStableSessionList, resolveSessionScope } from './sessionLists.js'
import { observeStageRuns } from './stageRuns.js'
import { FIXTURE_IDS as ID, buildSidebarState, createFixtureStores, progressMessage, startupPhase, tick }
    from '../../tests/helpers/sidebarFixture.js'

const ALL = '__all__'
const SCOPE_STAGES = ['scope:candidates', 'scope:members', 'scope:ordered']
const ids = (sessions) => sessions.map(s => s.id)

/** A fixture with pagination cutoffs, its shipped facades, and a scope resolver. */
function setup({ roots = 60, cutoffRoots = 40 } = {}) {
    const state = buildSidebarState({ roots, cutoffRoots })
    const { data, workspaces } = createFixtureStores(state)
    const resolve = (effectiveProjectId) => resolveSessionScope({
        effectiveProjectId,
        getProjectScopeIds: data.getProjectScopeIds,
        getVisibleProjectIds: workspaces.getVisibleProjectIds,
    })
    return { state, data, workspaces, resolve }
}

/** Own setup: make a session unread without an `mtime` change. */
function makeUnread(state, sessionId) {
    const session = state.sessions[sessionId]
    session.last_new_content_at = new Date(Date.parse(session.last_viewed_at) + 1000).toISOString()
}

/**
 * Keep `read()` subscribed like a component (an effect with a scheduler, so
 * the derivations rerun only at `flush()`), and count stage runs from now on.
 */
function subscribe(read) {
    let dirty = false
    const runner = effect(read, { scheduler: () => { dirty = true } })
    const counts = new Map()
    const stopObserving = observeStageRuns(name => counts.set(name, (counts.get(name) || 0) + 1))
    return {
        flush() { if (dirty) { dirty = false; runner() } },
        runs: (name) => counts.get(name) || 0,
        reset() { counts.clear() },
        stop() { stopObserving(); runner.effect.stop() },
    }
}

const subscribeList = (list) => subscribe(() => { list.members; list.ordered })

/** Replace `field` of the raw record by a counting accessor. */
function countReads(record, field) {
    const raw = toRaw(record)
    let value = raw[field]
    const counter = { reads: 0 }
    Object.defineProperty(raw, field, {
        configurable: true, enumerable: true,
        get() { counter.reads++; return value },
        set(next) { value = next },
    })
    return counter
}

const total = (counters) => counters.reduce((sum, counter) => sum + counter.reads, 0)

/** Project ids of a fixture scope: the fixture workspaces `w` and `w1`, or a project and its worktrees. */
function scopeProjectSet(state, scopeId) {
    if (scopeId === 'ws:w') return new Set(['p0', 'p1'])
    if (scopeId === 'ws:w1') return new Set(['p0'])
    return new Set([scopeId, ...Object.values(state.projects).filter(p => p.worktree_of === scopeId).map(p => p.id)])
}

/**
 * The ids a scope list must show (design 5.2), outside startup:
 *   - the roots of the scope's projects that are not hidden;
 *   - while the scope's cursor has more sessions to load, only those at or above its
 *     oldest mtime. A single project uses its own cursor; a workspace, a main
 *     repository with worktrees and all projects use the all-projects cursor;
 *   - in this order: the launched ephemeral root, the pinned roots, the roots with a
 *     process (most recently started first), then by mtime, most recent first.
 * The fixture gives every root a distinct mtime and every process a distinct start.
 */
function expectedIds(state, scopeId) {
    const projectIds = scopeId === ALL ? null : scopeProjectSet(state, scopeId)
    const single = projectIds !== null && !scopeId.startsWith('ws:') && projectIds.size === 1
    const cursor = state.localState.projects[single ? scopeId : ALL]
    const shown = Object.values(state.sessions).filter(s => !s.parent_session_id && !s.hidden
        && (!projectIds || projectIds.has(s.project_id))
        && (!cursor?.hasMoreSessions || s.mtime >= cursor.oldestSessionMtime))
    const key = (s) => {
        const process = state.processStates[s.id]
        return [s.ephemeral && !s.draft ? 0 : 1, s.pinned ? 0 : 1, process ? 0 : 1, -(process?.started_at ?? 0), -s.mtime]
    }
    const compare = (a, b) => {
        const [ka, kb] = [key(a), key(b)]
        const i = ka.findIndex((value, k) => value !== kb[k])
        return i < 0 ? 0 : ka[i] - kb[i]
    }
    return ids(shown.sort(compare))
}

/**
 * Positive controls on the same fixture: an order change of an in-scope
 * session without a process runs `members` and `ordered`; a hide runs
 * `candidates`. `p0-recent` must already be unread (own setup).
 */
function assertStagesCanRun(state, probe, label) {
    probe.reset()
    tick(state, ID.p0Recent, { top: true })
    probe.flush()
    assert.ok(probe.runs('scope:members') >= 1, `${label}: members positive control`)
    assert.ok(probe.runs('scope:ordered') >= 1, `${label}: ordered positive control`)
    state.sessions[ID.p0Second].hidden = true
    probe.flush()
    assert.ok(probe.runs('scope:candidates') >= 1, `${label}: candidates positive control`)
}

/**
 * Probes of the active in-scope tick, on the fields a list that scans the whole
 * store would read on that path. Single project: `project_id` of out-of-scope
 * roots and their subagents, `parent_session_id` and `hidden` of the subagents
 * of in-scope roots. Multi-project: `parent_session_id` of every subagent and
 * out-of-scope root, and `mtime` of out-of-scope roots (an all-projects list
 * filtered on the scope reads them). `categories` maps each probe category to
 * its counters; each counter's `read()` reads its field through the state.
 */
function installTickProbes(state, scopeId) {
    const inScope = scopeProjectSet(state, scopeId)
    const raw = toRaw(state.sessions)
    const roots = Object.values(raw).filter(s => !s.parent_session_id)
    const isInScope = (s) => inScope.has(s.project_id)
    const subagents = Object.values(raw).filter(s => s.parent_session_id)
    const probes = []
    const categories = new Map()
    const probe = (category, record, field) => {
        const counter = countReads(record, field)
        counter.read = () => state.sessions[record.id][field]
        if (!categories.has(category)) categories.set(category, [])
        categories.get(category).push(counter)
        probes.push(counter)
    }
    if (scopeId.startsWith('ws:')) {
        for (const s of subagents) probe('parent_session_id of subagents', s, 'parent_session_id')
        for (const s of roots.filter(s => !isInScope(s))) {
            probe('parent_session_id of out-of-scope roots', s, 'parent_session_id')
            probe('mtime of out-of-scope roots', s, 'mtime')
        }
    } else {
        for (const s of roots.filter(s => !isInScope(s))) probe('project_id of out-of-scope roots', s, 'project_id')
        for (const s of subagents) {
            if (isInScope(raw[s.parent_session_id])) {
                probe('parent_session_id of subagents of in-scope roots', s, 'parent_session_id')
                probe('hidden of subagents of in-scope roots', s, 'hidden')
            } else {
                probe('project_id of subagents of out-of-scope roots', s, 'project_id')
            }
        }
    }
    const candidateMtimes = roots.filter(s => isInScope(s) && !s.hidden).map(s => countReads(s, 'mtime'))
    return { probes, categories, candidateMtimes }
}

test('an active in-scope tick reads only in-scope candidate mtimes and reruns nothing after members', () => {
    for (const scopeId of ['p0', 'ws:w']) {
        const { state, resolve } = setup()
        const list = getScopeSessionList(state, resolve(scopeId))
        // Own setup: the ticked active session and the positive-control session are unread.
        makeUnread(state, ID.processAssistant)
        makeUnread(state, ID.p0Recent)
        const { probes, candidateMtimes } = installTickProbes(state, scopeId)
        const probe = subscribeList(list)
        const before = { members: list.members, ordered: list.ordered }
        tick(state, ID.processAssistant)
        for (const counter of [...probes, ...candidateMtimes]) counter.reads = 0  // the tick's own reads
        probe.flush()
        assert.equal(probe.runs('scope:members'), 1, scopeId)
        assert.equal(probe.runs('scope:candidates'), 0, scopeId)
        assert.equal(probe.runs('scope:ordered'), 0, scopeId)
        assert.equal(probe.runs('rootSessions'), 0, scopeId)
        assert.equal(total(probes), 0, `${scopeId}: no out-of-scope or subagent field is read`)
        assert.ok(candidateMtimes.length > 5, scopeId)
        assert.ok(candidateMtimes.every(counter => counter.reads === 1), `${scopeId}: one mtime read per candidate`)
        assert.strictEqual(list.members, before.members, scopeId)
        assert.strictEqual(list.ordered, before.ordered, scopeId)
        assertStagesCanRun(state, probe, scopeId)
        // Positive control of the zero 'rootSessions' runs above: a root add reruns the index.
        probe.reset()
        state.sessions['added-root'] = { ...toRaw(state.sessions[ID.p3Recent]), id: 'added-root' }
        probe.flush()
        assert.ok(probe.runs('rootSessions') >= 1, `${scopeId}: rootSessions positive control`)
        probe.stop()
    }
})

test('the probes of the active in-scope tick count every read of their field', () => {
    // The zero reads above can fail: a read of any probed field through the state counts.
    for (const scopeId of ['p0', 'ws:w']) {
        const state = buildSidebarState({ roots: 60, cutoffRoots: 40 })
        const { categories } = installTickProbes(state, scopeId)
        assert.ok(categories.size >= 3, scopeId)
        for (const [category, counters] of categories) {
            assert.ok(counters.length > 0, `${scopeId}: ${category}`)
            for (const counter of counters) counter.reads = 0  // the probe set-up's own reads
            for (const counter of counters) counter.read()
            assert.ok(counters.every(counter => counter.reads === 1), `${scopeId}: ${category}`)
        }
    }
})

test('a tick of a session without a process outside the scope does not rerun the scope list', () => {
    for (const scopeId of ['p0', 'ws:w']) {
        const { state, resolve } = setup()
        const list = getScopeSessionList(state, resolve(scopeId))
        // Own setup: p3-recent (outside p0 and w, no process) and the control session are unread.
        makeUnread(state, ID.p3Recent)
        makeUnread(state, ID.p0Recent)
        const probe = subscribeList(list)
        const before = { members: list.members, ordered: list.ordered }
        tick(state, ID.p3Recent)
        probe.flush()
        tick(state, ID.p3Recent, { top: true })
        probe.flush()
        for (const stage of SCOPE_STAGES) assert.equal(probe.runs(stage), 0, `${scopeId}: ${stage}`)
        assert.strictEqual(list.members, before.members, scopeId)
        assert.strictEqual(list.ordered, before.ordered, scopeId)
        assertStagesCanRun(state, probe, scopeId)
        probe.stop()
    }
})

test('a subagent add or removal reruns no scope stage', () => {
    for (const scopeId of ['p0', 'ws:w']) {
        const { state, resolve } = setup()
        const list = getScopeSessionList(state, resolve(scopeId))
        makeUnread(state, ID.p0Recent)  // own setup for the positive control
        const probe = subscribeList(list)
        const before = list.ordered
        const parent = toRaw(state.sessions[ID.child])
        state.sessions['child-sa-added'] = { ...toRaw(state.sessions[`${ID.child}-sa0`]), id: 'child-sa-added',
            parent_session_id: parent.id }
        probe.flush()
        delete state.sessions['child-sa-added']
        probe.flush()
        delete state.sessions[`${ID.p1Recent}-sa0`]
        probe.flush()
        // The writes reran the root index (retained), so the zero runs below are not vacuous.
        assert.ok(probe.runs('rootSessions') >= 3, scopeId)
        for (const stage of SCOPE_STAGES) assert.equal(probe.runs(stage), 0, `${scopeId}: ${stage}`)
        assert.strictEqual(list.ordered, before, scopeId)
        assertStagesCanRun(state, probe, scopeId)
        probe.stop()
    }
})

test('a one-project workspace and the same project scope are two lists with their own cursors', () => {
    const { state, data, resolve } = setup()
    const workspace = resolve('ws:w1')
    const project = resolve('p0')
    assert.deepEqual(workspace, { key: `${ALL}|p0`, projectIds: ['p0'], cursorId: ALL })
    assert.deepEqual(project, { key: 'p0|p0', projectIds: ['p0'], cursorId: 'p0' })
    const workspaceList = getScopeSessionList(state, workspace)
    const projectList = getScopeSessionList(state, project)
    assert.notStrictEqual(workspaceList, projectList)
    // cut-p0 sits between the p0 cursor and the all-projects cursor.
    assert.ok(ids(projectList.members).includes(ID.cutP0))
    assert.ok(!ids(workspaceList.members).includes(ID.cutP0))
    assert.deepEqual(ids(workspaceList.ordered), expectedIds(state, 'ws:w1'))
    assert.deepEqual(ids(projectList.ordered), expectedIds(state, 'p0'))
    // The head of p0 in plain terms: the sessions with a process by start, then by mtime.
    assert.deepEqual(ids(projectList.ordered).slice(0, 6),
        [ID.processAssistant, ID.processShell, ID.processCron, ID.child, ID.p0Recent, ID.p0Second])
    // Stable object per key; the store getters return the module's values unchanged.
    assert.strictEqual(getScopeSessionList(state, resolve('ws:w1')), workspaceList)
    assert.strictEqual(data.getScopeSessionList(project), projectList)
    assert.strictEqual(data.getProjectSessions('p0'), projectList.ordered)
    assert.strictEqual(getStableSessionList(state, 'p0', ALL), projectList.ordered)
})

test('a main repository with worktrees uses the all-projects cursor', () => {
    const { state, data, resolve } = setup()
    const scope = resolve('p2')
    assert.deepEqual(scope, { key: `${ALL}|p2,p2-wt1,p2-wt2`, projectIds: ['p2', 'p2-wt1', 'p2-wt2'], cursorId: ALL })
    const list = getScopeSessionList(state, scope)
    // cut-p2 is above the p2 cursor but under the all-projects cursor.
    assert.ok(!ids(list.members).includes(ID.cutP2))
    assert.ok(ids(data.getProjectSessions('p2')).includes(ID.cutP2))
    assert.ok(!ids(list.members).includes(ID.oldWorktree))
    assert.deepEqual(ids(list.ordered), expectedIds(state, 'p2'))
    assert.ok(list.members.every(s => ['p2', 'p2-wt1', 'p2-wt2'].includes(s.project_id)))
    // The key does not depend on the order of the project ids.
    assert.equal(resolveSessionScope({ effectiveProjectId: 'p2', getProjectScopeIds: () => ['p2-wt2', 'p2', 'p2-wt1'],
        getVisibleProjectIds: () => [] }).key, scope.key)
    // A worktree on its own is a single project: its own cursor entry (none here, so fully loaded).
    assert.deepEqual(resolve('p2-wt1'), { key: 'p2-wt1|p2-wt1', projectIds: ['p2-wt1'], cursorId: 'p2-wt1' })
    assert.ok(ids(getScopeSessionList(state, resolve('p2-wt1')).members).includes(ID.oldWorktree))
    // The all-projects cursor ends: the main repository list loads everything.
    state.localState.projects[ALL].hasMoreSessions = false
    assert.ok(ids(list.members).includes(ID.cutP2))
    assert.ok(ids(list.members).includes(ID.oldWorktree))
    assert.deepEqual(ids(list.ordered), expectedIds(state, 'p2'))
})

test('a falsy project id resolves to the all-projects list and cursor', () => {
    const { state, data, resolve } = setup()
    const all = resolve(ALL)
    assert.deepEqual(all, { key: `${ALL}|*`, projectIds: null, cursorId: ALL })
    for (const effectiveProjectId of [null, undefined, '']) {
        assert.deepEqual(resolve(effectiveProjectId), all, String(effectiveProjectId))
        assert.strictEqual(getScopeSessionList(state, resolve(effectiveProjectId)), getScopeSessionList(state, all))
    }
    const list = getScopeSessionList(state, all)
    assert.strictEqual(data.getAllSessions, list.ordered)
    assert.strictEqual(getStableSessionList(state, ALL, ALL), list.ordered)
    assert.deepEqual(ids(list.ordered), expectedIds(state, ALL))
    assert.ok(!ids(list.members).includes(ID.cutP2))
})

test('a session crossing the cutoff joins and leaves the members', () => {
    for (const [scopeId, sessionId] of [['p0', ID.ancestor], ['ws:w', ID.ancestor], ['p2', ID.oldWorktree]]) {
        const { state, resolve } = setup()
        const scope = resolve(scopeId)
        const list = getScopeSessionList(state, scope)
        makeUnread(state, sessionId)  // own setup: the ticked session is already unread
        const probe = subscribeList(list)
        assert.ok(!ids(list.members).includes(sessionId), scopeId)
        // Its first tick past the cutoff brings it in, at the top of the sessions without a process.
        tick(state, sessionId, { top: true })
        probe.flush()
        assert.ok(ids(list.members).includes(sessionId), scopeId)
        assert.deepEqual(ids(list.ordered), expectedIds(state, scopeId), scopeId)
        assert.equal(probe.runs('scope:candidates'), 0, scopeId)
        assert.equal(probe.runs('scope:members'), 1, scopeId)
        // The cursor moves past it: it leaves, but running sessions stay in scope.
        const cursor = state.localState.projects[scope.cursorId]
        cursor.oldestSessionMtime = state.sessions[sessionId].mtime + 1
        probe.flush()
        assert.ok(!ids(list.members).includes(sessionId), scopeId)
        assert.deepEqual(ids(list.ordered), scopeId === 'p2'
            ? [] : [ID.processAssistant, ID.processShell, ID.processCron], scopeId)
        // Pagination completes: every candidate is a member.
        cursor.hasMoreSessions = false
        probe.flush()
        assert.ok(ids(list.members).includes(sessionId), scopeId)
        assert.deepEqual(ids(list.ordered), expectedIds(state, scopeId), scopeId)
        // Positive control of the zero 'scope:candidates' runs above: a hide reruns the candidates.
        state.sessions[sessionId].hidden = true
        probe.flush()
        assert.ok(probe.runs('scope:candidates') >= 1, `${scopeId}: candidates positive control`)
        assert.ok(!ids(list.members).includes(sessionId), scopeId)
        probe.stop()
    }
})

test('hide and unhide move a session out of and back into the candidates', () => {
    for (const scopeId of ['p0', 'ws:w']) {
        const { state, resolve } = setup()
        const list = getScopeSessionList(state, resolve(scopeId))
        const probe = subscribeList(list)
        const original = ids(list.ordered)
        assert.ok(original.includes(ID.p0Second), scopeId)
        state.sessions[ID.p0Second].hidden = true
        probe.flush()
        assert.equal(probe.runs('scope:candidates'), 1, scopeId)
        assert.ok(!ids(list.members).includes(ID.p0Second), scopeId)
        assert.deepEqual(ids(list.ordered), original.filter(id => id !== ID.p0Second), scopeId)
        state.sessions[ID.p0Second].hidden = false
        probe.flush()
        assert.equal(probe.runs('scope:candidates'), 2, scopeId)
        assert.deepEqual(ids(list.ordered), original, scopeId)
        probe.stop()
    }
})

function assertRawEntries(state, list, label) {
    for (const stage of ['members', 'ordered']) {
        assert.ok(list[stage].length > 0, label)
        for (const s of list[stage]) {
            assert.ok(!isReactive(s), `${label}: ${stage} ${s.id} is raw`)
            assert.strictEqual(s, toRaw(state.sessions[s.id]), `${label}: ${stage} ${s.id}`)
        }
    }
}

function assertReactiveEntries(state, list, label) {
    for (const stage of ['members', 'ordered']) {
        for (const s of list[stage]) {
            assert.ok(isReactive(s), `${label}: ${stage} ${s.id} is reactive`)
            assert.strictEqual(s, state.sessions[s.id], `${label}: ${stage} ${s.id}`)
        }
    }
}

/** The three untracked changes of 5.3: a hide, a cutoff crossing, an order change. */
function applyUntrackedChanges(state, scope) {
    state.sessions[ID.p0Second].hidden = true
    state.sessions[ID.ancestor].mtime = state.localState.projects[scope.cursorId].oldestSessionMtime + 1
    tick(state, ID.p0Recent, { top: true })
}

test('during startup the stages track no session field, rerun on each progress message and output raw records', () => {
    for (const scopeId of ['p0', 'ws:w']) {
        const { state, resolve } = setup()
        const scope = resolve(scopeId)
        makeUnread(state, ID.p0Recent)  // own setup: the ticked session is already unread
        startupPhase(state, true)
        const list = getScopeSessionList(state, scope)
        const probe = subscribeList(list)
        assertRawEntries(state, list, scopeId)
        const before = ids(list.ordered)
        assert.ok(before.indexOf(ID.child) < before.indexOf(ID.p0Recent), `${scopeId}: child comes first`)
        applyUntrackedChanges(state, scope)
        state.processStates[ID.processCron].started_at = 999
        probe.flush()
        for (const stage of SCOPE_STAGES) assert.equal(probe.runs(stage), 0, `${scopeId}: ${stage} untracked`)
        assert.deepEqual(ids(list.ordered), before, scopeId)
        // Refresh guarantee: one progress message reruns each stage once and shows every change.
        progressMessage(state)
        probe.flush()
        for (const stage of SCOPE_STAGES) assert.equal(probe.runs(stage), 1, `${scopeId}: ${stage} on progress`)
        const after = ids(list.ordered)
        assert.ok(!after.includes(ID.p0Second), scopeId)
        assert.ok(after.includes(ID.ancestor), scopeId)
        assert.ok(after.indexOf(ID.p0Recent) < after.indexOf(ID.child), `${scopeId}: the order change shows up`)
        assert.deepEqual(after, expectedIds(state, scopeId), scopeId)
        assertRawEntries(state, list, scopeId)
        progressMessage(state)
        probe.flush()
        for (const stage of SCOPE_STAGES) assert.equal(probe.runs(stage), 2, `${scopeId}: ${stage} on second progress`)
        probe.stop()
    }
})

test('after startup the lists output reactive records and track fields again', () => {
    for (const scopeId of ['p0', 'ws:w']) {
        const { state, resolve } = setup()
        const scope = resolve(scopeId)
        makeUnread(state, ID.p0Recent)  // own setup: the ticked session is already unread
        startupPhase(state, true)
        const list = getScopeSessionList(state, scope)
        const probe = subscribeList(list)
        assertRawEntries(state, list, scopeId)
        startupPhase(state, false)
        probe.flush()
        assertReactiveEntries(state, list, scopeId)
        assert.deepEqual(ids(list.ordered), expectedIds(state, scopeId), scopeId)
        // Each change shows up at once, with no progress message.
        state.sessions[ID.p0Second].hidden = true
        probe.flush()
        assert.ok(!ids(list.members).includes(ID.p0Second), scopeId)
        state.sessions[ID.ancestor].mtime = state.localState.projects[scope.cursorId].oldestSessionMtime + 1
        probe.flush()
        assert.ok(ids(list.members).includes(ID.ancestor), scopeId)
        tick(state, ID.p0Recent, { top: true })
        probe.flush()
        state.processStates[ID.processCron].started_at = 999
        probe.flush()
        assert.deepEqual(ids(list.ordered), expectedIds(state, scopeId), scopeId)
        assertReactiveEntries(state, list, scopeId)
        probe.stop()
    }
})
