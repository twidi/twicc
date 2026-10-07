import { defineStore } from 'pinia'
import { useSettingsStore } from './settings'
import { useDataStore } from './data'
import { moveVisibleItems } from '../utils/listReorder'

export const useWorkspacesStore = defineStore('workspaces', {
    state: () => ({
        workspaces: [],           // Array of { id, name, archived, projectIds: string[], autoProjectPatterns?: string[], browserUrls?: [{url, label?, default?}] }
        _isApplyingRemote: false, // Guard to prevent echo on WS receive
    }),

    getters: {
        /** All workspaces, in their stored order. */
        getAllWorkspaces: (state) => state.workspaces,

        /** Get a workspace by its ID. */
        getWorkspaceById: (state) => (id) =>
            state.workspaces.find(w => w.id === id) || null,

        /**
         * Visible project IDs for a given workspace, respecting "show archived projects".
         * Returns the member projects in their custom order, each immediately
         * followed by its own git worktrees.
         *
         * A worktree's content (sessions, cost, activity) belongs to its main
         * repository's whole, so a member project's worktrees are part of the
         * workspace's visible set even when the worktree itself was never added
         * to the workspace — what matters is that the *parent* is a member.
         * Worktrees are still subject to the archived filter, and are only
         * pulled in for members that are themselves visible.
         *
         * Callers that need member projects only (counts, pickers, the member
         * list) filter worktrees out via `!project.worktree_of`.
         */
        getVisibleProjectIds() {
            return (workspaceId) => {
                const ws = this.getWorkspaceById(workspaceId)
                if (!ws) return []
                const dataStore = useDataStore()
                const settingsStore = useSettingsStore()
                const showArchived = settingsStore.isShowArchivedProjects
                const passes = (p) => p && (showArchived || !p.archived)
                const result = []
                const seen = new Set()
                const add = (id) => {
                    if (!seen.has(id)) {
                        seen.add(id)
                        result.push(id)
                    }
                }
                for (const pid of ws.projectIds) {
                    if (!passes(dataStore.getProject(pid))) continue
                    add(pid)
                    for (const wt of dataStore.getWorktreesOf(pid)) {
                        if (passes(wt)) add(wt.id)
                    }
                }
                return result
            }
        },

        /**
         * All project IDs of a workspace — every member plus each member's git
         * worktrees — regardless of archived state. Use for the workspace home
         * page aggregations (counter, cost, heatmaps, sparklines): they reflect
         * the workspace as a whole, including archived projects and worktrees
         * (a worktree's activity belongs to its main repository's whole).
         */
        getAllProjectIds() {
            return (workspaceId) => {
                const ws = this.getWorkspaceById(workspaceId)
                if (!ws) return []
                const dataStore = useDataStore()
                const result = []
                const seen = new Set()
                const add = (id) => {
                    if (!seen.has(id)) {
                        seen.add(id)
                        result.push(id)
                    }
                }
                for (const pid of ws.projectIds) {
                    add(pid)
                    for (const wt of dataStore.getWorktreesOf(pid)) add(wt.id)
                }
                return result
            }
        },

        /**
         * Working directory for a workspace terminal: the longest common path
         * prefix of the workspace's visible project directories (its "common
         * directory"). Returns the single project's dir when there's only one,
         * '/' when the projects diverge at the root, or null when the workspace
         * has no resolvable dirs. Used both by the workspace terminal panel and
         * when attaching a workspace terminal into a child panel.
         */
        getTerminalCwd() {
            return (workspaceId) => {
                const dataStore = useDataStore()
                const dirs = this.getVisibleProjectIds(workspaceId)
                    .map(pid => dataStore.getProject(pid))
                    .map(p => p?.directory)
                    .filter(Boolean)
                if (dirs.length === 0) return null
                if (dirs.length === 1) return dirs[0]
                // Find the longest common path prefix
                const parts = dirs.map(d => d.split('/'))
                const common = []
                for (let i = 0; i < parts[0].length; i++) {
                    const segment = parts[0][i]
                    if (parts.every(p => p[i] === segment)) {
                        common.push(segment)
                    } else {
                        break
                    }
                }
                return common.length > 1 ? common.join('/') : '/'
            }
        },

        /** Whether a workspace is activable (has at least one visible project). */
        isActivable() {
            return (workspaceId) => this.getVisibleProjectIds(workspaceId).length > 0
        },

        /** Non-archived workspaces that are activable. For use in selectors. */
        getSelectableWorkspaces() {
            const settingsStore = useSettingsStore()
            const showArchivedWs = settingsStore.isShowArchivedWorkspaces
            return this.workspaces.filter(ws =>
                (showArchivedWs || !ws.archived) && this.isActivable(ws.id)
            )
        },

        /** Get all non-archived workspaces that contain a given project ID. */
        getWorkspacesForProject: (state) => (projectId) =>
            state.workspaces.filter(ws => !ws.archived && ws.projectIds.includes(projectId)),

        /**
         * Whether a workspace contains a project, treating a member's git
         * worktrees as belonging to the workspace too: a worktree is "in" the
         * workspace when its main repository (`worktree_of`) is a member, even
         * if the worktree itself was never added to `projectIds`. This is the
         * single membership test used for workspace stickiness (router guard)
         * and for surfacing `workspace`-pinned sessions in the sidebar.
         *
         * Both the project id and its main-repo id are checked, so a worktree
         * that *was* explicitly added to the workspace still matches. Unknown
         * projects degrade to the plain explicit-membership check (since
         * `getMainRepoProjectId` returns the id unchanged). Archived state is
         * intentionally ignored — membership does not depend on it.
         */
        workspaceContainsProject() {
            return (workspaceId, projectId) => {
                const ws = this.getWorkspaceById(workspaceId)
                if (!ws || !projectId) return false
                if (ws.projectIds.includes(projectId)) return true
                const mainRepoId = useDataStore().getMainRepoProjectId(projectId)
                return mainRepoId !== projectId && ws.projectIds.includes(mainRepoId)
            }
        },

        /** Whether any archived workspace exists (to show/hide the toggle). */
        hasArchivedWorkspaces: (state) => state.workspaces.some(w => w.archived),
    },

    actions: {
        /** Apply workspaces received from the backend (WS message). */
        applyWorkspaces(workspaces) {
            this._isApplyingRemote = true
            this.workspaces = workspaces || []
            this._isApplyingRemote = false
        },

        /** Send current workspaces to the backend via WebSocket. */
        async _sendWorkspaces() {
            if (this._isApplyingRemote) return
            const { sendWorkspaces } = await import('../composables/useWebSocket')
            sendWorkspaces(this.workspaces)
        },

        /** Generate a human-readable workspace ID from the name.
         *  Keeps alphanumeric, underscores and hyphens; replaces everything else with a hyphen.
         *  Collapses consecutive hyphens, trims leading/trailing hyphens, lowercases.
         *  If the resulting ID already exists, appends -2, -3, etc. */
        _generateId(name) {
            let base = name
                .toLowerCase()
                .replace(/[^a-z0-9_-]/g, '-')
                .replace(/-{2,}/g, '-')
                .replace(/^-|-$/g, '')
            if (!base) base = 'workspace'

            const existingIds = new Set(this.workspaces.map(w => w.id))
            if (!existingIds.has(base)) return base

            let suffix = 2
            while (existingIds.has(`${base}-${suffix}`)) suffix++
            return `${base}-${suffix}`
        },

        /** Create a new workspace. Returns the new workspace object. */
        createWorkspace({ name, projectIds = [], archived = false, color = null, autoProjectPatterns = [], browserUrls = [] }) {
            const trimmedName = name.trim()
            const ws = {
                id: this._generateId(trimmedName),
                name: trimmedName,
                archived,
                projectIds,
                color,
                autoProjectPatterns,
                browserUrls,
            }
            this.workspaces.push(ws)
            this._sendWorkspaces()
            return ws
        },

        /** Update an existing workspace. */
        updateWorkspace(id, { name, projectIds, archived, color, autoProjectPatterns, browserUrls }) {
            const ws = this.workspaces.find(w => w.id === id)
            if (!ws) return
            if (name !== undefined) ws.name = name.trim()
            if (projectIds !== undefined) ws.projectIds = projectIds
            if (archived !== undefined) ws.archived = archived
            if (color !== undefined) ws.color = color
            if (autoProjectPatterns !== undefined) ws.autoProjectPatterns = autoProjectPatterns
            if (browserUrls !== undefined) ws.browserUrls = browserUrls
            this._sendWorkspaces()
        },

        /** Delete a workspace by ID. */
        deleteWorkspace(id) {
            const index = this.workspaces.findIndex(w => w.id === id)
            if (index === -1) return
            this.workspaces.splice(index, 1)
            this._sendWorkspaces()
        },

        /** Apply targeted membership changes for a single project across workspaces.
         *  Adds the project to each workspace in addWorkspaceIds (idempotent) and
         *  removes it from each in removeWorkspaceIds, touching only this project's
         *  entry — so concurrent changes to other projects' membership (or a
         *  workspace auto-added meanwhile) are preserved. Sends a single whole-blob
         *  update at the end. Used by the project edit dialog (edit mode). */
        applyProjectMembershipDeltas(projectId, addWorkspaceIds = [], removeWorkspaceIds = []) {
            const addSet = new Set(addWorkspaceIds)
            const removeSet = new Set(removeWorkspaceIds)
            let changed = false
            for (const ws of this.workspaces) {
                if (addSet.has(ws.id) && !ws.projectIds.includes(projectId)) {
                    ws.projectIds.push(projectId)
                    changed = true
                } else if (removeSet.has(ws.id) && ws.projectIds.includes(projectId)) {
                    ws.projectIds = ws.projectIds.filter(pid => pid !== projectId)
                    changed = true
                }
            }
            if (changed) this._sendWorkspaces()
        },

        /** Reorder workspaces (move from fromIndex to toIndex). */
        reorderWorkspace(fromIndex, toIndex, indices = this.workspaces.map((_, index) => index)) {
            if (!moveVisibleItems(this.workspaces, indices, fromIndex, toIndex)) return
            this._sendWorkspaces()
        },
    },
})
