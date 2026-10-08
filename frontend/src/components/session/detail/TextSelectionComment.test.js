import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'
import { unref } from 'vue'

const COMPONENT_URL = new URL('./TextSelectionComment.vue', import.meta.url)

function dataModule(source) {
    return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadComponent() {
    const source = fs.readFileSync(COMPONENT_URL, 'utf8')
    const { descriptor } = parse(source, { filename: COMPONENT_URL.pathname })
    let compiled = compileScript(descriptor, { id: 'text-selection-comment-test' }).content

    const vueStub = dataModule(`
        export const ref = value => ({ value, __v_isRef: true })
        // Tests that need a real provided value put it in globalThis.__tscProvided.
        export const inject = (key, fallback) => globalThis.__tscProvided?.[key] ?? fallback
        export const nextTick = callback => Promise.resolve().then(callback)
        export const computed = getter => ({ __v_isRef: true, get value() { return getter() } })
        export const onMounted = () => {}
        export const onBeforeUnmount = () => {}
    `)
    const codeCommentsStub = dataModule('export const formatComment = (...args) => globalThis.__tscFormat?.(...args) ?? ""')
    const settingsStub = dataModule(`
        export const useSettingsStore = () => ({
            isTouchDevice: true,
            isMac: false,
            selectionCommentHintDismissed: true,
            setSelectionCommentHintDismissed() {},
        })
    `)
    const mediaPreviewStub = dataModule(`
        export const isOpen = { value: false }
        export const openMediaPreview = () => {}
    `)
    const toastStub = dataModule(`
        export const toast = { error(msg) { globalThis.__tscErrors?.push(msg) }, success() {} }
    `)

    compiled = compiled
        .replace("from 'vue'", `from '${vueStub}'`)
        .replace("from '../../../stores/codeComments'", `from '${codeCommentsStub}'`)
        .replace("from '../../../stores/settings'", `from '${settingsStub}'`)
        .replace("from '../../../composables/useMediaPreview'", `from '${mediaPreviewStub}'`)
        .replace("from '../../../composables/useToast'", `from '${toastStub}'`)

    return (await import(dataModule(compiled))).default
}

function setupComponent(component, overrides = {}, emit = () => {}) {
    return component.setup({
        selectedText: 'selected text',
        position: { top: 100, left: 150, above: false },
        autoExpand: false,
        sourceLabel: '',
        subject: 'selected text',
        metadata: null,
        quoteMode: 'code',
        clearSourceSelection() {},
        captureScreenshot: null,
        attachScreenshot: null,
        focusComposerOnAdd: false,
        ...overrides,
    }, {
        expose() {},
        emit,
    })
}

test('clamp moves the panel below the shifted visual viewport top', async () => {
    const originalWindow = globalThis.window
    globalThis.window = {
        innerWidth: 800,
        innerHeight: 600,
        visualViewport: {
            offsetLeft: 0,
            offsetTop: 200,
            width: 400,
            height: 300,
        },
    }

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component)
        bindings.rootRef.value = {
            getBoundingClientRect: () => ({
                left: 50,
                right: 250,
                top: 100,
                bottom: 250,
            }),
        }

        bindings.clampToViewport()

        assert.deepEqual(bindings.panelOffset.value, { dx: 0, dy: 108 })
    } finally {
        globalThis.window = originalWindow
    }
})

test('clamp moves the panel inside the shifted visual viewport left edge', async () => {
    const originalWindow = globalThis.window
    globalThis.window = {
        innerWidth: 800,
        innerHeight: 600,
        visualViewport: {
            offsetLeft: 100,
            offsetTop: 0,
            width: 300,
            height: 600,
        },
    }

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component)
        bindings.rootRef.value = {
            getBoundingClientRect: () => ({
                left: 50,
                right: 250,
                top: 100,
                bottom: 250,
            }),
        }

        bindings.clampToViewport()

        assert.deepEqual(bindings.panelOffset.value, { dx: 58, dy: 0 })
    } finally {
        globalThis.window = originalWindow
    }
})

test('visual viewport scrolling re-clamps the expanded panel', async () => {
    const originalWindow = globalThis.window
    const visualViewport = new EventTarget()
    Object.assign(visualViewport, {
        offsetLeft: 0,
        offsetTop: 0,
        width: 400,
        height: 600,
    })
    globalThis.window = {
        innerWidth: 800,
        innerHeight: 600,
        visualViewport,
        requestAnimationFrame: callback => {
            queueMicrotask(callback)
            return 1
        },
    }

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component)
        bindings.rootRef.value = {
            getBoundingClientRect: () => ({
                left: 50,
                right: 250,
                top: 100,
                bottom: 250,
            }),
        }

        bindings.expand()
        await Promise.resolve()
        visualViewport.offsetTop = 200
        visualViewport.height = 300
        visualViewport.dispatchEvent(new Event('scroll'))
        await Promise.resolve()

        assert.deepEqual(bindings.panelOffset.value, { dx: 0, dy: 108 })
    } finally {
        globalThis.window = originalWindow
    }
})

