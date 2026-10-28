import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { createPinia, defineStore } from 'pinia'
import * as agentLinks from '../../utils/agentLinkIndex.js'
import * as visualItems from '../../utils/visualItems.js'
import * as parsed from '../../utils/parsedContent.js'
import * as contentVisibility from '../../utils/contentVisibility.js'
import * as constants from '../../constants.js'
import { getSessionCutoffMs } from '../../utils/sessions.js'
import { connectShareLive } from './shareLive.js'

const { DISPLAY_LEVEL, DISPLAY_MODE } = constants

// The real shim, its imports replaced by injected modules.
function makeStore(displayMode = DISPLAY_MODE.SIMPLIFIED) {
    const source = readFileSync(new URL('./dataStoreShim.js', import.meta.url), 'utf8')
    const body = source.replace(/^import .+ from .+\n/gm, '').replace('export const useDataStore', 'const useDataStore')
    const settings = { getDisplayMode: displayMode, areMessageTimestampsShown: false }
    const deps = { ...agentLinks, ...visualItems, ...parsed, ...contentVisibility, ...constants, defineStore,
        markRaw: Vue.markRaw, toRaw: Vue.toRaw, getSessionCutoffMs, shareApi: () => ({}), useSettingsStore: () => settings }
    const useDataStore = new Function(...Object.keys(deps), `${body}; return useDataStore`)(...Object.values(deps))
    return useDataStore(createPinia())
}

const message = line => ({ line_num: line, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
    group_head: null, group_tail: null, timestamp: null,
    content: JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [
        { type: 'text', text: 'Working' }, { type: 'tool_use', id: `tool-${line}`, name: 'Read', input: {} }] } }) })
const tool = (line, head) => ({ line_num: line, kind: 'tool_use', display_level: DISPLAY_LEVEL.COLLAPSIBLE,
    group_head: head, group_tail: line, timestamp: null, content: JSON.stringify({ type: 'user', message: { content: [] } }) })

test('a live group tail rewrite gives the message its group toggle', () => {
    const store = makeStore()
    store.addSessionItems('s', [message(1)])
    assert.equal(store.getSessionVisualItems('s')[0].suffixGroupHead, undefined)

    // The next tool joins the message's suffix: the backend rewrites line 1's group_tail.
    store.addSessionItems('s', [tool(2, 1)], [{ ...message(1), content: undefined, group_tail: 2 }])
    const [row] = store.getSessionVisualItems('s')
    assert.equal(row.lineNum, 1)
    assert.equal(row.suffixGroupHead, 1, 'the message now owns the toggle of the hidden tool')
    assert.equal(store.getSessionItem('s', 1).group_tail, 2)
    assert.ok(parsed.hasContent(store.getSessionItem('s', 1)), 'a metadata update keeps the loaded content')
})

test('an expanded internal suffix stays expanded once it becomes a group', () => {
    const store = makeStore()
    store.addSessionItems('s', [message(1)])
    store.toggleInternalExpandedGroup('s', 1, 1)
    store.addSessionItems('s', [tool(2, 1)], [{ ...message(1), content: undefined, group_tail: 2 }])
    assert.deepEqual(store.getInternalExpandedGroups('s', 1), [])
    assert.deepEqual(store.getSessionVisualItems('s').map(row => row.lineNum), [1, 2], 'the tool stays visible')
})

test('an update for a row not loaded yet is left to the metadata fetch', () => {
    const store = makeStore()
    store.addSessionItems('s', [tool(5, 5)], [{ ...message(3), content: undefined, group_tail: 5 }])
    assert.equal(store.getSessionItem('s', 3), null)
})

test('the live socket hands updated_metadata to its items callback', () => {
    const previousSocket = globalThis.WebSocket, previousLocation = globalThis.location
    let socket
    globalThis.location = { origin: 'https://share.example.test' }
    globalThis.WebSocket = class { constructor() { socket = this } close() {} }
    try {
        const calls = []
        const disconnect = connectShareLive({ tokenPath: '/share/token/', sessionId: 'root', onItems: (...args) => calls.push(args) })
        socket.onmessage({ data: JSON.stringify({ type: 'share_items_added', session_id: 'root', items: [], updated_metadata: [{ line_num: 1 }] }) })
        socket.onmessage({ data: JSON.stringify({ type: 'share_items_added', session_id: 'root', items: [{ line_num: 2 }] }) })
        assert.deepEqual(calls, [[[], 'root', [{ line_num: 1 }]], [[{ line_num: 2 }], 'root', null]])
        disconnect()
    } finally {
        globalThis.WebSocket = previousSocket
        globalThis.location = previousLocation
    }
})
