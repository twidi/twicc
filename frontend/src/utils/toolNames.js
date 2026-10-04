/**
 * Tool-name display helpers shared between the tool-card shell and
 * per-provider helpers.
 */

/**
 * One name segment → sentence-cased words. Splits both ``snake_case`` and
 * ``camelCase``/``PascalCase`` boundaries, then upper-cases the first letter
 * and lower-cases the rest:
 *   ``foo_bar`` → ``Foo bar``, ``AskUserQuestion`` → ``Ask user question``,
 *   ``WebSearch`` → ``Web search``, ``Read`` → ``Read``.
 *
 * Exported for the identifiers that are not tool names but follow the same
 * machine-written shape and deserve the same sentence case — Codex's
 * ``spawn_agent`` task names (``tweak_display_test`` → ``Tweak display
 * test``).
 */
export function humanizeToolSegment(raw) {
    const spaced = raw
        .replace(/([a-z0-9])([A-Z])/g, '$1 $2')     // fooBar → foo Bar
        .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')  // HTMLEdit → HTML Edit
        .replace(/_+/g, ' ')
        .trim()
    return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase() : ''
}

/**
 * Header label for tools that have neither a Task ``displayName`` nor a
 * provider-specific label. When ``forcedLabel`` is supplied, it wins exactly
 * like the tool-card shell's ``headerLabel`` branch; otherwise the shared MCP
 * and general-name rules below apply.
 *
 * - Fully-qualified / MCP names (containing ``__`` — Claude Code's
 *   ``mcp__server__tool``, Codex's
 *   ``mcp__chrome_devtools__take_screenshot``) split on ``__``; the ``mcp``
 *   prefix renders as the ``MCP`` acronym, each remaining ``server`` / ``tool``
 *   segment is sentence-cased, and they join with `` : ``:
 *   ``mcp__chrome_devtools__take_screenshot`` → ``MCP : Chrome devtools : Take
 *   screenshot``. Leading / trailing underscores per segment are dropped first
 *   (Codex bare MCP names often start with ``_``).
 * - Every other name (no ``__`` — ``request_user_input``, ``foo_bar``,
 *   ``AskUserQuestion``, ``Read``) is sentence-cased word-by-word →
 *   ``Request user input`` / ``Ask user question`` / ``Read``. So a tool
 *   never surfaces raw (snake_case or PascalCase) in the header without a
 *   per-tool ``getHeaderLabel`` entry.
 */
export function formatToolNameForHeader(rawName, forcedLabel = null) {
    if (typeof forcedLabel === 'string' && forcedLabel) return forcedLabel
    if (typeof rawName !== 'string') return ''
    if (!rawName.includes('__')) {
        return humanizeToolSegment(rawName)
    }
    return rawName
        .split('__')
        .map((s) => s.replace(/^_+|_+$/g, ''))
        .filter(Boolean)
        .map((s) => (s.toLowerCase() === 'mcp' ? 'MCP' : humanizeToolSegment(s)))
        .filter(Boolean)
        .join(' : ')
}

/**
 * Compact duration for a tool-card title: the two most significant non-zero
 * units, from milliseconds up to days (``500ms``, ``45s``, ``1s 500ms``,
 * ``1mn 50s``, ``3h 40mn``, ``2d 3h``). Non-finite or negative input → ``''``.
 */
export function formatDurationMsCompact(ms) {
    if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return ''
    let rest = Math.round(ms)
    const units = [['d', 86400000], ['h', 3600000], ['mn', 60000], ['s', 1000], ['ms', 1]]
    const parts = []
    for (const [label, size] of units) {
        const n = Math.floor(rest / size)
        rest -= n * size
        if (n > 0) parts.push(`${n}${label}`)
        if (parts.length === 2) break
    }
    return parts.length ? parts.join(' ') : '0ms'
}
