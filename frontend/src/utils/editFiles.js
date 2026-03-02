// frontend/src/utils/editFiles.js

/**
 * Extract file paths from Edit tool_use blocks in a session item's content.
 *
 * A session item's content is a JSON string representing a JSONL line.
 * For assistant messages, the parsed JSON looks like:
 *   { type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Edit', input: { file_path: '...' } }, ...] } }
 *
 * @param {string|null} contentJson - Raw JSON string of the session item content
 * @returns {string[]} Array of absolute file paths from Edit tool_use blocks (may contain duplicates)
 */
export function extractEditFilePaths(contentJson) {
    if (!contentJson) return []

    let parsed
    try {
        parsed = JSON.parse(contentJson)
    } catch {
        return []
    }

    // Only assistant messages contain tool_use blocks
    const content = parsed?.message?.content
    if (!Array.isArray(content)) return []

    const paths = []
    for (const block of content) {
        if (block.type === 'tool_use' && block.name === 'Edit' && block.input?.file_path) {
            paths.push(block.input.file_path)
        }
    }
    return paths
}

/**
 * Make an absolute file path relative to a base directory.
 *
 * @param {string} filePath - Absolute file path
 * @param {string} baseDir - Base directory to compute relative path from
 * @returns {string} Relative path, or the original path if it doesn't start with baseDir
 */
export function makeRelativePath(filePath, baseDir) {
    if (!baseDir || !filePath) return filePath

    // Ensure baseDir ends with /
    const normalizedBase = baseDir.endsWith('/') ? baseDir : baseDir + '/'

    if (filePath.startsWith(normalizedBase)) {
        return filePath.slice(normalizedBase.length)
    }
    return filePath
}

/**
 * Collect unique Edit file paths from a set of session items, made relative to a base directory.
 *
 * @param {Array} items - Array of session items (each with a .content JSON string)
 * @param {string|null} baseDir - Base directory for relative paths (git_directory or cwd)
 * @returns {string[]} Deduplicated, sorted array of relative file paths
 */
export function collectGroupEditFiles(items, baseDir) {
    const pathSet = new Set()
    for (const item of items) {
        for (const path of extractEditFilePaths(item.content)) {
            pathSet.add(makeRelativePath(path, baseDir))
        }
    }
    return [...pathSet].sort()
}
