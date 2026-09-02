<script setup>
import { computed, watch, ref, reactive, readonly, provide, inject, onActivated, onDeactivated, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useWorkspacesStore } from '../stores/workspaces'
import { useSettingsStore } from '../stores/settings'
import { useHelpStore } from '../stores/help'
import { getProviderHelpers } from '../providers'
import { useCommandRegistry } from '../composables/useCommandRegistry'
import { requestTitleSuggestion, notifySessionViewed, forceNotifySessionViewed, markSessionReadState, cancelSessionViewedThrottle } from '../composables/useWebSocket'
import { stopSessionProcess, hardKillSessionProcess } from '../composables/useStopSessionProcess'
import { useDragHover } from '../composables/useDragHover'
import { useSessionLayout } from '../composables/useSessionLayout'
import { PROCESS_STATE } from '../constants'
import SessionHeader from '../components/session/detail/SessionHeader.vue'
import SessionItemsList from '../components/session/detail/SessionItemsList.vue'
import SessionContent from '../components/session/detail/SessionContent.vue'
import FilesPanel from '../components/files/FilesPanel.vue'
import GitPanel from '../components/git/GitPanel.vue'
import TerminalPanel from '../components/terminal/TerminalPanel.vue'
import OrchestrationPanel from '../components/orchestration/OrchestrationPanel.vue'
import PlanPane from '../components/plan/PlanPane.vue'
import TaskPane from '../components/tasks/TaskPane.vue'
import WorkflowsPane from '../components/workflows/WorkflowsPane.vue'
import BrowserPane from '../components/browser/BrowserPane.vue'
import SessionLayout from '../components/session/layout/SessionLayout.vue'
import SessionTabLink from '../components/session/layout/SessionTabLink.vue'
import TabPlacementMenu from '../components/session/layout/TabPlacementMenu.vue'
import LayoutMenu from '../components/session/layout/LayoutMenu.vue'
import { DOCK_LABELS, DOCK_ICONS, PLACEMENT_OPTIONS } from '../components/session/layout/dockMeta'
import LayoutSaveDialog from '../components/session/layout/LayoutSaveDialog.vue'
import LayoutManagerDialog from '../components/session/layout/LayoutManagerDialog.vue'
import { useLayoutsStore, SINGLE_PANE_ID, SINGLE_PANE_NAME } from '../stores/layouts'
import { ancestorChain } from '../utils/projectAgentDefaults'
import { AGENT_SETTING_ICONS } from '../utils/agentSettingIcons'
import { resolveProjectLayoutId } from '../utils/layoutDefaults'
import AppTooltip from '../components/ui/AppTooltip.vue'
import TabBar from '../components/ui/TabBar.vue'
import ProcessIndicator from '../components/ui/ProcessIndicator.vue'
import CodeCommentsIndicator from '../components/ui/CodeCommentsIndicator.vue'
import { useCodeCommentsStore } from '../stores/codeComments'
import { useFramePoolStore } from '../stores/framePool'
import {
    buildFilesRouteParams,
    buildGitRouteParams,
    buildPlanRouteParams,
    clearTabRouteParams,
    buildSessionBaseRouteName,
    buildSubagentRouteName,
    buildTabRouteName,
    buildTerminalRouteParams,
    decodePath,
    parseRouteString,
    parseRouteTermIndex,
} from '../utils/granularRoutes'
import { getAgentDisplayLabel } from '../utils/agentLabel'
import { focusChatPrimary, gotoChatFooterPanel, runOnChatTab } from '../utils/focusChat'
import { toggleSessionMute } from '../composables/useSessionMute'
import { fileRootsFromStore } from '../utils/projectRoots'
import { normalizePosixPath } from '../utils/worktreePath'
import { computeSessionArtifactBookmarks } from '../utils/sessionArtifactBookmarks'

const route = useRoute()
const router = useRouter()
const store = useDataStore()
const workspacesStore = useWorkspacesStore()
const layoutsStore = useLayoutsStore()
const settingsStore = useSettingsStore()
const helpStore = useHelpStore()
const codeCommentsStore = useCodeCommentsStore()
const { registerCommands, unregisterCommands } = useCommandRegistry()

// Reference to session header for opening rename dialog
const sessionHeaderRef = ref(null)

// Reference to session items list for scroll compensation
const sessionItemsListRef = ref(null)

// Reference to FilesPanel for cross-tab file reveal
const filesPanelRef = ref(null)

// Reference to the Artifacts tab's FilesPanel (fixed root = session artifacts dir)
const artifactsPanelRef = ref(null)

// Reference to the Plan tab's pane (read-only plan markdown)
const planPaneRef = ref(null)

// Reference to the Workflows tab's pane — refetched on WS reconnect (a
// workflow_changed broadcast may have been dropped during the outage).
const workflowsPaneRef = ref(null)

const gitPanelRef = ref(null)
const terminalPanelRef = ref(null)

// ═══════════════════════════════════════════════════════════════════════════
// KeepAlive lifecycle: active state, listener setup/teardown
// ═══════════════════════════════════════════════════════════════════════════

const isActive = ref(true)

onMounted(() => {
    // Mark session as viewed on first render
    notifySessionViewed(sessionId.value, 'mounted')
    // Listen for tab keyboard shortcuts (dispatched by App.vue)
    window.addEventListener('twicc:tab-shortcut', handleTabShortcut)
    // Listen for layout keyboard shortcuts (maximize / minimize / restore the focused pane)
    window.addEventListener('twicc:layout-shortcut', handleLayoutShortcut)
    // Listen for live artifact file changes (dispatched by useWebSocket)
    window.addEventListener('twicc:artifact-files-changed', handleArtifactFilesChanged)
    // Listen for live plan content changes (dispatched by useWebSocket)
    window.addEventListener('twicc:plan-changed', handlePlanChanged)
    // Listen for WebSocket reconnections to refresh open tool panes whose live
    // updates (artifact_files_changed / plan_changed) may have been missed.
    window.addEventListener('twicc:ws-reconnected', handleWsReconnected)
})

onBeforeUnmount(() => {
    window.removeEventListener('twicc:tab-shortcut', handleTabShortcut)
    window.removeEventListener('twicc:layout-shortcut', handleLayoutShortcut)
    window.removeEventListener('twicc:artifact-files-changed', handleArtifactFilesChanged)
    window.removeEventListener('twicc:plan-changed', handlePlanChanged)
    window.removeEventListener('twicc:ws-reconnected', handleWsReconnected)
    cancelPaneFocus()
})

/**
 * The ArtifactsWatcher relayed changed file(s) under some session. Forward them
 * to this session's active Artifacts panel. The panel decides whether the
 * source is its physical root or the owner of its selected bookmark.
 */
function handleArtifactFilesChanged(e) {
    if (!e.detail?.sessionId || !isActive.value) return
    artifactsPanelRef.value?.onArtifactFilesChanged(e.detail.sessionId, e.detail.paths || [])
}

/**
 * The plans watcher relayed a plan content change for some session. Reload this
 * session's Plan pane when it's the matching, active session view (mirrors
 * handleArtifactFilesChanged). Tab presence (has_plan) is handled separately via
 * the session payload; this only refreshes the rendered markdown.
 */
function handlePlanChanged(e) {
    if (e.detail?.sessionId !== sessionId.value || !isActive.value) return
    planPaneRef.value?.reload()
}

/**
 * The WebSocket reconnected: live tool-pane updates (artifact_files_changed /
 * plan_changed) emitted during the outage are not replayed, so refresh the open
 * Artifacts and Plan panes now. Presence flags (has_artifacts / has_plan) are
 * already reconciled by the forced session reload in useReconciliation. Only the
 * active session view acts (cached background instances refresh when reactivated).
 */
function handleWsReconnected() {
    if (!isActive.value) return
    artifactsPanelRef.value?.reloadAll()
    planPaneRef.value?.reload()
    workflowsPaneRef.value?.reload()
}

onActivated(() => {
    isActive.value = true

    // Register contextual session commands in the command palette
    registerSessionCommands()

    // Mark session as viewed when re-activated (KeepAlive navigation back)
    notifySessionViewed(sessionId.value, 'activated')

    // Re-resolve if the session disappeared from the store while this cached
    // instance was inactive — typically a draft we created was rebound to a
    // canonical id and deleted while the user was on another session. Without
    // this re-check, the back navigation lands on the loading spinner forever
    // because the setup-time resolve only ran once with the draft still alive.
    if (!session.value) {
        ensureSessionResolved()
    }

    // Arriving on / returning to a session whose URL targets a filter-bearing tool tab focuses its
    // search input (deferred so the freshly (re)mounted panel sees the counter bump as a transition).
    nextTick(() => focusRouteToolTabOnArrival())
})

onDeactivated(() => {
    isActive.value = false

    // Drop any pending pane-focus rAF so it can't navigate this (now cached) session.
    cancelPaneFocus()

    // Force-send session_viewed to ensure last_viewed_at is fresh before leaving.
    // Without this, the throttle can cause last_viewed_at to be stale (set at navigation time)
    // while last_new_content_at was updated during viewing — making the session appear unread.
    forceNotifySessionViewed(sessionId.value, 'deactivated')

    // Unregister contextual session commands from the command palette
    unregisterCommands([...SESSION_COMMAND_IDS, ...LAYOUT_COMMAND_IDS])

    // Cancel any pending drag-hover timer
    chatTabDragHover.cancel()
})

provide('sessionActive', readonly(isActive))

// ─── Cross-tab file reveal (Git → Files / Artifacts) ─────────────────────────

/**
 * Reveal a file in the right tab. A path inside the session's artifacts dir
 * opens in the Artifacts tab; a markdown link (`preferPlanTab`) targeting a
 * tracked plan document (Session.plan_paths) opens in the Plan tab's rendered
 * view; everything else opens in the Files tab on the matching root. Provided
 * to descendant components (file links in tool uses, markdown, patch entries,
 * the Git diff "view in files" button — only markdown links opt into the Plan
 * redirect, the explicit Files-tab buttons keep their literal behavior).
 *
 * @param {string} absolutePath — the absolute filesystem path to reveal
 */
async function viewFileInFilesTab(absolutePath, { lineNum = null, preferPlanTab = false } = {}) {
    // Collapse any `.`/`..` segments up front: a caller may hand us a path with
    // literal traversal (e.g. a tool recorded `cwd + src/twicc/../../frontend/x`
    // without normalizing). Such a path is valid and resolves to a real file,
    // but the tab's tree reveal walks segments literally and has no `..` node,
    // so it would fail to locate it. Every branch below works off the clean path.
    absolutePath = normalizePosixPath(absolutePath)

    // Artifacts live outside the project file roots, in their own tab.
    // artifactsDir is only set when the session has artifacts (so the tab
    // exists), which naturally gates this branch.
    if (artifactsDir.value && absolutePath.startsWith(artifactsDir.value + '/')) {
        const relativePath = absolutePath.slice(artifactsDir.value.length + 1)
        navigateInTab('artifacts', buildFilesRouteParams({ rootKey: 'artifacts', filePath: relativePath }))
        await nextTick()
        await artifactsPanelRef.value?.revealFile(absolutePath, { lineNum })
        return
    }

    // A markdown link to a tracked plan document opens in the Plan tab
    // instead of Files. Line-targeted links keep going to Files — the plan
    // tab's rendered preview cannot scroll to a line.
    if (preferPlanTab && lineNum == null) {
        const planDocPath = trackedPlanDocPathFor(absolutePath)
        if (planDocPath) {
            onPlanNavigate({ docPath: planDocPath })
            return
        }
    }

    const project = store.getProject(session.value?.project_id)
    const roots = fileRootsFromStore(project, session.value, store)
    // Most-specific (deepest) matching root wins. When roots nest (a cwd inside
    // the git root), the ancestor root would otherwise capture every descendant
    // file and bury it under a longer relative path in the wrong tab — so the
    // "Working directory" root could never be the target of a nested file.
    let match = null
    for (const r of roots) {
        if (r.path && absolutePath.startsWith(r.path + '/') && (!match || r.path.length > match.path.length)) {
            match = r
        }
    }
    const rootKey = match?.key
    const relativePath = match ? absolutePath.slice(match.path.length + 1) : undefined

    navigateInTab('files', buildFilesRouteParams({ rootKey, filePath: relativePath }))
    await nextTick()
    await filesPanelRef.value?.revealFile(absolutePath, { lineNum })
}

provide('viewFileInFilesTab', viewFileInFilesTab)

/**
 * The stored plan_paths entry path matching an absolute file path, or null.
 * Relative entries resolve against the project directory and the worktree_of
 * parent's (same candidates as PlanPane); entries flagged missing on disk are
 * skipped so their links keep the Files-tab behavior.
 */
function trackedPlanDocPathFor(absolutePath) {
    const docs = session.value?.plan_paths
    if (!Array.isArray(docs) || docs.length === 0) return null
    const project = store.getProject(session.value?.project_id)
    const parent = project?.worktree_of ? store.getProject(project.worktree_of) : null
    const roots = [project?.directory, parent?.directory]
        .filter(Boolean)
        .map((root) => root.replace(/\/+$/, ''))
    for (const entry of docs) {
        if (!entry?.path || entry.exists === false) continue
        if (entry.path.startsWith('/')) {
            if (entry.path === absolutePath) return entry.path
        } else if (roots.some((root) => `${root}/${entry.path}` === absolutePath)) {
            return entry.path
        }
    }
    return null
}

function insertTextAtCursor(text, options) {
    sessionItemsListRef.value?.insertTextAtCursor(text, options)
}
provide('insertTextAtCursor', insertTextAtCursor)

