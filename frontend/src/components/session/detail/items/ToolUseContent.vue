<script setup>
import { computed, ref, inject, provide, watch, watchEffect, nextTick, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useCodeCommentsStore } from '../../../../stores/codeComments'
import CodeCommentsIndicator from '../../../ui/CodeCommentsIndicator.vue'
import { useDataStore } from '../../../../stores/data'
import { useSettingsStore } from '../../../../stores/settings'
import { apiFetch } from '../../../../utils/api'
import { PROCESS_STATE, PROCESS_STATE_COLORS } from '../../../../constants'
import { stopSubagent } from '../../../../composables/useWebSocket'
import { getSessionCutoffMs } from '../../../../utils/sessions'
import { formatToolNameForHeader } from '../../../../utils/toolNames'
import { getParsedContent, hasContent } from '../../../../utils/parsedContent'
import { getToolHelpers, getProviderHelpers } from '../../../../providers'
import { fileRootsFromStore } from '../../../../utils/projectRoots'
import { sessionRouteLocation } from '../../../../utils/sessionRoute'
import JsonHumanView from '../../../json/JsonHumanView.vue'
import MarkdownContent from '../../../ui/MarkdownContent.vue'
import AppTooltip from '../../../ui/AppTooltip.vue'
import ProcessDuration from '../../../ui/ProcessDuration.vue'

const route = useRoute()
const router = useRouter()
const dataStore = useDataStore()
const settingsStore = useSettingsStore()
const codeCommentsStore = useCodeCommentsStore()

// Resolve tool helpers from the session's provider. Resolved via a computed so
// it stays correct if the session reference changes (rare, but keeps reactivity).
const toolHelpers = computed(() => {
    const session = dataStore.getSession(props.sessionId)
    return getToolHelpers(session?.provider)
})
// Provider-level helpers — used for capabilities that are decided per
// provider rather than per tool (e.g. ``canStopSubagent``).
const providerHelpers = computed(() => {
    const session = dataStore.getSession(props.sessionId)
    return getProviderHelpers(session?.provider)
})

// Cross-tab file reveal (provided by SessionView)
const viewFileInFilesTab = inject('viewFileInFilesTab', null)

// Share bundle supplies this to open subagents in an in-page overlay instead of
// routing. Default null → SPA keeps its router.push behaviour (navigateToSubagent).
const openSubagent = inject('openSubagent', null)

// Share bundle supplies this to fetch tool results through the public token path
// instead of the auth-protected SPA API (a viewer holds no session). Default null
// → SPA keeps its apiFetch behaviour. Returns the ``{ results }`` payload.
const fetchToolResult = inject('fetchToolResult', null)
// Share-only seam: the Edit/Write tool_result line is DEBUG_ONLY and filtered out
// of the share's /items endpoint, so the full-file diff pulls it ceiling-exempt.
const fetchBackendPatchItems = inject('fetchBackendPatchItems', null)

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
    },
    // Provider-specific extras forwarded to helper hooks that opt into a
    // third argument (e.g. ``getExpectedResultCount``). Codex sets
    // ``{ wrapperType }`` so the helper can branch on the JSONL wrapper
    // shape without re-parsing the raw line. Other providers leave it null.
    extra: {
        type: Object,
        default: null
    }
})

// Line number of the Agent/Task tool_use in the parent session (for subagent comment indicators)
const parentToolUseLineNum = computed(() => {
    if (!props.parentSessionId) return null
    return dataStore.getAgentToolUseLineNum(props.parentSessionId, props.sessionId)
})

// Provide tool context for code comments in child editors (ToolDiffViewer).
// sessionId is always the root/main session so that "Add all" works session-wide.
// subagentSessionId tracks the subagent's own ID for scoped indicators.
provide('codeCommentToolContext', {
    toolUseId: props.toolId,
    sessionId: props.parentSessionId || props.sessionId,
    subagentSessionId: props.parentSessionId ? props.sessionId : '',
    projectId: props.projectId,
    lineNum: props.lineNum,  // tool_use's line number in the session
    subagentToolLineNum: props.parentSessionId ? parentToolUseLineNum.value : null,
})

// Polling configuration
const POLLING_DELAY_MS = 3000

// Template refs
const toolUseDetailsRef = ref(null)
const resultDetailsRef = ref(null)

// Lazy rendering: content is only mounted when wa-details is open.
// Initialized from the store to restore state across virtual scroller mount/unmount cycles.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, props.toolId))

// Track whether the current open transition should skip animation.
// When true, animation duration is set to 0ms via :style binding so wa-details
// opens instantly without a visible transition. Used for:
// - Restoration from virtual scroller (isOpen already true from store)
// - Auto-open of live Edit/Write diffs (shouldAutoOpen fires)
const instantOpen = ref(isOpen.value)

// Track open state for the nested result details (same declarative approach)
const isResultOpen = ref(
    isOpen.value && dataStore.isDetailOpen(props.sessionId, `result:${props.toolId}`)
)

