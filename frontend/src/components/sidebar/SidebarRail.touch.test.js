import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse } from '@vue/compiler-sfc'
import { createRailSessionLongPress } from '../../utils/railSessionLongPress.js'

const source = readFileSync(new URL('./SidebarRail.vue', import.meta.url), 'utf8')
const tooltipEntry = readFileSync(new URL('../../../node_modules/@awesome.me/webawesome/dist-cdn/components/tooltip/tooltip.js', import.meta.url), 'utf8')
const chunk = tooltipEntry.match(/from "(\.\.\/\.\.\/chunks\/[^"\n]+)"/)[1]
const tooltipSource = readFileSync(new URL(chunk, new URL('../../../node_modules/@awesome.me/webawesome/dist-cdn/components/tooltip/tooltip.js', import.meta.url)), 'utf8')
const focusBody = tooltipSource.match(/this.handleFocus = \(\) => \{([\s\S]*?)\n    \};/)[1]

function fixture(t, isTouchDevice = true) {
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
    const watchers = []
    let globalDismiss
    let unmount
    const { descriptor } = parse(source)
    const script = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
    const api = runInNewContext(`${script}\n;({setSessionTooltip, sessionPointerDown, sessionPointerEnter, sessionClick, sessionTooltipTrigger})`, {
        document, Map, createRailSessionLongPress,
        ref: value => ({ value }), computed: fn => ({ get value() { return fn() } }),
        defineProps: () => ({ mode: 'sessions', sidebarOpen: false }), defineEmits: () => () => {},
        useSettingsStore: () => ({ isTouchDevice }), useDataStore: () => ({}),
        useRoute: () => ({ params: {}, fullPath: '/sessions' }), useRouter: () => ({ push: route => pushed.push(route) }),
        useRailActiveSessions: () => ({ rows: [] }), sessionRouteLocation: session => session.id,
        onTooltipDismissal: callback => { globalDismiss = callback; return () => { globalDismiss = null } },
        onBeforeUnmount: callback => { unmount = callback }, watch: (_, callback) => watchers.push(callback),
    })
    const tooltip = { trigger: api.sessionTooltipTrigger.value, shown: 0, hidden: 0,
        show() { this.shown++ }, hide() { this.hidden++ }, setTrigger(value) { this.trigger = value },
        hasTrigger(value) { return this.trigger.split(' ').includes(value) } }
    tooltip.focus = runInNewContext(`(function() { ${focusBody} })`).bind(tooltip)
    api.setSessionTooltip('one', tooltip)
    const session = { id: 'one' }
    const down = pointerType => api.sessionPointerDown(session, { pointerType, pointerId: 1, isPrimary: true,
        button: 0, currentTarget: { isConnected: true }, clientX: 0, clientY: 0 })
    const click = () => api.sessionClick(session, { detail: 1, preventDefault() {}, stopPropagation() {} })
    t.after(() => unmount())
    return { api, tooltip, pushed, down, click, watchers, listeners, unmount,
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
