// Changelog parser and fetcher
// Fetches CHANGELOG.md from GitHub, parses it into structured data for the ChangelogDialog.

const GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/twidi/twicc/refs/heads/main/'
const CHANGELOG_URL = GITHUB_RAW_BASE + 'CHANGELOG.md'

/**
 * Fetch and parse the changelog.
 * In dev mode, fetches from the local backend endpoint; otherwise from GitHub.
 * @param {boolean} devMode - Whether the backend is running in dev mode
 * @returns {Promise<Array<{version: string, date: string|null, entries: Array}>>}
 */
export async function fetchChangelog(devMode = false) {
    const url = devMode ? '/api/changelog/' : CHANGELOG_URL
    const resp = await fetch(url)
    if (!resp.ok) throw new Error(`Failed to fetch changelog: ${resp.status}`)
    const versions = parseChangelog(await resp.text())
    // In non-dev mode, only keep real releases (version starts with a digit)
    return devMode ? versions : versions.filter(v => /^\d/.test(v.version))
}

/**
 * Parse a Keep-a-Changelog formatted markdown string into structured data.
 *
 * @param {string} markdown - Raw CHANGELOG.md content
 * @returns {Array<{version: string, date: string|null, entries: Array<{category: string, text: string, images: Array<{alt: string, path: string}>}>}>}
 */
export function parseChangelog(markdown) {
    const versions = []

    // Find all version headers: ## [Unreleased] or ## [1.2.3] - 2026-03-20
    const versionRegex = /^## \[(.+?)\](?:\s*-\s*(.+))?$/gm
    const versionHeaders = []
    let match

    while ((match = versionRegex.exec(markdown)) !== null) {
        versionHeaders.push({
            version: match[1],
            date: match[2]?.trim() || null,
            contentStart: match.index + match[0].length,
        })
    }

    for (let i = 0; i < versionHeaders.length; i++) {
        const header = versionHeaders[i]
        const contentEnd = i + 1 < versionHeaders.length
            ? markdown.lastIndexOf('\n', versionHeaders[i + 1].contentStart - versionHeaders[i + 1].version.length - 10)
            : markdown.length
        const content = markdown.slice(header.contentStart, contentEnd)

        const entries = parseVersionContent(content)
        if (entries.length > 0) {
            versions.push({
                version: header.version,
                date: header.date,
                entries,
            })
        }
    }

    return versions
}

/**
 * Parse the content within a single version section.
 */
function parseVersionContent(content) {
    const entries = []

    // Find category headers: ### Added, ### Changed, ### Fixed
    const categoryRegex = /^### (\w+)$/gm
    const categories = []
    let match

    while ((match = categoryRegex.exec(content)) !== null) {
        categories.push({
            name: match[1].toLowerCase(),
            contentStart: match.index + match[0].length,
        })
    }

    for (let i = 0; i < categories.length; i++) {
        const cat = categories[i]
        const contentEnd = i + 1 < categories.length ? categories[i + 1].contentStart - categories[i + 1].name.length - 5 : content.length
        const section = content.slice(cat.contentStart, contentEnd)

        // Parse top-level entries (lines starting with "- ")
        const lines = section.split('\n')
        let currentEntry = null

        for (const line of lines) {
            if (line.startsWith('- ')) {
                if (currentEntry) entries.push(currentEntry)
                currentEntry = {
                    category: cat.name,
                    text: line.slice(2),
                    images: [],
                }
            } else if (currentEntry && /^\s+- !\[/.test(line)) {
                // Image sub-item:   - ![Alt text](path/to/image.webp)
                const imgMatch = line.match(/^\s+- !\[([^\]]*)\]\(([^)]+)\)/)
                if (imgMatch) {
                    currentEntry.images.push({
                        alt: imgMatch[1],
                        path: imgMatch[2],
                    })
                }
            }
        }
        if (currentEntry) entries.push(currentEntry)
    }

    return entries
}

// Thin alias kept for the existing ChangelogDialog import. The canonical
// helper now lives in ./publicAsset.js and is reused by other features
// (e.g. tips) that need to load assets from frontend/public/. New callers
// should import `resolvePublicAssetUrl` directly.
export { resolvePublicAssetUrl as resolveImageLocalUrl } from './publicAsset.js'

/**
 * Resolve an image path to the GitHub raw URL (fallback).
 *
 * @param {string} path - Raw path from CHANGELOG
 * @returns {string} GitHub raw content URL
 */
export function resolveImageGitHubUrl(path) {
    return GITHUB_RAW_BASE + path
}

// Sentinel key for the combined "previous → current" entry in the version selector
export const COMBINED_VERSION_KEY = '__combined__'

// Category display order for the combined multi-version screen. Each release
// opens with a ### Summary (a single bold-led, one-line recap of the version) —
// a deliberate deviation from Keep a Changelog — followed by the standard
// ### Added / ### Changed / ### Fixed. Entries are grouped by category in this
// fixed order, then by version (oldest first) within each category — so a
// multi-version upgrade reads as "every release's summary first, then all the
// new features, then all the changes, then all the fixes". A fixed list is
// required because a category may appear in only some of the spanned versions,
// leaving document order ambiguous. Any unexpected category is appended
// afterwards in first-appearance order, so no entry is ever dropped.
const COMBINED_CATEGORY_ORDER = ['summary', 'added', 'changed', 'fixed']

/**
 * Build a combined version entry spanning all changelogs from after previousVersion
 * up to and including currentVersion. Uses file order (no semver comparison).
 * Returns null if no combined entry should be shown.
 */
export function buildCombinedVersion(allVersions, previousVersion, currentVersion) {
    if (!previousVersion || !currentVersion || previousVersion === currentVersion) return null

    const currentIdx = allVersions.findIndex(v => v.version === currentVersion)
    if (currentIdx === -1) return null

    const previousIdx = allVersions.findIndex(v => v.version === previousVersion)
    // If previous not found in changelog, take everything from current to end
    const endIdx = previousIdx === -1 ? allVersions.length : previousIdx

    if (currentIdx >= endIdx) return null

    const versionsInRange = allVersions.slice(currentIdx, endIdx)

    // Reverse to display oldest first (changelog file is newest-first)
    const reversed = [...versionsInRange].reverse()

    // Effective category order: the fixed list above for known categories, then
    // any unexpected ones in first-appearance order so nothing is dropped.
    const presentCategories = []
    for (const v of reversed) {
        for (const entry of v.entries) {
            if (!presentCategories.includes(entry.category)) presentCategories.push(entry.category)
        }
    }
    const orderedCategories = [
        ...COMBINED_CATEGORY_ORDER.filter(c => presentCategories.includes(c)),
        ...presentCategories.filter(c => !COMBINED_CATEGORY_ORDER.includes(c)),
    ]

    // Group by category first, then by version (oldest first) within each
    // category. Entry order inside a same category/version pair is preserved.
    const entries = []
    for (const category of orderedCategories) {
        for (const v of reversed) {
            for (const entry of v.entries) {
                if (entry.category === category) {
                    entries.push({ ...entry, _sourceVersion: v.version })
                }
            }
        }
    }

    if (!entries.length) return null

    return {
        version: COMBINED_VERSION_KEY,
        date: null,
        entries,
        _previousVersion: previousVersion,
        _currentVersion: currentVersion,
    }
}
