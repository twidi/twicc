import { i18n } from '../i18n/index.js'

/**
 * Shared error parsing utilities.
 *
 * Extracts human-readable info from the various error formats used by the
 * Claude SDK / API so that both inline session items (ApiError.vue) and toast
 * notifications (SessionToastContent.vue) can display clean messages.
 */

/**
 * Strip the generic Anthropic docs URL that the API appends to some error messages.
 * e.g. "Overloaded. https://docs.claude.com/en/api/errors" → "Overloaded."
 *
 * @param {string} message
 * @returns {string}
 */
export function stripAnthropicDocsUrl(message) {
    return message.replace(/\s*https:\/\/docs\.claude\.com\/\S*/g, '').trim()
}

/**
 * Parse an "API Error: <status> <json>" string into structured error info.
 *
 * This is the format produced by the SDK when it stringifies an API error.
 * Example input:
 *   "API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded."}}"
 *
 * @param {string} text - The raw error string
 * @returns {{ type: string, message: string, status: number|null }} Parsed error info,
 *          or null if the string does not match the expected format.
 */
export function parseApiErrorString(text) {
    if (!text) return null

    const match = text.match(/^API Error:\s*(\d+)?\s*(.*)$/s)
    if (!match) return null

    const [, statusCode, jsonPart] = match
    try {
        const parsed = JSON.parse(jsonPart)
        // Structure: {"type":"error","error":{"type":"api_error","message":"..."}}
        return {
            type: parsed?.error?.type || 'unknown_error',
            message: stripAnthropicDocsUrl(parsed?.error?.message || text),
            status: statusCode ? parseInt(statusCode) : null,
        }
    } catch {
        // Invalid JSON — return null so callers can fall back
        return null
    }
}

/**
 * Parse a process error message into a human-readable summary.
 *
 * Process error messages from the backend have the shape:
 *   "Claude reported error: API Error: 529 {...json...}"
 *   "SDK error: ..."
 *   "Unexpected error in message loop: ..."
 *
 * @param {string} errorMessage - The raw error string from the process state
 * @returns {{ summary: string, status: number|null }} A clean summary and optional HTTP status.
 */
export function parseProcessError(errorMessage) {
    if (!errorMessage) {
        return { summary: i18n.global.t('common.errors.unknown'), status: null }
    }

    // Strip the "Claude reported error: " prefix if present
    const prefixMatch = errorMessage.match(/^Claude reported error:\s*(.*)$/s)
    const inner = prefixMatch ? prefixMatch[1] : null

    // Try to parse the inner part as an "API Error: ..." string
    if (inner) {
        const apiError = parseApiErrorString(inner)
        if (apiError) {
            return {
                summary: apiError.message,
                status: apiError.status,
            }
        }
        // Not a structured API error — use the inner text as-is
        return { summary: inner, status: null }
    }

    // Other error formats (SDK error, unexpected error) — return as-is
    return { summary: errorMessage, status: null }
}
