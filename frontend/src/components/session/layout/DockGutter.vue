<script setup>
// One edge gutter (thin rail) holding the icons of collapsed / responsively-hidden docks.
// One icon PER TAB, anchored start/end mirroring the dock origin. A click dispatches the
// item's action (swap | restore | overlay) up to the layout.
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import SessionTabLink from './SessionTabLink.vue'
import GitChangeStats from '../../git/GitChangeStats.vue'
import UploadTabStatus from '../../files/UploadTabStatus.vue'

const props = defineProps({
    // resolver gutter: { edge, x, y, w, h, items: [{ dockId, tabs, action, anchor }] }
    gutter: { type: Object, required: true },
    openOverlayEdge: { type: String, default: null },
    // (item) -> the active tab id of a single rail dock. Marks that dock's active chip, and used by
    // empty-area clicks so they act on it, exactly like clicking the chip itself. See dockActiveTabId.
    resolveActiveTab: { type: Function, default: null },
    tabHref: { type: Function, required: true },
    // (tabId) -> change counts ({ modified, added, deleted, conflicted }) shown next to a tab's label, or null.
    tabChangeStats: { type: Function, default: null },
    // (tabId) -> upload status ({ count, percent, allStalled }) shown next to a tab's label, or null.
    tabUploadStatus: { type: Function, default: null },
})
const emit = defineEmits(['action'])

const style = computed(() => ({
    left: `${props.gutter.x}px`,
    top: `${props.gutter.y}px`,
    width: `${props.gutter.w}px`,
    height: `${props.gutter.h}px`,
}))

// The tab a rail dock is currently on — the chip it marks, and the one the rail's empty area acts on.
function activeTabOf(item) {
    return props.resolveActiveTab ? props.resolveActiveTab(item) : item.tabs[0]?.id
}

// Flatten items into per-tab icons, split into the start- and end-anchored groups. Each entry carries
// whether it is its dock's active tab, so exactly one chip per dock is marked.
function iconsFor(anchor) {
    const out = []
    for (const item of props.gutter.items) {
        if (item.anchor !== anchor) continue
        const activeId = activeTabOf(item)
        for (const tab of item.tabs) out.push({ item, tab, active: tab.id === activeId })
    }
    return out
}
const startIcons = computed(() => iconsFor('start'))
const endIcons = computed(() => iconsFor('end'))

// ---- Overflow handling: labels → icons only → icons + a "+N" chip per group. ----
// The rail's box is clipped by the resolver (its extent mirrors the region band it replaces), but
// the chip runs are absolutely positioned and rotated: nothing clips them, so a run longer than the
// rail used to spill over the bottom dock (classic mode) and cover its window buttons. Decide what
// fits BEFORE rendering, from cached natural widths, so the rendered state never feeds back into
// the decision (no flapping): a hidden mirror always renders every chip with its label, plus an
// icon-only copy and a "+N" sample; a ResizeObserver on the mirrors re-reads the widths when the
// tabs or the font change — never on rail resizes (the mirror's size doesn't depend on the rail's).
// offsetWidth is a pre-transform layout metric, so the rotation needs no math.
const CHIP_GAP = 2     // matches .g-group gap
const GROUP_GAP = 8    // min separation between the start and end runs
const RAIL_PADDING = 2 // matches --gutter-padding

const startMirrorRef = ref(null)
const endMirrorRef = ref(null)
const measures = ref({ start: null, end: null })

function readMirror(el) {
    if (!el) return null
    return {
        labelWs: [...el.querySelectorAll('.mm-label')].map((c) => c.offsetWidth),
        iconWs: [...el.querySelectorAll('.mm-icon')].map((c) => c.offsetWidth),
        plusW: el.querySelector('.mm-plus')?.offsetWidth || 0,
    }
}
function measure() {
    measures.value = { start: readMirror(startMirrorRef.value), end: readMirror(endMirrorRef.value) }
}
let mirrorObserver = null
onMounted(() => {
    mirrorObserver = new ResizeObserver(measure)
    if (startMirrorRef.value) mirrorObserver.observe(startMirrorRef.value)
    if (endMirrorRef.value) mirrorObserver.observe(endMirrorRef.value)
    measure()
})
onUnmounted(() => mirrorObserver?.disconnect())
// A tab-set change can leave the mirror's total width unchanged (the observer stays silent) while
// the per-chip arrays did change — re-read after the mirror re-renders. Keyed on a stable string
// (not the computed arrays, fresh on every resolver run) so rail resizes never trigger a re-read.
watch(
    () => [...startIcons.value, ...endIcons.value]
        .map((e) => `${e.item.dockId}:${e.tab.id}:${e.tab.icon}:${e.tab.label}`).join('|'),
    () => nextTick(measure),
)

