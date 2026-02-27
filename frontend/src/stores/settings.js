// frontend/src/stores/settings.js
// Persistent settings store with localStorage + backend sync for global settings

import { defineStore, acceptHMRUpdate } from 'pinia'
import { watch } from 'vue'
import { DEFAULT_DISPLAY_MODE, DEFAULT_THEME_MODE, DEFAULT_SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT, DEFAULT_MAX_CACHED_SESSIONS, DEFAULT_PERMISSION_MODE, DEFAULT_MODEL, DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT, PERMISSION_MODE, MODEL, SYNCED_SETTINGS_KEYS } from '../constants'
import { NOTIFICATION_SOUNDS } from '../utils/notificationSounds'
// Note: useDataStore is imported lazily to avoid circular dependency (settings.js ↔ data.js)
import { setThemeMode } from '../utils/theme'

const STORAGE_KEY = 'twicc-settings'

/**
 * Settings schema with default values.
 * When adding new settings: add them here with their default value.
 * When removing settings: just remove them from here (they'll be cleaned from localStorage).
 */
const SETTINGS_SCHEMA = {
    displayMode: DEFAULT_DISPLAY_MODE,
    fontSize: 16,
    themeMode: DEFAULT_THEME_MODE,
    sessionTimeFormat: DEFAULT_SESSION_TIME_FORMAT,
    titleGenerationEnabled: true,
    titleSystemPrompt: DEFAULT_TITLE_SYSTEM_PROMPT,
    showCosts: true,
    extraUsageOnlyWhenNeeded: true,
    maxCachedSessions: DEFAULT_MAX_CACHED_SESSIONS,
    autoUnpinOnArchive: true,
    terminalUseTmux: false,
    diffSideBySide: true,
    editorWordWrap: true,
    compactSessionList: false,
    defaultPermissionMode: DEFAULT_PERMISSION_MODE,
    alwaysApplyDefaultPermissionMode: false,
    defaultModel: DEFAULT_MODEL,
    alwaysApplyDefaultModel: false,
    // Notification settings: sound + browser notification for each event type
    notifUserTurnSound: NOTIFICATION_SOUNDS.NONE,
    notifUserTurnBrowser: false,
    notifPendingRequestSound: NOTIFICATION_SOUNDS.NONE,
    notifPendingRequestBrowser: false,
    // Not persisted - computed at runtime based on themeMode and system preference
    _effectiveTheme: null,
    // Not persisted - detected once at startup, true when primary input is touch
    _isTouchDevice: false,
    // Not persisted - guard flag to prevent re-broadcasting when applying remote settings
    _isApplyingRemoteSettings: false,
}

/**
 * Validators for each setting.
 * Returns true if the value is valid, false otherwise.
 * Invalid values will be replaced with defaults.
 */
const SETTINGS_VALIDATORS = {
    displayMode: (v) => [DISPLAY_MODE.CONVERSATION, DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.NORMAL, DISPLAY_MODE.DEBUG].includes(v),
    fontSize: (v) => typeof v === 'number' && v >= 12 && v <= 32,
    themeMode: (v) => [THEME_MODE.SYSTEM, THEME_MODE.LIGHT, THEME_MODE.DARK].includes(v),
    sessionTimeFormat: (v) => [SESSION_TIME_FORMAT.TIME, SESSION_TIME_FORMAT.RELATIVE_SHORT, SESSION_TIME_FORMAT.RELATIVE_NARROW].includes(v),
    titleGenerationEnabled: (v) => typeof v === 'boolean',
    titleSystemPrompt: (v) => typeof v === 'string' && v.includes('{text}'),
    showCosts: (v) => typeof v === 'boolean',
    extraUsageOnlyWhenNeeded: (v) => typeof v === 'boolean',
    maxCachedSessions: (v) => typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= 50,
    autoUnpinOnArchive: (v) => typeof v === 'boolean',
    terminalUseTmux: (v) => typeof v === 'boolean',
    diffSideBySide: (v) => typeof v === 'boolean',
    editorWordWrap: (v) => typeof v === 'boolean',
    compactSessionList: (v) => typeof v === 'boolean',
    defaultPermissionMode: (v) => Object.values(PERMISSION_MODE).includes(v),
    alwaysApplyDefaultPermissionMode: (v) => typeof v === 'boolean',
    defaultModel: (v) => Object.values(MODEL).includes(v),
    alwaysApplyDefaultModel: (v) => typeof v === 'boolean',
    notifUserTurnSound: (v) => Object.values(NOTIFICATION_SOUNDS).includes(v),
    notifUserTurnBrowser: (v) => typeof v === 'boolean',
    notifPendingRequestSound: (v) => Object.values(NOTIFICATION_SOUNDS).includes(v),
    notifPendingRequestBrowser: (v) => typeof v === 'boolean',
}

