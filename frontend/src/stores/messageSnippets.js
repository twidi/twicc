import { defineStore } from 'pinia'

/**
 * Store for message input snippets (reusable text snippets for the chat input).
 * Data is persisted in ~/.twicc/message-snippets.json via WebSocket sync.
 *
 * Snippet shape: { label: string, text: string }
 * - label is optional (empty string if not set)
 * - text is the message content to insert
 */
export const useMessageSnippetsStore = defineStore('messageSnippets', {
    state: () => ({
        snippets: {}, // { global: [], "project:<id>": [] }
        _initialized: false,
    }),

    getters: {
        /**
         * Get snippets for a given project: global + project-specific, merged.
         * @returns {Function} (projectId: string) => Array
         */
        getSnippetsForProject: (state) => (projectId) => {
            const global = state.snippets.global || []
            const projectKey = `project:${projectId}`
            const project = state.snippets[projectKey] || []
            return [...global, ...project]
        },

        /**
         * Check if there are any snippets for a given project (global or project-specific).
         * @returns {Function} (projectId: string) => boolean
         */
        hasSnippetsForProject: (state) => (projectId) => {
            const global = state.snippets.global || []
            const projectKey = `project:${projectId}`
            const project = state.snippets[projectKey] || []
            return global.length > 0 || project.length > 0
        },

        /**
         * Get all snippet scopes that have entries, for the manage dialog.
         * Returns array of { scope, snippets } sorted: global first, then projects alphabetically.
         * @returns {Array<{ scope: string, snippets: Array }>}
         */
        allSnippetScopes: (state) => {
            const result = []
            // Global first (only if it has snippets)
            const global = state.snippets.global || []
            if (global.length > 0) {
                result.push({ scope: 'global', snippets: global })
            }
            // Project scopes (only those with snippets)
            const projectScopes = Object.keys(state.snippets)
                .filter(k => k.startsWith('project:') && state.snippets[k]?.length > 0)
                .sort()
            for (const scope of projectScopes) {
                result.push({ scope, snippets: state.snippets[scope] })
            }
            return result
        },
    },

    actions: {
        /**
         * Apply config received from WebSocket (on connect or broadcast).
         */
        applyConfig(config) {
            this.snippets = config.snippets || {}
            this._initialized = true
        },

        /**
         * Send the full config to the backend via WebSocket.
         * Uses lazy import to avoid circular dependency with useWebSocket.
         */
        async _sendConfig() {
            const { sendMessageSnippetsConfig } = await import('../composables/useWebSocket')
            sendMessageSnippetsConfig({
                snippets: this.snippets,
            })
        },

        // ── Snippet mutations ────────────────────────────────

        addSnippet(scope, snippet) {
            if (!this.snippets[scope]) {
                this.snippets[scope] = []
            }
            this.snippets[scope].push(snippet)
            this._sendConfig()
        },

        updateSnippet(scope, index, snippet, newScope = null) {
            if (newScope && newScope !== scope) {
                // Move to different scope
                this.snippets[scope].splice(index, 1)
                // Clean up empty scope arrays
                if (this.snippets[scope].length === 0 && scope !== 'global') {
                    delete this.snippets[scope]
                }
                if (!this.snippets[newScope]) {
                    this.snippets[newScope] = []
                }
                this.snippets[newScope].push(snippet)
            } else {
                this.snippets[scope][index] = snippet
            }
            this._sendConfig()
        },

        deleteSnippet(scope, index) {
            this.snippets[scope].splice(index, 1)
            // Clean up empty scope arrays (but keep "global" even if empty)
            if (this.snippets[scope].length === 0 && scope !== 'global') {
                delete this.snippets[scope]
            }
            this._sendConfig()
        },

        reorderSnippet(scope, fromIndex, toIndex) {
            const arr = this.snippets[scope]
            if (!arr || toIndex < 0 || toIndex >= arr.length) return
            const [item] = arr.splice(fromIndex, 1)
            arr.splice(toIndex, 0, item)
            this._sendConfig()
        },
    },
})
