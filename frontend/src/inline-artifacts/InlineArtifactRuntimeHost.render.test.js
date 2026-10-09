import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'
import { installInlineRuntimeHost } from './runtimeHost.js'
import { frameClipPath } from '../utils/panelInsets.js'

function compileComponent(path, bindings) {
    const { descriptor } = parse(readFileSync(new URL(path, import.meta.url), 'utf8'))
    const compiled = compileScript(descriptor, { id: 'error-host-test', inlineTemplate: true,
        templateOptions: { compilerOptions: { isCustomElement: name => name.startsWith('wa-') } } }).content
    const script = compiled.replace(/import \{([^}]+)\} from ['"]vue['"]/g,
        (_, names) => `const {${names.replace(/ as /g, ': ')}} = Vue`)
        .replace(/^import .*$/gm, '').replace('export default', 'return')
    return new Function('Vue', ...Object.keys(bindings), script)(Vue, ...Object.values(bindings))
}

function fixture(t, fullscreen = true, ready = false, hidden = false, openedInTab = null) {
    const listeners = new Map(), observers = []
    // Plain values deliberately do not trigger Vue rendering on viewport resize.
    const window = { innerWidth: 1000, innerHeight: 800,
        addEventListener(name, handler) { listeners.set(name, handler) },
        removeEventListener(name) { listeners.delete(name) } }
    const Tools = compileComponent('../components/frames/FloatingPreviewTools.vue', {
        useResizeObserver: (target, callback) => observers.push(callback),
        AppTooltip: { render() { return Vue.h('tooltip', this.$slots.default?.()) } },
    })
    const Owner = compileComponent('./InlineArtifactFrameOwner.vue', { FloatingPreviewTools: Tools,
        useArtifactBroker: () => ({ brokerPrompt: Vue.ref(null), onBrokerDecision() {} }),
        inlineArtifactBrokerConfig: () => ({}), location: { href: 'https://viewer.test/' },
        ArtifactBrokerPrompt: { render() { return null } },
        useDataStore: () => ({ getSession: id => openedInTab && id === 's' ? { artifacts_dir: '/data/artifacts/s' } : null }) })
    const Host = compileComponent('./InlineArtifactRuntimeHost.vue', { window, FloatingPreviewTools: Tools,
        InlineArtifactFrameOwner: Owner, installInlineRuntimeHost, frameClipPath })
    function rect(node) {
        if (node.props?.class === 'frame-overlay') {
            const r = pool.frames['frame-a'].rect
            return { left: r.x, top: r.y, width: r.width, height: r.height }
        }
        if (node.props?.class === 'inline-artifact-error') {
            const style = node.props.style
            return style.inset != null
                ? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
                : { left: parseFloat(style.left), top: parseFloat(style.top),
                    width: parseFloat(style.width), height: parseFloat(style.height) }
        }
        const parent = node.parent ? rect(node.parent) : { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
        if (node.props?.class === 'preview-actions') {
            return { left: parent.left + parseFloat(node.props.style?.left ?? parent.width - 42),
                top: parent.top + parseFloat(node.props.style?.top ?? 12), width: 30, height: 30 }
        }
        return parent
    }
    const element = type => ({ type, props: {}, children: [], parent: null, style: { display: '' },
        offsetWidth: 30, offsetHeight: 30,
        get clientWidth() { return rect(this).width }, get clientHeight() { return rect(this).height },
        get offsetParent() { return this.parent }, get parentElement() { return this.parent },
        getBoundingClientRect() { const r = rect(this); return { ...r, bottom: r.top + r.height } },
        setPointerCapture() {} })
    const root = element('root'), body = element('body')
    const renderer = Vue.createRenderer({ createElement: element, querySelector: () => body,
        createText: text => ({ type: '#text', text }), createComment: text => ({ type: '#comment', text }),
        setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text },
        patchProp: (node, key, old, value) => { node.props[key] = value },
        insert(node, parent, anchor) {
            if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
            node.parent = parent
            const index = anchor ? parent.children.indexOf(anchor) : -1
            if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
        },
        remove(node) { node.parent?.children.splice(node.parent.children.indexOf(node), 1); node.parent = null },
        parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] })
    const events = []
    const entry = Vue.reactive({ artifactKey: 'a', frameId: 'frame-a', loadState: ready ? 'ready' : 'error',
        descriptor: { publicationKey: 'publication-a', status: 'ready', sourceSessionId: 's',
            publication: { src: 'inline-artifacts/a/index.html' } },
        attachment: fullscreen ? null : {}, geometryVisible: !fullscreen,
        geometryRect: { x: 10, y: 100, width: 500, height: 360 },
        geometryClipRect: { x: 10, y: 150, width: 500, height: 100 } })
    const runtime = { entries: Vue.reactive(new Map([['a', entry]])), loadedEntries: ready ? [entry] : [], active: Vue.ref(true),
        fullscreenArtifactKey: Vue.ref(fullscreen ? 'a' : null),
        geometry: { schedule: () => events.push('geometry') }, reload: key => events.push(`reload:${key}`),
        closeFullscreen() { runtime.fullscreenArtifactKey.value = null; events.push('close-fullscreen') } }
    const iframe = { id: 'retained-iframe' }
    const overlay = Vue.markRaw(element('overlay'))
    overlay.props.class = 'frame-overlay'; overlay.parent = body; body.children.push(overlay)
    const pool = { geometryEpoch: 0, beginDividerDrag: () => events.push('drag-start'),
        endDividerDrag: () => events.push('drag-end'), frameEl: () => iframe, frameOverlayEl: () => overlay,
        frames: Vue.reactive({ 'frame-a': { visible: !hidden, rect: entry.geometryRect, clipRect: entry.geometryClipRect } }) }
    const app = renderer.createApp({ render: () => Vue.h(Host, { runtime, pool }) })
    if (openedInTab) app.provide('viewFileInFilesTab', path => openedInTab.push(path))
    for (const tag of ['wa-button', 'wa-icon']) {
        app.component(tag, { inheritAttrs: false, render() { return Vue.h(tag, this.$attrs, this.$slots.default?.()) } })
    }
    app.mount(root)
    t.after(() => app.unmount())
    const find = predicate => { const scan = node => predicate(node) ? node : node.children?.map(scan).find(Boolean); return scan(body) }
    const button = label => find(node => node.type === 'wa-button' && node.props['aria-label'] === label)
    const pointer = (button, type, x, y) => button.props[`onPointer${type}`]({ pointerId: 1, button: 0,
        clientX: x, clientY: y, currentTarget: button })
    const resize = (width, height) => {
        window.innerWidth = width; window.innerHeight = height
        listeners.get('resize')?.()
        for (const callback of observers) callback()
    }
    return { entry, runtime, pool, iframe, events, find, button, pointer, resize, rect }
}

