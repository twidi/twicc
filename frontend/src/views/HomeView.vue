<script setup>
import { computed, ref, onMounted, onUnmounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { useWorkspacesStore } from '../stores/workspaces'
import { useStartupPolling } from '../composables/useStartupPolling'
import ProjectList from '../components/project/ProjectList.vue'
import WorkspaceList from '../components/workspace/WorkspaceList.vue'
import FetchErrorPanel from '../components/ui/FetchErrorPanel.vue'
import SettingsPopover from '../components/app/SettingsPopover.vue'
import PeerInboxButton from '../components/peer/PeerInboxButton.vue'
import ActivitySparkline from '../components/activity/ActivitySparkline.vue'
import AppTooltip from '../components/ui/AppTooltip.vue'
import BrandLogo from '../components/ui/BrandLogo.vue'
import StartupProgressCallout from '../components/app/StartupProgressCallout.vue'
import ProjectEditDialog from '../components/project/ProjectEditDialog.vue'
import WorkspaceManageDialog from '../components/workspace/WorkspaceManageDialog.vue'
import { ARTIFACT_ICON } from '../utils/artifactBookmark'

const router = useRouter()
const store = useDataStore()
const workspacesStore = useWorkspacesStore()

// Poll home data during startup so sparklines and project stats update
// as sessions are indexed by background compute.
useStartupPolling(() => store.loadHomeData())

// Total sessions count (sum of all projects)
const totalSessionsCount = computed(() =>
    store.getProjects.reduce((sum, p) => sum + (p.sessions_count || 0), 0)
)

// Loading and error states
const isLoading = computed(() => store.isProjectsListLoading)
const hasError = computed(() => store.didProjectsListFailToLoad)

function handleProjectSelect(project) {
    router.push({ name: 'project', params: { projectId: project.id } })
}

const createDialogRef = ref(null)
const manageDialogRef = ref(null)

function openCreateDialog() {
    createDialogRef.value?.open()
}

function handleProjectCreated(project) {
    router.push({ name: 'project', params: { projectId: project.id } })
}

// Workspaces
function handleWorkspaceSelect(workspace) {
    router.push({ name: 'projects-all', query: { workspace: workspace.id } })
}

function handleWorkspaceMenuSelect(event, workspace) {
    const item = event.detail?.item
    if (!item) return
    if (item.value === 'manage') {
        manageDialogRef.value?.openForWorkspace(workspace.id)
    } else if (item.value === 'archive') {
        workspacesStore.updateWorkspace(workspace.id, { archived: true })
    } else if (item.value === 'unarchive') {
        workspacesStore.updateWorkspace(workspace.id, { archived: false })
    } else if (item.value === 'delete') {
        workspacesStore.deleteWorkspace(workspace.id)
    }
}

// Global weekly activity from the store
const globalWeeklyActivity = computed(() => store.weeklyActivity._global || [])

async function handleRetry() {
    await store.loadHomeData()
}

// Open dialogs (triggered by command palette custom events)
function openNewProjectDialog() {
    createDialogRef.value?.open()
}
function openNewWorkspaceDialog() {
    manageDialogRef.value?.openNew()
}
function openManageWorkspacesDialog() {
    manageDialogRef.value?.open()
}
function openEditWorkspaceDialog(e) {
    manageDialogRef.value?.openForWorkspace(e.detail?.workspaceId)
}

onMounted(() => {
    window.addEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
    window.addEventListener('twicc:open-new-workspace-dialog', openNewWorkspaceDialog)
    window.addEventListener('twicc:open-manage-workspaces-dialog', openManageWorkspacesDialog)
    window.addEventListener('twicc:open-edit-workspace-dialog', openEditWorkspaceDialog)
})
onBeforeUnmount(() => {
    window.removeEventListener('twicc:open-new-project-dialog', openNewProjectDialog)
    window.removeEventListener('twicc:open-new-workspace-dialog', openNewWorkspaceDialog)
    window.removeEventListener('twicc:open-manage-workspaces-dialog', openManageWorkspacesDialog)
    window.removeEventListener('twicc:open-edit-workspace-dialog', openEditWorkspaceDialog)
})
</script>

<template>
    <div class="home-view">
        <header class="home-header">
            <BrandLogo :size="36" animated />
            <h1>Welcome to TwiCC</h1>
            <span id="home-global-sparkline" class="global-sparkline">
                <ActivitySparkline :data="globalWeeklyActivity" />
            </span>
            <AppTooltip for="home-global-sparkline">Overall activity (message turns per week)</AppTooltip>
            <div class="home-header-actions">
                <wa-button v-if="totalSessionsCount > 0" variant="brand" appearance="filled-outlined" size="small" @click="router.push({ name: 'projects-all' })">
                    All {{ totalSessionsCount }} session{{ totalSessionsCount === 1 ? '' : 's' }} <wa-icon slot="end" name="arrow-right"></wa-icon>
                </wa-button>
                <wa-button variant="brand" appearance="filled-outlined" size="small" @click="router.push({ name: 'projects-artifacts' })">
                    <wa-icon slot="start" :name="ARTIFACT_ICON"></wa-icon>
                    Artifacts <wa-icon slot="end" name="arrow-right"></wa-icon>
                </wa-button>
            </div>
        </header>

        <!-- Startup progress (initial sync / background compute) -->
        <StartupProgressCallout />

        <main class="home-content">
            <!-- Error state -->
            <FetchErrorPanel
                v-if="hasError"
                :loading="isLoading"
                @retry="handleRetry"
            >
                Failed to load projects
            </FetchErrorPanel>

            <!-- Loading state -->
            <div v-else-if="isLoading" class="loading-state">
                <wa-spinner></wa-spinner>
                <span>Loading projects...</span>
            </div>

            <!-- Normal content -->
            <template v-else>
                <!-- Workspaces section -->
                <WorkspaceList
                    @select="handleWorkspaceSelect"
                    @menu-select="handleWorkspaceMenuSelect"
                    @manage="manageDialogRef?.open()"
                    @create="manageDialogRef?.openNew()"
                />

                <ProjectList @select="handleProjectSelect" @create="openCreateDialog" />
            </template>
        </main>

        <div class="home-settings">
            <PeerInboxButton />
            <SettingsPopover />
        </div>

        <ProjectEditDialog ref="createDialogRef" @saved="handleProjectCreated" />
        <WorkspaceManageDialog ref="manageDialogRef" />
    </div>
</template>

<style scoped>
.home-view {
    padding: var(--wa-space-l);
    max-width: 900px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    /* Native page scroll is enabled via onMounted (overrides :root overflow:hidden) */
    min-height: 100dvh;
}

.home-header {
    display: flex;
    justify-content: start;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
    margin-bottom: var(--wa-space-xl);
    flex-shrink: 0;
}

.home-header h1 {
    margin: 0;
    font-size: var(--wa-font-size-2xl);
    font-weight: 700;
    color: var(--wa-color-text-normal);
}

.home-header-actions {
    margin-left: auto;
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.global-sparkline {
    flex-shrink: 0;
}

.home-content {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-l);
    min-height: 0;
}

.loading-state {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: var(--wa-space-s);
    min-height: 50dvh;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-m);
}

.home-settings {
    position: fixed;
    bottom: var(--wa-space-s);
    left: var(--wa-space-s);
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
}
</style>
