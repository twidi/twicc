// frontend/src/stores/data.js

import { defineStore } from 'pinia'

export const useDataStore = defineStore('data', {
    state: () => ({
        // Server data
        projects: {},       // { id: { id, sessions_count, mtime, archived } }
        sessions: {},       // { id: { id, project_id, last_line, mtime, archived } }
        sessionItems: {},   // { sessionId: [{ line_num, content }, ...] } - line_num is 1-based

        // Local UI state (separate from server data to avoid being overwritten)
        localState: {
            projects: {},   // { projectId: { sessionsFetched: boolean } }
            sessions: {}    // { sessionId: { itemsFetched: boolean, itemsLoading: boolean } }
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
        // Local state getters
        areSessionItemsLoading: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsLoading ?? false,
        areProjectSessionsFetched: (state) => (projectId) =>
            state.localState.projects[projectId]?.sessionsFetched ?? false,
        areSessionItemsFetched: (state) => (sessionId) =>
            state.localState.sessions[sessionId]?.itemsFetched ?? false
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
        },

        // Initial loading from API
        async loadProjects() {
            try {
                const res = await fetch('/api/projects/')
                if (!res.ok) {
                    console.error('Failed to load projects:', res.status, res.statusText)
                    return
                }
                const projects = await res.json()
                for (const p of projects) {
                    this.projects[p.id] = p
                }
            } catch (error) {
                console.error('Failed to load projects:', error)
            }
        },
        async loadSessions(projectId) {
            // Skip if already fetched
            if (this.localState.projects[projectId]?.sessionsFetched) {
                return
            }
            try {
                const res = await fetch(`/api/projects/${projectId}/sessions/`)
                if (!res.ok) {
                    console.error('Failed to load sessions:', res.status, res.statusText)
                    return
                }
                const sessions = await res.json()
                for (const s of sessions) {
                    this.sessions[s.id] = s
                }
                // Mark as fetched in localState
                if (!this.localState.projects[projectId]) {
                    this.localState.projects[projectId] = {}
                }
                this.localState.projects[projectId].sessionsFetched = true
            } catch (error) {
                console.error('Failed to load sessions:', error)
            }
        },
        async loadSessionItems(projectId, sessionId) {
            // Skip if already fetched
            if (this.localState.sessions[sessionId]?.itemsFetched) {
                return
            }
            // Initialize localState for this session if needed
            if (!this.localState.sessions[sessionId]) {
                this.localState.sessions[sessionId] = {}
            }
            this.localState.sessions[sessionId].itemsLoading = true
            try {
                const res = await fetch(`/api/projects/${projectId}/sessions/${sessionId}/items/`)
                if (!res.ok) {
                    console.error('Failed to load session items:', res.status, res.statusText)
                    return
                }
                const items = await res.json()
                this.sessionItems[sessionId] = items
                this.localState.sessions[sessionId].itemsFetched = true
            } catch (error) {
                console.error('Failed to load session items:', error)
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
         */
        async loadSessionItemsRanges(projectId, sessionId, ranges) {
            if (!ranges?.length) return

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
                    return
                }
                const items = await res.json()
                this.addSessionItems(sessionId, items)
            } catch (error) {
                console.error('Failed to load session items ranges:', error)
            }
        }
    }
})
