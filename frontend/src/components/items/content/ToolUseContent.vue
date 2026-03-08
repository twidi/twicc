<script setup>
import { computed, ref, inject, watch, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../../stores/data'
import { apiFetch } from '../../../utils/api'
import { getIconUrl, getFileIconId } from '../../../utils/fileIcons'
import { getLanguageFromPath } from '../../../utils/languages'
import { AGENT_TOOL_NAMES, isTrackedTool } from '../../../constants'
import { getSessionCutoffMs } from '../../../utils/sessions'
import { getTodoDescription, isValidTodos } from '../../../utils/todoList'
import JsonHumanView from '../../JsonHumanView.vue'
import MarkdownContent from '../../MarkdownContent.vue'
import AppTooltip from '../../AppTooltip.vue'
import ProcessDuration from '../../ProcessDuration.vue'
import TodoContent from './TodoContent.vue'

const route = useRoute()
const router = useRouter()
const dataStore = useDataStore()

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))

const props = defineProps({
    name: {
        type: String,
        required: true
    },
    input: {
        type: Object,
        default: () => ({})
    },
    toolId: {
        type: String,
        required: true
    },
    projectId: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    lineNum: {
        type: Number,
        required: true
    },
    timestamp: {
        type: String,
        default: null
    }
})

// Polling configuration
const POLLING_DELAY_MS = 3000

// Template ref for the result details element
const resultDetailsRef = ref(null)

// Lazy rendering: content is only mounted when wa-details is open
const isOpen = ref(false)

// Tool result state
const resultState = ref('idle') // 'idle' | 'loading' | 'loaded' | 'error'
const resultData = ref(null)
const resultError = ref(null)
const isPolling = ref(false)
const pollingIntervalId = ref(null)
const abortController = ref(null)

/**
 * Fetch tool result from API.
 * If result is empty and not already polling, starts polling.
 * If result has data, stops polling.
 */
async function fetchResult() {
    // Don't set loading state if we're polling (to avoid flicker)
    if (!isPolling.value) {
        resultState.value = 'loading'
    }
    resultError.value = null

    // Create new AbortController for this request
    abortController.value = new AbortController()

    try {
        // Build URL (handles subagent case via parentSessionId)
        const baseUrl = props.parentSessionId
            ? `/api/projects/${props.projectId}/sessions/${props.parentSessionId}/subagent/${props.sessionId}`
            : `/api/projects/${props.projectId}/sessions/${props.sessionId}`
        const url = `${baseUrl}/items/${props.lineNum}/tool-results/${props.toolId}/`
        const response = await apiFetch(url, { signal: abortController.value.signal })

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        resultData.value = data.results
        resultState.value = 'loaded'

        // If we got data, stop polling
        if (data.results && data.results.length > 0) {
            stopPolling()
        } else if (!isPolling.value) {
            // No data and not polling yet: start polling
            startPolling()
        }
    } catch (err) {
        // Ignore abort errors (expected when stopping polling)
        if (err.name === 'AbortError') {
            return
        }
        resultError.value = err.message
        resultState.value = 'error'
        stopPolling()
    } finally {
        abortController.value = null
    }
}

/**
 * Start polling for results at regular intervals.
 */
function startPolling() {
    if (pollingIntervalId.value) return // Already polling
    isPolling.value = true
    pollingIntervalId.value = setInterval(fetchResult, POLLING_DELAY_MS)
}

/**
 * Stop polling and reset polling state.
 * Also aborts any in-flight fetch request.
 */
function stopPolling() {
    // Abort any in-flight request
    if (abortController.value) {
        abortController.value.abort()
        abortController.value = null
    }
    if (pollingIntervalId.value) {
        clearInterval(pollingIntervalId.value)
        pollingIntervalId.value = null
    }
    isPolling.value = false
}

/**
 * Handler for when the result details section is opened.
 * Fetches if idle, or if loaded but empty (to retry).
 */
function onResultOpen() {
    // Fetch if idle, or if loaded but no data (retry)
    const shouldFetch = resultState.value === 'idle' ||
        (resultState.value === 'loaded' && (!resultData.value || resultData.value.length === 0))

    if (shouldFetch) {
        fetchResult()
    }
}

/**
 * Handler for when the result details section is closed.
 * Stops polling to avoid unnecessary requests.
 */
function onResultClose() {
    stopPolling()
}

