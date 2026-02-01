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

/**
 * Process state values (matches backend ProcessState enum).
 */
export const PROCESS_STATE = {
    STARTING: 'starting',
    ASSISTANT_TURN: 'assistant_turn',
    USER_TURN: 'user_turn',
    DEAD: 'dead',
}

/**
 * CSS color variables for each process state.
 * Used for consistent coloring across components (indicators, text, etc.).
 */
export const PROCESS_STATE_COLORS = {
    [PROCESS_STATE.STARTING]: 'var(--wa-color-warning-60)',
    [PROCESS_STATE.ASSISTANT_TURN]: 'var(--wa-color-brand-60)',
    [PROCESS_STATE.USER_TURN]: 'var(--wa-color-success-60)',
    [PROCESS_STATE.DEAD]: 'var(--wa-color-danger-60)',
}