test('visual viewport resize and scroll apply one correction before the next render', async () => {
    const originalWindow = globalThis.window
    const visualViewport = new EventTarget()
    Object.assign(visualViewport, {
        offsetLeft: 0,
        offsetTop: 0,
        width: 400,
        height: 600,
    })
    globalThis.window = {
        innerWidth: 800,
        innerHeight: 600,
        visualViewport,
        requestAnimationFrame: callback => {
            queueMicrotask(callback)
            return 1
        },
    }

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component)
        bindings.rootRef.value = {
            getBoundingClientRect: () => ({
                left: 50,
                right: 250,
                top: 100,
                bottom: 250,
            }),
        }

        bindings.expand()
        await Promise.resolve()
        visualViewport.offsetTop = 200
        visualViewport.height = 300
        visualViewport.dispatchEvent(new Event('resize'))
        visualViewport.dispatchEvent(new Event('scroll'))
        await Promise.resolve()

        assert.deepEqual(bindings.panelOffset.value, { dx: 0, dy: 108 })
    } finally {
        globalThis.window = originalWindow
    }
})

test('an above-selection anchor stays stable when the visual viewport height changes', async () => {
    const originalWindow = globalThis.window
    globalThis.window = {
        innerWidth: 800,
        innerHeight: 600,
        visualViewport: {
            offsetLeft: 0,
            offsetTop: 0,
            width: 400,
            height: 600,
        },
    }

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component, {
            position: { top: 500, left: 150, above: true },
        })
        bindings.expanded.value = true
        bindings.panelOffset.value = { dx: 0, dy: -100 }
        globalThis.window.visualViewport.height = 300

        assert.deepEqual(bindings.rootStyle.value, {
            left: '150px',
            top: '484px',
            transform: 'translate(calc(-50% + 0px), calc(-100% + -100px))',
        })
    } finally {
        globalThis.window = originalWindow
    }
})

test('window resizing re-clamps the panel when VisualViewport is unavailable', async () => {
    const originalWindow = globalThis.window
    const windowTarget = new EventTarget()
    Object.assign(windowTarget, {
        innerWidth: 400,
        innerHeight: 600,
        visualViewport: null,
        requestAnimationFrame: callback => {
            queueMicrotask(callback)
            return 1
        },
    })
    globalThis.window = windowTarget

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component)
        bindings.rootRef.value = {
            getBoundingClientRect: () => ({
                left: 50,
                right: 250,
                top: 350,
                bottom: 500,
            }),
        }

        bindings.expand()
        await Promise.resolve()
        windowTarget.innerHeight = 300
        windowTarget.dispatchEvent(new Event('resize'))
        await Promise.resolve()

        assert.deepEqual(bindings.panelOffset.value, { dx: 0, dy: -208 })
    } finally {
        globalThis.window = originalWindow
    }
})

// The settings stub above reports a touch device, which is the whole point here:
// a tap must not steal focus (it pops the on-screen keyboard), but ⌘↵ proves a
// physical keyboard, so chaining ⌘↵ (add) → ⌘↵ (send) has to keep working.
test('on touch, the keyboard add focuses the composer while a tap does not', async () => {
    const originalWindow = globalThis.window
    const inserts = []
    globalThis.window = { innerWidth: 800, innerHeight: 600, removeEventListener() {} }
    globalThis.__tscProvided = { insertTextAtCursor: (_text, options) => inserts.push(options) }

    try {
        const component = await loadComponent()
        const bindings = setupComponent(component, { focusComposerOnAdd: true })

        await bindings.addToMessage()
        await bindings.addToMessage({ fromKeyboard: true })

        assert.deepEqual(inserts, [{ focus: false }, { focus: true }])
    } finally {
        globalThis.window = originalWindow
        delete globalThis.__tscProvided
    }
})

async function formFixture(bridge, overrides = {}) {
    const originalWindow = globalThis.window
    globalThis.window = { innerWidth: 800, innerHeight: 600, removeEventListener() {} }
    const inserted = [], errors = [], formats = [], emitted = []
    globalThis.__tscProvided = { selectionCommentSend: bridge, insertTextAtCursor: (text, options) => inserted.push({ text, options }) }
    globalThis.__tscErrors = errors
    globalThis.__tscFormat = (...args) => { formats.push(args); return 'formatted' }
    const bindings = setupComponent(await loadComponent(), overrides, event => emitted.push(event))
    return { bindings, inserted, errors, formats, emitted, cleanup() {
        globalThis.window = originalWindow
        for (const key of ['__tscProvided', '__tscErrors', '__tscFormat']) delete globalThis[key]
    } }
}

