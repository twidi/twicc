// frontend/src/stores/data.js

import { defineStore } from 'pinia'
import { DEFAULT_DISPLAY_MODE } from '../constants'
import { computeVisualItems } from '../utils/visualItems'

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
            projects: {},   // { projectId: { sessionsFetched, sessionsLoading, sessionsLoadingError } }
            sessions: {},   // { sessionId: { itemsFetched, itemsLoading, itemsLoadingError } }

            // Display mode - global, not per-session
            // Values: 'debug' | 'normal' | 'simplified'
            sessionItemsDisplayMode: DEFAULT_DISPLAY_MODE,

            // Expanded groups - per session
            // { sessionId: [groupHeadLineNum, ...] }
            // Using array instead of Set for Vue reactivity
            sessionExpandedGroups: {},

            // Visual items - computed from sessionItems, display mode, and expanded groups
            // { sessionId: [{ lineNum, isGroupHead?, isExpanded? }, ...] }
            sessionVisualItems: {}
        }
    }),

    getters: {
        // Data getters (sorted by mtime descending - most recent first)
        getProjects: (state) => Object.values(state.projects).sort((a, b) => b.mtime - a.mtime),
        getProject: (state) => (id) => state.projects[id],
        getProjectSessions: (state) => (projectId) =>
            Object.values(state.sessions)
                .filter(s => s.project_id === projectId)
                .sort((a, b) => b.mtime - a.mtime),
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
        areSessionItemsFetched: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsFetched ?? false,

        // Display mode getter
        getDisplayMode: (state) => state.localState.sessionItemsDisplayMode,

        // Get expanded groups for a session (returns array)
        getExpandedGroups: (state) => (sessionId) =>
            state.localState.sessionExpandedGroups[sessionId] || [],

        // Check if a group is expanded
        isGroupExpanded: (state) => (sessionId, groupHeadLineNum) => {
            const groups = state.localState.sessionExpandedGroups[sessionId]
            return groups ? groups.includes(groupHeadLineNum) : false
        },

        // Get a single item by lineNum (handles 1-based to 0-based conversion)
        getSessionItem: (state) => (sessionId, lineNum) => {
            const items = state.sessionItems[sessionId]
            if (!items || lineNum < 1) return null
            return items[lineNum - 1] || null
        },

        // Get visual items for a session
        getSessionVisualItems: (state) => (sessionId) =>
            state.localState.sessionVisualItems[sessionId] || []
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
         */
        addSessionItems(sessionId, newItems) {
            if (!newItems?.length) return

            const items = this.sessionItems[sessionId]
            if (!items) {
                // Not initialized yet - create array from the items we have
                // Find max line_num to know array size
                const maxLineNum = Math.max(...newItems.map(item => item.line_num))
                this.sessionItems[sessionId] = Array.from(
                    { length: maxLineNum },
                    (_, index) => ({ line_num: index + 1 })
                )
            }

            const targetArray = this.sessionItems[sessionId]

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
         * Load sessions for a project from the API.
         * @param {string} projectId
         * @param {Object} options
         * @param {boolean} options.force - Force reload even if already fetched
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         * @returns {Promise<Set<string>>} Set of session IDs that have changed
         *          (sessions where itemsFetched=true AND mtime changed or new)
         */
        async loadSessions(projectId, { force = false, isInitialLoading = false } = {}) {
            const changedIds = new Set()

            // Skip if already fetched (unless forced)
            if (!force && this.localState.projects[projectId]?.sessionsFetched) {
                return changedIds
            }

            // Initialize localState for this project if needed
            if (!this.localState.projects[projectId]) {
                this.localState.projects[projectId] = {}
            }
            this.localState.projects[projectId].sessionsLoading = true

            try {
                const res = await fetch(`/api/projects/${projectId}/sessions/`)
                if (!res.ok) {
                    console.error('Failed to load sessions:', res.status, res.statusText)
                    if (isInitialLoading) {
                        this.localState.projects[projectId].sessionsLoadingError = true
                    }
                    return changedIds
                }
                const freshSessions = await res.json()
                for (const fresh of freshSessions) {
                    const local = this.sessions[fresh.id]
                    const wasItemsFetched = this.localState.sessions[fresh.id]?.itemsFetched

                    // Session changed if: itemsFetched AND (new OR mtime different)
                    if (wasItemsFetched && (!local || local.mtime !== fresh.mtime)) {
                        changedIds.add(fresh.id)
                    }

                    // Update store
                    this.sessions[fresh.id] = fresh
                }
                // Mark as fetched in localState and clear any previous error
                this.localState.projects[projectId].sessionsFetched = true
                this.localState.projects[projectId].sessionsLoadingError = false
                return changedIds
            } catch (error) {
                console.error('Failed to load sessions:', error)
                if (isInitialLoading) {
                    this.localState.projects[projectId].sessionsLoadingError = true
                }
                throw error  // Re-throw for reconciliation retry logic
            } finally {
                this.localState.projects[projectId].sessionsLoading = false
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
         * @param {Object} options
         * @param {boolean} options.isInitialLoading - If true, enables UI feedback (loading states, error handling)
         */
        async loadSessionItemsRanges(projectId, sessionId, ranges, { isInitialLoading = false } = {}) {
            if (!ranges?.length) return

            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }

            // Only set loading if isInitialLoading is true (initial load case)
            if (isInitialLoading) {
                this.localState.sessions[sessionId].itemsLoading = true
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

            try {
                const res = await fetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/items/?${params}`
                )
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
                if (isInitialLoading) {
                    this.localState.sessions[sessionId].itemsLoadingError = true
                }
            } finally {
                if (isInitialLoading) {
                    this.localState.sessions[sessionId].itemsLoading = false
                }
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

            const mode = this.localState.sessionItemsDisplayMode
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

        // Display mode and expanded groups actions

        /**
         * Set display mode.
         * @param {string} mode - 'debug' | 'normal' | 'simplified'
         */
        setDisplayMode(mode) {
            this.localState.sessionItemsDisplayMode = mode
            this.recomputeAllVisualItems()
        },

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
         * Load metadata for all items in a session (without content).
         * @param {string} projectId
         * @param {string} sessionId
         * @returns {Promise<Array|null>} Array of metadata objects or null on error
         */
        async loadSessionMetadata(projectId, sessionId) {
            try {
                const res = await fetch(
                    `/api/projects/${projectId}/sessions/${sessionId}/items/metadata/`
                )
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
        }
    }
})
