import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as Vue from 'vue'

const source = readFileSync(new URL('./FloatingPreviewTools.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const compiled = compileScript(descriptor, { id: 'floating-tools-test', inlineTemplate: true,
    templateOptions: { compilerOptions: { isCustomElement: name => name.startsWith('wa-') } } }).content
let resize
const script = compiled.replace(/import \{([^}]+)\} from ['"]vue['"]/g,
    (_, names) => `const {${names.replace(/ as /g, ': ')}} = Vue`)
    .replace(/^import .*$/gm, '')
    .replace('export default', 'return')
const Tools = new Function('Vue', 'useResizeObserver', 'AppTooltip', script)(Vue,
    (target, callback) => { resize = callback }, { props: ['placement', 'for'], render() { return Vue.h('tooltip', { placement: this.placement }, this.$slots.default?.()) } })

function fixture(t, overrides = {}, rootGeometry = {}) {
    const element = type => ({ type, props: {}, children: [], parent: null, style: { display: '' },
        offsetWidth: 30, offsetHeight: 30, clientWidth: 500, clientHeight: 400,
        get offsetParent() { return this.parent },
        get parentElement() { return this.parent },
        getBoundingClientRect() { return this.type === 'root'
            ? { left: this.left ?? 0, top: this.top ?? 0, bottom: (this.top ?? 0) + this.clientHeight, width: this.clientWidth, height: this.clientHeight }
            : { left: (this.parent?.left ?? 0) + parseFloat(this.props.style?.left ?? this.parent?.defaultLeft ?? 450),
                top: (this.parent?.top ?? 0) + parseFloat(this.props.style?.top ?? this.parent?.defaultTop ?? 340),
                bottom: (this.parent?.top ?? 0) + parseFloat(this.props.style?.top ?? this.parent?.defaultTop ?? 340) + 30, width: 30, height: 30 } },
        setPointerCapture() {},
    })
    const renderer = Vue.createRenderer({
        createElement: element, createText: text => ({ type: '#text', text }),
        createComment: text => ({ type: '#comment', text }),
        setText: (node, text) => { node.text = text }, setElementText: (node, text) => { node.text = text },
        patchProp: (node, key, old, value) => { node.props[key] = value },
        insert(node, parent, anchor) {
            if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1)
            node.parent = parent
            const index = anchor ? parent.children.indexOf(anchor) : -1
            if (index < 0) parent.children.push(node); else parent.children.splice(index, 0, node)
        },
        remove(node) { node.parent?.children.splice(node.parent.children.indexOf(node), 1); node.parent = null },
        parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1],
    })
    const events = [], actions = []
    const props = Vue.reactive({ actions: [{ id: 'reload', icon: 'rotate-right', label: 'Reload', action: () => actions.push('reload') }],
        fullscreen: false, resetKey: 'first', modeActive: false, ...overrides,
        onToggleFullscreen: () => { props.fullscreen = !props.fullscreen; events.push('fullscreen') },
        onDragStart: () => events.push('drag-start'), onDragEnd: () => events.push('drag-end'),
    })
    const root = Object.assign(element('root'), rootGeometry)
    const app = renderer.createApp({ render: () => Vue.h(Tools, props) })
    for (const tag of ['wa-button', 'wa-icon']) {
        app.component(tag, { inheritAttrs: false, render() { return Vue.h(tag, this.$attrs, this.$slots.default?.()) } })
    }
    app.mount(root)
    let mounted = true
    const unmount = () => {
        if (!mounted) return
        mounted = false
        app.unmount()
    }
    t.after(unmount)
    const find = predicate => { const scan = node => predicate(node) ? node : node.children?.map(scan).find(Boolean); return scan(root) }
    const button = label => find(node => node.type === 'wa-button' && node.props['aria-label'] === label)
    const pointer = (button, type, x, y, id = 1) => button.props[`onPointer${type}`]({ pointerId: id,
        button: 0, clientX: x, clientY: y, currentTarget: button })
    return { props, root, events, actions, button, find, pointer, resize: () => resize(), unmount }
}

test('collapsed tools expose Reload and toggle the same fullscreen action on click', async t => {
    const f = fixture(t)
    assert.equal(Boolean(f.button('Reload')), false)
    assert.equal(f.button('Tools').props['aria-expanded'], false)
    f.button('Tools').props.onClick(); await Vue.nextTick()
    f.button('Reload').props.onClick()
    assert.deepEqual(f.actions, ['reload'])
    const full = f.button('Full screen')
    full.props.onClick(); await Vue.nextTick()
    assert.equal(Object.is(f.button('Exit full screen'), full), true)
    assert.equal(full.children[0].props.name, 'compress')
    full.props.onClick(); await Vue.nextTick()
    assert.equal(Object.is(f.button('Full screen'), full), true)
    f.button('Hide tools').props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.button('Reload')), false)
})

