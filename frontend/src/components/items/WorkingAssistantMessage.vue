<script setup>
import { computed } from 'vue'

const props = defineProps({
    toolUse: {
        type: Object,
        default: null
    }
})

/**
 * Convert a tool name to a gerund form for display.
 *
 * Regular tools: lowercase, strip trailing vowels, add "ing".
 *   "Bash" → "bashing", "Write" → "writing", "Read" → "reading"
 *
 * MCP tools (prefixed "mcp__"): show "mcping (server-name)".
 *   "mcp__my-server__some_tool" → "mcping (my-server)"
 */
const toolAction = computed(() => {
    if (!props.toolUse?.name) return null
    const name = props.toolUse.name

    // MCP tools: mcp__<server>__<tool> → "mcping (<server>)"
    if (name.startsWith('mcp__')) {
        const parts = name.split('__')
        const server = parts[1] || 'mcp'
        return `mcping (${server})`
    }

    const lower = name.toLowerCase()
    const withoutTrailingVowels = lower.replace(/[aeiou]+$/, '')
    return withoutTrailingVowels + 'ing'
})
</script>

<template>
    <div class="working-assistant-message">
        <wa-spinner></wa-spinner>
        <span>Claude is {{ toolAction || 'working' }}...</span>
    </div>
</template>

<style scoped>
.working-assistant-message {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-style: italic;
}
</style>
