<script setup>
import { computed } from 'vue'
const props = defineProps({
    entry: { type: Object, required: true },
    showOriginal: { type: Boolean, default: false },
})
defineEmits(['cancel', 'retry', 'show-original'])
const paragraphs = computed(() => (props.entry.text || '').split(/\n\s*\n/).filter(text => text.trim()))
</script>

<template>
    <section class="summary-content" aria-label="Summary" :aria-busy="entry.state === 'running'">
        <small v-if="entry.previousPolicy">Previous policy</small>
        <div class="summary-text" aria-live="polite" aria-atomic="true">
            <p v-for="(paragraph, index) in paragraphs" :key="index">{{ paragraph }}</p>
        </div>
        <div v-if="entry.state === 'running'" class="summary-progress" role="status">
            <wa-spinner></wa-spinner> Generating summary…
        </div>
        <wa-callout v-if="entry.error" variant="danger">{{ entry.error }}</wa-callout>
        <small v-else-if="entry.state === 'cancelled'">Summary cancelled.</small>
        <div class="summary-actions">
            <wa-button v-if="entry.state === 'running'" size="small" @click="$emit('cancel')">Cancel</wa-button>
            <wa-button v-else size="small" @click="$emit('retry')">Retry</wa-button>
            <wa-button v-if="showOriginal" size="small" appearance="plain" @click="$emit('show-original')">Show original</wa-button>
        </div>
    </section>
</template>

<style scoped>
.summary-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5; }
.summary-text p { margin: 0 0 var(--wa-space-s); }
.summary-progress, .summary-actions { display: flex; align-items: center; gap: var(--wa-space-s); margin-top: var(--wa-space-s); }
small { color: var(--wa-color-text-quiet); }
</style>
