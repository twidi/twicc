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
 * - conversation: Show only user messages + last assistant message before each user message
 * - simplified: Show level 1, collapse level 2 groups, hide level 3
 * - normal: Show levels 1 and 2, hide level 3
 * - debug: Show all items (levels 1, 2, 3)
 */
export const DISPLAY_MODE = {
    DEBUG: 'debug',
    NORMAL: 'normal',
    SIMPLIFIED: 'simplified',
    CONVERSATION: 'conversation',
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
 * Session time format values.
 * - time: Show formatted time (smart format: hour if recent, date otherwise)
 * - relative_short: Show relative time with short format ("2 hr. ago")
 * - relative_narrow: Show relative time with narrow format ("2h ago")
 */
export const SESSION_TIME_FORMAT = {
    TIME: 'time',
    RELATIVE_SHORT: 'relative_short',
    RELATIVE_NARROW: 'relative_narrow',
}

export const DEFAULT_SESSION_TIME_FORMAT = SESSION_TIME_FORMAT.TIME

/**
 * Default system prompt for title generation via Haiku.
 * The {text} placeholder will be replaced with the user's message.
 */
export const DEFAULT_TITLE_SYSTEM_PROMPT = `Summarize the following user message in 5-7 words to create a concise session title.
Return ONLY the title, nothing else. No quotes, no explanation, no punctuation at the end.

IMPORTANT: The title must be in the same language as the user message. However, do not translate technical terms or words that are already in another language (e.g., if the user writes in French about code, keep English technical terms as-is).

User message:
{text}`

/**
 * Default maximum number of sessions kept alive in the cache (Vue KeepAlive).
 * Each cached session preserves its DOM, scroll position, and component state
 * for instant switching. Cost is ~150-500 KB per session (more with terminal).
 * Can be adjusted per device in settings.
 */
export const DEFAULT_MAX_CACHED_SESSIONS = 20

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
 * Synthetic items injected client-side (not from backend).
 * Each entry has:
 * - lineNum: negative to avoid collision with real backend line numbers (1-based)
 * - kind: string identifier used as syntheticKind and data-synthetic-kind attribute
 */
export const SYNTHETIC_ITEM = {
    OPTIMISTIC_USER_MESSAGE: { lineNum: -2000, kind: 'optimistic-user-message' },
    WORKING_ASSISTANT_MESSAGE: { lineNum: -1000, kind: 'working-assistant-message' },
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
 * Human-friendly names for each process state.
 */
export const PROCESS_STATE_NAMES = {
    [PROCESS_STATE.STARTING]: 'Starting',
    [PROCESS_STATE.ASSISTANT_TURN]: 'Assistant turn',
    [PROCESS_STATE.USER_TURN]: 'User turn',
    [PROCESS_STATE.DEAD]: 'Dead',
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

/**
 * Permission mode values (matches SDK PermissionMode).
 * Controls how Claude Code handles tool permission prompts.
 */
export const PERMISSION_MODE = {
    DEFAULT: 'default',
    ACCEPT_EDITS: 'acceptEdits',
    PLAN: 'plan',
    DONT_ASK: 'dontAsk',
    BYPASS: 'bypassPermissions',
}

export const DEFAULT_PERMISSION_MODE = PERMISSION_MODE.DEFAULT

/**
 * Human-friendly labels for each permission mode.
 */
export const PERMISSION_MODE_LABELS = {
    [PERMISSION_MODE.DEFAULT]: 'Default',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'Accept Edits',
    [PERMISSION_MODE.PLAN]: 'Plan',
    [PERMISSION_MODE.DONT_ASK]: "Don't Ask",
    [PERMISSION_MODE.BYPASS]: 'Bypass',
}

/**
 * Short descriptions for each permission mode (for tooltips/settings).
 */
export const PERMISSION_MODE_DESCRIPTIONS = {
    [PERMISSION_MODE.DEFAULT]: 'Prompts for permission on first use of each tool',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'Auto-accepts file edit permissions',
    [PERMISSION_MODE.PLAN]: 'Read-only: Claude can analyze but not modify files',
    [PERMISSION_MODE.DONT_ASK]: 'Auto-denies tools unless pre-approved via permission rules',
    [PERMISSION_MODE.BYPASS]: 'Skips all permission prompts',
}

/**
 * FontAwesome icon names for each permission mode (classic/solid style).
 */
export const PERMISSION_MODE_ICONS = {
    [PERMISSION_MODE.DEFAULT]: 'shield-halved',
    [PERMISSION_MODE.ACCEPT_EDITS]: 'pen-to-square',
    [PERMISSION_MODE.PLAN]: 'eye',
    [PERMISSION_MODE.DONT_ASK]: 'lock',
    [PERMISSION_MODE.BYPASS]: 'triangle-exclamation',
}

/**
 * Accent colors for each permission mode icon.
 */
export const PERMISSION_MODE_COLORS = {
    [PERMISSION_MODE.DEFAULT]: '#3b82f6',
    [PERMISSION_MODE.ACCEPT_EDITS]: '#22c55e',
    [PERMISSION_MODE.PLAN]: '#06b6d4',
    [PERMISSION_MODE.DONT_ASK]: '#f59e0b',
    [PERMISSION_MODE.BYPASS]: '#ef4444',
}

/**
 * Model values (matches SDK model parameter).
 * Controls which model Claude Code uses.
 */
export const MODEL = {
    OPUS: 'opus',
    SONNET: 'sonnet',
}

export const DEFAULT_MODEL = MODEL.OPUS

/**
 * Human-friendly labels for each model.
 */
export const MODEL_LABELS = {
    [MODEL.OPUS]: 'Opus',
    [MODEL.SONNET]: 'Sonnet',
}

/**
 * FontAwesome icon names for each model (classic/solid style).
 */
export const MODEL_ICONS = {
    [MODEL.OPUS]: 'star',
    [MODEL.SONNET]: 'bolt',
}

/**
 * Accent colors for each model icon.
 */
export const MODEL_COLORS = {
    [MODEL.OPUS]: '#e0a526',
    [MODEL.SONNET]: '#9b59b6',
}

/**
 * Settings keys that are synced across devices via backend settings.json.
 * All other settings remain local to the browser (localStorage only).
 */
export const SYNCED_SETTINGS_KEYS = new Set([
    'titleGenerationEnabled',
    'titleSystemPrompt',
    'defaultPermissionMode',
    'alwaysApplyDefaultPermissionMode',
    'defaultModel',
    'alwaysApplyDefaultModel',
    'autoUnpinOnArchive',
    'terminalUseTmux',
])
