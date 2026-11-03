<script setup>
import { computed, defineAsyncComponent, onBeforeUnmount, ref, useId, watch } from 'vue'
import { describeMessageSnapshot } from '../../utils/timeline/messageSnapshot'

const MarkdownContent = defineAsyncComponent(() => import('../ui/MarkdownContent.vue'))
const props = defineProps({
    eventId: { type: Number, required: true },
    kind: { type: String, required: true },
    data: { type: Object, default: null },
    contentId: { type: String, default: null },
    labelledBy: { type: String, default: null },
})
const emit = defineEmits(['toggle'])
const expanded = defineModel('expanded', { type: Boolean, default: false })
const vm = computed(() => describeMessageSnapshot(props.kind, props.data))
const instanceId = useId()
const regionId = computed(() => props.contentId ?? `timeline-message-${instanceId}`)
const previewRef = ref(null)
const excerptRef = ref(null)
const overflowing = ref(false)
const hasMore = computed(() => vm.value.hasMoreLines || overflowing.value)
let observer = null

function measurePreview() {
    // Compare with the whole preview width, before the chevron takes its space.
    overflowing.value = !!previewRef.value && !!excerptRef.value
        && excerptRef.value.scrollWidth > previewRef.value.clientWidth + 1
}

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
        <div v-if="!expanded" ref="previewRef" class="timeline-message-preview">
            <p ref="excerptRef" class="timeline-message-excerpt">{{ vm.excerpt }}</p>
            <button
                v-if="vm.expandable && hasMore"
                type="button"
                class="timeline-message-control timeline-message-chevron"
                aria-label="Open message"
                aria-expanded="false"
                :aria-controls="regionId"
                @click="toggle"
            ><wa-icon name="chevron-down" aria-hidden="true"></wa-icon></button>
        </div>
        <div
            v-if="expanded && vm.expandable"
            :id="regionId"
            role="region"
            :aria-labelledby="labelledBy || undefined"
            :aria-label="labelledBy ? undefined : 'Message'"
            class="timeline-message-body"
        >
            <MarkdownContent :source="vm.text" />
            <button type="button" class="timeline-message-control timeline-message-close" @click="toggle">Close message</button>
        </div>
    </div>
</template>

<style scoped>
.timeline-message {
    min-width: 0;
    overflow-wrap: anywhere;
}

.timeline-message-preview {
    display: flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
}

.timeline-message-excerpt {
    flex: 1 1 auto;
    min-width: 0;
    margin: 0;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
    color: var(--wa-color-text-quiet);
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

.timeline-message-chevron {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.25em;
    height: 1.25em;
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