/**
 * Handler for when the parent tool use details is closed.
 * Stops polling to avoid unnecessary requests.
 */
function onToolUseClose() {
    isOpen.value = false
    stopPolling()
}

/**
 * Handler for when the parent tool use details is opened.
 * If the result section is already open and has no data, triggers a fetch/poll.
 */
function onToolUseOpen() {
    isOpen.value = true
    // Check if result details is open (wa-details has an 'open' property)
    const isResultOpen = resultDetailsRef.value?.open === true

    if (isResultOpen) {
        // Result is open, check if we need to fetch/poll
        const shouldFetch = resultState.value === 'idle' ||
            (resultState.value === 'loaded' && (!resultData.value || resultData.value.length === 0))

        if (shouldFetch) {
            fetchResult()
        }
    }
}

// Cleanup on unmount (e.g., when changing session, toggling groups)
onUnmounted(() => {
    stopPolling()
})

// KeepAlive active state (provided by SessionView)
const sessionActive = inject('sessionActive', ref(true))

// Track whether polling was suspended by deactivation (to resume on reactivation)
let resultPollingPaused = false

watch(sessionActive, (active) => {
    if (active) {
        // Reactivated: resume polling only if it was suspended and still needed
        if (resultPollingPaused) {
            resultPollingPaused = false
            // Resume only if result is still empty (polling is self-limiting)
            if (!resultData.value || resultData.value.length === 0) {
                startPolling()
            }
        }
    } else {
        // Deactivated: pause active polling intervals without resetting state
        if (pollingIntervalId.value) {
            resultPollingPaused = true
            clearInterval(pollingIntervalId.value)
            pollingIntervalId.value = null
            // Keep isPolling.value = true so the UI still shows "checking again shortly..."
        }
        // Abort any in-flight result request
        if (abortController.value) {
            abortController.value.abort()
            abortController.value = null
        }
    }
})

// Computed for display: single result or array of multiple
const displayResult = computed(() => {
    if (!resultData.value || resultData.value.length === 0) return null
    if (resultData.value.length === 1) return resultData.value[0]
    return resultData.value
})

// --- Tool result JHV overrides ---
// Force specific valueType for certain tool result keys (prevents markdown auto-detection).
const RESULT_OVERRIDES = {
    Bash: { content: { valueType: 'string-multiline' } },
}

const resultOverrides = computed(() => RESULT_OVERRIDES[props.name] ?? {})

// --- Read tool: syntax-highlighted result ---

// Regex to match a cat -n formatted line: optional spaces, digits, → arrow, then content
const CAT_N_LINE_RE = /^(\s*\d+)→(.*)$/

/**
 * Parse cat -n formatted content (as produced by Claude's Read tool).
 * Returns the clean code, start line, and end line — or null if the content
 * doesn't match the expected format.
 */
function parseCatNContent(content) {
    if (typeof content !== 'string') return null

    const lines = content.split('\n')
    // Remove trailing empty line (common from trailing newline)
    if (lines.length > 0 && lines[lines.length - 1] === '') {
        lines.pop()
    }
    if (lines.length === 0) return null

    // Validate that the first non-empty line matches the pattern
    const firstNonEmpty = lines.find(l => l.length > 0)
    if (!firstNonEmpty || !CAT_N_LINE_RE.test(firstNonEmpty)) return null

    let startLine = null
    let endLine = null
    const codeLines = []

    for (const line of lines) {
        const match = line.match(CAT_N_LINE_RE)
        if (match) {
            const lineNum = parseInt(match[1], 10)
            if (startLine === null) startLine = lineNum
            endLine = lineNum
            codeLines.push(match[2])
        } else {
            // Line doesn't match pattern — keep as-is (shouldn't happen in well-formed output)
            codeLines.push(line)
        }
    }

    return { code: codeLines.join('\n'), startLine, endLine }
}

const isRead = computed(() => props.name === 'Read')

/**
 * For Read tool results: parsed code content with language info for syntax highlighting.
 * Returns { code, language, startLine, endLine, markdownSource } or null.
 */
const readResultCode = computed(() => {
    if (!isRead.value || !displayResult.value) return null

    // displayResult is the tool_result object { tool_use_id, type, content }
    const result = displayResult.value
    const content = typeof result === 'string' ? result : result?.content
    const parsed = parseCatNContent(content)
    if (!parsed) return null

    const language = getLanguageFromPath(props.input?.file_path) || ''
    return {
        ...parsed,
        language,
        markdownSource: '```' + language + '\n' + parsed.code + '\n```'
    }
})

