// Bounded acceptance controls on the existing production SessionView/KeepAlive fixture.
// Reuse its canonical provider seeding and fail-closed mutation-fetch responder unchanged.
import { nextTick, unref } from 'vue'
import { useSettingsStore } from '../../src/stores/settings'
import { waitForMarkdownCondition, waitForMarkdownRestoreThenReveal } from './markdownRenderingHarness.js'

const reports = [], errors = [], visibility = []
let ready = false, busy = false, startupFailure = null, fixture = null
const controls = document.createElement('section')
controls.id = 'markdown-conversation-controls'
controls.style.cssText = 'position:fixed;bottom:0;left:0;z-index:1100;background:Canvas;padding:4px;max-width:100%;max-height:22vh;overflow:auto'
const status = document.createElement('strong')
status.textContent = 'Markdown conversation startup pending'
controls.append(status)
document.body.append(controls)
const evidence = document.createElement('pre')
evidence.id = 'markdown-conversation-report'
evidence.style.display = 'none'
controls.append(evidence)
const buttons = []
function addButton(label, action) {
    const button = document.createElement('button')
    button.textContent = label; button.disabled = true
    button.onclick = () => execute(label, action).catch(() => {})
    buttons.push(button); controls.append(button)
}
function diagnostics() {
    return { provider: fixture?.provider, viewport: { width: innerWidth, height: innerHeight },
        visibility: document.visibilityState, visibilityChanges: [...visibility], errors: [...errors],
        productionFixture: fixture?.snapshot() }
}
function record(report) { reports.push(report); evidence.textContent = JSON.stringify(reports, null, 2); return report }
function requireCheck(value, message) { if (!value) throw new Error(message) }
async function execute(name, action) {
    if (!ready || busy) throw new Error('Markdown conversation is not ready or is busy')
    busy = true; buttons.forEach(button => { button.disabled = true })
    try {
        const result = await action()
        return record({ name, status: 'passed', result, ...diagnostics() })
    } catch (error) {
        errors.push(String(error))
        record({ name, status: 'failed/inconclusive', error: String(error), restoreChecks: error.restoreChecks || null, ...diagnostics() })
        throw error
    } finally { busy = false; buttons.forEach(button => { button.disabled = !ready }) }
}
function findList() {
    for (const element of document.querySelectorAll('#conversation .virtual-scroller')) {
        for (let instance = element.__vueParentComponent; instance; instance = instance.parent) {
            if (instance.type.__file?.endsWith('SessionItemsList.vue') && instance.props.sessionId === fixture.ids.mainId) return instance
        }
    }
    return null
}
function content(text, blockType = 'text') {
    if (fixture.provider === 'claude_code') return { type: 'assistant', uuid: 'markdown-fixture',
        message: { id: 'markdown-fixture', role: 'assistant', content: [blockType === 'thinking'
            ? { type: 'thinking', thinking: text } : { type: 'text', text }] } }
    if (blockType === 'thinking') return { type: 'response_item', payload: { type: 'reasoning', summary: [{ type: 'summary_text', text }] } }
    return { type: 'event_msg', payload: { type: 'item_completed', item: { type: 'AgentMessage', content: [{ type: 'Text', text }] } } }
}
let rowLine = null
function seed(text, blockType = 'text') {
    const id = fixture.ids.mainId
    const item = { line_num: fixture.store.sessionItems[id].length + 1,
        kind: blockType === 'thinking' && fixture.provider === 'codex' ? 'reasoning' : 'assistant_message',
        display_level: 0, group_head: null, group_tail: null, content: JSON.stringify(content(text, blockType)) }
    // Use the enum from an existing ALWAYS history row. Never assume its numeric encoding.
    item.display_level = fixture.store.sessionItems[id][0].display_level
    fixture.store.addSessionItems(id, [item])
    fixture.store.sessions[id].last_line = item.line_num
    rowLine = item.line_num
    return rowLine
}
function row() { return findList()?.proxy.$el.querySelector(`[data-line-num="${rowLine}"]`) }
function replace(text, blockType = 'text') {
    fixture.store.updateSessionItemsContent(fixture.ids.mainId, [{ line_num: rowLine, content: JSON.stringify(content(text, blockType)) }])
}
async function reveal(marker) {
    await nextTick()
    const scroller = findList()?.setupState.scrollerRef
    requireCheck(scroller, 'Production SessionItemsList scroller is missing')
    const scrollToKey = await scroller.scrollToKey(rowLine, { align: 'start', maxAttempts: 20 })
    requireCheck(scrollToKey === true, 'Production scrollToKey returns false')
    await waitForMarkdownCondition(() => Boolean(row()))
    const rowPresent = Boolean(row())
    await waitForMarkdownCondition(() => row()?.textContent.includes(marker))
    return { scrollToKey, rowPresent, componentContent: row().textContent, line: rowLine }
}
async function initialReveal() {
    await fixture.navigate('main')
    fixture.clear()
    seed('## Initial reveal\n\nReal Markdown initial reveal marker.')
    return reveal('Real Markdown initial reveal marker.')
}
function restoreState() {
    const list = findList()
    const state = list?.setupState
    const observable = Boolean(state && Object.hasOwn(state, 'streamSwapSavedScrollTop'))
    return { observable, savedScrollTop: state?.streamSwapSavedScrollTop,
        suspended: Boolean(unref(state?.scrollerRef?.suspended)),
        pending: !observable || state.streamSwapSavedScrollTop !== null
            || Boolean(unref(state.scrollerRef?.suspended)) || Boolean(unref(state.isAutoScrollingToBottom)) }
}
async function switchAndReturn() {
    await fixture.navigate('main')
    seed('Before KeepAlive switch.')
    const initial = await reveal('Before KeepAlive switch.')
    const params = { projectId: fixture.store.sessions[fixture.ids.mainId].project_id, sessionId: fixture.ids.otherId }
    // Direct routes avoid the older fixture's fixed settle timer.
    await fixture.router.push({ name: 'session', params })
    await nextTick()
    replace('## Latest while detached\n\nKeepAlive latest source marker.')
    await fixture.router.push({ name: 'session', params: { ...params, sessionId: fixture.ids.mainId } })
    await nextTick()
    // Observe the existing live setup getter. No scrollToKey runs while the retirement restore remains active.
    const returned = await waitForMarkdownRestoreThenReveal(restoreState, () => reveal('KeepAlive latest source marker.'))
    return { initial, returned }
}
async function openThinking() {
    await fixture.navigate('main')
    seed('**Thinking Markdown marker.**', 'thinking')
    await nextTick()
    const scroller = findList()?.setupState.scrollerRef
    requireCheck(await scroller?.scrollToKey(rowLine, { align: 'start', maxAttempts: 20 }), 'Thinking reveal returns false')
    await waitForMarkdownCondition(() => Boolean(row()?.querySelector('wa-details')))
    row().querySelector('wa-details').show()
    await waitForMarkdownCondition(() => row()?.querySelector('.markdown-body strong')?.textContent === 'Thinking Markdown marker.')
    return { rowPresent: true, strong: row().querySelector('.markdown-body strong').textContent }
}
async function typeComposer() {
    await fixture.navigate('main')
    const composer = document.querySelector('#conversation .message-input-container .cm-content')
        || document.querySelector('#conversation .cm-content[contenteditable="true"]')
    requireCheck(composer, 'Production composer contenteditable is missing')
    const blockedBefore = fixture.snapshot().requests.filter(entry => entry.method !== 'GET').length
    composer.focus()
    const text = 'Fixture composer typing without send.'
    // An actual input edit goes through the production editor. No Send action runs.
    const selection = getSelection(), range = document.createRange()
    range.selectNodeContents(composer); selection.removeAllRanges(); selection.addRange(range)
    requireCheck(document.execCommand('insertText', false, text), 'Browser composer insertText fails')
    await waitForMarkdownCondition(() => composer.textContent.includes(text))
    const blockedAfter = fixture.snapshot().requests.filter(entry => entry.method !== 'GET').length
    requireCheck(blockedAfter === blockedBefore, 'Composer typing attempts a mutation request')
    return { text: composer.textContent, mutations: blockedAfter - blockedBefore }
}
async function themeAndTools() {
    await fixture.navigate('main')
    const source = '```markdown\n# Nested heading\n\nNested content.\n```\n\n```mermaid\ngraph TD; A-->B;\n```\n\nTool restoration marker.'
    seed(source)
    const initial = await reveal('Tool restoration marker.')
    await waitForMarkdownCondition(() => Boolean(row()?.querySelector('.mermaid-diagram svg')))
    const wrapper = row().querySelector('.code-tools')
    requireCheck(wrapper, 'Code-tools wrapper is missing')
    wrapper.querySelector('[data-block-action="wrap"]').click()
    const wrap = wrapper.querySelector('pre').style.whiteSpace
    wrapper.querySelector('[data-block-action="view"]').click()
    await waitForMarkdownCondition(() => row()?.querySelector('.code-tools.is-rendered .code-tools-rendered h1')?.textContent === 'Nested heading')
    const settings = useSettingsStore()
    settings._effectiveColorScheme = settings._effectiveColorScheme === 'dark' ? 'light' : 'dark'
    document.documentElement.dataset.colorScheme = settings._effectiveColorScheme
    replace(source.replace('```markdown', '```md') + '\n\nLatest tool restoration marker.')
    await reveal('Latest tool restoration marker.')
    await waitForMarkdownCondition(() => row()?.querySelector('.code-tools.is-rendered .code-tools-rendered h1')?.textContent === 'Nested heading')
    requireCheck(row().querySelector('.code-tools') !== wrapper, 'Tool restoration does not exercise a replacement wrapper')
    requireCheck(row().querySelector('.code-tools pre').style.whiteSpace === wrap, 'Code wrapping state is lost')
    requireCheck(row().querySelector('.mermaid-diagram svg'), 'Theme update loses Mermaid SVG')
    return { initial, wrapperReplaced: row().querySelector('.code-tools') !== wrapper, wrap, theme: settings._effectiveColorScheme, nested: row().querySelector('.code-tools-rendered').textContent }
}
let visibilityProbe = null
async function beginVisibilityProbe() {
    await fixture.navigate('main')
    seed('Document visibility initial marker.')
    await reveal('Document visibility initial marker.')
    visibilityProbe = { startedAt: performance.now(), transitions: [], hiddenSourceWritten: false }
    return { instruction: 'Switch to another browser tab, then return. Finish the visibility probe after return.' }
}
document.addEventListener('visibilitychange', () => {
    const state = { at: performance.now(), visibility: document.visibilityState }
    visibility.push(state)
    if (!visibilityProbe) return
    visibilityProbe.transitions.push(state)
    if (document.visibilityState === 'hidden') {
        replace('Document visibility latest hidden source marker.')
        visibilityProbe.hiddenSourceWritten = true
    }
})
async function finishVisibilityProbe() {
    requireCheck(visibilityProbe?.hiddenSourceWritten, 'No actual hidden document transition occurs')
    requireCheck(document.visibilityState === 'visible', 'Document has not returned to visible')
    const result = await reveal('Document visibility latest hidden source marker.')
    const probe = visibilityProbe; visibilityProbe = null
    return { ...probe, result }
}
addButton('Markdown initial reveal', initialReveal)
addButton('Markdown KeepAlive switch', switchAndReturn)
addButton('Markdown open thinking', openThinking)
addButton('Type composer without send', typeComposer)
addButton('Markdown theme and tools', themeAndTools)
addButton('Begin document visibility', beginVisibilityProbe)
addButton('Finish document visibility', finishVisibilityProbe)
try {
    const query = new URLSearchParams(location.search)
    if (query.has('baseline') || query.has('publicationRateBaseline')) throw new Error('Production conversation entry rejects baseline adapters')
    await import('./invisibleStreaming.js')
    fixture = window.invisibleStreamingFixture
    await waitForMarkdownCondition(() => document.querySelector('#fixture-status')?.textContent === 'Fixture ready')
    requireCheck(!fixture.baseline && !fixture.publicationRateBaseline, 'Conversation must use production current components')
    ready = true
    status.textContent = 'Markdown conversation ready'
    buttons.forEach(button => { button.disabled = false })
} catch (error) {
    startupFailure = String(error); errors.push(startupFailure)
    status.textContent = `Markdown conversation startup failed: ${startupFailure}`
}
window.markdownConversationFixture = { ready: () => ready, startupFailure: () => startupFailure,
    initialReveal: () => execute('initial reveal', initialReveal), switchAndReturn: () => execute('KeepAlive switch', switchAndReturn),
    openThinking: () => execute('open thinking', openThinking), typeComposer: () => execute('composer typing', typeComposer),
    themeAndTools: () => execute('theme and tools', themeAndTools),
    beginVisibilityProbe: () => execute('begin visibility', beginVisibilityProbe),
    finishVisibilityProbe: () => execute('finish visibility', finishVisibilityProbe),
    exportEvidence: () => JSON.stringify({ reports, ...diagnostics() }, null, 2),
}
