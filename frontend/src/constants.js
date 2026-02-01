// frontend/src/constants.js

/**
 * Shared constants for the application.
 */

/**
 * Number of items to load at start (first N and last N) when viewing a session.
 * Also used during reconciliation to limit how many new items we fetch at once.
 */
export const INITIAL_ITEMS_COUNT = 100

/**
 * Display mode values for session items.
 * - debug: Show all items (levels 1, 2, 3)
 * - normal: Show levels 1 and 2, hide level 3
 * - simplified: Show level 1, collapse level 2 groups, hide level 3
 */
export const DISPLAY_MODE = {
    DEBUG: 'debug',
    NORMAL: 'normal',
    SIMPLIFIED: 'simplified',
}

export const DEFAULT_DISPLAY_MODE = DISPLAY_MODE.SIMPLIFIED

/**
 * Theme mode values.
 * - system: Follow system preference (prefers-color-scheme)
 * - light: Force light mode
 * - dark: Force dark mode
 */
export const THEME_MODE = {
    SYSTEM: 'system',
    LIGHT: 'light',
    DARK: 'dark',
}

export const DEFAULT_THEME_MODE = THEME_MODE.SYSTEM

/**
 * Process indicator style values for session list.
 * - dots: Colored dots (yellow=starting, blue=working, green=ready, red=dead)
 * - icons: Icons (spinner=starting, robot=working, hand=ready, warning=dead)
 */
export const PROCESS_INDICATOR = {
    DOTS: 'dots',
    ICONS: 'icons',
}

export const DEFAULT_PROCESS_INDICATOR = PROCESS_INDICATOR.ICONS

/**
 * Maximum context tokens for Claude (used for context usage percentage calculation).
 * Value: 200,000 tokens
 */
export const MAX_CONTEXT_TOKENS = 200_000

/**
 * Display level values for session items (matches backend ItemDisplayLevel enum).
 * - ALWAYS: Always shown in all modes
 * - COLLAPSIBLE: Shown in Normal, grouped in Simplified
 * - DEBUG_ONLY: Only shown in Debug mode
 */
export const DISPLAY_LEVEL = {
    ALWAYS: 1,
    COLLAPSIBLE: 2,
    DEBUG_ONLY: 3,
}
