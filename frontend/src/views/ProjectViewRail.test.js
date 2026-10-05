import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { computed, ref, reactive, watch, nextTick, effectScope } from 'vue'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const source = read('./ProjectView.vue')
const script = source.match(/<script setup>([\s\S]*?)<\/script>/)[1]
const css = source.slice(source.indexOf('<style'))
const body = name => {
    const start = script.indexOf(`function ${name}(`)
    assert.ok(start >= 0, name)
    return script.slice(start, script.indexOf('\n}', start) + 2)
}

test('effective state and CSS expose exactly one reopen control across the breakpoint', () => {
    assert.match(script, /const checked = ref\(initialSidebarChecked.value\)/)
    const open = script.match(/const sidebarOpen = computed\(\(\) => ([^\n]+)\)/)?.[1]
    const collapsed = script.match(/const railCollapsed = computed\(\(\) => ([^\n]+)\)/)?.[1]
    assert.equal(open, 'isNarrowViewport.value ? checked.value : !checked.value')
    assert.equal(collapsed, '!sidebarOpen.value && !settingsStore.isSidebarRailVisibleWhenClosed')
    for (const narrow of [false, true]) for (const checked of [false, true]) for (const keepVisible of [false, true]) {
        const sidebarOpen = runInNewContext(open, { isNarrowViewport: { value: narrow }, checked: { value: checked } })
        const railCollapsed = runInNewContext(collapsed, { sidebarOpen: { value: sidebarOpen }, settingsStore: { isSidebarRailVisibleWhenClosed: keepVisible } })
        assert.equal(sidebarOpen, narrow ? checked : !checked)
        assert.equal(railCollapsed, !sidebarOpen && !keepVisible)
        const railVisible = !railCollapsed
        const floatingVisible = railCollapsed
        assert.equal(railVisible, !railCollapsed)
        assert.equal(floatingVisible, railCollapsed)
        assert.equal(railVisible || floatingVisible, true)
        assert.equal(railVisible && floatingVisible, false)
    }
    for (const [breakpoint, condition] of [['>=', ':checked'], ['<', ':not(:checked)']]) {
        const selector = `.project-view-wrapper[data-rail-when-closed="hidden"]:has(.sidebar-toggle-checkbox${condition})`
        const media = css.lastIndexOf(`@media (width ${breakpoint} 640px)`, css.indexOf(selector))
        assert.ok(media >= 0)
        const openBrace = css.indexOf('{', media)
        let depth = 1
        let end = openBrace + 1
        while (depth) {
            if (css[end] === '{') depth++
            if (css[end] === '}') depth--
            end++
            assert.ok(end <= css.length, 'media block closes')
        }
        const block = css.slice(media, end)
        assert.ok(block.includes(`${selector} {\n        --rail-width: 0px;`))
        assert.ok(block.includes(`${selector} .sidebar-rail {\n        overflow: hidden;\n        visibility: hidden;`))
        assert.ok(block.includes(`${selector} .sidebar-toggle {\n        visibility: visible;`))
    }
})

