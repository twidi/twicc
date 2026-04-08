<script setup>
// UsageGraphDialog.vue - Dialog showing historical usage graphs (utilization & burn rate)
// for 5-hour and 7-day quota periods. Uses SVG charts following the same approach
// as ContributionSparklines.vue (gradient fills with polyline masks).
// Includes a period slider (1w → 1y) with automatic data granularity.

import { ref, computed, watch } from 'vue'
import { useSettingsStore } from '../stores/settings'

const settingsStore = useSettingsStore()
const isTouchDevice = computed(() => settingsStore.isTouchDevice)

const dialogRef = ref(null)
const activeTab = ref('five-hour')
const isLoading = ref(false)     // true only on initial fetch (no data yet)
const isRefreshing = ref(false)  // true during slider-triggered refetches (old data stays visible)
const errorMessage = ref('')

// ── Period slider ───────────────────────────────────────────────────
// Shared across both tabs. Each step defines range_days, bucket_minutes, and label.

const RANGE_STEPS = [
    { days: 0.25, bucket: 0,    label: '6 hours' },
    { days: 0.5,  bucket: 0,    label: '12 hours' },
    { days: 1,    bucket: 0,    label: '1 day' },
    { days: 3,    bucket: 0,    label: '3 days' },
    { days: 7,    bucket: 0,    label: '1 week' },
    { days: 14,   bucket: 30,   label: '2 weeks' },
    { days: 30,   bucket: 60,   label: '1 month' },
    { days: 90,   bucket: 300,  label: '3 months' },
    { days: 182,  bucket: 720,  label: '6 months' },
    { days: 365,  bucket: 1440, label: '1 year' },
]
const DEFAULT_RANGE_INDEX = 4 // 1 week
const rangeIndex = ref(DEFAULT_RANGE_INDEX)
const currentRange = computed(() => RANGE_STEPS[rangeIndex.value])

function onRangeChange(event) {
    const val = parseInt(event.target.value, 10)
    if (!isNaN(val) && val >= 0 && val < RANGE_STEPS.length && val !== rangeIndex.value) {
        rangeIndex.value = val
    }
}

// Re-fetch when range changes (after initial open)
let isOpen = false
watch(rangeIndex, () => {
    if (!isOpen) return
    _fetchGeneration++ // invalidate any in-flight fetches from previous range
    clearSnapshots()
    panOffsetMs.value = 0
    noMoreDataLeft.value = false
    fetchData({ isInitial: true })
})

// ── Curve visibility ───────────────────────────────────────────────
// Each curve can be toggled on/off by clicking its legend item.
const curveVisibility = ref({
    temporal_pct: true,
    utilization: true,
    burn_rate: true,
    recent_long: false,
    recent_short: false,
})

function toggleCurve(key) {
    curveVisibility.value[key] = !curveVisibility.value[key]
}

// ── Snapshot accumulator ───────────────────────────────────────────
// Snapshots are accumulated across fetches (initial + incremental panning).
// A non-reactive Map deduplicates by fetched_at; the sorted array ref drives rendering.
const snapshotsRaw = ref([])
let _snapshotMap = new Map()

function mergeSnapshots(newSnaps) {
    for (const snap of newSnaps) {
        _snapshotMap.set(snap.fetched_at, snap)
    }
    snapshotsRaw.value = [..._snapshotMap.values()]
        .sort((a, b) => a.fetched_at.localeCompare(b.fetched_at))
}

function clearSnapshots() {
    _snapshotMap = new Map()
    snapshotsRaw.value = []
    fetchedRangeStartMs.value = null
    fetchedRangeEndMs.value = null
}

// ── Panning state ──────────────────────────────────────────────────
// panOffsetMs: shift in ms from default position (<=0 = past, 0 = present)
const panOffsetMs = ref(0)
const isDragging = ref(false)
const isFetchingMore = ref(false)
const noMoreDataLeft = ref(false)
const fetchedRangeStartMs = ref(null) // left boundary of fetched data (epoch ms)
const fetchedRangeEndMs = ref(null)   // right boundary of fetched data (epoch ms)

let _dragStartX = 0
let _dragStartPanOffset = 0
let _dragSvgWidth = 1
let _dragStartY = 0
let _touchDragDirection = null // 'horizontal' | 'vertical' | null
let _fetchGeneration = 0 // incremented on range change / open to invalidate stale fetches

// ── Chart constants ─────────────────────────────────────────────────
const SVG_WIDTH = 1094
const SVG_HEIGHT = 180
const GRAPH_HEIGHT = 170
const VIEWBOX_HEIGHT = 180
const PADDING_TOP = 6
const MIN_Y = 1.0

// ── API fetching ────────────────────────────────────────────────────

/**
 * Fetch usage history data.
 * @param {object} opts
 * @param {boolean} opts.isInitial - First fetch for this range (fetches 3× period for buffer)
 * @param {string|null} opts.before - ISO timestamp for range end (null = now)
 * @param {number|null} opts.rangeDays - Override range_days (null = use current)
 * @param {boolean} opts.silent - If true, don't show loading indicators (for prefetch)
 */
