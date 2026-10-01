import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LIST_EXIT_DURATION_MS } from '../utils/listExit.js'

const here = dirname(fileURLToPath(import.meta.url))
const read = (rel) => readFileSync(join(here, '..', rel), 'utf8')
const norm = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\s+/g, ' ').trim()

test('1. the exit animation folds the height from the measured one, fades and slides, and lasts as long as the timer', () => {
    const css = norm(read('styles/motion.css'))
    assert.ok(css.includes('@keyframes list-leave { from { height: var(--list-leave-h); } to { height: 0; opacity: 0; translate: calc(-0.75rem * var(--motion-amount)) 0; } }'))
    assert.ok(css.includes(`.list-leaving { overflow: hidden; pointer-events: none; animation: list-leave ${LIST_EXIT_DURATION_MS}ms var(--motion-ease-out) forwards; }`))
})

test('2. the session list gives the scroller the displayed items and composes the exit with the cascade', () => {
    const sfc = read('components/session/list/SessionList.vue')
    assert.ok(sfc.includes("import { useListExit } from '../../../composables/useListExit'"))
    assert.ok(sfc.includes('isEligible: (s) => !!store.sessions[s.id]?.archived,'))
    assert.ok(sfc.includes('const rowClass = (session) => exits.exitClass(session) ?? cascade.itemClass(session)'))
    assert.ok(sfc.includes('const rowStyle = (session) => exits.exitStyle(session) ?? cascade.itemStyle(session)'))
    assert.ok(sfc.includes(':items="displayItems"'))
    assert.ok(sfc.includes(':item-class="rowClass"') && sfc.includes(':item-style="rowStyle"'))
})

test('3. the cascade still reads the real list, not the one holding the exiting rows', () => {
    const sfc = read('components/session/list/SessionList.vue')
    const cascade = sfc.slice(sfc.indexOf('const cascade = useListCascade('), sfc.indexOf('const cascade = useListCascade(') + 200)
    assert.ok(cascade.includes('items: sessions,'))
})

test('4. the artifacts list shows the displayed items and composes the exit with the cascade', () => {
    const sfc = read('components/artifacts/ArtifactBookmarkList.vue')
    assert.ok(sfc.includes("import { useListExit } from '../../composables/useListExit'"))
    assert.ok(sfc.includes('isEligible: () => true,'))
    assert.ok(sfc.includes('const entryClass = (b) => exits.exitClass(b) ?? cascade.itemClass(b)'))
    assert.ok(sfc.includes('const entryStyle = (b) => exits.exitStyle(b) ?? cascade.itemStyle(b)'))
    assert.ok(sfc.includes('v-for="(b, index) in displayItems"'))
    assert.ok(sfc.includes(':class="entryClass(b)"') && sfc.includes(':style="entryStyle(b)"'))
    const cascade = sfc.slice(sfc.indexOf('const cascade = useListCascade('), sfc.indexOf('const cascade = useListCascade(') + 120)
    assert.ok(cascade.includes('items: list,'), 'the cascade still reads the real list')
})

test('5. the empty state waits for the last exiting row: it reads the displayed items, not the real list', () => {
    assert.ok(read('components/artifacts/ArtifactBookmarkList.vue').includes('<div v-if="displayItems.length === 0" class="empty-state">'))
    assert.ok(read('components/session/list/SessionList.vue').includes('v-else-if="displayItems.length === 0 && !isLoading"'))
})
