<script setup>
import { computed, watch, ref, reactive, provide, nextTick, inject, onMounted, onBeforeUnmount, onActivated, onDeactivated } from 'vue'
import { useRouter } from 'vue-router'
import { useDebounceFn } from '@vueuse/core'
import { useDataStore } from '../../../stores/data'
import { INITIAL_ITEMS_COUNT, DISPLAY_MODE } from '../../../constants'
import { useSettingsStore } from '../../../stores/settings'
import { toast } from '../../../composables/useToast'
import { apiFetch } from '../../../utils/api'
import { getParsedContent, hasContent } from '../../../utils/parsedContent'
import { pendingSessionSearch } from '../../../utils/pendingSearch'
import { createComputePendingHint } from '../../../utils/computePendingHint.js'
import { resolveSessionComposerLock } from '../../../utils/sessionComposerLock.js'
import { classifyHref } from '../../../utils/fileLinks.js'
import { fileRootsFromStore } from '../../../utils/projectRoots'
import VirtualScroller from '../../virtual-scroller/VirtualScroller.vue'
import SessionItem from './SessionItem.vue'
import DaySeparator from './items/DaySeparator.vue'
import SessionSearchBar from '../list/SessionSearchBar.vue'
import FetchErrorPanel from '../../ui/FetchErrorPanel.vue'
import GroupToggle from './GroupToggle.vue'
import { useCodeCommentsStore } from '../../../stores/codeComments'
import MessageInput from '../../message/MessageInput.vue'
import PendingRequestForm from '../../message/PendingRequestForm.vue'
import HybridTerminalBlock from '../../message/HybridTerminalBlock.vue'
import GoalBlock from '../../message/GoalBlock.vue'
import ProcessIndicator from '../../ui/ProcessIndicator.vue'
import TextSelectionComment from './TextSelectionComment.vue'
import ChatNavToolbar from './ChatNavToolbar.vue'
import { useTextSelectionComment } from '../../../composables/useTextSelectionComment'
import { useChatNavigation } from '../../../composables/useChatNavigation'
import { getProviderLabel } from '../../../providers'

// All states should animate for the bottom process indicator
const BOTTOM_INDICATOR_ANIMATE_STATES = ['assistant_turn', 'user_turn', 'dead']

// Duration to show temporary indicators (user_turn, dead) in milliseconds
const TEMPORARY_INDICATOR_DURATION = 10000

const props = defineProps({
    sessionId: {
        type: String,
        required: true
    },
    parentSessionId: {
        type: String,
        default: null
    },
    projectId: {
        type: String,
        required: true
    },
})

const store = useDataStore()
const codeCommentsStore = useCodeCommentsStore()

const emit = defineEmits(['needs-title'])

// KeepAlive active state (provided by SessionView)
const sessionActive = inject('sessionActive', ref(true))

// Track whether items were added while the session was inactive,
// and whether the user was near the bottom before deactivation.
// Used on reactivation to decide if we should auto-scroll to bottom.
let itemCountAtDeactivation = null
let wasNearBottomAtDeactivation = false

// Saved scroll anchor for KeepAlive restore.
// Acts as a safety net: the VirtualScroller's composable handles suspend/resume
// internally via v-show, but SessionItemsList also captures the anchor in case
// the VirtualScroller needs to be restored from this level (e.g., if items changed
// while inactive and the composable's anchor is stale).
let savedScrollAnchor = null

// Pending scroll-to-bottom operation deferred because the scroller container was
// hidden (e.g., chat tab panel has display:none when navigating directly to /files).
// Set when scrollToBottomUntilStable would be called but the container has 0 height.
// Consumed by onScrollerBecameVisible when the container first gets a positive height.
let pendingScrollToBottom = null

// Reference to the VirtualScroller component
const scrollerRef = ref(null)

// In-session search bar state
const sessionSearchRef = ref(null)
const showSessionSearch = ref(false)

// Flag to track if we're currently auto-scrolling to bottom
// Used to handle new items arriving during the scroll retry loop
const isAutoScrollingToBottom = ref(false)

// Flag to track if we're in the initial scroll phase (scroller hidden until positioned)
// This prevents visible jumping when the scroller first appears at top then scrolls to bottom
const isInitialScrolling = ref(false)

// Callback to resolve when scroll stabilizes (set by scrollToEdgeUntilStable)
let onStabilizedCallback = null

// Timeout ID for the stability debounce
let stabilityTimeoutId = null

// Timeout ID for the absolute upper bound on the stability wait (see scrollToEdgeUntilStable).
// Independent of the debounce above — never reset by resize events.
let stabilityMaxWaitId = null

// The edge scroll currently in flight: `{ edge, promise }`, or null.
// Used to prevent concurrent calls to scrollToEdgeUntilStable — including a
// top and a bottom fighting over the scroll position.
let edgeScrollOperation = null

// Delay in ms to wait for no more resize events before considering stable
const STABILITY_DEBOUNCE_MS = 100

// Absolute cap on the initial-reveal stability wait. The debounce above is reset on every
// resize; if resizes never settle (e.g. a sub-pixel ResizeObserver loop), it would never fire
// and the scroller would stay visibility:hidden forever. This bound guarantees the reveal.
const MAX_STABILITY_WAIT_MS = 1000

// Buffer: load N items before/after visible range
const LOAD_BUFFER = 50

// Debounce delay for scroll-triggered loading (ms)
const LOAD_DEBOUNCE_MS = 150

// Minimum item size for the virtual scroller (in pixels)
const MIN_ITEM_SIZE = 50

// Track pending range to load (accumulated during debounce)
const pendingLoadRange = ref(null)

// Drag and drop state
const dragOverType = ref(null)  // null | 'files' | 'text'
let dragCounter = 0  // Track enter/leave events for nested elements
const messageInputRef = ref(null)
const pendingFormRef = ref(null)
const hybridTerminalRef = ref(null)
const goalBlockRef = ref(null)
// Visibility + attention reported by the hybrid terminal block: drive the
// composer's top separator (visible = open OR the warning callout is up) and
// the composer hybrid-icon tint (attention = the callout is up).
const hybridTerminalVisible = ref(false)
const hybridTerminalAttention = ref(false)
function onHybridTerminalState(state) {
    hybridTerminalVisible.value = state.visible
    hybridTerminalAttention.value = state.attention
}

// Session data
const session = computed(() => store.getSession(props.sessionId))
const project = computed(() => store.getProject(props.projectId))
const providerLabel = computed(() => getProviderLabel(session.value?.provider))
const settingsStore = useSettingsStore()

// Hybrid CLI mode: the embedded terminal block replaces the pending-request
// widget and the composer is never sending-locked (steering is allowed).
const isHybridSession = computed(() => session.value?.hybrid === true)

// Whether the session is stale (JSONL files deleted, history preserved as read-only)
const isStale = computed(() => session.value?.stale === true)

// Whether the session's provider is currently usable (intent-enabled AND
// runtime-running). Refusal during transient `starting` / `stopping`
// matches the back gate (`ensure_provider_running`).
const isProviderEnabled = computed(() => {
    const p = session.value?.provider
    return p ? store.isProviderAvailable(p) : true
})

// Session items (raw, with metadata + content)
const items = computed(() => store.getSessionItems(props.sessionId))

// Visual items (filtered by display mode and expanded groups)
const visualItems = computed(() => store.getSessionVisualItems(props.sessionId))

// Check if session computation is pending
const isComputePending = computed(() => {
    const sess = store.getSession(props.sessionId)
    return sess && sess.compute_version_up_to_date === false
})
const computePendingHintPhase = ref(null)
const computePendingHint = createComputePendingHint({
    setPhase: phase => { computePendingHintPhase.value = phase },
})

// Loading and error states
const isLoading = computed(() => store.areSessionItemsLoading(props.sessionId))
const hasError = computed(() => store.didSessionItemsFailToLoad(props.sessionId))

// Process state for this session (starting, assistant_turn, user_turn, dead)
const processState = computed(() => store.getProcessState(props.sessionId))

// Pending requests for this session (tool approvals and/or ask-user questions).
// The CLI can run multiple concurrency-safe tools in parallel, each with its own
// permission ask, so this is a list ordered oldest-first.
const pendingRequests = computed(() => store.getPendingRequests(props.sessionId))

// The next request to show (oldest first — FIFO queue)
const pendingRequest = computed(() => pendingRequests.value[0] || null)

// Whether any pending request is active. On a main session the request and the
// composer share the footer, each independently collapsible; on a subagent the
// request stands alone (no composer).
const hasPendingRequest = computed(() => pendingRequests.value.length > 0)

// Whether the head pending request can be answered from the GUI. Hybrid
// sessions register real answerable requests (the hook polls a status file
// for the answer); a request degraded to `hybrid_terminal` lost its GUI
// channel (CLI-side hook timeout) and is only answerable inside the TUI —
// the terminal block's badge is its surface.
const hasAnswerablePendingRequest = computed(() =>
    hasPendingRequest.value && pendingRequest.value.request_type !== 'hybrid_terminal'
)
const composerSendingLock = computed(() => resolveSessionComposerLock({
    hasAnswerablePendingRequest: hasAnswerablePendingRequest.value,
    isComputePending: isComputePending.value,
}))

// The session's current goal (last entry of the /goal lifecycle history, null
// when none or when the user dismissed it) — drives the goal bar at the very
// top of the footer stack.
const currentGoal = computed(() => store.getSessionCurrentGoal(props.sessionId))

// ── Footer accordion ─────────────────────────────────────────────────────────
// At most one of the four footer panels is open: the message input, the hybrid
// terminal, the pending-request form, or the goal panel. ``openBlock`` is the
// single source of truth — opening one reduces the others. Collapsing the
// composer or the pending-request form reaches the all-minimized 'none' state
// (their minimize button opens nothing else); closing the terminal or the goal
// panel returns to the composer (they displaced it). Each panel is driven
// through emit-free imperative setters (expand/collapse, open/close,
// restore/minimize), so applying the state never loops back into a request.
const openBlock = ref('message-input') // 'message-input' | 'terminal' | 'pending' | 'goal' | 'none'

function applyOpenBlock() {
    const id = openBlock.value
    if (messageInputRef.value) id === 'message-input' ? messageInputRef.value.expand() : messageInputRef.value.collapse()
    if (hybridTerminalRef.value) id === 'terminal' ? hybridTerminalRef.value.open() : hybridTerminalRef.value.close()
    if (pendingFormRef.value) id === 'pending' ? pendingFormRef.value.restoreIfMinimized() : pendingFormRef.value.minimize()
    if (goalBlockRef.value) id === 'goal' ? goalBlockRef.value.open() : goalBlockRef.value.collapse()
}
watch(openBlock, () => nextTick(applyOpenBlock))

// The open goal panel vanishing under the accordion (dismissed — possibly from
// another tab — or superseded state hiding the bar entirely): return home to
// the composer, like the last pending request resolving. No focus steal.
watch(currentGoal, (goal) => {
    if (!goal && openBlock.value === 'goal') goToComposer()
})