async function fetchData({ isInitial = false, before = null, rangeDays = null, silent = false } = {}) {
    const myGeneration = _fetchGeneration
    const hasData = snapshotsRaw.value.length > 0
    if (!silent) {
        if (hasData) {
            isRefreshing.value = true
        } else {
            isLoading.value = true
        }
    }
    errorMessage.value = ''
    try {
        const { days, bucket } = currentRange.value
        // Initial fetch: 3× the display period for panning buffer (2 extra periods to the left)
        const fetchDays = rangeDays ?? (isInitial ? Math.min(1825, days * 3) : days)
        let url = `/api/usage-history/?range_days=${fetchDays}&bucket_minutes=${bucket}`
        if (before) url += `&before=${encodeURIComponent(before)}`
        const res = await fetch(url)
        if (!res.ok) {
            throw new Error('Failed to fetch usage history')
        }
        const json = await res.json()

        // Discard results if a newer fetch was triggered (range changed / dialog reopened)
        if (myGeneration !== _fetchGeneration) return

        const newSnaps = json.snapshots || []
        mergeSnapshots(newSnaps)

        // Track fetched boundaries
        if (newSnaps.length > 0) {
            const earliestMs = new Date(newSnaps[0].fetched_at).getTime()
            const latestMs = new Date(newSnaps[newSnaps.length - 1].fetched_at).getTime()
            fetchedRangeStartMs.value = fetchedRangeStartMs.value !== null
                ? Math.min(fetchedRangeStartMs.value, earliestMs)
                : earliestMs
            fetchedRangeEndMs.value = fetchedRangeEndMs.value !== null
                ? Math.max(fetchedRangeEndMs.value, latestMs)
                : latestMs
        }

        // Detect left boundary: if the earliest returned data doesn't reach the cutoff,
        // there's no more data further back.
        const requestedEndMs = before ? new Date(before).getTime() : Date.now()
        const requestedCutoffMs = requestedEndMs - fetchDays * 86400000
        const allSnaps = snapshotsRaw.value
        if (allSnaps.length > 0) {
            const earliestKnownMs = new Date(allSnaps[0].fetched_at).getTime()
            // If earliest data is >1h after the requested cutoff → no data beyond it
            if (earliestKnownMs > requestedCutoffMs + 3600000) {
                noMoreDataLeft.value = true
            }
        }
        if (newSnaps.length === 0 && !isInitial) {
            noMoreDataLeft.value = true
        }
    } catch (e) {
        if (myGeneration !== _fetchGeneration) return
        if (!silent) errorMessage.value = e.message || 'Failed to load usage history'
    } finally {
        // Only clear loading state if this is still the current generation
        if (myGeneration === _fetchGeneration) {
            isLoading.value = false
            isRefreshing.value = false
        }
    }
}

// ── Public API ──────────────────────────────────────────────────────

function open(period = 'five-hour') {
    activeTab.value = period
    fiveHourHoveredIndex.value = null
    sevenDayHoveredIndex.value = null
    _fetchGeneration++ // invalidate any leftover fetches from previous open
    clearSnapshots()
    panOffsetMs.value = 0
    noMoreDataLeft.value = false
    rangeIndex.value = DEFAULT_RANGE_INDEX
    fetchData({ isInitial: true })
    isOpen = true
    dialogRef.value.open = true
}

function close() {
    isOpen = false
    // Clean up any in-progress drag
    if (isDragging.value) {
        isDragging.value = false
        document.removeEventListener('mousemove', _onDocMouseMove)
        document.removeEventListener('mouseup', _onDocMouseUp)
    }
    if (_prefetchTimer) {
        clearTimeout(_prefetchTimer)
        _prefetchTimer = null
    }
    dialogRef.value.open = false
}

defineExpose({ open, close })

// ── Chart helpers ───────────────────────────────────────────────────

function colorVars(prefix) {
    return {
        g1: `var(--sparkline-${prefix}-gradient-color-1)`,
        g2: `var(--sparkline-${prefix}-gradient-color-2)`,
        g3: `var(--sparkline-${prefix}-gradient-color-3)`,
        g4: `var(--sparkline-${prefix}-gradient-color-4)`,
        stroke: `var(--sparkline-${prefix}-stroke-color)`,
    }
}

/**
 * Filter accumulated snapshots to the visible viewport (with margin).
 * Avoids processing thousands of off-screen points after panning fetches.
 */
const visibleSnapshots = computed(() => {
    const marginMs = currentRange.value.days * 86400000 * 0.05 // 5% margin
    const start = tStart.value - marginMs
    const end = tEnd.value + marginMs
    return snapshotsRaw.value.filter(s => {
        const t = new Date(s.fetched_at).getTime()
        return t >= start && t <= end
    })
})

/**
 * Split visible snapshots into period-specific data arrays.
 * Each period gets utilization, burn rates (regular, recent), and temporal_pct.
 */
function extractPeriodData(snapshots, utilKey, burnKey, recentLongKey, recentShortKey, temporalKey) {
    return snapshots
        .filter(s => s[utilKey] != null || s[burnKey] != null || s[temporalKey] != null)
        .map(s => ({
            fetched_at: s.fetched_at,
            utilization: s[utilKey],
            burn_rate: s[burnKey],
            recent_long: s[recentLongKey],
            recent_short: s[recentShortKey],
            temporal_pct: s[temporalKey],
        }))
}

// ── Y-axis cap slider ──────────────────────────────────────────────
// Adjustable cap (100–1000%) to control how much of the burn rate spikes are visible.
const DEFAULT_Y_CAP = 500
const yCap = ref(DEFAULT_Y_CAP)

function onYCapChange(event) {
    const val = parseInt(event.target.value, 10)
    if (!isNaN(val) && val >= 100 && val <= 1000) {
        yCap.value = val
    }
}


/**
 * Compute Y-axis max from ALL curves' data (not just visible ones).
 * Ensures 100% is always visible with headroom, capped at the user-selected yCap.
 * The scale never changes when toggling curves on/off.
 */
function computeYMax(data) {
    const maxOf = key => Math.max(0, ...data.map(d => d[key] ?? 0))
    return Math.min(yCap.value, Math.max(105,
        maxOf('utilization'), maxOf('burn_rate'),
        maxOf('recent_long'), maxOf('recent_short'), maxOf('temporal_pct'),
    )) * 1.08
}

/**
 * Parse fetched_at timestamps into epoch-ms array for a dataset.
 */
function parseTimestamps(data) {
    return data.map(d => new Date(d.fetched_at).getTime())
}

/**
 * Build polyline points using real timestamps for X positioning.
 * The X-axis always spans [rangeStart, now] so data appears at the right
 * of the chart when the selected range is larger than available data.
 */
