import { i18n } from '../i18n/index.js'

/**
 * Display metadata for a command-invocation prefix (the character a user types
 * to invoke a provider command, e.g. ``/`` for Claude Code; Codex also has
 * ``$``). Centralised here so every surface that needs to render a button or
 * an icon for a given activation char (the snippets bar, future picker
 * surfaces, palette entries, …) reads from the same source of truth.
 *
 * Per-entry shape:
 *   - ``icon``      : wa-icon name (Font Awesome / Web Awesome catalogue).
 *   - ``iconStyle`` : inline style object applied to the icon, or ``null``.
 *                     Used to mirror the ``slash`` glyph horizontally so it
 *                     reads as ``/`` (Web Awesome's free tier ships ``slash``
 *                     but not ``slash-forward``).
 *   - ``tooltip``   : tooltip text for the button. Kept per-char on purpose
 *                     so each prefix can carry its own wording.
 */

const ACTIVATION_CHAR_METADATA = {
    '/': {
        icon: 'slash',
        iconStyle: { transform: 'scaleX(-1)' },
        get tooltip() { return i18n.global.t('composer.commands.insertSlash') },
    },
    '$': {
        icon: 'dollar-sign',
        iconStyle: null,
        get tooltip() { return i18n.global.t('composer.commands.insertDollar') },
    },
}

/**
 * Look up the display descriptor for ``char``. Returns ``null`` when the
 * char has no registered entry — callers should treat that as "do not render
 * a button" rather than fall back to a placeholder.
 */
export function getActivationCharMetadata(char) {
    return ACTIVATION_CHAR_METADATA[char] ?? null
}