test('Send to Agent delegates formatted content and closes only after insertion', async () => {
    const calls = []
    const f = await formFixture({ available: true, pending: false, async send(text, options) { calls.push(text); options.onPrepared() } }, {
        metadata: { filePath: '/a.js', lineFrom: 2, lineTo: 3, quoteMode: 'code', lang: 'js' }, sourceLabel: 'source',
    })
    try {
        f.bindings.commentText.value = 'comment'
        await f.bindings.sendToAgent()
        assert.deepEqual(calls, ['formatted\n'])
        assert.deepEqual(f.inserted, [])
        assert.deepEqual(f.emitted, ['send-prepared', 'close'])
        assert.deepEqual(f.formats[0], [
            { lineText: 'selected text', content: 'comment', filePath: '/a.js', lineFrom: 2, lineTo: 3 },
            { isSelectedText: true, sourceLabel: 'source', subject: 'selected text', quoteMode: 'code', lang: 'js' },
        ])
        await f.bindings.addToMessage()
        assert.deepEqual(f.formats[1], f.formats[0])
        assert.equal(f.inserted[0].text, 'formatted\n')
    } finally { f.cleanup() }
})

test('screenshot callback passes early upload notification and returns record once', async () => {
    let capturedOptions, attachmentCalls = 0
    const f = await formFixture({ available: true, pending: false, async send(text, options) {
        const onUploadStarted = () => {}
        const record = await options.attachScreenshot({ onUploadStarted })
        capturedOptions = { onUploadStarted }
        assert.equal(record.id, 'shot')
        options.onPrepared()
    } }, { attachScreenshot: async (url, options) => { attachmentCalls++; assert.equal(url, 'data:image/png;test'); capturedOptions = options; return { id: 'shot' } } })
    try {
        f.bindings.screenshotDataUrl.value = 'data:image/png;test'
        await f.bindings.sendToAgent()
        assert.equal(attachmentCalls, 1)
        assert.equal(typeof capturedOptions.onUploadStarted, 'function')
        assert.equal(f.bindings.submitting.value, false)
    } finally { f.cleanup() }
})

test('attachment rejection preserves form and uses existing attachment error toast', async () => {
    const f = await formFixture({ available: true, pending: false, async send(text, options) { await options.attachScreenshot({}) } }, {
        attachScreenshot: async () => { throw Error('storage') },
    })
    try {
        f.bindings.screenshotDataUrl.value = 'data:image/png;test'
        await f.bindings.sendToAgent()
        assert.deepEqual(f.emitted, [])
        assert.deepEqual(f.errors, ["Couldn't attach screenshot: storage"])
        assert.equal(f.bindings.submitting.value, false)
    } finally { f.cleanup() }
})

test('duplicate form submission and a pending session operation do not start another send', async () => {
    let finish, calls = 0
    const bridge = { available: true, pending: false, send() { calls++; return new Promise(resolve => { finish = resolve }) } }
    const f = await formFixture(bridge)
    try {
        const first = f.bindings.sendToAgent()
        await f.bindings.sendToAgent()
        assert.equal(calls, 1)
        finish()
        await first
        bridge.pending = true
        await f.bindings.sendToAgent()
        assert.equal(calls, 1)
        await f.bindings.addToMessage()
        assert.equal(f.inserted.length, 1)
    } finally { f.cleanup() }
})

test('Ctrl+Enter still adds to message instead of sending', async () => {
    const f = await formFixture({ available: true, pending: false, send() { assert.fail('shortcut must not send') } })
    try {
        f.bindings.handleKeydown({ key: 'Enter', ctrlKey: true, preventDefault() {} })
        await Promise.resolve()
        assert.equal(f.inserted.length, 1)
    } finally { f.cleanup() }
})

test('rendered form offers three actions only with a composer and disables only Send during another operation', async () => {
    const bridge = { available: true, pending: true }
    const f = await formFixture(bridge)
    try {
        f.bindings.expanded.value = true
        const source = fs.readFileSync(COMPONENT_URL, 'utf8')
        const { descriptor } = parse(source)
        const bindings = compileScript(descriptor, { id: 'button-test' }).bindings
        const { code, errors } = compileTemplate({ source: descriptor.template.content, filename: 'TextSelectionComment.vue', id: 'button-test',
            compilerOptions: { bindingMetadata: bindings, isCustomElement: tag => tag.startsWith('wa-') } })
        assert.deepEqual(errors, [])
        const compiled = code.replace('from "vue"', `from '${import.meta.resolve('vue')}'`)
        const { render } = await import(dataModule(compiled))
        const setup = new Proxy(f.bindings, { get: (target, key) => unref(target[key]) })
        function buttons(vnode, out = []) {
            if (!vnode || typeof vnode !== 'object') return out
            if (vnode.type === 'wa-button') out.push(vnode)
            if (Array.isArray(vnode.children)) vnode.children.forEach(child => buttons(child, out))
            return out
        }
        const actions = buttons(render({}, [], {}, setup))
        assert.deepEqual(actions.map(button => button.children.trim()), ['Cancel', 'Add to message', 'Send to Agent'])
        assert.equal(actions[1].props.disabled, false)
        assert.equal(actions[2].props.disabled, true)
        bridge.available = false
        const withoutComposer = buttons(render({}, [], {}, setup))
        assert.deepEqual(withoutComposer.map(button => button.children.trim()), ['Cancel', 'Add to message'])
    } finally { f.cleanup() }
})
