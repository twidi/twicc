// Test-only app: no main.js bootstrap, WebSocket, backend writes, or provider launch.
import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import { useDataStore } from '../../src/stores/data'
import { useSettingsStore } from '../../src/stores/settings'
import VirtualScroller from '../../src/components/virtual-scroller/VirtualScroller.vue'
import SessionItem from '../../src/components/session/detail/SessionItem.vue'
import { getParsedContent, setParsedContent } from '../../src/utils/parsedContent'
import { SYNTHETIC_ITEM, DISPLAY_MODE } from '../../src/constants'
import '@awesome.me/webawesome/dist/styles/webawesome.css'
import '@awesome.me/webawesome/dist/styles/themes/default.css'
import '../../src/styles/transcript-tokens.css'
import '../../src/styles/depth.css'
import '../../src/styles/glass.css'
import '../../src/styles/motion.css'
import '../../src/styles/glow.css'
import '../../src/styles/group-reveal.css'
import '../../src/styles/surfaces.css'
import '../../src/styles/tool-cards.css'
import '../../src/styles/quote-card.css'
import '@awesome.me/webawesome/dist/components/button/button.js'
import '@awesome.me/webawesome/dist/components/icon/icon.js'
import '@awesome.me/webawesome/dist/components/tooltip/tooltip.js'
import '@awesome.me/webawesome/dist/components/details/details.js'
import '@awesome.me/webawesome/dist/components/spinner/spinner.js'
import '@awesome.me/webawesome/dist/components/callout/callout.js'
import '@awesome.me/webawesome/dist/components/tag/tag.js'
import '@awesome.me/webawesome/dist/components/popover/popover.js'
import '../../src/utils/brandRobotIcon'
import { installDetailsMotion } from '../../src/utils/detailsMotion'
installDetailsMotion()

