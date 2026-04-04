// frontend/src/stores/data.js

import { defineStore, acceptHMRUpdate } from 'pinia'
import { toRaw } from 'vue'
import { getPrefixSuffixBoundaries } from '../utils/contentVisibility'
import { computeVisualItems, visualItemEqual } from '../utils/visualItems'
import { DISPLAY_LEVEL, DISPLAY_MODE, PROCESS_STATE, SYNTHETIC_ITEM } from '../constants'
import { getSessionCutoffMs } from '../utils/sessions'
import { useSettingsStore } from './settings'
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
import { processFile, mediasToSdkFormat } from '../utils/fileUtils'
import { generateUUID } from '../utils/crypto'
import { debounce } from '../utils/debounce'
import { apiFetch } from '../utils/api'
import { getParsedContent, setParsedContent, clearParsedContent, hasContent } from '../utils/parsedContent'
// Note: respondToPendingRequest is imported lazily to avoid circular dependency
// (data.js ↔ useWebSocket.js)

// Map of debounced save functions per session (to avoid mixing debounces)
const debouncedSaves = new Map()

// Special project ID for "All Projects" mode
export const ALL_PROJECTS_ID = '__all__'

/**
 * Sort sessions by display priority:
 * 1. Sessions with active process first (by started_at descending for stable ordering)
 * 2. Pinned sessions without process (by mtime descending)
 * 3. Remaining sessions (by mtime descending)
 *
 * @param {Object} processStates - Map of sessionId -> processState
 * @returns {function} Comparator function for Array.sort()
 */
function sessionSortComparator(processStates) {
    return (a, b) => {
        // 1. Sessions with active process first
        const aProcess = processStates[a.id]
        const bProcess = processStates[b.id]
        const aHasProcess = aProcess != null
        const bHasProcess = bProcess != null
        if (aHasProcess !== bHasProcess) return aHasProcess ? -1 : 1

        // 2. Among active sessions: sort by started_at descending (most recently started first)
        //    This gives a stable order since started_at never changes during process lifetime,
        //    avoiding rapid swapping when multiple sessions update frequently.
        if (aHasProcess && bHasProcess) {
            return (bProcess.started_at || 0) - (aProcess.started_at || 0)
        }

        // 3. Pinned sessions next (only when neither has a process)
        const aPinned = a.pinned || false
        const bPinned = b.pinned || false
        if (aPinned !== bPinned) return aPinned ? -1 : 1

        // 4. Within each group, sort by mtime descending
        return b.mtime - a.mtime
    }
}

