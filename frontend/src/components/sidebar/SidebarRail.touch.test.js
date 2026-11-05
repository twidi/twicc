import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createRailSessionLongPress } from '../../utils/railSessionLongPress.js'
import { createRailScopeNavigation } from '../../utils/railScopeNavigation.js'
import { resolveRailItems } from '../../utils/sidebarRail.js'
import { ALL_PROJECTS_ID } from '../../constants.js'
import { extractWorkspaceId, isWorkspaceProjectId } from '../../utils/workspaceIds.js'

const source = readFileSync(new URL('./SidebarRail.vue', import.meta.url), 'utf8')
const tooltipEntry = readFileSync(new URL('../../../node_modules/@awesome.me/webawesome/dist-cdn/components/tooltip/tooltip.js', import.meta.url), 'utf8')
const chunk = tooltipEntry.match(/from "(\.\.\/\.\.\/chunks\/[^"\n]+)"/)[1]
const tooltipSource = readFileSync(new URL(chunk, new URL('../../../node_modules/@awesome.me/webawesome/dist-cdn/components/tooltip/tooltip.js', import.meta.url)), 'utf8')
const focusBody = tooltipSource.match(/this.handleFocus = \(\) => \{([\s\S]*?)\n    \};/)[1]

async function renderTimeline(items, speech) {
    const { descriptor } = parse(source)
    const compiled = compileTemplate({ source: descriptor.template.content, filename: 'SidebarRail.vue', id: 'rail', compilerOptions: { mode: 'function' } })
    assert.deepEqual(compiled.errors, [])
    const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
    const app = Vue.createSSRApp({ render, data: () => ({
        top: items.filter(item => item.id === 'timeline'), bottom: [], speech,
        rows: [], railRows: [], recentProjects: [], recentWorkspaces: [], hasCentralContent: false,
    }) })
    app.component('AppTooltip', { setup: (_, { slots }) => () => Vue.h('span', { 'data-tooltip': true }, slots.default?.()) })
    app.config.warnHandler = () => {}
    return renderToString(app)
}

function fixture(t, isTouchDevice = true, tts = { status: 'idle', stop() {} }, { speech = { activePageId: null, activeLabel: '' }, realNavigation = false } = {}) {
    t.mock.timers.enable({ apis: ['setTimeout'] })
    const document = new EventTarget()
    const listeners = new Map()
    const add = document.addEventListener.bind(document)
    const remove = document.removeEventListener.bind(document)
    document.addEventListener = (type, listener, options) => {
        if (!listeners.has(type)) listeners.set(type, new Set())
        listeners.get(type).add(listener)
        add(type, listener, options)
    }
    document.removeEventListener = (type, listener, options) => {
        listeners.get(type)?.delete(listener)
        remove(type, listener, options)
    }
    const pushed = []
    const router = {
        currentRoute: { value: { fullPath: '/sessions' } },
        resolve: target => ({ fullPath: JSON.stringify(target) }),
        push: target => { pushed.push(target); router.currentRoute.value.fullPath = JSON.stringify(target) },
    }
    const watchers = []
    let globalDismiss
    let unmount
    const { descriptor } = parse(source)
    const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
    const api = runInNewContext(`${script}\n;({setSessionTooltip, sessionPointerDown, sessionPointerEnter, sessionClick, sessionTooltipTrigger, setEntryTooltip, entryPointerDown, scopeClick, scopeSelected, handleEntryTooltipHide, activate, items})`, {
        document, Map, createRailSessionLongPress,
        ALL_PROJECTS_ID, extractWorkspaceId, isWorkspaceProjectId, resolveRailItems,
        ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
        defineProps: () => ({ mode: 'sessions', sidebarOpen: false }), defineEmits: () => () => {},
        useSettingsStore: () => ({ isTouchDevice }), useSessionTreeExpansionStore: () => ({ state: { explicit: {} } }),
        useSessionTreeAncestors: () => {}, useDataStore: () => ({}),
        useRoute: () => ({ params: {}, fullPath: '/sessions' }), useRouter: () => realNavigation ? router : ({ push: route => pushed.push(route) }),
        useRailRecentProjects: () => ({ recentProjects: { value: [] }, recentWorkspaces: { value: [] } }),
        useWorkspacesStore: () => ({}), useTextToSpeechStore: () => tts, useTimelineSpeechStore: () => speech,
        nextTick: () => Promise.resolve(),
        createRailScopeNavigation: router => realNavigation ? createRailScopeNavigation(router, () => {}) : (kind, id) => pushed.push(id === undefined ? kind : `${kind}:${id}`),
        useRailActiveSessions: () => ({ rows: { value: [] }, treeCandidates: { value: [] } }), sessionRouteLocation: session => session.id,
        onTooltipDismissal: callback => { globalDismiss = callback; return () => { globalDismiss = null } },
        onBeforeUnmount: callback => { unmount = callback }, watch: (_, callback) => watchers.push(callback),
    })
    const tooltip = { trigger: api.sessionTooltipTrigger.value, shown: 0, hidden: 0, open: false,
        show() { this.open = true; this.shown++ }, hide() { this.open = false; this.hidden++ },
        isOpen() { return this.open }, setTrigger(value) { this.trigger = value },
        hasTrigger(value) { return this.trigger.split(' ').includes(value) } }
    tooltip.focus = runInNewContext(`(function() { ${focusBody} })`).bind(tooltip)
    api.setSessionTooltip('one', tooltip)
    const session = { id: 'one' }
    const down = pointerType => api.sessionPointerDown(session, { pointerType, pointerId: 1, isPrimary: true,
        button: 0, currentTarget: { isConnected: true }, clientX: 0, clientY: 0 })
    const click = () => api.sessionClick(session, { detail: 1, preventDefault() {}, stopPropagation() {} })
    t.after(() => unmount())
    return { api, document, tooltip, pushed, down, click, watchers, listeners, unmount,
        key: () => { const event = new Event('keydown'); Object.assign(event, { key: 'Tab' }); document.dispatchEvent(event) },
        dismiss: () => globalDismiss?.() }
}

