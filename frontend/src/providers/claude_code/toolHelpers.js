/**
 * Tool-rendering helpers for Claude Code sessions.
 *
 * Consumed by:
 *   - components/session/detail/items/ToolUseContent.vue (rich card)
 *   - components/session/detail/items/WorkingAssistantMessage.vue (status line)
 *
 * Encapsulates everything that's specific to Claude Code's tool catalogue
 * (Edit/Write/Read/Bash/Grep/Glob/Web{*}/Skill/Task/MCP/...) — naming,
 * summary formatting, which Vue component renders an input or a result, the
 * JsonHumanView overrides, the file-change stats source, and the backend-
 * patch extractor. The generic shell never branches on tool name itself.
 */

import { PROVIDER, AGENT_TOOL_NAMES } from '../../constants'
import { getTodoDescription, isValidTodos } from '../../utils/todoList'
import { BaseToolHelpers } from '../baseHelpers'
import { agentControlHeaderLabel, agentControlExpectedCount } from './agentControlTools'
import { capitalize } from '../utils/format'
import { formatRelativePath, fileIconFor } from '../utils/path'

import EditContent from '../../components/session/detail/items/claude_code/EditContent.vue'
import WriteContent from '../../components/session/detail/items/claude_code/WriteContent.vue'
import TodoContent from '../../components/session/detail/items/TodoContent.vue'
import TaskByIdContent from '../../components/session/detail/items/claude_code/TaskByIdContent.vue'
import ReadResultContent from '../../components/session/detail/items/claude_code/ReadResultContent.vue'
import BashResultContent from '../../components/session/detail/items/claude_code/BashResultContent.vue'
import WebContentResult from '../../components/session/detail/items/claude_code/WebContentResult.vue'
import MonitorResultContent from '../../components/session/detail/items/claude_code/MonitorResultContent.vue'

import DescriptionSummary from '../../components/session/detail/items/summary/DescriptionSummary.vue'
import SkillSummary from '../../components/session/detail/items/claude_code/summary/SkillSummary.vue'
import GrepSummary from '../../components/session/detail/items/summary/GrepSummary.vue'
import GlobSummary from '../../components/session/detail/items/claude_code/summary/GlobSummary.vue'
import WebFetchSummary from '../../components/session/detail/items/summary/WebFetchSummary.vue'
import WebSearchSummary from '../../components/session/detail/items/summary/WebSearchSummary.vue'
import ToolSearchSummary from '../../components/session/detail/items/claude_code/summary/ToolSearchSummary.vue'
import TodoSummary from '../../components/session/detail/items/summary/TodoSummary.vue'

const FILE_PATH_TOOLS = new Set(['Edit', 'Write', 'Read'])
const FILE_CHANGE_TOOLS = new Set(['Edit', 'Write'])
const DIRECT_CONTENT_TOOLS = new Set(['Bash', 'WebFetch', 'WebSearch'])
const MONITOR_TOOL_NAME = 'Monitor'
// Built-in task-tracking tools (``TaskCreate`` / ``TaskUpdate`` / ``TaskGet`` /
// ``TaskList``) share a hidden result section. Each keeps its raw header
// label so the operation stays readable. The canonical task payload is
// spliced into the tool_use block by the backend (see ``twiccTaskData`` /
// ``twiccTasksTotal`` / ``twiccTasksData`` in
// ``providers/claude_code/compute.py``). The three by-id tools render a
// compact ``TodoSummary``-style summary; ``TaskList`` renders a count after
// the header and the full plan body in ``TodoContent``.
const TASK_TOOLS = new Set(['TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList'])
const TASK_SUBAGENT_LABELS = {
    explore: 'exploring',
    plan: 'planning',
    bash: 'bashing',
}

const INPUT_OVERRIDES = {
    Bash: { command: { valueType: 'string-code', language: 'bash' } },
}

const CAT_N_LINE_RE = /^(\s*\d+)[→\t](.*)$/

// ─── Helpers private to this module ─────────────────────────────────────

