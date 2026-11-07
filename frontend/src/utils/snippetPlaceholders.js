import { i18n } from '../i18n/index.js'

// Placeholder definitions, extraction, resolution, and availability checks for snippets.

/**
 * Build a local ISO date/time from a Date object.
 * Uses local timezone (not UTC) since this runs in a terminal context.
 */
function localISO(d = new Date()) {
    const pad = (n) => String(n).padStart(2, '0')
    const date = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
    return { date, time, datetime: `${date}T${time}` }
}

/**
 * All available placeholders.
 * Each has:
 *   - id: the token used in {id} syntax in snippet text
 *   - label: short human-readable label for UI buttons
 *   - resolve(ctx): returns the replacement string, or null/undefined if unavailable
 *
 * Context object (ctx): { session, project, projectName }
 */
export const PLACEHOLDERS = [
    // Project-related
    { id: 'project-dir', labelKey: 'composer.snippets.placeholders.projectDir', get label() { return i18n.global.t(this.labelKey) }, resolve: (ctx) => ctx.project?.directory },
    { id: 'project-name', labelKey: 'composer.snippets.placeholders.projectName', get label() { return i18n.global.t(this.labelKey) }, resolve: (ctx) => ctx.projectName },
    // Session-related
    { id: 'session-cwd', labelKey: 'composer.snippets.placeholders.sessionDir', get label() { return i18n.global.t(this.labelKey) }, resolve: (ctx) => ctx.session?.cwd },
    { id: 'session-git-branch', labelKey: 'composer.snippets.placeholders.gitBranch', get label() { return i18n.global.t(this.labelKey) }, resolve: (ctx) => ctx.session?.git_branch },
    { id: 'session-git-dir', labelKey: 'composer.snippets.placeholders.gitDir', get label() { return i18n.global.t(this.labelKey) }, resolve: (ctx) => ctx.session?.git_directory },
    { id: 'session-id', labelKey: 'composer.snippets.placeholders.sessionId', get label() { return i18n.global.t(this.labelKey) }, resolve: (ctx) => ctx.session?.id },
    // Dynamic (always available — computed at insertion time)
    { id: 'date', labelKey: 'composer.snippets.placeholders.date', get label() { return i18n.global.t(this.labelKey) }, resolve: () => localISO().date },
    { id: 'time', labelKey: 'composer.snippets.placeholders.time', get label() { return i18n.global.t(this.labelKey) }, resolve: () => localISO().time },
    { id: 'datetime', labelKey: 'composer.snippets.placeholders.datetime', get label() { return i18n.global.t(this.labelKey) }, resolve: () => localISO().datetime },
]

/** Regex matching {placeholder-id} tokens. */
const PLACEHOLDER_REGEX = /\{([a-z][a-z0-9-]*)\}/g

/** Lookup map: id → placeholder definition. */
const PLACEHOLDER_MAP = new Map(PLACEHOLDERS.map(p => [p.id, p]))

/**
 * Extract known placeholder IDs from a text string.
 * Returns a deduplicated array of placeholder IDs found in the text.
 */
export function extractPlaceholders(text) {
    const found = new Set()
    for (const match of text.matchAll(PLACEHOLDER_REGEX)) {
        if (PLACEHOLDER_MAP.has(match[1])) {
            found.add(match[1])
        }
    }
    return [...found]
}

/**
 * Get the list of unavailable placeholders given a snippet's placeholder IDs
 * and the current resolution context.
 * Returns array of { id, label } for placeholders that cannot be resolved.
 */
export function getUnavailablePlaceholders(placeholderIds, ctx) {
    if (!placeholderIds || placeholderIds.length === 0) return []
    const unavailable = []
    for (const id of placeholderIds) {
        const p = PLACEHOLDER_MAP.get(id)
        if (!p) continue
        const value = p.resolve(ctx)
        if (value === null || value === undefined || value === '') {
            unavailable.push({ id: p.id, labelKey: p.labelKey, label: p.label })
        }
    }
    return unavailable
}

/**
 * Resolve all placeholders in a snippet's text using the given context.
 * Only replaces placeholders listed in placeholderIds (pre-computed on save).
 */
export function resolveSnippetText(text, placeholderIds, ctx) {
    if (!placeholderIds || placeholderIds.length === 0) return text
    let result = text
    for (const id of placeholderIds) {
        const p = PLACEHOLDER_MAP.get(id)
        if (p) {
            result = result.replaceAll(`{${id}}`, p.resolve(ctx) ?? '')
        }
    }
    return result
}
