import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import * as blocks from '../../../utils/chatBlocks.js'
import * as visibility from '../../../utils/contentVisibility.js'
import * as media from '../../../utils/attachmentStrip.js'
import { DISPLAY_MODE } from '../../../constants.js'
import { isTranscriptLoadCandidate } from '../../../utils/chatEvents/rows.js'
import { compileComponent, makeRenderer, descendants, flush } from '../../../../tests/helpers/scrollerComponentHarness.js'
function loadFunction(path, name, dependencies) {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8').replace(/^import .*$/gm, '').replace(/export function /g, 'function ')
    return new Function(...Object.keys(dependencies), `${source}; return ${name}`)(...Object.values(dependencies))
}
const useChatNavigation = loadFunction('../../../composables/useChatNavigation.js', 'useChatNavigation', { computed: Vue.computed, ...blocks })
test('block navigation resolves keys through retained exit rows and events; default callers still work', async () => {
    for (const withDisplay of [true, false]) {
        const visualItems = Vue.ref([{ lineNum: 1, kind: 'user_message', isBlockStart: true }, { lineNum: 'event:7', isSessionEvent: true },
            { lineNum: 3, kind: 'assistant_message', isBlockStart: true }])
        const displayItems = Vue.ref([visualItems.value[0], { lineNum: 2 }, ...visualItems.value.slice(1)])
        const scrollerRef = Vue.ref({ positions: (withDisplay ? displayItems : visualItems).value.map((item, i) => ({ key: item.lineNum, top: i * 100 })),
            scrollTop: withDisplay ? 190 : 150, getScrollState: () => ({ scrollTop: withDisplay ? 190 : 150 }), isAtBottom: () => false, isAtTop: () => false })
        const jumps = []
        const nav = useChatNavigation({ scrollerRef, visualItems, ...(withDisplay ? { displayItems } : {}),
            scrollToItem: line => jumps.push(line), scrollToEdge: edge => jumps.push(edge) })
        await nav.goNextBlock(); assert.deepEqual(jumps, [3])
        await nav.goTop(); await nav.goBottom(); assert.deepEqual(jumps.slice(1), ['top', 'bottom'])
        assert.deepEqual(blocks.blockTargets(visualItems.value), [0, 2])
    }
})
test('mapped internal controls use their segment expansion and comment scope', async t => {
    const toggled = [], stub = { render: () => null }
    const ContentList = compileComponent(new URL('./items/claude_code/ContentList.vue', import.meta.url), {
        '../../../../../inline-artifacts/rendering.js': { createInlineTextContext: () => null },
        '../../../../../stores/data': { useDataStore: () => ({ getEffectiveDisplayMode: () => DISPLAY_MODE.SIMPLIFIED,
            getInternalExpandedGroups: () => [], toggleEventSegment: (...args) => toggled.push(args) }) },
        '../../../../../stores/codeComments': { useCodeCommentsStore: () => ({ countBySession: () => 3, countBySource: () => 3,
            getCommentsBySession: () => [1, 8, 11].map(toolLineNum => ({ source: 'tool', subagentSessionId: '', toolLineNum })) }) },
        '../../../../../constants': { DISPLAY_MODE }, '../../../../../utils/attachmentStrip': media,
        '../../../../../utils/contentVisibility': visibility, '../../GroupToggle.vue': { props: ['expanded', 'commentsCount'],
            setup: (props, { emit }) => () => Vue.h('button', { expanded: props.expanded, count: props.commentsCount, onClick: () => emit('toggle') }) },
        '../TextContent.vue': stub, '../../../../media/AttachmentStrip.vue': stub, '../ToolUseContent.vue': stub,
        './ThinkingContent.vue': stub, '../UnknownEntry.vue': stub,
    })
    const { renderer, root } = makeRenderer()
    const app = renderer.createApp(ContentList, { items: [{ type: 'tool_use', name: 'Read' }, { type: 'text', text: 'done' }, { type: 'tool_use', name: 'Read' }],
        role: 'assistant', projectId: 'p', sessionId: 's', lineNum: 8, groupTail: 12, suffixExpanded: false,
        suffixSegmentRange: { head: 8, tail: 9 }, eventInternalControls: { 0: { segmentKey: 'tools:1:8', expanded: true, range: { head: 8, tail: 8 } } } })
    app.mount(root); t.after(() => app.unmount()); await flush()
    const buttons = descendants(root, n => n.type === 'button')
    assert.equal(buttons.length, 2); assert.equal(buttons[0].props.expanded, true)
    assert.deepEqual(buttons.map(n => n.props.count), [1, 1])
    buttons[0].props.onClick(); assert.deepEqual(toggled, [['s', 'tools:1:8']])
})
const listSource = readFileSync(new URL('./SessionItemsList.vue', import.meta.url), 'utf8')
const scrollSource = listSource.slice(listSource.indexOf('async function scrollToLineNum('), listSource.indexOf('\n}', listSource.indexOf('async function scrollToLineNum(')) + 2)
test('source search opens only its projected segment; string keys bypass transcript preload', async () => {
    const rows = Vue.ref([{ lineNum: 'event:7', isSessionEvent: true }, { lineNum: 5, content: null }]), expanded = [], loaded = [], jumps = []
    const dependencies = { visualItems: rows, items: Vue.ref([]), scrollerRef: Vue.ref({ scrollToKey: async key => { jumps.push(key); return true } }),
        props: { sessionId: 's' }, store: { localState: { sessionEventSegments: { s: [{ head: 2, members: [2, 3] }, { head: 6, members: [6, 7] }] } },
            getEffectiveDisplayMode: () => DISPLAY_MODE.SIMPLIFIED, expandGroup: (_, head) => expanded.push(head),
            recomputeVisualItems: () => { rows.value = [...rows.value, { lineNum: 7, content: '{}' }] }, loadSessionItemsRanges: async (...args) => loaded.push(args) },
        DISPLAY_MODE, nextTick: Vue.nextTick, LOAD_BUFFER: 1, isTranscriptLoadCandidate, lineNumsToRanges: lines => lines, scrollToFirstHighlight: () => {} }
    const scroll = new Function(...Object.keys(dependencies), `let scrollToLineNumGeneration=0; ${scrollSource}; return scrollToLineNum`)(...Object.values(dependencies))
    assert.equal(await scroll('event:7', { highlight: false }), true); assert.deepEqual(loaded, [])
    assert.equal(await scroll(7, { highlight: false }), true); assert.deepEqual(expanded, [6]); assert.deepEqual(jumps, ['event:7', 7])
})
test('a concurrent toggle never animates or retains an event row', async t => {
    const { useGroupReveal } = await import('../../../composables/useGroupReveal.js')
    const { useListExit } = await import('../../../composables/useListExit.js')
    const rows = Vue.shallowRef([{ lineNum: 1 }]), scope = Vue.effectScope()
    t.after(() => scope.stop())
    const event = { lineNum: 'event:7', isSessionEvent: true }, tool = { lineNum: 2 }
    let reveal, exits
    scope.run(() => {
        reveal = useGroupReveal({ items: rows, getKey: item => item.lineNum, isMember: item => !item.isSessionEvent })
        exits = useListExit({ items: rows, getKey: item => item.lineNum, isEligible: item => !item.isSessionEvent && reveal.isCollapsing(),
            scopeKey: () => 's', getVisibleRange: () => ({ start: 0, end: 4 }), getHeight: () => 30 })
    })
    reveal.noteToggle(1, true, 30); rows.value = [...rows.value, event, tool]; await flush()
    assert.equal(reveal.itemClass(event), null); assert.equal(reveal.itemStyle(event), null)
    assert.equal(reveal.itemStyle(tool)['--group-count'], 2)
    reveal.noteToggle(1, false, 30); rows.value = [rows.value[0]]; await flush()
    assert.equal(exits.displayItems.value.some(item => item.lineNum === 'event:7'), false)
    assert.equal(exits.displayItems.value.some(item => item.lineNum === 2), true)
})
test('event-only edge navigation uses the host edges and has no block targets', async () => {
    const rows = Vue.ref([{ lineNum: 'event:1', isSessionEvent: true }, { lineNum: 'event:2', isSessionEvent: true }]), edges = []
    const nav = useChatNavigation({ visualItems: rows, scrollerRef: Vue.ref({ positions: [], isAtBottom: () => false, isAtTop: () => false }),
        scrollToItem: () => assert.fail('event is not a conversation block'), scrollToEdge: edge => edges.push(edge) })
    await nav.goNextBlock(); await nav.goTop(); await nav.goBottom(); assert.deepEqual(edges, ['top', 'bottom'])
})