// Current session from route params
// IMPORTANT: sessionId is captured at creation time (not a reactive computed
// from route.params) because with KeepAlive, the route changes globally when
// switching sessions. If it were reactive, ALL cached SessionView instances
// would see the NEW session's params, breaking deactivation hooks and item lookups.
// The KeepAlive key (route.params.sessionId) ensures each instance gets the correct
// value at creation time and keeps it permanently.
//
// filterProjectId is the project the sidebar filter is on for THIS session's route.
// It is used only by router.push calls that rebuild the current URL, so that
// switching tabs (main / subagent / files / git / terminal) never changes the
// sidebar filter — even when the session lives in a different project than the
// filter (cross-filter pinned/active sessions, artifact bookmarks).
// Unlike sessionId it must NOT be frozen: the KeepAlive cache is keyed by session
// id alone, so one instance is re-entered under whatever filter is current at the
// time (leave the session, switch project, reopen it from the new project's
// sidebar). A stale value made every intra-session navigation — tab click, tab
// close, tab href — rewrite the URL with the project of the FIRST visit, yanking
// the sidebar back to it. The watcher below keeps it on the live route.
//
// projectId (declared further down, after `session`) is the project the session
// belongs to, driven by `session.project_id`. It is used for API calls, code-
// comments lookups, and WS payloads.
const filterProjectId = ref(route.params.projectId)
const sessionId = ref(route.params.sessionId)
// Both params are watched, not projectId alone: returning to the session after a
// detour (project root, another session) usually leaves projectId untouched while
// only sessionId flips back to ours, so watching the project alone would miss the
// re-entry. The sessionId guard is what keeps a cached background instance from
// adopting another session's project (route.params is global under KeepAlive); it
// also covers the deactivated window, so no onActivated re-sync is needed.
watch([() => route.params.projectId, () => route.params.sessionId], ([routeProjectId, routeSessionId]) => {
    if (routeSessionId !== sessionId.value || !routeProjectId) return
    filterProjectId.value = routeProjectId
})
const subagentId = computed(() => route.params.subagentId)
// Workflow run to focus in the Workflows tab (from .../workflows/<runId>).
// Scoped to this instance's session — route.params is global under KeepAlive.
const workflowFocusRunId = computed(() =>
    route.params.sessionId === sessionId.value ? (route.params.runId || null) : null
)

// Detect "All Projects" mode from route name
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))
const filesRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const filesRouteFilePath = computed(() => {
    const decoded = decodePath(parseRouteString(route.params.filePath))
    return decoded === null ? null : decoded
})
// Artifacts tab reuses the files route shape (rootKey + filePath).
const artifactsRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const artifactsRouteFilePath = computed(() => {
    const decoded = decodePath(parseRouteString(route.params.filePath))
    return decoded === null ? null : decoded
})
const gitRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const gitRouteCommitRef = computed(() => parseRouteString(route.params.commitRef))
const gitRouteFilePath = computed(() => {
    const decoded = decodePath(parseRouteString(route.params.filePath))
    return decoded === null ? null : decoded
})
const terminalRouteTermIndex = computed(() => parseRouteTermIndex(route.params.termIndex))
// Scoped to this instance's session (like workflowFocusRunId): route.params is
// global under KeepAlive, and a cached instance must not read another
// session's doc path.
const planRouteDocPath = computed(() => {
    if (route.params.sessionId !== sessionId.value) return null
    const decoded = decodePath(parseRouteString(route.params.docPath))
    return decoded === null ? null : decoded
})

// Session data
const session = computed(() => store.getSession(sessionId.value))

// ─── Artifacts tab ───────────────────────────────────────────────────────────
// The tab remains present when the session owns artifacts OR when its project
// context can see at least one bookmark. A worktree inherits its main project's
// bookmarks; workspace and global scopes then expand visibility independently
// of the owning project's raw main/worktree identity.
const artifactMainProjectId = computed(() => {
    const projectId = session.value?.project_id
    return projectId ? store.getMainRepoProjectId(projectId) : null
})
const sessionArtifactBookmarks = computed(() => computeSessionArtifactBookmarks({
    bookmarks: store.artifactBookmarks,
    projectId: session.value?.project_id,
    mainProjectId: artifactMainProjectId.value,
    projectScopeIds: session.value?.project_id
        ? [...new Set([session.value.project_id, artifactMainProjectId.value].filter(Boolean))]
        : [],
    workspaces: workspacesStore.workspaces,
    workspaceContainsProject: workspacesStore.workspaceContainsProject,
}))
const hasArtifacts = computed(() =>
    !!session.value?.has_artifacts || sessionArtifactBookmarks.value.length > 0,
)
const artifactsDir = computed(() => session.value?.artifacts_dir || null)
const artifactsExternalRoots = computed(() =>
    artifactsDir.value ? [{ key: 'artifacts', label: 'Artifacts', path: artifactsDir.value }] : []
)

// `sessionLoadError` drives the "not found" / "error" fallback in the template:
// - `null`: still loading, loaded successfully, or redirecting via draft alias
// - `'not-found'`: backend returned 404 — the session ID does not exist
// - `'error'`: network or server error — the user can try again by reloading
const sessionLoadError = ref(null)

// Resolve the session when it is missing from the store. Two paths:
// - Draft rebound to a canonical id (Codex flow when the bind happened while
//   the user was on another session — ``bindDraftSession`` skipped its inline
//   ``router.replace`` because ``onDraft`` was false, but still populated
//   ``draftAliases`` and deleted the draft). Redirect transparently via
//   ``router.replace`` so the user lands on the real session instead of a
//   "not found" screen. Preserves the forward history (replaceState only
//   touches the current history entry).
// - Otherwise fetch by id. Covers cross-filter deep links (the URL's
//   projectId is the sidebar filter, not the session's real project) and
//   direct artifact bookmarks into a project whose sessions haven't been loaded yet.
//   ``loadSessionById`` is idempotent.
//
// Called from setup (initial render) and from ``onActivated`` (cached KeepAlive
// instance whose session disappeared while it was inactive).
async function ensureSessionResolved() {
    if (session.value) {
        sessionLoadError.value = null
        return
    }

    const canonicalId = store.localState.draftAliases[sessionId.value]
    if (canonicalId) {
        router.replace({
            name: route.name,
            params: { ...route.params, sessionId: canonicalId },
            query: route.query,
        })
        return
    }

    try {
        const result = await store.loadSessionById(sessionId.value)
        if (!result) sessionLoadError.value = 'not-found'
    } catch {
        sessionLoadError.value = 'error'
    }
}

ensureSessionResolved()

// Session's project (data-driven). Stable per KeepAlive instance because
// sessionId is frozen and session.project_id is immutable for a given session.
// Used for API calls, code-comments lookups, WS payloads, and template props
// that identify the session's project (not the sidebar filter).
const projectId = computed(() => session.value?.project_id)

// Whether the session is in a git repository:
// - session has resolved git info (git_directory + git_branch from tool_use), OR
// - the project itself is inside a git repo (git_root resolved from project directory)
const hasGitRepo = computed(() =>
    (!!session.value?.git_directory && !!session.value?.git_branch)
    || !!store.getProject(session.value?.project_id)?.git_root
)

// Whether the session belongs to a spawned-session orchestration tree.
// ``spawn_root`` is set as soon as a session spawns its first child (it points
// to itself) or is itself spawned by another session — i.e. exactly when there
// is a topology worth showing. Drives the Orchestration tab's visibility.
const hasSpawnRoot = computed(() => !!session.value?.spawn_root)

// Whether the session has a provider plan file on disk (Claude Code:
// <claude home>/plans/<slug>.md). Drives the read-only Plan tab's visibility.
// Unlike artifacts, this is NOT monotonic — it flips back to false (and the tab
// disappears) when the plan file is deleted, via the plan_gone WS message.
// Plan tab presence: any tracked plan-like document (Session.plan_paths —
// native Claude plan + pattern-detected docs, provider-agnostic). Entries are
// never removed (deletion only flips their `exists` flag), so unlike the old
// has_plan gating the tab never disappears once a doc was tracked.
const hasPlan = computed(() => (session.value?.plan_paths?.length ?? 0) > 0)

// Whether the session carries a task/todo/plan snapshot (Session.tasks, synced
// like every other field). Drives the read-only Tasks tab's visibility. Like
// Plan, this is NOT monotonic — a recompute that finds no task state flips it
// back to empty and the tab disappears. Read through the normalised store getter
// (``{}`` -> null) so presence is just truthiness.
const hasTasks = computed(() => !!store.getSessionTasks(sessionId.value))

// Whether the session has at least one Claude Code workflow run (a wf_*.json in
// its <session_id>/workflows/ folder). Drives the Workflows tab's visibility.
// Monotonic (one-way) on the backend: the flag never flips back to false once
// set, so unlike Plan the tab never disappears once it has appeared.
const hasWorkflows = computed(() => !!session.value?.has_workflows)

// Code comments counts per tab
const filesCommentsCount = computed(() =>
    codeCommentsStore.countBySource(projectId.value, sessionId.value, 'files')
)
const gitCommentsCount = computed(() =>
    codeCommentsStore.countBySource(projectId.value, sessionId.value, 'git')
)
const chatCommentsCount = computed(() =>
    codeCommentsStore.getCommentsBySession(projectId.value, sessionId.value)
        .filter(c => c.source === 'tool' && !c.subagentSessionId).length
)
function agentCommentsCount(agentSessionId) {
    return codeCommentsStore.getCommentsBySession(projectId.value, sessionId.value)
        .filter(c => c.subagentSessionId === agentSessionId).length
}

// Tabs state - computed from store (automatically updates when session changes)
// Format: [{ id: 'agent-xxx', agentId: 'xxx' }, ...]
const openSubagentTabs = computed(() => {
    const saved = store.getSessionOpenTabs(sessionId.value)
    if (!saved) return []

    return saved.tabs
        .filter(id => id !== 'main' && id.startsWith('agent-'))
        .map(id => ({
            id,
            agentId: id.replace('agent-', '')
        }))
})

// Active tab ID ('main' for session, 'agent-xxx' for subagents, 'files'/'git'/'terminal' for tool tabs)
// Computed from route
const activeTabId = computed(() => {
    // KeepAlive keeps one SessionView instance per visited session alive at once, and they all read
    // the SAME global `route`. Only the instance the URL actually targets may derive its active tab
    // from it; every cached instance falls back to the inert center ('main') so it never acts on
    // another session's route — e.g. the layout's route-driven "reveal the active tab" watcher would
    // otherwise un-minimize (and persist) the matching dock in every cached session. This mirrors the
    // frozen `sessionId` / `filterProjectId` discipline above. 'main' (not null) because the value
    // flows into isCenterTab(), which calls `.startsWith` and isn't null-safe; 'main' is the fixed
    // center tab, never docked, so the reveal watcher short-circuits on it.
    if (route.params.sessionId !== sessionId.value) return 'main'
    if (subagentId.value) {
        return `agent-${subagentId.value}`
    }
    const name = route.name
    if (name === 'session-files' || name === 'projects-session-files') return 'files'
    if (name === 'session-artifacts' || name === 'projects-session-artifacts') return 'artifacts'
    if (name === 'session-git' || name === 'projects-session-git') return 'git'
    if (name === 'session-terminal' || name === 'projects-session-terminal') return 'terminal'
    if (name === 'session-orchestration' || name === 'projects-session-orchestration') return 'orchestration'
    if (name === 'session-plan' || name === 'projects-session-plan') return 'plan'
    if (name === 'session-tasks' || name === 'projects-session-tasks') return 'tasks'
    if (name === 'session-workflows' || name === 'projects-session-workflows') return 'workflows'
    if (name === 'session-browser' || name === 'projects-session-browser') return 'browser'
    return 'main'
})

// The dockable tool tabs — single source for the tool-tab roster (id, label, FA icon) and its
// presence condition. `present` gates everything downstream: the resolver input, the ←/→ order,
// the keyboard shortcuts, the template, and the redirect guard. Only Files and Terminal are always
// present; Git/Artifacts/Orchestration are conditional (future tabs may be too). `redirectReady` is
// "enough data is loaded to safely redirect away from an absent tab's URL" — git needs the project
// row (its repo-ness depends on it), the others just the session row. Chat + subagents are
// center-only, so they're not in here.
const TOOL_TABS = [
    { id: 'files', label: 'Files', icon: 'folder', present: () => true },
    { id: 'git', label: 'Git', icon: 'code-branch', present: () => hasGitRepo.value, redirectReady: () => !!store.getProject(session.value?.project_id) },
    { id: 'terminal', label: 'Terminal', icon: 'terminal', present: () => true },
    { id: 'tasks', label: 'Tasks', icon: 'square-check', present: () => hasTasks.value, redirectReady: () => !!session.value },
    { id: 'plan', label: 'Plan', icon: 'list-check', present: () => hasPlan.value, redirectReady: () => !!session.value },
    { id: 'artifacts', label: 'Artifacts', icon: 'shapes', present: () => hasArtifacts.value, redirectReady: () => !!session.value && store.artifactBookmarksLoaded },
    { id: 'orchestration', label: 'Orchestration', icon: 'diagram-project', present: () => hasSpawnRoot.value, redirectReady: () => !!session.value },
    { id: 'workflows', label: 'Workflows', icon: 'sitemap', present: () => hasWorkflows.value, redirectReady: () => !!session.value },
    { id: 'browser', label: 'Browser', icon: 'globe', present: () => true },
]
function toolTabById(tabId) { return TOOL_TABS.find((t) => t.id === tabId) || null }
// Non-tool tabs (main, agent-*) are never gated → treated as present.
function isToolTabPresent(tabId) { const t = toolTabById(tabId); return t ? t.present() : true }
const presentToolTabs = computed(() => TOOL_TABS.filter((t) => t.present()))

// FA icon names for the tab nav (chat + the tool tabs), derived from the registry — one place.
const TAB_ICONS = { main: 'comments', ...Object.fromEntries(TOOL_TABS.map((t) => [t.id, t.icon])) }

// The active tab when it's a tool tab that is *definitively* absent for this session — i.e. not
// present AND we already have enough data to be sure (`redirectReady`); null otherwise. As a
// computed it tracks every reactive source it reads (the presence flags, the session row, the
// project row), so it flips to the tab the moment the gating data finishes loading. That is what
// makes a cold load / direct navigation redirect: a plain watch on the presence flags alone never
// re-fires, because they stay stably false while only the readiness data changes underneath.
const absentActiveToolTab = computed(() => {
    const tab = toolTabById(activeTabId.value)
    if (!tab || tab.present()) return null
    if (tab.redirectReady && !tab.redirectReady()) return null  // wait until we can trust "absent"
    return tab
})

// Redirect away from a tool-tab URL when that tab isn't available for this session — e.g. a direct
// navigation / bookmark / back-forward to /git on a non-git session, or /orchestration with no
// spawn tree. Driven by the registry (replaces three near-identical per-tab watchers). Guards: skip
// while deactivated (KeepAlive) and if the route belongs to another session.
watch(absentActiveToolTab, (tab) => {
    if (!tab) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    router.replace({
        name: buildSessionBaseRouteName(isAllProjectsMode.value),
        params: { projectId: filterProjectId.value, sessionId: sessionId.value },
        query: route.query,
    })
}, { immediate: true })