// Tools that show file_path instead of description in the summary
const FILE_PATH_TOOLS = new Set(['Edit', 'Write', 'Read'])
const usesFilePath = computed(() => FILE_PATH_TOOLS.has(props.name) && !!props.input?.file_path)

// Make file_path relative to session's working directory when possible
const sessionBaseDir = computed(() => {
    const session = dataStore.getSession(props.sessionId)
    return session?.git_directory || session?.cwd || null
})

// File icon URL for file tools (null if no specific icon found)
const fileIconSrc = computed(() => {
    if (!usesFilePath.value) return null
    const filename = props.input.file_path.split('/').pop() || props.input.file_path
    const iconId = getFileIconId(filename)
    return iconId !== 'default-file' ? getIconUrl(iconId) : null
})

// Extract summary detail: file_path for file tools, description for others
const description = computed(() => {
    if (usesFilePath.value) {
        const filePath = props.input.file_path
        const baseDir = sessionBaseDir.value
        if (baseDir && filePath.startsWith(baseDir + '/')) {
            return filePath.slice(baseDir.length + 1)
        }
        return filePath
    }
    // Special tools have their own summary rendering
    if (isSkill.value || isGrep.value || isGlob.value || isTodoWrite.value || isWebFetch.value || isWebSearch.value || isToolSearch.value) return null
    return props.input?.description || null
})

// --- Skill tool summary ---
const isSkill = computed(() => props.name === 'Skill')
const skillDescription = computed(() => {
    if (!isSkill.value || !props.input?.skill) return null
    const skill = props.input.skill
    const colonIdx = skill.indexOf(':')
    if (colonIdx >= 0) {
        return {
            name: capitalize(skill.slice(colonIdx + 1)),
            namespace: capitalize(skill.slice(0, colonIdx))
        }
    }
    return { name: capitalize(skill), namespace: null }
})

// --- Grep tool summary ---
const isGrep = computed(() => props.name === 'Grep')
const grepParts = computed(() => {
    if (!isGrep.value) return null
    const pattern = props.input?.pattern || null
    // Use "type" if available, otherwise fall back to "glob"
    const fileType = props.input?.type || props.input?.glob || null
    const rawPath = props.input?.path || null
    if (!pattern && !fileType && !rawPath) return null

    let path = null
    let pathIconSrc = null
    if (rawPath) {
        const baseDir = sessionBaseDir.value
        path = (baseDir && rawPath.startsWith(baseDir + '/'))
            ? rawPath.slice(baseDir.length + 1)
            : rawPath
        const filename = rawPath.split('/').pop() || rawPath
        const iconId = getFileIconId(filename)
        pathIconSrc = iconId !== 'default-file' ? getIconUrl(iconId) : null
    }

    return { pattern, fileType, path, pathIconSrc }
})

// --- Glob tool summary ---
const isGlob = computed(() => props.name === 'Glob')
const globPattern = computed(() => {
    if (!isGlob.value) return null
    return props.input?.pattern || null
})

// --- WebFetch tool summary ---
const isWebFetch = computed(() => props.name === 'WebFetch')
const webFetchUrl = computed(() => {
    if (!isWebFetch.value) return null
    return props.input?.url || null
})

// --- WebSearch tool summary ---
const isWebSearch = computed(() => props.name === 'WebSearch')
const webSearchQuery = computed(() => {
    if (!isWebSearch.value) return null
    return props.input?.query || null
})

// --- ToolSearch tool summary ---
const isToolSearch = computed(() => props.name === 'ToolSearch')
const toolSearchQuery = computed(() => {
    if (!isToolSearch.value) return null
    return props.input?.query || null
})

// --- TodoWrite tool summary ---
const isTodoWrite = computed(() => props.name === 'TodoWrite')
const todosValid = computed(() => isTodoWrite.value && isValidTodos(props.input?.todos))
const todoDescription = computed(() => {
    if (!isTodoWrite.value) return null
    return getTodoDescription(props.input?.todos)
})

// Input without description for display
const displayInput = computed(() => {
    if (!props.input || Object.keys(props.input).length === 0) {
        return null
    }
    const { description, ...rest } = props.input
    return Object.keys(rest).length > 0 ? rest : null
})

