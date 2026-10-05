import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { toValue } from 'vue'

const source = readFileSync(new URL('./useNewSessionCreation.js', import.meta.url), 'utf8')

function setup({ gate = { state: true }, query = { workspace: 'ws', search: 'keep' } } = {}) {
    const calls = []
    const route = { query }
    const useCreation = runInNewContext(`${source.replace(/^import .*$/gm, '').replace('export function', 'function')}; useNewSessionCreation`, {
        toValue,
        useRoute: () => route,
        useRouter: () => ({ push: location => calls.push(['push', JSON.parse(JSON.stringify(location))]) }),
        useDataStore: () => ({ createDraftSession: (id, state) => { calls.push(['draft', id, state]); return 'draft-id' } }),
        ensureProjectTrust: async id => { calls.push(['trust', id]); return gate },
    })
    return { useCreation, calls }
}

test('Home creates a draft through the shared trust gate and opens it', async () => {
    const { useCreation, calls } = setup()
    await useCreation()('chosen')
    assert.deepEqual(calls, [
        ['trust', 'chosen'], ['draft', 'chosen', true],
        ['push', { name: 'projects-session', params: { projectId: 'chosen', sessionId: 'draft-id' }, query: { workspace: 'ws', search: 'keep' } }],
    ])
    assert.match(source, /import \{ ensureProjectTrust \} from '\.\/useTrustGate'/)
})

test('trust cancellation creates no draft and does not navigate', async () => {
    const { useCreation, calls } = setup({ gate: false })
    await useCreation()('chosen')
    assert.deepEqual(calls, [['trust', 'chosen']])
})

test('single-project selection preserves its filter while creating in another project or worktree', async () => {
    const { useCreation, calls } = setup({ gate: { state: false } })
    await useCreation({ projectId: () => 'filter', allProjects: () => false })('worktree')
    assert.deepEqual(calls, [
        ['trust', 'worktree'], ['draft', 'worktree', false],
        ['push', { name: 'session', params: { projectId: 'filter', sessionId: 'draft-id' }, query: { workspace: 'ws', search: 'keep' } }],
    ])
})

test('direct current-project creation and All Projects creation keep their existing routes', async () => {
    const { useCreation, calls } = setup({ query: {} })
    await useCreation({ projectId: () => 'current', allProjects: () => false })()
    assert.equal(calls[1][1], 'current')
    assert.deepEqual(calls[2][1], { name: 'session', params: { projectId: 'current', sessionId: 'draft-id' }, query: {} })
    calls.length = 0
    await useCreation({ projectId: () => 'current', allProjects: () => true })('other')
    assert.equal(calls[2][1].name, 'projects-session')
    assert.equal(calls[2][1].params.projectId, 'other')
})

test('missing project does not open the trust gate', async () => {
    const { useCreation, calls } = setup()
    await useCreation()()
    assert.deepEqual(calls, [])
})


test('unresolved trust passes its authoritative null state to the draft', async () => {
    const { useCreation, calls } = setup({ gate: { state: null } })
    await useCreation()('chosen')
    assert.deepEqual(calls[1], ['draft', 'chosen', null])
    assert.equal(calls[2][0], 'push')
})
