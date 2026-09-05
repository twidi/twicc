import { parseCodeModeScript } from './parseCodeModeScript.js'
import { formatToolNameForHeader } from '../../utils/toolNames.js'

const MCP_TOOL_NAME_PREFIX = 'mcp__'
const VIEW_IMAGE_TOOL_NAME = 'view_image'
const UPDATE_PLAN_TOOL_NAME = 'update_plan'
const WEB_RUN_TOOL_NAME = 'web__run'
// Hosted image generation on GPT-5.6 (``image_gen`` namespace). Shared with
// ``toolHelpers.js`` so the direct and the code-mode-wrapped call render alike.
export const IMAGE_GEN_TOOL_NAME = 'image_gen__imagegen'

const WEB_OPERATION_DEFINITIONS = [
    { key: 'search_query', category: 'search', label: 'Search' },
    { key: 'image_query', category: 'search', label: 'Image search' },
    { key: 'open', category: 'fetch', label: 'Open' },
    { key: 'click', category: 'fetch', label: 'Click' },
    { key: 'find', category: 'fetch', label: 'Find' },
    { key: 'screenshot', category: 'fetch', label: 'Screenshot' },
    { key: 'finance', category: 'other', label: 'Finance' },
    { key: 'weather', category: 'other', label: 'Weather' },
    { key: 'sports', category: 'other', label: 'Sports' },
    { key: 'time', category: 'other', label: 'Time' },
]

function isObject(value) {
    return value && typeof value === 'object' && !Array.isArray(value)
}

function nonEmptyArray(input, key) {
    const value = input?.[key]
    return Array.isArray(value) ? value.filter(isObject) : []
}

function compactStrings(values) {
    return [...new Set(values.filter((value) => typeof value === 'string' && value.trim())
        .map((value) => value.trim()))]
}

function searchSummaryItems(input) {
    return compactStrings([
        ...nonEmptyArray(input, 'search_query').map((item) => item.q),
        ...nonEmptyArray(input, 'image_query').map((item) => item.q),
    ])
}

function fetchSummaryItems(input) {
    const items = []
    for (const item of nonEmptyArray(input, 'open')) {
        if (typeof item.ref_id === 'string' && item.ref_id.trim()) items.push(item.ref_id)
    }
    for (const item of nonEmptyArray(input, 'click')) {
        if (typeof item.ref_id !== 'string' || !item.ref_id.trim()) continue
        const suffix = Number.isInteger(item.id) ? ` · link ${item.id}` : ''
        items.push(`${item.ref_id}${suffix}`)
    }
    for (const item of nonEmptyArray(input, 'find')) {
        if (typeof item.ref_id !== 'string' || !item.ref_id.trim()) continue
        const suffix = typeof item.pattern === 'string' && item.pattern.trim()
            ? ` · ${item.pattern.trim()}`
            : ''
        items.push(`${item.ref_id}${suffix}`)
    }
    for (const item of nonEmptyArray(input, 'screenshot')) {
        if (typeof item.ref_id !== 'string' || !item.ref_id.trim()) continue
        const suffix = Number.isInteger(item.pageno) ? ` · page ${item.pageno + 1}` : ''
        items.push(`${item.ref_id}${suffix}`)
    }
    return compactStrings(items)
}

/**
 * Describe a resolved ``web__run`` argument object for the tool card.
 *
 * Search-only calls become ``Web search``; navigation-only calls become
 * ``Web fetch``; compound calls and hosted data helpers (weather, finance,
 * sports, time) use the neutral ``Web`` label. The returned summary items
 * are already deduplicated and safe to render as plain text or links.
 */
export function describeWebRun(input) {
    if (!isObject(input)) return null
    const operations = WEB_OPERATION_DEFINITIONS.filter(({ key }) => nonEmptyArray(input, key).length > 0)
    if (operations.length === 0) return null

    const categories = new Set(operations.map(({ category }) => category))
    let kind = 'web'
    let summaryItems = operations.map(({ label }) => label)
    if (categories.size === 1 && categories.has('search')) {
        kind = 'search'
        summaryItems = searchSummaryItems(input)
    } else if (categories.size === 1 && categories.has('fetch')) {
        kind = 'fetch'
        summaryItems = fetchSummaryItems(input)
    }

    return {
        kind,
        summaryItems: summaryItems.length > 0 ? summaryItems : operations.map(({ label }) => label),
        operationKeys: operations.map(({ key }) => key),
    }
}

