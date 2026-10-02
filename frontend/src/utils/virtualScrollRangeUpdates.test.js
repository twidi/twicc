import test from 'node:test'
import assert from 'node:assert/strict'
import { createVirtualScrollRangeUpdates } from './virtualScrollRangeUpdates.js'
const tuple = { startIndex: 0, endIndex: 8, visibleStartIndex: 0, visibleEndIndex: 3 }
for (const first of ['normal', 'initial']) {
    test(`initial obligation is consumed by ${first}, then changed tuples remain eligible`, () => {
        const updates = createVirtualScrollRangeUpdates()
        assert.equal(updates.admit(tuple, { reason: first, epoch: 0 }), true)
        assert.equal(updates.admit(tuple, { reason: first === 'normal' ? 'initial' : 'normal', epoch: 0 }), false)
        assert.equal(updates.admit({ ...tuple, visibleEndIndex: 4 }, { reason: 'normal', epoch: 0 }), true)
        assert.equal(updates.admit({ ...tuple, visibleEndIndex: 4, endIndex: 9 }, { reason: 'normal', epoch: 0 }), true)
    })
}
for (const first of ['normal', 'recovery']) for (const changed of [false, true]) {
    test(`recovery ${first} first, changed=${changed}`, () => {
        const updates = createVirtualScrollRangeUpdates()
        updates.admit(tuple, { reason: 'initial', epoch: 0 })
        const recovered = { ...tuple, endIndex: changed ? 9 : 8 }
        assert.equal(updates.admit(recovered, { reason: first, epoch: 1 }), true)
        assert.equal(updates.admit(recovered, { reason: first === 'normal' ? 'recovery' : 'normal', epoch: 1 }), false)
        assert.equal(updates.admit(tuple, { reason: 'recovery', epoch: 0 }), false)
    })
}
