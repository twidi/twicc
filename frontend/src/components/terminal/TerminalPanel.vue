<script setup>
import { computed, inject, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch, watchEffect } from 'vue'
import { useRoute } from 'vue-router'
import { useSettingsStore } from '../../stores/settings'
import { useDataStore } from '../../stores/data'
import { useTerminalConfigStore } from '../../stores/terminalConfig'
import { useWorkspacesStore } from '../../stores/workspaces'
import { useTerminalTabsStore } from '../../stores/terminalTabs'
import { useTerminalPoolStore } from '../../stores/terminalPool'
import { useTerminalCommandStore } from '../../stores/terminalCommand'
import { sendWsMessage } from '../../composables/useWebSocket'
import { useFocusRetry } from '../../composables/useFocusRetry'
import { toast } from '../../composables/useToast'
import { mapGroupedEntries } from '../../utils/snippetGroups'
import { getUnavailablePlaceholders } from '../../utils/snippetPlaceholders'
import AppTooltip from '../ui/AppTooltip.vue'
import TabBar from '../ui/TabBar.vue'
import AttachTerminalMenu from './AttachTerminalMenu.vue'
import TerminalRenameDialog from './TerminalRenameDialog.vue'
import TerminalExtraKeysBar from './TerminalExtraKeysBar.vue'
import TerminalCombosDialog from './TerminalCombosDialog.vue'
import TerminalSnippetsDialog from './TerminalSnippetsDialog.vue'
import TerminalSnippetSendDialog from './TerminalSnippetSendDialog.vue'
import TextSelectionComment from '../session/detail/TextSelectionComment.vue'

const props = defineProps({
    contextKey: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        default: null,
    },
    projectId: {
        type: String,
        default: null,
    },
    cwd: {
        type: String,
        default: null,
    },
    active: {
        type: Boolean,
        default: false,
    },
    routeTermIndex: {
        default: undefined,
    },
    // Whether this panel currently owns the URL (it is the focused tab). When the
    // dockable layout shows several tool panels at once, only the focused one owns
    // the route; non-owners receive a blanked routeTermIndex. Route reconciliation
    // must run for the owner ONLY — a non-owner would read the blank index as
    // "go to term 0" and reset the visible terminal back to Main. Defaults to true
    // (non-docked: a panel is shown only when active, so it always owns the route).
    routeOwner: {
        type: Boolean,
        default: true,
    },
    // Bumped by the parent (SessionView) to focus the active terminal on a real navigation TOWARD this
    // tab (header click, keyboard, arrival) — never when the layout merely renders the panel. Mirrors the
    // tool tabs' :focus-request; here it focuses the terminal (or its Start/Reconnect overlay button).
    focusRequest: {
        type: Number,
        default: 0,
    },
})
const emit = defineEmits(['navigate'])

const route = useRoute()
const settingsStore = useSettingsStore()
const dataStore = useDataStore()
const terminalConfigStore = useTerminalConfigStore()
const workspacesStore = useWorkspacesStore()
const terminalTabsStore = useTerminalTabsStore()
const poolStore = useTerminalPoolStore()
const insertTextAtCursor = inject('insertTextAtCursor', null)

const session = computed(() => props.sessionId ? dataStore.getSession(props.sessionId) : null)
const resolvedProjectId = computed(() => props.projectId || session.value?.project_id)
// Project id used to SELECT which snippet lists to show: in a worktree session
// we borrow the main repository's workspaces and project-scoped snippets (a
// worktree has none of its own). Placeholders are still resolved against the
// real terminal project (see `buildPlaceholderContext`), not this one.
const snippetListProjectId = computed(() => dataStore.getMainRepoProjectId(resolvedProjectId.value))

// Build a placeholder resolution context for a given snippet.
// For snippets scoped to a project (scope "project:<id>"), use THAT project's data
// for project-related placeholders — even in workspace terminals showing snippets
// from multiple projects. Session is always from props (null for non-session terminals).
function buildPlaceholderContext(snippet) {
    const s = session.value
    // Project used to resolve {project-*} placeholders:
    // - Session/project terminal: always the terminal's own project (e.g. the
    //   worktree itself), even though the displayed snippets may be borrowed
    //   from the main repository's scope.
    // - Workspace/global terminal (no single project): each project-scoped
    //   snippet resolves against its own scope project.
    let pid = resolvedProjectId.value
    if (!pid && snippet?._scope?.startsWith('project:')) {
        pid = snippet._scope.slice('project:'.length)
    }
    const project = pid ? dataStore.getProject(pid) : null
    const projectName = pid ? dataStore.getProjectDisplayName(pid) : null
    return { session: s, project, projectName }
}

// Default context (no snippet-specific project) — used by the send dialog.
const placeholderContext = computed(() => buildPlaceholderContext(null))

// Workspace IDs for snippet scoping:
// - Session/project terminal in a workspace URL: use that workspace
// - Session/project terminal outside workspace: all workspaces containing the project
// - Workspace terminal: use that workspace (extracted from contextKey)
// - Global terminal: empty (global snippets only)
const snippetWorkspaceIds = computed(() => {
    const wsId = route.query.workspace
    if (wsId) return [wsId]
    // Workspace terminal: contextKey is "w:<workspaceId>"
    if (props.contextKey.startsWith('w:')) {
        return [props.contextKey.slice(2)]
    }
    const pid = snippetListProjectId.value
    if (!pid) return []
    return workspacesStore.getWorkspacesForProject(pid).map(ws => ws.id)
})

// Snippets available in current context, with placeholder availability checks.
// For workspace terminals (no single project), get snippets from all workspace projects.
const snippetsForProject = computed(() => {
    let raw
    if (resolvedProjectId.value) {
        // Session or project terminal: snippets for that project (the main repo
        // when the terminal's project is a git worktree).
        raw = terminalConfigStore.getSnippetsForProject(snippetListProjectId.value, snippetWorkspaceIds.value)
    } else if (props.contextKey.startsWith('w:')) {
        // Workspace terminal: merge snippets from all projects in the workspace
        const wsId = props.contextKey.slice(2)
        const projectIds = workspacesStore.getVisibleProjectIds(wsId) || []
        raw = terminalConfigStore.getSnippetsForWorkspace(projectIds, snippetWorkspaceIds.value)
    } else {
        // Global terminal: global snippets only
        raw = terminalConfigStore.getGlobalSnippets()
    }

    return mapGroupedEntries(raw, snippet => {
        const placeholders = snippet.placeholders || []
        if (placeholders.length === 0) return snippet
        // Per-snippet context: project-scoped snippets resolve {project-dir} etc.
        // using their own project's data, not the terminal's project.
        const ctx = buildPlaceholderContext(snippet)
        const unavailable = getUnavailablePlaceholders(placeholders, ctx)
        if (unavailable.length === 0) return snippet
        return {
            ...snippet,
            _disabled: true,
            _disabledReason: `Not available: ${unavailable.map(p => p.label).join(', ')}`,
        }
    })
})

// Whether this session uses tmux (determines if rename is persisted to backend)
const usesTmux = computed(() => {
    if (!settingsStore.isTerminalUseTmux) return false
    const s = session.value
    if (s?.draft || s?.archived) return false
    return true
})

// Dialog refs
const manageCombosDialogRef = ref(null)
const manageSnippetsDialogRef = ref(null)
const snippetSendDialogRef = ref(null)
const renameDialogRef = ref(null)

// --- Pool slots --------------------------------------------------------------
// Terminal instances live in the app-level pool (TerminalPool.vue); this panel
// renders only slot <div>s and publishes them to the pool, which teleports the
// matching instance into the active panel's slot. The pool is keyed by each
// terminal's home identity `${contextKey}#${index}`.
const ownKey = (index) => poolStore.keyFor(props.contextKey, index)
// Attachable ancestor scopes (project / worktree-project / workspace / global).
const isAncestorScope = (ctx) => ctx === 'global' || ctx.startsWith('p:') || ctx.startsWith('w:')
// A terminal must persist across navigation (so it stays connected and
// attachable from a child panel) when it has NO server-side session to re-attach
// to — i.e. a non-tmux terminal of an attachable ancestor scope. tmux terminals
// (re-attachable) and session terminals (never attached) are torn down on
// navigation as before.
const ownPersist = computed(() => isAncestorScope(props.contextKey) && !usesTmux.value)

// Whether ancestor terminals are tmux-backed (global setting). tmux scopes are
// discovered server-side (attachable even when not connected); non-tmux scopes
// are discovered from the pool (only currently-connected instances). Defined here
// (before route reconciliation) so a route can resolve an attached token at mount.
const ancestorsUseTmux = computed(() => settingsStore.isTerminalUseTmux)

