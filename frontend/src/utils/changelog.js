// Changelog parser and fetcher
// Fetches CHANGELOG.md from GitHub, parses it into structured data for the ChangelogDialog.

const GITHUB_RAW_BASE = 'https://raw.githubusercontent.com/twidi/twicc/refs/heads/main/'
const CHANGELOG_URL = GITHUB_RAW_BASE + 'CHANGELOG.md'

/**
 * Fetch and parse the changelog from GitHub.
 * @returns {Promise<Array<{version: string, date: string|null, entries: Array}>>}
 */
export async function fetchChangelog() {
    const resp = await fetch(CHANGELOG_URL)
    if (!resp.ok) throw new Error(`Failed to fetch changelog: ${resp.status}`)
    return parseChangelog(await resp.text())
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

/**
 * Resolve an image path from the changelog to a usable URL.
 * Strips the `frontend/public/` prefix and prepends the app base URL.
 *
 * @param {string} path - Raw path from CHANGELOG (e.g. "frontend/public/whats-new/v1.3/image.webp")
 * @returns {string} URL usable in an <img> src
 */
export function resolveImageLocalUrl(path) {
    // Strip "frontend/public/" prefix to get the public-relative path
    const publicPath = path.replace(/^frontend\/public\//, '')
    return (import.meta.env.BASE_URL || '/') + publicPath
}

/**
 * Resolve an image path to the GitHub raw URL (fallback).
 *
 * @param {string} path - Raw path from CHANGELOG
 * @returns {string} GitHub raw content URL
 */
export function resolveImageGitHubUrl(path) {
    return GITHUB_RAW_BASE + path
}
