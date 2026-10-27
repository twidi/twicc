import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

const source = readFileSync(new URL('./SessionListItem.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source, { filename: 'SessionListItem.vue' })
const root = descriptor.template.ast.children.find(node => node.type === 1)
const bind = (node, name) => node.props.find(prop => prop.type === 7 && prop.arg?.content === name)

test('treeRow prop defaults to null and the toggle-tree emit is declared', () => {
    assert.match(source, /treeRow: \{\s*type: Object,\s*default: null\s*\}/)
    assert.match(source, /defineEmits\(\[[^\]]*'toggle-tree'/)
    assert.match(source, /import \{ TREE_MAX_INDENT_LEVEL \} from '..\/..\/..\/utils\/sessionTree'/)
    assert.match(source, /Math\.min\(props\.treeRow\.depth, TREE_MAX_INDENT_LEVEL\)/)
})

test('wrapper keeps its class keys and carries the indent variable only for tree rows', () => {
    const classes = bind(root, 'class').exp.content
    for (const key of ['session-item-wrapper--no-menu', 'session-item-wrapper--active', 'session-item-wrapper--compact',
        'sidebar-row-wrapper--active', 'sidebar-row-wrapper--compact', 'sidebar-row-wrapper--selected']) {
        assert.ok(classes.includes(`'${key}'`), key)
    }
    assert.match(bind(root, 'style').exp.content, /treeRow \? \{ '--tree-indent-level': treeIndentLevel \} : undefined/)
    assert.match(source, /padding-inline-start: calc\([\s\S]*?var\(--tree-indent-level/)
    const button = root.children.find(node => node.type === 1 && node.tag === 'wa-button')
    assert.equal(bind(button, 'style'), undefined)
})

test('pill is a sibling of the row button, stops propagation and exposes aria state', () => {
    const pill = root.children.flatMap(node => node.type === 1 && node.tag === 'template' ? node.children : [node])
        .find(node => node.type === 1 && node.tag === 'button')
    assert.ok(pill, 'native pill button is a direct child of the wrapper')
    assert.ok(pill.props.some(prop => prop.name === 'class' && prop.value.content === 'tree-pill'))
    assert.ok(bind(pill, 'aria-expanded') && bind(pill, 'aria-label'))
    assert.match(bind(pill, 'aria-label').exp.content, /Collapse[\s\S]*Expand[\s\S]*treeRow\.count/)
    const rowButton = root.children.find(node => node.type === 1 && node.tag === 'wa-button')
    assert.ok(!JSON.stringify(rowButton.children).includes('"tag":"button"'))
    assert.ok(!JSON.stringify(rowButton.children).includes('tree-pill'))
    assert.match(source, /function handleToggleTree\(event\) \{\s*event\.stopPropagation\(\)\s*emit\('toggle-tree', props\.session\)/)
    assert.match(source, /\.tree-pill \{[^}]*height: auto;[^}]*\}/)
    assert.match(source, /\.tree-pill \{[^}]*display: inline-flex;/)
})

test('the pill carries one tooltip: the indicator renders none of its own', () => {
    assert.match(source, /<ProcessActivityIndicator[\s\S]*?:tooltip="false"[\s\S]*?\/>/)
    assert.equal(source.match(/:for="rowId\('tree-pill'\)"/g).length, 1)
    assert.match(source, /processActivityTooltip\(treeRow\.activity, treeActivityMode\)/)
    assert.match(source, /treeRow\.pinned > 0/)
    assert.match(source, /treeRow\.latestDate != null/)
    assert.match(source, /<template v-if="useRelativeTime"><wa-relative-time :date\.prop="timestampToDate\(treeRow\.latestDate\)"/)
    assert.match(source, /formatDate\(treeRow\.latestDate, \{ smart: true \}\)/)
})

test('the collapsed-only indicator and the parent hint render from treeRow', () => {
    assert.match(source, /↰ from \{\{ treeRow\.hint \}\}/)
    assert.match(source, /props\.treeRow\?\.hasChildren && !props\.treeRow\.expanded && props\.treeRow\.activity/)
    assert.match(source, /processActivityDisplayMode\(props\.treeRow\.activity\)/)
})

test('guides are drawn for child rows only, from the last TREE_MAX_INDENT_LEVEL - 1 entries', () => {
    assert.match(source, /row\.depth < 1\) return \[\]/)
    assert.match(source, /row\.through\.slice\(-\(TREE_MAX_INDENT_LEVEL - 1\)\)/)
    assert.match(source, /<span v-if="treeGuides\.length" class="tree-guides" aria-hidden="true">/)
    assert.match(source, /'tree-guide--last': guide\.own && treeRow\.isLast/)
    assert.match(source, /\.tree-guide--last \{\s*bottom: 50%;/)
})

test('the old toggle column, leaf spacer and collapsed summary are gone', () => {
    for (const name of ['tree-toggle', 'tree-toggle-spacer', 'tree-summary', 'treeSummary']) {
        assert.ok(!source.includes(name), name)
    }
    assert.ok(!/\.session-item-wrapper--tree \{[^}]*display: flex/.test(source))
})

test('the row reserves end padding only when it carries a pill', () => {
    assert.match(source, /'session-item--has-pill': treeRow\?\.hasChildren/)
    const css = readFileSync(new URL('../../../styles/sidebar-rows.css', import.meta.url), 'utf8')
    assert.match(css, /\.sidebar-row\.session-item--has-pill::part\(base\) \{[^}]*padding-inline-end:/)
})

test('template compiles', () => {
    const script = compileScript(descriptor, { id: 'tree-test' })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'SessionListItem.vue', id: 'tree-test',
        compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
})
