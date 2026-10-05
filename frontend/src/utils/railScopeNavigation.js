import { nextTick } from 'vue'
import { isNavigationFailure, NavigationFailureType } from 'vue-router'

// In-memory guard context. Never persists in route queries or browser history.
const rootNavigations = new WeakMap()
export function isRailScopeRootNavigation(router, route) {
    return rootNavigations.get(router)?.destination === route.fullPath
}

/** Open only the settled destination. Duplicate clicks also open a closed sidebar. */
export function createRailScopeNavigation(router, open = () => window.dispatchEvent(new Event('twicc:open-sidebar'))) {
    let navigation = 0
    return async (kind, id) => {
        const current = ++navigation
        const target = kind === 'project'
            ? { name: 'project', params: { projectId: id }, query: { workspace: '' } }
            : { name: 'projects-all', query: { workspace: id } }
        const destination = router.resolve(target).fullPath
        const context = { destination }
        rootNavigations.set(router, context)
        let failure
        try {
            failure = await router.push(target)
        } catch {
            // Vue Router reports rejected guards through its normal onError handlers.
            return
        } finally {
            if (rootNavigations.get(router) === context) rootNavigations.delete(router)
        }
        if (failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) return
        // The cold Home -> ProjectView navigation must install its event listener first.
        await nextTick()
        if (current === navigation && router.currentRoute.value.fullPath === destination) open()
    }
}

/** Reuse ProjectView's change handler for state synchronization and saved desktop width. */
export function openSidebarCheckbox(checkbox, mobile) {
    if (!checkbox || checkbox.checked === mobile) return
    checkbox.checked = mobile
    checkbox.dispatchEvent(new Event('change'))
}
