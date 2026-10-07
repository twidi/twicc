import { defineStore } from 'pinia'
import { createSnippetGroup, getGroupItems, mapGroupedEntries, moveGroupedEntry, removeSnippetGroup, renameSnippetGroup, updateGroupedEntry } from '../utils/snippetGroups.js'

/**
 * Store for terminal custom combos and snippets.
 * Data is persisted in ~/.twicc/terminal-config.json via WebSocket sync.
 */
export const useTerminalConfigStore = defineStore('terminalConfig', {
    state: () => ({
        combos: [],
        snippets: {}, // { global: [], "workspace:<id>": [], "project:<id>": [] }
        _initialized: false,
    }),

    getters: {
        /**
         * Get snippets for display on the bar: global + workspace(s) + project, merged.
         * @returns {Function} (projectId: string, workspaceIds?: string[]) => Array
         */
        getSnippetsForProject: (state) => (projectId, workspaceIds = null) => {
            const global = mapGroupedEntries((state.snippets.global || []), s => ({ ...s, _scope: 'global' }))
            const wsSnippets = (workspaceIds || []).flatMap(wsId =>
                mapGroupedEntries((state.snippets[`workspace:${wsId}`] || []), s => ({ ...s, _scope: `workspace:${wsId}` }))
            )
            const project = mapGroupedEntries((state.snippets[`project:${projectId}`] || []), s => ({ ...s, _scope: `project:${projectId}` }))
            return [...global, ...wsSnippets, ...project]
        },

        /**
         * Get global snippets only (for "All Projects" terminal).
         * @returns {Function} () => Array
         */
        getGlobalSnippets: (state) => () => {
            return mapGroupedEntries((state.snippets.global || []), s => ({ ...s, _scope: 'global' }))
        },

        /**
         * Get global + workspace(s) + all projects in the workspace (for workspace terminals).
         * @returns {Function} (projectIds: string[], workspaceIds?: string[]) => Array
         */
        getSnippetsForWorkspace: (state) => (projectIds, workspaceIds = null) => {
            const global = mapGroupedEntries((state.snippets.global || []), s => ({ ...s, _scope: 'global' }))
            const wsSnippets = (workspaceIds || []).flatMap(wsId =>
                mapGroupedEntries((state.snippets[`workspace:${wsId}`] || []), s => ({ ...s, _scope: `workspace:${wsId}` }))
            )
            const projSnippets = projectIds.flatMap(pid =>
                mapGroupedEntries((state.snippets[`project:${pid}`] || []), s => ({ ...s, _scope: `project:${pid}` }))
            )
            return [...global, ...wsSnippets, ...projSnippets]
        },

        /**
         * Check if there are any snippets for a given project (global, workspace(s) or project-specific).
         * Used for visibility logic on desktop.
         * @returns {Function} (projectId: string, workspaceIds?: string[]) => boolean
         */
        hasSnippetsForProject: (state) => (projectId, workspaceIds = null) => {
            if ((state.snippets.global || []).length > 0) return true
            if ((state.snippets[`project:${projectId}`] || []).length > 0) return true
            return (workspaceIds || []).some(wsId => (state.snippets[`workspace:${wsId}`] || []).length > 0)
        },

        /**
         * Get all snippet scopes that have entries, for the Manage dialog.
         * Order: global, current workspace, current project, other workspace projects,
         * other workspaces with snippets, other projects, then orphan scopes.
         * @returns {Function}
         */
        allSnippetScopes: (state) => (currentProjectId, orderedProjectIds, currentWorkspaceId = null, currentWorkspaceProjectIds = null) => {
            const result = []
            const handledScopes = new Set()

            function push(scope) {
                if (handledScopes.has(scope)) return false
                if (!state.snippets[scope]?.length) return false
                result.push({ scope, snippets: state.snippets[scope] })
                handledScopes.add(scope)
                return true
            }

            // 1. Global
            if ((state.snippets.global || []).length > 0) {
                result.push({ scope: 'global', snippets: state.snippets.global })
                handledScopes.add('global')
            }

            // 2. Current workspace
            if (currentWorkspaceId) {
                push(`workspace:${currentWorkspaceId}`)
            }

            // 3. Current project
            if (currentProjectId) {
                push(`project:${currentProjectId}`)
            }

            // 4. Other projects in current workspace
            if (currentWorkspaceProjectIds) {
                for (const pid of currentWorkspaceProjectIds) {
                    if (pid === currentProjectId) continue
                    push(`project:${pid}`)
                }
            }

            // 5. Other workspaces with snippets
            for (const scope of Object.keys(state.snippets)) {
                if (!scope.startsWith('workspace:')) continue
                push(scope)
            }

            // 6. Other projects in the provided order
            for (const pid of orderedProjectIds) {
                push(`project:${pid}`)
            }

            // 7. Orphan scopes (deleted workspaces/projects that still have snippets)
            for (const scope of Object.keys(state.snippets)) {
                if (scope === 'global') continue
                push(scope)
            }

            return result
        },
    },

    actions: {
        /**
         * Apply config received from WebSocket (on connect or broadcast).
         */
        applyConfig(config) {
            this.combos = config.combos || []
            this.snippets = config.snippets || {}
            this._initialized = true
        },

        /**
         * Send the full config to the backend via WebSocket.
         * Uses lazy import to avoid circular dependency with useWebSocket.
         */
        async _sendConfig() {
            const { sendTerminalConfig } = await import('../composables/useWebSocket')
            sendTerminalConfig({
                combos: this.combos,
                snippets: this.snippets,
            })
        },

        // ── Combo mutations ──────────────────────────────────

        addCombo(combo, groupId = null) {
            const items = getGroupItems(this.combos, groupId)
            if (!items) return false
            items.push(combo)
            this._sendConfig()
            return true
        },

        updateCombo(index, combo, sourceGroupId = null, targetGroupId = null) {
            if (!updateGroupedEntry(this.combos, { index, groupId: sourceGroupId }, combo, targetGroupId)) return false
            this._sendConfig()
            return true
        },

        deleteCombo(index, groupId = null) {
            const items = getGroupItems(this.combos, groupId)
            if (!items?.[index]) return false
            items.splice(index, 1)
            this._sendConfig()
            return true
        },

        moveCombo(from, to) {
            if (!moveGroupedEntry(this.combos, from, to)) return false
            this._sendConfig()
            return true
        },

        reorderCombo(fromIndex, toIndex) {
            return this.moveCombo({ index: fromIndex }, { index: toIndex > fromIndex ? toIndex + 1 : toIndex })
        },

        addComboGroup(label) {
            if (!label.trim()) return false
            this.combos.push(createSnippetGroup(label))
            this._sendConfig()
            return true
        },

        renameComboGroup(id, label) {
            if (!renameSnippetGroup(this.combos, id, label)) return false
            this._sendConfig()
            return true
        },

        deleteComboGroup(id) {
            if (!removeSnippetGroup(this.combos, id)) return false
            this._sendConfig()
            return true
        },

        // ── Snippet mutations ────────────────────────────────

        addSnippet(scope, snippet, groupId = null) {
            const entries = this.snippets[scope] || (this.snippets[scope] = [])
            const items = getGroupItems(entries, groupId)
            if (!items) return false
            items.push(snippet)
            this._sendConfig()
            return true
        },

        updateSnippet(scope, index, snippet, newScope = null, sourceGroupId = null, targetGroupId = null) {
            const targetScope = newScope || scope
            const source = getGroupItems(this.snippets[scope] || [], sourceGroupId)
            const destination = getGroupItems(this.snippets[targetScope] || [], targetGroupId)
            if (!source?.[index] || !destination) return false
            if (scope === targetScope) {
                if (!updateGroupedEntry(this.snippets[scope], { index, groupId: sourceGroupId }, snippet, targetGroupId)) return false
            } else {
                this.snippets[targetScope] ||= []
                const target = getGroupItems(this.snippets[targetScope], targetGroupId)
                source.splice(index, 1)
                target.push(snippet)
                if (this.snippets[scope].length === 0 && scope !== 'global') delete this.snippets[scope]
            }
            this._sendConfig()
            return true
        },

        deleteSnippet(scope, index, groupId = null) {
            const items = getGroupItems(this.snippets[scope] || [], groupId)
            if (!items?.[index]) return false
            items.splice(index, 1)
            if (this.snippets[scope].length === 0 && scope !== 'global') delete this.snippets[scope]
            this._sendConfig()
            return true
        },

        moveSnippet(scope, from, to) {
            if (!moveGroupedEntry(this.snippets[scope] || [], from, to)) return false
            this._sendConfig()
            return true
        },

        reorderSnippet(scope, fromIndex, toIndex) {
            return this.moveSnippet(scope, { index: fromIndex }, { index: toIndex > fromIndex ? toIndex + 1 : toIndex })
        },

        addSnippetGroup(scope, label) {
            if (!label.trim()) return false
            const entries = this.snippets[scope] || (this.snippets[scope] = [])
            entries.push(createSnippetGroup(label))
            this._sendConfig()
            return true
        },

        renameSnippetGroup(scope, id, label) {
            if (!renameSnippetGroup(this.snippets[scope] || [], id, label)) return false
            this._sendConfig()
            return true
        },

        deleteSnippetGroup(scope, id) {
            if (!removeSnippetGroup(this.snippets[scope] || [], id)) return false
            if (this.snippets[scope].length === 0 && scope !== 'global') delete this.snippets[scope]
            this._sendConfig()
            return true
        },
    },
})