test('checkbox mutations synchronize actual state and body clearance lifecycle', () => {
    assert.equal((script.match(/`\(width < \$\{MOBILE_BREAKPOINT\}px\)`/g) ?? []).length, 2)
    assert.ok(source.includes(':checked="checked"'))
    assert.doesNotMatch(script, /watch\(initialSidebarChecked/)
    const sync = body('syncSidebarState')
    assert.match(sync, /if \(checkbox\) checked.value = checkbox.checked/)
    let actual = { checked: true }
    const state = { value: false }
    const context = { document: { getElementById: () => actual }, checked: state }
    runInNewContext(`${sync}; syncSidebarState()`, context)
    assert.equal(state.value, true)
    actual = null
    runInNewContext(`${sync}; syncSidebarState()`, context)
    assert.equal(state.value, true, 'absent checkbox retains state')
    assert.match(body('resetSidebarToDefault'), /syncSidebarState\(\)/)
    assert.doesNotMatch(body('resetSidebarToDefault'), /saveSidebarState\(\{ open: true/)
    assert.equal((body('handleSplitReposition').match(/syncSidebarState\(\)/g) ?? []).length, 2)
    assert.match(body('handleSidebarToggle'), /syncSidebarState\(\)/)
    assert.match(script, /checkbox.checked = !newSessionId\s*syncSidebarState\(\)/)
    assert.match(script, /watch\(railCollapsed, \(collapsed\) => \{\s*document.body.classList.toggle\('sidebar-toggle-floating', collapsed\)\s*}, \{ immediate: true \}\)/)
    assert.match(script, /onBeforeUnmount\(\(\) => \{\s*document.body.classList.remove\('sidebar-toggle-floating'\)/)
    assert.match(script, /lastKnownPosition = sidebarState.open \? sidebarState.width : 0[\s\S]*?syncSidebarState\(\)/)
    assert.match(body('toggleSidebar'), /checkbox.dispatchEvent\(new Event\('change'\)\)/)
})

test('rail actions, unconditional floating anchor, and focus transfers are connected', () => {
    assert.match(script, /registerCommands, unregisterCommands, openPalette/)
    assert.match(script, /const floatingToggleEl = shallowRef\(null\)/)
    const floating = body('handleFloatingToggle')
    assert.match(floating, /toggleSidebar\(\)\s*await nextTick\(\)\s*document.getElementById\('sidebar-rail-toggle'\)\?\.focus\(\)/)
    assert.match(body('handleRailToggle'), /toggleSidebar\(\)\s*await nextTick\(\)\s*if \(railCollapsed.value\) floatingToggleEl.value\?\.focus\(\)/)
    assert.match(body('handleRailSelectMode'), /if \(mode === \(isArtifactsMode.value \? 'artifacts' : 'sessions'\)\) return\s*toggleSidebarView\(\)/)
    for (const attr of [':settings-anchor="railCollapsed ? floatingToggleEl : null"', `:mode="isArtifactsMode ? 'artifacts' : 'sessions'"`, ':sidebar-open="sidebarOpen"', ':peer-configured="peerSystemConfigured"', ':inbox-count="peersStore.inboxCount"', '@home="handleBackHome"', '@search="openAdvancedSearch"', '@palette="openPalette"', '@inbox="openPeerInbox"', '@toggle-sidebar="handleRailToggle"', '@select-mode="handleRailSelectMode"']) assert.ok(source.includes(attr), attr)
    assert.match(body('openPeerInbox'), /dispatchEvent\(new CustomEvent\('twicc:open-peer-inbox'\)\)/)
    const button = source.match(/<button[^>]*id="sidebar-toggle-button"[^>]*>/)?.[0]
    assert.ok(button)
    assert.doesNotMatch(button, /v-if|v-show/)
    for (const attr of ['ref="floatingToggleEl"', 'class="sidebar-toggle rail-button"', ':aria-label="OPEN_SIDEBAR_LABEL"', '@click="handleFloatingToggle"']) assert.ok(button.includes(attr), attr)
    assert.match(source, /<wa-icon name="angles-right"><\/wa-icon>\s*<PeerInboxBadge v-if="peersStore.inboxCount > 0" :count="peersStore.inboxCount" \/>\s*<\/button>\s*<AppTooltip for="sidebar-toggle-button" placement="top">/)
    assert.ok(source.indexOf('<SidebarRail') < source.indexOf('<button', source.indexOf('<SidebarRail')))
    assert.ok(source.indexOf('placement="top">{{ OPEN_SIDEBAR_LABEL }}') < source.indexOf('<wa-split-panel'))
    assert.match(source, /:data-rail-when-closed="settingsStore.isSidebarRailVisibleWhenClosed \? 'visible' : 'hidden'"/)
    assert.match(source, /'project-view-wrapper--peer': peerSystemConfigured/)
})

test('toggle handlers wait for the update before transferring focus', async () => {
    for (const [handler, collapsed, expected] of [
        ['handleFloatingToggle', false, ['toggle', 'tick', 'rail']],
        ['handleRailToggle', true, ['toggle', 'tick', 'floating']],
        ['handleRailToggle', false, ['toggle', 'tick']],
    ]) {
        const calls = []
        const context = {
            toggleSidebar: () => calls.push('toggle'),
            nextTick: async () => calls.push('tick'),
            railCollapsed: { value: collapsed },
            floatingToggleEl: { value: { focus: () => calls.push('floating') } },
            document: { getElementById: id => {
                assert.equal(id, 'sidebar-rail-toggle')
                return { focus: () => calls.push('rail') }
            } },
        }
        await runInNewContext(`async ${body(handler)}; ${handler}()`, context)
        assert.deepEqual(calls, expected)
    }
})

test('selecting the active rail mode does nothing and other modes retain navigation memory', () => {
    for (const artifacts of [false, true]) for (const mode of ['sessions', 'artifacts']) {
        let toggles = 0
        runInNewContext(`${body('handleRailSelectMode')}; handleRailSelectMode(mode)`, {
            mode, isArtifactsMode: { value: artifacts }, toggleSidebarView: () => toggles++,
        })
        assert.equal(toggles, mode === (artifacts ? 'artifacts' : 'sessions') ? 0 : 1)
    }
    const navigation = body('toggleSidebarView')
    assert.match(navigation, /if \(lastSessionsLocation.value\) router.push\(lastSessionsLocation.value\)/)
    assert.match(navigation, /if \(lastArtifactsLocation.value\) router.push\(lastArtifactsLocation.value\)/)
})

test('floating surfaces, mobile geometry, and conditional footer replace old controls', () => {
    assert.match(css, /\.project-view-wrapper \{\s*position: relative;\s*display: flex;\s*height: 100dvh;/)
    assert.match(css, /\.project-view \{[^}]*flex: 1;[^}]*min-width: 0;/)
    assert.match(css, /\.sidebar-toggle \{[^}]*position: absolute;[^}]*visibility: hidden;[^}]*z-index: 5;[^}]*--rail-button-bg: var\(--wa-color-surface-default\);[^}]*border: var\(--panel-border\);[^}]*border-radius: var\(--panel-radius\);[^}]*box-shadow: var\(--panel-shadow\);/)
    assert.match(css, /--sidebar-width: min\(300px, calc\(80vw - var\(--rail-width\)\)\);/)
    assert.match(css, /transform: translateX\(calc\(-100% - var\(--rail-width\)\)\);/)
    assert.match(css, /inset: 0;\s*left: var\(--rail-width\);/)
    assert.match(css, /max-width: 50rem;/)
    assert.match(css, /\.project-view-wrapper--peer \.sidebar-toggle \{\s*--sidebar-toggle-offset: var\(--wa-space-xs\);/)
    assert.match(css, /--sidebar-toggle-offset: var\(--wa-space-s\);/)
    for (const edge of ['left', 'bottom']) assert.ok(css.includes(`${edge}: calc(var(--sidebar-toggle-offset) + var(--panel-gap));`))
    const toggleRules = [...css.matchAll(/\.sidebar-toggle \{([^}]*)}/g)].map(match => match[1])
    for (const rule of toggleRules) assert.doesNotMatch(rule, /transition|transform|translate/)
    assert.match(script, /const hasSidebarFooter = computed\(\(\) => !!\(\(quotaHasUsage.value && quotaComputed.value\) \|\| unauthenticatedProviders.value.length\)\)/)
    assert.match(source, /<wa-divider v-if="hasSidebarFooter"><\/wa-divider>\s*<div v-if="hasSidebarFooter" class="sidebar-footer">/)
    assert.match(source, /'sidebar--no-footer': !hasSidebarFooter/)
    assert.match(css, /\.sidebar--no-footer \{\s*padding-bottom: var\(--panel-gap\);/)
    assert.match(css, /@container sidebar \(width <= 50px\) \{[\s\S]*?\.sidebar-header \{\s*visibility: hidden;/)
    assert.doesNotMatch(source.replace(/\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->|\/\/[^\n]*/g, ''), /SidebarViewSwitch|CommandPaletteButton|sidebarClosed|updateSidebarClosedClass|sidebar-footer-buttons|sidebar-toggle-shift|sidebar-footer-inset|back-button|icon-collapse|icon-expand/)
    assert.doesNotMatch(read('../components/session/SessionsSidebarControls.vue').replace(/\/\*[\s\S]*?\*\/|\/\/[^\n]*/g, ''), /openAdvancedSearch|search-advanced-button/)
    assert.doesNotMatch(read('../components/peer/PeerInboxButton.vue'), /@container sidebar/)
    for (const path of ['../components/sidebar/SidebarViewSwitch.vue', '../components/app/CommandPaletteButton.vue']) assert.equal(existsSync(new URL(path, import.meta.url)), false)
})

test('project view SFC compiles script, template, and styles', async () => {
    const { parse, compileScript, compileTemplate, compileStyle } = await import('@vue/compiler-sfc')
    const { descriptor, errors } = parse(source, { filename: 'ProjectView.vue' })
    assert.deepEqual(errors, [])
    const id = 'data-v-project-rail'
    const compiled = compileScript(descriptor, { id })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'ProjectView.vue', id,
        compilerOptions: { bindingMetadata: compiled.bindings } }).errors, [])
    for (const style of descriptor.styles) assert.deepEqual(compileStyle({ source: style.content, filename: 'ProjectView.vue', id, scoped: style.scoped }).errors, [])
})

