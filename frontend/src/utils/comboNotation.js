/**
 * Auto-generate display notation for combo steps.
 *
 * Notation rules:
 * - Modifiers uppercase, then key, separated by spaces: "CTRL C", "CTRL ALT D"
 * - Multi-step: joined with " · " (middle dot): "CTRL B · C"
 * - No "+" separator (avoids ambiguity with the "+" key)
 */

/** Map key identifiers to short display labels (matching built-in tab labels) */
const KEY_DISPLAY_MAP = {
    Escape: 'ESC',
    Tab: 'TAB',
    Enter: 'ENTER',
    ArrowLeft: '←',
    ArrowUp: '↑',
    ArrowDown: '↓',
    ArrowRight: '→',
    Home: 'HOME',
    End: 'END',
    PageUp: 'PGUP',
    PageDown: 'PGDN',
    Delete: 'DEL',
    Insert: 'INS',
}

/**
 * Get display text for a single key identifier.
 * @param {string} key - Key identifier (e.g. "c", "Escape", "F5")
 * @returns {string} Display text (e.g. "C", "ESC", "F5")
 */
function displayKey(key) {
    if (KEY_DISPLAY_MAP[key]) return KEY_DISPLAY_MAP[key]
    // F-keys: already uppercase-ish (F1, F12)
    if (/^F\d{1,2}$/.test(key)) return key
    // Single character: uppercase
    if (key.length === 1) return key.toUpperCase()
    // Fallback: uppercase
    return key.toUpperCase()
}

/**
 * Render a single step as display text.
 * @param {{ modifiers?: string[], key: string }} step
 * @returns {string} e.g. "CTRL C", "ALT .", "ESC"
 */
export function formatStep(step) {
    const parts = []
    if (step.modifiers) {
        for (const mod of step.modifiers) {
            parts.push(mod.toUpperCase())
        }
    }
    parts.push(displayKey(step.key))
    return parts.join(' ')
}

/**
 * Render a full combo (all steps) as display text.
 * If the combo has a label, returns the label instead.
 * @param {{ label?: string, steps: Array<{ modifiers?: string[], key: string }> }} combo
 * @returns {string} e.g. "CTRL C", "CTRL B · C", "tmux:new"
 */
export function formatCombo(combo) {
    if (combo.label) return combo.label
    return combo.steps.map(formatStep).join(' · ')
}

/**
 * Render the notation-only version (ignoring label). Used in Manage dialog
 * to show notation alongside label.
 * @param {{ steps: Array<{ modifiers?: string[], key: string }> }} combo
 * @returns {string}
 */
export function formatComboNotation(combo) {
    return combo.steps.map(formatStep).join(' · ')
}
