// useProjectMark — resolve a project's mark (icon + dot color) for <ProjectMark>.
//
// Centralizes what the ~7 dot-rendering sites used to duplicate:
//   - iconUrl: the server-resolved icon URL (inheritance already applied
//     backend-side via the repo-icon cache and icon_anchor), or null.
//   - dotColor: the project's color with the worktree → main-repo fallback,
//     plus optional caller overrides (live previews, explicit fallbacks).
//
// See docs/archives/plans/2026-07-17-project-icons-design.md.
import { computed, unref } from 'vue'
import { useDataStore } from '../stores/data'

/**
 * @param {import('vue').Ref<string>|string} projectIdRef  the project id (ref or plain)
 * @param {object} [options]
 * @param {import('vue').Ref<string>|string} [options.colorOverride]  when non-null
 *        (incl. ''), replaces the project's stored color (live color previews).
 * @param {import('vue').Ref<string>|string} [options.fallbackColor]  used when the
 *        project has no own color, before the worktree parent fallback.
 */
export function useProjectMark(projectIdRef, options = {}) {
    const store = useDataStore()
    const project = computed(() => store.getProject(unref(projectIdRef)))

    const iconUrl = computed(() => store.resolvedProjectIcons[unref(projectIdRef)] || null)

    const dotColor = computed(() => {
        const override = unref(options.colorOverride)
        const own = override !== undefined && override !== null ? override : project.value?.color
        if (own) return own
        const fallback = unref(options.fallbackColor)
        if (fallback) return fallback
        // Worktree fallback: inherit the main repository's color.
        const parentId = project.value?.worktree_of
        const parent = parentId ? store.getProject(parentId) : null
        return parent?.color || null
    })

    return { project, iconUrl, dotColor }
}
