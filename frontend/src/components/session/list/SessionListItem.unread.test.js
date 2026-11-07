import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./SessionListItem.vue', import.meta.url), 'utf8')
const template = source.slice(source.indexOf('<template>'), source.indexOf('</template>\n\n<style'))

// The `<ProcessIndicator ... />` element carrying the given id, and the tooltip line targeting it.
const indicatorBlock = id => template.match(new RegExp(`<ProcessIndicator\\b(?:(?!/>)[\\s\\S])*?rowId\\('${id}'\\)[\\s\\S]*?/>`))?.[0]
const tooltipLine = id => template.split('\n').find(line => line.includes('<AppTooltip') && line.includes(`:for="rowId('${id}')"`))

test('process-line ProcessIndicator always renders and carries the unread state', () => {
    const block = indicatorBlock('process-indicator')
    assert.ok(block, 'process-line indicator found')
    assert.match(block, /:unread="hasUnread"/)
    assert.doesNotMatch(block, /v-else|v-if/)
    const tooltip = tooltipLine('process-indicator')
    assert.ok(tooltip.includes("{{ hasUnread ? $t('session.list.unreadProcess', { status: processTooltip }) : processTooltip }}"))
    assert.ok(!/v-if="!hasUnread"/.test(tooltip))
})

test('compact ProcessIndicator carries the unread state and shows for unread or non-pending rows', () => {
    const block = indicatorBlock('compact-process-indicator')
    assert.ok(block, 'compact indicator found')
    assert.match(block, /:unread="hasUnread"/)
    assert.match(block, /v-if="compactView && !cumulativeMode && processState && !session\.ephemeral && \(hasUnread \|\| !pendingRequest\)"/)
    assert.match(block, /var\(--wa-font-size-xs\)/)
    assert.ok(tooltipLine('compact-process-indicator').includes("{{ hasUnread ? $t('session.list.unreadProcess', { status: processTooltip }) : processTooltip }}"))
})

test('the hand-drawn eye remains only for sessions without a process', () => {
    assert.ok(!template.includes('process-unread'))
    assert.ok(template.includes("rowId('standalone-unread')"))
    assert.match(template, /v-if="compactView && !cumulativeMode && hasUnread && !processState"\s+:id="rowId\('compact-unread'\)"/)
    assert.ok(tooltipLine('compact-unread').includes('v-if="compactView && !cumulativeMode && hasUnread && !processState"'))
})

test('compact pending-request hand conditions are unchanged', () => {
    assert.match(template, /v-if="compactView && !cumulativeMode && !hasUnread && pendingRequest"\s+:id="rowId\('compact-pending-request'\)"/)
    assert.ok(tooltipLine('compact-pending-request').includes('v-if="compactView && !cumulativeMode && !hasUnread && pendingRequest"'))
})
