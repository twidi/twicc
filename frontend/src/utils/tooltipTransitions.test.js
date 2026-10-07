import test from 'node:test'
import assert from 'node:assert/strict'
import WaTooltip from '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import { serializeTooltipTransitions } from './tooltipTransitions.js'

// Exercise the installed native lifecycle. Only the DOM animation surface is fake.
function fixture(t, animated = true, initialOpen = false) {
    const saved = { document: globalThis.document, requestAnimationFrame: globalThis.requestAnimationFrame }
    globalThis.document = new EventTarget()
    globalThis.requestAnimationFrame = callback => queueMicrotask(callback)
    class Surface extends EventTarget {
        classes = new Set()
        classList = { contains: name => this.classes.has(name), add: name => this.classes.add(name), remove: name => this.classes.delete(name) }
        getAnimations() { return animated && this.classes.size ? [{}] : [] }
        finish() { this.dispatchEvent(new Event('animationend')) }
    }
    const surface = new Surface(), el = new EventTarget(), events = []
    Object.assign(el, {
        open: initialOpen, disabled: false, isConnected: true,
        eventController: new AbortController(), handleDocumentKeyDown() {},
        body: { hidden: true }, popup: { active: false, popup: surface, reposition() {} },
        handleOpenChange: WaTooltip.prototype.handleOpenChange,
        show: WaTooltip.prototype.show, hide: WaTooltip.prototype.hide,
    })
    serializeTooltipTransitions(el)
    for (const name of ['wa-show', 'wa-hide', 'wa-after-show', 'wa-after-hide']) el.addEventListener(name, () => events.push(name))
    t.after(() => { el.eventController.abort(); Object.assign(globalThis, saved) })
    const change = value => { el.open = value; return el.handleOpenChange() }
    return { el, surface, events, change }
}

async function flush() { for (let i = 0; i < 12; i++) await Promise.resolve() }
async function finish(f) {
    for (let i = 0; i < 8; i++) {
        await flush()
        if (!f.surface.classes.size) return
        f.surface.finish()
    }
    await flush()
    assert.equal(f.surface.classes.size, 0, 'all native animations complete')
}

test('a tooltip reopened during its hide animation ends visible and closes normally afterwards', async t => {
    const f = fixture(t)
    const first = f.change(true); await finish(f); await first
    const closing = f.change(false); await flush()
    const reopening = f.change(true)
    await finish(f); await Promise.all([closing, reopening])
    assert.equal(f.el.open, true)
    assert.equal(f.el.body.hidden, false)
    assert.equal(f.el.popup.active, true)
    const final = f.change(false); await finish(f); await final
    assert.equal(f.el.body.hidden, true); assert.equal(f.el.popup.active, false)
})

test('a close during an opening ends hidden with the native completion events', async t => {
    const f = fixture(t)
    const opening = f.change(true); await flush()
    const closing = f.change(false)
    await finish(f); await Promise.all([opening, closing])
    assert.equal(f.el.body.hidden, true); assert.equal(f.el.popup.active, false)
    assert.equal(f.events.at(-1), 'wa-after-hide')
})

test('rapid repeated changes settle on the latest state without duplicate native openings', async t => {
    const f = fixture(t)
    const opening = f.change(true); await flush()
    const operations = [opening, f.change(false), f.change(true), f.change(false), f.change(true)]
    let completed = false
    Promise.all(operations).then(() => { completed = true })
    await finish(f); await flush()
    assert.equal(completed, true, 'no native transition remains pending')
    assert.equal(f.el.body.hidden, false); assert.equal(f.el.popup.active, true)
    assert.equal(f.events.filter(name => name === 'wa-show').length, 1)
})

test('zero-duration transitions retain their native lifecycle', async t => {
    const f = fixture(t, false)
    await f.change(true); assert.equal(f.el.body.hidden, false)
    await f.change(false); assert.equal(f.el.body.hidden, true)
    assert.deepEqual(f.events, ['wa-show', 'wa-after-show', 'wa-hide', 'wa-after-hide'])
})

test('a pending close cancelled before it starts does not register an already open tooltip again', async t => {
    const f = fixture(t)
    const first = f.change(true); await finish(f); await first
    const closing = f.change(false), reopening = f.change(true)
    await finish(f); await Promise.all([closing, reopening])
    assert.equal(f.events.filter(name => name === 'wa-show').length, 1)
    assert.equal(f.el.body.hidden, false); assert.equal(f.el.popup.active, true)
})

test('removal during a hide does not run a queued reopening', async t => {
    const f = fixture(t)
    const first = f.change(true); await finish(f); await first
    const closing = f.change(false); await flush()
    const reopening = f.change(true)
    f.el.isConnected = false
    await finish(f); await Promise.all([closing, reopening])
    assert.equal(f.el.body.hidden, true); assert.equal(f.el.popup.active, false)
    assert.equal(f.events.filter(name => name === 'wa-show').length, 1)
})

test('installation is idempotent and guards the native method contract', async t => {
    const f = fixture(t), wrapped = f.el.handleOpenChange
    serializeTooltipTransitions(f.el)
    assert.strictEqual(f.el.handleOpenChange, wrapped)
    assert.equal(typeof WaTooltip.prototype.handleOpenChange, 'function')
    assert.ok(WaTooltip.prototype.update.toString().includes('this[decoratedFnName]('), 'native watches call the instance method')
    assert.throws(() => serializeTooltipTransitions({}), /handleOpenChange/)
})

test('public requests settle even when an intermediate close is cancelled', async t => {
    const f = fixture(t)
    let open = false
    Object.defineProperty(f.el, 'open', {
        get: () => open,
        set: value => {
            if (value === open) return
            open = value
            queueMicrotask(() => f.el.handleOpenChange())
        },
    })
    let completed = 0
    const opening = f.el.show().then(() => completed++)
    await flush()
    const closing = f.el.hide().then(() => completed++)
    await flush()
    const reopening = f.el.show().then(() => completed++)
    await finish(f); await flush()
    assert.equal(completed, 3, 'cancelled public requests must not wait for events that never occur')
    await Promise.all([opening, closing, reopening])
    assert.equal(f.el.body.hidden, false)
    assert.equal(f.events.filter(name => name === 'wa-show').length, 1)
})

test('reconnection during opening restores the native keyboard listener', async t => {
    const f = fixture(t)
    let keydowns = 0
    f.el.handleDocumentKeyDown = () => keydowns++
    const opening = f.change(true); await flush()
    // Match the native disconnect cleanup and reconnect's fresh controller.
    document.removeEventListener('keydown', f.el.handleDocumentKeyDown)
    f.el.eventController.abort()
    f.el.eventController = new AbortController()
    const closing = f.change(false), reopening = f.change(true)
    await finish(f); await Promise.all([opening, closing, reopening])
    document.dispatchEvent(new Event('keydown'))
    assert.equal(keydowns, 1, 'the new connection has a keyboard listener')
    assert.equal(f.el.body.hidden, false)
})

test('an initially open tooltip runs its native registration lifecycle', async t => {
    const f = fixture(t, true, true)
    const opening = f.el.handleOpenChange()
    await finish(f); await opening
    assert.equal(f.events.filter(name => name === 'wa-show').length, 1)
    assert.equal(f.el.body.hidden, false)
})