// Execute the production state and watchers with Vue's real scheduler.
function sidebarHarness({ mobile, open, artifacts = false, bookmarkId, bookmarks = { pinned: { id: 'pinned' } }, loaded = true }) {
    const scope = effectScope()
    const route = reactive({ params: { sessionId: artifacts ? undefined : 'session', bookmarkId } })
    const isArtifactsMode = ref(artifacts)
    const narrow = ref(mobile)
    const checkbox = { checked: false }
    const saved = []
    const store = reactive({ artifactBookmarks: bookmarks, artifactBookmarksLoaded: loaded })
    const context = {
        computed, ref, watch, route, isArtifactsMode, store,
        sessionId: computed(() => route.params.sessionId),
        isNarrowViewport: narrow,
        sidebarState: { open, width: 320 },
        isMobile: () => narrow.value,
        window: {}, document: { getElementById: () => checkbox },
        SIDEBAR_COLLAPSE_THRESHOLD: 50, requestAnimationFrame: () => {},
        settingsStore: { isSidebarRailVisibleWhenClosed: true },
        quotaHasUsage: ref(false), quotaComputed: ref(null), unauthenticatedProviders: ref([]),
        shallowRef: ref, saveSidebarState: state => saved.push(state),
    }
    const state = script.slice(script.indexOf('const initialSidebarChecked ='), script.indexOf('watch(initialSidebarChecked') >= 0
        ? script.indexOf('watch(initialSidebarChecked') : script.indexOf('// Open the sidebar on entry'))
    const mobileWatcher = script.slice(script.indexOf('// On mobile, session selection'), script.indexOf('// Reset sidebar to default width'))
    const entryStart = script.indexOf('// Open the sidebar on entry')
    const entryWatcher = entryStart < 0 ? '' : script.slice(entryStart, script.indexOf('watch(railCollapsed', entryStart))
    let result
    scope.run(() => {
        result = runInNewContext(`${state}
let lastKnownPosition = 0;
let ignoringReposition = false;
${body('syncSidebarState')}
${body('handleSidebarToggle')}
${body('handleSplitReposition')}
${mobileWatcher}
${entryWatcher}
;({ checked, sidebarOpen })`, context)
        checkbox.checked = result.checked.value
        // Simulate the checkbox property patch before post-flush route effects.
        watch(result.checked, value => { checkbox.checked = value })
    })
    return { ...result, route, isArtifactsMode, narrow, checkbox, saved,
        store, toggle: () => runInNewContext('handleSidebarToggle()', context),
        collapseByDrag: () => runInNewContext('const panel = { positionInPixels: 30 }; handleSplitReposition({ target: panel, currentTarget: panel })', context),
        stop: () => scope.stop() }
}