test('a single fullscreen action stays directly reachable without a tools toggle', t => {
    const f = fixture(t, { actions: [] })
    assert.equal(Boolean(f.button('Tools')), false)
    assert.equal(Boolean(f.button('Full screen')), true)
})

test('dragging clamps position, suppresses its click, balances hooks, and resets for the next page', async t => {
    const f = fixture(t, { modeActive: true })
    const tools = f.button('Tools')
    assert.equal(Boolean(f.find(node => node.props?.class === 'preview-tools-dot')), true)
    f.pointer(tools, 'down', 450, 340)
    f.pointer(tools, 'move', 452, 342)
    assert.deepEqual(f.events, [])
    f.pointer(tools, 'move', 150, 340)
    f.pointer(tools, 'up', 150, 340)
    tools.props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.button('Reload')), false)
    assert.deepEqual(f.events, ['drag-start', 'drag-end'])
    const row = f.find(node => node.props?.class === 'preview-actions')
    assert.equal(row.props.style.left, '150px')
    tools.props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.find(node => node.props?.class?.includes('preview-actions-list--up'))), true)
    f.root.clientWidth = 100; f.root.clientHeight = 100
    f.resize(); await Vue.nextTick()
    assert.equal(row.props.style.left, '70px')
    assert.equal(row.props.style.top, '70px')
    assert.equal(Boolean(f.find(node => node.type === 'tooltip' && node.props.placement === 'right')), true,
        'resize must use the clamped position before the DOM patch')
    f.props.resetKey = 'next'; await Vue.nextTick()
    assert.equal(Boolean(f.button('Reload')), false)
    assert.equal(row.props.style, undefined)
    f.pointer(f.button('Tools'), 'down', 450, 340)
    f.pointer(f.button('Tools'), 'move', 500, 390)
    f.unmount()
    assert.deepEqual(f.events, ['drag-start', 'drag-end', 'drag-start', 'drag-end'])
})

test('configured mode and link actions retain their behavior and indicator', async t => {
    const f = fixture(t, { actions: [{ id: 'mode', label: 'Select an element', icon: 'arrow-pointer', active: true,
        action() { f.actions.push('select') } }, { id: 'open', label: 'Open in new tab', icon: 'up-right-from-square', href: '/artifacts/7/' }] })
    f.button('Tools').props.onClick(); await Vue.nextTick()
    assert.equal(f.button('Select an element').props['aria-pressed'], true)
    f.button('Select an element').props.onClick()
    assert.deepEqual(f.actions, ['select'])
    assert.equal(f.button('Open in new tab').props.href, '/artifacts/7/')
    assert.equal(f.button('Open in new tab').props.target, '_blank')
    assert.equal(f.button('Open in new tab').props.rel, 'noopener')
})


test('folded and expanded menus stay reachable through fullscreen resize and cached zero-size parents', async t => {
    const f = fixture(t)
    const row = f.find(node => node.props?.class === 'preview-actions')
    const tools = f.button('Tools')
    f.pointer(tools, 'down', 450, 340)
    f.pointer(tools, 'move', 900, 700)
    f.pointer(tools, 'up', 900, 700)
    tools.props.onClick(); await Vue.nextTick()
    assert.equal(row.props.style.left, '470px')
    assert.equal(row.props.style.top, '370px')
    f.root.clientWidth = 0; f.root.clientHeight = 0
    f.resize(); await Vue.nextTick()
    assert.equal(row.props.style.left, '470px', 'cached zero-size parents must not reset position')
    f.root.clientWidth = 200; f.root.clientHeight = 180
    f.resize(); await Vue.nextTick()
    assert.equal(row.props.style.left, '170px')
    assert.equal(row.props.style.top, '150px')
    tools.props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.find(node => node.props?.class?.includes('preview-actions-list--up'))), true)
    f.button('Full screen').props.onClick(); await Vue.nextTick()
    f.root.clientWidth = 800; f.root.clientHeight = 600; f.resize()
    f.pointer(f.button('Hide tools'), 'down', 170, 150)
    f.pointer(f.button('Hide tools'), 'move', 760, 560)
    f.pointer(f.button('Hide tools'), 'up', 760, 560)
    await Vue.nextTick()
    assert.equal(row.props.style.left, '760px')
    f.button('Exit full screen').props.onClick(); await Vue.nextTick()
    f.root.clientWidth = 200; f.root.clientHeight = 180; f.resize(); await Vue.nextTick()
    assert.equal(row.props.style.left, '170px')
    assert.equal(row.props.style.top, '150px')
    f.pointer(f.button('Hide tools'), 'down', 170, 150)
    f.pointer(f.button('Hide tools'), 'move', 0, 0)
    f.pointer(f.button('Hide tools'), 'cancel', 0, 0)
    await Vue.nextTick()
    assert.equal(Boolean(f.find(node => node.props?.class?.includes('preview-actions-list--up'))), false)
    assert.equal(Boolean(f.find(node => node.type === 'tooltip' && node.props.placement === 'right')), true)
})

