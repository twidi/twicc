import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, nextTick, watch } from 'vue'

// Execute production getters/actions with Pinia. The full store requires the browser.
const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
const getterStart = source.indexOf('        getProjectDisplayName:')
const getterEnd = source.indexOf('\n    },\n\n    actions:', getterStart)
const actionStart = source.indexOf('        updateProject(project)')
const actionEnd = source.indexOf('        /**', actionStart)
assert.ok(getterStart >= 0 && getterEnd > getterStart)
assert.ok(actionStart >= 0 && actionEnd > actionStart)
const useProjects = defineStore('project-display-name-test', {
    state: () => ({ projects: {}, localState: { projectDisplayNames: {} } }),
    getters: new Function(`return { ${source.slice(getterStart, getterEnd)} }`)(),
    actions: new Function(`return { ${source.slice(actionStart, actionEnd)} }`)(),
})
const project = { id: '-home-dev-fixture', name: 'Fixture', directory: '/home/dev/fixture', mtime: 1, total_cost: 0 }
function makeStore() {
    const store = useProjects(createPinia())
    store.updateProject({ ...project })
    return store
}

// Unconditional invalidation changes the composer's object-valued context.
test('project activity preserves the resolved composer context', async () => {
    const store = makeStore()
    assert.equal(store.getProjectDisplayName(project.id), 'Fixture')
    const context = computed(() => ({ project: store.projects[project.id], projectName: store.getProjectDisplayName(project.id) }))
    const before = context.value
    let updates = 0
    const stop = watch(context, () => updates++)
    try {
        store.updateProject({ ...project, mtime: 2, total_cost: 0.85 })
        await nextTick()
        assert.strictEqual(context.value, before)
        assert.equal(updates, 0)
        assert.equal(store.projects[project.id].mtime, 2)
        assert.equal(store.projects[project.id].total_cost, 0.85)
    } finally { stop() }
})

test('partial metadata updates preserve the display-name cache', () => {
    const store = makeStore()
    assert.equal(store.getProjectDisplayName(project.id), 'Fixture')
    store.updateProject({ id: project.id, mtime: 3 })
    assert.equal(store.localState.projectDisplayNames[project.id], 'Fixture')
})

test('a real rename refreshes the display name', () => {
    const store = makeStore()
    assert.equal(store.getProjectDisplayName(project.id), 'Fixture')
    store.updateProject({ id: project.id, name: 'Renamed' })
    assert.equal(store.getProjectDisplayName(project.id), 'Renamed')
    store.updateProject({ id: project.id, name: null })
    assert.equal(store.getProjectDisplayName(project.id), 'fixture')
})

test('directory changes refresh an unnamed project display name', () => {
    const store = makeStore()
    store.updateProject({ id: project.id, name: null })
    assert.equal(store.getProjectDisplayName(project.id), 'fixture')
    store.updateProject({ id: project.id, directory: '/home/dev/other' })
    assert.equal(store.getProjectDisplayName(project.id), 'other')
})

test('a new project clears an obsolete cached name', () => {
    const store = useProjects(createPinia())
    store.localState.projectDisplayNames[project.id] = 'Obsolete'
    store.updateProject({ ...project })
    assert.equal(store.getProjectDisplayName(project.id), 'Fixture')
})

test('metadata updates replace nested settings and preserve client fields', () => {
    const store = makeStore()
    store.projects[project.id].default_agent_settings = { effort: 'high', selected_model: 'old' }
    store.projects[project.id].clientOnly = true
    const before = store.projects[project.id]
    store.updateProject({ id: project.id, default_agent_settings: { effort: 'low' } })
    assert.strictEqual(store.projects[project.id], before)
    assert.deepEqual(store.projects[project.id].default_agent_settings, { effort: 'low' })
    assert.equal(store.projects[project.id].clientOnly, true)
})