/**
 * Extract the base64 ``data:`` URLs from the ``input_image`` parts of a tool
 * result (``view_image``, ``image_gen__imagegen``). ``result`` is the shell's
 * ``displayResult``: a single row payload (``function_call_output`` /
 * ``custom_tool_call_output``), or the row array of a chained code-mode
 * ``exec`` — a wrapped ``imagegen`` that outlives its ``yield_time_ms``
 * returns ``Script running`` first and the image only lands in a later
 * ``wait`` chunk. Returns ``[]`` when no row carries a usable ``input_image``
 * part.
 */
export function extractInputImageUrls(result) {
    const rows = Array.isArray(result) ? result : [result]
    const urls = []
    for (const row of rows) {
        const output = row?.output
        if (!Array.isArray(output)) continue
        for (const part of output) {
            if (
                part && typeof part === 'object' &&
                part.type === 'input_image' &&
                typeof part.image_url === 'string' && part.image_url
            ) {
                urls.push(part.image_url)
            }
        }
    }
    return urls
}

/**
 * Return the single resolved nested call when TwiCC has a dedicated display
 * path for its argument shape. Other scripts intentionally fall back to the
 * generic Run code card.
 */
export function resolveCodeModeCall(input) {
    const source = typeof input === 'string' ? input : input?.input
    if (typeof source !== 'string' || !source) return null
    const { calls } = parseCodeModeScript(source)
    if (calls.length !== 1 || !calls[0].resolved) return null
    const call = calls[0]
    if (call.name === 'exec_command') {
        const arg = call.arg
        return isObject(arg) && typeof arg.cmd === 'string' && arg.cmd ? call : null
    }
    if (call.name === 'apply_patch') {
        return typeof call.arg === 'string' && call.arg ? call : null
    }
    if (call.name === VIEW_IMAGE_TOOL_NAME) {
        const arg = call.arg
        return isObject(arg) && typeof arg.path === 'string' && arg.path ? call : null
    }
    if (call.name === UPDATE_PLAN_TOOL_NAME) {
        return isObject(call.arg) ? call : null
    }
    if (call.name === WEB_RUN_TOOL_NAME) {
        return describeWebRun(call.arg) ? call : null
    }
    if (call.name === IMAGE_GEN_TOOL_NAME) {
        const arg = call.arg
        return isObject(arg) && typeof arg.prompt === 'string' && arg.prompt.trim() ? call : null
    }
    if (call.name.startsWith(MCP_TOOL_NAME_PREFIX)) {
        const arg = call.arg
        return arg === null || isObject(arg) ? call : null
    }
    return null
}

/**
 * Summarize every nested call in a code-mode script using the same naming
 * chain as a normal tool card: an optional provider label first, then the
 * shared MCP/general formatter. Equal display names are grouped in source
 * order and receive a multiplication suffix.
 */
export function summarizeCodeModeCalls(input, getHeaderLabel = null) {
    const source = typeof input === 'string' ? input : input?.input
    if (typeof source !== 'string' || !source) return ''
    const { calls } = parseCodeModeScript(source)
    if (calls.length === 0) return ''

    const counts = new Map()
    for (const call of calls) {
        const arg = call.resolved ? call.arg : null
        const forcedLabel = typeof getHeaderLabel === 'function'
            ? getHeaderLabel(call.name, arg)
            : null
        const displayName = formatToolNameForHeader(call.name, forcedLabel)
        counts.set(displayName, (counts.get(displayName) ?? 0) + 1)
    }
    return [...counts.entries()]
        .map(([name, count]) => (count > 1 ? `${name} ×${count}` : name))
        .join(', ')
}
