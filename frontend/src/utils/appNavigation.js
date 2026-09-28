// frontend/src/utils/appNavigation.js
// A flag for page loads that the app itself starts (after a login, a backend
// version mismatch, a resync). A `beforeunload` handler reads it so it does not
// ask "Leave site?" for a navigation the app already decided on.

let appNavigation = false

/** Mark that the app starts a page load now. Call it right before the load. */
export function markAppNavigation() {
    appNavigation = true
}

/** True once `markAppNavigation()` was called in this page. */
export function isAppNavigation() {
    return appNavigation
}
