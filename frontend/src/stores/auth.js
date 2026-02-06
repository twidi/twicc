// frontend/src/stores/auth.js

import { defineStore } from 'pinia'

export const useAuthStore = defineStore('auth', {
    state: () => ({
        // null = not checked yet, true = authenticated, false = not authenticated
        authenticated: null,
        // Whether the server requires a password
        passwordRequired: null,
        // Loading state for initial check
        checking: true,
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
    },

    actions: {
        /**
         * Check authentication status with the server.
         * Called once at app startup.
         */
        async checkAuth() {
            this.checking = true
            try {
                const res = await fetch('/api/auth/check/')
                if (!res.ok) throw new Error('Auth check failed')
                const data = await res.json()
                this.authenticated = data.authenticated
                this.passwordRequired = data.password_required
            } catch (e) {
                console.error('Auth check failed:', e)
                // On network error, assume auth is required (safe default)
                this.authenticated = false
                this.passwordRequired = true
            } finally {
                this.checking = false
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