test('artifact entry opens only unselected destinations after route and checkbox updates', async () => {
    for (const mobile of [false, true]) for (const open of [false, true]) for (const bookmarkId of [undefined, 'saved-bookmark']) {
        const h = sidebarHarness({ mobile, open, bookmarkId: undefined })
        h.checkbox.checked = mobile ? open : !open
        h.checked.value = h.checkbox.checked
        h.route.params.sessionId = undefined
        h.route.params.bookmarkId = bookmarkId
        h.isArtifactsMode.value = true
        await nextTick()
        assert.equal(h.sidebarOpen.value, bookmarkId ? open : true, JSON.stringify({ mobile, open, bookmarkId }))
        assert.equal(h.checkbox.checked, mobile ? h.sidebarOpen.value : !h.sidebarOpen.value)
        if (!mobile && !bookmarkId) assert.equal(h.saved.at(-1).open, true)
        h.stop()
    }
})

test('artifact selection, deselection, and viewport changes do not force the sidebar open', async () => {
    for (const mobile of [false, true]) {
        const h = sidebarHarness({ mobile, open: false, artifacts: true, bookmarkId: 'saved-bookmark' })
        assert.equal(h.sidebarOpen.value, false, 'selected artifact mount respects stored open state')
        h.route.params.bookmarkId = undefined
        await nextTick()
        assert.equal(h.sidebarOpen.value, false, 'deselection is not mode entry')
        h.route.params.bookmarkId = 'other-bookmark'
        await nextTick()
        assert.equal(h.sidebarOpen.value, false)
        // Preserve existing inverted checkbox semantics across viewport changes.
        h.narrow.value = !mobile
        await nextTick()
        assert.equal(h.checked.value, h.checkbox.checked)
        assert.equal(h.sidebarOpen.value, true, 'existing checkbox inversion changes effective open state')
        assert.equal(h.saved.length, 0, 'resize does not run entry effects')
        h.stop()
    }
})

