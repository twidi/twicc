<script setup>
// Provider × model × effort matrix. Data is assembled provider-agnostically in
// useSessionAgentSettings.matrixBlocks: rows are a provider's models, columns
// the shared effort ladder, each cell picks provider + model + effort in one
// click. Layout: one grid so every block's effort columns stay aligned; each
// provider's name runs vertically (read bottom-to-top) down the left of its own
// sub-matrix, with a divider between providers. Disabled cells are efforts the
// (provider, model) pair doesn't support; a dot marks the default cell; the
// current selection is highlighted; "Show older models" reveals non-latest rows.
import { computed, ref, useId, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { useBenchmarkTaskStore } from '../../stores/benchmarkTask'
import HoverInfoPanel from '../ui/HoverInfoPanel.vue'
import ProviderIcon from '../ui/ProviderIcon.vue'
import { effortIconSrc } from '../../utils/effortIcon'
import { formatBenchmarkDetails } from '../../utils/benchmarkScores'

const props = defineProps({
    // [{ provider, label, icon, isCurrent, rows: [{ model, label, isLatest,
    //    cells: [{ effort, enabled, selected, isDefault, isProviderDefault,
    //             score, benchmark }] }] }]
    // score is the benchmark score (integer 0..100) or null ("?"); benchmark is
    // { row, scored } (the snapshot row and its computed score entry) or null,
    // feeding the per-cell details panel.
    blocks: { type: Array, default: () => [] },
    // [{ effort, label }] — shared columns across every block
    effortColumns: { type: Array, default: () => [] },
})

const emit = defineEmits(['select'])

// Unique prefix for per-cell tooltip anchor ids (several matrices may coexist —
// the message popover and the Settings panel).
const uid = useId()

// Stable id shared by a cell's button and its "Benchmark data" tooltip. Model
// aliases carry dots (e.g. "opus-4.5"); neutralise anything outside [A-Za-z0-9_-]
// so the id stays safe for wa-tooltip's ``for`` anchor resolution.
function cellId(provider, model, effort) {
    const key = `${provider}-${model}-${effort}`.replace(/[^A-Za-z0-9_-]/g, '_')
    return `${uid}-cell-${key}`
}

const showOldModels = ref(false)

const hasOldModels = computed(() =>
    props.blocks.some(b => b.rows.some(r => !r.isLatest)),
)

// Whether any cell is flagged as the default (dot). The per-session popover
// always has one; the per-provider defaults editor passes no default cell (the
// selected cell IS the default there), so the "default" legend entry is hidden.
const hasDefaultCell = computed(() =>
    props.blocks.some(b => b.rows.some(r => r.cells.some(c => c.isDefault))),
)

// Assign every element an explicit grid row/column so blocks share one grid
// (aligned effort columns) while each provider label spans its own rows. Column
// 1 = vertical provider label, column 2 = model label, columns 3.. = efforts;
// row 1 = effort headers. A non-latest row stays visible when it holds the
// current selection, so the highlighted cell never hides while collapsed.
const layout = computed(() => {
    const efforts = props.effortColumns.map((c, i) => ({ ...c, col: i + 3 }))
    let row = 2
    const blocks = props.blocks.map((block, bi) => {
        const dividerRow = bi > 0 ? row++ : null
        const vis = block.rows.filter(
            r => r.isLatest || showOldModels.value || r.cells.some(c => c.selected),
        )
        const labelRow = row
        const rows = vis.map(r => {
            const built = {
                model: r.model,
                name: r.name,
                version: r.version,
                isLatest: r.isLatest,
                row,
                cells: r.cells.map((cell, ci) => ({ ...cell, col: ci + 3 })),
            }
            row += 1
            return built
        })
        return {
            provider: block.provider,
            label: block.label,
            icon: block.icon,
            dividerRow,
            labelRow,
            labelSpan: Math.max(1, rows.length),
            rows,
        }
    })
    return { efforts, blocks }
})

const gridStyle = computed(() => ({
    gridTemplateColumns: `auto auto repeat(${props.effortColumns.length}, minmax(2.25rem, 1fr))`,
}))

function onCellClick(provider, model, cell) {
    // Swallow the click a long-press synthesised, so long-press (which opens the
    // panel) never also selects the cell.
    if (longPressFired) { longPressFired = false; return }
    if (!cell.enabled) return
    emit('select', { provider, model, effort: cell.effort })
}

// One "Benchmark data" tooltip per visible enabled cell: the score details for
// the current task controls when the couple has a score, else a short "no data"
// note (the "?" cells). Disabled (unsupported-effort) cells get none. Built off
// ``layout`` so it only covers the currently-rendered cells.
const cellTips = computed(() => {
    const effortLabel = (effort) => props.effortColumns.find(c => c.effort === effort)?.label ?? String(effort)
    const tips = []
    for (const block of layout.value.blocks) {
        for (const r of block.rows) {
            const modelLabel = [r.name, r.version].filter(Boolean).join(' ')
            for (const cell of r.cells) {
                if (!cell.enabled) continue
                const hasData = cell.score != null && !!cell.benchmark
                tips.push({
                    id: cellId(block.provider, r.model, cell.effort),
                    title: `Benchmark data for ${modelLabel} × ${effortLabel(cell.effort)}`,
                    details: hasData ? formatBenchmarkDetails(cell.benchmark.row, cell.benchmark.scored, taskStore.taskType) : null,
                })
            }
        }
    }
    return tips
})

const tipsById = computed(() => {
    const m = new Map()
    for (const t of cellTips.value) m.set(t.id, t)
    return m
})

// ─── Floating "Benchmark data" panel ──────────────────────────────────────
// One persistent HoverInfoPanel shows the hovered / long-pressed cell's details.
// Desktop: it follows the pointer (always below it). Touch: long-press toggles it
// below the cell. All the geometry lives here; the panel just renders at left/top.
const settingsStore = useSettingsStore()
const taskStore = useBenchmarkTaskStore()
const gridRef = ref(null)
const panelRef = ref(null)

const activeTip = ref(null)
const panelLeft = ref(0)
const panelTop = ref(0)

const PANEL_OFFSET = 14 // gap between the pointer and the panel (cursor mode)
const VIEWPORT_MARGIN = 8

// Place the panel: prefer RIGHT + BELOW; flip to LEFT / ABOVE only when that side
// would run past the visible edge (so "right + below by default" holds while
// there's room, and it never gets cut off). Clamp inside the viewport either way.
// The anchor carries both candidates per axis:
//   leftIfRight  — panel left when placed to the right
//   rightIfLeft  — x its RIGHT edge aligns to when flipped left
//   topIfBelow   — panel top when placed below
//   bottomIfAbove— y its BOTTOM edge aligns to when flipped above
let lastAnchor = null

function positionPanel(anchor) {
    lastAnchor = anchor
    applyPosition()
}

// Recompute from the stored anchor with the panel's CURRENT measured size (or a
// rough fallback before the first paint). Re-run on nextTick after a show so the
// first frame — and every touch long-press — lands with the real dimensions.
function applyPosition() {
    if (!lastAnchor) return
    const { leftIfRight, rightIfLeft, topIfBelow, bottomIfAbove } = lastAnchor
    const el = panelRef.value?.rootEl
    const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    const w = el?.offsetWidth || 20 * rootPx
    const h = el?.offsetHeight || (activeTip.value?.details ? 15 * rootPx : 4.5 * rootPx)
    const vw = window.innerWidth
    const vh = window.innerHeight
    const M = VIEWPORT_MARGIN

    let left = (leftIfRight + w <= vw - M) ? leftIfRight : (rightIfLeft - w)
    left = Math.max(M, Math.min(left, vw - w - M))
    let top = (topIfBelow + h <= vh - M) ? topIfBelow : (bottomIfAbove - h)
    top = Math.max(M, Math.min(top, vh - h - M))

    panelLeft.value = left
    panelTop.value = top
}

function tipForEvent(e) {
    const btn = e.target.closest?.('.matrix-cell')
    const id = btn?.dataset.tipId
    return { btn: id ? btn : null, tip: id ? (tipsById.value.get(id) ?? null) : null }
}

// Small hide delay: crossing the gap between cells (or a header) briefly lands on
// a non-cell — without the delay the panel would blink off then on. Re-entering
// any cell within the delay cancels the pending hide.
let hideTimer = null
function scheduleHide() {
    if (hideTimer) return
    hideTimer = setTimeout(() => { hideTimer = null; activeTip.value = null }, 120)
}
function cancelHide() {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null }
}

