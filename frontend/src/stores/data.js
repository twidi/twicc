// frontend/src/stores/data.js

import { defineStore } from 'pinia'
import { getPrefixSuffixBoundaries } from '../utils/contentVisibility'
import { computeVisualItems } from '../utils/visualItems'
import { useSettingsStore } from './settings'

// Special project ID for "All Projects" mode
export const ALL_PROJECTS_ID = '__all__'

export const useDataStore = defineStore('data', {
    state: () => ({
        // Server data
        projects: {},       // { id: { id, sessions_count, mtime, archived } }
        sessions: {},       // { id: { id, project_id, last_line, mtime, archived } }
        sessionItems: {},   // { sessionId: [{ line_num, content }, ...] } - line_num is 1-based

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

            // Visual items - computed from sessionItems, display mode, and expanded groups
            // { sessionId: [{ lineNum, isGroupHead?, isExpanded? }, ...] }
            sessionVisualItems: {},

            // Open tabs per session - for tab restoration when returning to a session
            // { sessionId: { tabs: ['main', 'agent-xxx', ...], activeTab: 'agent-xxx' } }
            // Note: 'main' is always implicitly open, but included for consistency
            sessionOpenTabs: {},

            // Agent links cache - maps tool_id to agent_id for Task tool_use items
            // { sessionId: { toolId: agentId | null } }
            // null means explicitly not found (avoid re-fetching)
            agentLinks: {}
        }
    }),

    getters: {
        // Data getters (sorted by mtime descending - most recent first)
        getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
        getProject: (state) => (id) => state.projects[id],
        getProjectSessions: (state) => (projectId) => {
            const oldestMtime = state.localState.projects[projectId]?.oldestSessionMtime
            return Object.values(state.sessions)
                .filter(s => s.project_id === projectId && !s.parent_session_id)
                // Filter to only sessions within the fetched range (mtime >= oldestMtime)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort((a, b) => b.mtime - a.mtime)
        },
        getAllSessions: (state) => {
            const oldestMtime = state.localState.projects[ALL_PROJECTS_ID]?.oldestSessionMtime
            return Object.values(state.sessions)
                .filter(s => !s.parent_session_id)
                // Filter to only sessions within the fetched range (mtime >= oldestMtime)
                .filter(s => oldestMtime == null || s.mtime >= oldestMtime)
                .sort((a, b) => b.mtime - a.mtime)
        },
        getSession: (state) => (id) => state.sessions[id],
        getSessionItems: (state) => (sessionId) => state.sessionItems[sessionId] || [],

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

        // Get open tabs for a session
        getSessionOpenTabs: (state) => (sessionId) =>
            state.localState.sessionOpenTabs[sessionId] || null,

        // Get cached agent link for a tool_id in a session
        // Returns: agentId (string), null (not found), or undefined (not fetched yet)
        getAgentLink: (state) => (sessionId, toolId) => {
            const sessionLinks = state.localState.agentLinks[sessionId]
            if (!sessionLinks) return undefined
            return sessionLinks.hasOwnProperty(toolId) ? sessionLinks[toolId] : undefined
        }
    },

    actions: {
        // Projects
        addProject(project) {
            this.$patch({ projects: { [project.id]: project } })
        },
        updateProject(project) {
            // $patch does a deep merge: only modified props trigger a re-render
            this.$patch({ projects: { [project.id]: project } })
        },

        // Sessions
        addSession(session) {
            this.$patch({ sessions: { [session.id]: session } })
        },
        updateSession(session) {
            this.$patch({ sessions: { [session.id]: session } })
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
                        if (!hadGroupTail && willHaveGroupTail && existingItem.content) {
                            this._migrateInternalSuffixToExternal(sessionId, update.line_num, existingItem.content)
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
         * @param {string} contentString - The raw JSON content of the item
         * @private
         */
        _migrateInternalSuffixToExternal(sessionId, lineNum, contentString) {
            // Check if there are any internal expanded groups for this item
            const itemInternalGroups = this.localState.sessionInternalExpandedGroups[sessionId]?.[lineNum]
            if (!itemInternalGroups?.length) return

            // Parse content to find the suffix boundaries
            let parsed
            try {
                parsed = JSON.parse(contentString)
            } catch {
                return
            }

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
                const res = await fetch('/api/projects/')
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
            const res = await fetch(url)

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
                const res = await fetch(`/api/projects/${projectId}/sessions/${sessionId}/items/`)
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
                const res = await fetch(`${baseUrl}/items/?${params}`)
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
            delete this.localState.agentLinks[sessionId]
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
            const items = this.sessionItems[sessionId]
            if (!items) {
                this.localState.sessionVisualItems[sessionId] = []
                return
            }

            // Get effective display mode from settings store
            const settingsStore = useSettingsStore()
            const mode = settingsStore.getDisplayMode
            const expandedGroups = this.localState.sessionExpandedGroups[sessionId] || []

            this.localState.sessionVisualItems[sessionId] = computeVisualItems(items, mode, expandedGroups)
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
                const res = await fetch(`${baseUrl}/items/metadata/`)
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
                    // Update content
                    sessionItemsArray[index].content = item.content
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
         * @param {string|null} agentId - The agent ID or null if not found
         */
        setAgentLink(sessionId, toolId, agentId) {
            if (!this.localState.agentLinks[sessionId]) {
                this.localState.agentLinks[sessionId] = {}
            }
            this.localState.agentLinks[sessionId][toolId] = agentId
        },

        /**
         * Clear agent links cache for a session.
         * @param {string} sessionId - The session ID
         */
        clearAgentLinks(sessionId) {
            delete this.localState.agentLinks[sessionId]
        }
    }
})
