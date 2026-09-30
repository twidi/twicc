// Run with: node --test src/utils/footerMotion.test.js (from the frontend dir)
// Footer blocks motion (visual refresh step 7d, docs/plans/2026-09-30-footer-blocks-motion-design.md
// §4.1): the pure helpers of the controller.
import test from 'node:test'
import assert from 'node:assert/strict'

import {
    FOOTER_MOTION,
    fadeKeyframes,
    footerMotionTimings,
    heightKeyframes,
    insetKeyframes,
    paddingBoxOffsets,
    restoreKeyframes,
    shouldAnimate,
    tokenMs,
} from './footerMotion.js'

test('FOOTER_MOTION holds the motion.css values', () => {
    assert.deepEqual({ ...FOOTER_MOTION }, {
        heightMs: 380,
        fadeMs: 200,
        heightEasing: 'cubic-bezier(.25, .46, .45, .94)',
        fadeEasing: 'ease-in-out',
    })
})

test('tokenMs parses ms and s, falls back on empty, garbage and zero', () => {
    assert.equal(tokenMs('380ms', 1), 380)
    assert.equal(tokenMs(' 0.2s ', 1), 200)
    assert.equal(tokenMs('', 380), 380)
    assert.equal(tokenMs('   ', 380), 380)
    assert.equal(tokenMs(undefined, 200), 200)
    assert.equal(tokenMs('fast', 200), 200)
    assert.equal(tokenMs('0ms', 380), 380)
    assert.equal(tokenMs('-5ms', 380), 380)
})

test('footerMotionTimings reads the tokens, falls back per value, keeps ease-in-out for fades', () => {
    const values = { '--motion-dur-3': '400ms', '--motion-dur-2': '0.25s', '--motion-ease-out-height': ' ease-out ' }
    assert.deepEqual(footerMotionTimings((name) => values[name] ?? ''), {
        heightMs: 400, fadeMs: 250, heightEasing: 'ease-out', fadeEasing: 'ease-in-out',
    })
    assert.deepEqual(footerMotionTimings(() => ''), { ...FOOTER_MOTION })
    assert.deepEqual(footerMotionTimings(() => 'garbage').heightMs, 380)
})

test('heightKeyframes: px heights, overflowY clip on both frames', () => {
    assert.deepEqual(heightKeyframes(12.5, 0), [
        { height: '12.5px', overflowY: 'clip' },
        { height: '0px', overflowY: 'clip' },
    ])
})

test('fadeKeyframes', () => {
    assert.deepEqual(fadeKeyframes(0, 1), [{ opacity: 0 }, { opacity: 1 }])
    assert.deepEqual(fadeKeyframes(1, 0), [{ opacity: 1 }, { opacity: 0 }])
})

test('insetKeyframes: the first frame carries from, the last the target', () => {
    assert.deepEqual(insetKeyframes({ top: 300, bottom: 4 }, { top: 0, bottom: 0 }), [
        { inset: '300px 0 4px 0' },
        { inset: '0px 0 0px 0' },
    ])
})

test('restoreKeyframes: inset from → target, the discrete properties on both frames', () => {
    const discrete = { position: 'absolute', zIndex: 2, height: 'auto', maxHeight: 'none' }
    assert.deepEqual(restoreKeyframes({ top: 0, bottom: 0 }, { top: 420, bottom: 60 }), [
        { inset: '0px 0 0px 0', ...discrete },
        { inset: '420px 0 60px 0', ...discrete },
    ])
})

test('shouldAnimate: disabled, hidden, sub-pixel delta → false; 0 → N and N → 0 → true', () => {
    const base = { enabled: true, visible: true }
    assert.equal(shouldAnimate({ ...base, fromPx: 10, toPx: 50 }), true)
    assert.equal(shouldAnimate({ ...base, enabled: false, fromPx: 10, toPx: 50 }), false)
    assert.equal(shouldAnimate({ ...base, visible: false, fromPx: 10, toPx: 50 }), false)
    assert.equal(shouldAnimate({ ...base, fromPx: 10, toPx: 10.8 }), false)
    assert.equal(shouldAnimate({ ...base, fromPx: 10, toPx: 11 }), true)
    assert.equal(shouldAnimate({ ...base, fromPx: 0, toPx: 40 }), true)
    assert.equal(shouldAnimate({ ...base, fromPx: 40, toPx: 0 }), true)
})

test('paddingBoxOffsets: measured against the padding box (a 1px border gives the same inset)', () => {
    const block = { top: 110, bottom: 480 }
    const plain = paddingBoxOffsets(block, { top: 100, bottom: 500 }, { clientTop: 0, clientHeight: 400, offsetHeight: 400 })
    assert.deepEqual(plain, { top: 10, bottom: 20 })
    // Same padding box, drawn inside a 1px border: the border box is 2px taller.
    const bordered = paddingBoxOffsets(block, { top: 99, bottom: 501 }, { clientTop: 1, clientHeight: 400, offsetHeight: 402 })
    assert.deepEqual(bordered, plain)
})
