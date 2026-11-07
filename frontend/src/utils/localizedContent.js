import { DEFAULT_LOCALE, isSupportedLocale } from '../i18n/index.js'
import { stripFrontMatter } from './frontMatter.js'
import { resolvePublicAssetUrl } from './publicAsset.js'

/** Load authored Markdown, with English fallback and a separate cache per language. */
export function createLocalizedContentLoader(folder, { cache = !import.meta.env?.DEV } = {}) {
    const bodies = new Map()

    async function read(key, locale) {
        const path = `${folder}/${locale === DEFAULT_LOCALE ? '' : `${locale}/`}${key}.md`
        if (cache && bodies.has(path)) return bodies.get(path)
        const response = await fetch(resolvePublicAssetUrl(path))
        if (!response.ok) throw new Error(`Content request failed: HTTP ${response.status}`)
        const raw = await response.text()
        // Vite can return the SPA document with status 200 for a missing public file.
        if (/text\/html/i.test(response.headers.get('content-type') || '') || /^\s*<(?:!doctype\s+html|html\b)/i.test(raw)) {
            throw new Error('Expected Markdown content, received an HTML document')
        }
        const body = stripFrontMatter(raw)
        if (!body.trim()) throw new Error('Empty Markdown content')
        const content = { body, locale }
        if (cache) bodies.set(path, content)
        return content
    }

    return async (key, requestedLocale) => {
        const locale = isSupportedLocale(requestedLocale) ? requestedLocale : DEFAULT_LOCALE
        if (locale !== DEFAULT_LOCALE) {
            try { return await read(key, locale) }
            catch { /* Missing or unavailable translations fall back to the English source. */ }
        }
        return read(key, DEFAULT_LOCALE)
    }
}
