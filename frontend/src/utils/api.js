// frontend/src/utils/api.js

import { useAuthStore } from '../stores/auth'

/**
 * Lazily import the router to avoid circular dependencies.
 * (router imports views → views import components → components import api.js)
 */
async function getRouter() {
    const { router } = await import('../router')
    return router
}

/**
 * Fetch wrapper that handles 401 responses by redirecting to login.
 *
 * Usage: drop-in replacement for fetch() in API calls.
 * On 401, marks the auth store as unauthenticated and redirects to /login.
 *
 * @param {string} url - The URL to fetch
 * @param {RequestInit} [options] - Fetch options
 * @returns {Promise<Response>} - The fetch response
 */
export async function apiFetch(url, options) {
    const response = await fetch(url, options)

    if (response.status === 401) {
        const authStore = useAuthStore()
        authStore.handleUnauthorized()
        // Redirect to login with current path as redirect target
        const router = await getRouter()
        const currentPath = router.currentRoute.value.fullPath
        if (router.currentRoute.value.name !== 'login') {
            router.replace({ name: 'login', query: { redirect: currentPath } })
        }
    }

    return response
}
