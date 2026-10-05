import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { openSidebarCheckbox } from '../utils/railScopeNavigation.js'
import { runInNewContext } from 'node:vm'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('./ProjectView.vue')
const splitModule = read('../../node_modules/@awesome.me/webawesome/dist/components/split-panel/split-panel.js')
const splitSource = read(`../../node_modules/@awesome.me/webawesome/dist/chunks/${splitModule.match(/"\.\.\/\.\.\/chunks\/([^" ]+)"/)[1]}`)
const body = name => {
    const start = source.indexOf(`function ${name}(`)
    return source.slice(start, source.indexOf('\n}', start) + 2)
}

// Run Web Awesome's real conversion, watcher, and ResizeObserver methods.
// Flex layout can connect the element at size zero, before its first resize.
const methods = ['percentageToPixels', 'pixelsToPercentage', 'handleResize', 'handlePositionChange', 'handlePositionInPixelsChange']
const splitMethods = methods.map(name => {
    const start = splitSource.indexOf(`  ${name}(`)
    return splitSource.slice(start, splitSource.indexOf('\n  }', start) + 4)
}).join('\n')
const SplitPanel = runInNewContext(`(class { ${splitMethods} })`, { WaRepositionEvent: class {} })

function lifecycle(stored) {
    const storage = new Map([['twicc-sidebar-state', JSON.stringify(stored)]])
    const checkbox = { checked: !stored.open }
    const context = {
        DEFAULT_SIDEBAR_WIDTH: 300,
        SIDEBAR_COLLAPSE_THRESHOLD: 120,
        SIDEBAR_STORAGE_KEY: 'twicc-sidebar-state',
        localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
        document: { getElementById: () => checkbox },
        isMobile: () => false,
        ignoringReposition: false,
        lastKnownPosition: stored.open ? stored.width : 0,
        sidebarOpen: { value: stored.open },
        checked: { value: checkbox.checked },
    }
    runInNewContext(`${body('loadSidebarState')}; ${body('saveSidebarState')}; ${body('syncSidebarState')};
        ${body('handleSplitReposition')}; ${body('handleSidebarToggle')};
        sidebarState = loadSidebarState()`, context)
    const attribute = runInNewContext(source.match(/:position-in-pixels="([^"]+)"/)[1], context)
    const split = new SplitPanel()
    Object.assign(split, {
        size: 0, position: 50, primary: 'start', orientation: 'horizontal',
        positionInPixels: context.sidebarState.width, cachedPositionInPixels: 0,
        getAttribute: () => attribute,
        dispatchEvent: () => context.handleSplitReposition({ target: split, currentTarget: split }),
    })
    // Vue onMounted applies the stored property before WA knows the flex width.
    split.handlePositionInPixelsChange()
    assert.equal(split.position, Infinity)
    split.handlePositionChange()
    assert.ok(Number.isNaN(split.positionInPixels))
    // WA recovers invalid initialization from the HTML attribute on first resize.
    split.handleResize([{ contentRect: { width: 1346, height: 900 } }])
    split.handlePositionChange()
    return { split, context, checkbox, saved: () => JSON.parse(storage.get('twicc-sidebar-state')) }
}

test('zero-size split-panel initialization restores and preserves saved width', () => {
    const state = lifecycle({ open: true, width: 437 })
    assert.ok(Math.abs(state.split.positionInPixels - 437) < 0.001)
    assert.equal(state.saved().open, true)
    assert.ok(Math.abs(state.saved().width - 437) < 0.001)
})

test('closed sidebar retains saved width through initialization and reopen', () => {
    const state = lifecycle({ open: false, width: 437 })
    assert.ok(Math.abs(state.split.positionInPixels - 437) < 0.001)
    assert.deepEqual(state.saved(), { open: false, width: 437 })
    state.checkbox.checked = false
    state.context.sidebarOpen.value = true
    state.context.handleSidebarToggle()
    assert.deepEqual(state.saved(), { open: true, width: 437 })
})

test('normal drag persists a new width after initialization', () => {
    const state = lifecycle({ open: true, width: 437 })
    state.split.position = state.split.pixelsToPercentage(512)
    state.split.handlePositionChange()
    assert.deepEqual(state.saved(), { open: true, width: 512 })
})

test('missing saved state uses the default width for split-panel initialization', () => {
    const context = {
        DEFAULT_SIDEBAR_WIDTH: 300,
        SIDEBAR_STORAGE_KEY: 'twicc-sidebar-state',
        localStorage: { getItem: () => null },
    }
    const initial = runInNewContext(`${body('loadSidebarState')}; loadSidebarState()`, context)
    assert.equal(initial.open, true)
    assert.equal(initial.width, 300)
    const state = lifecycle(initial)
    assert.equal(state.split.positionInPixels, 300)
    assert.deepEqual(state.saved(), { open: true, width: 300 })
})

for (const mobile of [false, true]) {
    test(`rail open event uses the real ${mobile ? 'mobile' : 'desktop'} checkbox and persistence handler`, () => {
        const state = lifecycle({ open: false, width: 437 })
        const { context, checkbox } = state
        context.isMobile = () => mobile
        context.openSidebarCheckbox = openSidebarCheckbox
        checkbox.checked = !mobile
        checkbox.dispatchEvent = () => {
            context.sidebarOpen.value = mobile ? checkbox.checked : !checkbox.checked
            context.handleSidebarToggle()
        }
        runInNewContext(body('handleOpenSidebar'), context)
        context.handleOpenSidebar()
        context.handleOpenSidebar()
        assert.equal(context.checked.value, mobile)
        assert.equal(context.sidebarOpen.value, true)
        assert.deepEqual(state.saved(), { open: !mobile, width: 437 }, 'desktop persists width; mobile keeps desktop state')
    })
}
