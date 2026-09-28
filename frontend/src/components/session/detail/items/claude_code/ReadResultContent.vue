<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import { getLanguageFromPath } from '../../../../../utils/languages'

const props = defineProps({
    result: { type: [Object, String, Array], required: true },
    input: { type: Object, default: () => ({}) },
})

// Regex to match a cat -n formatted line: optional spaces, digits, separator (→ or tab), then content.
// Old format used → (U+2192 arrow), new format uses \t (standard cat -n). Both must be supported.
const CAT_N_LINE_RE = /^(\s*\d+)[→\t](.*)$/

function parseCatNContent(content) {
    if (typeof content !== 'string') return null
    const lines = content.split('\n')
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    if (lines.length === 0) return null
    const firstNonEmpty = lines.find(l => l.length > 0)
    if (!firstNonEmpty || !CAT_N_LINE_RE.test(firstNonEmpty)) return null
    let startLine = null
    let endLine = null
    const codeLines = []
    for (const line of lines) {
        const match = line.match(CAT_N_LINE_RE)
        if (match) {
            const lineNum = parseInt(match[1], 10)
            if (startLine === null) startLine = lineNum
            endLine = lineNum
            codeLines.push(match[2])
        } else {
            codeLines.push(line)
        }
    }
    return { code: codeLines.join('\n'), startLine, endLine }
}

const parsed = computed(() => {
    const r = props.result
    const content = typeof r === 'string' ? r : r?.content
    return parseCatNContent(content)
})

const markdownSource = computed(() => {
    if (!parsed.value) return null
    const language = getLanguageFromPath(props.input?.file_path) || ''
    return '```' + language + '\n' + parsed.value.code + '\n```'
})
</script>

<template>
    <template v-if="parsed && markdownSource">
        <div class="read-result-header">Lines {{ parsed.startLine }}–{{ parsed.endLine }}</div>
        <MarkdownContent :source="markdownSource" :show-toolbar="false" />
    </template>
</template>

<style scoped>
.read-result-header {
    font-size: var(--wa-font-size-s);
    color: var(--wa-color-text-quiet);
    margin-bottom: var(--wa-space-xs);
}
</style>