test('touch native focus stays disabled after short tap, cancellation, and delayed focus', t => {
    const f = fixture(t)
    f.down('touch'); f.tooltip.focus()
    f.dismiss(); t.mock.timers.tick(450); f.tooltip.focus(); f.click()
    assert.equal(f.tooltip.shown, 0)
    assert.deepEqual(f.pushed, ['one'])
})

test('hold suppresses navigation; route change cancels preview without losing synthetic-click suppression', t => {
    const f = fixture(t)
    f.down('touch'); t.mock.timers.tick(450)
    assert.equal(f.tooltip.shown, 1)
    f.watchers[0](); f.click(); assert.deepEqual(f.pushed, [])
    f.down('touch'); f.click(); assert.deepEqual(f.pushed, ['one'])
})

test('hybrid touch changes the native trigger before focus; desktop mouse restores focus behavior', t => {
    const f = fixture(t, false)
    f.tooltip.focus(); assert.equal(f.tooltip.shown, 1)
    f.down('touch'); f.tooltip.focus(); assert.equal(f.tooltip.shown, 1)
    f.dismiss(); t.mock.timers.tick(450)
    f.api.sessionPointerEnter({ pointerType: 'mouse' }); f.tooltip.focus()
    assert.equal(f.tooltip.shown, 2)
})


for (const isTouchDevice of [false, true]) {
    test(`Tab after touch ${isTouchDevice ? 'keeps mobile manual trigger' : 'restores desktop native focus'}`, t => {
        const f = fixture(t, isTouchDevice)
        f.down('touch'); f.click(); assert.deepEqual(f.pushed, ['one'])
        f.tooltip.focus(); assert.equal(f.tooltip.shown, 0, 'touch-generated focus stays manual')
        f.key(); f.tooltip.focus()
        assert.equal(f.tooltip.shown, isTouchDevice ? 0 : 1)
        assert.deepEqual(f.pushed, ['one'], 'keyboard focus does not navigate')
    })
}

