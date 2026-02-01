// frontend/src/stores/settings.js
// Persistent settings store with localStorage support

import { defineStore } from 'pinia'
import { watch } from 'vue'
import { DEFAULT_DISPLAY_MODE, DEFAULT_THEME_MODE, DEFAULT_PROCESS_INDICATOR, DISPLAY_MODE, THEME_MODE, PROCESS_INDICATOR } from '../constants'
import { useDataStore } from './data'
import { setThemeMode } from '../main'

const STORAGE_KEY = 'twicc-settings'

/**
 * Settings schema with default values.
 * When adding new settings: add them here with their default value.
 * When removing settings: just remove them from here (they'll be cleaned from localStorage).
 */
const SETTINGS_SCHEMA = {
    baseDisplayMode: DEFAULT_DISPLAY_MODE,
    debugEnabled: false,
    fontSize: 16,
    themeMode: DEFAULT_THEME_MODE,
    processIndicator: DEFAULT_PROCESS_INDICATOR,
}

/**
 * Validators for each setting.
 * Returns true if the value is valid, false otherwise.
 * Invalid values will be replaced with defaults.
 */
const SETTINGS_VALIDATORS = {
    baseDisplayMode: (v) => [DISPLAY_MODE.NORMAL, DISPLAY_MODE.SIMPLIFIED].includes(v),
    debugEnabled: (v) => typeof v === 'boolean',
    fontSize: (v) => typeof v === 'number' && v >= 12 && v <= 32,
    themeMode: (v) => [THEME_MODE.SYSTEM, THEME_MODE.LIGHT, THEME_MODE.DARK].includes(v),
    processIndicator: (v) => [PROCESS_INDICATOR.DOTS, PROCESS_INDICATOR.ICONS].includes(v),
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
         * Effective display mode: 'debug' if debugEnabled, otherwise baseDisplayMode.
         */
        getDisplayMode: (state) =>
            state.debugEnabled ? DISPLAY_MODE.DEBUG : state.baseDisplayMode,

        getBaseDisplayMode: (state) => state.baseDisplayMode,
        isDebugEnabled: (state) => state.debugEnabled,
        getFontSize: (state) => state.fontSize,
        getThemeMode: (state) => state.themeMode,
        getProcessIndicator: (state) => state.processIndicator,
    },

    actions: {
        /**
         * Set base display mode (normal or simplified).
         * @param {string} mode - 'normal' | 'simplified'
         */
        setBaseDisplayMode(mode) {
            if (SETTINGS_VALIDATORS.baseDisplayMode(mode)) {
                this.baseDisplayMode = mode
            }
        },

        /**
         * Set debug mode enabled/disabled.
         * @param {boolean} enabled
         */
        setDebugEnabled(enabled) {
            if (SETTINGS_VALIDATORS.debugEnabled(enabled)) {
                this.debugEnabled = enabled
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
         * Set the process indicator style.
         * @param {string} style - 'dots' | 'icons'
         */
        setProcessIndicator(style) {
            if (SETTINGS_VALIDATORS.processIndicator(style)) {
                this.processIndicator = style
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
    watch(
        () => ({
            baseDisplayMode: store.baseDisplayMode,
            debugEnabled: store.debugEnabled,
            fontSize: store.fontSize,
            themeMode: store.themeMode,
            processIndicator: store.processIndicator,
        }),
        (newSettings) => {
            saveSettings(newSettings)
        },
        { deep: true }
    )

    // Watch for theme changes
    watch(() => store.themeMode, (mode) => {
        setThemeMode(mode)
    })

    // Watch for font size changes
    watch(() => store.fontSize, (size) => {
        document.documentElement.style.fontSize = `${size}px`
    })

    // Watch for display mode changes (baseDisplayMode or debugEnabled)
    // Recompute all visual items when display mode changes
    watch(
        () => store.getDisplayMode,
        () => {
            const dataStore = useDataStore()
            dataStore.recomputeAllVisualItems()
        }
    )
}
