import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const controls = read('./SessionsSidebarControls.vue')
const view = read('../../views/ProjectView.vue')
const list = read('./list/SessionList.vue')
const item = read('./list/SessionListItem.vue')

test('controls declare the prop and the checkbox item after the tree item', () => {
    assert.match(controls, /showProviderIcons: \{ type: Boolean, default: false \}/)
    assert.match(
        controls,
        /value="show-as-tree"[\s\S]*?<\/wa-dropdown-item>\s*<wa-dropdown-item\s+type="checkbox"\s+value="show-provider-icons"\s+:checked="showProviderIcons"\s*>\s*\{\{ \$t\('session\.list\.showProviderIcons'\) \}\}\s*<\/wa-dropdown-item>/,
    )
})

test('ProjectView derives the setting and handles the menu value', () => {
    assert.match(view, /const showProviderIcons = computed\(\(\) => settingsStore\.isShowProviderIcons\)/)
    assert.match(
        view,
        /value === 'show-provider-icons'\) \{\s*settingsStore\.setShowProviderIcons\(item\.checked\)/,
    )
})

test('ProjectView passes the setting to the controls and to every SessionList', () => {
    const controlsTag = view.match(/<SessionsSidebarControls[\s\S]*?\/>/)[0]
    assert.match(controlsTag, /:show-provider-icons="showProviderIcons"/)
    const lists = view.match(/<SessionList\b[\s\S]*?\/>/g)
    assert.ok(lists.length >= 1)
    for (const tag of lists) assert.match(tag, /:show-provider-icons="showProviderIcons"/)
})

test('SessionList defaults to true and forwards to SessionListItem', () => {
    assert.match(list, /showProviderIcons: \{\s*type: Boolean,\s*default: true\s*\}/)
    const tag = list.match(/<SessionListItem\b[\s\S]*?\/>/)[0]
    assert.match(tag, /:show-provider-icon="showProviderIcons"/)
})

test('SessionListItem gates the provider icon with a prop defaulting to true', () => {
    assert.match(item, /showProviderIcon: \{\s*type: Boolean,\s*default: true\s*\}/)
    assert.match(item, /<ProviderIcon v-if="showProviderIcon && providerIcon"/)
})
