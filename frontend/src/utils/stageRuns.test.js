import test from 'node:test'
import assert from 'node:assert/strict'
import { noteStageRun, observeStageRuns } from './stageRuns.js'

test('an observer sees each stage run until stop()', () => {
    const seen = []
    const stop = observeStageRuns(name => seen.push(name))
    noteStageRun('rootSessions')
    noteStageRun('scope:members')
    noteStageRun('rootSessions')
    stop()
    noteStageRun('scope:ordered')
    assert.deepEqual(seen, ['rootSessions', 'scope:members', 'rootSessions'])
    stop()
})

test('every registered observer sees the same runs', () => {
    const first = [], second = []
    const stopFirst = observeStageRuns(name => first.push(name))
    const stopSecond = observeStageRuns(name => second.push(name))
    noteStageRun('unread:counts')
    stopFirst()
    noteStageRun('memo:unread')
    stopSecond()
    assert.deepEqual(first, ['unread:counts'])
    assert.deepEqual(second, ['unread:counts', 'memo:unread'])
})

test('without an observer a run is dropped, not queued', () => {
    noteStageRun('blocks:natural')
    const seen = []
    const stop = observeStageRuns(name => seen.push(name))
    try {
        assert.deepEqual(seen, [])
        noteStageRun('blocks:natural')
        assert.deepEqual(seen, ['blocks:natural'])
    } finally { stop() }
})
