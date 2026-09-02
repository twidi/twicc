// frontend/src/constants.js

/**
 * Shared constants for the application.
 */

/**
 * GitHub Sponsors page for the project maintainer. Single source of truth for
 * every place that links to it (settings footer, changelog sponsor screen, …).
 */
export const SPONSOR_URL = 'https://github.com/sponsors/twidi'

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

export const DEFAULT_DISPLAY_MODE = DISPLAY_MODE.NORMAL

/**
 * Color scheme values.
 * - system: Follow system preference (prefers-color-scheme)
 * - light: Force light mode
 * - dark: Force dark mode
 */
export const COLOR_SCHEME = {
    SYSTEM: 'system',
    LIGHT: 'light',
    DARK: 'dark',
}

export const DEFAULT_COLOR_SCHEME = COLOR_SCHEME.SYSTEM

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
 * Default maximum number of sessions kept alive in the cache (Vue KeepAlive).
 * Each cached session preserves its DOM, scroll position, and component state
 * for instant switching. Cost is ~150-500 KB per session (more with terminal).
 * Can be adjusted per device in settings.
 */
export const DEFAULT_MAX_CACHED_SESSIONS = 20

/**
 * Tool names that spawn subagent sessions.
 * "Task" is the legacy name, "Agent" is the new one — both behave identically.
 */
export const AGENT_TOOL_NAMES = new Set(['Task', 'Agent'])

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
    // Failed sends use baseLineNum - seq as their lineNum (e.g., -3000, -3001, ...)
    FAILED_USER_MESSAGE: { baseLineNum: -3000, kind: 'failed-user-message' },
    OPTIMISTIC_USER_MESSAGE: { lineNum: -2000, kind: 'optimistic-user-message' },
    STARTING_ASSISTANT_MESSAGE: { lineNum: -1500, kind: 'starting-assistant-message' },
    // Streaming blocks use baseLineNum - blockIndex as their lineNum (e.g., -1000, -1001, ...)
    STREAMING_BLOCK: { baseLineNum: -1000, kind: 'streaming-block' },
    WORKING_ASSISTANT_MESSAGE: { lineNum: -500, kind: 'working-assistant-message' },
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
    [PROCESS_STATE.ASSISTANT_TURN]: 'var(--wa-color-blue-60)',
    [PROCESS_STATE.USER_TURN]: 'var(--wa-color-success-60)',
    [PROCESS_STATE.DEAD]: 'var(--wa-color-danger-60)',
}

/**
 * Backend provider values (matches backend Provider enum).
 * Identifies the backend that produced a session.
 */
export const PROVIDER = {
    CLAUDE_CODE: 'claude_code',
    CODEX: 'codex',
}

/**
 * Web Awesome theme values.
 * Controls the visual theme applied to Web Awesome components.
 */
export const WA_THEME = {
    DEFAULT: 'default',
    SHOELACE: 'shoelace',
    AWESOME: 'awesome',
}

export const WA_THEME_LABELS = {
    [WA_THEME.DEFAULT]: 'Default',
    [WA_THEME.SHOELACE]: 'Shoelace',
    [WA_THEME.AWESOME]: 'Awesome',
}

export const WA_THEME_DEFAULT_PALETTE = {
    [WA_THEME.AWESOME]: 'bright',
    [WA_THEME.DEFAULT]: 'default',
    [WA_THEME.SHOELACE]: 'shoelace',
}

/**
 * Web Awesome brand color values.
 * Controls the accent/brand color used throughout the UI.
 */
export const WA_BRAND = {
    BLUE: 'blue',
    RED: 'red',
    ORANGE: 'orange',
    YELLOW: 'yellow',
    GREEN: 'green',
    CYAN: 'cyan',
    INDIGO: 'indigo',
    PURPLE: 'purple',
    PINK: 'pink',
    GRAY: 'gray',
}

export const WA_BRAND_LABELS = {
    [WA_BRAND.BLUE]: 'Blue',
    [WA_BRAND.RED]: 'Red',
    [WA_BRAND.ORANGE]: 'Orange',
    [WA_BRAND.YELLOW]: 'Yellow',
    [WA_BRAND.GREEN]: 'Green',
    [WA_BRAND.CYAN]: 'Cyan',
    [WA_BRAND.INDIGO]: 'Indigo',
    [WA_BRAND.PURPLE]: 'Purple',
    [WA_BRAND.PINK]: 'Pink',
    [WA_BRAND.GRAY]: 'Gray',
}

/**
 * Generic settings keys synced across devices via backend settings.json.
 * Provider-owned synced keys are declared by each provider's helper
 * (``BaseProviderHelpers.getSyncedSettingsKeys``) — they are dispatched to
 * provider stores by the settings orchestrator and must not be listed here.
 * All other settings remain local to the browser (localStorage only).
 */
export const SYNCED_SETTINGS_KEYS = new Set([
    'defaultProvider', 'defaultLayoutId', 'disabledProviders', 'orchestrationDisabledProviders',
    'titleGenerationEnabled', 'titleAutoApply',
    'titleSuggestionModel', 'titleSystemPrompt', 'autoUnpinOnArchive',
    'worktreeDirectoryTemplate',
    'terminalUseTmux', 'terminalTmuxConfigPath',
    'waTheme', 'waBrand',
    'externalNotificationTargets', 'publicBaseUrl', 'shareBaseUrl', 'peerBaseUrl', 'peerDisplayName',
    'notifyOnExtraUsageStart',
    'allowAgentSessionShares', 'allowAgentArtifactShares',
    // Synced but intentionally never shown in the settings panel: records that
    // the user has seen the hybrid-mode explainer dialog (so it stops gating
    // the toggle). Written only via the dialog's "don't show again" switch.
    'claudeHybridExplainerSeen',
    // Start new Claude Code sessions in hybrid mode by default (drafts only;
    // never enforced on existing sessions). Shown in the Claude settings section.
    'claudeHybridDefault',
    // Anonymous telemetry opt-in/out, and whether the user has seen the
    // telemetry notice (gates a one-time explainer). Shown in the General section.
    'telemetryEnabled', 'telemetryNoticeSeen',
])

export const TITLE_SUGGESTION_MODEL = Object.freeze({
    PROVIDER: 'provider',
    HAIKU: 'haiku',
    LUNA: 'luna',
})

const TITLE_SUGGESTION_MODEL_VALUES = new Set(Object.values(TITLE_SUGGESTION_MODEL))

export function resolveTitleSuggestionModel(value) {
    return TITLE_SUGGESTION_MODEL_VALUES.has(value) ? value : TITLE_SUGGESTION_MODEL.PROVIDER
}
