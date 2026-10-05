// Run with: node --test src/utils/panelInsets.test.js (from the frontend dir)
import test from 'node:test'
import assert from 'node:assert/strict'

import { resolveLayout } from './layoutResolver.js'
import {
    CARD_CORNER_RADIUS,
    FLUSH_TOLERANCE_PX,
    NO_INSETS,
    frameClipPath,
    frameFlushCorners,
    innerEdges,
    insetRectStyle,
    visibleRect,
} from './panelInsets.js'

const TABS = [
    { id: 'main', label: 'Chat', fixedCenter: true },
    { id: 'files', label: 'Files' },
    { id: 'git', label: 'Git' },
    { id: 'terminal', label: 'Terminal' },
    { id: 'browser', label: 'Browser' },
]
const layout = (assignment, viewport, extra = {}) => resolveLayout({ tabs: TABS, assignment, viewport, ...extra })
const region = (render, id) => {
    const r = render.regions.find((x) => x.id === id)
    assert.ok(r, `region ${id} missing (got ${render.regions.map((x) => x.id).join(', ')})`)
    return r
}
const edgesOf = (render, id) => innerEdges(region(render, id), render.viewport)
const E = (left, top, right, bottom) => ({ left, top, right, bottom })

test('widescreen: left column, split right column, center, bottom under the center', () => {
    const r = layout({ files: 'left-top', git: 'right-top', terminal: 'right-bottom', browser: 'bottom-left' }, { w: 1600, h: 900 })
    assert.equal(r.mode, 'widescreen')
    assert.deepEqual(edgesOf(r, 'left-col'), E(false, false, true, false))
    assert.deepEqual(edgesOf(r, 'right-top'), E(true, false, false, true))
    assert.deepEqual(edgesOf(r, 'right-bottom'), E(true, true, false, false))
    assert.deepEqual(edgesOf(r, 'center'), E(true, false, true, true))
    assert.deepEqual(edgesOf(r, 'bottom'), E(true, true, true, false))
})

test('classic: full-width bottom under a center and a right column', () => {
    const r = layout({ files: 'right-top', terminal: 'bottom-left' }, { w: 850, h: 900 })
    assert.equal(r.mode, 'classic')
    assert.deepEqual(edgesOf(r, 'center'), E(false, false, true, true))
    assert.deepEqual(edgesOf(r, 'right-col'), E(true, false, false, true))
    assert.deepEqual(edgesOf(r, 'bottom'), E(false, true, false, false))
})

test('merged siblings: one right region spanning the full height', () => {
    const r = layout({ git: 'right-top', terminal: 'right-bottom' }, { w: 1600, h: 500 })
    const col = region(r, 'right-col')
    assert.equal(col.merged, true)
    assert.deepEqual(innerEdges(col, r.viewport), E(true, false, false, false))
})

test('split left column and split bottom', () => {
    const left = layout({ files: 'left-top', git: 'left-bottom' }, { w: 1600, h: 900 })
    assert.deepEqual(edgesOf(left, 'left-top'), E(false, false, true, true))
    assert.deepEqual(edgesOf(left, 'left-bottom'), E(false, true, true, false))
    const bottom = layout({ terminal: 'bottom-left', browser: 'bottom-right' }, { w: 1600, h: 900 })
    assert.deepEqual(edgesOf(bottom, 'bottom-left'), E(false, true, true, false))
    assert.deepEqual(edgesOf(bottom, 'bottom-right'), E(true, true, false, false))
    assert.deepEqual(edgesOf(bottom, 'center'), E(false, false, false, true))
})

test('a rail on each edge makes the facing center edge inner', () => {
    const left = layout({ files: 'left-top' }, { w: 1600, h: 900 }, { collapsed: ['left-top'] })
    assert.ok(left.gutters.some((g) => g.edge === 'left'))
    assert.deepEqual(edgesOf(left, 'center'), E(true, false, false, false))
    const right = layout({ files: 'right-top' }, { w: 1600, h: 900 }, { collapsed: ['right-top'] })
    assert.ok(right.gutters.some((g) => g.edge === 'right'))
    assert.deepEqual(edgesOf(right, 'center'), E(false, false, true, false))
    const bottom = layout({ terminal: 'bottom-left' }, { w: 1600, h: 900 }, { collapsed: ['bottom-left'] })
    assert.ok(bottom.gutters.some((g) => g.edge === 'bottom'))
    assert.deepEqual(edgesOf(bottom, 'center'), E(false, false, false, true))
})

test('bottom overlay: stops above the bottom rail', () => {
    // Height below centerMinH + bottomMinH (370) → the bottom becomes an overlay over a rail.
    const r = layout({ terminal: 'bottom-left' }, { w: 1600, h: 300 })
    const ov = r.overlays.find((o) => o.edge === 'bottom')
    assert.ok(ov, 'bottom overlay expected')
    assert.deepEqual(innerEdges(ov.rect, r.viewport), E(false, true, false, true))
    assert.deepEqual(edgesOf(r, 'center'), E(false, false, false, true))
})

