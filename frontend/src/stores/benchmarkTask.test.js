import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { useBenchmarkTaskStore } from './benchmarkTask.js'

function setup() { setActivePinia(createPinia()); return useBenchmarkTaskStore() }

test('resetTransientControls turns off older models and auto-select, keeps the task controls', () => {
    const store = setup()
    store.showOlder = true
    store.autoSelectBest = true
    store.defaultProviderOnly = true
    store.setTaskType('coding')
    store.setDifficulty(80)
    store.setFavor('speed')

    store.resetTransientControls()

    assert.equal(store.showOlder, false)
    assert.equal(store.autoSelectBest, false)
    assert.equal(store.defaultProviderOnly, false)
    assert.equal(store.taskType, 'coding')
    assert.equal(store.difficulty, 80)
    assert.equal(store.favor, 'speed')
})