// --- Tool running state (unified for all tracked tools) ---

const isTask = computed(() => AGENT_TOOL_NAMES.has(props.name))
const isTracked = computed(() => isTrackedTool(props.name))
const isBackground = computed(() => !!props.input?.run_in_background)
const toolState = computed(() => isTracked.value ? dataStore.getToolState(props.sessionId, props.toolId) : null)

// Whether this tool_use predates the session's last start/stop cycle
// (session was restarted or stopped since — this tool can't be running)
const isStaleToolUse = computed(() => {
    if (!props.timestamp) return false
    const cutoff = getSessionCutoffMs(dataStore.sessions[props.sessionId])
    return cutoff > 0 && new Date(props.timestamp).getTime() < cutoff
})

// Unix timestamp (seconds) for ProcessDuration — from the JSONL item timestamp
const toolStartedAt = computed(() => {
    if (!props.timestamp) return null
    return new Date(props.timestamp).getTime() / 1000
})

// --- Tool running spinner (for all tracked tools except Agent/Task which have their own UI) ---

const isToolRunning = computed(() => {
    if (!isTracked.value || isTask.value) return false
    if (isStaleToolUse.value) return false
    const resultCount = toolState.value?.resultCount || 0
    const requiredCount = isBackground.value ? 2 : 1
    return resultCount < requiredCount
})
const toolSpinnerId = computed(() => `tool-spinner-${props.toolId}`)

// --- View Agent button for Task tool_use ---

// Task tool: display subagent_type instead of "Task"
// "silent-failure-hunter" → "Silent failure hunter"
function capitalize(str) {
    return str.replace(/-/g, ' ').replace(/^\w/, c => c.toUpperCase())
}

const taskDisplayName = computed(() => {
    if (!isTask.value || !props.input?.subagent_type || props.input.subagent_type === 'general-purpose') return null
    const sat = props.input.subagent_type
    const colonIdx = sat.indexOf(':')
    if (colonIdx >= 0) {
        return {
            name: capitalize(sat.slice(colonIdx + 1)),
            namespace: capitalize(sat.slice(0, colonIdx))
        }
    }
    return { name: capitalize(sat), namespace: null }
})

// Agent link: reactive lookup from the store cache.
// The cache is populated by fetchSubagentsState (on session load) and
// by the WS agent_link_created handler — no polling needed.
// Returns { agentId, isBackground } or undefined.
const agentLink = computed(() => dataStore.getAgentLink(props.sessionId, props.toolId))
const agentId = computed(() => agentLink.value?.agentId)

const isAgentRunning = computed(() => {
    if (!isTask.value || !agentId.value) return false
    if (isStaleToolUse.value) return false
    const resultCount = toolState.value?.resultCount || 0
    const requiredCount = (agentLink.value?.isBackground) ? 2 : 1
    return resultCount < requiredCount
})

// Unique ID for the View Agent button (for tooltip targeting)
const viewAgentButtonId = computed(() => `view-agent-${props.toolId}`)

/**
 * Navigate to the subagent tab.
 */
function navigateToSubagent() {
    if (!agentId.value) return
    router.push({
        name: isAllProjectsMode.value ? 'projects-session-subagent' : 'session-subagent',
        params: {
            projectId: props.projectId,
            sessionId: props.sessionId,
            subagentId: agentId.value
        }
    })
}

</script>

