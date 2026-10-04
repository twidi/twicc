// frontend/src/extensions/collapsedBar.js
// Shared separator bar for collapsed unchanged lines in diffs (used by smartCollapseUnchanged
// and patchEllipsis). A hatched band: the top half reveals lines above the bar, the bottom half
// reveals lines below it, and a centred pill reveals everything. Texts shorten with the width.

import { EditorView } from '@codemirror/view'

const SVG_NS = 'http://www.w3.org/2000/svg'

/** Height (px) of each bar kind, for the widgets' `estimatedHeight`. */
export const BAR_HEIGHT = 52
export const BAR_HEIGHT_SOLO = 34

// "Fold" glyphs: the rule is the bar, the chevron points to where the lines will appear.
const GLYPHS = {
    up: 'M4 20h16M7 14l5-5 5 5',
    down: 'M4 4h16M7 10l5 5 5-5',
}

function el(tag, className, text) {
    const node = document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
}

function glyph(direction) {
    const svg = document.createElementNS(SVG_NS, 'svg')
    svg.setAttribute('viewBox', '0 0 24 24')
    svg.setAttribute('aria-hidden', 'true')
    const path = document.createElementNS(SVG_NS, 'path')
    path.setAttribute('d', GLYPHS[direction])
    svg.appendChild(path)
    return svg
}

/** A span per text tier; CSS shows only the one matching the bar's data attribute. */
function tiers(prefix, texts) {
    return texts.map((text, i) => el('span', `cm-cb-tier cm-cb-${prefix}${i}`, text))
}

function halfButton(direction, step, onClick) {
    const word = direction === 'up' ? 'above' : 'below'
    const button = el('button', `cm-cb-half cm-cb-${direction}`)
    button.type = 'button'
    button.title = `Show ${step} lines ${word} the bar`
    button.setAttribute('aria-label', button.title)
    button.append(glyph(direction), ...tiers('h', [`Show ${step} lines ${word}`, `+${step} ${word}`, `+${step}`]))
    button.addEventListener('click', e => {
        e.stopPropagation()
        onClick(e.currentTarget)
    })
    return button
}

function halfPlaceholder(direction) {
    const div = el('div', `cm-cb-half cm-cb-${direction} cm-cb-off`)
    div.setAttribute('aria-hidden', 'true')
    return div
}

function preview(direction) {
    return el('div', `cm-cb-preview cm-cb-preview-${direction}`, '+ lines appear here')
}

function intersects(a, b, margin) {
    return a.left < b.right + margin && a.right > b.left - margin
        && a.top < b.bottom + margin && a.bottom > b.top - margin
}

/**
 * Pick the richest text tiers that do not collide, from the real measured widths:
 * the pill text shortens first, then the halves', alternately (pill 0→1, halves 0→1, pill 1→2…).
 */
function fit(bar) {
    const pill = bar.querySelector('.cm-cb-all')
    const halves = [...bar.querySelectorAll('.cm-cb-half:not(.cm-cb-off)')]
    let h = 0
    let p = 0
    const apply = () => {
        bar.dataset.h = h
        bar.dataset.p = p
    }
    const fits = () => {
        const barRect = bar.getBoundingClientRect()
        const pillRect = pill.getBoundingClientRect()
        if (pillRect.width > barRect.width - 12 + 0.5) return false
        return halves.every(half => {
            const rect = half.getBoundingClientRect()
            const visible = [...half.children].filter(child => getComputedStyle(child).display !== 'none')
            const right = Math.max(...visible.map(child => child.getBoundingClientRect().right))
            if (right > barRect.right - 8) return false
            const isUp = half.classList.contains('cm-cb-up')
            // The label sits in the outer corner of its half: only that strip can touch the pill.
            return !intersects({
                left: rect.left, right,
                top: isUp ? rect.top + 6 : rect.bottom - 20,
                bottom: isUp ? rect.top + 20 : rect.bottom - 6,
            }, pillRect, 4)
        })
    }
    apply()
    for (let guard = 0; guard < 8 && !fits(); guard++) {
        if (p < 3 && (p <= h || h === 2)) p++
        else if (h < 2) h++
        else break
        apply()
    }
}

