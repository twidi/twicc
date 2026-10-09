import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createScrollDriver } from './scrollDriver.js'

function fixture({ scrollTop = 500, scrollHeight = 5000, clientHeight = 800 } = {}) {
    const listeners = new Map()
    const events = []
    const container = { scrollTop, scrollHeight, clientHeight,
        scrollBy({ top }) { this.scrollTop = Math.min(this.scrollHeight - this.clientHeight, Math.max(0, this.scrollTop + top)) },
        dispatchEvent(event) { events.push(event.type); return true },
        addEventListener: (type, fn) => listeners.set(type, fn), removeEventListener: type => listeners.delete(type) }
    let time = 0, pending = null
    const driver = createScrollDriver(container, { now: () => time,
        requestFrame: fn => { pending = fn; return 1 }, cancelFrame: () => { pending = null } })
    /** Run animation frames of 16 ms until none is pending (bounded). */
    const run = (frames = 600) => { for (let i = 0; i < frames && pending; i++) { time += 16; const fn = pending; pending = null; fn() } }
    return { container, driver, events, listeners, run, isRunning: () => pending != null }
}

test('a pixel wheel scrolls at once and tells the scroller a user is scrolling', () => {
    const f = fixture()
    f.driver.wheel(120, 0)
    assert.equal(f.container.scrollTop, 620)
    assert.deepEqual(f.events, ['wheel'])
    assert.equal(f.isRunning(), false)
})

test('a notched wheel eases to its target and accumulates fast notches', () => {
    const f = fixture()
    f.driver.wheel(3, 1) // 3 lines
    f.driver.wheel(3, 1)
    f.run()
    assert.equal(f.container.scrollTop, 500 + 6 * 19)
    assert.equal(f.isRunning(), false)
    f.driver.wheel(1, 2) // one page
    f.run()
    assert.equal(f.container.scrollTop, 500 + 6 * 19 + 800)
})

test('an eased scroll stops at the end of the chat instead of stalling', () => {
    const f = fixture({ scrollTop: 4150 })
    f.driver.wheel(10, 1)
    f.run()
    assert.equal(f.container.scrollTop, 4200)
    assert.equal(f.isRunning(), false)
})

test('touch moves follow the finger one to one and a fling keeps going, then stops', () => {
    const f = fixture()
    f.driver.touchStart()
    f.driver.touchMove(30)
    f.driver.touchMove(-10)
    assert.equal(f.container.scrollTop, 520)
    assert.deepEqual(f.events, ['touchmove', 'touchmove'])
    f.driver.touchEnd(2)
    f.run()
    const travelled = f.container.scrollTop - 520
    assert.ok(travelled > 400 && travelled < 1000, `momentum distance ${travelled}`)
    assert.equal(f.isRunning(), false)
})

test('a slow release does not fling, a new touch or real input cancels momentum', () => {
    const f = fixture()
    f.driver.touchEnd(0.01)
    assert.equal(f.isRunning(), false)
    f.driver.touchEnd(1)
    assert.equal(f.isRunning(), true)
    f.driver.touchStart()
    assert.equal(f.isRunning(), false)
    f.driver.touchEnd(1)
    f.listeners.get('wheel')({ isTrusted: false }) // our own synthetic notification
    assert.equal(f.isRunning(), true)
    f.listeners.get('wheel')({ isTrusted: true })
    assert.equal(f.isRunning(), false)
})

test('momentum stops at the chat edge and dispose removes the listeners', () => {
    const f = fixture({ scrollTop: 10 })
    f.driver.touchEnd(-3)
    f.run()
    assert.equal(f.container.scrollTop, 0)
    assert.equal(f.isRunning(), false)
    f.driver.dispose()
    assert.equal(f.listeners.size, 0)
})