// ── Desktop: the panel follows the pointer ────────────────────────────────
function onGridPointerMove(e) {
    if (settingsStore.isTouchDevice) return
    const { tip } = tipForEvent(e)
    if (!tip) { scheduleHide(); return }
    cancelHide()
    activeTip.value = tip
    positionPanel({
        leftIfRight: e.clientX + PANEL_OFFSET,
        rightIfLeft: e.clientX - PANEL_OFFSET,
        topIfBelow: e.clientY + PANEL_OFFSET,
        bottomIfAbove: e.clientY - PANEL_OFFSET,
    })
}

function onGridPointerLeave() {
    if (settingsStore.isTouchDevice) return
    scheduleHide()
}

// ── Touch: long-press toggles an anchored panel ───────────────────────────
let lpTimer = null
let lpStart = null
let lpTarget = null
let longPressFired = false

function clearLongPress() {
    if (lpTimer) { clearTimeout(lpTimer); lpTimer = null }
    lpStart = null
    lpTarget = null
}

function onTouchStart(e) {
    longPressFired = false
    const { btn, tip } = tipForEvent(e)
    if (!tip) return
    const t = e.touches[0]
    lpStart = { x: t.clientX, y: t.clientY }
    lpTarget = { btn, tip }
    clearTimeout(lpTimer)
    lpTimer = setTimeout(fireLongPress, 450)
}