test('the actual failed fullscreen host resizes without reactive entry changes and keeps Reload and Exit reachable', async t => {
    const f = fixture(t)
    await Vue.nextTick()
    const overlay = f.find(node => node.props?.class === 'inline-artifact-error')
    const row = f.find(node => node.props?.class === 'preview-actions')
    f.button('Tools').props.onClick(); await Vue.nextTick()
    const exit = f.button('Exit full screen')
    assert.equal(exit.props.disabled, false)
    f.resize(300, 180); await Vue.nextTick()
    assert.equal(f.rect(overlay).width, 300)
    assert.equal(f.rect(row).left + f.rect(row).width <= 300, true, 'default Tools follows the CSS viewport')
    f.resize(1000, 800); await Vue.nextTick()
    f.pointer(f.button('Hide tools'), 'down', 958, 12)
    f.pointer(f.button('Hide tools'), 'move', 950, 750)
    f.pointer(f.button('Hide tools'), 'up', 950, 750)
    f.button('Hide tools').props.onClick(); await Vue.nextTick()
    f.resize(300, 180); await Vue.nextTick()
    assert.equal(f.rect(overlay).width, 300)
    assert.equal(f.rect(overlay).height, 180)
    assert.equal(f.rect(row).left, 270)
    assert.equal(f.rect(row).top, 150)
    assert.equal(Object.is(f.button('Exit full screen'), exit), true)
    f.button('Reload').props.onClick()
    assert.equal(f.events.includes('reload:a'), true)
    f.button('Exit full screen').props.onClick(); await Vue.nextTick()
    assert.equal(f.events.includes('close-fullscreen'), true)
    assert.equal(Boolean(f.find(node => node.props?.class === 'inline-artifact-error')), false)
})

