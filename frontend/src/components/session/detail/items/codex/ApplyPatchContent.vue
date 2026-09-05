<script setup>
import { computed, inject, watchEffect } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import { getParsedContent, hasContent } from '../../../../../utils/parsedContent'
import { applyStructuredPatch, reconstructFromHunks } from '../../../../../utils/patchUtils'
import { parseApplyPatchEnvelope } from '../../../../../providers/codex/parsePatch'
import { parseUnifiedDiff } from '../../../../../providers/codex/parseUnifiedDiff'
import { fileChangeItem } from '../../../../../providers/codex/canonical'
import { formatRelativePath, fileIconFor } from '../../../../../providers/utils/path'
import { fileRootsFromStore } from '../../../../../utils/projectRoots'
import ApplyPatchFileEntry from './ApplyPatchFileEntry.vue'

/**
 * Body of an ``apply_patch`` tool_use card. Mirrors Claude Code's
 * ``EditContent`` / ``WriteContent`` (a CodeMirror MergeView in a
 * ``ToolDiffViewer``), with two twists:
 *
 *  - One block per file. ``apply_patch`` can touch several paths in
 *    a single call; each gets its own header + diff viewer.
 *  - Two information sources, depending on whether the matching
 *    canonical ``FileChange`` item is loaded:
 *      * pre-result: parse the raw v4a envelope (``input``) locally.
 *        We get every path + a fragment-style diff per file (no
 *        gutter line numbers).
 *      * post-result: use ``changes[path]`` from the event payload —
 *        absolute paths, ``unified_diff`` with real line numbers for
 *        updates, full ``content`` for adds/deletes. When the backend
 *        managed to capture the pre-patch file contents (via the
 *        CodexAgent ``item/started`` hook on ``FileChangeThreadItem``),
 *        ``payload.original_files[path]`` carries the full original
 *        string and we render a smartCollapseUnchanged diff rather
 *        than a hunk-window reconstruction.
 *
 *    The transition is transparent (Vue reactivity through
 *    ``dataStore.sessionItems``).
 */

const props = defineProps({
    // Raw v4a envelope passed by the model (``custom_tool_call.input``).
    input: { type: String, required: true },
    // Session id of the call — drives both the result lookup and the
    // base-dir computation for relative paths.
    sessionId: { type: String, required: true },
    // ``call_id`` of the originating ``custom_tool_call``. Used to find
    // the matching canonical ``FileChange`` item line in the session.
    toolId: { type: String, required: true },
})

const dataStore = useDataStore()

// Share-only: the canonical ``FileChange`` item line carrying ``original_files`` is
// DEBUG_ONLY and filtered out of the share's /items, so ``patchEndPayload`` (which
// reads it from the store) finds nothing → hunks fallback. Pull it ceiling-exempt
// by tool id and seed the store; patchEndPayload then reacts to its arrival.
const fetchBackendPatchItems = inject('fetchBackendPatchItems', null)
if (fetchBackendPatchItems) {
    watchEffect(async () => {
        const toolState = dataStore.getToolState(props.sessionId, props.toolId)
        const lineNums = toolState?.toolResultLineNums
        if (!Array.isArray(lineNums) || lineNums.length === 0) return
        const missing = lineNums.some((ln) => {
            const it = dataStore.getSessionItem(props.sessionId, ln)
            return !(it && hasContent(it))
        })
        if (!missing) return
        const rows = await fetchBackendPatchItems(props.toolId)
        if (rows?.length) dataStore.addSessionItems(props.sessionId, rows)
    })
}

const session = computed(() => dataStore.getSession(props.sessionId))
const project = computed(() => {
    const s = session.value
    return s ? dataStore.getProject(s.project_id) : null
})

const sessionBaseDir = computed(() => (
    session.value?.git_directory || session.value?.cwd || null
))

// Roots considered safe targets for the View-in-Files button. Same canonical
// derivation the shell uses for its own button (see ``ToolUseContent.vue`` and
// utils/projectRoots.js) so each per-file header has the same eligibility check.
const fileTabRoots = computed(() =>
    fileRootsFromStore(project.value, session.value, dataStore).map(r => r.path)
)

/**
 * Look up the canonical ``FileChange`` item line that pairs with our
 * ``call_id`` and return its parsed payload, or ``null`` until the
 * matching tool_result row reaches the store.
 *
 * Direct hit through the ``toolStates`` index — Codex creates two
 * ``ToolResultLink`` rows per tool_use (the canonical ``FileChange`` item +
 * custom_tool_call_output) at line numbers that aren't necessarily
 * adjacent, so we walk every line number the API surfaces in
 * ``toolResultLineNums`` and return the first line carrying a
 * ``FileChange`` item for our call. Reactive both on
 * item arrival (new line content) and on ``toolStates`` update (new
 * link recorded).
 */
