<script setup>
/**
 * AgentStatusLine - The layout of a live agent status line at the bottom of a
 * session: an icon (default slot's first child, typically a ProcessIndicator)
 * followed by an italic phrase.
 *
 * Shared by WorkingAssistantMessage ("Claude is thinking...", ASSISTANT_TURN)
 * and BackgroundWorkStatus ("1 background shell still running", USER_TURN) so
 * both read the same. Content is slotted, so each owner keeps its own scoped
 * styles for what it puts inside.
 */
</script>

<template>
    <div class="agent-status-line">
        <slot></slot>
    </div>
</template>

<style scoped>
.agent-status-line {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    font-style: italic;
    font-size: var(--wa-font-size-m);
}
</style>
