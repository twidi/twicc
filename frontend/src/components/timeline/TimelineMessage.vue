<script setup>
import { computed, defineAsyncComponent, ref, useId, watch } from 'vue'
import { describeMessageSnapshot } from '../../utils/timeline/messageSnapshot'

const MarkdownContent = defineAsyncComponent(() => import('../ui/MarkdownContent.vue'))
const props = defineProps({
    eventId: { type: Number, required: true },
    kind: { type: String, required: true },
    data: { type: Object, default: null },
})
const vm = computed(() => describeMessageSnapshot(props.kind, props.data))
const expanded = ref(false)
const instanceId = useId()
const buttonId = `timeline-message-toggle-${instanceId}`
const regionId = `timeline-message-region-${instanceId}`
// Virtual rows can receive a different event without remounting this component.
watch(() => props.eventId, () => { expanded.value = false }, { flush: 'sync' })
</script>

<template>
    <div v-if="vm.role" class="timeline-message">
        <p v-if="!expanded" class="timeline-message-excerpt">{{ vm.excerpt }}</p>
        <button
            v-if="vm.expandable"
            :id="buttonId"
            type="button"
            class="timeline-message-toggle"
            :aria-expanded="expanded ? 'true' : 'false'"
            :aria-controls="regionId"
            @click="expanded = !expanded"
        >{{ expanded ? 'Hide message' : 'Show message' }}</button>
        <div v-if="expanded && vm.expandable" :id="regionId" role="region" :aria-labelledby="buttonId" class="timeline-message-body">
            <MarkdownContent :source="vm.text" />
        </div>
    </div>
</template>

<style scoped>
.timeline-message {
    min-width: 0;
    overflow-wrap: anywhere;
}

.timeline-message-excerpt {
    margin: 0;
    color: var(--wa-color-text-quiet);
}

.timeline-message-toggle {
    appearance: none;
    border: none;
    background: none;
    padding: 0;
    height: auto;
    font: inherit;
    color: var(--wa-color-brand-60);
    cursor: pointer;
}

.timeline-message-toggle:hover {
    text-decoration: underline;
}

.timeline-message-toggle:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.timeline-message-body {
    margin-block-start: var(--wa-space-xs);
}
</style>
