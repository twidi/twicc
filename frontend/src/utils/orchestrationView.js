// Pure logic of the Orchestration tab (no Vue, no store, no router): state buckets, subtree and
// parent lookup, the time bar's range and geometry, the annotation tree, the subagent model label.
// Design: docs/plans/2026-10-05-orchestration-tab-redesign-spec.md.

// ── State buckets (spec 3.2) ────────────────────────────────────────────────
export const BUCKET_ORDER = ['working', 'awaiting', 'idle', 'stopped']

// State colours of the summary tile (rows and donut arcs) and, except for ``stopped``, of the node borders:
// a stopped node is neutral grey in the summary and has its own fainter neutral border (below). ``starting``
// is in the working bucket, so its border is blue although its icon is warning-coloured (the one place the
// border does not follow the icon).
export const BUCKET_COLORS = {
    working: 'var(--wa-color-blue-60)',
    awaiting: 'var(--wa-color-warning-60)',
    idle: 'var(--wa-color-success-60)',
    stopped: 'var(--wa-color-neutral-50)',
}
// The card border: a stopped node is neutral at reduced opacity (fainter than the summary's grey).
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

/**
 * ``nextNodes`` (a fresh topology payload's ``nodes``) with the PREVIOUS object substituted for every node
 * whose content is unchanged (same id, same JSON), so Vue does not re-render the cards of unchanged nodes.
 * Follows the next order; nodes absent from ``nextNodes`` are dropped.
 */
export function reuseUnchangedNodes(previousNodes, nextNodes) {
    const previousById = new Map((previousNodes ?? []).map(node => [node.id, node]))
    return nextNodes.map(node => {
        const previous = previousById.get(node.id)
        return previous && JSON.stringify(previous) === JSON.stringify(node) ? previous : node
    })
}

/**
 * ``nextTree`` ({ id, children }) with previous objects substituted: the whole previous tree when it is
 * identical, otherwise a new node whose children reuse the previous subtrees (matched by id) that did not change.
 */
export function reuseUnchangedTree(previousTree, nextTree) {
    if (!previousTree || !nextTree || previousTree.id !== nextTree.id) return nextTree
    if (JSON.stringify(previousTree) === JSON.stringify(nextTree)) return previousTree
    const previousChildren = new Map((previousTree.children ?? []).map(child => [child.id, child]))
    return {
        ...nextTree,
        children: (nextTree.children ?? []).map(child => reuseUnchangedTree(previousChildren.get(child.id), child)),
    }
}

/**
 * ``nextMap`` (an object of plain values) with the previous value objects substituted where equal (same JSON).
 * Returns ``previousMap`` itself when the keys and every value are unchanged, so a prop holding the map does
 * not change either.
 */
export function reuseUnchangedMap(previousMap, nextMap) {
    if (!previousMap) return nextMap
    const keys = Object.keys(nextMap)
    let identical = keys.length === Object.keys(previousMap).length
    const result = {}
    for (const key of keys) {
        const previous = previousMap[key]
        const keep = previous !== undefined && JSON.stringify(previous) === JSON.stringify(nextMap[key])
        result[key] = keep ? previous : nextMap[key]
        if (!keep) identical = false
    }
    return identical ? previousMap : result
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
 * Bar geometry of a whole tree, each node relative to the span of its DIRECT parent.
 *
 * ``root``: ``{ id, children }`` (recursive); ``itemsById``: a Map or plain object id -> item (same shape as
 * the ``items`` of ``computeTimeline``). The root is relative to itself (full track). The children of a node
 * share ``computeTimeline([node, ...children])``: the parent's span, extended only if a child falls outside it.
 * An id with no item (or no start) gets no entry; a node without item still ranges its children.
 * Returns a plain object id -> ``{ left, width, live }``.
 */
export function computeTreeGeometry(root, itemsById, now) {
    const lookup = itemsById instanceof Map ? id => itemsById.get(id) : id => itemsById?.[id]
    const geometry = {}
    const itemsOf = nodes => nodes.map(n => lookup(n.id)).filter(Boolean)
    const rootItem = lookup(root.id)
    if (rootItem) Object.assign(geometry, computeTimeline([rootItem], now).geometry)
    const visit = (node) => {
        const children = node.children ?? []
        if (!children.length) return
        const { geometry: local } = computeTimeline(itemsOf([node, ...children]), now)
        for (const child of children) {
            if (local[child.id]) geometry[child.id] = local[child.id]
            visit(child)
        }
    }
    visit(root)
    return geometry
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

/**
 * The card's tags: every nested object value is decomposed into its leaf entries, as if they were dotted
 * annotations. Arrays, scalars, null and empty objects are leaves. A key with an empty segment is not split.
 * Returns ``{ path: string[], key: string, value }[]`` sorted by ``key`` (plain string order).
 */
export function flattenAnnotations(annotations) {
    const out = []
    const walk = (base, key, value) => {
        const parts = key.split('.')
        const path = [...base, ...(parts.some(part => part === '') ? [key] : parts)]
        if (isPlainObject(value) && Object.keys(value).length) {
            for (const [nestedKey, nested] of Object.entries(value)) walk(path, nestedKey, nested)
        } else {
            out.push({ path, key: path.join('.'), value })
        }
    }
    if (isPlainObject(annotations)) {
        for (const [key, value] of Object.entries(annotations)) walk([], key, value)
    }
    return out.sort((a, b) => byString(a.key, b.key))
}

/**
 * Splits the longest path-segment prefix shared by ALL entries (from ``flattenAnnotations``), capped so every
 * entry keeps at least one segment. Returns ``{ prefix: string | null, entries: { key, fullKey, value }[] }``
 * where ``key`` is the remaining key.
 */
export function splitCommonPrefix(entries) {
    let length = 0
    if (entries.length) {
        const cap = Math.min(...entries.map(entry => entry.path.length)) - 1
        while (length < cap && entries.every(entry => entry.path[length] === entries[0].path[length])) length += 1
    }
    return {
        prefix: length ? entries[0].path.slice(0, length).join('.') : null,
        entries: entries.map(entry => ({
            key: entry.path.slice(length).join('.'),
            fullKey: entry.key,
            value: entry.value,
        })),
    }
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

/**
 * Line index (0-based) of each element of a wrapping flex row, from their `offsetTop`s in flow order:
 * the first element is on line 0, and a new line starts whenever the top grows.
 */
export function lineIndices(tops) {
    const lines = []
    let line = -1
    let lineTop = null
    for (const top of tops) {
        if (lineTop === null || top > lineTop) {
            line += 1
            lineTop = top
        }
        lines.push(line)
    }
    return lines
}

/**
 * One step of the "fit tags in `maxLines` lines" loop. `entryLines` are the line indices of the entry tags
 * currently displayed, `chevronLine` the one of the button that follows them. Returns the entry count to
 * display next: the same count when the button is within the budget (stable), else fewer, never more than
 * the entries within the budget and always at least one fewer than shown (the button needs room too).
 */
export function nextFitCount({ entryLines, chevronLine, maxLines }) {
    const shown = entryLines.length
    if (chevronLine < maxLines || shown === 0) return shown
    const inBudget = entryLines.filter((line) => line < maxLines).length
    return Math.min(shown - 1, inBudget)
}
