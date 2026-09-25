import assert from 'node:assert/strict'
import test from 'node:test'

import { applyBackgroundWork } from './wsProcessState.js'

const work = { subagents: 0, shells: 1, monitors: 0, scheduled_wakeup_at: null, goal: false }

test('patches the known process in place, keeping its live tools', () => {
    const tools = [{ id: 'tool-1' }]
    const processStates = { s1: { state: 'user_turn', tools, background_work_in_progress: null } }

    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: work }), true)
    assert.deepEqual(processStates.s1.background_work_in_progress, work)
    assert.equal(processStates.s1.tools, tools)
})

test('nothing running clears the value', () => {
    const processStates = { s1: { state: 'user_turn', background_work_in_progress: work } }

    applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: null })
    assert.equal(processStates.s1.background_work_in_progress, null)
})

test('an unknown session is left alone', () => {
    const processStates = {}

    assert.equal(applyBackgroundWork(processStates, { session_id: 's1', background_work_in_progress: work }), false)
    assert.deepEqual(processStates, {})
})
