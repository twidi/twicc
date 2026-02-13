// frontend/src/stores/settings.js
// Persistent settings store with localStorage support

import { defineStore } from 'pinia'
import { watch } from 'vue'
import { DEFAULT_DISPLAY_MODE, DEFAULT_THEME_MODE, DEFAULT_SESSION_TIME_FORMAT, DEFAULT_TITLE_SYSTEM_PROMPT, DEFAULT_MAX_CACHED_SESSIONS, DISPLAY_MODE, THEME_MODE, SESSION_TIME_FORMAT } from '../constants'
import { useDataStore } from './data'
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
    tooltipsEnabled: true,
    titleGenerationEnabled: true,
    titleSystemPrompt: DEFAULT_TITLE_SYSTEM_PROMPT,
    autoHideHeaderFooter: false,
    extraUsageOnlyWhenNeeded: true,
    maxCachedSessions: DEFAULT_MAX_CACHED_SESSIONS,
    autoUnpinOnArchive: true,
    terminalUseTmux: false,
    diffSideBySide: true,
    // Not persisted - computed at runtime based on themeMode and system preference
    _effectiveTheme: null,
}

/**
 * Validators for each setting.
 * Returns true if the value is valid, false otherwise.
 * Invalid values will be replaced with defaults.
 */
const SETTINGS_VALIDATORS = {
    displayMode: (v) => [DISPLAY_MODE.NORMAL, DISPLAY_MODE.SIMPLIFIED, DISPLAY_MODE.DEBUG].includes(v),
    fontSize: (v) => typeof v === 'number' && v >= 12 && v <= 32,
    themeMode: (v) => [THEME_MODE.SYSTEM, THEME_MODE.LIGHT, THEME_MODE.DARK].includes(v),
    sessionTimeFormat: (v) => [SESSION_TIME_FORMAT.TIME, SESSION_TIME_FORMAT.RELATIVE_SHORT, SESSION_TIME_FORMAT.RELATIVE_NARROW].includes(v),
    tooltipsEnabled: (v) => typeof v === 'boolean',
    titleGenerationEnabled: (v) => typeof v === 'boolean',
    titleSystemPrompt: (v) => typeof v === 'string' && v.includes('{text}'),
    autoHideHeaderFooter: (v) => typeof v === 'boolean',
    extraUsageOnlyWhenNeeded: (v) => typeof v === 'boolean',
    maxCachedSessions: (v) => typeof v === 'number' && Number.isInteger(v) && v >= 1 && v <= 50,
    autoUnpinOnArchive: (v) => typeof v === 'boolean',
    terminalUseTmux: (v) => typeof v === 'boolean',
    diffSideBySide: (v) => typeof v === 'boolean',
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
        areTooltipsEnabled: (state) => state.tooltipsEnabled,
        isTitleGenerationEnabled: (state) => state.titleGenerationEnabled,
        getTitleSystemPrompt: (state) => state.titleSystemPrompt,
        isAutoHideHeaderFooterEnabled: (state) => state.autoHideHeaderFooter,
        isExtraUsageOnlyWhenNeeded: (state) => state.extraUsageOnlyWhenNeeded,
        getMaxCachedSessions: (state) => state.maxCachedSessions,
        isAutoUnpinOnArchive: (state) => state.autoUnpinOnArchive,
        isTerminalUseTmux: (state) => state.terminalUseTmux,
        isDiffSideBySide: (state) => state.diffSideBySide,
        /**
         * Effective theme: always returns 'light' or 'dark', never 'system'.
         * Takes into account the system preference when themeMode is 'system'.
         */
        getEffectiveTheme: (state) => state._effectiveTheme,
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
         * Set tooltips enabled/disabled.
         * @param {boolean} enabled
         */
        setTooltipsEnabled(enabled) {
            if (SETTINGS_VALIDATORS.tooltipsEnabled(enabled)) {
                this.tooltipsEnabled = enabled
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
         * Set auto-hide header/footer on scroll enabled/disabled.
         * @param {boolean} enabled
         */
        setAutoHideHeaderFooter(enabled) {
            if (SETTINGS_VALIDATORS.autoHideHeaderFooter(enabled)) {
                this.autoHideHeaderFooter = enabled
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
            tooltipsEnabled: store.tooltipsEnabled,
            titleGenerationEnabled: store.titleGenerationEnabled,
            titleSystemPrompt: store.titleSystemPrompt,
            autoHideHeaderFooter: store.autoHideHeaderFooter,
            extraUsageOnlyWhenNeeded: store.extraUsageOnlyWhenNeeded,
            maxCachedSessions: store.maxCachedSessions,
            autoUnpinOnArchive: store.autoUnpinOnArchive,
            terminalUseTmux: store.terminalUseTmux,
            diffSideBySide: store.diffSideBySide,
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

    // Initialize effective theme and listen for system preference changes
    store._updateEffectiveTheme()
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
        store._updateEffectiveTheme()
    })

    // Watch for font size changes
    watch(() => store.fontSize, (size) => {
        document.documentElement.style.fontSize = `${size}px`
    })

    // Watch for display mode changes
    // Recompute all visual items when display mode changes
    watch(
        () => store.getDisplayMode,
        () => {
            const dataStore = useDataStore()
            dataStore.recomputeAllVisualItems()
        }
    )
}