// Ordered list of attachable ancestor scopes for this panel. Empty for the
// global panel (it has no parents). Every other panel has at least 'global'.
const ancestorScopes = computed(() => {
    const ctx = props.contextKey
    if (ctx === 'global') return []

    const scopes = []

    // The single project this panel belongs to (session → its project; project
    // panel → that project). Workspace panels own no single project.
    let ownProjectId = null
    if (ctx.startsWith('s:')) {
        ownProjectId = resolvedProjectId.value
    } else if (ctx.startsWith('p:')) {
        ownProjectId = ctx.slice(2)
    }

    if (ownProjectId) {
        const proj = dataStore.getProject(ownProjectId)
        const mainRepoId = dataStore.getMainRepoProjectId(ownProjectId)
        if (ctx.startsWith('s:') && proj?.worktree_of) {
            // Session inside a git worktree: the worktree project itself, then
            // its main repository as "Project".
            scopes.push({ scope: 'worktree', label: 'Worktree', contextKey: `p:${ownProjectId}`, projectId: ownProjectId })
            scopes.push({ scope: 'project', label: 'Project', contextKey: `p:${mainRepoId}`, projectId: mainRepoId })
        } else if (ctx.startsWith('s:')) {
            // Session inside a plain project.
            scopes.push({ scope: 'project', label: 'Project', contextKey: `p:${ownProjectId}`, projectId: ownProjectId })
        } else if (proj?.worktree_of) {
            // Worktree-project panel: only its main repository sits above it.
            scopes.push({ scope: 'project', label: 'Project', contextKey: `p:${mainRepoId}`, projectId: mainRepoId })
        }

        // Workspaces containing the (main-repo) project — one section each.
        const workspaces = workspacesStore.getWorkspacesForProject(mainRepoId)
        const multiple = workspaces.length > 1
        for (const ws of workspaces) {
            scopes.push({
                scope: `workspace:${ws.id}`,
                label: multiple ? `Workspace (${ws.name})` : 'Workspace',
                contextKey: `w:${ws.id}`,
                wsId: ws.id,
                cwd: workspacesStore.getTerminalCwd(ws.id),
            })
        }
    }

    // Global is the ancestor of every non-global panel.
    scopes.push({ scope: 'global', label: 'Global', contextKey: 'global' })
    return scopes
})

// Keys of ancestor terminals whose owner flagged them "auto-attach in children"
// (the @twicc_autoattach tmux user option, surfaced via discovery + the
// terminal_autoattach_changed broadcast). This is the source of truth for the
// panel's NON-detachable (forced) tabs — derived, never written into the pool's
// attachment registry, so toggling the parent flag makes the child tab appear/
// disappear symmetrically. tmux-only (non-tmux scopes carry no backend flag).
// Declared HERE (with ancestorScopes, before route reconciliation) to avoid a
// TDZ: applyRouteTermIndex reads forcedKeys.value during the immediate route watch.
const forcedKeys = computed(() => {
    if (!ancestorsUseTmux.value) return []
    const keys = []
    for (const scope of ancestorScopes.value) {
        for (const index of terminalTabsStore.indices[scope.contextKey] || []) {
            if (terminalTabsStore.isAutoAttach(scope.contextKey, index)) {
                keys.push(poolStore.keyFor(scope.contextKey, index))
            }
        }
    }
    return keys
})
const isForced = (key) => forcedKeys.value.includes(key)

// Declared early (before the slot-publishing watchEffect, which calls
// startModeFor() synchronously during setup). startModeFor reads
// discoveryTimedOut; a `ref` is NOT hoisted like the function is, so declaring it
// down in the "Main-terminal start decision" section caused a TDZ when a panel
// mounts active with no tmux session discovered yet (indices === undefined).
const discoveryTimedOut = ref(false)

// Slot elements by pool key (filled by template function refs).
const slotEls = reactive({})
function setSlotEl(key, el) {
    if (el) slotEls[key] = el
    else delete slotEls[key]
}
// Keys this panel currently publishes (to release them when no longer wanted).
const publishedKeys = new Set()
// Own keys that have had a live instance — used to detect a secondary terminal's
// exit (its descriptor vanishing from the pool while we're active).
const seenOwnKeys = new Set()

const INVALID_ROUTE_TERM_INDEX = -1

// --- Terminal tab management ---
const terminals = ref([{ index: 0, label: 'Main' }])
const activeIndex = ref(0)
const nextIndex = ref(1) // monotonically increasing counter

// --- Attached parent-scope terminals ---
// Terminals borrowed from an ancestor scope (worktree → project → workspace →
// global) and rendered before the panel's own tabs. The attachment registry
// lives in the pool store (so it survives navigation); this panel reads its own
// attachments from there. `activeAttachedKey` holds the pool key of the active
// tab when it is an attached one (else null → an own tab, tracked by
// `activeIndex`, is active). The wa-tab panel name of an attached tab IS its pool
// key (which never starts with "term-", so it's distinguishable from own tabs).
const activeAttachedKey = ref(null)
const pendingRouteTermIndex = ref(null)
// A key the user just detached. Its route token still points at it for a tick
// (the navigate back to an own tab is async), so the route-retry watcher below
// would re-attach it immediately — undoing the detach. Suppress re-attach of this
// key until the route actually leaves its token (or the user re-attaches it).
const justDetachedKey = ref(null)
const unavailableRouteTermIndex = ref(undefined)
let nextNavigationReplace = false
let syncingFromRoute = false
let backendIndicesReady = false

const isRouteTermUnavailable = computed(() =>
    unavailableRouteTermIndex.value !== undefined
    && (backendIndicesReady || unavailableRouteTermIndex.value === INVALID_ROUTE_TERM_INDEX)
)
const fallbackTermIndex = computed(() => findFallbackTermIndex(
    typeof props.routeTermIndex === 'number' ? props.routeTermIndex : activeIndex.value
))
const isActiveAttached = computed(() => activeAttachedKey.value !== null)
// A forced (auto-attached / pinned-in-parent) tab displayed in a child: it's
// driven entirely from its owner level, so the child exposes no lifecycle action
// for it — neither Kill (it isn't ours) nor Detach (auto-attach is owner-controlled).
const isActiveForcedAttached = computed(() => isActiveAttached.value && isForced(activeAttachedKey.value))
// The pool key of the active tab (attached key, or the own tab's home key).
const activeKey = computed(() => activeAttachedKey.value ?? ownKey(activeIndex.value))
// Route value for the active tab: a plain index for own tabs, the attached pool
// key otherwise (encoded to the URL token by buildTerminalRouteParams). Matches
// the space of `props.routeTermIndex` for echo-suppression in the navigate watch.
const currentRouteValue = computed(() =>
    activeAttachedKey.value !== null ? activeAttachedKey.value : activeIndex.value
)
// The route-unavailable callout only applies to own terminals; an explicitly
// activated attached tab is shown even if the URL points at a dead own index.
const showUnavailableState = computed(() => isRouteTermUnavailable.value && !isActiveAttached.value)
const activeTabPanel = computed(() => {
    if (activeAttachedKey.value !== null) return activeAttachedKey.value
    return isRouteTermUnavailable.value ? '__unavailable__' : `term-${activeIndex.value}`
})
const activeApi = computed(() => poolStore.getApi(activeKey.value))
const isActiveMain = computed(() => activeAttachedKey.value === null && activeIndex.value === 0)
const unavailableRouteMessage = computed(() => {
    const v = unavailableRouteTermIndex.value
    if (v === INVALID_ROUTE_TERM_INDEX) return 'Requested terminal is not available.'
    // String value = an attached parent terminal's pool key (internal format).
    if (typeof v === 'string') return 'The attached terminal is no longer available.'
    return `Terminal \`${v}\` is no longer available.`
})

// Flattened toolbar state from the active terminal's API.
// Note: the API lives in the pool store's reactive state, which auto-unwraps the
// refs inside it — so activeApi.value.isConnected returns the boolean, not a Ref.
const tb = reactive({
    get isConnected() { return activeApi.value?.isConnected ?? false },
    get canScrollUp() { return activeApi.value?.canScrollUp ?? false },
    get canScrollDown() { return activeApi.value?.canScrollDown ?? false },
    get paneAlternate() { return activeApi.value?.paneAlternate ?? false },
    get scrollingToEdge() { return activeApi.value?.scrollingToEdge ?? false },
    get hasSelection() { return activeApi.value?.hasSelection ?? false },
    get touchMode() { return activeApi.value?.touchMode ?? 'scroll' },
})

/** Activate an own terminal tab (clears any active attached tab). */
function activateOwnTab(index) {
    activeAttachedKey.value = null
    activeIndex.value = index
}

function createTerminal() {
    const index = nextIndex.value++
    terminals.value.push({ index, label: `Term ${index + 1}` })
    activateOwnTab(index)
}