function buildPolylinePoints(data, timestamps, valueKey, yMax, tStart, tEnd) {
    if (!data.length) return ''
    return data
        .map((d, i) => {
            const tRange = tEnd - tStart
            const x = tRange > 0
                ? ((timestamps[i] - tStart) / tRange) * SVG_WIDTH
                : SVG_WIDTH / 2
            const val = d[valueKey] ?? 0
            const y = yMax > 0
                ? Math.max(MIN_Y, (val / yMax) * GRAPH_HEIGHT)
                : MIN_Y
            return `${Math.round(x * 100) / 100},${Math.round(y * 100) / 100}`
        })
        .join(' ')
}

/**
 * Compute the Y position of the 100% reference line for a given yMax.
 */
function computeRefLineY(max) {
    const ratio = 100 / max
    return PADDING_TOP + GRAPH_HEIGHT - (ratio * GRAPH_HEIGHT)
}

/**
 * Find the closest data point index for a given X ratio (0–1) using timestamps.
 * Uses binary search for efficiency with large datasets.
 */
function findClosestIndex(timestamps, tStart, tEnd, ratio) {
    if (!timestamps.length) return null
    const target = tStart + ratio * (tEnd - tStart)

    // Binary search for closest timestamp
    let lo = 0
    let hi = timestamps.length - 1
    while (lo < hi) {
        const mid = (lo + hi) >> 1
        if (timestamps[mid] < target) lo = mid + 1
        else hi = mid
    }
    // lo is the first index >= target; check if lo-1 is closer
    if (lo > 0 && (target - timestamps[lo - 1]) < (timestamps[lo] - target)) {
        return lo - 1
    }
    return lo
}

// ── Time range boundaries ──────────────────────────────────────────
// baseTEnd = most recent data timestamp (anchor for panOffset=0)
// tEnd = baseTEnd + panOffsetMs (shifted by panning)
// tStart = tEnd - range_days

const baseTEnd = computed(() => {
    const snaps = snapshotsRaw.value
    if (snaps.length > 0) {
        return new Date(snaps[snaps.length - 1].fetched_at).getTime()
    }
    return Date.now()
})
const tEnd = computed(() => baseTEnd.value + panOffsetMs.value)
const tStart = computed(() => tEnd.value - currentRange.value.days * 86400000)

// ── Visible period label ───────────────────────────────────────────
const visiblePeriodLabel = computed(() => {
    const locale = navigator.language
    const startDate = new Date(tStart.value)
    const endDate = new Date(tEnd.value)
    const now = new Date()
    const showYear = startDate.getFullYear() !== now.getFullYear()
        || endDate.getFullYear() !== now.getFullYear()
    const opts = {
        month: 'short', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
        ...(showYear ? { year: 'numeric' } : {}),
    }
    return `${startDate.toLocaleString(locale, opts)} — ${endDate.toLocaleString(locale, opts)}`
})

// ── Panning clamp ──────────────────────────────────────────────────
function clampPanOffset(offset) {
    // Can't go into the future
    offset = Math.min(0, offset)
    // Can't go before the left boundary when we know there's no more data
    if (noMoreDataLeft.value && snapshotsRaw.value.length > 0) {
        const earliestMs = new Date(snapshotsRaw.value[0].fetched_at).getTime()
        const displayMs = currentRange.value.days * 86400000
        const minOffset = earliestMs - baseTEnd.value + displayMs
        offset = Math.max(minOffset, offset)
    }
    return offset
}

// ── Drag-to-pan handlers ───────────────────────────────────────────

function updatePanFromDrag(deltaX) {
    const timeRange = currentRange.value.days * 86400000
    // Drag right (deltaX > 0) → pan offset becomes more negative (viewing past)
    const newOffset = _dragStartPanOffset - (deltaX / _dragSvgWidth) * timeRange
    panOffsetMs.value = clampPanOffset(newOffset)
    schedulePrefetchCheck()
}

// Mouse handlers (document-level move/up for smooth tracking outside SVG)
function onChartMouseDown(event) {
    if (event.button !== 0) return // left click only
    event.preventDefault()
    _dragStartX = event.clientX
    _dragStartPanOffset = panOffsetMs.value
    _dragSvgWidth = event.currentTarget.getBoundingClientRect().width || 1
    isDragging.value = true
    fiveHourHoveredIndex.value = null
    sevenDayHoveredIndex.value = null
    document.addEventListener('mousemove', _onDocMouseMove)
    document.addEventListener('mouseup', _onDocMouseUp)
}

function _onDocMouseMove(event) {
    if (!isDragging.value) return
    updatePanFromDrag(event.clientX - _dragStartX)
}

function _onDocMouseUp() {
    if (!isDragging.value) return
    isDragging.value = false
    document.removeEventListener('mousemove', _onDocMouseMove)
    document.removeEventListener('mouseup', _onDocMouseUp)
}

// Touch handlers — horizontal drag pans, vertical drag scrolls normally.
// touch-action: pan-y on the SVG (set in CSS) lets the browser handle vertical scroll.
function onChartTouchStart(event) {
    if (event.touches.length !== 1) return
    const touch = event.touches[0]
    _dragStartX = touch.clientX
    _dragStartY = touch.clientY
    _dragStartPanOffset = panOffsetMs.value
    _touchDragDirection = null
}

function onChartTouchMove(event) {
    if (event.touches.length !== 1) return
    const touch = event.touches[0]

    // Determine direction on first significant move
    if (_touchDragDirection === null) {
        const dx = Math.abs(touch.clientX - _dragStartX)
        const dy = Math.abs(touch.clientY - _dragStartY)
        if (dx < 5 && dy < 5) return
        _touchDragDirection = dx > dy ? 'horizontal' : 'vertical'
        if (_touchDragDirection === 'horizontal') {
            isDragging.value = true
            _dragSvgWidth = event.currentTarget.getBoundingClientRect().width || 1
            fiveHourHoveredIndex.value = null
            sevenDayHoveredIndex.value = null
        }
    }

    if (_touchDragDirection !== 'horizontal') return
    updatePanFromDrag(touch.clientX - _dragStartX)
}

