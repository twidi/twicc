import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { createPinia, defineStore } from 'pinia'
import { computed, watch, nextTick } from 'vue'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants.js'
import { computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor } from '../utils/visualItems.js'
import { getParsedContent, setParsedContent } from '../utils/parsedContent.js'
import { buildBackgroundWorkStatusLines } from '../utils/backgroundWork.js'
import { isBufferActive } from '../utils/streamingBuffer.js'

const source = readFileSync(new URL('./data.js', import.meta.url), 'utf8')
function section(startText, endText) {
    const start = source.indexOf(startText)
    const end = source.indexOf(endText, start)
    assert.ok(start >= 0 && end > start)
    return source.slice(start, end)
}
const helperSource = section('function computeLastToolVisible(', '\n/**')
const actionSource = section('        recomputeVisualItems(sessionId)', '\n        /**')
let id = 0
function fixture({ working = false, items = [message(1)] } = {}) {
    const settings = { getDisplayMode: DISPLAY_MODE.NORMAL, areMessageTimestampsShown: false }
    const deps = { useSettingsStore: () => settings, DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM,
        computeVisualItems, visualItemEqual, insertDaySeparators, makeBackgroundWorkStatusItem, markLiveTimestampAnchor,
        getParsedContent, setParsedContent, buildBackgroundWorkStatusLines, isBufferActive,
        isLaunchedEphemeral: () => false }
    const actions = new Function(...Object.keys(deps), `${helperSource}\nreturn { ${actionSource} }`)(...Object.values(deps))
    const store = defineStore(`visual-stability-${++id}`, {
        state: () => ({ sessions: { main: { id: 'main', provider: 'claude_code' } }, sessionItems: { main: items },
            processStates: working ? { main: { state: PROCESS_STATE.ASSISTANT_TURN, label: 'working', tools: [] } } : {},
            localState: { failedSends: {}, optimisticMessages: {}, sessionDebugOverride: {}, sessionExpandedGroups: {},
                sessionDetailedBlocks: {}, streamingBlocks: {}, sessionVisualItems: {}, visualItemCache: {} } }), actions,
    })(createPinia())
    store.recomputeVisualItems('main')
    return { store, settings, rows: () => store.localState.sessionVisualItems.main }
}
function message(line, extra = {}) {
    return { line_num: line, kind: 'assistant_message', display_level: DISPLAY_LEVEL.ALWAYS,
        content: JSON.stringify({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: `Message ${line}` }] } }), ...extra }
}

test('same-content recompute preserves the visual list and its subscribers', async () => {
    const f = fixture(), before = f.rows()
    const rows = computed(() => f.rows())
    let updates = 0
    const stop = watch(rows, () => updates++)
    try {
        f.store.recomputeVisualItems('main')
        await nextTick()
        assert.strictEqual(f.rows(), before)
        assert.equal(updates, 0)
    } finally { stop() }
})

test('empty recomputes preserve the empty visual list', () => {
    const f = fixture({ items: [] }), before = f.rows()
    f.store.recomputeVisualItems('main')
    assert.strictEqual(f.rows(), before)
})

test('insertion deletion and order changes replace the visual list', () => {
    const f = fixture(), before = f.rows()
    f.store.sessionItems.main.push(message(2))
    f.store.recomputeVisualItems('main')
    assert.notStrictEqual(f.rows(), before)
    assert.deepEqual(f.rows().map(r => r.lineNum), [1, 2])
    const inserted = f.rows()
    f.store.sessionItems.main.reverse()
    f.store.recomputeVisualItems('main')
    assert.notStrictEqual(f.rows(), inserted)
    assert.deepEqual(f.rows().map(r => r.lineNum), [2, 1])
    f.store.sessionItems.main.pop()
    f.store.recomputeVisualItems('main')
    assert.deepEqual(f.rows().map(r => r.lineNum), [2])
})

test('display mode and group expansion still change visible rows', () => {
    const f = fixture({ items: [message(1), message(2, { display_level: DISPLAY_LEVEL.DEBUG_ONLY })] })
    assert.deepEqual(f.rows().map(r => r.lineNum), [1])
    f.settings.getDisplayMode = DISPLAY_MODE.DEBUG
    f.store.recomputeVisualItems('main')
    assert.deepEqual(f.rows().map(r => r.lineNum), [1, 2])
    f.settings.getDisplayMode = DISPLAY_MODE.SIMPLIFIED
    f.store.sessionItems.main = [message(1, { display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: 1, group_tail: 2 }),
        message(2, { display_level: DISPLAY_LEVEL.COLLAPSIBLE, group_head: 1, group_tail: 2 })]
    f.store.recomputeVisualItems('main')
    assert.deepEqual(f.rows().map(r => r.lineNum), [1])
    f.store.localState.sessionExpandedGroups.main = [1]
    f.store.recomputeVisualItems('main')
    assert.deepEqual(f.rows().map(r => r.lineNum), [1, 2])
})

test('unchanged working status preserves parsed content', () => {
    const f = fixture({ working: true }), before = f.rows(), row = before.at(-1), parsed = getParsedContent(row)
    f.store.processStates.main.tools = []
    f.store.recomputeVisualItems('main')
    assert.strictEqual(f.rows(), before)
    assert.strictEqual(getParsedContent(f.rows().at(-1)), parsed)
})

test('working label tools and target changes remain visible', () => {
    const f = fixture({ working: true })
    for (const change of [
        { label: 'waiting for sub-agent' },
        { label: 'compacting' },
        { tools: [{ id: 'read', name: 'Read', input: { file_path: '/fixture/file' } }] },
        { lastStartedToolId: 'read' },
    ]) {
        const before = f.rows().at(-1)
        Object.assign(f.store.processStates.main, change)
        f.store.recomputeVisualItems('main')
        assert.notStrictEqual(f.rows().at(-1), before)
        for (const [key, value] of Object.entries(change)) assert.deepEqual(getParsedContent(f.rows().at(-1))[key], value)
    }
})