function toolTabRouteLocation(tab, params = {}) {
    return {
        name: buildTabRouteName({
            isAllProjectsMode: isAllProjectsMode.value,
            isSessionRoute: true,
            tab,
        }),
        params: clearTabRouteParams(tab, {
            projectId: filterProjectId.value,
            sessionId: sessionId.value,
            ...params,
        }),
        query: route.query,
    }
}

function navigateInTab(tab, params = {}, method = 'push') {
    router[method](toolTabRouteLocation(tab, params))
}

// While docking is active several tool panels are visible at once, but only the focused tab
// (activeTabId) owns the URL. This drives the panels' `route-owner` prop: a non-owner panel
// receives blanked route props (its params belong to whoever owns the URL) and must NOT
// sync-from-route — otherwise it reads the blanks as "nothing selected" and clears its open
// file / commit / terminal tab (the docked-panel-clears-at-blur bug). Focus itself is claimed by
// interaction (requestPaneFocus) or by the panel's own navigation events — not gated here.
function ownsRoute(tabId) {
    return !layout.dockingRendered.value || activeTabId.value === tabId
}

// Each user navigation from a panel cancels a pending pane-focus claim from the same gesture
// (the navigation already focuses the panel, precisely) — see requestPaneFocus.
function onFilesNavigate({ rootKey, filePath, replace }) {
    cancelPaneFocus()
    const params = buildFilesRouteParams({ rootKey, filePath })
    rememberToolTabRoute('files', params)
    navigateInTab('files', params, replace ? 'replace' : 'push')
}

function onArtifactsNavigate({ rootKey, filePath, replace }) {
    cancelPaneFocus()
    const params = buildFilesRouteParams({ rootKey, filePath })
    rememberToolTabRoute('artifacts', params)
    navigateInTab('artifacts', params, replace ? 'replace' : 'push')
}

function onGitNavigate({ rootKey, commitRef, filePath, replace }) {
    cancelPaneFocus()
    const params = buildGitRouteParams({ rootKey, commitRef, filePath })
    rememberToolTabRoute('git', params)
    navigateInTab('git', params, replace ? 'replace' : 'push')
}

function onPlanNavigate({ docPath, replace }) {
    cancelPaneFocus()
    const params = buildPlanRouteParams({ docPath })
    rememberToolTabRoute('plan', params)
    navigateInTab('plan', params, replace ? 'replace' : 'push')
}

// No route-ownership gate here: the reactive re-grab that used to loop (applyRouteTermIndex →
// replaceToTerm when visible-but-not-owner) is now stopped at the source by the panel's
// `route-owner` prop, so the only navigate events reaching this handler are user/command-driven
// — and those SHOULD navigate (and focus the terminal), e.g. clicking a term tab while unfocused.
function onTerminalNavigate({ termIndex, replace }) {
    cancelPaneFocus()
    const params = buildTerminalRouteParams({ termIndex })
    rememberToolTabRoute('terminal', params)
    navigateInTab('terminal', params, replace ? 'replace' : 'push')
}

// The Workflows pane reflects its active run in the URL. Unlike the other panes this
// navigation can be programmatic (a run appearing live auto-activates its tab), so it
// is scoped to the session that currently owns the route: a KeepAlive-cached instance
// whose workflow just produced a new run must not yank the URL onto itself.
function onWorkflowsNavigate({ runId }) {
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    cancelPaneFocus()
    navigateInTab('workflows', { runId }, 'replace')
}

const TOOL_TAB_IDS = ['files', 'artifacts', 'git', 'terminal', 'orchestration', 'plan', 'tasks', 'workflows', 'browser']

// Keep the last granular URL visited for each tool tab so switching away and back
// restores the previous state instead of resetting the panel to its base route.
const rememberedToolTabRoutes = reactive({
    files: null,
    artifacts: null,
    git: null,
    terminal: null,
    // Orchestration, Tasks, Workflows and Browser have no granular sub-route;
    // kept here so the generic tool-tab navigation in switchToTab treats them
    // uniformly.
    orchestration: null,
    plan: null,
    tasks: null,
    workflows: null,
    browser: null,
})

function getCurrentToolTabRouteParams(tabId) {
    if (tabId === 'files') {
        return buildFilesRouteParams({
            rootKey: filesRouteRootKey.value,
            filePath: filesRouteFilePath.value,
        })
    }

    if (tabId === 'artifacts') {
        return buildFilesRouteParams({
            rootKey: artifactsRouteRootKey.value,
            filePath: artifactsRouteFilePath.value,
        })
    }

    if (tabId === 'git') {
        return buildGitRouteParams({
            rootKey: gitRouteRootKey.value,
            commitRef: gitRouteCommitRef.value,
            filePath: gitRouteFilePath.value,
        })
    }

    if (tabId === 'terminal') {
        return buildTerminalRouteParams({
            termIndex: terminalRouteTermIndex.value,
        })
    }

    if (tabId === 'plan') {
        return buildPlanRouteParams({
            docPath: planRouteDocPath.value,
        })
    }

    return null
}

function rememberToolTabRoute(tabId, params = getCurrentToolTabRouteParams(tabId)) {
    if (!TOOL_TAB_IDS.includes(tabId)) return
    rememberedToolTabRoutes[tabId] = params ?? {}
}

// One canonical route location per visible session tab. Both SPA navigation
// and native hrefs use this function so links retain granular tool state
// (selected file, commit, terminal, plan document, etc.).
function sessionTabRouteLocation(panel) {
    if (panel === 'main') {
        return {
            name: buildSessionBaseRouteName(isAllProjectsMode.value),
            params: {
                projectId: filterProjectId.value,
                sessionId: sessionId.value,
            },
            query: route.query,
        }
    }

    if (panel.startsWith('agent-')) {
        return {
            name: buildSubagentRouteName(isAllProjectsMode.value),
            params: {
                projectId: filterProjectId.value,
                sessionId: sessionId.value,
                subagentId: panel.slice('agent-'.length),
            },
            query: route.query,
        }
    }

    if (TOOL_TAB_IDS.includes(panel)) {
        return toolTabRouteLocation(panel, rememberedToolTabRoutes[panel] ?? {})
    }

    return null
}

function sessionTabHref(panel) {
    // Clicking the route-owning tab is a no-op today, so its native link must
    // be the exact current URL. This also preserves route details that are not
    // part of tab memory, such as a focused workflow run.
    if (panel === activeTabId.value && route.params.sessionId === sessionId.value) {
        return route.fullPath
    }
    const location = sessionTabRouteLocation(panel)
    return location ? router.resolve(location).href : route.fullPath
}

watch(
    [
        isActive,
        activeTabId,
        filesRouteRootKey,
        filesRouteFilePath,
        artifactsRouteRootKey,
        artifactsRouteFilePath,
        gitRouteRootKey,
        gitRouteCommitRef,
        gitRouteFilePath,
        terminalRouteTermIndex,
        planRouteDocPath,
    ],
    ([active, tabId]) => {
        if (!active) return
        if (route.params.sessionId !== sessionId.value) return
        if (!TOOL_TAB_IDS.includes(tabId)) return
        rememberToolTabRoute(tabId)
    },
    { immediate: true }
)

/**
 * Navigate to a specific tab by panel name.
 * Used both by the wa-tab-group event handler and compact-mode tab buttons.
 * @param {string} panel - The panel name (e.g., 'main', 'agent-xxx', 'files', 'git', 'terminal')
 */
function switchToTab(panel) {
    // Ignore if already on this tab (avoid infinite loop)
    if (panel === activeTabId.value) return
    const location = sessionTabRouteLocation(panel)
    if (location) return router.push(location)
}

// ═══════════════════════════════════════════════════════════════════════════
// Dockable layout (opt-in). Until a tool tab is docked, the plain tab group below
// behaves exactly as before; once docked, the resolver-driven SessionLayout kicks in.
// ═══════════════════════════════════════════════════════════════════════════

// Root element of the docking area, measured by the composable.
const sessionLayoutRef = ref(null)

// The center tab group (chat + tool/subagent tabs). Reffed so we can carry the "double-click to
// maximize/restore" native title on its nav strip only — see syncCenterBarTitle below.
const sessionTabsRef = ref(null)

// Resolver input: chat is the fixed center anchor; the present tool tabs come from the TOOL_TABS
// registry above (subagents are center-only and not dockable, so they're excluded here).
const layoutTabs = computed(() => [
    { id: 'main', label: 'Chat', icon: TAB_ICONS.main, fixedCenter: true },
    ...presentToolTabs.value.map((t) => ({ id: t.id, label: t.label, icon: t.icon })),
])

const layout = useSessionLayout({
    sessionId,
    containerRef: () => sessionLayoutRef.value?.$el,
    tabs: layoutTabs,
    routeActiveTabId: activeTabId,
})

const LAYOUT_TOOL_IDS = TOOL_TABS.map((t) => t.id)

// ── Persistent frames: keep pooled iframes (Browser pane, artifact/plan HTML
// previews) aligned with their placeholders across layout changes that move a
// pane WITHOUT resizing it (dock re-assignment, overlay open/close, maximize) —
// ResizeObserver is blind to position-only moves, so bump the geometry epoch
// once the DOM settles and let each frame re-measure.
const framePool = useFramePoolStore()
watch(
    [() => layout.render.value, () => layout.openOverlayEdge.value, () => layout.maximizedRegion.value],
    () => nextTick(() => framePool.bumpGeometry()),
    { flush: 'post' }
)
// A tool pane whose frame is currently shown inside the docking overlay must
// raise its frame above the overlay panel (its own z-index can't — DockRegion
// isolates the stacking context). FilePane is rendered by the Files and
// Artifacts panels AND by PlanPane in render-only mode.
const browserFrameElevated = computed(() => layout.targetKeyForTab('browser') === 'overlay')
const filesFrameElevated = computed(() => layout.targetKeyForTab('files') === 'overlay')
const artifactsFrameElevated = computed(() => layout.targetKeyForTab('artifacts') === 'overlay')
const planFrameElevated = computed(() => layout.targetKeyForTab('plan') === 'overlay')

// Mobile fallback: below mobileMaxW the resolver folds everything into a plain tab strip and the
// whole docking system is skipped (no docks possible). Hide the per-tab placement arrows there —
// they'd offer to place a tab into a dock that can't exist. Gated on `measured` so the arrows
// don't flash out before the area is first measured on a normal-width screen.
const layoutTabsMode = computed(() => layout.measured.value && layout.render.value.mode === 'tabs')

// A tool tab is shown in the center strip unless it's currently docked.
function showInCenter(tabId) {
    return !layout.dockingRendered.value || layout.dockOf(tabId) === 'center'
}
const orderedCenterToolTabs = computed(() =>
    layout.orderedTabs.value.filter((tab) => !tab.fixedCenter && showInCenter(tab.id))
)
function toolTabCommentsCount(tabId) {
    if (tabId === 'files') return filesCommentsCount.value
    if (tabId === 'git') return gitCommentsCount.value
    return null
}
function isCenterTab(tabId) {
    if (tabId === 'main' || tabId.startsWith('agent-')) return true
    return showInCenter(tabId)
}
// The center strip's active tab: the routed tab when it lives in the center, otherwise the last
// center tab (so focusing a docked tab doesn't blank the center). The remembered tab can itself
// have just left the center: when you dock the very tab the center was showing, its route stays
// (it's now active in the dock) but it's no longer a center tab, and `lastCenterTab` still points
// at it. Re-validate the fallback and drop back to Chat ('main', always center-only) so the center
// re-selects a visible tab — without touching the route.
const lastCenterTab = ref('main')
watch(activeTabId, (id) => { if (id && isCenterTab(id)) lastCenterTab.value = id }, { immediate: true })

// First time the user opens this session's Artifacts tab, surface the
// artifacts help (with the dismiss switch). The tab only exists once the
// session has artifacts, so activeTabId only becomes 'artifacts' when it's
// reachable; `immediate` also covers landing directly on the artifacts URL.
// maybeAutoShow no-ops once seen, if disabled, or if another help is open.
watch(activeTabId, (id) => {
    if (id !== 'artifacts') return
    helpStore.maybeAutoShow('what-are-artifacts', {
        platform: settingsStore._isTouchDevice ? 'mobile' : 'desktop',
        os: settingsStore.os,
        enabledProviders: settingsStore.enabledProviders,
    })
}, { immediate: true })

// Same nudge for the Browser tab: first visit surfaces its help page (what
// the tab can do, the companion script, embedding limits).
watch(activeTabId, (id) => {
    if (id !== 'browser') return
    helpStore.maybeAutoShow('browser-tab', {
        platform: settingsStore._isTouchDevice ? 'mobile' : 'desktop',
        os: settingsStore.os,
        enabledProviders: settingsStore.enabledProviders,
    })
}, { immediate: true })

const centerActiveTab = computed(() => {
    if (isCenterTab(activeTabId.value)) return activeTabId.value
    return isCenterTab(lastCenterTab.value) ? lastCenterTab.value : 'main'
})

// Whether a tool panel is the visible one at its destination (drives its :active prop).
function isToolTabShown(tabId) {
    if (showInCenter(tabId)) return centerActiveTab.value === tabId
    return layout.isToolPanelVisible(tabId)
}

// Is the center the region that owns the route (its shown tab is the URL's tab)? True whenever the
// route owner is a center tab — including single-pane mode, where every tab is a center tab, so the
// center bar always reads as active (opacity 1). False only when a dock owns the route, which dims
// the center's tab bar to mark it as a non-active region.
const isCenterRouteActive = computed(() => isCenterTab(activeTabId.value))

