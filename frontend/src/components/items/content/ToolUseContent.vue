<script setup>
import { computed, ref, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { useDataStore } from '../../../stores/data'
import JsonViewer from '../../JsonViewer.vue'

const router = useRouter()
const dataStore = useDataStore()

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
    }
})

// Polling configuration
const POLLING_DELAY_MS = 3000

// Template ref for the result details element
const resultDetailsRef = ref(null)

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
        const response = await fetch(url, { signal: abortController.value.signal })

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
    stopPolling()
}

/**
 * Handler for when the parent tool use details is opened.
 * If the result section is already open and has no data, triggers a fetch/poll.
 */
function onToolUseOpen() {
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
    // Abort any in-flight agent link request
    if (agentLinkAbortController.value) {
        agentLinkAbortController.value.abort()
    }
})

// Computed for display: single result or array of multiple
const displayResult = computed(() => {
    if (!resultData.value || resultData.value.length === 0) return null
    if (resultData.value.length === 1) return resultData.value[0]
    return resultData.value
})

// Extract description from input if present
const description = computed(() => props.input?.description || null)

// Input without description for display
const displayInput = computed(() => {
    if (!props.input || Object.keys(props.input).length === 0) {
        return null
    }
    const { description, ...rest } = props.input
    return Object.keys(rest).length > 0 ? rest : null
})

// --- View Agent button for Task tool_use ---

// Is this a Task tool_use?
const isTask = computed(() => props.name === 'Task')

// Agent link state: 'idle' | 'loading' | 'found' | 'not_found'
const agentLinkState = ref('idle')
const agentLinkAbortController = ref(null)

/**
 * Fetch the agent ID for this Task tool_use and navigate to the subagent tab.
 */
async function handleViewAgent() {
    // Only for regular sessions (not subagents)
    if (props.parentSessionId) return

    // Check cache first
    const cached = dataStore.getAgentLink(props.sessionId, props.toolId)
    if (cached !== undefined) {
        if (cached === null) {
            // Already fetched, not found
            agentLinkState.value = 'not_found'
        } else {
            // Found in cache, navigate
            navigateToSubagent(cached)
        }
        return
    }

    // Fetch from API
    agentLinkState.value = 'loading'
    agentLinkAbortController.value = new AbortController()

    try {
        const url = `/api/projects/${props.projectId}/sessions/${props.sessionId}/items/${props.lineNum}/tool-agent-id/${props.toolId}/`
        const response = await fetch(url, { signal: agentLinkAbortController.value.signal })

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`)
        }

        const data = await response.json()
        const agentId = data.agent_id

        // Cache the result
        dataStore.setAgentLink(props.sessionId, props.toolId, agentId)

        if (agentId) {
            agentLinkState.value = 'found'
            navigateToSubagent(agentId)
        } else {
            agentLinkState.value = 'not_found'
        }
    } catch (err) {
        if (err.name === 'AbortError') return
        console.error('Failed to fetch agent link:', err)
        agentLinkState.value = 'not_found'
    } finally {
        agentLinkAbortController.value = null
    }
}

/**
 * Navigate to the subagent tab.
 */
function navigateToSubagent(agentId) {
    router.push({
        name: 'session-subagent',
        params: {
            projectId: props.projectId,
            sessionId: props.sessionId,
            subagentId: agentId
        }
    })
}

// Collapsed paths for JsonNode (input)
const collapsedPaths = ref(new Set())

// Collapsed paths for result JsonNode
const resultCollapsedPaths = ref(new Set())

function togglePath(path) {
    if (collapsedPaths.value.has(path)) {
        collapsedPaths.value.delete(path)
    } else {
        collapsedPaths.value.add(path)
    }
    // Trigger reactivity
    collapsedPaths.value = new Set(collapsedPaths.value)
}

function toggleResultPath(path) {
    if (resultCollapsedPaths.value.has(path)) {
        resultCollapsedPaths.value.delete(path)
    } else {
        resultCollapsedPaths.value.add(path)
    }
    // Trigger reactivity
    resultCollapsedPaths.value = new Set(resultCollapsedPaths.value)
}
</script>

<template>
    <wa-details class="item-details tool-use" :class="{'with-right-button' : isTask}" icon-placement="start" @wa-show="onToolUseOpen" @wa-hide="onToolUseClose">
        <span slot="summary" class="items-details-summary">
            <span class="items-details-summary-left">
                <strong class="items-details-summary-name">{{ name }}</strong>
                <template v-if="description">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description">{{ description }}</span>
                </template>
            </span>
            <!-- View Agent button for Task tool_use (only in regular sessions) -->
            <template v-if="isTask && !parentSessionId">
                <wa-button
                    v-if="agentLinkState !== 'not_found'"
                    size="small"
                    variant="brand"
                    appearance="outlined"
                    :loading="agentLinkState === 'loading'"
                    @click.stop="handleViewAgent"
                >
                    View Agent
                </wa-button>
                <span v-else class="agent-not-found">Agent not found</span>
            </template>
        </span>
        <div v-if="displayInput" class="tool-input">
            <JsonViewer
                :data="displayInput"
                path="root"
                :collapsed-paths="collapsedPaths"
                @toggle="togglePath"
            />
        </div>
        <div v-else class="tool-no-input">
            No input parameters
        </div>
        <wa-details ref="resultDetailsRef" class="tool-result" @wa-show="onResultOpen" @wa-hide="onResultClose">
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
                    <JsonViewer
                        :data="displayResult"
                        path="result"
                        :collapsed-paths="resultCollapsedPaths"
                        @toggle="toggleResultPath"
                    />
                </div>
            </div>
        </wa-details>
    </wa-details>
</template>

<style scoped>
wa-details::part(content) {
    padding-top: 0;
}

wa-details.with-right-button {
    /* Summary layout with button on the right */
    &::part(header) {
        padding-right: 6px
    }

    .items-details-summary {
        display: flex;
        align-items: center;
        gap: var(--wa-space-m);
        width: 100%;

        wa-button {
            margin-block: -1em;
        }
    }

    .items-details-summary-left {
        flex: 1;
        min-width: 0; /* Allow text wrapping */
    }

    .items-details-summary-description {
        /* Description can wrap on multiple lines */
        word-wrap: break-word;
    }
}

.agent-not-found {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
    font-style: italic;
    white-space: nowrap;
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
    margin-top: var(--wa-space-l);
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
</style>