// Move keyboard focus into a block's natural target: the composer textarea, the
// embedded terminal, the pending form's primary control, or the goal panel's
// scrollable body. Each child's
// requestFocus is order-independent (focuses now if shown, else once the
// accordion opens it), so this can fire right after flipping ``openBlock``.
function focusBlock(id) {
    if (id === 'message-input') messageInputRef.value?.requestFocus?.()
    else if (id === 'terminal') hybridTerminalRef.value?.requestFocus?.()
    else if (id === 'pending') pendingFormRef.value?.requestFocus?.()
    else if (id === 'goal') goalBlockRef.value?.requestFocus?.()
}

// Open a block (the accordion reduces the others). ``focus`` only when a caller
// wants the caret moved — never on a session switch or the initial mount.
function setOpenBlock(id, { focus = false } = {}) {
    openBlock.value = id
    if (focus) focusBlock(id)
}

// Returning home to the composer — closing the terminal, or the last pending
// request resolving. ``focus`` is controlled by the caller.
function goToComposer(focus = false) {
    setOpenBlock('message-input', { focus })
}

// The pending form's own minimize button — mirrors the composer's: minimize
// everything ('none') and open nothing else, so reducing the open block never
// pops another one up. Collapse it explicitly (not only via the openBlock
// transition) so it still works in the defensive case where ``openBlock`` is
// already 'none' — there setOpenBlock is a no-op, the watcher never fires, and
// applyOpenBlock would never call minimize(). Belt and braces on top of the
// mount-time accordion init (the immediate sessionId watch below).
function collapsePendingRequest() {
    pendingFormRef.value?.minimize()
    openBlock.value = 'none'
}

// A new pending request takes the slot (focus it); resolving the last one
// returns home to the composer and focuses it, ready for the next message —
// except on touch devices, where stealing focus pops the on-screen keyboard.
// `flush: 'post'` is required: the form mounts via `v-if` in the *same* flush,
// so a default 'pre' watcher would run before `pendingFormRef` is set and
// `focusBlock('pending')` would silently no-op (the form's `requestFocus` is
// the unconditional focuser that moves the caret onto `.auto-focused`, even
// out of the composer textarea). Focus only on the active session — a
// background split-view/KeepAlive instance opens the block but never grabs
// focus.
watch(() => pendingRequest.value?.request_id, (id, oldId) => {
    if (id && id !== oldId) setOpenBlock('pending', { focus: sessionActive.value })
    else if (!id && oldId && openBlock.value === 'pending') goToComposer(!settingsStore.isTouchDevice)
}, { flush: 'post' })

// Reset on session switch (this component is reused across sessions). Force a
// re-apply even when the value is unchanged, so the panels reset too.
// ``immediate`` so this also runs on the very first mount: a cold load directly
// onto a session that already has a pending request would otherwise leave
// ``openBlock`` stuck at its 'message-input' default — the accordion invariant
// is never established, applyOpenBlock never runs, and the pending form's
// minimize button (which relies on the openBlock transition) silently no-ops.
watch(() => props.sessionId, () => {
    openBlock.value = pendingRequest.value ? 'pending' : 'message-input'
    nextTick(applyOpenBlock)
}, { immediate: true })

// External footer-panel navigation (global Alt+Shift+{PageDown/PageUp/T}
// shortcuts and the matching command-palette actions, routed through
// ``gotoChatFooterPanel``). Window events so they reach whichever main session
// is active — guarded to it (not a backgrounded KeepAlive instance, not a
// subagent). Each is a no-op when its target panel can't be shown.
//
// Focus rule: open the panel (reducing the others) and focus it — but ONLY when
// focus isn't already inside it. That honors the shortcuts' "go to X if not
// already there" (already open + focused → nothing happens, no caret jump in the
// composer), keeps focus when arriving from another panel, and still focuses for
// the command-palette actions (the palette has stolen focus by the time they run).
function focusIsInside(selector) {
    return !!document.activeElement?.closest?.(selector)
}
function gotoMessageInput() {
    if (!sessionActive.value || props.parentSessionId) return
    setOpenBlock('message-input', { focus: !focusIsInside('.message-input') })
}
function gotoPendingRequest() {
    if (!sessionActive.value || props.parentSessionId) return
    if (hasAnswerablePendingRequest.value) {
        setOpenBlock('pending', { focus: !focusIsInside('.pending-request-form') })
        return
    }
    // No pending request: Page Up from the message input on a hybrid session
    // opens the CLI terminal instead (its "up" neighbour). The command-palette
    // "Focus Pending Request" never reaches here — its when-guard requires a
    // pending request.
    if (openBlock.value === 'message-input' && isHybridSession.value) {
        setOpenBlock('terminal', { focus: !focusIsInside('.hybrid-terminal-block') })
    }
}
function gotoTerminal() {
    if (!sessionActive.value || props.parentSessionId) return
    if (!isHybridSession.value) return
    setOpenBlock('terminal', { focus: !focusIsInside('.hybrid-terminal-block') })
}
// Alt+Shift+T is double-acting: open the terminal when it isn't the open panel,
// or swap back to the message input when it already is. (The command-palette
// "Open Claude CLI Terminal" stays open-only, via gotoTerminal above.)
function toggleTerminal() {
    if (!sessionActive.value || props.parentSessionId) return
    if (!isHybridSession.value) return
    if (openBlock.value === 'terminal') {
        setOpenBlock('message-input', { focus: !focusIsInside('.message-input') })
    } else {
        setOpenBlock('terminal', { focus: !focusIsInside('.hybrid-terminal-block') })
    }
}
// Alt+Shift+H / "Toggle Hybrid Mode": trigger the composer's hybrid button as if
// clicked (enable / disable a draft, stage / un-stage an SDK session, or open the
// confirm dialog). The composer's own method gates feasibility (Claude only, not
// a committed-permanent session).
function toggleHybrid() {
    if (!sessionActive.value || props.parentSessionId) return
    messageInputRef.value?.hybridToggle?.()
}

/**
 * Whether the VirtualScroller should be visible.
 * Uses v-show (not v-if) to keep the component alive across KeepAlive cycles,
 * preserving the composable's height cache and scroll state.
 *
 * The scroller is shown when:
 * - Not in compute pending state
 * - No loading error
 * - Not currently loading
 * - There are items to display
 */
const showVirtualScroller = computed(() => {
    return !isComputePending.value && !hasError.value && !isLoading.value && (visualItems.value?.length > 0)
})

// Timer for temporary indicator display (user_turn, dead)
let temporaryIndicatorTimer = null
const showTemporaryIndicator = ref(false)

// Computed: should we show the bottom process indicator?
// - Always show for starting/assistant_turn
// - Show for user_turn/dead only for TEMPORARY_INDICATOR_DURATION seconds
const shouldShowProcessIndicator = computed(() => {
    if (!processState.value) return false
    const state = processState.value.state
    if (state === 'starting' || state === 'assistant_turn') return true
    if ((state === 'user_turn' || state === 'dead') && showTemporaryIndicator.value) return true
    return false
})

// Watch process state changes to manage temporary indicator
// Only show user_turn/dead when the state actually CHANGES (not on initial mount)
// Guarded: skip timer creation when inactive (KeepAlive deactivated)
watch(processState, (newState, oldState) => {
    // Clear any existing timer
    if (temporaryIndicatorTimer) {
        clearTimeout(temporaryIndicatorTimer)
        temporaryIndicatorTimer = null
    }

    if (!newState) {
        showTemporaryIndicator.value = false
        return
    }

    // Skip timer creation when inactive (DOM is detached)
    if (!sessionActive.value) return

    const state = newState.state
    const oldStateValue = oldState?.state

    if (state === 'user_turn' || state === 'dead') {
        // Only show if state actually changed (not on initial mount when already in this state)
        if (oldState && oldStateValue !== state) {
            showTemporaryIndicator.value = true
            temporaryIndicatorTimer = setTimeout(() => {
                showTemporaryIndicator.value = false
                temporaryIndicatorTimer = null
            }, TEMPORARY_INDICATOR_DURATION)
        } else {
            // Initial mount or same state - don't show temporary indicator
            showTemporaryIndicator.value = false
        }
    } else {
        showTemporaryIndicator.value = false
    }
}, { immediate: true })

// Cleanup timers when deactivated (KeepAlive moves DOM to detached storage)
onDeactivated(() => {
    if (temporaryIndicatorTimer) {
        clearTimeout(temporaryIndicatorTimer)
        temporaryIndicatorTimer = null
    }
    if (stabilityTimeoutId) {
        clearTimeout(stabilityTimeoutId)
        stabilityTimeoutId = null
    }
    if (stabilityMaxWaitId) {
        clearTimeout(stabilityMaxWaitId)
        stabilityMaxWaitId = null
    }

    // Capture state for reactivation: track item count and scroll position
    itemCountAtDeactivation = visualItems.value?.length ?? null
    const scroller = scrollerRef.value
    wasNearBottomAtDeactivation = scroller ? scroller.isAtBottom() : false

    // Save scroll anchor as safety net for restoration after reactivation
    savedScrollAnchor = scroller ? scroller.getScrollAnchor() : null

    // Clear pending scroll — KeepAlive deactivation takes over via handlePostResume
    pendingScrollToBottom = null

    // If the initial scroll was deferred (scroller hidden on first load), reset the flag
    // so the scroller isn't left permanently invisible after reactivation.
    isInitialScrolling.value = false
})

// On reactivation: handle scroll restoration after KeepAlive reattaches the DOM.
//
// The VirtualScroller composable handles anchor-based scroll restoration internally
// via suspend/resume. If the container is hidden (e.g., inactive wa-tab-panel),
// resume is deferred until the container becomes visible.
//
// SessionItemsList adds one override on top: if items were added while inactive
// and the user was near bottom, scroll to bottom instead of restoring the anchor.
// This must also be deferred if the container is not yet visible.
onActivated(() => {
    handlePostResume()
    // Coming back to an already-loaded session: verify its items still cover
    // everything the server has. Broadcasts lost during a disconnect can leave
    // holes that neither the reconciliation nor the live gap-fill caught (e.g.
    // an unrelated broadcast refreshed the session's mtime during the
    // reconciliation window, hiding it from the changed-set). Cheap local
    // scan; only fetches when lines are actually missing.
    if (store.areSessionItemsFetched(props.sessionId)) {
        store.ensureSessionItemsCoverage(props.sessionId).catch(() => {})
    }
})

// Watch for deferred resume completion: when the scroller's suspended state
// transitions from true to false, the actual resume just happened (possibly
// deferred because the container was hidden). Apply any post-resume overrides.
watch(
    () => scrollerRef.value?.suspended?.value,
    (newVal, oldVal) => {
        if (oldVal === true && newVal === false) {
            nextTick(() => handlePostResume())
        }
    },
)

