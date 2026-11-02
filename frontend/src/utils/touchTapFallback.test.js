import test from 'node:test'
import assert from 'node:assert/strict'
import { createTouchTapFallback } from './touchTapFallback.js'

function setup() {
    let t = 0
    let seq = 0
    const timers = new Map()
    const tap = createTouchTapFallback({
        setTimer: (fn, ms) => { const h = ++seq; timers.set(h, { fn, at: t + ms }); return h },
        clearTimer: h => timers.delete(h),
        now: () => t,
    })
    const advance = ms => {
        t += ms
        for (const [h, timer] of [...timers]) {
            if (timer.at <= t) { timers.delete(h); timer.fn() }
        }
    }
    return { tap, advance, timers }
}
const touch = { pointerType: 'touch' }

test('touch up then no click runs the action once after the delay', () => {
    const { tap, advance } = setup()
    let n = 0
    tap.down('a', touch)
    tap.up('a', touch, () => n++)
    advance(349)
    assert.equal(n, 0)
    advance(1)
    assert.equal(n, 1)
    advance(5000)
    assert.equal(n, 1)
})

test('a click before the delay cancels the fallback and is handled', () => {
    const { tap, advance } = setup()
    let n = 0
    tap.down('a', touch)
    tap.up('a', touch, () => n++)
    advance(100)
    assert.equal(tap.click('a'), true)
    advance(1000)
    assert.equal(n, 0)
})

test('a click after the fallback ran is ignored within the window, handled after', () => {
    const { tap, advance } = setup()
    tap.down('a', touch)
    tap.up('a', touch, () => {})
    advance(350)
    advance(500)
    assert.equal(tap.click('a'), false)

    tap.down('a', touch)
    tap.up('a', touch, () => {})
    advance(350)
    advance(1000)
    assert.equal(tap.click('a'), true)
})

test('a late click on another button is handled', () => {
    const { tap, advance } = setup()
    tap.down('a', touch)
    tap.up('a', touch, () => {})
    advance(350)
    assert.equal(tap.click('b'), true)
})

test('mouse and pen never arm', () => {
    for (const pointerType of ['mouse', 'pen']) {
        const { tap, timers } = setup()
        tap.down('a', { pointerType })
        tap.up('a', { pointerType }, () => assert.fail('must not run'))
        assert.equal(timers.size, 0)
        assert.equal(tap.click('a'), true)
    }
})

test('pointercancel cancels the pending fallback', () => {
    const { tap, advance } = setup()
    let n = 0
    tap.down('a', touch)
    tap.up('a', touch, () => n++)
    tap.cancel()
    advance(1000)
    assert.equal(n, 0)
})

test('a pointerdown elsewhere cancels the pending fallback', () => {
    const { tap, advance } = setup()
    let n = 0
    tap.down('a', touch)
    tap.up('a', touch, () => n++)
    tap.down('b', touch)
    advance(1000)
    assert.equal(n, 0)
})

test('up on a different button than down never arms', () => {
    const { tap, timers } = setup()
    tap.down('a', touch)
    tap.up('b', touch, () => assert.fail('must not run'))
    assert.equal(timers.size, 0)
})

test('two successive taps work independently', () => {
    const { tap, advance } = setup()
    const ran = []
    tap.down('a', touch)
    tap.up('a', touch, () => ran.push('a'))
    advance(350)
    tap.down('b', touch)
    tap.up('b', touch, () => ran.push('b'))
    advance(350)
    assert.deepEqual(ran, ['a', 'b'])
})

test('cancel() while armed means the action never runs', () => {
    const { tap, advance, timers } = setup()
    tap.down('a', touch)
    tap.up('a', touch, () => assert.fail('must not run'))
    tap.cancel()
    assert.equal(timers.size, 0)
    advance(5000)
})

test('a new tap within the window after a fallback run is handled normally', () => {
    const { tap, advance } = setup()
    let n = 0
    tap.down('a', touch)
    tap.up('a', touch, () => n++)
    advance(350)
    advance(100)
    tap.down('a', touch)
    tap.up('a', touch, () => n++)
    assert.equal(tap.click('a'), true)
    advance(1000)
    assert.equal(n, 1)
})

test('the fallback arms for a 3 px move and not for a 25 px move', () => {
    const { tap, timers } = setup()
    tap.down('a', { pointerType: 'touch', clientX: 10, clientY: 10 })
    tap.up('a', { pointerType: 'touch', clientX: 13, clientY: 10 }, () => {})
    assert.equal(timers.size, 1)
    tap.down('a', { pointerType: 'touch', clientX: 10, clientY: 10 })
    tap.up('a', { pointerType: 'touch', clientX: 10, clientY: 35 }, () => {})
    assert.equal(timers.size, 0)
})
