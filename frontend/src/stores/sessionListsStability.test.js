import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, nextTick, watch, toRaw } from 'vue'
import { sessionSortComparator } from '../utils/sessionSort.js'
import { getStableSessionList } from '../utils/sessionLists.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const start = source.indexOf('        getProjectSessions:')
const end = source.indexOf('        getSession: (state)', start)
const getters = new Function('hasActiveStartupPhase', 'toRaw', 'sessionSortComparator', 'ALL_PROJECTS_ID', 'getStableSessionList',
    `return { ${source.slice(start, end)} }`)(progress => !!progress.running, toRaw, sessionSortComparator, '__all__', getStableSessionList)
const useFixture = defineStore('session-list-stability', {
    state: () => ({ startupProgress: {}, localState: { projects: {
        __all__: { hasMoreSessions: true, oldestSessionMtime: 50 },
        p: { hasMoreSessions: true, oldestSessionMtime: 50 },
    } }, sessions: {
        a: { id: 'a', project_id: 'p', mtime: 100 }, b: { id: 'b', project_id: 'p', mtime: 90 },
        c: { id: 'c', project_id: 'q', mtime: 80 }, old: { id: 'old', project_id: 'p', mtime: 40 },
    }, processStates: { a: { started_at: 1 }, b: { started_at: 2 } } }), getters,
})
const fixture = () => useFixture(createPinia())

test('publishing identical displayed ids does not update switcher consumers', async () => {
    const actionStart = source.indexOf('        setDisplayedSessionIds(ids)')
    const actionEnd = source.indexOf('        /**', actionStart)
    const actions = new Function(`return { ${source.slice(actionStart, actionEnd)} }`)()
    const store = fixture()
    store.localState.displayedSessionIds = ['b', 'a']
    let updates = 0
    const stop = watch(() => store.localState.displayedSessionIds, () => updates++)
    try {
        actions.setDisplayedSessionIds.call(store, ['b', 'a'])
        await nextTick()
        assert.equal(updates, 0)
        actions.setDisplayedSessionIds.call(store, ['a', 'b'])
        await nextTick()
        assert.equal(updates, 1)
    } finally { stop() }
})

test('active mtime updates retain project and global lists during pagination', async () => {
    const store = fixture(), project = computed(() => store.getProjectSessions('p'))
    const before = [store.getAllSessions, project.value]
    let updates = 0
    const stop = watch([() => store.getAllSessions, project], () => updates++)
    try {
        for (let i = 0; i < 10; i++) { store.sessions.a.mtime++; await nextTick() }
        assert.equal(updates, 0)
        assert.strictEqual(store.getAllSessions, before[0])
        assert.strictEqual(project.value, before[1])
        assert.deepEqual(project.value.map(s => s.id), ['b', 'a'])
    } finally { stop() }
})

test('stable active membership does not repeat sorting during mtime updates', () => {
    const store = fixture()
    let reads = 0
    Object.defineProperty(store.processStates.a, 'started_at', { configurable: true, get() { reads++; return 1 } })
    store.getAllSessions
    const before = reads
    store.sessions.a.mtime++
    store.getAllSessions
    assert.equal(reads, before)
})

test('pagination crossings and completion still change list membership', () => {
    const store = fixture()
    assert.deepEqual(store.getProjectSessions('p').map(s => s.id), ['b', 'a'])
    store.sessions.old.mtime = 60
    assert.deepEqual(store.getProjectSessions('p').map(s => s.id), ['b', 'a', 'old'])
    store.sessions.old.mtime = 40
    assert.deepEqual(store.getProjectSessions('p').map(s => s.id), ['b', 'a'])
    store.localState.projects.p.hasMoreSessions = false
    assert.deepEqual(store.getProjectSessions('p').map(s => s.id), ['b', 'a', 'old'])
})

test('inactive ordering pins process changes and hidden changes remain reactive', () => {
    const store = fixture()
    store.localState.projects.__all__.hasMoreSessions = false
    store.sessions.old.mtime = 85
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['b', 'a', 'old', 'c'])
    store.sessions.c.mtime = 86
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['b', 'a', 'c', 'old'])
    store.sessions.old.pinned = 'all'
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['old', 'b', 'a', 'c'])
    store.processStates.a.started_at = 3
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['old', 'a', 'b', 'c'])
    store.sessions.a.hidden = true
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['old', 'b', 'c'])
    delete store.processStates.b
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['old', 'b', 'c'])
})

test('startup tracking resumes after metadata computation completes', () => {
    const store = fixture()
    store.startupProgress.running = true
    const before = store.getAllSessions
    store.sessions.a.hidden = true
    assert.strictEqual(store.getAllSessions, before)
    store.startupProgress.running = false
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['b', 'c'])
    store.sessions.a.hidden = false
    assert.deepEqual(store.getAllSessions.map(s => s.id), ['b', 'a', 'c'])
})
