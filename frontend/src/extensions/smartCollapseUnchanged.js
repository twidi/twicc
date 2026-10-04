// frontend/src/extensions/smartCollapseUnchanged.js
// Custom CodeMirror 6 extension for progressive expansion of unchanged lines in diffs.
// Replaces @codemirror/merge's built-in collapseUnchanged.

import { StateField, StateEffect } from '@codemirror/state'
import { EditorView, Decoration, WidgetType } from '@codemirror/view'
import { getChunks, mergeViewSiblings } from '@codemirror/merge'
import { BAR_HEIGHT, BAR_HEIGHT_SOLO, collapsedBarTheme, createCollapsedBar, destroyBar } from './collapsedBar'

// ─── State Effects ─────────────────────────────────────────────────────────

/** Reveal `count` lines from the top of the collapsed zone at `pos`. */
const expandTop = StateEffect.define({
    map: (value, change) => ({ ...value, pos: change.mapPos(value.pos) }),
})

/** Reveal `count` lines from the bottom of the collapsed zone at `pos`. */
const expandBottom = StateEffect.define({
    map: (value, change) => ({ ...value, pos: change.mapPos(value.pos) }),
})

/** Remove the collapsed zone at `pos` entirely. */
const expandAll = StateEffect.define({
    map: (value, change) => change.mapPos(value),
})

// ─── Position mapping (side-by-side sync) ──────────────────────────────────

/**
 * Map a document position from one side of a diff to the other.
 * Walks the chunk list, translating positions by tracking offsets between
 * each side's chunk boundaries. Same algorithm as the native internal mapPos.
 *
 * @param {number} pos - Position in the source document
 * @param {Chunk[]} chunks - Array of Chunk objects from getChunks()
 * @param {boolean} isA - Whether pos is in document A (true) or B (false)
 * @returns {number} Corresponding position in the other document
 */
function mapPos(pos, chunks, isA) {
    let startOur = 0, startOther = 0
    for (let i = 0; ; i++) {
        const next = i < chunks.length ? chunks[i] : null
        if (!next || (isA ? next.fromA : next.fromB) >= pos)
            return startOther + (pos - startOur)
        ;[startOur, startOther] = isA ? [next.toA, next.toB] : [next.toB, next.toA]
    }
}

// ─── Widget ────────────────────────────────────────────────────────────────

class SmartCollapseWidget extends WidgetType {
    /**
     * @param {number} lines - Total hidden lines
     * @param {boolean} canShowAbove - Offer the button revealing lines above the bar
     * @param {boolean} canShowBelow - Offer the button revealing lines below the bar
     * @param {number} step - Lines to reveal per click
     */
    constructor(lines, canShowAbove, canShowBelow, step) {
        super()
        this.lines = lines
        this.canShowAbove = canShowAbove
        this.canShowBelow = canShowBelow
        this.step = step
    }

    eq(other) {
        return this.lines === other.lines
            && this.canShowAbove === other.canShowAbove
            && this.canShowBelow === other.canShowBelow
            && this.step === other.step
    }

    toDOM(view) {
        // "Above" reveals the top of the collapsed zone, "below" its bottom; the bar sits between.
        const effects = {
            above: () => expandTop.of({ pos: 0, count: this.step }),
            below: () => expandBottom.of({ pos: 0, count: this.step }),
            all: () => expandAll.of(0),
        }
        return createCollapsedBar({
            lines: this.lines,
            canShowAbove: this.canShowAbove,
            canShowBelow: this.canShowBelow,
            step: this.step,
            onAction: (action, target) => this._dispatch(view, target, effects[action]()),
        })
    }

    destroy(dom) { destroyBar(dom) }

    /**
     * Dispatch an effect on the current view, and synchronize with sibling
     * in side-by-side mode. Resolves `pos` from the DOM element at dispatch time.
     */
    _dispatch(view, target, effectTemplate) {
        const pos = view.posAtDOM(target)

        // Build the actual effect with the resolved position
        let effect
        if (effectTemplate.is(expandAll)) {
            effect = expandAll.of(pos)
        } else if (effectTemplate.is(expandTop)) {
            effect = expandTop.of({ pos, count: effectTemplate.value.count })
        } else {
            effect = expandBottom.of({ pos, count: effectTemplate.value.count })
        }

        view.dispatch({ effects: effect })

        // Synchronize with sibling in side-by-side mode
        const siblings = mergeViewSiblings(view)
        if (siblings) {
            const chunkInfo = getChunks(view.state)
            if (chunkInfo) {
                const mappedPos = mapPos(pos, chunkInfo.chunks, chunkInfo.side === 'a')
                let siblingEffect
                if (effectTemplate.is(expandAll)) {
                    siblingEffect = expandAll.of(mappedPos)
                } else if (effectTemplate.is(expandTop)) {
                    siblingEffect = expandTop.of({ pos: mappedPos, count: effectTemplate.value.count })
                } else {
                    siblingEffect = expandBottom.of({ pos: mappedPos, count: effectTemplate.value.count })
                }
                const sibling = chunkInfo.side === 'a' ? siblings.b : siblings.a
                sibling.dispatch({ effects: siblingEffect })
            }
        }
    }

