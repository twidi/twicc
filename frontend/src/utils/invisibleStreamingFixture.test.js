import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { resolveLayout } from './layoutResolver.js'
import { DISPLAY_LEVEL } from '../constants.js'
const fixture = readFileSync(new URL('../../tests/browser/invisibleStreaming.js', import.meta.url), 'utf8')
const data = readFileSync(new URL('../stores/data.js', import.meta.url), 'utf8')
const seedStart = fixture.indexOf('function seedSession('), seedEnd = fixture.indexOf('\nseedSession(mainId)', seedStart)
const getterStart = data.indexOf('        getSessionTasks:'), getterEnd = data.indexOf('        },', getterStart) + 10
assert.ok(seedStart >= 0 && seedEnd > seedStart && getterStart >= 0 && getterEnd > getterStart)
const { getSessionTasks } = new Function(`return { ${data.slice(getterStart, getterEnd)} }`)()
for (const provider of ['claude_code', 'codex']) test(`${provider}: fixture seeds a real Tasks presence snapshot and rendered right-top dock`, () => {
    const store = { sessions: {}, localState: { sessions: {}, agentLoaded: {} }, processStates: {},
        initSessionItemsFromMetadata() {}, updateSessionItemsContent() {} }
    const seed = new Function('store', 'provider', 'projectId', 'DISPLAY_LEVEL', 'finalContent',
        `${fixture.slice(seedStart, seedEnd)}; return seedSession`)(store, provider, 'fixture-project', DISPLAY_LEVEL, () => ({}))
    for (const id of ['main', 'other', 'agent']) {
        seed(id)
        const tasks = getSessionTasks(store)(id)
        assert.ok(tasks, 'Missing tasks makes SessionView remove the Tasks tab')
        assert.equal(tasks.provider, provider)
        assert.equal(tasks.items.length, 1)
        assert.equal(tasks.items[0].status, 'pending')
        const render = resolveLayout({ tabs: [{ id: 'main', fixedCenter: true }, { id: 'tasks' }],
            assignment: store.sessions[id].layout.assignment, viewport: { w: 1680, h: 1000 }, collapsed: [] })
        assert.ok(render.regions.some(region => region.slots.some(slot => slot.dockId === 'right-top' && slot.tabs.some(tab => tab.id === 'tasks'))))
        const maximized = resolveLayout({ tabs: [{ id: 'main', fixedCenter: true }, { id: 'tasks' }],
            assignment: store.sessions[id].layout.assignment, viewport: { w: 1680, h: 1000 }, collapsed: [], maximized: ['right-top'] })
        assert.ok(maximized.regions.some(region => region.kind === 'maximized' && region.slots.some(slot => slot.dockId === 'right-top')))
    }
})
