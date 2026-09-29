// Run with: node --test src/utils/sidebarRows.test.js (from the frontend dir)
// Gliding open-row fill in the sidebar lists (visual refresh step 6a-bis,
// docs/plans/2026-09-29-sidebar-row-glide-design.md §4.2): the two DOM helpers the session
// and artifacts lists pass to useGlideInk, driven with fake elements; and the reveal helpers
// of the open row (§10: bands read from the list, margins, the artifacts entry target).
import test from 'node:test'
import assert from 'node:assert/strict'

import { activeRowBase, entranceOffset, entryReveal, revealBands, revealMargins } from './sidebarRows.js'

/** A fake element answering querySelector / closest from plain maps. */
function makeElement({ selectors = {}, closest = {}, shadowRoot = null, translate = 'none' } = {}) {
    return {
        translate,
        shadowRoot,
        querySelector(selector) { return selectors[selector] ?? null },
        closest(selector) { return closest[selector] ?? null },
    }
}

const fakeEnv = { getComputedStyle: (element) => ({ translate: element.translate }) }

// ---------------------------------------------------------------------------
// activeRowBase
// ---------------------------------------------------------------------------

test('activeRowBase: the base part of the open row host under the root', () => {
    const base = makeElement()
    const host = makeElement({ shadowRoot: makeElement({ selectors: { '[part~="base"]': base } }) })
    const root = makeElement({ selectors: { '.sidebar-row--active': host } })
    assert.equal(activeRowBase(root), base)
})

test('activeRowBase: null without a root, an open row, a shadow root or a base part', () => {
    assert.equal(activeRowBase(null), null)
    assert.equal(activeRowBase(undefined), null)
    assert.equal(activeRowBase(makeElement()), null, 'no open row')
    const unrendered = makeElement({ selectors: { '.sidebar-row--active': makeElement() } })
    assert.equal(activeRowBase(unrendered), null, 'no shadow root yet')
    const empty = makeElement({ selectors: { '.sidebar-row--active': makeElement({ shadowRoot: makeElement() }) } })
    assert.equal(activeRowBase(empty), null, 'shadow root without a base part')
})

// ---------------------------------------------------------------------------
// entranceOffset
// ---------------------------------------------------------------------------

/** A root whose open row sits in an entering element with this computed translate. */
function enteringRoot(translate) {
    const entering = makeElement({ translate })
    const host = makeElement({ closest: { '.list-entering': entering } })
    return makeElement({ selectors: { '.sidebar-row--active': host } })
}

test('entranceOffset: the translate of the open row\'s .list-entering ancestor', () => {
    assert.deepEqual(entranceOffset(enteringRoot('none'), fakeEnv), { x: 0, y: 0 })
    assert.deepEqual(entranceOffset(enteringRoot('0px 3.5px'), fakeEnv), { x: 0, y: 3.5 })
    assert.deepEqual(entranceOffset(enteringRoot('2px'), fakeEnv), { x: 2, y: 0 }, 'one value: x only')
    assert.deepEqual(entranceOffset(enteringRoot('1.5px -4px'), fakeEnv), { x: 1.5, y: -4 })
})

test('entranceOffset: { 0, 0 } without a root, an open row or an entering ancestor', () => {
    assert.deepEqual(entranceOffset(null, fakeEnv), { x: 0, y: 0 })
    assert.deepEqual(entranceOffset(makeElement(), fakeEnv), { x: 0, y: 0 }, 'no open row')
    const settled = makeElement({ selectors: { '.sidebar-row--active': makeElement() } })
    assert.deepEqual(entranceOffset(settled, fakeEnv), { x: 0, y: 0 }, 'not entering')
})

test('entranceOffset reads globalThis when no env is given', () => {
    const previous = globalThis.getComputedStyle
    globalThis.getComputedStyle = (element) => ({ translate: element.translate })
    try {
        assert.deepEqual(entranceOffset(enteringRoot('0px 6px')), { x: 0, y: 6 })
    } finally {
        globalThis.getComputedStyle = previous
    }
})

// ---------------------------------------------------------------------------
// revealBands / revealMargins / entryReveal (§10)
// ---------------------------------------------------------------------------

/** A fake env whose computed style answers the reveal properties from a plain map. */
function bandsEnv(values) {
    return { getComputedStyle: () => ({ getPropertyValue: (name) => values[name] ?? '' }) }
}

test('revealBands: top, and bottom + cover, in px', () => {
    const env = bandsEnv({
        '--sidebar-row-reveal-top': '75px',
        '--sidebar-row-reveal-bottom': '75px',
        '--sidebar-row-reveal-cover': '46.875px',
    })
    assert.deepEqual(revealBands({}, env), { top: 75, bottom: 121.875 })
})

test('revealBands: each property defaults to 0', () => {
    assert.deepEqual(revealBands({}, bandsEnv({})), { top: 0, bottom: 0 })
    assert.deepEqual(revealBands({}, bandsEnv({ '--sidebar-row-reveal-cover': '46.875px' })), { top: 0, bottom: 46.875 })
    assert.deepEqual(revealBands({}, bandsEnv({ '--sidebar-row-reveal-top': ' 0px' })), { top: 0, bottom: 0 })
})

test('revealBands: { 0, 0 } for a null element, without reading styles', () => {
    const env = { getComputedStyle: () => { throw new Error('not read') } }
    assert.deepEqual(revealBands(null, env), { top: 0, bottom: 0 })
})

test('revealBands reads globalThis when no env is given', () => {
    const previous = globalThis.getComputedStyle
    globalThis.getComputedStyle = bandsEnv({ '--sidebar-row-reveal-top': '12px' }).getComputedStyle
    try {
        assert.deepEqual(revealBands({}), { top: 12, bottom: 0 })
    } finally {
        globalThis.getComputedStyle = previous
    }
})

test('revealMargins: the bands shifted by the list padding', () => {
    assert.deepEqual(revealMargins({ top: 75, bottom: 121.875 }, 4), { marginTop: 71, marginBottom: 125.875 })
    assert.deepEqual(revealMargins({ top: 0, bottom: 46.875 }, 4), { marginTop: -4, marginBottom: 50.875 })
})

test('entryReveal: the nearest target, or null within 1px of the current scroll', () => {
    const view = { scrollTop: 100, clientHeight: 400, scrollHeight: 1200 }
    const bands = { top: 75, bottom: 75 }
    assert.equal(entryReveal({ ...view, offsetTop: 250, offsetHeight: 36 }, bands), null, 'inside the zone')
    assert.equal(entryReveal({ ...view, offsetTop: 150, offsetHeight: 36 }, bands), 75, 'above: top band edge')
    assert.equal(entryReveal({ ...view, offsetTop: 420, offsetHeight: 36 }, bands), 456 - 400 + 75, 'below: bottom band edge')
    assert.equal(entryReveal({ ...view, offsetTop: 1150, offsetHeight: 36 }, bands), 800, 'clamped at scrollHeight - clientHeight')
    assert.equal(entryReveal({ ...view, offsetTop: 174.5, offsetHeight: 36 }, bands), null, '0.5px away: null')
    assert.equal(entryReveal({ ...view, offsetTop: 173, offsetHeight: 36 }, bands), 98, '2px away: the target')
})