function getDisplayName(name, input) {
    if (AGENT_TOOL_NAMES.has(name)) {
        const sat = input?.subagent_type
        if (!sat || sat === 'general-purpose') return null
        const colonIdx = sat.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(sat.slice(colonIdx + 1)),
                namespace: capitalize(sat.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(sat), namespace: null }
    }
    if (name === 'Skill' && input?.skill) {
        const skill = input.skill
        const colonIdx = skill.indexOf(':')
        if (colonIdx >= 0) {
            return {
                name: capitalize(skill.slice(colonIdx + 1)),
                namespace: capitalize(skill.slice(0, colonIdx)),
            }
        }
        return { name: capitalize(skill), namespace: null }
    }
    return null
}

function buildGrepInline(pattern, fileType, path) {
    const parts = []
    if (pattern) parts.push(pattern)
    if (fileType) parts.push(`in ${fileType} files`)
    if (path) parts.push(`in ${path}`)
    return parts.length ? parts.join(' ') : null
}

function parseCatNContent(content) {
    if (typeof content !== 'string') return null
    const lines = content.split('\n')
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    if (lines.length === 0) return null
    const firstNonEmpty = lines.find(l => l.length > 0)
    if (!firstNonEmpty || !CAT_N_LINE_RE.test(firstNonEmpty)) return null
    return true
}

function getDirectContent(name, result) {
    if (!DIRECT_CONTENT_TOOLS.has(name)) return null
    const content = typeof result === 'string' ? result : result?.content
    if (typeof content !== 'string' || !content) return null
    return content
}

/**
 * Map a backend-spliced ``twiccTasksData`` entry to the ``todo`` shape that
 * ``TodoContent`` understands. Tasks carry ``{id, subject, activeForm,
 * description, status, blocks, blockedBy}``; TodoContent reads ``status``
 * plus ``content`` / ``activeForm`` (with ``content`` winning by default).
 * We project ``subject`` onto ``content`` so the list reads like a normal
 * todo plan.
 */
function tasksDataToTodos(tasksData) {
    if (!Array.isArray(tasksData)) return null
    const todos = []
    for (const task of tasksData) {
        if (!task || typeof task !== 'object') continue
        const status = task.status
        if (typeof status !== 'string') continue
        const content = task.subject
        const activeForm = task.activeForm
        if (typeof content !== 'string' && typeof activeForm !== 'string') continue
        const todo = { status }
        if (typeof content === 'string') todo.content = content
        if (typeof activeForm === 'string') todo.activeForm = activeForm
        todos.push(todo)
    }
    return todos.length > 0 ? todos : null
}

/**
 * Build the ``parts`` array consumed by ``TodoSummary`` for the three
 * by-id task tools (TaskCreate / TaskUpdate / TaskGet).
 *
 *   - TaskCreate → ``[{text: '<id>'}, {text: subject, status: 'pending'}]``
 *     (the id slot is dropped when the backend couldn't allocate one — e.g.
 *     the TaskCreate input was missing a subject).
 *   - TaskUpdate → ``[{text: '<id>/<total>'}, {text: activeForm || subject,
 *     status: input.status}]`` (status is the explicit target the call sets).
 *   - TaskGet    → ``[{text: '<id>/<total>'}, {text: subject, status:
 *     taskData.status}]`` (no status in the input — use the in-memory task
 *     state at the moment the tool_use was processed).
 *
 * ``twiccTaskData`` / ``twiccTasksTotal`` are spliced into the tool_use
 * block by the backend; we receive them via the helper's ``options``
 * argument (forwarded by ``ToolUseContent.vue`` from ``props.extra``).
 * Returns ``null`` when nothing meaningful can be rendered, so the shell
 * skips the summary description entirely rather than showing a stub.
 * ``TaskList`` is handled separately in ``getSummaryRendering`` — it has
 * a count-only summary plus a TodoContent-style detail.
 */