test('failed fullscreen keeps Reload and the shared exit button; an unavailable inline frame cannot expand', async t => {
    const f = fixture(t, { fullscreenDisabled: true })
    f.button('Tools').props.onClick(); await Vue.nextTick()
    assert.equal(f.button('Full screen').props.disabled, true)
    f.props.fullscreen = true; await Vue.nextTick()
    assert.equal(f.button('Exit full screen').props.disabled, false)
    f.button('Reload').props.onClick()
    assert.deepEqual(f.actions, ['reload'])
    f.button('Exit full screen').props.onClick(); await Vue.nextTick()
    assert.equal(f.button('Full screen').props.disabled, true)
})


test('the default Tools anchor follows the visible clip after GoLast without parent resize', async t => {
    const f = fixture(t, { frameRect: { x: 0, y: -326.548, width: 500, height: 873.991 },
        visibleBounds: { x: 0, y: 97.486, width: 500, height: 461.775 } },
        { top: -326.548, clientHeight: 873.991, defaultTop: 12 })
    await Vue.nextTick()
    const row = f.find(node => node.props?.class === 'preview-actions')
    assert.equal(row.props.style.top, '424.034px')
    f.button('Tools').props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.button('Reload')), true)
    assert.equal(Boolean(f.find(node => node.props?.class?.includes('preview-actions-list--up'))), false)
    f.props.visibleBounds = { x: 0, y: 200, width: 500, height: 150 }
    await Vue.nextTick()
    assert.equal(row.props.style.top, '526.548px')
    f.props.frameRect = { x: 0, y: 0, width: 500, height: 873.991 }
    f.props.visibleBounds = null
    f.root.top = 0
    await Vue.nextTick()
    assert.equal(row.props.style, undefined, 'unclipped default placement returns to the CSS corner')
})

test('dragged Tools stays inside a shrinking clip with unchanged parent dimensions and fullscreen return', async t => {
    const f = fixture(t, { frameRect: { x: 0, y: 100, width: 500, height: 360 },
        visibleBounds: { x: 0, y: 100, width: 500, height: 400 } },
        { top: 100, clientHeight: 360, defaultTop: 12 })
    await Vue.nextTick()
    const row = f.find(node => node.props?.class === 'preview-actions')
    const tools = f.button('Tools')
    f.pointer(tools, 'down', 450, 112)
    f.pointer(tools, 'move', 450, 430)
    f.pointer(tools, 'up', 450, 430)
    tools.props.onClick(); await Vue.nextTick()
    assert.equal(row.props.style.top, '330px')
    f.props.visibleBounds = { x: 0, y: 100, width: 500, height: 250 }
    await Vue.nextTick()
    assert.equal(row.props.style.top, '220px')
    tools.props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.find(node => node.props?.class?.includes('preview-actions-list--up'))), true)
    f.props.visibleBounds = { x: 100, y: 100, width: 100, height: 100 }
    await Vue.nextTick()
    assert.equal(row.props.style.left, '170px')
    assert.equal(row.props.style.top, '70px')
    f.button('Full screen').props.onClick()
    f.root.top = 0; f.root.clientWidth = 800; f.root.clientHeight = 600
    f.props.frameRect = { x: 0, y: 0, width: 800, height: 600 }; f.props.visibleBounds = null
    await Vue.nextTick()
    f.pointer(f.button('Hide tools'), 'down', 170, 70)
    f.pointer(f.button('Hide tools'), 'move', 760, 560)
    f.pointer(f.button('Hide tools'), 'up', 760, 560)
    f.button('Hide tools').props.onClick() // consume the drag's synthetic click
    await Vue.nextTick()
    f.button('Exit full screen').props.onClick()
    f.root.top = 100; f.root.clientWidth = 500; f.root.clientHeight = 360
    f.props.frameRect = { x: 0, y: 100, width: 500, height: 360 }
    f.props.visibleBounds = { x: 0, y: 150, width: 500, height: 100 }
    await Vue.nextTick()
    assert.equal(row.props.style.left, '470px')
    assert.equal(row.props.style.top, '120px')
    assert.equal(Boolean(f.button('Reload')), true)
    f.button('Hide tools').props.onClick(); await Vue.nextTick()
    assert.equal(Boolean(f.button('Tools')), true)
    assert.equal(row.props.style.top, '120px')
})
