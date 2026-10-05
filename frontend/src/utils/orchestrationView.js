// Pure logic of the Orchestration tab (no Vue, no store, no router): state buckets, subtree and
// parent lookup, the time bar's range and geometry, the annotation tree, the subagent model label.
// Design: docs/plans/2026-10-05-orchestration-tab-redesign-spec.md.

// ── State buckets (spec 3.2) ────────────────────────────────────────────────
export const BUCKET_ORDER = ['working', 'awaiting', 'idle', 'stopped']

// Donut arcs and node borders. ``starting`` is in the working bucket, so its border is blue
// although its icon is warning-coloured (the one place the border does not follow the icon).
export const BUCKET_COLORS = {
    working: 'var(--wa-color-blue-60)',
    awaiting: 'var(--wa-color-warning-60)',
    idle: 'var(--wa-color-success-60)',
    stopped: 'var(--wa-color-neutral-50)',
}
// The card border: a stopped node is neutral at reduced opacity.
export const BUCKET_BORDER_COLORS = {
    ...BUCKET_COLORS,
    stopped: 'color-mix(in oklab, var(--wa-color-neutral-50) 40%, transparent)',
}

export function bucketOfProcessState(state) {
    switch (state) {
        case 'starting':
        case 'assistant_turn':
            return 'working'
        case 'awaiting_user_input':
            return 'awaiting'
        case 'user_turn':
            return 'idle'
        default:
            return 'stopped'
    }
}

export function countBuckets(buckets) {
    const counts = { working: 0, awaiting: 0, idle: 0, stopped: 0 }
    for (const bucket of buckets) counts[bucket] += 1
    return counts
}

// ── Tree helpers ────────────────────────────────────────────────────────────
/** The node ``id`` inside ``tree`` ({ id, children }), at any depth; null when absent. */
export function findSubtree(tree, id) {
    if (!tree) return null
    if (tree.id === id) return tree
    for (const child of tree.children ?? []) {
        const found = findSubtree(child, id)
        if (found) return found
    }
    return null
}

/** The node followed by its descendants, depth first. */
export function flattenTree(node) {
    return [node, ...(node.children ?? []).flatMap(flattenTree)]
}

/** The topology node of the session that spawned ``sessionId``; null when none or not in the payload. */
export function parentOf(nodesById, sessionId) {
    const parentId = nodesById[sessionId]?.session?.spawned_by
    return parentId ? (nodesById[parentId] ?? null) : null
}

export function isoMs(iso) {
    if (!iso) return null
    const ms = Date.parse(iso)
    return Number.isNaN(ms) ? null : ms
}

// ── Time bar (spec 6) ───────────────────────────────────────────────────────
export const MIN_BAR_WIDTH_PERCENT = 1.5

/**
 * Range and per-node bar geometry of a tree.
 *
 * ``items``: ``{ id, start, end, working }`` with ``start``/``end`` in ms (``null`` when unknown).
 * A node with no start has no bar and stays out of the range. The range end is the latest of the
 * non-working ends, every start, and ``now`` when a node is working; a working node's end IS the
 * range end, so its bar reaches the right end of the track whatever the client clock says.
 */
export function computeTimeline(items, now) {
    const ranged = items.filter(item => item.start != null)
    if (!ranged.length) return { range: null, geometry: {} }
    let start = Infinity
    let end = -Infinity
    for (const item of ranged) {
        start = Math.min(start, item.start)
        end = Math.max(end, item.start)
        if (!item.working) end = Math.max(end, item.end ?? item.start)
    }
    if (ranged.some(item => item.working)) end = Math.max(end, now)
    const span = end - start
    const geometry = {}
    for (const item of ranged) {
        const itemEnd = item.working ? end : Math.max(item.end ?? item.start, item.start)
        let left = 0
        let width = 100
        if (span > 0) {
            width = Math.max(((itemEnd - item.start) / span) * 100, MIN_BAR_WIDTH_PERCENT)
            left = Math.min(((item.start - start) / span) * 100, 100 - width)
        }
        geometry[item.id] = { left, width, live: item.working }
    }
    return { range: { start, end, spanSeconds: span / 1000 }, geometry }
}

/**
 * Sum, in seconds, of the durations of the nodes of ``items`` (same shape as ``computeTimeline``), except
 * ``excludeId``. A node's duration follows the bars: nothing without a start; a working node ends at
 * ``rangeEnd``; otherwise it ends at its end, raised to its start. Overlaps are simply summed.
 * Null when no node qualifies.
 */
export function cumulativeSeconds(items, rangeEnd, excludeId = null) {
    let total = 0
    let counted = 0
    for (const item of items) {
        if (item.start == null || item.id === excludeId) continue
        const end = item.working ? rangeEnd : Math.max(item.end ?? item.start, item.start)
        if (end == null) continue
        total += Math.max(end - item.start, 0)
        counted += 1
    }
    return counted ? total / 1000 : null
}

// ── Annotations (spec 5.6) ──────────────────────────────────────────────────
const byString = (a, b) => (a < b ? -1 : a > b ? 1 : 0)
const isPlainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value)

/** Entries sorted by key (plain string order). */
export function annotationEntries(annotations) {
    if (!isPlainObject(annotations)) return []
    return Object.entries(annotations).sort(([a], [b]) => byString(a, b))
}

/** Tag/leaf text: strings as is, numbers and booleans as text, null as ``null``, objects and arrays as compact JSON. */
export function annotationValueText(value) {
    if (value === null) return 'null'
    if (typeof value === 'string') return value
    if (typeof value === 'object') return JSON.stringify(value)
    return String(value)
}

/**
 * The popover's tree: keys split on ``.``, shared prefixes share a level, siblings sorted by name.
 * An object value expands like extra dotted levels. Several values on one path are all kept.
 * Returns ``{ name, values: string[], children }[]``.
 */
export function buildAnnotationTree(annotations) {
    const root = new Map()
    const ensure = (path) => {
        let level = root
        let node = null
        for (const name of path) {
            if (!level.has(name)) level.set(name, { name, values: [], children: new Map() })
            node = level.get(name)
            level = node.children
        }
        return node
    }
    // ``base`` is the path of the object the key belongs to ([] at the top level).
    const add = (base, key, value) => {
        const parts = key.split('.')
        if (parts.some(part => part === '')) {
            // Not split: the whole key is one leaf, and an object value there is compact JSON text.
            ensure([...base, key]).values.push(annotationValueText(value))
            return
        }
        const path = [...base, ...parts]
        if (isPlainObject(value) && Object.keys(value).length) {
            ensure(path)
            for (const [nestedKey, nested] of Object.entries(value).sort(([a], [b]) => byString(a, b))) {
                add(path, nestedKey, nested)
            }
        } else {
            ensure(path).values.push(annotationValueText(value))
        }
    }
    for (const [key, value] of annotationEntries(annotations)) add([], key, value)
    const finish = (level) => [...level.values()]
        .sort((a, b) => byString(a.name, b.name))
        .map(node => ({ name: node.name, values: node.values, children: finish(node.children) }))
    return finish(root)
}

// ── Subagent model (spec 5.4) ───────────────────────────────────────────────
/**
 * Label of a subagent's model (``{ raw, family, version }``), formatted as ``SessionHeader`` formats the
 * session's last used model. Null without a model, or without family or version.
 */
export function agentModelLabel(model) {
    if (!model?.family || !model?.version) return null
    return `${model.family} ${model.version}`
}