function buildTaskSummaryParts(name, input, options) {
    const safeInput = input || {}
    const taskData = options?.twiccTaskData || null
    const tasksTotal = options?.twiccTasksTotal ?? null

    if (name === 'TaskCreate') {
        const subject = taskData?.subject || safeInput.subject
        if (!subject) return null
        const id = taskData?.id ?? null
        const parts = []
        if (id != null) parts.push({ text: `${id}` })
        parts.push({ text: subject, status: 'pending' })
        return parts
    }

    // TaskUpdate / TaskGet share the by-id lookup shape.
    const taskId = safeInput.taskId
    if (taskId == null) return null

    const counter = tasksTotal != null ? `${taskId}/${tasksTotal}` : `${taskId}`

    let label = null
    let status = null
    if (name === 'TaskUpdate') {
        label = taskData?.activeForm || taskData?.subject || null
        status = safeInput.status || taskData?.status || null
    } else if (name === 'TaskGet') {
        label = taskData?.subject || null
        status = taskData?.status || null
    }

    const parts = [{ text: counter }]
    if (label) parts.push({ text: label, status })
    return parts
}

// ─── Monitor aggregation ─────────────────────────────────────────────────

function aggregateMonitorOutput(toolId, options) {
    // ``options.resultsArray`` is the already-fetched tool_result payload list
    // for this tool_use (see ToolUseContent.vue's ``aggregatedExecOutput``
    // computed). The backend returns one entry per ToolResultLink, in
    // tool_result_line_num ASC order, with the tool_result block unwrapped
    // (see ``claude_code/helpers.py:get_tool_results``). We use this directly
    // instead of going back through the session items store, which only
    // carries the visible window of items and would yield placeholders for
    // Monitor chunks outside that window.
    const results = options?.resultsArray
    if (!Array.isArray(results) || results.length === 0) return null

    const bodies = []
    let isTerminated = false
    for (let idx = 0; idx < results.length; idx++) {
        // Index 0 is the original Monitor "Monitor started …" row — its
        // content is not user-facing; the backend only used it to map
        // taskId → tool_use_id. Backend orders results by line_num ASC and
        // the SDK writes the Monitor tool_result before any task
        // notification or terminal attachment, so index 0 is reliable.
        if (idx === 0) continue
        const block = results[idx]
        if (!block || block.type !== 'tool_result') continue
        // The terminal chunk carries twiccMonitorTerminal (mirror of the
        // top-level flag the backend uses for compute_link_extra). Skipping
        // it here keeps the terminal's status string out of the concatenated
        // body; isTerminated is enough for the renderer.
        if (block.twiccMonitorTerminal === true) {
            isTerminated = true
            continue
        }
        const body = typeof block.content === 'string' ? block.content : ''
        if (body) bodies.push(body)
    }
    return {
        aggregatedOutput: bodies.join('\n'),
        isTerminated,
    }
}

// ─── ClaudeCodeToolHelpers ──────────────────────────────────────────────

export class ClaudeCodeToolHelpers extends BaseToolHelpers {
    static provider = PROVIDER.CLAUDE_CODE

    // ─── Summary ─────────────────────────────────────────────────────────

    computeToolSummary(name, input, baseDir) {
        const safeInput = input || {}
        const displayName = getDisplayName(name, safeInput)

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            const description = formatRelativePath(safeInput.file_path, baseDir)
            return { displayName, inline: description }
        }

        if (name === 'Skill' && displayName) {
            return { displayName, inline: displayName.name }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                const path = rawPath ? formatRelativePath(rawPath, baseDir) : null
                return {
                    displayName,
                    inline: buildGrepInline(pattern, fileType, path),
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return { displayName, inline: safeInput.pattern }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return { displayName, inline: safeInput.url }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return { displayName, inline: safeInput.query }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return { displayName, inline: safeInput.query }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return { displayName, inline: null }
        }

        // TaskCreate / TaskUpdate / TaskGet render their summary through
        // ``getSummaryRendering`` (TodoSummary-shaped); the inline / description
        // surface is bypassed so the input ``description`` lands in the
        // JsonHumanView fallback alongside subject + activeForm.
        if (TASK_TOOLS.has(name)) {
            return { displayName, inline: null }
        }

        const description = safeInput.description || null
        return { displayName, inline: description }
    }

    getVerb(name, input) {
        if (!name) return null
        if (AGENT_TOOL_NAMES.has(name)) {
            const subtype = input?.subagent_type?.toLowerCase()
            if (subtype && TASK_SUBAGENT_LABELS[subtype]) {
                return TASK_SUBAGENT_LABELS[subtype]
            }
            return 'agenting'
        }
        if (name.startsWith('mcp__')) {
            const parts = name.split('__')
            const server = parts[1] || 'mcp'
            return `mcping (${server})`
        }
        const lower = name.toLowerCase()
        return lower.replace(/[aeiou]+$/, '') + 'ing'
    }