// How many chips each group shows, and whether labels are hidden rail-wide. Cascade: (1) every
// label fits → full chips; (2) icons alone fit → icons rail-wide (per-group would mix styles);
// (3) k icons + a "+N" chip, dropping trailing chips from the fuller group first. Reading
// gutter.w/h keeps this reactive to rail resizes — pure arithmetic, no DOM.
const plan = computed(() => {
    const nS = startIcons.value.length
    const nE = endIcons.value.length
    const all = { iconsOnly: false, start: nS, end: nE }
    const mS = measures.value.start, mE = measures.value.end
    if (!mS || !mE || mS.labelWs.length !== nS || mE.labelWs.length !== nE) return all
    const extent = props.gutter.edge === 'bottom' ? props.gutter.w : props.gutter.h
    const avail = extent - 2 * RAIL_PADDING
    const groupGap = nS > 0 && nE > 0 ? GROUP_GAP : 0
    const run = (ws) => ws.reduce((a, b) => a + b, 0) + Math.max(0, ws.length - 1) * CHIP_GAP
    if (run(mS.labelWs) + run(mE.labelWs) + groupGap <= avail) return all
    if (run(mS.iconWs) + run(mE.iconWs) + groupGap <= avail) return { ...all, iconsOnly: true }
    const groupW = (m, k, n) => {
        if (!n) return 0
        const chips = m.iconWs.slice(0, k)
        if (k < n) chips.push(m.plusW)
        return run(chips)
    }
    let kS = nS, kE = nE
    while ((kS > 0 || kE > 0) && groupW(mS, kS, nS) + groupW(mE, kE, nE) + groupGap > avail) {
        if (kS >= kE && kS > 0) kS -= 1
        else kE -= 1
    }
    return { iconsOnly: true, start: kS, end: kE }
})
const startVisible = computed(() => startIcons.value.slice(0, plan.value.start))
const endVisible = computed(() => endIcons.value.slice(0, plan.value.end))

// The "+N" chip of a group: stands for the group's hidden trailing tabs and acts on the whole dock
// exactly like its active chip would (same onClick path: peek toggle for overlay docks, deferred
// restore/swap with double-click-maximize otherwise) — once the dock is open, its own tab bar shows
// everything. `active: true` because its target IS the dock's active tab (keeps verb/isOpen right).
function plusEntryFor(entries, visibleCount) {
    const hidden = entries.slice(visibleCount)
    const item = hidden[0].item
    const activeId = activeTabOf(item)
    const tab = item.tabs.find((t) => t.id === activeId) || item.tabs[0]
    return { item, tab, active: true, hidden }
}
const startPlus = computed(() => plan.value.start < startIcons.value.length
    ? plusEntryFor(startIcons.value, plan.value.start) : null)
const endPlus = computed(() => plan.value.end < endIcons.value.length
    ? plusEntryFor(endIcons.value, plan.value.end) : null)
function plusTitle(p) {
    return `${p.hidden.length} more: ${p.hidden.map((e) => e.tab.label).join(', ')} — ${verb(p)}`
}

