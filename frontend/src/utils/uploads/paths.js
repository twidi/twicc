// frontend/src/utils/uploads/paths.js
// Path helpers of the upload feature (spec §6.6, §6.10). Pure: no DOM, no store.

/**
 * Collapse the leading slashes of a path to one.
 *
 * Under the `/` root, `FileTree` builds paths like `//home/u`, and POSIX
 * `normpath` keeps a leading `//`: the upload target must be `/home/u`.
 *
 * @param {string} path
 * @returns {string}
 */
export function collapseLeadingSlashes(path) {
    return path.replace(/^\/+/, '/')
}

/**
 * True when `path` is `root` or inside it. For root `/`, every absolute path.
 *
 * @param {string} root
 * @param {string} path
 * @returns {boolean}
 */
export function pathContains(root, path) {
    if (!root || !path) return false
    if (root === '/') return path.startsWith('/')
    return path === root || path.startsWith(root + '/')
}

/**
 * Find the node of `absPath` in a file tree whose root node is at `rootPath`.
 *
 * Same walk as `FileTreePanel.findNodeInTree`, with the `/` rule of
 * `pathContains`, ignoring empty path segments (`//home/u`).
 *
 * @param {object|null} tree - root node `{ name, type, loaded?, children? }`
 * @param {string} rootPath - absolute path of the root node
 * @param {string} absPath
 * @returns {object|null} the node, or null when absent
 */
export function findTreeNode(tree, rootPath, absPath) {
    if (!tree || !rootPath || !absPath) return null
    if (!pathContains(rootPath, absPath)) return null
    const rest = rootPath === '/' ? absPath : absPath.slice(rootPath.length)
    const segments = rest.split('/').filter(Boolean)
    let current = tree
    for (const segment of segments) {
        if (!Array.isArray(current.children)) return null
        const child = current.children.find(c => c.name === segment)
        if (!child) return null
        current = child
    }
    return current
}

/**
 * The target directory as shown in the in-tab indicator: relative to the
 * panel root when inside it (`.` for the root itself), else absolute.
 *
 * @param {string} targetDir
 * @param {string|null} rootPath
 * @returns {string}
 */
export function displayTargetDir(targetDir, rootPath) {
    if (!rootPath || !pathContains(rootPath, targetDir)) return targetDir
    if (targetDir === rootPath) return '.'
    const rest = rootPath === '/' ? targetDir : targetDir.slice(rootPath.length)
    return rest.split('/').filter(Boolean).join('/') || '.'
}

/**
 * Last segment of a path.
 *
 * @param {string} path
 * @returns {string}
 */
export function baseName(path) {
    if (!path) return ''
    const parts = path.split('/').filter(Boolean)
    return parts.length ? parts[parts.length - 1] : path
}
