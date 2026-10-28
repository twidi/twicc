// Run with: node --test src/utils/detachedAnchorGuard.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import WaTooltip from '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import WaPopover from '@awesome.me/webawesome/dist/components/popover/popover.js'
import { installDetachedAnchorGuard } from './detachedAnchorGuard.js'

function fakeClass() {
    const calls = []
    class Fake {}
    Fake.prototype.handleForChange = function (...args) { calls.push(args); return 'resolved' }
    return { Fake, calls }
}

test('the lookup runs while attached and is skipped while detached', () => {
    const { Fake, calls } = fakeClass()
    installDetachedAnchorGuard([Fake])
    const attached = Object.assign(new Fake(), { getRootNode: () => ({ getElementById: () => null }) })
    assert.equal(attached.handleForChange('old', 'new'), 'resolved')
    assert.deepEqual(calls, [['old', 'new']])
    const detached = Object.assign(new Fake(), { getRootNode: () => ({ tagName: 'DIV' }) })
    assert.equal(detached.handleForChange('old', 'new'), undefined)
    assert.equal(calls.length, 1)
})

test('installing twice wraps once; a missing handleForChange fails loudly', () => {
    const { Fake } = fakeClass()
    installDetachedAnchorGuard([Fake])
    const wrapped = Fake.prototype.handleForChange
    installDetachedAnchorGuard([Fake])
    assert.equal(Fake.prototype.handleForChange, wrapped)
    class Renamed {}
    assert.throws(() => installDetachedAnchorGuard([Renamed]), /handleForChange/)
})

test('Web Awesome contract guard: tooltip and popover resolve `for` in handleForChange', () => {
    for (const target of [WaTooltip, WaPopover]) {
        const source = String(target.prototype.handleForChange)
        assert.match(source, /getRootNode\(\)/, target.name)
        assert.match(source, /getElementById/, target.name)
    }
})