function findFallbackTermIndex(target = activeIndex.value) {
    const sorted = [...terminals.value].map(t => t.index).sort((a, b) => a - b)
    if (sorted.length === 0) return 0
    const lowerOrEqual = sorted.filter(index => index <= target)
    return lowerOrEqual.length ? lowerOrEqual[lowerOrEqual.length - 1] : sorted[0]
}

function syncActiveIndex(index) {
    syncingFromRoute = true
    // Route reconciliation always lands on an own tab, so leaving an attached tab.
    activeAttachedKey.value = null
    activeIndex.value = index
    nextTick(() => {
        syncingFromRoute = false
    })
}

/** Activate an attached tab from a route reconciliation (no echo navigate). */
function syncActiveAttached(key) {
    syncingFromRoute = true
    activeAttachedKey.value = key
    nextTick(() => {
        syncingFromRoute = false
    })
}

function replaceToTerm(index) {
    emit('navigate', { termIndex: index, replace: true })
    nextNavigationReplace = false
}

/** Remove a terminal tab (idempotent — no-op if already removed). */
function removeTerminalTab(index) {
    if (index === 0) return // main terminal tab is permanent
    const idx = terminals.value.findIndex(t => t.index === index)
    if (idx === -1) return
    terminals.value.splice(idx, 1)
    if (activeIndex.value === index) {
        const prevTerminal = terminals.value[Math.max(0, idx - 1)]
        nextNavigationReplace = false
        activeIndex.value = prevTerminal?.index ?? 0
    }
    // Release the pool slot (unmounts the instance if nothing else references it).
    const key = ownKey(index)
    seenOwnKeys.delete(key)
    publishedKeys.delete(key)
    poolStore.clearSlot(key)
    // Eagerly remove from store so that syncTerminalsFromBackend doesn't
    // re-add this tab before the backend's terminal_killed broadcast arrives.
    terminalTabsStore.removeIndex(props.contextKey, index)
}

/** Kill a secondary terminal: send WS message to clean up tmux + remove tab. */
function killTerminal(index) {
    if (index === 0) return
    sendWsMessage({
        type: 'kill_terminal',
        terminal_context: props.contextKey,
        terminal_index: index,
    })
    removeTerminalTab(index)
}

function onTerminalTabShow(event) {
    const panelName = event.detail?.name
    if (!panelName || panelName === '__unavailable__') return
    if (panelName.startsWith('term-')) {
        activeAttachedKey.value = null
        activeIndex.value = parseInt(panelName.slice(5), 10)
    } else {
        // Attached tab — its wa-tab panel name is the pool key.
        activeAttachedKey.value = panelName
    }
}

// --- Terminal tab rename ---

/** Default label for a terminal index (used when no custom label is set). */
function defaultLabel(index) {
    return index === 0 ? 'Main' : `Term ${index + 1}`
}

/** Open the rename dialog for a terminal tab. */
function openRenameDialog(index) {
    const term = terminals.value.find(t => t.index === index)
    if (!term) return
    renameDialogRef.value?.open(index, term.label, defaultLabel(index))
}

/** Handle double-click on a tab: open rename dialog for that tab. */
function onTabDblClick(index) {
    openRenameDialog(index)
}

/** Handle save from the rename dialog. */
function handleRename(index, label) {
    const term = terminals.value.find(t => t.index === index)
    if (!term) return

    // Update local label immediately (optimistic)
    term.label = label || defaultLabel(index)

    // For tmux sessions, persist to backend
    if (usesTmux.value) {
        sendWsMessage({
            type: 'rename_terminal',
            terminal_context: props.contextKey,
            terminal_index: index,
            label,
        })
    }
}

// --- Snippet helpers ---

const SNIPPET_LABEL_MAX_LENGTH = 30

/**
 * Send a snippet to a terminal API, reconnecting first if the terminal
 * is disconnected. If already connected, sends immediately.
 */
function sendSnippetToApi(api, snippet) {
    if (api.isConnected) {
        api.handleSnippetPress(snippet)
        return
    }
    // Terminal is disconnected — reconnect and send once ready
    api.reconnect()
    const stopWatch = watch(
        () => api.isConnected,
        (connected) => {
            if (connected) {
                stopWatch()
                api.handleSnippetPress(snippet)
            }
        },
    )
    setTimeout(() => stopWatch(), 10000)
}

// --- Edit-before-send dialog ---

function handleSnippetEditSend(snippet) {
    snippetSendDialogRef.value?.open(snippet)
}

function handleSnippetSendDialogSend(editedSnippet, target) {
    handleSnippetSendTo(editedSnippet, target)
}

// --- Snippet dispatch (main button click) ---

function handleSnippetPress(snippet) {
    if (snippet.openInNewTab) {
        handleSnippetSendTo(snippet, 'new')
        return
    }
    const api = activeApi.value
    if (!api) return
    sendSnippetToApi(api, snippet)
}

// --- Send snippet to a specific terminal target ---

function handleSnippetSendTo(snippet, target) {
    if (target === 'new') {
        // Clean the snippet label (same sanitization as TerminalRenameDialog)
        const label = snippet.label.trim().slice(0, SNIPPET_LABEL_MAX_LENGTH)

        // Create a new terminal tab and name it after the snippet
        const newIndex = nextIndex.value
        createTerminal()
        const term = terminals.value.find(t => t.index === newIndex)
        if (term && label) {
            term.label = label
        }

        // Once the PTY chain is fully wired and the shell prompt has rendered:
        // send the snippet and persist the label to tmux. We wait on `isReady`
        // (not `isConnected`) because the latter fires on `websocket.accept`,
        // before tmux has attached the pane — input sent that early gets eaten.
        const stopWatch = watch(
            () => poolStore.getApi(ownKey(newIndex))?.isReady,
            (ready) => {
                if (ready) {
                    stopWatch()
                    poolStore.getApi(ownKey(newIndex))?.handleSnippetPress?.(snippet)
                    if (usesTmux.value && label) {
                        sendWsMessage({
                            type: 'rename_terminal',
                            terminal_context: props.contextKey,
                            terminal_index: newIndex,
                            label,
                        })
                    }
                }
            },
            { immediate: true },
        )
        // Safety: stop watching after 10 seconds to avoid leaks
        setTimeout(() => stopWatch(), 10000)
    } else {
        const targetIndex = Number(target)
        const api = poolStore.getApi(ownKey(targetIndex))
        if (api) {
            sendSnippetToApi(api, snippet)
            activateOwnTab(targetIndex)
        }
    }
}

// --- External "launch a command in this terminal context" requests ---
//
// Other components (e.g. the Claude CLI not-authenticated toast) queue a
// command via the terminalCommand store. We pick it up here, always open a
// fresh new tab, make it the active (route-driven) tab so it actually starts,
// and send the command once the tab is ready.

const terminalCommandStore = useTerminalCommandStore()

watch(
    () => terminalCommandStore.pending[props.contextKey],
    (entry) => {
        if (!entry) return

        // Defer past this tick. The callers queue the command and then navigate
        // to the bare ``…/terminal`` URL (no termIndex), so the panel mounts with
        // a pending entry. The route-reconciliation watcher below runs right after
        // this one and, seeing no termIndex, forces activeIndex back to 0 — which
        // would clobber a tab created/activated synchronously here. Running on
        // nextTick lets that reset happen first; we then create the tab and drive
        // the route to it, so our navigation is the one that wins.
        nextTick(() => {
            const targetIndex = nextIndex.value
            createTerminal()

            // Make the new tab the active one *through the route* (the source of
            // truth for activeIndex). This is what flips TerminalInstance's
            // ``active`` prop and triggers start(); without it the WS never opens,
            // ``isReady`` never flips, and the command would only fire on a manual
            // tab click. ``replace`` so we don't stack an extra history entry on
            // top of the caller's push to the terminal view.
            emit('navigate', { termIndex: targetIndex, replace: true })

            // Wait on `isReady` (full PTY/tmux/shell chain alive) rather than
            // `isConnected` (WS accepted) so the command doesn't land before the
            // tmux pane is wired or the shell has rendered its prompt.
            const stopWatch = watch(
                () => poolStore.getApi(ownKey(targetIndex))?.isReady,
                (ready) => {
                    if (!ready) return
                    stopWatch()
                    poolStore.getApi(ownKey(targetIndex))?.handleSnippetPress?.({
                        snippet: entry.snippet,
                        appendEnter: entry.appendEnter,
                        placeholders: [],
                    })
                    terminalCommandStore.take(props.contextKey)
                },
                { immediate: true },
            )
            setTimeout(() => stopWatch(), 10000)
        })
    },
    { immediate: true },
)

// Push the active tab into the URL (own index, or attached pool key → token).
watch(currentRouteValue, (val) => {
    if (!props.active) return
    if (syncingFromRoute) return
    if (pendingRouteTermIndex.value != null) return
    if (val === props.routeTermIndex) return
    emit('navigate', {
        termIndex: val,
        replace: nextNavigationReplace,
    })
    nextNavigationReplace = false
})

