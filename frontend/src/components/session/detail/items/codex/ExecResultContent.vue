<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

/**
 * Codex exec_command Result body.
 *
 * Mirrors Claude Code's ``BashResultContent`` — the input is the
 * ``aggregated_output`` string built by ``aggregateExecCommandOutput``
 * from the chain of ``function_call_output`` rows the backend rebound
 * to this tool_use_id (the parent ``exec_command`` plus every
 * ``write_stdin`` polling output). Codex's CLI no longer ships the
 * structured ``event_msg.exec_command_end`` event, so we reconstruct
 * the same combined stdout/stderr surface from those chunks. We wrap
 * it in a fenced code block (no language so MarkdownContent's Shiki
 * renders it monospace, no syntax coloring) and let MarkdownContent
 * handle the toolbar, raw view and overflow scroll.
 */

const props = defineProps({
    // Synthetic payload from the helper: ``{ aggregated_output }``
    // where ``aggregated_output`` is the concatenated body string.
    result: { type: Object, required: true },
})

const markdownSource = computed(() => {
    const out = props.result?.aggregated_output
    if (typeof out !== 'string' || !out) return null
    return '```\n' + out + '\n```'
})
</script>

<template>
    <MarkdownContent v-if="markdownSource" :source="markdownSource" :show-toolbar="false" />
</template>
