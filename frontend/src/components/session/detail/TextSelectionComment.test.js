import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

import { parse, compileScript } from '@vue/compiler-sfc'

const COMPONENT_URL = new URL('./TextSelectionComment.vue', import.meta.url)

function dataModule(source) {
    return `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
}

async function loadComponent() {
    const source = fs.readFileSync(COMPONENT_URL, 'utf8')
    const { descriptor } = parse(source, { filename: COMPONENT_URL.pathname })
    let compiled = compileScript(descriptor, { id: 'text-selection-comment-test' }).content

    const vueStub = dataModule(`
        export const ref = value => ({ value })
        // Tests that need a real provided value put it in globalThis.__tscProvided.
        export const inject = (key, fallback) => globalThis.__tscProvided?.[key] ?? fallback
        export const nextTick = callback => Promise.resolve().then(callback)
        export const computed = getter => ({ get value() { return getter() } })
        export const onMounted = () => {}
        export const onBeforeUnmount = () => {}
    `)
    const codeCommentsStub = dataModule('export const formatComment = () => ""')
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
        export const toast = { error() {}, success() {} }
    `)

    compiled = compiled
        .replace("from 'vue'", `from '${vueStub}'`)
        .replace("from '../../../stores/codeComments'", `from '${codeCommentsStub}'`)
        .replace("from '../../../stores/settings'", `from '${settingsStub}'`)
        .replace("from '../../../composables/useMediaPreview'", `from '${mediaPreviewStub}'`)
        .replace("from '../../../composables/useToast'", `from '${toastStub}'`)

    return (await import(dataModule(compiled))).default
}

function setupComponent(component, overrides = {}) {
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
        emit() {},
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