    ignoreEvent(e) { return e instanceof MouseEvent }

    get estimatedHeight() { return this.canShowAbove || this.canShowBelow ? BAR_HEIGHT : BAR_HEIGHT_SOLO }
}

// ─── StateField ────────────────────────────────────────────────────────────

/**
 * Build a new RangeSet by updating a single decoration identified by `fromPos`.
 * Used by expandTop/expandBottom to narrow a collapsed range.
 */
function adjustDecoration(deco, fromPos, state, adjustFn, step, minSize) {
    const result = []
    let found = false
    const cursor = deco.iter()
    while (cursor.value) {
        if (!found && cursor.from === fromPos) {
            found = true
            const fromLine = state.doc.lineAt(cursor.from).number
            const toLine = state.doc.lineAt(cursor.to).number
            const adjusted = adjustFn(fromLine, toLine)
            if (adjusted && adjusted.lines >= minSize) {
                const newFrom = state.doc.line(adjusted.from).from
                const newTo = state.doc.line(adjusted.to).to
                result.push({
                    from: newFrom,
                    to: newTo,
                    value: Decoration.replace({
                        widget: new SmartCollapseWidget(adjusted.lines, adjusted.lines > step, adjusted.lines > step, step),
                        block: true,
                    }),
                })
            }
            // else: lines < minSize → drop the decoration (auto-expand)
        } else {
            result.push({ from: cursor.from, to: cursor.to, value: cursor.value })
        }
        cursor.next()
    }
    return Decoration.set(result.map(r => r.value.range(r.from, r.to)))
}

function createSmartCollapsedRanges(margin, minSize, step) {
    return StateField.define({
        create() { return Decoration.none },
        update(deco, tr) {
            deco = deco.map(tr.changes)

            // Late initialization for side-by-side mode: MergeView adds ChunkField
            // via appendConfig AFTER state creation, so our init() returns Decoration.none.
            // Detect the first transaction where chunks become available and build decorations.
            if (deco.size === 0 && !getChunks(tr.startState) && getChunks(tr.state)) {
                return buildSmartCollapsedRanges(tr.state, margin, minSize, step)
            }

            for (const e of tr.effects) {
                if (e.is(expandAll)) {
                    deco = deco.update({ filter: from => from !== e.value })
                } else if (e.is(expandTop)) {
                    deco = adjustDecoration(deco, e.value.pos, tr.state, (fromLine, toLine) => {
                        const newFrom = fromLine + e.value.count
                        const lines = toLine - newFrom + 1
                        return { from: newFrom, to: toLine, lines }
                    }, step, minSize)
                } else if (e.is(expandBottom)) {
                    deco = adjustDecoration(deco, e.value.pos, tr.state, (fromLine, toLine) => {
                        const newTo = toLine - e.value.count
                        const lines = newTo - fromLine + 1
                        return { from: fromLine, to: newTo, lines }
                    }, step, minSize)
                }
            }
            return deco
        },
        provide: f => EditorView.decorations.from(f),
    })
}

// ─── Initialization ────────────────────────────────────────────────────────

/**
 * Compute the initial set of collapsed decorations from the diff chunks.
 * Same algorithm as native buildCollapsedRanges but uses public getChunks() API.
 */
function buildSmartCollapsedRanges(state, margin, minSize, step) {
    const chunkInfo = getChunks(state)
    if (!chunkInfo) return Decoration.none

    const { chunks, side } = chunkInfo
    const isA = side === 'a'
    const ranges = []
    let prevLine = 1

    for (let i = 0; ; i++) {
        const chunk = i < chunks.length ? chunks[i] : null
        const collapseFrom = i ? prevLine + margin : 1
        const collapseTo = chunk
            ? state.doc.lineAt(isA ? chunk.fromA : chunk.fromB).number - 1 - margin
            : state.doc.lines
        const lines = collapseTo - collapseFrom + 1

        if (lines >= minSize) {
            ranges.push(
                Decoration.replace({
                    widget: new SmartCollapseWidget(lines, lines > step, lines > step, step),
                    block: true,
                }).range(
                    state.doc.line(collapseFrom).from,
                    state.doc.line(collapseTo).to,
                )
            )
        }

        if (!chunk) break
        prevLine = state.doc.lineAt(Math.min(state.doc.length, isA ? chunk.toA : chunk.toB)).number
    }

    return Decoration.set(ranges)
}

// ─── Entry point ───────────────────────────────────────────────────────────

/**
 * Smart collapse for unchanged lines in diffs.
 * Replaces @codemirror/merge's built-in collapseUnchanged with
 * progressive expansion (show N lines top/bottom, or show all).
 *
 * @param {Object} config
 * @param {number} [config.margin=3] - Context lines around each changed chunk.
 * @param {number} [config.minSize=4] - Minimum lines to trigger collapse.
 * @param {number} [config.step=20]   - Lines revealed per partial expand click.
 * @returns {Extension}
 */
export function smartCollapseUnchanged({ margin = 3, minSize = 4, step = 20 } = {}) {
    const field = createSmartCollapsedRanges(margin, minSize, step)
    return [
        field.init(state => buildSmartCollapsedRanges(state, margin, minSize, step)),
        collapsedBarTheme,
    ]
}