/**
 * Apply post-resume logic: scroll to bottom if items were added while inactive
 * and the user was near bottom. Only acts if the scroller is not suspended
 * (i.e., the composable has completed its resume and the container is visible).
 */
function handlePostResume() {
    const scroller = scrollerRef.value
    if (!scroller || scroller.suspended?.value) return

    if (
        itemCountAtDeactivation !== null
        && visualItems.value?.length > itemCountAtDeactivation
        && wasNearBottomAtDeactivation
    ) {
        scrollToBottomUntilStable()
    }

    // Clear saved state now that we've handled reactivation
    itemCountAtDeactivation = null
    wasNearBottomAtDeactivation = false
    savedScrollAnchor = null
}

/**
 * Handle VirtualScroller becoming visible after being hidden (e.g., switching
 * from Files/Git tab to Chat tab when the session was loaded while Chat was hidden).
 *
 * Executes any deferred scrollToBottomUntilStable that couldn't run while the
 * scroller container had 0 height.
 */
function onScrollerBecameVisible() {
    if (pendingScrollToBottom) {
        const options = pendingScrollToBottom
        pendingScrollToBottom = null
        scrollToBottomUntilStable(options)
    }
}

// Build base URL for API calls (handles subagent case)
const apiBaseUrl = computed(() => {
    if (props.parentSessionId) {
        return `/api/projects/${props.projectId}/sessions/${props.parentSessionId}/subagent/${props.sessionId}`
    }
    return `/api/projects/${props.projectId}/sessions/${props.sessionId}`
})

/**
 * Load subagent session details from API and add to store.
 * Called when opening a subagent tab if the session is not already in the store.
 * This handles the case of direct URL access before WebSocket has delivered the session.
 */
async function loadSubagentSession() {
    try {
        const url = `${apiBaseUrl.value}/`
        const response = await apiFetch(url)
        if (!response.ok) {
            console.error('Failed to load subagent session:', response.status)
            return null
        }
        const sessionData = await response.json()
        store.addSession(sessionData)
        return sessionData
    } catch (error) {
        console.error('Failed to load subagent session:', error)
        return null
    }
}

/**
 * Load session data: metadata (all items) + initial content (last N items).
 * Fetches both in parallel for faster loading.
 *
 * We only load the last N items initially since sessions open at the bottom.
 * Items at the top will be lazy-loaded when the user scrolls up.
 */
async function loadSessionData(lastLine) {
    const sId = props.sessionId

    // Mark as fetched first (before async operations to avoid race conditions)
    if (!store.localState.sessions[sId]) {
        store.localState.sessions[sId] = {}
    }
    store.localState.sessions[sId].itemsFetched = true
    store.localState.sessions[sId].itemsLoading = true

    // Drop any orphaned ended streaming blocks (typical case: a Codex
    // canonical session whose live items_added landed while the user was
    // on another session, so _retireStreamingBlocks never ran and the
    // wire-only stream_uuid is gone by the time the REST fetch returns).
    store.clearEndedStreamingBlocks(sId)

    try {
        // Build range for initial content.
        // Parent sessions open at the bottom → load last N items.
        // Subagent sessions open at the top → load first N items.
        const ranges = []
        if (lastLine <= INITIAL_ITEMS_COUNT) {
            // Small session: load everything
            ranges.push([1, lastLine])
        } else if (props.parentSessionId) {
            // Subagent: load first N items (opens at the top)
            ranges.push([1, INITIAL_ITEMS_COUNT])
        } else {
            // Large session: load only last N items
            ranges.push([lastLine - INITIAL_ITEMS_COUNT + 1, lastLine])
        }

        // Build range params for items endpoint
        const params = new URLSearchParams()
        for (const [min, max] of ranges) {
            params.append('range', `${min}:${max}`)
        }

        // Fetch BOTH in parallel
        const [metadataResult, itemsResult] = await Promise.all([
            store.loadSessionMetadata(props.projectId, sId, props.parentSessionId),
            fetch(`${apiBaseUrl.value}/items/?${params}`)
                .then(res => res.ok ? res.json() : null)
                .catch(() => null)
        ])

        // Check for errors
        if (!metadataResult || !itemsResult) {
            store.localState.sessions[sId].itemsLoadingError = true
            return
        }

        // Process results
        store.initSessionItemsFromMetadata(sId, metadataResult)
        store.updateSessionItemsContent(sId, itemsResult)

        // Success
        store.localState.sessions[sId].itemsLoadingError = false

        // Session opening = audit point for sends whose outcome was lost
        // with a previous WebSocket/tab (send-failure recovery). This is the
        // REAL opening path — data.js's loadSessionItems is not called here.
        store.auditInflightSends(sId)

    } catch (error) {
        console.error('Failed to load session data:', error)
        store.localState.sessions[sId].itemsLoadingError = true
    } finally {
        store.localState.sessions[sId].itemsLoading = false
    }
}

// Load session data when session changes
watch([() => props.sessionId, session], async ([newSessionId, newSession], [oldSessionId] = []) => {
    if (!newSessionId) return
    const sessionChanged = newSessionId !== oldSessionId

    // If session is not in store and this is a subagent, load it first
    // (handles direct URL access before WebSocket delivers the session)
    if (!newSession && props.parentSessionId) {
        const loadedSession = await loadSubagentSession()
        if (!loadedSession) return
        // The watch will re-trigger with the loaded session
        return
    }

    if (!newSession) return

    // Don't load data for draft sessions (they have no items yet)
    if (newSession.draft) {
        return
    }

    // Don't load if computation is pending
    if (newSession.compute_version_up_to_date === false) {
        return
    }

    const lastLine = newSession.last_line
    if (!lastLine) return

    // Only initialize and load if not already done
    const isFirstLoad = !store.areSessionItemsFetched(newSessionId)

    if (isFirstLoad) {
        await loadSessionData(lastLine)

        // Fetch tool states first (needed by fetchSubagentsState to determine agent running status)
        await store.fetchToolStates(props.projectId, newSessionId)

        // For parent sessions, fetch all subagent states.
        // Populates the agent link cache (tool_use_id → agent_id) for View Agent buttons,
        // and creates synthetic process states for agents still running.
        if (!props.parentSessionId) {
            store.fetchSubagentsState(props.projectId, newSessionId)
            // Workflow tool-links (View Workflow buttons). Only sessions that
            // actually ran a local workflow carry them, so gate on has_workflows
            // to skip the derive scan everywhere else.
            if (store.getSession(newSessionId)?.has_workflows) {
                store.fetchWorkflowLinks(props.projectId, newSessionId)
            }
        }
    } else if (sessionChanged) {
        // Navigating (back) to an already-loaded session: verify its items
        // still cover everything the server has — see the same call in
        // onActivated. Gated on sessionChanged because this watch also fires
        // on every mutation of the session object (each session_updated),
        // where a coverage scan would race the in-flight items_added stream.
        store.ensureSessionItemsCoverage(newSessionId).catch(() => {})
    }

    // Skip DOM-manipulating scroll when inactive (KeepAlive deactivated)
    if (!sessionActive.value) return

    // Subagent tabs open at the top — skip scroll-to-bottom
    if (props.parentSessionId) return

    // A session-object replacement (same id, items already loaded — e.g. the
    // reconciliation's loadSessions after a WebSocket reconnect) is not an
    // opening: follow the bottom only when the user was already there, exactly
    // like live-arriving items. Only a real opening scrolls unconditionally.
    if (!isFirstLoad && !sessionChanged) {
        const scroller = scrollerRef.value
        if (!scroller) return
        if (!isAutoScrollingToBottom.value && !scroller.isAtBottom()) return
    }

    // Scroll to end of session (with retry until stable)
    // Mark as initial scroll to hide scroller until positioned (only on first load)
    // When returning to an already-loaded session, items are already sized so no resize events will fire
    await nextTick()

    // Check if the scroller container is visible (chat tab panel is active).
    // When navigating directly to a non-chat tab (e.g., /files), the chat panel
    // has display:none and scrollToBottom has no effect. In that case, defer the
    // scroll until the chat tab becomes visible (handled by onScrollerBecameVisible).
    const scroller = scrollerRef.value
    const scrollState = scroller?.getScrollState()
    if (scrollState && scrollState.clientHeight === 0) {
        pendingScrollToBottom = { isInitial: isFirstLoad }
        if (isFirstLoad) {
            isInitialScrolling.value = true
        }
    } else {
        pendingScrollToBottom = null
        scrollToBottomUntilStable({ isInitial: isFirstLoad })
    }
}, { immediate: true })

