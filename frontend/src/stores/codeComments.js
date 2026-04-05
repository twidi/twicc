// frontend/src/stores/codeComments.js
// Pinia store for code comments (inline annotations on code lines).

import { toRaw } from 'vue'
import { defineStore, acceptHMRUpdate } from 'pinia'
import { getAllCodeComments, saveCodeComment, deleteCodeComment } from '../utils/codeCommentsStorage'
import { getLanguageFromPath } from '../utils/languages'

// ─── Key helpers ────────────────────────────────────────────────────────────

/**
 * Build a serialized key from the 6 comment identity fields.
 * Uses \0 as separator (invalid in file paths on all OSes).
 */
export function buildCommentKey({ projectId, sessionId, filePath, source, sourceRef, lineNumber }) {
    return `${projectId}\0${sessionId}\0${filePath}\0${source}\0${sourceRef}\0${lineNumber}`
}

/**
 * Build the compound key array for IndexedDB operations (delete, get).
 */
export function buildKeyArray({ projectId, sessionId, filePath, source, sourceRef, lineNumber }) {
    return [projectId, sessionId, filePath, source, sourceRef, lineNumber]
}

// ─── Count cache key builders ───────────────────────────────────────────────

function projectKey(c) { return c.projectId }
function sessionKey(c) { return `${c.projectId}\0${c.sessionId}` }
function sourceKey(c) { return `${c.projectId}\0${c.sessionId}\0${c.source}` }
function sourceRefKey(c) { return `${c.projectId}\0${c.sessionId}\0${c.source}\0${c.sourceRef}` }
function fileKey(c) { return `${c.projectId}\0${c.sessionId}\0${c.source}\0${c.sourceRef}\0${c.filePath}` }

const COUNT_KEY_BUILDERS = [projectKey, sessionKey, sourceKey, sourceRefKey, fileKey]

// ─── Debounce timers (module-level, not reactive) ───────────────────────────

const _debouncers = {}
const DEBOUNCE_MS = 500

// ─── Store ──────────────────────────────────────────────────────────────────