/**
 * Load settings from localStorage, merge with schema, and clean up.
 * - Unknown keys (removed settings) are discarded
 * - Missing keys (new settings) get default values
 * - Invalid values get replaced with defaults
 * @returns {Object} Clean settings object matching the schema
 */
function loadSettings() {
    const settings = { ...SETTINGS_SCHEMA }

    try {
        const stored = localStorage.getItem(STORAGE_KEY)
        if (stored) {
            const parsed = JSON.parse(stored)

            // Migrate legacy baseDisplayMode + debugEnabled → displayMode
            if ('baseDisplayMode' in parsed || 'debugEnabled' in parsed) {
                const debugEnabled = parsed.debugEnabled === true
                const baseMode = parsed.baseDisplayMode || DEFAULT_DISPLAY_MODE
                parsed.displayMode = debugEnabled ? DISPLAY_MODE.DEBUG : baseMode
                delete parsed.baseDisplayMode
                delete parsed.debugEnabled
            }

            // Only keep keys that exist in schema and have valid values
            for (const key of Object.keys(SETTINGS_SCHEMA)) {
                if (key in parsed) {
                    const validator = SETTINGS_VALIDATORS[key]
                    if (!validator || validator(parsed[key])) {
                        settings[key] = parsed[key]
                    }
                    // If validation fails, keep the default
                }
            }
        }
    } catch (e) {
        console.warn('Failed to load settings from localStorage:', e)
    }

    // Save cleaned settings back to localStorage
    saveSettings(settings)

    return settings
}

/**
 * Save settings to localStorage.
 * @param {Object} settings - Settings object to save
 */
function saveSettings(settings) {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(settings))
    } catch (e) {
        console.warn('Failed to save settings to localStorage:', e)
    }
}

