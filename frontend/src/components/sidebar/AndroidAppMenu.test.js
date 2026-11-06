import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'

const state = { version: '0.4.0', engine: 'GeckoView 159', activeInstanceId: 'a', instances: [
    { id: 'a', name: 'Phone', address: 'https://phone.example' },
    { id: 'b', name: 'Computer', address: 'https://computer.example' },
] }
async function load(nativeState = state, forceFixed = false, backendVersion = '1.96.2') {
    const source = readFileSync(new URL('./AndroidAppMenu.vue', import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    const hooks = {}, calls = []
    const document = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} }
    const client = { async request(command, args) {
        calls.push([command, args])
        if (command === 'getState') {
            if (!nativeState) throw new Error('Unavailable')
            return nativeState
        }
    } }
    const script = compileScript(descriptor, { id: 'menu' }).content
        .replace(/^import .*$/gm, '').replace('export default', 'const component =')
    const component = runInNewContext(`${script}; component`, {
        ...Vue, markAndroidApp() {}, observeAndroidPush() {}, disposeAndroidPush() {}, BrandLogo: {}, getAndroidAppClient: () => client,
        watchAndroidMenuPlacement: (_, onTarget) => { hooks.target = onTarget; return { refresh() {}, stop() {} } },
        onMounted: fn => { hooks.mount = fn }, onBeforeUnmount: fn => { hooks.unmount = fn },
        window: { addEventListener() {}, removeEventListener() {}, getComputedStyle: button => ({ visibility: button.visibility }) },
        document,
        setInterval: fn => { hooks.heartbeat = fn; return 1 }, clearInterval() {}, IntersectionObserver: undefined,
    })
    const props = Vue.reactive({ forceFixed, backendVersion, sessionId: null, sessionReady: false })
    const ctx = component.setup(props, { expose() {} })
    await hooks.mount()
    const compiled = compileTemplate({ source: descriptor.template.content, filename: 'AndroidAppMenu.vue', id: 'menu',
        compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') } })
    assert.deepEqual(compiled.errors, [])
    const render = runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
    async function html() {
        const app = Vue.createSSRApp({ render, data: () => ({ ...ctx, ...props }) })
        app.component('BrandLogo', { render: () => Vue.h('svg', { 'data-logo': 'twicc' }) })
        app.config.warnHandler = () => {}
        const context = {}
        const output = await renderToString(app, context)
        return output + Object.values(context.teleports ?? {}).join('')
    }
    return { ctx, calls, hooks, html, document, props }
}
test('ordinary browser renders no Android control and starts no heartbeat', async () => {
    const { html, calls, hooks } = await load(null)
    assert.doesNotMatch(await html(), /Android app menu|wa-dropdown|wa-dialog/)
    assert.deepEqual(calls.map(([command]) => command), ['getState'])
    hooks.unmount()
})
test('session reports wait for loaded content and clear immediately on navigation or a loading error', async () => {
    const { props, calls, hooks } = await load()
    props.sessionId = 'a'
    await Vue.nextTick()
    hooks.heartbeat()
    assert.equal(calls.at(-1)[1].sessionId, null)
    props.sessionReady = true
    await Vue.nextTick()
    assert.equal(calls.at(-1)[1].sessionId, 'a')
    props.sessionId = 'b'; props.sessionReady = false
    await Vue.nextTick()
    assert.equal(calls.at(-1)[1].sessionId, null)
    props.sessionReady = true
    await Vue.nextTick()
    assert.equal(calls.at(-1)[1].sessionId, 'b')
    props.sessionReady = false
    await Vue.nextTick()
    assert.equal(calls.at(-1)[1].sessionId, null)
    hooks.unmount()
})
test('native menu renders saved instances, active marker, app details, and actions', async () => {
    const { html, hooks } = await load()
    const output = await html()
    for (const text of ['Android app menu', 'TwiCC 1.96.2', 'Android 0.4.0', 'Phone', 'Computer',
        'https://computer.example', 'Active', 'Manage instances', 'Reload page', 'Force refresh', 'Server home', 'Open in browser']) {
        assert.ok(output.includes(text), text)
    }
    assert.match(output, /value="instance:a"[^>]*checked[^>]*disabled/)
    assert.match(output, /value="instance:b"/)
    assert.match(output, /data-logo="twicc"/)
    assert.doesNotMatch(output, /GeckoView 159/)
    hooks.unmount()
})

test('the menu has no manual push setup action', async () => {
    const supported = await load()
    assert.doesNotMatch(await supported.html(), /Set up push test|pushProbe/)
    supported.hooks.unmount()
})

test('Server home follows the menu placement while both versions stay visible', async () => {
    const { hooks, html, props } = await load()
    hooks.target('#rail-anchor')
    let output = await html()
    assert.match(output, /TwiCC 1\.96\.2/)
    assert.match(output, /Android 0\.4\.0/)
    assert.doesNotMatch(output, /Server home/)
    props.forceFixed = true
    output = await html()
    assert.match(output, /TwiCC 1\.96\.2/)
    assert.match(output, /Android 0\.4\.0/)
    assert.match(output, /Server home/)
    props.forceFixed = false
    hooks.target(null)
    assert.match(await html(), /Server home/)
    props.backendVersion = '1.97.0'
    assert.match(await html(), /TwiCC 1\.97\.0/)
    hooks.unmount()
})
test('menu dispatches a saved UUID and confirms force refresh before sending the command', async () => {
    const { ctx, calls, hooks } = await load()
    ctx.confirmDialog.value = { open: false }
    await ctx.selectAction({ detail: { item: { value: 'instance:b' } } })
    assert.ok(calls.some(([command, args]) => command === 'switchInstance' && args.instanceId === 'b'))
    await ctx.selectAction({ detail: { item: { value: 'forceRefresh' } } })
    assert.equal(ctx.confirmDialog.value.open, true)
    assert.ok(!calls.some(([command]) => command === 'forceRefresh'))
    await ctx.confirmForceRefresh()
    assert.ok(calls.some(([command]) => command === 'forceRefresh'))
    hooks.unmount()
})

test('lease reports only a visible trigger and releases its owner on unmount', async () => {
    const { ctx, calls, hooks, document } = await load()
    ctx.trigger.value = { isConnected: true, visibility: 'visible', getBoundingClientRect: () => ({ width: 36, height: 36 }) }
    hooks.heartbeat()
    assert.equal(calls.at(-1)[1].visible, true)
    const owner = calls.at(-1)[1].owner
    ctx.trigger.value.visibility = 'hidden'
    hooks.heartbeat()
    assert.equal(calls.at(-1)[1].visible, false)
    ctx.trigger.value.visibility = 'visible'
    document.visibilityState = 'hidden'
    hooks.heartbeat()
    assert.equal(calls.at(-1)[1].visible, false)
    document.visibilityState = 'visible'
    hooks.heartbeat()
    assert.equal(calls.at(-1)[1].visible, true)
    hooks.unmount()
    assert.equal(calls.at(-1)[1].visible, false)
    assert.equal(calls.at(-1)[1].owner, owner)
})

test('one menu uses fixed placement without a rail and connecting mode overrides a registered rail', async () => {
    const { ctx, hooks, html, props } = await load()
    assert.match(await html(), /android-app-menu-host--fixed/)
    assert.equal(ctx.railTarget.value, null)
    const anchor = { id: 'rail-anchor' }
    hooks.target(anchor)
    assert.equal(ctx.railTarget.value.id, 'rail-anchor')
    props.forceFixed = true
    assert.equal(ctx.railTarget.value, null)
    props.forceFixed = false
    assert.equal(ctx.railTarget.value.id, 'rail-anchor')
    hooks.target(null)
    assert.equal(ctx.railTarget.value, null)
    hooks.unmount()
})
