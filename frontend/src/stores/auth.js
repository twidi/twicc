import { remoteUiMessage } from '../i18n/remoteMessages.js'
import { i18n } from '../i18n/index.js'
import { localUiMessage } from '../i18n/uiMessages.js'
// frontend/src/stores/auth.js

import { defineStore, acceptHMRUpdate } from 'pinia'

export const useAuthStore = defineStore('auth', {
    state: () => ({
        // null = not checked yet, true = authenticated, false = not authenticated
        authenticated: null,
        // Whether the server requires a password
        passwordRequired: null,
        // Available before login; the WebSocket supplies version updates after login.
        serverVersion: null,
        // Loading state for initial check
        checking: true,
        // True when checkAuth is retrying after network errors
        connectionError: false,
        // Set (to a reason string) when the server refuses this request because
        // the instance has no password and is being reached over a non-local
        // URL. main.js renders a terminal access-blocked screen when truthy.
        accessDenied: null,
        // Command the operator must run to set a password (carries the correct
        // launch prefix for how this TwiCC was started). Shown on that screen.
        setPasswordCommand: null,
        // ── Multi-user (design docs/plans/2026-11-18-multi-user-design.md) ──
        // At least one account exists: the login screen offers email + password.
        accountsEnabled: false,
        // Google sign-in is configured on the server.
        googleEnabled: false,
        // The instance (owner) password is set.
        ownerPassword: false,
        // The signed-in identity: {kind: 'owner'|'user', id, email, display_name,
        // role: 'admin'|'member', is_admin, avatar_url} — null when signed out.
        principal: null,
        // Refusal code of the last Google sign-in (single-use, from /api/auth/check/).
        googleError: null,
        // From /api/me/: null = unrestricted (admin/owner), else {projectId: 'view'|'operate'}.
        projectAccess: null,
        // Workspace ids a member may see (null = unrestricted).
        visibleWorkspaceIds: null,
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

        /**
         * Whether the caller is an admin. True for the owner (instance password,
         * unprotected local mode) and admin accounts; also true while the
         * principal is unknown so a single-user install never loses UI.
         */
        isAdmin: (state) => !state.principal || state.principal.is_admin !== false,

        /** Whether the caller is a member account (restricted to granted projects). */
        isMember: (state) => !!state.principal && state.principal.is_admin === false,

        /**
         * Whether the caller may view a project. Unrestricted callers see everything.
         * Returns a function (Pinia getter with argument).
         */
        canView: (state) => (projectId) => {
            if (!state.projectAccess) return true
            return !!projectId && Object.hasOwn(state.projectAccess, projectId)
        },

        /** Whether the caller may drive agents / write in a project. */
        canOperate: (state) => (projectId) => {
            if (!state.projectAccess) return true
            return !!projectId && state.projectAccess[projectId] === 'operate'
        },
    },

    actions: {
        /** Apply a /api/auth/check/ payload. */
        applyCheck(data) {
            this.authenticated = data.authenticated
            this.passwordRequired = data.password_required
            this.serverVersion = typeof data.version === 'string' ? data.version : null
            this.accessDenied = data.access_denied || null
            this.setPasswordCommand = data.set_password_command || null
            this.accountsEnabled = !!data.accounts_enabled
            this.googleEnabled = !!data.google_enabled
            this.ownerPassword = !!data.owner_password
            this.principal = data.principal || null
            if (data.google_error) this.googleError = data.google_error
        },

        /**
         * Load what the caller may reach (/api/me/). Unrestricted callers get
         * null maps; on failure the previous state is kept.
         */
        async fetchMe() {
            try {
                const res = await fetch('/api/me/')
                if (!res.ok) return
                const data = await res.json()
                if (data.principal) this.principal = data.principal
                this.projectAccess = data.unrestricted ? null : (data.projects || {})
                this.visibleWorkspaceIds = data.unrestricted ? null : (data.workspace_ids || [])
            } catch {
                // Network error — keep the previous access state.
            }
        },

        /**
         * React to an ``accounts_changed`` broadcast: re-check identity and access.
         * Returns true when what this caller may see changed (role or project map),
         * so the caller can reload the views that depend on it.
         */
        async refreshAccess() {
            const before = JSON.stringify([this.principal?.id, this.principal?.role, this.projectAccess, this.visibleWorkspaceIds])
            await this.checkAuthOnce()
            if (this.authenticated) await this.fetchMe()
            const after = JSON.stringify([this.principal?.id, this.principal?.role, this.projectAccess, this.visibleWorkspaceIds])
            return before !== after
        },

        /** Read and clear the last Google sign-in refusal code. */
        consumeGoogleError() {
            const code = this.googleError
            this.googleError = null
            return code
        },

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
                    this.applyCheck(data)
                    if (this.authenticated) await this.fetchMe()
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
                this.applyCheck(data)
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
         * Attempt to log in with a password, as an account (with ``email``) or
         * as the instance owner (without).
         * @param {string} password
         * @param {string} [email]
         * @returns {{ success: boolean, error?: string }}
         */
        async login(password, email = '') {
            try {
                const body = email ? { email, password } : { password }
                const res = await fetch('/api/auth/login/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body),
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
                    return { success: false, error: i18n.global.t('auth.errors.noPassword'), errorMessage: localUiMessage('auth.errors.noPassword') }
                } else {
                    return { success: false, error: data.error || i18n.global.t('auth.errors.invalidPassword'),
                        errorMessage: remoteUiMessage(data, data.error || localUiMessage('auth.errors.invalidPassword')) }
                }
            } catch (e) {
                console.error('Login failed:', e)
                return { success: false, error: i18n.global.t('auth.errors.network'), errorMessage: localUiMessage('auth.errors.network') }
            }
        },

        /**
         * Log out and clear session.
         */
        async logout() {
            const { useTextToSpeechStore } = await import('./textToSpeech.js')
            const { useTimelineSpeechStore } = await import('./timelineSpeech.js')
            const { stopAndroidPush } = await import('../utils/androidPush.js')
            await stopAndroidPush().catch(() => {})
            useTimelineSpeechStore().disable()
            await useTextToSpeechStore().stopAll()
            try {
                await fetch('/api/auth/logout/', { method: 'POST' })
            } catch (e) {
                console.error('Logout failed:', e)
            }
            this.authenticated = false
            this.principal = null
            this.projectAccess = null
            this.visibleWorkspaceIds = null
        },

        /**
         * Called when a 401 response is received from any API call.
         * Redirects to login.
         */
        async handleUnauthorized() {
            if (this.authenticated !== false) {
                this.authenticated = false
            }
            // A real authorization loss stops the owner before navigation. A visit to /login alone does not.
            const { useTextToSpeechStore } = await import('./textToSpeech.js')
            await useTextToSpeechStore().stopAll()
        },
    },
})

// Pinia HMR support: allows Vite to hot-replace the store definition
// without propagating the update to importers (like main.js), which would
// cause a full page reload.
if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useAuthStore, import.meta.hot))
}
