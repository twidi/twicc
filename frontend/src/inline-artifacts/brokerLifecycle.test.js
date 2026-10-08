import test from 'node:test'
import assert from 'node:assert/strict'
import { createArtifactBrokerBinding } from '../composables/useArtifactBroker.js'

function fixture() {
    const connections = []
    const timers = new Map()
    const binding = createArtifactBrokerBinding({
        mount: (iframe, config) => {
            const connection = { config, destroyCount: 0, destroy() { this.destroyCount++ } }
            connections.push(connection)
            return connection
        },
        setTimer: fn => { const key = Symbol(); timers.set(key, fn); return key },
        clearTimer: key => timers.delete(key),
    })
    const iframe = { contentWindow: {} }
    const failures = [], ready = []
    const config = { documentUrl: 'https://local.test/widget/index.html', bindingKey: 'one',
        inlineGeneration: 1, onInlineReady: () => ready.push(1), onInlineError: e => failures.push(e) }
    binding.bind(iframe, config)
    return { binding, iframe, config, connections, timers, failures, ready }
}
test('passive hiding retains connection and pending consent until disposal', async () => {
    const f = fixture()
    let settled = false
    const result = f.connections[0].config.showPrompt({ type: 'network', host: 'example' })
    result.then(() => { settled = true })
    const prompt = f.binding.brokerPrompt.value
    // Visibility belongs to the owner and does not enter the binding configuration.
    f.binding.bind(f.iframe, { ...f.config })
    await Promise.resolve()
    assert.equal(settled, false)
    assert.equal(f.connections[0].destroyCount, 0)
    assert.equal(f.binding.brokerPrompt.value, prompt)
    f.binding.dispose()
    assert.equal(await result, 'deny')
    assert.equal(f.connections[0].destroyCount, 1)
    assert.equal(f.timers.size, 0)
})
test('same contentWindow rebinds once when document or binding identity changes', async () => {
    const f = fixture()
    const pending = f.connections[0].config.showPrompt({ type: 'network' })
    f.binding.bind(f.iframe, { ...f.config, bindingKey: 'two', inlineGeneration: 2 })
    assert.equal(await pending, 'deny')
    assert.equal(f.connections.length, 2)
    assert.equal(f.connections[0].destroyCount, 1)
    f.binding.bind(f.iframe, { ...f.config, bindingKey: 'two', inlineGeneration: 2 })
    assert.equal(f.connections.length, 2)
    f.binding.bind(f.iframe, { ...f.config, documentUrl: 'https://local.test/widget/new.htm' })
    assert.equal(f.connections.length, 3)
    f.binding.dispose()
})
test('only current bound shim ready clears timeout; old callbacks stay inert', () => {
    const f = fixture()
    const oldReady = f.connections[0].config.onInlineReady
    f.binding.bind(f.iframe, { ...f.config, bindingKey: 'two', inlineGeneration: 2 })
    oldReady()
    assert.deepEqual(f.ready, [])
    assert.equal(f.timers.size, 1)
    f.connections[1].config.onInlineReady()
    assert.deepEqual(f.ready, [1])
    assert.equal(f.timers.size, 0)
    f.binding.dispose()
})
test('bound shim timeout creates retryable load error without iframe load inference', () => {
    const f = fixture()
    for (const callback of [...f.timers.values()]) callback()
    assert.deepEqual(f.failures, ['document_unavailable'])
    assert.deepEqual(f.ready, [])
    f.binding.dispose()
})

