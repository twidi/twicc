import assert from 'node:assert/strict'
import test from 'node:test'

import {
    TIMELINE_CHANGED_EVENT, WS_FIRST_OPEN_EVENT, WS_RECONNECTED_EVENT, bindTimelineEvents,
} from './liveEvents.js'

function setup() {
    const target = new EventTarget()
    const calls = []
    const feed = { notifyLatest: (id) => calls.push(['latest', id]), refresh: () => calls.push(['refresh']) }
    return { target, calls, unbind: bindTimelineEvents(target, feed) }
}

test('the event names are the ones useWebSocket.js dispatches', () => {
    assert.deepEqual([TIMELINE_CHANGED_EVENT, WS_RECONNECTED_EVENT, WS_FIRST_OPEN_EVENT], [
        'twicc:timeline-changed', 'twicc:ws-reconnected', 'twicc:ws-first-open',
    ])
})

test('a signal hands its latest id to the feed', () => {
    const { target, calls } = setup()
    target.dispatchEvent(new CustomEvent(TIMELINE_CHANGED_EVENT, { detail: { latestId: 42 } }))
    assert.deepEqual(calls, [['latest', 42]])
})

test('a malformed signal is ignored', () => {
    const { target, calls } = setup()
    for (const detail of [undefined, {}, { latestId: null }, { latestId: '7' }, { latestId: 1.5 }]) {
        target.dispatchEvent(new CustomEvent(TIMELINE_CHANGED_EVENT, { detail }))
    }
    assert.deepEqual(calls, [])
})

test('a reconnection and the first OPEN of the socket both refresh the feed', () => {
    const { target, calls } = setup()
    target.dispatchEvent(new Event(WS_RECONNECTED_EVENT))
    target.dispatchEvent(new Event(WS_FIRST_OPEN_EVENT))
    assert.deepEqual(calls, [['refresh'], ['refresh']])
})

test('resync_required is not a timeline event: nothing else is listened to', () => {
    const { target, calls } = setup()
    for (const name of ['twicc:resync-required', 'resync_required', 'twicc:ws-open', 'twicc:ws-closed']) {
        target.dispatchEvent(new Event(name))
    }
    assert.deepEqual(calls, [])
})

test('unbinding removes every listener', () => {
    const { target, calls, unbind } = setup()
    unbind()
    target.dispatchEvent(new CustomEvent(TIMELINE_CHANGED_EVENT, { detail: { latestId: 1 } }))
    target.dispatchEvent(new Event(WS_RECONNECTED_EVENT))
    target.dispatchEvent(new Event(WS_FIRST_OPEN_EVENT))
    assert.deepEqual(calls, [])
})
