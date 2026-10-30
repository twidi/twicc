import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'

const read = () => readFileSync(new URL('./SidebarRail.vue', import.meta.url), 'utf8')

test('rail has keyed groups, native accessible buttons, and a stable Settings fragment', () => {
    const source = read()
    assert.match(source, /<nav aria-label="Main navigation" class="sidebar-rail">\s*<div class="panel-card">/)
    assert.equal((source.match(/<template v-for="item in (top|bottom)" :key="item.id">/g) ?? []).length, 2)
    assert.equal((source.match(/class="rail-spacer"/g) ?? []).length, 1)
    assert.ok(source.indexOf('item in top') < source.indexOf('class="rail-spacer"'))
    assert.ok(source.indexOf('class="rail-spacer"') < source.indexOf('item in bottom'))
    assert.equal((source.match(/<button\b/g) ?? []).length, 7)
    assert.equal((source.match(/:aria-pressed="item.active"/g) ?? []).length, 2)
    assert.equal((source.match(/:aria-label="item.label"/g) ?? []).length, 3)
    assert.equal((source.match(/:disabled="item.disabled"/g) ?? []).length, 2)
    assert.equal((source.match(/<\/button>\s*<AppTooltip/g) ?? []).length, 5)
    assert.doesNotMatch(source, /aria-expanded|glass-/)
    for (const attribute of ['trigger-appearance="plain"', 'trigger-icon-only', 'placement="right-end"',
        'tooltip-placement="right"', ':trigger-label="item.label"', ':position-anchor="settingsAnchor"']) {
        assert.ok(source.includes(attribute), attribute)
    }
    assert.match(source, /v-if="item.id === 'settings'" class="rail-settings"/)
    assert.match(source, /<PeerInboxBadge v-if="item.id === 'inbox'" :count="item.badge"/)
    assert.match(source, /settingsAnchor: \{ type: Object, default: null \}/)
    assert.match(source, /isMac: settingsStore.isMac/)
    assert.match(source, /resolveRailItems\(/)
    assert.match(source, /`sidebar-rail-\$\{item.id\}`/)
    assert.match(source, /emit\('select-mode', item.id\)/)
    assert.match(source, /emit\('toggle-sidebar'\)/)
})

test('rail slot, flexible spacer, and short-height scrolling preserve button sizes', () => {
    const source = read()
    assert.match(source, /container-type: size;/)
    assert.match(source, /container-name: rail;/)
    assert.match(source, /width: var\(--rail-width\);/)
    assert.match(source, /z-index: 3;/)
    assert.match(source, /@media \(width < 640px\)[\s\S]*z-index: 101;[\s\S]*background: var\(--canvas-background\);[\s\S]*background-attachment: fixed;/)
    assert.match(source, /\.panel-card \{[^}]*flex-direction: column;[^}]*gap: var\(--rail-gap\);[^}]*padding: var\(--rail-card-padding\);[^}]*flex: 1;[^}]*margin-block: var\(--panel-gap\);[^}]*margin-inline-start: var\(--panel-gap\);/)
    assert.match(source, /\.rail-spacer \{\s*flex: 1;\s*min-height: 0;\s*overflow-x: hidden;\s*overflow-y: auto;/)
    assert.match(source, /\.rail-settings \{[^}]*display: flex;[^}]*flex: none;/)
    assert.match(source, /:deep\(wa-tooltip\) \{\s*position: absolute;/)
    assert.match(source, /@container rail \(height < 27\.5rem\)[\s\S]*overflow-x: hidden;[\s\S]*overflow-y: auto;[\s\S]*scrollbar-width: none;/)
    assert.match(source, /\.panel-card::-webkit-scrollbar \{\s*display: none;/)
    assert.match(source, /\.rail-divider \{\s*flex: none;/)
})

test('rail SFC compiles its script, template, and scoped style', async () => {
    const source = read()
    const { parse, compileScript, compileTemplate, compileStyle } = await import('@vue/compiler-sfc')
    const { descriptor, errors } = parse(source, { filename: 'SidebarRail.vue' })
    assert.deepEqual(errors, [])
    const children = node => node.children.filter(child => child.type === 1)
    const attrs = node => Object.fromEntries(node.props.map(prop => prop.type === 6
        ? [prop.name, prop.value?.content ?? ''] : [prop.rawName, prop.exp?.content ?? '']))
    const [nav] = children(descriptor.template.ast)
    assert.equal(nav.tag, 'nav')
    assert.equal(attrs(nav)['aria-label'], 'Main navigation')
    const [card] = children(nav)
    assert.equal(attrs(card).class, 'panel-card')
    const [top, firstDivider, spacer, secondDivider, bottom] = children(card)
    for (const divider of [firstDivider, secondDivider]) {
        assert.equal(divider.tag, 'wa-divider')
        assert.equal(attrs(divider)['v-if'], 'hasCentralContent')
    }
    assert.deepEqual([attrs(top)['v-for'], attrs(bottom)['v-for']], ['item in top', 'item in bottom'])
    for (const group of [top, bottom]) {
        assert.equal(group.tag, 'template')
        assert.equal(attrs(group)[':key'], 'item.id')
    }
    assert.equal(attrs(spacer).class, 'rail-spacer')
    const [settingsWrapper, newSession, otherBottom] = children(bottom)
    assert.equal(attrs(newSession)['v-else-if'], "item.id === 'new-session'")
    assert.equal(children(newSession)[0].tag, 'NewSessionProjectPicker')
    assert.equal(attrs(settingsWrapper)['v-if'], "item.id === 'settings'")
    assert.equal(children(settingsWrapper)[0].tag, 'SettingsPopover')
    for (const group of [top, otherBottom]) {
        const [button, tooltip] = children(group)
        assert.equal(button.tag, 'button')
        assert.equal(attrs(button)[':aria-pressed'], 'item.active')
        assert.equal(attrs(button)[':aria-label'], 'item.label')
        assert.equal(attrs(button)[':disabled'], 'item.disabled')
        assert.equal(tooltip.tag, 'AppTooltip')
        assert.equal(attrs(tooltip)[':for'], attrs(button)[':id'])
    }
    const [sessionEntry] = children(spacer)
    const [sessionButton, sessionTooltip] = children(sessionEntry)
    assert.equal(attrs(sessionButton)[':aria-pressed'], 'row.session.id === currentSessionId')
    const [preview] = children(sessionTooltip)
    const [sessionRow] = children(preview)
    assert.equal(sessionRow.tag, 'SessionListItem')
    assert.equal(attrs(sessionRow)[':active'], 'row.session.id === currentSessionId')
    assert.equal(attrs(sessionRow)[':highlight-active'], 'false')
    const id = 'data-v-rail'
    const script = compileScript(descriptor, { id })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'SidebarRail.vue', id,
        compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
    for (const style of descriptor.styles) {
        assert.deepEqual(compileStyle({ source: style.content, filename: 'SidebarRail.vue', id, scoped: style.scoped }).errors, [])
    }
})

test('rail accepts all navigation modes and defaults an omitted sidebar state to closed', async () => {
    const { parse, compileScript } = await import('@vue/compiler-sfc')
    const { descriptor } = parse(read(), { filename: 'SidebarRail.vue' })
    const script = compileScript(descriptor, { id: 'data-v-rail' }).content
        .replace(/^import .*$/gm, '')
        .replace('export default', 'const component =')
    const props = runInNewContext(`${script}; component.props`)
    for (const mode of ['home', 'sessions', 'artifacts']) {
        assert.equal(props.mode.validator(mode), true, mode)
    }
    assert.equal(props.mode.validator('unknown'), false)
    assert.equal(props.sidebarOpen.required, undefined)
    assert.equal(props.sidebarOpen.default, false)
})


test('global active entries reuse the full isolated row and canonical navigation', () => {
    const source = read()
    assert.match(source, /useRailActiveSessions\(store, \(\) => currentSessionId.value, \{/)
    assert.match(source, /props.mode === 'sessions' \? route.params.sessionId \|\| null : null/)
    assert.match(source, /router.push\(sessionRouteLocation\(session, route\)\)/)
    assert.match(source, /v-for="row in railRows" :key="row.session.id"/)
    assert.equal((source.match(/v-if="hasCentralContent"/g) ?? []).length, 2)
    assert.match(source, /force interactive hoist placement="right"/)
    assert.match(source, /id-prefix="rail-preview-" :show-menu="false" :selection-enabled="false"/)
    assert.match(source, /:compact-view="false" :show-project-name="true" :show-title-tooltip="false"/)
    assert.match(source, /<ProjectMark[\s\S]*v-if="row.pendingRequest && !row.hasUnread"[\s\S]*<ProcessIndicator[\s\S]*:unread="row.hasUnread"/)
    assert.match(source, /--max-width: min\(24rem, calc\(100vw - var\(--rail-width\) - 1.5rem\)\)/)
    assert.match(source, /container-name: session-list/)
    assert.doesNotMatch(source, /loadProjectSessions|selectedSessionId/)
})


test('preview width reserves the rail and popup space on narrow viewports', () => {
    const source = read()
    assert.match(source, /width: min\(22rem, calc\(100vw - var\(--rail-width\) - 3rem\)\)/)
    // Browser regression: 360px viewport, 352px client width, popup begins at 56px.
    // The row width plus the tooltip's 16px body padding must remain inside that edge.
    const previewWidth = Math.min(22 * 16, 360 - 54 - 3 * 16)
    assert.ok(56 + previewWidth + 16 <= 352)
    assert.equal(Math.min(22 * 16, 1280 - 54 - 3 * 16), 22 * 16)
})

test('tree mode: collapsed state is the read-only explicit false and a node row shows the cumulative indicator', () => {
    const source = read()
    assert.match(source, /treeEnabled: \(\) => isTreeMode.value/)
    assert.match(source, /isCollapsed: id => treeExpansion.state.explicit\[id\] === false/)
    assert.match(source, /useSessionTreeAncestors\(treeCandidates, isTreeMode\)/)
    assert.doesNotMatch(source, /treeExpansion\.(toggle|reveal|reconcile)|setExpanded/)
    assert.match(source, /<ProcessActivityIndicator\s+v-if="row.cumulative" :summary="row.cumulative" size="small" :tooltip="false"\s*\/>\s*<template v-else>[\s\S]*v-if="row.pendingRequest && !row.hasUnread"[\s\S]*row.processState.state[\s\S]*:unread="row.hasUnread"[\s\S]*<\/template>/)
    assert.match(source, /:aria-pressed="row.session.id === currentSessionId"/)
})

test('tree mode: a short segment chains the project marks of one tree, drawn by the non-last rows', () => {
    const source = read()
    assert.match(source, /v-for="row in railRows" :key="row.session.id" class="rail-session"\s*:class="row.groupClass"/)
    assert.match(source, /all\[index - 1\]\?\.groupId === group/)
    assert.match(source, /'rail-session--grouped'/)
    assert.match(source, /'rail-session--group-middle' : 'rail-session--group-last'\) : 'rail-session--group-first'/)
    assert.doesNotMatch(source, /\.rail-spacer \{[^}]*margin-inline/)
    assert.match(source, /\.rail-session--group-first::after,\s*\.rail-session--group-middle::after \{[^}]*z-index: 1;[^}]*width: 1px;[^}]*top: var\(--rail-segment-start\);[^}]*height: calc\(var\(--rail-segment-end\) - var\(--rail-segment-start\)\);[^}]*var\(--sidebar-divider-color, var\(--wa-color-surface-border\)\);[^}]*pointer-events: none;/)
    assert.doesNotMatch(source, /group-last::(before|after)/)
    assert.match(source, /--project-mark-icon-size: var\(--rail-mark-size\);/)
})