function onChartTouchEnd() {
    if (_touchDragDirection === 'horizontal') {
        isDragging.value = false
    }
    _touchDragDirection = null
}

// ── Prefetch logic ─────────────────────────────────────────────────
// When the viewport approaches the boundary of fetched data, prefetch more.

let _prefetchTimer = null

function schedulePrefetchCheck() {
    if (_prefetchTimer) return
    _prefetchTimer = setTimeout(() => {
        _prefetchTimer = null
        checkAndPrefetch()
    }, 150)
}

async function checkAndPrefetch() {
    if (isFetchingMore.value || noMoreDataLeft.value) return
    if (fetchedRangeStartMs.value === null) return

    const displayMs = currentRange.value.days * 86400000
    const viewStart = tStart.value

    // Trigger when viewport start is within 1 display period of fetched start
    if (viewStart <= fetchedRangeStartMs.value + displayMs) {
        isFetchingMore.value = true
        try {
            const before = new Date(fetchedRangeStartMs.value).toISOString()
            await fetchData({
                before,
                rangeDays: currentRange.value.days,
                silent: true,
            })
        } finally {
            isFetchingMore.value = false
        }
    }
}

// ── Five Hour chart data ────────────────────────────────────────────

const fiveHourData = computed(() => extractPeriodData(
    visibleSnapshots.value, 'fh_utilization', 'fh_burn_rate',
    'fh_recent_long', 'fh_recent_short', 'fh_temporal_pct',
))
const fhTimestamps = computed(() => parseTimestamps(fiveHourData.value))
const fhYMax = computed(() => computeYMax(fiveHourData.value))
const fhRefLineY = computed(() => computeRefLineY(fhYMax.value))

function fhPolyline(key) {
    return computed(() => buildPolylinePoints(fiveHourData.value, fhTimestamps.value, key, fhYMax.value, tStart.value, tEnd.value))
}
const fhUtilPoints = fhPolyline('utilization')
const fhBurnPoints = fhPolyline('burn_rate')
const fhRecentLongPoints = fhPolyline('recent_long')
const fhRecentShortPoints = fhPolyline('recent_short')
const fhTemporalPoints = fhPolyline('temporal_pct')

// Show temporal_pct for 5h quota only up to 2 weeks (index 1)
const fhShowTemporal = computed(() => rangeIndex.value <= 5)

const fhCurves = computed(() => {
    const vis = curveVisibility.value
    const curves = []
    if (fhShowTemporal.value) {
        curves.push({
            key: 'temporal_pct',
            label: 'Time elapsed',
            decimals: 0,
            points: fhTemporalPoints.value,
            gradientId: 'usage-temporal-gradient-fh',
            maskId: 'usage-temporal-mask-fh',
            colorPrefix: 'green',
            visible: vis.temporal_pct,
        })
    }
    curves.push(
        {
            key: 'utilization',
            label: 'Utilization',
            decimals: 1,
            points: fhUtilPoints.value,
            gradientId: 'usage-util-gradient-fh',
            maskId: 'usage-util-mask-fh',
            colorPrefix: 'blue',
            visible: vis.utilization,
        },
        {
            key: 'burn_rate',
            label: 'Burn rate',
            decimals: 0,
            points: fhBurnPoints.value,
            gradientId: 'usage-burn-gradient-fh',
            maskId: 'usage-burn-mask-fh',
            colorPrefix: 'red',
            visible: vis.burn_rate,
        },
        {
            key: 'recent_long',
            label: 'Burn rate (last 1h)',
            decimals: 0,
            points: fhRecentLongPoints.value,
            gradientId: 'usage-recent-long-gradient-fh',
            maskId: 'usage-recent-long-mask-fh',
            colorPrefix: 'purple',
            visible: vis.recent_long,
        },
        {
            key: 'recent_short',
            label: 'Burn rate (last 30min)',
            decimals: 0,
            points: fhRecentShortPoints.value,
            gradientId: 'usage-recent-short-gradient-fh',
            maskId: 'usage-recent-short-mask-fh',
            colorPrefix: 'pink',
            visible: vis.recent_short,
        },
    )
    return curves
})

// ── Seven Day chart data ────────────────────────────────────────────

const sevenDayData = computed(() => extractPeriodData(
    visibleSnapshots.value, 'sd_utilization', 'sd_burn_rate',
    'sd_recent_long', 'sd_recent_short', 'sd_temporal_pct',
))
const sdTimestamps = computed(() => parseTimestamps(sevenDayData.value))
const sdYMax = computed(() => computeYMax(sevenDayData.value))
const sdRefLineY = computed(() => computeRefLineY(sdYMax.value))

function sdPolyline(key) {
    return computed(() => buildPolylinePoints(sevenDayData.value, sdTimestamps.value, key, sdYMax.value, tStart.value, tEnd.value))
}
const sdUtilPoints = sdPolyline('utilization')
const sdBurnPoints = sdPolyline('burn_rate')
const sdRecentLongPoints = sdPolyline('recent_long')
const sdRecentShortPoints = sdPolyline('recent_short')
const sdTemporalPoints = sdPolyline('temporal_pct')

// Show temporal_pct for 7d quota only up to 6 months (index 4)
const sdShowTemporal = computed(() => rangeIndex.value <= 8)