function onTouchMove(e) {
    if (!lpStart) return
    const t = e.touches[0]
    if (Math.abs(t.clientX - lpStart.x) > 10 || Math.abs(t.clientY - lpStart.y) > 10) clearLongPress()
}

function onTouchEnd() {
    clearLongPress()
}

function fireLongPress() {
    lpTimer = null
    const target = lpTarget
    if (!target?.tip) return
    longPressFired = true // swallow the ensuing click so the cell isn't selected
    // A long-press on the cell that already owns the panel closes it.
    if (activeTip.value?.id === target.tip.id) { activeTip.value = null; return }
    const r = target.btn.getBoundingClientRect()
    activeTip.value = target.tip
    positionPanel({
        leftIfRight: r.left,
        rightIfLeft: r.right,
        topIfBelow: r.bottom + 8,
        bottomIfAbove: r.top - 8,
    })
}

// Close an open (touch) panel when tapping outside the matrix, or on any scroll.
function onDocPointerDown(e) {
    if (activeTip.value && !gridRef.value?.contains(e.target)) activeTip.value = null
}
function onScroll() {
    if (activeTip.value) activeTip.value = null
}

// Keep the shown tip pointing at the fresh object after a recompute (task controls /
// show-older toggle); drop it if its cell is gone.
watch(cellTips, () => {
    if (activeTip.value) activeTip.value = tipsById.value.get(activeTip.value.id) ?? null
})

// Re-place with the real measured size once the (new) content has painted — the
// first show and every touch long-press otherwise use the fallback estimate.
watch(activeTip, (t) => { if (t) nextTick(applyPosition) })

onMounted(() => {
    document.addEventListener('pointerdown', onDocPointerDown, true)
    window.addEventListener('scroll', onScroll, true)
})
onBeforeUnmount(() => {
    clearLongPress()
    cancelHide()
    document.removeEventListener('pointerdown', onDocPointerDown, true)
    window.removeEventListener('scroll', onScroll, true)
})
</script>

