// frontend/src/stores/data.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { toRaw } from 'vue'
import { getPrefixSuffixBoundaries } from '../utils/contentVisibility'
import { computeVisualItems, visualItemEqual, insertDaySeparators } from '../utils/visualItems'
import { DISPLAY_LEVEL, DISPLAY_MODE, INITIAL_ITEMS_COUNT, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants'
import { getProviderHelpers, getProviderStore } from '../providers'
import { getSessionCutoffMs, isSessionUnread } from '../utils/sessions'
import {
    resolveDraftProvider,
    resolveProjectDefaultProvider,
    resolveProjectAgentDefaults,
} from '../utils/projectAgentDefaults'
import { resolveProjectLayoutId } from '../utils/layoutDefaults'
import { resolveProjectIconUrl } from '../utils/projectIcon'
import { resolveProjectTrust } from '../utils/trust'
import { useSettingsStore } from './settings'
import { useLayoutsStore } from './layouts'
import {
    saveDraftMessage,
    getDraftMessage,
    deleteDraftMessage,
    getAllDraftMessages,
    saveDraftSession,
    getDraftSession,
    deleteDraftSession as deleteDraftSessionFromDb,
    getAllDraftSessions,
    saveDraftMedia,
    deleteDraftMedia,
    getDraftMediasBySession,
    deleteAllDraftMediasForSession,
    getAllDraftMedias
} from '../utils/draftStorage'
import { saveInflightSend, deleteInflightSend, getAllInflightSends } from '../utils/inflightStorage'
import { liveDraftKey, sweepPendingRequestDrafts } from '../utils/pendingRequestDraftStorage'
import {
    processFile,
    mediasToSdkFormat,
    getDraftMediaBytes,
    MAX_FILES_PER_DRAFT,
    MAX_TOTAL_BYTES_PER_DRAFT,
} from '../utils/fileUtils'
import { generateUUID } from '../utils/crypto'
import { debounce } from '../utils/debounce'
import { apiFetch } from '../utils/api'
import { applySessionMuteOnUserTurn } from '../utils/sessionMute'
import { isWorkspaceProjectId, extractWorkspaceId } from '../utils/workspaceIds'
import { getParsedContent, setParsedContent, clearParsedContent, hasContent } from '../utils/parsedContent'
import { initBuffer, feedDelta, flushBuffer, destroySessionBuffers, destroyAllBuffers } from '../utils/streamingBuffer'

// Map of debounced save functions per session (to avoid mixing debounces)
const debouncedSaves = new Map()

// In-flight loadSessions promise per projectId. Concurrent callers await the
// in-flight load instead of getting a silently-empty changed set — the
// reconciliation relies on the returned ids to know which sessions to reload.
const sessionsLoadInFlight = new Map() // projectId -> Promise<Set<sessionId>>

// Sessions with an ensureSessionItemsCoverage pass in flight (coalescing guard).
const itemsCoverageInFlight = new Set() // sessionId

/**
 * Coalesce ascending 1-based line numbers into [min, max] ranges.
 * @param {number[]} lineNums - Sorted ascending line numbers
 * @returns {Array<[number, number]>}
 */
function lineNumsToRanges(lineNums) {
    const ranges = []
    for (const n of lineNums) {
        const last = ranges[ranges.length - 1]
        if (last && n === last[1] + 1) {
            last[1] = n
        } else {
            ranges.push([n, n])
        }
    }
    return ranges
}

// ---- Dockable-layout persistence (Session.layout) -------------------------------------------------
// Per-session debounced PATCH of the layout intention to the backend; mirrors `debouncedSaves`. The
// `pending` set guards against the echoed `session_updated` re-hydrating (clobbering) a layout we just
// changed locally — our working copy is authoritative until the PATCH settles.
// Drafts have no backend row: instead of a PATCH, their edits are mirrored straight onto the in-memory
// `session.layout` (so any re-hydrate — e.g. the optimistic `updateSession` from a throttled
// `session_viewed` — is a no-op) and snapshotted to IndexedDB so they survive a reload.
const layoutPersistDebouncers = new Map() // sessionId -> debounced fn
const layoutPersistPending = new Set()    // sessionIds with an unsaved / in-flight layout change
const LAYOUT_PERSIST_DEBOUNCE_MS = 500

/** A fresh empty intention — single pane. Matches EMPTY_INTENTION in useSessionLayout.js. */
function emptyLayoutIntention() {
    return { assignment: {}, collapsed: [], activeSide: 'left', activeResize: 'left',
             activeByGroup: {}, resizeFractions: {}, tabOrder: [], maximized: null }
}

/** The persisted subset, in a FIXED key order (so echo / cross-device compares are stable). Everything
 *  except the transient `maximized`, which is never persisted. */
function stripLayoutForPersist(intention) {
    const i = intention || {}
    return {
        assignment: { ...(i.assignment || {}) },
        collapsed: [...(i.collapsed || [])],
        activeSide: i.activeSide === 'right' ? 'right' : 'left',
        activeResize: i.activeResize === 'right' ? 'right' : 'left',
        activeByGroup: { ...(i.activeByGroup || {}) },
        resizeFractions: { ...(i.resizeFractions || {}) },
        tabOrder: [...(i.tabOrder || [])],
    }
}

/** Tolerant merge of a persisted blob into a fresh working copy (fill defaults, drop unknown keys).
 *  This is the no-version migration strategy; `maximized` always resets to null (never persisted). */
function hydrateLayoutIntention(persisted) {
    const e = emptyLayoutIntention()
    const p = persisted && typeof persisted === 'object' ? persisted : {}
    if (p.assignment && typeof p.assignment === 'object') e.assignment = { ...p.assignment }
    if (Array.isArray(p.collapsed)) e.collapsed = [...p.collapsed]
    if (p.activeSide === 'left' || p.activeSide === 'right') e.activeSide = p.activeSide
    if (p.activeResize === 'left' || p.activeResize === 'right') e.activeResize = p.activeResize
    if (p.activeByGroup && typeof p.activeByGroup === 'object') e.activeByGroup = { ...p.activeByGroup }
    if (p.resizeFractions && typeof p.resizeFractions === 'object') e.resizeFractions = { ...p.resizeFractions }
    if (Array.isArray(p.tabOrder)) e.tabOrder = [...new Set(p.tabOrder.filter((id) => typeof id === 'string'))]
    return e
}

/** The catalog template subset (structure only) of an intention — what a named layout stores, and
 *  what is applied when loading one (runtime fields / maximized are session-only). */
function layoutTemplate(intention) {
    const i = intention || {}
    return {
        assignment: { ...(i.assignment || {}) },
        collapsed: [...(i.collapsed || [])],
        resizeFractions: { ...(i.resizeFractions || {}) },
        tabOrder: [...(i.tabOrder || [])],
    }
}

// How long a ``text`` streaming block can stay quiet before we flip its
// ``stopped`` flag and let the WorkingAssistantMessage placeholder reappear.
// Used by streamBlockDelta below. Codex's ``item/completed`` event can lag
// the last actual ``agentMessage/delta`` by several seconds (15+ observed),
// during which the SDK has nothing more to say but the agent is technically
// still working — without this nudge the UI looks frozen with no indicator.
// Kept deliberately long: the placeholder is appended below the streaming
// text, so every appearance/disappearance shifts the whole block. Short
// natural pauses between deltas are common on both providers, and a tighter
// delay made the indicator flicker in and out while the text jumped with it.
const STREAM_BLOCK_INACTIVITY_MS = 2000

// Max number of sessions surfaced by the Ctrl+` session switcher (the MRU
// itself keeps up to 100 entries; the switcher panel shows the most recent
// slice of it).
const MRU_SWITCHER_LIMIT = 20

// Cancel any pending inactivity timer attached to a streaming block. Safe
// to call when no timer is set. Called from streamBlockStop / start /
// retire / process-state-dead paths so we never leak a setTimeout.
function clearBlockInactivityTimer(block) {
    if (block?._inactivityTimer) {
        clearTimeout(block._inactivityTimer)
        block._inactivityTimer = null
    }
}

// In-flight send registry: one snapshot per send_message frame, kept between
// "the frame left the socket" and "the real user_message line arrived" (or an
// error frame consumed it). Powers the send-failure recovery flow: restoring
// the composer draft and dropping the optimistic chat message. Module-level
// and non-reactive on purpose — only programmatic lookups, never rendered.
// Snapshots hold the ORIGINAL draft-format medias (not the SDK conversion),
// so restoring is conversion-free.
//
// The registry is mirrored to IndexedDB (utils/inflightStorage.js) and
// hydrated at boot: an error frame only reaches the socket that sent the
// message, so a snapshot whose error was lost with the WebSocket (frozen or
// killed tab) must survive until the audit (auditInflightSends) can check it
// against the session's persisted items — the JSONL user_message line being
// the ground truth for "this send was delivered".
const inflightSends = new Map()
// Retention for unresolved snapshots: long on purpose — a tab killed days
// ago should still surface its lost message when the session is reopened.
const INFLIGHT_SEND_TTL_MS = 7 * 24 * 60 * 60 * 1000
// Minimum snapshot age before the audit may call it undelivered: younger
// sends may simply not have their user_message line written yet.
const INFLIGHT_AUDIT_MIN_AGE_MS = 60 * 1000
// Max metadata-only user_message lines whose content the audit fetches
// before concluding (recent-most kept — see auditInflightSends).
const INFLIGHT_AUDIT_FETCH_CAP = 300
// Monotonic counter giving each failed-send bubble a unique, stable
// synthetic lineNum (FAILED_USER_MESSAGE.baseLineNum - seq) for the
// visual-item cache. Display order is by sentAt, not by lineNum.
let failedSendSeq = 0

// Identity of a user message for "is this the same send?" comparisons. Text is
// the discriminant when there is any; a message made only of attachments has
// none, so its attachment count stands in (two attachment-only sends of the
// same size are then indistinguishable — acceptable, exactly like two identical
// texts). Returns null when the message carries neither.
function userMessageMatchKey(providerHelpers, parsed) {
    const text = providerHelpers.extractUserMessageText(parsed)
    if (text) return `t:${text}`
    const count = providerHelpers.extractUserMessageAttachmentCount(parsed)
    return count > 0 ? `a:${count}` : null
}

// Same key, computed from an in-flight send snapshot (composer side) rather
// than from a parsed JSONL item.
function inflightSendMatchKey(entry) {
    const text = (entry?.text || '').trim()
    if (text) return `t:${text}`
    // ``mediaCount`` is the fallback for a snapshot rehydrated from IndexedDB
    // whose medias were too big to persist (mediasDropped).
    const count = entry?.medias?.length || entry?.mediaCount || 0
    return count > 0 ? `a:${count}` : null
}

function userMessageMatchesOptimistic(providerHelpers, optimistic, item) {
    if (!providerHelpers || !optimistic || item?.kind !== 'user_message') return false

    const optimisticKey = userMessageMatchKey(providerHelpers, getParsedContent(optimistic))
    if (!optimisticKey) return false

    const parsed = getParsedContent(item)
    const createdAtMs = optimistic._optimisticCreatedAtMs
    const itemTimestampMs = typeof parsed?.timestamp === 'string'
        ? Date.parse(parsed.timestamp)
        : Number.NaN
    if (createdAtMs && Number.isFinite(itemTimestampMs) && itemTimestampMs < createdAtMs - 1000) {
        return false
    }

    return userMessageMatchKey(providerHelpers, parsed) === optimisticKey
}

// Special project ID for "All Projects" mode
export const ALL_PROJECTS_ID = '__all__'

// Aggregate per-provider startup-progress entries for a single phase into the
// flat ``{ current, total, completed }`` shape consumed by the UI. Returns
// null when no provider has reported yet so callers can stay falsy-aware.
function aggregatePhase(byProvider) {
    if (!byProvider) return null
    const entries = Object.values(byProvider)
    if (entries.length === 0) return null
    let current = 0
    let total = 0
    let completed = true
    for (const entry of entries) {
        current += entry.current ?? 0
        total += entry.total ?? 0
        if (!entry.completed) completed = false
    }
    return { current, total, completed }
}

// Cheap "still booting" probe used by hot getters (unread counts, sessions
// list) to skip expensive work while any provider is still mid-phase.
function hasActiveStartupPhase(startupProgress) {
    for (const byProvider of Object.values(startupProgress)) {
        if (!byProvider) continue
        for (const entry of Object.values(byProvider)) {
            if (entry && !entry.completed) return true
        }
    }
    return false
}

/**
 * Sort sessions by display priority:
 * 1. Pinned sessions first (top-level split — any non-null pin mode counts).
 * 2. Within each pin group: sessions with active process first (by started_at
 *    descending for stable ordering).
 * 3. Remaining sessions within each pin group: by mtime descending.
 *
 * @param {Object} processStates - Map of sessionId -> processState
 * @returns {function} Comparator function for Array.sort()
 */
export function sessionSortComparator(processStates) {
    return (a, b) => {
        // 1. Pinned sessions first (regardless of mode).
        //    `pinned` is a string ('project'/'workspace'/'all') or null — any truthy
        //    value means pinned.
        const aPinned = !!a.pinned
        const bPinned = !!b.pinned
        if (aPinned !== bPinned) return aPinned ? -1 : 1

        // 2. Within the same pin group: sessions with active process first.
        const aProcess = processStates[a.id]
        const bProcess = processStates[b.id]
        const aHasProcess = aProcess != null
        const bHasProcess = bProcess != null
        if (aHasProcess !== bHasProcess) return aHasProcess ? -1 : 1

        // 3. Among active sessions: sort by started_at descending (most recently started first).
        //    This gives a stable order since started_at never changes during process lifetime,
        //    avoiding rapid swapping when multiple sessions update frequently.
        if (aHasProcess && bHasProcess) {
            return (bProcess.started_at || 0) - (aProcess.started_at || 0)
        }

        // 4. Non-active sessions: sort by mtime descending.
        return b.mtime - a.mtime
    }
}

/**
 * Compute display metadata for a streaming synthetic item.
 *
 * Decides display_level / group_head / group_tail based on the block's type
 * and surrounding context, so the synthetic item participates in the existing
 * grouping/visibility logic in visualItems.js.
 *
 * Rules:
 *   - text block:
 *       display_level = ALWAYS, no group.
 *   - thinking block:
 *       display_level = COLLAPSIBLE.
 *       group_head:
 *         - if the last real item is in an open group (COLLAPSIBLE with
 *           group_head set, OR ALWAYS with group_tail set), join it.
 *         - otherwise, become own group head (group_head = self.line_num).
 *       group_tail = null (a streaming thinking is never a suffix anchor).
 *
 * Note on conversation mode: streaming items are always hidden in conversation
 * mode unless the current block is in detailed mode. That filtering happens
 * in visualItems.js, not here.
 *
 * @param {Object} block - a streaming block: { blockIndex, blockType, ... }
 * @param {Object|null} lastRealItem - last DISPLAYABLE item (display_level
 *   ALWAYS or COLLAPSIBLE) in sessionItems before streaming was injected.
 *   Caller must scan past DEBUG_ONLY items and items with null display_level
 *   so the anchor reflects the last item the user actually sees.
 * @param {number} streamingLineNum - the synthetic line_num for this block
 *   (= SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex).
 * @returns {{display_level: number, group_head: number|null, group_tail: number|null}}
 */
function getStreamingItemMetadata(block, lastRealItem, streamingLineNum) {
    if (block.blockType === 'text') {
        return {
            display_level: DISPLAY_LEVEL.ALWAYS,
            group_head: null,
            group_tail: null,
        }
    }

    // Thinking block.
    let groupHead = streamingLineNum  // default: own fake group
    if (lastRealItem) {
        if (
            lastRealItem.display_level === DISPLAY_LEVEL.COLLAPSIBLE &&
            lastRealItem.group_head != null
        ) {
            // Join the existing COLLAPSIBLE group.
            groupHead = lastRealItem.group_head
        } else if (
            lastRealItem.display_level === DISPLAY_LEVEL.ALWAYS &&
            lastRealItem.group_tail != null
        ) {
            // Continue the ALWAYS-suffix group started by lastRealItem.
            groupHead = lastRealItem.line_num
        }
    }
    return {
        display_level: DISPLAY_LEVEL.COLLAPSIBLE,
        group_head: groupHead,
        group_tail: null,
    }
}

/**
 * Whether the tool card the working-message would point to is actually visible
 * on screen, given the current display mode and expansion state.
 *
 * The working-message component drops the parenthesised target (e.g. the file
 * path) when there's a single active tool and its card sits right above. That
 * shortcut assumes the card is visible; in simplified mode the tool may live
 * inside a collapsed group, and in conversation mode the whole non-user block
 * is hidden unless "show details" is open.
 *
 * @param {Array} items - real session items (no synthetic).
 * @param {string|null} lastStartedToolId - id of the most recently started tool.
 * @param {string} mode - effective DISPLAY_MODE.
 * @param {Array<number>} expandedGroups - line_nums of expanded group heads.
 * @param {boolean} isCurrentBlockDetailed - conversation mode: whether the
 *   current user block has its detail toggle open.
 * @returns {boolean}
 */
function computeLastToolVisible(items, lastStartedToolId, mode, expandedGroups, isCurrentBlockDetailed) {
    if (mode === DISPLAY_MODE.NORMAL || mode === DISPLAY_MODE.DEBUG) return true
    if (!lastStartedToolId) return false
    for (let i = items.length - 1; i >= 0; i--) {
        const it = items[i]
        if (it.kind !== 'assistant_message') continue
        const parsed = getParsedContent(it)
        const blocks = parsed?.message?.content
        if (!Array.isArray(blocks)) continue
        const hasTool = blocks.some(b => b?.type === 'tool_use' && b?.id === lastStartedToolId)
        if (!hasTool) continue
        if (it.display_level === DISPLAY_LEVEL.DEBUG_ONLY) return false
        if (mode === DISPLAY_MODE.CONVERSATION) return isCurrentBlockDetailed
        if (mode === DISPLAY_MODE.SIMPLIFIED) {
            if (it.display_level === DISPLAY_LEVEL.ALWAYS) return true
            const head = it.group_head ?? it.line_num
            return expandedGroups.includes(head)
        }
        return true
    }
    return false
}

/**
 * Base MRU eligibility: the session exists in the store, isn't hidden, and isn't
 * a subagent (subagents are reached through their parent, never standalone).
 * Archived state is intentionally NOT considered here — the two MRU consumers
 * diverge on it: the Ctrl+` switcher keeps archived sessions (this predicate),
 * the post-archive fallback drops them (`isSwitchableSession`).
 * @param {object|undefined} session
 * @returns {boolean}
 */
function isMruEligible(session) {
    return !!session && !session.hidden && !session.parent_session_id
}

/**
 * Whether a session is a valid target for the post-archive auto-navigation
 * fallback (`getNextMruPath`). Like `isMruEligible` but archived sessions are
 * excluded: after archiving the current session we must land on a *non*-archived
 * one, never bounce onto another archived session. The Ctrl+` switcher
 * deliberately uses the looser `isMruEligible` instead — it lets you jump back
 * to a session you just archived (MRU = the last sessions you were on, whatever
 * their state).
 * @param {object|undefined} session
 * @returns {boolean}
 */
function isSwitchableSession(session) {
    return isMruEligible(session) && !session.archived
}

export const useDataStore = defineStore('data', {
    state: () => ({
        // Server data
        projects: {},       // { id: { id, sessions_count, mtime, stale, worktree_of } } — worktree_of: parent project id when this project is a git worktree, else null
        sessions: {},       // { id: { id, project_id, provider, last_line, mtime, stale } }
        artifactBookmarks: {},      // { id: { id, name, scope, session_id, project_id, relative_path, root, file_ext, available? } } — artifact bookmarks
        artifactBookmarksLoaded: false,
        // Session items indexed by session ID.
        // { sessionId: [{ line_num, content, display_level, ... }] } - line_num is 1-based
        //
        // ⚠️  IMPORTANT: Never access item.content directly for parsing.
        // Use getParsedContent(item) from utils/parsedContent.js instead.
        // Use hasContent(item) to check if content is available.
        sessionItems: {},

        // Process state for active Claude processes
        // { sessionId: { state: 'starting'|'assistant_turn'|'user_turn'|'dead', error?: string } }
        processStates: {},

        // Lifecycle state of each provider's orchestrator (mirrors backend
        // `twicc.providers.state.ProviderState`). Updated from the bootstrap
        // payload and live `provider_state_changed` WS messages. Used by
        // `isProviderAvailable` (provider must be both intent-enabled AND
        // in 'running' state) to gate runtime UI (callouts, pickers, ...).
        // Default fallback when a provider is missing from the map: 'stopped'.
        // { 'claude_code': 'running', 'codex': 'stopped' }
        providerStates: {},

        // Weekly activity data (from /api/home/ endpoint)
        // { _global: [...], projectId: [...] } — each value is Array of { date, user_message_count }
        weeklyActivity: {},

        // WebSocket connection state (updated by useWebSocket composable)
        wsConnected: false,

        // Startup progress (from WebSocket startup_progress messages).
        // Indexed by phase, then by provider key — so the per-phase total
        // displayed in the UI is the sum of every provider that emitted
        // progress for that phase. Provider-agnostic phases (e.g.
        // ``search_index``) bucket under ``__global__``.
        // Shape: { [phase]: { [provider | '__global__']: { current, total, completed } } }
        startupProgress: {},

        // Server info (from WebSocket messages)
        currentVersion: null,           // string, from server_version message
        pendingChangelogVersion: null,  // string, version to show in changelog dialog after app is ready
        previousChangelogVersion: null, // string, previousLastChangelogVersionSeen from backend
        // True while the startup hybrid-mode announcement (App.vue) is pending or
        // open. The changelog auto-open waits for this to clear so the two dialogs
        // never stack on launch — the announcement takes priority.
        hybridAnnouncementActive: false,
        // Same purpose as hybridAnnouncementActive, but for the telemetry notice
        // (App.vue). Kept as its own flag rather than folded into the hybrid one —
        // the two can be pending independently (e.g. hybrid holds the floor first,
        // then releases into a still-pending telemetry notice) and a shared name
        // would read as hybrid-specific everywhere else it's referenced.
        telemetryNoticeActive: false,
        latestVersion: null,            // { version, releaseUrl } or null, from update_available message

        // Local UI state (separate from server data to avoid being overwritten)
        localState: {
            projectsList: {
                loading: false,
                loadingError: false
            },
            projects: {},   // { projectId: { sessionsFetched, sessionsLoading, sessionsLoadingError, hasMoreSessions, oldestSessionMtime } }
            sessions: {},   // { sessionId: { itemsFetched, itemsLoading, itemsLoadingError } }

            // Expanded groups - per session (session-level groups)
            // { sessionId: [groupHeadLineNum, ...] }
            // Using array instead of Set for Vue reactivity
            sessionExpandedGroups: {},

            // Expanded internal groups - per session, per item (content-level groups within ALWAYS items)
            // { sessionId: { lineNum: [startIndex, ...] } }
            // Two-level structure allows easy invalidation of entire session
            sessionInternalExpandedGroups: {},

            // Blocks expanded to detailed mode in conversation view.
            // { sessionId: [userMessageLineNum, ...] }
            // Each entry is the line_num of the last user_message before a non-user block.
            // When present, all non-user items following that user_message (up to the next
            // user_message) are rendered in detailed/normal mode instead of conversation mode.
            // Using array instead of Set for Vue reactivity (same pattern as sessionExpandedGroups).
            // Ephemeral: not persisted, lost on page refresh.
            sessionDetailedBlocks: {},

            // Debug display override — per session (dev-mode "force debug view"
            // toggle in the session header). When set, the session renders in
            // DISPLAY_MODE.DEBUG regardless of the global display mode.
            // { sessionId: true }. Ephemeral: not persisted, lost on page refresh.
            sessionDebugOverride: {},

            // Visual items - computed from sessionItems, display mode, and expanded groups
            // { sessionId: [{ lineNum, isGroupHead?, isExpanded? }, ...] }
            sessionVisualItems: {},

            // Visual item reference cache - used to stabilize object references
            // across recomputes so Vue skips re-renders for unchanged items.
            // { sessionId: Map<lineNum, visualItem> }
            // Not reactive (plain object + Maps) — only used internally by
            // recomputeVisualItems, never read by Vue templates.
            visualItemCache: {},

            // Open tabs per session - for tab restoration when returning to a session
            // { sessionId: { tabs: ['main', 'agent-xxx', ...], activeTab: 'agent-xxx' } }
            // Note: 'main' is always implicitly open, but included for consistency
            sessionOpenTabs: {},

            // Dockable layout intention per session (ephemeral; persistence deferred).
            // { sessionId: { assignment: { tabId: dockId }, collapsed: [dockId], activeSide,
            //                activeResize, activeByGroup: { groupKey: tabId },
            //                resizeFractions: { configKey: number }, tabOrder: [tabId],
            //                maximized: [dockId] | null } }
            // Geometry is NOT stored here — only the user's drag intention (fractions). The pure
            // layout resolver recomputes px from these (clamped) on every render. `maximized` is a
            // transient view state (a region's dockIds, or ['center']) — excluded from persistence.
            sessionLayout: {},

            // Agent links cache - maps tool_id to agent_id for Task tool_use items
            // { sessionId: { toolId: agentId } }
            // Only caches found agents (not-found triggers polling, not caching)
            agentLinks: {},
            // sessionId -> { tool_use_id: run_id } for the in-chat "View Workflow" button.
            workflowLinks: {},

            // Tool states - maps tool_use_id to { resultCount, completedAt, error, extra, toolResultLineNums }
            // { sessionId: { toolUseId: { resultCount, completedAt, error, extra, toolResultLineNums } } }
            // Populated by fetchToolStates on session load and WS tool_state
            toolStates: {},

            // Live items - tracks which session items arrived via WebSocket (real-time).
            // { sessionId: Set<lineNum> }
            // Used by auto-open live edit diffs feature: only items received in real-time
            // should auto-open, not historical items loaded from the API.
            liveItems: {},

            // Open wa-details state - persists open/close across virtual scroller mount/unmount.
            // { sessionId: { key: true, ... } }
            // Keys: toolId for tool_use details, `result:${toolId}` for tool result details.
            // Only open items are stored (sparse map). Ephemeral: not persisted, lost on refresh.
            openDetails: {},

            // Project display names cache - computed from name, directory, or id
            // { projectId: displayName }
            // Updated when project data changes
            projectDisplayNames: {},

            // Draft messages - unsent messages/titles per session
            // { sessionId: { message?: string, title?: string } }
            // Persisted to IndexedDB with debounce
            draftMessages: {},

            // Monotonic per-session counters bumped by appendDraftMessage.
            // A mounted composer watches its counter to resync its textarea:
            // its draft watcher deliberately ignores non-empty textareas, so
            // a programmatic append needs this explicit side channel.
            // { sessionId: number }. Ephemeral: not persisted.
            draftAppendSignals: {},

            // Title suggestions by session ID
            // Format: { sessionId: { suggestion: string, sourcePrompt?: string } }
            titleSuggestions: {},

            // Map { draftId: canonicalId } populated by ``bindDraftSession`` so
            // any backend message that still carries the draft id (e.g. a
            // ``title_suggested`` whose ``suggest_title`` was sent before the
            // bind) can be redirected to the canonical key. Lives for the
            // session's lifetime; entries don't go stale because the canonical
            // id is what every consumer now uses.
            draftAliases: {},

            // Sessions waiting on an auto-applied title — populated when the
            // user sends the first message of a draft and ``titleAutoApply`` is
            // enabled. The App-level watcher (in ``App.vue``) reacts to entries
            // here, waits for the matching ``titleSuggestions`` entry, applies
            // it to the session and persists it via :meth:`renameSession` once
            // the session has stopped being a draft. Each entry stores the
            // ``projectId`` because that's what ``renameSession`` needs and
            // the watcher otherwise has no way to recover it.
            // Format: { sessionId: { projectId: string } }
            pendingTitleAutoApply: {},

            // Draft attachments - media files pending send per session
            // { sessionId: Map<mediaId, DraftMedia> }
            // Stored separately from draftMessages to avoid rewriting large blobs on each keystroke
            attachments: {},

            // Number of files currently being processed (encoded/resized) per session.
            // { sessionId: number }
            // Used to block the send button until all files are ready.
            processingAttachments: {},

            // MRU (Most Recently Used) navigation tracking
            // Ordered array of { path, sessionId } entries, most recent first
            // path: the full route path (e.g. /project/abc/session/xyz/files)
            // sessionId: the session ID from the route, or null if no session selected
            // Used to navigate back when archiving the current session
            mruPaths: [],

            // Ordered ids of the sessions currently shown in the sidebar list,
            // published by SessionList.vue (its already-filtered `sessions`
            // computed). The Ctrl+Shift+` switcher reads this to offer the
            // on-screen list — pushing the rendered result guarantees parity
            // (filters, scope, search) far more simply than recomputing it.
            displayedSessionIds: [],

            // Count of artifact bookmarks currently shown in the sidebar list,
            // published by ArtifactBookmarkList.vue (its already-filtered `list`
            // computed). The mirror of `displayedSessionIds` for the artifacts
            // sidebar mode — read to drive the `data-has-items` presence flag on
            // `.sidebar-sessions`. A count (not ids) since no consumer needs the
            // ids yet.
            displayedArtifactBookmarkCount: 0,

            // Optimistic messages - user messages displayed immediately after send,
            // before the backend confirms with a real user_message item.
            // { sessionId: { syntheticKind, content, kind } }
            // Cleared when the real user_message arrives in addSessionItems.
            optimisticMessages: {},
            // Failed sends, rendered in the conversation flow as red
            // user-message bubbles with Retry/Edit/Delete actions (messaging
            // pattern). Set when a send_message error frame matches an
            // in-flight send or when the audit declares a snapshot lost.
            // { sessionId: { requestId: { requestId, text, medias,
            //   mediasDropped, code, message, sentAt, item } } }
            // ``item`` is the materialized synthetic session item (built once,
            // injected by recomputeVisualItems like the optimistic message).
            failedSends: {},

            // Staged (pending, not-yet-committed) hybrid switch for an existing
            // SDK session, keyed by sessionId. A startup-type change: chosen via
            // the toggle dialog but only committed (`set_session_hybrid`) on the
            // next Send / Apply. Deliberately volatile — NOT persisted to
            // IndexedDB — so a page reload before applying drops the staged
            // choice (the switch is irreversible only once committed).
            // { sessionId: true }
            stagedHybrid: {},

            // Streaming blocks - live text/thinking deltas from the SDK stream.
            // { sessionId: { messageId, blocks: [{ blockIndex, blockType, text, stopped, uuid }] } }
            // Each block is rendered as a synthetic visual item until the real
            // SessionItem (matched by message_id + uuid) arrives from the watcher.
            // `stopped` is set to true when content_block_stop fires (text is final
            // but uuid not yet known). While any block has stopped=false, the
            // WorkingAssistantMessage is hidden (streaming is actively showing content).
            streamingBlocks: {},

            // Pending draft → canonical session bindings, armed when a
            // `session_bound` WS message arrives before the canonical
            // session is in the store (i.e. before the watcher's
            // `session_updated`). { draftSessionId: sessionId }
            // Drained by tryFinalizePendingBinding() the moment the
            // canonical session lands in the store.
            pendingDraftBindings: {},

            // Peer messages delivered to a NEW session, waiting for that
            // session to exist. { sessionId: peerMessageId } — keyed by the
            // draft id, rekeyed to the canonical one on bind. Drained by
            // `_tryLinkPeerDelivery` when the real session lands in the store,
            // which is what finally records the delivery target backend-side.
            // Mirrored on the draft's IndexedDB record, so the link survives a
            // reload between the delivery and the first send.
            pendingPeerDeliveries: {},
        }
    }),

    getters: {
        /**
         * Find an artifact bookmark by its (session, relative path) — used by the
         * artifact action bar to reflect the bookmarked state of the shown file.
         */
        artifactBookmarkFor: (state) => (sessionId, relativePath) =>
            Object.values(state.artifactBookmarks).find(
                b => b.session_id === sessionId && b.relative_path === relativePath,
            ) || null,
        /**
         * Display-ready list for the Ctrl+` session switcher: the most-recently
         * visited sessions, newest first, one entry per session (the MRU is
         * already deduped by session, keeping the latest sub-route visited).
         * Project-page entries (no sessionId) are dropped — this is a session
         * switcher — and ineligible sessions are filtered via isMruEligible
         * (vanished / hidden / subagents). Archived sessions are kept on purpose:
         * the MRU is state-agnostic, so you can switch straight back to a session
         * you just archived. Capped at MRU_SWITCHER_LIMIT. Each item is
         * { session, path }, where `path` is the exact URL to restore (so
         * committing lands on the last tab visited within that session).
         */
        mruSwitcherSessions: (state) => {
            const out = []
            for (const entry of state.localState.mruPaths) {
                if (!entry.sessionId) continue
                const session = state.sessions[entry.sessionId]
                if (!isMruEligible(session)) continue
                out.push({ session, path: entry.path })
                if (out.length >= MRU_SWITCHER_LIMIT) break
            }
            return out
        },
        /**
         * Display-ready list for the "displayed sessions" mode of the switcher:
         * the sidebar's currently-rendered sessions, in order. Sourced from the
         * ids SessionList publishes (already filtered/scoped/searched), so no
         * re-filtering here — we trust the published list and just resolve each
         * id to its session, dropping any that vanished. Each item is
         * { session }; the navigation target is built at commit time.
         */
        displayedSwitcherSessions: (state) => {
            const out = []
            for (const id of state.localState.displayedSessionIds) {
                const session = state.sessions[id]
                if (!session) continue
                out.push({ session })
            }
            return out
        },
        // Data getters (sorted by mtime descending - most recent first)
        getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
        // Projects shown in "all projects" pickers/lists: excludes git worktrees
        // (those with `worktree_of` set), which are surfaced separately under
        // their main repository. Use this for every surface that lists or counts
        // top-level projects; keep `getProjects` (raw) for aggregates over
        // sessions/cost and for uniqueness checks that must see every project.
        getListableProjects: (state) =>
            Object.values(state.projects).filter(p => !p.worktree_of).sort((a, b) => b.mtime - a.mtime),
        // Worktree projects whose main repository is `projectId` (i.e. their
        // `worktree_of` points at it), sorted by mtime desc. Used to nest a
        // project's worktrees under it in the sidebar selector / New Session.
        getWorktreesOf: (state) => (projectId) =>
            Object.values(state.projects)
                .filter(p => p.worktree_of === projectId)
                .sort((a, b) => b.mtime - a.mtime),
        // Session scope of a project: the project itself plus its own git
        // worktrees. A worktree's sessions/cost/activity belong to its main
        // repository's whole, so viewing a main repo aggregates its worktrees'
        // sessions too — like a workspace aggregates its members, one level
        // down. Worktrees are kept regardless of archived state: the
        // session-level archive filter hides their sessions when "show archived"
        // is off, so toggling the flag needs no refetch. Returns [projectId] for
        // the All-Projects / workspace pseudo-ids (they scope sessions their own
        // way), or [] when no project id is given.
        getProjectScopeIds: (state) => (projectId) => {
            if (!projectId || projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(projectId)) {
                return projectId ? [projectId] : []
            }
            const ids = [projectId]
            for (const p of Object.values(state.projects)) {
                if (p.worktree_of === projectId) ids.push(p.id)
            }
            return ids
        },
        // Indicator/badge scope of a project: the project itself plus its
        // *visible* git worktrees, respecting the "show archived projects"
        // setting. Mirrors `getProjectScopeIds` but archived-aware — process /
        // unread badges and unread counts reflect what the user can see, while
        // `getProjectScopeIds` (archived-blind) drives session fetching and
        // whole-project stats. This is the single definition of a project
        // badge's scope: `AggregatedProcessIndicator` expands every project id
        // it receives through it, so a project's badge aggregates its worktrees
        // exactly like a workspace aggregates its members one level down.
        // Returns [projectId] for the All-Projects / workspace pseudo-ids (they
        // scope their own way), or [] when no project id is given.
        getProjectIndicatorScopeIds: (state) => (projectId) => {
            if (!projectId || projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(projectId)) {
                return projectId ? [projectId] : []
            }
            const showArchived = useSettingsStore().isShowArchivedProjects
            const ids = [projectId]
            for (const p of Object.values(state.projects)) {
                if (p.worktree_of === projectId && (showArchived || !p.archived)) ids.push(p.id)
            }
            return ids
        },
        getProject: (state) => (id) => state.projects[id],
        // Effective icon URL per project id, resolved by walking the inheritance
        // chain (utils/projectIcon.js) so a manual override cascades to
        // descendants. Memoized as a map — recomputed only when `projects`
        // changes — so per-render lookups (session lists, palette, …) are O(1).
        resolvedProjectIcons: (state) => {
            const out = {}
            for (const id in state.projects) {
                out[id] = resolveProjectIconUrl(id, state.projects)
            }
            return out
        },
        // Set of project ids whose *effective* trust is NOT trusted — i.e.
        // explicitly untrusted (`trust === false`) OR unknown (no own decision
        // and nothing resolvable up the chain). Drives the untrusted-project
        // indicator shown on project/worktree badges and in the command palette.
        // Computed once and cached by Pinia; thanks to Vue's fine-grained
        // reactivity it only recomputes when a trust-relevant field
        // (`trust`/`trust_propagation`/`worktree_of`/`directory`) of some project
        // changes value or a project is added/removed — never on a plain `mtime`
        // bump (the resolver reads none of those churny fields).
        untrustedProjectIds: (state) => {
            const ids = new Set()
            for (const id in state.projects) {
                if (resolveProjectTrust(id, state.projects).state !== true) ids.add(id)
            }
            return ids
        },
        // Resolve a project id to its main repository's project id: if the
        // project is a git worktree (`worktree_of` set), return the parent's id,
        // else the id itself. Used to display snippet/preset lists based on the
        // main repo while in a worktree session — a worktree has no workspaces or
        // project-scoped snippets of its own, so it borrows the main
        // repository's. Falls back to the id as-is when the project is unknown.
        getMainRepoProjectId: (state) => (projectId) =>
            (projectId && state.projects[projectId]?.worktree_of) || projectId,
        getProjectSessions: (state) => (projectId) => {
            const projectState = state.localState.projects[projectId]
            // Only apply the mtime lower-bound when there are more pages to load.
            // When all pages have been fetched (hasMoreSessions=false), every
            // session in the store should be visible — including ones added via
            // WS during background compute whose mtime may be older than the bound.
            const oldestMtime = projectState?.hasMoreSessions
                ? projectState.oldestSessionMtime
                : null
            // During startup, skip per-property reactive tracking on sessions.
            // Object.keys() tracks ITERATE_KEY (add/remove triggers re-eval),
            // then toRaw() avoids the ~23K track() calls per eval from filter/sort
            // property accesses. Normal tracking resumes after startup.
            const isStartup = hasActiveStartupPhase(state.startupProgress)
            let sessions, pStates
            if (isStartup) {
                Object.keys(state.sessions)
                const raw = toRaw(state.sessions)
                sessions = Object.values(raw)
                pStates = toRaw(state.processStates)
            } else {
                sessions = Object.values(state.sessions)
                pStates = state.processStates
            }
            return sessions
                .filter(s => s.project_id === projectId && !s.parent_session_id && !s.hidden)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort(sessionSortComparator(pStates))
        },
        getAllSessions: (state) => {
            const allState = state.localState.projects[ALL_PROJECTS_ID]
            const oldestMtime = allState?.hasMoreSessions
                ? allState.oldestSessionMtime
                : null
            // During startup, skip per-property reactive tracking on sessions.
            // Object.keys() tracks ITERATE_KEY (add/remove triggers re-eval),
            // then toRaw() avoids the ~23K track() calls per eval from filter/sort
            // property accesses. Normal tracking resumes after startup.
            const isStartup = hasActiveStartupPhase(state.startupProgress)
            let sessions, pStates
            if (isStartup) {
                Object.keys(state.sessions)
                const raw = toRaw(state.sessions)
                sessions = Object.values(raw)
                pStates = toRaw(state.processStates)
            } else {
                sessions = Object.values(state.sessions)
                pStates = state.processStates
            }
            return sessions
                .filter(s => !s.parent_session_id && !s.hidden)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort(sessionSortComparator(pStates))
        },
        getSession: (state) => (id) => state.sessions[id],
        getSessionProvider: (state) => (sessionId) => state.sessions[sessionId]?.provider ?? null,
        getSessionItems: (state) => (sessionId) => state.sessionItems[sessionId] || [],
        /**
         * Latest task/todo/plan snapshot for a session, normalised across
         * providers (Claude Code ``TodoWrite`` / ``Task*``, Codex
         * ``update_plan``). Returns ``null`` when the session has no task state
         * (backend default ``{}``), so consumers can ``v-if`` on the result.
         * Shape: ``{provider, source, line, updated_at, explanation, items:
         * [{status, content?, activeForm?}]}``. Carried on the serialized
         * Session like every other field (see ``serialize_session`` backend +
         * ``updateSession``), so it stays reactive through ``session_updated``.
         * Not rendered anywhere yet — exposed for future consumers.
         */
        getSessionTasks: (state) => (sessionId) => {
            const tasks = state.sessions[sessionId]?.tasks
            return tasks && tasks.items ? tasks : null
        },
        /**
         * Plan-like documents the session touched (``Session.plan_paths``),
         * sorted newest-first by ``updated_at``. Entries: ``{path, exists,
         * created_at, updated_at, source}`` — ``path`` is relative to the
         * session's project directory when the doc lives under it (resolve
         * with the ``worktree_of`` parent as fallback), absolute otherwise
         * (e.g. the native Claude plan). ``[]`` when none. Carried on the
         * serialized Session, so it stays reactive through ``session_updated``.
         * Drives the Plan tab (``PlanPane.vue``).
         */
        getSessionPlanDocs: (state) => (sessionId) => {
            const docs = state.sessions[sessionId]?.plan_paths
            if (!Array.isArray(docs) || docs.length === 0) return []
            return [...docs].sort(
                (a, b) => String(b.updated_at || '').localeCompare(String(a.updated_at || '')),
            )
        },
        /**
         * The session's current goal — the last entry of the goal lifecycle
         * history (``Session.goals``, see the backend ``providers/goals.py``
         * docstring for the shape), or ``null`` when the session never had a
         * goal or the user dismissed the latest one. Shape: ``{objectives:
         * [str], state: 'active'|'completed', cleared, raw_state, created_at,
         * updated_at, dismissed?}``. Carried on the serialized Session, so it
         * stays reactive through ``session_updated``.
         */
        getSessionCurrentGoal: (state) => (sessionId) => {
            const goals = state.sessions[sessionId]?.goals
            const last = goals?.length ? goals[goals.length - 1] : null
            return last && !last.dismissed ? last : null
        },

        /**
         * What recovery (if any) to offer on a terminal API-error item — returns
         * ``'resend' | 'retry' | null``:
         *  - ``'resend'`` — the turn errored at its very start: the displayed
         *    item RIGHT BEFORE it is a real ``user_message``, so re-send that
         *    prompt (the original text; attachments are already in context).
         *  - ``'retry'`` — the error hit MID-turn: the item before it is
         *    anything else (assistant output, a tool result, …), so there is no
         *    clean message to re-send — ask the agent to resume the interrupted
         *    turn instead.
         *  - ``null`` — a displayed item follows this api_error (not the last
         *    turn), there is no recoverable predecessor, a send is already in
         *    flight, or the agent is already starting/working.
         * Purely DB-derived (survives reload, identical SDK/hybrid). The caller
         * gates on ``isApiErrorMessage`` (terminal shape) first; intermediate
         * retry-progress items never reach here.
         * @param {string} sessionId
         * @param {number} lineNum - this api_error item's line number
         */
        apiErrorRecovery: (state) => (sessionId, lineNum) => {
            // A send/recovery already in progress (optimistic bubble, failed-send
            // bubble, or the agent already starting/working) → offer nothing.
            if (state.localState.optimisticMessages[sessionId]) return null
            const failed = state.localState.failedSends[sessionId]
            if (failed && Object.keys(failed).length) return null
            const procState = state.processStates[sessionId]?.state
            if (procState === PROCESS_STATE.STARTING || procState === PROCESS_STATE.ASSISTANT_TURN) {
                return null
            }
            const items = state.sessionItems[sessionId]
            if (!items) return null
            // Walk the DISPLAYED items (ALWAYS / COLLAPSIBLE — debug-only and
            // not-yet-computed lines don't count) from the tail: nothing
            // displayed may follow this api_error, and the displayed item right
            // before it decides the affordance (user_message → resend, anything
            // else → retry).
            for (let i = items.length - 1; i >= 0; i--) {
                const it = items[i]
                if (it.display_level !== DISPLAY_LEVEL.ALWAYS
                    && it.display_level !== DISPLAY_LEVEL.COLLAPSIBLE) continue
                if (it.line_num > lineNum) return null
                if (it.line_num === lineNum) continue
                return it.kind === 'user_message' ? 'resend' : 'retry'
            }
            return null
        },

        // Process state getter - returns { state, error?, pending_requests? } or null if no active process
        getProcessState: (state) => (sessionId) => state.processStates[sessionId] || null,

        // Effective context max for a session — provider-specific rules (such
        // as Claude Code's auto-promote-to-1M when usage > 85% of the 200K
        // window) live in the provider helpers. Single source of truth used by
        // the settings selector, the header progress ring, and the value sent
        // to the backend so they stay in sync. ``overrideModel`` lets callers
        // preview the value for a model not yet persisted on the session.
        getEffectiveContextMax: (state) => (sessionId, overrideModel = undefined) => {
            const session = state.sessions[sessionId]
            if (!session) return null
            const helpers = getProviderHelpers(session.provider)
            return helpers ? helpers.getEffectiveContextMax(session, overrideModel) : (session.context_max ?? null)
        },

        /**
         * Whether a stop request has been sent for this session and we're
         * waiting for the backend to confirm the process has died.
         * Used by UI components to show a spinner / disabled state on stop buttons.
         */
        isSessionStopping: (state) => (sessionId) =>
            state.processStates[sessionId]?.stopping === true,

        // Whether a session has active (non-stopped) streaming blocks
        hasActiveStreaming: (state) => (sessionId) => {
            const streaming = state.localState.streamingBlocks[sessionId]
            return streaming?.blocks.some(b => !b.stopped) ?? false
        },

        // Pending requests getter - returns an array (oldest first) of pending requests,
        // or an empty array if none. Multiple permission asks can be concurrent within
        // a single session (parallel concurrency-safe tools like Read + Glob).
        getPendingRequests: (state) => (sessionId) =>
            state.processStates[sessionId]?.pending_requests || [],

        /**
         * Count sessions with unread content for a project's badge — its own
         * sessions plus those of its visible git worktrees (a project's badge
         * aggregates its worktrees like a workspace aggregates its members).
         * Uses the canonical `isSessionUnread` predicate. Membership is tested
         * against the scope set, so a worktree counted once never double-counts.
         * @param {string} projectId - The project ID
         * @returns {number} The number of unread sessions
         */
        getProjectUnreadCount() {
            return (projectId) => {
                if (hasActiveStartupPhase(this.startupProgress)) return 0
                const scope = new Set(this.getProjectIndicatorScopeIds(projectId))
                let count = 0
                for (const session of Object.values(this.sessions)) {
                    if (session.hidden) continue
                    if (!scope.has(session.project_id)) continue
                    if (isSessionUnread(session, this.processStates[session.id])) count++
                }
                return count
            }
        },

        /**
         * Whether any session globally is in assistant_turn state.
         * Used by the dynamic favicon to show a blue activity dot.
         * Synthetic subagent states are skipped (a subagent is not a session,
         * and a stale synthetic must not pin the favicon "active"), as are
         * hidden sessions (kept out of every user-facing indicator).
         * @returns {boolean}
         */
        hasGlobalAssistantTurn: (state) => {
            for (const [sessionId, ps] of Object.entries(state.processStates)) {
                if (ps.synthetic || state.sessions[sessionId]?.hidden) continue
                if (ps.state === 'assistant_turn') return true
            }
            return false
        },

        /**
         * Whether at least one session of the given provider has a live (non-dead) process.
         * Dead processes are removed from processStates entirely, so any entry means alive.
         * Synthetic subagent states are skipped: they are display plumbing, not
         * real processes (hidden sessions DO count here — this is a safety guard,
         * not a user-facing counter, and their processes are just as live).
         * Used by the Settings panel to prevent disabling a provider that is still in use.
         * @returns {function(string): boolean}
         */
        hasActiveSessionForProvider: (state) => (provider) => {
            for (const ps of Object.values(state.processStates)) {
                if (ps?.synthetic) continue
                if (ps?.provider === provider) return true
            }
            return false
        },

        /**
         * Return the lifecycle state of the given provider, falling back to
         * ``'stopped'`` if absent from the map (a provider missing from the
         * snapshot is treated as not yet started, never as undefined).
         */
        getProviderState: (state) => (provider) => state.providerStates[provider] || 'stopped',

        /**
         * Whether the given provider is usable for runtime calls right now.
         * Combines the intent layer (settings store: enabledProviders) with
         * the runtime layer (this store: providerStates === 'running').
         * Used by every UI surface that pilots a provider's SDK/agent:
         * the in-session callout, the agent-settings picker, the rename
         * action, etc. Choices-of-intent UI (default-provider select,
         * settings sections) keep using settings.enabledProviders directly.
         */
        isProviderAvailable() {
            return (provider) => {
                if (!provider) return false
                const settings = useSettingsStore()
                return settings.enabledProviders.includes(provider)
                    && this.getProviderState(provider) === 'running'
            }
        },

        /**
         * Count sessions with unread content across all projects.
         * Same logic as getProjectUnreadCount but without project filter.
         * @returns {number} The number of unread sessions
         */
        getGlobalUnreadCount: (state) => {
            if (hasActiveStartupPhase(state.startupProgress)) return 0
            let count = 0
            for (const session of Object.values(state.sessions)) {
                if (session.hidden) continue
                if (isSessionUnread(session, state.processStates[session.id])) count++
            }
            return count
        },

        // Startup progress getters — aggregate per-phase across every
        // provider that has reported progress. ``current`` and ``total``
        // are summed; ``completed`` is true only when every provider in
        // the phase has reported completion.
        initialSyncProgress: (state) => aggregatePhase(state.startupProgress.initial_sync),
        backgroundComputeProgress: (state) => aggregatePhase(state.startupProgress.background_compute),
        searchIndexProgress: (state) => aggregatePhase(state.startupProgress.search_index),
        isStartupInProgress: (state) => hasActiveStartupPhase(state.startupProgress),
        isInitialSyncInProgress: (state) => {
            const byProvider = state.startupProgress.initial_sync
            if (!byProvider) return false
            return Object.values(byProvider).some(p => p && !p.completed)
        },

        // Local state getters - loading
        isProjectsListLoading: (state) => state.localState.projectsList.loading,
        areSessionsLoading: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsLoading ?? false,
        areSessionItemsLoading: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsLoading ?? false,

        // Local state getters - errors
        didProjectsListFailToLoad: (state) => state.localState.projectsList.loadingError,
        didSessionsFailToLoad: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsLoadingError ?? false,
        didSessionItemsFailToLoad: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsLoadingError ?? false,

        // Local state getters - fetched
        areProjectSessionsFetched: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsFetched ?? false,
        areAllProjectsSessionsFetched: (state) =>
            state.localState.projects[ALL_PROJECTS_ID]?.sessionsFetched ?? false,
        areSessionItemsFetched: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsFetched ?? false,

        // Local state getters - pagination
        hasMoreSessions: (state) => (projectId) =>
            state.localState.projects[projectId]?.hasMoreSessions ?? true,

        // Get expanded groups for a session (returns array)
        getExpandedGroups: (state) => (sessionId) =>
            state.localState.sessionExpandedGroups[sessionId] || [],

        // Check if a group is expanded
        isGroupExpanded: (state) => (sessionId, groupHeadLineNum) => {
            const groups = state.localState.sessionExpandedGroups[sessionId]
            return groups ? groups.includes(groupHeadLineNum) : false
        },

        // Get expanded internal groups for a specific item in a session
        getInternalExpandedGroups: (state) => (sessionId, lineNum) => {
            const sessionGroups = state.localState.sessionInternalExpandedGroups[sessionId]
            if (!sessionGroups) return []
            return sessionGroups[lineNum] || []
        },

        // Check if an internal group is expanded
        isInternalGroupExpanded: (state) => (sessionId, lineNum, startIndex) => {
            const sessionGroups = state.localState.sessionInternalExpandedGroups[sessionId]
            if (!sessionGroups) return false
            const itemGroups = sessionGroups[lineNum]
            return itemGroups ? itemGroups.includes(startIndex) : false
        },

        // Get a single item by lineNum (handles 1-based to 0-based conversion)
        getSessionItem: (state) => (sessionId, lineNum) => {
            const items = state.sessionItems[sessionId]
            if (!items || lineNum < 1) return null
            return items[lineNum - 1] || null
        },

        // Get visual items for a session
        getSessionVisualItems: (state) => (sessionId) =>
            state.localState.sessionVisualItems[sessionId] || [],

        // Check if a conversation block is in detailed mode
        isBlockDetailed: (state) => (sessionId, userMessageLineNum) => {
            const blocks = state.localState.sessionDetailedBlocks[sessionId]
            return blocks ? blocks.includes(userMessageLineNum) : false
        },

        // Effective display mode for a session: a per-session debug override
        // (dev-mode header toggle) wins over the global display mode.
        getEffectiveDisplayMode: (state) => (sessionId) =>
            state.localState.sessionDebugOverride[sessionId]
                ? DISPLAY_MODE.DEBUG
                : useSettingsStore().getDisplayMode,

        // Whether the session has the debug view forced (drives the header toggle's active state).
        isSessionDebugForced: (state) => (sessionId) =>
            !!state.localState.sessionDebugOverride[sessionId],

        // Get open tabs for a session
        getSessionOpenTabs: (state) => (sessionId) =>
            state.localState.sessionOpenTabs[sessionId] || null,

        // Get the dockable layout intention for a session (or null if untouched)
        getSessionLayout: (state) => (sessionId) =>
            state.localState.sessionLayout[sessionId] || null,

        // The catalog template (structure only) of a session's current layout — for "Save layout".
        getSessionLayoutTemplate: (state) => (sessionId) =>
            layoutTemplate(state.localState.sessionLayout[sessionId]),

        // Get cached agent link for a tool_id in a session
        // Returns: { agentId, isBackground } or undefined (not in cache)
        getAgentLink: (state) => (sessionId, toolId) => {
            const sessionLinks = state.localState.agentLinks[sessionId]
            if (!sessionLinks) return undefined
            return sessionLinks[toolId]
        },

        /** run_id for a Workflow tool_use, or undefined until its link is known. */
        getWorkflowLink: (state) => (sessionId, toolId) => {
            const sessionLinks = state.localState.workflowLinks[sessionId]
            if (!sessionLinks) return undefined
            return sessionLinks[toolId]
        },

        /** Reverse lookup: find the agent link in the parent session that spawned a given subagent. */
        getAgentLinkByAgentId: (state) => (parentSessionId, subagentSessionId) => {
            const sessionLinks = state.localState.agentLinks[parentSessionId]
            if (!sessionLinks) return null
            for (const link of Object.values(sessionLinks)) {
                if (link.agentId === subagentSessionId) return link
            }
            return null
        },

        /** Reverse lookup: find the tool_use line number in the parent session that spawned a given subagent. */
        getAgentToolUseLineNum: (state) => (parentSessionId, subagentSessionId) => {
            const sessionLinks = state.localState.agentLinks[parentSessionId]
            if (!sessionLinks) return null
            for (const link of Object.values(sessionLinks)) {
                if (link.agentId === subagentSessionId) return link.toolUseLineNum ?? null
            }
            return null
        },

        // Get tool state for a tool_use_id in a session
        // Returns: { resultCount, completedAt, error, extra, toolResultLineNums } or null
        getToolState: (state) => (sessionId, toolUseId) => {
            const sessionStates = state.localState.toolStates[sessionId]
            if (!sessionStates) return null
            return sessionStates[toolUseId] || null
        },

        // Check if an item arrived via WebSocket (live, real-time)
        isItemLive: (state) => (sessionId, lineNum) => {
            return !!state.localState.liveItems[sessionId]?.has(lineNum)
        },

        // Check if a wa-details panel is open (persisted across virtual scroller cycles)
        isDetailOpen: (state) => (sessionId, key) => {
            return !!state.localState.openDetails[sessionId]?.[key]
        },

        // Get draft message for a session
        getDraftMessage: (state) => (sessionId) =>
            state.localState.draftMessages[sessionId] || null,

        // Get the append signal for a session (see draftAppendSignals)
        getDraftAppendSignal: (state) => (sessionId) =>
            state.localState.draftAppendSignals[sessionId] || 0,

        // Get stored title suggestion for a session
        getTitleSuggestion: (state) => (sessionId) =>
            state.localState.titleSuggestions[sessionId]?.suggestion || null,

        // Get the full title suggestion entry (to distinguish "no response yet" from "failed")
        getTitleSuggestionEntry: (state) => (sessionId) =>
            state.localState.titleSuggestions[sessionId] || null,

        // Get the source prompt used for a suggestion (for draft invalidation)
        getTitleSuggestionSourcePrompt: (state) => (sessionId) =>
            state.localState.titleSuggestions[sessionId]?.sourcePrompt || null,

        // Get attachments for a session as an array (preserving order from Map)
        getAttachments: (state) => (sessionId) => {
            const map = state.localState.attachments[sessionId]
            return map ? Array.from(map.values()) : []
        },

        // Get attachment count for a session
        getAttachmentCount: (state) => (sessionId) => {
            const map = state.localState.attachments[sessionId]
            return map ? map.size : 0
        },

        // A failed send entry (red bubble in the conversation flow)
        getFailedSend: (state) => (sessionId, requestId) =>
            state.localState.failedSends[sessionId]?.[requestId] || null,

        // Whether an existing SDK session has a staged (pending, not-yet-applied)
        // hybrid switch — committed on the next Send/Apply, droppable until then.
        isHybridStaged: (state) => (sessionId) =>
            state.localState.stagedHybrid[sessionId] === true,

        // Whether any files are currently being processed (encoded/resized) for a session
        isProcessingAttachments: (state) => (sessionId) => {
            return (state.localState.processingAttachments[sessionId] || 0) > 0
        },

        // Get display name for a project (uses cache, computes if missing)
        getProjectDisplayName: (state) => (projectId) => {
            // Return from cache if available
            if (state.localState.projectDisplayNames[projectId]) {
                return state.localState.projectDisplayNames[projectId]
            }

            // Compute and cache
            const project = state.projects[projectId]
            if (!project) return projectId // Fallback to raw ID if project not loaded

            let displayName

            if (project.name) {
                // 1. User-defined name takes priority
                displayName = project.name
            } else if (project.directory) {
                // 2. Last part of directory path
                const parts = project.directory.split('/')
                displayName = parts[parts.length - 1] || project.directory
            } else {
                // 3. Last part of ID after dashes
                const parts = project.id.split('-')
                displayName = parts[parts.length - 1] || project.id
            }

            // Cache it
            state.localState.projectDisplayNames[projectId] = displayName
            return displayName
        }
    },

    actions: {
        // Provider lifecycle state — written from bootstrap (snapshot) and
        // from `provider_state_changed` WS pushes (live transitions).
        applyProviderStates(providerStates) {
            if (!providerStates || typeof providerStates !== 'object') return
            this.providerStates = { ...providerStates }
        },
        setProviderState(provider, state) {
            if (!provider || !state) return
            this.providerStates = { ...this.providerStates, [provider]: state }
        },

        // Server info
        setCurrentVersion(version) {
            this.currentVersion = version
        },
        setPreviousChangelogVersion(version) {
            this.previousChangelogVersion = version
        },
        setPendingChangelogVersion(version) {
            this.pendingChangelogVersion = version
        },
        clearPendingChangelogVersion() {
            this.pendingChangelogVersion = null
        },
        setHybridAnnouncementActive(active) {
            this.hybridAnnouncementActive = active
        },
        setTelemetryNoticeActive(active) {
            this.telemetryNoticeActive = active
        },
        setLatestVersion(version, releaseUrl) {
            this.latestVersion = { version, releaseUrl }
        },

        // Startup progress
        setStartupProgress(provider, phase, current, total, completed) {
            const key = provider ?? '__global__'
            this.startupProgress = {
                ...this.startupProgress,
                [phase]: {
                    ...(this.startupProgress[phase] || {}),
                    [key]: { current, total, completed },
                },
            }
        },

        // Projects
        addProject(project) {
            // Upsert via updateProject so a project_created that happens to target
            // an already-known project replaces fields instead of deep-merging
            // (see updateProject's note on removed nested keys).
            this.updateProject(project)
        },
        updateProject(project) {
            // Replace fields wholesale — do NOT $patch here. $patch DEEP-merges,
            // which keeps keys that were REMOVED from a nested object. Concretely:
            // setting a default_agent_settings field back to "inherit" drops it
            // from the bundle, but as long as another field keeps the bundle
            // non-null, the deep-merge would preserve the removed key with its
            // old value — so the project edit dialog re-shows it instead of
            // "Inherit". Object.assign replaces each top-level key (nested objects
            // by reference, so removals propagate), while preserving any
            // client-only keys the payload doesn't carry.
            const existing = this.projects[project.id]
            if (existing) Object.assign(existing, project)
            else this.projects[project.id] = project
            // Invalidate display name cache so it gets recomputed
            delete this.localState.projectDisplayNames[project.id]
        },
        /**
         * Set the archived state of a project.
         * @param {string} projectId - The project ID
         * @param {boolean} archived - Whether to archive or unarchive
         * @throws {Error} If the update fails
         */
        async setProjectArchived(projectId, archived) {
            // Optimistic update
            const project = this.projects[projectId]
            const oldArchived = project?.archived

            if (project) {
                project.archived = archived
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ archived })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update project')
                }

                const updatedProject = await response.json()
                this.updateProject(updatedProject)

            } catch (error) {
                // Rollback on error
                if (project && oldArchived !== undefined) {
                    project.archived = oldArchived
                }
                throw error
            }
        },

        // Sessions
        addSession(session) {
            this.$patch({ sessions: { [session.id]: session } })
            this._hydrateSessionLayoutFromPersisted(session.id, session.layout)
            this.tryFinalizePendingBinding(session.id)
            this._tryLinkPeerDelivery(session)
        },
        updateSession(session) {
            // When lifecycle timestamps change, clean up stale synthetic process states
            // for child agents that predate the new cutoff
            const prev = this.sessions[session.id]
            if (prev && (prev.last_started_at !== session.last_started_at ||
                         prev.last_stopped_at !== session.last_stopped_at)) {
                this._cleanStaleChildSynthetics(session)
            }
            // Safety net: a subagent reported stopped clears its own synthetic
            // "running" state. The timestamp guard keeps a fresh synthetic (agent
            // relaunched) safe from a stale session_updated of a previous run.
            const ownSynthetic = this.processStates[session.id]
            if (ownSynthetic?.synthetic && session.last_stopped_at) {
                const stoppedMs = Date.parse(session.last_stopped_at)
                const startedMs = ownSynthetic.started_at ? ownSynthetic.started_at * 1000 : 0
                if (!Number.isNaN(stoppedMs) && stoppedMs >= startedMs) {
                    this.removeSyntheticProcessState(session.id)
                }
            }
            // Never let last_new_content_at regress — an optimistic value (set when
            // process_state exits assistant_turn) can be overwritten by a stale
            // session_updated broadcast from the file watcher.
            if (prev?.last_new_content_at && session.last_new_content_at &&
                session.last_new_content_at < prev.last_new_content_at) {
                session = { ...session, last_new_content_at: prev.last_new_content_at }
            }
            this.$patch({ sessions: { [session.id]: session } })
            // A session that finishes (re)computing AFTER its items were already
            // loaded now carries its ToolResultLink / AgentLink / workflow rows,
            // but the tool_state / agent_link broadcasts that stop the spinners
            // were dropped (emitted before the rows existed, or never emitted by
            // the batch compute). Re-pull the link caches so spinners settle.
            // Edge-guarded (false→true) + items already loaded: fires once, only
            // where a spinner can show, and never overlaps SessionItemsList's
            // first-load fetch (which runs only when items are NOT yet fetched).
            if (prev && prev.compute_version_up_to_date === false &&
                session.compute_version_up_to_date === true &&
                this.localState.sessions[session.id]?.itemsFetched) {
                this.refreshSessionToolStates(session.project_id, session.id).catch(() => {})
            }
            // Re-seed the live layout working copy from the persisted Session.layout (initial load /
            // cross-device sync), unless we have an unsaved local edit in flight (guarded inside).
            this._hydrateSessionLayoutFromPersisted(session.id, session.layout)
            this.tryFinalizePendingBinding(session.id)
            this._tryLinkPeerDelivery(session)
        },
        /**
         * Remove a session from the store by id.
         * Called when the backend emits ``session_removed`` (e.g. a session
         * was hidden and the REST API no longer serves it). Delegates the
         * derived-state cleanup (sessionItems, expandedGroups, agentLinks,
         * etc.) to ``unloadSession`` so the same teardown logic is shared
         * with reconciliation-driven unloads; then drops the row itself
         * from ``sessions`` and removes it from the MRU.
         * @param {string} sessionId
         */
        removeSession(sessionId) {
            this.unloadSession(sessionId)
            delete this.sessions[sessionId]
            this.removeMruSession(sessionId)
        },
        /**
         * Resolve a project's inherited agent-settings defaults into a CONCRETE
         * 7-field bundle to FREEZE onto a new draft (the snapshot taken at draft
         * creation — see docs/plans/2026-06-09-project-agent-defaults-design.md).
         * Field by field: the project chain's value (worktree / path ancestors)
         * wins, else the provider's global default. The model is the alias (e.g.
         * "opus"), never a pinned version, so version auto-upgrade is preserved.
         * Fields the provider's global store doesn't expose resolve to null (e.g.
         * thinking / chrome / fast for Codex) — the backend treats those as
         * unsupported anyway.
         * @param {string} projectId
         * @param {string} provider - Wire key of the session's provider
         * @returns {Object} the 7 agent-settings fields, all concrete or null
         */
        _resolveDraftAgentSettings(projectId, provider, trustState = undefined) {
            const chain = resolveProjectAgentDefaults(projectId, provider, this.projects)
            const pStore = getProviderStore(provider)
            // Trust-dependent permission seed (trust design §13.3): a project
            // whose effective trust is NOT trusted (untrusted or unknown) seeds
            // from the `permission_mode_if_untrusted` chain — same inheritance,
            // different field — falling back to the global untrusted default.
            // `trustState` (from the trust gate) is AUTHORITATIVE when provided:
            // the store may not have caught up with a backend seed yet (the
            // project_updated broadcast races this call).
            const state = trustState !== undefined
                ? trustState
                : resolveProjectTrust(projectId, this.projects).state
            const untrusted = state !== true
            const permissionMode = untrusted
                ? (chain.permission_mode_if_untrusted ?? pStore?.defaultUntrustedPermissionMode ?? null)
                : (chain.permission_mode ?? pStore?.defaultPermissionMode ?? null)
            return {
                selected_model: chain.selected_model ?? pStore?.defaultModel ?? null,
                permission_mode: permissionMode,
                effort: chain.effort ?? pStore?.defaultEffort ?? null,
                thinking_enabled: chain.thinking_enabled ?? pStore?.defaultThinking ?? null,
                claude_in_chrome: chain.claude_in_chrome ?? pStore?.defaultClaudeInChrome ?? null,
                fast_mode: chain.fast_mode ?? pStore?.defaultFastMode ?? null,
                context_max: chain.context_max ?? pStore?.defaultContextMax ?? null,
            }
        },

        /** Pick the 7 agent-settings fields off a (draft) session object. */
        _pickAgentSettings(session) {
            return {
                selected_model: session.selected_model ?? null,
                permission_mode: session.permission_mode ?? null,
                effort: session.effort ?? null,
                thinking_enabled: session.thinking_enabled ?? null,
                claude_in_chrome: session.claude_in_chrome ?? null,
                fast_mode: session.fast_mode ?? null,
                context_max: session.context_max ?? null,
            }
        },

        /** Single writer for a draft's IndexedDB record. ``saveDraftSession`` does a whole-record
         *  ``put`` (overwrites), so every field that must survive a reload — metadata, agent settings,
         *  and the dockable ``layout`` — is snapshotted here from the in-memory session, the single
         *  source of truth. No-op for non-draft sessions. */
        _saveDraftToIndexedDB(sessionId) {
            const s = this.sessions[sessionId]
            if (!s?.draft) return
            saveDraftSession(sessionId, {
                projectId: s.project_id,
                title: s.title,
                provider: s.provider,
                hybrid: s.hybrid,
                // Deep-clone to a plain object: ``s.layout`` lives in Pinia state, so reading it back
                // yields a Vue reactive Proxy, which IndexedDB's structured clone rejects. The layout is
                // pure JSON data (it round-trips to the backend as JSON), so this is exact. null /
                // undefined (legacy / single-pane drafts) pass through untouched.
                layout: s.layout == null ? s.layout : JSON.parse(JSON.stringify(s.layout)),
                // Peer message this draft was created to answer, if any (see
                // `setDraftPeerMessage`): persisted so a reload before the
                // first send does not lose the link.
                peerMessageId: s.peerMessageId,
                ...this._pickAgentSettings(s),
            }).catch((err) =>
                console.warn('Failed to save draft session to IndexedDB:', err),
            )
        },

        /**
         * Create a draft session for a project.
         * Draft sessions exist only in the frontend until the first message is sent.
         * @param {string} projectId - The project ID
         * @param {boolean|null} [trustState] - The project's effective trust as
         *   settled by the trust gate (`ensureProjectTrust(...).state`).
         *   Authoritative over the store's local resolution when provided.
         * @param {string|null} [initialProvider] - Explicit initial provider.
         *   When absent, the project chain and global default select it.
         * @returns {string} The generated session ID (UUID)
         */
        createDraftSession(projectId, trustState = undefined, initialProvider = null) {
            const id = generateUUID()
            const now = Date.now() / 1000  // Unix timestamp in seconds
            // Provider preselect: an explicit caller choice, else the project's
            // inherited default provider, else the global default.
            const provider = resolveDraftProvider(
                projectId,
                this.projects,
                useSettingsStore().defaultProvider,
                initialProvider,
            )
            // Snapshot the resolved agent settings onto the draft (concrete), so
            // launching the session freezes today's project → global defaults
            // regardless of later default changes (option A).
            const settings = this._resolveDraftAgentSettings(projectId, provider, trustState)
            // Hybrid-by-default (synced setting): new Claude Code sessions start in
            // hybrid mode when enabled. Claude Code only — the flag is meaningless
            // for other providers. Existing sessions are never touched. Also gated by
            // the server hybrid feature flag: never seed a hybrid draft while off, or
            // it could never be sent (the backend refuses to launch it).
            const hybrid = provider === 'claude_code'
                && useSettingsStore().isClaudeHybridEnabled
                && useSettingsStore().isClaudeHybridDefault
            // Seed the dockable layout from the resolved project → global default (snapshot at
            // creation, like the agent settings). The draft renders with it immediately; on send it
            // rides the create payload and is frozen onto Session.layout. {} = single pane.
            const layoutId = resolveProjectLayoutId(projectId, this.projects, useSettingsStore().getDefaultLayoutId)
            const layout = useLayoutsStore().intentionForId(layoutId)
            this.sessions[id] = {
                id,
                project_id: projectId,
                provider,
                title: null,  // null = user hasn't set a title yet, UI will display "New session"
                mtime: now,
                last_line: 0,
                draft: true,
                hybrid,
                layout,
                ...settings,
            }
            // Persist to IndexedDB (settings + layout included so the seeded default survives a reload)
            this._saveDraftToIndexedDB(id)
            return id
        },

        /**
         * Remember that this draft was created to receive a peer message
         * delivered "to a new session".
         *
         * The delivery is recorded backend-side with no target: the session
         * has no DB row yet — the provider creates it when the user sends the
         * prefilled composer, and may never (a discarded draft). The link is
         * therefore completed later, by `_tryLinkPeerDelivery`, once the real
         * session lands in the store.
         *
         * @param {string} draftId
         * @param {number|string} peerMessageId - `PeerMessage.id` (REST pk).
         */
        setDraftPeerMessage(draftId, peerMessageId) {
            const session = this.sessions[draftId]
            if (!session?.draft || peerMessageId == null) return
            session.peerMessageId = peerMessageId
            this.localState.pendingPeerDeliveries[draftId] = peerMessageId
            this._saveDraftToIndexedDB(draftId)
        },

        /**
         * Record the delivery target of a peer message whose session finally
         * exists. No-op unless that exact session was awaited.
         *
         * Fire-and-forget: the backend fills an EMPTY link only, so a failure
         * (offline, message redelivered elsewhere meanwhile) costs nothing but
         * a blank target in the inbox — never a wrong one.
         *
         * @param {Object} session - The session that just landed in the store.
         */
        _tryLinkPeerDelivery(session) {
            if (!session || session.draft) return
            const messageId = this.localState.pendingPeerDeliveries[session.id]
            if (messageId == null) return
            delete this.localState.pendingPeerDeliveries[session.id]
            apiFetch(`/api/peer-messages/${messageId}/link-session/`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: session.id }),
            }).catch((err) =>
                console.warn('[peer] could not record the delivery target:', err),
            )
        },

        /**
         * Toggle the hybrid CLI mode flag on a draft session (free both ways
         * while still a draft — it only becomes one-way once the session is
         * created). No-op for non-draft sessions: existing sessions switch
         * through the one-way `set_session_hybrid` WS command instead.
         * @param {string} sessionId
         * @param {boolean} value
         */
        setDraftHybrid(sessionId, value) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return
            session.hybrid = !!value
            this._saveDraftToIndexedDB(sessionId)
        },

        /**
         * Change the provider of an existing draft session, re-snapshotting the
         * agent settings to the new provider's resolved defaults (project chain →
         * global). No-op if the session is not a draft. Persists provider +
         * settings to IndexedDB so they survive reloads. The popover additionally
         * re-pins its live refs via ``resetAllToDefaults`` for an immediate update.
         * @param {string} sessionId
         * @param {string} provider - Wire key of the new provider
         */
        setDraftProvider(sessionId, provider) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return
            if (session.provider === provider) return
            session.provider = provider
            // Re-snapshot the agent settings for the NEW provider (the previous
            // bundle held the old provider's resolved defaults).
            const settings = this._resolveDraftAgentSettings(session.project_id, provider)
            Object.assign(session, settings)
            // Re-resolve the hybrid flag for the new provider: it is Claude Code
            // only, so switching to another provider clears it; switching to
            // Claude Code applies the hybrid-by-default setting (when the server
            // hybrid feature flag is on).
            session.hybrid = provider === 'claude_code'
                && useSettingsStore().isClaudeHybridEnabled
                && useSettingsStore().isClaudeHybridDefault
            this._saveDraftToIndexedDB(sessionId)
        },

        /**
         * Write the closed agent-settings bundle onto a draft session and persist it to IndexedDB.
         * Drafts have no "Apply" step — every popover change is live — and unlike real sessions (whose
         * choices ride the WS Send/Apply payload) a draft must persist them itself, or they reset to the
         * creation-time snapshot on reload. No-op for non-draft sessions.
         * @param {string} sessionId
         * @param {Object} settings - The 7-field bundle (extra keys ignored by ``_pickAgentSettings``).
         */
        setDraftAgentSettings(sessionId, settings) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return
            Object.assign(session, this._pickAgentSettings(settings))
            this._saveDraftToIndexedDB(sessionId)
        },

        /**
         * Delete a draft session from IndexedDB, and optionally from store.
         * Only deletes if the session exists and has draft: true.
         * @param {string} sessionId - The session ID to delete
         * @param {Object} options - Options
         * @param {boolean} options.keepInStore - If true (the draft is being
         *   promoted to a real session on send), only drop the IndexedDB record:
         *   keep the live session in the store AND its MRU slot — it stays a
         *   valid navigation target.
         */
        deleteDraftSession(sessionId, { keepInStore = false } = {}) {
            if (this.sessions[sessionId]?.draft) {
                // A discarded draft never becomes a session, so a peer delivery
                // waiting on it waits forever: drop it. `keepInStore` is the
                // opposite case — the draft was just SENT and is about to
                // become the very session we are waiting for.
                if (!keepInStore) {
                    delete this.localState.pendingPeerDeliveries[sessionId]
                    // Removing the MRU entry is the navigational half of "this
                    // session leaves the store" — keep it paired with the delete
                    // (same as removeSession). When keepInStore promotes the draft
                    // to a real session, the slot must survive, so it is NEVER
                    // dropped here; the canonical-id rekey happens in
                    // bindDraftSession for providers that reassign the id (Codex).
                    delete this.sessions[sessionId]
                    this.removeMruSession(sessionId)
                }
                // Delete from IndexedDB
                deleteDraftSessionFromDb(sessionId).catch(err =>
                    console.warn('Failed to delete draft session from IndexedDB:', err)
                )
            }
        },

        /**
         * Bind a local draft session to its canonical id, once both the
         * `session_bound` WS message and the canonical session itself are
         * available in the store. Decisions are taken at this point, not when
         * `session_bound` arrived: if the user has navigated away from the
         * draft in the meantime, no redirect happens.
         *
         * No-op when `draftId === sessionId`: the provider accepted the
         * client-supplied id (Claude Code) and the existing `session_updated`
         * path will upgrade the draft entry in place.
         *
         * @param {string} draftId - The local draft session id (URL key).
         * @param {string} sessionId - The provider's canonical session id.
         */
        async bindDraftSession(draftId, sessionId) {
            delete this.localState.pendingDraftBindings[draftId]

            // A peer delivery waiting on this draft follows it to the canonical
            // id. When the canonical session is ALREADY in the store, no
            // addSession/updateSession will fire again — link it right here.
            const pendingPeer = this.localState.pendingPeerDeliveries[draftId]
            if (pendingPeer != null && draftId !== sessionId) {
                delete this.localState.pendingPeerDeliveries[draftId]
                this.localState.pendingPeerDeliveries[sessionId] = pendingPeer
            }
            this._tryLinkPeerDelivery(this.sessions[sessionId])

            if (draftId === sessionId) {
                return
            }

            // Carry the optimistic user message over to the canonical key so
            // it stays visible across the router.replace below. Without this
            // migration, the bubble would disappear the moment the URL flips
            // (optimisticMessages is keyed by sessionId, so the draft entry
            // becomes orphan) and reappear only when the watcher catches the
            // real user_message from the JSONL — a short flicker we want to
            // avoid. ``addSessionItems`` clears the entry as soon as the real
            // message arrives, so this is purely about closing the gap.
            const optimistic = this.localState.optimisticMessages[draftId]
            if (optimistic) {
                this.localState.optimisticMessages[sessionId] = optimistic
                delete this.localState.optimisticMessages[draftId]
                this.recomputeVisualItems(sessionId)
            }

            // Rehome the optimistic ``starting`` process state onto the canonical
            // key. The send path sets it under the only id it has — the draft id
            // (see ``setProcessState(sessionId, …, STARTING)`` in the send flow) —
            // but the backend broadcasts every real state (starting → … → dead)
            // under the canonical id the provider mints. When the two differ
            // (Codex), the draft-keyed entry is never updated nor removed: no
            // ``dead`` ever arrives for it, and nothing else here touches
            // ``processStates``. It then lingers as a phantom that inflates every
            // active-process count until the next full reload. Move it to the
            // canonical key if the backend's own entry has not landed yet (keeps
            // the "starting" feedback flicker-free), else just drop it — the
            // backend entry is authoritative.
            const draftProcessState = this.processStates[draftId]
            if (draftProcessState) {
                if (!this.processStates[sessionId]) {
                    this.processStates[sessionId] = { ...draftProcessState }
                }
                delete this.processStates[draftId]
            }

            // Same rekey for an already-arrived title suggestion. The
            // ``suggest_title`` request was sent under the draft id (only id
            // known at request time), so a fast response can have landed in
            // ``titleSuggestions[draftId]`` before this bind runs. Move it to
            // the canonical key so the SessionView watcher — which queries by
            // canonical id after the router.replace below — picks it up.
            const titleSuggestion = this.localState.titleSuggestions[draftId]
            if (titleSuggestion) {
                this.localState.titleSuggestions[sessionId] = titleSuggestion
                delete this.localState.titleSuggestions[draftId]
            }

            // Same migration for an in-flight auto-apply intent — the
            // App-level watcher is observing the draft id at the moment of
            // bind, so the entry must follow the session to its canonical id.
            const pendingAuto = this.localState.pendingTitleAutoApply[draftId]
            if (pendingAuto) {
                this.localState.pendingTitleAutoApply[sessionId] = pendingAuto
                delete this.localState.pendingTitleAutoApply[draftId]
            }

            // Late ``title_suggested`` messages may still arrive with the
            // draft id long after this bind has finished, so register a
            // forwarding alias that ``handleTitleSuggested`` will resolve.
            this.localState.draftAliases[draftId] = sessionId

            // Carry the draft's MRU slot over to the canonical id (the id segment
            // is rewritten inside the stored path) so the freshly-created session
            // stays reachable in the Ctrl+` switcher no matter where the user is.
            // The router.replace below only re-touches the MRU while still on the
            // draft, so without this a navigated-away session would be orphaned.
            // Runs before the replace: touchMruPath dedups by id, collapsing the
            // two into a single canonical entry in the on-draft case.
            this.rekeyMruSession(draftId, sessionId)

            const { router } = await import('../router')
            const onDraft = router.currentRoute.value.params.sessionId === draftId

            if (onDraft) {
                const currentRoute = router.currentRoute.value
                await router.replace({
                    name: currentRoute.name,
                    params: { ...currentRoute.params, sessionId },
                    query: currentRoute.query,
                })
            }

            this.deleteDraftSession(draftId)
        },

        /**
         * Check whether a pending draft binding targets the given session id
         * and, if so, finalize it. Called from `addSession` / `updateSession`
         * after the store patch so the canonical session is already visible
         * to `bindDraftSession`.
         * @param {string} sessionId - The session that just landed in the store.
         */
        tryFinalizePendingBinding(sessionId) {
            const pending = this.localState.pendingDraftBindings
            for (const draftId of Object.keys(pending)) {
                if (pending[draftId] === sessionId) {
                    this.bindDraftSession(draftId, sessionId)
                    return
                }
            }
        },

        /**
         * Initialize session items array with placeholders.
         * Placeholders are objects with only line_num (no content).
         * @param {string} sessionId
         * @param {number} lastLine - Total number of lines (session.last_line)
         */
        initSessionItems(sessionId, lastLine) {
            if (this.sessionItems[sessionId]) return // Already initialized

            this.sessionItems[sessionId] = Array.from(
                { length: lastLine },
                (_, index) => ({ line_num: index + 1 }) // line_num is 1-based
            )
        },

        /**
         * Add or update session items in the array.
         * Items are placed at their correct index (line_num - 1).
         * If items arrive beyond current array size, extends with placeholders.
         * @param {string} sessionId
         * @param {Array<{line_num: number, content: string}>} newItems
         * @param {Array<{line_num: number, display_level: number, group_head: number|null, group_tail: number|null, kind: string|null}>|null} updatedMetadata - Metadata of pre-existing items that were modified
         */
        addSessionItems(sessionId, newItems, updatedMetadata = null) {
            let targetArray = this.sessionItems[sessionId]

            // First, apply metadata updates to pre-existing items
            if (updatedMetadata?.length && targetArray) {
                for (const update of updatedMetadata) {
                    const index = update.line_num - 1
                    const existingItem = targetArray[index]
                    if (!existingItem) continue

                    // For user_message or assistant_message that acquires a group_tail,
                    // check if we need to migrate internal suffix expansion to external group
                    if (existingItem.kind === 'user_message' || existingItem.kind === 'assistant_message') {
                        const hadGroupTail = existingItem.group_tail != null
                        const willHaveGroupTail = update.group_tail != null
                        if (!hadGroupTail && willHaveGroupTail && hasContent(existingItem)) {
                            this._migrateInternalSuffixToExternal(sessionId, update.line_num, existingItem)
                        }
                    }

                    // Apply all metadata fields
                    existingItem.display_level = update.display_level
                    existingItem.group_head = update.group_head
                    existingItem.group_tail = update.group_tail
                    existingItem.kind = update.kind
                    if (update.timestamp !== undefined) {
                        existingItem.timestamp = update.timestamp
                    }
                }
            }

            // Then add new items
            if (!newItems?.length) {
                // Even with no new items, metadata updates may require recompute
                if (updatedMetadata?.length) {
                    this.recomputeVisualItems(sessionId)
                }
                return
            }

            if (!targetArray) {
                // Not initialized yet - create array from the items we have
                // Find max line_num to know array size
                const maxLineNum = Math.max(...newItems.map(item => item.line_num))
                targetArray = this.sessionItems[sessionId] = Array.from(
                    { length: maxLineNum },
                    (_, index) => ({ line_num: index + 1 })
                )
            }

            let extendedOverGap = false
            for (const item of newItems) {
                const index = item.line_num - 1 // line_num is 1-based, array is 0-based

                // Extending by MORE than the item's own slot means placeholders
                // are created for intermediate lines this iteration won't fill.
                if (targetArray.length < index) {
                    extendedOverGap = true
                }

                // Extend array with placeholders if needed
                while (targetArray.length <= index) {
                    targetArray.push({ line_num: targetArray.length + 1 })
                }

                // Place item at correct index
                targetArray[index] = item
            }

            // Extending jumped over lines this batch may not fill (typical after
            // a reconnect: the broadcasts for those lines were lost during the
            // outage). The bare placeholders created above carry no display_level,
            // so the scroller's gap-fill can never see them — heal in the
            // background (no-op if the batch itself filled every created slot).
            if (extendedOverGap) {
                this.ensureSessionItemsCoverage(sessionId).catch(() => {})
            }

            // Resolve in-flight send snapshots whose user_message line just
            // arrived (live deliveries come through here, not through
            // clearOptimisticMessageIfMatched — without this, successful
            // sends would keep their persisted snapshot until the audit
            // mistakes them for lost ones).
            this.resolveInflightSends(sessionId, newItems)

            // Clear optimistic message when a real user_message arrives from the backend
            if (this.localState.optimisticMessages[sessionId] &&
                newItems.some(item => item.kind === 'user_message')) {
                delete this.localState.optimisticMessages[sessionId]
            }

            // Retire streaming blocks whose real items have arrived
            this._retireStreamingBlocks(sessionId, newItems)

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Migrate internal suffix expansion state to external group expansion.
         *
         * When an ALWAYS item with an internal suffix acquires a group_tail (because
         * a COLLAPSIBLE item arrived after it), the suffix becomes external.
         * If the user had expanded that internal suffix, we need to migrate
         * that expansion state to the session-level expanded groups.
         *
         * @param {string} sessionId
         * @param {number} lineNum - The line_num of the ALWAYS item
         * @param {Object} item - The session item object
         * @private
         */
        _migrateInternalSuffixToExternal(sessionId, lineNum, item) {
            // Check if there are any internal expanded groups for this item
            const itemInternalGroups = this.localState.sessionInternalExpandedGroups[sessionId]?.[lineNum]
            if (!itemInternalGroups?.length) return

            // Parse content to find the suffix boundaries
            const parsed = getParsedContent(item)
            if (!parsed) return

            const content = parsed?.message?.content
            if (!Array.isArray(content) || content.length === 0) return

            // Use getPrefixSuffixBoundaries with groupTail=true to find where suffix would start
            // (we pass a truthy value for groupTail since we're checking what WILL become external)
            const { suffixStartIndex } = getPrefixSuffixBoundaries(content, null, true)
            if (suffixStartIndex == null) return

            // Check if the suffix was expanded as an internal group
            if (itemInternalGroups.includes(suffixStartIndex)) {
                // Migrate: add to session-level expanded groups
                if (!this.localState.sessionExpandedGroups[sessionId]) {
                    this.localState.sessionExpandedGroups[sessionId] = []
                }
                if (!this.localState.sessionExpandedGroups[sessionId].includes(lineNum)) {
                    this.localState.sessionExpandedGroups[sessionId].push(lineNum)
                }

                // Remove from internal groups
                const idx = itemInternalGroups.indexOf(suffixStartIndex)
                if (idx >= 0) {
                    itemInternalGroups.splice(idx, 1)
                }
            }
        },

        // Initial loading from API

        /**
         * Load all projects from the API.
         * @param {Object} options
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         * @returns {Promise<Set<string>>} Set of project IDs that have changed
         *          (projects where sessionsFetched=true AND mtime changed or new)
         */
        async loadProjects({ isInitialLoading = false } = {}) {
            const changedIds = new Set()
            this.localState.projectsList.loading = true
            try {
                const res = await apiFetch('/api/projects/')
                if (!res.ok) {
                    console.error('Failed to load projects:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.projectsList.loadingError = true
                    }
                    return changedIds
                }
                const freshProjects = await res.json()
                for (const fresh of freshProjects) {
                    const local = this.projects[fresh.id]
                    const wasSessionsFetched = this.localState.projects[fresh.id]?.sessionsFetched

                    // Project changed if: sessionsFetched AND (new OR mtime different)
                    if (wasSessionsFetched && (!local || local.mtime !== fresh.mtime)) {
                        changedIds.add(fresh.id)
                    }

                    // Update store
                    this.projects[fresh.id] = fresh
                }
                // Success: clear any previous error
                this.localState.projectsList.loadingError = false
                return changedIds
            } catch (error) {
                console.error('Failed to load projects:', error)
                if (isInitialLoading) {
                    this.localState.projectsList.loadingError = true
                }
                throw error  // Re-throw for reconciliation retry logic
            } finally {
                this.localState.projectsList.loading = false
            }
        },
        /**
         * Load home page data: projects with weekly activity.
         * Calls /api/home/ which returns projects and weekly activity in one request.
         * Weekly activity is stored separately in weeklyActivity (not on project objects).
         */
        async loadHomeData() {
            // Only show loading indicator on initial load, not on background
            // refreshes (e.g. startup polling) — otherwise the project list
            // flashes away and back on every poll tick.
            const isInitialLoad = Object.keys(this.projects).length === 0
            if (isInitialLoad) {
                this.localState.projectsList.loading = true
            }
            try {
                const res = await apiFetch('/api/home/')
                if (!res.ok) {
                    console.error('Failed to load home data:', res.status, res.statusText)
                    if (isInitialLoad) {
                        this.localState.projectsList.loadingError = true
                    }
                    return
                }
                const data = await res.json()

                // Update projects and weekly activity (strip weekly_activity
                // from project objects, compare before updating to avoid
                // unnecessary re-renders of chart components).
                for (const fresh of data.projects) {
                    const { weekly_activity, ...projectData } = fresh
                    this.projects[projectData.id] = projectData
                    const activity = weekly_activity || []
                    if (JSON.stringify(activity) !== JSON.stringify(this.weeklyActivity[projectData.id])) {
                        this.weeklyActivity[projectData.id] = activity
                    }
                }

                // Store global weekly activity (compare before updating)
                const globalActivity = data.global_weekly_activity || []
                if (JSON.stringify(globalActivity) !== JSON.stringify(this.weeklyActivity._global)) {
                    this.weeklyActivity._global = globalActivity
                }

                this.localState.projectsList.loadingError = false
            } catch (error) {
                console.error('Failed to load home data:', error)
                if (isInitialLoad) {
                    this.localState.projectsList.loadingError = true
                }
            } finally {
                if (isInitialLoad) {
                    this.localState.projectsList.loading = false
                }
            }
        },
        /**
         * Ensure localState.projects[projectId] exists with all pagination fields.
         * @param {string} projectId - Project ID or ALL_PROJECTS_ID
         * @returns {Object} The project's local state object
         * @private
         */
        _ensureProjectLocalState(projectId) {
            if (!this.localState.projects[projectId]) {
                this.localState.projects[projectId] = {
                    sessionsFetched: false,
                    sessionsLoading: false,
                    sessionsLoadingError: false,
                    hasMoreSessions: true,
                    oldestSessionMtime: null,
                }
            }
            return this.localState.projects[projectId]
        },

        /**
         * Fetch a page of sessions from the API.
         * @param {string} projectId - Project ID or ALL_PROJECTS_ID
         * @returns {Promise<{sessions: Array, has_more: boolean}>}
         * @private
         */
        async _fetchSessionsPage(projectId) {
            const state = this._ensureProjectLocalState(projectId)

            // Build URL based on project type. A single real project always uses
            // its own endpoint, which already returns the project's sessions AND
            // its git worktrees' in one response (their sessions belong to the
            // whole — like a workspace aggregates its members, one level down);
            // the worktree scope is resolved server-side, so no scope routing or
            // second call is needed here. The all-projects / workspace pseudo-ids
            // use the multi-project endpoint with an explicit id list.
            const isMultiProject = projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(projectId)
            const baseUrl = isMultiProject
                ? '/api/sessions/'
                : `/api/projects/${projectId}/sessions/`

            // Add cursor if we have one (for pagination)
            const params = new URLSearchParams()
            if (state.oldestSessionMtime != null) {
                params.set('before_mtime', state.oldestSessionMtime)
            }

            // For workspace mode, filter by the workspace's visible project IDs
            if (isWorkspaceProjectId(projectId)) {
                const wsId = extractWorkspaceId(projectId)
                // Lazy import to avoid circular dependency
                const { useWorkspacesStore } = await import('./workspaces')
                const wsStore = useWorkspacesStore()
                const visibleIds = wsStore.getVisibleProjectIds(wsId)
                if (visibleIds.length) {
                    params.set('project_ids', visibleIds.join(','))
                }
            }

            const url = params.toString() ? `${baseUrl}?${params}` : baseUrl
            const res = await apiFetch(url)

            if (!res.ok) {
                throw new Error(`Failed to load sessions: ${res.status}`)
            }

            return await res.json()
        },

        /**
         * Load sessions for a project or all projects (with pagination support).
         * Handles both initial load and "load more" for infinite scroll.
         *
         * @param {string} projectId - Project ID or ALL_PROJECTS_ID for all projects
         * @param {Object} options
         * @param {boolean} options.force - Reset pagination and reload from beginning
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         * @returns {Promise<Set<string>>} Set of session IDs that have changed
         *          (sessions where itemsFetched=true AND mtime changed or new)
         */
        async loadSessions(projectId, { force = false, isInitialLoading = false } = {}) {
            // Serialize concurrent loads of the same project. The previous
            // "skip if already loading" guard returned an EMPTY set to the
            // second caller — the reconciliation then believed nothing had
            // changed and skipped sessions whose items DID change. Instead,
            // await the in-flight load: a non-force caller is satisfied by its
            // result; a force caller runs its own load once the line is free.
            while (sessionsLoadInFlight.has(projectId)) {
                const inflightResult = await sessionsLoadInFlight.get(projectId).catch(() => new Set())
                if (!force) return inflightResult
            }

            const promise = this._doLoadSessions(projectId, { force, isInitialLoading })
            sessionsLoadInFlight.set(projectId, promise)
            try {
                return await promise
            } finally {
                sessionsLoadInFlight.delete(projectId)
            }
        },
        async _doLoadSessions(projectId, { force = false, isInitialLoading = false } = {}) {
            const changedIds = new Set()
            const state = this._ensureProjectLocalState(projectId)

            // Skip if fully loaded (unless force)
            if (!force && state.sessionsFetched && !state.hasMoreSessions) {
                return changedIds
            }

            // Reset pagination state if force
            if (force) {
                state.oldestSessionMtime = null
                state.hasMoreSessions = true
            }

            state.sessionsLoading = true

            try {
                const data = await this._fetchSessionsPage(projectId)

                // Merge sessions into store and track changes
                for (const fresh of data.sessions) {
                    const local = this.sessions[fresh.id]
                    const wasItemsFetched = this.localState.sessions[fresh.id]?.itemsFetched

                    // Session changed if: itemsFetched AND (new OR mtime different)
                    if (wasItemsFetched && (!local || local.mtime !== fresh.mtime)) {
                        changedIds.add(fresh.id)
                    }

                    // Update store
                    this.sessions[fresh.id] = fresh
                }

                // Update pagination state
                state.sessionsFetched = true
                state.hasMoreSessions = data.has_more

                // Cascade: when fetching a workspace scope, mark each member project
                // as fetched too. Without this, the `areProjectSessionsFetched` guard
                // in useWebSocket.js `session_updated` would drop new sessions for
                // projects the user has never opened individually, even though we
                // just loaded them collectively via the workspace endpoint.
                if (isWorkspaceProjectId(projectId)) {
                    const wsId = extractWorkspaceId(projectId)
                    const { useWorkspacesStore } = await import('./workspaces')
                    const wsStore = useWorkspacesStore()
                    for (const realProjectId of wsStore.getVisibleProjectIds(wsId)) {
                        this._ensureProjectLocalState(realProjectId).sessionsFetched = true
                    }
                } else if (Array.isArray(data.scope_project_ids)) {
                    // Same cascade for a single real project: its endpoint returns
                    // its own sessions plus its git worktrees', and reports the full
                    // covered scope. Mark every covered project fetched — including
                    // worktrees with no sessions yet — so their live session_updated
                    // pushes aren't dropped by the areProjectSessionsFetched guard.
                    // Sourced from the response (not getProjectScopeIds) so it is
                    // reliable on cold load, before the projects list has populated.
                    for (const id of data.scope_project_ids) {
                        this._ensureProjectLocalState(id).sessionsFetched = true
                    }
                }

                // Update cursor (oldest mtime received)
                if (data.sessions.length > 0) {
                    const oldestReceived = Math.min(...data.sessions.map(s => s.mtime))
                    state.oldestSessionMtime = oldestReceived
                }

                state.sessionsLoadingError = false
                return changedIds
            } catch (error) {
                console.error('Failed to load sessions:', error)
                if (isInitialLoading) {
                    state.sessionsLoadingError = true
                }
                throw error  // Re-throw for reconciliation retry logic
            } finally {
                state.sessionsLoading = false
            }
        },
        /**
         * Load all "sticky" sessions across every project into the store. A
         * sticky session is one that the sidebar may need to render even when
         * a different project/workspace is filtered: pinned sessions (any pin
         * mode), sessions with unread content, or sessions that currently have
         * an active Claude SDK process.
         *
         * The single-project `loadSessions(projectId)` call only populates
         * `this.sessions` with sessions belonging to that project, so without
         * this preload a cross-filter session would be missing from the store
         * and invisible to the sidebar. Subsequent updates are covered by the
         * existing `session_updated` / process-state WebSocket broadcasts.
         */
        async loadStickySessions() {
            try {
                const res = await apiFetch('/api/sessions/?pinned=1&unread=1&has_process=1')
                if (!res.ok) {
                    console.error('Failed to load sticky sessions:', res.status, res.statusText)
                    return
                }
                const data = await res.json()
                for (const session of data.sessions) {
                    this.sessions[session.id] = session
                }
            } catch (error) {
                console.error('Failed to load sticky sessions:', error)
            }
        },
        /**
         * Fetch a single session by ID when its project is not known ahead of time.
         * Populates `this.sessions[sessionId]` on success so reactive consumers
         * (SessionView, SessionList fallback) can proceed. Used when opening a
         * session whose project was not pre-loaded via `loadSessions` — e.g. a
         * cross-filter deep link where the URL's projectId is the sidebar filter
         * rather than the session's real project.
         * @param {string} sessionId
         * @returns {Promise<Object|null>} The session object, or null if not found.
         */
        async loadSessionById(sessionId) {
            if (this.sessions[sessionId]) {
                return this.sessions[sessionId]
            }
            try {
                const response = await fetch(`/api/sessions/${sessionId}/`)
                if (response.status === 404) {
                    return null
                }
                if (!response.ok) {
                    throw new Error(`Failed to load session: ${response.status}`)
                }
                const session = await response.json()
                this.sessions[session.id] = session
                return session
            } catch (error) {
                console.error(`Failed to load session ${sessionId}:`, error)
                throw error
            }
        },
        // --- Artifact bookmarks ---
        async loadArtifactBookmarks() {
            try {
                const res = await apiFetch('/api/artifact-bookmarks/')
                if (!res.ok) {
                    console.error('Failed to load artifact bookmarks:', res.status, res.statusText)
                    return
                }
                const data = await res.json()
                this.setArtifactBookmarks(data.bookmarks)
            } catch (error) {
                console.error('Failed to load artifact bookmarks:', error)
            } finally {
                this.artifactBookmarksLoaded = true
            }
        },
        // Replace the whole bookmark set from a full snapshot (boot REST load and
        // the `artifact_bookmarks_updated` WS message pushed on every connect).
        // A wholesale replace — not a merge — so removals that happened while
        // disconnected are reflected. The transient per-open `available` flag set
        // by fetchArtifactBookmarkDetail is intentionally dropped; it is re-fetched lazily.
        setArtifactBookmarks(list) {
            const next = {}
            for (const b of list || []) next[b.id] = b
            this.artifactBookmarks = next
            this.artifactBookmarksLoaded = true
        },
        async fetchArtifactBookmarkDetail(id) {
            // Always GET the detail (fresh server-side `available` flag), upsert
            // metadata, and return the full payload incl. `available`, or null on 404.
            try {
                const res = await apiFetch(`/api/artifact-bookmarks/${id}/`)
                if (res.status === 404) { delete this.artifactBookmarks[id]; return null }
                if (!res.ok) throw new Error(`Failed to load artifact bookmark: ${res.status}`)
                const b = await res.json()
                this.artifactBookmarks[b.id] = b
                return b
            } catch (error) {
                console.error(`Failed to fetch artifact bookmark ${id}:`, error)
                return null
            }
        },
        upsertArtifactBookmark(bookmark) { this.artifactBookmarks[bookmark.id] = bookmark },
        removeArtifactBookmark(id) { delete this.artifactBookmarks[id] },
        async createArtifactBookmark({ sessionId, relativePath, name, scope }) {
            const res = await apiFetch('/api/artifact-bookmarks/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ session_id: sessionId, relative_path: relativePath, name, scope }),
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err?.error || 'Failed to create artifact bookmark')
            }
            const b = await res.json()
            this.artifactBookmarks[b.id] = b
            return b
        },
        async updateArtifactBookmark(id, patch) {
            const res = await apiFetch(`/api/artifact-bookmarks/${id}/`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(patch),
            })
            if (!res.ok) throw new Error('Failed to update artifact bookmark')
            const b = await res.json()
            this.artifactBookmarks[b.id] = b
            return b
        },
        async deleteArtifactBookmark(id) {
            const res = await apiFetch(`/api/artifact-bookmarks/${id}/`, { method: 'DELETE' })
            if (!res.ok) throw new Error('Failed to delete artifact bookmark')
            delete this.artifactBookmarks[id]
        },
        /**
         * Load all items for a session from the API.
         * @param {string} projectId
         * @param {string} sessionId
         * @param {Object} options
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         */
        async loadSessionItems(projectId, sessionId, { isInitialLoading = false } = {}) {
            // Skip if already fetched
            if (this.localState.sessions[sessionId]?.itemsFetched) {
                return
            }
            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }

            // Only set loading if isInitialLoading is true (initial load case)
            if (isInitialLoading) {
                this.localState.sessions[sessionId].itemsLoading = true
            }

            try {
                const res = await apiFetch(`/api/projects/${projectId}/sessions/${sessionId}/items/`)
                if (!res.ok) {
                    console.error('Failed to load session items:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.sessions[sessionId].itemsLoadingError = true
                    }
                    return
                }
                const items = await res.json()
                this.sessionItems[sessionId] = items
                this.clearOptimisticMessageIfMatched(sessionId, items)
                this.localState.sessions[sessionId].itemsFetched = true
                this.localState.sessions[sessionId].itemsLoadingError = false
                // Session opening = audit point for sends whose outcome was
                // lost with a previous WebSocket/tab (send-failure recovery).
                this.auditInflightSends(sessionId)
            } catch (error) {
                console.error('Failed to load session items:', error)
                if (isInitialLoading) {
                    this.localState.sessions[sessionId].itemsLoadingError = true
                }
            } finally {
                this.localState.sessions[sessionId].itemsLoading = false
            }
        },

        /**
         * Load specific ranges of session items.
         * @param {string} projectId
         * @param {string} sessionId
         * @param {Array<number|[number, number|null]>} ranges - Array of ranges (line_num is 1-based):
         *   - number: exact line (e.g., 5)
         *   - [min, max]: range (e.g., [10, 20])
         *   - [min, null]: from min onwards (e.g., [10, null])
         *   - [null, max]: up to max (e.g., [null, 10])
         * @param {string|null} parentSessionId - If provided, this is a subagent request
         * @returns {Promise<boolean>} true on success (or nothing to do), false if the fetch failed —
         *   callers like the reconciliation need to know the lines are still missing.
         */
        async loadSessionItemsRanges(projectId, sessionId, ranges, parentSessionId = null) {
            if (!ranges?.length) return true

            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }

            // Coerce a value to an integer string ('' if missing/invalid).
            const toIntStr = (v) => {
                if (v == null || v === '') return ''
                const n = Number(v)
                return Number.isInteger(n) ? String(n) : null
            }

            // Build query params
            const params = new URLSearchParams()
            for (const range of ranges) {
                if (typeof range === 'number' || typeof range === 'string') {
                    const s = toIntStr(range)
                    if (s) {
                        params.append('range', s)
                    } else {
                        console.warn('loadSessionItemsRanges: skipping invalid range', range)
                    }
                } else if (Array.isArray(range)) {
                    const [min, max] = range
                    const minStr = toIntStr(min)
                    const maxStr = toIntStr(max)
                    if (minStr === null || maxStr === null || (minStr === '' && maxStr === '')) {
                        console.warn('loadSessionItemsRanges: skipping invalid range', range)
                        continue
                    }
                    params.append('range', `${minStr}:${maxStr}`)
                } else {
                    console.warn('loadSessionItemsRanges: skipping invalid range', range)
                }
            }

            // Refuse to call without any range — would fetch the entire session.
            if ([...params].length === 0) {
                console.error('loadSessionItemsRanges: no valid range provided, aborting', ranges)
                return false
            }

            // Build URL (handle subagent case)
            const baseUrl = parentSessionId
                ? `/api/projects/${projectId}/sessions/${parentSessionId}/subagent/${sessionId}`
                : `/api/projects/${projectId}/sessions/${sessionId}`

            try {
                const res = await apiFetch(`${baseUrl}/items/?${params}`)
                if (!res.ok) {
                    console.error('Failed to load session items ranges:', res.status, res.statusText)
                    return false
                }
                const items = await res.json()
                this.addSessionItems(sessionId, items)
                // Success: clear any previous error
                this.localState.sessions[sessionId].itemsLoadingError = false
                return true
            } catch (error) {
                console.error('Failed to load session items ranges:', error)
                return false
            }
        },

        /**
         * Ensure the loaded items of a session cover everything the server has,
         * healing the holes left by WebSocket broadcasts lost while disconnected.
         *
         * Two kinds of missing coverage are handled:
         * - Missing lines in the tail window (the last INITIAL_ITEMS_COUNT lines
         *   up to session.last_line): fetched WITH content — that is what the
         *   user is looking at.
         * - Bare placeholders before the tail window ({ line_num } only, created
         *   when a live item extended the array over a gap): they carry no
         *   display_level, so computeVisualItems drops them and the scroller's
         *   gap-fill can never see them. Healed by re-fetching the metadata and
         *   merging it in; the scroller then loads their content on demand like
         *   any other metadata-only item.
         *
         * Deliberately NOT based on "server last_line vs our last item": a live
         * item received right after a reconnect extends the array over the gap,
         * making that comparison read as up-to-date while the outage lines are
         * still missing.
         *
         * Concurrent calls per session coalesce: the second call returns true
         * immediately, the in-flight one is doing the work.
         *
         * @param {string} sessionId
         * @returns {Promise<boolean>} false if a needed fetch failed (lines are still missing).
         */
        async ensureSessionItemsCoverage(sessionId) {
            const session = this.sessions[sessionId]
            // Only meaningful for sessions whose items are (supposedly) loaded.
            if (!session || !this.localState.sessions[sessionId]?.itemsFetched) return true
            const serverLastLine = session.last_line || 0
            if (!serverLastLine) return true

            if (itemsCoverageInFlight.has(sessionId)) return true
            itemsCoverageInFlight.add(sessionId)
            try {
                const projectId = session.project_id
                const parentSessionId = session.parent_session_id || null
                const items = this.sessionItems[sessionId] || []
                // "Bare" = neither content nor metadata (placeholder or absent).
                const isBare = (item) => !item || (item.display_level == null && !hasContent(item))

                // Tail window: collect missing lines, to fetch with content.
                const windowStart = Math.max(1, serverLastLine - INITIAL_ITEMS_COUNT + 1)
                const missingTail = []
                for (let line = windowStart; line <= serverLastLine; line++) {
                    if (isBare(items[line - 1])) missingTail.push(line)
                }

                // Before the window: bare placeholders only need their metadata back.
                let hasBareBeforeWindow = false
                const beforeEnd = Math.min(items.length, windowStart - 1)
                for (let idx = 0; idx < beforeEnd; idx++) {
                    if (isBare(items[idx])) {
                        hasBareBeforeWindow = true
                        break
                    }
                }

                let ok = true
                if (missingTail.length) {
                    const loaded = await this.loadSessionItemsRanges(
                        projectId, sessionId, lineNumsToRanges(missingTail), parentSessionId,
                    )
                    ok = loaded && ok
                }
                if (hasBareBeforeWindow) {
                    const metadata = await this.loadSessionMetadata(projectId, sessionId, parentSessionId)
                    if (metadata) {
                        this.mergeSessionItemsMetadata(sessionId, metadata)
                    } else {
                        ok = false
                    }
                }
                return ok
            } finally {
                itemsCoverageInFlight.delete(sessionId)
            }
        },

        /**
         * Flag the session's currently-missing tail-window lines as "live", so
         * the auto-open-diffs setting opens edits that landed while the socket
         * was down — those come back via reconciliation, not the live stream
         * that normally sets the flag, so without this they stay collapsed.
         *
         * Computed up-front from the bare tail (same window as
         * ``ensureSessionItemsCoverage``) and INDEPENDENT of it: a concurrent
         * gap-heal (``ensureSessionItemsCoverage`` triggered by a post-reconnect
         * live item extending over the outage gap) coalesces the coverage call,
         * so tying the flag to "the lines this coverage loaded" would race and
         * silently drop it. Marking the bare set here, before the load, is
         * race-proof: the content fills in afterwards and the freshly-mounted
         * edit cards read the flag as true.
         *
         * Only edits actually auto-open — ``isLive``'s sole consumer is the
         * auto-open gate. The CALLER scopes this to the active session (auto-open
         * fires only for the session on screen), and only on a real reconnect
         * (never first connect, which would open every historical diff).
         */
        markNewTailItemsLive(sessionId) {
            const session = this.sessions[sessionId]
            if (!session || !this.localState.sessions[sessionId]?.itemsFetched) return
            const serverLastLine = session.last_line || 0
            if (!serverLastLine) return
            const items = this.sessionItems[sessionId] || []
            const isBare = (item) => !item || (item.display_level == null && !hasContent(item))
            const windowStart = Math.max(1, serverLastLine - INITIAL_ITEMS_COUNT + 1)
            const newLines = []
            for (let line = windowStart; line <= serverLastLine; line++) {
                if (isBare(items[line - 1])) newLines.push(line)
            }
            if (newLines.length) this.markItemsLive(sessionId, newLines)
        },

        /**
         * Merge freshly-fetched metadata into the items array without touching
         * loaded content. Fills bare placeholders (no display_level, no content)
         * so they become visible to the scroller's gap-fill; items that already
         * have metadata or content are left alone (live updates keep them fresher
         * than this snapshot).
         * @param {string} sessionId
         * @param {Array} metadata - Same shape as initSessionItemsFromMetadata's input
         */
        mergeSessionItemsMetadata(sessionId, metadata) {
            const targetArray = this.sessionItems[sessionId]
            if (!targetArray) return

            let changed = false
            for (const m of metadata) {
                const index = m.line_num - 1
                while (targetArray.length <= index) {
                    targetArray.push({ line_num: targetArray.length + 1 })
                }
                const existing = targetArray[index]
                if (existing.display_level != null || hasContent(existing)) continue
                targetArray[index] = {
                    line_num: m.line_num,
                    display_level: m.display_level,
                    group_head: m.group_head,
                    group_tail: m.group_tail,
                    kind: m.kind,
                    timestamp: m.timestamp ?? null,
                    content: null,
                }
                changed = true
            }
            if (changed) {
                this.recomputeVisualItems(sessionId)
            }
        },

        // Unload actions (for reconciliation failures or cache cleanup)

        /**
         * Unload a session's items data.
         * Resets itemsFetched to false and clears the items array.
         * Does NOT remove the session itself from the store.
         * @param {string} sessionId
         */
        unloadSession(sessionId) {
            if (this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId].itemsFetched = false
                this.localState.sessions[sessionId].itemsLoading = false
            }
            delete this.sessionItems[sessionId]
            delete this.localState.sessionExpandedGroups[sessionId]
            delete this.localState.sessionDebugOverride[sessionId]
            delete this.localState.sessionInternalExpandedGroups[sessionId]
            delete this.localState.sessionVisualItems[sessionId]
            delete this.localState.visualItemCache[sessionId]
            delete this.localState.optimisticMessages[sessionId]
            delete this.localState.agentLinks[sessionId]
            delete this.localState.workflowLinks[sessionId]
            delete this.localState.toolStates[sessionId]
            delete this.localState.liveItems[sessionId]
            delete this.localState.openDetails[sessionId]
            // Remove synthetic process state if this is a subagent
            if (this.processStates[sessionId]?.synthetic) {
                delete this.processStates[sessionId]
            }
            // Remove synthetic process states for all subagents of this session
            for (const [id, ps] of Object.entries(this.processStates)) {
                if (ps.synthetic && this.sessions[id]?.parent_session_id === sessionId) {
                    delete this.processStates[id]
                }
            }
        },

        /**
         * Unload a project's sessions data.
         * Resets sessionsFetched to false, clears all sessions of this project,
         * and unloads all their items.
         * Does NOT remove the project itself from the store.
         * @param {string} projectId
         */
        unloadProject(projectId) {
            // First, unload all sessions of this project
            const sessionsToUnload = Object.values(this.sessions)
                .filter(s => s.project_id === projectId)
                .map(s => s.id)

            for (const sessionId of sessionsToUnload) {
                this.unloadSession(sessionId)
                delete this.sessions[sessionId]
            }

            // Then reset the project's fetch state
            if (this.localState.projects[projectId]) {
                this.localState.projects[projectId].sessionsFetched = false
            }
        },

        // Visual items computation

        /**
         * Recompute visual items for a session based on current mode and expanded groups.
         * Should be called after:
         * - sessionItems changes (metadata loaded, content loaded, new item via WebSocket)
         * - Display mode changes
         * - Group is toggled
         *
         * @param {string} sessionId
         */
        recomputeVisualItems(sessionId) {
            const items = this.sessionItems[sessionId] || []
            const failedSends = this.localState.failedSends[sessionId]
            const hasFailedSends = !!failedSends && Object.keys(failedSends).length > 0
            if (!items.length && !this.localState.optimisticMessages[sessionId] && !hasFailedSends) {
                this.localState.sessionVisualItems[sessionId] = []
                this.localState.visualItemCache[sessionId] = new Map()
                return
            }

            // Get effective display mode from settings store, unless this session
            // has the dev-mode debug override forced (header toggle), which wins.
            const settingsStore = useSettingsStore()
            const mode = this.localState.sessionDebugOverride[sessionId]
                ? DISPLAY_MODE.DEBUG
                : settingsStore.getDisplayMode
            const expandedGroups = this.localState.sessionExpandedGroups[sessionId] || []

            // Detect assistant_turn (used by computeVisualItems for conversation mode
            // filtering, and for the synthetic working assistant message)
            const processState = this.processStates[sessionId]
            const isAssistantTurn = processState?.state === PROCESS_STATE.ASSISTANT_TURN

            let allItems = items || []
            // Append failed-send bubbles (messaging pattern: undeliverable
            // messages stay in the flow, marked failed), oldest first,
            // before the current optimistic message. Items materialize
            // lazily: at boot, hydration may register entries before the
            // session (hence its provider-shaped content) is known.
            if (hasFailedSends) {
                const failedItems = Object.values(failedSends)
                    .sort((a, b) => a.sentAt - b.sentAt)
                    .map(failedSend => failedSend.item
                        || (failedSend.item = this._materializeFailedSendItem(failedSend)))
                    .filter(Boolean)
                allItems = [...allItems, ...failedItems]
            }
            // Append optimistic message if one exists for this session
            const optimistic = this.localState.optimisticMessages[sessionId]
            if (optimistic) {
                allItems = [...allItems, optimistic]
            }

            // Append a synthetic "starting" assistant message when in starting state.
            // Same structure as the working message but with a simpler content.
            const isStarting = processState?.state === PROCESS_STATE.STARTING
            let startingMessage = null
            if (isStarting) {
                const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE
                startingMessage = {
                    line_num: lineNum,
                    content: null,
                    kind: 'assistant_message',
                    syntheticKind,
                    display_level: DISPLAY_LEVEL.ALWAYS,
                    group_head: null,
                    group_tail: null,
                }
                setParsedContent(startingMessage, {
                    type: 'assistant',
                    syntheticKind,
                    message: { role: 'assistant', content: [] },
                })
                allItems = allItems === items ? [...items, startingMessage] : [...allItems, startingMessage]
            }

            // Inject streaming blocks as synthetic items (one per active block).
            // Streaming blocks appear BEFORE the working message in the list.
            const streaming = this.localState.streamingBlocks[sessionId]
            const streamingItems = []
            let hasActiveTextStreaming = false
            if (streaming?.blocks.length) {
                const { baseLineNum, kind: streamingSyntheticKind } = SYNTHETIC_ITEM.STREAMING_BLOCK
                // Last displayable item before streaming (used for group inheritance
                // decisions on streaming thinking blocks). Scans backward past
                // DEBUG_ONLY items and items whose metadata isn't computed yet,
                // so we anchor on the last item the user actually sees.
                let lastRealItem = null
                for (let i = items.length - 1; i >= 0; i--) {
                    const dl = items[i].display_level
                    if (dl === DISPLAY_LEVEL.ALWAYS || dl === DISPLAY_LEVEL.COLLAPSIBLE) {
                        lastRealItem = items[i]
                        break
                    }
                }
                for (const block of streaming.blocks) {
                    if (!block.stopped && block.blockType === 'text') hasActiveTextStreaming = true
                    const lineNum = baseLineNum - block.blockIndex
                    const displayText = block.displayedText ?? block.text
                    const contentBlock = block.blockType === 'thinking'
                        ? { type: 'thinking', thinking: displayText, streaming: !block.stopped }
                        : { type: 'text', text: displayText }
                    const meta = getStreamingItemMetadata(block, lastRealItem, lineNum)
                    const streamItem = {
                        line_num: lineNum,
                        content: null,
                        kind: 'assistant_message',
                        syntheticKind: streamingSyntheticKind,
                        display_level: meta.display_level,
                        group_head: meta.group_head,
                        group_tail: meta.group_tail,
                    }
                    setParsedContent(streamItem, {
                        type: 'assistant',
                        syntheticKind: streamingSyntheticKind,
                        message: { role: 'assistant', content: [contentBlock] },
                    })
                    streamingItems.push(streamItem)
                    allItems = allItems === items ? [...items, streamItem] : [...allItems, streamItem]
                }
            }

            // Get detailed blocks for conversation mode (per-block detail toggle).
            // Computed early because the working-message gating below needs to know
            // whether streaming text is actually visible (it's hidden in conversation
            // mode unless the current block is in detailed mode).
            const detailedBlocksArray = this.localState.sessionDetailedBlocks[sessionId] || []
            const detailedBlocks = new Set(detailedBlocksArray)

            // In conversation mode, streaming items are hidden unless the current
            // block (= the latest user_message) is in detailed mode. When streaming
            // is hidden, we keep the working-message visible so the user has a
            // status indicator instead of a blank screen.
            let isCurrentBlockDetailed = false
            if (mode === DISPLAY_MODE.CONVERSATION && detailedBlocks.size > 0) {
                for (let i = items.length - 1; i >= 0; i--) {
                    if (items[i].kind === 'user_message') {
                        isCurrentBlockDetailed = detailedBlocks.has(items[i].line_num)
                        break
                    }
                }
            }
            const streamingTextWillBeVisible = hasActiveTextStreaming && (
                mode !== DISPLAY_MODE.CONVERSATION || isCurrentBlockDetailed
            )

            // Append a synthetic "working" assistant message when in assistant_turn.
            // Hidden when streaming text is actually visible to the user (which
            // depends on mode and detailed-block state).
            // Injected into allItems so computeVisualItems handles it like any other item.
            // computeVisualItems knows to always let synthetic items (line_num < 0) through,
            // even in conversation mode which normally filters assistant messages.
            let workingMessage = null
            if (isAssistantTurn && !streamingTextWillBeVisible) {
                const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE

                workingMessage = {
                    line_num: lineNum,
                    content: null,
                    kind: 'assistant_message',
                    syntheticKind,
                    display_level: DISPLAY_LEVEL.ALWAYS,
                    group_head: null,
                    group_tail: null,
                }
                // Whether the tool card the working-message refers to is actually
                // visible on screen. The component drops the parenthesised target
                // when the user can already see the card right above; in modes
                // where tools are hidden by default (simplified groups, conversation
                // blocks), keep the target unless the user has opened the relevant
                // group/block.
                const lastToolVisible = computeLastToolVisible(
                    items,
                    processState?.lastStartedToolId,
                    mode,
                    expandedGroups,
                    isCurrentBlockDetailed,
                )
                // The working message's visible state (status label + active
                // tools) lives in _parsedContent, which the stabilizer
                // (visualItemEqual) skips. Without a top-level field that
                // changes with it, a label/tools change with nothing else
                // moving — e.g. a Codex /compact flipping the label to
                // "compacting" with no new items arriving — would reuse the
                // cached placeholder and never re-render. This signature
                // forces re-stabilization when the visible state changes.
                workingMessage.workingStatusKey = JSON.stringify([
                    processState?.label || null,
                    processState?.tools || [],
                    processState?.lastStartedToolId || null,
                    lastToolVisible,
                ])
                setParsedContent(workingMessage, {
                    type: 'assistant',
                    syntheticKind,
                    label: processState?.label || null,
                    tools: processState?.tools || [],
                    lastStartedToolId: processState?.lastStartedToolId || null,
                    lastToolVisible,
                    message: {
                        role: 'assistant',
                        content: []
                    }
                })
                allItems = allItems === items ? [...items, workingMessage] : [...allItems, workingMessage]
            }

            const visualItems = computeVisualItems(allItems, mode, expandedGroups, isAssistantTurn, detailedBlocks)

            // Reorder /compact command before its compact_summary.
            // In the JSONL file, the compact_summary line appears before the /compact command
            // (despite the user typing it first), so we fix the visual order here.
            if (this.sessions[sessionId]?.compacted) {
                for (let i = 0; i < visualItems.length; i++) {
                    if (visualItems[i].kind !== 'compact_summary') continue
                    for (let j = i + 1; j < Math.min(i + 10, visualItems.length); j++) {
                        if (visualItems[j].kind !== 'user_message') continue
                        const parsed = getParsedContent(visualItems[j])
                        const text = parsed?.message?.content
                        if (typeof text === 'string' && text.includes('<command-name>/compact</command-name>')) {
                            const [moved] = visualItems.splice(j, 1)
                            visualItems.splice(i, 0, moved)
                            break
                        }
                        break  // Only check the first user_message after compact_summary
                    }
                }
            }

            // Propagate syntheticKind to visual items for synthetic messages.
            // computeVisualItems doesn't know about syntheticKind, so we add it here.
            const streamingLineNums = streamingItems.length
                ? new Set(streamingItems.map(si => si.line_num))
                : null
            for (let i = visualItems.length - 1; i >= 0; i--) {
                const vi = visualItems[i]
                if (vi.lineNum <= SYNTHETIC_ITEM.FAILED_USER_MESSAGE.baseLineNum) {
                    vi.syntheticKind = SYNTHETIC_ITEM.FAILED_USER_MESSAGE.kind
                } else if (vi.lineNum === SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.lineNum && optimistic) {
                    vi.syntheticKind = optimistic.syntheticKind
                } else if (vi.lineNum === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.lineNum && startingMessage) {
                    vi.syntheticKind = startingMessage.syntheticKind
                } else if (vi.lineNum === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.lineNum && workingMessage) {
                    vi.syntheticKind = workingMessage.syntheticKind
                    // Carry the status signature onto the visual item so the
                    // stabilizer detects label/tools changes (see above).
                    vi.workingStatusKey = workingMessage.workingStatusKey
                } else if (streamingLineNums?.has(vi.lineNum)) {
                    vi.syntheticKind = SYNTHETIC_ITEM.STREAMING_BLOCK.kind
                }
                // Synthetic items are always at the end, stop as soon as we hit a real item
                if (vi.lineNum >= 0) break
            }

            // Mark each visual item as start/end of its run (block of consecutive
            // user_message items vs block of consecutive non-user items). The CSS
            // uses these flags (.is-block-start / .is-block-end) to render the
            // top/bottom borders of the visual card without depending on
            // adjacent-sibling selectors over `.virtual-scroller-item`. Stable
            // class assignment avoids layout shifts when the scroller loads/unloads
            // items at the rendered range edges.
            for (let i = 0; i < visualItems.length; i++) {
                const isUser = visualItems[i].kind === 'user_message'
                const prevIsUser = i > 0 ? visualItems[i - 1].kind === 'user_message' : null
                const nextIsUser = i < visualItems.length - 1 ? visualItems[i + 1].kind === 'user_message' : null
                visualItems[i].isBlockStart = i === 0 || isUser !== prevIsUser
                visualItems[i].isBlockEnd = i === visualItems.length - 1 || isUser !== nextIsUser
            }

            // Insert per-block day separators (on calendar-day changes, only at
            // inter-block boundaries) when the message-timestamps setting is on.
            // Runs after the block flags are set (it relies on isBlockEnd) and
            // before stabilization so separators get stable references too.
            const renderItems = settingsStore.areMessageTimestampsShown
                ? insertDaySeparators(visualItems)
                : visualItems

            // Stabilize visual item references: reuse cached objects when properties
            // haven't changed, so Vue sees the same reference and skips re-render.
            const cache = this.localState.visualItemCache[sessionId] || new Map()
            const newCache = new Map()

            const stableItems = renderItems.map(vi => {
                const cached = cache.get(vi.lineNum)
                if (visualItemEqual(cached, vi)) {
                    // Properties identical — reuse old reference.
                    // Forward the parsed content from the new computation to the
                    // cached object in case items were re-parsed (e.g. content loaded).
                    const parsed = getParsedContent(vi)
                    if (parsed !== null) setParsedContent(cached, parsed)
                    newCache.set(vi.lineNum, cached)
                    return cached
                }
                // Changed or new item — use the new object.
                // Forward parsed content so it's available on the visual item.
                const parsed = getParsedContent(vi)
                if (parsed !== null) setParsedContent(vi, parsed)
                newCache.set(vi.lineNum, vi)
                return vi
            })

            this.localState.visualItemCache[sessionId] = newCache
            this.localState.sessionVisualItems[sessionId] = stableItems
        },

        /**
         * Recompute visual items for ALL sessions.
         * Called when display mode changes (affects all sessions).
         */
        recomputeAllVisualItems() {
            for (const sessionId of Object.keys(this.sessionItems)) {
                this.recomputeVisualItems(sessionId)
            }
        },

        // Optimistic message actions

        /**
         * Set an optimistic user message for a session.
         * Displayed immediately in the conversation while waiting for the backend
         * to confirm with a real user_message item.
         * @param {string} sessionId
         * @param {string} text - The message text
         * @param {Object} [attachments] - Optional attachments in SDK format
         * @param {Array} [attachments.images] - Image blocks ({ type: 'image', source: {...} })
         * @param {Array} [attachments.documents] - Document blocks ({ type: 'document', source: {...} })
         */
        setOptimisticMessage(sessionId, text, attachments) {
            const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE
            // Store as sessionItem format (snake_case) since it's injected into
            // the items array before computeVisualItems processes it.
            const optimisticItem = {
                line_num: lineNum,
                content: null,
                kind: 'user_message',
                syntheticKind,
                _optimisticCreatedAtMs: Date.now(),
                display_level: DISPLAY_LEVEL.ALWAYS,
                group_head: null,
                group_tail: null
            }
            // The parsed-content shape is provider-specific: each renderer in
            // ``SessionItem.vue`` expects its own native JSONL layout (Claude
            // Code reads ``message.content[]``, Codex reads
            // ``payload.message``). The provider's helpers own that mapping.
            const provider = this.getSession(sessionId)?.provider
            const helpers = getProviderHelpers(provider)
            setParsedContent(
                optimisticItem,
                helpers.buildOptimisticUserMessageContent(text, attachments),
            )
            this.localState.optimisticMessages[sessionId] = optimisticItem
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Clear the optimistic message for a session.
         * Called when the real user_message arrives from the backend.
         * @param {string} sessionId
         */
        clearOptimisticMessage(sessionId) {
            if (this.localState.optimisticMessages[sessionId]) {
                delete this.localState.optimisticMessages[sessionId]
                this.recomputeVisualItems(sessionId)
            }
        },

        /**
         * Clear the optimistic message when an API-loaded user_message matches it.
         *
         * Live WebSocket additions can clear on any new user_message because
         * they are causally tied to fresh backend lines. API loads can include
         * older user_messages, so they use content matching to avoid dropping
         * a just-sent placeholder before its real line has been persisted.
         * @param {string} sessionId
         * @param {Array<Object>} items
         */
        clearOptimisticMessageIfMatched(sessionId, items) {
            this.resolveInflightSends(sessionId, items)
            const optimistic = this.localState.optimisticMessages[sessionId]
            if (!optimistic || !items?.length) return

            const providerHelpers = getProviderHelpers(this.getSession(sessionId)?.provider)
            if (items.some(item => userMessageMatchesOptimistic(providerHelpers, optimistic, item))) {
                delete this.localState.optimisticMessages[sessionId]
            }
        },

        // Send-failure recovery actions (see registerInflightSend for the flow)

        /**
         * Snapshot an outgoing send so it can be restored if the backend
         * cannot deliver it to the agent.
         *
         * Flow: MessageInput registers the snapshot (original draft-format
         * medias) right before clearing the composer; a backend ``send_ack``
         * frame echoing the request_id confirms delivery and drops it
         * (confirmInflightSend); an ``error`` frame consumes it instead
         * (failInflightSend → failed bubble + restore); failing those, the
         * arrival of the matching real user_message resolves it silently; a
         * TTL sweep drops forgotten entries.
         * @param {string} requestId
         * @param {Object} snapshot - { sessionId, text, medias, optimisticShown, startingSet, noLineExpected }
         */
        registerInflightSend(requestId, snapshot) {
            const now = Date.now()
            for (const [id, entry] of inflightSends) {
                if (now - entry.sentAt > INFLIGHT_SEND_TTL_MS) this._dropInflightSend(id)
            }
            const entry = { ...snapshot, sentAt: now }
            inflightSends.set(requestId, entry)
            // Write-through to IndexedDB so the snapshot survives a killed
            // or frozen tab (the audit rediscovers it at the next boot).
            saveInflightSend(requestId, entry).catch(err =>
                console.warn('Failed to persist in-flight send snapshot:', err)
            )
        },

        /** Drop a snapshot from both the registry and IndexedDB. */
        _dropInflightSend(requestId) {
            inflightSends.delete(requestId)
            deleteInflightSend(requestId).catch(err =>
                console.warn('Failed to delete in-flight send snapshot:', err)
            )
        },

        /**
         * Shared post-send bookkeeping for the composer and the failed-bubble
         * Retry: snapshot the outgoing send, show the optimistic bubble, and
         * set the optimistic "starting" process state when no process runs.
         * (During an assistant turn the message is queued backend-side and
         * its user_message line only appears later — no bubble then.)
         * @param {string} sessionId
         * @param {string} projectId
         * @param {string} requestId
         * @param {Object} send - { text, medias, images, documents }:
         *   medias in original draft format (for restore), images/documents
         *   in SDK format (for the optimistic bubble)
         */
        registerOutgoingSend(sessionId, projectId, requestId, { text, medias, images, documents }) {
            const state = this.processStates[sessionId]?.state
            const optimisticShown = state !== PROCESS_STATE.ASSISTANT_TURN
            const startingSet = optimisticShown && !state
            // Claude Code folds a message accepted mid-turn into the running
            // turn and never writes a user_message line for it (hybrid CLI
            // sessions DO — they are excluded). Absence of a line is then no
            // evidence of non-delivery: only the backend send_ack confirms
            // these, so the audit must never flag them as undelivered.
            const session = this.getSession(sessionId)
            const noLineExpected = session?.provider === 'claude_code'
                && !session?.hybrid
                && state === PROCESS_STATE.ASSISTANT_TURN
            this.registerInflightSend(requestId, {
                sessionId,
                text,
                medias: medias || [],
                optimisticShown,
                startingSet,
                noLineExpected,
            })
            if (optimisticShown) {
                const attachments = (images?.length || documents?.length)
                    ? { images, documents }
                    : undefined
                this.setOptimisticMessage(sessionId, text, attachments)
                // The backend broadcasts STARTING before spawning the
                // subprocess, but the SDK connect() blocks the event loop so
                // the frame only lands seconds later — this gives immediate
                // visual feedback.
                if (startingSet) {
                    this.setProcessState(sessionId, projectId, PROCESS_STATE.STARTING)
                }
            }
        },

        /**
         * Drop in-flight snapshots whose text matches a freshly arrived
         * user_message — the send demonstrably reached the agent.
         * @param {string} sessionId
         * @param {Array<Object>} items
         */
        resolveInflightSends(sessionId, items) {
            const failed = this.localState.failedSends[sessionId]
            const hasFailed = !!failed && Object.keys(failed).length > 0
            if ((!inflightSends.size && !hasFailed) || !items?.length) return
            const providerHelpers = getProviderHelpers(this.getSession(sessionId)?.provider)
            if (!providerHelpers) return
            const keys = new Set()
            for (const item of items) {
                if (item?.kind !== 'user_message') continue
                const key = userMessageMatchKey(providerHelpers, getParsedContent(item))
                if (key) keys.add(key)
            }
            if (!keys.size) return
            for (const [id, entry] of inflightSends) {
                if (entry.sessionId !== sessionId) continue
                const key = inflightSendMatchKey(entry)
                if (key && keys.has(key)) this._dropInflightSend(id)
            }
            // A failed bubble whose message finally arrived was delivered after
            // all (audited failures are best-effort guesses) — self-heal by
            // removing it from the flow.
            if (hasFailed) {
                for (const entry of Object.values(failed)) {
                    const key = inflightSendMatchKey(entry)
                    if (key && keys.has(key)) this.removeFailedSend(sessionId, entry.requestId)
                }
            }
        },

        /**
         * Consume the in-flight snapshot matching a send_message error frame
         * and turn it into a failed bubble in the conversation flow.
         * @param {string} requestId
         * @param {Object} info - { code, message } from the error frame
         * @returns {boolean} true when a snapshot was found and handled
         */
        failInflightSend(requestId, info) {
            const entry = inflightSends.get(requestId)
            if (!entry) return false
            inflightSends.delete(requestId)
            this._applySendFailure(requestId, entry, info)
            return true
        },

        /**
         * Positive delivery acknowledgement from the backend (``send_ack``
         * frame): the message reached the agent. Drop the snapshot, and heal
         * any failed bubble a lost or premature failure signal produced for
         * it (the ack is authoritative — it proves delivery). This is the
         * only confirmation for messages Claude Code accepts mid-turn, which
         * never get their own user_message line.
         * @param {string} sessionId
         * @param {string} requestId
         */
        confirmInflightSend(sessionId, requestId) {
            this._dropInflightSend(requestId)
            if (sessionId) this.removeFailedSend(sessionId, requestId)
        },

        /**
         * Late-failure path: the agent died after accepting the send but
         * possibly before processing it. Every unresolved snapshot of the
         * session becomes a failed bubble.
         * @param {string} sessionId
         * @param {Object} info - { code, message }
         * @returns {boolean} true when an unresolved snapshot existed
         */
        failPendingSendsForSession(sessionId, info) {
            let any = false
            for (const [id, entry] of inflightSends) {
                if (entry.sessionId !== sessionId) continue
                inflightSends.delete(id)
                this._applySendFailure(id, entry, info)
                any = true
            }
            return any
        },

        // Turn a failed in-flight send into a "failed message" bubble shown
        // in situ in the conversation flow (messaging pattern), with
        // Retry/Edit/Delete actions. The in-memory registry entry is
        // consumed but the IndexedDB copy is UPDATED (not deleted) with the
        // failure reason, so an unhandled bubble — including its precise
        // reason — survives a page reload. The persisted copy is deleted on
        // retry/edit/delete (and on resolution).
        _applySendFailure(requestId, entry, info) {
            const { sessionId } = entry
            // Undo what the optimistic send did to the chat: the ghost user
            // message, and the optimistic "starting" process state (only if
            // no real broadcast replaced it in the meantime).
            if (entry.optimisticShown) this.clearOptimisticMessage(sessionId)
            if (entry.startingSet && this.processStates[sessionId]?.state === PROCESS_STATE.STARTING) {
                delete this.processStates[sessionId]
            }
            const code = info.code || 'send_failed'
            const message = info.message || 'The message could not be delivered.'
            const failedAt = info.failedAt || Date.now()
            const failedSend = {
                requestId,
                sessionId,
                text: entry.text,
                medias: entry.medias || [],
                mediasDropped: !!entry.mediasDropped,
                code,
                message,
                sentAt: entry.sentAt || failedAt,
            }
            failedSend.item = this._materializeFailedSendItem(failedSend)
            if (!this.localState.failedSends[sessionId]) {
                this.localState.failedSends[sessionId] = {}
            }
            this.localState.failedSends[sessionId][requestId] = failedSend
            saveInflightSend(requestId, { ...entry, failed: { code, message, failedAt } }).catch(err =>
                console.warn('Failed to persist send failure:', err)
            )
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Build the synthetic session item for a failed send. Same rendering
         * path as the optimistic user message (provider-shaped parsed
         * content); the failure banner reads the extra ``failedSend`` field.
         *
         * Returns ``null`` when the session (hence its provider) is not
         * known yet — at boot, hydration can run before the sessions arrive.
         * recomputeVisualItems retries lazily on every pass.
         */
        _materializeFailedSendItem(failedSend) {
            const helpers = getProviderHelpers(this.getSession(failedSend.sessionId)?.provider)
            if (!helpers) return null
            const { baseLineNum, kind: syntheticKind } = SYNTHETIC_ITEM.FAILED_USER_MESSAGE
            const item = {
                line_num: baseLineNum - failedSendSeq++,
                content: null,
                kind: 'user_message',
                syntheticKind,
                display_level: DISPLAY_LEVEL.ALWAYS,
                group_head: null,
                group_tail: null,
            }
            const { images, documents } = mediasToSdkFormat(failedSend.medias || [])
            const attachments = (images.length || documents.length)
                ? { images, documents }
                : undefined
            const parsed = helpers.buildOptimisticUserMessageContent(failedSend.text, attachments)
            parsed.syntheticKind = syntheticKind
            parsed.failedSend = {
                requestId: failedSend.requestId,
                code: failedSend.code,
                message: failedSend.message,
                mediasDropped: failedSend.mediasDropped,
                sentAt: failedSend.sentAt,
            }
            setParsedContent(item, parsed)
            return item
        },

        /**
         * Remove a failed bubble (after retry, edit, delete, or when its
         * user_message line finally arrived) and its persisted snapshot.
         * @param {string} sessionId
         * @param {string} requestId
         */
        removeFailedSend(sessionId, requestId) {
            const failed = this.localState.failedSends[sessionId]
            if (!failed?.[requestId]) return
            delete failed[requestId]
            if (!Object.keys(failed).length) {
                delete this.localState.failedSends[sessionId]
            }
            deleteInflightSend(requestId).catch(err =>
                console.warn('Failed to delete in-flight send snapshot:', err)
            )
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Stage or un-stage a pending hybrid switch for an existing SDK session.
         * Local + volatile (never persisted): the switch is only committed via
         * ``set_session_hybrid`` on the next Send/Apply. Re-clicking the toggle
         * un-stages it silently.
         * @param {string} sessionId
         * @param {boolean} staged
         */
        setStagedHybrid(sessionId, staged) {
            if (staged) {
                this.localState.stagedHybrid[sessionId] = true
            } else {
                delete this.localState.stagedHybrid[sessionId]
            }
        },

        /**
         * Load persisted in-flight send snapshots into the registry.
         * Called at app startup: snapshots left behind by a killed or frozen
         * tab (whose error frame was lost with the WebSocket) become visible
         * to the audit again. Expired entries are swept on the way.
         */
        async hydrateInflightSends() {
            let stored
            try {
                stored = await getAllInflightSends()
            } catch (err) {
                console.warn('Failed to load in-flight sends from IndexedDB:', err)
                return
            }
            const now = Date.now()
            for (const [requestId, entry] of Object.entries(stored)) {
                if (!entry?.sessionId || !entry.text || now - (entry.sentAt || 0) > INFLIGHT_SEND_TTL_MS) {
                    deleteInflightSend(requestId).catch(() => {})
                    continue
                }
                if (entry.failed) {
                    // The failure (and its precise reason) was already known
                    // before the reload — re-materialize the bubble directly,
                    // no audit needed.
                    if (!this.localState.failedSends[entry.sessionId]?.[requestId]) {
                        this._applySendFailure(requestId, entry, entry.failed)
                    }
                    continue
                }
                if (!inflightSends.has(requestId)) inflightSends.set(requestId, entry)
            }
            // Sessions opened before hydration finished never saw these
            // snapshots — audit them now (the reverse order is covered by
            // the audit call in loadSessionData).
            this.auditAllLoadedInflightSends()
        },

        /**
         * Audit the session's unresolved in-flight snapshots against its
         * loaded items — the recovery path for failures whose error frame
         * never reached us (WebSocket cut, tab frozen/killed).
         *
         * A snapshot whose text matches a user_message line was delivered:
         * resolved silently. The rest is genuinely unconfirmed and becomes
         * failed bubbles in the conversation flow — unless the send is
         * recent or the agent is mid-turn (a queued message only gets its
         * user_message line when the turn picks it up). Wrong guesses
         * self-heal: resolveInflightSends removes the bubble if the matching
         * line eventually arrives.
         * @param {string} sessionId
         */
        async auditInflightSends(sessionId) {
            const items = this.sessionItems[sessionId]
            if (items?.length) this.resolveInflightSends(sessionId, items)
            // Without the full item list, absence of a match proves nothing.
            if (!this.localState.sessions[sessionId]?.itemsFetched) return
            const state = this.processStates[sessionId]?.state
            if (state === PROCESS_STATE.ASSISTANT_TURN || state === PROCESS_STATE.STARTING) return
            const now = Date.now()
            const candidates = []
            for (const [id, entry] of inflightSends) {
                if (entry.sessionId !== sessionId) continue
                // No user_message line will ever confirm these (Claude Code
                // mid-turn); only the backend send_ack does. Absence here is
                // not evidence of failure — never declare them undelivered.
                if (entry.noLineExpected) continue
                if (now - entry.sentAt < INFLIGHT_AUDIT_MIN_AGE_MS) continue
                candidates.push(id)
            }
            if (!candidates.length) return

            // Session opening only loads content for the TAIL of the items
            // (virtual scroller): older user_message lines are metadata-only
            // and resolveInflightSends cannot match what it cannot read — a
            // delivered message whose line fell outside the window would be
            // wrongly declared lost. Fetch the content of those lines first
            // (recent-most capped; a recent send cannot have hundreds of
            // user messages written after its own line); addSessionItems
            // re-runs resolution with them.
            const missingLines = []
            for (const item of items || []) {
                if (item?.kind !== 'user_message' || hasContent(item)) continue
                missingLines.push(item.line_num)
            }
            const linesToFetch = missingLines.slice(-INFLIGHT_AUDIT_FETCH_CAP)
            if (linesToFetch.length) {
                const projectId = this.getSession(sessionId)?.project_id
                if (!projectId) return
                await this.loadSessionItemsRanges(projectId, sessionId, linesToFetch)
                // Some requested lines still have no content (fetch failed):
                // delivery cannot be ruled out — keep the snapshots for a
                // later audit instead of crying wolf.
                const itemsNow = this.sessionItems[sessionId] || []
                if (linesToFetch.some(n => itemsNow[n - 1] && !hasContent(itemsNow[n - 1]))) return
            }

            const message = 'This message could not be confirmed as delivered — '
                + 'it may never have reached the agent (interrupted connection?).'
            for (const id of candidates) {
                const entry = inflightSends.get(id)
                if (!entry) continue // resolved by a fetched line or a concurrent audit
                inflightSends.delete(id)
                this._applySendFailure(id, entry, { code: 'delivery_unconfirmed', message })
            }
        },

        /**
         * Run the audit for every session that has unresolved snapshots.
         * Sessions without loaded items are skipped by the per-session audit
         * (nothing to match against) and will be checked when opened.
         */
        auditAllLoadedInflightSends() {
            const sessionIds = new Set()
            for (const entry of inflightSends.values()) sessionIds.add(entry.sessionId)
            for (const sessionId of sessionIds) this.auditInflightSends(sessionId)
        },

        /**
         * Put snapshotted medias back into the session's draft attachments
         * (in-memory map + IndexedDB), used by the send-failure restore.
         * @param {string} sessionId
         * @param {Array<Object>} medias - original draft-format media objects
         */
        async restoreDraftAttachments(sessionId, medias) {
            if (!medias?.length) return
            if (!this.localState.attachments[sessionId]) {
                this.localState.attachments[sessionId] = new Map()
            }
            const map = this.localState.attachments[sessionId]
            const draft = await getDraftMessage(sessionId) || {}
            draft.mediaIds = draft.mediaIds || []
            for (const media of medias) {
                try {
                    await saveDraftMedia(media)
                } catch (err) {
                    console.warn('Failed to re-save restored draft media:', err)
                }
                map.set(media.id, media)
                if (!draft.mediaIds.includes(media.id)) draft.mediaIds.push(media.id)
            }
            await saveDraftMessage(sessionId, draft).catch(err =>
                console.warn('Failed to save restored draft message:', err)
            )
        },

        // Expanded groups actions

        /**
         * Toggle expanded state of a group.
         * @param {string} sessionId
         * @param {number} groupHeadLineNum - line_num of the group head item
         */
        toggleExpandedGroup(sessionId, groupHeadLineNum) {
            // Ensure array exists for this session
            if (!this.localState.sessionExpandedGroups[sessionId]) {
                this.localState.sessionExpandedGroups[sessionId] = []
            }

            const groups = this.localState.sessionExpandedGroups[sessionId]
            const index = groups.indexOf(groupHeadLineNum)

            if (index >= 0) {
                // Collapse: remove from array
                groups.splice(index, 1)
            } else {
                // Expand: add to array
                groups.push(groupHeadLineNum)
            }

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Expand a group (idempotent).
         * @param {string} sessionId
         * @param {number} groupHeadLineNum - line_num of the group head item
         */
        expandGroup(sessionId, groupHeadLineNum) {
            if (!this.localState.sessionExpandedGroups[sessionId]) {
                this.localState.sessionExpandedGroups[sessionId] = []
            }
            const groups = this.localState.sessionExpandedGroups[sessionId]
            if (!groups.includes(groupHeadLineNum)) {
                groups.push(groupHeadLineNum)
            }
        },

        /**
         * Collapse a group (idempotent).
         * @param {string} sessionId
         * @param {number} groupHeadLineNum - line_num of the group head item
         */
        collapseGroup(sessionId, groupHeadLineNum) {
            const groups = this.localState.sessionExpandedGroups[sessionId]
            if (groups) {
                const index = groups.indexOf(groupHeadLineNum)
                if (index >= 0) {
                    groups.splice(index, 1)
                }
            }
        },

        /**
         * Collapse all groups for a session.
         * @param {string} sessionId
         */
        collapseAllGroups(sessionId) {
            this.localState.sessionExpandedGroups[sessionId] = []
        },

        // Detailed blocks actions (conversation mode per-block detail toggle)

        /**
         * Toggle a conversation block between conversation and detailed display mode.
         * @param {string} sessionId
         * @param {number} userMessageLineNum - line_num of the last user_message before the block
         */
        toggleBlockDetailedMode(sessionId, userMessageLineNum) {
            if (!this.localState.sessionDetailedBlocks[sessionId]) {
                this.localState.sessionDetailedBlocks[sessionId] = []
            }

            const blocks = this.localState.sessionDetailedBlocks[sessionId]
            const index = blocks.indexOf(userMessageLineNum)

            if (index >= 0) {
                // Collapse back to conversation mode: remove from array
                blocks.splice(index, 1)
            } else {
                // Expand to detailed mode: add to array
                blocks.push(userMessageLineNum)
            }

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Toggle the per-session debug view override (dev-mode header button).
         * When on, the session renders in DISPLAY_MODE.DEBUG regardless of the
         * global display mode; toggling off restores the global mode.
         * @param {string} sessionId
         */
        toggleSessionDebug(sessionId) {
            if (this.localState.sessionDebugOverride[sessionId]) {
                delete this.localState.sessionDebugOverride[sessionId]
            } else {
                this.localState.sessionDebugOverride[sessionId] = true
            }
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Ensure a conversation block is in detailed mode (expand without toggling).
         * No-op if the block is already expanded.
         * @param {string} sessionId
         * @param {number} userMessageLineNum - line_num of the last user_message before the block
         * @returns {boolean} true if the block was expanded (visual items recomputed)
         */
        ensureBlockDetailed(sessionId, userMessageLineNum) {
            if (!this.localState.sessionDetailedBlocks[sessionId]) {
                this.localState.sessionDetailedBlocks[sessionId] = []
            }

            const blocks = this.localState.sessionDetailedBlocks[sessionId]
            if (blocks.includes(userMessageLineNum)) {
                return false  // Already expanded
            }

            blocks.push(userMessageLineNum)
            this.recomputeVisualItems(sessionId)
            return true
        },

        /**
         * Toggle expanded state of an internal group within an ALWAYS item's content.
         * @param {string} sessionId
         * @param {number} lineNum - line_num of the ALWAYS item containing the group
         * @param {number} startIndex - startIndex of the internal group within content array
         */
        toggleInternalExpandedGroup(sessionId, lineNum, startIndex) {
            // Ensure nested structure exists
            if (!this.localState.sessionInternalExpandedGroups[sessionId]) {
                this.localState.sessionInternalExpandedGroups[sessionId] = {}
            }
            if (!this.localState.sessionInternalExpandedGroups[sessionId][lineNum]) {
                this.localState.sessionInternalExpandedGroups[sessionId][lineNum] = []
            }

            const groups = this.localState.sessionInternalExpandedGroups[sessionId][lineNum]
            const index = groups.indexOf(startIndex)

            if (index >= 0) {
                // Collapse: remove from array
                groups.splice(index, 1)
            } else {
                // Expand: add to array
                groups.push(startIndex)
            }
        },

        /**
         * Load metadata for all items in a session (without content).
         * @param {string} projectId
         * @param {string} sessionId
         * @param {string|null} parentSessionId - If provided, this is a subagent request
         * @returns {Promise<Array|null>} Array of metadata objects or null on error
         */
        async loadSessionMetadata(projectId, sessionId, parentSessionId = null) {
            // Build URL (handle subagent case)
            const baseUrl = parentSessionId
                ? `/api/projects/${projectId}/sessions/${parentSessionId}/subagent/${sessionId}`
                : `/api/projects/${projectId}/sessions/${sessionId}`

            try {
                const res = await apiFetch(`${baseUrl}/items/metadata/`)
                if (!res.ok) {
                    console.error('Failed to load session metadata:', res.status, res.statusText)
                    return null
                }
                return await res.json()
            } catch (error) {
                console.error('Failed to load session metadata:', error)
                return null
            }
        },

        /**
         * Initialize sessionItems array from metadata (no content).
         * @param {string} sessionId
         * @param {Array} metadata - Array of { line_num, display_level, group_head, group_tail }
         */
        initSessionItemsFromMetadata(sessionId, metadata) {
            this.sessionItems[sessionId] = metadata.map(m => ({
                line_num: m.line_num,
                display_level: m.display_level,
                group_head: m.group_head,
                group_tail: m.group_tail,
                kind: m.kind,
                timestamp: m.timestamp ?? null,  // ISO 8601; available before content loads
                content: null  // Will be filled by content fetch
            }))

            // Compute visual items after initialization
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Update existing session items with fetched content.
         * @param {string} sessionId
         * @param {Array} items - Array of { line_num, content, display_level, group_head, group_tail, kind }
         */
        updateSessionItemsContent(sessionId, items) {
            const sessionItemsArray = this.sessionItems[sessionId]
            if (!sessionItemsArray) return

            const updatedItems = []
            for (const item of items) {
                const index = item.line_num - 1  // line_num is 1-based
                if (sessionItemsArray[index]) {
                    // Update content and invalidate parsed content cache
                    sessionItemsArray[index].content = item.content
                    clearParsedContent(sessionItemsArray[index])
                    // Also update metadata in case it was computed after initial load
                    if (item.display_level != null) {
                        sessionItemsArray[index].display_level = item.display_level
                    }
                    if (item.group_head != null) {
                        sessionItemsArray[index].group_head = item.group_head
                    }
                    if (item.group_tail != null) {
                        sessionItemsArray[index].group_tail = item.group_tail
                    }
                    if (item.kind !== undefined) {
                        sessionItemsArray[index].kind = item.kind
                    }
                    if (item.timestamp !== undefined) {
                        sessionItemsArray[index].timestamp = item.timestamp
                    }
                    updatedItems.push(sessionItemsArray[index])
                }
            }

            this.clearOptimisticMessageIfMatched(sessionId, updatedItems)

            // Recompute visual items in case metadata changed
            this.recomputeVisualItems(sessionId)
        },

        // Tab management actions

        /**
         * Add a tab to a session's open tabs.
         * @param {string} sessionId - The session ID
         * @param {string} tabId - The tab ID to add (e.g., 'agent-xxx')
         */
        addSessionTab(sessionId, tabId) {
            if (!this.localState.sessionOpenTabs[sessionId]) {
                this.localState.sessionOpenTabs[sessionId] = {
                    tabs: ['main'],
                    activeTab: 'main'
                }
            }
            const state = this.localState.sessionOpenTabs[sessionId]
            if (!state.tabs.includes(tabId)) {
                state.tabs.push(tabId)
            }
        },

        /**
         * Remove a tab from a session's open tabs.
         * @param {string} sessionId - The session ID
         * @param {string} tabId - The tab ID to remove (e.g., 'agent-xxx')
         */
        removeSessionTab(sessionId, tabId) {
            const state = this.localState.sessionOpenTabs[sessionId]
            if (!state) return

            const index = state.tabs.indexOf(tabId)
            if (index > -1) {
                state.tabs.splice(index, 1)
            }
        },

        /**
         * Set the active tab for a session.
         * @param {string} sessionId - The session ID
         * @param {string} tabId - The active tab ID
         */
        setSessionActiveTab(sessionId, tabId) {
            if (!this.localState.sessionOpenTabs[sessionId]) {
                this.localState.sessionOpenTabs[sessionId] = {
                    tabs: ['main'],
                    activeTab: 'main'
                }
            }
            this.localState.sessionOpenTabs[sessionId].activeTab = tabId
        },

        /**
         * Clear saved tabs for a session.
         * @param {string} sessionId - The session ID
         */
        clearSessionOpenTabs(sessionId) {
            delete this.localState.sessionOpenTabs[sessionId]
        },

        // ---- Dockable layout (per-session intention; persisted to Session.layout, synced) ----

        /** Ensure a layout-intention record exists for a session, return it. Seeded once (tolerant
         *  merge) from the persisted ``Session.layout`` so a saved layout applies on load. */
        ensureSessionLayout(sessionId) {
            if (!this.localState.sessionLayout[sessionId]) {
                this.localState.sessionLayout[sessionId] = hydrateLayoutIntention(this.sessions[sessionId]?.layout)
            }
            return this.localState.sessionLayout[sessionId]
        },

        /** Assign a tab to a dock, or back to the center ('center' / null clears the entry). */
        setTabDock(sessionId, tabId, dest) {
            const layout = this.ensureSessionLayout(sessionId)
            if (!dest || dest === 'center') {
                delete layout.assignment[tabId]
            } else {
                layout.assignment[tabId] = dest
            }
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Atomically place and order a draggable tool tab. One global order is filtered by dock at
         *  render time, so it also orders merged regions, gutters and overlays. */
        moveLayoutTab(sessionId, { tabId, dest, tabOrder, restoreDestination = true }) {
            const layout = this.ensureSessionLayout(sessionId)
            if (!dest || dest === 'center') delete layout.assignment[tabId]
            else layout.assignment[tabId] = dest
            layout.tabOrder = [...tabOrder]
            // A drag onto a minimized destination is an explicit restore/create action: reveal it.
            if (restoreDestination && dest && dest !== 'center') {
                const collapsedIndex = layout.collapsed.indexOf(dest)
                if (collapsedIndex > -1) layout.collapsed.splice(collapsedIndex, 1)
            }
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Minimize a dock to its edge gutter. */
        minimizeDock(sessionId, dockId) {
            const layout = this.ensureSessionLayout(sessionId)
            if (!layout.collapsed.includes(dockId)) layout.collapsed.push(dockId)
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Restore a minimized dock from its gutter. */
        restoreDock(sessionId, dockId) {
            const layout = this.ensureSessionLayout(sessionId)
            const i = layout.collapsed.indexOf(dockId)
            if (i > -1) layout.collapsed.splice(i, 1)
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Which side wins under mutual exclusion (one column fits). */
        setLayoutActiveSide(sessionId, side) {
            this.ensureSessionLayout(sessionId).activeSide = side
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Which column has priority while resizing (the dragged side wins; the other is squeezed). */
        setLayoutActiveResize(sessionId, side) {
            this.ensureSessionLayout(sessionId).activeResize = side
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Set a draggable layout fraction (e.g. leftColFrac, bottomFrac) — fed to the resolver as a
         *  config override. Geometry stays computed; only the intention (the 0..1 fraction) is kept. */
        setLayoutResizeFraction(sessionId, key, value) {
            this.ensureSessionLayout(sessionId).resizeFractions[key] = value
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Remember the active tab within a region group (groupKey from groupKeyOf). */
        setLayoutGroupActiveTab(sessionId, groupKey, tabId) {
            this.ensureSessionLayout(sessionId).activeByGroup[groupKey] = tabId
            this.persistSessionLayoutDebounced(sessionId)
        },

        /** Maximize a region (the array of dockIds it holds, or ['center']) to fill the layout area,
         *  or pass null to restore. Transient view state — the only exit is restore. NOT persisted. */
        setLayoutMaximized(sessionId, dockIds) {
            this.ensureSessionLayout(sessionId).maximized = dockIds && dockIds.length ? dockIds : null
        },

        /** Schedule a debounced persist of the layout intention (Session.layout). Coalesces resize-drag
         *  bursts and discrete place/minimize edits. Real sessions PATCH the backend; drafts (no backend
         *  row) get the stripped intention mirrored onto the in-memory ``session.layout`` RIGHT NOW —
         *  before the debounce — so any layout re-hydrate (notably the optimistic ``updateSession`` from
         *  a throttled ``session_viewed``, which fires ~30s after the draft is first viewed and then
         *  every 30s) is a no-op instead of snapping the draft back to its frozen creation-time seed.
         *  The slower IndexedDB snapshot (reload durability) rides the debounced flush below. */
        persistSessionLayoutDebounced(sessionId) {
            const session = this.sessions[sessionId]
            if (!session) return
            if (session.draft) {
                const intention = this.localState.sessionLayout[sessionId]
                if (intention) session.layout = stripLayoutForPersist(intention)
            }
            layoutPersistPending.add(sessionId)
            if (!layoutPersistDebouncers.has(sessionId)) {
                layoutPersistDebouncers.set(
                    sessionId,
                    debounce((id) => this.persistSessionLayout(id), LAYOUT_PERSIST_DEBOUNCE_MS),
                )
            }
            layoutPersistDebouncers.get(sessionId)(sessionId)
        },

        /** Flush the stripped intention (no transient ``maximized``) to its store: a backend PATCH for a
         *  real session, the IndexedDB draft record for a draft (whose ``session.layout`` mirror is kept
         *  up to date synchronously in ``persistSessionLayoutDebounced``). */
        async persistSessionLayout(sessionId) {
            const session = this.sessions[sessionId]
            const intention = this.localState.sessionLayout[sessionId]
            if (!session || !intention) {
                layoutPersistPending.delete(sessionId)
                return
            }
            const payload = stripLayoutForPersist(intention)
            if (session.draft) {
                // No backend row — re-affirm the mirror (in case the debounce coalesced several edits)
                // and snapshot the whole draft to IndexedDB so the customization survives a reload.
                session.layout = payload
                layoutPersistPending.delete(sessionId)
                this._saveDraftToIndexedDB(sessionId)
                return
            }
            if (!session.project_id) {
                layoutPersistPending.delete(sessionId)
                return
            }
            try {
                const response = await apiFetch(
                    `/api/projects/${session.project_id}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ layout: payload }),
                    },
                )
                if (response.ok) {
                    // Reflect locally so the echoed ``session_updated`` matches (no re-hydrate).
                    if (this.sessions[sessionId]) this.sessions[sessionId].layout = payload
                } else {
                    console.error('Failed to persist session layout', sessionId, response.status)
                }
            } catch (e) {
                console.error('Failed to persist session layout', sessionId, e)
            } finally {
                layoutPersistPending.delete(sessionId)
            }
        },

        /** Re-seed the working copy from a persisted ``Session.layout`` (initial load / cross-device
         *  sync), unless an unsaved local change is in flight (ours wins). Only acts on a session
         *  already being viewed (has a working copy); otherwise ensureSessionLayout seeds it lazily. */
        _hydrateSessionLayoutFromPersisted(sessionId, persisted) {
            if (persisted === undefined) return
            const cur = this.localState.sessionLayout[sessionId]
            if (!cur) return
            if (layoutPersistPending.has(sessionId)) return
            const next = hydrateLayoutIntention(persisted)
            if (JSON.stringify(stripLayoutForPersist(cur)) === JSON.stringify(stripLayoutForPersist(next))) return
            this.localState.sessionLayout[sessionId] = next
        },

        /** Drop all layout intention for a session (cancel any pending persist). */
        clearSessionLayout(sessionId) {
            const d = layoutPersistDebouncers.get(sessionId)
            if (d) d.cancel()
            layoutPersistDebouncers.delete(sessionId)
            layoutPersistPending.delete(sessionId)
            delete this.localState.sessionLayout[sessionId]
        },

        /** Replace a session's live layout with a copy of a catalog layout's template intention
         *  (assignment / collapsed / resizeFractions / tabOrder). Runtime fields and maximized reset to defaults.
         *  ``intention`` = {} ⇒ single pane. Persists (debounced). */
        loadLayoutIntoSession(sessionId, intention) {
            const e = emptyLayoutIntention()
            const t = layoutTemplate(intention)
            e.assignment = t.assignment
            e.collapsed = t.collapsed
            e.resizeFractions = t.resizeFractions
            e.tabOrder = t.tabOrder
            this.localState.sessionLayout[sessionId] = e
            this.persistSessionLayoutDebounced(sessionId)
        },

        // Agent links cache actions

        /**
         * Set an agent link in the cache.
         * @param {string} sessionId - The session ID
         * @param {string} toolId - The tool_use_id
         * @param {string} agentId - The agent ID (only cache when found)
         * @param {boolean} isBackground - Whether the agent runs in background
         * @param {?number} toolUseLineNum - Line of the spawning tool_use
         * @param {?string} slug - Spawned subagent's nickname (Codex
         *   ``agent_nickname`` persisted as ``Session.slug``). Joined
         *   into the AgentLink payload at the API / WS boundary so
         *   downstream code can label tab headers / tool-card summaries
         *   without separately hydrating the subagent Session row.
         */
        setAgentLink(sessionId, toolId, agentId, isBackground = false, toolUseLineNum = null, slug = null, stoppedAt = null) {
            if (!agentId) return // Only cache found agents
            if (!this.localState.agentLinks[sessionId]) {
                this.localState.agentLinks[sessionId] = {}
            }
            // ``stoppedAt``: the subagent's own file reported it idle (see the
            // backend's ``subagent_turn_boundary``). Only the load path knows
            // it — a link is created at spawn time, when nothing has stopped
            // yet — so the live view reads ``sessions[agentId].last_stopped_at`
            // instead, which the subagent's own ``session_updated`` refreshes.
            this.localState.agentLinks[sessionId][toolId] = { agentId, isBackground, toolUseLineNum, slug, stoppedAt }
        },

        /**
         * Clear agent links cache for a session.
         * @param {string} sessionId - The session ID
         */
        clearAgentLinks(sessionId) {
            delete this.localState.agentLinks[sessionId]
        },

        setWorkflowLink(sessionId, toolId, runId) {
            if (!runId) return
            if (!this.localState.workflowLinks[sessionId]) {
                this.localState.workflowLinks[sessionId] = {}
            }
            this.localState.workflowLinks[sessionId][toolId] = runId
        },

        clearWorkflowLinks(sessionId) {
            delete this.localState.workflowLinks[sessionId]
        },

        /**
         * Set tool state for a tool_use_id in a session.
         * @param {string} sessionId - The session ID
         * @param {string} toolUseId - The tool_use_id
         * @param {number} resultCount - The number of tool_results received
         * @param {string|null} completedAt - ISO timestamp of the latest tool_result
         * @param {string|null} error - Error message if the tool errored
         * @param {string|null} extra - Extra JSON data (e.g., file change stats)
         * @param {number[]} toolResultLineNums - Line numbers of every tool_result row, ordered ASC
         */
        setToolState(sessionId, toolUseId, resultCount, completedAt, error = null, extra = null, toolResultLineNums = []) {
            if (!this.localState.toolStates[sessionId]) {
                this.localState.toolStates[sessionId] = {}
            }
            this.localState.toolStates[sessionId][toolUseId] = { resultCount, completedAt, error, extra, toolResultLineNums }
        },

        /**
         * Mark session items as live (arrived via WebSocket in real-time).
         * @param {string} sessionId - The session ID
         * @param {number[]} lineNums - Line numbers of items that arrived via WebSocket
         */
        markItemsLive(sessionId, lineNums) {
            if (!lineNums?.length) return
            if (!this.localState.liveItems[sessionId]) {
                this.localState.liveItems[sessionId] = new Set()
            }
            for (const ln of lineNums) {
                this.localState.liveItems[sessionId].add(ln)
            }
        },

        /**
         * Fetch tool states for a session from the API.
         * Populates the toolStates cache.
         *
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         */
        async fetchToolStates(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/tool-states/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const data = await response.json()
                if (data.tools && Object.keys(data.tools).length > 0) {
                    const states = {}
                    for (const [toolUseId, state] of Object.entries(data.tools)) {
                        states[toolUseId] = {
                            resultCount: state.result_count,
                            completedAt: state.completed_at,
                            error: state.error ?? null,
                            extra: state.extra ?? null,
                            toolResultLineNums: Array.isArray(state.tool_result_line_nums)
                                ? state.tool_result_line_nums
                                : [],
                        }
                    }
                    this.localState.toolStates[sessionId] = states
                }
            } catch (error) {
                console.error('Failed to fetch tool states:', error)
            }
        },

        // Open details state actions (persisted across virtual scroller mount/unmount)

        /**
         * Set or clear the open state of a wa-details panel.
         * @param {string} sessionId - The session ID
         * @param {string} key - Unique key (toolId, `result:${toolId}`, etc.)
         * @param {boolean} open - Whether the panel is open
         */
        setDetailOpen(sessionId, key, open) {
            if (open) {
                if (!this.localState.openDetails[sessionId]) {
                    this.localState.openDetails[sessionId] = {}
                }
                this.localState.openDetails[sessionId][key] = true
            } else {
                if (this.localState.openDetails[sessionId]) {
                    delete this.localState.openDetails[sessionId][key]
                }
            }
        },

        // Subagent state actions

        /**
         * Set a synthetic process state for a subagent (assistant_turn).
         * Does not overwrite real (non-synthetic) process states.
         * Triggers recomputeVisualItems only if the session's items are loaded
         * and the assistant_turn status actually changed.
         *
         * @param {string} agentSessionId - The subagent session ID
         * @param {string} parentSessionId - The parent session that spawned the subagent
         *   (its ``provider`` is inherited by the synthetic state).
         * @param {string} projectId - The project ID
         * @param {number|null} startedAtUnix - Unix timestamp (seconds) of when the agent started
         */
        setSyntheticProcessState(agentSessionId, parentSessionId, projectId, startedAtUnix) {
            // Don't overwrite real process states (from ProcessManager)
            if (this.processStates[agentSessionId] && !this.processStates[agentSessionId].synthetic) {
                return
            }
            const provider = this.getSessionProvider(parentSessionId)
            if (!provider) {
                console.warn('[setSyntheticProcessState] no provider for parent session', parentSessionId)
                return
            }
            const wasAssistantTurn = this.processStates[agentSessionId]?.state === PROCESS_STATE.ASSISTANT_TURN
            this.processStates[agentSessionId] = {
                state: PROCESS_STATE.ASSISTANT_TURN,
                project_id: projectId,
                provider,
                started_at: startedAtUnix,
                state_changed_at: startedAtUnix,
                memory: null,
                error: null,
                pending_requests: [],
                session_title: null,
                project_name: null,
                synthetic: true,
            }
            if (!wasAssistantTurn && this.sessionItems[agentSessionId]) {
                this.recomputeVisualItems(agentSessionId)
            }
        },

        /**
         * Clean up synthetic process states for child agents that predate the session's
         * lifecycle cutoff (max of last_started_at, last_stopped_at)).
         * Called reactively when session lifecycle timestamps change in updateSession.
         *
         * @param {Object} session - The session object (with last_started_at, last_stopped_at)
         */
        _cleanStaleChildSynthetics(session) {
            const links = this.localState.agentLinks[session.id]
            if (!links) return
            const cutoff = getSessionCutoffMs(session)
            if (!cutoff) return
            for (const { agentId } of Object.values(links)) {
                const ps = this.processStates[agentId]
                if (!ps?.synthetic) continue
                // started_at is in seconds, cutoff in ms
                const startedMs = ps.started_at ? ps.started_at * 1000 : 0
                if (startedMs < cutoff) {
                    this.removeSyntheticProcessState(agentId)
                }
            }
        },

        /**
         * Remove a synthetic process state for a subagent.
         * Only removes if the process state is synthetic (not a real process).
         * Triggers recomputeVisualItems only if the session's items are loaded.
         *
         * @param {string} agentSessionId - The subagent session ID
         */
        removeSyntheticProcessState(agentSessionId) {
            const ps = this.processStates[agentSessionId]
            if (!ps?.synthetic) return
            const wasAssistantTurn = ps.state === PROCESS_STATE.ASSISTANT_TURN
            delete this.processStates[agentSessionId]
            if (wasAssistantTurn && this.sessionItems[agentSessionId]) {
                this.recomputeVisualItems(agentSessionId)
            }
        },

        /**
         * Fetch and set synthetic process states for all subagents of a session.
         * Called at session load time when the session has a process in assistant_turn.
         * Creates synthetic processState entries for agents that are not done.
         *
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The parent session ID
         */
        async fetchWorkflowLinks(projectId, sessionId) {
            // Couples {tool_use_id, run_id} for Workflow tool_uses, so the chat
            // can show "View Workflow". Best-effort: a failure just defers the
            // button to the next load / the workflow_link_created WS event.
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/workflow-links/`
                const response = await apiFetch(url)
                if (!response.ok) return
                const links = await response.json()
                for (const link of links) {
                    this.setWorkflowLink(sessionId, link.tool_use_id, link.run_id)
                }
            } catch {
                // ignore
            }
        },

        async fetchSubagentsState(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/subagents/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const agents = await response.json()

                // Cutoff: agents started before this are definitely not running
                const cutoff = getSessionCutoffMs(this.sessions[sessionId])

                for (const agent of agents) {
                    this.setAgentLink(sessionId, agent.tool_use_id, agent.agent_id, agent.is_background, agent.tool_use_line_num, agent.agent_slug ?? null, agent.agent_stopped_at ?? null)

                    // Skip synthetic process state if agent predates the session's last start/stop cycle
                    const agentStartedMs = agent.started_at ? new Date(agent.started_at).getTime() : 0
                    if (cutoff && agentStartedMs < cutoff) continue

                    // …or if the subagent's own file already reported it idle. Its
                    // parent's tool chain may never complete (Codex multi-agent v2:
                    // a subagent answering through send_message produces no second
                    // result), so the result count below would resurrect a
                    // "running" indicator for a subagent that finished long ago.
                    if (agent.agent_stopped_at) continue

                    // Create synthetic process state if agent is not done yet
                    const toolState = this.localState.toolStates[sessionId]?.[agent.tool_use_id]
                    const resultCount = toolState?.resultCount || 0
                    const requiredCount = agent.is_background ? 2 : 1
                    if (resultCount < requiredCount) {
                        const startedAtUnix = agent.started_at ? new Date(agent.started_at).getTime() / 1000 : null
                        this.setSyntheticProcessState(agent.agent_id, sessionId, projectId, startedAtUnix)
                    }
                }
            } catch (error) {
                console.error('Failed to fetch subagents state:', error)
            }
        },

        /**
         * Re-hydrate the derived link caches (tool states, subagent links,
         * workflow links) for a single already-loaded session.
         *
         * These caches are normally filled once on first load (SessionItemsList)
         * and then kept live by the WS ``tool_state`` / ``agent_link_created`` /
         * ``workflow_link_created`` broadcasts. Two situations leave them stale
         * with no live signal to fix them, so a tool spinner keeps spinning even
         * though the matching tool_result rows are present:
         *   - a WebSocket outage drops the broadcasts (the watcher never replays
         *     a line it already processed), and
         *   - a session whose compute lagged behind its items had its links built
         *     after the (dropped) broadcasts, or by the batch compute which does
         *     not broadcast at all.
         * Pulling the authoritative counts from the REST endpoints settles them.
         *
         * Order matters: ``fetchSubagentsState`` reads ``toolStates`` to decide
         * whether an agent is still running, so tool states are refreshed first.
         * Subagent / workflow links only exist on parent sessions — mirror the
         * first-load gating in ``SessionItemsList``.
         */
        async refreshSessionToolStates(projectId, sessionId) {
            await this.fetchToolStates(projectId, sessionId)
            const session = this.sessions[sessionId]
            if (session && !session.parent_session_id) {
                await this.fetchSubagentsState(projectId, sessionId)
                if (session.has_workflows) {
                    await this.fetchWorkflowLinks(projectId, sessionId)
                }
            }
        },

        /**
         * Refresh the link caches (see :meth:`refreshSessionToolStates`) for
         * EVERY session whose items are currently loaded — the only sessions
         * that can render a tool spinner. Called after a WebSocket reconnect so
         * the ``tool_state`` / ``agent_link_created`` broadcasts dropped during
         * the outage are recovered everywhere they matter, not just for the
         * focused session (other open panes, or sessions whose mtime read
         * unchanged because a concurrent ``session_updated`` refreshed it).
         */
        async refreshAllLoadedToolStates() {
            const refs = []
            for (const [sessionId, local] of Object.entries(this.localState.sessions)) {
                if (!local?.itemsFetched) continue
                const projectId = this.sessions[sessionId]?.project_id
                if (projectId) refs.push({ projectId, sessionId })
            }
            await Promise.allSettled(
                refs.map(({ projectId, sessionId }) => this.refreshSessionToolStates(projectId, sessionId)),
            )
        },

        // Process state actions

        /**
         * Optimistically mark a session as "stopping" so the spinner reacts to
         * the click immediately, before the backend confirms. The backend is the
         * source of truth from here on: it carries the flag on every
         * `process_state` and in the `active_processes` snapshot, so the spinner
         * survives a WS reconnect / page refresh and only drops when the process
         * actually dies (entry removed). No-op if there is no active process.
         * @param {string} sessionId
         */
        setSessionStopping(sessionId) {
            const ps = this.processStates[sessionId]
            if (!ps) return
            this.processStates[sessionId] = { ...ps, stopping: true }
        },

        /**
         * Set process state for a session (from WebSocket process_state message).
         * Removes the entry when state is 'dead'.
         * @param {string} sessionId
         * @param {string} projectId - The project ID this session belongs to
         * @param {string} state - 'starting' | 'assistant_turn' | 'user_turn' | 'dead'
         * @param {object} extra - Additional fields: provider, started_at, state_changed_at, memory, error, pending_requests, session_title, project_name
         */
        setProcessState(sessionId, projectId, state, extra = {}) {
            const previousState = this.processStates[sessionId]?.state
            const wasAssistantTurn = previousState === PROCESS_STATE.ASSISTANT_TURN
            const wasStarting = previousState === PROCESS_STATE.STARTING
            // Keep an in-flight "stopping" flag across non-dead transitions so
            // the spinner reflects the whole kill, not just the first state
            // change the interrupt produces (e.g. assistant_turn → user_turn).
            // The backend carries `stopping` on the message; we OR it with the
            // optimistic local flag so neither a not-yet-confirmed click nor a
            // pre-kill broadcast can drop it. It only clears on `dead` below.
            const wasStopping = this.processStates[sessionId]?.stopping === true
            // Status-line override (WorkingAssistantMessage's text: "compacting",
            // "waiting for 2 subagents", …). The backend recomputes it on every
            // snapshot, so when it says something that value wins — including
            // right after a reconnect or a page load, where the one-shot
            // `process_label` message that carried it is long gone.
            //
            // It is omitted from the payload when there is none, which is also
            // what a plain state refresh looks like — so fall back to the label
            // already on screen when the state itself did not change. Wiping is
            // the intended behaviour on a real transition (that IS how a label
            // disappears at turn end), but a pending request landing, a memory
            // or title update, a `stopping` flag all re-broadcast the same state
            // mid-work and must not drop a label the agent still considers
            // current.
            const keptLabel = state === previousState
                ? this.processStates[sessionId]?.label ?? null
                : null

            if (state === 'dead') {
                // Remove dead processes from the map
                delete this.processStates[sessionId]
                // Clean up any lingering streaming blocks and buffers
                const lingering = this.localState.streamingBlocks[sessionId]
                if (lingering) {
                    for (const block of lingering.blocks) {
                        clearBlockInactivityTimer(block)
                    }
                }
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
                // The process is gone, and so are its pending requests and the
                // forms that carried them — drop whatever answers the user had
                // started typing into them. Waiting for the next
                // ``active_processes`` sweep would leave them behind for as
                // long as this tab stays connected.
                sweepPendingRequestDrafts(new Set(), Date.now(), sessionId).catch(err =>
                    console.warn('Failed to sweep pending request drafts from IndexedDB:', err)
                )
            } else {
                this.processStates[sessionId] = {
                    state,
                    project_id: projectId,
                    provider: extra.provider || null,
                    started_at: extra.started_at || null,
                    state_changed_at: extra.state_changed_at || null,
                    memory: extra.memory || null,
                    error: extra.error || null,
                    pending_requests: extra.pending_requests || [],
                    active_crons: extra.active_crons || null,
                    session_title: extra.session_title || null,
                    project_name: extra.project_name || null,
                    // Provider/mode-specific live bag (e.g. hybrid's
                    // {mode, terminal_blocked}). The options arg is itself
                    // named ``extra``; ``extra.extra`` is the serialized field.
                    extra: extra.extra || null,
                    tools: [],
                    lastStartedToolId: null,
                    // Backend truth OR optimistic local flag (see `wasStopping`).
                    stopping: extra.stopping === true || wasStopping,
                    // Backend truth when it carries one, else the label already
                    // on screen (see `keptLabel`).
                    label: extra.label ?? keptLabel,
                }

                // Auto-unarchive: running and archived are mutually exclusive.
                // But a stop-in-progress is NOT a start: the `stopping`
                // process_state broadcast before the (up-to-30s) kill must not
                // resurrect the session the user just archived (archiving stops
                // the process, which emits exactly this non-dead broadcast).
                // Only a genuine (re)start (stopping=false) un-archives.
                const session = this.sessions[sessionId]
                if (session?.archived && projectId && !this.processStates[sessionId].stopping) {
                    this.setSessionArchived(projectId, sessionId, false)
                }

                // A turn that ends without a clean stream end (soft interrupt,
                // turn error) strands its in-flight blocks: the SDK cuts the
                // stream mid-block, so neither stream_block_stop (the thinking
                // spinner keeps spinning) nor stream_block_end (uuid stays
                // null, so no real item can ever retire the block) will
                // arrive. Drop those orphans when the session leaves
                // assistant_turn. The recompute below always fires for this
                // transition, repainting without the synthetic block.
                if (wasAssistantTurn && state !== PROCESS_STATE.ASSISTANT_TURN) {
                    this._dropOrphanedStreamingBlocks(sessionId)
                }
            }

            // Recompute visual items when isAssistantTurn or isStarting changes
            // (controls the synthetic working/starting messages and conversation mode filtering)
            const isStarting = state === PROCESS_STATE.STARTING
            const isAssistantTurn = state === PROCESS_STATE.ASSISTANT_TURN
            if (wasAssistantTurn !== isAssistantTurn || wasStarting !== isStarting) {
                this.recomputeVisualItems(sessionId)
            }
        },

        /**
         * Initialize process states from WebSocket active_processes message.
         * Called on connection to sync with backend.
         * @param {Array<{session_id: string, project_id: string, state: string, started_at?: number, state_changed_at?: number, memory?: number, session_title?: string, project_name?: string}>} processes
         */
        setActiveProcesses(processes) {
            // Stamped before anything else: the draft sweep at the bottom uses
            // it as the cut-off for "could this entry have been in the
            // snapshot?".
            const snapshotAt = Date.now()

            // Sessions showing synthetic/live UI before the rebuild. Neither
            // clearing streaming blocks nor ending an assistant turn recomputes
            // visual items on its own, so after a reconnect a session that was
            // streaming (or mid-turn) when the socket dropped keeps its stale
            // "thinking" block / working placeholder on screen until some
            // unrelated event recomputes it (the "frozen until manual reload"
            // reconnect bug). Collect them now; recompute after the rebuild.
            const sessionsToRecompute = new Set(Object.keys(this.localState.streamingBlocks))
            for (const [sid, st] of Object.entries(this.processStates)) {
                if (st.state === PROCESS_STATE.ASSISTANT_TURN || st.state === PROCESS_STATE.STARTING) {
                    sessionsToRecompute.add(sid)
                }
            }

            // Clear existing states and rebuild from server data
            this.processStates = {}
            // Clear stale streaming blocks and buffers from previous connection
            destroyAllBuffers()
            this.localState.streamingBlocks = {}
            for (const p of processes) {
                // Only add non-dead processes
                if (p.state !== 'dead') {
                    this.processStates[p.session_id] = {
                        state: p.state,
                        project_id: p.project_id,
                        provider: p.provider || null,
                        started_at: p.started_at || null,
                        state_changed_at: p.state_changed_at || null,
                        memory: p.memory || null,
                        error: p.error || null,
                        pending_requests: p.pending_requests || [],
                        active_crons: p.active_crons || null,
                        session_title: p.session_title || null,
                        project_name: p.project_name || null,
                        // Provider/mode-specific live bag (hybrid's
                        // {mode, terminal_blocked}) — kept in the initial
                        // snapshot so a late-opened client sees it too.
                        extra: p.extra || null,
                        tools: Array.isArray(p.active_tools) ? p.active_tools : [],
                        lastStartedToolId: p.last_started_tool_id || null,
                        // Restore an in-flight stop from the snapshot so the
                        // spinner survives this (re)connect / page refresh.
                        stopping: p.stopping === true,
                        // Same for the status-line override: the agent
                        // recomputes it for every snapshot, so a client that
                        // arrives while a turn is held for background work
                        // learns why instead of showing a bare "thinking".
                        label: p.label || null,
                    }

                    // Auto-unarchive: running and archived are mutually exclusive.
                    // Skip a stopping process (see handleProcessState): a
                    // stop-in-progress snapshot landing during the kill window
                    // (or a reconnect mid-archive) must not undo a fresh archive.
                    const session = this.sessions[p.session_id]
                    if (session?.archived && p.project_id && p.stopping !== true) {
                        this.setSessionArchived(p.project_id, p.session_id, false)
                    }
                }
            }

            // Recompute the sessions we touched now that streaming blocks are
            // gone and process states match the server: drops orphaned synthetic
            // streaming items and stale working/starting placeholders left over
            // from a turn that ended (or got cut) while the socket was down.
            // Only sessions with a cached visual list can be showing a stale
            // synthetic item, so skip the rest (recomputeVisualItems would
            // otherwise materialise an empty entry for never-rendered sessions).
            for (const sid of sessionsToRecompute) {
                if (this.localState.sessionVisualItems[sid]) {
                    this.recomputeVisualItems(sid)
                }
            }

            // Garbage-collect the pending-request drafts (utils/
            // pendingRequestDraftStorage.js). This snapshot is the only moment
            // the client knows the COMPLETE set of live requests, so it is the
            // authoritative sweep: after a backend restart every process is
            // gone, the snapshot is empty, and the whole store is dropped.
            // ``snapshotAt`` keeps a draft written for a request that appeared
            // after the snapshot was built — it cannot be in ``liveKeys``, but
            // it is not stale either.
            const liveKeys = new Set()
            for (const p of processes) {
                if (p.state === 'dead') continue
                for (const request of p.pending_requests || []) {
                    liveKeys.add(liveDraftKey(p.session_id, request.request_id))
                }
            }
            sweepPendingRequestDrafts(liveKeys, snapshotAt).catch(err =>
                console.warn('Failed to sweep pending request drafts from IndexedDB:', err)
            )
        },

        // ── Streaming blocks ─────────────────────────────────────────────

        /**
         * Handle a stream_block_start event from the SDK.
         * Creates or resets the streaming state for this session/message,
         * then adds the new block entry.
         */
        streamBlockStart(sessionId, messageId, blockIndex, blockType) {
            const existing = this.localState.streamingBlocks[sessionId]
            if (!existing || existing.messageId !== messageId) {
                // New message — start fresh (destroy any old buffers).
                // Before dropping the previous entry we close any of its
                // streaming detailKeys we may have left open: the next
                // synthetic block will land at the same negative ``lineNum``
                // and ``Reasoning.vue`` / ``ThinkingContent.vue`` initialize
                // ``isOpen`` from ``isDetailOpen``, so a stale ``true`` from
                // the previous reasoning would auto-open the next one.
                // ``_retireStreamingBlocks`` does the same reset when the
                // real SessionItem arrives — this branch handles the race
                // where the next stream starts before the previous JSONL
                // line lands (typical for back-to-back Codex reasonings).
                if (existing) {
                    const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
                    for (const oldBlock of existing.blocks) {
                        clearBlockInactivityTimer(oldBlock)
                        this.setDetailOpen(
                            sessionId,
                            `line:${baseLineNum - oldBlock.blockIndex}:0`,
                            false,
                        )
                    }
                }
                destroySessionBuffers(sessionId)
                this.localState.streamingBlocks[sessionId] = {
                    messageId,
                    blocks: [{ blockIndex, blockType, text: '', displayedText: '', stopped: false, uuid: null }],
                }
            } else {
                // Same message, additional block (e.g. thinking then text)
                existing.blocks.push({ blockIndex, blockType, text: '', displayedText: '', stopped: false, uuid: null })
            }

            // Initialize the adaptive buffer for this block
            initBuffer(sessionId, blockIndex, (displayedText) => {
                this._onBufferDrain(sessionId, blockIndex, displayedText)
            })

            this.recomputeVisualItems(sessionId)
        },

        /**
         * Handle a stream_block_delta event — append text to the current block.
         * Feeds the delta into the adaptive buffer which drains it smoothly
         * via requestAnimationFrame, patching the visual item on each frame.
         */
        streamBlockDelta(sessionId, messageId, blockIndex, text) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming || streaming.messageId !== messageId) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (!block) return

            block.text += text
            feedDelta(sessionId, blockIndex, text)

            // (Re)arm the inactivity timer for text blocks. If the SDK goes
            // quiet for STREAM_BLOCK_INACTIVITY_MS we'll flip ``stopped`` so
            // the WorkingAssistantMessage indicator reappears, even though
            // ``item/completed`` from Codex may still be seconds away. If a
            // delta arrives after the flip, we revert ``stopped`` back to
            // false so the indicator hides again while new content streams.
            // Thinking blocks are excluded: they don't gate the
            // WorkingAssistantMessage (``hasActiveTextStreaming`` only looks
            // at text blocks).
            if (block.blockType === 'text') {
                clearBlockInactivityTimer(block)
                if (block.stopped) {
                    block.stopped = false
                    this.recomputeVisualItems(sessionId)
                }
                block._inactivityTimer = setTimeout(() => {
                    block._inactivityTimer = null
                    if (block.stopped) return
                    block.stopped = true
                    this.recomputeVisualItems(sessionId)
                }, STREAM_BLOCK_INACTIVITY_MS)
            }
        },

        /**
         * Buffer drain callback — patches the streaming visual item with
         * the currently displayed text. Called from requestAnimationFrame
         * by the adaptive buffer.
         * @private
         */
        _onBufferDrain(sessionId, blockIndex, displayedText) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (!block) return

            block.displayedText = displayedText

            const { baseLineNum, kind: streamingSyntheticKind } = SYNTHETIC_ITEM.STREAMING_BLOCK
            const targetLineNum = baseLineNum - blockIndex
            const visualItems = this.localState.sessionVisualItems[sessionId]
            if (!visualItems) return

            const idx = visualItems.findIndex(vi => vi.lineNum === targetLineNum)
            if (idx === -1) return

            const contentBlock = block.blockType === 'thinking'
                ? { type: 'thinking', thinking: displayedText, streaming: !block.stopped }
                : { type: 'text', text: displayedText }
            const newParsed = {
                type: 'assistant',
                syntheticKind: streamingSyntheticKind,
                message: { role: 'assistant', content: [contentBlock] },
            }

            const newVi = { ...visualItems[idx] }
            setParsedContent(newVi, newParsed)
            visualItems[idx] = newVi

            const cache = this.localState.visualItemCache[sessionId]
            if (cache) cache.set(targetLineNum, newVi)
        },

        /**
         * Handle a stream_block_stop event — mark the block as stopped (text
         * is final but uuid not yet known). Flushes the buffer then triggers
         * recompute because the WorkingAssistantMessage visibility depends
         * on this flag.
         */
        streamBlockStop(sessionId, messageId, blockIndex) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming || streaming.messageId !== messageId) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (block) {
                // Don't flush the buffer here — let it keep draining naturally.
                // The remaining chars will be displayed over the next few hundred ms
                // before the real item arrives and retires the block.
                clearBlockInactivityTimer(block)
                block.stopped = true
                this.recomputeVisualItems(sessionId)
            }
        },

        /**
         * Handle a stream_block_end event — record the uuid so we can match
         * the real SessionItem when it arrives from the watcher.
         *
         * Also handles a race condition: the watcher's session_items_added may
         * arrive BEFORE this end event. In that case, _retireStreamingBlocks
         * already ran but couldn't match (uuid was null). We scan existing
         * session items for a retroactive match.
         */
        streamBlockEnd(sessionId, messageId, blockIndex, uuid) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming || streaming.messageId !== messageId) return
            const block = streaming.blocks.find(b => b.blockIndex === blockIndex)
            if (!block) return
            block.uuid = uuid

            // Retroactive match: the real item may already be in sessionItems
            const items = this.sessionItems[sessionId]
            if (!items) return
            for (let i = items.length - 1; i >= 0; i--) {
                const item = items[i]
                if (item.kind !== 'assistant_message' && item.kind !== 'content_items' && item.kind !== 'reasoning') continue
                // Provider-agnostic uuid path: when the backend stamped a
                // ``stream_uuid`` on the wire item (Codex live-sync), that
                // single field is sufficient — no need to parse content or
                // match message.id. For Claude the uuid lives inside the
                // parsed JSONL ``uuid`` field, gated by ``message.id``.
                if (item.stream_uuid === uuid) {
                    this._retireStreamingBlocks(sessionId, [item])
                    this.recomputeVisualItems(sessionId)
                    return
                }
                const parsed = getParsedContent(item)
                if (!parsed) continue
                if (parsed.message?.id !== messageId) continue
                if (parsed.uuid === uuid) {
                    this._retireStreamingBlocks(sessionId, [item])
                    this.recomputeVisualItems(sessionId)
                    return
                }
            }
        },

        /**
         * Try to retire streaming blocks whose real SessionItem has arrived.
         * Called from addSessionItems after new items are placed in the array.
         *
         * Match strategy (in order):
         *   1. ``item.stream_uuid`` (Codex live-sync) — the backend popped
         *      the streaming registry and stamped the SDK ``item_id`` on
         *      the wire payload. We retire the block whose uuid matches,
         *      no parsed content needed.
         *   2. Otherwise, parse the JSONL ``uuid`` and ``message.id``
         *      (Claude path) and match those against the streaming entry.
         */
        _retireStreamingBlocks(sessionId, newItems) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return []

            // Retired (streamingLineNum, realLineNum) pairs, returned so the UI
            // can bridge the swap (carry the streamed item's measured height
            // over to the real item before the recompute renders it).
            const retired = []

            for (const item of newItems) {
                if (item.kind !== 'assistant_message' && item.kind !== 'content_items' && item.kind !== 'reasoning') continue

                let itemUuid = item.stream_uuid
                let parsed = null
                if (!itemUuid) {
                    parsed = getParsedContent(item)
                    if (!parsed) continue
                    const itemMessageId = parsed.message?.id
                    if (itemMessageId !== streaming.messageId) continue
                    itemUuid = parsed.uuid
                    if (!itemUuid) continue
                }

                // Find and remove the matching block
                const idx = streaming.blocks.findIndex(b => b.uuid === itemUuid)
                if (idx !== -1) {
                    const block = streaming.blocks[idx]
                    retired.push({
                        streamingLineNum: SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex,
                        realLineNum: item.line_num,
                    })

                    // Transfer wa-details open state from streaming to real item
                    if (block.blockType === 'thinking') {
                        // Lazy parse: Codex went through the stream_uuid
                        // short-circuit and ``parsed`` may still be null. Claude
                        // already had it loaded by the parent loop.
                        if (!parsed) parsed = getParsedContent(item)
                        const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
                        const streamingDetailKey = `line:${baseLineNum - block.blockIndex}:0`
                        if (this.isDetailOpen(sessionId, streamingDetailKey)) {
                            // The real item's detailKey depends on the provider:
                            //   - Codex: the JSONL line itself *is* the reasoning
                            //     item (kind=reasoning), mono-block, ``:0`` suffix.
                            //   - Claude: the thinking sits inside an
                            //     assistant_message's content array; look up its
                            //     position to build ``line:${lineNum}:${idx}``.
                            let targetKey = null
                            if (item.kind === 'reasoning') {
                                targetKey = `line:${item.line_num}:0`
                            } else {
                                const content = parsed?.message?.content
                                if (Array.isArray(content)) {
                                    const thinkingIdx = content.findIndex(c => c.type === 'thinking')
                                    if (thinkingIdx !== -1) {
                                        targetKey = `line:${item.line_num}:${thinkingIdx}`
                                    }
                                }
                            }
                            if (targetKey) {
                                this.setDetailOpen(sessionId, targetKey, true)
                            }
                            this.setDetailOpen(sessionId, streamingDetailKey, false)
                        }
                        // Transfer expandedGroups state for fake-group case.
                        // If this thinking block was its own group_head (fake group)
                        // and the user expanded it, migrate that entry to the real
                        // item's group_head so expansion persists across the swap.
                        const expanded = this.localState.sessionExpandedGroups[sessionId]
                        if (expanded && expanded.length > 0) {
                            const streamingLineNum = SYNTHETIC_ITEM.STREAMING_BLOCK.baseLineNum - block.blockIndex
                            const idxInExpanded = expanded.indexOf(streamingLineNum)
                            if (idxInExpanded !== -1) {
                                // Determine the real group_head this thinking block
                                // belongs to. Look at the real item's group_head; if
                                // null (no group), drop the entry; else add the real
                                // group_head if not already there.
                                const realGroupHead = item.group_head
                                expanded.splice(idxInExpanded, 1)
                                if (realGroupHead != null && !expanded.includes(realGroupHead)) {
                                    expanded.push(realGroupHead)
                                }
                            }
                        }
                    }

                    clearBlockInactivityTimer(block)
                    flushBuffer(sessionId, block.blockIndex)
                    streaming.blocks.splice(idx, 1)
                }
            }

            // If all blocks retired, clean up
            if (streaming.blocks.length === 0) {
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
            }

            return retired
        },

        /**
         * Drop streaming blocks that already ended (``uuid`` set) but were
         * never retired by a matching ``session_items_added`` broadcast.
         *
         * The drop happens when the user is on a different session while
         * the canonical session's live items arrive: the WS handler skips
         * ``addSessionItems`` because ``itemsFetched`` is still false on
         * the canonical id, so ``_retireStreamingBlocks`` never runs. On
         * Codex specifically the retirement key is the wire-only
         * ``stream_uuid`` (not persisted), so by the time the user lands
         * on the session and items are fetched from the REST API, no
         * match is possible anymore and the synthetic ``streaming-block``
         * item would survive forever alongside the real ``agent_message``.
         *
         * Called from ``loadSessionData`` (SessionItemsList.vue) before
         * fetching items. Only ended blocks (``uuid !== null``) are
         * dropped, so active streaming visible when the user lands on a
         * session mid-turn keeps painting live deltas.
         */
        clearEndedStreamingBlocks(sessionId) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return

            const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
            const expanded = this.localState.sessionExpandedGroups[sessionId]
            const remaining = []
            let anyCleared = false
            for (const block of streaming.blocks) {
                if (block.uuid !== null) {
                    clearBlockInactivityTimer(block)
                    flushBuffer(sessionId, block.blockIndex)
                    // For thinking blocks: close the streaming detail key
                    // (otherwise a stale ``true`` for the synthetic lineNum
                    // would auto-open the next block landing at that slot)
                    // and drop the matching expandedGroups entry (no real
                    // item to migrate the expansion to — we never matched).
                    if (block.blockType === 'thinking') {
                        const streamingLineNum = baseLineNum - block.blockIndex
                        this.setDetailOpen(sessionId, `line:${streamingLineNum}:0`, false)
                        if (expanded && expanded.length > 0) {
                            const idx = expanded.indexOf(streamingLineNum)
                            if (idx !== -1) expanded.splice(idx, 1)
                        }
                    }
                    anyCleared = true
                } else {
                    remaining.push(block)
                }
            }
            if (!anyCleared) return

            if (remaining.length === 0) {
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
            } else {
                streaming.blocks = remaining
            }
            this.recomputeVisualItems(sessionId)
        },

        /**
         * Drop streaming blocks that never got their ``stream_block_end``
         * (``uuid`` still null) — orphans of a turn cut mid-stream (soft
         * interrupt, turn error). Nothing will ever stop or retire them.
         *
         * Blocks WITH a uuid are kept: stream events are ordered before the
         * process_state broadcast on the WS, so at turn end a properly ended
         * block already has its uuid and is awaiting normal retirement by
         * the real item — dropping it would flash the content away on every
         * turn end. (``clearEndedStreamingBlocks`` above handles the inverse
         * selection, in a REST-reload context where retirement is moot.)
         *
         * Called from ``setProcessState`` when a session leaves
         * assistant_turn; the caller's recompute repaints the visual items.
         */
        _dropOrphanedStreamingBlocks(sessionId) {
            const streaming = this.localState.streamingBlocks[sessionId]
            if (!streaming) return

            const { baseLineNum } = SYNTHETIC_ITEM.STREAMING_BLOCK
            const expanded = this.localState.sessionExpandedGroups[sessionId]
            const remaining = []
            for (const block of streaming.blocks) {
                if (block.uuid === null) {
                    clearBlockInactivityTimer(block)
                    flushBuffer(sessionId, block.blockIndex)
                    // Same thinking-block housekeeping as
                    // clearEndedStreamingBlocks: close the streaming detail
                    // key and drop the expandedGroups entry, so a stale
                    // ``true`` doesn't auto-open the next block landing at
                    // the same synthetic lineNum.
                    if (block.blockType === 'thinking') {
                        const streamingLineNum = baseLineNum - block.blockIndex
                        this.setDetailOpen(sessionId, `line:${streamingLineNum}:0`, false)
                        if (expanded && expanded.length > 0) {
                            const idx = expanded.indexOf(streamingLineNum)
                            if (idx !== -1) expanded.splice(idx, 1)
                        }
                    }
                } else {
                    remaining.push(block)
                }
            }
            if (remaining.length === streaming.blocks.length) return

            if (remaining.length === 0) {
                destroySessionBuffers(sessionId)
                delete this.localState.streamingBlocks[sessionId]
            } else {
                streaming.blocks = remaining
            }
        },

        // Session rename action

        /**
         * Rename a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {string} newTitle - The new title
         * @throws {Error} If the rename fails
         */
        async renameSession(projectId, sessionId, newTitle) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldTitle = session?.title

            if (session) {
                session.title = newTitle
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ title: newTitle })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to rename session')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (session && oldTitle !== undefined) {
                    session.title = oldTitle
                }
                throw error
            }
        },

        /**
         * Dismiss the session's current goal (hide its footer bar). One-way:
         * there is no un-dismiss; a new goal reopens the bar naturally. The
         * target's `created_at` pins the exact goal (the backend rejects a
         * stale target if a newer goal took the last slot meanwhile, and an
         * active goal — the UI only offers the cross on a closed one).
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {string} createdAt - `created_at` of the goal being dismissed
         * @throws {Error} If the update fails
         */
        async dismissSessionGoal(projectId, sessionId, createdAt) {
            // Optimistic update: hide the bar immediately.
            const session = this.sessions[sessionId]
            const goals = session?.goals
            const target = goals?.length ? goals[goals.length - 1] : null
            const applied = target && target.created_at === createdAt && !target.dismissed
            if (applied) {
                target.dismissed = true
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ dismiss_goal: createdAt })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to dismiss the goal')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (applied) {
                    target.dismissed = false
                }
                throw error
            }
        },

        /**
         * Ask the backend to re-probe the on-disk existence of the session's
         * tracked plan documents. An entry's `exists` flag is set at
         * write-detection time from the `tool_use` line — logged before the
         * tool flushes the file — so a doc written exactly once can be recorded
         * as `missing` and stay that way until the (rare) full recompute
         * re-probes. The Plan tab fires this on activation to clear such a
         * stale flag: the backend persists + broadcasts `session_updated` only
         * when a flag actually flipped. Best-effort — failures are swallowed
         * (the flags just stay as they were). We also merge `plan_paths` from
         * the HTTP response directly (covers the hidden-session case, which
         * skips the broadcast); merging only that field avoids clobbering any
         * in-flight optimistic layout/browser_url state.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         */
        async refreshSessionPlanExistence(projectId, sessionId) {
            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ refresh_plan_existence: true })
                    }
                )
                if (!response.ok) return
                const updatedSession = await response.json()
                const cur = this.sessions[sessionId]
                if (cur && Array.isArray(updatedSession.plan_paths)) {
                    cur.plan_paths = updatedSession.plan_paths
                }
            } catch {
                // Best-effort re-probe; a failure leaves the flags unchanged.
            }
        },

        // --- MRU (Most Recently Used) navigation tracking ---

        /**
         * Publish the ordered ids of the sessions currently shown in the sidebar.
         * Called by SessionList.vue whenever its rendered list changes, so the
         * "displayed sessions" switcher mode mirrors the screen exactly.
         * @param {string[]} ids - Session ids, in display order.
         */
        setDisplayedSessionIds(ids) {
            this.localState.displayedSessionIds = ids
        },

        /**
         * Record how many artifact bookmarks the sidebar list currently renders.
         * Called by ArtifactBookmarkList.vue whenever its rendered list changes,
         * so the `data-has-items` presence flag tracks the artifacts mode too.
         * @param {number} count - Number of bookmarks shown (search-filtered).
         */
        setDisplayedArtifactBookmarkCount(count) {
            this.localState.displayedArtifactBookmarkCount = count
        },

        /**
         * Record the current route in the MRU stack.
         * Replaces the previous entry for the same path, or for the same sessionId
         * (so each session only has one entry — the latest URL visited within it).
         * Entries without a sessionId (project pages) are deduplicated by path.
         * @param {string} path - The full route path (e.g. /project/abc/session/xyz/files)
         * @param {string|null} sessionId - The session ID from the route, or null
         */
        touchMruPath(path, sessionId) {
            const mru = this.localState.mruPaths
            // Remove previous entry for the same session (or same path if no session)
            const index = sessionId
                ? mru.findIndex(entry => entry.sessionId === sessionId)
                : mru.findIndex(entry => entry.path === path)
            if (index > -1) {
                mru.splice(index, 1)
            }
            mru.unshift({ path, sessionId })
            // Cap length to avoid unbounded growth
            if (mru.length > 100) {
                mru.length = 100
            }
        },

        /**
         * Remove all MRU entries for a given session.
         * Called when a session is hidden/removed from the store or a draft is
         * deleted — NOT on archive: archived sessions stay in the MRU so the
         * Ctrl+` switcher can switch back to them.
         * @param {string} sessionId - The session ID to remove
         */
        removeMruSession(sessionId) {
            this.localState.mruPaths = this.localState.mruPaths.filter(
                entry => entry.sessionId !== sessionId
            )
        },

        /**
         * Rekey an MRU entry from one session id to another, rewriting the id
         * segment inside its stored path. Used when a draft is promoted to a
         * provider-assigned canonical id (Codex) so the session keeps its MRU
         * slot under the new id rather than being orphaned by the draft cleanup.
         * No-op when there is no entry for ``oldId``. If an entry for ``newId``
         * already exists (e.g. a router.replace has already touched it), the
         * stale ``oldId`` entry is dropped instead of duplicated.
         * @param {string} oldId - The draft (local) session id.
         * @param {string} newId - The canonical (provider) session id.
         */
        rekeyMruSession(oldId, newId) {
            if (oldId === newId) return
            const mru = this.localState.mruPaths
            const index = mru.findIndex(entry => entry.sessionId === oldId)
            if (index === -1) return
            if (mru.some(entry => entry.sessionId === newId)) {
                // A fresher canonical entry already exists — drop the stale one.
                mru.splice(index, 1)
                return
            }
            const entry = mru[index]
            mru.splice(index, 1, {
                path: entry.path.replaceAll(oldId, newId),
                sessionId: newId,
            })
        },

        /**
         * Find the next MRU path to navigate to.
         * Returns the path of the most recent entry whose session (if any)
         * is not archived and not a subagent.
         * @param {string|null} excludeSessionId - Session to exclude (typically the one being archived)
         * @returns {string|null} The path to navigate to, or null if none found
         */
        getNextMruPath(excludeSessionId = null) {
            for (const entry of this.localState.mruPaths) {
                if (entry.sessionId === excludeSessionId) continue
                // Entries without a session (project pages) are always valid
                if (!entry.sessionId) return entry.path
                // Entries with a session: check the session is still reachable
                // (shared predicate with the Ctrl+` switcher, see above).
                if (!isSwitchableSession(this.sessions[entry.sessionId])) continue
                return entry.path
            }
            return null
        },

        /**
         * Set the archived state of a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {boolean} archived - Whether to archive or unarchive
         * @throws {Error} If the update fails
         */
        async setSessionArchived(projectId, sessionId, archived) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldArchived = session?.archived

            // Auto-unpin on archive: if archiving a pinned session and setting is enabled
            const settingsStore = useSettingsStore()
            const shouldUnpin = archived && session?.pinned && settingsStore.isAutoUnpinOnArchive
            const oldPinned = session?.pinned

            if (session) {
                session.archived = archived
                if (shouldUnpin) {
                    session.pinned = null
                }
            }

            // Note: archived sessions are intentionally KEPT in the MRU stack so
            // the Ctrl+` switcher can still jump back to a session you just
            // archived (the switcher's getter filters by isMruEligible, which
            // allows archived; the post-archive auto-nav fallback excludes them).

            // Build the PATCH payload
            const patchData = { archived }
            if (shouldUnpin) {
                patchData.pinned = null
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(patchData)
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update session')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (session) {
                    if (oldArchived !== undefined) {
                        session.archived = oldArchived
                    }
                    if (shouldUnpin && oldPinned !== undefined) {
                        session.pinned = oldPinned
                    }
                }
                throw error
            }
        },

        /**
         * Apply a bulk-archive broadcast from the backend. Local-only:
         * marks sessions as archived in the store. Does NOT call the backend
         * (the backend already archived them). Does NOT touch pinned sessions
         * (the backend filtered them out). Archived sessions stay in the MRU
         * stack so the Ctrl+` switcher can still reach them.
         */
        applyBulkArchiveFromBroadcast(sessionIds) {
            for (const sid of sessionIds) {
                const session = this.sessions[sid]
                if (session) {
                    session.archived = true
                }
            }
        },

        /**
         * Call the bulk-archive endpoint.
         *
         * @param {Object} params
         * @param {string} params.olderThan    - ISO timestamp threshold.
         * @param {Object} params.scope        - { type: 'project'|'workspace'|'all', id: string|null }.
         * @param {string} [params.titleQuery] - If non-empty, restrict to sessions whose title (or id)
         *                                       subsequence-matches the query — same semantics as the
         *                                       sidebar filter.
         * @param {boolean} [params.includeArchivedProjects] - For workspace/all scopes, include
         *                                       sessions belonging to archived projects. Ignored
         *                                       server-side for scope='project'.
         * @param {boolean} [params.dryRun]    - If true, returns only the count.
         * @param {AbortSignal} [params.signal] - Abort signal for cancellable dry-runs.
         * @returns {Promise<{count: number, has_archived_in_scope: boolean}>}
         */
        async bulkArchiveSessions({
            olderThan,
            scope,
            titleQuery = '',
            includeArchivedProjects = false,
            dryRun = false,
            signal = null,
        }) {
            const body = {
                older_than: olderThan,
                scope: scope.type,
                dry_run: dryRun,
            }
            if (scope.type === 'project') body.project_id = scope.id
            if (scope.type === 'workspace') body.workspace_id = scope.id
            if (titleQuery) body.title_query = titleQuery
            if (includeArchivedProjects) body.include_archived_projects = true

            const res = await apiFetch('/api/sessions/bulk-archive/', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
                signal,
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.error || `HTTP ${res.status}`)
            }
            return res.json()
        },

        /**
         * Set the pin mode of a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {('project'|'workspace'|'all'|null)} mode - Pin mode, or null to unpin
         * @throws {Error} If the update fails
         */
        async setSessionPinMode(projectId, sessionId, mode) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldPinned = session?.pinned

            if (session) {
                session.pinned = mode
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ pinned: mode })
                    }
                )

                if (!response.ok) {
                    const data = await response.json()
                    throw new Error(data.error || 'Failed to update session')
                }

                const updatedSession = await response.json()
                this.sessions[sessionId] = { ...this.sessions[sessionId], ...updatedSession }

            } catch (error) {
                // Rollback on error
                if (session && oldPinned !== undefined) {
                    session.pinned = oldPinned
                }
                throw error
            }
        },

        /**
         * Set whether a session sends a notification when the agent finishes work.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {boolean} value - Whether to mute the notification
         * @throws {Error} If the update fails
         */
        async setSessionMuteOnUserTurn(projectId, sessionId, value) {
            return applySessionMuteOnUserTurn(this.sessions, apiFetch, projectId, sessionId, value)
        },

        // Draft messages actions

        /**
         * Get or create a debounced save function for a session.
         * @param {string} sessionId
         * @returns {Function} Debounced save function
         * @private
         */
        _getDebouncedSave(sessionId) {
            if (!debouncedSaves.has(sessionId)) {
                debouncedSaves.set(sessionId, debounce((draft) => {
                    saveDraftMessage(sessionId, draft).catch(err =>
                        console.warn('Failed to save draft message to IndexedDB:', err)
                    )
                }, 500))
            }
            return debouncedSaves.get(sessionId)
        },

        /**
         * Set the draft message for a session.
         * Called by MessageInput on each keystroke.
         * If message is empty, clears the draft entirely.
         * @param {string} sessionId
         * @param {string} message
         */
        setDraftMessage(sessionId, message) {
            if (!message) {
                // Message is empty - clear the draft
                if (this.localState.draftMessages[sessionId]) {
                    this.clearDraftMessage(sessionId)
                }
                return
            }

            // Message has content - save it
            this.localState.draftMessages[sessionId] = { message }

            // Persist to IndexedDB with debounce
            const debouncedSave = this._getDebouncedSave(sessionId)
            debouncedSave({ message })
        },

        /**
         * Append text to a session's draft on behalf of a programmatic flow
         * (Peer delivery): existing draft + blank line + text, or just the
         * text. Also bumps the session's append signal so an already-mounted
         * composer re-reads the draft — its own draft watcher deliberately
         * ignores non-empty textareas, so a plain draft update would stay
         * invisible there (and be lost on the next keystroke).
         * @param {string} sessionId
         * @param {string} text
         */
        async appendDraftMessage(sessionId, text) {
            const existing = this.getDraftMessage(sessionId)?.message?.trim() || ''
            this.setDraftMessage(sessionId, existing ? `${existing}\n\n${text}` : text)
            const signals = this.localState.draftAppendSignals
            signals[sessionId] = (signals[sessionId] || 0) + 1
            // Flush instead of waiting out the 500 ms debounce: the caller
            // navigates right after, and an embedding host may turn that
            // navigation into a page unload that would drop the pending
            // write. Best effort — the store copy is what the composer
            // shows and sends either way.
            const debouncedSave = debouncedSaves.get(sessionId)
            if (debouncedSave) {
                debouncedSave.cancel()
                debouncedSaves.delete(sessionId)
            }
            await saveDraftMessage(sessionId, this.localState.draftMessages[sessionId]).catch(err =>
                console.warn('Failed to flush draft message to IndexedDB:', err)
            )
        },

        /**
         * Set the draft title for a session (draft sessions only).
         * Called by SessionRenameDialog when title is modified before first message.
         * Updates the draft session in IndexedDB with the new title.
         * @param {string} sessionId
         * @param {string} title
         */
        setDraftTitle(sessionId, title) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return

            // Mirror the title in-memory, then snapshot the whole draft (settings, hybrid, layout) so a
            // rename doesn't drop the other fields from the IndexedDB record. Fire and forget.
            session.title = title
            this._saveDraftToIndexedDB(sessionId)
        },

        /**
         * Clear the draft for a session.
         * Called after successful message send.
         * @param {string} sessionId
         */
        clearDraftMessage(sessionId) {
            delete this.localState.draftMessages[sessionId]

            // Cancel any pending debounced save
            const debouncedSave = debouncedSaves.get(sessionId)
            if (debouncedSave) {
                debouncedSave.cancel()
                debouncedSaves.delete(sessionId)
            }

            // Delete from IndexedDB
            deleteDraftMessage(sessionId).catch(err =>
                console.warn('Failed to delete draft message from IndexedDB:', err)
            )
        },

        /**
         * Load all draft messages from IndexedDB into local state.
         * Called at app startup.
         */
        async hydrateDraftMessages() {
            try {
                const drafts = await getAllDraftMessages()
                this.localState.draftMessages = drafts
            } catch (err) {
                console.warn('Failed to load draft messages from IndexedDB:', err)
            }
        },

        /**
         * Load all draft sessions from IndexedDB into the sessions store.
         * Called at app startup, BEFORE hydrateDraftMessages.
         * Recreates session objects with: id, project_id, title (or 'New session'),
         * mtime=now, last_line=0, draft=true, plus the persisted dockable ``layout``
         * (legacy drafts saved before layout persistence carry none → single pane).
         */
        async hydrateDraftSessions() {
            try {
                const draftSessions = await getAllDraftSessions()
                const now = Date.now() / 1000
                const defaultProvider = useSettingsStore().defaultProvider
                for (const [sessionId, draft] of Object.entries(draftSessions)) {
                    const { projectId, title } = draft
                    // Stored provider wins; else the project's inherited default
                    // (legacy drafts saved without one), else global.
                    const provider = draft.provider
                        || resolveProjectDefaultProvider(projectId, this.projects)
                        || defaultProvider
                    // Restore the frozen agent-settings snapshot. Legacy drafts
                    // saved before option A carry none → resolve now (they were
                    // never launched, so freezing today's defaults is correct).
                    const settings = draft.selected_model !== undefined
                        ? this._pickAgentSettings(draft)
                        : this._resolveDraftAgentSettings(projectId, provider)
                    this.sessions[sessionId] = {
                        id: sessionId,
                        project_id: projectId,
                        provider,
                        title: title || null,  // null = user hasn't set a title yet
                        mtime: now,
                        last_line: 0,
                        draft: true,
                        // Gated by the server hybrid feature flag: a persisted
                        // hybrid draft hydrates as a normal (sendable) draft while
                        // hybrid mode is off, rather than a stuck hybrid one.
                        hybrid: !!draft.hybrid && useSettingsStore().isClaudeHybridEnabled,
                        // Restore the persisted dockable layout (frozen at creation, then kept in sync
                        // with the user's edits). Undefined for legacy drafts → ensureSessionLayout
                        // seeds an empty (single-pane) intention.
                        layout: draft.layout,
                        peerMessageId: draft.peerMessageId,
                        ...settings,
                    }
                    // Re-arm the peer delivery this draft was created for, so a
                    // reload between the delivery and the first send does not
                    // lose the link (see `setDraftPeerMessage`).
                    if (draft.peerMessageId != null) {
                        this.localState.pendingPeerDeliveries[sessionId] = draft.peerMessageId
                    }
                }
            } catch (err) {
                console.warn('Failed to load draft sessions from IndexedDB:', err)
            }
        },

        // Draft session cleanup

        /**
         * Clean up orphan draft sessions from IndexedDB.
         * Reads all draft sessions from IndexedDB and checks against the backend API.
         * If a session exists on the backend, the draft entry is removed from IndexedDB
         * (and from the store if it still has draft: true).
         * Errors are silently ignored — the next cycle will retry.
         */
        async cleanupOrphanDraftSessions() {
            let draftSessions
            try {
                draftSessions = await getAllDraftSessions()
            } catch {
                return  // IndexedDB error, retry next cycle
            }

            const entries = Object.entries(draftSessions)
            if (entries.length === 0) return

            for (const [sessionId, data] of entries) {
                const projectId = data?.projectId
                if (!projectId) {
                    // Corrupted entry — no project ID means we can't check the API, just remove it
                    deleteDraftSessionFromDb(sessionId).catch(() => {})
                    if (this.sessions[sessionId]?.draft) {
                        delete this.sessions[sessionId]
                    }
                    continue
                }
                try {
                    const response = await apiFetch(
                        `/api/projects/${projectId}/sessions/${sessionId}/`,
                        { method: 'HEAD' }
                    )
                    if (response.ok) {
                        // Session exists on backend — remove the orphan draft
                        deleteDraftSessionFromDb(sessionId).catch(() => {})
                        if (this.sessions[sessionId]?.draft) {
                            delete this.sessions[sessionId]
                        }
                    }
                    // 404 = genuine draft, keep it. Other errors = skip silently.
                } catch {
                    // Network error, skip this session
                }
            }
        },

        // Title suggestion actions

        /**
         * Handle title_suggested message from WebSocket.
         * Always stores sourcePrompt (for regeneration), and suggestion if available.
         * @param {Object} data - { sessionId, suggestion, sourcePrompt }
         */
        handleTitleSuggested(data) {
            const { sessionId, suggestion, sourcePrompt } = data
            // Resolve the draft alias if the backend echoed the draft id back
            // (the ``suggest_title`` payload was sent under the draft id; for
            // providers that rebind to a canonical id, we want the response
            // to land on the canonical key so the SessionView watcher sees it).
            const sid = this.localState.draftAliases[sessionId] || sessionId
            // Always store the response so the frontend knows the request completed
            // (distinguishes "no response yet" from "response received with failure")
            this.localState.titleSuggestions[sid] = {
                suggestion: suggestion || null,
                sourcePrompt: sourcePrompt || null,
            }
        },

        /**
         * Clear title suggestion for a session (after use).
         * @param {string} sessionId
         */
        clearTitleSuggestion(sessionId) {
            delete this.localState.titleSuggestions[sessionId]
        },

        /**
         * Register a session as waiting on an auto-applied title.
         * Consumed by the App-level watcher which reacts to the matching
         * ``titleSuggestions`` entry.
         * @param {string} sessionId
         * @param {string} projectId
         */
        registerPendingTitleAutoApply(sessionId, projectId) {
            this.localState.pendingTitleAutoApply[sessionId] = { projectId }
        },

        /**
         * Drop a pending auto-apply entry (after success or definitive failure).
         * @param {string} sessionId
         */
        clearPendingTitleAutoApply(sessionId) {
            delete this.localState.pendingTitleAutoApply[sessionId]
        },

        // =========================================================================
        // Attachment actions (for document upload)
        // =========================================================================

        /**
         * Add a file attachment to a session.
         *
         * Processes the file (validation + encoding) using the provider's
         * attachment capabilities and stores in IndexedDB. The provider is
         * resolved from the session row so call sites don't have to thread
         * the capabilities through themselves — a stray drop on a Codex
         * session validates against Codex rules without the caller knowing.
         *
         * @param {string} sessionId - The session ID
         * @param {File} file - The file to add
         * @returns {Promise<DraftMedia>} The processed media object
         * @throws {Error} If validation fails or file cannot be processed
         */
        async addAttachment(sessionId, file) {
            const session = this.getSession(sessionId)
            const helpers = getProviderHelpers(session?.provider)
            const capabilities = helpers?.getAttachmentSupport() ?? {
                images: false, documents: false, maxBytes: 0,
                acceptedMimeTypes: [], resizeImages: false,
            }

            // Per-draft hard caps (uniform across providers): refuse the
            // upload up front rather than running the (potentially slow)
            // resize pipeline only to discover the draft can't take more.
            const existing = this.localState.attachments[sessionId]
            const existingCount = existing?.size ?? 0
            if (existingCount >= MAX_FILES_PER_DRAFT) {
                throw new Error(
                    `Maximum ${MAX_FILES_PER_DRAFT} files per draft reached`,
                )
            }
            let storedBytes = 0
            if (existing) {
                for (const media of existing.values()) {
                    storedBytes += getDraftMediaBytes(media)
                }
            }
            // Conservative check: compare stored (post-resize) total to
            // 32 MB minus the new file's raw source size. The new file
            // will shrink after resize, but blocking on the raw size is
            // safer and avoids running the encode pipeline for an upload
            // we'd reject anyway.
            if (storedBytes + file.size > MAX_TOTAL_BYTES_PER_DRAFT) {
                const totalMB = (MAX_TOTAL_BYTES_PER_DRAFT / 1024 / 1024).toFixed(0)
                const storedMB = (storedBytes / 1024 / 1024).toFixed(1)
                throw new Error(
                    `Draft total size limit reached (${totalMB} MB max; ${storedMB} MB already attached)`,
                )
            }

            // Track that a file is being processed (blocks the send button)
            this.localState.processingAttachments[sessionId] =
                (this.localState.processingAttachments[sessionId] || 0) + 1

            try {
                // Process file (validates and encodes)
                const media = await processFile(file, sessionId, capabilities)

                // Save to IndexedDB
                await saveDraftMedia(media)

                // Update in-memory state
                if (!this.localState.attachments[sessionId]) {
                    this.localState.attachments[sessionId] = new Map()
                }
                this.localState.attachments[sessionId].set(media.id, media)

                // Update draft message with media ID (for order preservation)
                const draft = await getDraftMessage(sessionId) || {}
                draft.mediaIds = draft.mediaIds || []
                draft.mediaIds.push(media.id)
                await saveDraftMessage(sessionId, draft)

                return media
            } finally {
                // Decrement counter (whether success or failure)
                this.localState.processingAttachments[sessionId]--
                if (this.localState.processingAttachments[sessionId] <= 0) {
                    delete this.localState.processingAttachments[sessionId]
                }
            }
        },

        /**
         * Remove an attachment from a session.
         * @param {string} sessionId - The session ID
         * @param {string} mediaId - The media ID to remove
         */
        async removeAttachment(sessionId, mediaId) {
            // Remove from IndexedDB
            await deleteDraftMedia(mediaId)

            // Remove from in-memory state
            this.localState.attachments[sessionId]?.delete(mediaId)

            // Update draft message to remove media ID
            const draft = await getDraftMessage(sessionId)
            if (draft?.mediaIds) {
                draft.mediaIds = draft.mediaIds.filter(id => id !== mediaId)
                await saveDraftMessage(sessionId, draft)
            }
        },

        /**
         * Remove every non-image attachment (PDF, TXT) from a draft.
         *
         * Used by the provider-switcher UX in the agent settings popover:
         * Codex has no protocol for documents, so when a draft holds any
         * PDF/TXT the Codex option is gated behind an explicit "remove
         * the documents to continue" affordance. Returns the count of
         * removed attachments so the caller can toast a confirmation.
         *
         * @param {string} sessionId - The session ID
         * @returns {Promise<number>} Number of attachments removed
         */
        async removeNonImageAttachments(sessionId) {
            const map = this.localState.attachments[sessionId]
            if (!map || map.size === 0) return 0
            const toRemove = []
            for (const media of map.values()) {
                if (media.type !== 'image') toRemove.push(media.id)
            }
            for (const id of toRemove) {
                await this.removeAttachment(sessionId, id)
            }
            return toRemove.length
        },

        /**
         * Load attachments for a session from IndexedDB.
         * Called when entering a session to restore persisted attachments.
         * @param {string} sessionId - The session ID
         */
        async loadAttachmentsForSession(sessionId) {
            try {
                const medias = await getDraftMediasBySession(sessionId)
                if (medias.length > 0) {
                    this.localState.attachments[sessionId] = new Map(
                        medias.map(m => [m.id, m])
                    )
                }
            } catch (err) {
                console.warn('Failed to load attachments from IndexedDB:', err)
            }
        },

        /**
         * Clear all attachments for a session.
         * Called after successful message send.
         * @param {string} sessionId - The session ID
         */
        async clearAttachmentsForSession(sessionId) {
            // Remove from IndexedDB
            await deleteAllDraftMediasForSession(sessionId)

            // Clear in-memory state
            delete this.localState.attachments[sessionId]
        },

        /**
         * Get attachments in Claude SDK format (images and documents separated).
         * @param {string} sessionId - The session ID
         * @returns {{ images: Object[], documents: Object[] }} SDK-formatted blocks
         */
        getAttachmentsForSdk(sessionId) {
            const map = this.localState.attachments[sessionId]
            if (!map || map.size === 0) {
                return { images: [], documents: [] }
            }
            return mediasToSdkFormat(Array.from(map.values()))
        },

        /**
         * Load all draft attachments from IndexedDB into local state.
         * Called at app startup.
         */
        async hydrateAttachments() {
            try {
                const allMedias = await getAllDraftMedias()
                // Group by sessionId
                for (const media of allMedias) {
                    if (!this.localState.attachments[media.sessionId]) {
                        this.localState.attachments[media.sessionId] = new Map()
                    }
                    this.localState.attachments[media.sessionId].set(media.id, media)
                }
            } catch (err) {
                console.warn('Failed to load attachments from IndexedDB:', err)
            }
        }
    }
})

// Pinia HMR support: hot-replace actions/getters without full page reload.
// We wrap acceptHMRUpdate with state save/restore because Pinia's patchObject
// loses dynamic keys: it skips keys present in the old state but absent from
// the fresh state() initializer (e.g. projects: {} starts empty, so all
// runtime-added project IDs are dropped during the merge).
if (import.meta.hot) {
    // Create the HMR handler once at module eval time (standard Pinia pattern).
    // We wrap it to save/restore state around the call because Pinia's patchObject
    // loses dynamic keys (it skips old keys absent from the fresh state() initializer).
    const piniaHmrHandler = acceptHMRUpdate(useDataStore, import.meta.hot)

    import.meta.hot.accept((newModule) => {
        const pinia = import.meta.hot.data?.pinia || useDataStore._pinia
        if (!pinia) return
        const store = pinia._s.get('data')
        if (!store) return

        // Save current state values (raw references, no cloning needed)
        const savedState = {}
        for (const key of Object.keys(store.$state)) {
            savedState[key] = toRaw(store.$state[key])
        }

        // Apply Pinia's HMR update (updates actions/getters but loses dynamic state keys)
        piniaHmrHandler(newModule)

        // Restore state values that were lost by patchObject
        store.$patch((state) => {
            for (const [key, value] of Object.entries(savedState)) {
                state[key] = value
            }
        })
    })
}
