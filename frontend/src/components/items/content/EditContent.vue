<script setup>
import { computed } from 'vue'
import { structuredPatch } from 'diff'
import MarkdownContent from '../../MarkdownContent.vue'

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
    }
})

/**
 * Build a hunk descriptor from a structuredPatch hunk object.
 * Used for both backend (real file positions) and frontend (old_string positions) hunks.
 */
function buildHunk(hunk, { useRealLineNumbers = false } = {}) {
    const lines = []
    for (const line of hunk.lines) {
        if (line.startsWith('\\')) continue
        lines.push(line)
    }
    let added = 0
    let removed = 0
    for (const line of lines) {
        if (line.startsWith('+')) added++
        else if (line.startsWith('-')) removed++
    }
    let lineLabel = null
    if (useRealLineNumbers) {
        // Backend hunks have real file positions — show newStart/newLines range directly
        const start = hunk.newStart
        const end = hunk.newStart + hunk.newLines - 1
        lineLabel = end <= start ? `Line ${start}` : `Lines ${start}–${end}`
    }
    return {
        added,
        removed,
        lineLabel,
        source: '```diff\n' + lines.join('\n') + '\n```'
    }
}

/**
 * Diff hunks for display.
 *
 * When backendPatch is available (structuredPatch from the tool_result), we use it directly:
 * real file line numbers, real file context.
 *
 * Otherwise, we fall back to computing the diff locally from old_string / new_string.
 */
const diffHunks = computed(() => {
    // Backend path: use real structuredPatch from tool_result
    if (props.backendPatch) {
        return props.backendPatch.map(hunk => buildHunk(hunk, { useRealLineNumbers: true }))
    }

    // Fallback: compute diff locally from old_string / new_string
    const oldStr = props.input.old_string ?? ''
    const newStr = props.input.new_string ?? ''
    const result = structuredPatch('', '', oldStr, newStr, '', '', { context: 3 })
    return result.hunks.map(hunk => buildHunk(hunk))
})

const multipleHunks = computed(() => diffHunks.value.length > 1)

const isReplaceAll = computed(() => !!props.input.replace_all)

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch)
</script>

<template>
    <div class="edit-content">
        <div v-if="showSpinner" class="edit-loading">
            <wa-spinner></wa-spinner>
        </div>
        <template v-else>
            <div v-for="(hunk, index) in diffHunks" :key="index" class="edit-hunk">
                <div v-if="hunk.lineLabel || multipleHunks" class="edit-hunk-prelude">
                    <span v-if="hunk.lineLabel" class="hunk-lines">{{ hunk.lineLabel }}</span>
                    <template v-if="multipleHunks">
                        <span class="diff-added">+{{ hunk.added }}</span>
                        <span class="diff-removed">-{{ hunk.removed }}</span>
                    </template>
                </div>
                <MarkdownContent :source="hunk.source" />
            </div>
        </template>
    </div>
</template>

<style scoped>
.edit-content {
    padding: var(--wa-space-xs) 0;
}

.edit-replace-all {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    font-style: italic;
    margin-bottom: var(--wa-space-xs);
}

.edit-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}

.edit-hunk + .edit-hunk {
    margin-top: var(--wa-space-s);
}

.edit-hunk-prelude {
    display: flex;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    font-family: var(--wa-font-family-code);
    font-weight: bold;
    padding: var(--wa-space-3xs) 0;

    .hunk-lines {
        color: var(--wa-color-text-quiet);
        font-weight: normal;
    }
    .diff-added {
        color: var(--wa-color-success-50);
    }
    .diff-removed {
        color: var(--wa-color-danger-50);
    }
}

/* Hide the "DIFF" language label — always diff here, no need to show it */
.edit-content :deep(.markdown-body) {
    max-height: 20.25rem;
    overflow: auto;
}

/* Hide the "DIFF" language label — always diff here, no need to show it */
.edit-content :deep(pre.shiki[data-language="diff"]) {
    padding-top: 16px;
}
.edit-content :deep(pre.shiki[data-language="diff"]::before) {
    display: none;
}
</style>