<template>
    <div class="matrix">
        <div
            class="matrix-grid"
            ref="gridRef"
            :style="gridStyle"
            @mousemove="onGridPointerMove"
            @mouseleave="onGridPointerLeave"
            @touchstart.passive="onTouchStart"
            @touchmove.passive="onTouchMove"
            @touchend="onTouchEnd"
            @touchcancel="onTouchEnd"
            @contextmenu.prevent
        >
            <!-- Effort header row (col 1-2 empty corner, efforts from col 3) -->
            <div class="matrix-corner" style="grid-column: 1 / 3; grid-row: 1"></div>
            <div
                v-for="e in layout.efforts"
                :key="e.effort"
                class="matrix-col-header"
                :style="{ gridColumn: e.col, gridRow: 1 }"
            >
                <wa-icon
                    v-if="effortIconSrc(e.effort)"
                    auto-width
                    :src="effortIconSrc(e.effort)"
                    class="matrix-col-header-icon"
                ></wa-icon>
                <span>{{ e.label }}</span>
            </div>

            <template v-for="block in layout.blocks" :key="block.provider">
                <div
                    v-if="block.dividerRow"
                    class="matrix-divider"
                    :style="{ gridColumn: '1 / -1', gridRow: block.dividerRow }"
                ></div>

                <!-- Vertical provider label (read bottom-to-top), spanning its
                     block's rows; the icon rides at the bottom, upright. -->
                <div
                    class="matrix-vlabel"
                    :style="{ gridColumn: 1, gridRow: `${block.labelRow} / span ${block.labelSpan}` }"
                >
                    <span class="matrix-vlabel-text">{{ block.label }}</span>
                    <ProviderIcon
                        v-if="block.icon"
                        class="matrix-vlabel-icon"
                        :provider="block.provider"
                    />
                </div>

                <template v-for="r in block.rows" :key="r.model">
                    <div
                        class="matrix-row-header"
                        :class="{ old: !r.isLatest }"
                        :style="{ gridColumn: 2, gridRow: r.row }"
                    >
                        <span>{{ r.name }}</span>
                        <span v-if="r.version" class="matrix-model-version">{{ r.version }}</span>
                    </div>
                    <button
                        v-for="cell in r.cells"
                        :key="cell.effort"
                        :data-tip-id="cell.enabled ? cellId(block.provider, r.model, cell.effort) : undefined"
                        type="button"
                        class="matrix-cell"
                        :class="{ selected: cell.selected, unavailable: !cell.enabled, 'top-solid': cell.borderStyle === 'solid', 'top-dashed': cell.borderStyle === 'dashed', 'score-high': cell.enabled && cell.score != null && cell.score > 80 }"
                        :style="{ gridColumn: cell.col, gridRow: r.row, '--score-alpha': cell.enabled && cell.score != null ? cell.score / 100 : 0 }"
                        :disabled="!cell.enabled"
                        :aria-label="`${block.label} · ${r.name} ${r.version} · ${cell.effort}${cell.enabled ? ` · score ${cell.score ?? 'unknown'}` : ''}`"
                        :aria-pressed="cell.selected"
                        @click="onCellClick(block.provider, r.model, cell)"
                    >
                        <span v-if="cell.isDefault" class="matrix-default-dot"></span>
                        <span v-else-if="cell.isProviderDefault" class="matrix-default-dot hollow"></span>
                        <span v-if="cell.enabled" class="matrix-cell-score" :class="{ 'no-score': cell.score == null }">{{ cell.score ?? '?' }}</span>
                        <span v-if="cell.selected" class="matrix-cell-check">
                            <wa-icon name="check"></wa-icon>
                        </span>
                    </button>
                </template>
            </template>
        </div>

        <div class="matrix-footer">
            <button
                v-if="hasOldModels"
                type="button"
                class="matrix-old-toggle"
                @click="showOldModels = !showOldModels"
            >
                {{ showOldModels ? 'Hide older models' : 'Show older models' }}
            </button>
            <span class="matrix-legend">
                <span v-if="hasDefaultCell" class="matrix-legend-item"><span class="matrix-default-dot"></span> default</span>
                <span class="matrix-legend-item"><wa-icon class="matrix-legend-check" name="check"></wa-icon> selected</span>
            </span>
        </div>

        <!-- Single floating "Benchmark data" panel for the hovered / long-pressed
             cell. Teleported to <body> (see HoverInfoPanel); the geometry lives in
             this component's script. -->
        <HoverInfoPanel
            ref="panelRef"
            :visible="activeTip != null"
            :left="panelLeft"
            :top="panelTop"
        >
            <div v-if="activeTip" class="cell-tip">
                <div class="cell-tip-title">{{ activeTip.title }}</div>
                <template v-if="activeTip.details">
                    <div v-for="d in activeTip.details" :key="d.label" class="cell-tip-row">
                        <div class="cell-tip-metric">
                            <span class="cell-tip-label">{{ d.label }}</span>
                            <span class="cell-tip-value">{{ d.value }}</span>
                        </div>
                        <div class="cell-tip-desc">{{ d.description }}</div>
                    </div>
                </template>
                <div v-else class="cell-tip-empty">Artificial Analysis provides no usable data for this model &times; effort.</div>
            </div>
        </HoverInfoPanel>
    </div>
