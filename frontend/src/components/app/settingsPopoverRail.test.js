import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

const sfc = readFileSync(new URL('./SettingsPopover.vue', import.meta.url), 'utf8')
const script = sfc.match(/<script setup>([\s\S]*?)<\/script>/)[1]
const template = sfc.match(/<template>([\s\S]*)<\/template>/)[1]
const style = sfc.match(/<style scoped>([\s\S]*?)<\/style>/)[1]
function fn(name) {
    const match = script.match(new RegExp(`(?:async )?function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n\\}`))
    assert.ok(match, `${name} exists`)
    return match[0]
}

test('trigger defaults, accessible label, tooltip, and local switch', () => {
    for (const [name, type, value] of [
        ['triggerAppearance', 'String', "'outlined'"], ['triggerLabel', 'String', 'null'],
        ['triggerIconOnly', 'Boolean', 'false'], ['placement', 'String', "'top'"],
        ['tooltipPlacement', 'String', "'top'"], ['positionAnchor', 'Object', 'null'],
    ]) assert.ok(script.includes(`${name}: { type: ${type}, default: ${value} }`), name)
    assert.match(template, /id="settings-trigger"/)
    assert.match(template, /'settings-trigger--icon-only': props.triggerIconOnly/)
    assert.match(template, /class="settings-trigger-label"[^>]*>[\s\S]*?props.triggerLabel \?\? 'Settings'/)
    assert.match(template, /:placement="props.tooltipPlacement"[^>]*>\{\{ props.triggerLabel \?\? 'Toggle settings' \}\}/)
    assert.match(template, /Keep the icon bar visible when the sidebar is closed/)
    assert.match(script, /computed\(\(\) => store.isSidebarRailVisibleWhenClosed\)/)
    assert.match(template, /:checked="sidebarRailVisibleWhenClosed"[\s\S]*?@change="onSidebarRailVisibleWhenClosedChange"/)
    assert.match(fn('onSidebarRailVisibleWhenClosedChange'), /store.setSidebarRailVisibleWhenClosed\(event.target.checked\)/)
    assert.doesNotMatch(sfc, /settings-trigger-badge|@container sidebar/)
})

test('one Teleport contains the popover and all four dialogs', () => {
    assert.equal((template.match(/<Teleport /g) ?? []).length, 1)
    const body = template.match(/<Teleport to="body">([\s\S]*?)<\/Teleport>/)[1]
    for (const tag of ['wa-popover', 'ChangelogDialog', 'LayoutManagerDialog', 'ShareManagerDialog', 'TelemetryPayloadDialog']) {
        assert.ok(body.includes(`<${tag} `), tag)
    }
    assert.ok(template.indexOf('<AppTooltip for="settings-trigger"') < template.indexOf('<Teleport'))
    assert.match(body, /for="settings-trigger" :placement="props.placement"/)
    assert.match(body, /@wa-after-hide.self="onPopoverAfterHide"/)
})

