<script setup>
// ProjectDetailPanel.vue - Detail panel shown when no session is selected.
// Delegates header display to ProjectDetailHeader, then shows tabbed content.

import { ref, computed, watch, watchEffect, onMounted, onBeforeUnmount, onActivated, onDeactivated } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ALL_PROJECTS_ID, useDataStore } from '../../stores/data'
import { useWorkspacesStore } from '../../stores/workspaces'
import { isWorkspaceProjectId, extractWorkspaceId } from '../../utils/workspaceIds'
import ProjectDetailHeader from './ProjectDetailHeader.vue'
import { apiFetch } from '../../utils/api'
import ProjectDetailNavList from './ProjectDetailNavList.vue'
import ContributionGraphs from '../activity/ContributionGraphs.vue'
import FilesPanel from '../files/FilesPanel.vue'
import GitPanel from '../git/GitPanel.vue'
import TerminalPanel from '../terminal/TerminalPanel.vue'
import TabBar from '../ui/TabBar.vue'
import { deriveFileRoots, getWorktreeParent } from '../../utils/projectRoots'
import { worktreeLabel } from '../../utils/worktree'
import {
    buildFilesRouteParams,
    buildGitRouteParams,
    clearTabRouteParams,
    buildProjectBaseRouteName,
    buildTabRouteName,
    buildTerminalRouteParams,
    decodePath,
    parseRouteString,
    parseRouteTermIndex,
} from '../../utils/granularRoutes'

const props = defineProps({
    /** Project ID or ALL_PROJECTS_ID for aggregate view */
    projectId: {
        type: String,
        required: true,
    },
    /** Whether this panel is currently visible (not hidden behind a session view) */
    active: {
        type: Boolean,
        default: true,
    },
})

const dataStore = useDataStore()
const workspacesStore = useWorkspacesStore()
const route = useRoute()
const router = useRouter()

// KeepAlive lifecycle — track whether this instance is active (not cached)
const isKeptAlive = ref(true)
onActivated(() => { isKeptAlive.value = true })
onDeactivated(() => { isKeptAlive.value = false })

// Effective active state: visible AND not deactivated by KeepAlive
const isActive = computed(() => props.active && isKeptAlive.value)

// Mode detection — needed by terminal, files, and tab management sections
const isWorkspaceMode = computed(() => isWorkspaceProjectId(props.projectId))
const isAllProjectsMode = computed(() => route.name?.startsWith('projects-'))
const workspaceId = computed(() => isWorkspaceMode.value ? extractWorkspaceId(props.projectId) : null)
const workspaceProjectIds = computed(() =>
    workspaceId.value ? workspacesStore.getVisibleProjectIds(workspaceId.value) : null
)
// Stats (heatmap/sparkline) include archived projects so the history reflects
// the workspace as a whole, not just what is currently shown in lists.
const workspaceStatsProjectIds = computed(() =>
    workspaceId.value ? workspacesStore.getAllProjectIds(workspaceId.value) : null
)

// Project IDs aggregated for the stats panel (contribution heatmaps / graphs).
// No archived filter — reflects the whole. Workspace: all members + their
// worktrees. Single project with worktrees: the project + its worktrees. A
// plain project or All-Projects returns null, so ContributionGraphs falls back
// to its own per-project / global endpoint.
const statsProjectIds = computed(() => {
    if (isWorkspaceMode.value) return workspaceStatsProjectIds.value
    if (props.projectId === ALL_PROJECTS_ID) return null
    const scope = dataStore.getProjectScopeIds(props.projectId)
    return scope.length > 1 ? scope : null
})

const terminalContextKey = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID) {
        return 'global'
    }
    if (isWorkspaceProjectId(props.projectId)) {
        return `w:${extractWorkspaceId(props.projectId)}`
    }
    return `p:${props.projectId}`
})

// For project terminals, pass the real project ID (not workspace/all-projects pseudo-IDs)
const terminalProjectId = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(props.projectId)) {
        return null
    }
    return props.projectId
})