</template>

<style scoped>
.matrix {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.matrix-grid {
    display: grid;
    gap: 3px;
    align-items: stretch;
}

.matrix-col-header {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    font-size: var(--wa-font-size-xs);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-normal);
    text-align: center;
    padding-bottom: var(--wa-space-3xs);
    align-self: end;
}

/* The 5-bar level icon above the effort name (colours baked into the SVG). */
.matrix-col-header-icon {
    font-size: 1.4em;
}

.matrix-divider {
    border-top: 1px solid var(--wa-color-surface-border);
    margin: var(--wa-space-2xs) 0;
    height: 0;
}

.matrix-vlabel {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding-right: var(--wa-space-2xs);
}

.matrix-vlabel-text {
    /* Vertical, read bottom-to-top (rotate 180° flips vertical-rl upward). */
    writing-mode: vertical-rl;
    transform: rotate(180deg);
    white-space: nowrap;
    font-size: var(--wa-font-size-s);
    font-weight: var(--wa-font-weight-semibold);
    color: var(--wa-color-text-normal);
}

/* Sits at the bottom of the vertical label (the start of the bottom-to-top
   read), kept upright since it's a sibling of — not inside — the rotated text. */
.matrix-vlabel-icon {
    font-size: 1rem;
    color: var(--wa-color-text-normal);
}

.matrix-row-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    padding-right: var(--wa-space-xs);
    white-space: nowrap;
}

.matrix-model-version {
    color: var(--wa-color-text-quiet);
}

.matrix-row-header.old {
    color: var(--wa-color-text-quiet);
}

.matrix-cell {
    /* Shared foreground for the score, the selected check and the default dot.
       Flipped to the surface color above score 80 in light mode only (see
       .score-high) so it stays readable on the strong fill. --cell-check-fg is
       its counter-color, used inside the selected-check badge (disc = fg,
       glyph = counter) so the badge reads on any fill. */
    --cell-fg: var(--wa-color-text-normal);
    --cell-check-fg: var(--wa-color-surface-default);
    position: relative;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 1.9rem;
    border: 1px solid var(--wa-color-neutral-border-normal, var(--wa-color-surface-border));
    border-radius: var(--wa-border-radius-s);
    /* Score-driven fill: brand at alpha = score/100 (0 -> transparent, 100 ->
       full brand). --score-alpha is set inline per cell. */
    background-color: color-mix(in srgb, var(--wa-color-brand-60) calc(var(--score-alpha, 0) * 100%), transparent);
    cursor: pointer;
    padding: 0;
    transition: background-color 0.1s, border-color 0.1s;
}

/* Above score 80, in light mode only, flip the shared foreground to the surface
   color so the number stays readable on the strong fill. In dark mode
   text-normal already reads on the fill, so we leave it (no inversion). */
:root:not(.wa-dark) .matrix-cell.score-high {
    --cell-fg: var(--wa-color-surface-default);
    --cell-check-fg: var(--wa-color-text-normal);
}

/* Benchmark score (integer 0..100, or "?" when there's no benchmark data). */
.matrix-cell-score {
    font-size: var(--wa-font-size-xs);
    font-variant-numeric: tabular-nums;
    line-height: 1;
    color: var(--cell-fg);
}