// Deferred pane-focus claim. Interacting with a pane that doesn't own the route should focus it
// (claim the URL) — but a pane interaction that IS a navigation (opening a file, switching a
// terminal tab, selecting a commit) already focuses the pane precisely via its own navigate.
// The claim is requested from the CLICK (the end of the gesture) and resolved on the next frame,
// by which point any navigation the click produced — synchronously (file/commit select) or via a
// watcher microtask (terminal tab) — has already run and called cancelPaneFocus. So a navigating
// click wins (one clean navigation, no transient) and a plain click falls through to focus the
// pane's CURRENT state (switchToTab → the tab's remembered route, which mirrors what it shows).
// This replaces the old "navigate-to-remembered on pointerdown", a second navigation that raced
// with — and, fired before the click, preceded — the gesture's real action.
let paneFocusRaf = null
let paneFocusTab = null
let paneFocusRoute = null
function requestPaneFocus(tabId) {
    if (!tabId) return
    // Pure route claim — it does NOT focus the panel's content. Clicking inside a pane's body (or a
    // terminal, a file in the tree, …) must not steal focus to the content; only an explicit tab
    // ACTIVATION does (tab-header click / keyboard / arrival), handled by requestPanelFocus elsewhere.
    paneFocusTab = tabId
    if (paneFocusRaf != null) return
    // Snapshot the route at the gesture's start. The claim is the FALLBACK for a plain click; if the
    // same click also navigated (a file/artifact/subagent link or a tree/commit/terminal selection in
    // the chat or a pane), the route changes before this rAF resolves — drop the now-stale claim so it
    // can't bounce the URL back to the center tab. This enforces "a navigating click wins" at the
    // source instead of relying on every navigating handler to remember cancelPaneFocus (those calls
    // stay valid and just short-circuit earlier). A plain focus claim leaves the route untouched until
    // the rAF's own switchToTab, so it still goes through.
    paneFocusRoute = route.fullPath
    paneFocusRaf = requestAnimationFrame(() => {
        paneFocusRaf = null
        const tab = paneFocusTab
        const claimedAt = paneFocusRoute
        paneFocusTab = null
        paneFocusRoute = null
        if (tab && route.fullPath === claimedAt) switchToTab(tab)
    })
}
function cancelPaneFocus() {
    paneFocusTab = null
    paneFocusRoute = null
    if (paneFocusRaf != null) {
        cancelAnimationFrame(paneFocusRaf)
        paneFocusRaf = null
    }
}
// Explicit tab navigation from the layout (dock tab click, gutter swap/restore) — an action, so
// it supersedes any pending pane-focus claim and navigates immediately.
function onLayoutSelectTab(tabId) {
    cancelPaneFocus()
    switchToTab(tabId)
}

// A dock tab HEADER was clicked (distinct from a body/route claim) — an explicit activation, so focus
// its content, whether the tab was already the region's shown one or a background tab being brought up.
function onLayoutTabActivate(tabId) {
    if (ACTIVATION_FOCUS_TABS.includes(tabId)) requestPanelFocus(tabId)
}

// A pointer drag must outlive the surface it started from. Reveal the full layout as soon as the
// gesture activates; the SessionLayout owns the window listeners, so closing an overlay or restoring
// a maximized region cannot cancel the drag. The successful drop then focuses the moved tab.
function onLayoutTabDragStart() {
    cancelPaneFocus()
    if (layout.maximizedRegion.value) layout.restoreMaximized()
    if (layout.openOverlayEdge.value) onOverlayDismiss()
}
function onLayoutTabDrop(tabId, { activate = true } = {}) {
    if (!activate) return
    onLayoutSelectTab(tabId)
    onLayoutTabActivate(tabId)
}

// ─── Tool-panel activation focus ──────────────────────────────────────────────
// The tool tabs whose primary content we focus when the user navigates TOWARD the tab (a real switch, a
// keyboard shortcut, or arriving with the URL pointing at it) — never when the layout merely renders the
// panel (a docked secondary on load, a minimized dock). Each tab carries a counter passed to its panel as
// :focus-request; bumping it tells the panel to focus its content: the tree's search filter (or the open
// file) for Files/Git/Artifacts, the active terminal (or its Start/Reconnect overlay) for the terminal.
// Deliberately NOT driven by onTabShow/onLayoutSelectTab (wa-tab-group emits wa-tab-show on initial
// render too) nor by watching activeTabId (route claims from clicking inside a shown panel change it).
// Distinct from requestPaneFocus, which only claims the URL for a pane. Orchestration has no focus target.
// On a touch device every one of these targets is a text field (filter input, editor, address bar), so
// focusing it pops the on-screen keyboard over the panel the user just opened — the focus itself buys
// nothing there (it exists for tab/arrow navigation). Only the terminal keeps it: typing IS the point.
// A manual tap into a filter still focuses and opens the keyboard, as the platform does natively.
// The one touch exception is a request coming FROM a keyboard shortcut (`fromKeyboard`): the gesture
// proves a physical keyboard, so tab/arrow navigation is exactly what the user is doing.
const ACTIVATION_FOCUS_TABS = ['files', 'git', 'artifacts', 'terminal', 'browser']
const TOUCH_ACTIVATION_FOCUS_TABS = ['terminal']
const panelFocusRequests = reactive({ files: 0, git: 0, artifacts: 0, terminal: 0, browser: 0 })
function requestPanelFocus(tabId, { fromKeyboard = false } = {}) {
    if (settingsStore.isTouchDevice && !fromKeyboard && !TOUCH_ACTIVATION_FOCUS_TABS.includes(tabId)) return
    if (tabId in panelFocusRequests) panelFocusRequests[tabId]++
}
// Arriving on / returning to a session whose URL targets such a tab is an explicit navigation toward
// it → focus its content. Read (never watch) activeTabId here, so in-session route claims can't trigger
// it; mouse switches and keyboard are handled by the gesture handlers.
function focusRouteToolTabOnArrival() {
    if (!isActive.value || !session.value) return
    if (ACTIVATION_FOCUS_TABS.includes(activeTabId.value)) requestPanelFocus(activeTabId.value)
}
// Cold deep-link: the session can resolve after onActivated (the panel isn't mounted yet then) — once
// it appears while this view is active, re-fire the arrival focus (deferred to after the panel mounts).
watch(() => !!session.value, (has, had) => {
    if (has && !had) nextTick(() => focusRouteToolTabOnArrival())
})

// Overlay focus lifecycle. A peek overlay is transient: opening it on a tab makes that tab active
// (route owner) so its panel syncs from the route — but we remember the tab that was active just
// before, and an explicit dismiss (backdrop / close button / toggle) returns focus to it. Switching
// tabs WITHIN the overlay keeps the same remembered tab (captured only on the first open). A
// non-dismiss close (the tab was placed into a real dock/center, or a resize dropped the overlay)
// leaves focus on wherever it landed — we only clear the memory (handled by the watcher below).
let overlayReturnTab = null
function onOverlayActivate(tabId) {
    if (overlayReturnTab === null) {
        const prior = activeTabId.value
        // Never remember an overlay-only tab as the return target — dismissing to it would just
        // re-open the overlay (the auto-open watcher below). Fall back to the center anchor.
        overlayReturnTab = layout.overlayEdgeForTab(prior) ? 'main' : prior
    }
    cancelPaneFocus()
    switchToTab(tabId)
    // Peeking a docked tab in an overlay makes a previously-hidden tab visible → focus its content.
    if (ACTIVATION_FOCUS_TABS.includes(tabId)) requestPanelFocus(tabId)
}
function onOverlayDismiss() {
    // No remembered tab (overlay was opened by direct navigation, not a gesture) → fall back to the
    // center, so dismissing leaves focus on a visible tab and can't re-trigger the auto-open.
    const back = overlayReturnTab ?? 'main'
    overlayReturnTab = null
    cancelPaneFocus()
    switchToTab(back)
}
// Any overlay close clears the remembered return tab. A dismiss already cleared it synchronously
// above; this catches closes driven purely by the route leaving overlay mode (back/forward, the
// tab being placed into a real dock, a resize) so a later open recaptures a fresh prior.
// openOverlayEdge is derived from the route, so the overlay opens/closes on its own — there is no
// auto-open watcher to maintain; navigating to an overlay-mode tab shows it, navigating away hides it.
watch(() => layout.openOverlayEdge.value, (edge) => {
    if (!edge) overlayReturnTab = null
})

// Click-to-focus for the center zone (mirror of DockRegion's): clicking the center content while
// a dock owns the URL focuses the center's active tab. Tab clicks navigate on their own, so skip
// clicks that land on the nav. Listens on click (gesture end) + deferred, like the dock claim.
function onCenterClick(event) {
    if (!layout.dockingRendered.value) return
    // Empty area of the tab bar (a shadow part, retargeted to the host): act exactly like clicking the
    // active tab's header. A tab header is slotted (target !== host) and handled by onCenterTabClick on
    // its own; a click inside a panel body falls through to a route-only claim (no content focus).
    if (event.target === event.currentTarget) { onCenterTabClick(centerActiveTab.value); return }
    if (event.target?.closest?.('[slot="nav"]')) return
    requestPaneFocus(centerActiveTab.value)
}

// Clicking a center tab's label claims focus for that tab (deferred). wa-tab-show (→ onTabShow)
// handles switching to a *different* tab and supersedes this; this also covers clicking the tab
// that is ALREADY the center's active one (no wa-tab-show fires then) while a dock owns the route.
// Sub-controls inside a tab (placement arrow, close icon) stop their own click, so it never lands here.
function onCenterTabClick(tabId) {
    // Clicking a tool tab's HEADER is an explicit activation → focus its content (always — mouse or
    // keyboard, already shown or not; only clicking a pane's BODY must not, see requestPaneFocus).
    if (ACTIVATION_FOCUS_TABS.includes(tabId)) requestPanelFocus(tabId)
    if (!layout.dockingRendered.value) return
    requestPaneFocus(tabId)
}

// Minimizing the dock that holds the focused tab would leave the URL on a now-hidden panel —
// hand focus back to the center's active tab.
async function onLayoutMinimize(dockIds) {
    const focusedLeaving = dockIds.includes(layout.dockOf(activeTabId.value))
    await layout.withRouteRevealSuspended(async () => {
        cancelPaneFocus()
        layout.minimize(dockIds)
        if (focusedLeaving) await switchToTab(centerActiveTab.value)
    })
}

// Maximize (transient view state; the only exit is restore). Maximizing a region routes + focuses its
// active tab so the URL points into what's shown; restore brings the prior layout back unchanged.
const isCenterMaximized = computed(() => layout.isCenterMaximized.value)
// "Not single pane": the render has at least one non-center region (a dock or a maximized region) or
// a gutter. Gates the Save + Maximize buttons (the Select menu shows always — it's how you enter a
// layout from single pane). False in single-pane and the mobile tab strip (no dock regions there).
const hasDocks = computed(() => {
    const r = layout.render.value
    return r.regions.some((reg) => reg.kind !== 'center') || r.gutters.length > 0
})
// Center tabs' placement arrows: hidden in the mobile tab strip and while the center is maximized
// (a maximized region is a separate mode — the only exit is restore, no re-docking).
const showCenterPlacementArrows = computed(() => !layoutTabsMode.value && !isCenterMaximized.value)

// Layout catalog — Save / Select. Loading copies a catalog layout's intention into the session;
// saving stores the session's current structure (the template subset) as a named/overwritten layout.
const layoutSaveDialogRef = ref(null)
const currentLayoutTemplate = computed(() => store.getSessionLayoutTemplate(sessionId.value))
function onSelectLayout(layoutId) {
    store.loadLayoutIntoSession(sessionId.value, layoutsStore.intentionForId(layoutId))
}

// Scope-default rows for the layout menu: the default layout resolved at each distinct level of the
// session's project chain — worktree (the project itself, when it's a worktree), project (the main
// repo / nearest path ancestor that sets one), and global (settings). Only levels that resolve to a
// REAL named layout are shown (single-pane / dangling → skipped, the menu's "Single pane" covers it);
// most-specific wins on ties, so a layout never appears twice. The menu also drops these ids from its
// named-layouts list. Order: worktree → project → global.
const layoutScopeDefaults = computed(() => {
    const pid = projectId.value
    if (!pid) return []
    const chain = ancestorChain(pid, store.projects)   // [self, parent, ...]
    const self = chain[0]
    const isWorktree = !!self?.worktree_of
    const worktreeId = isWorktree ? self?.default_layout_id : null
    let projDefaultId = null
    for (let i = isWorktree ? 1 : 0; i < chain.length; i++) {
        if (chain[i].default_layout_id != null) { projDefaultId = chain[i].default_layout_id; break }
    }
    const rows = []
    const seen = new Set()
    const add = (scope, label, id) => {
        if (!id || id === SINGLE_PANE_ID || seen.has(id)) return
        const layout = layoutsStore.getLayoutById(id)   // null for single-pane / dangling
        if (!layout) return
        seen.add(id)
        rows.push({ scope, label, layoutId: id, layoutName: layout.name })
    }
    add('worktree', 'Worktree default', worktreeId)
    add('project', 'Project default', projDefaultId)
    add('global', 'Global default', settingsStore.getDefaultLayoutId)
    return rows
})

