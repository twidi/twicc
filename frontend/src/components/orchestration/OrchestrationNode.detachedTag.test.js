import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const vue = readFileSync(new URL('./OrchestrationNode.vue', import.meta.url), 'utf8')
const css = readFileSync(new URL('./treeNode.css', import.meta.url), 'utf8')
const agentVue = readFileSync(new URL('./AgentTreeNode.vue', import.meta.url), 'utf8')

test('the detached tag follows the current tag inside one tags wrapper', () => {
    assert.match(vue, /<span v-if="isCurrent \|\| isDetached" class="orch-tags">\s*<span v-if="isCurrent" class="orch-current-tag">current<\/span>\s*<span\s+v-if="isDetached"\s+class="orch-detached-tag"/)
    assert.match(vue, />detached<\/span>/)
})

test('the detached tag explains itself in its title and is not clickable', () => {
    assert.ok(vue.includes('title="Shown as a separate root in the sidebar tree. The session is still part of this orchestration."'))
    const tag = vue.slice(vue.indexOf('class="orch-detached-tag"'), vue.indexOf('>detached</span>'))
    assert.doesNotMatch(tag, /@click|router-link/)
})

test('the flag is inert without a parent and uses the visibility-gated computed', () => {
    assert.match(vue, /const isDetached = visibleComputed\(\(\) => \{[\s\S]*?session\?\.tree_detached === true && !!session\.spawned_by/)
})

test('the agents view has no detached tag', () => {
    assert.doesNotMatch(agentVue, /detached/)
})

test('the detached tag shares the current tag shape with a neutral colour', () => {
    assert.match(css, /\.orch-current-tag,\s*\.orch-detached-tag \{/)
    assert.match(css, /\.orch-detached-tag \{\s*border-color: var\(--wa-color-neutral-border-quiet\);\s*color: var\(--wa-color-text-quiet\);/)
})

test('on a narrow pane the tags move to their own line, with the panel breakpoint', () => {
    const panel = readFileSync(new URL('./OrchestrationPanel.vue', import.meta.url), 'utf8')
    assert.ok(panel.includes('@container orch (max-width: 480px)'))
    const block = css.slice(css.indexOf('@container orch (max-width: 480px)'))
    assert.match(block, /\.ocard-title \{\s*flex-wrap: wrap;/)
    assert.match(block, /\.orch-tags \{\s*flex-basis: 100%;/)
})