test('tree mode: .rail-session creates no stacking context that could hide the segment', () => {
    const source = read()
    const rule = source.match(/\.rail-session--grouped \{([^}]*)\}/)[1]
    assert.doesNotMatch(rule, /z-index|isolation|overflow|transform|opacity|filter/)
    assert.doesNotMatch(source, /\.rail-session \{[^}]*(z-index|isolation|overflow|transform|opacity|filter)/)
})

test('tree mode: a repeated project shows a dot in the mark box and the segments reach its center', () => {
    const source = read()
    assert.match(source, /<span v-if="row.showMarkDot" class="rail-mark-dot" aria-hidden="true"><\/span>\s*<ProjectMark\s+v-else/)
    assert.match(source, /'rail-session--dot': row.showMarkDot, 'rail-session--next-dot': linkedAfter && all\[index \+ 1\].showMarkDot/)
    assert.match(source, /\.rail-session--dot \{\s*--rail-segment-start: calc\(var\(--rail-button-size\) \/ 2\);/)
    assert.match(source, /\.rail-session--next-dot \{\s*--rail-segment-end: calc\(var\(--rail-button-size\) \+ var\(--rail-gap\) \+ var\(--rail-button-size\) \/ 2\);/)
    assert.match(source, /--rail-segment-start: calc\(var\(--rail-button-size\) - var\(--rail-mark-free\) \+ var\(--rail-mark-clearance\)\);/)
    assert.match(source, /--rail-segment-end: calc\(var\(--rail-button-size\) \+ var\(--rail-gap\) \+ var\(--rail-mark-free\) - var\(--rail-mark-clearance\)\);/)
    assert.match(source, /\.rail-mark-dot \{[^}]*z-index: 2;[^}]*width: var\(--rail-mark-size\);[^}]*height: var\(--rail-mark-size\);/)
    assert.match(source, /\.rail-mark-dot::before \{[^}]*border-radius: 50%;[^}]*var\(--sidebar-divider-color, var\(--wa-color-surface-border\)\)/)
})

