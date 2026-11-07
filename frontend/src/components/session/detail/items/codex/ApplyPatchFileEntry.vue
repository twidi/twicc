<script setup>
import { computed, inject } from 'vue'
import ToolDiffViewer from '../claude_code/ToolDiffViewer.vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'

/**
 * One file entry in an ``apply_patch`` body — header (path with icon
 * and a View-in-Files button, only when there's more than one file)
 * plus a CodeMirror ``ToolDiffViewer`` showing the change.
 *
 * Rendering modes (driven by ``diffMode``):
 *  - 'full-file-update' / 'hunks-update' / 'fragment-update' → diff viewer with old/new
 *  - 'add'          → full file body, read-only code editor
 *  - 'delete'       → diff viewer with empty modified side
 *  - 'pending-delete' → placeholder until the result loads the body
 */

const props = defineProps({
    // Absolute path (used as the language hint for syntax highlighting
    // and as the navigation target for the View-in-Files button).
    path: { type: String, required: true },
    // Path made relative to the session's base dir (header text).
    displayPath: { type: String, required: true },
    fileIconSrc: { type: String, default: null },
    movePath: { type: String, default: null },
    diffMode: {
        type: String,
        required: true,
        validator: (v) => [
            'fragment-update', 'hunks-update', 'full-file-update',
            'add', 'delete', 'pending-delete',
        ].includes(v),
    },
    original: { type: String, default: '' },
    modified: { type: String, default: '' },
    originalLineMap: { type: Array, default: null },
    modifiedLineMap: { type: Array, default: null },
    // First modified line on the new side (for the View-in-Files
    // navigation hint). Null when not applicable (delete / add /
    // pending-delete) or when the diff has no hunks to anchor on.
    firstModifiedLine: { type: Number, default: null },
    // Per-file diff stats — surfaced in the header next to the path.
    // The shell's overall ``+N -N`` badge in the tool summary still
    // aggregates over all files (this just gives a per-file breakdown).
    linesAdded: { type: Number, default: 0 },
    linesRemoved: { type: Number, default: 0 },
    // Roots considered valid navigation targets — the View-in-Files
    // button only shows when ``path`` lives under one of them. Comes
    // from ``ApplyPatchContent.vue`` (session cwd / git_directory /
    // project directory / project git_root).
    fileTabRoots: { type: Array, default: () => [] },
    // Show the per-file header. False when the call only touches one
    // file (no need to repeat the path that's already in the summary,
    // and the shell's own View-in-Files button already covers it).
    showHeader: { type: Boolean, default: false },
    // True for the last entry in the file list — used to drop the
    // separator line that sits between adjacent file blocks.
    isLast: { type: Boolean, default: false },
})

const viewerMode = computed(() => (props.diffMode === 'add' ? 'full' : 'diff'))

const viewFileInFilesTab = inject('viewFileInFilesTab', null)

const canViewInFilesTab = computed(() => {
    if (!props.showHeader) return false
    if (!viewFileInFilesTab) return false
    if (!props.path) return false
    return props.fileTabRoots.some((root) => props.path.startsWith(root + '/'))
})

const viewInFilesButtonId = computed(() => `view-in-files-${props.path}`)

function openInFilesTab() {
    if (!viewFileInFilesTab) return
    viewFileInFilesTab(props.path, { lineNum: props.firstModifiedLine ?? null })
}
</script>

<template>
    <div class="apply-patch-file" :class="{ 'with-header': showHeader, 'is-last': isLast }">
        <div v-if="showHeader" class="apply-patch-file-header">
            <span class="apply-patch-file-path">
                <img v-if="fileIconSrc" :src="fileIconSrc" class="apply-patch-file-icon" alt="" />
                <span class="apply-patch-file-name">{{ displayPath }}</span>
                <wa-icon
                    v-if="movePath"
                    name="arrow-right"
                    class="apply-patch-move-arrow"
                ></wa-icon>
                <span v-if="movePath" class="apply-patch-file-name">{{ movePath }}</span>
            </span>
            <span
                v-if="linesAdded > 0 || linesRemoved > 0"
                class="apply-patch-file-stats"
            >
                <span v-if="linesAdded > 0" class="diff-added">+{{ linesAdded }}</span>
                <span v-if="linesRemoved > 0" class="diff-removed">-{{ linesRemoved }}</span>
            </span>
            <template v-if="canViewInFilesTab">
                <wa-button
                    :id="viewInFilesButtonId"
                    size="small"
                    variant="neutral"
                    appearance="outlined"
                    class="apply-patch-view-in-files"
                    @click.stop="openInFilesTab"
                >
                    <wa-icon name="folder-open"></wa-icon>
                </wa-button>
                <AppTooltip :for="viewInFilesButtonId">{{ $t('session.tools.patch.viewInFiles') }}</AppTooltip>
            </template>
        </div>
        <div v-if="diffMode === 'pending-delete'" class="apply-patch-pending">
            <wa-icon name="trash"></wa-icon>
            {{ $t('session.tools.patch.pendingDeletion') }}
        </div>
        <div v-else class="apply-patch-file-body">
            <ToolDiffViewer
                :mode="viewerMode"
                :original="original"
                :modified="modified"
                :file-path="path"
                :original-line-map="originalLineMap"
                :modified-line-map="modifiedLineMap"
            />
        </div>
    </div>
</template>

<style scoped>
.apply-patch-file:not(.is-last) {
    padding-bottom: var(--wa-space-m);
    border-bottom: 1px solid var(--wa-color-surface-border);
}

.apply-patch-file-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    padding: var(--wa-space-2xs) 0;
    margin-bottom: var(--wa-space-2xs);
    font-size: var(--wa-font-size-s);
}

.apply-patch-file-path {
    flex: 1;
    min-width: 0;
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    font-family: var(--wa-font-mono);
    overflow-wrap: anywhere;
}

.apply-patch-file-icon {
    width: 1em;
    height: 1em;
    vertical-align: text-bottom;
    flex-shrink: 0;
}

.apply-patch-move-arrow {
    color: var(--wa-color-text-quiet);
}

.apply-patch-file-stats {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-shrink: 0;
    font-family: var(--wa-font-mono);
    font-weight: bold;
    white-space: nowrap;
}

.apply-patch-file-stats .diff-added {
    color: var(--wa-color-success-50);
}

.apply-patch-file-stats .diff-removed {
    color: var(--wa-color-danger-50);
}

.apply-patch-view-in-files {
    flex-shrink: 0;
    font-size: var(--wa-font-size-s);
}

.apply-patch-file-body {
    height: 23rem;
}

.apply-patch-pending {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-s);
    background: var(--wa-color-surface-raised);
    border-radius: var(--wa-border-radius-m);
}
</style>
