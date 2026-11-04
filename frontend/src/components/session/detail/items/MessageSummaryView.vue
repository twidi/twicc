<script setup>
// Chat rendering of a message summary: the summary text replaces the original
// text (same markdown rendering), with a switch to go back to the original.
import { computed } from 'vue'
import MarkdownContent from '../../../ui/MarkdownContent.vue'

const props = defineProps({ summary: { type: Object, required: true } })

const entry = computed(() => props.summary.entry)
const running = computed(() => entry.value?.state === 'running' || props.summary.generation?.state === 'running')
const errors = computed(() => [props.summary.error, entry.value?.error, props.summary.generation?.error]
    .filter((message, index, all) => message && all.indexOf(message) === index))
const canRegenerate = computed(() => !running.value && props.summary.generationAvailable)
</script>

<template>
    <section class="message-summary" aria-label="Message summary" :aria-busy="running">
        <div class="message-summary-controls">
            <wa-switch size="small" checked @change="!$event.target.checked && summary.showOriginal()">Show summary</wa-switch>
            <a v-if="running" href="#" class="summary-link" @click.prevent="summary.cancel">Cancel</a>
            <a v-else-if="canRegenerate" href="#" class="summary-link" @click.prevent="summary.regenerate">Regenerate</a>
            <span v-if="running || summary.loading" class="summary-status" role="status">
                <wa-spinner></wa-spinner> {{ running ? 'Generating…' : 'Loading…' }}
            </span>
        </div>
        <MarkdownContent v-if="entry?.text" :source="entry.text" :summary-context="summary" summary-active speakable />
        <wa-callout v-for="message in errors" :key="message" variant="danger">{{ message }}</wa-callout>
    </section>
</template>

<style scoped>
.message-summary { overflow-wrap: anywhere; }
.message-summary-controls { display: flex; align-items: center; gap: var(--wa-space-m); margin-bottom: var(--wa-space-xs); font-size: var(--wa-font-size-s); }
.summary-link { color: var(--wa-color-brand-text); text-decoration: none; }
.summary-link:hover { text-decoration: underline; }
.summary-status { display: inline-flex; align-items: center; gap: var(--wa-space-xs); color: var(--wa-color-text-quiet); }
</style>