export const useDataStore = defineStore('data', {
    state: () => ({
        // Server data
        projects: {},       // { id: { id, sessions_count, mtime, stale } }
        sessions: {},       // { id: { id, project_id, last_line, mtime, stale } }
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

        // Weekly activity data (from /api/home/ endpoint)
        // { _global: [...], projectId: [...] } — each value is Array of { date, user_message_count }
        weeklyActivity: {},

        // Usage quota data (from periodic usage sync)
        // { success: bool, raw: serialized snapshot, computed: computeUsageData() result }
        usage: null,

        // WebSocket connection state (updated by useWebSocket composable)
        wsConnected: false,

        // Startup progress (from WebSocket startup_progress messages)
        // { initial_sync?: { current, total, completed }, background_compute?: { current, total, completed } }
        startupProgress: {},

        // Server info (from WebSocket messages)
        currentVersion: null,           // string, from server_version message
        latestVersion: null,            // { version, releaseUrl } or null, from update_available message
        claudeStatus: 'operational',    // string, from claude_status message

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

            // Agent links cache - maps tool_id to agent_id for Task tool_use items
            // { sessionId: { toolId: agentId } }
            // Only caches found agents (not-found triggers polling, not caching)
            agentLinks: {},

            // Tool states - maps tool_use_id to { resultCount, completedAt, error, extra }
            // { sessionId: { toolUseId: { resultCount, completedAt, error, extra } } }
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

            // Title suggestions by session ID
            // Format: { sessionId: { suggestion: string, sourcePrompt?: string } }
            titleSuggestions: {},

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

            // Optimistic messages - user messages displayed immediately after send,
            // before the backend confirms with a real user_message item.
            // { sessionId: { syntheticKind, content, kind } }
            // Cleared when the real user_message arrives in addSessionItems.
            optimisticMessages: {},
        }
    }),

    getters: {
        // Data getters (sorted by mtime descending - most recent first)
        getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
        getProject: (state) => (id) => state.projects[id],
        getProjectSessions: (state) => (projectId) => {
            const projectState = state.localState.projects[projectId]
            // Only apply the mtime lower-bound when there are more pages to load.
            // When all pages have been fetched (hasMoreSessions=false), every
            // session in the store should be visible — including ones added via
            // WS during background compute whose mtime may be older than the bound.
            const oldestMtime = projectState?.hasMoreSessions
                ? projectState.oldestSessionMtime
                : null
            return Object.values(state.sessions)
                .filter(s => s.project_id === projectId && !s.parent_session_id)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort(sessionSortComparator(state.processStates))
        },
        getAllSessions: (state) => {
            const allState = state.localState.projects[ALL_PROJECTS_ID]
            const oldestMtime = allState?.hasMoreSessions
                ? allState.oldestSessionMtime
                : null
            return Object.values(state.sessions)
                .filter(s => !s.parent_session_id)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort(sessionSortComparator(state.processStates))
        },
        getSession: (state) => (id) => state.sessions[id],
        getSessionItems: (state) => (sessionId) => state.sessionItems[sessionId] || [],

        // Process state getter - returns { state, error?, pending_request? } or null if no active process
        getProcessState: (state) => (sessionId) => state.processStates[sessionId] || null,

        // Pending request getter - returns the pending_request object or null
        getPendingRequest: (state) => (sessionId) =>
            state.processStates[sessionId]?.pending_request || null,

        /**
         * Get aggregated process state for a project.
         * Returns the most important state across all sessions in the project.
         * Priority: dead > user_turn > starting > assistant_turn
         * @param {string} projectId - The project ID
         * @returns {string|null} The most important state or null if no active processes
         */
        getProjectProcessState: (state) => (projectId) => {
            // Priority order (higher = more important)
            const priority = {
                assistant_turn: 1,
                starting: 2,
                user_turn: 3,
                dead: 4,
            }

            let mostImportantState = null
            let highestPriority = 0

            for (const [sessionId, processState] of Object.entries(state.processStates)) {
                // Check if this process belongs to the project
                // Use project_id stored in processState (from WebSocket messages)
                if (processState.project_id !== projectId) continue

                const statePriority = priority[processState.state] || 0
                if (statePriority > highestPriority) {
                    highestPriority = statePriority
                    mostImportantState = processState.state
                }
            }

            return mostImportantState
        },

        /**
         * Check if any session in a project has active cron jobs.
         * @param {string} projectId - The project ID
         * @returns {boolean} True if at least one session has active crons
         */
        getProjectHasActiveCrons: (state) => (projectId) => {
            for (const processState of Object.values(state.processStates)) {
                if (processState.project_id !== projectId) continue
                if (processState.active_crons?.length > 0) return true
            }
            return false
        },

        /**
         * Get the total count of active cron jobs across all sessions in a project.
         * @param {string} projectId - The project ID
         * @returns {number} The total number of active crons
         */
        getProjectActiveCronCount: (state) => (projectId) => {
            let count = 0
            for (const processState of Object.values(state.processStates)) {
                if (processState.project_id !== projectId) continue
                count += processState.active_crons?.length || 0
            }
            return count
        },

        /**
         * Get the count of active processes for a project.
         * @param {string} projectId - The project ID
         * @returns {number} The number of active processes
         */
        getProjectProcessCount: (state) => (projectId) => {
            let count = 0
            for (const processState of Object.values(state.processStates)) {
                if (processState.project_id === projectId) {
                    count++
                }
            }
            return count
        },

        /**
         * Count sessions with unread content in a project.
         * A session is unread when last_new_content_at > last_viewed_at (or last_viewed_at is null).
         * Only counts non-draft, non-archived, non-subagent sessions.
         * If a process is running for the session, only counts when in user_turn.
         * @param {string} projectId - The project ID
         * @returns {number} The number of unread sessions
         */
        getProjectUnreadCount: (state) => (projectId) => {
            let count = 0
            for (const session of Object.values(state.sessions)) {
                if (session.project_id !== projectId) continue
                if (session.draft || session.archived || session.parent_session_id) continue
                if (!session.last_new_content_at) continue
                if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) continue
                // If process is running, only count when in user_turn
                const processState = state.processStates[session.id]
                if (processState && processState.state !== 'user_turn') continue
                count++
            }
            return count
        },

        /**
         * Whether any session globally is in assistant_turn state.
         * Used by the dynamic favicon to show a blue activity dot.
         * @returns {boolean}
         */
        hasGlobalAssistantTurn: (state) => {
            for (const processState of Object.values(state.processStates)) {
                if (processState.state === 'assistant_turn') return true
            }
            return false
        },

        /**
         * Count sessions with unread content across all projects.
         * Same logic as getProjectUnreadCount but without project filter.
         * @returns {number} The number of unread sessions
         */
        getGlobalUnreadCount: (state) => {
            let count = 0
            for (const session of Object.values(state.sessions)) {
                if (session.draft || session.archived || session.parent_session_id) continue
                if (!session.last_new_content_at) continue
                if (session.last_viewed_at && session.last_new_content_at <= session.last_viewed_at) continue
                // If process is running, only count when in user_turn
                const processState = state.processStates[session.id]
                if (processState && processState.state !== 'user_turn') continue
                count++
            }
            return count
        },

        // Startup progress getters
        initialSyncProgress: (state) => state.startupProgress.initial_sync || null,
        backgroundComputeProgress: (state) => state.startupProgress.background_compute || null,
        searchIndexProgress: (state) => state.startupProgress.search_index || null,
        isStartupInProgress: (state) =>
            Object.values(state.startupProgress).some(p => p && !p.completed),
        isInitialSyncInProgress: (state) => {
            const sync = state.startupProgress.initial_sync
            return sync != null && !sync.completed
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

        // Get open tabs for a session
        getSessionOpenTabs: (state) => (sessionId) =>
            state.localState.sessionOpenTabs[sessionId] || null,

        // Get cached agent link for a tool_id in a session
        // Returns: { agentId, isBackground } or undefined (not in cache)
        getAgentLink: (state) => (sessionId, toolId) => {
            const sessionLinks = state.localState.agentLinks[sessionId]
            if (!sessionLinks) return undefined
            return sessionLinks[toolId]
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
        // Returns: { resultCount, completedAt, error, extra, toolResultLineNum } or null
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
        // Usage
        setUsage(hasOauth, success, reason, rawData, computedData) {
            this.usage = { hasOauth, success, reason, raw: rawData, computed: computedData }
        },

        // Server info
        setCurrentVersion(version) {
            this.currentVersion = version
        },
        setLatestVersion(version, releaseUrl) {
            this.latestVersion = { version, releaseUrl }
        },
        setClaudeStatus(status) {
            this.claudeStatus = status
        },

        // Startup progress
        setStartupProgress(phase, current, total, completed) {
            this.startupProgress = {
                ...this.startupProgress,
                [phase]: { current, total, completed },
            }
        },

        // Projects
        addProject(project) {
            this.$patch({ projects: { [project.id]: project } })
            // Invalidate display name cache so it gets recomputed
            delete this.localState.projectDisplayNames[project.id]
        },
        updateProject(project) {
            // $patch does a deep merge: only modified props trigger a re-render
            this.$patch({ projects: { [project.id]: project } })
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
                this.$patch({ projects: { [projectId]: updatedProject } })

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
        },
        updateSession(session) {
            // When lifecycle timestamps change, clean up stale synthetic process states
            // for child agents that predate the new cutoff
            const prev = this.sessions[session.id]
            if (prev && (prev.last_started_at !== session.last_started_at ||
                         prev.last_stopped_at !== session.last_stopped_at)) {
                this._cleanStaleChildSynthetics(session)
            }
            this.$patch({ sessions: { [session.id]: session } })
        },
        /**
         * Create a draft session for a project.
         * Draft sessions exist only in the frontend until the first message is sent.
         * @param {string} projectId - The project ID
         * @returns {string} The generated session ID (UUID)
         */
        createDraftSession(projectId) {
            const id = generateUUID()
            const now = Date.now() / 1000  // Unix timestamp in seconds
            this.sessions[id] = {
                id,
                project_id: projectId,
                title: null,  // null = user hasn't set a title yet, UI will display "New session"
                mtime: now,
                last_line: 0,
                draft: true,
            }
            // Persist to IndexedDB
            saveDraftSession(id, { projectId }).catch(err =>
                console.warn('Failed to save draft session to IndexedDB:', err)
            )
            return id
        },

        /**
         * Delete a draft session from IndexedDB, and optionally from store.
         * Only deletes if the session exists and has draft: true.
         * @param {string} sessionId - The session ID to delete
         * @param {Object} options - Options
         * @param {boolean} options.keepInStore - If true, only delete from IndexedDB (keep in store)
         */
        deleteDraftSession(sessionId, { keepInStore = false } = {}) {
            if (this.sessions[sessionId]?.draft) {
                if (!keepInStore) {
                    delete this.sessions[sessionId]
                }
                this.removeMruSession(sessionId)
                // Delete from IndexedDB
                deleteDraftSessionFromDb(sessionId).catch(err =>
                    console.warn('Failed to delete draft session from IndexedDB:', err)
                )
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

            for (const item of newItems) {
                const index = item.line_num - 1 // line_num is 1-based, array is 0-based

                // Extend array with placeholders if needed
                while (targetArray.length <= index) {
                    targetArray.push({ line_num: targetArray.length + 1 })
                }

                // Place item at correct index
                targetArray[index] = item
            }

            // Clear optimistic message when a real user_message arrives from the backend
            if (this.localState.optimisticMessages[sessionId] &&
                newItems.some(item => item.kind === 'user_message')) {
                delete this.localState.optimisticMessages[sessionId]
            }

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

            // Build URL based on project type
            const isAllProjects = projectId === ALL_PROJECTS_ID
            const baseUrl = isAllProjects
                ? '/api/sessions/'
                : `/api/projects/${projectId}/sessions/`

            // Add cursor if we have one (for pagination)
            const params = new URLSearchParams()
            if (state.oldestSessionMtime != null) {
                params.set('before_mtime', state.oldestSessionMtime)
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
            const changedIds = new Set()
            const state = this._ensureProjectLocalState(projectId)

            // Skip if already loading
            if (state.sessionsLoading) {
                return changedIds
            }

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
                this.localState.sessions[sessionId].itemsFetched = true
                this.localState.sessions[sessionId].itemsLoadingError = false
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
         */
        async loadSessionItemsRanges(projectId, sessionId, ranges, parentSessionId = null) {
            if (!ranges?.length) return

            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }

            // Build query params
            const params = new URLSearchParams()
            for (const range of ranges) {
                if (typeof range === 'number') {
                    params.append('range', String(range))
                } else if (Array.isArray(range)) {
                    const [min, max] = range
                    const minStr = min != null ? String(min) : ''
                    const maxStr = max != null ? String(max) : ''
                    params.append('range', `${minStr}:${maxStr}`)
                }
            }

            // Build URL (handle subagent case)
            const baseUrl = parentSessionId
                ? `/api/projects/${projectId}/sessions/${parentSessionId}/subagent/${sessionId}`
                : `/api/projects/${projectId}/sessions/${sessionId}`

            try {
                const res = await apiFetch(`${baseUrl}/items/?${params}`)
                if (!res.ok) {
                    console.error('Failed to load session items ranges:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.sessions[sessionId].itemsLoadingError = true
                    }
                    return
                }
                const items = await res.json()
                this.addSessionItems(sessionId, items)
                // Success: clear any previous error
                this.localState.sessions[sessionId].itemsLoadingError = false
            } catch (error) {
                console.error('Failed to load session items ranges:', error)
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
            delete this.localState.sessionInternalExpandedGroups[sessionId]
            delete this.localState.sessionVisualItems[sessionId]
            delete this.localState.visualItemCache[sessionId]
            delete this.localState.optimisticMessages[sessionId]
            delete this.localState.agentLinks[sessionId]
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
            if (!items.length && !this.localState.optimisticMessages[sessionId]) {
                this.localState.sessionVisualItems[sessionId] = []
                this.localState.visualItemCache[sessionId] = new Map()
                return
            }

            // Get effective display mode from settings store
            const settingsStore = useSettingsStore()
            const mode = settingsStore.getDisplayMode
            const expandedGroups = this.localState.sessionExpandedGroups[sessionId] || []

            // Detect assistant_turn (used by computeVisualItems for conversation mode
            // filtering, and for the synthetic working assistant message)
            const processState = this.processStates[sessionId]
            const isAssistantTurn = processState?.state === PROCESS_STATE.ASSISTANT_TURN

            let allItems = items || []
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

            // Append a synthetic "working" assistant message when in assistant_turn.
            // Injected into allItems so computeVisualItems handles it like any other item.
            // computeVisualItems knows to always let synthetic items (line_num < 0) through,
            // even in conversation mode which normally filters assistant messages.
            let workingMessage = null
            if (isAssistantTurn) {
                const { lineNum, kind: syntheticKind } = SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE

                // Walk backwards through items to find the most recent tool_use.
                // Skip over tool_result items (content_items whose content is all
                // tool_result entries) so that the status line keeps showing the
                // current tool name even after its result has arrived.
                // Also track whether we skipped any tool_result items: if so, the
                // tool_use has completed; otherwise it is still in progress.
                let toolUse = null
                let toolUseCompleted = false
                for (let i = items.length - 1; i >= 0; i--) {
                    const item = items[i]
                    if (item.kind === 'system') continue
                    if (item.kind !== 'assistant_message' && item.kind !== 'content_items') break
                    const parsed = getParsedContent(item)
                    if (!parsed) break
                    const contentArray = parsed?.message?.content
                    if (!Array.isArray(contentArray) || contentArray.length === 0) break
                    const lastContent = contentArray[contentArray.length - 1]
                    if (lastContent.type === 'tool_use') {
                        toolUse = lastContent
                        break
                    }
                    // If every entry is a tool_result, skip this item and keep looking
                    if (contentArray.every(c => c.type === 'tool_result')) {
                        toolUseCompleted = true
                        continue
                    }
                    // Otherwise (text, image, etc.) stop searching
                    break
                }

                workingMessage = {
                    line_num: lineNum,
                    content: null,
                    kind: 'assistant_message',
                    syntheticKind,
                    display_level: DISPLAY_LEVEL.ALWAYS,
                    group_head: null,
                    group_tail: null,
                }
                setParsedContent(workingMessage, {
                    type: 'assistant',
                    syntheticKind,
                    toolUse,
                    toolUseCompleted,
                    message: {
                        role: 'assistant',
                        content: []
                    }
                })
                allItems = allItems === items ? [...items, workingMessage] : [...allItems, workingMessage]
            }

            // Get detailed blocks for conversation mode (per-block detail toggle)
            const detailedBlocksArray = this.localState.sessionDetailedBlocks[sessionId] || []
            const detailedBlocks = new Set(detailedBlocksArray)

            const visualItems = computeVisualItems(allItems, mode, expandedGroups, isAssistantTurn, detailedBlocks)

            // Propagate syntheticKind to visual items for synthetic messages.
            // computeVisualItems doesn't know about syntheticKind, so we add it here.
            for (let i = visualItems.length - 1; i >= 0; i--) {
                const vi = visualItems[i]
                if (vi.lineNum === SYNTHETIC_ITEM.OPTIMISTIC_USER_MESSAGE.lineNum && optimistic) {
                    vi.syntheticKind = optimistic.syntheticKind
                } else if (vi.lineNum === SYNTHETIC_ITEM.STARTING_ASSISTANT_MESSAGE.lineNum && startingMessage) {
                    vi.syntheticKind = startingMessage.syntheticKind
                } else if (vi.lineNum === SYNTHETIC_ITEM.WORKING_ASSISTANT_MESSAGE.lineNum && workingMessage) {
                    vi.syntheticKind = workingMessage.syntheticKind
                }
                // Synthetic items are always at the end, stop as soon as we hit a real item
                if (vi.lineNum >= 0) break
            }

            // Stabilize visual item references: reuse cached objects when properties
            // haven't changed, so Vue sees the same reference and skips re-render.
            const cache = this.localState.visualItemCache[sessionId] || new Map()
            const newCache = new Map()

            const stableItems = visualItems.map(vi => {
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
                display_level: DISPLAY_LEVEL.ALWAYS,
                group_head: null,
                group_tail: null
            }
            // Build the content blocks: images/documents first, then text
            // (same order as the backend in process.py)
            const contentBlocks = []
            if (attachments?.images?.length) contentBlocks.push(...attachments.images)
            if (attachments?.documents?.length) contentBlocks.push(...attachments.documents)
            contentBlocks.push({ type: 'text', text })
            setParsedContent(optimisticItem, {
                type: 'user',
                syntheticKind,
                message: {
                    role: 'user',
                    content: contentBlocks
                }
            })
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
                }
            }

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

        // Agent links cache actions

        /**
         * Set an agent link in the cache.
         * @param {string} sessionId - The session ID
         * @param {string} toolId - The tool_use_id
         * @param {string} agentId - The agent ID (only cache when found)
         * @param {boolean} isBackground - Whether the agent runs in background
         */
        setAgentLink(sessionId, toolId, agentId, isBackground = false, toolUseLineNum = null) {
            if (!agentId) return // Only cache found agents
            if (!this.localState.agentLinks[sessionId]) {
                this.localState.agentLinks[sessionId] = {}
            }
            this.localState.agentLinks[sessionId][toolId] = { agentId, isBackground, toolUseLineNum }
        },

        /**
         * Clear agent links cache for a session.
         * @param {string} sessionId - The session ID
         */
        clearAgentLinks(sessionId) {
            delete this.localState.agentLinks[sessionId]
        },

        /**
         * Set tool state for a tool_use_id in a session.
         * @param {string} sessionId - The session ID
         * @param {string} toolUseId - The tool_use_id
         * @param {number} resultCount - The number of tool_results received
         * @param {string|null} completedAt - ISO timestamp of the latest tool_result
         * @param {string|null} error - Error message if the tool errored
         * @param {string|null} extra - Extra JSON data (e.g., file change stats)
         * @param {number|null} toolResultLineNum - Line number of the tool_result item
         */
        setToolState(sessionId, toolUseId, resultCount, completedAt, error = null, extra = null, toolResultLineNum = null) {
            if (!this.localState.toolStates[sessionId]) {
                this.localState.toolStates[sessionId] = {}
            }
            this.localState.toolStates[sessionId][toolUseId] = { resultCount, completedAt, error, extra, toolResultLineNum }
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
                            toolResultLineNum: state.tool_result_line_num ?? null,
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
         * @param {string} projectId - The project ID
         * @param {number|null} startedAtUnix - Unix timestamp (seconds) of when the agent started
         */
        setSyntheticProcessState(agentSessionId, projectId, startedAtUnix) {
            // Don't overwrite real process states (from ProcessManager)
            if (this.processStates[agentSessionId] && !this.processStates[agentSessionId].synthetic) {
                return
            }
            const wasAssistantTurn = this.processStates[agentSessionId]?.state === PROCESS_STATE.ASSISTANT_TURN
            this.processStates[agentSessionId] = {
                state: PROCESS_STATE.ASSISTANT_TURN,
                project_id: projectId,
                started_at: startedAtUnix,
                state_changed_at: startedAtUnix,
                memory: null,
                error: null,
                pending_request: null,
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
        async fetchSubagentsState(projectId, sessionId) {
            try {
                const url = `/api/projects/${projectId}/sessions/${sessionId}/subagents/`
                const response = await apiFetch(url)
                if (!response.ok) return

                const agents = await response.json()

                // Cutoff: agents started before this are definitely not running
                const cutoff = getSessionCutoffMs(this.sessions[sessionId])

                for (const agent of agents) {
                    this.setAgentLink(sessionId, agent.tool_use_id, agent.agent_id, agent.is_background, agent.tool_use_line_num)

                    // Skip synthetic process state if agent predates the session's last start/stop cycle
                    const agentStartedMs = agent.started_at ? new Date(agent.started_at).getTime() : 0
                    if (cutoff && agentStartedMs < cutoff) continue

                    // Create synthetic process state if agent is not done yet
                    const toolState = this.localState.toolStates[sessionId]?.[agent.tool_use_id]
                    const resultCount = toolState?.resultCount || 0
                    const requiredCount = agent.is_background ? 2 : 1
                    if (resultCount < requiredCount) {
                        const startedAtUnix = agent.started_at ? new Date(agent.started_at).getTime() / 1000 : null
                        this.setSyntheticProcessState(agent.agent_id, projectId, startedAtUnix)
                    }
                }
            } catch (error) {
                console.error('Failed to fetch subagents state:', error)
            }
        },

        // Process state actions

        /**
         * Set process state for a session (from WebSocket process_state message).
         * Removes the entry when state is 'dead'.
         * @param {string} sessionId
         * @param {string} projectId - The project ID this session belongs to
         * @param {string} state - 'starting' | 'assistant_turn' | 'user_turn' | 'dead'
         * @param {object} extra - Additional fields: started_at, state_changed_at, memory, error, pending_request, session_title, project_name
         */
        setProcessState(sessionId, projectId, state, extra = {}) {
            const previousState = this.processStates[sessionId]?.state
            const wasAssistantTurn = previousState === PROCESS_STATE.ASSISTANT_TURN
            const wasStarting = previousState === PROCESS_STATE.STARTING

            if (state === 'dead') {
                // Remove dead processes from the map
                delete this.processStates[sessionId]
            } else {
                this.processStates[sessionId] = {
                    state,
                    project_id: projectId,
                    started_at: extra.started_at || null,
                    state_changed_at: extra.state_changed_at || null,
                    memory: extra.memory || null,
                    error: extra.error || null,
                    pending_request: extra.pending_request || null,
                    active_crons: extra.active_crons || null,
                    session_title: extra.session_title || null,
                    project_name: extra.project_name || null,
                }

                // Auto-unarchive: running and archived are mutually exclusive
                const session = this.sessions[sessionId]
                if (session?.archived && projectId) {
                    this.setSessionArchived(projectId, sessionId, false)
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
            // Clear existing states and rebuild from server data
            this.processStates = {}
            for (const p of processes) {
                // Only add non-dead processes
                if (p.state !== 'dead') {
                    this.processStates[p.session_id] = {
                        state: p.state,
                        project_id: p.project_id,
                        started_at: p.started_at || null,
                        state_changed_at: p.state_changed_at || null,
                        memory: p.memory || null,
                        error: p.error || null,
                        pending_request: p.pending_request || null,
                        active_crons: p.active_crons || null,
                        session_title: p.session_title || null,
                        project_name: p.project_name || null,
                    }

                    // Auto-unarchive: running and archived are mutually exclusive
                    const session = this.sessions[p.session_id]
                    if (session?.archived && p.project_id) {
                        this.setSessionArchived(p.project_id, p.session_id, false)
                    }
                }
            }
        },

        /**
         * Respond to a pending request on a session's process.
         * Sends the response via WebSocket.
         * @param {string} sessionId - The session ID
         * @param {string} requestId - The pending request ID
         * @param {object} responseData - The response payload:
         *   For tool approval: { request_type: 'tool_approval', decision: 'allow'|'deny', message?, updated_input? }
         *   For ask user question: { request_type: 'ask_user_question', answers: { questionText: selectedLabel, ... } }
         * @returns {boolean} True if the message was sent
         */
        async respondToPendingRequest(sessionId, requestId, responseData) {
            // Lazy import to avoid circular dependency (data.js ↔ useWebSocket.js)
            const { respondToPendingRequest: sendResponse } = await import('../composables/useWebSocket')
            return sendResponse(sessionId, requestId, responseData)
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

        // --- MRU (Most Recently Used) navigation tracking ---

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
         * Called when a session is archived or a draft is deleted.
         * @param {string} sessionId - The session ID to remove
         */
        removeMruSession(sessionId) {
            this.localState.mruPaths = this.localState.mruPaths.filter(
                entry => entry.sessionId !== sessionId
            )
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
                // Entries with a session: check the session is still valid
                const session = this.sessions[entry.sessionId]
                if (!session) continue
                if (session.archived) continue
                if (session.parent_session_id) continue
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
                    session.pinned = false
                }
            }

            // Remove from MRU when archiving
            if (archived) {
                this.removeMruSession(sessionId)
            }

            // Build the PATCH payload
            const patchData = { archived }
            if (shouldUnpin) {
                patchData.pinned = false
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
         * Set the pinned state of a session.
         * @param {string} projectId - The project ID
         * @param {string} sessionId - The session ID
         * @param {boolean} pinned - Whether to pin or unpin
         * @throws {Error} If the update fails
         */
        async setSessionPinned(projectId, sessionId, pinned) {
            // Optimistic update
            const session = this.sessions[sessionId]
            const oldPinned = session?.pinned

            if (session) {
                session.pinned = pinned
            }

            try {
                const response = await apiFetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/`,
                    {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ pinned })
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
         * Set the draft title for a session (draft sessions only).
         * Called by SessionRenameDialog when title is modified before first message.
         * Updates the draft session in IndexedDB with the new title.
         * @param {string} sessionId
         * @param {string} title
         */
        setDraftTitle(sessionId, title) {
            const session = this.sessions[sessionId]
            if (!session?.draft) return

            // Update IndexedDB with projectId and title (fire and forget)
            saveDraftSession(sessionId, {
                projectId: session.project_id,
                title
            }).catch(err =>
                console.warn('Failed to save draft session title to IndexedDB:', err)
            )
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
         * mtime=now, last_line=0, draft=true.
         */
        async hydrateDraftSessions() {
            try {
                const draftSessions = await getAllDraftSessions()
                const now = Date.now() / 1000
                for (const [sessionId, { projectId, title }] of Object.entries(draftSessions)) {
                    this.sessions[sessionId] = {
                        id: sessionId,
                        project_id: projectId,
                        title: title || null,  // null = user hasn't set a title yet
                        mtime: now,
                        last_line: 0,
                        draft: true,
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
            // Always store the response so the frontend knows the request completed
            // (distinguishes "no response yet" from "response received with failure")
            this.localState.titleSuggestions[sessionId] = {
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

        // =========================================================================
        // Attachment actions (for document upload)
        // =========================================================================

        /**
         * Add a file attachment to a session.
         * Processes the file (validation + encoding) and stores in IndexedDB.
         * @param {string} sessionId - The session ID
         * @param {File} file - The file to add
         * @returns {Promise<DraftMedia>} The processed media object
         * @throws {Error} If validation fails or file cannot be processed
         */
        async addAttachment(sessionId, file) {
            // Track that a file is being processed (blocks the send button)
            this.localState.processingAttachments[sessionId] =
                (this.localState.processingAttachments[sessionId] || 0) + 1

            try {
                // Process file (validates and encodes)
                const media = await processFile(file, sessionId)

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