/** Re-fit the bar whenever its width changes. The observer is released by `destroyBar`. */
function observe(bar) {
    const observer = new ResizeObserver(() => fit(bar))
    observer.observe(bar)
    bar._cbObserver = observer
}

/** Release what `createCollapsedBar` / `createEllipsisBar` set up. */
export function destroyBar(bar) {
    bar._cbObserver?.disconnect()
    bar._cbObserver = null
}

/**
 * Build the interactive bar.
 *
 * @param {Object} config
 * @param {number} config.lines - Hidden lines.
 * @param {boolean} config.canShowAbove - Offer the "lines above the bar" button.
 * @param {boolean} config.canShowBelow - Offer the "lines below the bar" button.
 * @param {number} config.step - Lines revealed per partial click.
 * @param {(action: 'above'|'below'|'all', target: HTMLElement) => void} config.onAction
 */
export function createCollapsedBar({ lines, canShowAbove, canShowBelow, step, onAction }) {
    const solo = !canShowAbove && !canShowBelow
    const bar = el('div', `cm-collapsedBar${solo ? ' cm-cb-solo' : ''}`)
    bar.dataset.h = 0
    bar.dataset.p = 0

    if (!solo) {
        bar.append(
            canShowAbove ? halfButton('up', step, target => onAction('above', target)) : halfPlaceholder('up'),
            canShowBelow ? halfButton('down', step, target => onAction('below', target)) : halfPlaceholder('down'),
            preview('up'),
            preview('down'),
        )
    }

    const mid = el('div', 'cm-cb-mid')
    const all = el('button', 'cm-cb-all')
    all.type = 'button'
    all.title = `Show all ${lines} unchanged lines`
    all.setAttribute('aria-label', all.title)
    all.append(...tiers('p', [
        `Show all ${lines} unchanged lines`,
        `+${lines} unchanged lines`,
        `Show all ${lines}`,
        `+${lines}`,
    ]))
    all.addEventListener('click', e => {
        e.stopPropagation()
        onAction('all', e.currentTarget)
    })
    mid.appendChild(all)
    bar.appendChild(mid)

    observe(bar)
    return bar
}

/** Build the static bar of patch-only diffs: the same band, a centred "···" pill, no action. */
export function createEllipsisBar() {
    const bar = el('div', 'cm-collapsedBar cm-cb-solo')
    const mid = el('div', 'cm-cb-mid')
    mid.appendChild(el('span', 'cm-cb-all cm-cb-static', '···'))
    bar.appendChild(mid)
    return bar
}

// ─── Theme ─────────────────────────────────────────────────────────────────

const ACCENT = 'var(--wa-color-brand-60)'
const mix = (percent, base = 'transparent') => `color-mix(in oklab, ${ACCENT} ${percent}%, ${base})`
const HATCH = `repeating-linear-gradient(135deg, ${mix(9)} 0 6px, transparent 6px 12px)`
const PREVIEW_HATCH = `repeating-linear-gradient(135deg, ${mix(16)} 0 6px, transparent 6px 12px)`

/** Hide every text tier of `prefix` except the one named by the bar's `data-<prefix>` attribute. */
function tierRules(prefix, count) {
    const rules = {}
    for (let active = 0; active < count; active++) {
        for (let other = 0; other < count; other++) {
            if (other === active) continue
            rules[`.cm-collapsedBar[data-${prefix}="${active}"] .cm-cb-${prefix}${other}`] = { display: 'none' }
        }
    }
    return rules
}

