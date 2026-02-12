/**
 * Client-side tree search/filter utility.
 *
 * Provides the same fuzzy/exact search logic as the backend `file_tree.py`
 * `search_files()`, but operates on an in-memory tree structure (e.g. the
 * git changed-files tree returned by the backend).
 *
 * Search modes:
 * - **Fuzzy subsequence** (default): each character of the query must appear
 *   in order in the file path (case-insensitive).
 *   Example: "vw" matches "views.py", "vue_wrapper.js"
 * - **Exact substring**: query starts with `"` or `'` (leading quote stripped,
 *   optional trailing matching quote stripped).
 *   Example: `"foo` matches files containing "foo" in their path.
 */

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Recursively flatten a tree into a list of file entries.
 *
 * @param {Object} node - Tree node: { name, type, children?, status? }
 * @param {string} parentPath - Accumulated parent path (empty for root)
 * @returns {Array<{ path: string, name: string, status?: string }>}
 */
function flattenTree(node, parentPath = '') {
    const files = []

    if (!node.children) return files

    for (const child of node.children) {
        const childPath = parentPath ? `${parentPath}/${child.name}` : child.name

        if (child.type === 'file') {
            const entry = { path: childPath, name: child.name }
            if (child.status) entry.status = child.status
            if (child.staged_status) entry.staged_status = child.staged_status
            if (child.unstaged_status) entry.unstaged_status = child.unstaged_status
            files.push(entry)
        } else if (child.type === 'directory') {
            files.push(...flattenTree(child, childPath))
        }
    }

    return files
}

/**
 * Check if `query` is a subsequence of `text` (case-insensitive).
 * Each character of query must appear in order in text.
 */
function isSubsequence(query, text) {
    let qi = 0
    for (const ch of text) {
        if (qi < query.length && ch === query[qi]) {
            qi++
        }
    }
    return qi === query.length
}

/**
 * Parse the search query to determine mode and cleaned query.
 *
 * @param {string} rawQuery - The raw query string
 * @returns {{ query: string, exact: boolean }}
 */
function parseQuery(rawQuery) {
    const trimmed = rawQuery.trim()
    if (!trimmed) return { query: '', exact: false }

    // Detect exact mode: starts with " or '
    if (trimmed.startsWith('"') || trimmed.startsWith("'")) {
        const quote = trimmed[0]
        let inner = trimmed.slice(1)
        // Strip optional trailing matching quote
        if (inner.endsWith(quote)) {
            inner = inner.slice(0, -1)
        }
        return { query: inner, exact: true }
    }

    return { query: trimmed, exact: false }
}

/**
 * Build a tree from a list of matching file entries.
 *
 * Creates a tree with only the matching files and their ancestor directories.
 * Preserves the `status` field on file nodes.
 *
 * @param {string} rootName - Name for the root node
 * @param {Array<{ path: string, status?: string }>} files - Matching files
 * @returns {Object} Tree root node
 */
function buildFilteredTree(rootName, files) {
    const tree = { name: rootName, type: 'directory', loaded: true, children: [] }
    const dirNodes = {}

    for (const file of files) {
        const parts = file.path.split('/')

        // Create all parent directories
        for (let i = 0; i < parts.length - 1; i++) {
            const dirKey = parts.slice(0, i + 1).join('/')
            if (!(dirKey in dirNodes)) {
                const parentKey = i > 0 ? parts.slice(0, i).join('/') : null
                const parentNode = parentKey ? dirNodes[parentKey] : tree
                const newDir = { name: parts[i], type: 'directory', loaded: true, children: [] }
                parentNode.children.push(newDir)
                dirNodes[dirKey] = newDir
            }
        }

        // Add the file itself
        const parentKey = parts.length > 1 ? parts.slice(0, -1).join('/') : null
        const parentNode = parentKey ? dirNodes[parentKey] : tree
        const fileNode = { name: parts[parts.length - 1], type: 'file' }
        if (file.status) fileNode.status = file.status
        if (file.staged_status) fileNode.staged_status = file.staged_status
        if (file.unstaged_status) fileNode.unstaged_status = file.unstaged_status
        parentNode.children.push(fileNode)
    }

    // Sort children: directories first (alphabetically case-insensitive), then files
    function sortChildren(node) {
        if (node.children) {
            node.children.sort((a, b) => {
                const typeOrder = (a.type === 'directory' ? 0 : 1) - (b.type === 'directory' ? 0 : 1)
                if (typeOrder !== 0) return typeOrder
                return a.name.toLowerCase().localeCompare(b.name.toLowerCase())
            })
            for (const child of node.children) {
                sortChildren(child)
            }
        }
    }

    sortChildren(tree)
    return tree
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Search/filter an in-memory tree structure.
 *
 * Replicates the backend `search_files()` scoring logic:
 * 1. Filename match (highest priority)
 * 2. Contiguous substring match
 * 3. Shorter paths preferred
 *
 * @param {Object} tree - Root tree node: { name, type, children, loaded }
 * @param {string} rawQuery - Search query (may include leading quote for exact mode)
 * @param {number} [maxResults=50] - Maximum number of results
 * @returns {{ tree: Object, total: number, truncated: boolean }}
 */
export function searchTreeFiles(tree, rawQuery, maxResults = 50) {
    const { query, exact } = parseQuery(rawQuery)

    if (!query) {
        return { tree: null, total: 0, truncated: false }
    }

    const lowerQuery = query.toLowerCase()
    const files = flattenTree(tree)
    const scored = []

    for (const file of files) {
        const lowerPath = file.path.toLowerCase()
        const lowerName = file.name.toLowerCase()

        // Check if the file matches the query
        if (exact) {
            if (!lowerPath.includes(lowerQuery)) continue
        } else {
            if (!isSubsequence(lowerQuery, lowerPath)) continue
        }

        // Score: same logic as backend file_tree.py
        let filenameMatch
        if (exact) {
            filenameMatch = lowerName.includes(lowerQuery)
        } else {
            filenameMatch = isSubsequence(lowerQuery, lowerName)
        }

        const contiguousBonus = lowerPath.includes(lowerQuery) ? 1 : 0

        // Score tuple: (filename_match, contiguous_bonus, -path_length)
        // We encode as a single comparable number for sorting
        const score = (filenameMatch ? 1000000 : 0) + (contiguousBonus * 1000) - file.path.length

        scored.push({ score, file })
    }

    // Sort by score descending
    scored.sort((a, b) => b.score - a.score)

    const total = scored.length
    const truncated = total > maxResults
    const topFiles = scored.slice(0, maxResults).map(s => s.file)

    // Build filtered tree
    const resultTree = buildFilteredTree(tree.name, topFiles)

    return { tree: resultTree, total, truncated }
}
