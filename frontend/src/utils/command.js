/**
 * Extract command information from text that starts with <command-name> XML tags
 * @param {string} text - The text to parse
 * @returns {Object|null} - Command object with name, message, args or null if not a command
 */
export function extractCommand(text) {
    if (typeof text !== 'string') return null

    if (!text.startsWith('<command-')) return null

    const xmlText = `<root>${text}</root>`

    let parsed
    try {
        const parser = new DOMParser()
        const doc = parser.parseFromString(xmlText, 'text/xml')
        if (doc.querySelector('parsererror')) return null
        parsed = doc.documentElement
    } catch {
        return null
    }

    const name = parsed.querySelector('command-name')?.textContent
    if (!name) return null

    return {
        name,
        message: parsed.querySelector('command-message')?.textContent ?? null,
        args: parsed.querySelector('command-args')?.textContent ?? null
    }
}

/**
 * Convert command text to a display-friendly string
 * @param {string} text - The text to convert
 * @returns {string|null} - Display string or null if not a command
 */
export function commandToText(text) {
    const command = extractCommand(text)
    if (!command) return null

    let result = command.name
    if (command.args) {
        result += ` ${command.args}`
    }
    return result
}