// Scope targets for the save dialog's "set as default" checkboxes. One checkbox per applicable level
// of the session's project chain: worktree (the project itself, only when it's a worktree), project
// (the main repo when worktree, else the project itself), and global (settings) — always last.
// For each, we surface the OWN (non-inherited) default: it drives the pre-check (checked iff that
// level has no explicit non-single-pane default of its own — the global "first save" heuristic
// generalised to every level) and the "(current / inherited: X)" hint. Order: worktree → project →
// global. Mirrors the chain logic of layoutScopeDefaults; the dialog applies the checked ones.
const layoutSaveScopes = computed(() => {
    const globalId = settingsStore.getDefaultLayoutId
    const nameForId = (id) =>
        !id || id === SINGLE_PANE_ID ? SINGLE_PANE_NAME : layoutsStore.getLayoutById(id)?.name || SINGLE_PANE_NAME
    // A project-backed scope (worktree / project): its own column drives the pre-check + the hint.
    const projectScope = (key, label, target) => {
        const ownId = target.default_layout_id ?? null
        const inherited = ownId == null
        const shownId = inherited ? resolveProjectLayoutId(target.id, store.projects, globalId) : ownId
        return {
            key,
            label,
            targetProjectId: target.id,
            preChecked: (ownId || SINGLE_PANE_ID) === SINGLE_PANE_ID,
            inherited,
            currentName: nameForId(shownId),
        }
    }
    const scopes = []
    const self = projectId.value ? store.projects[projectId.value] : null
    if (self) {
        if (self.worktree_of) {
            scopes.push(projectScope('worktree', 'In Worktree', self))
            const parent = store.projects[self.worktree_of]
            // Drop the project row if the parent repo isn't loaded — nothing to target.
            if (parent) scopes.push(projectScope('project', `In Project — ${store.getProjectDisplayName(parent.id)}`, parent))
        } else {
            scopes.push(projectScope('project', 'In Project', self))
        }
    }
    // Global (chain root): always concrete, never inherited.
    scopes.push({
        key: 'global',
        label: 'Globally',
        targetProjectId: null,
        preChecked: (globalId || SINGLE_PANE_ID) === SINGLE_PANE_ID,
        inherited: false,
        currentName: nameForId(globalId),
    })
    return scopes
})
function onOpenSaveLayout() {
    layoutSaveDialogRef.value?.open()
}
const layoutManagerDialogRef = ref(null)
function onManageLayouts() {
    layoutManagerDialogRef.value?.open()
}
async function onCenterMaximize() {
    await layout.withRouteRevealSuspended(async () => {
        cancelPaneFocus()
        layout.maximize(['center'])
        await switchToTab(centerActiveTab.value)
    })
}
async function onLayoutMaximize(dockIds, tabId) {
    await layout.withRouteRevealSuspended(async () => {
        cancelPaneFocus()
        layout.maximize(dockIds)
        if (tabId) await switchToTab(tabId)
    })
}
function onLayoutRestoreMaximized() {
    // A region maximized straight from the rail (a minimized or swapped-out dock) keeps its
    // underlying collapsed/swapped state, so restoring drops it back to the rail — its focused tab
    // would then be invisible. Mirror onLayoutMinimize and hand focus back to the center.
    const focused = activeTabId.value
    const dock = layout.dockOf(focused)
    layout.restoreMaximized()
    if (dock && dock !== 'center' && !layout.isToolPanelVisible(focused)) {
        // A restoring double-click on the maximized tab queued a deferred pane-focus (from its tab-header
        // clicks). Left to fire on the next frame, its rAF would re-route to the now-railed tab AFTER we
        // moved off it, and the layout watch — no longer seeing it maximized — would un-minimize the dock
        // (re-dock it). Cancel it so the dock stays minimized/swapped-out, then route to the center.
        cancelPaneFocus()
        switchToTab(centerActiveTab.value)
    }
}
// Double-clicking the center tab bar toggles maximize for the whole central zone — only where the
// maximize button exists (hasDocks); a no-op in single pane / the mobile tab strip. Scoped to the
// group's own nav tabs: closest('wa-tab') + a direct-child check so a nested wa-tab (e.g. the
// terminal's internal tabs) or the nav cluster (layout menu / maximize button) never triggers it.
function onCenterTabDblClick(event) {
    // The empty bar area (target === the group host) counts too, alongside the group's own nav tabs.
    if (event.target !== event.currentTarget) {
        const tab = event.target?.closest?.('wa-tab')
        if (!tab || tab.getAttribute('slot') !== 'nav' || tab.parentElement !== event.currentTarget) return
    }
    if (!hasDocks.value) return
    if (isCenterMaximized.value) onLayoutRestoreMaximized()
    else onCenterMaximize()
}

// Advertise the double-click maximize/restore via a native title, scoped to the tab strip ONLY. The
// title can't live on the wa-tab-group host: it's the flat-tree ancestor of both the nav strip AND the
// `part="body"` slot that holds the panels, so a host title leaks the tooltip into every tab's content.
// We set it on the `part="nav"` container instead (the same strip the ::part(nav) cursor targets) — the
// common ancestor of the bar region and nothing else, so the tooltip covers the whole bar (empty area +
// tabs, found via flat-tree traversal) and never the panel content below.
async function syncCenterBarTitle() {
    // sessionTabsRef is the TabBar wrapper instance; the native <wa-tab-group> is on `.el`.
    const group = sessionTabsRef.value?.el
    if (!group) return
    if (group.updateComplete) await group.updateComplete
    const nav = group.shadowRoot?.querySelector('[part~="nav"]')
    if (!nav) return
    const title = hasDocks.value
        ? (isCenterMaximized.value ? 'Double-click to restore' : 'Double-click to maximize')
        : null
    if (title) nav.setAttribute('title', title)
    else nav.removeAttribute('title')
}
watch([hasDocks, isCenterMaximized, sessionTabsRef], syncCenterBarTitle, { immediate: true })

// ─── Fixed nav-cluster geometry (see the cluster's template comment) ─────────
// One observer, two targets: the cluster (its width changes with hasDocks and menu content) and
// the bar container (its height follows fonts/zoom). Width → reserved ::part(nav) margin;
// height → the cluster's own height, so its bottom border lands on the tabs' track.
const layoutNavClusterEl = ref(null)
const centerNavClusterWidth = ref(0)
const centerNavBarHeight = ref(0)
let centerNavContainerEl = null
const centerNavObserver = new ResizeObserver(measureCenterNav)
function measureCenterNav() {
    // getBoundingClientRect, NOT offsetWidth/offsetHeight: those round to integers, and the bar's
    // real height is usually fractional (em paddings × font size, zoom, mobile DPR) — a rounded
    // cluster height lands its bottom border up to 1px off the tabs' track.
    centerNavClusterWidth.value = layoutNavClusterEl.value?.getBoundingClientRect().width || 0
    centerNavBarHeight.value = centerNavContainerEl?.getBoundingClientRect().height || 0
}
watch(layoutNavClusterEl, (el, old) => {
    if (old) centerNavObserver.unobserve(old)
    if (el) centerNavObserver.observe(el)
    measureCenterNav()
})
watch(sessionTabsRef, async (bar) => {
    const group = bar?.el
    if (group?.updateComplete) await group.updateComplete
    const nav = group?.shadowRoot?.querySelector('[part~="nav"]') || null
    if (nav === centerNavContainerEl) return
    if (centerNavContainerEl) centerNavObserver.unobserve(centerNavContainerEl)
    if (nav) centerNavObserver.observe(nav)
    centerNavContainerEl = nav
    measureCenterNav()
}, { immediate: true })
onBeforeUnmount(() => centerNavObserver.disconnect())

// ─── Layout actions on the focused pane (keyboard shortcuts + palette) ───────
// These drive the SAME handlers as the on-screen maximize / minimize / restore
// buttons, targeting the region that holds the focused (route) tab — i.e. the
// region whose button you'd otherwise click. No new layout logic lives here.

// The dock region currently holding the focused tab, or null in single pane /
// the mobile tab strip (where no maximize/minimize button exists). The center
// is itself a region, so a focused center returns the center region.
function focusedDockRegion() {
    if (!layout.dockingRendered.value) return null
    return layout.render.value.regions.find(
        (r) => r.slots.some((s) => s.tabs.some((t) => t.id === activeTabId.value)),
    ) || null
}
// Maximize is available on any focused region (center included), unless already maximized.
function canMaximizeFocusedPane() {
    return !layout.maximizedRegion.value && !!focusedDockRegion()
}
// Minimize only exists on real dock regions — the center has no minimize button.
function canMinimizeFocusedPane() {
    if (layout.maximizedRegion.value) return false
    const region = focusedDockRegion()
    return !!region && !region.slots.some((s) => s.dockId === 'center')
}
function maximizeFocusedPane() {
    if (!canMaximizeFocusedPane()) return false
    onLayoutMaximize(focusedDockRegion().slots.map((s) => s.dockId), activeTabId.value)
    return true
}
function minimizeFocusedPane() {
    if (!canMinimizeFocusedPane()) return false
    onLayoutMinimize(focusedDockRegion().slots.map((s) => s.dockId))
    return true
}
function restoreMaximizedPane() {
    if (!layout.maximizedRegion.value) return false
    onLayoutRestoreMaximized()
    return true
}
// Alt+Shift+Enter toggles: restore when a pane is maximized, else maximize the focused one —
// same as the maximize/restore double-click on a region.
function toggleMaximizeFocusedPane() {
    return layout.maximizedRegion.value ? restoreMaximizedPane() : maximizeFocusedPane()
}

// Alt+Shift+{Enter|Backspace} (dispatched by App.vue). Only the active session view acts;
// `handled` is flipped back so App.vue swallows the key only when something happened (so
// e.g. Alt+Shift+Enter in single pane, with nothing to maximize, stays inert).
function handleLayoutShortcut(event) {
    if (!isActive.value) return
    let acted = false
    if (event.detail?.action === 'maximize') acted = toggleMaximizeFocusedPane()
    else if (event.detail?.action === 'minimize') acted = minimizeFocusedPane()
    if (acted && event.detail) event.detail.handled = true
}

// Teleport target registry: logical key -> element. The center slot registers its tab-panel
// targets; dock regions / the overlay register theirs. Tool panels teleport to targetKeyForTab().
const layoutTargets = reactive({})
function registerLayoutTarget(key, el) { layoutTargets[key] = el }
function unregisterLayoutTarget(key) { delete layoutTargets[key] }
function toolTarget(tabId) { return layoutTargets[layout.targetKeyForTab(tabId)] || null }

// Stable ref callbacks for the center tab-panel targets (avoid re-running on every render).
const centerTargetSetters = Object.fromEntries(
    LAYOUT_TOOL_IDS.map((id) => [id, (el) => (el
        ? registerLayoutTarget(`center:${id}`, el)
        : unregisterLayoutTarget(`center:${id}`))])
)

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: tab navigation (Alt+Shift+1-4, ←/→, ↑)
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Ordered list of all visible tabs (for sequential ←/→ navigation): main, subagents, then the
// present tool tabs in registry order (files, [git], terminal, [artifacts], [orchestration]).
const orderedTabs = computed(() => {
    const tabs = ['main']
    for (const tab of openSubagentTabs.value) tabs.push(tab.id)
    tabs.push(...presentToolTabs.value.map((t) => t.id))
    return tabs
})

// Tab visit history for Alt+Shift+↑ (last-visited, Alt+Tab-like behavior).
// Plain array (not reactive) — no template depends on it.
// Persists as long as the component is KeepAlive'd.
const tabHistory = []
const MAX_TAB_HISTORY = 50

function pushTabHistory(tabId) {
    if (tabHistory.length > 0 && tabHistory[tabHistory.length - 1] === tabId) return
    tabHistory.push(tabId)
    if (tabHistory.length > MAX_TAB_HISTORY) tabHistory.shift()
}

// Track tab transitions for history (separate from the store sync watcher).
// oldTabId is undefined on the first call, so we guard with `if (oldTabId)`.
watch(activeTabId, (newTabId, oldTabId) => {
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    if (oldTabId) pushTabHistory(oldTabId)
})

// Direct tab mapping: Alt+Shift+{1..9, 0} → fixed tabs (subagents are skipped).
// Tasks (5), Plan (6), Artifacts (7), Orchestration (8) and Workflows (9) are
// conditional — the handler no-ops when the tab is absent. 0 is Browser (the
// last tab, always present).
const DIRECT_TAB_MAP = { 1: 'main', 2: 'files', 3: 'git', 4: 'terminal', 5: 'tasks', 6: 'plan', 7: 'artifacts', 8: 'orchestration', 9: 'workflows', 0: 'browser' }

/**
 * Handle keyboard tab shortcut events dispatched from App.vue.
 * Only the active SessionView instance processes the event (KeepAlive guard).
 */
function handleTabShortcut(event) {
    if (!isActive.value) return

    const { type, index } = event.detail
    let targetTab = null

    if (type === 'direct') {
        targetTab = DIRECT_TAB_MAP[index]
        if (!targetTab) return
        if (!isToolTabPresent(targetTab)) return
    } else if (type === 'prev' || type === 'next') {
        const tabs = orderedTabs.value
        const currentIndex = tabs.indexOf(activeTabId.value)
        if (currentIndex === -1) return
        const newIndex = type === 'next'
            ? (currentIndex + 1) % tabs.length
            : (currentIndex - 1 + tabs.length) % tabs.length
        targetTab = tabs[newIndex]
    } else if (type === 'last-visited') {
        const tabs = orderedTabs.value
        // Walk history backwards to find the most recent tab that still exists
        // and isn't the currently active one
        for (let i = tabHistory.length - 1; i >= 0; i--) {
            const tabId = tabHistory[i]
            if (tabId !== activeTabId.value && tabs.includes(tabId)) {
                targetTab = tabId
                break
            }
        }
    }

    if (!targetTab) return
    switchToTab(targetTab)
    // Keyboard navigation toward an activation-focus tool tab focuses its content — whether the tab
    // was hidden (a switch) or already visible (explicit keyboard intent on a shown panel). fromKeyboard
    // lifts the touch-device gate: this event only exists because a physical keyboard produced it.
    if (ACTIVATION_FOCUS_TABS.includes(targetTab)) requestPanelFocus(targetTab, { fromKeyboard: true })
    // Same intent for the Chat tab: keyboard arrival focuses its primary control — the pending-request
    // form when one is shown, else the message input — exactly like Alt+Shift+M. Chat isn't an
    // ACTIVATION_FOCUS_TAB (its target lives in the footer accordion, reached via focusChatPrimary's own
    // retry loop rather than the panelFocusRequests counter), so it needs its own branch. It can't be
    // deferred to onTabShow: that handler early-returns on the programmatic wa-tab-show this navigation
    // triggers (the spurious-event guard), so the chat focus must fire from here.
    else if (targetTab === 'main') nextTick(() => focusChatPrimary())
}

// ═══════════════════════════════════════════════════════════════════════════
// Drag-hover: spring-loaded tab switching (hover 1s while dragging to switch)
// ═══════════════════════════════════════════════════════════════════════════

// Drag-hover on the Chat tab: switches to it when dragging files/text over it for 1 second.
// If files/text are dropped directly on the tab, forward to SessionItemsList for processing.
const chatTabDragHover = useDragHover({
    onActivate: () => switchToTab('main'),
    shouldActivate: () => activeTabId.value !== 'main',
    onDropData: (data) => {
        // Ensure we're on the Chat tab before forwarding
        if (activeTabId.value !== 'main') {
            switchToTab('main')
        }
        nextTick(() => {
            sessionItemsListRef.value?.handleForwardedDrop(data)
        })
    },
})

// Pick up pending drop data from ProjectView (when files/text were dropped on a session list item).
const pendingDropData = inject('pendingDropData', ref(null))
watch(pendingDropData, (data) => {
    if (!data || data.sessionId !== sessionId.value) return
    // Consume the pending data
    pendingDropData.value = null
    // Ensure we're on the Chat tab
    if (activeTabId.value !== 'main') {
        switchToTab('main')
    }
    nextTick(() => {
        sessionItemsListRef.value?.handleForwardedDrop(data)
    })
})

/**
 * Handle tab change event from wa-tab-group.
 * Updates the URL to reflect the new active tab.
 */
function onTabShow(event) {
    const panel = event.detail?.name
    if (!panel) return
    // wa-tab-group re-emits wa-tab-show whenever its :active binding changes programmatically — most
    // notably on KeepAlive return, where the docking area measures 0×0 and `dockingRendered` flips,
    // recomputing centerActiveTab (a docked tool tab → 'main'). Acting on that would navigate the route
    // OFF the docked tab back to Chat. A genuine user switch always targets a tab DIFFERENT from the
    // center's current active (clicking the already-active tab fires no wa-tab-show), so forward only
    // real changes — mirrors DockRegion.onShow's spurious-wa-tab-show guard.
    if (panel === centerActiveTab.value) return
    cancelPaneFocus()
    switchToTab(panel)
}

