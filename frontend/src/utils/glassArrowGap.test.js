// Run with: node --test src/utils/glassArrowGap.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

import { computeArrowGap } from './glassArrowGap.js'

const BODY = { left: 100, top: 200, right: 400, bottom: 500, width: 300, height: 300 }

function rect(left, top, width, height) {
    return { left, top, width, height, right: left + width, bottom: top + height }
}

test('popover below its anchor: the arrow sits on the top edge', () => {
    const arrow = rect(180, 194, 12, 12)
    assert.deepEqual(computeArrowGap('bottom', BODY, arrow), { side: 'top', start: 80, end: 92, center: 86 })
    assert.deepEqual(computeArrowGap('bottom-start', BODY, arrow), { side: 'top', start: 80, end: 92, center: 86 })
})

test('popover above its anchor: the arrow sits on the bottom edge', () => {
    const arrow = rect(250, 494, 12, 12)
    assert.deepEqual(computeArrowGap('top-end', BODY, arrow), { side: 'bottom', start: 150, end: 162, center: 156 })
})

test('popover on the left or right of its anchor: the gap runs along the vertical edge', () => {
    assert.deepEqual(computeArrowGap('left', BODY, rect(394, 300, 12, 12)), { side: 'right', start: 100, end: 112, center: 106 })
    assert.deepEqual(computeArrowGap('right-start', BODY, rect(94, 220, 12, 12)), { side: 'left', start: 20, end: 32, center: 26 })
})

test('the gap is clamped to the body edge', () => {
    assert.deepEqual(computeArrowGap('bottom', BODY, rect(95, 194, 12, 12)), { side: 'top', start: 0, end: 7, center: 3.5 })
    assert.deepEqual(computeArrowGap('bottom', BODY, rect(395, 194, 12, 12)), { side: 'top', start: 295, end: 300, center: 297.5 })
})

test('a scaled popover (mid show animation) gives the unscaled gap', () => {
    // Body drawn at 90 %: 270px wide on screen for a 300px layout box.
    const body = { left: 100, top: 200, right: 370, bottom: 470, width: 270, height: 270 }
    const arrow = rect(172, 194, 10.8, 10.8)
    assert.deepEqual(computeArrowGap('bottom', body, arrow, 0.9), { side: 'top', start: 80, end: 92, center: 86 })
})

test('no gap without a placement, an arrow box or a body box', () => {
    assert.equal(computeArrowGap('', BODY, rect(180, 194, 12, 12)), null)
    assert.equal(computeArrowGap(null, BODY, rect(180, 194, 12, 12)), null)
    assert.equal(computeArrowGap('bottom', BODY, rect(180, 194, 0, 0)), null)
    assert.equal(computeArrowGap('bottom', { ...BODY, width: 0, height: 0 }, rect(180, 194, 12, 12)), null)
    assert.equal(computeArrowGap('diagonal', BODY, rect(180, 194, 12, 12)), null)
})

test('center: the rounded middle of the gap', () => {
    // 10.123 → 10.12 and 15.456 → 15.46 (rounded like start and end); middle 12.79.
    const gap = computeArrowGap('bottom', BODY, rect(110.123, 194, 5.333, 12))
    assert.equal(gap.start, 10.12)
    assert.equal(gap.end, 15.46)
    assert.equal(gap.center, 12.79)
})

test('the reposition handler publishes --popover-arrow-center (popover origin, overlay motion §5.3)', () => {
    const source = readFileSync(new URL('./glassArrowGap.js', import.meta.url), 'utf8')
    const handler = source.slice(source.indexOf('function onReposition('), source.indexOf('let installed'))
    assert.ok(handler.includes("host.style.setProperty('--popover-arrow-center', `${gap.center}px`)"), 'the handler sets --popover-arrow-center')
    const noGap = handler.slice(handler.indexOf('if (!gap)'), handler.indexOf('host.dataset.glassArrow = gap.side'))
    assert.ok(!noGap.includes('--popover-arrow-center'), 'the no-gap branch only drops data-glass-arrow')
})