export const collapsedBarTheme = EditorView.baseTheme({
    '.cm-collapsedBar': {
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'stretch',
        height: `${BAR_HEIGHT}px`,
        whiteSpace: 'nowrap',
        cursor: 'default',
        color: 'var(--wa-color-text-quiet)',
        font: '12px/1 var(--wa-font-family-body, system-ui, sans-serif)',
        background: `${HATCH}, ${mix(4, 'var(--wa-color-surface-default)')}`,
        borderBlock: `1px dashed ${mix(40)}`,
    },
    '.cm-collapsedBar.cm-cb-solo': { height: `${BAR_HEIGHT_SOLO}px` },

    // Reset the native button chrome; the classed rules below out-rank it (3 classes vs 2 + 1 tag).
    '.cm-collapsedBar button': {
        appearance: 'none', background: 'none', border: '0', margin: '0', padding: '0',
        font: 'inherit', color: 'inherit', cursor: 'pointer', minWidth: '0',
        display: 'inline-flex', alignItems: 'center', gap: '5px',
        '-webkit-tap-highlight-color': 'transparent',
    },
    '.cm-collapsedBar button:focus-visible': { outline: `2px solid ${ACCENT}`, outlineOffset: '-2px' },
    '.cm-collapsedBar svg': {
        width: '14px', height: '14px', flex: 'none', fill: 'none', stroke: 'currentColor',
        strokeWidth: '2.4', strokeLinecap: 'round', strokeLinejoin: 'round',
    },

    // Halves: the top one reveals lines above the bar, the bottom one lines below it.
    '.cm-collapsedBar .cm-cb-half': {
        flex: '1 1 0', width: '100%', padding: '0 12px', justifyContent: 'flex-start',
        transition: 'background-color .12s',
    },
    '.cm-collapsedBar .cm-cb-up': { alignItems: 'flex-start', paddingTop: '6px' },
    '.cm-collapsedBar .cm-cb-down': { alignItems: 'flex-end', paddingBottom: '6px' },
    '.cm-collapsedBar .cm-cb-half:hover': { color: ACCENT, background: mix(14) },
    '.cm-collapsedBar .cm-cb-half:active': { background: mix(24) },
    '.cm-collapsedBar .cm-cb-off': { cursor: 'default', pointerEvents: 'none' },

    // Centred pill (both axes), over a dashed line marking the middle.
    '.cm-collapsedBar .cm-cb-mid': {
        position: 'absolute', inset: '0', display: 'grid', placeItems: 'center',
        pointerEvents: 'none', paddingInline: '6px',
    },
    '.cm-collapsedBar:not(.cm-cb-solo) .cm-cb-mid::before': {
        content: '""', position: 'absolute', insetInline: '0', top: '50%', borderTop: `1px dashed ${mix(40)}`,
    },
    '.cm-collapsedBar .cm-cb-all': {
        position: 'relative', pointerEvents: 'auto', justifyContent: 'center', height: '22px', padding: '0 12px',
        borderRadius: '999px', maxWidth: '100%', overflow: 'hidden',
        background: 'var(--wa-color-surface-raised)', border: `1px solid ${mix(40, 'var(--wa-color-surface-border)')}`,
        boxShadow: '0 1px 2px color-mix(in oklab, #000 12%, transparent)',
    },
    '.cm-collapsedBar .cm-cb-all:hover': { color: ACCENT, borderColor: ACCENT, background: mix(10, 'var(--wa-color-surface-raised)') },
    '.cm-collapsedBar .cm-cb-all:active': { transform: 'translateY(1px)' },
    '.cm-collapsedBar .cm-cb-static': { display: 'inline-flex', alignItems: 'center', pointerEvents: 'none' },

    // Text tiers, chosen by fit() through the bar's data attributes.
    '.cm-collapsedBar .cm-cb-tier': { fontVariantNumeric: 'tabular-nums' },
    ...tierRules('h', 3),
    ...tierRules('p', 4),

    // Hover preview: a hatched strip where the lines would appear (above / below the bar).
    '.cm-collapsedBar .cm-cb-preview': {
        display: 'none', position: 'absolute', insetInline: '0', height: '26px', zIndex: '3', pointerEvents: 'none',
        paddingInline: '10px', textAlign: 'right', font: '600 10.5px/24px var(--wa-font-family-body, system-ui, sans-serif)',
        color: ACCENT, background: `${PREVIEW_HATCH}, ${mix(9, 'var(--wa-color-surface-default)')}`,
    },
    '.cm-collapsedBar .cm-cb-preview-up': { bottom: '100%', borderTop: `1px dashed ${ACCENT}` },
    '.cm-collapsedBar .cm-cb-preview-down': { top: '100%', borderBottom: `1px dashed ${ACCENT}` },
    '.cm-collapsedBar:has(.cm-cb-up:is(:hover, :focus-visible)) .cm-cb-preview-up': { display: 'block' },
    '.cm-collapsedBar:has(.cm-cb-down:is(:hover, :focus-visible)) .cm-cb-preview-down': { display: 'block' },
})