/**
 * Close a subagent tab.
 * @param {string} tabId - The tab ID to close (e.g., 'agent-xxx')
 */
function closeTab(tabId) {
    const tabs = openSubagentTabs.value
    const index = tabs.findIndex(t => t.id === tabId)
    if (index === -1) return

    // Remove the tab from store
    store.removeSessionTab(sessionId.value, tabId)

    // If this was the active tab, navigate to the tab on the left
    if (activeTabId.value === tabId) {
        if (index > 0) {
            // Go to the previous subagent tab (use current tabs, not yet updated)
            const prevTab = tabs[index - 1]
            router.push({
                name: buildSubagentRouteName(isAllProjectsMode.value),
                params: {
                    projectId: filterProjectId.value,
                    sessionId: sessionId.value,
                    subagentId: prevTab.agentId
                },
                query: route.query,
            })
        } else {
            // No more subagent tabs, go to main
            router.push({
                name: buildSessionBaseRouteName(isAllProjectsMode.value),
                params: {
                    projectId: filterProjectId.value,
                    sessionId: sessionId.value
                },
                query: route.query,
            })
        }
    }
}

/**
 * Open a subagent tab if not already open.
 * @param {string} agentId - The agent ID
 */
function openSubagentTab(agentId) {
    store.addSessionTab(sessionId.value, `agent-${agentId}`)
}

/**
 * Label rendered in the subagent tab buttons (compact dropdown, tab
 * bar, wa-tabs nav). Prefers ``Session.slug`` when the provider exposes
 * one (Codex stores the agent_nickname there); falls back to the first
 * 8 characters of the agent id otherwise (Claude Code, where slug
 * is currently unset).
 */
function getAgentTabLabel(agentId) {
    return getAgentDisplayLabel(agentId, store)
}

// Watch subagentId to open tab when navigating to a subagent URL.
// Two guards prevent incorrect tab additions with KeepAlive (same logic as activeTabId watcher):
// 1. isActive: skip when deactivated — don't react to route changes while cached
// 2. sessionId check: skip when the route belongs to a different session
watch(subagentId, (newSubagentId) => {
    if (!newSubagentId) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    openSubagentTab(newSubagentId)
}, { immediate: true })

// Sync active tab in store when the route changes for THIS session.
watch(activeTabId, (newTabId) => {
    if (!sessionId.value) return
    if (!isActive.value) return
    if (route.params.sessionId !== sessionId.value) return
    store.setSessionActiveTab(sessionId.value, newTabId)

}, { immediate: true })

/**
 * Handle a session that needs a title after sending its first message.
 * If title auto-apply is enabled, requests a suggestion and applies it
 * automatically when it arrives (same flow as the rename dialog's Save).
 * Otherwise, opens the rename dialog.
 */
function handleNeedsTitle() {
    if (settingsStore.isTitleAutoApply && settingsStore.isTitleGenerationEnabled) {
        const sid = sessionId.value
        const pid = projectId.value
        const prompt = store.getDraftMessage(sid)?.message?.trim()
        if (!prompt) return

        // Register the intent in the store BEFORE firing the WS request so
        // the global auto-apply watcher (set up in main.js via
        // ``startAutoApplyTitleWatcher``) is already observing this session
        // even if the backend reply comes back in the same tick. The
        // watcher lives at module scope and survives the router.replace
        // that ``bindDraftSession`` performs for Codex drafts.
        store.registerPendingTitleAutoApply(sid, pid)
        requestTitleSuggestion(sid, prompt, settingsStore.getTitleSystemPrompt)
    } else {
        sessionHeaderRef.value?.openRenameDialog({ showHint: true })
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// Command palette: contextual session commands
// ═══════════════════════════════════════════════════════════════════════════

// The transcript moves exposed by SessionItemsList's `chatNav`, in the order
// the ChatNavToolbar shows them (and with its icons).
const CHAT_NAV_COMMANDS = [
    { idSuffix: 'top',    label: 'Go to First Message',    icon: 'angles-up',    move: 'goTop' },
    { idSuffix: 'prev',   label: 'Previous Message Block', icon: 'chevron-up',   move: 'goPrevBlock' },
    { idSuffix: 'next',   label: 'Next Message Block',     icon: 'chevron-down', move: 'goNextBlock' },
    { idSuffix: 'bottom', label: 'Go to Last Message',     icon: 'angles-down',  move: 'goBottom' },
]

const SESSION_COMMAND_IDS = [
    'session.rename',
    'session.archive',
    'session.unarchive',
    'session.pin-mode',
    'session.mute',
    'session.mark-read',
    'session.mark-unread',
    'session.stop',
    'session.force-stop',
    'session.delete-draft',
    'session.focus-input',
    'session.focus-pending',
    'session.focus-terminal',
    'session.toggle-hybrid',
    ...CHAT_NAV_COMMANDS.map(c => `session.nav-${c.idSuffix}`),
    'session.collapse-input',
    'session.expand-input',
    'session.model',
    'session.effort',
    'session.permission',
    'session.thinking',
    'session.context',
    'session.chrome',
    'session.fast-mode',
]

// Layout-category command ids — registered via buildLayoutCommands(), listed
// here so onDeactivated / onBeforeUnmount unregister them with the session ones.
const LAYOUT_COMMAND_IDS = [
    'layout.maximize-pane',
    'layout.minimize-pane',
    'layout.restore-pane',
    'layout.move-tab',
    'layout.save',
    'layout.load',
]

// Read/unread gate for the current session, mirroring SessionListItem's
// canToggleReadState + hasUnread — minus the "is this the active row" guard,
// since here the session IS the one on screen. Returns `{ unread }` (the raw
// unread flag), or null when toggling read state isn't allowed (draft, archived,
// or a process running outside user_turn). Archived sessions never read as
// unread, so both palette commands are dropped for them.
function currentSessionReadState() {
    const s = store.getSession(sessionId.value)
    if (!s || s.draft || s.archived) return null
    const ps = store.getProcessState(sessionId.value)
    if (ps && ps.state !== PROCESS_STATE.USER_TURN) return null
    const unread = !!s.last_new_content_at
        && (!s.last_viewed_at || s.last_new_content_at > s.last_viewed_at)
    return { unread }
}

function registerSessionCommands() {
    // Layout commands go in first, as their own registerCommands call: they never
    // depend on provider resolution, so even if the settings block below failed to
    // build they'd already be registered (the bug this split fixes).
    registerCommands(buildLayoutCommands())
    registerCommands([
        {
            id: 'session.rename',
            label: 'Rename Session',
            icon: 'pencil',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft
            },
            action: () => sessionHeaderRef.value?.openRenameDialog(),
        },
        {
            id: 'session.archive',
            label: 'Archive Session',
            icon: 'box-archive',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft && !s.archived
            },
            action: () => stopSessionProcess(sessionId.value, { archive: true }),
        },
        {
            id: 'session.unarchive',
            label: 'Unarchive Session',
            icon: 'box-open',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.archived
            },
            action: () => store.setSessionArchived(projectId.value, sessionId.value, false),
        },
        {
            id: 'session.pin-mode',
            label: 'Change Pin Mode…',
            icon: 'thumbtack',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft
            },
            items: () => {
                const s = store.getSession(sessionId.value)
                const current = s?.pinned ?? null
                const pick = (mode) => store.setSessionPinMode(projectId.value, sessionId.value, mode)
                return [
                    { id: 'none',      label: 'Not pinned',   action: () => pick(null),        active: !current },
                    { id: 'project',   label: 'Project',      action: () => pick('project'),   active: current === 'project' },
                    { id: 'workspace', label: 'Workspace',    action: () => pick('workspace'), active: current === 'workspace' },
                    { id: 'all',       label: 'Everywhere', action: () => pick('all'),       active: current === 'all' },
                ]
            },
        },
        {
            id: 'session.mute',
            label: 'Mute "Finished Working" Notification',
            icon: 'bell-slash',
            category: 'session',
            // Mirrors the session header's bell. Silences that one notification
            // family for this session only; every other alert still comes
            // through. Kept visible when no such channel is enabled — the flag
            // is a durable preference, and the toggle says so itself.
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !s.draft
            },
            toggled: () => !!store.getSession(sessionId.value)?.mute_on_user_turn,
            action: () => toggleSessionMute(sessionId.value),
        },
        {
            id: 'session.mark-read',
            label: 'Mark as Read',
            icon: 'eye-slash',
            category: 'session',
            when: () => currentSessionReadState()?.unread === true,
            action: () => markSessionReadState(sessionId.value, false),
        },
        {
            id: 'session.mark-unread',
            label: 'Mark as Unread',
            icon: 'eye',
            category: 'session',
            when: () => currentSessionReadState()?.unread === false,
            action: () => {
                // Cancel any pending session_viewed throttle so it can't re-mark
                // this session read, flag it unread, then leave it — staying on
                // the session would reset it to read (mirrors SessionListItem's
                // mark-unread on the active row).
                cancelSessionViewedThrottle(sessionId.value)
                markSessionReadState(sessionId.value, true)
                if (isAllProjectsMode.value) {
                    router.push({ name: 'projects-all', query: route.query.workspace ? { workspace: route.query.workspace } : {} })
                } else {
                    router.push({ name: 'project', params: { projectId: filterProjectId.value } })
                }
            },
        },
        {
            id: 'session.stop',
            label: 'Stop Process',
            icon: 'stop',
            category: 'session',
            when: () => {
                const ps = store.getProcessState(sessionId.value)
                return !!ps && ps.state !== PROCESS_STATE.DEAD && !ps.synthetic
            },
            action: () => stopSessionProcess(sessionId.value),
        },
        {
            id: 'session.force-stop',
            label: 'Force Kill Process',
            icon: 'bolt',
            category: 'session',
            when: () => {
                const ps = store.getProcessState(sessionId.value)
                return !!ps && ps.state !== PROCESS_STATE.DEAD && !ps.synthetic
            },
            action: () => hardKillSessionProcess(sessionId.value),
        },
        {
            id: 'session.delete-draft',
            label: 'Delete Draft',
            icon: 'trash',
            category: 'session',
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s && !!s.draft
            },
            action: () => {
                store.deleteDraftSession(sessionId.value)
                if (isAllProjectsMode.value) {
                    router.push({ name: 'projects-all', query: route.query.workspace ? { workspace: route.query.workspace } : {} })
                } else {
                    router.push({ name: 'project', params: { projectId: filterProjectId.value } })
                }
            },
        },
        {
            id: 'session.focus-input',
            label: 'Focus Message Input',
            icon: 'keyboard',
            category: 'session',
            // Direct access to the message input specifically — opens it in the
            // footer accordion (reducing the terminal / pending request) and
            // focuses the textarea. Distinct from Alt+Shift+M, which lands on the
            // pending request form instead when one is open. Mirrors Alt+Shift+PageDown.
            action: () => gotoChatFooterPanel(route, router, 'twicc:goto-message-input'),
        },
        {
            id: 'session.focus-pending',
            label: 'Focus Pending Request',
            icon: 'reply',
            category: 'session',
            // Only when an answerable pending request form is shown (a request
            // degraded to badge-only — hybrid_terminal — has no form to focus).
            when: () => {
                const reqs = store.getPendingRequests(sessionId.value)
                return reqs.length > 0 && reqs[0].request_type !== 'hybrid_terminal'
            },
            // Opens the pending request in the accordion (reducing the others) and
            // focuses its primary control. Mirrors Alt+Shift+PageUp.
            action: () => gotoChatFooterPanel(route, router, 'twicc:goto-pending-request'),
        },
        {
            id: 'session.focus-terminal',
            label: 'Open Claude CLI Terminal',
            icon: 'terminal',
            category: 'session',
            // Hybrid sessions only — the embedded CLI terminal block. Hidden
            // entirely while the hybrid feature flag is off.
            when: () => settingsStore.isClaudeHybridEnabled && store.getSession(sessionId.value)?.hybrid === true,
            // Opens the terminal in the accordion (reducing the others) and
            // focuses the xterm. (Alt+Shift+T toggles instead; this only opens.)
            action: () => gotoChatFooterPanel(route, router, 'twicc:goto-terminal'),
        },
        {
            id: 'session.toggle-hybrid',
            label: 'Toggle Hybrid Mode',
            icon: 'right-left',
            category: 'session',
            // Claude sessions where hybrid is actually toggleable — not a
            // committed-permanent one (its switch is one-way). Drafts, staged
            // SDK sessions, and plain non-hybrid SDK sessions all qualify.
            when: () => {
                const s = store.getSession(sessionId.value)
                return !!s
                    && settingsStore.isClaudeHybridEnabled
                    && s.provider === 'claude_code'
                    && !s.hidden
                    && !s.parent_session_id
                    && !(!s.draft && s.hybrid === true)
            },
            // Same as clicking the composer's hybrid button (enable/disable a
            // draft, stage/un-stage, or open the confirm dialog). Mirrors Alt+Shift+H.
            action: () => gotoChatFooterPanel(route, router, 'twicc:toggle-hybrid'),
        },
        ...CHAT_NAV_COMMANDS.map(({ idSuffix, label, icon, move }) => ({
            id: `session.nav-${idSuffix}`,
            label,
            icon,
            category: 'navigation',
            // The four moves of the ChatNavToolbar, which is mouse-only. They
            // share one guard — "there is something to navigate" — rather than
            // one per direction: the palette hides what it can't offer, and a
            // command that vanishes at an extreme reads as a bug. Each move is
            // already a no-op once it runs out of blocks.
            when: () => !!sessionItemsListRef.value?.chatNav?.hasNavigation(),
            action: () => runOnChatTab(route, router, () => sessionItemsListRef.value?.chatNav?.[move]()),
        })),
        {
            id: 'session.collapse-input',
            label: 'Collapse Message Input',
            icon: 'chevron-down',
            category: 'session',
            // Only when a message input is actually shown (main session, not
            // stale/disabled) and currently expanded — including while it sits
            // next to a pending request (the composer collapses independently,
            // leaving the request as-is). We probe the DOM because the collapsed
            // state is local to MessageInput; when() is re-evaluated each palette open.
            when: () => !!document.querySelector('.message-input:not(.collapsed)'),
            action: () => document
                .querySelector('.message-input:not(.collapsed)')
                ?.dispatchEvent(new CustomEvent('twicc:collapse-composer')),
        },
        {
            id: 'session.expand-input',
            label: 'Expand Message Input',
            icon: 'chevron-up',
            category: 'session',
            when: () => !!document.querySelector('.message-input.collapsed'),
            // Expand the composer directly (the same twicc:expand-composer event).
            // We can't route through focusChatPrimary anymore: a collapsed composer
            // now coexists with a pending request, and focusChatPrimary would focus
            // the request instead of expanding the composer. MessageInput focuses
            // the textarea itself once expanded, and expanding it reduces the
            // request (at most one of the two is expanded at a time).
            action: () => document
                .querySelector('.message-input.collapsed')
                ?.dispatchEvent(new CustomEvent('twicc:expand-composer')),
        },
        ...buildSessionSettingsCommands(),
    ])
}

