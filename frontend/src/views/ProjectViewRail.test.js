import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, existsSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

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
    assert.match(script, /watch\(initialSidebarChecked, syncSidebarState, \{ flush: 'post' \}\)/)
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
    assert.match(script, /checkbox.checked = false\s*}\s*syncSidebarState\(\)/)
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
