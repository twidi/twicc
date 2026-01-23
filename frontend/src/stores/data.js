// frontend/src/stores/data.js

import { defineStore } from 'pinia'

export const useDataStore = defineStore('data', {
    state: () => ({
        // Server data
        projects: {},       // { id: { id, sessions_count, mtime, archived } }
        sessions: {},       // { id: { id, project_id, last_line, mtime, archived } }
        sessionItems: {},   // { sessionId: [{ line_num, content }, ...] }

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

        // Session Items - handles out-of-order arrivals by maintaining line_num sort
        addSessionItems(sessionId, newItems) {
            this.$patch((state) => {
                if (!newItems?.length) return

                if (!state.sessionItems[sessionId]) {
                    state.sessionItems[sessionId] = []
                }

                const existing = state.sessionItems[sessionId]

                // Sort new items first
                const sorted = newItems.toSorted((a, b) => a.line_num - b.line_num)

                // Nothing existing → just assign sorted
                if (existing.length === 0) {
                    existing.push(...sorted)
                    return
                }


                const lastExisting = existing[existing.length - 1].line_num
                const firstExisting = existing[0].line_num
                const firstNew = sorted[0].line_num
                const lastNew = sorted[sorted.length - 1].line_num

                if (firstNew > lastExisting) {
                    // Case 1: all new items come after → push
                    existing.push(...sorted)
                } else if (lastNew < firstExisting) {
                    // Case 2: all new items come before → unshift
                    existing.unshift(...sorted)
                } else {
                    // Case 3: overlap → push + full re-sort
                    existing.push(...sorted)
                    existing.sort((a, b) => a.line_num - b.line_num)
                }
            })
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
         * @param {Array<string|[number, number|null]>} ranges - Array of ranges:
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