// The peek is open on THIS chip: its edge holds the open overlay and the chip is its dock's active tab
// (the overlay shows exactly that tab). Both halves are needed — an edge can hold two docks, and a dock
// several tabs, all of which would otherwise read as open.
function isOpen(entry) {
    return entry.item.action === 'overlay' && props.openOverlayEdge === props.gutter.edge && entry.active
}
function verb(entry) {
    if (entry.item.action === 'swap') return 'show this column'
    if (entry.item.action === 'restore') return 'restore'
    return isOpen(entry) ? 'close overlay' : 'peek overlay'
}
// A restore/swap chip's double-click maximizes its dock (see onClick); advertise it in the chip's
// title. Overlay chips don't maximize on double-click (their second click just forces the peek open),
// so they keep the plain single-action title.
function chipTitle(entry) {
    const base = `${entry.tab.label} — ${verb(entry)}`
    return entry.item.action === 'overlay' ? base : `${base} · double-click to maximize`
}
// Title for the rail's empty area (clicking anywhere acts on the pointed dock). The double-click hint
// only holds when a restore/swap dock is here — an overlay-only rail double-click just forces the peek.
const emptyAreaTitle = computed(() =>
    props.gutter.items.some((it) => it.action !== 'overlay')
        ? 'Click to open · double-click to maximize'
        : 'Click to open'
)
// Double-click on a rail chip = maximize that dock. A native dblclick is impossible on a restore/swap
// chip: its first click moves the dock out of the rail, so the chip is gone before a dblclick could
// fire. So we detect the double-click ourselves — hold the single action for DOUBLE_CLICK_DELAY and
// promote it to 'maximize' if a second click on the same chip lands first. Overlay chips stay instant
// (the peek isn't destructive); their second rapid click just forces the peek open so a double-click
// can't toggle it shut. Keep DOUBLE_CLICK_DELAY in sync with the rest of the layout's double-click feel.
const DOUBLE_CLICK_DELAY = 250 // ms

let pendingTimer = null
let pendingEntry = null
let lastOverlay = { key: null, at: 0 }

function chipKey(entry) { return entry.item.dockId + ':' + entry.tab.id }
function fire(entry, action) {
    emit('action', { edge: props.gutter.edge, dockId: entry.item.dockId, tabId: entry.tab.id, action })
}
function cancelPending() {
    if (pendingTimer) { clearTimeout(pendingTimer); pendingTimer = null }
    pendingEntry = null
}
function flushPending() {
    if (!pendingTimer) return
    clearTimeout(pendingTimer); pendingTimer = null
    const e = pendingEntry; pendingEntry = null
    if (e) fire(e, e.item.action)
}

function onClick(entry) {
    if (entry.item.action === 'overlay') {
        // A pending restore/swap from another chip shouldn't be cancelled by an overlay click — let it run.
        flushPending()
        const key = chipKey(entry)
        const now = Date.now()
        if (lastOverlay.key === key && now - lastOverlay.at < DOUBLE_CLICK_DELAY) {
            lastOverlay = { key: null, at: 0 }
            fire(entry, 'overlay-open') // force open: a double-click leaves the peek open, never toggled shut
            return
        }
        lastOverlay = { key, at: now }
        fire(entry, entry.item.action) // normal peek toggle (instant)
        return
    }
    // restore / swap: defer the native action; a quick second click on the same chip becomes maximize.
    if (pendingEntry && chipKey(pendingEntry) === chipKey(entry)) {
        cancelPending()
        fire(entry, 'maximize')
        return
    }
    flushPending() // a different chip was pending — let its native action run, then defer this one
    pendingEntry = entry
    pendingTimer = setTimeout(() => {
        pendingTimer = null
        const e = pendingEntry; pendingEntry = null
        if (e) fire(e, e.item.action)
    }, DOUBLE_CLICK_DELAY)
}

// The empty area of the rail (anywhere that isn't a chip) acts on the dock it points at, on that
// dock's active tab — reusing the chip path so it gets the same deferred single/double handling. With
// at most two docks per edge, always on opposite anchors (start = top/left, end = bottom/right), the
// click position along the rail axis resolves the dock with no guessing: first half -> the start dock,
// second half -> the end dock.
function targetItemFor(event) {
    const items = props.gutter.items
    if (!items.length) return null
    if (items.length === 1) return items[0]
    const rect = event.currentTarget.getBoundingClientRect()
    const vertical = props.gutter.edge !== 'bottom'
    const rel = vertical
        ? (event.clientY - rect.top) / (rect.height || 1)
        : (event.clientX - rect.left) / (rect.width || 1)
    const wantAnchor = rel < 0.5 ? 'start' : 'end'
    return items.find((it) => it.anchor === wantAnchor) || items[0]
}
function onEmptyAreaClick(event) {
    if (event.target.closest('.g-chip')) return // a chip handles its own click
    const item = targetItemFor(event)
    if (!item) return
    const tabId = props.resolveActiveTab ? props.resolveActiveTab(item) : item.tabs[0]?.id
    if (!tabId) return
    onClick({ item, tab: { id: tabId } })
}

