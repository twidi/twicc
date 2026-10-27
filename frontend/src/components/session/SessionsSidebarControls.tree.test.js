import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const controls = read('./SessionsSidebarControls.vue')
const view = read('../../views/ProjectView.vue')

test('controls declare the showAsTree prop and the checkbox item after compact view', () => {
    assert.match(controls, /showAsTree: \{ type: Boolean, default: false \}/)
    assert.match(
        controls,
        /value="compact-view"[\s\S]*?<\/wa-dropdown-item>\s*<wa-dropdown-item\s+type="checkbox"\s+value="show-as-tree"\s+:checked="showAsTree"\s*>\s*Show as tree\s*<\/wa-dropdown-item>/,
    )
})

test('ProjectView derives showAsTree from the setting and handles the menu value', () => {
    assert.match(view, /const showAsTree = computed\(\(\) => settingsStore\.isShowSessionsAsTree\)/)
    assert.match(
        view,
        /value === 'show-as-tree'\) \{\s*settingsStore\.setShowSessionsAsTree\(item\.checked\)/,
    )
})

test('ProjectView passes showAsTree to the controls and to every SessionList', () => {
    const controlsTag = view.match(/<SessionsSidebarControls[\s\S]*?\/>/)[0]
    assert.match(controlsTag, /:show-as-tree="showAsTree"/)
    const lists = view.match(/<SessionList\b[\s\S]*?\/>/g)
    assert.ok(lists.length >= 1)
    for (const tag of lists) assert.match(tag, /:show-as-tree="showAsTree"/)
})
