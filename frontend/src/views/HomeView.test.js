import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { computed, ref } from 'vue'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'

const source = readFileSync(new URL('./HomeView.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'HomeView.vue' })
const script = compileScript(descriptor, { id: 'data-v-home' })
const elements = node => node.children?.filter(child => child.type === 1) ?? []
const attrs = node => Object.fromEntries(node.props.map(prop => prop.type === 6
    ? [prop.name, prop.value?.content ?? ''] : [prop.rawName, prop.exp?.content ?? '']))
const [root] = elements(descriptor.template.ast)
const rail = elements(root).find(node => node.tag === 'SidebarRail')

// Compile the actual setup. Replace browser/store dependencies at their boundaries.
function setup() {
    const routes = []
    const events = []
    let paletteOpens = 0
    const bindings = Object.fromEntries(Object.keys(script.bindings).map(name => [name, {}]))
    Object.assign(bindings, {
        computed, ref,
        onMounted() {}, onUnmounted() {}, onBeforeUnmount() {},
        provideHomeCardCascade() {}, useStartupPolling() {},
        useRouter: () => ({ push: route => routes.push(JSON.parse(JSON.stringify(route))) }),
        useDataStore: () => ({ getProjects: [], weeklyActivity: {} }),
        useWorkspacesStore: () => ({}),
        usePeersStore: () => ({ inboxCount: 3 }),
        usePeerSystemConfigured: () => ref(true),
        useCommandRegistry: () => ({ openPalette: () => paletteOpens++ }),
        window: { dispatchEvent: event => events.push(event.type) },
        CustomEvent: class { constructor(type) { this.type = type } },
    })
    const executable = script.content.replace(/^import .*$/gm, '').replace('export default', 'const component =')
    const state = runInNewContext(`${executable}; component.setup({}, { expose() {} })`, bindings)
    return { state, routes, events, paletteOpens: () => paletteOpens }
}

test('Home always mounts the existing rail in Home mode without a sidebar', () => {
    assert.ok(rail, 'Home rail exists')
    const props = attrs(rail)
    assert.equal(props.mode, 'home')
    assert.equal(props['v-if'], undefined)
    assert.equal(props['v-show'], undefined)
    assert.equal(props[':sidebar-open'], undefined)
    assert.doesNotMatch(source, /isSidebarRailVisibleWhenClosed|home-settings|SettingsPopover|PeerInboxButton/)
    assert.equal(props[':peer-configured'], 'peerSystemConfigured')
    assert.equal(props[':inbox-count'], 'peersStore.inboxCount')
    assert.equal(props['@home'], undefined, 'Home stays on Home')
    assert.equal(props['@toggle-sidebar'], undefined)
})

test('Home rail modes navigate to fresh global session and artifact scopes', () => {
    assert.ok(rail)
    const { state, routes } = setup()
    const selectMode = state[attrs(rail)['@select-mode']]
    selectMode('sessions')
    selectMode('artifacts')
    assert.deepEqual(routes, [{ name: 'projects-all' }, { name: 'projects-artifacts' }])
})

test('Home rail opens the command palette and the existing peer inbox event', () => {
    assert.ok(rail)
    const runtime = setup()
    runtime.state[attrs(rail)['@palette']]()
    runtime.state[attrs(rail)['@inbox']]()
    assert.equal(runtime.paletteOpens(), 1)
    assert.deepEqual(runtime.events, ['twicc:open-peer-inbox'])
})

test('Home compiles and reserves space for its fixed viewport rail', () => {
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'HomeView.vue',
        id: 'data-v-home', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
    for (const style of descriptor.styles) {
        assert.deepEqual(compileStyle({ source: style.content, filename: 'HomeView.vue',
            id: 'data-v-home', scoped: style.scoped }).errors, [])
    }
    assert.equal(attrs(root).class, 'home-page')
    const css = descriptor.styles.map(style => style.content).join('\n')
    assert.match(css, /\.home-page\s*\{[^}]*padding-inline-start:\s*var\(--rail-width\)/)
    assert.match(css, /\.home-rail\s*\{[^}]*position:\s*fixed;[^}]*inset-block:\s*0;[^}]*inset-inline-start:\s*0;[^}]*height:\s*100dvh;/)
    assert.match(css, /\.home-view\s*\{[^}]*max-width:\s*900px;[^}]*margin:\s*0 auto;/)
})