const style = document.createElement('style')
style.textContent = `body{margin:0;padding:20px;font:16px sans-serif}button{margin:4px;padding:8px}#transcript{height:420px;width:min(720px,95vw);border:1px solid #888}pre{white-space:pre-wrap;max-width:1000px} .history{padding:12px;height:42px} .target-row{padding:12px}`
document.head.append(style)
const sessionId = 'streaming-browser-fixture', projectId = 'streaming-browser-project'
const lineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum
const tracked = ['VirtualScrollerItem.vue', 'SessionItem.vue', 'items/claude_code/Message.vue', 'items/codex/Message.vue']
const lifetimes = new Map(), resources = [], warnings = []
// Request diagnostics stay inside this test app. No methods perform backend writes.
new PerformanceObserver(list => resources.push(...list.getEntries().map(entry => entry.name))).observe({ type: 'resource', buffered: true })
const status = ref('Ready')
const diagnostics = ref('Ready. Select a scenario.'), generation = ref(0), scroller = ref(null), active = ref('claude_code text')
let store, busy = false
const settle = async () => { await nextTick(); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); await new Promise(resolve => setTimeout(resolve, 160)) }
function tagged(instance) {
    const file = instance.$options.__file || ''
    const name = tracked.find(path => file.endsWith(path))
    if (!name) return null
    const props = instance.$props
    const target = name === 'VirtualScrollerItem.vue' ? props.itemKey === lineNum : props.sessionId === sessionId && props.lineNum === lineNum
    return target ? { name, uid: instance.$.uid, target: name === 'VirtualScrollerItem.vue' ? { itemKey: props.itemKey } : { sessionId: props.sessionId, lineNum: props.lineNum } } : null
}
function record(instance, event) {
    const tag = tagged(instance)
    if (!tag) return
    const key = `${tag.name}:${tag.uid}`
    const entry = lifetimes.get(key) || { ...tag, mounts: 0, unmounts: 0 }
    entry[event]++
    lifetimes.set(key, entry)
}
function lifetimeSnapshot() { return [...lifetimes.values()].map(entry => ({ ...entry })) }
function rowElement() { return document.querySelector(`[data-line-num="${lineNum}"]`) }
function observed() {
    const element = rowElement()
    return { text: element?.innerText || '', height: element?.getBoundingClientRect().height || 0,
        measuredHeight: scroller.value?.getItemHeight(lineNum), scroll: scroller.value?.getScrollState() }
}
// Structural fixture replacement, explicitly separate from production recomputeVisualItems.
function initialize(provider, blockType, history = 0) {
    generation.value++
    lifetimes.clear()
    store.sessions[sessionId] = { id: sessionId, provider, project_id: projectId }
    store.projects[projectId] = { id: projectId, directory: '/test-only' }
    store.localState.streamingBlocks[sessionId] = { messageId: 'fixture-message', blocks: [{ blockIndex: 0, blockType, text: '', displayedText: '', stopped: false, uuid: 'fixture-uuid' }] }
    store.localState.openDetails[sessionId] = {}
    const row = { lineNum, kind: 'assistant_message', syntheticKind: SYNTHETIC_ITEM.STREAMING_BLOCK.kind }
    setParsedContent(row, { type: 'assistant', syntheticKind: row.syntheticKind, message: { role: 'assistant', content: [{ type: blockType, [blockType === 'thinking' ? 'thinking' : 'text']: '', streaming: true }] } })
    const rows = [...Array.from({ length: history }, (_, index) => ({ lineNum: index + 1, history: true })), row]
    store.localState.sessionVisualItems[sessionId] = rows
    store.localState.visualItemCache[sessionId] = new Map(rows.map(item => [item.lineNum, item]))
}
const longText = 'Latest wrapping text stays visible. '.repeat(35)
async function run(provider, blockType, proposed = false, geometry = false) {
    if (busy) return
    busy = true
    status.value = 'Running'
    try {
        active.value = `${provider} ${proposed ? 'proposed plan' : blockType}${geometry ? ' geometry' : ''}`
        initialize(provider, blockType, geometry ? 100 : 0)
        await settle()
        if (geometry) {
            await scroller.value.scrollToEdge('bottom', { maxAttempts: 8 })
            await settle()
            // Place the real scrollbar at its physical end, like a reader dragging it there.
            // Fixture history measurements can leave estimated navigation short of that end.
            const container = document.querySelector('#transcript .virtual-scroller')
            container.scrollTop = container.scrollHeight
            await settle()
        }
        // Open real wa-details after mounting. Keep its real transition and Markdown enabled.
        if (blockType === 'thinking') { rowElement()?.querySelector('wa-details')?.show(); await settle() }
        // Discard the previous structural fixture's completed unmounts before the publication baseline.
        for (const [key, entry] of lifetimes) if (entry.unmounts) lifetimes.delete(key)
        const baseline = lifetimeSnapshot(), initial = observed(), observations = []
        const prefixes = geometry ? ['', 'Short prefix', ...Array.from({ length: 18 }, (_, i) => 'Latest wrapping text stays visible. '.repeat(Math.min(35, (i + 1) * 2)))] : proposed
            ? ['Plain introduction', 'Plain introduction\n<proposed_plan>\n', 'Plain introduction\n<proposed_plan>\n# Proposed plan\n\nGrowing plan details.', 'Plain introduction\n<proposed_plan>\n# Proposed plan\n\nGrowing plan details.\n</proposed_plan>']
            : ['', 'Short prefix', blockType === 'thinking' ? `Thinking grows. ${longText}` : longText]
        for (const prefix of prefixes) {
            store._onBufferDrain(sessionId, 0, prefix)
            await settle()
            observations.push({ prefix, ...observed() })
        }
        const after = lifetimeSnapshot()
        const expectedFiles = ['VirtualScrollerItem.vue', 'SessionItem.vue', `items/${provider}/Message.vue`]
        const lifetimeStable = expectedFiles.every(name => {
            const before = baseline.filter(entry => entry.name === name)
            const current = after.filter(entry => entry.name === name)
            return before.length === 1 && current.length === 1 && before[0].uid === current[0].uid &&
                before[0].mounts === current[0].mounts && current[0].unmounts === 0
        })
        const finalText = observations.at(-1).text
        const textVisible = finalText.includes(proposed ? 'Growing plan details.' : 'Latest wrapping text stays visible.')
        let readingAbove = null
        if (geometry) {
            scroller.value.setScrollTop(200)
            await settle()
            const before = scroller.value.getScrollState()
            store._onBufferDrain(sessionId, 0, `${longText}\nReading above stays fixed. ${longText}`)
            await settle()
            const afterScroll = scroller.value.getScrollState()
            readingAbove = { before, after: afterScroll, stable: Math.abs(before.scrollTop - afterScroll.scrollTop) <= 1 }
        }
        diagnostics.value = JSON.stringify({ scenario: active.value, lifetimeStable, textVisible, baseline, after,
            initial, observations, bottomFollowing: geometry ? observations.every(({ scroll }) => scroll.scrollHeight - scroll.clientHeight - scroll.scrollTop <= 1) : null, heightChanged: observations.at(-1).height > initial.height, readingAbove,
            requests: { count: new Set(resources).size, backend: [...new Set(resources)].filter(url => new URL(url).pathname.startsWith('/api/')), external: [...new Set(resources)].filter(url => new URL(url).origin !== location.origin) }, warnings,
            scope: 'Real SFC content publication and geometry. Structural fixture only; no natural final JSONL reconciliation.' }, null, 2)
    } catch (error) { diagnostics.value = `${error.stack}` }
    finally { busy = false; status.value = 'Complete' }
}
const pinia = createPinia()
const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { render: () => null } }] })
const app = createApp({ setup() {
    store = useDataStore()
    const settings = useSettingsStore()
    // Independent Pinia state only. Do not initialize settings persistence or backend sync.
    settings.displayMode = DISPLAY_MODE.SIMPLIFIED
    initialize('claude_code', 'text')
    return () => h('main', [h('h1', 'Streaming row validation'),
        h('p', 'Isolated fixture. Real provider components. No backend writes or provider launch.'), h('p', { id: 'status' }, status.value),
        h('div', [
            ['Claude text', () => run('claude_code', 'text')], ['Claude thinking', () => run('claude_code', 'thinking')],
            ['Codex text', () => run('codex', 'text')], ['Codex thinking', () => run('codex', 'thinking')],
            ['Codex proposed plan', () => run('codex', 'text', true)], ['Claude geometry', () => run('claude_code', 'text', false, true)],
            ['Codex geometry', () => run('codex', 'text', false, true)],
            ['Open thinking', () => rowElement()?.querySelector('wa-details')?.show()],
            ['Publish next prefix', async () => { const block = store.localState.streamingBlocks[sessionId].blocks[0]; store._onBufferDrain(sessionId, 0, `${block.displayedText}\nManual publication.`); await settle(); diagnostics.value = JSON.stringify({ after: lifetimeSnapshot(), observed: observed() }, null, 2) }],
        ].map(([label, onClick]) => h('button', { onClick }, label))),
        h('h2', active.value), h('div', { id: 'transcript' }, [h(VirtualScroller, {
            key: generation.value, ref: scroller, items: store.localState.sessionVisualItems[sessionId], itemKey: item => item.lineNum,
        }, { default: ({ item }) => item.history ? h('div', { class: 'history' }, `History row ${item.lineNum}`)
            : h('div', { class: 'target-row' }, [h(SessionItem, { content: getParsedContent(item), kind: item.kind,
                syntheticKind: item.syntheticKind, projectId, sessionId, lineNum: item.lineNum, externallyGrouped: true })]) })]),
        h('h2', 'Diagnostics'), h('pre', { id: 'diagnostics' }, diagnostics.value)])
} })
app.mixin({ mounted() { record(this, 'mounts') }, unmounted() { record(this, 'unmounts') } })
app.config.warnHandler = message => warnings.push(message)
app.use(pinia)
app.use(router)
await router.push('/')
await router.isReady()
app.mount('#app')
