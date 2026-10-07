import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createRailSessionLongPress } from './railSessionLongPress.js'

function fixture(t) {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const document = new EventTarget()
    const source = { isConnected: true }
    const shown = []
    const hidden = []
    let openId = null
    const controller = createRailSessionLongPress({ document,
        isOpen: id => openId === id,
        show: id => { openId = id; shown.push(id) },
        hide: id => { if (!id || openId === id) openId = null; hidden.push(id || true) } })
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

test('a second hold on an open preview closes it and still suppresses navigation', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450); f.pointer('pointerup'); assert.equal(f.click(), true)
    f.down(); t.mock.timers.tick(450); f.pointer('pointerup'); assert.equal(f.click(), true)
    assert.deepEqual(f.shown, ['one'])
    assert.deepEqual(f.hidden, ['one'])
    t.mock.timers.tick(3000)
    assert.deepEqual(f.hidden, ['one'], 'closing cancels automatic dismissal')
})

test('a touch preview closes after three seconds and still suppresses a delayed synthetic click', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450); f.pointer('pointerup')
    t.mock.timers.tick(2999); assert.deepEqual(f.hidden, [])
    t.mock.timers.tick(1); assert.deepEqual(f.hidden, ['one'])
    assert.equal(f.click(), true)
})

test('opening a different entry resets the timeout and only closes that entry', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450); t.mock.timers.tick(1500)
    f.controller.start('two', { currentTarget: f.source, pointerType: 'touch', pointerId: 1,
        isPrimary: true, button: 0, clientX: 20, clientY: 20 })
    t.mock.timers.tick(450)
    t.mock.timers.tick(1500)
    assert.deepEqual(f.hidden, [])
    t.mock.timers.tick(1500)
    assert.deepEqual(f.hidden, ['two'])
})

for (const action of ['cancel', 'dispose']) {
    test(`${action} clears the automatic dismissal timer`, t => {
        const f = fixture(t)
        f.down(); t.mock.timers.tick(450)
        f.controller[action]()
        const count = f.hidden.length
        t.mock.timers.tick(3000)
        assert.equal(f.hidden.length, count)
    })
}

test('dismissal of another entry does not clear the active preview timeout', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450)
    f.controller.clearAutoHide('two')
    t.mock.timers.tick(3000)
    assert.deepEqual(f.hidden, ['one'])
})

test('native dismissal clears the active preview timeout', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450)
    f.controller.clearAutoHide('one')
    t.mock.timers.tick(3000)
    assert.deepEqual(f.hidden, [])
})

test('switching to a mouse press clears automatic dismissal', t => {
    const f = fixture(t)
    f.down(); t.mock.timers.tick(450)
    f.down({ pointerType: 'mouse' })
    t.mock.timers.tick(3000)
    assert.deepEqual(f.hidden, [])
    assert.deepEqual(f.shown, ['one'])
})
