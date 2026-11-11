// frontend/src/utils/download.js

/**
 * Trigger a browser download for a same-origin URL.
 *
 * A temporary anchor is used rather than fetch + blob so the browser streams
 * the response straight to disk (no full copy in memory) and shows its own
 * progress UI. The file name comes from the endpoint's ``Content-Disposition``
 * header; the empty ``download`` attribute only marks the navigation as a
 * download without overriding that name.
 *
 * Must be called from a user gesture (a menu selection handler qualifies),
 * otherwise Safari blocks it.
 *
 * @param {string} url - Same-origin URL serving the file as an attachment.
 */
export function triggerDownload(url) {
    if (!url) return
    const link = document.createElement('a')
    link.href = url
    link.download = ''
    link.rel = 'noopener'
    document.body.appendChild(link)
    link.click()
    link.remove()
}

/**
 * Encode an absolute file path into the trailing segments of a raw-serving URL.
 * Each segment is encoded on its own so the slashes stay path separators.
 *
 * @param {string} filePath - Absolute path of the file.
 * @returns {string} Encoded path, without a leading slash.
 */
export function encodeFilePathSegments(filePath) {
    return filePath
        .replace(/^\/+/, '')
        .split('/')
        .map(encodeURIComponent)
        .join('/')
}

/**
 * base64url-encode a string (unicode-safe), for the confinement-root segment of
 * the standalone raw-serving URL.
 *
 * @param {string} str - The confinement root path.
 * @returns {string} base64url representation, unpadded.
 */
export function base64UrlEncode(str) {
    const bytes = new TextEncoder().encode(str)
    let binary = ''
    for (const byte of bytes) binary += String.fromCharCode(byte)
    return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/**
 * URL that serves a file's raw bytes as a download.
 *
 * Project scope (``apiPrefix`` carries the project/session prefix) puts the path
 * under that prefix; standalone scope (the Artifacts tab, no project) carries
 * the confinement root as a base64url segment. Mirrors FilePane's rawFileUrl.
 *
 * @param {object} options
 * @param {string} options.filePath - Absolute path of the file.
 * @param {string|null} options.projectId - Project id, or null for standalone scope.
 * @param {string} options.apiPrefix - API prefix for the project scope.
 * @param {string|null} options.root - Confinement root for the standalone scope.
 * @returns {string|null} The download URL, or null without a file path.
 */
export function buildFileDownloadUrl({ filePath, projectId, apiPrefix, root }) {
    if (!filePath) return null
    const trailing = encodeFilePathSegments(filePath)
    if (projectId) {
        return `${apiPrefix}/file-raw/${trailing}?download=1`
    }
    return `/api/file-raw/${base64UrlEncode(root || '')}/${trailing}?download=1`
}

/**
 * URL that streams a directory as a ZIP attachment.
 *
 * @param {object} options
 * @param {string} options.directoryPath - Absolute path of the directory.
 * @param {string|null} options.projectId - Project id, or null for standalone scope.
 * @param {string} options.apiPrefix - API prefix for the project/session scope.
 * @param {string|null} options.root - Confinement root for standalone scope.
 * @returns {string|null} The download URL, or null without a directory path.
 */
export function buildDirectoryDownloadUrl({ directoryPath, projectId, apiPrefix, root }) {
    if (!directoryPath) return null
    const query = new URLSearchParams({ path: directoryPath })
    if (projectId) return `${apiPrefix}/directory-download/?${query}`
    query.set('root', root || '')
    return `/api/directory-download/?${query}`
}
