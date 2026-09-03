/**
 * Static commands — registered once at app startup from App.vue.
 *
 * These commands don't depend on component lifecycle; they rely only on
 * store state, route state, and the router instance.
 *
 * Categories covered: navigation, creation, display, provider:<key>, ui.
 */

import { useCommandRegistry } from '../composables/useCommandRegistry'
import { ensureProjectTrust } from '../composables/useTrustGate'
import { useSettingsStore } from '../stores/settings'
import { useDataStore, ALL_PROJECTS_ID } from '../stores/data'
import { useWorkspacesStore } from '../stores/workspaces'
import { getRegisteredProviders, getProviderHelpers, getProviderOptions } from '../providers'
import { useRoute } from 'vue-router'
import { clearTabRouteParams } from '../utils/granularRoutes'
import { sessionRouteLocation } from '../utils/sessionRoute'
import { computeSidebarSessionBlocks } from '../utils/sidebarSessions'
import { isSessionUnread } from '../utils/sessions'
import { worktreeLabel } from '../utils/worktree'
import { resolveProjectIconUrl } from '../utils/projectIcon'
import { toWorkspaceProjectId } from '../utils/workspaceIds'
import { ARTIFACT_ICON } from '../utils/artifactBookmark'
import { AGENT_SETTING_ICONS } from '../utils/agentSettingIcons'
import { lastSessionsLocation, lastArtifactsLocation } from '../utils/sidebarViewMemory'
import { apiFetch } from '../utils/api'
import { toast } from '../composables/useToast'
import { useTerminalTabsStore } from '../stores/terminalTabs'
import { useWorkflowRunsStore } from '../stores/workflowRuns'
import { usePeersStore } from '../stores/peers'
import { useHelpStore } from '../stores/help'
import { usePeerSystemConfigured } from '../composables/usePeerSystemConfigured'
import { showHelp } from '../components/help/showHelp'
import { TERMINAL_ROUTES, WORKFLOW_ROUTES } from '../utils/tabRoutes'
import {
    DISPLAY_MODE,
    COLOR_SCHEME,
    WA_THEME,
    WA_THEME_LABELS,
    WA_BRAND,
    WA_BRAND_LABELS,
} from '../constants'

// Top-level command glyph for a boolean flag, aligned with the shared flag
// icon system (AGENT_SETTING_ICONS) so the command uses the same glyph/family/
// tint as the settings summary strip and the switch rows.
const flagCommandIcon = (field) => {
    const info = AGENT_SETTING_ICONS[field]
    return { icon: info.icon ?? null, iconSrc: info.src ?? null, iconFamily: info.family ?? null, iconColor: info.color }
}

// The boolean-flag fields whose nested On/Off choices render the flag icon
// (coloured when on, dimmed + struck when off) via SettingFlagIcon.
const FLAG_ICON_FIELDS = new Set(['thinking_enabled', 'fast_mode', 'claude_in_chrome'])

// Per-provider "Change Default …" palette command descriptors. Each entry
// targets one agent-setting wire-name; ``selected_model`` is handled out
// of band because it consumes the model registry's groupings.
const PROVIDER_DEFAULTS_SIMPLE_FIELDS = [
    { field: 'effort',                       idSuffix: 'effort',               label: 'Change Default Effort…',                       icon: 'gauge' },
    { field: 'permission_mode',              idSuffix: 'permission',           label: 'Change Default Permission Mode…',              icon: 'shield-halved' },
    { field: 'permission_mode_if_untrusted', idSuffix: 'permission-untrusted', label: 'Change Default Permission Mode (Untrusted)…',   icon: 'shield-halved' },
    { field: 'thinking_enabled',             idSuffix: 'thinking',             label: 'Change Default Thinking…',                     ...flagCommandIcon('thinking_enabled') },
    { field: 'context_max',                  idSuffix: 'context',              label: 'Change Default Context Size…',                 icon: 'window-maximize' },
    { field: 'claude_in_chrome',             idSuffix: 'chrome',               label: 'Change Default Claude in Chrome MCP…',         ...flagCommandIcon('claude_in_chrome') },
    { field: 'fast_mode',                    idSuffix: 'fast-mode',            label: 'Change Default Fast Mode…',                    ...flagCommandIcon('fast_mode') },
]

// Resolve the per-choice icon props for a nested "Change Default …" sub-item,
// reusing the value's real icon from the shared agent-setting icon system:
//   effort            → coloured 5-bar level SVG (iconSrc)
//   permission_mode*  → tinted permission glyph (permIcon + permColor)
//   flag fields       → on/off flag glyph (flagField + flagOn)
// Returns {} for fields without a per-value icon (e.g. context_max).
function choiceIconProps(helpers, field, value) {
    if (field === 'effort') {
        const iconSrc = helpers.getEffortIconSrc(value)
        return iconSrc ? { iconSrc } : {}
    }
    if (field === 'permission_mode' || field === 'permission_mode_if_untrusted') {
        const info = helpers.getChoiceIcon('permission_mode', value)
        return info ? { permIcon: info.icon, permColor: info.color } : {}
    }
    if (FLAG_ICON_FIELDS.has(field)) {
        return { flagField: field, flagOn: value === true }
    }
    return {}
}

/**
 * Build the "Change Default …" palette commands for every registered
 * provider. Each provider that supports an agent setting field
 * contributes one command; commands land in the per-provider category
 * (``provider:<key>``) so the palette renders one section per provider.
 *
 * Reads/writes go through ``helpers.getDefaultValue`` /
 * ``helpers.setDefaultValue`` so a model change re-applies
 * ``enforceAgentSettingsConsistency`` automatically (rolls back
 * context_max / effort when the new model doesn't support them).
 */
function buildProviderDefaultsCommands(settings) {
    const commands = []
    for (const provider of getRegisteredProviders()) {
        const helpers = getProviderHelpers(provider)
        if (!helpers) continue
        const category = `provider:${provider}`
        const whenEnabled = () => settings.enabledProviders.includes(provider)

        if (helpers.supportsAgentSetting('selected_model')) {
            commands.push({
                id: `${category}.model`,
                label: 'Change Default Model…',
                icon: 'robot',
                category,
                when: whenEnabled,
                items: () => {
                    const current = helpers.getDefaultValue('selected_model')
                    const groups = helpers.getModelSelectGroups(helpers.getModelRegistry?.() ?? [])
                    const items = []
                    groups.forEach((group, idx) => {
                        const groupKey = `model_group_${idx}`
                        for (const entry of group.entries ?? []) {
                            if (entry.disabled) continue
                            items.push({
                                id: entry.value,
                                group: groupKey,
                                label: entry.label,
                                action: () => helpers.setDefaultValue('selected_model', entry.value),
                                active: current === entry.value,
                            })
                        }
                    })
                    return items
                },
            })
        }

        for (const { field, idSuffix, label, icon, iconFamily, iconColor } of PROVIDER_DEFAULTS_SIMPLE_FIELDS) {
            if (!helpers.supportsAgentSetting(field)) continue
            commands.push({
                id: `${category}.${idSuffix}`,
                label,
                icon,
                iconFamily,
                iconColor,
                category,
                when: whenEnabled,
                items: () => {
                    const current = helpers.getDefaultValue(field)
                    const ctx = { effectiveModel: helpers.getDefaultValue('selected_model') }
                    return helpers.getFieldChoices(field)
                        .filter(choice => !helpers.isChoiceDisabled(field, choice.value, ctx))
                        .map(choice => ({
                            id: String(choice.value),
                            label: choice.label,
                            action: () => helpers.setDefaultValue(field, choice.value),
                            active: current === choice.value,
                            ...choiceIconProps(helpers, field, choice.value),
                        }))
                },
            })
        }
    }
    return commands
}