// On a cold deep-link the session — and thus its provider — resolves AFTER the
// onActivated() that first built the command list, so buildSessionSettingsCommands()
// returned [] and the provider-dependent settings commands (Change Model/Effort/…)
// never registered. Re-register just those once the provider lands. (Layout commands
// don't need this: they carry no provider dependency and register up front.)
watch(() => session.value?.provider, (provider) => {
    if (provider && isActive.value) registerCommands(buildSessionSettingsCommands())
})

// ─── Session settings commands (mirror of MessageInput settings popover) ────

function sessionSettingsGate() {
    const s = store.getSession(sessionId.value)
    if (!s) return null
    const gate = sessionItemsListRef.value?.getSessionGateState()
    if (!gate) return null
    if (gate.isStarting) return null
    return gate
}

function getSessionSettingValue(key) {
    return sessionItemsListRef.value?.getSessionSetting(key) ?? null
}

function setSessionSettingValue(key, value) {
    sessionItemsListRef.value?.setSessionSetting(key, value)
}

function buildSessionSettingsCommands() {
    const provider = session.value?.provider
    const helpers = getProviderHelpers(provider)
    if (!helpers) return []

    const isAvailable = () => !!sessionSettingsGate()

    function buildDefaultItem(field, current) {
        return {
            id: '__default__',
            group: 'default',
            label: `Default: ${helpers.getDefaultValueLabel(field, helpers.getDefaultValue(field))}`,
            action: () => setSessionSettingValue(field, null),
            active: current === null,
        }
    }

    function buildSimpleCommand(field, { id, label, icon, iconSrc, when }) {
        if (!helpers.supportsAgentSetting(field)) return []
        return [{
            id,
            label,
            icon,
            iconSrc,
            category: 'session',
            when: when ?? isAvailable,
            items: () => {
                const gate = sessionSettingsGate()
                if (!gate) return []
                const current = getSessionSettingValue(field)
                const items = [buildDefaultItem(field, current)]
                for (const choice of helpers.getFieldChoices(field)) {
                    if (helpers.isChoiceDisabled(field, choice.value, gate)) continue
                    items.push({
                        id: String(choice.value),
                        group: 'force',
                        label: choice.label,
                        action: () => setSessionSettingValue(field, choice.value),
                        active: current === choice.value,
                    })
                }
                return items
            },
        }]
    }

    return [
        ...(helpers.supportsAgentSetting('selected_model') ? [{
            id: 'session.model',
            label: 'Change Session Model…',
            icon: 'robot',
            category: 'session',
            when: isAvailable,
            items: () => {
                const current = getSessionSettingValue('selected_model')
                const items = [buildDefaultItem('selected_model', current)]
                const groups = helpers.getModelSelectGroups(helpers.getModelRegistry?.() ?? [])
                groups.forEach((group, idx) => {
                    const groupKey = `model_group_${idx}`
                    for (const entry of group.entries ?? []) {
                        if (entry.disabled) continue
                        items.push({
                            id: entry.value,
                            group: groupKey,
                            label: entry.label,
                            action: () => setSessionSettingValue('selected_model', entry.value),
                            active: current === entry.value,
                        })
                    }
                })
                return items
            },
        }] : []),
        ...buildSimpleCommand('effort', {
            id: 'session.effort',
            label: 'Change Session Effort…',
            icon: 'gauge',
        }),
        ...buildSimpleCommand('thinking_enabled', {
            id: 'session.thinking',
            label: 'Change Session Thinking…',
            iconSrc: AGENT_SETTING_ICONS.thinking_enabled.src,
        }),
        ...buildSimpleCommand('permission_mode', {
            id: 'session.permission',
            label: 'Change Session Permission Mode…',
            icon: 'shield-halved',
        }),
        ...buildSimpleCommand('context_max', {
            id: 'session.context',
            label: 'Change Session Context Size…',
            icon: 'window-maximize',
            when: () => {
                const gate = sessionSettingsGate()
                if (!gate) return false
                return !gate.isContextMaxForced && !gate.isContextMaxForcedByModel
            },
        }),
        ...buildSimpleCommand('claude_in_chrome', {
            id: 'session.chrome',
            label: 'Change Session Claude in Chrome MCP…',
            icon: 'globe',
        }),
        ...buildSimpleCommand('fast_mode', {
            id: 'session.fast-mode',
            label: 'Change Session Fast Mode…',
            icon: 'gauge-high',
        }),
    ]
}

// ─── Layout commands (own palette category) ─────────────────────────────────
// Kept out of buildSessionSettingsCommands on purpose: those bail out early
// (`if (!helpers) return []`) when the session's provider isn't resolved yet —
// e.g. on a cold deep-link — so anything defined past that point silently fails
// to register. Layout actions don't depend on the provider, so they live here
// and register unconditionally.

function buildLayoutCommands() {
    // Shared guard: only the active session view, and never the mobile tab strip
    // (no dock regions there, so layout actions are meaningless / absent).
    const ready = () => isActive.value && !layoutTabsMode.value
    // The focused tab can move to a dock unless it's center-only (chat / a subagent).
    const movableTab = (id) => id !== 'main' && !id.startsWith('agent-')
    return [
        {
            id: 'layout.maximize-pane',
            label: 'Maximize Focused Pane',
            icon: 'expand',
            category: 'layout',
            when: () => ready() && canMaximizeFocusedPane(),
            action: () => maximizeFocusedPane(),
        },
        {
            id: 'layout.minimize-pane',
            label: 'Minimize Focused Pane',
            icon: 'window-minimize',
            category: 'layout',
            when: () => ready() && canMinimizeFocusedPane(),
            action: () => minimizeFocusedPane(),
        },
        {
            id: 'layout.restore-pane',
            label: 'Restore Maximized Pane',
            icon: 'compress',
            category: 'layout',
            when: () => ready() && !!layout.maximizedRegion.value,
            action: () => restoreMaximizedPane(),
        },
        {
            id: 'layout.move-tab',
            label: 'Move Current Tab to…',
            icon: 'up-down-left-right',
            category: 'layout',
            when: () => ready() && movableTab(activeTabId.value),
            items: () => {
                const tabId = activeTabId.value
                const current = layout.dockOf(tabId)
                return PLACEMENT_OPTIONS
                    .filter((dest) => dest !== current)
                    .map((dest) => ({
                        id: dest,
                        label: DOCK_LABELS[dest],
                        // Same position-hinting SVGs as the per-tab placement menu (TabPlacementMenu);
                        // custom icons consumed via `src`, not an FA glyph name — hence iconSrc.
                        iconSrc: DOCK_ICONS[dest],
                        action: () => layout.place(tabId, dest),
                    }))
            },
        },
        {
            id: 'layout.save',
            label: 'Save Layout…',
            icon: 'floppy-disk',
            category: 'layout',
            when: () => ready() && hasDocks.value,
            action: () => onOpenSaveLayout(),
        },
        {
            id: 'layout.load',
            label: 'Load Layout…',
            icon: 'table-columns',
            category: 'layout',
            // Hidden in the mobile tab strip, like the on-screen layout menu — no layout there.
            when: () => ready() && !layoutTabsMode.value,
            // Mirrors LayoutMenu's catalog: Single pane, then the resolved scope
            // defaults, then the remaining named layouts (deduped against them).
            items: () => {
                const items = [{
                    id: SINGLE_PANE_ID,
                    label: 'Single pane',
                    group: 'base',
                    action: () => onSelectLayout(SINGLE_PANE_ID),
                }]
                for (const d of layoutScopeDefaults.value) {
                    items.push({
                        id: d.layoutId,
                        label: `${d.label} — ${d.layoutName}`,
                        group: 'scope',
                        action: () => onSelectLayout(d.layoutId),
                    })
                }
                const scopeIds = new Set(layoutScopeDefaults.value.map((d) => d.layoutId))
                for (const l of layoutsStore.getAllLayouts) {
                    if (scopeIds.has(l.id)) continue
                    items.push({
                        id: l.id,
                        label: l.name,
                        group: 'named',
                        action: () => onSelectLayout(l.id),
                    })
                }
                return items
            },
        },
    ]
}

onBeforeUnmount(() => {
    unregisterCommands([...SESSION_COMMAND_IDS, ...LAYOUT_COMMAND_IDS])
    chatTabDragHover.cancel()
})
</script>

