<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'

const props = defineProps({
    toolUse: {
        type: Object,
        default: null
    },
    toolUseCompleted: {
        type: Boolean,
        default: false
    }
})

/**
 * Convert a tool name to a gerund form for display.
 *
 * Task tool: uses subagent_type from input to pick a friendly label:
 *   "Explore" → "exploring", "Plan" → "planning", "Bash" → "bashing", other → "agenting"
 *
 * MCP tools (prefixed "mcp__"): show "mcping (server-name)".
 *   "mcp__my-server__some_tool" → "mcping (my-server)"
 *
 * Regular tools: lowercase, strip trailing vowels, add "ing".
 *   "Bash" → "bashing", "Write" → "writing", "Read" → "reading"
 */
const TASK_SUBAGENT_LABELS = {
    explore: 'exploring',
    plan: 'planning',
    bash: 'bashing',
}

const toolAction = computed(() => {
    if (!props.toolUse?.name) return null
    const name = props.toolUse.name

    // Task tool (agent): derive label from subagent_type input
    if (name.toLowerCase() === 'task') {
        const subtype = props.toolUse.input?.subagent_type?.toLowerCase()
        if (subtype && TASK_SUBAGENT_LABELS[subtype]) {
            return TASK_SUBAGENT_LABELS[subtype]
        }
        return 'agenting'
    }

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

/**
 * Displayed action with a delayed fallback to "working".
 *
 * - Tool in progress (toolUseCompleted=false): show its name immediately,
 *   keep it on screen indefinitely until the result arrives or a new tool starts.
 * - Tool completed (toolUseCompleted=true): keep showing the tool name for
 *   FALLBACK_DELAY_MS, then fall back to null/"working".
 * - If a new (different) tool starts during the delay, it shows immediately
 *   and cancels the pending fallback.
 */
const FALLBACK_DELAY_MS = 5000

const displayedAction = ref(null)
let pendingTimer = null

watch(
    () => ({ action: toolAction.value, completed: props.toolUseCompleted }),
    ({ action, completed }) => {
        if (pendingTimer) {
            clearTimeout(pendingTimer)
            pendingTimer = null
        }

        if (action != null && !completed) {
            // Tool in progress → show immediately, keep indefinitely
            displayedAction.value = action
        } else if (action != null && completed) {
            // Tool just completed → show it immediately, then schedule fallback
            displayedAction.value = action
            pendingTimer = setTimeout(() => {
                displayedAction.value = null
                pendingTimer = null
            }, FALLBACK_DELAY_MS)
        } else {
            // No tool at all → fall back to "working" immediately
            displayedAction.value = null
        }
    },
    { immediate: true }
)

onUnmounted(() => {
    if (pendingTimer) clearTimeout(pendingTimer)
})
</script>

<template>
    <div class="working-assistant-message text-content">
        <wa-spinner></wa-spinner>
        <span>Claude is {{ displayedAction || 'working' }}...</span>
    </div>
</template>

<style scoped>
.working-assistant-message {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-style: italic;
    font-size: var(--wa-font-size-m);
}
</style>