// Cap on how many sessions "Go to Session…" exposes. The list is already
// prioritized (extra → cross-filter pinned → cross-filter active → natural),
// so the cap keeps big session sets from filling the palette but always keeps
// the sticky groups visible.
const SESSION_NAV_LIMIT = 100

// Priority used to aggregate process states across a set of projects: the
// highest-priority active state among their sessions wins.
const PROCESS_STATE_PRIORITY = { starting: 3, assistant_turn: 2, user_turn: 1 }

/**
 * Build a per-project activity index in a single pass over all sessions.
 *
 * Returns a Map keyed by `project_id` whose values fold two things, mirroring
 * the sidebar's "what needs attention first" rules:
 *   - the highest-priority active process state among the project's own
 *     sessions (starting → assistant_turn → user_turn; `dead` ignored), and
 *   - whether any of them reads as unread (shared `isSessionUnread` predicate:
 *     content added after last view, excluding drafts/archived/subagents/
 *     hidden, and not while the agent is working).
 *
 * Subagents, drafts and archived sessions are skipped. The map holds only a
 * project's OWN sessions; callers that need a project + its worktrees (or a
 * whole workspace) fold several entries together with `aggregateActivity`.
 * One pass keeps the pickers cheap even with many projects.
 */
function buildProjectActivityMap(data) {
    const map = new Map()
    const processStates = data.processStates
    for (const s of Object.values(data.sessions)) {
        if (s.parent_session_id || s.draft || s.archived) continue
        let entry = map.get(s.project_id)
        if (!entry) {
            entry = { state: null, priority: -1, hasUnread: false }
            map.set(s.project_id, entry)
        }
        const ps = processStates[s.id]
        if (ps && ps.state && ps.state !== 'dead') {
            const priority = PROCESS_STATE_PRIORITY[ps.state] ?? -1
            if (priority > entry.priority) {
                entry.state = ps.state
                entry.priority = priority
            }
        }
        if (!entry.hasUnread && isSessionUnread(s, ps)) entry.hasUnread = true
    }
    return map
}

/**
 * Fold the per-project entries of `buildProjectActivityMap` over a set of
 * project ids into a single `{ processState, hasUnread }` summary — used for a
 * workspace (its visible members + their worktrees, via `getVisibleProjectIds`)
 * or a project + its worktrees. The highest-priority state wins; unread is the
 * logical OR.
 */
function aggregateActivity(activityMap, projectIds) {
    let state = null
    let priority = -1
    let hasUnread = false
    for (const id of projectIds || []) {
        const entry = activityMap.get(id)
        if (!entry) continue
        if (entry.priority > priority) {
            state = entry.state
            priority = entry.priority
        }
        if (entry.hasUnread) hasUnread = true
    }
    return { processState: state ? { state } : null, hasUnread }
}

/**
 * Aggregate a single project's activity together with its git worktrees one
 * level down — the indicator every project picker shows, matching the badges
 * elsewhere in the UI. For a worktree row (no sub-worktrees) this folds just
 * its own entry.
 */
function aggregateProjectActivity(activityMap, data, projectId) {
    const ids = [projectId]
    for (const wt of data.getWorktreesOf(projectId)) ids.push(wt.id)
    return aggregateActivity(activityMap, ids)
}

/**
 * Build the "Go to Session…" sub-picker items so they mirror SessionList's
 * sidebar order and groups:
 *   1. The deep-linked "extra" session when the selected session is out of
 *      every filter (prepended at the very top of the sidebar).
 *   2. Cross-filter pinned sessions (pin mode `workspace`/`all`).
 *   3. Cross-filter active sessions (running process or unread) when the
 *      "Always show active sessions" setting is on.
 *   4. Natural scope sessions for the current filter (project / workspace /
 *      all projects).
 *
 * The grouping logic itself is shared with SessionList through
 * `computeSidebarSessionBlocks` — this function only handles the mapping
 * from blocks to palette items (labels, icons, actions) and the navigation
 * rules that keep the sidebar where it was after opening a cross-filter
 * session.
 */
function buildSessionNavItems({
    data, workspaces, settings, route, router,
    isAllProjectsMode, routeSessionId, routeProjectId,
}) {
    const currentSessionId = routeSessionId()
    const currentProjectId = routeProjectId()
    const allProjects = isAllProjectsMode()
    const activeWorkspaceId = route.query.workspace || null

    // Synthesize an `effectiveProjectId` matching the one ProjectView.vue
    // derives for SessionList, so the shared helper reads from the same
    // natural scope.
    const effectiveProjectId = (() => {
        if (!allProjects) return currentProjectId
        if (activeWorkspaceId) return toWorkspaceProjectId(activeWorkspaceId)
        return ALL_PROJECTS_ID
    })()

    const { extra, crossFilterPinned, crossFilterActive, natural } = computeSidebarSessionBlocks({
        data,
        workspaces,
        effectiveProjectId,
        activeWorkspaceId,
        sessionId: currentSessionId,
        showArchived: settings.isShowArchivedSessions,
        showArchivedProjects: settings.isShowArchivedProjects,
        showActiveAcrossFilters: settings.isShowActiveAcrossFilters,
    })

    // Mirror ProjectView.handleSessionSelect via the shared helper: keep the
    // current frame (prefix mode + current project filter + workspace) and change
    // only the session id.
    const navigate = (s) => router.push(sessionRouteLocation(s, route))

    // Each session item carries the visual metadata the palette uses to
    // mirror a sidebar row: project color dot + pin icon on the left, process
    // state indicator / unread flag on the right. `group` drives the
    // inter-group divider rendered by CommandPalette in nested mode.
    const toItem = (s, group) => {
        const project = data.projects[s.project_id]
        // When the session lives in a git worktree, the row mirrors WorktreeBadge:
        // a code-branch marker before the title and the dot color falling back to
        // the parent repo's when the worktree has none of its own.
        const isWorktree = !!project?.worktree_of
        const parentColor = isWorktree ? (data.projects[project.worktree_of]?.color ?? null) : null
        const processState = data.processStates[s.id] || null
        // Canonical predicate so the palette's unread dot matches the sidebar
        // eye — same archived/hidden/subagent guards, same user_turn refinement.
        const hasUnread = isSessionUnread(s, processState)
        return {
            id: s.id,
            label: s.title || s.id,
            action: () => navigate(s),
            group,
            session: {
                projectId: s.project_id,
                projectColor: project?.color ?? parentColor ?? null,
                // Effective icon resolved via the project inheritance chain.
                projectIconUrl: resolveProjectIconUrl(s.project_id, data.projects),
                isWorktree,
                pinned: s.pinned || null,
                processState,
                hasUnread,
            },
        }
    }

    const ordered = [
        ...(extra ? [toItem(extra, 'extra')] : []),
        ...crossFilterPinned.map(s => toItem(s, 'pinned')),
        ...crossFilterActive.map(s => toItem(s, 'active')),
        ...natural.filter(s => !s.draft).map(s => toItem(s, 'natural')),
    ]
    return ordered.slice(0, SESSION_NAV_LIMIT)
}

