// frontend/src/providers/gemini/constants.js
//
// Provider-specific value enums for the Gemini agent settings. These mirror
// the wire values stored on ``Session`` rows and the synced settings, and
// are used by the Gemini-only choice catalogue. Mirrors the backend
// ``providers/gemini/constants.py`` — keep in sync.

/**
 * Permission mode values for Gemini sessions: the Gemini CLI approval modes.
 */
export const PERMISSION_MODE = {
    DEFAULT: 'default',
    AUTO_EDIT: 'auto_edit',
    PLAN: 'plan',
    YOLO: 'yolo',
}

/**
 * Permission modes allowed for sessions in an UNTRUSTED (or unknown-trust)
 * project. Mirrors the backend ``UNTRUSTED_PERMISSION_MODES``.
 */
export const UNTRUSTED_PERMISSION_MODES = [
    PERMISSION_MODE.DEFAULT,
    PERMISSION_MODE.AUTO_EDIT,
    PERMISSION_MODE.PLAN,
]

/**
 * Effort level values for Gemini sessions.
 *
 * The Gemini CLI exposes no per-session effort control: the single level only
 * keeps the model × effort matrix clickable (a provider without effort choices
 * has no enabled cell, so no model could be picked).
 */
export const EFFORT = {
    MEDIUM: 'medium',
}

/**
 * Name of the Gemini CLI tool that runs a subagent (args ``{ agent_name,
 * prompt }``). Its call is linked to the subagent session by an AgentLink.
 */
export const AGENT_TOOL_NAME = 'invoke_agent'

/**
 * Tool call statuses after which a Gemini tool call is finished. Any other
 * status means the call is still running.
 */
export const TERMINAL_TOOL_STATUSES = ['success', 'error', 'cancelled']
