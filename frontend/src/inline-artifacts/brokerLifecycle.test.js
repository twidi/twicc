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

test('controlled dialog hiding does not dismiss pending consent during close transitions', async () => {
    const { readFile } = await import('node:fs/promises')
    const { createRequire } = await import('node:module')
    const { pathToFileURL } = await import('node:url')
    const { parse, compileScript } = await import('@vue/compiler-sfc')
    const { createRenderer, nextTick, h, ref } = await import('vue')
    const source = await readFile(new URL('../components/artifacts/ArtifactBrokerPrompt.vue', import.meta.url), 'utf8')
    const { descriptor } = parse(source, { templateParseOptions: { isCustomElement: tag => tag.startsWith('wa-') } })
    const vueUrl = pathToFileURL(createRequire(import.meta.url).resolve('vue')).href
    const code = compileScript(descriptor, { id: 'prompt', inlineTemplate: true, templateOptions: { compilerOptions: { isCustomElement: tag => tag.startsWith('wa-') } } }).content
        .replaceAll('from "vue"', `from '${vueUrl}'`).replaceAll("from 'vue'", `from '${vueUrl}'`)
    const { default: Prompt } = await import(`data:text/javascript;base64,${Buffer.from(code).toString('base64')}`)
    const nodes = []
    const node = type => { const value = { type, props: {}, children: [] }; nodes.push(value); return value }
    const renderer = createRenderer({ createElement: node, createText: () => node('text'),
        createComment: () => node('comment'), setText() {}, setElementText() {}, parentNode: el => el.parent,
        nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] ?? null, patchProp: (el, key, old, value) => { el.props[key] = value },
        insert: (el, parent, anchor) => {
            el.parent = parent
            const index = anchor ? parent.children.indexOf(anchor) : -1
            if (index < 0) parent.children.push(el)
            else parent.children.splice(index, 0, el)
        }, remove: el => { el.parent?.children.splice(el.parent.children.indexOf(el), 1) } })
    const visible = ref(true), prompt = ref({ type: 'network', host: 'https://example.test', kind: 'public' })
    const decisions = []
    const app = renderer.createApp({ render: () => h(Prompt, { prompt: prompt.value,
        visible: visible.value, onDecision: d => decisions.push(d) }) })
    app.mount(node('root'))
    const dialog = nodes.find(n => n.type === 'wa-dialog')
    visible.value = false
    await nextTick()
    assert.equal(dialog.props.open, false)
    visible.value = true
    await nextTick()
    // The close transition emits after the owner becomes visible again.
    ;(dialog.props['onWa-hide'] || dialog.props.onWaHide)({ target: dialog, currentTarget: dialog })
    assert.deepEqual(decisions, [])
    prompt.value = { type: 'network', host: 'https://queued.test', kind: 'public' }
    await nextTick()
    ;(dialog.props['onWa-hide'] || dialog.props.onWaHide)({ target: dialog, currentTarget: dialog })
    assert.deepEqual(decisions, ['deny'])
    // A user dismissal already closes WA. Clearing its prompt must not queue another close.
    prompt.value = null
    await nextTick()
    prompt.value = { type: 'network', host: 'https://next.test', kind: 'public' }
    await nextTick()
    ;(dialog.props['onWa-hide'] || dialog.props.onWaHide)({ target: dialog, currentTarget: dialog })
    assert.deepEqual(decisions, ['deny', 'deny'])
    app.unmount()
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