/**
 * Register all static commands.
 * Called once during app setup in App.vue.
 * @param {import('vue-router').Router} router
 */
export function initStaticCommands(router) {
    const { registerCommands } = useCommandRegistry()
    const settings = useSettingsStore()
    const data = useDataStore()
    const workspaces = useWorkspacesStore()
    const terminalTabsStore = useTerminalTabsStore()
    const workflowRunsStore = useWorkflowRunsStore()
    const peers = usePeersStore()
    const help = useHelpStore()
    const peerSystemConfigured = usePeerSystemConfigured()
    const route = useRoute()

    // ── Helpers ────────────────────────────────────────────────────────────

    /** Whether the current route is in "all projects" mode */
    function isAllProjectsMode() {
        return route.name?.startsWith('projects-')
    }

    /** Session ID from the current route (if any) */
    function routeSessionId() {
        return route.params.sessionId || null
    }

    /** Project ID from the current route (if any) */
    function routeProjectId() {
        return route.params.projectId || null
    }

    /** Help pages available in this environment, alphabetical (as Settings → Help lists them). */
    function availableHelpPages() {
        return help.getAvailableHelp({
            platform: settings._isTouchDevice ? 'mobile' : 'desktop',
            os: settings.os,
            enabledProviders: settings.enabledProviders,
        }).sort((a, b) => a.title.localeCompare(b.title))
    }

    /** Terminal indices of the currently visible terminal panel (empty if none). */
    function terminalIndices() {
        const ctx = terminalTabsStore.activeContextKey
        return ctx ? (terminalTabsStore.indices[ctx] || []) : []
    }
    /** Workflows of the route's session (empty if none). */
    function workflowRunList() {
        return workflowRunsStore.getRuns(routeSessionId())
    }

    const PROJECT_DETAIL_ROUTES = new Set([
        'project', 'project-files', 'project-git', 'project-terminal',
        'projects-all', 'projects-files', 'projects-git', 'projects-terminal',
    ])

    /** Whether the current route is on a project detail panel (not a session) */
    function isOnProjectDetail() {
        return PROJECT_DETAIL_ROUTES.has(route.name)
    }

    /** Projects + worktrees for palette pickers, mixed and sorted by mtime desc
     *  (getProjects is the raw list, worktrees included). Archived entries are
     *  dropped unless the "show archived projects" setting is enabled. */
    function pickerEntries() {
        return data.getProjects.filter(p => settings.isShowArchivedProjects || !p.archived)
    }

    /** Action-time verifier for the "New Session in New Worktree" command. The
     *  command is offered for every non-worktree project regardless of the
     *  cached git_root (which may be a stale null for a repo git-init'd after
     *  the project was created). On activation we re-resolve git_root live: if
     *  the directory is a git repo, mirror the value into the store (so the
     *  WorktreeDialog composes its default path from a fresh git_root, not a
     *  stale null) and open the dialog; otherwise tell the user there is none. */
    async function openWorktreeForProject(projectId) {
        try {
            const res = await apiFetch(`/api/projects/${projectId}/resolve-git/`, { method: 'POST' })
            if (!res.ok) {
                toast.error('Could not check this folder for a git repository.')
                return
            }
            const { git_root } = await res.json()
            if (!git_root) {
                toast.info('No git repository found in this folder.')
                return
            }
            data.updateProject({ id: projectId, git_root })
            window.dispatchEvent(new CustomEvent('twicc:open-worktree-dialog', { detail: { projectId } }))
        } catch {
            toast.error('Could not check this folder for a git repository.')
        }
    }

    /** Map a project to a palette sub-item carrying the colored dot metadata
     *  (mirrors the session list dot) plus the absolute directory path, shown
     *  under the name and included in the palette's fuzzy search. The optional
     *  `activity` (`{ processState, hasUnread }`, from `aggregateProjectActivity`)
     *  rides on `project` so the palette renders the same right-aligned
     *  indicator as session/workspace rows. */
    function toProjectItem(p, action, activity = null) {
        return {
            id: p.id,
            label: data.getProjectDisplayName(p.id),
            path: p.directory ?? null,
            action,
            project: {
                color: p.color ?? null,
                icon_url: resolveProjectIconUrl(p.id, data.projects),
                untrusted: data.untrustedProjectIds.has(p.id),
                processState: activity?.processState ?? null,
                hasUnread: activity?.hasUnread ?? false,
            },
        }
    }

    /** Map a git worktree to a palette sub-item. Mirrors WorktreeBadge: the dot
     *  uses the worktree's own color falling back to the parent's; the label is
     *  the worktree's own name or its final directory segment; `worktree.parentName`
     *  is rendered as a prefix (parent name + code-branch icon) and is searchable
     *  alongside the label and the path. The optional `activity` rides on
     *  `project` (same indicator as a plain project row). */
    function toWorktreeItem(wt, action, activity = null) {
        const parent = wt.worktree_of ? data.getProject(wt.worktree_of) : null
        return {
            id: wt.id,
            label: worktreeLabel(wt) || data.getProjectDisplayName(wt.id),
            path: wt.directory ?? null,
            action,
            project: {
                color: wt.color ?? parent?.color ?? null,
                icon_url: resolveProjectIconUrl(wt.id, data.projects),
                untrusted: data.untrustedProjectIds.has(wt.id),
                processState: activity?.processState ?? null,
                hasUnread: activity?.hasUnread ?? false,
            },
            worktree: { parentName: parent ? data.getProjectDisplayName(parent.id) : '' },
        }
    }

    /** Dispatch a picker entry to the right item mapper based on whether it is a
     *  worktree. The action closure and optional aggregated `activity` are
     *  supplied by each command. */
    function toPickerItem(p, action, activity = null) {
        return p.worktree_of ? toWorktreeItem(p, action, activity) : toProjectItem(p, action, activity)
    }

    // ── Display mode labels ───────────────────────────────────────────────

    const DISPLAY_MODE_LABELS = {
        [DISPLAY_MODE.CONVERSATION]: 'Conversation',
        [DISPLAY_MODE.SIMPLIFIED]: 'Simplified',
        [DISPLAY_MODE.NORMAL]: 'Detailed',
        [DISPLAY_MODE.DEBUG]: 'Debug',
    }

    // ── Color scheme labels ────────────────────────────────────────────────

    const COLOR_SCHEME_LABELS = {
        [COLOR_SCHEME.SYSTEM]: 'System',
        [COLOR_SCHEME.LIGHT]: 'Light',
        [COLOR_SCHEME.DARK]: 'Dark',
    }

    // ── Commands ──────────────────────────────────────────────────────────

    registerCommands([

        // ── Navigation ────────────────────────────────────────────────

        {
            id: 'nav.home',
            label: 'Go to Home',
            icon: 'house',
            category: 'navigation',
            action: () => router.push({ name: 'home' }),
        },
        {
            id: 'nav.project',
            label: 'Go to Project\u2026',
            icon: 'folder',
            category: 'navigation',
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return pickerEntries().map(p => toPickerItem(p,
                    () => router.push({ name: 'project', params: { projectId: p.id } }),
                    aggregateProjectActivity(activityMap, data, p.id),
                ))
            },
        },
        {
            id: 'nav.session',
            label: 'Go to Session\u2026',
            icon: 'message',
            category: 'navigation',
            items: () => buildSessionNavItems({
                data, workspaces, settings, route, router,
                isAllProjectsMode, routeSessionId, routeProjectId,
            }),
        },
        {
            id: 'nav.all-projects',
            label: 'Go to All Projects',
            icon: 'layer-group',
            category: 'navigation',
            action: () => router.push({ name: 'projects-all' }),
        },
        {
            id: 'nav.workspace',
            label: 'Go to Workspace\u2026',
            icon: 'layer-group',
            category: 'navigation',
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return workspaces.getSelectableWorkspaces.map(ws => {
                    const { processState, hasUnread } = aggregateActivity(activityMap, workspaces.getVisibleProjectIds(ws.id))
                    return {
                        id: ws.id,
                        label: ws.name,
                        action: () => router.push({ name: 'projects-all', query: { workspace: ws.id } }),
                        workspace: {
                            color: ws.color || null,
                            processState,
                            hasUnread,
                        },
                    }
                })
            },
        },
        {
            id: 'nav.artifacts-list',
            label: 'Go to Artifacts List',
            icon: ARTIFACT_ICON,
            category: 'navigation',
            // Switch the sidebar to its Artifacts (bookmarks) mode, keeping the
            // current scope. Hidden when already there. Mirrors ProjectView's
            // switchToArtifacts; falls back to all-projects when no project is
            // in the route (home / settings).
            when: () => route.name !== 'project-artifacts' && route.name !== 'projects-artifacts',
            action: () => {
                // Same restore-the-last-URL behaviour as the sidebar view switch.
                if (lastArtifactsLocation.value) { router.push(lastArtifactsLocation.value); return }
                const workspaceQuery = route.query.workspace ? { workspace: route.query.workspace } : {}
                const projectId = routeProjectId()
                if (isAllProjectsMode() || !projectId) {
                    router.push({ name: 'projects-artifacts', query: workspaceQuery })
                } else {
                    router.push({ name: 'project-artifacts', params: { projectId } })
                }
            },
        },
        {
            id: 'nav.sessions-list',
            label: 'Go to Sessions List',
            icon: 'comments',
            category: 'navigation',
            // The inverse: back to the Sessions sidebar, keeping the current
            // scope. Only shown while in Artifacts mode. Mirrors
            // ProjectView's switchToSessions.
            when: () => route.name === 'project-artifacts' || route.name === 'projects-artifacts',
            action: () => {
                // Same restore-the-last-URL behaviour as the sidebar view switch.
                if (lastSessionsLocation.value) { router.push(lastSessionsLocation.value); return }
                const workspaceQuery = route.query.workspace ? { workspace: route.query.workspace } : {}
                const projectId = routeProjectId()
                if (isAllProjectsMode() || !projectId) {
                    router.push({ name: 'projects-all', query: workspaceQuery })
                } else {
                    router.push({ name: 'project', params: { projectId } })
                }
            },
        },
        {
            id: 'nav.search',
            label: 'Search Sessions\u2026',
            icon: 'magnifying-glass',
            category: 'navigation',
            action: () => window.dispatchEvent(new CustomEvent('twicc:open-search')),
        },
        {
            id: 'nav.find-in-session',
            label: 'Search in Session\u2026',
            icon: 'magnifying-glass',
            category: 'navigation',
            when: () => route.name === 'session' || route.name === 'projects-session',
            action: () => window.dispatchEvent(new Event('twicc:toggle-session-search')),
        },
        {
            id: 'nav.tab.chat',
            label: 'Switch to Chat Tab',
            icon: 'comment',
            category: 'navigation',
            when: () => !!routeSessionId(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session' : 'session'
                router.push({
                    name,
                    params: clearTabRouteParams('files', {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.files',
            label: 'Switch to Files Tab',
            icon: 'file-code',
            category: 'navigation',
            when: () => !!routeSessionId(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-files' : 'session-files'
                router.push({
                    name,
                    params: clearTabRouteParams('git', {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.git',
            label: 'Switch to Git Tab',
            icon: 'code-branch',
            category: 'navigation',
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId) return false
                const session = data.getSession(sessionId)
                if (!session) return false
                // Show when session has git info or the project has a git root
                return (!!session.git_directory && !!session.git_branch)
                    || !!data.getProject(session.project_id)?.git_root
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-git' : 'session-git'
                router.push({
                    name,
                    params: clearTabRouteParams('terminal', {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.terminal',
            label: 'Switch to Terminal Tab',
            icon: 'terminal',
            category: 'navigation',
            when: () => !!routeSessionId(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-terminal' : 'session-terminal'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.artifacts',
            label: 'Switch to Artifacts Tab',
            icon: 'images',
            category: 'navigation',
            // Only sessions that have artifacts on disk expose the tab
            // (mirrors SessionView's `hasArtifacts`).
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId) return false
                return !!data.getSession(sessionId)?.has_artifacts
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-artifacts' : 'session-artifacts'
                router.push({
                    name,
                    params: clearTabRouteParams('artifacts', {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.orchestration',
            label: 'Switch to Orchestration Tab',
            icon: 'sitemap',
            category: 'navigation',
            // Only sessions that belong to a spawned-session orchestration tree
            // expose the tab (mirrors SessionView's `hasSpawnRoot`).
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId) return false
                return !!data.getSession(sessionId)?.spawn_root
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-orchestration' : 'session-orchestration'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.plan',
            label: 'Switch to Plan Tab',
            icon: 'list-check',
            category: 'navigation',
            // Only sessions with at least one tracked plan-like document
            // expose the tab (mirrors SessionView's `hasPlan`).
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId) return false
                return (data.getSession(sessionId)?.plan_paths?.length ?? 0) > 0
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-plan' : 'session-plan'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.tasks',
            label: 'Switch to Tasks Tab',
            icon: 'square-check',
            category: 'navigation',
            // Only sessions that carry a task/todo/plan snapshot expose the tab
            // (mirrors SessionView's `hasTasks`).
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId) return false
                return !!data.getSessionTasks(sessionId)
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-tasks' : 'session-tasks'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.workflows',
            label: 'Switch to Workflows Tab',
            icon: 'sitemap',
            category: 'navigation',
            // Only sessions with at least one workflow run expose the tab
            // (mirrors SessionView's `hasWorkflows`).
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId) return false
                return !!data.getSession(sessionId)?.has_workflows
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-workflows' : 'session-workflows'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.tab.browser',
            label: 'Switch to Browser Tab',
            icon: 'globe',
            category: 'navigation',
            when: () => !!routeSessionId(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-session-browser' : 'session-browser'
                router.push({
                    name,
                    params: {
                        projectId: route.params.projectId,
                        sessionId: route.params.sessionId,
                    },
                    query: route.query,
                })
            },
        },

        // Terminal / workflow tab navigation — palette twins of the Alt+Ctrl+Shift
        // shortcuts: gated to the matching route, hidden with < 2 tabs (no point
        // navigating a single one), reusing the same CustomEvents the shortcuts
        // dispatch (the visible panel handles them). The pickers mark the current tab.
        {
            id: 'nav.terminal.prev',
            label: 'Go to Previous Terminal',
            icon: 'terminal',
            category: 'navigation',
            when: () => TERMINAL_ROUTES.has(route.name) && terminalIndices().length >= 2,
            action: () => window.dispatchEvent(new CustomEvent('twicc:terminal-tab-shortcut', { detail: { type: 'prev' } })),
        },
        {
            id: 'nav.terminal.next',
            label: 'Go to Next Terminal',
            icon: 'terminal',
            category: 'navigation',
            when: () => TERMINAL_ROUTES.has(route.name) && terminalIndices().length >= 2,
            action: () => window.dispatchEvent(new CustomEvent('twicc:terminal-tab-shortcut', { detail: { type: 'next' } })),
        },
        {
            id: 'nav.terminal.pick',
            label: 'Go to Terminal…',
            icon: 'terminal',
            category: 'navigation',
            when: () => TERMINAL_ROUTES.has(route.name) && terminalIndices().length >= 2,
            items: () => {
                const ctx = terminalTabsStore.activeContextKey
                const activeIdx = ctx ? terminalTabsStore.active[ctx] : null
                return terminalIndices().map((termIndex, pos) => ({
                    id: String(termIndex),
                    label: terminalTabsStore.getLabel(ctx, termIndex) || (termIndex === 0 ? 'Main' : `Term ${termIndex + 1}`),
                    active: termIndex === activeIdx,
                    action: () => window.dispatchEvent(new CustomEvent('twicc:terminal-tab-shortcut', { detail: { type: 'direct', index: pos + 1 } })),
                }))
            },
        },
        {
            id: 'nav.workflow.prev',
            label: 'Go to Previous Workflow',
            icon: 'sitemap',
            category: 'navigation',
            when: () => WORKFLOW_ROUTES.has(route.name) && workflowRunList().length >= 2,
            action: () => window.dispatchEvent(new CustomEvent('twicc:workflow-tab-shortcut', { detail: { type: 'prev' } })),
        },
        {
            id: 'nav.workflow.next',
            label: 'Go to Next Workflow',
            icon: 'sitemap',
            category: 'navigation',
            when: () => WORKFLOW_ROUTES.has(route.name) && workflowRunList().length >= 2,
            action: () => window.dispatchEvent(new CustomEvent('twicc:workflow-tab-shortcut', { detail: { type: 'next' } })),
        },
        {
            id: 'nav.workflow.pick',
            label: 'Go to Workflow…',
            icon: 'sitemap',
            category: 'navigation',
            when: () => WORKFLOW_ROUTES.has(route.name) && workflowRunList().length >= 2,
            items: () => {
                const activeId = workflowRunsStore.getActive(routeSessionId())
                return workflowRunList().map((run, pos) => ({
                    id: run.run_id,
                    label: run.name || run.run_id,
                    active: run.run_id === activeId,
                    action: () => window.dispatchEvent(new CustomEvent('twicc:workflow-tab-shortcut', { detail: { type: 'direct', index: pos + 1 } })),
                }))
            },
        },

        // Project detail panel tabs
        {
            id: 'nav.project-tab.stats',
            label: 'Switch to Stats Tab',
            icon: 'chart-line',
            category: 'navigation',
            when: () => isOnProjectDetail(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-all' : 'project'
                router.push({
                    name,
                    params: clearTabRouteParams('files', isAllProjectsMode() ? {} : { projectId: route.params.projectId }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.project-tab.files',
            label: 'Switch to Files Tab (Project)',
            icon: 'file-code',
            category: 'navigation',
            when: () => isOnProjectDetail(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-files' : 'project-files'
                router.push({
                    name,
                    params: clearTabRouteParams('git', isAllProjectsMode() ? {} : { projectId: route.params.projectId }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.project-tab.git',
            label: 'Switch to Git Tab (Project)',
            icon: 'code-branch',
            category: 'navigation',
            when: () => {
                if (!isOnProjectDetail()) return false
                // Git tab only available in single-project mode with a git root
                const projectId = routeProjectId()
                if (!projectId) return false
                return !!data.getProject(projectId)?.git_root
            },
            action: () => {
                const name = isAllProjectsMode() ? 'projects-git' : 'project-git'
                router.push({
                    name,
                    params: clearTabRouteParams('terminal', isAllProjectsMode() ? {} : { projectId: route.params.projectId }),
                    query: route.query,
                })
            },
        },
        {
            id: 'nav.project-tab.terminal',
            label: 'Switch to Terminal Tab (Project)',
            icon: 'terminal',
            category: 'navigation',
            when: () => isOnProjectDetail(),
            action: () => {
                const name = isAllProjectsMode() ? 'projects-terminal' : 'project-terminal'
                router.push({
                    name,
                    params: isAllProjectsMode() ? {} : { projectId: route.params.projectId },
                    query: route.query,
                })
            },
        },

        // ── Creation ──────────────────────────────────────────────────

        {
            id: 'create.session',
            label: 'New Session in Current Project',
            icon: 'plus',
            category: 'creation',
            when: () => !!routeProjectId(),
            // Render the current project as a project/worktree badge (colored dot +
            // name, or parent + code-branch + folder) with its directory as
            // helptext — the same display as the "New Session in…" picker rows.
            // The palette reads `target()` in root & search modes; `label` above
            // stays plain text so fuzzy search still finds the command.
            target: () => {
                const id = routeProjectId()
                const p = id ? data.getProject(id) : null
                if (!p) return null
                if (p.worktree_of) {
                    const parent = data.getProject(p.worktree_of)
                    return {
                        prefix: 'New Session in',
                        label: worktreeLabel(p) || data.getProjectDisplayName(p.id),
                        path: p.directory ?? null,
                        project: { color: p.color ?? parent?.color ?? null, icon_url: resolveProjectIconUrl(p.id, data.projects), untrusted: data.untrustedProjectIds.has(p.id) },
                        worktree: { parentName: parent ? data.getProjectDisplayName(parent.id) : '' },
                    }
                }
                return {
                    prefix: 'New Session in',
                    label: data.getProjectDisplayName(p.id),
                    path: p.directory ?? null,
                    project: { color: p.color ?? null, icon_url: resolveProjectIconUrl(p.id, data.projects), untrusted: data.untrustedProjectIds.has(p.id) },
                }
            },
            action: async () => {
                const projectId = routeProjectId()
                const gate = await ensureProjectTrust(projectId)
                if (!gate) return
                const sessionId = data.createDraftSession(projectId, gate.state)
                const name = isAllProjectsMode() ? 'projects-session' : 'session'
                router.push({ name, params: { projectId, sessionId } })
            },
        },
        {
            id: 'create.session-in',
            label: 'New Session in\u2026',
            icon: 'square-plus',
            category: 'creation',
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return pickerEntries().map(p => toPickerItem(p, async () => {
                const gate = await ensureProjectTrust(p.id)
                if (!gate) return
                const sessionId = data.createDraftSession(p.id, gate.state)
                // Preserve the current sidebar filter: the draft lives in
                // project p.id (data), but we keep the URL's projectId on
                // the current filter so the sidebar does not switch. The
                // SessionView derives the draft's real project from
                // session.project_id. In all-projects mode there is no
                // single-project filter to preserve, and on pages without
                // a project segment in the URL (home, settings) there is
                // no filter either — in both cases fall back to the
                // draft's project for URL canonicity.
                // `query: route.query` carries the current ?workspace=…
                // along with any other query params. The router guard
                // would normally drop workspace when navigating to a
                // project outside it, but we set workspace explicitly so
                // the guard short-circuits and our value wins.
                const filterProjectId = route.params.projectId
                if (isAllProjectsMode() || !filterProjectId) {
                    const name = isAllProjectsMode() ? 'projects-session' : 'session'
                    router.push({ name, params: { projectId: p.id, sessionId }, query: route.query })
                } else {
                    router.push({ name: 'session', params: { projectId: filterProjectId, sessionId }, query: route.query })
                }
            }, aggregateProjectActivity(activityMap, data, p.id)))
            },
        },
        {
            id: 'create.session-in-new-worktree',
            label: 'New Session in New Worktree…',
            icon: 'code-branch',
            category: 'creation',
            // Shown for every non-worktree project (a worktree can't have
            // worktrees). git is verified at activation, NOT here: the cached
            // git_root may be a stale null for a repo git-init'd after the
            // project was created, so gating the entry point on it would hide it
            // exactly when it's needed. openWorktreeForProject re-checks live and
            // either opens the WorktreeDialog (App.vue) or says there's no repo.
            when: () => pickerEntries().some(p => !p.worktree_of),
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return pickerEntries()
                    .filter(p => !p.worktree_of)
                    .map(p => toProjectItem(p,
                        () => openWorktreeForProject(p.id),
                        aggregateProjectActivity(activityMap, data, p.id),
                    ))
            },
        },
        {
            id: 'create.project',
            label: 'New Project',
            icon: 'folder-plus',
            category: 'creation',
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-new-project-dialog'))
            },
        },
        {
            id: 'create.workspace',
            label: 'New Workspace',
            icon: 'layer-group',
            category: 'creation',
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-new-workspace-dialog'))
            },
        },

        // ── Display ───────────────────────────────────────────────────

        {
            id: 'display.color-scheme',
            label: 'Change Color Scheme\u2026',
            icon: 'circle-half-stroke',
            category: 'display',
            items: () => [
                { id: COLOR_SCHEME.SYSTEM, label: COLOR_SCHEME_LABELS[COLOR_SCHEME.SYSTEM], action: () => settings.setColorScheme(COLOR_SCHEME.SYSTEM), active: settings.colorScheme === COLOR_SCHEME.SYSTEM },
                { id: COLOR_SCHEME.LIGHT, label: COLOR_SCHEME_LABELS[COLOR_SCHEME.LIGHT], action: () => settings.setColorScheme(COLOR_SCHEME.LIGHT), active: settings.colorScheme === COLOR_SCHEME.LIGHT },
                { id: COLOR_SCHEME.DARK, label: COLOR_SCHEME_LABELS[COLOR_SCHEME.DARK], action: () => settings.setColorScheme(COLOR_SCHEME.DARK), active: settings.colorScheme === COLOR_SCHEME.DARK },
            ],
        },
        {
            id: 'display.wa-theme',
            label: 'Change Theme…',
            icon: 'palette',
            category: 'display',
            items: () => Object.values(WA_THEME).map(value => ({
                id: value,
                label: WA_THEME_LABELS[value],
                action: () => settings.setWaTheme(value),
                active: settings.waTheme === value,
            })),
        },
        {
            id: 'display.wa-brand',
            label: 'Change Brand Color…',
            icon: 'droplet',
            category: 'display',
            items: () => Object.values(WA_BRAND).map(value => ({
                id: value,
                label: WA_BRAND_LABELS[value],
                action: () => settings.setWaBrand(value),
                active: settings.waBrand === value,
            })),
        },
        {
            id: 'display.mode',
            label: 'Change Display Mode\u2026',
            icon: 'eye',
            category: 'display',
            items: () => [
                { id: DISPLAY_MODE.CONVERSATION, label: DISPLAY_MODE_LABELS[DISPLAY_MODE.CONVERSATION], action: () => settings.setDisplayMode(DISPLAY_MODE.CONVERSATION), active: settings.displayMode === DISPLAY_MODE.CONVERSATION },
                { id: DISPLAY_MODE.SIMPLIFIED, label: DISPLAY_MODE_LABELS[DISPLAY_MODE.SIMPLIFIED], action: () => settings.setDisplayMode(DISPLAY_MODE.SIMPLIFIED), active: settings.displayMode === DISPLAY_MODE.SIMPLIFIED },
                { id: DISPLAY_MODE.NORMAL, label: DISPLAY_MODE_LABELS[DISPLAY_MODE.NORMAL], action: () => settings.setDisplayMode(DISPLAY_MODE.NORMAL), active: settings.displayMode === DISPLAY_MODE.NORMAL },
                { id: DISPLAY_MODE.DEBUG, label: DISPLAY_MODE_LABELS[DISPLAY_MODE.DEBUG], action: () => settings.setDisplayMode(DISPLAY_MODE.DEBUG), active: settings.displayMode === DISPLAY_MODE.DEBUG },
            ],
        },
        {
            id: 'display.toggle-costs',
            label: 'Toggle Show Costs',
            icon: 'coins',
            category: 'display',
            toggled: () => settings.areCostsShown,
            action: () => settings.setShowCosts(!settings.showCosts),
        },
        {
            id: 'display.toggle-compact',
            label: 'Toggle Compact Session List',
            icon: 'bars',
            category: 'display',
            toggled: () => settings.isCompactSessionList,
            action: () => settings.setCompactSessionList(!settings.compactSessionList),
        },
        {
            id: 'display.toggle-show-archived-sessions',
            label: 'Toggle Show Archived Sessions',
            icon: 'box-archive',
            category: 'display',
            toggled: () => settings.isShowArchivedSessions,
            action: () => settings.setShowArchivedSessions(!settings.showArchivedSessions),
        },
        {
            id: 'display.toggle-show-archived-projects',
            label: 'Toggle Show Archived Projects',
            icon: 'box-archive',
            category: 'display',
            toggled: () => settings.isShowArchivedProjects,
            action: () => settings.setShowArchivedProjects(!settings.showArchivedProjects),
        },
        {
            id: 'display.toggle-show-archived-workspaces',
            label: 'Toggle Show Archived Workspaces',
            icon: 'box-archive',
            category: 'display',
            toggled: () => settings.isShowArchivedWorkspaces,
            action: () => settings.setShowArchivedWorkspaces(!settings.showArchivedWorkspaces),
        },
        {
            id: 'display.toggle-active-across-filters',
            label: 'Toggle Show Active Sessions Across Projects',
            icon: 'signal',
            category: 'display',
            toggled: () => settings.isShowActiveAcrossFilters,
            action: () => settings.setShowActiveAcrossFilters(!settings.showActiveAcrossFilters),
        },
        {
            id: 'display.toggle-user-turn-toast',
            label: 'Toggle "Finished Working" Toast',
            icon: 'bell',
            category: 'display',
            // One channel among four (Settings → Notifications → Agent finished
            // working), not a master switch: the sound, the browser notification
            // and the push are left untouched.
            toggled: () => settings.isNotifUserTurnToast,
            action: () => settings.setNotifUserTurnToast(!settings.notifUserTurnToast),
        },
        {
            id: 'display.font-increase',
            label: 'Increase Font Size',
            icon: 'magnifying-glass-plus',
            category: 'display',
            action: () => settings.setFontSize(Math.min(settings.fontSize + 1, 32)),
        },
        {
            id: 'display.font-decrease',
            label: 'Decrease Font Size',
            icon: 'magnifying-glass-minus',
            category: 'display',
            action: () => settings.setFontSize(Math.max(settings.fontSize - 1, 12)),
        },
        {
            id: 'display.toggle-editor-word-wrap',
            label: 'Toggle Editor Word Wrap',
            icon: 'text-width',
            category: 'display',
            toggled: () => settings.isEditorWordWrap,
            action: () => settings.setEditorWordWrap(!settings.editorWordWrap),
        },
        {
            id: 'display.toggle-editor-diff-layout',
            label: 'Toggle Editor Side-by-Side Diff',
            icon: 'columns',
            category: 'display',
            toggled: () => settings.isDiffSideBySide,
            action: () => settings.setDiffSideBySide(!settings.diffSideBySide),
        },
        {
            id: 'display.toggle-show-diffs',
            label: 'Toggle Auto Open Live Edit Diffs',
            icon: 'code-compare',
            category: 'display',
            toggled: () => settings.isShowDiffs,
            action: () => settings.setShowDiffs(!settings.showDiffs),
        },
        {
            id: 'display.toggle-session-diff-word-wrap',
            label: 'Toggle Session Diff Word Wrap',
            icon: 'text-width',
            category: 'display',
            toggled: () => settings.isToolDiffWordWrap,
            action: () => settings.setToolDiffWordWrap(!settings.toolDiffWordWrap),
        },
        {
            id: 'display.toggle-session-diff-layout',
            label: 'Toggle Session Side-by-Side Diff',
            icon: 'columns',
            category: 'display',
            toggled: () => settings.isToolDiffSideBySide,
            action: () => settings.setToolDiffSideBySide(!settings.toolDiffSideBySide),
        },

        // ── Terminal ──────────────────────────────────────────────────

        {
            id: 'terminal.toggle-mac-option-is-meta',
            label: 'Toggle Terminal Option Key (⌥) as Meta',
            icon: 'keyboard',
            category: 'terminal',
            when: () => settings.isMac,
            toggled: () => settings.isTerminalMacOptionIsMeta,
            action: () => settings.setTerminalMacOptionIsMeta(!settings.terminalMacOptionIsMeta),
        },

        // ── Provider defaults (one section per registered provider) ───

        ...buildProviderDefaultsCommands(settings),

        // ── UI ────────────────────────────────────────────────────────

        {
            id: 'ui.default-provider',
            label: 'Change Default Provider…',
            icon: 'plug',
            category: 'ui',
            when: () => settings.enabledProviders.length > 1,
            items: () => getProviderOptions()
                .filter(({ value }) => settings.enabledProviders.includes(value))
                .map(({ value, label }) => ({
                    id: value,
                    label,
                    action: () => settings.setDefaultProvider(value),
                    active: settings.defaultProvider === value,
                })),
        },
        {
            id: 'ui.manage-workspaces',
            label: 'Manage Workspaces',
            icon: 'layer-group',
            category: 'ui',
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-manage-workspaces-dialog'))
            },
        },
        {
            id: 'ui.share-session',
            label: 'Share Session…',
            icon: 'share-nodes',
            category: 'ui',
            // Current session only, and only when a share host is configured (a
            // draft has no transcript to share). Opens the globally-mounted
            // ShareDialog in ProjectView via the shared window event.
            when: () => {
                const sessionId = routeSessionId()
                if (!sessionId || !settings.getUsableShareBaseUrl) return false
                const s = data.getSession(sessionId)
                return !!s && !s.draft
            },
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-share-dialog', { detail: { sessionId: routeSessionId() } }))
            },
        },
        {
            id: 'ui.manage-shares',
            label: 'Manage Shared Links',
            icon: 'share-nodes',
            category: 'ui',
            // Only when a share host is configured (no host → no links to manage).
            // Opens the globally-mounted ShareManagerDialog (App.vue) via event.
            when: () => !!settings.getUsableShareBaseUrl,
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-share-manager'))
            },
        },
        {
            id: 'ui.peer-inbox',
            label: 'Open Peer Inbox',
            icon: 'envelope',
            category: 'ui',
            // The three peer commands share the condition that decides whether
            // the peer surfaces exist at all (sidebar button, Settings → Peers
            // actions): an address, or peers/messages that outlived one. With
            // nothing at all, Peers is still a setup form and they lead nowhere.
            when: () => peerSystemConfigured.value,
            action: () => window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox')),
        },
        {
            id: 'ui.peer-compose',
            label: 'Write to a Peer…',
            icon: 'paper-plane',
            category: 'ui',
            // Also needs someone to write to — the compose dialog's peer select
            // only lists active relationships (same gate as the inbox footer).
            when: () => peerSystemConfigured.value && peers.peers.some(p => p.state === 'active'),
            action: () => window.dispatchEvent(new CustomEvent('twicc:open-peer-compose')),
        },
        {
            id: 'ui.manage-peers',
            label: 'Manage Peers',
            icon: 'user-group',
            category: 'ui',
            when: () => peerSystemConfigured.value,
            action: () => window.dispatchEvent(new CustomEvent('twicc:open-peers-manager')),
        },
        {
            id: 'ui.help',
            label: 'Open Help Page…',
            icon: 'circle-question',
            category: 'ui',
            // Same list as Settings → Help: every page whose constraints match
            // this environment, alphabetical. Manual opens never show the
            // "Don't show this again" switch — they are re-readings.
            when: () => availableHelpPages().length > 0,
            items: () => availableHelpPages().map(page => ({
                id: page.key,
                label: page.title,
                action: () => showHelp(page.key, { showDontShowAgain: false }),
            })),
        },
        {
            id: 'ui.edit-project',
            label: 'Edit Current Project',
            icon: 'pencil',
            category: 'ui',
            when: () => !!routeProjectId(),
            // Opens the globally-mounted ProjectEditDialog (App.vue) for the
            // route's project — works for a worktree project too.
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-edit-project-dialog', { detail: { projectId: routeProjectId() } }))
            },
        },
        {
            id: 'ui.archive-project',
            label: 'Archive Current Project',
            icon: 'box-archive',
            category: 'ui',
            when: () => {
                const p = data.getProject(routeProjectId())
                return !!p && !p.archived
            },
            action: () => data.setProjectArchived(routeProjectId(), true),
        },
        {
            id: 'ui.unarchive-project',
            label: 'Unarchive Current Project',
            icon: 'box-open',
            category: 'ui',
            when: () => {
                const p = data.getProject(routeProjectId())
                return !!p && !!p.archived
            },
            action: () => data.setProjectArchived(routeProjectId(), false),
        },
        {
            id: 'ui.edit-workspace',
            label: 'Edit Current Workspace',
            icon: 'pencil',
            category: 'ui',
            when: () => !!route.query.workspace,
            action: () => {
                window.dispatchEvent(new CustomEvent('twicc:open-edit-workspace-dialog', { detail: { workspaceId: route.query.workspace } }))
            },
        },
        {
            id: 'ui.archive-workspace',
            label: 'Archive Current Workspace',
            icon: 'box-archive',
            category: 'ui',
            when: () => {
                const ws = route.query.workspace ? workspaces.getWorkspaceById(route.query.workspace) : null
                return !!ws && !ws.archived
            },
            action: () => workspaces.updateWorkspace(route.query.workspace, { archived: true }),
        },
        {
            id: 'ui.unarchive-workspace',
            label: 'Unarchive Current Workspace',
            icon: 'box-open',
            category: 'ui',
            when: () => {
                const ws = route.query.workspace ? workspaces.getWorkspaceById(route.query.workspace) : null
                return !!ws && !!ws.archived
            },
            action: () => workspaces.updateWorkspace(route.query.workspace, { archived: false }),
        },
        {
            id: 'ui.edit-project-pick',
            label: 'Edit Project…',
            icon: 'pen-to-square',
            category: 'ui',
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return pickerEntries().map(p => toPickerItem(p,
                    () => window.dispatchEvent(new CustomEvent('twicc:open-edit-project-dialog', { detail: { projectId: p.id } })),
                    aggregateProjectActivity(activityMap, data, p.id),
                ))
            },
        },
        {
            id: 'ui.archive-project-pick',
            label: 'Archive Project…',
            icon: 'box-archive',
            category: 'ui',
            when: () => data.getProjects.some(p => !p.archived),
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return data.getProjects.filter(p => !p.archived).map(p => toPickerItem(p,
                    () => data.setProjectArchived(p.id, true),
                    aggregateProjectActivity(activityMap, data, p.id),
                ))
            },
        },
        {
            id: 'ui.unarchive-project-pick',
            label: 'Unarchive Project…',
            icon: 'box-open',
            category: 'ui',
            // Lists every archived project regardless of the "show archived
            // projects" display toggle, so an archived project is always
            // reachable to restore.
            when: () => data.getProjects.some(p => p.archived),
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return data.getProjects.filter(p => p.archived).map(p => toPickerItem(p,
                    () => data.setProjectArchived(p.id, false),
                    aggregateProjectActivity(activityMap, data, p.id),
                ))
            },
        },
        {
            id: 'ui.edit-workspace-pick',
            label: 'Edit Workspace…',
            icon: 'pen-to-square',
            category: 'ui',
            when: () => workspaces.getSelectableWorkspaces.length > 0,
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return workspaces.getSelectableWorkspaces.map(ws => {
                    const { processState, hasUnread } = aggregateActivity(activityMap, workspaces.getVisibleProjectIds(ws.id))
                    return {
                        id: ws.id,
                        label: ws.name,
                        action: () => window.dispatchEvent(new CustomEvent('twicc:open-edit-workspace-dialog', { detail: { workspaceId: ws.id } })),
                        workspace: {
                            color: ws.color || null,
                            processState,
                            hasUnread,
                        },
                    }
                })
            },
        },
        {
            id: 'ui.archive-workspace-pick',
            label: 'Archive Workspace…',
            icon: 'box-archive',
            category: 'ui',
            when: () => workspaces.workspaces.some(ws => !ws.archived),
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return workspaces.workspaces.filter(ws => !ws.archived).map(ws => {
                    const { processState, hasUnread } = aggregateActivity(activityMap, workspaces.getVisibleProjectIds(ws.id))
                    return {
                        id: ws.id,
                        label: ws.name,
                        action: () => workspaces.updateWorkspace(ws.id, { archived: true }),
                        workspace: { color: ws.color || null, processState, hasUnread },
                    }
                })
            },
        },
        {
            id: 'ui.unarchive-workspace-pick',
            label: 'Unarchive Workspace…',
            icon: 'box-open',
            category: 'ui',
            // Lists every archived workspace regardless of the "show archived
            // workspaces" display toggle.
            when: () => workspaces.workspaces.some(ws => ws.archived),
            items: () => {
                const activityMap = buildProjectActivityMap(data)
                return workspaces.workspaces.filter(ws => ws.archived).map(ws => {
                    const { processState, hasUnread } = aggregateActivity(activityMap, workspaces.getVisibleProjectIds(ws.id))
                    return {
                        id: ws.id,
                        label: ws.name,
                        action: () => workspaces.updateWorkspace(ws.id, { archived: false }),
                        workspace: { color: ws.color || null, processState, hasUnread },
                    }
                })
            },
        },
        {
            id: 'ui.settings',
            label: 'Open Settings',
            icon: 'gear',
            category: 'ui',
            action: () => {
                document.querySelector('#settings-trigger')?.click()
            },
        },
    ])
}