/**
 * Attach an ancestor terminal identified by its pool key, materialising the
 * descriptor from the matching ancestor scope (used when a route points at an
 * attached terminal that isn't attached yet — deep link / back-forward after
 * detach). Returns false if no ancestor scope matches the key's context.
 */
function attachKeyFromRoute(key) {
    const hash = key.lastIndexOf('#')
    if (hash === -1) return false
    const contextKey = key.slice(0, hash)
    const index = Number.parseInt(key.slice(hash + 1), 10)
    if (!Number.isInteger(index)) return false
    const scope = ancestorScopes.value.find(s => s.contextKey === contextKey)
    if (!scope) return false
    // Only attach if the requested terminal actually EXISTS — never spawn a
    // phantom from a stale URL. tmux: it must already be live in the pool or
    // listed by discovery; non-tmux: it must be live in the pool. (For an
    // undiscovered tmux ancestor we kick off discovery and a watcher retries.)
    if (ancestorsUseTmux.value) {
        if (!poolStore.descriptors[key] && !(terminalTabsStore.indices[contextKey] || []).includes(index)) {
            return false
        }
    } else if (!poolStore.descriptors[key]) {
        return false
    }
    poolStore.attach(props.contextKey, key, {
        contextKey,
        index,
        projectId: scope.projectId ?? null,
        sessionId: null,
        cwd: scope.cwd ?? null,
        startMode: 'auto',
        persist: !ancestorsUseTmux.value,
    })
    return true
}

function applyRouteTermIndex(target) {
    if (!props.active) return

    // Attached parent-scope terminal (route token decoded to a pool key).
    if (typeof target === 'string') {
        pendingRouteTermIndex.value = null
        if (poolStore.attachmentsFor(props.contextKey).includes(target)
            || forcedKeys.value.includes(target)
            || attachKeyFromRoute(target)) {
            unavailableRouteTermIndex.value = undefined
            syncActiveAttached(target)
        } else {
            // Not attachable yet (terminal not found). For tmux the ancestor's
            // list may simply be undiscovered — fetch it so the retry watcher can
            // resolve us once it (and any live instance) is known.
            if (ancestorsUseTmux.value) requestAncestorDiscovery()
            unavailableRouteTermIndex.value = target
            syncActiveIndex(findFallbackTermIndex(0))
        }
        return
    }

    if (target === undefined) {
        pendingRouteTermIndex.value = null
        unavailableRouteTermIndex.value = undefined
        if (props.routeTermIndex !== 0) {
            syncActiveIndex(0)
            replaceToTerm(0)
        } else {
            syncActiveIndex(0)
        }
        return
    }

    if (target === null) {
        pendingRouteTermIndex.value = null
        unavailableRouteTermIndex.value = INVALID_ROUTE_TERM_INDEX
        syncActiveIndex(findFallbackTermIndex(0))
        return
    }

    if (terminals.value.some(term => term.index === target)) {
        pendingRouteTermIndex.value = null
        unavailableRouteTermIndex.value = undefined
        syncActiveIndex(target)
        return
    }

    if (!backendIndicesReady) {
        pendingRouteTermIndex.value = target
        unavailableRouteTermIndex.value = undefined
        syncActiveIndex(findFallbackTermIndex(target))
        return
    }

    pendingRouteTermIndex.value = null
    unavailableRouteTermIndex.value = target
    syncActiveIndex(findFallbackTermIndex(target))
}

watch(
    () => [props.active, props.routeTermIndex, props.routeOwner],
    ([active, target]) => {
        if (!active || !props.routeOwner) return
        applyRouteTermIndex(target)
    },
    { immediate: true },
)

// Retry attached-route resolution when the inputs it needs become available:
// ancestor scopes (data loaded late), the source instance going live (non-tmux),
// or tmux discovery listing it. Lets a browser-history landing on an attachment
// URL auto-(re)attach as soon as the target terminal exists. No-op once the
// target is attached (so it never loops).
watch(
    () => {
        const t = props.routeTermIndex
        if (typeof t !== 'string') return undefined
        const hash = t.lastIndexOf('#')
        if (hash === -1) return undefined
        const ctx = t.slice(0, hash)
        return [
            ancestorScopes.value.length,
            poolStore.descriptors[t] ? 1 : 0,
            (terminalTabsStore.indices[ctx] || []).length,
        ].join(':')
    },
    () => {
        const target = props.routeTermIndex
        if (typeof target === 'string'
            && props.active && props.routeOwner
            && target !== justDetachedKey.value
            && activeAttachedKey.value !== target
            && !poolStore.attachmentsFor(props.contextKey).includes(target)) {
            applyRouteTermIndex(target)
        }
    },
)

// Once the route actually leaves a just-detached key's token, the re-attach
// suppression is no longer needed — drop it so the key can be re-attached later.
watch(() => props.routeTermIndex, (r) => {
    if (justDetachedKey.value !== null && r !== justDetachedKey.value) justDetachedKey.value = null
})

// Focus the active terminal — its xterm, or the Start/Reconnect overlay button when no live terminal —
// for an activation gesture. Routed through the shared focus-retry pump (like the tool tabs): a single
// .focus() is unreliable here because switching a sub-tab fires a route claim + navigate whose reveal
// frames steal focus right after, and a keyboard tab switch flips props.active before the panel is shown.
// The pump re-asserts each frame until focus holds (focusContent returns whether it landed) — and since
// it re-evaluates the target each frame, it follows the state as it transitions (connecting → connected).
// `showUnavailableState` (not isRouteTermUnavailable) is the right gate: an active ATTACHED tab is shown
// and focusable even when the own-route index is unavailable.
const requestTerminalFocus = useFocusRetry()
function focusActiveTerminal() {
    requestTerminalFocus(() => {
        if (showUnavailableState.value) return true // nothing focusable — satisfy the pump so it stops
        const api = activeApi.value
        if (!api) return null // active TerminalInstance not registered yet — wait for it to appear
        return api.focusContent?.() ?? false
    })
}

// Switching terminal sub-tabs (activeIndex change while the panel is shown) focuses the new terminal.
watch(activeIndex, () => {
    if (!props.active) return
    focusActiveTerminal()
})

// A real navigation toward the terminal tab (header click, keyboard, arrival) bumps focusRequest →
// focus the active terminal. Deliberately driven by this explicit-intent signal, NOT by props.active
// (mere visibility), so a docked terminal merely rendered never steals focus. No props.active guard
// here: the bump fires alongside the route change, which propagates async, so active may still be false
// when this runs — the parent only bumps when navigating TOWARD the terminal, and the retry pump waits
// for the panel to become focusable (focusContent no-ops while it's display:none), so it lands correctly.
watch(() => props.focusRequest, () => {
    focusActiveTerminal()
})

// --- Toolbar action helpers (delegate to activeApi) ---

function handleScrollToEdge(direction) {
    const api = activeApi.value
    if (!api) return
    api.scrollingToEdge ? api.cancelScrollToEdge() : api.scrollToEdge(direction)
}

function handleTouchModeChange(event) {
    const api = activeApi.value
    if (!api) return
    api.touchMode = event.target.checked ? 'select' : 'scroll'
}

function handleTouchModeToggle() {
    const api = activeApi.value
    if (!api) return
    api.touchMode = api.touchMode === 'scroll' ? 'select' : 'scroll'
}

function handleCopy() {
    activeApi.value?.copySelection?.()
}

// --- Text selection comment ---

const terminalCommentRef = ref(null)
const terminalCommentText = ref('')
const terminalCommentPosition = ref(null)
const commentButtonRef = ref(null)

function handleComment() {
    const api = activeApi.value
    if (!api) return
    const text = api.getSelectionText?.()
    if (!text) return

    // Position below the comment button
    const btn = commentButtonRef.value
    if (btn) {
        const rect = btn.getBoundingClientRect()
        terminalCommentPosition.value = {
            top: rect.bottom,
            left: rect.left + rect.width / 2,
            above: false,
        }
    }
    terminalCommentText.value = text
}

function closeTerminalComment() {
    terminalCommentPosition.value = null
    terminalCommentText.value = ''
}

function handlePaste() {
    activeApi.value?.handleExtraKeyPaste?.()
}

function handleDisconnect() {
    // Send Ctrl+D (EOF) to the active terminal; the shell exits naturally and the
    // backend sends pty_exited. Used for the Main, whose tab stays with a reconnect
    // overlay. Secondaries are killed via handleKillOrDisconnect → killTerminal.
    activeApi.value?.disconnect?.()
}

/**
 * Toolbar danger button. The Main is disconnected gracefully (Ctrl+D; its tab
 * stays with a reconnect overlay). A secondary is killed outright via killTerminal
 * — tmux kill-session + synchronous tab removal — which is reliable even when a
 * foreground program would swallow the Ctrl+D, and notifies other devices. Only
 * ever reached for own tabs (attached tabs use Detach).
 */