export const useSettingsStore = defineStore('settings', {
    state: () => loadSettings(),

    getters: {
        /**
         * Current display mode: 'simplified', 'normal', or 'debug'.
         */
        getDisplayMode: (state) => state.displayMode,
        getFontSize: (state) => state.fontSize,
        getThemeMode: (state) => state.themeMode,
        getSessionTimeFormat: (state) => state.sessionTimeFormat,
        isTitleGenerationEnabled: (state) => state.titleGenerationEnabled,
        getTitleSystemPrompt: (state) => state.titleSystemPrompt,
        areCostsShown: (state) => state.showCosts,
        isExtraUsageOnlyWhenNeeded: (state) => state.extraUsageOnlyWhenNeeded,
        getMaxCachedSessions: (state) => state.maxCachedSessions,
        isAutoUnpinOnArchive: (state) => state.autoUnpinOnArchive,
        isTerminalUseTmux: (state) => state.terminalUseTmux,
        isDiffSideBySide: (state) => state.diffSideBySide,
        isEditorWordWrap: (state) => state.editorWordWrap,
        isCompactSessionList: (state) => state.compactSessionList,
        getDefaultPermissionMode: (state) => state.defaultPermissionMode,
        isAlwaysApplyDefaultPermissionMode: (state) => state.alwaysApplyDefaultPermissionMode,
        getDefaultModel: (state) => state.defaultModel,
        isAlwaysApplyDefaultModel: (state) => state.alwaysApplyDefaultModel,
        getNotifUserTurnSound: (state) => state.notifUserTurnSound,
        isNotifUserTurnBrowser: (state) => state.notifUserTurnBrowser,
        getNotifPendingRequestSound: (state) => state.notifPendingRequestSound,
        isNotifPendingRequestBrowser: (state) => state.notifPendingRequestBrowser,
        /**
         * Effective theme: always returns 'light' or 'dark', never 'system'.
         * Takes into account the system preference when themeMode is 'system'.
         */
        getEffectiveTheme: (state) => state._effectiveTheme,
        /**
         * Whether the primary input device is touch (no hover support).
         * Detected once at startup. Used to disable tooltips on touch devices.
         */
        isTouchDevice: (state) => state._isTouchDevice,
    },

    actions: {
        /**
         * Set display mode.
         * @param {string} mode - 'simplified' | 'normal' | 'debug'
         */
        setDisplayMode(mode) {
            if (SETTINGS_VALIDATORS.displayMode(mode)) {
                this.displayMode = mode
            }
        },

        /**
         * Set the global font size.
         * @param {number} size - Font size in pixels (12-32)
         */
        setFontSize(size) {
            const numSize = Number(size)
            if (SETTINGS_VALIDATORS.fontSize(numSize)) {
                this.fontSize = numSize
            }
        },

        /**
         * Set the theme mode.
         * @param {string} mode - 'system' | 'light' | 'dark'
         */
        setThemeMode(mode) {
            if (SETTINGS_VALIDATORS.themeMode(mode)) {
                this.themeMode = mode
            }
        },

        /**
         * Set the session time format.
         * @param {string} format - 'time' | 'relative'
         */
        setSessionTimeFormat(format) {
            if (SETTINGS_VALIDATORS.sessionTimeFormat(format)) {
                this.sessionTimeFormat = format
            }
        },

        /**
         * Toggle title generation enabled/disabled.
         * @param {boolean} enabled
         */
        setTitleGenerationEnabled(enabled) {
            if (SETTINGS_VALIDATORS.titleGenerationEnabled(enabled)) {
                this.titleGenerationEnabled = enabled
            }
        },

        /**
         * Set the title system prompt.
         * @param {string} prompt - Must contain {text} placeholder
         */
        setTitleSystemPrompt(prompt) {
            if (SETTINGS_VALIDATORS.titleSystemPrompt(prompt)) {
                this.titleSystemPrompt = prompt
            }
        },

        /**
         * Reset the title system prompt to default.
         */
        resetTitleSystemPrompt() {
            this.titleSystemPrompt = DEFAULT_TITLE_SYSTEM_PROMPT
        },

        /**
         * Set costs display enabled/disabled.
         * @param {boolean} enabled
         */
        setShowCosts(enabled) {
            if (SETTINGS_VALIDATORS.showCosts(enabled)) {
                this.showCosts = enabled
            }
        },

        /**
         * Set extra usage "only when needed" mode.
         * @param {boolean} enabled
         */
        setExtraUsageOnlyWhenNeeded(enabled) {
            if (SETTINGS_VALIDATORS.extraUsageOnlyWhenNeeded(enabled)) {
                this.extraUsageOnlyWhenNeeded = enabled
            }
        },

        /**
         * Set the maximum number of cached sessions (KeepAlive).
         * @param {number} count - Number of sessions to keep alive (1-50)
         */
        setMaxCachedSessions(count) {
            const numCount = Number(count)
            if (SETTINGS_VALIDATORS.maxCachedSessions(numCount)) {
                this.maxCachedSessions = numCount
            }
        },

        /**
         * Set auto-unpin on archive enabled/disabled.
         * @param {boolean} enabled
         */
        setAutoUnpinOnArchive(enabled) {
            if (SETTINGS_VALIDATORS.autoUnpinOnArchive(enabled)) {
                this.autoUnpinOnArchive = enabled
            }
        },

        /**
         * Set terminal tmux persistence enabled/disabled.
         * @param {boolean} enabled
         */
        setTerminalUseTmux(enabled) {
            if (SETTINGS_VALIDATORS.terminalUseTmux(enabled)) {
                this.terminalUseTmux = enabled
            }
        },

        /**
         * Set diff side-by-side default mode.
         * @param {boolean} enabled
         */
        setDiffSideBySide(enabled) {
            if (SETTINGS_VALIDATORS.diffSideBySide(enabled)) {
                this.diffSideBySide = enabled
            }
        },

        /**
         * Set editor word wrap mode.
         * @param {boolean} enabled
         */
        setEditorWordWrap(enabled) {
            if (SETTINGS_VALIDATORS.editorWordWrap(enabled)) {
                this.editorWordWrap = enabled
            }
        },

        /**
         * Set compact session list mode.
         * @param {boolean} enabled
         */
        setCompactSessionList(enabled) {
            if (SETTINGS_VALIDATORS.compactSessionList(enabled)) {
                this.compactSessionList = enabled
            }
        },

        /**
         * Set the default permission mode for new sessions.
         * @param {string} mode - One of PERMISSION_MODE values
         */
        setDefaultPermissionMode(mode) {
            if (SETTINGS_VALIDATORS.defaultPermissionMode(mode)) {
                this.defaultPermissionMode = mode
            }
        },

        /**
         * Set whether the default permission mode should always be applied,
         * even for sessions that have an explicit mode in the database.
         * @param {boolean} enabled
         */
        setAlwaysApplyDefaultPermissionMode(enabled) {
            if (SETTINGS_VALIDATORS.alwaysApplyDefaultPermissionMode(enabled)) {
                this.alwaysApplyDefaultPermissionMode = enabled
            }
        },

        /**
         * Set the default Claude model for new sessions.
         * @param {string} model - One of MODEL values
         */
        setDefaultModel(model) {
            if (SETTINGS_VALIDATORS.defaultModel(model)) {
                this.defaultModel = model
            }
        },

        /**
         * Set whether the default Claude model should always be applied,
         * even for sessions that have an explicit model in the database.
         * @param {boolean} enabled
         */
        setAlwaysApplyDefaultModel(enabled) {
            if (SETTINGS_VALIDATORS.alwaysApplyDefaultModel(enabled)) {
                this.alwaysApplyDefaultModel = enabled
            }
        },

        /**
         * Set notification sound for user turn events.
         * @param {string} sound - One of NOTIFICATION_SOUNDS values
         */
        setNotifUserTurnSound(sound) {
            if (SETTINGS_VALIDATORS.notifUserTurnSound(sound)) {
                this.notifUserTurnSound = sound
            }
        },

        /**
         * Set browser notification for user turn events.
         * @param {boolean} enabled
         */
        setNotifUserTurnBrowser(enabled) {
            if (SETTINGS_VALIDATORS.notifUserTurnBrowser(enabled)) {
                this.notifUserTurnBrowser = enabled
            }
        },

        /**
         * Set notification sound for pending request events.
         * @param {string} sound - One of NOTIFICATION_SOUNDS values
         */
        setNotifPendingRequestSound(sound) {
            if (SETTINGS_VALIDATORS.notifPendingRequestSound(sound)) {
                this.notifPendingRequestSound = sound
            }
        },

        /**
         * Set browser notification for pending request events.
         * @param {boolean} enabled
         */
        setNotifPendingRequestBrowser(enabled) {
            if (SETTINGS_VALIDATORS.notifPendingRequestBrowser(enabled)) {
                this.notifPendingRequestBrowser = enabled
            }
        },

        /**
         * Apply synced settings received from the backend.
         * Merges with schema: validates each key, ignores unknown keys,
         * keeps current value if validation fails.
         * Sets a guard flag to prevent the synced-settings watcher from
         * sending these values back to the backend.
         * @param {Object} remoteSettings - Settings object from backend
         */
        applySyncedSettings(remoteSettings) {
            if (!remoteSettings || typeof remoteSettings !== 'object') return
            this._isApplyingRemoteSettings = true
            for (const key of SYNCED_SETTINGS_KEYS) {
                if (key in remoteSettings) {
                    const validator = SETTINGS_VALIDATORS[key]
                    if (!validator || validator(remoteSettings[key])) {
                        this[key] = remoteSettings[key]
                    }
                }
            }
            this._isApplyingRemoteSettings = false
        },

        /**
         * Update the effective theme based on themeMode and system preference.
         * Called internally when themeMode changes or system preference changes.
         */
        _updateEffectiveTheme() {
            if (this.themeMode === THEME_MODE.SYSTEM) {
                this._effectiveTheme = window.matchMedia('(prefers-color-scheme: dark)').matches
                    ? THEME_MODE.DARK
                    : THEME_MODE.LIGHT
            } else {
                this._effectiveTheme = this.themeMode
            }
        },
    },
})

