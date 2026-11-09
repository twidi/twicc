import { ref } from 'vue'

export const PROJECT_DIRECTORY_STATUS = Object.freeze({ OK: 'ok', MISSING: 'missing', BLOCKED: 'blocked' })

/**
 * The live state of a project directory, for one session view.
 *
 * `Project.stale` is a stored observation, so a view asks the backend when it
 * opens a stopped session. Any failure of that check leaves the status `ok`:
 * the composer stays available and the send path reports a launch failure.
 *
 * @param {{request: Function, onProjectUpdated: Function}} dependencies
 *   `request` has the `fetch` signature. `onProjectUpdated` receives the project
 *   that a successful recreation returns.
 */
export function createProjectDirectoryStatus({ request, onProjectUpdated }) {
    const status = ref(PROJECT_DIRECTORY_STATUS.OK)
    const recreating = ref(false)
    // The backend error payload of the last refused recreation, or `{}` when no payload exists.
    const recreateError = ref(null)
    // Only the latest check may publish: the view can move to another session meanwhile.
    let generation = 0

    function clear() {
        generation += 1
        status.value = PROJECT_DIRECTORY_STATUS.OK
        recreateError.value = null
    }

    async function check(projectId) {
        clear()
        const current = generation
        let next = PROJECT_DIRECTORY_STATUS.OK
        try {
            const response = await request(`/api/projects/${projectId}/directory-status/`)
            if (response.ok) next = (await response.json()).status
        } catch {
            next = PROJECT_DIRECTORY_STATUS.OK
        }
        if (current === generation) status.value = next
    }

    async function recreate(projectId) {
        if (recreating.value) return
        recreating.value = true
        recreateError.value = null
        try {
            const response = await request(`/api/projects/${projectId}/recreate-directory/`, { method: 'POST' })
            const payload = await response.json().catch(() => ({}))
            if (response.ok) {
                onProjectUpdated(payload)
                generation += 1
                status.value = PROJECT_DIRECTORY_STATUS.OK
            } else {
                recreateError.value = payload
            }
        } catch {
            recreateError.value = {}
        } finally {
            recreating.value = false
        }
    }

    return { status, recreating, recreateError, check, clear, recreate }
}
