// frontend/src/utils/uploads/tree.js
// Panel refresh on completion (spec §6.10): the tree-node merge and the
// per-directory refresher. Pure: the panel only wires them.

import { findTreeNode, pathContains } from './paths.js'

/**
 * Merge a directory listing into a loaded tree node, in place.
 *
 * The new children follow the listing order. A child whose name and type
 * match an existing child reuses the existing node object (its loaded subtree
 * and open state stay); other children come from the listing.
 *
 * @param {object} node - a loaded directory node
 * @param {{children?: object[]}} listing - the `directory-tree` answer
 */
export function mergeListingIntoNode(node, listing) {
    const existing = Array.isArray(node.children) ? node.children : []
    node.children = (listing.children || []).map(child => (
        existing.find(old => old.name === child.name && old.type === child.type) || child
    ))
}

/**
 * Create the directory refresher of one panel.
 *
 * `completed(record)` refreshes only the node of `record.target_dir`, when
 * the panel's current tree root contains `record.final_path`. Per
 * `target_dir`, at most one listing fetch runs, plus one trailing fetch if
 * more completions arrive meanwhile. An absent node or a stub
 * (`loaded: false`) is left alone; after the fetch the node is found again
 * (the tree may have been replaced), and the merge goes into that node only.
 *
 * @param {object} deps
 * @param {(path: string) => Promise<object|null>} deps.fetchListing - the panel's `lazyLoadDir`
 * @param {() => ({tree: object, rootPath: string}|null)} deps.getTree - the
 *     current tree and its root path, or null when the panel is not started
 * @param {(tree: object, rootPath: string, path: string) => object|null} [deps.findNode]
 * @param {(node: object, listing: object) => void} [deps.merge]
 * @returns {{completed: (record: object) => Promise<void>, refresh: (targetDir: string) => Promise<void>}}
 */
export function createDirRefresher({
    fetchListing,
    getTree,
    findNode = findTreeNode,
    merge = mergeListingIntoNode,
}) {
    const running = new Map() // targetDir → { trailing, promise }

    function loadedNode(dir) {
        const current = getTree()
        if (!current) return null
        const node = findNode(current.tree, current.rootPath, dir)
        return node && node.loaded !== false ? node : null
    }

    async function refreshOnce(dir) {
        if (!loadedNode(dir)) return
        let listing = null
        try {
            listing = await fetchListing(dir)
        } catch {
            listing = null
        }
        if (!listing) return
        const node = loadedNode(dir)
        if (node) merge(node, listing)
    }

    function refresh(targetDir) {
        const current = running.get(targetDir)
        if (current) {
            current.trailing = true
            return current.promise
        }
        const state = { trailing: false, promise: null }
        running.set(targetDir, state)
        state.promise = (async () => {
            try {
                do {
                    state.trailing = false
                    await refreshOnce(targetDir)
                } while (state.trailing)
            } finally {
                running.delete(targetDir)
            }
        })()
        return state.promise
    }

    function completed(record) {
        const current = getTree()
        if (!current || !record?.final_path || !record.target_dir) return Promise.resolve()
        if (!pathContains(current.rootPath, record.final_path)) return Promise.resolve()
        return refresh(record.target_dir)
    }

    return { completed, refresh }
}