test('opening placement stays synchronous and preserves reset/seeding', () => {
    const show = fn('onPopoverShow')
    assert.ok(show.startsWith('function onPopoverShow()'))
    assert.doesNotMatch(show, /\breturn\b|\bawait\b/)
    assert.match(show, /popoverOpen = true/)
    assert.match(show, /props.placement !== 'top' && popover\?\.anchor/)
    assert.match(show, /popover\.placement = resolveSettingsPlacement\(/)
    assert.match(show, /anchorRight: popover.anchor.getBoundingClientRect\(\).right/)
    assert.match(show, /innerWidth: window.innerWidth/)
    for (const reset of ['benchmarkTaskStore.resetTransientControls()', 'mobileShowContent.value = false', '    afterSwap = null', "seedOriginField('publicBaseUrl')", "seedOriginField('shareBaseUrl')"]) assert.ok(show.includes(reset), reset)
    assert.match(script, /onMounted\(applyAnchor\)/)
    assert.match(script, /watch\(\(\) => props.positionAnchor, applyAnchor\)/)
    assert.match(fn('onPopoverAfterHide'), /resetTransientControls\(\)[\s\S]*popoverOpen = false[\s\S]*applyAnchor\(\)/)
})

test('opening Settings bounds the whole popup to the available anchor space', () => {
    const popup = {}
    const context = {
        popoverRef: { value: { popup } }, popoverOpen: false,
        props: { placement: 'top' }, SETTINGS_POPOVER_MARGIN: 16,
        benchmarkTaskStore: { resetTransientControls() {} },
        mobileShowContent: { value: true }, afterSwap: () => {},
        worktreeDirInput: { value: '' }, worktreeDirectoryTemplate: { value: '' },
        seedOriginField() {}, activeSection: { value: 'general' },
    }
    runInNewContext(`${fn('onPopoverShow')}; onPopoverShow()`, context)
    assert.equal(popup.autoSize, 'vertical')
    assert.equal(popup.autoSizePadding, 16)
    assert.equal(popup.shiftPadding, 16)
})

test('whole Settings body shrinks the scrolling panels without losing footer access', () => {
    const body = style.match(/\.settings-popover::part\(body\) \{([^}]+)\}/)[1]
    assert.match(body, /box-sizing: border-box/)
    assert.match(body, /max-height: var\(--auto-size-available-height,/)
    assert.match(body, /overflow: auto/)
    const layout = style.match(/\.settings-layout \{([^}]+)\}/)[1]
    assert.match(layout, /min-height: min\(8rem, var\(--auto-size-available-height,/)
    assert.match(layout, /height: min\(calc\(90dvh - 8rem\), 50rem\)/)
    assert.match(style, /\.settings-popover > :not\(\.settings-layout\) \{\s*flex-shrink: 0/)
    for (const panel of ['settings-nav', 'settings-detail']) {
        assert.match(style.match(new RegExp(`\\.${panel} \\{([^}]+)\\}`))[1], /overflow-y: auto/)
    }
})

test('anchor waits, reads latest props, defers while open, restores, and guards unmount', async () => {
    let ready
    const oldAnchor = {}
    const latestAnchor = {}
    const trigger = {}
    const writes = []
    const popover = { updateComplete: new Promise(resolve => { ready = resolve }) }
    let anchor = oldAnchor
    Object.defineProperty(popover, 'anchor', { get: () => anchor, set: value => { anchor = value; writes.push(value) } })
    const context = { popoverRef: { value: popover }, props: { positionAnchor: oldAnchor }, popoverOpen: false, document: { getElementById: () => trigger } }
    runInNewContext(`${fn('applyAnchor')}; this.applyAnchor = applyAnchor`, context)
    const pending = context.applyAnchor()
    assert.deepEqual(writes, [])
    context.props.positionAnchor = latestAnchor
    ready()
    await pending
    assert.equal(anchor, latestAnchor)
    context.popoverOpen = true
    context.props.positionAnchor = null
    await context.applyAnchor()
    assert.equal(anchor, latestAnchor)
    context.popoverOpen = false
    await context.applyAnchor()
    assert.equal(anchor, trigger)
    await context.applyAnchor()
    assert.equal(writes.length, 2)
    context.document.getElementById = () => null
    await context.applyAnchor()
    assert.equal(writes.length, 2)
    popover.updateComplete = new Promise(resolve => { ready = resolve })
    context.props.positionAnchor = latestAnchor
    const unmounting = context.applyAnchor()
    context.popoverRef.value = null
    ready()
    await unmounting
    assert.equal(writes.length, 2)
})

test('icon-only resets, hidden label, and shared sizing', () => {
    const base = style.match(/#settings-trigger.settings-trigger--icon-only::part\(base\) \{([^}]+)\}/)[1]
    for (const declaration of ['width: var(--rail-button-size)', 'height: var(--rail-button-size)', 'padding: 0', 'border: 0', 'line-height: 1', 'font-size: var(--rail-icon-size)', 'color: var(--rail-button-color)', 'background-color: var(--rail-button-bg, transparent)', 'background-image: none', 'scale: none', 'transition: none']) assert.ok(base.includes(declaration), declaration)
    assert.match(style, /@media \(hover: hover\) \{\s*#settings-trigger.settings-trigger--icon-only:hover::part\(base\)/)
    assert.match(style, /#settings-trigger.settings-trigger--icon-only:active::part\(base\)/)
    const hidden = style.match(/\.settings-trigger--icon-only > \.settings-trigger-label \{([^}]+)\}/)[1]
    for (const declaration of ['position: absolute', 'inline-size: 1px', 'block-size: 1px', 'overflow: hidden', 'clip-path: inset(50%)', 'white-space: nowrap']) assert.ok(hidden.includes(declaration), declaration)
    assert.match(style, /--max-width: v-bind\(popoverWidth\)/)
    assert.match(style, /width: min\(v-bind\(popoverWidth\), v-bind\(popoverMaxWidth\)\)/)
    assert.match(style, /transition: rotate 600ms var\(--motion-ease-out\)/)
})

test('template and scoped styles compile, including CSS v-bind', async t => {
    let compiler
    try { compiler = await import('@vue/compiler-sfc') }
    catch (error) {
        if (error.code !== 'ERR_MODULE_NOT_FOUND') throw error
        t.skip('@vue/compiler-sfc is unavailable; no dependency installation is authorized')
        return
    }
    const { descriptor, errors } = compiler.parse(sfc, { filename: 'SettingsPopover.vue' })
    assert.deepEqual(errors, [])
    const id = 'data-v-rail-settings'
    assert.deepEqual(compiler.compileTemplate({ source: descriptor.template.content, filename: 'SettingsPopover.vue', id }).errors, [])
    for (const block of descriptor.styles) assert.deepEqual(compiler.compileStyle({ source: block.content, filename: 'SettingsPopover.vue', id, scoped: block.scoped }).errors, [])
})