const sdCurves = computed(() => {
    const vis = curveVisibility.value
    const curves = []
    if (sdShowTemporal.value) {
        curves.push({
            key: 'temporal_pct',
            label: 'Time elapsed',
            decimals: 0,
            points: sdTemporalPoints.value,
            gradientId: 'usage-temporal-gradient-sd',
            maskId: 'usage-temporal-mask-sd',
            colorPrefix: 'green',
            visible: vis.temporal_pct,
        })
    }
    curves.push(
        {
            key: 'utilization',
            label: 'Utilization',
            decimals: 1,
            points: sdUtilPoints.value,
            gradientId: 'usage-util-gradient-sd',
            maskId: 'usage-util-mask-sd',
            colorPrefix: 'blue',
            visible: vis.utilization,
        },
        {
            key: 'burn_rate',
            label: 'Burn rate',
            decimals: 0,
            points: sdBurnPoints.value,
            gradientId: 'usage-burn-gradient-sd',
            maskId: 'usage-burn-mask-sd',
            colorPrefix: 'red',
            visible: vis.burn_rate,
        },
        {
            key: 'recent_long',
            label: 'Burn rate (last 24h)',
            decimals: 0,
            points: sdRecentLongPoints.value,
            gradientId: 'usage-recent-long-gradient-sd',
            maskId: 'usage-recent-long-mask-sd',
            colorPrefix: 'purple',
            visible: vis.recent_long,
        },
        {
            key: 'recent_short',
            label: 'Burn rate (last 12h)',
            decimals: 0,
            points: sdRecentShortPoints.value,
            gradientId: 'usage-recent-short-gradient-sd',
            maskId: 'usage-recent-short-mask-sd',
            colorPrefix: 'pink',
            visible: vis.recent_short,
        },
    )
    return curves
})

// ── Hover tooltip (independent per panel) ───────────────────────────

const fiveHourHoveredIndex = ref(null)
const sevenDayHoveredIndex = ref(null)

// Reset hover when data changes
watch(snapshotsRaw, () => {
    fiveHourHoveredIndex.value = null
    sevenDayHoveredIndex.value = null
})

function makeMouseMove(timestamps, hoveredIndexRef) {
    return function onSvgMouseMove(event) {
        if (isTouchDevice.value || isDragging.value) return
        const svg = event.currentTarget
        const rect = svg.getBoundingClientRect()
        const ratio = (event.clientX - rect.left) / rect.width
        const ts = timestamps.value
        if (!ts.length) return
        hoveredIndexRef.value = findClosestIndex(ts, tStart.value, tEnd.value, ratio)
    }
}

const onFhMouseMove = makeMouseMove(fhTimestamps, fiveHourHoveredIndex)
const onSdMouseMove = makeMouseMove(sdTimestamps, sevenDayHoveredIndex)
function onFhMouseLeave() { fiveHourHoveredIndex.value = null }
function onSdMouseLeave() { sevenDayHoveredIndex.value = null }

/**
 * Get the SVG X coordinate for a hovered data point based on its timestamp.
 */
function getTimestampSvgX(hovIdx, timestamps) {
    if (hovIdx === null || !timestamps.length) return 0
    const tRange = tEnd.value - tStart.value
    if (tRange <= 0) return 0
    return ((timestamps[hovIdx] - tStart.value) / tRange) * SVG_WIDTH
}

/**
 * Get the tooltip left percentage for a hovered data point based on its timestamp.
 */
function getTimestampTooltipPct(hovIdx, timestamps) {
    if (hovIdx === null || !timestamps.length) return 0
    const tRange = tEnd.value - tStart.value
    if (tRange <= 0) return 0
    return ((timestamps[hovIdx] - tStart.value) / tRange) * 100
}

const fhCursorX = computed(() => getTimestampSvgX(fiveHourHoveredIndex.value, fhTimestamps.value))
const fhTooltipPct = computed(() => getTimestampTooltipPct(fiveHourHoveredIndex.value, fhTimestamps.value))
const fhHoveredData = computed(() => {
    const idx = fiveHourHoveredIndex.value
    return idx !== null ? fiveHourData.value[idx] : null
})

const sdCursorX = computed(() => getTimestampSvgX(sevenDayHoveredIndex.value, sdTimestamps.value))
const sdTooltipPct = computed(() => getTimestampTooltipPct(sevenDayHoveredIndex.value, sdTimestamps.value))
const sdHoveredData = computed(() => {
    const idx = sevenDayHoveredIndex.value
    return idx !== null ? sevenDayData.value[idx] : null
})

/**
 * Format an ISO datetime string for tooltip display.
 */
function formatTimestamp(isoStr) {
    if (!isoStr) return ''
    const date = new Date(isoStr)
    const locale = navigator.language
    return date.toLocaleString(locale, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    })
}

/**
 * Format a curve value for tooltip display using the curve's decimal precision.
 */
function formatCurveValue(data, curve) {
    const val = data?.[curve.key]
    if (val == null) return '–'
    return val.toFixed(curve.decimals) + '%'
}

function onTabShow(event) {
    const panel = event.detail?.name
    if (panel) {
        activeTab.value = panel
    }
}
</script>

