<script setup>
// The controls of a message summary, above its text: the switch back to the
// original, the regeneration link, the progress and the errors.
import { computed } from 'vue'

const props = defineProps({ summary: { type: Object, required: true } })

const showing = computed(() => props.summary.view === 'summary')
function toggle(event) {
    if (event.target.checked) props.summary.summarize()
    else props.summary.showOriginal()
}
</script>

<template>
    <div class="message-summary-controls" role="group" aria-label="Message summary" :aria-busy="summary.running">
        <div class="message-summary-actions">
            <wa-switch size="small" :checked="showing" @change="toggle">Show summary</wa-switch>
            <template v-if="showing">
                <a v-if="summary.running" href="#" class="summary-link" @click.prevent="summary.cancel">Cancel</a>
                <a v-else-if="summary.generationAvailable" href="#" class="summary-link" @click.prevent="summary.regenerate">Regenerate</a>
                <span v-if="summary.running || summary.loading" class="summary-status" role="status">
                    <wa-spinner></wa-spinner> {{ summary.running ? 'Generating…' : 'Loading…' }}
                </span>
            </template>
        </div>
        <template v-if="showing">
            <wa-callout v-for="message in summary.errors" :key="message" variant="danger">{{ message }}</wa-callout>
        </template>
    </div>
</template>

<style scoped>
.message-summary-controls { display: flex; flex-direction: column; gap: var(--wa-space-xs); margin-bottom: var(--wa-space-xs); font-size: var(--wa-font-size-s); }
/* The line height of the switch: the link and the status stay shorter than it, so the row keeps its height in every state. */
.message-summary-actions { display: flex; align-items: center; gap: var(--wa-space-m); line-height: 1.2; }
.summary-link { color: var(--wa-color-text-link); text-decoration: var(--wa-link-decoration-default); text-decoration-thickness: 0.09375em; text-underline-offset: 0.125em; }
.summary-link:hover { color: color-mix(in oklab, var(--wa-color-text-link), var(--wa-color-mix-hover)); }
.summary-status { display: inline-flex; align-items: center; gap: var(--wa-space-xs); color: var(--wa-color-text-quiet); }
</style>
