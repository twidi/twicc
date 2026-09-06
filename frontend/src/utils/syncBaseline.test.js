import assert from 'node:assert/strict'
import test from 'node:test'

import { createSyncBaseline } from './syncBaseline.js'

const snapshot = {
    projects: { p1: { mtime: 100 }, p2: { mtime: 200 } },
    sessions: { s1: { mtime: 10 }, s2: { mtime: 20 } },
}

test('without a baseline, the current mtime is the comparison point', () => {
    const baseline = createSyncBaseline()

    assert.equal(baseline.pending, false)
    assert.equal(baseline.projectMtime('p1', 100), 100)
    assert.equal(baseline.sessionMtime('s1', 10), 10)
})

test('a captured baseline survives a live overwrite of the current mtime', () => {
    // The outage race: a session_updated / project_updated frame received on
    // the reopened socket refreshes the local mtime to the server value, which
    // then reads as "unchanged" against the fresh REST payload. The baseline
    // frozen at disconnect must still expose the pre-outage value.
    const baseline = createSyncBaseline()
    baseline.capture(snapshot)

    assert.equal(baseline.pending, true)
    assert.equal(baseline.projectMtime('p1', 150), 100)
    assert.equal(baseline.sessionMtime('s1', 15), 10)
})

test('the first baseline wins while one is pending', () => {
    // A second disconnect during the reconciliation must not replace the
    // pre-outage snapshot with mtimes already refreshed by live frames.
    const baseline = createSyncBaseline()
    assert.equal(baseline.capture(snapshot), true)
    assert.equal(baseline.capture({
        projects: { p1: { mtime: 999 } },
        sessions: { s1: { mtime: 999 } },
    }), false)

    assert.equal(baseline.projectMtime('p1', 999), 100)
    assert.equal(baseline.sessionMtime('s1', 999), 10)
})

test('clearing the baseline restores the current-mtime comparison', () => {
    const baseline = createSyncBaseline()
    baseline.capture(snapshot)
    baseline.clear()

    assert.equal(baseline.pending, false)
    assert.equal(baseline.projectMtime('p1', 150), 150)
    assert.equal(baseline.sessionMtime('s1', 15), 15)
})

test('an id unknown at capture time falls back to the current mtime', () => {
    // A session loaded after the disconnect (REST still up, socket down) has
    // no pre-outage value; its current mtime is the only honest reference.
    const baseline = createSyncBaseline()
    baseline.capture(snapshot)

    assert.equal(baseline.projectMtime('p-new', 300), 300)
    assert.equal(baseline.sessionMtime('s-new', 30), 30)
    assert.equal(baseline.sessionMtime('s-new', undefined), undefined)
})