async function promptFixture() {
    const { readFile } = await import('node:fs/promises')
    const { createRequire } = await import('node:module')
    const { pathToFileURL } = await import('node:url')
    const { parse, compileScript } = await import('@vue/compiler-sfc')
    const { createRenderer, nextTick, h, ref } = await import('vue')
    const source = await readFile(new URL('../components/artifacts/ArtifactBrokerPrompt.vue', import.meta.url), 'utf8')
    const { descriptor } = parse(source, { templateParseOptions: { isCustomElement: tag => tag.startsWith('wa-') } })
    const vueUrl = pathToFileURL(createRequire(import.meta.url).resolve('vue')).href
    const code = compileScript(descriptor, { id: 'prompt', inlineTemplate: true,
        templateOptions: { compilerOptions: { isCustomElement: tag => tag.startsWith('wa-') } } }).content
        .replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
    const { default: Prompt } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
    const nodes = []
    const node = type => {
        const value = { type, props: {}, children: [] }
        nodes.push(value)
        return value
    }
    const bindingFixture = fixture()
    const { binding, connections } = bindingFixture
    const visible = ref(true)
    const decisions = []
    const renderer = createRenderer({ createElement: node, createText: () => node('text'),
        createComment: () => node('comment'), setText() {}, setElementText() {}, parentNode: el => el.parent,
        nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null,
        patchProp: (el, key, old, value) => {
            el.props[key] = value
            if (key === 'open') el.open = value
        },
        insert: (el, parent, anchor) => {
            el.parent = parent
            const index = anchor ? parent.children.indexOf(anchor) : -1
            if (index < 0) parent.children.push(el)
            else parent.children.splice(index, 0, el)
        }, remove: el => { el.parent?.children.splice(el.parent.children.indexOf(el), 1) } })
    const app = renderer.createApp({ render: () => h(Prompt, { prompt: binding.brokerPrompt.value,
        visible: visible.value, onDecision: decision => {
            decisions.push(decision)
            binding.onBrokerDecision(decision)
        } }) })
    app.mount(node('root'))
    const dialog = nodes.find(n => n.type === 'wa-dialog')
    dialog.dialog = { open: false, close() { this.open = false } }
    dialog.removeOpenListeners = () => {}
    dialog.show = () => { dialog.open = true; dialog.dialog.open = true }
    class HideEvent {
        type = 'wa-hide'
        defaultPrevented = false
        preventDefault() { this.defaultPrevented = true }
    }
    class AfterHideEvent { type = 'wa-after-hide' }
    dialog.dispatchEvent = event => {
        event.target = dialog
        event.currentTarget = dialog
        const camelName = event.type.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())
        const handler = dialog.props[`on${camelName[0].toUpperCase()}${camelName.slice(1)}`]
            ?? dialog.props[`on${event.type[0].toUpperCase()}${event.type.slice(1)}`]
        handler?.(event)
    }
    const waModuleUrl = new URL('../../node_modules/@awesome.me/webawesome/dist/components/dialog/dialog.js', import.meta.url)
    const waModule = await readFile(waModuleUrl, 'utf8')
    const classChunk = waModule.match(/import\s*\{\s*WaDialog\s*\}\s*from\s*"([^"]+)"/)[1]
    const waSource = await readFile(new URL(classChunk, waModuleUrl), 'utf8')
    const closeBody = waSource.match(/async requestClose\(source\) \{([^]*?)\n  \}\n  addOpenListeners\(/)[1]
    const openBody = waSource.match(/handleOpenChange\(\) \{([^]*?)\n  \}\n  \/\*\*/)[1]
    const animations = []
    // Execute the installed WA method body, with only the animation boundary replaced.
    dialog.requestClose = new Function('WaHideEvent', 'WaAfterHideEvent', 'animateWithClass',
        'unlockBodyScrolling', `return async function(source) {${closeBody}}`)(HideEvent, AfterHideEvent,
        (target, kind) => kind === 'hide' ? new Promise(resolve => animations.push(resolve)) : Promise.resolve(),
        () => {})
    dialog.handleOpenChange = new Function(openBody)
    let open = dialog.open
    Object.defineProperty(dialog, 'open', { get: () => open, set: value => {
        if (open === value) return
        open = value
        dialog.updateComplete = Promise.resolve().then(() => dialog.handleOpenChange())
    } })
    const flush = async () => {
        await nextTick()
        await dialog.updateComplete
        await nextTick()
        await dialog.updateComplete
    }
    const request = host => connections[0].config.showPrompt({ type: 'network', host, kind: 'public' })
    const finishHide = async () => {
        for (const finish of animations.splice(0)) finish()
        await flush()
    }
    let disposed = false
    const dispose = () => {
        if (disposed) return
        disposed = true
        binding.dispose()
        app.unmount()
    }
    return { binding, visible, dialog, decisions, request, flush, finishHide, dispose }
}

