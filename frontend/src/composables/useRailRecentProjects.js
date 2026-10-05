import { computed, onScopeDispose, ref } from 'vue'

const RECENT_WINDOW_SECONDS = 7 * 24 * 60 * 60

/**
 * Recent top-level projects and their selectable workspaces for SidebarRail.
 * Call inside the component's scope. The optional clock returns Unix seconds.
 * Shared store getters own activity, order, and workspace visibility/membership.
 */
export function useRailRecentProjects(store, workspacesStore, settingsStore, clock = () => Date.now() / 1000) {
    const now = ref(clock())
    const timer = setInterval(() => { now.value = clock() }, 60_000)
    onScopeDispose(() => clearInterval(timer))

    const recentProjects = computed(() => {
        const cutoff = now.value - RECENT_WINDOW_SECONDS
        return store.getListableProjects.filter(project => {
            if (project.archived && !settingsStore.isShowArchivedProjects) return false
            const activity = store.getProjectActivity(project.id)
            return activity > 0 && activity >= cutoff
        })
    })

    const recentWorkspaces = computed(() => {
        const projectIds = new Set(recentProjects.value.map(project => project.id))
        return workspacesStore.getSelectableWorkspaces.filter(workspace =>
            workspacesStore.getVisibleProjectIds(workspace.id).some(id => projectIds.has(id))
        )
    })

    return { recentProjects, recentWorkspaces }
}
