import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()
const sfc = read('components/session/detail/GroupToggle.vue')
const template = sfc.slice(sfc.indexOf('<template>'), sfc.indexOf('<style'))
const style = norm(sfc.slice(sfc.indexOf('<style')))

// Visual refresh retouches: in the simplified mode the hidden elements of a group are revealed by a
// hairline with a count pill, drawn like the day separators, instead of a faint switch. Only the control
// changes: the items of the group, expanded or not, are untouched.
test('1. a real button with the expanded state, a rule on each side of the pill, no switch any more', () => {
    assert.ok(template.includes('<button type="button" class="group-toggle" :aria-expanded="expanded" @click="handleClick">'))
    assert.equal(template.match(/<span class="group-toggle-line"><\/span>/g).length, 2)
    assert.ok(template.includes('<span class="group-toggle-pill">'))
    assert.ok(!sfc.includes('wa-switch'))
})

test('2. the pill reads "N tool(s)" with no icon before it, keeps the code-comments indicator; the chevron shows the state', () => {
    assert.ok(template.includes("{{ itemCount }} tool{{ itemCount !== 1 ? 's' : '' }}"))
    assert.ok(!template.includes('ellipsis'))
    assert.ok(template.includes('<CodeCommentsIndicator :count="commentsCount" :show-tooltip="false" class="toggle-comments-indicator" />'))
    assert.ok(template.includes('<wa-icon name="chevron-down" class="group-toggle-chevron"></wa-icon>'))
    assert.ok(style.includes('.group-toggle[aria-expanded="true"] .group-toggle-chevron { rotate: -180deg; }'))
})

test('3. the rules are the day separator\'s: 1px surface border, a flex row with a medium gap, the same spacing as the old switch', () => {
    assert.ok(style.includes('.group-toggle-line { flex: 1; height: 0; border-top: var(--wa-border-width-s) solid var(--wa-color-surface-border); }'))
    const root = style.match(/\.group-toggle \{([^}]*)\}/)[1]
    for (const decl of ['display: flex;', 'align-items: center;', 'gap: var(--wa-space-m);', 'width: 100%;',
        '--spacing-top: calc(var(--content-card-not-start-item, 1) * var(--wa-space-s));',
        '--spacing-bottom: calc(var(--content-card-not-end-item, 1) * var(--wa-space-s));',
        'margin-top: var(--spacing-top);', 'margin-bottom: var(--spacing-bottom);']) {
        assert.ok(root.includes(decl), decl)
    }
})

test('4. the pill lights up in the accent on hover, on keyboard focus and while the group is open (its fill is the translucent flat of the fields, --field-bg, with a fallback where it does not exist)', () => {
    assert.ok(style.includes('.group-toggle:hover .group-toggle-pill, .group-toggle:focus-visible .group-toggle-pill, .group-toggle[aria-expanded="true"] .group-toggle-pill {'))
    assert.match(style, /\.group-toggle-pill \{[^}]*background: var\(--field-bg, var\(--wa-color-surface-default\)\);/)
})

test('5. the open toggle still pulls the item below it up (the rule that targeted the checked switch now targets the button)', () => {
    const item = norm(read('components/session/detail/SessionItem.vue'))
    assert.ok(item.includes('.group-toggle[aria-expanded="true"]:not(:has(+.session-item > .json-view:first-child)) { margin-bottom: calc(var(--card-spacing) * -1/4); z-index: 1; }'))
    assert.ok(!item.includes('wa-switch:state(checked)'))
})