test('tree mode: a collapsed node tooltip lists its active descendants inside the preview wrapper', () => {
    const source = read()
    assert.match(source, /const RAIL_PREVIEW_MAX_DESCENDANTS = 5/)
    const preview = source.slice(source.indexOf('class="rail-session-preview"'), source.indexOf('</AppTooltip>', source.indexOf('class="rail-session-preview"')))
    assert.match(preview, /<template v-if="row.activeDescendants\?\.length">\s*<wa-divider class="rail-preview-divider" \/>/)
    assert.match(preview, /row.activeDescendants.slice\(0, RAIL_PREVIEW_MAX_DESCENDANTS\)/)
    assert.match(preview, /type="button" class="rail-preview-child" @click="openSession\(child.session\)"/)
    assert.match(preview, /\+\{\{ row.activeDescendants.length - RAIL_PREVIEW_MAX_DESCENDANTS \}\} more/)
    assert.doesNotMatch(preview, /expand|toggle/)
})

test('rail rows and preview children render the unread eye through ProcessIndicator', () => {
    const source = read()
    assert.doesNotMatch(source, /<wa-icon v-if="row.hasUnread"/)
    assert.match(source, /:unread="row.hasUnread"\s*:style="row.hasUnread \? \{ fontSize: '0.75rem' \} : undefined"/)
    assert.match(source, /v-if="child.pendingRequest && !child.hasUnread" name="hand"/)
    assert.match(source, /v-else-if="child.processState"[\s\S]*:unread="child.hasUnread"\s*:style="child.hasUnread \? \{ fontSize: '0.75rem' \} : undefined"/)
    // The bare eye remains only for a child without processState.
    assert.match(source, /<wa-icon v-else-if="child.hasUnread" name="eye" class="rail-unread" \/>/)
    assert.equal((source.match(/name="eye"/g) ?? []).length, 1)
})

test('a playing speech shows a speaker whose waves light up in turn, with no movement', () => {
    const source = read()
    assert.match(source, /<svg\s+v-if="item.id === 'speech' && tts.status === 'playing'"\s+class="rail-speech-waves"[^>]*aria-hidden="true"/)
    assert.equal((source.match(/class="rail-speech-wave"/g) ?? []).length, 3)
    assert.match(source, /<wa-icon v-else :name="item.icon" \/>/)
    const keyframes = source.match(/@keyframes rail-speech-wave \{[\s\S]*?\n\}/)?.[0] ?? ''
    assert.match(keyframes, /opacity/)
    // An opacity cascade only: it may keep running under reduced motion.
    assert.doesNotMatch(keyframes, /transform|scale|translate/)
})