onUnmounted(cancelPending)
</script>

<template>
    <div class="dock-gutter" :class="[gutter.edge, { 'icons-only': plan.iconsOnly }]" :style="style" :title="emptyAreaTitle" @click="onEmptyAreaClick">
        <div class="g-group start">
            <SessionTabLink
                v-for="entry in startVisible"
                :key="entry.item.dockId + ':' + entry.tab.id"
                :href="tabHref(entry.tab.id)"
                tabbable
                class="g-chip"
                :data-layout-tab-id="entry.tab.id"
                :data-layout-dock-id="entry.item.dockId"
                :class="{ active: entry.active, open: isOpen(entry) }"
                :title="chipTitle(entry)"
                :aria-label="`${entry.tab.label} — ${verb(entry)}`"
                @plain-click="onClick(entry)"
            >
                <wa-icon :name="entry.tab.icon"></wa-icon>
                <span class="g-label">{{ entry.tab.label }}</span>
                <GitChangeStats v-if="tabChangeStats" class="g-label" :stats="tabChangeStats(entry.tab.id)" />
                <UploadTabStatus v-if="tabUploadStatus" class="g-label" :status="tabUploadStatus(entry.tab.id)" />
            </SessionTabLink>
            <SessionTabLink
                v-if="startPlus"
                :href="tabHref(startPlus.tab.id)"
                tabbable
                class="g-chip g-plus"
                :class="{ active: startPlus.hidden.some((e) => e.active), open: isOpen(startPlus) }"
                :title="plusTitle(startPlus)"
                :aria-label="plusTitle(startPlus)"
                @plain-click="onClick(startPlus)"
            >+{{ startPlus.hidden.length }}</SessionTabLink>
        </div>
        <div class="g-group end">
            <SessionTabLink
                v-for="entry in endVisible"
                :key="entry.item.dockId + ':' + entry.tab.id"
                :href="tabHref(entry.tab.id)"
                tabbable
                class="g-chip"
                :data-layout-tab-id="entry.tab.id"
                :data-layout-dock-id="entry.item.dockId"
                :class="{ active: entry.active, open: isOpen(entry) }"
                :title="chipTitle(entry)"
                :aria-label="`${entry.tab.label} — ${verb(entry)}`"
                @plain-click="onClick(entry)"
            >
                <wa-icon :name="entry.tab.icon"></wa-icon>
                <span class="g-label">{{ entry.tab.label }}</span>
                <GitChangeStats v-if="tabChangeStats" class="g-label" :stats="tabChangeStats(entry.tab.id)" />
                <UploadTabStatus v-if="tabUploadStatus" class="g-label" :status="tabUploadStatus(entry.tab.id)" />
            </SessionTabLink>
            <SessionTabLink
                v-if="endPlus"
                :href="tabHref(endPlus.tab.id)"
                tabbable
                class="g-chip g-plus"
                :class="{ active: endPlus.hidden.some((e) => e.active), open: isOpen(endPlus) }"
                :title="plusTitle(endPlus)"
                :aria-label="plusTitle(endPlus)"
                @plain-click="onClick(endPlus)"
            >+{{ endPlus.hidden.length }}</SessionTabLink>
        </div>
        <!-- Hidden measurement mirrors: every chip with its label (and change counts, upload status), an icon-only copy of each, and a
             "+N" sample (worst-case digits: the group's full count). Never visible, never clipped by
             icons-only (plain spans, no .g-label), never hit-testable. -->
        <div ref="startMirrorRef" class="g-measure" aria-hidden="true">
            <span v-for="entry in startIcons" :key="'l:' + entry.item.dockId + ':' + entry.tab.id" class="g-chip mm-label">
                <wa-icon :name="entry.tab.icon"></wa-icon>
                <span>{{ entry.tab.label }}</span>
                <GitChangeStats v-if="tabChangeStats" :stats="tabChangeStats(entry.tab.id)" />
                <UploadTabStatus v-if="tabUploadStatus" :status="tabUploadStatus(entry.tab.id)" />
            </span>
            <span v-for="entry in startIcons" :key="'i:' + entry.item.dockId + ':' + entry.tab.id" class="g-chip mm-icon">
                <wa-icon :name="entry.tab.icon"></wa-icon>
            </span>
            <span class="g-chip mm-plus">+{{ startIcons.length }}</span>
        </div>
        <div ref="endMirrorRef" class="g-measure" aria-hidden="true">
            <span v-for="entry in endIcons" :key="'l:' + entry.item.dockId + ':' + entry.tab.id" class="g-chip mm-label">
                <wa-icon :name="entry.tab.icon"></wa-icon>
                <span>{{ entry.tab.label }}</span>
                <GitChangeStats v-if="tabChangeStats" :stats="tabChangeStats(entry.tab.id)" />
                <UploadTabStatus v-if="tabUploadStatus" :status="tabUploadStatus(entry.tab.id)" />
            </span>
            <span v-for="entry in endIcons" :key="'i:' + entry.item.dockId + ':' + entry.tab.id" class="g-chip mm-icon">
                <wa-icon :name="entry.tab.icon"></wa-icon>
            </span>
            <span class="g-chip mm-plus">+{{ endIcons.length }}</span>
        </div>
    </div>
</template>

<style scoped>
.dock-gutter {
    --gutter-size: 30px; /* Keep updated with railW from layoutResolver.js */
    --gutter-padding: 2px;
    position: absolute;
    display: flex;
    justify-content: space-between;
    gap: var(--gutter-padding);
    padding: var(--gutter-padding);
    /* The whole rail is clickable (empty area opens the pointed dock; chips carry their own action). */
    cursor: pointer;
    background: transparent; /* on the canvas, like the sidebar */
    z-index: 12; /* above an open overlay backdrop, so gutters stay clickable */
}
/* On the canvas: no border (the gap separates the rail from the cards). */
.dock-gutter.left {
    flex-direction: column;
    align-items: center;
}
.dock-gutter.right {
    flex-direction: column;
    align-items: center;
}
.dock-gutter.bottom {
    flex-direction: row;
    align-items: center;
}
.g-group {
    display: flex;
    gap: 2px;
}

.dock-gutter {
    .g-group {
        align-items: center;
        height: var(--gutter-size);
        position: absolute;
    }
    &.left, &.right {
        .g-group {
            transform: rotate(-90deg);
            &.start {
                top: calc(var(--gutter-padding) - var(--gutter-size));
                right: 0;
                transform-origin: bottom right;
            }
            &.end {
                bottom: calc(var(--gutter-padding) - var(--gutter-size));
                left: 0;
                transform-origin: top left;
            }
        }
    }
    &.bottom .g-group {
        top: var(--gutter-padding);
        &.start {
            left: var(--gutter-padding);
        }
        &.end {
            right: var(--gutter-padding);
        }
    }
}
.g-chip {
    display: inline-flex;
    flex-direction: row;
    align-items: center;
    justify-content: center;
    gap: 5px;
    border-radius: var(--wa-border-radius-s, 4px);
    background: transparent;
    border: none;
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    padding: 3px 7px;
    font-size: 0.75rem;
    line-height: 1;
    white-space: nowrap;
    transition: background-color 0.15s, color 0.15s;
}
.g-chip wa-icon {
    flex: 0 0 auto;
    font-size: 1.1em;
    margin-inline-end: 0;
}
.g-chip:hover {
    color: inherit;
}
/* Two marks, one chip per rail dock. `active` = the tab the dock is on (always shown, open or not):
   full-strength text, no fill, so a resting rail stays calm. `open` = that tab is currently peeking as
   an overlay: the fill makes it the only chip on the rail that reads as pressed. */
.g-chip.active {
    color: inherit;
}
.g-chip.open {
    color: var(--wa-color-brand-on-quiet);
    background: var(--wa-color-brand-fill-quiet);
}
/* Rail-wide icons-only mode (decided by `plan`): labels vanish, chips shrink to their icons.
   Mirror labels are plain spans (no .g-label), so measurements are never affected. */
.dock-gutter.icons-only .g-group .g-label {
    display: none;
}
/* Hidden measurement mirrors. width:max-content escapes the 30px rail's containing-block cap so
   the run lays out at its natural length; offsetWidth ignores transforms, so no rotation math. */
.g-measure {
    position: absolute;
    top: 0;
    left: 0;
    display: flex;
    gap: 2px;
    width: max-content;
    visibility: hidden;
    pointer-events: none;
}
</style>
