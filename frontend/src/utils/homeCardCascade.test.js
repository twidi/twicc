// Run with: node --test src/utils/homeCardCascade.test.js (from the frontend dir)
// Home card cascade (visual refresh step 7b, docs/plans/2026-09-30-home-motion-design.md §3.3):
// the pure rules.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    HOME_CARD_DURATION_MS,
    HOME_CARD_MAX_INDEX,
    HOME_CARD_STAGGER_MS,
    HOME_SPARKLINE_REVEAL_MS,
    homeCardEndMs,
    planHomeCardCascade,
} from './homeCardCascade.js'

const VIEW = { top: 0, bottom: 800 }
/** A 100px-tall card rect starting at `top`. */
const card = (top) => ({ top, bottom: top + 100 })

test('1. planHomeCardCascade: every card on screen, in order', () => {
    assert.deepEqual(planHomeCardCascade([card(0), card(120), card(240)], VIEW), [0, 1, 2])
})

test('2. planHomeCardCascade: the cards above and below the view get null', () => {
    assert.deepEqual(planHomeCardCascade([card(-300), card(100), card(300), card(900)], VIEW), [null, 0, 1, null])
})

test('3. planHomeCardCascade: the index is capped', () => {
    const rects = Array.from({ length: 14 }, (_, i) => ({ top: i * 50, bottom: i * 50 + 40 }))
    assert.deepEqual(planHomeCardCascade(rects, VIEW), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10])
})

test('4. planHomeCardCascade: no card on screen, no card at all', () => {
    assert.deepEqual(planHomeCardCascade([card(900), card(1000)], VIEW), [null, null])
    assert.deepEqual(planHomeCardCascade([card(-500), card(-300)], VIEW), [null, null])
    assert.deepEqual(planHomeCardCascade([], VIEW), [])
})

test('5. homeCardEndMs: the later of the card and sparkline animations, plus the margin', () => {
    assert.equal(homeCardEndMs(0), 900)
    assert.equal(homeCardEndMs(3), 1080)
})

test('6. the constants', () => {
    assert.equal(HOME_CARD_STAGGER_MS, 60)
    assert.equal(HOME_CARD_DURATION_MS, 420)
    assert.equal(HOME_SPARKLINE_REVEAL_MS, 800)
    assert.equal(HOME_CARD_MAX_INDEX, 10)
})
