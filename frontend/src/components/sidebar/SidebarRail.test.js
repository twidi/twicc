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
    assert.equal((source.match(/<button\b/g) ?? []).length, 2)
    assert.equal((source.match(/:aria-pressed="item.active"/g) ?? []).length, 2)
    assert.equal((source.match(/:aria-label="item.label"/g) ?? []).length, 2)
    assert.equal((source.match(/:disabled="item.disabled"/g) ?? []).length, 2)
    assert.equal((source.match(/<\/button>\s*<AppTooltip/g) ?? []).length, 2)
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
    assert.match(source, /\.rail-spacer \{\s*flex: 1;/)
    assert.match(source, /\.rail-settings \{[^}]*display: flex;[^}]*flex: none;/)
    assert.match(source, /:deep\(wa-tooltip\) \{\s*position: absolute;/)
    assert.match(source, /@container rail \(height < 22rem\)[\s\S]*overflow-x: hidden;[\s\S]*overflow-y: auto;[\s\S]*scrollbar-width: none;/)
    assert.match(source, /\.panel-card::-webkit-scrollbar \{\s*display: none;/)
    assert.doesNotMatch(source.slice(source.indexOf('<style'), source.indexOf('@container')), /overflow:/)
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
    const [top, spacer, bottom] = children(card)
    assert.deepEqual([attrs(top)['v-for'], attrs(bottom)['v-for']], ['item in top', 'item in bottom'])
    for (const group of [top, bottom]) {
        assert.equal(group.tag, 'template')
        assert.equal(attrs(group)[':key'], 'item.id')
    }
    assert.equal(attrs(spacer).class, 'rail-spacer')
    const [settingsWrapper, otherBottom] = children(bottom)
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