function handleKillOrDisconnect() {
    if (isActiveMain.value) {
        handleDisconnect()
    } else {
        killTerminal(activeIndex.value)
    }
}

// --- Attach parent-scope terminals ------------------------------------------
// `ancestorScopes` / `ancestorsUseTmux` are defined earlier (in the pool-slots
// section), before the route reconciliation — so a route can resolve an attached
// token on first mount. Here we build the menu sections and manage attachments
// (which live in the pool store so they survive navigation).

// Menu sections: per ancestor scope, the attachable terminals (only scopes that
// currently have at least one are kept). tmux → server-discovered list; non-tmux
// → currently-connected pool instances.
const attachSections = computed(() => {
    const myAttached = poolStore.attachmentsFor(props.contextKey)
    return ancestorScopes.value
        .map((scope) => {
            let entries // [{ index, label }]
            if (ancestorsUseTmux.value) {
                entries = (terminalTabsStore.indices[scope.contextKey] || []).map((index) => ({
                    index,
                    label: terminalTabsStore.getLabel(scope.contextKey, index) || defaultLabel(index),
                }))
            } else {
                entries = poolStore.liveKeysForContext(scope.contextKey)
                    .map((key) => poolStore.descriptors[key])
                    .map((d) => ({ index: d.index, label: d.label || defaultLabel(d.index) }))
                    .sort((a, b) => a.index - b.index)
            }
            const items = entries.map(({ index, label }) => {
                const key = poolStore.keyFor(scope.contextKey, index)
                return {
                    key,
                    contextKey: scope.contextKey,
                    index,
                    scopeLabel: scope.label,
                    projectId: scope.projectId ?? null,
                    cwd: scope.cwd ?? null,
                    label,
                    attached: myAttached.includes(key) || forcedKeys.value.includes(key),
                }
            })
            return { scope: scope.scope, label: scope.label, items }
        })
        .filter((section) => section.items.length > 0)
})

// Attached tabs for this panel (read from the pool), with a reactive display
// label "<Scope>: <terminal name>" (the name follows an ancestor-level rename).
const attachedTabs = computed(() => {
    const manual = poolStore.attachmentsFor(props.contextKey)
    // Forced first (in ancestor order), then any manual-only extras. A key that
    // is both forced and manual renders once, as forced (non-detachable).
    const orderedKeys = [...forcedKeys.value, ...manual.filter((k) => !forcedKeys.value.includes(k))]
    return orderedKeys
        .map((key) => {
            const forced = isForced(key)
            // Prefer the live pool descriptor; for a forced key whose owner panel
            // was never opened this session, synthesize from the ancestor scope.
            const d = poolStore.descriptors[key]
            const hash = key.lastIndexOf('#')
            const contextKey = key.slice(0, hash)
            const index = Number.parseInt(key.slice(hash + 1), 10)
            const scope = ancestorScopes.value.find((s) => s.contextKey === contextKey)
            if (!d && !scope) return null // unknown ancestor (data not loaded yet) → skip
            const projectId = d?.projectId ?? scope?.projectId ?? null
            const cwd = d?.cwd ?? scope?.cwd ?? null
            const scopeLabel = scope ? scope.label : contextKey
            // Label source MUST match the original: tmux → store label (cross-device,
            // from discovery); non-tmux → the live descriptor's client-side label
            // (terminalTabsStore.labels is fed only by tmux-only messages, so
            // getLabel() is '' in non-tmux mode). Forced tabs are tmux-only.
            const foreignLabel = (ancestorsUseTmux.value
                ? terminalTabsStore.getLabel(contextKey, index)
                : (d?.label || terminalTabsStore.getLabel(contextKey, index))) || defaultLabel(index)
            return {
                key, contextKey, index, projectId, cwd, forced, scopeLabel,
                displayLabel: `${scopeLabel}: ${foreignLabel}`,
            }
        })
        .filter(Boolean)
})

/** Discover the terminals of every tmux ancestor scope (called when the menu
 *  opens). Non-tmux scopes are read live from the pool — nothing to fetch. */
function requestAncestorDiscovery() {
    if (!ancestorsUseTmux.value || !dataStore.wsConnected) return
    for (const scope of ancestorScopes.value) {
        sendWsMessage({ type: 'list_terminals', terminal_context: scope.contextKey })
    }
}

// Proactively discover ancestor terminals (and thus their AutoAttach flags) once
// the panel is active, so forced tabs appear without opening the attach menu.
// Only fetch scopes still unknown to the store (the `=== undefined` guard prevents
// spam); the terminal_autoattach_changed / _renamed / _killed broadcasts keep them
// fresh afterwards. The ancestor-key dep re-runs it if scopes load late.
watch(
    [() => props.active, () => dataStore.wsConnected, () => ancestorScopes.value.map((s) => s.contextKey).join('|')],
    () => {
        if (!props.active || !ancestorsUseTmux.value || !dataStore.wsConnected) return
        for (const scope of ancestorScopes.value) {
            if (terminalTabsStore.indices[scope.contextKey] === undefined) {
                sendWsMessage({ type: 'list_terminals', terminal_context: scope.contextKey })
            }
        }
    },
    { immediate: true },
)

/** Attach an ancestor terminal, or focus it if already attached. */
function attachTerminal(item) {
    // An explicit (re)attach overrides any pending detach-suppression for this key.
    if (justDetachedKey.value === item.key) justDetachedKey.value = null
    poolStore.attach(props.contextKey, item.key, {
        contextKey: item.contextKey,
        index: item.index,
        projectId: item.projectId,
        sessionId: null, // ancestors are never sessions
        cwd: item.cwd,
        startMode: 'auto',
        label: item.label,
        persist: !ancestorsUseTmux.value,
    })
    activeAttachedKey.value = item.key
}

/** Detach an attached tab (never kills the source terminal). */
function detachTerminal(key) {
    const before = poolStore.attachmentsFor(props.contextKey)
    const idx = before.indexOf(key)
    poolStore.detach(props.contextKey, key)
    // Block the route-retry watcher from re-attaching this key off the stale URL
    // token before the navigate-away lands (cleared once the route leaves it).
    justDetachedKey.value = key
    if (activeAttachedKey.value === key) {
        const remaining = poolStore.attachmentsFor(props.contextKey)
        activeAttachedKey.value = remaining[idx] ?? remaining[idx - 1] ?? null
    }
}

function handleDetach() {
    // Forced (auto-attached) tabs cannot be detached from a child — only the
    // owning ancestor panel can turn the flag off.
    if (activeAttachedKey.value !== null && !isForced(activeAttachedKey.value)) {
        detachTerminal(activeAttachedKey.value)
    }
}

// --- AutoAttach-in-children (owner side) ------------------------------------
// Shown only when ALL hold: the panel's own scope can be an ancestor of others
// (global / p: / w:); it is tmux-backed (the flag is a tmux user option); and a
// real tmux SESSION exists for the active tab. The last point matters: the flag
// lives on the tmux session, so there is nothing to pin for a Main that was never
// started (its "Start terminal" state) or any index without a live tmux session.
// `terminalTabsStore.indices` is the discovered set of live tmux sessions here —
// the same source startModeFor() trusts.
const canBroadcast = computed(() =>
    isAncestorScope(props.contextKey)
    && usesTmux.value
    && (terminalTabsStore.indices[props.contextKey] || []).includes(activeIndex.value))
// AutoAttach flag of the active OWN tab (the toggle's target).
const activeOwnAutoAttach = computed(() =>
    terminalTabsStore.isAutoAttach(props.contextKey, activeIndex.value))

function toggleAutoAttach() {
    if (isActiveAttached.value) return
    const next = !activeOwnAutoAttach.value
    // Optimistic local write; the broadcast confirms cross-device.
    terminalTabsStore.setAutoAttach(props.contextKey, activeIndex.value, next)
    sendWsMessage({
        type: 'set_terminal_autoattach',
        terminal_context: props.contextKey,
        terminal_index: activeIndex.value,
        enabled: next,
    })
}

// Keep `activeAttachedKey` valid: when its tab leaves the attachment list (manual
// detach, or the pool auto-removed it because its source terminal exited), move
// to another attached tab or back to the own tabs.
watch(
    () => poolStore.attachmentsFor(props.contextKey).slice(),
    (keys) => {
        // A forced (auto-attached) key is never in `attachments`, so guard against
        // it here — otherwise an unrelated manual attach/detach would yank an
        // active forced tab off-screen.
        if (activeAttachedKey.value !== null
            && !keys.includes(activeAttachedKey.value)
            && !forcedKeys.value.includes(activeAttachedKey.value)) {
            activeAttachedKey.value = keys.length ? keys[keys.length - 1] : null
        }
    },
)