onMounted(() => {
    // After first render, restore normal animation duration for future open/close
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
    // Fetch the result when it's already meant to be visible at mount:
    // either the "Result" disclosure was restored open, or this is an
    // inline-result tool (e.g. view_image) whose card is open — its
    // result renders directly in the body, so it must be fetched up front.
    if (isResultOpen.value || (rendersResultInline.value && isOpen.value && showResultDetails.value)) {
        ensureResultFetched()
    }
})

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
        let data
        if (fetchToolResult) {
            // Share bundle: fetch via the token path (no SPA auth); no abort/polling wiring.
            data = await fetchToolResult(props.lineNum, props.toolId, props.parentSessionId)
        } else {
            // Build URL (handles subagent case via parentSessionId)
            const baseUrl = props.parentSessionId
                ? `/api/projects/${props.projectId}/sessions/${props.parentSessionId}/subagent/${props.sessionId}`
                : `/api/projects/${props.projectId}/sessions/${props.sessionId}`
            const url = `${baseUrl}/items/${props.lineNum}/tool-results/${props.toolId}/`
            const response = await apiFetch(url, { signal: abortController.value.signal })

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`)
            }

            data = await response.json()
        }
        resultData.value = data.results
        resultState.value = 'loaded'

        // Stop polling once two conditions both hold:
        //   1. We have at least ``requiredDisplayCount`` rows
        //      (placeholder is replaced by real content).
        //   2. The helper says the tool isn't running anymore — count-based
        //      tools flip this when ``resultCount`` reaches the expected
        //      total; chained-result shells flip it when their final
        //      chunk arrives carrying ``extra.is_terminated``.
        // Either condition unmet → keep polling so progressive output
        // (Codex's exec_command stream) keeps refreshing the body.
        const stillRunning = !transcriptFrozen.value
            && (toolHelpers.value?.isToolRunning(props.name, props.input, helperOptions.value) ?? false)
        const haveAll = !stillRunning
            && data.results
            && data.results.length >= requiredDisplayCount.value
        // A frozen transcript never grows: whatever this fetch returned is final.
        if (haveAll || transcriptFrozen.value) {
            stopPolling()
        } else if (!isPolling.value) {
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
 * Fetch the result if it isn't loaded yet, or was loaded but came back
 * short of ``requiredDisplayCount`` (retry). Shared by the "Result"
 * disclosure handlers and the inline-result auto-fetch path.
 */
function ensureResultFetched() {
    const shouldFetch = resultState.value === 'idle' ||
        (resultState.value === 'loaded' && (!resultData.value || resultData.value.length < requiredDisplayCount.value))
    if (shouldFetch) {
        fetchResult()
    }
}

/**
 * Handler for when the result details section is opened.
 * Fetches if idle, or if loaded but empty (to retry).
 */
function onResultOpen() {
    isResultOpen.value = true
    dataStore.setDetailOpen(props.sessionId, `result:${props.toolId}`, true)
    ensureResultFetched()
}

/**
 * Handler for when the result details section is closed.
 * Stops polling to avoid unnecessary requests.
 */
function onResultClose() {
    isResultOpen.value = false
    dataStore.setDetailOpen(props.sessionId, `result:${props.toolId}`, false)
    stopPolling()
}

/**
 * Handler for when the parent tool use details is closed.
 * Stops polling to avoid unnecessary requests.
 */
function onToolUseClose() {
    isOpen.value = false
    isResultOpen.value = false
    dataStore.setDetailOpen(props.sessionId, props.toolId, false)
    dataStore.setDetailOpen(props.sessionId, `result:${props.toolId}`, false)
    stopPolling()
}

/**
 * Handler for when the parent tool use details is opened.
 * If the result section is already open and has no data, triggers a fetch/poll.
 */
function onToolUseOpen() {
    isOpen.value = true
    dataStore.setDetailOpen(props.sessionId, props.toolId, true)
    // Fetch the result when it needs to be visible: the "Result" disclosure
    // is open, or this is an inline-result tool that renders its result
    // directly in the body as soon as the card opens.
    if (isResultOpen.value || (rendersResultInline.value && showResultDetails.value)) {
        ensureResultFetched()
    }
}

// Cleanup on unmount (e.g., when changing session, toggling groups)
onUnmounted(() => {
    stopPolling()
})

// KeepAlive active state (provided by SessionView)
const sessionActive = inject('sessionActive', ref(true))

// Frozen-transcript flag (provided by the read-only share viewer for snapshots /
// closed shares): nothing can be running there, so the running spinners and the
// result polling short-circuit off. The SPA never provides it.
const transcriptFrozen = inject('transcriptFrozen', ref(false))

// Request scroll-to-bottom from SessionItemsList (for auto-open expansion)
const requestScrollToBottomIfNeeded = inject('requestScrollToBottomIfNeeded', null)

// Track whether polling was suspended by deactivation (to resume on reactivation)
let resultPollingPaused = false

watch(sessionActive, (active) => {
    if (active) {
        // Reactivated: resume polling only if it was suspended and still needed
        if (resultPollingPaused) {
            resultPollingPaused = false
            // Resume only if results are still incomplete (polling is self-limiting).
            if (!resultData.value || resultData.value.length < requiredDisplayCount.value) {
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

// Computed for display: single result or array of multiple.
// While we haven't reached the helper's ``requiredDisplayCount`` yet,
// we pretend there's no result so the UI keeps showing the
// "Result not yet available …" message + polling spinner — matching
// the empty-result state. Beyond the threshold, behaviour is the
// pre-existing one: single → object, multiple → array.
const displayResult = computed(() => {
    if (!resultData.value || resultData.value.length === 0) return null
    if (resultData.value.length < requiredDisplayCount.value) return null
    // Providers may opt into a custom pick/transform when several
    // ToolResultLink rows back the same call but only one carries the
    // user-facing payload (Codex MCP tools, for example, push aside the
    // ``function_call_output`` and surface the richer
    // canonical ``McpToolCall`` item instead). ``undefined`` means
    // "default behaviour" — single row collapses to the row itself,
    // multiple rows stay as an array.
    // ``input`` rides along for helpers whose pick depends on what the
    // call asked for (Codex's code-mode ``exec`` resolving its nested
    // MCP call from the script source).
    const transformed = toolHelpers.value?.transformDisplayResult(
        props.name, resultData.value, { ...helperOptions.value, input: props.input },
    )
    if (transformed !== undefined) return transformed
    if (resultData.value.length === 1) return resultData.value[0]
    return resultData.value
})

// --- Tool input JHV overrides ---
// Force specific valueType for certain tool input keys (prevents markdown auto-detection).
const inputOverrides = computed(() => toolHelpers.value?.getInputOverrides(props.name, props.input) ?? {})

// --- Tool result JHV overrides ---
// Force specific valueType for certain tool result keys (prevents markdown auto-detection).
const resultOverrides = computed(() => toolHelpers.value?.getResultOverrides(props.name) ?? {})

// Make file_path relative to session's working directory when possible
const sessionBaseDir = computed(() => {
    const session = dataStore.getSession(props.sessionId)
    return session?.git_directory || session?.cwd || null
})

const summary = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return { displayName: null, inline: null }
    return helpers.computeToolSummary(props.name, props.input, sessionBaseDir.value)
})

// Bundle handed to helper hooks (``getHeaderLabel``,
// ``getSummaryRendering``, ``getExpectedResultCount``) on top of the
// raw ``extra`` prop the parent supplied. Exposes pre-bound lookup
// functions that hit the dataStore's existing indexes so helpers can
// resolve a tool_use's matching tool_result rows in O(k) instead of
// scanning the session linearly. The shell stays provider-agnostic
// — it just hands over the lookups, and individual helpers decide
// which ones they need (Codex iterates ``toolState.toolResultLineNums``
// to find the ``event_msg.*_end`` row by ``call_id``).
const helperOptions = computed(() => {
    // Surface the spawned subagent's nickname (joined into the
    // AgentLink payload at the API / WS boundary, see ``setAgentLink``
    // in ``stores/data.js``) so per-provider helpers can label the
    // tool card without separately hydrating the subagent Session row.
    // Reactive: when the ack arrives and ``setAgentLink`` runs, this
    // ref re-emits and any helper using ``agentSlug`` (e.g. Codex's
    // ``getSummaryRendering`` for ``spawn_agent`` showing ``(Bohr)``
    // next to the header) re-renders automatically.
    //
    // ``resultsArray`` exposes the already-fetched tool_result payload
    // list (one entry per ``ToolResultLink``, in tool_result_line_num
    // ASC order, with the wrapper unwrapped by ``get_tool_results``).
    // Helpers that need to walk the chain (aggregators, end-event
    // resolvers) read this directly instead of walking the session
    // items store — which only carries the visible window of items
    // and yields placeholders for chunks outside it.
    const agentLink = dataStore.getAgentLink(props.sessionId, props.toolId)
    return {
        ...(props.extra || {}),
        toolId: props.toolId,
        sessionId: props.sessionId,
        toolState: dataStore.getToolState(props.sessionId, props.toolId),
        // Live process state (reactive) so liveness-aware helpers can stop
        // a spinner once the session is idle — e.g. Codex's exec chain,
        // orphaned by a soft interrupt, has no closing chunk to flip
        // ``extra.is_terminated`` (see ``CodexToolHelpers.isToolRunning``).
        processState: dataStore.getProcessState(props.sessionId)?.state ?? null,
        // The link's own slug is resolved when the link is *created*, so it
        // is null whenever the subagent's transcript had not been parsed yet
        // — systematically on Codex multi-agent v2, where the parent
        // announces the spawn (`SubAgentActivity`) ~100 ms before the
        // subagent's file exists. The subagent's session row carries the
        // same nickname and reaches the store through its own
        // `session_updated`, so fall back to it; a reload re-resolves the
        // link's copy anyway (`fetchSubagentsState`).
        agentSlug: agentLink?.slug || (agentLink?.agentId
            ? dataStore.getSession(agentLink.agentId)?.slug || null
            : null),
        getSessionItem: (lineNum) => dataStore.getSessionItem(props.sessionId, lineNum),
        getToolState: (toolUseId) => dataStore.getToolState(props.sessionId, toolUseId),
        resultsArray: resultData.value,
    }
})

// Aggregated payload for chained-result tools (Codex's exec_command
// family, Claude Code's Monitor). Computed lazily — the helper
// short-circuits to ``null`` for every other tool, so the cost is paid
// only when the tool opted in via :meth:`shouldAggregateExecOutput`.
// Recomputes whenever ``resultData`` changes (new chunks arrive through
// the fetch / poll loop, which propagate via ``helperOptions``), driving
// the progressive rendering downstream.
const aggregatedExecOutput = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers?.shouldAggregateExecOutput?.(props.name)) return null
    return helpers.getAggregatedExecOutput?.(props.name, props.toolId, helperOptions.value) ?? null
})

// Static per-name header label override (e.g. "TodoWrite" → "Todo").
// Dynamic overrides driven by the tool's input (Task subagent_type, Skill name)
// continue to flow through summary.displayName.
const headerLabel = computed(() => toolHelpers.value?.getHeaderLabel(props.name, props.input, helperOptions.value) ?? null)

// Whether the error text should render as Markdown vs plain text.
const errorAsMarkdown = computed(() => !!toolHelpers.value?.errorIsMarkdown(props.name))

// Convenience refs so the template stays readable without value-piercing.
const displayName = computed(() => summary.value.displayName)

// Per-tool summary description rendering, resolved via the helper.
// Returns { component, props } or null. The shell renders it via
// <component :is> after the em-dash separator.
const summaryRendering = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return null
    return helpers.getSummaryRendering(props.name, props.input, sessionBaseDir.value, helperOptions.value)
})

// First modified line number from the backend patch (for "View in Files tab" navigation)
const firstModifiedLine = computed(() => {
    if (!fileChangeBackendPatch.value?.length) return null
    return fileChangeBackendPatch.value[0].newStart
})

// View-in-Files target: ``{ filePath, lineHint }`` or ``null``. The helper
// owns the per-tool wiring (which input key carries the file path, whether to
// scroll to the first modified line vs. an offset, etc.) so the shell stays
// neutral about input field names.
const openInFilesTarget = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return null
    return helpers.getOpenInFilesTarget(props.name, props.input, {
        firstModifiedLine: firstModifiedLine.value,
    })
})

// "View in Files tab" button: visible when the target file falls within a
// valid root. Uses the main session's roots (parent for subagents, self for
// regular sessions) to match what the Files tab can display.
const viewInFilesButtonId = computed(() => `view-in-files-${props.toolId}`)
const canViewInFilesTab = computed(() => {
    if (!viewFileInFilesTab) return false
    const target = openInFilesTarget.value
    if (!target) return false
    // Use the main session (parent for subagents, self for regular sessions)
    const mainSessionId = props.parentSessionId || props.sessionId
    const mainSession = dataStore.getSession(mainSessionId)
    // Artifacts dir is outside the project roots — it opens in the Artifacts tab.
    if (mainSession?.artifacts_dir && target.filePath.startsWith(mainSession.artifacts_dir + '/')) {
        return true
    }
    const project = dataStore.getProject(mainSession?.project_id || props.projectId)
    const roots = fileRootsFromStore(project, mainSession, dataStore)
    return roots.some(root => target.filePath.startsWith(root.path + '/'))
})

function openInFilesTab() {
    const target = openInFilesTarget.value
    if (!target) return
    viewFileInFilesTab(target.filePath, { lineNum: target.lineHint })
}

// Input object for the JsonHumanView fallback. The helper decides whether
// any field should be hidden (e.g. Claude Code drops ``description`` since
// it's already rendered in the summary header).
const displayInput = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return null
    return helpers.getDisplayInputObject(props.name, props.input)
})

const inputRendering = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return null
    return helpers.getInputRendering(props.name, props.input, {
        // Provider-specific extras (e.g. Claude Code's ``twiccTasksData``
        // for ``TaskList``) are spread first so the well-known keys below
        // always win on collision.
        ...(props.extra || {}),
        isSubagent: !!props.parentSessionId,
        backendPatch: fileChangeBackendPatch.value,
        backendPatchLoading: fileChangeBackendPatchLoading.value,
        originalFile: fileChangeOriginalFile.value,
        // Plumbed through so per-provider input renderers can locate
        // the matching tool_result rows in the store on their own
        // (e.g. Codex's ``apply_patch`` reading ``FileChange``).
        sessionId: props.sessionId,
        toolId: props.toolId,
    })
})

const resultRendering = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers || !displayResult.value) return null
    return helpers.getResultRendering(props.name, displayResult.value, props.input, {
        isSubagent: !!props.parentSessionId,
        // Chained-result tools read this instead of the raw
        // ``displayResult`` row; null for everyone else.
        aggregatedExecOutput: aggregatedExecOutput.value,
        // Raw ToolResultLink payload list, for helpers that branch on
        // whether a specific row shape has arrived (e.g. Codex's
        // code-mode ``exec`` checking for a rebound ``*_end`` event).
        resultsArray: resultData.value,
    })
})

// --- Tool running state (unified for all tracked tools) ---

const isTask = computed(() => !!toolHelpers.value?.isAgentTool(props.name))
// Provider-level opt-out for the Stop button next to View Agent — the
// shell offers it whenever an agent tool runs in background mode, but
// providers whose backend ``stop_subagent`` hook isn't wired yet (Codex
// today) can hide it instead of dispatching a request that drops on
// the floor. Default in BaseToolHelpers is true.
const canStopAgent = computed(() => !!providerHelpers.value?.canStopSubagent())
const toolState = computed(() => dataStore.getToolState(props.sessionId, props.toolId))

// Tool error: non-null error string means the tool_result reported an error
const toolErrorText = computed(() => toolState.value?.error || null)
const isToolError = computed(() => !!toolErrorText.value)

// Whether to still show the Result details when there's an error
// (Bash errors only show "Exit code N" so the full output is useful; Unknown errors need details too)
const showResultDetailsOnError = computed(() => {
    if (!isToolError.value) return false
    return !!toolHelpers.value?.showsResultOnError(props.name) || toolErrorText.value === 'Unknown error'
})

// Whether this tool's result renders inline (in the card body, in place
// of the "Result" disclosure) rather than behind a collapsible section.
// Drives both the template branch and the auto-fetch on card open.
const rendersResultInline = computed(() => (
    !!toolHelpers.value?.rendersResultInline(props.name, props.input)
))

// Central guard for Result details visibility
const showResultDetails = computed(() => {
    const helpers = toolHelpers.value
    // Resultless tools (``getExpectedResultCount`` returns 0) have no
    // matched output anywhere — opening the section would only trigger
    // an HTTP fetch that returns 404. Hide the section entirely.
    if (helpers?.getExpectedResultCount(props.name, props.input, helperOptions.value) === 0) {
        return false
    }
    // Tools that explicitly suppress the Result section (e.g. Claude Code's
    // TaskCreate / TaskUpdate / TaskGet: the tool_result body adds nothing
    // the input + summary haven't already shown).
    if (helpers?.hidesResult(props.name)) {
        return false
    }
    // A specialized input renderer (Edit/Write/TodoWrite) typically owns the
    // success-case UI on its own — the Result section stays hidden. Tools
    // that opt in via showsResultOnUnknownError still surface it for the
    // special "Unknown error" text (Edit/Write); TodoWrite does not.
    if (inputRendering.value) {
        if (toolErrorText.value === 'Unknown error') {
            return !!helpers?.showsResultOnUnknownError(props.name)
        }
        return false
    }
    if (isToolError.value) return showResultDetailsOnError.value
    return true
})

// --- Auto-open Edit/Write details for live diffs when setting is enabled ---
// Only auto-opens diffs that arrived via WebSocket (live), not historical diffs
// loaded from the API when opening/scrolling a session. The live flag is tracked
// per item line_num in the store (liveItems), set when session_items_added arrives.
const isLive = computed(() => dataStore.isItemLive(props.sessionId, props.lineNum))

const shouldAutoOpen = computed(() => {
    if (!settingsStore.showDiffs) return false
    if (isToolError.value) return false
    if (!isLive.value) return false
    return !!toolHelpers.value?.shouldAutoOpenLive(props.name, props.input)
})

// Guard: auto-open at most once per component instance
let hasAutoOpened = false

// immediate: true ensures auto-open fires on fresh mount (e.g., after virtual
// scroller recycles components when returning to a KeepAlive-cached session).
watch(shouldAutoOpen, (val) => {
    if (val && !hasAutoOpened && !isOpen.value) {
        hasAutoOpened = true
        // Request scroll-to-bottom BEFORE expanding so the anchor sentinel is
        // in view; native scroll anchoring then keeps the viewport at the
        // bottom as the item height grows during the auto-open expansion.
        requestScrollToBottomIfNeeded?.()
        isOpen.value = true
        // Always skip animation for auto-open (whether during setup or after mount).
        // The user should see the diff appear instantly, not animate open.
        instantOpen.value = true
        dataStore.setDetailOpen(props.sessionId, props.toolId, true)
        // Restore normal animation duration after the instant open has been rendered
        nextTick(() => { instantOpen.value = false })
    }
}, { immediate: true })

// Auto-close: if the diff was auto-opened and the tool result comes back with
// an error, close it — the diff will be stale since Claude will retry shortly.
watch(isToolError, (errored) => {
    if (errored && hasAutoOpened && isOpen.value) {
        isOpen.value = false
        dataStore.setDetailOpen(props.sessionId, props.toolId, false)
    }
})

// Diff stats for Edit/Write tools (parsed from the extra JSON field)
const fileChangeStats = computed(() => {
    const helpers = toolHelpers.value
    if (!helpers) return null
    return helpers.computeFileChangeStats(
        props.name,
        props.input,
        toolState.value,
        !!props.parentSessionId,
    )
})

// --- Edit/Write tools: fetch structuredPatch + originalFile from the tool_result item ---
// When fileChangeStats is available, we know the backend has computed hunk data.
// We fetch the actual tool_result item to get the real structuredPatch with file line numbers
// and the originalFile content for full-file diff rendering.
const fileChangeBackendPatch = ref(null)
const fileChangeOriginalFile = ref(null)
const fileChangeBackendPatchLoading = ref(false)

watchEffect(async () => {
    // Subagent path: helpers don't fetch backend patch (rendering uses input directly).
    if (props.parentSessionId) return

    const helpers = toolHelpers.value
    if (!helpers || !helpers.needsBackendPatchFetch(props.name) || !fileChangeStats.value) {
        fileChangeBackendPatch.value = null
        fileChangeOriginalFile.value = null
        return
    }
    // Pick the latest tool_result line for the backend-patch fetch
    // (preserves the previous ``Max(tool_result_line_num)``
    // aggregation semantic now that the API surfaces the full list
    // ordered ASC). Claude Code's Edit / Write only emit a single
    // result, so first == last in practice; other providers don't
    // reach this path (``needsBackendPatchFetch`` is false).
    const resultLineNums = toolState.value?.toolResultLineNums
    const lineNum = resultLineNums?.length ? resultLineNums[resultLineNums.length - 1] : null
    if (!lineNum) return
    if (fileChangeBackendPatch.value?._lineNum === lineNum) return

    function applyExtracted(parsed) {
        const data = helpers.extractBackendPatchData(parsed)
        if (!data) return
        if (data.patch) {
            fileChangeBackendPatch.value = Object.freeze(
                Object.assign([...data.patch], { _lineNum: lineNum })
            )
        }
        if (typeof data.originalFile === 'string') {
            fileChangeOriginalFile.value = data.originalFile
        }
    }

    const item = dataStore.getSessionItem(props.sessionId, lineNum)
    if (item && hasContent(item)) {
        applyExtracted(getParsedContent(item))
        return
    }
    // Share: the result line is ceiling-filtered out of /items, so fetch it by tool
    // id via the ceiling-exempt seam and seed the store — then extract as usual.
    if (fetchBackendPatchItems) {
        fileChangeBackendPatchLoading.value = true
        try {
            const rows = await fetchBackendPatchItems(props.toolId)
            if (rows?.length) {
                dataStore.addSessionItems(props.sessionId, rows)
                const fetched = dataStore.getSessionItem(props.sessionId, lineNum)
                if (fetched && hasContent(fetched)) applyExtracted(getParsedContent(fetched))
            }
        } finally {
            fileChangeBackendPatchLoading.value = false
        }
        return
    }
    fileChangeBackendPatchLoading.value = true
    try {
        await dataStore.loadSessionItemsRanges(
            props.projectId, props.sessionId, [lineNum], props.parentSessionId
        )
        const fetched = dataStore.getSessionItem(props.sessionId, lineNum)
        if (fetched && hasContent(fetched)) {
            applyExtracted(getParsedContent(fetched))
        }
    } finally {
        fileChangeBackendPatchLoading.value = false
    }
})

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

// --- Tool running spinner (for all tools except Agent/Task which have their own UI) ---

// Number of result rows the helper says are needed before the Result
// section can render meaningful content. Drives both the polling
// loop and the "Result not yet available" placeholder. Default 1
// (= render whatever arrives first); Codex's ``apply_patch`` raises
// it to 2 so the rich canonical ``FileChange`` item is always part of
// what's displayed (see helper docs).
const requiredDisplayCount = computed(() => (
    toolHelpers.value?.getRequiredResultCountForDisplay(props.name, props.input, helperOptions.value) ?? 1
))

const isToolRunning = computed(() => {
    if (transcriptFrozen.value) return false
    if (isTask.value) return false
    if (isStaleToolUse.value) return false
    // Defer to the provider-level helper so a tool whose finished-ness
    // is signalled by content (e.g. Codex's ``exec_command`` chain
    // reading ``toolState.extra.is_terminated``) overrides the default
    // count-based check without touching this shell.
    return toolHelpers.value?.isToolRunning(props.name, props.input, helperOptions.value) ?? false
})
const toolSpinnerId = computed(() => `tool-spinner-${props.toolId}`)

// --- View Agent button for Task tool_use ---

// Agent link: reactive lookup from the store cache.
// The cache is populated by fetchSubagentsState (on session load) and
// by the WS agent_link_created handler — no polling needed.
// Returns { agentId, isBackground } or undefined.
const agentLink = computed(() => dataStore.getAgentLink(props.sessionId, props.toolId))
const agentId = computed(() => agentLink.value?.agentId)

const agentCommentsCount = computed(() => {
    if (!agentId.value) return 0
    return codeCommentsStore.getCommentsBySession(props.projectId, props.parentSessionId || props.sessionId)
        .filter(c => c.subagentSessionId === agentId.value).length
})

// The subagent's own transcript reported it idle. Live, that lands on its
// session row (its `session_updated` refreshes `last_stopped_at`); after a
// reload the session may not be loaded at all, so the agent-links payload
// carries the same timestamp. Only consulted for providers whose spawn tool
// can outlive its subagent's completion — see `agentRunEndsOnSubagentIdle`.
const agentReportedIdle = computed(() => {
    if (!agentId.value) return false
    if (!toolHelpers.value?.agentRunEndsOnSubagentIdle?.()) return false
    return !!(dataStore.getSession(agentId.value)?.last_stopped_at ?? agentLink.value?.stoppedAt)
})

const isAgentRunning = computed(() => {
    if (transcriptFrozen.value) return false
    if (!isTask.value || !agentId.value) return false
    if (isStaleToolUse.value) return false
    if (agentReportedIdle.value) return false
    const resultCount = toolState.value?.resultCount || 0
    const requiredCount = (agentLink.value?.isBackground) ? 2 : 1
    return resultCount < requiredCount
})

// Whether the pre-ack "agent starting" spinner should be visible.
// True iff the spawn tool is still waiting for an AgentLink AND the
// helper considers the call to be still running. Mirrors the
// terminated-on-error pattern that ``CodexToolHelpers.isToolRunning``
// already applies to the shell family — a failed spawn ack writes
// ``toolState.error`` (and the backend flags ``extra.is_terminated``
// too), both of which the helper turns into ``isToolRunning=false``,
// so the spinner stops instead of spinning forever waiting for an
// ack that will never come. Without this guard, a rejected
// ``spawn_agent`` (e.g. Codex's "Full-history forked agents inherit
// the parent agent type, ..." text body sent in lieu of the
// ``{agent_id, nickname}`` JSON ack) leaves the card stuck on the
// spinner.
const isAgentSpawnPending = computed(() => {
    if (transcriptFrozen.value) return false
    if (!isTask.value) return false
    if (agentId.value) return false
    if (isStaleToolUse.value) return false
    return !!toolHelpers.value?.isToolRunning(props.name, props.input, helperOptions.value)
})

// Unique ID for the View Agent button (for tooltip targeting)
const viewAgentButtonId = computed(() => `view-agent-${props.toolId}`)

// Code comments indicator for file-change tools
const isEditOrWrite = computed(() => !!toolHelpers.value?.isFileChangeTool(props.name))
const toolCommentsCount = computed(() => {
    if (!isEditOrWrite.value) return 0
    if (!toolHelpers.value?.getFilePath(props.name, props.input)) return 0
    const rootSessionId = props.parentSessionId || props.sessionId
    return codeCommentsStore.getCommentsBySession(props.projectId, rootSessionId)
        .filter(c => c.source === 'tool' && c.sourceRef === props.toolId).length
})

// Track when a stop-agent request has been sent
const stoppingAgent = ref(false)

// Reset stoppingAgent when the agent stops running
watch(isAgentRunning, (running) => {
    if (!running) {
        stoppingAgent.value = false
    }
})

/**
 * Navigate to the subagent tab.
 */
function navigateToSubagent() {
    if (!agentId.value) return
    if (openSubagent) { openSubagent(agentId.value); return }
    // Preserve the current frame (prefix mode + current project + workspace) and
    // only open the subagent suffix. Carrying the workspace explicitly keeps it
    // even when viewing a cross-filter session whose project is outside it.
    router.push(sessionRouteLocation(
        { id: props.sessionId, project_id: props.projectId },
        route,
        { subagentId: agentId.value },
    ))
}

// --- Workflow link (the in-chat "View Workflow" button) ---
// run_id for this Workflow tool_use, reactive from the store cache (populated
// by fetchWorkflowLinks on load + the workflow_link_created WS event).
const isWorkflow = computed(() => props.name === 'Workflow')
const workflowRunId = computed(() => dataStore.getWorkflowLink(props.sessionId, props.toolId))
const viewWorkflowButtonId = computed(() => `view-workflow-${props.toolId}`)

/** Open the Workflows tab focused on this run. */
function navigateToWorkflow() {
    if (!workflowRunId.value) return
    // Same session, Workflows tab focused on this run: reuse the frame logic
    // (prefix mode + current project + workspace) and switch to the workflows tab.
    const location = sessionRouteLocation({ id: props.sessionId, project_id: props.projectId }, route)
    location.name = `${location.name}-workflows`
    location.params.runId = workflowRunId.value
    router.push(location)
}

/**
 * Stop the running agent via the SDK.
 */
function handleStopAgent() {
    if (agentId.value && isAgentRunning.value && !stoppingAgent.value) {
        stoppingAgent.value = true
        stopSubagent(props.sessionId, agentId.value)
    }
}

</script>

<template>
    <wa-details ref="toolUseDetailsRef" :open="isOpen" :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null" class="item-details tool-use" icon-placement="start" @wa-show.self="onToolUseOpen" @wa-hide.self="onToolUseClose">
        <div slot="summary" class="items-details-summary">
            <div class="items-details-summary-left">
                <strong v-if="isTask && displayName" class="items-details-summary-name">{{ displayName.name }}<span v-if="displayName.namespace" class="items-details-summary-quiet"> ({{ displayName.namespace }})</span></strong>
                <strong v-else class="items-details-summary-name">{{ formatToolNameForHeader(name, headerLabel) }}</strong>
                <template v-if="summaryRendering">
                    <span class="items-details-summary-separator"> — </span>
                    <component :is="summaryRendering.component" v-bind="summaryRendering.props" />
                    <CodeCommentsIndicator :count="toolCommentsCount" :show-tooltip="false" class="tool-comments-indicator" />
                </template>
            </div>
            <div class="items-details-summary-right">
                <!-- View Agent indicator for Task tool_use (only in regular sessions) -->
                <template v-if="isTask && !parentSessionId">
                    <!-- Agent not yet started: spinner. Hidden when the spawn
                         itself failed (toolState.error) or was otherwise
                         flagged terminated by the backend — see
                         isAgentSpawnPending. -->
                    <wa-spinner v-if="isAgentSpawnPending" class="agent-starting-spinner"></wa-spinner>
                    <!-- Agent started: View Agent button (with pulsing robot
                         if still running). Skipped when the spawn ack failed
                         and no AgentLink was ever created — the error
                         callout below already tells the user what happened,
                         and a View Agent click would just be a no-op. -->
                    <template v-else-if="agentId">
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
                            <wa-icon v-if="isAgentRunning" slot="start" name="robot" class="agent-running-icon" :style="{ color: PROCESS_STATE_COLORS[PROCESS_STATE.ASSISTANT_TURN] }"></wa-icon>
                            View Agent
                            <CodeCommentsIndicator slot="end" :count="agentCommentsCount" :show-tooltip="false" class="agent-comments-indicator" />
                        </wa-button>
                        <wa-button
                            v-if="isAgentRunning && agentLink?.isBackground && canStopAgent"
                            :id="`stop-agent-${props.toolId}`"
                            size="small"
                            variant="danger"
                            appearance="filled"
                            class="stop-agent-button"
                            :loading="stoppingAgent"
                            :disabled="stoppingAgent"
                            @click.stop="handleStopAgent"
                        >
                            <wa-icon name="ban" label="Stop Agent"></wa-icon>
                        </wa-button>
                        <AppTooltip v-if="isAgentRunning && agentLink?.isBackground && canStopAgent" :for="`stop-agent-${props.toolId}`">Stop this agent</AppTooltip>
                    </template>
                </template>
                <!-- View Workflow indicator for the Workflow tool_use (regular sessions only) -->
                <template v-if="isWorkflow && !parentSessionId && workflowRunId">
                    <wa-button
                        :id="viewWorkflowButtonId"
                        size="small"
                        variant="brand"
                        appearance="outlined"
                        @click.stop="navigateToWorkflow"
                    >
                        <wa-icon slot="start" name="sitemap"></wa-icon>
                        View Workflow
                    </wa-button>
                    <AppTooltip :for="viewWorkflowButtonId">Open this workflow run</AppTooltip>
                </template>
                <!-- Tool running spinner (Bash, WebFetch, MCP, etc.) -->
                <template v-if="isToolRunning">
                    <AppTooltip v-if="toolStartedAt" :for="toolSpinnerId">
                        Running for <ProcessDuration :state-changed-at="toolStartedAt" />
                    </AppTooltip>
                    <wa-spinner :id="toolSpinnerId" class="tool-running-spinner"></wa-spinner>
                </template>
                <!-- File change stats (Edit / Write) -->
                <span v-if="fileChangeStats" class="file-change-stats">
                    <span class="diff-added">+{{ fileChangeStats.lines_added }}</span>
                    <span v-if="fileChangeStats.lines_removed != null" class="diff-removed">-{{ fileChangeStats.lines_removed }}</span>
                </span>
                <!-- Tool error indicator -->
                <wa-icon v-if="isToolError" name="xmark" class="tool-error-icon"></wa-icon>
                <!-- View in Files tab button (Read / Write / Edit) — last in the row -->
                <template v-if="canViewInFilesTab">
                    <wa-button
                        :id="viewInFilesButtonId"
                        size="small"
                        variant="neutral"
                        appearance="outlined"
                        class="view-in-files-button"
                        @click.stop="openInFilesTab"
                    >
                        <wa-icon name="folder-open"></wa-icon>
                    </wa-button>
                    <AppTooltip :for="viewInFilesButtonId">View in Files tab</AppTooltip>
                </template>
            </div>
        </div>
        <template v-if="isOpen">
            <component
                v-if="inputRendering"
                :is="inputRendering.component"
                v-bind="inputRendering.props"
            />
            <div v-else-if="displayInput" class="tool-input">
                <JsonHumanView :value="displayInput" :overrides="inputOverrides" />
            </div>
            <div v-else class="tool-no-input">
                No input parameters
            </div>
            <!-- Tool error message (shown directly, replaces the Result details unless Bash/Unknown) -->
            <wa-callout v-if="isToolError" variant="danger" appearance="outlined" class="tool-error-message">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                <MarkdownContent v-if="errorAsMarkdown" :source="toolErrorText" />
                <template v-else>{{ toolErrorText }}</template>
            </wa-callout>
            <template v-if="showResultDetails">
                <!-- Inline-result tools (e.g. view_image): the result IS the
                     body — render it directly in place of the "Result"
                     disclosure, fetched as soon as the card opened. -->
                <div v-if="rendersResultInline" class="tool-result-inline">
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
                        <component
                            v-if="resultRendering"
                            :is="resultRendering.component"
                            v-bind="resultRendering.props"
                        />
                        <JsonHumanView
                            v-else
                            :value="displayResult"
                            :overrides="resultOverrides"
                        />
                    </div>
                </div>
                <wa-details v-else ref="resultDetailsRef" :open="isResultOpen" :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null" class="tool-result" @wa-show="onResultOpen" @wa-hide="onResultClose">
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
                            <component
                                v-if="resultRendering"
                                :is="resultRendering.component"
                                v-bind="resultRendering.props"
                            />
                            <JsonHumanView
                                v-else
                                :value="displayResult"
                                :overrides="resultOverrides"
                            />
                        </div>
                    </div>
                </wa-details>
            </template>
        </template>
    </wa-details>
</template>

<style scoped>
wa-details::part(content) {
    padding-top: 0;
}

wa-details.item-details {
    .items-details-summary {
        .agent-starting-spinner, .tool-running-spinner {
            font-size: 1.2em;
        }
        .agent-starting-spinner {
            --indicator-color: var(--wa-color-warning-60);
        }
        .agent-running-icon {
            animation: pulse 1s ease-in-out infinite;
        }
        .agent-comments-indicator, .tool-comments-indicator {
            font-size: var(--wa-font-size-xs);
        }
    }

    .tool-error-icon {
        color: var(--wa-color-danger-50);
        font-size: 1.2em;
    }

    .file-change-stats {
        display: flex;
        gap: var(--wa-space-xs);
        font-family: var(--wa-font-mono);
        font-weight: bold;
        white-space: nowrap;

        .diff-added {
            color: var(--wa-color-success-50);
        }
        .diff-removed {
            color: var(--wa-color-danger-50);
        }
    }
}

/* Hide the "BASH" language label for tool inputs (used for bash tools, so it's always bash) */
.tool-input > .jhv-node :deep(pre.shiki[data-language="bash"]) {
    padding-top: 16px;
}
.tool-input > .jhv-node :deep(pre.shiki[data-language="bash"]::before) {
    display: none;
}

.tool-input {
    padding: 0;
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

/* Inline-result body (in place of the "Result" disclosure) — same top
 * gap as the collapsible so the card spacing stays consistent. */
.tool-result-inline {
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

.tool-error-message {
    margin-top: var(--wa-space-xs);
}

.tool-result-error {
    color: var(--wa-color-danger-60);
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

@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
</style>

<style>
/* Common summary classes — non-scoped so per-tool summary mini-components
 * (under items/claude_code/summary/) can use them by name without scoped CSS
 * isolation. SessionItem.vue (parent) already declares
 * `.items-details-summary-separator` and base `.items-details-summary-description`
 * styles globally; we keep the shell-specific `word-wrap: break-word` override
 * here so the description still wraps the way it did before this refactor. */

.items-details-summary-description {
    /* Less aggressive than SessionItem.vue's `overflow-wrap: anywhere` —
     * preserves pre-refactor wrapping behaviour for tool descriptions. */
    word-wrap: break-word;

    &.no-wrap {
        white-space: nowrap;
    }

    /* Single-line ellipsis truncation (opt-in via DescriptionSummary's
     * ``truncate`` prop — Codex's Exec summary uses it). The
     * ``min-width: 0`` lets the span shrink below its intrinsic
     * ``max-content`` inside the inline-flex parent. */
    &.truncate-summary {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        min-width: 0;
    }
}

/* When the summary uses an icon wrapper, the wrapper itself must be
 * shrinkable too so its inner truncate-summary span has a width to
 * work against. */
.items-details-summary-file.truncate-summary-wrapper {
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
}

.items-details-summary-quiet {
    color: var(--wa-color-text-quiet);
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
</style>