<template>
    <div class="session-view">
        <!-- Main session header (always visible, above tabs) -->
        <SessionHeader
            v-if="session"
            ref="sessionHeaderRef"
            :session-id="sessionId"
            mode="session"
        />

        <SessionLayout
            v-if="session"
            ref="sessionLayoutRef"
            :layout="layout"
            :tab-href="sessionTabHref"
            :register-target="registerLayoutTarget"
            :unregister-target="unregisterLayoutTarget"
            @select-tab="onLayoutSelectTab"
            @tab-activate="onLayoutTabActivate"
            @focus-pane="requestPaneFocus"
            @minimize="onLayoutMinimize"
            @maximize="onLayoutMaximize"
            @restore-maximized="onLayoutRestoreMaximized"
            @overlay-activate="onOverlayActivate"
            @overlay-dismiss="onOverlayDismiss"
            @tab-drag-start="onLayoutTabDragStart"
            @tab-drop="onLayoutTabDrop"
        >
        <TabBar
            ref="sessionTabsRef"
            :active="centerActiveTab"
            @wa-tab-show="onTabShow"
            @click.capture="onCenterClick"
            @dblclick="onCenterTabDblClick"
            class="session-tabs"
            :class="{ 'tabnav-dimmed': !isCenterRouteActive, 'tabbar-maximizable': hasDocks }"
            :style="{ '--layout-nav-cluster-w': `${centerNavClusterWidth}px` }"
        >
            <!-- Tab navigation -->
            <wa-tab slot="nav" panel="main"
                @click="onCenterTabClick('main')"
                @dragenter="chatTabDragHover.onDragenter"
                @dragleave="chatTabDragHover.onDragleave"
                @dragover="chatTabDragHover.onDragover"
                @drop="chatTabDragHover.onDrop"
                :class="{ 'drag-hover-pending': chatTabDragHover.isPending.value }"
            >
                <SessionTabLink :href="sessionTabHref('main')">
                    <wa-icon :name="TAB_ICONS.main"></wa-icon>
                    Chat
                    <CodeCommentsIndicator :count="chatCommentsCount" :show-tooltip="false" class="tab-comments-indicator" />
                    <wa-icon
                        v-if="store.getPendingRequests(sessionId).length > 0"
                        :id="`session-tab-chat-${sessionId}-pending-request`"
                        name="hand"
                        class="pending-request-indicator"
                    ></wa-icon>
                </SessionTabLink>
                <AppTooltip v-if="store.getPendingRequests(sessionId).length > 0" :for="`session-tab-chat-${sessionId}-pending-request`">Waiting for your response</AppTooltip>
            </wa-tab>

            <!-- Subagent tabs with close button -->
            <template v-for="tab in openSubagentTabs" :key="tab.id">
                <wa-tab slot="nav" :panel="tab.id" @click="onCenterTabClick(tab.id)">
                    <span class="subagent-tab-content">
                        <SessionTabLink :href="sessionTabHref(tab.id)">
                            <wa-icon name="robot"></wa-icon>
                            <span>Agent "{{ getAgentTabLabel(tab.agentId) }}"</span>
                            <ProcessIndicator
                                v-if="store.getProcessState(tab.agentId)"
                                :state="store.getProcessState(tab.agentId).state"
                                size="small"
                            />
                            <CodeCommentsIndicator :count="agentCommentsCount(tab.agentId)" :show-tooltip="false" class="tab-comments-indicator" />
                        </SessionTabLink>
                        <span class="tab-close-icon" @click.stop="closeTab(tab.id)">
                            <wa-icon name="xmark" label="Close tab"></wa-icon>
                        </span>
                    </span>
                </wa-tab>
            </template>

            <!-- Tool tabs — actual keyed DOM order follows the persisted drag order, so visual,
                 keyboard and overflow-scroller order always agree. Chat/subagents remain anchored. -->
            <wa-tab
                v-for="tab in orderedCenterToolTabs"
                :key="tab.id"
                slot="nav"
                :panel="tab.id"
                @click="onCenterTabClick(tab.id)"
            >
                <SessionTabLink :href="sessionTabHref(tab.id)">
                    <wa-icon :name="tab.icon"></wa-icon>
                    {{ tab.label }}
                    <CodeCommentsIndicator
                        v-if="toolTabCommentsCount(tab.id) !== null"
                        :count="toolTabCommentsCount(tab.id)"
                        :show-tooltip="false"
                        class="tab-comments-indicator"
                    />
                </SessionTabLink>
                <TabPlacementMenu v-if="showCenterPlacementArrows" :tab-id="tab.id" current="center" @place="(dest) => layout.place(tab.id, dest)" />
            </wa-tab>

            <!-- Main session panel -->
            <wa-tab-panel name="main">
                <SessionItemsList
                    ref="sessionItemsListRef"
                    :session-id="sessionId"
                    :project-id="projectId"
                    @needs-title="handleNeedsTitle"
                />
            </wa-tab-panel>

            <!-- Subagent panels -->
            <wa-tab-panel
                v-for="tab in openSubagentTabs"
                :key="tab.id"
                :name="tab.id"
            >
                <SessionContent
                    :session-id="tab.agentId"
                    :parent-session-id="sessionId"
                    :project-id="projectId"
                />
            </wa-tab-panel>

            <!-- Tool panels live in the host below (teleported); here are only their center targets -->
            <wa-tab-panel v-if="showInCenter('files')" name="files">
                <div :ref="centerTargetSetters.files" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="isToolTabPresent('git') && showInCenter('git')" name="git">
                <div :ref="centerTargetSetters.git" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="showInCenter('terminal')" name="terminal">
                <div :ref="centerTargetSetters.terminal" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="isToolTabPresent('tasks') && showInCenter('tasks')" name="tasks">
                <div :ref="centerTargetSetters.tasks" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="isToolTabPresent('plan') && showInCenter('plan')" name="plan">
                <div :ref="centerTargetSetters.plan" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="isToolTabPresent('artifacts') && showInCenter('artifacts')" name="artifacts">
                <div :ref="centerTargetSetters.artifacts" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="isToolTabPresent('orchestration') && showInCenter('orchestration')" name="orchestration">
                <div :ref="centerTargetSetters.orchestration" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="isToolTabPresent('workflows') && showInCenter('workflows')" name="workflows">
                <div :ref="centerTargetSetters.workflows" class="layout-center-target"></div>
            </wa-tab-panel>
            <wa-tab-panel v-if="showInCenter('browser')" name="browser">
                <div :ref="centerTargetSetters.browser" class="layout-center-target"></div>
            </wa-tab-panel>
        </TabBar>

        <!-- Fixed nav cluster: [Layout menu ▾] [Maximize]. NOT slotted into the tab strip — the
             strip's nav scrolls, and these must stay visible without covering a tab. A sibling of
             the TabBar, absolutely positioned over the bar's right end within SessionLayout's
             .center-slot; the cluster's measured width is reserved as ::part(nav) margin so the
             tabs and scroll chevrons stop before it, and the bar's measured height sizes it so
             its bottom border continues the tabs' track. The layout menu (Save + Select) shows
             whenever docking is available; Maximize only when not single pane. Hidden entirely in
             the mobile tab strip — no layout there. -->
        <div
            v-if="!layoutTabsMode"
            ref="layoutNavClusterEl"
            class="layout-nav-cluster"
            :class="{ 'tabnav-dimmed': !isCenterRouteActive }"
            :style="centerNavBarHeight ? { height: `${centerNavBarHeight}px` } : null"
        >
            <LayoutMenu :has-docks="hasDocks" :scope-defaults="layoutScopeDefaults" @save="onOpenSaveLayout" @select="onSelectLayout" @manage="onManageLayouts" />

            <!-- While maximized the button wears a loud brand-accent fill (the docks are hidden and
                 this is the only way back); plain otherwise so the maximize affordance stays quiet. -->
            <wa-button
                v-if="hasDocks"
                class="layout-winbtn reduced-height"
                :variant="isCenterMaximized ? 'brand' : 'neutral'"
                :appearance="isCenterMaximized ? 'accent' : 'plain'"
                size="small"
                :title="isCenterMaximized ? 'Restore (Alt+Shift+Enter)' : 'Maximize main area (Alt+Shift+Enter)'"
                :aria-label="isCenterMaximized ? 'Restore' : 'Maximize main area'"
                @click.stop="isCenterMaximized ? onLayoutRestoreMaximized() : onCenterMaximize()"
            >
                <wa-icon :name="isCenterMaximized ? 'compress' : 'expand'"></wa-icon>
            </wa-button>
        </div>
        </SessionLayout>

        <!-- Session not found (backend returned 404) -->
        <div v-else-if="sessionLoadError === 'not-found'" class="empty-state">
            <wa-callout variant="warning" size="small">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                Session not found
            </wa-callout>
        </div>

        <!-- Session load failed (network / server error) -->
        <div v-else-if="sessionLoadError === 'error'" class="empty-state">
            <wa-callout variant="danger" size="small">
                <wa-icon slot="icon" name="triangle-exclamation"></wa-icon>
                Failed to load session
            </wa-callout>
        </div>

        <!-- Loading state -->
        <div v-else class="empty-state">
            <wa-spinner></wa-spinner>
            <span>Loading session...</span>
        </div>

        <!-- Save-layout dialog (opened from the tab nav's Save button) -->
        <LayoutSaveDialog v-if="session" ref="layoutSaveDialogRef" :intention="currentLayoutTemplate" :scopes="layoutSaveScopes" />

        <!-- Catalog manager (rename / delete + reassignment), opened from the layout menu's "Manage…" -->
        <LayoutManagerDialog v-if="session" ref="layoutManagerDialogRef" />

        <!-- Tool panels: mounted once here, teleported to their center slot, dock region, or overlay.
             Moving a tab between docks just retargets its Teleport — the instance is never re-mounted. -->
        <div v-if="session" class="layout-panel-host" aria-hidden="true">
            <Teleport :to="toolTarget('files')" :disabled="!toolTarget('files')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('files')">
                    <FilesPanel
                        ref="filesPanelRef"
                        :project-id="session?.project_id"
                        :session-id="session?.id"
                        :git-directory="session?.git_directory"
                        :session-cwd="session?.cwd"
                        :project-git-root="store.getProject(session?.project_id)?.git_root"
                        :project-directory="store.getProject(session?.project_id)?.directory"
                        :route-root-key="activeTabId === 'files' ? filesRouteRootKey : undefined"
                        :route-file-path="activeTabId === 'files' ? filesRouteFilePath : undefined"
                        :route-owner="ownsRoute('files')"
                        :active="isActive && isToolTabShown('files')"
                        :focus-request="panelFocusRequests.files"
                        :is-draft="session?.draft === true"
                        :frame-elevated="filesFrameElevated"
                        @navigate="onFilesNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="isToolTabPresent('git')" :to="toolTarget('git')" :disabled="!toolTarget('git')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('git')">
                    <GitPanel
                        ref="gitPanelRef"
                        :project-id="session?.project_id"
                        :session-id="session?.id"
                        :git-directory="session?.git_directory"
                        :project-git-root="store.getProject(session?.project_id)?.git_root"
                        :initial-branch="session?.git_branch || ''"
                        :route-root-key="activeTabId === 'git' ? gitRouteRootKey : undefined"
                        :route-commit-ref="activeTabId === 'git' ? gitRouteCommitRef : undefined"
                        :route-file-path="activeTabId === 'git' ? gitRouteFilePath : undefined"
                        :route-owner="ownsRoute('git')"
                        :active="isActive && isToolTabShown('git')"
                        :focus-request="panelFocusRequests.git"
                        :is-draft="session?.draft === true"
                        @navigate="onGitNavigate"
                    />
                </div>
            </Teleport>

            <Teleport :to="toolTarget('terminal')" :disabled="!toolTarget('terminal')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('terminal')">
                    <TerminalPanel
                        ref="terminalPanelRef"
                        :context-key="`s:${session.id}`"
                        :session-id="session.id"
                        :project-id="session.project_id"
                        :route-term-index="activeTabId === 'terminal' ? terminalRouteTermIndex : undefined"
                        :route-owner="ownsRoute('terminal')"
                        :active="isActive && isToolTabShown('terminal')"
                        :focus-request="panelFocusRequests.terminal"
                        @navigate="onTerminalNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="isToolTabPresent('tasks')" :to="toolTarget('tasks')" :disabled="!toolTarget('tasks')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('tasks')">
                    <TaskPane :session-id="session.id" />
                </div>
            </Teleport>

            <Teleport v-if="isToolTabPresent('plan')" :to="toolTarget('plan')" :disabled="!toolTarget('plan')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('plan')">
                    <PlanPane
                        ref="planPaneRef"
                        :session-id="session.id"
                        :project-id="session.project_id"
                        :active="isActive && isToolTabShown('plan')"
                        :route-doc-path="planRouteDocPath"
                        :route-owner="ownsRoute('plan')"
                        :frame-elevated="planFrameElevated"
                        @navigate="onPlanNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="isToolTabPresent('artifacts')" :to="toolTarget('artifacts')" :disabled="!toolTarget('artifacts')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('artifacts')">
                    <FilesPanel
                        ref="artifactsPanelRef"
                        :project-id="null"
                        :session-id="null"
                        :api-prefix="'/api'"
                        :external-roots="artifactsExternalRoots"
                        :root-restriction="artifactsDir"
                        :show-root-selector="false"
                        root-label="Session artifacts"
                        :preview-by-default="true"
                        :artifact-bookmark-session-id="session?.id"
                        :artifact-bookmarks="sessionArtifactBookmarks"
                        :artifact-bookmark-project-id="session?.project_id"
                        :artifact-bookmark-main-project-id="artifactMainProjectId"
                        :route-root-key="activeTabId === 'artifacts' ? artifactsRouteRootKey : undefined"
                        :route-file-path="activeTabId === 'artifacts' ? artifactsRouteFilePath : undefined"
                        :route-owner="ownsRoute('artifacts')"
                        :active="isActive && isToolTabShown('artifacts')"
                        :focus-request="panelFocusRequests.artifacts"
                        :frame-elevated="artifactsFrameElevated"
                        @navigate="onArtifactsNavigate"
                    />
                </div>
            </Teleport>

            <Teleport v-if="isToolTabPresent('orchestration')" :to="toolTarget('orchestration')" :disabled="!toolTarget('orchestration')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('orchestration')">
                    <OrchestrationPanel
                        :session-id="session.id"
                        :project-id="session.project_id"
                        :active="isActive && isToolTabShown('orchestration')"
                    />
                </div>
            </Teleport>

            <Teleport v-if="isToolTabPresent('workflows')" :to="toolTarget('workflows')" :disabled="!toolTarget('workflows')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('workflows')">
                    <WorkflowsPane
                        ref="workflowsPaneRef"
                        :session-id="session.id"
                        :project-id="session.project_id"
                        :focus-run-id="workflowFocusRunId"
                        :active="isActive && isToolTabShown('workflows')"
                        @navigate="onWorkflowsNavigate"
                    />
                </div>
            </Teleport>

            <Teleport :to="toolTarget('browser')" :disabled="!toolTarget('browser')">
                <div class="layout-tool-wrap" v-show="layout.isToolPanelVisible('browser')">
                    <BrowserPane
                        :session-id="session.id"
                        :project-id="session?.project_id"
                        :active="isActive && isToolTabShown('browser')"
                        :focus-request="panelFocusRequests.browser"
                        :frame-elevated="browserFrameElevated"
                        @interact="requestPaneFocus('browser')"
                    />
                </div>
            </Teleport>
        </div>
    </div>
</template>

<style scoped>
.session-view {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow: hidden;
    position: relative;
}

.session-view > wa-divider {
    flex-shrink: 0;
}

/* Dockable layout: panels are mounted once in this hidden host, then teleported into the
   center tab-panel / a dock region / the overlay. While here (no target) they stay mounted. */
.layout-panel-host {
    display: none;
}
.layout-center-target,
.layout-tool-wrap {
    flex: 1;
    min-height: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* ═══════════════════════════════════════════════════════════════════════════
   Tab group styles
   ═══════════════════════════════════════════════════════════════════════════ */

.session-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

/* Fixed tab-nav cluster: absolutely positioned over the bar's right end (containing block:
   SessionLayout's .center-slot), outside the nav scroller so it never scrolls away with the
   tabs. Height is measured (see measureCenterNav); the bottom border continues the tabs' track
   with the same tokens WA uses. Dimmed with the bar when the center doesn't own the route. */
.layout-nav-cluster {
    position: absolute;
    top: 0;
    inset-inline-end: 0;
    box-sizing: border-box;
    display: flex;
    align-items: center;
    gap: var(--wa-space-3xs);
    border-bottom: var(--divider-size) solid var(--wa-color-neutral-fill-normal);
    transition: opacity var(--wa-transition-fast, 0.15s) var(--wa-transition-easing, ease);
}
.layout-nav-cluster.tabnav-dimmed {
    opacity: 0.5;
}
/* Reserve the cluster's width at the end of the nav container: the tab strip (and its end
   scroll chevron, positioned at the container's edge) stops before the fixed cluster. */
.session-tabs::part(nav) {
    margin-inline-end: var(--layout-nav-cluster-w, 0px);
}
.layout-winbtn {
    --wa-form-control-padding-inline: 0.3em;
}

.session-tabs::part(tabs) {
    align-items: center;
}
.session-tabs::part(base) {
    height: 100%;
    overflow: hidden;
}

.session-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

/* Direct-child combinator on purpose: this zeroes padding on the center's OWN tab
   panels (main/files/git/…), which must sit edge-to-edge. Without the `>` it was a
   descendant selector and leaked through a teleported pane into a NESTED wa-tab-panel
   — e.g. the Workflows pane's per-run panel, whose `--wa-space-m` padding got wiped in
   the center but survived in a dock (outside .session-tabs). Scoping to direct children
   keeps the center bare while letting nested panes keep their own padding. */
.session-tabs > :deep(wa-tab-panel::part(base)) {
    padding: 0;
}

/* Active-region cue: when a dock owns the route, the center is a non-active region, so its tab bar
   (the nav strip only — not the panels below) is dimmed. The center bar is full opacity whenever the
   route owner is a center tab, including single-pane mode (every tab is a center tab there). */
.session-tabs.tabnav-dimmed::part(nav) {
    opacity: 0.5;
}
.session-tabs::part(nav) {
    transition: opacity var(--wa-transition-fast, 0.15s) var(--wa-transition-easing, ease);
}
/* Double-click the center bar to maximize/restore — only when there are docks (otherwise it's a
   no-op, so no pointer/title is bound either). Pointer signals the bar is interactive. */
.session-tabs.tabbar-maximizable::part(nav) {
    cursor: pointer;
}

/* Active tab panel needs to fill available space and handle overflow */
.session-tabs :deep(wa-tab-panel[active]) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.session-tabs :deep(wa-tab-panel[active])::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}

/* Subagent tab content wrapper */
.subagent-tab-content {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
}

.tab-close-icon {
    aspect-ratio: 1;
    height: 3em;
    margin-right: -1em;
    width: auto;
    font-size: 0.75rem;
    opacity: 0.5;
    cursor: pointer;
    transition: opacity 0.15s ease;
    display: grid;
    place-items: center;
}

.tab-close-icon:hover {
    opacity: 1;
}

.tab-comments-indicator {
    font-size: var(--wa-font-size-xs);
    flex-shrink: 0;
}

.pending-request-indicator {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-s);
    animation: pending-pulse 1.5s ease-in-out infinite;
    flex-shrink: 0;
    align-self: center;
}

@keyframes pending-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.3; }
}


/* ═══════════════════════════════════════════════════════════════════════════
   Empty state
   ═══════════════════════════════════════════════════════════════════════════ */

.empty-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    height: 200px;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-l);
}
</style>