test('the actual inline error host supplies its visible clip to the same Tools component', async t => {
    const f = fixture(t, false)
    await Vue.nextTick()
    const row = f.find(node => node.props?.class === 'preview-actions')
    assert.equal(f.rect(row).top, 150)
    f.entry.geometryClipRect = { x: 10, y: 200, width: 500, height: 60 }
    await Vue.nextTick()
    assert.equal(f.rect(row).top, 200)
    f.button('Tools').props.onClick(); await Vue.nextTick()
    assert.equal(f.button('Full screen').props.disabled, true)
    f.button('Reload').props.onClick()
    assert.equal(f.events.includes('reload:a'), true)
})


test('the actual frame owner retains expanded Tools and dragged placement through transient visibility gaps', async t => {
    const f = fixture(t, false, true)
    await Vue.nextTick()
    f.button('Tools').props.onClick(); await Vue.nextTick()
    const row = f.find(node => node.props?.class === 'preview-actions')
    const start = f.rect(row)
    f.pointer(f.button('Hide tools'), 'down', start.left, start.top)
    f.pointer(f.button('Hide tools'), 'move', 300, 200)
    f.pointer(f.button('Hide tools'), 'up', 300, 200)
    f.button('Hide tools').props.onClick(); await Vue.nextTick()
    const top = row.props.style.top
    const left = row.props.style.left
    f.pool.frames['frame-a'].visible = false
    await Vue.nextTick()
    assert.equal(Boolean(f.find(node => node.props?.class === 'preview-actions')), true, 'the hidden menu stays mounted')
    assert.equal(row.style.display, 'none', 'hidden controls must have no pointer hit area')
    f.pool.frames['frame-a'].visible = true
    await Vue.nextTick()
    assert.equal(Object.is(f.find(node => node.props?.class === 'preview-actions'), row), true)
    assert.equal(row.style.display, '')
    assert.equal(row.props.style.top, top)
    assert.equal(row.props.style.left, left)
    assert.equal(Boolean(f.button('Hide tools')), true)
    assert.equal(Boolean(f.button('Reload')), true)
    assert.equal(Object.is(f.pool.frameEl('frame-a'), f.iframe), true)
})


test('the actual frame owner first mounted hidden computes the default clip placement when revealed', async t => {
    const f = fixture(t, false, true, true)
    await Vue.nextTick()
    const row = f.find(node => node.props?.class === 'preview-actions')
    assert.equal(row.style.display, 'none')
    assert.equal(row.props.style, undefined)
    f.pool.frames['frame-a'].visible = true
    await Vue.nextTick()
    assert.equal(row.style.display, '')
    assert.equal(f.rect(row).top, 150)
    assert.equal(Boolean(f.button('Tools')), true)
})


test('the frame owner opens its page in the Artifacts tab, leaving full screen first', async t => {
    const opened = []
    const f = fixture(t, true, true, false, opened)
    await Vue.nextTick()
    f.button('Tools').props.onClick(); await Vue.nextTick()
    const list = f.find(node => String(node.props?.class ?? '').includes('preview-actions-list'))
    const labels = list.children.filter(node => node.type === 'wa-button').map(node => node.props['aria-label'])
    assert.deepEqual(labels, ['Exit full screen', 'Reload', 'Open in Artifacts tab'])
    f.button('Open in Artifacts tab').props.onClick(); await Vue.nextTick()
    assert.deepEqual(opened, ['/data/artifacts/s/inline-artifacts/a/index.html'])
    assert.equal(f.events.includes('close-fullscreen'), true)
})

test('without an Artifacts tab (share viewer) the frame owner offers no Open in Artifacts tab', async t => {
    const f = fixture(t, true, true)
    await Vue.nextTick()
    f.button('Tools').props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.button('Reload')), true)
    assert.equal(Boolean(f.button('Open in Artifacts tab')), false)
})
