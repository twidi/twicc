// Shared memory of the last full route location per sidebar mode (Sessions vs
// Artifacts). Switching modes — from the icon bar mode buttons, the
// command palette, anywhere — restores the target mode's last URL, so you land
// back exactly where you were (open session/sub-tab, or open bookmark) instead
// of a bare mode root.
//
// These are module-level singletons (shared across every importer). ProjectView
// is the single writer: it updates them as the route changes and resets them
// when the project/workspace scope changes (the remembered URLs belong to the
// previous scope). Everything else only reads them to navigate.
//
// A location is a vue-router target: { name, params, query }.
import { ref } from 'vue'

export const lastSessionsLocation = ref(null)
export const lastArtifactsLocation = ref(null)

// The scope (effectiveProjectId) the stored locations belong to. ProjectView
// resets the memory when this changes, so a different project/workspace never
// restores a stale cross-scope URL — even after leaving and coming back.
export const memoryScope = ref(null)

export function resetSidebarViewMemory() {
    lastSessionsLocation.value = null
    lastArtifactsLocation.value = null
}
