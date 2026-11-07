import { i18n } from '../i18n/index.js'

/**
 * Tool-name display helpers shared between the tool-card shell and
 * per-provider helpers.
 */

// Only controlled first-party tool identities select messages. Unknown/custom
// identities retain the existing humanized-name display.
const TOOL_HEADER_KEYS = {
    'Read': 'session.tools.headers.read',
    'Write': 'session.tools.headers.write',
    'Edit': 'session.tools.headers.edit',
    'Grep': 'session.tools.headers.grep',
    'WebFetch': 'session.tools.headers.webFetch',
    'WebSearch': 'session.tools.headers.webSearch',
    'ToolSearch': 'session.tools.headers.searchTools',
    'AskUserQuestion': 'session.tools.headers.askQuestion',
    'Task': 'session.tools.headers.task',
    'Agent': 'session.tools.headers.agent',
    'TaskCreate': 'session.tools.headers.createTask',
    'TaskUpdate': 'session.tools.headers.updateTask',
    'TaskGet': 'session.tools.headers.getTask',
    'TaskList': 'session.tools.headers.listTasks',
    'TaskStop': 'session.tools.headers.stopTask',
    'TaskOutput': 'session.tools.headers.taskOutput',
    'SendMessage': 'session.tools.control.sendMessage',
    'Monitor': 'session.tools.headers.monitor',
    'create_goal': 'session.tools.headers.createGoal',
    'update_goal': 'session.tools.headers.updateGoal',
    'get_goal': 'session.tools.headers.getGoal',
    'request_user_input': 'session.tools.headers.requestInput',
    'request_user_input_async': 'session.tools.headers.requestInput',
    'apply_patch': 'session.tools.headers.edit',
    'update_plan': 'session.tools.headers.todo',
    'exec_command': 'session.tools.headers.shell',
    'view_image': 'session.tools.headers.image',
    'web_search_call': 'session.tools.headers.webSearch',
    'clock__sleep': 'session.tools.headers.sleep',
    'web__run': 'session.tools.headers.web',
    'image_gen__imagegen': 'session.tools.headers.imageGeneration',
    'exec': 'session.tools.headers.runCode',
    'spawn_agent': 'session.tools.headers.spawnAgent',
    'collaboration__spawn_agent': 'session.tools.headers.spawnAgent',
    'collaboration__followup_task': 'session.tools.control.followupTask',
    'collaboration__send_message': 'session.tools.control.sendMessage',
    'collaboration__interrupt_agent': 'session.tools.control.interruptAgent',
    'collaboration__wait_agent': 'session.tools.headers.waitAgent',
    'collaboration__list_agents': 'session.tools.headers.listAgents',
    'list_mcp_resources': 'session.tools.headers.listMcpResources',
    'list_mcp_resource_templates': 'session.tools.headers.listMcpResourceTemplates',
    'read_mcp_resource': 'session.tools.headers.readMcpResource',
}

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
 * - Known first-party tool IDs select translated labels at call time.
 * - Every other name (no ``__`` — ``request_user_input``, ``foo_bar``,
 *   ``AskUserQuestion``, ``Read``) is sentence-cased word-by-word →
 *   ``Request user input`` / ``Ask user question`` / ``Read``. So a tool
 *   never surfaces raw (snake_case or PascalCase) in the header without a
 *   per-tool ``getHeaderLabel`` entry.
 */
export function formatToolNameForHeader(rawName, forcedLabel = null) {
    if (typeof forcedLabel === 'string' && forcedLabel) return forcedLabel
    if (typeof rawName !== 'string') return ''
    if (Object.hasOwn(TOOL_HEADER_KEYS, rawName)) return i18n.global.t(TOOL_HEADER_KEYS[rawName])
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
 * ``1m 50s``, ``3h 40m``, ``2d 3h`` in English). Non-finite or negative input → ``''``.
 */
export function formatDurationMsCompact(ms) {
    if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return ''
    let rest = Math.round(ms)
    const units = [['day', 86400000], ['hour', 3600000], ['minute', 60000], ['second', 1000], ['millisecond', 1]]
    const format = (value, unit) => new Intl.NumberFormat(i18n.global.locale.value, {
        style: 'unit', unit, unitDisplay: 'narrow', maximumFractionDigits: 0,
    }).format(value)
    const parts = []
    for (const [label, size] of units) {
        const n = Math.floor(rest / size)
        rest -= n * size
        if (n > 0) parts.push(format(n, label))
        if (parts.length === 2) break
    }
    return parts.length ? parts.join(' ') : format(0, 'millisecond')
}
