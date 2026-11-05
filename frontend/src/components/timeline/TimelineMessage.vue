<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, ref, useId, watch } from 'vue'
import { describeMessageSnapshot } from '../../utils/timeline/messageSnapshot'

import MessageSummaryControls from '../session/detail/items/MessageSummaryControls.vue'

const MarkdownContent = defineAsyncComponent(() => import('../ui/MarkdownContent.vue'))
const props = defineProps({
    summary: { type: Object, default: null },
    eventId: { type: Number, required: true },
    kind: { type: String, required: true },
    data: { type: Object, default: null },
    contentId: { type: String, default: null },
    labelledBy: { type: String, default: null },
})
const emit = defineEmits(['toggle', 'more-change'])
const expanded = defineModel('expanded', { type: Boolean, default: false })
const vm = computed(() => describeMessageSnapshot(props.kind, props.data))
// The summary takes the place of the original text.
const bodyText = computed(() => props.summary?.view === 'summary' ? props.summary.entry?.text : vm.value.text)
const instanceId = useId()
const regionId = computed(() => props.contentId ?? `timeline-message-${instanceId}`)
const previewRef = ref(null)
const excerptRef = ref(null)
const overflowing = ref(false)
const hasMore = computed(() => vm.value.hasMoreLines || overflowing.value)
let observer = null

function measurePreview() {
    overflowing.value = !!previewRef.value && !!excerptRef.value
        && excerptRef.value.scrollWidth > previewRef.value.clientWidth + 1
}

watch([() => props.eventId, hasMore], ([, more]) => emit('more-change', more), { immediate: true })

watch(previewRef, element => {
    observer?.disconnect()
    observer = null
    if (element && typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(measurePreview)
        observer.observe(element)
    }
    measurePreview()
}, { flush: 'post' })
watch(() => vm.value.excerpt, measurePreview, { flush: 'post' })
onBeforeUnmount(() => observer?.disconnect())

function toggle() {
    expanded.value = !expanded.value
    emit('toggle')
}

// Virtual rows can receive a different event without remounting this component.
watch(() => props.eventId, () => { expanded.value = false }, { flush: 'sync' })
</script>

<template>
    <div v-if="vm.role" class="timeline-message">
        <!-- Keep measuring this line when expanded so the label's chevron follows width changes. -->
        <div
            ref="previewRef"
            class="timeline-message-preview"
            :class="{ 'timeline-message-preview--measurement': expanded }"
            :aria-hidden="expanded ? 'true' : undefined"
        >
            <button
                v-if="vm.expandable"
                ref="excerptRef"
                type="button"
                class="timeline-message-control timeline-message-excerpt"
                :aria-expanded="String(expanded)"
                :aria-controls="regionId"
                :disabled="expanded"
                @click="toggle"
            >{{ vm.excerpt }}</button>
            <p v-else ref="excerptRef" class="timeline-message-excerpt">{{ vm.excerpt }}</p>
        </div>
        <div
            v-if="expanded && vm.expandable"
            :id="regionId"
            role="region"
            :aria-labelledby="labelledBy || undefined"
            :aria-label="labelledBy ? undefined : 'Message'"
            class="timeline-message-body"
        >
            <MessageSummaryControls v-if="summary?.hasSummary" :summary="summary" />
            <MarkdownContent v-if="bodyText" :source="bodyText" />
            <button type="button" class="timeline-message-control timeline-message-close" @click="toggle">Close message</button>
        </div>
    </div>
</template>

<style scoped>
.timeline-message {
    position: relative;
    min-width: 0;
    overflow-wrap: anywhere;
}

.timeline-message-preview {
    min-width: 0;
}

.timeline-message-preview--measurement {
    position: absolute;
    inset-inline: 0;
    inset-block-start: 0;
    visibility: hidden;
    pointer-events: none;
}

.timeline-message-excerpt {
    display: block;
    width: 100%;
    min-width: 0;
    margin: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: var(--wa-color-text-quiet);
    text-align: start;
}

.timeline-message-control {
    appearance: none;
    border: none;
    background: none;
    padding: 0;
    margin: 0;
    height: auto;
    font: inherit;
    color: var(--wa-color-text-quiet);
    cursor: pointer;
}

.timeline-message-close {
    display: inline-block;
    margin-block-start: var(--wa-space-xs);
    font-size: var(--wa-font-size-xs);
}

.timeline-message-control:hover {
    color: var(--wa-color-brand-60);
}

.timeline-message-close:hover {
    text-decoration: underline;
}

.timeline-message-control:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
