<script setup>
/**
 * Renders the final result of a Codex `spawn_agent` tool call.
 *
 * Source of truth is the second `ToolResultLink` of the spawn_agent —
 * the synthetic one rebound from the `<subagent_notification>` user
 * message Codex injects in the parent thread when the subagent reaches
 * a final `AgentStatus`. The first link (the spawn ack carrying
 * `{agent_id, nickname}`) is handled separately by `transformDisplayResult`.
 *
 * The status enum is rendered as:
 *  - `Completed(msg)` -> the message body as markdown
 *  - `Errored(msg)` -> the same body, error styling already comes from the
 *    `ToolResultLink.error` field via the standard error callout
 *  - `"shutdown"` / `"not_found"` -> a short label (no body to render)
 */
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

defineProps({
    /** The completed/errored message body, or null for status-only variants. */
    message: { type: String, default: null },
    /** Short label for status-only variants (`shutdown`, `not_found`). */
    statusLabel: { type: String, default: null },
    /** Subagent nickname captured from the spawn ack, e.g. "Chandrasekhar". */
    nickname: { type: String, default: null },
})
</script>

<template>
    <div class="spawn-agent-result">
        <div v-if="nickname" class="spawn-agent-meta">
            Subagent: <strong>{{ nickname }}</strong>
        </div>
        <MarkdownContent v-if="message" :source="message" :show-toolbar="false" />
        <div v-else-if="statusLabel" class="spawn-agent-status-only">
            {{ statusLabel }}
        </div>
    </div>
</template>

<style scoped>
.spawn-agent-result {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
}
.spawn-agent-meta {
    font-size: 0.875rem;
    color: var(--wa-color-text-quiet);
}
.spawn-agent-status-only {
    font-style: italic;
    color: var(--wa-color-text-quiet);
}
</style>