test('keyboard input cancels pending hybrid hold and unmount removes document listeners', t => {
    const f = fixture(t, false)
    f.down('touch'); t.mock.timers.tick(200); f.key(); t.mock.timers.tick(450)
    assert.equal(f.tooltip.shown, 0)
    assert.equal(f.listeners.get('keydown')?.size, 1)
    f.down('touch'); f.unmount()
    for (const listeners of f.listeners.values()) assert.equal(listeners.size, 0)
    f.key(); f.tooltip.focus(); t.mock.timers.tick(450)
    assert.equal(f.tooltip.shown, 0, 'removed keyboard listener cannot restore native focus')
})

for (const kind of ['project', 'workspace']) {
    test(`${kind} touch tap navigates; hold suppresses only that entry kind`, t => {
        const f = fixture(t)
        f.api.setEntryTooltip(`${kind}:one`, f.tooltip)
        const press = () => f.api.entryPointerDown(`${kind}:one`, { pointerType: 'touch', pointerId: 1,
            isPrimary: true, button: 0, currentTarget: { isConnected: true }, clientX: 0, clientY: 0 })
        const click = () => f.api.scopeClick(kind, 'one', { detail: 1, preventDefault() {}, stopPropagation() {} })
        press(); click(); assert.deepEqual(f.pushed, [`${kind}:one`])
        press(); t.mock.timers.tick(450); click()
        assert.equal(f.tooltip.shown, 1)
        assert.deepEqual(f.pushed, [`${kind}:one`])
        f.api.sessionClick({ id: 'one' }, { detail: 1 })
        assert.deepEqual(f.pushed, [`${kind}:one`, 'one'], 'same ID in another group remains navigable')
        press(); click()
        assert.deepEqual(f.pushed, [`${kind}:one`, 'one', `${kind}:one`])
    })
}

test('scope selected state does not mark recent entries on unrelated session routes', t => {
    const f = fixture(t)
    assert.equal(f.api.scopeSelected('project', 'one'), false)
    assert.equal(f.api.scopeSelected('workspace', 'one'), false)
})

for (const kind of ['session', 'project', 'workspace']) {
    test(`${kind} touch holds toggle the preview without navigation and close after three seconds`, t => {
        const f = fixture(t)
        if (kind !== 'session') f.api.setEntryTooltip(`${kind}:one`, f.tooltip)
        const press = () => f.api.entryPointerDown(`${kind}:one`, { pointerType: 'touch', pointerId: 1,
            isPrimary: true, button: 0, currentTarget: { isConnected: true }, clientX: 0, clientY: 0 })
        const click = () => {
            const event = { detail: 1, preventDefault() {}, stopPropagation() {} }
            if (kind === 'session') f.api.sessionClick({ id: 'one' }, event)
            else f.api.scopeClick(kind, 'one', event)
        }
        press(); t.mock.timers.tick(450); click()
        assert.equal(f.tooltip.open, true)
        press(); t.mock.timers.tick(450); click()
        assert.equal(f.tooltip.open, false, 'another hold closes the visible preview')
        assert.deepEqual(f.pushed, [])
        press(); t.mock.timers.tick(450); click()
        t.mock.timers.tick(2999); assert.equal(f.tooltip.open, true)
        t.mock.timers.tick(1); assert.equal(f.tooltip.open, false, 'touch preview expires')
        assert.deepEqual(f.pushed, [])
    })
}

test('switching from a touch preview to desktop mouse input removes automatic dismissal', t => {
    const f = fixture(t, false)
    f.down('touch'); t.mock.timers.tick(450)
    f.api.sessionPointerEnter({ pointerType: 'mouse' })
    f.tooltip.focus()
    t.mock.timers.tick(3000)
    assert.equal(f.tooltip.open, true)
    assert.equal(f.tooltip.hidden, 0)
})

test('native dismissal clears the timeout without a second hide', t => {
    const f = fixture(t)
    f.down('touch'); t.mock.timers.tick(450)
    f.tooltip.hide()
    const element = {}
    f.api.handleEntryTooltipHide('session:one', { target: element, currentTarget: element })
    t.mock.timers.tick(3000)
    assert.equal(f.tooltip.hidden, 1)
})

test('a nested tooltip hide does not cancel the rail preview timeout', t => {
    const f = fixture(t)
    f.down('touch'); t.mock.timers.tick(450)
    f.api.handleEntryTooltipHide('session:one', { target: {}, currentTarget: {} })
    t.mock.timers.tick(3000)
    assert.equal(f.tooltip.hidden, 1)
    assert.equal(f.tooltip.open, false)
})

