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
    store.hoverDetails = true
    store.setTaskType('coding')
    store.setDifficulty(80)
    store.setFavor('speed')

    store.resetTransientControls()

    assert.equal(store.showOlder, false)
    assert.equal(store.autoSelectBest, false)
    assert.equal(store.defaultProviderOnly, false)
    assert.equal(store.hoverDetails, false)
    assert.equal(store.taskType, 'coding')
    assert.equal(store.difficulty, 80)
    assert.equal(store.favor, 'speed')
})

test('the picker mode starts simple and survives resetTransientControls', () => {
    const store = setup()
    assert.equal(store.pickerMode, 'simple')
    store.setPickerMode('matrix')
    store.resetTransientControls()
    assert.equal(store.pickerMode, 'matrix')
    store.setPickerMode('bogus')
    assert.equal(store.pickerMode, 'matrix')
})

test('going back to the simple picker turns auto-select off', () => {
    const store = setup()
    store.setPickerMode('matrix')
    store.autoSelectBest = true
    store.setPickerMode('simple')
    assert.equal(store.autoSelectBest, false)
})