// Focus the attached terminal when it becomes the active tab — through the same
// focus-retry pump as own tabs (switching to an attached tab changes
// activeAttachedKey, not activeIndex, so the activeIndex watcher above misses it).
watch(activeAttachedKey, (key) => {
    if (key === null) return
    focusActiveTerminal()
})

// --- Pool slot publishing ----------------------------------------------------
// While this panel is active, publish a slot for each tab (own + attached) so the
// pool teleports the matching instance into it. All own tabs get a slot (only the
// active one is visible — preserves the no-resize-flash behavior); the pool keeps
// inactive ones alive in place. When the panel goes inactive or unmounts we
// release every slot (un-attached own terminals are then torn down; attached and
// tmux ones persist server-side / in the pool).
watchEffect(() => {
    if (!props.active) {
        for (const key of publishedKeys) poolStore.clearSlot(key, slotEls[key])
        publishedKeys.clear()
        seenOwnKeys.clear()
        return
    }
    const wanted = new Set()
    for (const t of terminals.value) {
        const key = ownKey(t.index)
        // A previously-published own terminal whose pool descriptor has vanished
        // means its PTY exited (Ctrl+D, `exit`, kill, crash) — onExit dropped it.
        // Re-materialising it via setSlot would resurrect the dead shell (and, in
        // tmux, spawn a brand-new session). Skip it so the exit watcher below
        // removes the tab. The Main keeps its descriptor in onExit (reconnect
        // overlay), so it never matches this guard.
        if (seenOwnKeys.has(key) && !poolStore.descriptors[key]) continue
        wanted.add(key)
        seenOwnKeys.add(key)
        poolStore.setSlot(key, {
            contextKey: props.contextKey,
            index: t.index,
            projectId: resolvedProjectId.value,
            sessionId: props.sessionId,
            cwd: props.cwd,
            startMode: startModeFor(t.index),
            label: t.label,
            persist: ownPersist.value,
        }, slotEls[key], activeAttachedKey.value === null && t.index === activeIndex.value)
    }
    for (const tab of attachedTabs.value) {
        wanted.add(tab.key)
        const isActive = activeAttachedKey.value === tab.key
        if (tab.forced && !poolStore.descriptors[tab.key]) {
            // Forced tab whose owner panel was never opened this session — create
            // the instance here. setSlotTarget would early-return on the missing
            // descriptor (blank tab); setSlot materializes it. persist:false → tmux
            // is re-attachable, so a GC on navigation is fine (rebuilt from the flag).
            poolStore.setSlot(tab.key, {
                contextKey: tab.contextKey,
                index: tab.index,
                projectId: tab.projectId,
                sessionId: null,
                cwd: tab.cwd,
                startMode: 'auto',
                label: tab.displayLabel,
                persist: false,
            }, slotEls[tab.key], isActive)
        } else {
            // Descriptor owned by the home panel (manual) or already materialized
            // (forced) — only relocate it here.
            poolStore.setSlotTarget(tab.key, slotEls[tab.key], isActive)
        }
    }
    for (const key of [...publishedKeys]) {
        if (!wanted.has(key)) {
            poolStore.clearSlot(key, slotEls[key])
            publishedKeys.delete(key)
        }
    }
    for (const key of wanted) publishedKeys.add(key)
})

// Close an own secondary tab when its instance exits (the pool drops its
// descriptor on PTY exit). Guarded by `props.active` so navigation/teardown —
// where descriptors legitimately disappear — never closes tabs.
watch(
    () => terminals.value
        .filter((t) => t.index > 0)
        .map((t) => {
            const key = ownKey(t.index)
            // Read the reactive pool descriptor FIRST so this watcher always tracks
            // it. `seenOwnKeys` is a plain (non-reactive) Set; leading the `&&` with
            // it short-circuits when the key isn't seen, dropping descriptors[key]
            // from the deps — then a later PTY-exit deletion never re-fires this
            // watcher and the dead tab is never removed. (seenOwnKeys still gates a
            // real exit vs a navigation teardown; operand order only affects tracking.)
            return { index: t.index, exited: !poolStore.descriptors[key] && seenOwnKeys.has(key) }
        }),
    (list) => {
        if (!props.active) return
        for (const { index, exited } of list) {
            if (exited) removeTerminalTab(index)
        }
    },
    { deep: true },
)

onBeforeUnmount(() => {
    for (const key of publishedKeys) poolStore.clearSlot(key, slotEls[key])
    publishedKeys.clear()
})

// --- Discovery and cross-device sync ---

let discoveryDone = false

function requestTerminalDiscovery() {
    if (!props.active) return
    if (discoveryDone) return
    if (!dataStore.wsConnected) return
    const sent = sendWsMessage({
        type: 'list_terminals',
        terminal_context: props.contextKey,
    })
    if (sent) {
        discoveryDone = true
    }
}

// When the panel first becomes active and the WebSocket is ready, request the
// terminal list from the backend. On direct page loads, the panel can mount
// before the socket is open, so we retry when wsConnected flips to true.
watch(
    [() => props.active, () => dataStore.wsConnected],
    () => {
        requestTerminalDiscovery()
    },
    { immediate: true },
)

// --- Main-terminal start decision -------------------------------------------
// The Main sub-tab (index 0) must NOT auto-create its PTY/tmux just because the terminal is displayed
// (e.g. docked by default) — merely viewing a session would otherwise spawn a tmux session. It ATTACHES
// when a tmux session already exists for it, else shows a "Start" callout (the user connects explicitly).
// Other sub-tabs auto-connect as before. Existence comes from the list_terminals discovery above (main
// WS, no PTY). startMode per index: 'auto' (connect/attach), 'manual' (show Start), 'pending' (discovery
// not back yet) — with a 4s safety net so a dropped discovery never strands the Main on a blank area.
// discoveryTimedOut is declared early (pool-slots section) to avoid a TDZ in the
// slot-publishing watchEffect → startModeFor path.
let discoveryTimer = null
watch(
    () => props.active,
    (active) => {
        if (active && terminalTabsStore.indices[props.contextKey] === undefined && discoveryTimer === null) {
            discoveryTimer = setTimeout(() => { discoveryTimedOut.value = true }, 4000)
        }
    },
    { immediate: true },
)
watch(
    () => terminalTabsStore.indices[props.contextKey],
    (indices) => {
        if (indices !== undefined && discoveryTimer !== null) {
            clearTimeout(discoveryTimer)
            discoveryTimer = null
        }
    },
)
onBeforeUnmount(() => {
    if (discoveryTimer !== null) clearTimeout(discoveryTimer)
})

function startModeFor(index) {
    if (index !== 0) return 'auto'
    const indices = terminalTabsStore.indices[props.contextKey]
    if (indices === undefined) return discoveryTimedOut.value ? 'manual' : 'pending'
    return indices.includes(0) ? 'auto' : 'manual'
}

// Watch the terminalTabsStore for backend terminal updates
watch(
    () => terminalTabsStore.indices[props.contextKey],
    (backendIndices, oldIndices) => {
        if (!backendIndices) return
        backendIndicesReady = true
        syncTerminalsFromBackend(backendIndices, oldIndices)
        if (pendingRouteTermIndex.value != null) {
            applyRouteTermIndex(pendingRouteTermIndex.value)
        }
    },
    { immediate: true },
)

function syncTerminalsFromBackend(backendIndices, oldIndices) {
    const localIndices = new Set(terminals.value.map(t => t.index))

    // Add tabs for backend terminals not present locally
    for (const index of backendIndices) {
        if (!localIndices.has(index)) {
            // Use label from store if available, otherwise default
            const storeLabel = terminalTabsStore.getLabel(props.contextKey, index)
            const label = storeLabel || defaultLabel(index)
            terminals.value.push({ index, label })
        }
    }

    // Remove tabs for terminals killed from another device
    // (only if we have old indices to compare — skip on first load)
    if (oldIndices) {
        const removedIndices = oldIndices.filter(i => !backendIndices.includes(i))
        for (const index of removedIndices) {
            if (index === 0) continue // main terminal never removed
            const idx = terminals.value.findIndex(t => t.index === index)
            if (idx !== -1) {
                terminals.value.splice(idx, 1)
                if (activeIndex.value === index) {
                    const prevTerminal = terminals.value[Math.max(0, idx - 1)]
                    nextNavigationReplace = false
                    activeIndex.value = prevTerminal?.index ?? 0
                }
            }
        }
    }

    // Sort terminals by index for consistent ordering
    terminals.value.sort((a, b) => a.index - b.index)

    // Update nextIndex to avoid collisions
    const maxIndex = Math.max(0, ...backendIndices, ...terminals.value.map(t => t.index))
    if (maxIndex >= nextIndex.value) {
        nextIndex.value = maxIndex + 1
    }
}

