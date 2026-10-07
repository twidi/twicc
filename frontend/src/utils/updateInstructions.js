// Bootstrap metadata lives outside stores to avoid circular imports.
const fallback = {
    before: 'Update TwiCC with the package manager used to install it, then restart.',
    command: null,
    after: '',
}
let instructions = fallback

export function setUpdateInstructions(value) {
    instructions = value && typeof value.before === 'string' ? value : fallback
}

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, char => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[char])
}

export function getUpdateInstructionsHtml() {
    const { before, command, after } = instructions
    const parts = [escapeHtml(before)]
    if (command) {
        parts.push(`<code style="background: var(--wa-color-neutral-fill-normal); color: var(--wa-color-neutral-on-normal); padding: 0.1em 0.4em; border-radius: 3px; font-size: 0.9em; overflow-wrap: anywhere;">${escapeHtml(command)}</code>`)
    }
    if (after) parts.push(escapeHtml(after))
    return parts.join(' ')
}