/* "?" placeholder for cells without benchmark data — kept quiet. */
.matrix-cell-score.no-score {
    color: var(--wa-color-text-quiet);
}

.matrix-cell.selected .matrix-cell-score {
    font-weight: var(--wa-font-weight-semibold);
}

.matrix-cell:hover:not(:disabled) {
    border-color: var(--wa-color-brand-border-normal, var(--wa-color-brand-60));
}

/* Best score of a provider block: solid border for the user's default provider
   (or a lone provider), dashed for each other provider. Drawn as an overlay
   ring (::after, out of flow) so the thick border never shifts the grid — every
   cell keeps its 1px base border — and `outline` stays free for keyboard focus.
   inset: -2px bleeds the ring 1px into the 3px grid gap instead of shrinking the
   fill; the ring paints over the base border underneath. */
.matrix-cell.top-solid::after,
.matrix-cell.top-dashed::after {
    content: "";
    position: absolute;
    inset: -2px;
    border-radius: inherit;
    pointer-events: none;
    border: 3px solid var(--wa-color-text-normal);
}

.matrix-cell.top-dashed::after {
    border-style: dashed;
}

/* Selection marker — a badge straddling the bottom-right corner: a filled disc
   in the cell's foreground color with the check in the counter-color, plus a
   surface-colored ring to detach it from the fill. Stays legible whatever the
   score fill or the user's brand color. */
.matrix-cell-check {
    position: absolute;
    right: -4px;
    bottom: -4px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--cell-fg);
    color: var(--cell-check-fg);
    font-size: 9px;
    line-height: 1;
    border: 1px solid var(--wa-color-surface-default);
    box-sizing: content-box;
    z-index: 1;
    pointer-events: none;
}

.matrix-cell.unavailable {
    cursor: not-allowed;
    background: repeating-linear-gradient(
        -45deg,
        transparent,
        transparent 4px,
        var(--wa-color-neutral-fill-quiet, rgba(128, 128, 128, 0.08)) 4px,
        var(--wa-color-neutral-fill-quiet, rgba(128, 128, 128, 0.08)) 8px
    );
    border-color: transparent;
    opacity: 0.55;
}

.matrix-default-dot {
    position: absolute;
    top: 3px;
    right: 3px;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    /* In a cell: matches the score (inverts above 80). In the legend (no
       --cell-fg ancestor): falls back to the normal text color. */
    background: var(--cell-fg, var(--wa-color-text-normal));
}

/* Another provider's default: a hollow ring of the same 6px size (border-box so
   the border eats into the size instead of growing it). */
.matrix-default-dot.hollow {
    box-sizing: border-box;
    background: transparent;
    border: 1px solid var(--cell-fg, var(--wa-color-text-normal));
}

.matrix-footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.matrix-old-toggle {
    background: none;
    border: none;
    padding: 0;
    cursor: pointer;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    &:hover {
        text-decoration: underline;
    }
}

.matrix-legend {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin-left: auto;

    .matrix-default-dot {
        position: static;
    }
}

.matrix-legend-item {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.matrix-legend-check {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
}

/* ─── "Benchmark data" panel content (slotted into HoverInfoPanel) ──────── */
.cell-tip {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
    text-align: left;
}

.cell-tip-title {
    font-weight: var(--wa-font-weight-bold);
    font-size: var(--wa-font-size-s);
    margin-bottom: var(--wa-space-3xs);
}

.cell-tip-row {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
}

/* Label left, value right (tabular, bold). */
.cell-tip-metric {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--wa-space-l);
    font-size: var(--wa-font-size-s);
    line-height: 1.2;
}

/* Both the metric label and its value are bolded to stand out from the muted
   description line below. */
.cell-tip-label {
    font-weight: var(--wa-font-weight-bold);
}

.cell-tip-value {
    font-variant-numeric: tabular-nums;
    font-weight: var(--wa-font-weight-bold);
    white-space: nowrap;
}

.cell-tip-desc {
    font-size: var(--wa-font-size-xs);
    opacity: 0.75;
    line-height: 1.25;
}

.cell-tip-empty {
    font-size: var(--wa-font-size-s);
    line-height: 1.3;
}
</style>