const patchEndPayload = computed(() => {
    const toolState = dataStore.getToolState(props.sessionId, props.toolId)
    const lineNums = toolState?.toolResultLineNums
    if (!Array.isArray(lineNums) || lineNums.length === 0) return null
    for (const ln of lineNums) {
        if (!Number.isInteger(ln) || ln < 1) continue
        const item = dataStore.getSessionItem(props.sessionId, ln)
        if (!item) continue
        const parsed = getParsedContent(item)
        const payload = fileChangeItem(parsed)
        if (!payload) continue
        // Direct apply_patch: the event carries our own call_id. Nested
        // (code-mode) patch: the backend rebound the event to our exec's
        // link chain but the payload keeps Codex's synthesized nested id
        // (``exec-<uuid>``) — accept it, the lineNums list is already
        // scoped to our tool_use so this stays unambiguous.
        if (
            payload.id !== props.toolId
            && !(typeof payload.id === 'string' && payload.id.startsWith('exec-'))
        ) continue
        return payload
    }
    return null
})

/**
 * Per-path lookup table built from the backend's
 * ``compute_link_extra`` JSON, exposed by the ``tool-states``
 * REST view as ``ToolResultLink.extra``. The base orchestration
 * computes stats once when the ``FileChange`` line is recorded;
 * we just read the result here. Returns ``null`` until the link is
 * persisted (live race window or pre-result), so callers fall back
 * to the v4a parser's own counts in that case.
 */
const backendFileStatsByPath = computed(() => {
    const toolState = dataStore.getToolState(props.sessionId, props.toolId)
    if (!toolState?.extra) return null
    let parsed
    try {
        parsed = JSON.parse(toolState.extra)
    } catch {
        return null
    }
    const files = parsed?.files
    if (!Array.isArray(files)) return null
    const out = {}
    for (const f of files) {
        if (f && typeof f.path === 'string') out[f.path] = f
    }
    return out
})

/**
 * Per-file entries to render. The shape mirrors what
 * ``ApplyPatchFileEntry`` consumes.
 *
 * ``diffMode`` is one of:
 *   - 'fragment-update':  pre-result update, free-form old/new strings
 *   - 'full-file-update': post-result update with the original file
 *                         content available in the backend payload
 *                         (preferred — collapseUnchanged-friendly diff)
 *   - 'hunks-update':     post-result update without an original file
 *                         (fallback — patch-window reconstruction)
 *   - 'add':              new file body, full content
 *   - 'delete':           file body that was removed
 *   - 'pending-delete':   pre-result delete (no body to show yet)
 */
/**
 * Per-file +/- counts derived from the ``FileChange`` change entry
 * itself — the fallback when the backend stats aren't available on
 * ``ToolResultLink.extra`` (nested code-mode patches: that slot carries
 * the exec spinner's ``is_terminated`` instead). Mirrors the backend's
 * ``_count_diff_lines`` rules: only ``+`` / ``-`` payload lines of the
 * unified diff count (headers and ``@@`` hunk markers excluded); adds and
 * deletes count every content line.
 */
function countChangeLines(change) {
    if (change.type === 'update') {
        let added = 0
        let removed = 0
        for (const line of (typeof change.unified_diff === 'string' ? change.unified_diff : '').split('\n')) {
            if (!line) continue
            if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) continue
            if (line[0] === '+') added += 1
            else if (line[0] === '-') removed += 1
        }
        return { added, removed }
    }
    const content = typeof change.content === 'string' ? change.content : ''
    const lines = content
        ? (content.match(/\n/g)?.length ?? 0) + (content.endsWith('\n') ? 0 : 1)
        : 0
    if (change.type === 'add') return { added: lines, removed: 0 }
    if (change.type === 'delete') return { added: 0, removed: lines }
    return { added: 0, removed: 0 }
}