test('overlay rect: inset from the rail and from the escape strip', () => {
    const r = layout({ files: 'right-top' }, { w: 700, h: 900 })
    const ov = r.overlays.find((o) => o.edge === 'right')
    assert.ok(ov, 'right overlay expected')
    assert.deepEqual(innerEdges(ov.rect, r.viewport), E(true, false, true, false))
    assert.deepEqual(edgesOf(r, 'center'), E(false, false, true, false))
})

test('maximized and tabs mode: one full region, no inner edge', () => {
    const max = layout({ files: 'right-top' }, { w: 1600, h: 900 }, { maximized: ['center'] })
    assert.deepEqual(innerEdges(max.regions[0], max.viewport), NO_INSETS)
    const tabs = layout({ files: 'right-top' }, { w: 480, h: 900 })
    assert.equal(tabs.mode, 'tabs')
    assert.deepEqual(innerEdges(tabs.regions[0], tabs.viewport), NO_INSETS)
})

test('innerEdges tolerates sub-pixel rects on the boundary', () => {
    assert.deepEqual(innerEdges({ x: 0.3, y: 0, w: 999.8, h: 600 }, { w: 1000, h: 600 }), NO_INSETS)
})

test('insetRectStyle moves only the inner edges, in CSS', () => {
    assert.deepEqual(insetRectStyle({ x: 10, y: 0, w: 100, h: 50 }, E(true, false, false, true)), {
        left: 'calc(10px + var(--panel-half-gap) * 1)',
        top: 'calc(0px + var(--panel-half-gap) * 0)',
        width: 'calc(100px - var(--panel-half-gap) * 1)',
        height: 'calc(50px - var(--panel-half-gap) * 1)',
    })
    assert.deepEqual(insetRectStyle({ x: 0, y: 0, w: 10, h: 10 }, E(true, true, true, true)).width,
        'calc(10px - var(--panel-half-gap) * 2)')
})

const CARD = { x: 100, y: 100, width: 400, height: 300 }
const C = (tl, tr, br, bl) => ({ tl, tr, br, bl })

test('frameFlushCorners: frame filling the card inside a 1px or 4px border', () => {
    assert.deepEqual(frameFlushCorners({ x: 101, y: 101, width: 398, height: 298 }, CARD), C(true, true, true, true))
    assert.deepEqual(frameFlushCorners({ x: 104, y: 104, width: 392, height: 292 }, CARD), C(true, true, true, true))
})

test('frameFlushCorners: frame under a toolbar reaches only the bottom corners', () => {
    assert.deepEqual(frameFlushCorners({ x: 101, y: 150, width: 398, height: 249 }, CARD), C(false, false, true, true))
})

test('frameFlushCorners: one corner, and the tolerance boundary', () => {
    assert.deepEqual(frameFlushCorners({ x: 101, y: 200, width: 200, height: 199 }, CARD), C(false, false, false, true))
    const at = FLUSH_TOLERANCE_PX
    assert.deepEqual(frameFlushCorners({ x: 100 + at, y: 100 + at, width: 400 - 2 * at, height: 300 - 2 * at }, CARD), C(true, true, true, true))
    const past = FLUSH_TOLERANCE_PX + 1
    assert.deepEqual(frameFlushCorners({ x: 100 + past, y: 100 + past, width: 400 - 2 * past, height: 300 - 2 * past }, CARD), C(false, false, false, false))
})

test('visibleRect: intersection with the clip container, or the frame itself', () => {
    const frame = { x: 101, y: 101, width: 398, height: 600 }
    assert.deepEqual(visibleRect(frame, null), frame)
    assert.deepEqual(visibleRect(frame, { x: 101, y: 101, width: 398, height: 298 }), { x: 101, y: 101, width: 398, height: 298 })
    // Judged on the visible rect, a frame scrolled past the card bottom still gets its bottom corners.
    assert.deepEqual(frameFlushCorners(visibleRect(frame, { x: 101, y: 101, width: 398, height: 298 }), CARD), C(true, true, true, true))
    assert.deepEqual(frameFlushCorners(frame, CARD), C(true, true, false, false))
})

test('frameClipPath: none, clip only, corners only, both', () => {
    const frame = { x: 0, y: 0, width: 100, height: 100 }
    assert.equal(frameClipPath(frame, null, null), null)
    assert.equal(frameClipPath(frame, null, C(false, false, false, false)), null)
    assert.equal(frameClipPath(frame, { x: 0, y: 10, width: 100, height: 80 }, null), 'inset(10px 0px 10px 0px)')
    assert.equal(frameClipPath(frame, null, C(false, false, true, true)),
        `inset(0px 0px 0px 0px round 0 0 ${CARD_CORNER_RADIUS} ${CARD_CORNER_RADIUS})`)
    assert.equal(frameClipPath(frame, { x: 5, y: 0, width: 95, height: 100 }, C(true, false, false, false)),
        `inset(0px 0px 0px 5px round ${CARD_CORNER_RADIUS} 0 0 0)`)
})