test('bare artifact mount opens the sidebar and repeated mode entry opens it again', async () => {
    for (const mobile of [false, true]) {
        const h = sidebarHarness({ mobile, open: false, artifacts: true })
        assert.equal(h.sidebarOpen.value, true)
        h.isArtifactsMode.value = false
        h.route.params.sessionId = 'session'
        await nextTick()
        h.checked.value = !mobile
        await nextTick()
        h.route.params.sessionId = undefined
        h.isArtifactsMode.value = true
        await nextTick()
        assert.equal(h.sidebarOpen.value, true)
        h.stop()
    }
})

test('returning to mobile Sessions closes selected sessions and opens the Sessions root', async () => {
    for (const sessionId of [undefined, 'selected-session']) {
        const h = sidebarHarness({ mobile: true, open: false, artifacts: true, bookmarkId: 'saved' })
        h.route.params.sessionId = sessionId
        h.route.params.bookmarkId = undefined
        h.isArtifactsMode.value = false
        await nextTick()
        assert.equal(h.sidebarOpen.value, !sessionId)
        h.route.params.sessionId = 'next-session'
        await nextTick()
        assert.equal(h.sidebarOpen.value, false)
        h.route.params.sessionId = undefined
        await nextTick()
        assert.equal(h.sidebarOpen.value, true)
        h.stop()
    }
})

test('shared route memory restores the selected artifact and exact Sessions route', async () => {
    const scope = effectScope()
    const route = reactive({ name: 'project-session', fullPath: '/project/session/files', params: { projectId: 'project', sessionId: 'session' }, query: { tab: 'files' } })
    const isArtifactsMode = computed(() => route.name === 'project-artifacts')
    const lastSessionsLocation = ref(null)
    const lastArtifactsLocation = ref(null)
    const pushed = []
    const context = { watch, route, isArtifactsMode, lastSessionsLocation, lastArtifactsLocation,
        router: { push: location => pushed.push(location) },
        switchToArtifacts: () => assert.fail('remembered artifact route must win'),
        switchToSessions: () => assert.fail('remembered session route must win') }
    const start = script.indexOf('watch(() => route.fullPath, () => {')
    const memory = script.slice(start, script.indexOf('// Track the open bookmark', start))
    scope.run(() => runInNewContext(memory, context))
    const sessions = JSON.parse(JSON.stringify(lastSessionsLocation.value))
    route.name = 'project-artifacts'
    route.params = { projectId: 'project', bookmarkId: 'remembered' }
    route.query = { filter: 'html' }
    route.fullPath = '/project/artifacts/remembered?filter=html'
    await nextTick()
    const artifacts = JSON.parse(JSON.stringify(lastArtifactsLocation.value))
    runInNewContext(`${body('toggleSidebarView')}; toggleSidebarView()`, context)
    assert.deepEqual(JSON.parse(JSON.stringify(pushed.pop())), sessions)
    Object.assign(route, sessions, { fullPath: '/project/session/files' })
    await nextTick()
    runInNewContext(`${body('toggleSidebarView')}; toggleSidebarView()`, context)
    assert.deepEqual(JSON.parse(JSON.stringify(pushed.pop())), artifacts)
    scope.stop()
})