export const useCodeCommentsStore = defineStore('codeComments', {
    state: () => ({
        /**
         * All comments indexed by serialized key.
         * @type {Object<string, {projectId: string, sessionId: string, filePath: string, source: string, sourceRef: string, lineNumber: number, lineText: string, content: string, createdAt: number, updatedAt: number}>}
         */
        comments: {},

        /**
         * Cached counts at each hierarchical level.
         * Keyed by the level's composite key (built by COUNT_KEY_BUILDERS).
         * Updated incrementally on add/remove — never recomputed by iteration.
         * @type {{ byProject: Object<string,number>, bySession: Object<string,number>, bySource: Object<string,number>, bySourceRef: Object<string,number>, byFile: Object<string,number> }}
         */
        counts: {
            byProject: {},
            bySession: {},
            bySource: {},
            bySourceRef: {},
            byFile: {},
        },
    }),

    getters: {
        /**
         * Returns a function that filters comments matching a given context.
         * Usage: store.getCommentsForContext({ projectId, sessionId, filePath, source, sourceRef })
         * Returns an array of comment objects (with lineNumber and content).
         */
        getCommentsForContext: (state) => (context) => {
            if (!context) return []
            return Object.values(state.comments).filter(c =>
                c.projectId === context.projectId &&
                c.sessionId === context.sessionId &&
                c.filePath === context.filePath &&
                c.source === context.source &&
                c.sourceRef === (context.sourceRef ?? '')
            )
        },

        /** Get all comments with content for a session (across all files/sources).
         *  Only returns comments with non-empty trimmed content — used for
         *  indicators and "add to message" features. Empty textareas are still
         *  stored and displayed as widgets, but don't signal to the user. */
        getCommentsBySession: (state) => (projectId, sessionId) => {
            return Object.values(state.comments).filter(c =>
                c.projectId === projectId && c.sessionId === sessionId && c.content?.trim()
            )
        },

        // ─── Cached hierarchical count getters (O(1) lookups) ────────────

        /** Count all comments in a project. */
        countByProject: (state) => (projectId) => {
            return state.counts.byProject[projectId] || 0
        },

        /** Count comments in a specific session. */
        countBySession: (state) => (projectId, sessionId) => {
            return state.counts.bySession[`${projectId}\0${sessionId}`] || 0
        },

        /** Count comments for a source tab (files/git/tool) within a session. */
        countBySource: (state) => (projectId, sessionId, source) => {
            return state.counts.bySource[`${projectId}\0${sessionId}\0${source}`] || 0
        },

        /** Count comments for a specific source reference within a session+source. */
        countBySourceRef: (state) => (projectId, sessionId, source, sourceRef) => {
            return state.counts.bySourceRef[`${projectId}\0${sessionId}\0${source}\0${sourceRef ?? ''}`] || 0
        },

        /** Count comments for a specific file within a full context. */
        countByFile: (state) => (projectId, sessionId, source, sourceRef, filePath) => {
            return state.counts.byFile[`${projectId}\0${sessionId}\0${source}\0${sourceRef ?? ''}\0${filePath}`] || 0
        },
    },

    actions: {
        // ─── Count cache management ─────────────────────────────────────

        /** Rebuild all count caches from scratch (called once at hydration).
         *  Only counts comments with non-empty trimmed content (indicators
         *  should only signal "you have content to send", not empty textareas). */
        _rebuildCounts() {
            const caches = [
                this.counts.byProject = {},
                this.counts.bySession = {},
                this.counts.bySource = {},
                this.counts.bySourceRef = {},
                this.counts.byFile = {},
            ]
            for (const comment of Object.values(this.comments)) {
                if (!comment.content?.trim()) continue
                COUNT_KEY_BUILDERS.forEach((fn, i) => {
                    const k = fn(comment)
                    caches[i][k] = (caches[i][k] || 0) + 1
                })
            }
        },

        /** Increment counts for a comment (only if it has content). */
        _incrementCounts(comment) {
            if (!comment.content?.trim()) return
            const caches = [this.counts.byProject, this.counts.bySession, this.counts.bySource, this.counts.bySourceRef, this.counts.byFile]
            COUNT_KEY_BUILDERS.forEach((fn, i) => {
                const k = fn(comment)
                caches[i][k] = (caches[i][k] || 0) + 1
            })
        },

        /** Decrement counts for a comment (only if it had content). */
        _decrementCounts(comment) {
            if (!comment.content?.trim()) return
            const caches = [this.counts.byProject, this.counts.bySession, this.counts.bySource, this.counts.bySourceRef, this.counts.byFile]
            COUNT_KEY_BUILDERS.forEach((fn, i) => {
                const k = fn(comment)
                const newVal = (caches[i][k] || 0) - 1
                if (newVal <= 0) delete caches[i][k]
                else caches[i][k] = newVal
            })
        },

        // ─── CRUD actions ───────────────────────────────────────────────

        /**
         * Hydrate the store from IndexedDB at app startup.
         */
        async hydrateComments() {
            try {
                const all = await getAllCodeComments()
                const comments = {}
                for (const comment of all) {
                    const key = buildCommentKey(comment)
                    comments[key] = comment
                }
                this.comments = comments
                this._rebuildCounts()
            } catch (err) {
                console.error('[codeComments] Failed to hydrate from IndexedDB:', err)
            }
        },

        /**
         * Add a new comment (empty content). Writes to IndexedDB immediately.
         * @param {Object} context - { projectId, sessionId, filePath, source, sourceRef }
         * @param {number} lineNumber
         * @param {string} [lineText]
         */
        addComment(context, lineNumber, lineText) {
            const commentData = {
                projectId: context.projectId,
                sessionId: context.sessionId,
                subagentSessionId: context.subagentSessionId ?? '',
                filePath: context.filePath,
                source: context.source,
                sourceRef: context.sourceRef ?? '',
                toolLineNum: context.toolLineNum ?? null,
                subagentToolLineNum: context.subagentToolLineNum ?? null,
                lineNumber,
                lineText: lineText ?? '',
                content: '',
                createdAt: Date.now(),
                updatedAt: Date.now(),
            }
            const key = buildCommentKey(commentData)
            if (this.comments[key]) return // already exists

            this.comments[key] = commentData
            this._incrementCounts(commentData)
            // Save the plain object (before Vue wraps it in a reactive proxy)
            // — IndexedDB's structured clone cannot handle Proxy objects.
            saveCodeComment({ ...commentData }).catch(err =>
                console.error('[codeComments] Failed to save:', err)
            )
        },

        /**
         * Update comment content. Debounces IndexedDB write.
         * @param {Object} context - { projectId, sessionId, filePath, source, sourceRef }
         * @param {number} lineNumber
         * @param {string} content
         */
        updateComment(context, lineNumber, content) {
            const key = buildCommentKey({ ...context, sourceRef: context.sourceRef ?? '', lineNumber })
            const comment = this.comments[key]
            if (!comment) return

            // Track content transition for count cache updates.
            // Decrement BEFORE content change (guard checks current content),
            // increment AFTER (guard checks new content).
            const hadContent = !!comment.content?.trim()
            const hasContent = !!content?.trim()

            if (hadContent && !hasContent) this._decrementCounts(comment)

            comment.content = content
            comment.updatedAt = Date.now()

            if (!hadContent && hasContent) this._incrementCounts(comment)

            // Debounce IndexedDB write — use toRaw() to unwrap the reactive
            // proxy before saving; IndexedDB's structured clone cannot handle Proxies.
            clearTimeout(_debouncers[key])
            _debouncers[key] = setTimeout(() => {
                const raw = toRaw(this.comments[key])
                if (raw) {
                    saveCodeComment({ ...raw }).catch(err =>
                        console.error('[codeComments] Failed to save:', err)
                    )
                }
                delete _debouncers[key]
            }, DEBOUNCE_MS)
        },

        /**
         * Remove a comment. Deletes from IndexedDB immediately.
         * @param {Object} context - { projectId, sessionId, filePath, source, sourceRef }
         * @param {number} lineNumber
         */
        removeComment(context, lineNumber) {
            const fields = { ...context, sourceRef: context.sourceRef ?? '', lineNumber }
            const key = buildCommentKey(fields)
            // Flush any pending debounced write
            clearTimeout(_debouncers[key])
            delete _debouncers[key]

            const comment = this.comments[key]
            if (comment) this._decrementCounts(comment)
            delete this.comments[key]
            deleteCodeComment(buildKeyArray(fields)).catch(err =>
                console.error('[codeComments] Failed to delete:', err)
            )
        },

        /** Remove all comments for a session. */
        removeAllSessionComments(projectId, sessionId) {
            const keys = Object.entries(this.comments)
                .filter(([, c]) => c.projectId === projectId && c.sessionId === sessionId)
                .map(([key, c]) => ({ key, comment: c, fields: { projectId: c.projectId, sessionId: c.sessionId, filePath: c.filePath, source: c.source, sourceRef: c.sourceRef, lineNumber: c.lineNumber } }))

            for (const { key, comment, fields } of keys) {
                clearTimeout(_debouncers[key])
                delete _debouncers[key]
                this._decrementCounts(comment)
                delete this.comments[key]
                deleteCodeComment(buildKeyArray(fields)).catch(err =>
                    console.error('[codeComments] Failed to delete:', err)
                )
            }
        },
    },
})