// For workspace terminals, the working directory is the lowest common ancestor
// of all project directories (computed by the workspaces store, shared with the
// attach-parent-terminals feature).
const terminalCwd = computed(() =>
    isWorkspaceMode.value && workspaceId.value
        ? workspacesStore.getTerminalCwd(workspaceId.value)
        : null
)

const homeDir = ref(null)

watchEffect(async () => {
    if (props.projectId === ALL_PROJECTS_ID && !homeDir.value) {
        try {
            const res = await apiFetch('/api/home-directory/')
            if (res.ok) {
                const data = await res.json()
                homeDir.value = data.path
            }
        } catch { /* ignore */ }
    }
})

// For project files, pass the real project ID (not workspace/all-projects pseudo-IDs)
const filesProjectId = computed(() => {
    if (props.projectId === ALL_PROJECTS_ID || isWorkspaceProjectId(props.projectId)) return null
    return props.projectId
})

const filesApiPrefix = computed(() => {
    // Single project mode: use project-scoped endpoints (browsing restricted via validate_path)
    if (!isAllProjectsMode.value && !isWorkspaceMode.value) {
        return `/api/projects/${props.projectId}`
    }
    // All-projects and workspace modes: use standalone endpoints (restricted via ?root= param)
    return '/api'
})

const filesRootRestriction = computed(() => {
    // Project mode: restriction handled by validate_path, no need for ?root=
    if (!isAllProjectsMode.value && !isWorkspaceMode.value) return null
    // All-projects mode: no restriction (user's machine, no scope to enforce)
    if (props.projectId === ALL_PROJECTS_ID) return null
    // Workspace mode: restrict to LCA
    return terminalCwd.value
})

const filesAvailableRoots = computed(() => {
    // All-projects mode
    if (props.projectId === ALL_PROJECTS_ID) {
        if (!homeDir.value) return []
        const roots = [{ key: 'home', label: 'Home directory', path: homeDir.value }]
        if (homeDir.value !== '/') {
            roots.push({ key: 'root', label: 'System root', path: '/' })
        }
        return roots
    }

    // Workspace mode
    if (isWorkspaceMode.value) {
        const roots = []
        const lca = terminalCwd.value  // reuse the LCA already computed for terminal
        if (!lca) return []
        roots.push({ key: 'common', label: 'Common directory', path: lca })

        // Add unique project directories that differ from LCA
        const seen = new Set([lca])
        const projectEntries = []
        for (const pid of workspaceProjectIds.value || []) {
            const project = dataStore.getProject(pid)
            const dir = project?.directory
            if (!dir || seen.has(dir)) continue
            seen.add(dir)
            // The root selector renders the project badge (a worktree reads as
            // "<main repo> ⎇ <folder>"); `label` is the same text, used for
            // sorting, so a worktree sorts right after its main repository.
            const ownName = project.worktree_of
                ? worktreeLabel(project) || dataStore.getProjectDisplayName(pid)
                : dataStore.getProjectDisplayName(pid)
            const parentName = project.worktree_of && dataStore.getProject(project.worktree_of)
                ? dataStore.getProjectDisplayName(project.worktree_of)
                : ''
            projectEntries.push({
                key: `p:${pid}`,
                label: parentName ? `${parentName} / ${ownName}` : ownName,
                path: dir,
                projectId: pid,
            })
        }
        projectEntries.sort((a, b) => a.label.localeCompare(b.label))
        roots.push(...projectEntries)
        return roots
    }

    // Single project mode — canonical derivation, including worktree main-repo
    // roots (utils/projectRoots.js). The project view passes these as
    // externalRoots, so FilesPanel's own worktree lookup is bypassed here.
    const project = dataStore.getProject(props.projectId)
    if (!project?.directory) return []
    const parent = getWorktreeParent(project, dataStore)
    return deriveFileRoots({
        projectDirectory: project.directory,
        projectGitRoot: project.git_root,
        parentDirectory: parent?.directory,
        parentGitRoot: parent?.git_root,
    })
})