<template>
    <wa-dialog ref="dialogRef" label="Usage History" class="usage-graph-dialog" without-header>
        <div class="usage-graph-content">
            <div class="usage-graph-header">
                <h2 class="usage-graph-title">
                    <wa-icon name="chart-line"></wa-icon>
                    Usage History
                </h2>
                <div class="usage-graph-range-control">
                    <span class="usage-graph-range-label">{{ currentRange.label }}</span>
                    <wa-slider
                        :min.prop="0"
                        :max.prop="RANGE_STEPS.length - 1"
                        :step.prop="1"
                        :value.prop="rangeIndex"
                        @input="onRangeChange"
                        size="small"
                        class="usage-graph-range-slider"
                    ></wa-slider>
                </div>
                <div class="usage-graph-cap-control">
                    <span class="usage-graph-cap-label">Y axis max {{ yCap }}%</span>
                    <wa-slider
                        :min.prop="100"
                        :max.prop="1000"
                        :step.prop="50"
                        :value.prop="yCap"
                        @input="onYCapChange"
                        size="small"
                        class="usage-graph-cap-slider"
                    ></wa-slider>
                </div>
                <wa-button variant="neutral" appearance="plain" size="small" @click="close">
                    <wa-icon name="xmark"></wa-icon>
                </wa-button>
            </div>

            <div class="usage-graph-period-bar">
                <span class="usage-graph-period-label">{{ visiblePeriodLabel }}</span>
            </div>

            <wa-tab-group @wa-tab-show="onTabShow">
                <wa-tab slot="nav" panel="five-hour" :active="activeTab === 'five-hour'">5h Quota</wa-tab>
                <wa-tab slot="nav" panel="seven-day" :active="activeTab === 'seven-day'">7d Quota</wa-tab>

                <!-- ═══ Five Hour Panel ═══ -->
                <wa-tab-panel name="five-hour">
                    <div class="usage-chart-panel">
                        <div v-if="isLoading" class="usage-chart-loading">
                            <wa-spinner></wa-spinner>
                            <span>Loading usage history…</span>
                        </div>
                        <wa-callout v-else-if="errorMessage" variant="danger" size="small">{{ errorMessage }}</wa-callout>
                        <template v-else-if="fiveHourData.length > 0">
                            <div class="usage-chart-wrapper" :class="{ 'is-refreshing': isRefreshing }">
                                <div v-if="isRefreshing" class="usage-chart-refresh-indicator">
                                    <wa-spinner></wa-spinner>
                                </div>
                                <svg
                                    :width="SVG_WIDTH"
                                    aria-hidden="true"
                                    :height="SVG_HEIGHT"
                                    class="usage-chart-svg"
                                    :class="{ 'is-dragging': isDragging }"
                                    :viewBox="`0 0 ${SVG_WIDTH} ${VIEWBOX_HEIGHT}`"
                                    preserveAspectRatio="none"
                                    @mousedown="onChartMouseDown"
                                    @mousemove="onFhMouseMove"
                                    @mouseleave="onFhMouseLeave"
                                    @touchstart="onChartTouchStart"
                                    @touchmove="onChartTouchMove"
                                    @touchend="onChartTouchEnd"
                                >
                                    <defs>
                                        <template v-for="curve in fhCurves" :key="curve.key">
                                            <linearGradient :id="curve.gradientId" x1="0" x2="0" y1="1" y2="0">
                                                <stop offset="0%" :stop-color="colorVars(curve.colorPrefix).g1"></stop>
                                                <stop offset="10%" :stop-color="colorVars(curve.colorPrefix).g2"></stop>
                                                <stop offset="25%" :stop-color="colorVars(curve.colorPrefix).g3"></stop>
                                                <stop offset="50%" :stop-color="colorVars(curve.colorPrefix).g4"></stop>
                                            </linearGradient>
                                            <mask :id="curve.maskId" x="0" y="0" :width="SVG_WIDTH" :height="GRAPH_HEIGHT">
                                                <polyline
                                                    :transform="`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`"
                                                    :points="curve.points"
                                                    fill="transparent"
                                                    :stroke="colorVars(curve.colorPrefix).stroke"
                                                    stroke-width="2"
                                                ></polyline>
                                            </mask>
                                        </template>
                                    </defs>

                                    <!-- 100% reference line -->
                                    <line
                                        :x1="0" :x2="SVG_WIDTH"
                                        :y1="fhRefLineY" :y2="fhRefLineY"
                                        class="usage-reference-line"
                                        stroke="var(--wa-color-text-quiet)"
                                        stroke-width="1"
                                        stroke-dasharray="6 4"
                                        vector-effect="non-scaling-stroke"
                                        opacity="0.5"
                                    />

                                    <g :transform="`translate(0, ${PADDING_TOP})`">
                                        <template v-for="curve in fhCurves" :key="curve.key">
                                            <rect
                                                v-if="curve.visible"
                                                x="0"
                                                y="-2"
                                                :width="SVG_WIDTH"
                                                :height="GRAPH_HEIGHT + 2"
                                                :style="`stroke: none; fill: url(#${curve.gradientId}); mask: url(#${curve.maskId});`"
                                            ></rect>
                                        </template>
                                    </g>

                                    <!-- Vertical cursor line -->
                                    <line
                                        v-if="fiveHourHoveredIndex !== null"
                                        :x1="fhCursorX" :x2="fhCursorX"
                                        y1="0" :y2="VIEWBOX_HEIGHT"
                                        class="sparkline-cursor-line"
                                        stroke="var(--wa-color-text-quiet)"
                                        stroke-width="1"
                                        stroke-dasharray="4 3"
                                        vector-effect="non-scaling-stroke"
                                    />
                                </svg>

                                <!-- Hover tooltip -->
                                <div
                                    v-if="fiveHourHoveredIndex !== null && fhHoveredData && !isTouchDevice"
                                    class="usage-chart-tooltip"
                                    :style="{ left: `${fhTooltipPct}%`, transform: `translateX(-${fhTooltipPct}%)` }"
                                >
                                    <template v-for="curve in fhCurves" :key="curve.key">
                                        <div v-if="curve.visible" class="usage-chart-tooltip-row">
                                            <span :class="`usage-chart-tooltip-swatch usage-chart-tooltip-swatch--${curve.colorPrefix}`"></span>
                                            <span class="usage-chart-tooltip-label">{{ curve.label }}</span>
                                            <span>{{ formatCurveValue(fhHoveredData, curve) }}</span>
                                        </div>
                                    </template>
                                    <div class="usage-chart-tooltip-date">{{ formatTimestamp(fhHoveredData.fetched_at) }}</div>
                                </div>
                            </div>
                            <div class="usage-chart-legend">
                                <div
                                    v-for="curve in fhCurves"
                                    :key="curve.key"
                                    class="usage-chart-legend-item usage-chart-legend-item--clickable"
                                    :class="{ 'is-hidden': !curve.visible }"
                                    @click="toggleCurve(curve.key)"
                                >
                                    <span :class="`usage-chart-legend-swatch usage-chart-legend-swatch--${curve.colorPrefix}`"></span>
                                    <span class="usage-chart-legend-text">{{ curve.label }} (%)</span>
                                </div>
                                <div class="usage-chart-legend-item">
                                    <span class="usage-chart-legend-ref"></span>
                                    <span class="usage-chart-legend-text">100% threshold</span>
                                </div>
                            </div>
                        </template>
                        <div v-else class="usage-chart-empty">
                            <wa-icon name="chart-line"></wa-icon>
                            <span>No data available for this period</span>
                        </div>
                    </div>
                </wa-tab-panel>

                <!-- ═══ Seven Day Panel ═══ -->
                <wa-tab-panel name="seven-day">
                    <div class="usage-chart-panel">
                        <div v-if="isLoading" class="usage-chart-loading">
                            <wa-spinner></wa-spinner>
                            <span>Loading usage history…</span>
                        </div>
                        <wa-callout v-else-if="errorMessage" variant="danger" size="small">{{ errorMessage }}</wa-callout>
                        <template v-else-if="sevenDayData.length > 0">
                            <div class="usage-chart-wrapper" :class="{ 'is-refreshing': isRefreshing }">
                                <div v-if="isRefreshing" class="usage-chart-refresh-indicator">
                                    <wa-spinner></wa-spinner>
                                </div>
                                <svg
                                    :width="SVG_WIDTH"
                                    aria-hidden="true"
                                    :height="SVG_HEIGHT"
                                    class="usage-chart-svg"
                                    :class="{ 'is-dragging': isDragging }"
                                    :viewBox="`0 0 ${SVG_WIDTH} ${VIEWBOX_HEIGHT}`"
                                    preserveAspectRatio="none"
                                    @mousedown="onChartMouseDown"
                                    @mousemove="onSdMouseMove"
                                    @mouseleave="onSdMouseLeave"
                                    @touchstart="onChartTouchStart"
                                    @touchmove="onChartTouchMove"
                                    @touchend="onChartTouchEnd"
                                >
                                    <defs>
                                        <template v-for="curve in sdCurves" :key="curve.key">
                                            <linearGradient :id="curve.gradientId" x1="0" x2="0" y1="1" y2="0">
                                                <stop offset="0%" :stop-color="colorVars(curve.colorPrefix).g1"></stop>
                                                <stop offset="10%" :stop-color="colorVars(curve.colorPrefix).g2"></stop>
                                                <stop offset="25%" :stop-color="colorVars(curve.colorPrefix).g3"></stop>
                                                <stop offset="50%" :stop-color="colorVars(curve.colorPrefix).g4"></stop>
                                            </linearGradient>
                                            <mask :id="curve.maskId" x="0" y="0" :width="SVG_WIDTH" :height="GRAPH_HEIGHT">
                                                <polyline
                                                    :transform="`translate(0, ${GRAPH_HEIGHT}) scale(1,-1)`"
                                                    :points="curve.points"
                                                    fill="transparent"
                                                    :stroke="colorVars(curve.colorPrefix).stroke"
                                                    stroke-width="2"
                                                ></polyline>
                                            </mask>
                                        </template>
                                    </defs>

                                    <!-- 100% reference line -->
                                    <line
                                        :x1="0" :x2="SVG_WIDTH"
                                        :y1="sdRefLineY" :y2="sdRefLineY"
                                        class="usage-reference-line"
                                        stroke="var(--wa-color-text-quiet)"
                                        stroke-width="1"
                                        stroke-dasharray="6 4"
                                        vector-effect="non-scaling-stroke"
                                        opacity="0.5"
                                    />

                                    <g :transform="`translate(0, ${PADDING_TOP})`">
                                        <template v-for="curve in sdCurves" :key="curve.key">
                                            <rect
                                                v-if="curve.visible"
                                                x="0"
                                                y="-2"
                                                :width="SVG_WIDTH"
                                                :height="GRAPH_HEIGHT + 2"
                                                :style="`stroke: none; fill: url(#${curve.gradientId}); mask: url(#${curve.maskId});`"
                                            ></rect>
                                        </template>
                                    </g>

                                    <!-- Vertical cursor line -->
                                    <line
                                        v-if="sevenDayHoveredIndex !== null"
                                        :x1="sdCursorX" :x2="sdCursorX"
                                        y1="0" :y2="VIEWBOX_HEIGHT"
                                        class="sparkline-cursor-line"
                                        stroke="var(--wa-color-text-quiet)"
                                        stroke-width="1"
                                        stroke-dasharray="4 3"
                                        vector-effect="non-scaling-stroke"
                                    />
                                </svg>

                                <!-- Hover tooltip -->
                                <div
                                    v-if="sevenDayHoveredIndex !== null && sdHoveredData && !isTouchDevice"
                                    class="usage-chart-tooltip"
                                    :style="{ left: `${sdTooltipPct}%`, transform: `translateX(-${sdTooltipPct}%)` }"
                                >
                                    <template v-for="curve in sdCurves" :key="curve.key">
                                        <div v-if="curve.visible" class="usage-chart-tooltip-row">
                                            <span :class="`usage-chart-tooltip-swatch usage-chart-tooltip-swatch--${curve.colorPrefix}`"></span>
                                            <span class="usage-chart-tooltip-label">{{ curve.label }}</span>
                                            <span>{{ formatCurveValue(sdHoveredData, curve) }}</span>
                                        </div>
                                    </template>
                                    <div class="usage-chart-tooltip-date">{{ formatTimestamp(sdHoveredData.fetched_at) }}</div>
                                </div>
                            </div>
                            <div class="usage-chart-legend">
                                <div
                                    v-for="curve in sdCurves"
                                    :key="curve.key"
                                    class="usage-chart-legend-item usage-chart-legend-item--clickable"
                                    :class="{ 'is-hidden': !curve.visible }"
                                    @click="toggleCurve(curve.key)"
                                >
                                    <span :class="`usage-chart-legend-swatch usage-chart-legend-swatch--${curve.colorPrefix}`"></span>
                                    <span class="usage-chart-legend-text">{{ curve.label }} (%)</span>
                                </div>
                                <div class="usage-chart-legend-item">
                                    <span class="usage-chart-legend-ref"></span>
                                    <span class="usage-chart-legend-text">100% threshold</span>
                                </div>
                            </div>
                        </template>
                        <div v-else class="usage-chart-empty">
                            <wa-icon name="chart-line"></wa-icon>
                            <span>No data available for this period</span>
                        </div>
                    </div>
                </wa-tab-panel>
            </wa-tab-group>
        </div>
    </wa-dialog>
