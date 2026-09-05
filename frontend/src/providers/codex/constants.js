// frontend/src/providers/codex/constants.js
//
// Provider-specific value enums for the Codex agent settings. These mirror
// the wire values stored on ``Session`` rows and the synced settings, and
// are used by the Codex-only choice catalogue.

/**
 * Context window size values exposed by the Codex provider.
 *
 * Unlike Claude's 200K/1M these are not a user choice: the window is a fixed
 * per-model property (the registry entry's ``provider_extra.context_window``),
 * pinned by ``enforceAgentSettingsConsistency``. ``DEFAULT`` (272K, used by
 * Astra and pre-5.6 models) doubles as the last-resort fallback for legacy sessions;
 * ``LARGE`` (372K) is the GPT-5.6 tiers' window. Mirrors the backend
 * ``AGENT_SETTINGS_CHOICES`` in ``providers/codex/helpers.py`` — keep in sync.
 */
export const CONTEXT_MAX = {
    DEFAULT: 272_000,
    LARGE: 372_000,
}

/**
 * Permission mode values for Codex sessions. Mirrors the Codex CLI's
 * permission presets (read-only / auto / autonomous / auto-review / yolo).
 */
export const PERMISSION_MODE = {
    READ_ONLY: 'read_only',
    STRICT: 'strict',
    AUTO: 'auto',
    AUTONOMOUS: 'autonomous',
    AUTO_REVIEW: 'auto_review',
    YOLO: 'yolo',
}

/**
 * Permission modes allowed for sessions in an UNTRUSTED (or unknown-trust)
 * project. Mirrors the backend ``UNTRUSTED_PERMISSION_MODES`` in
 * ``providers/codex/constants.py`` — keep in sync.
 */
export const UNTRUSTED_PERMISSION_MODES = [
    PERMISSION_MODE.READ_ONLY,
    PERMISSION_MODE.STRICT,
    PERMISSION_MODE.AUTO,
    PERMISSION_MODE.AUTONOMOUS,
    PERMISSION_MODE.AUTO_REVIEW,
]

/**
 * Effort level values for Codex sessions.
 *
 * ``MAX`` and ``ULTRA`` are model-gated: the registry entry's
 * ``provider_extra.supports_effort_{max,ultra}`` says which models take them
 * (Astra, Sol, and Terra take both; Luna takes ``max`` only). Mirrors the
 * backend ``AGENT_SETTINGS_CHOICES`` in ``providers/codex/helpers.py``.
 */
export const EFFORT = {
    LOW: 'low',
    MEDIUM: 'medium',
    HIGH: 'high',
    X_HIGH: 'xhigh',
    MAX: 'max',
    ULTRA: 'ultra',
}