<template>
    <wa-details class="item-details tool-use" :class="{'with-right-part' : (isTask && !parentSessionId) || isToolRunning}" icon-placement="start" @wa-show.self="onToolUseOpen" @wa-hide.self="onToolUseClose">
        <span slot="summary" class="items-details-summary">
            <span class="items-details-summary-left">
                <strong v-if="taskDisplayName" class="items-details-summary-name">{{ taskDisplayName.name }}<span v-if="taskDisplayName.namespace" class="items-details-summary-quiet"> ({{ taskDisplayName.namespace }})</span></strong>
                <strong v-else-if="isTodoWrite" class="items-details-summary-name">Todo</strong>
                <strong v-else class="items-details-summary-name">{{ name.replaceAll('__', ' ') }}</strong>
                <template v-if="description">
                    <span class="items-details-summary-separator"> — </span>
                    <span v-if="fileIconSrc" class="items-details-summary-file">
                        <img :src="fileIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
                        <span class="items-details-summary-description">{{ description }}</span>
                    </span>
                    <span v-else class="items-details-summary-description">{{ description }}</span>
                </template>
                <!-- Skill tool: show skill name, with namespace in quiet mode -->
                <template v-else-if="skillDescription">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description">{{ skillDescription.name }}<span v-if="skillDescription.namespace" class="items-details-summary-quiet"> ({{ skillDescription.namespace }})</span></span>
                </template>
                <!-- Grep tool: "`pattern` in `type` files in [path]" -->
                <template v-else-if="grepParts">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description items-details-summary-grep">
                        <code v-if="grepParts.pattern">{{ grepParts.pattern }}</code>
                        <span v-if="grepParts.fileType">in <code>{{ grepParts.fileType }}</code> files</span>
                        <span v-if="grepParts.path">in
                            <span v-if="grepParts.pathIconSrc" class="items-details-summary-file">
                                <img :src="grepParts.pathIconSrc" class="items-details-summary-file-icon" loading="lazy" width="16" height="16" />
                                <span>{{ grepParts.path }}</span>
                            </span>
                            <span v-else>{{ grepParts.path }}</span>
                        </span>
                    </span>
                </template>
                <!-- Glob tool: show pattern in code -->
                <template v-else-if="globPattern">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description"><code>{{ globPattern }}</code></span>
                </template>
                <!-- WebFetch tool: show URL as a link -->
                <template v-else-if="webFetchUrl">
                    <span class="items-details-summary-separator"> — </span>
                    <a :href="webFetchUrl" target="_blank" rel="noopener noreferrer nofollow" class="items-details-summary-description items-details-summary-link" @click.stop>{{ webFetchUrl }}<wa-icon name="arrow-up-right-from-square" class="items-details-summary-link-icon"></wa-icon></a>
                </template>
                <!-- WebSearch tool: show query -->
                <template v-else-if="webSearchQuery">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description">{{ webSearchQuery }}</span>
                </template>
                <!-- ToolSearch tool: show query -->
                <template v-else-if="toolSearchQuery">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description">{{ toolSearchQuery }}</span>
                </template>
                <!-- TodoWrite tool: show progress description -->
                <template v-else-if="todoDescription">
                    <template v-for="(part, i) in todoDescription" :key="i">
                        <span class="items-details-summary-separator"> — </span>
                        <span class="items-details-summary-description" :class="{ 'no-wrap': !part.status }">{{ part.text }}<wa-icon v-if="part.status === 'completed'" name="check" class="todo-icon todo-icon-completed"></wa-icon></span>
                    </template>
                </template>
            </span>
            <!-- View Agent indicator for Task tool_use (only in regular sessions) -->
            <template v-if="isTask && !parentSessionId">
                <!-- Agent not yet started: spinner -->
                <wa-spinner v-if="!agentId" class="agent-starting-spinner"></wa-spinner>
                <!-- Agent started: View Agent button (with pulsing robot if still running) -->
                <template v-else>
                    <AppTooltip v-if="isAgentRunning && toolStartedAt" :for="viewAgentButtonId">
                        Agent running for <ProcessDuration :state-changed-at="toolStartedAt" />
                    </AppTooltip>
                    <wa-button
                        :id="viewAgentButtonId"
                        size="small"
                        variant="brand"
                        appearance="outlined"
                        @click.stop="navigateToSubagent"
                    >
                        <wa-icon v-if="isAgentRunning" slot="start" name="robot" class="agent-running-icon"></wa-icon>
                        View Agent
                    </wa-button>
                </template>
            </template>
            <!-- Tool running spinner (Bash, WebFetch, MCP, etc.) -->
            <template v-if="isToolRunning">
                <AppTooltip v-if="toolStartedAt" :for="toolSpinnerId">
                    Running for <ProcessDuration :state-changed-at="toolStartedAt" />
                </AppTooltip>
                <wa-spinner :id="toolSpinnerId" class="tool-running-spinner"></wa-spinner>
            </template>
        </span>
        <template v-if="isOpen">
            <TodoContent v-if="isTodoWrite && todosValid" :todos="input.todos" />
            <div v-else-if="displayInput" class="tool-input">
                <JsonHumanView
                    :value="displayInput"
                />
            </div>
            <div v-else class="tool-no-input">
                No input parameters
            </div>
            <wa-details v-if="!isTodoWrite || !todosValid" ref="resultDetailsRef" class="tool-result" @wa-show="onResultOpen" @wa-hide="onResultClose">
                <span slot="summary">Result</span>
                <div class="tool-result-content">
                    <div v-if="resultState === 'loading'" class="tool-result-loading">
                        <wa-spinner></wa-spinner>
                        <span>Loading result...</span>
                    </div>
                    <div v-else-if="resultState === 'error'" class="tool-result-error">
                        Error loading result: {{ resultError }}
                    </div>
                    <div v-else-if="resultState === 'loaded' && !displayResult && isPolling" class="tool-result-polling">
                        <wa-spinner></wa-spinner>
                        <span>Result not yet available. Checking again shortly...</span>
                    </div>
                    <div v-else-if="resultState === 'loaded' && !displayResult" class="tool-result-empty">
                        No result available
                    </div>
                    <div v-else-if="resultState === 'loaded' && displayResult" class="tool-result-data">
                        <template v-if="readResultCode">
                            <div class="read-result-header">Lines {{ readResultCode.startLine }}–{{ readResultCode.endLine }}</div>
                            <MarkdownContent :source="readResultCode.markdownSource" />
                        </template>
                        <JsonHumanView
                            v-else
                            :value="displayResult"
                            :overrides="resultOverrides"
                        />
                    </div>
                </div>
            </wa-details>
        </template>
    </wa-details>
