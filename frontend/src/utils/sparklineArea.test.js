// Run with: node --test src/utils/sparklineArea.test.js (from the frontend dir)
// Area under a stats curve (visual refresh step 7a,
// docs/plans/2026-09-30-stats-motion-design.md §6.2, §8).
import test from 'node:test'
import assert from 'node:assert/strict'

import { areaPoints } from './sparklineArea.js'

test('1. closes the curve along the baseline', () => {
    assert.equal(areaPoints('0,1 10,5 20,3', 1), '0,1 10,5 20,3 20,1 0,1')
})

test('2. a single point closes on itself', () => {
    assert.equal(areaPoints('5,3', 1), '5,3 5,1 5,1')
})

test('3. no points, no area', () => {
    assert.equal(areaPoints('', 1), '')
})
