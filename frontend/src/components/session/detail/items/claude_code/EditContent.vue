<script setup>
import { computed } from 'vue'
import { applyStructuredPatch, reconstructFromHunks } from '../../../../../utils/patchUtils'
import ToolDiffViewer from './ToolDiffViewer.vue'

/**
 * Prepare old_string / new_string for the fragment diff viewer.
 *
 * Two transformations are applied:
 *
 * 1. **Trailing newline normalization:** Ensures both strings end with '\n'.
 *    Without this, appending lines at the end causes the diff engine to mark the
 *    last common line as "changed" (trailing newline mismatch), shifting the change
 *    boundary and breaking the collapse threshold calculation.
 *
 * 2. **Leading padding:** The final full-file diff (with collapseUnchanged) shows 4
 *    visual lines before the first change: 1 "N unchanged lines" header + 3 context
 *    lines (margin=3, minSize=4). We pad with empty lines so the fragment diff
 *    produces the same visual header, avoiding a jump when the final diff arrives.
 *    Required total: margin (3) + minSize (4) = 7 unchanged lines at the start.
 */
function prepareFragmentStrings(original, modified) {
    // Count common prefix on RAW strings (before trailing newline normalization).
    // Counting after normalization would include the trailing '' from the added '\n',
    // which is ambiguous: the diff engine may match it to a different empty line
    // in the modified text, effectively "stealing" one common prefix line and
    // dropping below the collapse threshold.
    const origLines = original.split('\n')
    const modLines = modified.split('\n')
    let commonPrefix = 0
    const limit = Math.min(origLines.length, modLines.length)
    for (let i = 0; i < limit; i++) {
        if (origLines[i] !== modLines[i]) break
        commonPrefix++
    }

    // Normalize trailing newlines so the diff engine sees the last common line
    // as truly unchanged (no "no newline at end of file" mismatch)
    let orig = original.endsWith('\n') ? original : original + '\n'
    let mod = modified.endsWith('\n') ? modified : modified + '\n'

    // margin=3 context lines + minSize=4 to trigger collapse = 7 unchanged lines needed
    const target = 7
    const needed = Math.max(0, target - commonPrefix)
    if (needed > 0) {
        const padding = '\n'.repeat(needed)
        orig = padding + orig
        mod = padding + mod
    }

    return { original: orig, modified: mod }
}

const props = defineProps({
    input: {
        type: Object,
        required: true
    },
    backendPatch: {
        type: Array,
        default: null
    },
    backendPatchLoading: {
        type: Boolean,
        default: false
    },
    originalFile: {
        type: String,
        default: null
    },
    isSubagent: {
        type: Boolean,
        default: false,
    },
})

/**
 * Compute original and modified strings for the diff viewer.
 *
 * Priority:
 * 1. If originalFile + backendPatch available (extras loaded):
 *    original = originalFile, modified = applyStructuredPatch(originalFile, patch)
 *    → full-file diff with smartCollapseUnchanged
 * 2. If backendPatch available without originalFile:
 *    reconstruct original/modified from hunks with real line numbers
 *    → patch-only diff with ellipsis separators
 * 3. Fallback: original = old_string, modified = new_string (fragment only)
 */
const diffData = computed(() => {
    // Subagent: always use raw fragment without padding
    if (props.isSubagent) {
        const oldStr = props.input.old_string ?? ''
        const newStr = props.input.new_string ?? ''
        return { original: oldStr, modified: newStr }
    }

    // Main session: try full-file mode first
    if (props.originalFile != null && props.backendPatch) {
        const modified = applyStructuredPatch(props.originalFile, props.backendPatch)
        if (modified != null) {
            return {
                original: props.originalFile,
                modified,
            }
        }
        // applyPatch failed — fall through
    }

    // Patch-only mode: reconstruct from hunks (real line numbers, more context)
    if (props.backendPatch) {
        const reconstructed = reconstructFromHunks(props.backendPatch)
        if (reconstructed) {
            return reconstructed
        }
    }

    // Fragment mode: use old_string / new_string directly.
    // Normalize trailing newlines and pad with empty lines so collapseUnchanged produces
    // the same visual header (collapsed header + 3 context lines) as the final full-file diff.
    return prepareFragmentStrings(props.input.old_string ?? '', props.input.new_string ?? '')
})

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch)
</script>

<template>
    <div class="edit-content">
        <div v-if="showSpinner" class="edit-loading">
            <wa-spinner></wa-spinner>
        </div>
        <template v-else>
            <p v-if="isSubagent" class="subagent-line-warning">{{ $t('session.tools.edit.fragmentLineNumbers') }}</p>
            <ToolDiffViewer
                mode="diff"
                :original="diffData.original"
                :modified="diffData.modified"
                :file-path="input.file_path"
                :original-line-map="diffData.originalLineMap || null"
                :modified-line-map="diffData.modifiedLineMap || null"
            />
        </template>
    </div>
</template>

<style scoped>
.edit-content {
    height: 23rem;
}
.edit-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}
.subagent-line-warning {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    margin: 0 0 var(--wa-space-2xs) 0;
    font-style: italic;
}
</style>