test('the next queued consent reopens after actual WA native close completion', async t => {
    const f = await promptFixture()
    t.after(f.dispose)
    const first = f.request('first')
    const second = f.request('second')
    let secondSettled = false
    second.then(() => { secondSettled = true })
    await f.flush()
    assert.equal(f.dialog.dialog.open, true)
    const closing = f.dialog.requestClose(f.dialog.dialog)
    assert.equal(await first, 'deny')
    await f.flush()
    assert.equal(f.binding.brokerPrompt.value.host, 'second')
    assert.equal(f.dialog.props.open, true)
    assert.equal(f.dialog.open, true)
    await f.finishHide()
    await closing
    await f.flush()
    assert.equal(f.dialog.open, true)
    assert.equal(f.dialog.dialog.open, true)
    assert.equal(secondSettled, false)
    assert.deepEqual(f.decisions, ['deny'])
    f.dispose()
    assert.equal(await second, 'deny')
})

test('passive hide then return during WA animation reopens the same pending consent', async t => {
    const f = await promptFixture()
    t.after(f.dispose)
    const result = f.request('pending')
    let settled = false
    result.then(() => { settled = true })
    await f.flush()
    const pending = f.binding.brokerPrompt.value
    f.visible.value = false
    await f.flush()
    assert.equal(f.dialog.props.open, false)
    assert.equal(settled, false)
    f.visible.value = true
    await f.flush()
    await f.finishHide()
    await f.flush()
    assert.equal(f.binding.brokerPrompt.value, pending)
    assert.equal(settled, false)
    assert.equal(f.dialog.open, true)
    assert.equal(f.dialog.dialog.open, true)
    assert.deepEqual(f.decisions, [])
    f.dispose()
    assert.equal(await result, 'deny')
})

test('a prompt stays pending and the native modal stays closed while its owner stays hidden', async t => {
    const f = await promptFixture()
    t.after(f.dispose)
    const result = f.request('pending')
    await f.flush()
    f.visible.value = false
    await f.flush()
    await f.finishHide()
    await f.flush()
    assert.equal(f.dialog.open, false)
    assert.equal(f.dialog.dialog.open, false)
    assert.equal(f.binding.brokerPrompt.value.host, 'pending')
    assert.deepEqual(f.decisions, [])
    f.visible.value = true
    await f.flush()
    assert.equal(f.dialog.open, true)
    assert.equal(f.dialog.dialog.open, true)
    assert.equal(f.binding.brokerPrompt.value.host, 'pending')
    f.dispose()
    assert.equal(await result, 'deny')
})

test('a duplicate native close during animation cannot deny the next queued prompt', async t => {
    const f = await promptFixture()
    t.after(f.dispose)
    const first = f.request('first')
    const second = f.request('second')
    let secondSettled = false
    second.then(() => { secondSettled = true })
    await f.flush()
    const closing = f.dialog.requestClose(f.dialog.dialog)
    assert.equal(await first, 'deny')
    await f.flush()
    const duplicate = f.dialog.requestClose(f.dialog.dialog)
    await f.flush()
    assert.equal(secondSettled, false)
    assert.deepEqual(f.decisions, ['deny'])
    await f.finishHide()
    await Promise.all([closing, duplicate])
    await f.flush()
    assert.equal(f.dialog.open, true)
    assert.equal(f.dialog.dialog.open, true)
    const nextClosing = f.dialog.requestClose(f.dialog.dialog)
    assert.equal(await second, 'deny')
    await f.finishHide()
    await nextClosing
    await f.flush()
    assert.deepEqual(f.decisions, ['deny', 'deny'])
    assert.equal(f.dialog.dialog.open, false)
})