test('empty bookmark store preserves sidebar state on artifact mount and entry', async () => {
    for (const mobile of [false, true]) for (const open of [false, true]) for (const artifacts of [false, true]) {
        const h = sidebarHarness({ mobile, open, artifacts, bookmarks: {} })
        if (artifacts) assert.equal(h.sidebarOpen.value, open, 'empty artifact mount keeps stored state')
        if (!artifacts) {
            h.checked.value = mobile ? open : !open
            await nextTick()
            h.route.params.sessionId = undefined
            h.isArtifactsMode.value = true
            await nextTick()
            assert.equal(h.sidebarOpen.value, open, 'empty mode entry keeps current state')
        }
        h.store.artifactBookmarks = { later: { id: 'later' } }
        await nextTick()
        assert.equal(h.sidebarOpen.value, open, 'later pin creation is not entry')
        assert.equal(h.saved.length, 0)
        h.stop()
    }
})

test('cold artifact mount and entry resolve bookmark presence once loading completes', async () => {
    for (const mobile of [false, true]) for (const artifacts of [false, true]) for (const hasPins of [false, true]) {
        const h = sidebarHarness({ mobile, open: false, artifacts, loaded: false, bookmarks: {} })
        h.route.params.sessionId = undefined
        h.isArtifactsMode.value = true
        await nextTick()
        assert.equal(h.sidebarOpen.value, false, 'unknown pins keep the sidebar closed')
        h.store.artifactBookmarks = hasPins ? { pinned: { id: 'pinned' } } : {}
        h.store.artifactBookmarksLoaded = true
        await nextTick()
        assert.equal(h.sidebarOpen.value, hasPins, 'first loaded snapshot resolves entry')
        h.checkbox.checked = !mobile
        h.toggle()
        h.store.artifactBookmarksLoaded = false
        await nextTick()
        h.store.artifactBookmarks = { refresh: { id: 'refresh' } }
        h.store.artifactBookmarksLoaded = true
        await nextTick()
        assert.equal(h.sidebarOpen.value, false, 'refresh does not repeat entry')
        h.stop()
    }
})

test('pending cold artifact entry cancels on user toggle, selection, exit, or viewport change', async () => {
    for (const mobile of [false, true]) for (const action of ['toggle', 'select', 'exit', 'resize']) {
        const h = sidebarHarness({ mobile, open: action === 'toggle', artifacts: true, loaded: false, bookmarks: {} })
        if (action === 'toggle') {
            h.checkbox.checked = !mobile
            h.toggle()
        } else if (action === 'select') h.route.params.bookmarkId = 'selected'
        else if (action === 'exit') {
            h.isArtifactsMode.value = false
            h.route.params.sessionId = 'session'
        } else h.narrow.value = !mobile
        await nextTick()
        if (action === 'select') {
            h.route.params.bookmarkId = undefined
            await nextTick()
        }
        const stateAfterAction = h.sidebarOpen.value
        const savedAfterAction = h.saved.length
        h.store.artifactBookmarks = { pinned: { id: 'pinned' } }
        h.store.artifactBookmarksLoaded = true
        await nextTick()
        assert.equal(h.sidebarOpen.value, stateAfterAction, action)
        assert.equal(h.saved.length, savedAfterAction, 'loading must not apply another toggle')
        h.stop()
    }
})

test('selected cold artifact mount preserves stored state after load and deselection', async () => {
    for (const mobile of [false, true]) for (const open of [false, true]) {
        const h = sidebarHarness({ mobile, open, artifacts: true, bookmarkId: 'selected', loaded: false, bookmarks: {} })
        assert.equal(h.sidebarOpen.value, open)
        h.store.artifactBookmarks = { pinned: { id: 'pinned' } }
        h.store.artifactBookmarksLoaded = true
        await nextTick()
        h.route.params.bookmarkId = undefined
        await nextTick()
        assert.equal(h.sidebarOpen.value, open)
        assert.equal(h.saved.length, 0)
        h.stop()
    }
})


test('desktop drag collapse cancels pending cold artifact entry', async () => {
    const h = sidebarHarness({ mobile: false, open: true, artifacts: true, loaded: false, bookmarks: {} })
    h.collapseByDrag()
    assert.equal(h.sidebarOpen.value, false)
    h.store.artifactBookmarks = { pinned: { id: 'pinned' } }
    h.store.artifactBookmarksLoaded = true
    await nextTick()
    assert.equal(h.sidebarOpen.value, false, 'loading preserves explicit drag collapse')
    h.stop()
})