// Retry loading session data after error
async function handleRetry() {
    if (!session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    const sId = props.sessionId

    // Reset fetched state to allow reload
    if (store.localState.sessions[sId]) {
        store.localState.sessions[sId].itemsFetched = false
    }
    // Clear existing items
    delete store.sessionItems[sId]
    delete store.sessionVisualItems[sId]

    await loadSessionData(lastLine)

    // Subagent tabs open at the top — skip scroll-to-bottom
    if (props.parentSessionId) return

    // Scroll to bottom after successful load
    // Mark as initial scroll to hide scroller until positioned
    await nextTick()
    scrollToBottomUntilStable({ isInitial: true })
}

/**
 * Called when session becomes ready (compute completed).
 * Triggered by watching compute_version_up_to_date transition.
 */
async function onComputeCompleted() {
    if (!session.value) return

    const lastLine = session.value.last_line
    if (!lastLine) return

    await loadSessionData(lastLine)

    // Skip DOM-manipulating scroll when inactive (KeepAlive deactivated)
    if (!sessionActive.value) return

    // Subagent tabs open at the top — skip scroll-to-bottom
    if (props.parentSessionId) return

    // Mark as initial scroll to hide scroller until positioned
    await nextTick()
    scrollToBottomUntilStable({ isInitial: true })
}

// Watch for session compute completion
watch(() => session.value?.compute_version_up_to_date, (newValue, oldValue) => {
    // Transition from false (or undefined) to true
    if (newValue === true && oldValue !== true) {
        onComputeCompleted()
    }
})

watch(
    [() => props.sessionId, isComputePending],
    ([, pending]) => computePendingHint.update(pending),
    { immediate: true },
)

/**
 * Watch for new items being added to the session.
 * Auto-scrolls to bottom if user was near bottom (or already auto-scrolling).
 *
 * Uses a pre-flush watcher to capture "wasNearBottom" state BEFORE Vue updates the DOM,
 * then scrolls after the DOM update if needed.
 */
watch(
    () => visualItems.value?.length,
    async (newLength, oldLength) => {
        // Only handle additions (not initial load or removals)
        if (!newLength || !oldLength || newLength <= oldLength) return

        // Skip DOM-manipulating scroll when inactive (KeepAlive deactivated).
        // The reactivation handler will check if items were added and scroll if needed.
        if (!sessionActive.value) return

        const scroller = scrollerRef.value
        if (!scroller) return

        // Check if we should auto-scroll:
        // 1. We're currently in the middle of an auto-scroll operation, OR
        // 2. User was near the bottom before the new items arrived
        const shouldAutoScroll = isAutoScrollingToBottom.value || scroller.isAtBottom()

        if (shouldAutoScroll) {
            // Wait for Vue to render the new items
            await nextTick()
            scrollToBottomUntilStable()
        }
    }
)

/**
 * Preserve the scroll position across the streaming-to-real item swap.
 *
 * When a streamed block is retired (_retireStreamingBlocks), the synthetic item
 * (negative lineNum) is replaced by the real JSONL item (positive lineNum). The
 * scroller does not know the real item's height for a few frames -- and the
 * real item then re-renders its markdown asynchronously -- so scrollHeight
 * collapses and the browser clamps scrollTop, dumping a reader of the streamed
 * text at the bottom, where the at-bottom machinery then pins them. The content
 * is identical, so the position is fully recoverable:
 *
 *   1. capture scrollTop synchronously BEFORE the swap ($onAction runs before
 *      the action body);
 *   2. wait until the position is REACHABLE again -- scrollHeight recovered
 *      enough that the write would not be clamped. A height-stability debounce
 *      is the wrong signal here: it can fire while the item still renders
 *      empty, and never fire while another block keeps streaming;
 *   3. write it back, then hold it for a few frames: the clamp may have flipped
 *      the at-bottom flag on, and until the sentinel observer reports back, any
 *      DOM growth natively re-pins the view to the bottom.
 *
 * Any manual scroll gesture aborts the restore: the user's move is a newer
 * intent than the saved position.
 *
 * The restore alone proved insufficient: within the clamped window, unrelated
 * watchers (session_updated, new items) read the phantom at-bottom state and
 * legitimately scroll to the bottom on their own. The height floor applied in
 * the after() hook is therefore the primary defense -- it prevents the dip
 * (and thus the phantom state) from ever existing; the capture/restore below
 * remains as a safety net.
 */
let streamSwapSavedScrollTop = null  // pre-swap scrollTop while a restore is in flight
let streamSwapSeq = 0                // only the latest swap performs the restore

// Manual-scroll gestures that abort a pending restore.
const STREAM_SWAP_USER_EVENTS = ['wheel', 'touchstart', 'mousedown', 'keydown']

// Upper bound on the reachability wait. Hit only when the final layout ends up
// genuinely shorter than the streamed one (e.g. a streamed-open thinking block
// re-rendering collapsed): restoring would land somewhere wrong -- give up.
const STREAM_SWAP_RESTORE_MAX_MS = 3000

// How many frames the restore re-asserts the position (see point 3 above).
const STREAM_SWAP_HOLD_FRAMES = 8

// Height floor bridging the swap: the real item inherits the streamed item's
// measured height (scroller cache seed + CSS min-height on the element) so
// scrollHeight never dips while its markdown re-renders. Cleared after a
// delay: kept forever it would block legitimate shrinks (e.g. the user
// collapsing a code block inside the item).
const streamSwapHeightFloors = reactive(new Map())  // realLineNum -> px
const STREAM_SWAP_FLOOR_MS = 3000
const streamSwapItemMinHeight = (item) => streamSwapHeightFloors.get(item.lineNum) ?? null

// Floors are keyed by lineNum within one session only.
watch(() => props.sessionId, () => streamSwapHeightFloors.clear())

store.$onAction(({ name, args, after }) => {
    if (name !== '_retireStreamingBlocks') return
    if (args[0] !== props.sessionId) return
    // Without streaming state the action is a no-op -- nothing will move.
    if (!store.localState.streamingBlocks[props.sessionId]) return
    if (!sessionActive.value) return
    const scroller = scrollerRef.value
    if (!scroller) return
    const state = scroller.getScrollState()
    if (!state || state.clientHeight === 0) return
    // Near the bottom, the follow-the-conversation logic owns the scroll.
    if (scroller.isAtBottom()) return

    const seq = ++streamSwapSeq
    // A restore already in flight keeps its saved value: the current scrollTop
    // may already be clamped by a previous swap of the same turn.
    if (streamSwapSavedScrollTop === null) streamSwapSavedScrollTop = state.scrollTop
    const saved = streamSwapSavedScrollTop

    after(async (retiredPairs) => {
        // Primary defense, synchronous (before the caller's recomputeVisualItems
        // renders the swap): carry the streamed item's measured height onto the
        // real item, in the scroller's height cache (positions/spacers) AND as
        // a DOM min-height floor on the mounted element. With both in place,
        // scrollHeight never dips, the browser never clamps, and the at-bottom
        // machinery never mistakes the reader for being at the bottom.
        if (Array.isArray(retiredPairs)) {
            for (const { streamingLineNum, realLineNum } of retiredPairs) {
                const h = scroller.getItemHeight(streamingLineNum)
                if (!h || h <= MIN_ITEM_SIZE) continue
                scroller.seedItemHeight(realLineNum, h)
                streamSwapHeightFloors.set(realLineNum, h)
                setTimeout(() => streamSwapHeightFloors.delete(realLineNum), STREAM_SWAP_FLOOR_MS)
            }
        }

        const container = scroller.$el
        let userTookOver = false
        const markUserScroll = () => { userTookOver = true }
        for (const ev of STREAM_SWAP_USER_EVENTS) {
            container?.addEventListener(ev, markUserScroll, { passive: true })
        }
        const nextFrame = () => new Promise(requestAnimationFrame)
        try {
            // The caller (addSessionItems) runs recomputeVisualItems right after
            // this action returns; nextTick lets the DOM reflect the swap first.
            await nextTick()

            // Phase 1 -- wait until the saved position is reachable again.
            const deadline = performance.now() + STREAM_SWAP_RESTORE_MAX_MS
            while (true) {
                if (seq !== streamSwapSeq) return  // superseded: the newer swap restores
                if (userTookOver) {
                    streamSwapSavedScrollTop = null
                    return
                }
                const st = scroller.getScrollState()
                if (st.clientHeight > 0 && st.scrollHeight - st.clientHeight >= saved - 0.5) break
                if (performance.now() >= deadline) {
                    streamSwapSavedScrollTop = null
                    return
                }
                await nextFrame()
            }

            // Phase 2 -- write, then keep re-asserting for a few frames.
            // setScrollTop is a no-op when the position is already in place, so
            // the quiet frames cost nothing.
            for (let frame = 0; frame < STREAM_SWAP_HOLD_FRAMES; frame++) {
                if (seq !== streamSwapSeq) return
                if (userTookOver) break
                scroller.setScrollTop(saved)
                await nextFrame()
            }
            streamSwapSavedScrollTop = null
        } finally {
            for (const ev of STREAM_SWAP_USER_EVENTS) {
                container?.removeEventListener(ev, markUserScroll)
            }
        }
    })
})

/**
 * Resolve the pending initial-scroll stability wait, if any, clearing both its debounce
 * and its absolute max-wait timers. Idempotent — safe to call from whichever timer fires first.
 */
function resolveStability() {
    if (stabilityTimeoutId) {
        clearTimeout(stabilityTimeoutId)
        stabilityTimeoutId = null
    }
    if (stabilityMaxWaitId) {
        clearTimeout(stabilityMaxWaitId)
        stabilityMaxWaitId = null
    }
    if (onStabilizedCallback) {
        const callback = onStabilizedCallback
        onStabilizedCallback = null
        callback()
    }
}

/**
 * Handle item resize events from VirtualScroller.
 * Used to detect when items have finished resizing for scroll stability detection.
 */
function onItemResized() {
    // If we're waiting for stability, reset the debounce timer
    if (onStabilizedCallback) {
        // Clear existing timeout
        if (stabilityTimeoutId) {
            clearTimeout(stabilityTimeoutId)
        }

        // Set new timeout - if no more resizes happen within STABILITY_DEBOUNCE_MS,
        // we consider it stable
        stabilityTimeoutId = setTimeout(() => {
            resolveStability()
        }, STABILITY_DEBOUNCE_MS)
    }
}

/**
 * Scroll to bottom and wait until the scroll position stabilizes.
 *
 * Thin wrapper kept for the many automatic call sites (session open, live
 * items, KeepAlive resume, …) — see scrollToEdgeUntilStable.
 *
 * @param {Object} [options] - Options for the scroll operation
 * @param {boolean} [options.isInitial=false] - Whether this is the initial scroll after session load.
 */
function scrollToBottomUntilStable(options = {}) {
    return scrollToEdgeUntilStable('bottom', options)
}

/**
 * Scroll to one end of the transcript and wait until the position stabilizes.
 *
 * The first scroll brings the target edge into view, after which native browser
 * scroll anchoring takes over and holds it as items continue to resize
 * (CodeMirror, mermaid, etc. rendering async). We still wait for stability
 * before revealing the scroller on initial load so the user doesn't see a
 * visible jump from the first scroll position to the final settled position.
 *
 * @param {'top' | 'bottom'} edge - Which end to scroll to.
 * @param {Object} [options] - Options for the scroll operation
 * @param {boolean} [options.isInitial=false] - Whether this is the initial scroll after session load.
 *   When true, the scroller is kept invisible until scroll is stable to prevent visual jumping.
 *   Only ever set for the bottom edge.
 */
async function scrollToEdgeUntilStable(edge, options = {}) {
    const { isInitial = false } = options
    const scroller = scrollerRef.value
    if (!scroller) return

    // If a scroll operation is already in progress, wait for it to complete
    // This prevents concurrent calls from interfering with each other
    if (edgeScrollOperation) {
        const previousEdge = edgeScrollOperation.edge
        await edgeScrollOperation.promise
        // After waiting, the previous operation already reached this same edge,
        // so we can return early unless this is an initial scroll that needs
        // visibility handling. A different edge is a new intent — carry on.
        if (previousEdge === edge && !isInitial) return
    }

    const jump = edge === 'top' ? scroller.scrollToTop : scroller.scrollToBottom

    // Create a new promise for this operation and store it
    let resolveScrollPromise
    edgeScrollOperation = {
        edge,
        promise: new Promise(resolve => {
            resolveScrollPromise = resolve
        }),
    }

    try {
        // Only the bottom edge feeds the "follow the conversation" logic; a
        // scroll to the top must not make live items pull the view back down.
        if (edge === 'bottom') {
            isAutoScrollingToBottom.value = true
        }

        // For initial scroll, hide the scroller until we're positioned
        if (isInitial) {
            isInitialScrolling.value = true
        }

        // Scroll to the edge: at the bottom this brings the anchor sentinel into
        // view so native scroll anchoring engages for any subsequent height growth.
        jump({ behavior: 'auto' })

        // Wait for stability: no more resize events for STABILITY_DEBOUNCE_MS,
        // OR an absolute MAX_STABILITY_WAIT_MS ceiling (whichever comes first).
        await new Promise(resolve => {
            onStabilizedCallback = resolve

            // Absolute upper bound on the wait. The debounce below is reset on every
            // resize; if resizes never settle (e.g. a sub-pixel ResizeObserver loop),
            // it would never fire and the scroller would stay visibility:hidden forever.
            // This timer is independent — never reset by resizes — so the reveal always happens.
            stabilityMaxWaitId = setTimeout(() => {
                resolveStability()
            }, MAX_STABILITY_WAIT_MS)

            // IMPORTANT: Don't start the timer immediately!
            // We need to wait for Vue to render and ResizeObserver to fire.
            // Use requestAnimationFrame + setTimeout to ensure we start AFTER
            // the initial batch of resize events has a chance to arrive.
            requestAnimationFrame(() => {
                setTimeout(() => {
                    // Start the stability timer only after giving resize events a chance to fire
                    // If no resize events have arrived by now and reset this timer,
                    // we're already stable
                    if (!stabilityTimeoutId) {
                        stabilityTimeoutId = setTimeout(() => {
                            resolveStability()
                        }, STABILITY_DEBOUNCE_MS)
                    }
                }, 0)
            })
        })

        // Final scroll to ensure we're at the very edge
        jump({ behavior: 'auto' })

        isAutoScrollingToBottom.value = false

        // Reveal the scroller now that we're positioned at the bottom. Clear UNCONDITIONALLY:
        // isInitialScrolling may have been set by the load watcher (first load while the chat tab
        // was hidden) while this call runs with isInitial=false — a later watcher pass can overwrite
        // pendingScrollToBottom to { isInitial: false } without resetting the flag. Gating the reveal
        // on isInitial would then leave the scroller visibility:hidden forever (Firefox blank-chat bug).
        isInitialScrolling.value = false
    } finally {
        // Clear the operation and resolve it so any waiters can proceed
        edgeScrollOperation = null
        resolveScrollPromise()
    }
}

/**
 * Convert an array of line numbers to ranges for API calls.
 * e.g., [1, 2, 3, 5, 6, 10] -> [[1, 3], [5, 6], [10, 10]]
 */
function lineNumsToRanges(lineNums) {
    if (lineNums.length === 0) return []

    const sorted = [...lineNums].sort((a, b) => a - b)
    const ranges = []
    let rangeStart = sorted[0]
    let rangeEnd = sorted[0]

    for (let i = 1; i < sorted.length; i++) {
        if (sorted[i] === rangeEnd + 1) {
            rangeEnd = sorted[i]
        } else {
            ranges.push([rangeStart, rangeEnd])
            rangeStart = sorted[i]
            rangeEnd = sorted[i]
        }
    }

    ranges.push([rangeStart, rangeEnd])
    return ranges
}

/**
 * Execute the pending load - called after debounce.
 * Loads specific line numbers instead of ranges of indices.
 */
async function executePendingLoad() {
    const range = pendingLoadRange.value
    if (!range || !range.lineNums || range.lineNums.length === 0) return

    pendingLoadRange.value = null

    const ranges = lineNumsToRanges(range.lineNums)

    if (ranges.length > 0) {
        const scroller = scrollerRef.value
        const wasAtBottom = scroller?.isAtBottom?.() ?? false

        await store.loadSessionItemsRanges(
            props.projectId,
            props.sessionId,
            ranges,
            props.parentSessionId
        )

        if (scroller && wasAtBottom) {
            const state = scroller.getScrollState()
            const distanceFromBottom = state.scrollHeight - state.scrollTop - state.clientHeight
            if (distanceFromBottom > 5) {
                await nextTick()
                scrollToBottomUntilStable()
            }
        }
    }
}

const debouncedLoad = useDebounceFn(executePendingLoad, LOAD_DEBOUNCE_MS)

/**
 * Handle scroller update event - triggers lazy loading for visible items.
 * Works with visualItems (filtered list) and maps to actual line numbers.
 *
 * @param {{ startIndex: number, endIndex: number, visibleStartIndex: number, visibleEndIndex: number }} payload
 *   - startIndex/endIndex: indices of items being rendered (with buffer)
 *   - visibleStartIndex/visibleEndIndex: indices of items actually visible (no buffer)
 */
function onScrollerUpdate({ startIndex, endIndex, visibleStartIndex, visibleEndIndex }) {
    const visItems = visualItems.value
    if (!visItems || visItems.length === 0) return

    // Add buffer around visible range
    const bufferedStart = Math.max(0, visibleStartIndex - LOAD_BUFFER)
    const bufferedEnd = Math.min(visItems.length - 1, visibleEndIndex + LOAD_BUFFER)

    // Collect line numbers that need content loading
    const lineNumsToLoad = []
    for (let i = bufferedStart; i <= bufferedEnd; i++) {
        const visualItem = visItems[i]
        // Day separators carry no content and a non-numeric key — never queue them.
        if (visualItem && !visualItem.isDaySeparator && !hasContent(visualItem)) {
            lineNumsToLoad.push(visualItem.lineNum)
        }
    }

    if (lineNumsToLoad.length > 0) {
        pendingLoadRange.value = { lineNums: lineNumsToLoad }
        debouncedLoad()
    }
}

/**
 * Toggle a group's expanded state.
 * Called when clicking on a GroupToggle component.
 */
function toggleGroup(groupHeadLineNum) {
    store.toggleExpandedGroup(props.sessionId, groupHeadLineNum)
}

/** Set of tool line numbers that have code comments (for GroupToggle indicators). */
const commentedToolLineNums = computed(() => {
    // Comments are stored with the root session ID, not the subagent's.
    const rootSessionId = props.parentSessionId || props.sessionId
    if (codeCommentsStore.countBySession(props.projectId, rootSessionId) === 0) return null
    if (codeCommentsStore.countBySource(props.projectId, rootSessionId, 'tool') === 0) return null

    const comments = codeCommentsStore.getCommentsBySession(props.projectId, rootSessionId)
        .filter(c => c.source === 'tool')

    const lineNums = new Set()
    if (props.parentSessionId) {
        // Inside a subagent: only this subagent's toolLineNums
        for (const c of comments) {
            if (c.subagentSessionId === props.sessionId && c.toolLineNum != null) {
                lineNums.add(c.toolLineNum)
            }
        }
    } else {
        // Main session: direct tool comments + subagent comments via subagentToolLineNum
        for (const c of comments) {
            if (!c.subagentSessionId && c.toolLineNum != null) {
                // Direct tool in main session
                lineNums.add(c.toolLineNum)
            } else if (c.subagentSessionId && c.subagentToolLineNum != null) {
                // Subagent tool — use the parent session's Agent/Task tool_use line number
                lineNums.add(c.subagentToolLineNum)
            }
        }
    }
    return lineNums.size > 0 ? lineNums : null
})

/**
 * Set of conversation-mode block IDs (user_message lineNums) that contain
 * tool comments. Pre-computed by scanning visual items to determine block
 * boundaries and checking if any commentedToolLineNum falls within each block.
 */
const blocksWithComments = computed(() => {
    const toolLineNums = commentedToolLineNums.value
    if (!toolLineNums) return null

    const items = visualItems.value
    if (!items || items.length === 0) return null

    // Collect block boundaries: each block starts after a user_message
    // and ends before the next user_message (or end of items).
    const blocks = []  // [{ blockId, startLineNum, endLineNum }]
    let currentBlockId = null
    let blockStartLineNum = null

    for (const item of items) {
        if (item.detailToggleFor != null) {
            // This item has a toggle → it belongs to a block
            if (currentBlockId !== null && currentBlockId !== item.detailToggleFor) {
                // Close previous block
                blocks.push({ blockId: currentBlockId, start: blockStartLineNum })
            }
            currentBlockId = item.detailToggleFor
            blockStartLineNum = item.lineNum
        }
    }

    // If no blocks have toggles, nothing to check
    if (blocks.length === 0 && currentBlockId === null) return null

    // Check which blocks have comments (using a simple approach:
    // for each commented line num, check if it's > blockId for any block)
    const result = new Set()
    for (const ln of toolLineNums) {
        // A tool at lineNum ln belongs to the block whose blockId (user_message lineNum)
        // is the largest blockId that is less than ln
        let bestBlockId = null
        for (const item of items) {
            if (item.kind === 'user_message' && item.lineNum < ln) {
                bestBlockId = item.lineNum
            }
        }
        if (bestBlockId !== null) result.add(bestBlockId)
    }
    return result.size > 0 ? result : null
})

/** Count comments in a conversation-mode block (1 if has comments, 0 otherwise). */
function blockCommentsCount(blockId) {
    return blocksWithComments.value?.has(blockId) ? 1 : 0
}

/** Count tool comments that fall within a group's line number range. */
function groupCommentsCount(groupHeadLineNum, groupTailLineNum) {
    const lineNums = commentedToolLineNums.value
    if (!lineNums) return 0
    const tail = groupTailLineNum ?? groupHeadLineNum
    let count = 0
    for (const ln of lineNums) {
        if (ln >= groupHeadLineNum && ln <= tail) count++
    }
    return count
}

/**
 * Get the scroller element for scroll compensation.
 * @returns {HTMLElement|null}
 */
function getScrollerElement() {
    return scrollerRef.value?.$el ?? null
}

// =============================================================================
// Drag and Drop Handlers
// =============================================================================

/**
 * Handle dragenter event.
 * Uses a counter to properly handle nested elements.
 */
function onDragEnter(event) {
    const types = event.dataTransfer?.types
    // Only handle file or text drops (not internal browser drags like link/bookmark)
    const hasFiles = types?.includes('Files')
    const hasText = types?.includes('text/plain')
    if (!hasFiles && !hasText) return
    event.preventDefault()
    dragCounter++
    if (dragCounter === 1) {
        // Files take precedence (a file drop may also carry text/plain)
        dragOverType.value = hasFiles ? 'files' : 'text'
        // Listen for drag cancellation (Escape key, drop outside window, etc.)
        // dragend fires on the source element when the drag ends without a successful drop.
        document.addEventListener('dragend', onDragEnd, true)
    }
}

/**
 * Handle dragend event (fires when drag is cancelled, e.g. by Escape).
 * This is the only reliable way to detect drag cancellation, since the browser
 * consumes the first Escape keypress to cancel the native drag before it
 * reaches keydown listeners.
 */
function onDragEnd() {
    dragCounter = 0
    dragOverType.value = null
    document.removeEventListener('dragend', onDragEnd, true)
}

/**
 * Handle dragleave event.
 * Uses a counter to properly handle nested elements.
 */
function onDragLeave(event) {
    const types = event.dataTransfer?.types
    if (!types?.includes('Files') && !types?.includes('text/plain')) return
    event.preventDefault()
    dragCounter--
    if (dragCounter === 0) {
        dragOverType.value = null
        document.removeEventListener('dragend', onDragEnd, true)
    }
}

/**
 * Handle dragover event - required to allow drop.
 */
function onDragOver(event) {
    const types = event.dataTransfer?.types
    if (!types?.includes('Files') && !types?.includes('text/plain')) return
    event.preventDefault()
}

/**
 * Handle drop event - process dropped files or insert dropped text.
 */
async function onDrop(event) {
    event.preventDefault()
    dragCounter = 0
    dragOverType.value = null
    document.removeEventListener('dragend', onDragEnd, true)

    const dataTransfer = event.dataTransfer
    const hasFiles = dataTransfer?.types?.includes('Files')

    if (hasFiles) {
        const files = dataTransfer.files
        if (!files || files.length === 0) return

        // Process each file
        for (const file of files) {
            await processDroppedFile(file)
        }
    } else {
        // Dropped text — insert into the message textarea
        const text = dataTransfer?.getData('text/plain')
        if (text && messageInputRef.value) {
            messageInputRef.value.insertTextAtCursor(text)
        }
    }
}

/**
 * Process a single dropped file. Validation against the active provider's
 * attachment capabilities (MIME, max bytes) happens inside
 * ``store.addAttachment``; any failure surfaces here as a thrown ``Error``
 * with a user-friendly message that we toast.
 */
async function processDroppedFile(file) {
    try {
        await store.addAttachment(props.sessionId, file)
    } catch (error) {
        toast.error(error.message || 'Failed to process file', {
            title: 'Cannot attach file'
        })
    }
}

// =============================================================================
// In-session search (Ctrl+F)
// =============================================================================

// Search highlight terms provided to child components (MarkdownContent uses them)
const searchHighlightTerms = ref([])
provide('searchHighlightTerms', searchHighlightTerms)

// File-link resolution for MarkdownContent rendered inside the session items
// list. Classification reads the current session/project roots lazily so that
// late-arriving sync data (cwd, git_directory) is picked up on the next render.
const fileLinksRouter = useRouter()
const openFileInFilesTab = inject('viewFileInFilesTab', null)

/**
 * Probe candidate absolute paths via the lightweight `meta_only` endpoint and
 * return the first (in order) that exists on disk, or null if none do.
 */
async function firstExistingPath(paths) {
    const checks = await Promise.all(paths.map(async (p) => {
        try {
            const res = await apiFetch(`/api/file-content/?path=${encodeURIComponent(p)}&meta_only=true`)
            return res.ok
        } catch {
            return false
        }
    }))
    const idx = checks.findIndex(Boolean)
    return idx === -1 ? null : paths[idx]
}

/**
 * Open a relative markdown file link that may belong to several nested roots:
 * pick the first candidate that exists, then reveal it. A single candidate
 * (absolute paths, artifact refs, or a single-root project) skips the probe and
 * opens directly — same cost as before. Falls back to the first candidate when
 * none exist, preserving the prior "no longer available in this root" surface.
 */
async function openMarkdownFileLink(candidates, opts) {
    if (!openFileInFilesTab || !candidates?.length) return
    const target = candidates.length > 1
        ? ((await firstExistingPath(candidates)) ?? candidates[0])
        : candidates[0]
    // Markdown links are the one caller that opts into the Plan-tab redirect
    // for tracked plan documents; explicit Files-tab buttons stay literal.
    openFileInFilesTab(target, { ...opts, preferPlanTab: true })
}

provide('markdownFileLinks', {
    // artifactsDir lets classifyHref route the session's artifact paths
    // (absolute, or relative "artifacts/<session_id>/…") to the Artifacts tab
    // via viewFileInFilesTab. Only set once the session has artifacts.
    classifyHref: (href) => classifyHref(href, {
        router: fileLinksRouter,
        roots: fileRootsFromStore(project.value, session.value, store),
        artifactsDir: session.value?.artifacts_dir || null,
    }),
    openFile: openMarkdownFileLink,
})

// Provide a function for child components (e.g., ToolUseContent) to request
// scroll-to-bottom when they are about to expand (auto-open diffs, etc.).
// Native scroll anchoring on the sentinel keeps us pinned during the expansion
// animation; the explicit scroll-to-bottom call brings us back to the sentinel
// in case rendering jitter pushed it slightly out of view.
provide('requestScrollToBottomIfNeeded', () => {
    if (props.parentSessionId) return // Subagent sessions don't auto-scroll
    const scroller = scrollerRef.value
    if (!scroller) return
    if (isAutoScrollingToBottom.value || scroller.isAtBottom()) {
        scrollToBottomUntilStable()
    }
})

function handleSearchTerms(terms) {
    searchHighlightTerms.value = terms
}

/**
 * Check whether the current selection lives inside the session items area
 * (the virtual scroller) and outside any CodeMirror editor — used to decide
 * whether to prefill the in-session search bar with the selected text.
 */
function isSelectionInSessionContent(selection) {
    const anchor = selection?.anchorNode
    if (!anchor) return false
    const scrollerEl = scrollerRef.value?.$el
    if (!scrollerEl?.contains(anchor)) return false
    if (anchor.closest?.('.cm-editor') || anchor.parentElement?.closest('.cm-editor')) return false
    return true
}

/**
 * Toggle the in-session search bar.
 * Only responds when this is a main session (not subagent) and is currently active.
 *
 * When opening: sets e.detail.handled = true so App.vue blocks the native browser Find.
 * When closing: leaves handled = false so the native Ctrl+F passes through to the browser.
 */
function handleToggleSessionSearch(e) {
    // Only respond for the main chat tab (not subagent views)
    if (props.parentSessionId) return
    // Only respond when this session is active (KeepAlive)
    if (!sessionActive.value) return

    if (showSessionSearch.value) {
        closeSessionSearch()
        // Don't set handled — let the browser open its native Find bar
    } else {
        // Capture selection before opening the bar (focus would clear it).
        // Only prefill when the selection lives inside the session items area.
        const selection = window.getSelection()
        let prefill = ''
        if (selection && isSelectionInSessionContent(selection)) {
            prefill = selection.toString().replace(/\s+/g, ' ').trim()
        }

        showSessionSearch.value = true
        nextTick(() => {
            if (prefill) {
                sessionSearchRef.value?.openWithQuery(prefill)
            } else {
                sessionSearchRef.value?.open()
            }
        })
        e.detail.handled = true
    }
}

function closeSessionSearch() {
    showSessionSearch.value = false
    sessionSearchRef.value?.reset()
    searchHighlightTerms.value = []
}

/**
 * Handle global keyboard shortcuts for in-session search.
 * When the search bar is visible:
 * - F3 / Shift+F3: navigate to next/previous match
 * - Escape: close the search bar
 * These work regardless of where focus is within the session.
 */
function handleSessionSearchKeydown(e) {
    if (!showSessionSearch.value) return
    if (props.parentSessionId) return
    if (!sessionActive.value) return

    if (e.key === 'F3') {
        e.preventDefault()
        if (e.shiftKey) {
            sessionSearchRef.value?.goToPrevious()
        } else {
            sessionSearchRef.value?.goToNext()
        }
    } else if (e.key === 'Escape') {
        e.preventDefault()
        closeSessionSearch()
    }
}

onMounted(() => {
    window.addEventListener('twicc:toggle-session-search', handleToggleSessionSearch)
    window.addEventListener('keydown', handleSessionSearchKeydown)
    window.addEventListener('twicc:goto-message-input', gotoMessageInput)
    window.addEventListener('twicc:goto-pending-request', gotoPendingRequest)
    window.addEventListener('twicc:goto-terminal', gotoTerminal)
    window.addEventListener('twicc:toggle-terminal', toggleTerminal)
    window.addEventListener('twicc:toggle-hybrid', toggleHybrid)
})
onBeforeUnmount(() => {
    computePendingHint.dispose()
    window.removeEventListener('twicc:toggle-session-search', handleToggleSessionSearch)
    window.removeEventListener('keydown', handleSessionSearchKeydown)
    window.removeEventListener('twicc:goto-message-input', gotoMessageInput)
    window.removeEventListener('twicc:goto-pending-request', gotoPendingRequest)
    window.removeEventListener('twicc:goto-terminal', gotoTerminal)
    window.removeEventListener('twicc:toggle-terminal', toggleTerminal)
    window.removeEventListener('twicc:toggle-hybrid', toggleHybrid)
})

// Watch for pending search from the global SearchOverlay.
// When the user clicks a session result in the overlay, the query is stored in
// pendingSessionSearch. This watcher picks it up once the target session's
// SessionItemsList is active and opens the in-session search bar with that query.
watch(pendingSessionSearch, (pending) => {
    if (!pending) return
    if (pending.sessionId !== props.sessionId) return
    if (props.parentSessionId) return  // Only main session, not subagent
    if (!sessionActive.value) return

    // Consume the pending search
    const q = pending.query
    pendingSessionSearch.value = null

    // Open the search bar with the query
    showSessionSearch.value = true
    nextTick(() => {
        sessionSearchRef.value?.openWithQuery(q)
    })
}, { immediate: true })

// =============================================================================
// Scroll to line number (generic, used by search navigation and future features)
// =============================================================================

// Counter to detect stale scroll operations (when user clicks next/prev rapidly)
let scrollToLineNumGeneration = 0

/**
 * Scroll the virtual scroller to make the item at the given lineNum visible.
 *
 * Handles:
 * - Conversation mode: expands the block if the item is hidden (non-last assistant message)
 * - Content loading: ensures the target item's content is loaded before scrolling
 * - Pre-loading: fetches a buffer of items around the target to reduce placeholder flicker
 * - Jump-settle-correct: delegates to VirtualScroller.scrollToKey for stable positioning
 *
 * @param {number|string} lineNum - The key of the item to scroll to (a line number, or a
 *   day separator's synthetic key)
 * @param {Object} [options]
 * @param {'start' | 'center' | 'end'} [options.align='center'] - Where to leave the item
 * @param {number} [options.offset=0] - Pixels of room to leave before the item
 * @param {boolean} [options.highlight=true] - Refine the position onto the item's first
 *   search highlight. Only wanted when the search bar drives the scroll.
 * @returns {Promise<boolean>} true if the item was successfully scrolled into view
 */
async function scrollToLineNum(lineNum, options = {}) {
    const { align = 'center', offset = 0, highlight = true } = options
    const generation = ++scrollToLineNumGeneration
    const scroller = scrollerRef.value
    if (!scroller) return false

    const visItems = visualItems.value

    // Step 1: Check if the item is already in the visual items list
    let found = visItems.some(vi => vi.lineNum === lineNum)

    // Step 2: If not found and in conversation mode, expand the block
    // (effective mode, so a per-session debug override is accounted for)
    if (!found && store.getEffectiveDisplayMode(props.sessionId) === DISPLAY_MODE.CONVERSATION) {
        const rawItems = items.value
        const rawItem = rawItems.find(ri => ri.line_num === lineNum)

        if (rawItem) {
            // Find the blockId: walk backwards from this item to find the last user_message
            let blockId = null
            for (let i = rawItems.indexOf(rawItem) - 1; i >= 0; i--) {
                if (rawItems[i].kind === 'user_message') {
                    blockId = rawItems[i].line_num
                    break
                }
            }

            if (blockId !== null) {
                store.ensureBlockDetailed(props.sessionId, blockId)
                await nextTick()
                found = visualItems.value.some(vi => vi.lineNum === lineNum)
            }
        }
    }

    if (!found) return false
    if (generation !== scrollToLineNumGeneration) return false  // Stale

    // Step 3: Ensure the target item's content is loaded (plus a buffer around it)
    const visItems2 = visualItems.value
    const targetIndex = visItems2.findIndex(vi => vi.lineNum === lineNum)
    if (targetIndex === -1) return false

    // Collect lineNums that need loading in a buffer around the target
    const bufferSize = LOAD_BUFFER
    const startIdx = Math.max(0, targetIndex - bufferSize)
    const endIdx = Math.min(visItems2.length - 1, targetIndex + bufferSize)
    const lineNumsToLoad = []

    for (let i = startIdx; i <= endIdx; i++) {
        if (!visItems2[i].isDaySeparator && !hasContent(visItems2[i])) {
            lineNumsToLoad.push(visItems2[i].lineNum)
        }
    }

    if (lineNumsToLoad.length > 0) {
        const ranges = lineNumsToRanges(lineNumsToLoad)
        await store.loadSessionItemsRanges(
            props.projectId,
            props.sessionId,
            ranges,
            props.parentSessionId
        )
        if (generation !== scrollToLineNumGeneration) return false  // Stale
        await nextTick()
    }

    // Step 4: Scroll to the item via the virtual scroller's jump-settle-correct
    const visible = await scroller.scrollToKey(lineNum, { align, offset })
    if (!visible) return false
    if (generation !== scrollToLineNumGeneration) return false  // Stale

    // Step 5: If the item is tall, scroll to the first search highlight within it
    if (highlight) {
        await nextTick()  // Let v-highlight directive apply marks
        scrollToFirstHighlight(lineNum)
    }

    return true
}

/**
 * Scroll the virtual scroller container so the first <mark class="search-highlight">
 * inside the given item is visible. Useful when an item is taller than the viewport
 * and the highlight is out of view after the initial scroll-to-item.
 */
function scrollToFirstHighlight(lineNum) {
    const scrollerEl = scrollerRef.value?.$el
    if (!scrollerEl) return

    const itemEl = scrollerEl.querySelector(`.session-item[data-line-num="${lineNum}"]`)
    if (!itemEl) return

    const mark = itemEl.querySelector('mark.search-highlight')
    if (!mark) return

    // Check if the mark is already visible in the scroller viewport
    const scrollerRect = scrollerEl.getBoundingClientRect()
    const markRect = mark.getBoundingClientRect()
    const isMarkVisible = markRect.top >= scrollerRect.top && markRect.bottom <= scrollerRect.bottom
    if (isMarkVisible) return

    // Scroll the mark into view within the scroller container
    mark.scrollIntoView({ block: 'center', behavior: 'instant' })
}

/**
 * Handle navigate event from the search bar.
 */
function handleSearchNavigate(lineNum) {
    scrollToLineNum(lineNum)
}

// =============================================================================
// Chat navigation toolbar (extremes + block by block)
// =============================================================================

// The scrolling element itself, so the toolbar pinned over it can forward wheel
// events instead of swallowing them.
const scrollerElement = computed(() => scrollerRef.value?.$el ?? null)

const {
    hasNavigation: navHasNavigation,
    canGoTop: navCanGoTop,
    canGoPrev: navCanGoPrev,
    canGoNext: navCanGoNext,
    canGoBottom: navCanGoBottom,
    goTop: navGoTop,
    goPrevBlock: navGoPrevBlock,
    goNextBlock: navGoNextBlock,
    goBottom: navGoBottom,
} = useChatNavigation({
    scrollerRef,
    visualItems,
    // `align: 'start'` pins the block to the top of the viewport, so the reader
    // gets the whole screen to read it. scrollToIndex clamps to the maximum
    // scroll, so the last block simply lands as high as it can go.
    scrollToItem: (lineNum, offset) =>
        scrollToLineNum(lineNum, { align: 'start', highlight: false, offset }),
    scrollToEdge: (edge) => scrollToEdgeUntilStable(edge),
})

// Expose methods for parent components
/**
 * Process externally forwarded drop data (from drag-hover on tabs or session list).
 * Accepts pre-extracted files and text since dataTransfer is only available
 * synchronously in the original drop event handler.
 * @param {{ files: File[], text: string|null }} data
 */
async function handleForwardedDrop({ files, text }) {
    if (files && files.length > 0) {
        for (const file of files) {
            await processDroppedFile(file)
        }
    } else if (text) {
        if (messageInputRef.value) {
            messageInputRef.value.insertTextAtCursor(text)
        }
    }
}

// =============================================================================
// Text selection comment (ephemeral annotation on selected session text)
// =============================================================================

/** True when `el` holds both ends of the selection, not just its start. */
function holdsWholeSelection(el, selection) {
    if (!selection?.rangeCount) return false
    const range = selection.getRangeAt(0)
    return el.contains(range.startContainer) && el.contains(range.endContainer)
}

/**
 * Decide how a conversation selection should be quoted. The chat mixes prose
 * with two kinds of code view, and the formatted comment mirrors whichever the
 * selection sits entirely inside:
 *  - a tool diff/write block (CodeMirror) → fenced, language from the file path;
 *  - a code block the agent wrote (shiki <pre>) → fenced, language from the
 *    data-language attribute MarkdownContent adds after render;
 *  - anything else, or a selection straddling both → blockquote.
 */
function describeChatSelection(anchor, selection) {
    const el = anchor?.nodeType === Node.ELEMENT_NODE ? anchor : anchor?.parentElement
    if (!el) return { quoteMode: 'quote' }

    const toolBlock = el.closest('[data-file-path]')
    if (toolBlock && holdsWholeSelection(toolBlock, selection)) {
        return { quoteMode: 'code', filePath: toolBlock.dataset.filePath || null }
    }

    const pre = el.closest('pre')
    if (pre && holdsWholeSelection(pre, selection)) {
        const lang = pre.dataset.language
            || pre.querySelector('code[class*="language-"]')?.className.match(/language-(\S+)/)?.[1]
        return { quoteMode: 'code', lang: lang && lang !== 'text' ? lang : null }
    }

    return { quoteMode: 'quote' }
}

const {
    textSelectionCommentRef,
    textSelectionText,
    textSelectionPosition,
    textSelectionMetadata,
    closeTextSelectionComment,
} = useTextSelectionComment({
    containerRef: scrollerRef,
    enrichNativeMetadata: describeChatSelection,
    enabled: ref(true),
})

defineExpose({
    getScrollerElement,
    handleForwardedDrop,
    // Transcript navigation, for the command-palette commands SessionView
    // registers: the same four moves the ChatNavToolbar offers, plus the
    // "anything to navigate at all" flag that gates them.
    chatNav: {
        // A getter, not the ref itself: only top-level exposed refs are
        // unwrapped, and reading `.value` through two proxies invites the kind
        // of silent `undefined` a guard would swallow.
        hasNavigation: () => navHasNavigation.value,
        goTop: navGoTop,
        goPrevBlock: navGoPrevBlock,
        goNextBlock: navGoNextBlock,
        goBottom: navGoBottom,
    },
    insertTextAtCursor: (text, options) => messageInputRef.value?.insertTextAtCursor(text, options),
    getSessionSetting: (key) => messageInputRef.value?.getSessionSetting(key) ?? null,
    setSessionSetting: (key, value) => messageInputRef.value?.setSessionSetting(key, value),
    getSessionGateState: () => messageInputRef.value?.getSessionGateState() ?? null,
})
</script>

<template>
    <div
        class="session-items-list"
        :class="{ 'drag-over': dragOverType }"
        @dragenter="onDragEnter"
        @dragleave="onDragLeave"
        @dragover="onDragOver"
        @drop="onDrop"
    >
        <!-- In-session search bar (Ctrl+F) -->
        <SessionSearchBar
            v-if="showSessionSearch"
            ref="sessionSearchRef"
            :session-id="sessionId"
            @close="closeSessionSearch"
            @navigate="handleSearchNavigate"
            @update:terms="handleSearchTerms"
        />

        <!-- Compute pending state -->
        <div v-if="isComputePending" class="compute-pending-state">
            <wa-callout variant="warning">
                <wa-icon slot="icon" name="hourglass"></wa-icon>
                <div class="compute-pending-copy">
                    <span>Session is being prepared, please wait...</span>
                    <span v-if="computePendingHintPhase">
                        Preparation is taking longer than expected. An agent outside this TwiCC instance may still be
                        updating this session. Preparation will resume automatically when the session becomes stable.
                    </span>
                    <span v-if="computePendingHintPhase === 'restart'">
                        If this message remains after the agent finishes, restart this TwiCC instance.
                    </span>
                </div>
            </wa-callout>
        </div>

        <!-- Error state -->
        <FetchErrorPanel
            v-else-if="hasError"
            :loading="isLoading"
            @retry="handleRetry"
        >
            Failed to load session content
        </FetchErrorPanel>

        <!-- Loading state -->
        <div v-else-if="isLoading" class="empty-state">
            <wa-spinner></wa-spinner>
            <span>Loading...</span>
        </div>

        <!-- Draft session empty state -->
        <div v-else-if="session?.draft && !visualItems.length" class="empty-state">
        </div>

        <!-- Empty state (no items and not a special state above) -->
        <div v-else-if="!visualItems.length" class="empty-state">
            Nothing to show yet
        </div>

        <!--
            Items list (virtualized), plus the navigation toolbar pinned over its
            bottom-right corner. The wrapper is what gives the toolbar its
            positioning context: `.session-items-list` also holds the composer
            below, so anchoring to it would drop the toolbar onto the composer.

            IMPORTANT: Uses v-show instead of v-if/v-else-if to keep the VirtualScroller
            mounted across KeepAlive deactivation/activation cycles. Without this, the
            v-else-if chain causes the VirtualScroller to be destroyed and recreated,
            losing the composable's height cache and scroll state.
            See spec: "Problems Encountered > VirtualScroller Scroll Position Loss"
        -->
        <div v-show="showVirtualScroller" class="chat-scroll-area">
            <VirtualScroller
                ref="scrollerRef"
                :items="visualItems"
                :item-key="item => item.lineNum"
                :item-min-height="streamSwapItemMinHeight"
                :min-item-height="MIN_ITEM_SIZE"
                :buffer="5000"
                :unload-buffer="10000"
                :prevent-auto-scroll-to-bottom="!!parentSessionId"
                class="session-items"
                :class="{ 'initial-scrolling': isInitialScrolling }"
                @update="onScrollerUpdate"
                @item-resized="onItemResized"
                @became-visible="onScrollerBecameVisible"
            >
                <template #default="{ item, index }">
                    <!-- Day separator (horizontal rule + date) — must come before the
                         placeholder branch since separators carry no content. -->
                    <DaySeparator
                        v-if="item.isDaySeparator"
                        :label="item.dayLabel"
                        :day-key="item.dayKey"
                    />

                    <!-- Placeholder (no content loaded yet) -->
                    <div
                        v-else-if="!hasContent(item)"
                        :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                        :style="{ minHeight: MIN_ITEM_SIZE + 'px' }"
                    ></div>

                    <!-- Group head: show toggle (+ item content if expanded) -->
                    <template v-else-if="item.isGroupHead">
                        <GroupToggle
                            :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd && !item.isExpanded }"
                            :expanded="item.isExpanded"
                            :item-count="item.groupSize"
                            :comments-count="groupCommentsCount(item.lineNum, item.groupTail)"
                            @toggle="toggleGroup(item.lineNum)"
                        />
                        <SessionItem
                            v-if="item.isExpanded"
                            :class="{ 'is-block-end': item.isBlockEnd }"
                            :content="getParsedContent(item)"
                            :kind="item.kind"
                            :synthetic-kind="item.syntheticKind || null"
                            :project-id="projectId"
                            :session-id="sessionId"
                            :parent-session-id="parentSessionId"
                            :line-num="item.lineNum"
                            :externally-grouped="item.externallyGrouped || false"
                            :is-block-end="item.isBlockEnd || false"
                        />
                    </template>

                    <!-- Regular item (including ALWAYS with prefix/suffix): show item content -->
                    <SessionItem
                        v-else
                        :class="{ 'is-block-start': item.isBlockStart, 'is-block-end': item.isBlockEnd }"
                        :content="getParsedContent(item)"
                        :kind="item.kind"
                        :synthetic-kind="item.syntheticKind || null"
                        :project-id="projectId"
                        :session-id="sessionId"
                        :parent-session-id="parentSessionId"
                        :line-num="item.lineNum"
                        :externally-grouped="item.externallyGrouped || false"
                        :group-head="item.groupHead"
                        :group-tail="item.groupTail"
                        :prefix-expanded="item.prefixExpanded || false"
                        :suffix-expanded="item.suffixExpanded || false"
                        :detail-toggle-for="item.detailToggleFor ?? null"
                        :block-comments-count="item.detailToggleFor != null ? blockCommentsCount(item.detailToggleFor) : 0"
                        :is-block-start="item.isBlockStart || false"
                        :is-block-end="item.isBlockEnd || false"
                        @toggle-suffix="toggleGroup(item.suffixGroupHead)"
                    />
                </template>
            </VirtualScroller>

            <!-- Hidden alongside the scroller during the initial scroll-to-bottom
                 (`.initial-scrolling` only covers the scroller itself), and on a
                 transcript that fits on one screen. -->
            <ChatNavToolbar
                v-show="navHasNavigation && !isInitialScrolling"
                :can-go-top="navCanGoTop"
                :can-go-prev="navCanGoPrev"
                :can-go-next="navCanGoNext"
                :can-go-bottom="navCanGoBottom"
                :scroll-element="scrollerElement"
                @top="navGoTop"
                @prev="navGoPrevBlock"
                @next="navGoNextBlock"
                @bottom="navGoBottom"
            />
        </div>

        <div class="session-footer">
            <!-- Stale session banner (replaces message input for stale main sessions) -->
            <div v-if="isStale && !parentSessionId" class="stale-banner">
                <wa-callout variant="warning" appearance="outlined">
                    <wa-icon slot="icon" name="clock-rotate-left"></wa-icon>
                    <div class="stale-banner-content">
                        <strong>Read-only session</strong>
                        <span>The session files were cleaned up by {{ providerLabel }}. The conversation history has been preserved for reference.</span>
                    </div>
                </wa-callout>
            </div>
            <!-- Provider disabled banner (replaces message input when the session's provider is disabled) -->
            <div v-else-if="!isProviderEnabled && !parentSessionId" class="provider-disabled-banner">
                <wa-callout variant="warning" appearance="outlined">
                    <wa-icon slot="icon" name="circle-pause"></wa-icon>
                    <div class="provider-disabled-content">
                        <strong>{{ providerLabel }} is disabled</strong>
                        <span>
                            Re-enable {{ providerLabel }} from
                            <strong>Settings → Providers</strong> to resume this session.
                        </span>
                    </div>
                </wa-callout>
            </div>
            <template v-else>
                <!-- Current goal bar (topmost of the footer stack). Collapsed to a
                     single-line bar by default; opening it (read-only objectives
                     panel) goes through the accordion like the other panels.
                     Hidden once the user dismisses a closed goal; a new goal
                     brings it back. -->
                <GoalBlock
                    v-if="currentGoal"
                    ref="goalBlockRef"
                    :session-id="sessionId"
                    :project-id="projectId"
                    :sending-locked="hasAnswerablePendingRequest"
                    @request-open="setOpenBlock('goal', { focus: true })"
                    @request-collapse="goToComposer(true)"
                />
                <!-- Pending request form. When multiple parallel requests are pending, the
                     oldest is shown and a counter is displayed for the others. On a main
                     session it stacks above the composer; the two coordinate so at most one
                     is expanded — opening the request reduces the composer (`@expand`),
                     while reducing either leaves the other as-is. On a subagent it stands
                     alone (no composer). On hybrid sessions the same widget renders for
                     answerable requests (dual surface: the TUI dialog stays answerable
                     too, first responder wins); only a request degraded to badge-only
                     (`hybrid_terminal`, GUI channel expired) hides it. -->
                <PendingRequestForm
                    v-if="hasAnswerablePendingRequest"
                    ref="pendingFormRef"
                    :session-id="sessionId"
                    :pending-request="pendingRequest"
                    :pending-count="pendingRequests.length"
                    @request-open="setOpenBlock('pending', { focus: true })"
                    @request-collapse="collapsePendingRequest"
                />
                <!-- Hybrid CLI sessions: the embedded terminal, with a badge in the
                     block header pointing at pending prompts (the TUI surface).
                     Gated by the hybrid feature flag — see the notice below for the
                     flag-off case (an existing hybrid session on a server where the
                     feature is disabled). -->
                <HybridTerminalBlock
                    v-if="isHybridSession && !parentSessionId && settingsStore.isClaudeHybridEnabled"
                    ref="hybridTerminalRef"
                    :session-id="sessionId"
                    @request-open="setOpenBlock('terminal', { focus: true })"
                    @request-collapse="goToComposer(true)"
                    @show-pending-form="setOpenBlock('pending', { focus: true })"
                    @state-change="onHybridTerminalState"
                />
                <!-- Hybrid mode disabled on this server: the session is hybrid but
                     the feature is gated off, so the CLI terminal can't run. Show a
                     notice in its place rather than a dead terminal. -->
                <div
                    v-else-if="isHybridSession && !parentSessionId"
                    class="hybrid-disabled-notice"
                >
                    <wa-callout variant="warning" size="small">
                        <wa-icon slot="icon" name="triangle-exclamation" variant="classic"></wa-icon>
                        This session runs in hybrid Claude CLI mode, which is currently
                        disabled on this server. It can't be used until hybrid mode is
                        re-enabled.
                    </wa-callout>
                </div>
                <!-- Message input (main sessions only). Always rendered so prepared text is
                     preserved. Sending is locked while session computation is pending or an
                     answerable request is open; the textarea remains usable. Opening it reduces
                     the request (`@expand`); both can be reduced at once. On hybrid sessions only
                     the degraded badge-only state (no form, answer happens inside the TUI) keeps
                     sending possible: it would steer or queue in the TUI. -->
                <MessageInput
                    v-if="!parentSessionId"
                    ref="messageInputRef"
                    :session-id="sessionId"
                    :project-id="projectId"
                    :sending-locked="composerSendingLock.locked"
                    :sending-locked-reason="composerSendingLock.reason"
                    :sending-locked-presentation="composerSendingLock.presentation"
                    :has-panel-above="hasAnswerablePendingRequest || hybridTerminalVisible || !!currentGoal"
                    :terminal-visible="hybridTerminalVisible"
                    :terminal-attention="hybridTerminalAttention"
                    @needs-title="emit('needs-title')"
                    @request-expand="openBlock = 'message-input'"
                    @request-collapse="openBlock = 'none'"
                    @open-terminal="setOpenBlock('terminal', { focus: true })"
                />
            </template>
        </div>

        <!-- Drop zone overlay -->
        <div v-if="dragOverType" class="drop-overlay">
            <div class="drop-overlay-content">
                <wa-icon :name="dragOverType === 'files' ? 'cloud-upload' : 'text-indent-left'" style="font-size: 3rem;"></wa-icon>
                <span>{{ dragOverType === 'files' ? 'Drop files here' : 'Drop text into message' }}</span>
            </div>
        </div>

        <!-- Ephemeral text selection comment widget (teleported to body to avoid overflow clipping) -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="textSelectionPosition"
                ref="textSelectionCommentRef"
                :selected-text="textSelectionText"
                :position="textSelectionPosition"
                :metadata="textSelectionMetadata"
                focus-composer-on-add
                @close="closeTextSelectionComment"
            />
        </Teleport>
    </div>
</template>

<style scoped>
.session-items-list {
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
    overflow: hidden;
    position: relative;
}

/* Drop zone visual feedback */
.session-items-list.drag-over {
    outline: 3px dashed var(--wa-color-primary);
    outline-offset: -3px;
}

.drop-overlay {
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.5);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
    pointer-events: none;
}