test('disposal during native close completion denies all consent and never reopens a hidden modal', async t => {
    const f = await promptFixture()
    t.after(f.dispose)
    const first = f.request('first')
    const second = f.request('second')
    await f.flush()
    f.visible.value = false
    await f.flush()
    f.binding.dispose()
    f.visible.value = true
    await f.flush()
    assert.deepEqual(await Promise.all([first, second]), ['deny', 'deny'])
    await f.finishHide()
    await f.flush()
    assert.equal(f.dialog.open, false)
    assert.equal(f.dialog.dialog.open, false)
    assert.equal(f.binding.brokerPrompt.value, null)
    assert.deepEqual(f.decisions, [])
})

test('inactive correction preserves captured generation until current document navigation', async () => {
    const { inlineArtifactBrokerConfig } = await import('../composables/useArtifactBroker.js')
    const { createInlineArtifactRuntime } = await import('./runtime.js')
    const { createPinia, setActivePinia } = await import('pinia')
    const { useFramePoolStore } = await import('../stores/framePool.js')
    setActivePinia(createPinia())
    const pool = useFramePoolStore()
    const descriptor = { sourceSessionId: 'parent', artifactId: 'widget', publicationKey: 'one',
        status: 'ready', title: 'Widget', height: 360, codeRevision: null }
    const runtime = createInlineArtifactRuntime({ viewId: 'parent', pool, adapter: {
        probe: async () => ({ available: true }), documentUrl: () => '/widget/index.html',
        brokerConfig: () => ({ inArtifactsRoot: true }), retry() {}, dispose() {} } })
    runtime.reconcile({ revision: 1, descriptors: [descriptor] })
    const key = '["parent","widget"]'
    const detach = runtime.attach(key, 'one', { isVisible: () => true })
    await new Promise(resolve => setImmediate(resolve))
    const entry = runtime.entries.get(key)
    const f = fixture()
    f.binding.bind(f.iframe, inlineArtifactBrokerConfig(entry, runtime, 'https://local.test'))
    const pending = f.connections[1].config.showPrompt({ type: 'network' })
    let settled = false
    pending.then(() => { settled = true })
    detach()
    runtime.setActive(false)
    assert.equal(entry.visible, false)
    assert.equal(f.connections[1].destroyCount, 0)
    await Promise.resolve()
    assert.equal(settled, false)
    runtime.reconcile({ revision: 2, descriptors: [{ ...descriptor, publicationKey: 'two' }] })
    assert.equal(entry.generation, 2)
    const oldConfig = inlineArtifactBrokerConfig(entry, runtime, 'https://local.test')
    assert.equal(oldConfig.inlineGeneration, 1)
    oldConfig.onInlineReady()
    assert.equal(entry.loadState, 'idle')
    runtime.setActive(true)
    runtime.attach(key, 'two', { isVisible: () => true })
    await new Promise(resolve => setImmediate(resolve))
    const next = inlineArtifactBrokerConfig(entry, runtime, 'https://local.test')
    assert.equal(next.inlineGeneration, 2)
    f.binding.bind(f.iframe, next)
    assert.equal(await pending, 'deny')
    assert.equal(f.connections[1].destroyCount, 1)
    next.onInlineReady()
    assert.equal(entry.loadState, 'ready')
    runtime.dispose()
    f.binding.dispose()
    assert.equal(f.connections[2].destroyCount, 1)
})

test('disposal denies every queued consent request from a retained connection', async () => {
    const f = fixture()
    const show = f.connections[0].config.showPrompt
    const first = show({ type: 'network', host: 'first' })
    const second = show({ type: 'network', host: 'second' })
    assert.equal(f.binding.brokerPrompt.value.host, 'first')
    f.binding.onBrokerDecision('session')
    assert.equal(await first, 'session')
    assert.equal(f.binding.brokerPrompt.value.host, 'second')
    const third = show({ type: 'network', host: 'third' })
    f.binding.dispose()
    assert.deepEqual(await Promise.all([second, third]), ['deny', 'deny'])
})
