<script setup>
// Chat rendering of a message summary: the summary text replaces the original
// text, with a switch to go back to the original. The text goes through
// TextContent, as a direct child of the session item: the chat styles the
// assistant text (toolbar position included) through this exact structure.
import { computed } from 'vue'
import TextContent from './TextContent.vue'

const props = defineProps({ summary: { type: Object, required: true } })

const entry = computed(() => props.summary.entry)
const running = computed(() => entry.value?.state === 'running' || props.summary.generation?.state === 'running')
const errors = computed(() => [props.summary.error, entry.value?.error, props.summary.generation?.error]
    .filter((message, index, all) => message && all.indexOf(message) === index))
const showing = computed(() => props.summary.view === 'summary')
const canRegenerate = computed(() => !running.value && props.summary.generationAvailable)
function toggle(event) {
    if (event.target.checked) props.summary.summarize()
    else props.summary.showOriginal()
}
</script>

<template>
    <div class="message-summary-controls" role="group" aria-label="Message summary" :aria-busy="running">
        <wa-switch size="small" :checked="showing" @change="toggle">Show summary</wa-switch>
        <template v-if="showing">
            <a v-if="running" href="#" class="summary-link" @click.prevent="summary.cancel">Cancel</a>
            <a v-else-if="canRegenerate" href="#" class="summary-link" @click.prevent="summary.regenerate">Regenerate</a>
            <span v-if="running || summary.loading" class="summary-status" role="status">
                <wa-spinner></wa-spinner> {{ running ? 'Generating…' : 'Loading…' }}
            </span>
        </template>
    </div>
    <template v-if="showing">
        <TextContent v-if="entry?.text" :text="entry.text" role="assistant" summary-active />
        <wa-callout v-for="message in errors" :key="message" variant="danger">{{ message }}</wa-callout>
    </template>
</template>

<style scoped>
.message-summary-controls { display: flex; align-items: center; gap: var(--wa-space-m); margin-bottom: var(--wa-space-xs); font-size: var(--wa-font-size-s); }
.summary-link { color: var(--wa-color-text-link); text-decoration: var(--wa-link-decoration-default); text-decoration-thickness: 0.09375em; text-underline-offset: 0.125em; }
.summary-link:hover { color: color-mix(in oklab, var(--wa-color-text-link), var(--wa-color-mix-hover)); }
.summary-status { display: inline-flex; align-items: center; gap: var(--wa-space-xs); color: var(--wa-color-text-quiet); }
</style>
