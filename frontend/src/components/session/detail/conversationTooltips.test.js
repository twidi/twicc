// Conversation items mount by the dozen (scroll, streaming, session switch): what they
// do not show is not mounted, and their button tooltips wait for a first hover or focus.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const read = rel => readFileSync(new URL(rel, import.meta.url), 'utf8')
const tooltipTags = source => [...source.matchAll(/<AppTooltip\b[^>]*>/g)].map(match => match[0])

test('the Show JSON button and its tooltip mount only in debug mode', () => {
    const source = read('./SessionItem.vue')
    assert.match(source, /<wa-button\s+v-if="isEffectiveDebug && !showJson"\s+:id="`json-toggle-\$\{sessionId\}-\$\{lineNum\}`"/)
    assert.match(source, /<AppTooltip v-if="isEffectiveDebug && !showJson" :for="`json-toggle-\$\{sessionId\}-\$\{lineNum\}`">\{\{ \$t\('session\.items\.showJson'\) \}\}<\/AppTooltip>/)
})

test('the detail toggle tooltip is deferred', () => {
    const [tag] = tooltipTags(read('./SessionItem.vue')).filter(tag => tag.includes('detail-toggle-'))
    assert.match(tag, /^<AppTooltip deferred /)
})

test('every tool button tooltip is deferred', () => {
    const tags = tooltipTags(read('./items/ToolUseContent.vue'))
    assert.equal(tags.length, 5)
    for (const tag of tags) assert.match(tag, /^<AppTooltip deferred /, tag)
})
