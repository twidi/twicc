import test from 'node:test'
import assert from 'node:assert/strict'

import {
    registerSessionLinkComposer, getSessionLinkComposer, useSessionLinkTarget,
    sessionLinkDrag, startSessionLinkDrag, cancelSessionLinkDrag, isSessionLinkDragActive,
} from './useSessionLinkDrag.js'

// The module reads `window` and `document` only while a drag runs; stub them
// after the imports (Vue's DOM runtime probes `document` when it loads).
const win = new EventTarget()
let zoneAt = () => null
globalThis.window = win
globalThis.document = {
    elementFromPoint: (x, y) => zoneAt(x, y),
    body: { style: { userSelect: '' }, classList: { add() {}, remove() {} } },
}

const session = { id: 'src', title: 'Source', projectId: 'p', markdown: '[Source](/project/p/session/src)' }
const move = (type, x, y) => Object.assign(new Event(type), { pointerId: 7, clientX: x, clientY: y })
const chat = id => ({ closest: () => ({ getAttribute: () => id }) })

function recordEnds() {
    const ends = []
    const listener = event => ends.push(event.detail.inserted)
    win.addEventListener('twicc:session-link-drag-end', listener)
    return { ends, stop: () => win.removeEventListener('twicc:session-link-drag-end', listener) }
}

test('the registry follows the mounted composers and drives the link target', () => {
    const entry = { el: () => null, insert: async () => true }
    const route = { params: { sessionId: 'a' } }
    const { targetSessionId } = useSessionLinkTarget(route)
    assert.equal(targetSessionId.value, null)
    const unregister = registerSessionLinkComposer('a', entry)
    assert.equal(getSessionLinkComposer('a').insert, entry.insert)
    assert.equal(targetSessionId.value, 'a')
    const newer = { el: () => null, insert: async () => true }
    const unregisterNewer = registerSessionLinkComposer('a', newer)
    unregister()
    assert.equal(getSessionLinkComposer('a').insert, newer.insert, 'an older unregister keeps the newer entry')
    unregisterNewer()
    assert.equal(targetSessionId.value, null)
})

test('a release over another chat after 10 px of travel inserts the link', async () => {
    const inserted = []
    const unregister = registerSessionLinkComposer('target', { el: () => null, insert: async (markdown, opts) => { inserted.push([markdown, opts]); return true } })
    const { ends, stop } = recordEnds()
    zoneAt = () => chat('target')
    startSessionLinkDrag(session, { pointerId: 7, pointerType: 'touch', x: 0, y: 0 })
    win.dispatchEvent(move('pointermove', 6, 0))
    assert.equal(sessionLinkDrag.overSessionId, null, 'not yet 10 px')
    win.dispatchEvent(move('pointermove', 12, 0))
    assert.equal(sessionLinkDrag.overSessionId, 'target')
    win.dispatchEvent(move('pointerup', 12, 0))
    await new Promise(resolve => setImmediate(resolve))
    assert.deepEqual(inserted, [[session.markdown, { input: 'touch' }]])
    assert.deepEqual(ends, [true])
    assert.equal(isSessionLinkDragActive(), false)
    assert.equal(sessionLinkDrag.session, null)
    stop(); unregister()
})

test('a release elsewhere, on the source chat, or after Escape inserts nothing', async () => {
    let calls = 0
    const unregisters = ['target', 'src'].map(id => registerSessionLinkComposer(id, { el: () => null, insert: async () => { calls++; return true } }))
    const { ends, stop } = recordEnds()
    for (const [zone, end] of [[null, 'up'], ['src', 'up'], ['target', 'escape']]) {
        zoneAt = () => (zone ? chat(zone) : null)
        startSessionLinkDrag(session, { pointerId: 7, pointerType: 'mouse', x: 0, y: 0 })
        win.dispatchEvent(move('pointermove', 30, 0))
        if (end === 'up') win.dispatchEvent(move('pointerup', 30, 0))
        else win.dispatchEvent(Object.assign(new Event('keydown'), { key: 'Escape' }))
        await new Promise(resolve => setImmediate(resolve))
    }
    assert.equal(calls, 0)
    assert.deepEqual(ends, [false, false, false])
    stop(); unregisters.forEach(unregister => unregister())
})

test('a click right after a drag is swallowed', () => {
    startSessionLinkDrag(session, { pointerId: 7, pointerType: 'mouse', x: 0, y: 0 })
    cancelSessionLinkDrag()
    const click = new Event('click', { cancelable: true })
    win.dispatchEvent(click)
    assert.equal(click.defaultPrevented, true)
})