// Watch store labels for cross-device sync (another client renamed a terminal)
watch(
    () => terminalTabsStore.labels[props.contextKey],
    (storeLabels) => {
        if (!storeLabels) return
        for (const term of terminals.value) {
            const storeLabel = storeLabels[term.index]
            if (storeLabel) {
                term.label = storeLabel
            } else if (!storeLabel && terminalTabsStore.indices[props.contextKey]?.includes(term.index)) {
                // Label was cleared in the store for a known backend terminal → reset to default
                term.label = defaultLabel(term.index)
            }
        }
    },
    { deep: true },
)

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: terminal tab navigation (Alt+Ctrl+Shift+{1-9, ←/→, ↑})
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Tab visit history for Alt+Ctrl+Shift+↑ (last-visited, Alt+Tab-like behavior).
const terminalTabHistory = []
const MAX_TERMINAL_TAB_HISTORY = 50

function pushTerminalTabHistory(termIndex) {
    if (terminalTabHistory.length > 0 && terminalTabHistory[terminalTabHistory.length - 1] === termIndex) return
    terminalTabHistory.push(termIndex)
    if (terminalTabHistory.length > MAX_TERMINAL_TAB_HISTORY) terminalTabHistory.shift()
}

// Track terminal tab transitions for history
watch(activeIndex, (newIndex, oldIndex) => {
    if (!props.active) return
    if (oldIndex !== undefined && oldIndex !== newIndex) pushTerminalTabHistory(oldIndex)
})

// Announce this panel to the store while it's the visible one, so the command
// palette's "Go to … terminal" commands know which terminal is shown + active
// (and its worktree-aware contextKey) without recomputing it.
watch([() => props.active, activeIndex], ([active, idx]) => {
    if (active) terminalTabsStore.setActivePanel(props.contextKey, idx)
    else terminalTabsStore.clearActivePanel(props.contextKey)
}, { immediate: true })

function handleTerminalTabShortcut(event) {
    if (!props.active) return

    const { type, index } = event.detail

    if (type === 'direct') {
        // Direct access: number N → the Nth terminal tab (1-based positional)
        const term = terminals.value[index - 1]
        if (term) activateOwnTab(term.index)
    } else if (type === 'prev' || type === 'next') {
        const currentIdx = terminals.value.findIndex(t => t.index === activeIndex.value)
        if (currentIdx === -1) return
        const newIdx = type === 'next'
            ? (currentIdx + 1) % terminals.value.length
            : (currentIdx - 1 + terminals.value.length) % terminals.value.length
        activateOwnTab(terminals.value[newIdx].index)
    } else if (type === 'last-visited') {
        const validIndices = new Set(terminals.value.map(t => t.index))
        for (let i = terminalTabHistory.length - 1; i >= 0; i--) {
            const idx = terminalTabHistory[i]
            if (idx !== activeIndex.value && validIndices.has(idx)) {
                activateOwnTab(idx)
                return
            }
        }
    }
}

onMounted(() => {
    window.addEventListener('twicc:terminal-tab-shortcut', handleTerminalTabShortcut)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:terminal-tab-shortcut', handleTerminalTabShortcut)
    terminalTabsStore.clearActivePanel(props.contextKey)
})

defineExpose({ activeIndex })
</script>

