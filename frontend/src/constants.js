// frontend/src/constants.js

/**
 * Shared constants for the application.
 */

/**
 * Number of items to load at start (first N and last N) when viewing a session.
 * Also used during reconciliation to limit how many new items we fetch at once.
 */
export const INITIAL_ITEMS_COUNT = 20

/**
 * Display mode values for session items.
 * - debug: Show all items (levels 1, 2, 3)
 * - normal: Show levels 1 and 2, hide level 3
 * - simplified: Show level 1, collapse level 2 groups, hide level 3
 */
export const DISPLAY_MODES = {
    DEBUG: 'debug',
    NORMAL: 'normal',
    SIMPLIFIED: 'simplified',
}

export const DEFAULT_DISPLAY_MODE = DISPLAY_MODES.NORMAL
