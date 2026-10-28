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
    assert.match(source, /import \{ TREE_MAX_INDENT_LEVEL, treeGuides \} from '..\/..\/..\/utils\/sessionTree'/)
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

test('rows without children reserve no space: no spacer, flex layout only on parent rows', () => {
    assert.ok(!source.includes('tree-toggle-spacer'))
    assert.match(source, /'session-item-wrapper--tree-parent': !!treeRow\?\.hasChildren/)
    assert.match(source, /\.session-item-wrapper--tree-parent \{[^}]*display: flex;/)
    assert.doesNotMatch(source, /\.session-item-wrapper--tree \{[^}]*display:/)
    assert.match(source, /\.tree-toggle \{[^}]*flex: 0 0 var\(--tree-step\);/)
})

test('the old collapsed summary is gone and the parent hint stays', () => {
    assert.ok(!source.includes('treeSummary'))
    assert.ok(!source.includes('tree-summary'))
    assert.match(source, /↰ from \{\{ treeRow\.hint \}\}/)
})

test('a collapsed parent shows the cumulative indicator instead of its own indicators', () => {
    assert.match(source, /props\.treeRow\?\.activity \? processActivityDisplayMode\(props\.treeRow\.activity\) : null/)
    // compact: the three own indicators are hidden, the cumulative one takes the slot
    for (const id of ['compact-unread', 'compact-pending-request', 'compact-process-indicator']) {
        const own = source.split('\n').filter(line => line.includes(`rowId('${id}')`) && line.includes('v-if'))
        assert.ok(own.every(line => line.includes('!cumulativeMode')), id)
    }
    assert.match(source, /v-if="compactView && cumulativeMode"\s*:summary="treeRow\.activity"[^>]*class="compact-process-indicator"/)
    // normal view: standalone unread eye hidden, process cell swaps in the cumulative indicator
    assert.match(source, /hasUnread && !processState && !cumulativeMode/)
    assert.match(source, /<ProcessActivityIndicator\s+v-if="cumulativeMode"\s+:summary="treeRow\.activity"/)
    assert.match(source, /!compactView && \(\(processState && !session\.ephemeral\) \|\| cumulativeMode\)/)
    assert.match(source, /import ProcessActivityIndicator from '..\/..\/ui\/ProcessActivityIndicator\.vue'/)
})

test('guide lines render from treeGuides in the separators color and only for child rows', () => {
    assert.match(source, /treeRow \? treeGuides\(props\.treeRow\) : null/)
    assert.match(source, /var\(--sidebar-divider-color, var\(--wa-color-surface-border\)\)/)
    assert.match(source, /--tree-line: var\(--wa-border-width-s\) solid/)
    assert.match(source, /props\.treeRow\.depth < TREE_MAX_INDENT_LEVEL/)
    assert.match(source, /\.tree-guide--ends \{\s*inset-block-end: calc\(100% - var\(--tree-anchor\)\);/)
    // the arrow, the elbow and the stub anchor to the title line, not to 50% of the row
    assert.match(source, /\.tree-elbow \{\s*inset-block-start: var\(--tree-anchor\);/)
    assert.match(source, /\.tree-guide--stub \{\s*inset-block-start: calc\(var\(--tree-anchor\) \+ var\(--tree-stub-gap\)\);/)
    assert.match(source, /> \.tree-toggle \{\s*align-self: flex-start;\s*height: calc\(2 \* var\(--tree-anchor\)\);/)
    assert.match(source, /--tree-anchor: 50%;/)
    // hinted rows: the hint line sits above the title in compact rows too (2xs padding there)
    assert.match(source, /\.session-item-wrapper--tree-hinted\.session-item-wrapper--compact \{\s*--tree-anchor: calc\(\s*var\(--wa-space-2xs\) \+ var\(--wa-border-width-s\)\s*\+ var\(--wa-font-size-2xs\) \* var\(--wa-line-height-normal\) \+ var\(--wa-space-3xs\)/)
    assert.match(source, /\.session-item-wrapper--tree-hinted:not\(\.session-item-wrapper--compact\) \{\s*--tree-anchor: calc\(\s*var\(--wa-space-xs\)/)
    assert.match(source, /inline-size: calc\(var\(--tree-step\) \/ 2 - var\(--tree-elbow-gap\)\)/)
})

test('template compiles', () => {
    const script = compileScript(descriptor, { id: 'tree-test' })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'SessionListItem.vue', id: 'tree-test',
        compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
})

test('a child in its parent project hides the project marker, the dot, its tooltip and the project line when empty', () => {
    assert.match(source, /effectiveShowProjectName = computed\(\(\) => \{\s*\/\/[^\n]*\n\s*if \(props\.treeRow\?\.sameProjectAsParent\) return false/)
    assert.match(source, /v-if="compactView && effectiveShowProjectName"\s+:id="rowId\('compact-project-dot'\)"/)
    assert.match(source, /v-if="!compactView && \(effectiveShowProjectName \|\| hasCodeComments \|\| \(hasUnread/)
    // The worktree marker does not depend on the project name flag.
    assert.match(source, /v-if="showWorktreeIcon"/)
    assert.doesNotMatch(source, /showWorktreeIcon = computed\([^)]*effectiveShowProjectName/)
})