// ─── Formatting helpers ─────────────────────────────────────────────────────

/**
 * Return a backtick fence long enough to avoid conflicts with the content.
 * Scans for the longest run of consecutive backticks and uses one more.
 */
function makeFence(text) {
    let max = 0
    const re = /`+/g
    let m
    while ((m = re.exec(text)) !== null) {
        if (m[0].length > max) max = m[0].length
    }
    return '`'.repeat(Math.max(3, max + 1))
}

/**
 * Format a single comment for insertion into the message textarea.
 */
export function formatComment(comment) {
    const lang = getLanguageFromPath(comment.filePath) || ''
    const fence = makeFence(comment.lineText)
    const quotedComment = comment.content.split('\n').map(line => `> ${line}`).join('\n')
    return `\n---\nComment on **\`${comment.filePath}\`** line ${comment.lineNumber}:\n${fence}${lang}\n${comment.lineText}\n${fence}\n\n${quotedComment}`
}

/**
 * Format multiple comments for insertion into the message textarea.
 * Groups by file and sorts by line number for readability.
 */
export function formatAllComments(comments) {
    const sorted = [...comments].sort((a, b) =>
        a.filePath.localeCompare(b.filePath) || a.lineNumber - b.lineNumber
    )
    return sorted.map(c => formatComment(c)).join('\n')
}

/**
 * Build a Set of paths (files + all ancestor directories) from a list of file paths.
 * Used by file trees to show comment indicators on both files and containing folders.
 */
export function buildCommentedPathsSet(filePaths) {
    const set = new Set()
    for (const fp of filePaths) {
        set.add(fp)
        // Add all ancestor directories
        let dir = fp
        while (true) {
            const slash = dir.lastIndexOf('/')
            if (slash <= 0) break
            dir = dir.substring(0, slash)
            if (set.has(dir)) break // already added this and all parents
            set.add(dir)
        }
    }
    return set
}

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(useCodeCommentsStore, import.meta.hot))
}