const fileEntries = computed(() => {
    const baseDir = sessionBaseDir.value
    const decorate = (path) => ({
        path,
        displayPath: formatRelativePath(path, baseDir),
        fileIconSrc: fileIconFor(path),
    })
    const backendStats = backendFileStatsByPath.value

    const payload = patchEndPayload.value
    if (payload && payload.changes && typeof payload.changes === 'object') {
        // ``original_files`` is the side-band map injected by the backend
        // when ``CodexAgent`` managed to capture pre-patch contents (see
        // ``CodexSessionCompute.transform_tool_result_with_cache``). May be
        // missing entirely (re-compute of an old session, file too large,
        // ``add`` only), or missing a specific ``path`` — the per-file
        // logic below falls back to ``hunks-update`` in that case.
        const originalFiles = (
            payload.original_files && typeof payload.original_files === 'object'
                ? payload.original_files
                : null
        )
        const entries = []
        for (const [path, change] of Object.entries(payload.changes)) {
            if (!change || typeof change !== 'object') continue
            // Stats from the backend's ``ToolResultLink.extra`` when
            // available (direct apply_patch); otherwise derived from
            // the change entry itself (nested code-mode patches never
            // get the stats extra — see ``countChangeLines``).
            const stats = backendStats ? backendStats[path] : null
            const counts = stats
                ? { added: stats.lines_added ?? 0, removed: stats.lines_removed ?? 0 }
                : countChangeLines(change)
            const base = {
                ...decorate(path),
                movePath: null,
                firstModifiedLine: null,
                linesAdded: counts.added,
                linesRemoved: counts.removed,
            }
            if (change.type === 'update') {
                const hunks = parseUnifiedDiff(change.unified_diff || '')
                const originalContent = (
                    originalFiles && typeof originalFiles[path] === 'string'
                        ? originalFiles[path]
                        : null
                )
                // Full-file mode: re-apply the patch on the captured
                // original. Matches ``EditContent.vue`` ergonomics —
                // smartCollapseUnchanged kicks in and the diff card
                // shows the surrounding file context.
                if (originalContent != null && hunks.length) {
                    const modified = applyStructuredPatch(originalContent, hunks)
                    if (modified != null) {
                        entries.push({
                            ...base,
                            movePath: change.move_path || null,
                            diffMode: 'full-file-update',
                            original: originalContent,
                            modified,
                            firstModifiedLine: hunks[0].newStart ?? null,
                        })
                        continue
                    }
                }
                const reconstructed = hunks.length ? reconstructFromHunks(hunks) : null
                if (reconstructed) {
                    entries.push({
                        ...base,
                        movePath: change.move_path || null,
                        diffMode: 'hunks-update',
                        original: reconstructed.original,
                        modified: reconstructed.modified,
                        originalLineMap: reconstructed.originalLineMap,
                        modifiedLineMap: reconstructed.modifiedLineMap,
                        firstModifiedLine: hunks[0].newStart ?? null,
                    })
                } else {
                    // Empty diff (e.g. rename without content change).
                    entries.push({
                        ...base,
                        movePath: change.move_path || null,
                        diffMode: 'fragment-update',
                        original: '',
                        modified: '',
                    })
                }
            } else if (change.type === 'add') {
                const content = typeof change.content === 'string' ? change.content : ''
                entries.push({ ...base, diffMode: 'add', modified: content })
            } else if (change.type === 'delete') {
                const content = typeof change.content === 'string' ? change.content : ''
                entries.push({ ...base, diffMode: 'delete', original: content, modified: '' })
            }
        }
        return entries
    }

    // Pre-result fallback: parse the raw v4a envelope. The parser
    // already counts ``+`` / ``-`` lines per file, so we just forward
    // them through.
    const parsed = parseApplyPatchEnvelope(props.input)
    return parsed.map((file) => {
        const base = {
            ...decorate(file.path),
            movePath: file.movePath,
            firstModifiedLine: null,
            linesAdded: file.linesAdded ?? 0,
            linesRemoved: file.linesRemoved ?? 0,
        }
        if (file.action === 'update') {
            return {
                ...base,
                diffMode: 'fragment-update',
                original: file.oldString ?? '',
                modified: file.newString ?? '',
            }
        }
        if (file.action === 'add') {
            return {
                ...base,
                diffMode: 'add',
                modified: file.content ?? '',
            }
        }
        // Delete: v4a body is empty — we won't have the file content
        // until the result arrives. Render a placeholder block.
        return { ...base, diffMode: 'pending-delete' }
    })
})

const showFileHeader = computed(() => fileEntries.value.length > 1)
</script>

<template>
    <div class="apply-patch-content">
        <div v-if="fileEntries.length === 0" class="apply-patch-empty">
            <wa-icon name="circle-info"></wa-icon>
            Patch envelope not parseable yet.
        </div>
        <ApplyPatchFileEntry
            v-for="(entry, idx) in fileEntries"
            :key="entry.path"
            :path="entry.path"
            :display-path="entry.displayPath"
            :file-icon-src="entry.fileIconSrc"
            :move-path="entry.movePath"
            :diff-mode="entry.diffMode"
            :original="entry.original"
            :modified="entry.modified"
            :original-line-map="entry.originalLineMap"
            :modified-line-map="entry.modifiedLineMap"
            :first-modified-line="entry.firstModifiedLine"
            :lines-added="entry.linesAdded"
            :lines-removed="entry.linesRemoved"
            :file-tab-roots="fileTabRoots"
            :show-header="showFileHeader"
            :is-last="idx === fileEntries.length - 1"
        />
    </div>
</template>

<style scoped>
.apply-patch-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
}

.apply-patch-empty {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) 0;
}
</style>