    getHeaderLabel(name, input, options) {
        // A control card (``SendMessage`` / ``TaskStop`` / ``TaskOutput``
        // targeting an agent) reads its action label first — the default
        // Task labels below stay reserved for shell calls (71 of 75
        // ``TaskStop`` and all 28 ``TaskOutput`` calls target shells, and
        // must not read "Stop agent" / "Agent output").
        const controlLabel = agentControlHeaderLabel(name, options?.agentInteraction)
        if (controlLabel) return controlLabel
        if (name === 'TodoWrite') return 'Todo'
        // TaskCreate / TaskUpdate / TaskGet / TaskList keep their raw names —
        // the operation is informative on its own and a flat ``Task`` header
        // hides it (we'd then only tell create vs update vs get apart via
        // the status icon, which is too subtle).
        return null
    }

    // ─── Input / Result rendering ───────────────────────────────────────

    getInputRendering(name, input, ctx) {
        const safeInput = input || {}
        const editProps = {
            input: safeInput,
            backendPatch: ctx?.backendPatch ?? null,
            backendPatchLoading: ctx?.backendPatchLoading ?? false,
            originalFile: ctx?.originalFile ?? null,
            isSubagent: !!ctx?.isSubagent,
        }
        if (name === 'Edit' && 'old_string' in safeInput && 'new_string' in safeInput) {
            return { component: EditContent, props: editProps }
        }
        if (name === 'Write' && 'content' in safeInput) {
            return { component: WriteContent, props: editProps }
        }
        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return { component: TodoContent, props: { todos: safeInput.todos } }
        }
        if (name === 'TaskCreate' || name === 'TaskUpdate' || name === 'TaskGet') {
            // For TaskUpdate / TaskGet, show the full task data captured at
            // the moment of the call (twiccTaskData) — it carries every
            // field of the task, not just the bare tool input (which is
            // taskId + the fields being updated for Update, or just taskId
            // for Get). Fall back to the tool input when the snapshot is
            // missing (e.g. legacy block, malformed input, unknown taskId).
            let input
            let inputOverrides = this.getInputOverrides(name)
            if (name === 'TaskUpdate' || name === 'TaskGet') {
                const taskData = ctx?.twiccTaskData
                if (taskData && typeof taskData === 'object') {
                    input = taskData
                    // The task id is already shown in the summary header
                    // (TodoSummary "<id>/<total>"), so hide it from the
                    // JSON Human View to avoid redundant noise.
                    inputOverrides = { ...inputOverrides, id: { hidden: true } }
                } else {
                    input = this.getDisplayInputObject(name, safeInput) ?? {}
                }
            } else {
                input = this.getDisplayInputObject(name, safeInput) ?? {}
            }
            const todos = tasksDataToTodos(ctx?.twiccTasksData)
            return {
                component: TaskByIdContent,
                props: {
                    input,
                    inputOverrides,
                    todos,
                },
            }
        }
        if (name === 'TaskList') {
            const todos = tasksDataToTodos(ctx?.twiccTasksData)
            if (todos) {
                return { component: TodoContent, props: { todos } }
            }
        }
        return null
    }

    getResultRendering(name, result, input, ctx) {
        if (name === MONITOR_TOOL_NAME) {
            const agg = ctx?.aggregatedExecOutput
            const aggregatedOutput = (agg && typeof agg.aggregatedOutput === 'string') ? agg.aggregatedOutput : ''
            const isTerminated = !!(agg && agg.isTerminated)
            return {
                component: MonitorResultContent,
                props: { aggregatedOutput, isTerminated },
            }
        }
        if (name === 'Read') {
            const content = typeof result === 'string' ? result : result?.content
            if (parseCatNContent(content)) {
                return { component: ReadResultContent, props: { result, input } }
            }
            return null
        }
        if (name === 'Bash' && getDirectContent(name, result) != null) {
            return { component: BashResultContent, props: { result } }
        }
        if ((name === 'WebFetch' || name === 'WebSearch') && getDirectContent(name, result) != null) {
            return { component: WebContentResult, props: { result } }
        }
        return null
    }

    getSummaryRendering(name, input, baseDir, options) {
        const safeInput = input || {}

        if (name === 'TaskList') {
            const tasks = options?.twiccTasksData
            if (!Array.isArray(tasks)) return null
            const count = tasks.length
            const label = count === 1 ? '1 task' : `${count} tasks`
            return {
                component: DescriptionSummary,
                props: { description: label, fileIconSrc: null },
            }
        }

        if (TASK_TOOLS.has(name)) {
            const parts = buildTaskSummaryParts(name, safeInput, options)
            if (parts) {
                return { component: TodoSummary, props: { parts } }
            }
            return null
        }

        if (FILE_PATH_TOOLS.has(name) && safeInput.file_path) {
            return {
                component: DescriptionSummary,
                props: {
                    description: formatRelativePath(safeInput.file_path, baseDir),
                    fileIconSrc: fileIconFor(safeInput.file_path),
                },
            }
        }

        if (name === 'Skill') {
            const dn = getDisplayName(name, safeInput)
            if (dn) {
                return {
                    component: SkillSummary,
                    props: { name: dn.name, namespace: dn.namespace },
                }
            }
        }

        if (name === 'Grep') {
            const pattern = safeInput.pattern || null
            const fileType = safeInput.type || safeInput.glob || null
            const rawPath = safeInput.path || null
            if (pattern || fileType || rawPath) {
                return {
                    component: GrepSummary,
                    props: {
                        pattern,
                        fileType,
                        path: rawPath ? formatRelativePath(rawPath, baseDir) : null,
                        pathIconSrc: rawPath ? fileIconFor(rawPath) : null,
                    },
                }
            }
        }

        if (name === 'Glob' && safeInput.pattern) {
            return {
                component: GlobSummary,
                props: { pattern: safeInput.pattern },
            }
        }

        if (name === 'WebFetch' && safeInput.url) {
            return {
                component: WebFetchSummary,
                props: { url: safeInput.url },
            }
        }

        if (name === 'WebSearch' && safeInput.query) {
            return {
                component: WebSearchSummary,
                props: { query: safeInput.query },
            }
        }

        if (name === 'ToolSearch' && safeInput.query) {
            return {
                component: ToolSearchSummary,
                props: { query: safeInput.query },
            }
        }

        if (name === 'TodoWrite' && isValidTodos(safeInput.todos)) {
            return {
                component: TodoSummary,
                props: { parts: getTodoDescription(safeInput.todos) },
            }
        }

        // Fallback: any tool whose input carries a `description` field.
        if (safeInput.description) {
            return {
                component: DescriptionSummary,
                props: { description: safeInput.description, fileIconSrc: null },
            }
        }

        return null
    }

    getInputOverrides(name) {
        return INPUT_OVERRIDES[name] ?? {}
    }

    // ─── Input value extraction ──────────────────────────────────────────

    getFilePath(name, input) {
        if (!FILE_PATH_TOOLS.has(name)) return null
        return input?.file_path ?? null
    }

    getOpenInFilesTarget(name, input, ctx) {
        const filePath = this.getFilePath(name, input)
        if (!filePath) return null
        // Edit/Write: scroll to the first modified line from the backend patch.
        // Read: scroll to the offset line carried by the tool input.
        const lineHint = ctx?.firstModifiedLine ?? input?.offset ?? null
        return { filePath, lineHint }
    }

    getDisplayInputObject(name, input) {
        if (!input || Object.keys(input).length === 0) return null
        // ``TaskCreate``'s description is the meaty long-form text we want to
        // keep visible — its summary uses ``subject`` instead of
        // ``description``, so stripping it here would drop it from the UI
        // entirely. Other tools (Bash, …) surface ``description`` in the
        // summary header, so we keep stripping it for them.
        if (name === 'TaskCreate') {
            return input
        }
        const { description: _description, ...rest } = input
        return Object.keys(rest).length > 0 ? rest : null
    }

    getExpectedResultCount(name, input, options) {
        // A control card's own rule: ``SendMessage`` that opens a run
        // expects the ack and the run's end signal (2); every other
        // control call expects 1 — so a resumed run's second row is
        // fetched live (today ``SendMessage`` was always 1).
        const controlCount = agentControlExpectedCount(name, options?.agentInteraction)
        if (controlCount !== null) return controlCount
        if (name === MONITOR_TOOL_NAME) return 1
        // Claude Code's Bash tool emits two tool_result events when launched
        // with ``run_in_background``: one for the start, one for the final
        // output. Every other tool emits a single result.
        return input?.run_in_background ? 2 : 1
    }

    getRequiredResultCountForDisplay(name, input, options) {
        // Control cards always display at 1 row, even when their expected
        // count is 2, so the ack ("Resuming agent …") shows at once instead
        // of hiding behind "Result not yet available" for the whole run.
        if (options?.agentInteraction) return 1
        if (name === MONITOR_TOOL_NAME) return 1
        return this.getExpectedResultCount(name, input, options)
    }

    isToolRunning(name, input, options) {
        if (name === MONITOR_TOOL_NAME) {
            if (options?.toolState?.error) return false
            const extra = options?.toolState?.extra
            if (!extra) return true
            try {
                const parsed = typeof extra === 'string' ? JSON.parse(extra) : extra
                return !parsed?.is_terminated
            } catch {
                return true
            }
        }
        return super.isToolRunning(name, input, options)
    }

    shouldAggregateExecOutput(name) {
        return name === MONITOR_TOOL_NAME
    }

    getAggregatedExecOutput(name, toolId, options) {
        if (name !== MONITOR_TOOL_NAME) return null
        return aggregateMonitorOutput(toolId, options)
    }

    // ─── Capability flags ────────────────────────────────────────────────

    isFileChangeTool(name) {
        return FILE_CHANGE_TOOLS.has(name)
    }

    isAgentTool(name) {
        return AGENT_TOOL_NAMES.has(name)
    }

    shouldAutoOpenLive(name /*, input */) {
        return name === 'Edit' || name === 'Write'
    }

    showsResultOnError(name) {
        // Bash errors only carry "Exit code N" — the full output is in the result.
        return name === 'Bash'
    }

    showsResultOnUnknownError(name) {
        // Edit/Write benefit from the diagnostic detail in the Result section;
        // TodoWrite never shows the Result (its specialized renderer is enough).
        return name === 'Edit' || name === 'Write'
    }

    errorIsMarkdown(name) {
        // ExitPlanMode's error text is the user-rejected plan, formatted as Markdown.
        return name === 'ExitPlanMode'
    }

    hidesResult(name) {
        // TaskCreate / TaskUpdate / TaskGet — the canonical task payload is
        // already spliced into the tool_use by the backend (twiccTaskData),
        // so the tool_result body is pure redundancy.
        return TASK_TOOLS.has(name)
    }

    // ─── File-change stats ───────────────────────────────────────────────

    computeFileChangeStats(name, input, toolState, isSubagent) {
        if (!FILE_CHANGE_TOOLS.has(name)) return null

        // Subagent: stats come from the input directly (no backend patch).
        if (isSubagent) {
            if (name === 'Edit' && input?.old_string != null && input?.new_string != null) {
                return {
                    lines_removed: input.old_string.split('\n').length,
                    lines_added: input.new_string.split('\n').length,
                }
            }
            if (name === 'Write' && input?.content != null) {
                return { lines_added: input.content.split('\n').length }
            }
            return null
        }

        // Main session: stats come from the backend (ToolResultLink.extra JSON).
        if (!toolState?.extra) return null
        try {
            return JSON.parse(toolState.extra)
        } catch {
            return null
        }
    }

    // ─── Backend-patch fetcher ───────────────────────────────────────────

    needsBackendPatchFetch(name) {
        return FILE_CHANGE_TOOLS.has(name)
    }

    extractBackendPatchData(parsedToolResult) {
        const toolUseResult = parsedToolResult?.toolUseResult
        if (!toolUseResult) return null
        const patch = toolUseResult.structuredPatch
        const originalFile = toolUseResult.originalFile
        const out = {}
        if (Array.isArray(patch) && patch.length > 0) out.patch = patch
        if (typeof originalFile === 'string') out.originalFile = originalFile
        return Object.keys(out).length > 0 ? out : null
    }
}

export const claudeCodeToolHelpers = new ClaudeCodeToolHelpers()