// Tab management — derived from route (like SessionView)
const filesPanelRef = ref(null)
const gitPanelRef = ref(null)
const terminalPanelRef = ref(null)

const isSingleProjectMode = computed(() => !isAllProjectsMode.value && !isWorkspaceMode.value)

const hasGitRepo = computed(() => {
    if (!isSingleProjectMode.value) return false
    return !!dataStore.getProject(props.projectId)?.git_root
})

const TABS = computed(() => {
    const tabs = [
        { id: 'stats', label: 'Stats', icon: 'chart-simple' },
        { id: 'files', label: 'Files', icon: 'folder' },
    ]
    if (hasGitRepo.value) {
        tabs.push({ id: 'git', label: 'Git', icon: 'code-branch' })
    }
    tabs.push({ id: 'terminal', label: 'Terminal', icon: 'terminal' })
    return tabs
})

// Active tab derived from the route name
const activeTab = computed(() => {
    const name = route.name
    if (name === 'project-files' || name === 'projects-files') return 'files'
    if (name === 'project-git' || name === 'projects-git') return 'git'
    if (name === 'project-terminal' || name === 'projects-terminal') return 'terminal'
    return 'stats'
})
const filesRouteRootKey = computed(() => parseRouteString(route.params.rootKey))
const filesRouteFilePath = computed(() => {
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

watch([activeTab, hasGitRepo], ([tabId, hasGit]) => {
    if (tabId === 'git' && !hasGit) {
        if (!isActive.value) return
        if (!dataStore.getProject(props.projectId)) return
        router.replace({
            name: buildProjectBaseRouteName(isAllProjectsMode.value),
            params: isAllProjectsMode.value ? {} : { projectId: props.projectId },
            query: route.query,
        })
    }
}, { immediate: true })

function navigateInTab(tabId, params = {}, method = 'push') {
    router[method]({
        name: buildTabRouteName({
            isAllProjectsMode: isAllProjectsMode.value,
            isSessionRoute: false,
            tab: tabId,
        }),
        params: clearTabRouteParams(tabId, isAllProjectsMode.value ? params : { projectId: props.projectId, ...params }),
        query: route.query,
    })
}

function onFilesNavigate({ rootKey, filePath, replace }) {
    const params = buildFilesRouteParams({ rootKey, filePath })
    rememberToolTabRoute('files', params)
    navigateInTab('files', params, replace ? 'replace' : 'push')
}

function onGitNavigate({ rootKey, commitRef, filePath, replace }) {
    const params = buildGitRouteParams({ rootKey, commitRef, filePath })
    rememberToolTabRoute('git', params)
    navigateInTab('git', params, replace ? 'replace' : 'push')
}

function onTerminalNavigate({ termIndex, replace }) {
    const params = buildTerminalRouteParams({ termIndex })
    rememberToolTabRoute('terminal', params)
    navigateInTab('terminal', params, replace ? 'replace' : 'push')
}

const TOOL_TAB_IDS = ['files', 'git', 'terminal']

// Each tool tab remembers its last granular route so coming back to it restores
// the same panel state instead of resetting to /files, /git, or /terminal.
const rememberedToolTabRoutes = {
    files: null,
    git: null,
    terminal: null,
}

function getCurrentToolTabRouteParams(tabId) {
    if (tabId === 'files') {
        return buildFilesRouteParams({
            rootKey: filesRouteRootKey.value,
            filePath: filesRouteFilePath.value,
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

    return null
}

function rememberToolTabRoute(tabId, params = getCurrentToolTabRouteParams(tabId)) {
    if (!TOOL_TAB_IDS.includes(tabId)) return
    rememberedToolTabRoutes[tabId] = params ?? {}
}

watch(
    [
        isActive,
        activeTab,
        filesRouteRootKey,
        filesRouteFilePath,
        gitRouteRootKey,
        gitRouteCommitRef,
        gitRouteFilePath,
        terminalRouteTermIndex,
    ],
    ([active, tabId]) => {
        if (!active) return
        if (!TOOL_TAB_IDS.includes(tabId)) return
        rememberToolTabRoute(tabId)
    },
    { immediate: true }
)

// Note: TABS already has { id, label, icon } — pass directly to header for the compact dropdown

function switchToTab(tabId) {
    if (tabId === activeTab.value) return
    if (TOOL_TAB_IDS.includes(tabId)) {
        navigateInTab(tabId, rememberedToolTabRoutes[tabId] ?? {})
    } else {
        // Stats = default route (no suffix)
        router.push({
            name: buildProjectBaseRouteName(isAllProjectsMode.value),
            params: isAllProjectsMode.value ? {} : { projectId: props.projectId },
            query: route.query,
        })
    }
}

function onTabShow(event) {
    const panel = event.detail?.name
    // Only handle events from our own tabs (not from nested tab-groups like TerminalPanel's)
    if (panel && TABS.value.some(t => t.id === panel)) switchToTab(panel)
}

// ═══════════════════════════════════════════════════════════════════════════
// Keyboard shortcuts: tab navigation (Alt+Shift+1-4, ←/→, ↑)
// Events dispatched by App.vue, handled here by the active instance only.
// ═══════════════════════════════════════════════════════════════════════════

// Ordered list of all visible tabs (for sequential ←/→ navigation).
const orderedTabs = computed(() => TABS.value.map(t => t.id))

// Tab visit history for Alt+Shift+↑ (last-visited, Alt+Tab-like behavior).
const tabHistory = []
const MAX_TAB_HISTORY = 50

function pushTabHistory(tabId) {
    if (tabHistory.length > 0 && tabHistory[tabHistory.length - 1] === tabId) return
    tabHistory.push(tabId)
    if (tabHistory.length > MAX_TAB_HISTORY) tabHistory.shift()
}

watch(activeTab, (newTabId, oldTabId) => {
    if (!isActive.value) return
    if (oldTabId) pushTabHistory(oldTabId)
})

// Direct tab mapping: Alt+Shift+{1,2,3,4} → fixed tabs (matches session pattern)
const DIRECT_TAB_MAP = { 1: 'stats', 2: 'files', 3: 'git', 4: 'terminal' }

function handleTabShortcut(event) {
    if (!isActive.value) return

    const { type, index } = event.detail
    let targetTab = null

    if (type === 'direct') {
        targetTab = DIRECT_TAB_MAP[index]
        if (!targetTab) return
        if (targetTab === 'git' && !hasGitRepo.value) return
    } else if (type === 'prev' || type === 'next') {
        const tabs = orderedTabs.value
        const currentIndex = tabs.indexOf(activeTab.value)
        if (currentIndex === -1) return
        const newIndex = type === 'next'
            ? (currentIndex + 1) % tabs.length
            : (currentIndex - 1 + tabs.length) % tabs.length
        targetTab = tabs[newIndex]
    } else if (type === 'last-visited') {
        const tabs = orderedTabs.value
        for (let i = tabHistory.length - 1; i >= 0; i--) {
            const tabId = tabHistory[i]
            if (tabId !== activeTab.value && tabs.includes(tabId)) {
                targetTab = tabId
                break
            }
        }
    }

    if (!targetTab) return
    switchToTab(targetTab)
}

onMounted(() => {
    window.addEventListener('twicc:tab-shortcut', handleTabShortcut)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:tab-shortcut', handleTabShortcut)
})
</script>

<template>
    <div class="project-detail-panel">
        <ProjectDetailHeader :project-id="projectId" />

        <wa-divider></wa-divider>

        <TabBar
            :active="activeTab"
            class="detail-tabs"
            @wa-tab-show="onTabShow"
        >
            <wa-tab v-for="tab in TABS" :key="tab.id" slot="nav" :panel="tab.id">
                <wa-icon :name="tab.icon"></wa-icon>
                {{ tab.label }}
            </wa-tab>

            <wa-tab-panel name="stats">
                <ProjectDetailNavList :project-id="projectId" class="stats-nav-list" />
                <wa-divider class="stats-nav-list-divider"></wa-divider>
                <ContributionGraphs :project-id="projectId" :project-ids="statsProjectIds" />
            </wa-tab-panel>

            <wa-tab-panel name="files">
                <FilesPanel
                    ref="filesPanelRef"
                    :api-prefix="filesApiPrefix"
                    :project-id="filesProjectId"
                    :root-restriction="filesRootRestriction"
                    :external-roots="filesAvailableRoots"
                    :route-root-key="activeTab === 'files' ? filesRouteRootKey : undefined"
                    :route-file-path="activeTab === 'files' ? filesRouteFilePath : undefined"
                    :active="isActive && activeTab === 'files'"
                    @navigate="onFilesNavigate"
                />
            </wa-tab-panel>

            <wa-tab-panel v-if="hasGitRepo" name="git">
                <GitPanel
                    ref="gitPanelRef"
                    :project-id="projectId"
                    :session-id="projectId"
                    :project-git-root="dataStore.getProject(projectId)?.git_root"
                    :route-root-key="activeTab === 'git' ? gitRouteRootKey : undefined"
                    :route-commit-ref="activeTab === 'git' ? gitRouteCommitRef : undefined"
                    :route-file-path="activeTab === 'git' ? gitRouteFilePath : undefined"
                    :is-draft="true"
                    :active="isActive && activeTab === 'git'"
                    @navigate="onGitNavigate"
                />
            </wa-tab-panel>

            <wa-tab-panel name="terminal">
                <TerminalPanel
                    ref="terminalPanelRef"
                    :context-key="terminalContextKey"
                    :project-id="terminalProjectId"
                    :cwd="terminalCwd"
                    :route-term-index="activeTab === 'terminal' ? terminalRouteTermIndex : undefined"
                    :active="isActive && activeTab === 'terminal'"
                    @navigate="onTerminalNavigate"
                />
            </wa-tab-panel>
        </TabBar>
    </div>
</template>

<style scoped>
.project-detail-panel {
    container: project-detail / inline-size;
    display: flex;
    flex-direction: column;
    height: 100%;
    padding-top: var(--wa-space-s);
    width: 100%;
    overflow: hidden;
}

wa-divider {
    --spacing: 0;
    --width: var(--divider-size);
}

.detail-tabs {
    flex: 1;
    min-height: 0;
    overflow: hidden;
}

.detail-tabs::part(base) {
    height: 100%;
    overflow: hidden;
}

.detail-tabs::part(body) {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}

.detail-tabs :deep(wa-tab-panel::part(base)) {
    padding: 0;
}

.detail-tabs :deep(wa-tab-panel[active]) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
}

.detail-tabs :deep(wa-tab-panel[active])::part(base) {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
}

.detail-tabs :deep(wa-tab-panel[name="files"])::part(base),
.detail-tabs :deep(wa-tab-panel[name="git"])::part(base),
.detail-tabs :deep(wa-tab-panel[name="terminal"])::part(base) {
    overflow-y: hidden;
    padding-bottom: 0;
}

.stats-nav-list, .stats-nav-list-divider {
    display: none;
}

@media (max-height: 900px) {
    .project-detail-panel {
        padding-top: 0;
    }

    /* Hide the divider (the tab bar now stays inline in the content) */
    wa-divider {
        display: none;
    }

    .stats-nav-list {
        display: flex;
        padding: var(--wa-space-s) var(--wa-space-m);
    }
    .stats-nav-list-divider {
        display: block;
        --spacing: 0;
        --width: var(--divider-size);
    }
}
</style>
