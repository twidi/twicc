import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

test('1. the block opens like a curtain: each row grows in its own segment and slides in from outside the chat, one after the other', () => {
    const css = norm(read('styles/group-reveal.css'))
    assert.ok(css.includes('@keyframes group-enter { from { opacity: 0; translate: calc((-100% - var(--card-spacing, 1rem)) * var(--motion-amount)) 0; } }'))
    assert.ok(css.includes('@keyframes group-grow { from { grid-template-rows: 0fr; } to { grid-template-rows: 1fr; } }'))
    assert.ok(css.includes('@keyframes group-head-grow { from { grid-template-rows: auto 0fr; } to { grid-template-rows: auto 1fr; } }'))
    // The segment and the start of a row, from its index and the group's count.
    assert.ok(css.includes('.session-items .virtual-scroller-item:is(.group-row-growing, .group-head-revealing) { --group-seg: min(60ms, calc(400ms / var(--group-count, 1))); --group-delay: calc(var(--group-index, 0) * var(--group-seg)); }'))
    // A row: its space opens in its segment (linear, so the whole block grows steadily); its content slides in from its start.
    assert.ok(css.includes('.session-items .virtual-scroller-item.group-row-growing { display: grid; animation: group-grow var(--group-seg) linear both; animation-delay: var(--group-delay); }'))
    assert.ok(css.includes('.session-items .virtual-scroller-item.group-row-growing > :not(.group-toggle) { min-height: 0; overflow: hidden; animation: group-enter 320ms var(--motion-ease-out) both; animation-delay: var(--group-delay); }'))
    // A placeholder row (content not loaded: an empty box with an inline min-height) grows by that min-height.
    assert.ok(css.includes('@keyframes group-placeholder-grow { from { min-height: 0; } }'))
    assert.ok(css.includes('.session-items .virtual-scroller-item.group-row-growing:has(> [style*="min-height"]) { display: flow-root; animation: none; }'))
    assert.ok(css.includes('.session-items .virtual-scroller-item.group-row-growing > [style*="min-height"] { animation: group-enter 320ms var(--motion-ease-out) both, group-placeholder-grow var(--group-seg) linear both; animation-delay: var(--group-delay), var(--group-delay); }'))
    assert.ok(!css.includes('!important'))
    // The head: its item is the second track, the first row of the curtain.
    assert.ok(css.includes('.session-items .virtual-scroller-item.group-head-revealing { display: grid; animation: group-head-grow var(--group-seg) linear both; animation-delay: var(--group-delay); }'))
    assert.ok(css.includes('.session-items .virtual-scroller-item.group-head-revealing > .session-item { min-height: 0; overflow: hidden; animation: group-enter 320ms var(--motion-ease-out) both; animation-delay: var(--group-delay); }'))
    assert.ok(css.includes('.group-head-leaving { overflow: hidden; pointer-events: none; animation: list-leave 260ms var(--motion-ease-out) forwards; }'))
    assert.doesNotMatch(css, /list-enter/, 'no vertical rise')
})

test('2. the chat list: displayed items in the scroller, exits only for a collapse, the group noted before the store action', () => {
    const sfc = read('components/session/detail/SessionItemsList.vue')
    assert.ok(sfc.includes('const groupReveal = useGroupReveal({ items: visualItems, getKey: item => item.lineNum })'))
    assert.ok(sfc.includes('isEligible: () => groupReveal.isCollapsing(),'))
    assert.ok(sfc.includes('const rowClass = item => exits.exitClass(item) ?? groupReveal.itemClass(item) ?? entrance.itemClass(item)'))
    assert.ok(sfc.includes('const rowStyle = item => exits.exitStyle(item) ?? groupReveal.itemStyle(item) ?? entrance.itemStyle(item)'))
    assert.ok(sfc.includes(':items="displayItems"') && sfc.includes(':item-class="rowClass"') && sfc.includes(':item-style="rowStyle"'))
    const toggle = sfc.slice(sfc.indexOf('function toggleGroup('), sfc.indexOf('/** Set of tool line numbers'))
    assert.ok(toggle.indexOf('groupReveal.noteToggle(') < toggle.indexOf('store.toggleExpandedGroup('), 'noted before the action')
})

test('3. the head item stays in the template while it folds out', () => {
    const sfc = read('components/session/detail/SessionItemsList.vue')
    assert.ok(sfc.includes('v-if="item.isExpanded || groupReveal.isHeadLeaving(item.lineNum)"'))
    assert.ok(sfc.includes('groupReveal.headLeaveClass(item.lineNum)') && sfc.includes('groupReveal.headLeaveStyle(item.lineNum)'))
})

test('4. the growth does not depend on a height measured at insertion (it was wrong for a row whose content renders late)', () => {
    const src = read('composables/useGroupReveal.js')
    assert.ok(!src.includes('getBoundingClientRect') && !src.includes('.animate('))
    assert.ok(src.includes("classes.push('group-row-growing')"))
    assert.ok(src.includes("{ '--group-index': index, '--group-count': groupCount.value }"))
})

test('5. the sheet is imported after motion.css (it reads its keyframes and tokens)', () => {
    const main = read('main.js')
    assert.ok(main.includes("import './styles/group-reveal.css'"))
    assert.ok(main.indexOf("import './styles/group-reveal.css'") > main.indexOf("import './styles/motion.css'"))
})
