// Run with: node --test src/utils/countUp.test.js (from the frontend dir)
// Count-up of the project stats numbers (visual refresh step 7a,
// docs/plans/2026-09-30-stats-motion-design.md §4.2, §8).
import test from 'node:test'
import assert from 'node:assert/strict'

import { easeOutCubic, tweenValue, formatAvg } from './countUp.js'

test('1. easeOutCubic: 0 → 0, 1 → 1, 0.5 → 0.875', () => {
    assert.equal(easeOutCubic(0), 0)
    assert.equal(easeOutCubic(1), 1)
    assert.equal(easeOutCubic(0.5), 0.875)
})

test('2. tweenValue: starts on from, ends exactly on to, eased in between', () => {
    assert.equal(tweenValue(0, 1234, 0), 0)
    assert.equal(tweenValue(0, 1234, 1), 1234)
    assert.equal(tweenValue(0, 0.1, 1), 0.1)
    assert.equal(tweenValue(0, 100, 0.5), 87.5)
})

test('3. tweenValue: works downward', () => {
    assert.equal(tweenValue(100, 20, 0), 100)
    assert.equal(tweenValue(100, 20, 1), 20)
    assert.equal(tweenValue(100, 20, 0.5), 30)
})

test('3b. tweenValue: exactly to at the end even when the arithmetic is inexact', () => {
    // 0.7 + (0.1 - 0.7) * 1 === 0.09999999999999998
    assert.equal(tweenValue(0.7, 0.1, 1), 0.1)
})

test('3c. tweenValue: a negative progress stays on from (rAF timestamp before the start)', () => {
    assert.equal(tweenValue(0, 1234, -0.003), 0)
    assert.equal(tweenValue(100, 20, -0.5), 100)
})

test('4. formatAvg: null → -, whole → integer, one decimal otherwise', () => {
    assert.equal(formatAvg(null), '-')
    assert.equal(formatAvg(12), '12')
    assert.equal(formatAvg(1.25), '1.3')
    assert.equal(formatAvg(0.04), '0')
})