<template>
    <div class="terminal-panel">
        <!-- Merged toolbar: terminal tabs (left) + action buttons (right) -->
        <div class="terminal-actions-bar">
            <!-- Left: TabBar used only for its scrollable nav -->
            <TabBar
                :active="activeTabPanel"
                class="terminal-tab-nav"
                @wa-tab-show="onTerminalTabShow"
            >
                <wa-tab
                    v-if="isRouteTermUnavailable"
                    slot="nav"
                    panel="__unavailable__"
                    class="terminal-unavailable-tab"
                    aria-hidden="true"
                    tabindex="-1"
                ></wa-tab>

                <!-- Attached parent-scope terminals, rendered before the own tabs -->
                <wa-tab
                    v-for="tab in attachedTabs"
                    :key="tab.key"
                    slot="nav"
                    :panel="tab.key"
                    class="terminal-attached-tab"
                >
                    <wa-icon name="link" class="terminal-attached-icon"></wa-icon>
                    {{ tab.displayLabel }}
                </wa-tab>

                <wa-tab
                    v-for="term in terminals"
                    :key="term.index"
                    slot="nav"
                    :panel="`term-${term.index}`"
                    @dblclick="onTabDblClick(term.index)"
                >
                    {{ term.label }}
                </wa-tab>

                <wa-button
                    slot="nav"
                    variant="brand"
                    appearance="outlined"
                    size="small"
                    class="add-terminal-button reduced-height"
                    @click="createTerminal"
                >
                    <wa-icon name="plus"></wa-icon>
                </wa-button>
            </TabBar>

            <!-- Right: rename button (always visible) + terminal-specific actions (when connected) -->
            <div v-if="!showUnavailableState" class="terminal-actions">
                <!-- Attach a terminal from a parent level (worktree → project → workspace → global) -->
                <AttachTerminalMenu
                    v-if="ancestorScopes.length"
                    :sections="attachSections"
                    @open="requestAncestorDiscovery"
                    @attach="attachTerminal"
                />

                <template v-if="tb.isConnected">
                    <!-- Scroll to edge buttons -->
                    <wa-button
                        v-if="tb.canScrollUp || tb.paneAlternate"
                        id="terminal-scroll-top-button"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="scroll-edge-button reduced-height"
                        :loading="tb.scrollingToEdge"
                        @click="handleScrollToEdge('top')"
                    >
                        <wa-icon name="angles-up"></wa-icon>
                    </wa-button>
                    <AppTooltip
                        v-if="tb.canScrollUp || tb.paneAlternate"
                        for="terminal-scroll-top-button"
                    >Scroll to top</AppTooltip>

                    <wa-button
                        v-if="tb.canScrollDown || tb.paneAlternate"
                        id="terminal-scroll-bottom-button"
                        variant="neutral"
                        appearance="plain"
                        size="small"
                        class="scroll-edge-button reduced-height"
                        :loading="tb.scrollingToEdge"
                        @click="handleScrollToEdge('bottom')"
                    >
                        <wa-icon name="angles-down"></wa-icon>
                    </wa-button>
                    <AppTooltip
                        v-if="tb.canScrollDown || tb.paneAlternate"
                        for="terminal-scroll-bottom-button"
                    >Scroll to bottom</AppTooltip>

                    <!-- Mobile-only: scroll/select mode toggle -->
                    <div v-if="settingsStore.isTouchDevice" class="touch-mode-group">
                        <span
                            class="touch-mode-label"
                            @click="handleTouchModeToggle"
                        >Scroll</span>
                        <wa-switch
                            size="small"
                            class="touch-mode-switch"
                            :checked="tb.touchMode === 'select'"
                            @change="handleTouchModeChange"
                        >Select</wa-switch>
                    </div>

                    <!-- Comment button (only when a message input is available) -->
                    <wa-button
                        v-if="tb.hasSelection && insertTextAtCursor"
                        ref="commentButtonRef"
                        id="terminal-comment-button"
                        variant="brand"
                        appearance="filled-outlined"
                        size="small"
                        class="comment-button reduced-height"
                        @click="handleComment"
                    >
                        <wa-icon name="comment" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="tb.hasSelection && insertTextAtCursor" for="terminal-comment-button">Comment on selection</AppTooltip>

                    <!-- Copy button (all devices) -->
                    <wa-button
                        v-if="tb.hasSelection"
                        id="terminal-copy-button"
                        variant="neutral"
                        appearance="filled"
                        size="small"
                        class="copy-button reduced-height"
                        @click="handleCopy"
                    >
                        <wa-icon name="copy" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip v-if="tb.hasSelection" for="terminal-copy-button">Copy selection</AppTooltip>

                    <!-- Paste button -->
                    <wa-button
                        id="terminal-paste-button"
                        variant="neutral"
                        appearance="filled"
                        size="small"
                        class="paste-button reduced-height"
                        @click="handlePaste"
                    >
                        <wa-icon name="paste" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-paste-button">Paste from clipboard</AppTooltip>
                </template>

                <wa-divider v-if="tb.isConnected && !isActiveForcedAttached" orientation="vertical"></wa-divider>

                <!-- AutoAttach into children — owner toggle (tmux ancestor scopes only) -->
                <template v-if="canBroadcast && !isActiveAttached">
                    <wa-button
                        id="terminal-autoattach-button"
                        variant="neutral"
                        :appearance="activeOwnAutoAttach ? 'filled' : 'plain'"
                        size="small"
                        :class="['autoattach-button', 'reduced-height', { 'autoattach-button--active': activeOwnAutoAttach }]"
                        @click="toggleAutoAttach"
                    >
                        <wa-icon name="thumbtack" :label="activeOwnAutoAttach ? 'Disable auto-attach in children' : 'Auto-attach in children'"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-autoattach-button">{{ activeOwnAutoAttach ? 'Auto-attached in children — click to stop' : 'Auto-attach this terminal in children' }}</AppTooltip>
                </template>

                <!-- Rename button — own tabs only (attached tabs are renamed at their own level) -->
                <template v-if="!isActiveAttached">
                    <wa-button
                        id="terminal-rename-button"
                        variant="neutral"
                        appearance="filled"
                        size="small"
                        class="rename-button reduced-height"
                        @click="openRenameDialog(activeIndex)"
                    >
                        <wa-icon name="pen-to-square" variant="regular"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-rename-button">Rename tab</AppTooltip>
                </template>

                <!-- Detach button — for a MANUALLY attached parent terminal (never kills its
                     tmux). Forced (auto-attached) tabs cannot be detached from a child. -->
                <template v-if="isActiveAttached && !isForced(activeAttachedKey)">
                    <wa-button
                        id="terminal-detach-button"
                        variant="neutral"
                        appearance="filled"
                        size="small"
                        class="disconnect-button reduced-height"
                        @click="handleDetach"
                    >
                        <wa-icon name="link-slash" label="Detach terminal"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-detach-button">Detach terminal</AppTooltip>
                </template>

                <!-- Disconnect / Kill button — own tabs only. Never for an attached
                     tab: a manual one shows Detach above; a forced (auto-attached)
                     one is owner-controlled and exposes no lifecycle action here. -->
                <template v-else-if="!isActiveAttached && tb.isConnected">
                    <wa-button
                        id="terminal-disconnect-button"
                        variant="danger"
                        appearance="outlined"
                        size="small"
                        class="disconnect-button reduced-height"
                        @click="handleKillOrDisconnect"
                    >
                        <wa-icon name="ban" :label="isActiveMain ? 'Disconnect' : 'Kill terminal'"></wa-icon>
                    </wa-button>
                    <AppTooltip for="terminal-disconnect-button">{{ isActiveMain ? 'Disconnect' : 'Kill terminal' }}</AppTooltip>
                </template>
            </div>
        </div>

        <div v-if="showUnavailableState" class="terminal-unavailable-state">
            <wa-callout variant="warning" class="terminal-unavailable-callout">
                {{ unavailableRouteMessage }}
            </wa-callout>
        </div>

        <!-- Terminal panels: empty slots. The app-level pool teleports each live
             TerminalInstance into the matching slot (by element ref). Slots overlay
             each other; only the active one is visible. visibility:hidden (not
             display:none) keeps hidden terminals' dimensions — no resize flash. -->
        <div v-if="!showUnavailableState" class="terminal-panels-container">
            <!-- Attached parent-scope terminals (foreign context, hosted in the pool) -->
            <div
                v-for="tab in attachedTabs"
                :key="tab.key"
                :class="['terminal-panel-wrapper', { active: activeAttachedKey === tab.key }]"
            >
                <div class="terminal-slot" :ref="(el) => setSlotEl(tab.key, el)"></div>
            </div>

            <div
                v-for="term in terminals"
                :key="term.index"
                :class="['terminal-panel-wrapper', { active: activeAttachedKey === null && activeIndex === term.index }]"
            >
                <div class="terminal-slot" :ref="(el) => setSlotEl(ownKey(term.index), el)"></div>
            </div>
        </div>

        <TerminalExtraKeysBar
            v-if="!showUnavailableState"
            :active-modifiers="activeApi?.activeModifiers ?? { ctrl: false, alt: false, shift: false }"
            :locked-modifiers="activeApi?.lockedModifiers ?? { ctrl: false, alt: false, shift: false }"
            :is-touch-device="settingsStore.isTouchDevice"
            :combos="terminalConfigStore.combos"
            :snippets="snippetsForProject"
            :context="[contextKey, sessionId, resolvedProjectId, active, activeAttachedKey, activeIndex, route.query.workspace]"
            :terminals="terminals"
            :active-terminal-index="activeIndex"
            @key-input="(...args) => activeApi?.handleExtraKeyInput?.(...args)"
            @modifier-toggle="(...args) => activeApi?.handleExtraKeyModifierToggle?.(...args)"
            @paste="() => activeApi?.handleExtraKeyPaste?.()"
            @combo-press="(...args) => activeApi?.handleComboPress?.(...args)"
            @snippet-press="handleSnippetPress"
            @snippet-send-to="handleSnippetSendTo"
            @snippet-edit-send="handleSnippetEditSend"
            @snippet-disabled-press="(snippet) => toast.warning(snippet._disabledReason)"
            @manage-combos="manageCombosDialogRef?.open()"
            @manage-snippets="manageSnippetsDialogRef?.open()"
        />

        <TerminalCombosDialog ref="manageCombosDialogRef" />
        <TerminalSnippetsDialog
            ref="manageSnippetsDialogRef"
            :current-project-id="snippetListProjectId"
        />
        <TerminalSnippetSendDialog
            ref="snippetSendDialogRef"
            :terminals="terminals"
            :active-terminal-index="activeIndex"
            :placeholder-context="placeholderContext"
            @send="handleSnippetSendDialogSend"
        />
        <TerminalRenameDialog
            ref="renameDialogRef"
            @save="handleRename"
        />

        <!-- Ephemeral text selection comment widget (teleported to body to avoid overflow clipping) -->
        <Teleport to="body">
            <TextSelectionComment
                v-if="terminalCommentPosition"
                ref="terminalCommentRef"
                :selected-text="terminalCommentText"
                :position="terminalCommentPosition"
                auto-expand
                source-label="from terminal"
                @close="closeTerminalComment"
            />
        </Teleport>
    </div>
</template>

<style scoped>
.terminal-panel {
    height: 100%;
    display: flex;
    flex-direction: column;
}

.terminal-unavailable-state {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: var(--wa-space-m);
}

.terminal-unavailable-callout {
    flex: 0 0 auto;
    width: auto;
    max-width: min(32rem, 100%);
}

/* ── Merged toolbar ─────────────────────────────────────── */

.terminal-actions-bar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: start;
    column-gap: var(--wa-space-m);
    border-bottom: var(--divider-size) solid var(--wa-color-surface-border);
    flex-shrink: 0;
    min-height: 2rem;
}

/* TabBar used only for its scrollable nav — hide its body */
.terminal-tab-nav {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    font-size: var(--wa-font-size-s);
    margin-bottom: calc(-1.5 * var(--divider-size));
    padding-top: 2.5px;
}
.terminal-tab-nav::part(tabs) {
    border-bottom-color: transparent;
}
.terminal-tab-nav::part(base) {
    overflow: hidden;
}
.terminal-tab-nav::part(body) {
    display: none;
}
.terminal-tab-nav::part(nav) {
    border-bottom: none;
    padding-bottom: 0;
}
.terminal-tab-nav::part(tabs) {
    align-items: center;
}
.terminal-unavailable-tab {
    display: none;
}
/* Attached parent-scope tabs are marked by their link icon alone. */
.terminal-attached-icon {
    font-size: 0.8em;
    opacity: 0.7;
}
.add-terminal-button {
    margin-left: var(--wa-space-2xs);
    align-self: center;
}

.terminal-actions {
    margin-inline-start: auto;
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-shrink: 0;
    wa-divider {
        --spacing: 0px;
    }
    padding-right: var(--wa-space-xs);
}

.scroll-edge-button {
    opacity: 0.5;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.scroll-edge-button:hover {
    opacity: 1;
}

.disconnect-button {
    opacity: 0.6;
    transition: opacity 0.15s;
    flex-shrink: 0;
}

.disconnect-button:hover:not([disabled]) {
    opacity: 1;
}

.touch-mode-group {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
}

.touch-mode-label {
    cursor: pointer;
    font-size: var(--wa-font-size-s);
    user-select: none;
}

.rename-button,
.copy-button,
.paste-button {
    flex-shrink: 0;
}

/* Auto-attach (pin) toggle: same look as today, but the thumbtack is rotated
   like the session pin, and turns yellow (instead of relying only on the filled
   background) when active. */
.autoattach-button::part(label) {
    transform: rotate(30deg);
}
.autoattach-button--active::part(base) {
    color: var(--wa-color-yellow-80);
}

/* ── Terminal panels ─────────────────────────────────────── */

.terminal-panels-container {
    flex: 1;
    min-height: 0;
    position: relative;
    overflow: hidden;
}

.terminal-panel-wrapper {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    visibility: hidden;
}

.terminal-panel-wrapper.active {
    visibility: visible;
}

/* Teleport target for the pooled TerminalInstance. Flex column so the instance's
   .terminal-area (flex: 1) fills it. */
.terminal-slot {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}
</style>