</template>

<style scoped>
wa-details::part(content) {
    padding-top: 0;
}

wa-details.with-right-part {
    /* Summary layout with something  on the right */
    &::part(header) {
        padding-right: 6px
    }

    .items-details-summary {
        display: flex;
        align-items: center;
        gap: var(--wa-space-m);
        width: 100%;

        wa-button {
            margin-block: -1rem;
        }
        .agent-starting-spinner, .tool-running-spinner {
            font-size: 1.2em;
        }
        .agent-starting-spinner {
            --indicator-color: var(--wa-color-warning-60);
        }
        .agent-running-icon {
            animation: pulse 1s ease-in-out infinite;
        }
        & > :not(wa-button):last-child {
            margin-right: var(--spacing);
        }
    }

    .items-details-summary-left {
        flex: 1;
        min-width: 0; /* Allow text wrapping */
    }
}

wa-details {
    .items-details-summary-left {
        display: inline-flex;
        align-items: center;
        gap: var(--wa-space-xs);
        max-width: 100%; /* Constrain to parent width so text can wrap */
    }

    .items-details-summary-description {
        /* Description can wrap on multiple lines */
        word-wrap: break-word;

        &.no-wrap {
            white-space: nowrap;
        }
    }
}

.items-details-summary-file {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
}

.items-details-summary-file-icon {
    vertical-align: text-bottom;
    flex-shrink: 0;
}

.items-details-summary-quiet {
    color: var(--wa-color-text-quiet);
}

.items-details-summary-grep {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
    & > span {
        display: inline-flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
}

.items-details-summary-description code {
    font-size: 1em;
    background: var(--wa-color-neutral-fill-quiet);
    border-radius: var(--wa-border-radius-s);
}

.items-details-summary-link {
    color: inherit;
    text-decoration: none;
    &:hover {
        text-decoration: underline;
    }
}

.items-details-summary-link-icon {
    font-size: var(--wa-font-size-2xs);
    margin-left: var(--wa-space-3xs);
    opacity: 0.6;
}

.tool-input {
    padding: var(--wa-space-xs) 0;
    overflow-x: auto;
}

.tool-no-input {
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}

.tool-result {
    margin-top: calc(var(--card-spacing, var(--wa-space-l)) / 2);
}

.tool-result-content {
    padding: var(--wa-space-xs) 0;
}

.tool-result-loading,
.tool-result-polling {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
}

.tool-result-error {
    color: var(--wa-color-danger-text);
}

.tool-result-empty {
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.tool-result-data {
    overflow-x: auto;
}

.read-result-header {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin-bottom: var(--wa-space-xs);
}

.todo-icon {
    margin-left: var(--wa-space-xs);
    font-size: 0.85em;
    vertical-align: baseline;
}

.todo-icon-completed {
    color: var(--wa-color-success-60);
}

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
</style>
