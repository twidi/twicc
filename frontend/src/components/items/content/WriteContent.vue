<script setup>
import { computed } from 'vue'
import { getLanguageFromPath } from '../../../utils/languages'
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
 */
function buildHunk(hunk) {
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
    const start = hunk.newStart
    const end = hunk.newStart + hunk.newLines - 1
    const lineLabel = end <= start ? `Line ${start}` : `Lines ${start}–${end}`
    return {
        added,
        removed,
        lineLabel,
        source: '```diff\n' + lines.join('\n') + '\n```'
    }
}

/**
 * When backendPatch is available, render as diff hunks (same as EditContent).
 */
const diffHunks = computed(() => {
    if (!props.backendPatch) return null
    return props.backendPatch.map(hunk => buildHunk(hunk))
})

const multipleHunks = computed(() => diffHunks.value && diffHunks.value.length > 1)

/**
 * Fallback: render the file content as a syntax-highlighted fenced code block.
 * Language is detected from file_path.
 */
const codeSource = computed(() => {
    if (diffHunks.value) return null
    const content = props.input.content ?? ''
    const language = getLanguageFromPath(props.input.file_path) || ''
    return '```' + language + '\n' + content + '\n```'
})

const showSpinner = computed(() => props.backendPatchLoading && !props.backendPatch)
</script>

<template>
    <div class="write-content">
        <div v-if="showSpinner" class="write-loading">
            <wa-spinner></wa-spinner>
        </div>
        <template v-else-if="diffHunks">
            <div v-for="(hunk, index) in diffHunks" :key="index" class="write-hunk">
                <div v-if="hunk.lineLabel || multipleHunks" class="write-hunk-prelude">
                    <span v-if="hunk.lineLabel" class="hunk-lines">{{ hunk.lineLabel }}</span>
                    <template v-if="multipleHunks">
                        <span class="diff-added">+{{ hunk.added }}</span>
                        <span class="diff-removed">-{{ hunk.removed }}</span>
                    </template>
                </div>
                <MarkdownContent :source="hunk.source" />
            </div>
        </template>
        <MarkdownContent v-else :source="codeSource" />
    </div>
</template>

<style scoped>
.write-content {
    padding: var(--wa-space-xs) 0;
}

.write-loading {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-s) 0;
}

.write-content :deep(.markdown-body) {
    max-height: 20.25rem;
    overflow: auto;
}

.write-hunk + .write-hunk {
    margin-top: var(--wa-space-xs);
}

.write-hunk-prelude {
    display: flex;
    gap: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
    font-family: var(--wa-font-family-mono);
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

/* Hide the "DIFF" language label when showing diff hunks */
.write-hunk :deep(pre.shiki[data-language="diff"]) {
    padding-top: 16px;
}
.write-hunk :deep(pre.shiki[data-language="diff"]::before) {
    display: none;
}
</style>
