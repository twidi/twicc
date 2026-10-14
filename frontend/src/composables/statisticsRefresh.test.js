import test from 'node:test'
import assert from 'node:assert/strict'

const module = await import('./statisticsRefresh.js').catch(() => ({}))

for (const target of ['home', 'daily']) {
    test(`${target}: concurrent signals coalesce and an in-flight signal schedules a trailing refresh`, async () => {
        assert.equal(typeof module.createStatisticsRefresh, 'function', 'statistics refresh is missing')
        const gates = []
        let calls = 0
        const refresh = module.createStatisticsRefresh(() => {
            calls++
            return new Promise(resolve => gates.push(resolve))
        })
        const first = refresh()
        refresh()
        refresh()
        await Promise.resolve()
        assert.equal(calls, 1)
        refresh()
        refresh()
        gates.shift()()
        await new Promise(resolve => setImmediate(resolve))
        assert.equal(calls, 2)
        gates.shift()()
        await first
        assert.equal(calls, 2)
    })
}

test('statistics refresh updates loaded root snapshots and separately loaded child records', async () => {
    const refreshed = []
    const store = {
        sessions: {
            root: { id: 'root', project_id: 'project' },
            child: { id: 'child', parent_session_id: 'root', total_cost: 1 },
            empty: { id: 'empty', parent_session_id: 'root', total_cost: 1 },
            other: { id: 'other', parent_session_id: 'elsewhere', total_cost: 1 },
        },
        localState: { agentLoaded: { root: true }, sessions: { child: { itemsFetched: true } } },
        async fetchSubagentsState(project, root) { refreshed.push([project, root]) },
        async refreshSessionRecord(id) { this.sessions[id].total_cost = 7 },
    }
    await module.refreshStatisticsSessions(store, ['root'])
    assert.deepEqual(refreshed, [['project', 'root']])
    assert.equal(store.sessions.child.total_cost, 7)
    assert.equal(store.sessions.empty.total_cost, 7)
    assert.equal(store.sessions.other.total_cost, 1)
    assert.deepEqual(Object.keys(store.sessions), ['root', 'child', 'empty', 'other'])
})