/**
 * Initialize settings store: apply initial values and set up watchers.
 * Call this once after Pinia is installed.
 * Handles:
 * - localStorage persistence (auto-save on changes)
 * - Theme mode changes
 * - Font size application
 * - Display mode changes (triggers visual items recompute)
 *
 * Note: Theme is applied early in main.js before CSS imports to prevent flash.
 * This function only sets up the watcher for subsequent theme changes.
 */
export function initSettings() {
    const store = useSettingsStore()

    // Apply initial font size (theme is already applied in main.js)
    document.documentElement.style.fontSize = `${store.fontSize}px`

    // Watch all state changes and save to localStorage
    // Note: _effectiveTheme is excluded as it's computed at runtime
    watch(
        () => ({
            displayMode: store.displayMode,
            fontSize: store.fontSize,
            themeMode: store.themeMode,
            sessionTimeFormat: store.sessionTimeFormat,
            titleGenerationEnabled: store.titleGenerationEnabled,
            titleSystemPrompt: store.titleSystemPrompt,
            showCosts: store.showCosts,
            extraUsageOnlyWhenNeeded: store.extraUsageOnlyWhenNeeded,
            maxCachedSessions: store.maxCachedSessions,
            autoUnpinOnArchive: store.autoUnpinOnArchive,
            terminalUseTmux: store.terminalUseTmux,
            diffSideBySide: store.diffSideBySide,
            editorWordWrap: store.editorWordWrap,
            compactSessionList: store.compactSessionList,
            defaultPermissionMode: store.defaultPermissionMode,
            alwaysApplyDefaultPermissionMode: store.alwaysApplyDefaultPermissionMode,
            defaultModel: store.defaultModel,
            alwaysApplyDefaultModel: store.alwaysApplyDefaultModel,
            notifUserTurnSound: store.notifUserTurnSound,
            notifUserTurnBrowser: store.notifUserTurnBrowser,
            notifPendingRequestSound: store.notifPendingRequestSound,
            notifPendingRequestBrowser: store.notifPendingRequestBrowser,
        }),
        (newSettings) => {
            saveSettings(newSettings)
        },
        { deep: true }
    )

    // Watch for theme changes
    watch(() => store.themeMode, (mode) => {
        setThemeMode(mode)
        store._updateEffectiveTheme()
    })

    // Detect touch device once at startup (primary input has no hover support)
    store._isTouchDevice = window.matchMedia('(hover: none)').matches

    // Initialize effective theme and listen for system preference changes
    store._updateEffectiveTheme()
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        store._updateEffectiveTheme()
    })

    // Watch for font size changes
    watch(() => store.fontSize, (size) => {
        document.documentElement.style.fontSize = `${size}px`
    })

    // Watch synced settings and send to backend when changed by the user.
    // The guard flag (_isApplyingRemoteSettings) prevents re-sending when
    // changes come from the backend via WebSocket.
    // Lazy import of useWebSocket avoids circular dependency (settings.js ↔ useWebSocket.js).
    watch(
        () => {
            const synced = {}
            for (const key of SYNCED_SETTINGS_KEYS) {
                synced[key] = store[key]
            }
            return synced
        },
        async (newSynced) => {
            if (store._isApplyingRemoteSettings) return
            const { sendSyncedSettings } = await import('../composables/useWebSocket')
            sendSyncedSettings(newSynced)
        },
        { deep: true }
    )

    // Watch for display mode changes
    // Recompute all visual items when display mode changes
    watch(
        () => store.getDisplayMode,
        async () => {
            // Lazy import to avoid circular dependency (settings.js ↔ data.js)
            const { useDataStore } = await import('./data')
            const dataStore = useDataStore()
            dataStore.recomputeAllVisualItems()
        }
    )
}

// Pinia HMR support: allows Vite to hot-replace the store definition
// without propagating the update to importers (like main.js), which would
// cause a full page reload.
if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useSettingsStore, import.meta.hot))
}