</template>

<style scoped>
.usage-graph-dialog {
    --width: min(1200px, calc(100vw - 2rem));
    --max-height: min(90vh, calc(100vh - 2rem));
}

.usage-graph-dialog::part(body) {
    padding: 0;
}

.usage-graph-content {
    display: flex;
    flex-direction: column;
}

.usage-graph-header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    padding: var(--wa-space-m) var(--wa-space-l);
    border-bottom: 1px solid var(--wa-color-border);
    gap: var(--wa-space-m);
}

.usage-graph-title {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-size: var(--wa-font-size-l);
    font-weight: var(--wa-font-weight-bold);
    margin: 0;
    white-space: nowrap;
}

/* Range slider control in header */
.usage-graph-range-control {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex: 1;
    justify-content: center;
}

.usage-graph-range-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    font-weight: 600;
    min-width: 5.5em;
    text-align: center;
}

.usage-graph-range-slider {
    width: 8rem;
}

/* Y-axis cap slider control in header */
.usage-graph-cap-control {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
}

.usage-graph-cap-label {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    font-weight: 600;
    min-width: 5.5em;
    text-align: center;
    white-space: nowrap;
}

.usage-graph-cap-slider {
    width: 6rem;
}

/* Visible period date range */
.usage-graph-period-bar {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-2xs) var(--wa-space-l);
    border-bottom: 1px solid var(--wa-color-border);
}