test('stopping the speech indicator hands its keyboard focus to New session', async t => {
    for (const [focused, focusVisible, handOff] of [[true, true, true], [true, false, false], [false, false, false]]) {
        const name = !focused ? 'not focused' : focusVisible ? 'keyboard focus' : 'mouse focus'
        await t.test(name, async t => {
            const calls = []
            const tts = { status: 'playing', skip(...args) { assert.deepEqual(args, []); calls.push('stop') } }
            const f = fixture(t, false, tts)
            const speech = { id: 'sidebar-rail-speech', matches: selector => selector === ':focus-visible' && focusVisible }
            const newSession = { id: 'sidebar-rail-new-session', focus() { calls.push('focus') } }
            f.document.activeElement = focused ? speech : null
            f.document.getElementById = id => {
                // The speech button is gone once stopped: focus waits for the re-render.
                assert.deepEqual(calls, ['stop', 'tick'])
                return id === newSession.id ? newSession : null
            }
            const pending = f.api.activate({ id: 'speech' })
            calls.push('tick')
            await pending
            assert.deepEqual(calls, handOff ? ['stop', 'tick', 'focus'] : ['stop', 'tick'])
        })
    }
})


test('timeline action opens the All Projects root through scope navigation', t => {
    const f = fixture(t)
    f.api.activate({ id: 'timeline' })
    assert.deepEqual(f.pushed, ['all-projects'])
})

test('the listening destination survives idle or manual speech and disappears only when listening stops', async t => {
    const speech = { activePageId: 'ws:one', activeLabel: 'Workspace one' }
    const tts = { status: 'idle', stop() {} }
    const f = fixture(t, false, tts, { speech })
    for (const status of ['idle', 'loading', 'playing']) {
        tts.status = status
        const item = f.api.items.value.find(item => item.id === 'timeline')
        assert.equal(item.label, 'Listening timeline: Workspace one')
        const html = await renderTimeline(f.api.items.value, speech)
        assert.match(html, /aria-label="Listening timeline: Workspace one"/)
        assert.match(html, /class="rail-timeline-audio" aria-hidden="true"/)
        assert.match(html, /data-tooltip="true"[^>]*>Listening timeline: Workspace one<\/span>/)
        assert.equal((html.match(/<button\b/g) ?? []).length, 1, 'one navigation control')
        assert.doesNotMatch(html, /aria-pressed/)
    }
    speech.activePageId = null
    speech.activeLabel = ''
    assert.equal(f.api.items.value.find(item => item.id === 'timeline').label, 'All Projects timeline')
    const html = await renderTimeline(f.api.items.value, speech)
    assert.match(html, /aria-label="All Projects timeline"/)
    assert.doesNotMatch(html, /rail-timeline-audio/)
})

for (const [pageId, label, expected] of [
    [null, '', { name: 'projects-all', query: { workspace: '' } }],
    [ALL_PROJECTS_ID, 'All projects', { name: 'projects-all', query: { workspace: '' } }],
    ['ws:workspace-one', 'Workspace one', { name: 'projects-all', query: { workspace: 'workspace-one' } }],
    ['project-one', 'Project one', { name: 'project', params: { projectId: 'project-one' }, query: { workspace: '' } }],
    ['worktree-one', 'Worktree one', { name: 'project', params: { projectId: 'worktree-one' }, query: { workspace: '' } }],
]) {
    test(`the timeline rail destination opens ${pageId ?? 'the default global scope'} without toggling listening`, async t => {
        const speech = { activePageId: pageId, activeLabel: label,
            enable() { assert.fail('navigation cannot enable listening') },
            disable() { assert.fail('navigation cannot disable listening') },
        }
        const f = fixture(t, false, { status: 'idle', stop() { assert.fail('navigation cannot stop speech') } }, { speech, realNavigation: true })
        await f.api.activate({ id: 'timeline' })
        assert.deepEqual(JSON.parse(JSON.stringify(f.pushed)), [expected])
        assert.equal(speech.activePageId, pageId)
    })
}
