// frontend/src/utils/theme.js
// Theme management utilities - extracted to avoid circular imports with main.js

let currentThemeMode = localStorage.getItem('twicc-settings')
    ? JSON.parse(localStorage.getItem('twicc-settings')).themeMode || 'system'
    : 'system'

function applyColorScheme() {
    let isDark
    if (currentThemeMode === 'system') {
        isDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    } else {
        isDark = currentThemeMode === 'dark'
    }
    document.documentElement.classList.toggle('wa-dark', isDark)
    // Also set data-theme for github-markdown-css compatibility
    document.documentElement.dataset.theme = isDark ? 'dark' : 'light'
}

/**
 * Set the theme mode and apply the color scheme.
 * Called by settings store when theme mode changes.
 * @param {string} mode - 'system' | 'light' | 'dark'
 */
export function setThemeMode(mode) {
    currentThemeMode = mode
    applyColorScheme()
}

/**
 * Initialize theme on app startup.
 * Apply initial theme and listen for system preference changes.
 */
export function initTheme() {
    applyColorScheme()
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', applyColorScheme)
}