.usage-graph-period-label {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-variant-numeric: tabular-nums;
}

.usage-chart-panel {
    padding: var(--wa-space-m) var(--wa-space-l) var(--wa-space-l);
    min-height: 260px;
    display: flex;
    flex-direction: column;
}

.usage-chart-wrapper {
    position: relative;
    width: 100%;
    user-select: none;
}

.usage-chart-wrapper.is-refreshing .usage-chart-svg {
    opacity: 0.4;
    transition: opacity 0.15s ease;
}

.usage-chart-refresh-indicator {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 5;
    pointer-events: none;
}

.usage-chart-svg {
    display: block;
    width: 100%;
    height: 280px;
    border-radius: var(--wa-border-radius-m);
    cursor: grab;
    touch-action: pan-y;
}

.usage-chart-svg.is-dragging {
    cursor: grabbing;
}

/* 100% reference line */
.usage-reference-line {
    pointer-events: none;
}

/* Vertical cursor line inside SVG */
.sparkline-cursor-line {
    pointer-events: none;
}

/* Loading state */
.usage-chart-loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xl);
    color: var(--wa-color-text-quiet);
    flex: 1;
}

/* Empty state */
.usage-chart-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xl);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    flex: 1;
}

.usage-chart-empty wa-icon {
    font-size: var(--wa-font-size-2xl);
    opacity: 0.4;
}

/* Legend */
.usage-chart-legend {
    display: flex;
    justify-content: center;
    gap: var(--wa-space-l);
    margin-top: var(--wa-space-s);
}

.usage-chart-legend-item {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.usage-chart-legend-item--clickable {
    cursor: pointer;
    user-select: none;
    transition: opacity 0.15s ease;
}

.usage-chart-legend-item--clickable:hover {
    opacity: 0.8;
}

.usage-chart-legend-item.is-hidden .usage-chart-legend-swatch {
    background: var(--wa-color-text-quiet) !important;
    opacity: 0.3;
}

.usage-chart-legend-item.is-hidden .usage-chart-legend-text {
    opacity: 0.4;
    text-decoration: line-through;
}

.usage-chart-legend-swatch {
    display: inline-block;
    width: 12px;
    height: 3px;
    border-radius: 1px;
}

.usage-chart-legend-swatch--green {
    background: var(--sparkline-green-gradient-color-3);
}

.usage-chart-legend-swatch--blue {
    background: var(--sparkline-blue-gradient-color-3);
}

.usage-chart-legend-swatch--red {
    background: var(--sparkline-red-gradient-color-3);
}

.usage-chart-legend-swatch--orange {
    background: var(--sparkline-orange-gradient-color-3);
}

.usage-chart-legend-swatch--purple {
    background: var(--sparkline-purple-gradient-color-3);
}

.usage-chart-legend-swatch--pink {
    background: var(--sparkline-pink-gradient-color-3);
}

.usage-chart-legend-ref {
    display: inline-block;
    width: 12px;
    height: 0;
    border-top: 1px dashed var(--wa-color-text-quiet);
    opacity: 0.6;
}

.usage-chart-legend-text {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

/* Hover tooltip */
.usage-chart-tooltip {
    position: absolute;
    bottom: 100%;
    margin-bottom: var(--wa-space-2xs);
    pointer-events: none;
    z-index: 10;
    background: var(--wa-color-surface-raised);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-xs) var(--wa-space-s);
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-normal);
    white-space: nowrap;
    box-shadow: var(--wa-shadow-s);
}

.usage-chart-tooltip-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.usage-chart-tooltip-label {
    flex: 1;
    margin-right: var(--wa-space-s);
}

.usage-chart-tooltip-swatch {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
}

.usage-chart-tooltip-swatch--green {
    background: var(--sparkline-green-gradient-color-3);
}

.usage-chart-tooltip-swatch--blue {
    background: var(--sparkline-blue-gradient-color-3);
}

.usage-chart-tooltip-swatch--red {
    background: var(--sparkline-red-gradient-color-3);
}

.usage-chart-tooltip-swatch--orange {
    background: var(--sparkline-orange-gradient-color-3);
}

.usage-chart-tooltip-swatch--purple {
    background: var(--sparkline-purple-gradient-color-3);
}

.usage-chart-tooltip-swatch--pink {
    background: var(--sparkline-pink-gradient-color-3);
}

.usage-chart-tooltip-date {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
    margin-top: var(--wa-space-3xs);
}
</style>
