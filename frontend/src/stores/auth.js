// frontend/src/stores/auth.js

import { defineStore, acceptHMRUpdate } from 'pinia'

export const useAuthStore = defineStore('auth', {
    state: () => ({
        // null = not checked yet, true = authenticated, false = not authenticated
        authenticated: null,
        // Whether the server requires a password
        passwordRequired: null,
        // Loading state for initial check
        checking: true,
        // True when checkAuth is retrying after network errors
        connectionError: false,
    }),

    getters: {
        /**
         * Whether the auth state has been determined.
         * Used to avoid showing login page before we know if auth is needed.
         */
        isReady: (state) => state.checking === false,

        /**
         * Whether the user needs to log in.
         * False if no password is configured or already authenticated.
         */
        needsLogin: (state) => {
            if (state.checking) return false
            return state.passwordRequired && !state.authenticated
        },

        /**
         * True when the initial auth check is failing due to network errors
         * and is retrying. Used to show a "Connecting..." overlay instead of
         * the login page (e.g. after restart all, Vite reloads before backend
         * is ready).
         */
        isConnecting: (state) => state.checking && state.connectionError,
    },

    actions: {
        /**
         * Check authentication status with the server.
         * Retries with exponential backoff on network errors (backend not ready).
         * Called once at app startup via the router guard.
         */
        async checkAuth() {
            this.checking = true
            this.connectionError = false

            const MAX_RETRIES = 20 // ~2 minutes total with exponential backoff
            const BASE_DELAY = 500 // ms
            const MAX_DELAY = 5000 // ms

            for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
                try {
                    const res = await fetch('/api/auth/check/')
                    if (!res.ok) throw new Error(`Auth check failed: ${res.status}`)
                    const data = await res.json()
                    this.authenticated = data.authenticated
                    this.passwordRequired = data.password_required
                    this.connectionError = false
                    this.checking = false
                    return // Success
                } catch (e) {
                    console.warn(`Auth check attempt ${attempt + 1} failed:`, e.message)

                    if (attempt < MAX_RETRIES) {
                        this.connectionError = true
                        const delay = Math.min(BASE_DELAY * Math.pow(2, attempt), MAX_DELAY)
                        await new Promise(resolve => setTimeout(resolve, delay))
                    } else {
                        // All retries exhausted — fall back to requiring auth (safe default)
                        console.error('Auth check failed after all retries, assuming auth required')
                        this.authenticated = false
                        this.passwordRequired = true
                        this.connectionError = false
                        this.checking = false
                    }
                }
            }
        },

        /**
         * Single-attempt auth check (no retries, no loading state change).
         * Used for periodic polling from the login page to detect when the
         * backend becomes ready or when password config changes.
         */
        async checkAuthOnce() {
            try {
                const res = await fetch('/api/auth/check/')
                if (!res.ok) return // Silently ignore errors
                const data = await res.json()
                this.authenticated = data.authenticated
                this.passwordRequired = data.password_required
                // If we were in a connection error state, clear it
                if (this.connectionError) {
                    this.connectionError = false
                    this.checking = false
                }
            } catch {
                // Network error — ignore, will retry on next interval
            }
        },

        /**
         * Attempt to log in with a password.
         * @param {string} password
         * @returns {{ success: boolean, error?: string }}
         */
        async login(password) {
            try {
                const res = await fetch('/api/auth/login/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ password }),
                })
                const data = await res.json()

                if (res.ok) {
                    this.authenticated = true
                    return { success: true }
                } else if (res.status === 400 && data.error === 'No password configured') {
                    // Password was removed from config — re-check auth state
                    await this.checkAuthOnce()
                    if (!this.needsLogin) {
                        return { success: true }
                    }
                    return { success: false, error: data.error }
                } else {
                    return { success: false, error: data.error || 'Invalid password' }
                }
            } catch (e) {
                console.error('Login failed:', e)
                return { success: false, error: 'Network error' }
            }
        },

        /**
         * Log out and clear session.
         */
        async logout() {
            try {
                await fetch('/api/auth/logout/', { method: 'POST' })
            } catch (e) {
                console.error('Logout failed:', e)
            }
            this.authenticated = false
        },

        /**
         * Called when a 401 response is received from any API call.
         * Redirects to login.
         */
        handleUnauthorized() {
            if (this.authenticated !== false) {
                this.authenticated = false
            }
        },
    },
})

// Pinia HMR support: allows Vite to hot-replace the store definition
// without propagating the update to importers (like main.js), which would
// cause a full page reload.
if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useAuthStore, import.meta.hot))
}
