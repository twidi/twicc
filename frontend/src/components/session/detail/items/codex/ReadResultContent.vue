<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import { getLanguageFromPath } from '../../../../../utils/languages'

/**
 * Codex exec_command Result body, specialised for read-flavoured
 * calls (the parsed_cmd primary is a ``read`` variant). Mirrors
 * Claude Code's ``ReadResultContent``: detects ``cat -n`` /
 * ``nl -ba`` style line numbers in the aggregated output, strips
 * them, and renders the bare code as a fenced code block coloured
 * by the file's extension. Bare cat / sed -n outputs (no leading
 * line numbers) still render as a plain coloured block, just
 * without the "Lines X–Y" header.
 */

const props = defineProps({
    // Concatenated body of the chain — produced by
    // ``aggregateExecCommandOutput`` from every ``function_call_output``
    // row attached to this tool_use_id (the parent exec_command's own
    // output plus any write_stdin polling chunk).
    aggregatedOutput: { type: String, required: true },
    // Path of the file that was read, taken from the primary
    // ``ParsedCommand`` (``read.path``). Drives Shiki's language
    // detection via the file extension.
    path: { type: String, required: true },
})

// Standard ``nl -ba`` / ``cat -n`` separator is a tab. Older Anthropic
// formatting also used the U+2192 arrow; keeping it makes us tolerant
// to that legacy without any cost.
const NUMBERED_LINE_RE = /^(\s*\d+)[→\t](.*)$/

function parseNumberedContent(content) {
    if (typeof content !== 'string') return null
    const lines = content.split('\n')
    if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
    if (lines.length === 0) return null
    // Bail unless the first non-empty line matches: streams without
    // leading numbers should fall through to plain rendering.
    const firstNonEmpty = lines.find((l) => l.length > 0)
    if (!firstNonEmpty || !NUMBERED_LINE_RE.test(firstNonEmpty)) return null
    let startLine = null
    let endLine = null
    const codeLines = []
    for (const line of lines) {
        const match = line.match(NUMBERED_LINE_RE)
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

const parsed = computed(() => parseNumberedContent(props.aggregatedOutput))

const language = computed(() => getLanguageFromPath(props.path) || '')

const markdownSource = computed(() => {
    const code = parsed.value?.code ?? props.aggregatedOutput
    if (typeof code !== 'string' || !code) return null
    return '```' + language.value + '\n' + code + '\n```'
})
</script>

<template>
    <template v-if="markdownSource">
        <div v-if="parsed" class="read-result-header">Lines {{ parsed.startLine }}–{{ parsed.endLine }}</div>
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