.drop-overlay-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    color: white;
    font-size: var(--wa-font-size-xl);
    font-weight: 500;
}

/* Positioning context for the navigation toolbar, and nothing else: it takes
   the scroller's place in the column so the layout is unchanged. */
.chat-scroll-area {
    position: relative;
    display: flex;
    flex-direction: column;
    flex: 1;
    min-height: 0;
}

.session-items {
    flex: 1;
    min-height: 0;
    padding-bottom: var(--wa-space-2xl);
}

/* Hide scroller during initial scroll to bottom to prevent visible jumping.
   Using visibility:hidden keeps the element in the layout and scrollable,
   but invisible until we're positioned at the bottom. */
.session-items.initial-scrolling {
    visibility: hidden;
}

.empty-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}

.compute-pending-state {
    flex: 1;
    padding: var(--wa-space-l);
    display: flex;
    align-items: center;
    justify-content: center;
}

.compute-pending-state wa-callout {
    max-width: 500px;
}

.compute-pending-copy {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
}

.session-footer {
    position: relative;
    overflow-y: auto;
    > wa-divider {
        --width: var(--divider-size);
        --spacing: 0;
    }
}
.session-footer:has(.pending-request-form.maximized),
.session-footer:has(.hybrid-terminal-block.maximized),
.session-footer:has(.goal-block.maximized) {
    position: static;
}

.stale-banner {
    padding: var(--wa-space-s);
}

.hybrid-disabled-notice {
    padding: var(--wa-space-s);
}

.stale-banner-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

.provider-disabled-banner {
    padding: var(--wa-space-s);
}

.provider-disabled-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);
}

</style>
