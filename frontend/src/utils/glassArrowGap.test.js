// Run with: node --test src/utils/glassArrowGap.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { computeArrowGap } from './glassArrowGap.js'

const BODY = { left: 100, top: 200, right: 400, bottom: 500, width: 300, height: 300 }

function rect(left, top, width, height) {
    return { left, top, width, height, right: left + width, bottom: top + height }
}

test('popover below its anchor: the arrow sits on the top edge', () => {
    const arrow = rect(180, 194, 12, 12)
    assert.deepEqual(computeArrowGap('bottom', BODY, arrow), { side: 'top', start: 80, end: 92 })
    assert.deepEqual(computeArrowGap('bottom-start', BODY, arrow), { side: 'top', start: 80, end: 92 })
})

test('popover above its anchor: the arrow sits on the bottom edge', () => {
    const arrow = rect(250, 494, 12, 12)
    assert.deepEqual(computeArrowGap('top-end', BODY, arrow), { side: 'bottom', start: 150, end: 162 })
})

test('popover on the left or right of its anchor: the gap runs along the vertical edge', () => {
    assert.deepEqual(computeArrowGap('left', BODY, rect(394, 300, 12, 12)), { side: 'right', start: 100, end: 112 })
    assert.deepEqual(computeArrowGap('right-start', BODY, rect(94, 220, 12, 12)), { side: 'left', start: 20, end: 32 })
})

test('the gap is clamped to the body edge', () => {
    assert.deepEqual(computeArrowGap('bottom', BODY, rect(95, 194, 12, 12)), { side: 'top', start: 0, end: 7 })
    assert.deepEqual(computeArrowGap('bottom', BODY, rect(395, 194, 12, 12)), { side: 'top', start: 295, end: 300 })
})

test('a scaled popover (mid show animation) gives the unscaled gap', () => {
    // Body drawn at 90 %: 270px wide on screen for a 300px layout box.
    const body = { left: 100, top: 200, right: 370, bottom: 470, width: 270, height: 270 }
    const arrow = rect(172, 194, 10.8, 10.8)
    assert.deepEqual(computeArrowGap('bottom', body, arrow, 0.9), { side: 'top', start: 80, end: 92 })
})

test('no gap without a placement, an arrow box or a body box', () => {
    assert.equal(computeArrowGap('', BODY, rect(180, 194, 12, 12)), null)
    assert.equal(computeArrowGap(null, BODY, rect(180, 194, 12, 12)), null)
    assert.equal(computeArrowGap('bottom', BODY, rect(180, 194, 0, 0)), null)
    assert.equal(computeArrowGap('bottom', { ...BODY, width: 0, height: 0 }, rect(180, 194, 12, 12)), null)
    assert.equal(computeArrowGap('diagonal', BODY, rect(180, 194, 12, 12)), null)
})
