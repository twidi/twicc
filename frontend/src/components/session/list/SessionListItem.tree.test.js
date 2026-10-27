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

test('toggle is a sibling of the row button, stops propagation and exposes aria state', () => {
    const toggle = root.children.find(node => node.type === 1 && node.tag === 'button')
    assert.ok(toggle, 'native toggle button is a direct child of the wrapper')
    assert.ok(toggle.props.some(prop => prop.name === 'class' && prop.value.content === 'tree-toggle'))
    assert.ok(bind(toggle, 'aria-expanded') && bind(toggle, 'aria-label'))
    const rowButton = root.children.find(node => node.type === 1 && node.tag === 'wa-button')
    assert.ok(!JSON.stringify(rowButton.children).includes('"tag":"button"'))
    assert.match(source, /function handleToggleTree\(event\) \{\s*event\.stopPropagation\(\)\s*emit\('toggle-tree', props\.session\)/)
    assert.match(source, /\.tree-toggle \{[^}]*height: auto;[^}]*\}/)
    assert.match(source, /\.tree-toggle \{[^}]*display: inline-flex;/)
})

test('collapsed summary and parent hint render from treeRow', () => {
    assert.match(source, /↰ from \{\{ treeRow\.hint \}\}/)
    assert.match(source, /props\.treeRow\?\.hasChildren && !props\.treeRow\.expanded/)
    assert.match(source, /treeSummary\.latestDate != null[\s\S]*?formatDate\(treeSummary\.latestDate/)
})

test('collapsed summary date honours the relative time setting like the row date', () => {
    assert.match(source, /v-if="useRelativeTime" :date\.prop="timestampToDate\(treeSummary\.latestDate\)"/)
})

test('template compiles', () => {
    const script = compileScript(descriptor, { id: 'tree-test' })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'SessionListItem.vue', id: 'tree-test',
        compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
})
