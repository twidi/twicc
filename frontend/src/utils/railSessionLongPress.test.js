import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRailSessionLongPress } from './railSessionLongPress.js'

function fixture(t) {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const document = new EventTarget()
    const source = { isConnected: true }
    const shown = []
    const hidden = []
    const controller = createRailSessionLongPress({ document,
        show: id => shown.push(id), hide: () => hidden.push(true) })
    const pointer = (type, overrides = {}) => {
        const event = new Event(type, { cancelable: true })
        Object.assign(event, { pointerType: 'touch', pointerId: 1, isPrimary: true,
            clientX: 20, clientY: 20, button: 0, ...overrides })
        document.dispatchEvent(event)
        return event
    }
    const down = (overrides = {}) => controller.start('one', { currentTarget: source,
        pointerType: 'touch', pointerId: 1, isPrimary: true, clientX: 20, clientY: 20, button: 0, ...overrides })
    const click = (overrides = {}) => {
        const event = new Event('click', { cancelable: true })
        Object.assign(event, { detail: 1, ...overrides })
        return controller.consumeClick('one', event)
    }
    t.after(() => controller.dispose())
    return { controller, source, shown, hidden, pointer, down, click }
}

test('short tap navigates; stationary hold opens only at 450ms and stays after release', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(449)
    assert.deepEqual(f.shown, [])
    f.pointer('pointerup'); assert.equal(f.click(), false)
    f.down(); t.mock.timers.tick(449); assert.deepEqual(f.shown, [])
    t.mock.timers.tick(1); assert.deepEqual(f.shown, ['one'])
    f.pointer('pointerup'); assert.equal(f.click(), true)
    assert.deepEqual(f.hidden, [])
    f.down(); f.pointer('pointerup'); assert.equal(f.click(), false)
})

for (const [name, event, overrides] of [
    ['movement', 'pointermove', { clientY: 31 }], ['scroll', 'scroll', {}],
    ['cancel', 'pointercancel', {}], ['multi-touch', 'pointerdown', { pointerId: 2, isPrimary: false }],
]) {
    test(`${name} cancels the pending hold without swallowing a click`, t => {
        const f = fixture(t)
        f.down(); t.mock.timers.tick(200); f.pointer(event, overrides); t.mock.timers.tick(450)
        assert.deepEqual(f.shown, [])
        assert.equal(f.click(), false)
    })
}

test('10px movement remains eligible; mouse and non-primary touches never start', t => {
    const f = fixture(t)
    f.down({ pointerType: 'mouse' }); t.mock.timers.tick(450)
    f.down({ isPrimary: false }); t.mock.timers.tick(450)
    assert.deepEqual(f.shown, [])
    f.down(); f.pointer('pointermove', { clientX: 30 }); t.mock.timers.tick(450)
    assert.deepEqual(f.shown, ['one'])
})

for (const action of ['cancel', 'dispose', 'source removal']) {
    test(`${action} prevents a delayed tooltip`, t => {
        const f = fixture(t)
        f.down()
        if (action === 'source removal') f.source.isConnected = false
        else f.controller[action]()
        t.mock.timers.tick(450)
        assert.deepEqual(f.shown, [])
    })
}

test('keyboard activation is never swallowed and cancellation hides an already open preview', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450); f.pointer('pointerup')
    assert.equal(f.click({ detail: 0 }), false)
    f.controller.cancel()
    assert.equal(f.hidden.length, 1)
    assert.equal(f.click(), true, 'successful hold still suppresses its delayed click')
    f.down(); f.pointer('pointerup'); assert.equal(f.click(), false)
})

test('a deliberate mouse press clears suppression after a cancelled successful touch hold', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450); f.pointer('pointercancel')
    f.down({ pointerType: 'mouse' })
    assert.equal(f.click(), false)
})
