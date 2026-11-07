<script setup>
// The controls of a message summary, above its text: the switch back to the
// original, the regeneration link, the progress and the errors.
import { computed } from 'vue'
import SummaryGenerationLinks from '../../../ui/SummaryGenerationLinks.vue'

const props = defineProps({ summary: { type: Object, required: true } })

const showing = computed(() => props.summary.view === 'summary')
function toggle(event) {
    if (event.target.checked) props.summary.summarize()
    else props.summary.showOriginal()
}
</script>

<template>
    <div class="message-summary-controls" role="group" :aria-label="$t('session.summary.messageLabel')" :aria-busy="summary.running">
        <div class="message-summary-actions">
            <wa-switch size="small" :checked="showing" @change="toggle">{{ $t('session.summary.show') }}</wa-switch>
            <template v-if="showing">
                <SummaryGenerationLinks
                    :running="summary.running"
                    :loading="summary.loading"
                    :can-regenerate="summary.generationAvailable"
                    @cancel="summary.cancel"
                    @regenerate="summary.regenerate"
                />
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
.message-summary-actions { display: flex; flex-wrap: wrap; align-items: center; gap: var(--wa-space-xs) var(--wa-space-m); line-height: 1.2; }
/* On a narrow screen the items move to the next line instead of breaking their own text. */
.message-summary-actions > :deep(*) { white-space: nowrap; }
</style>
