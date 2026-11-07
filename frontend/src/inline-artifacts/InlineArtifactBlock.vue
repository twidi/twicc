<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
import { computed, inject, onBeforeUnmount, onMounted, ref, unref, watch } from 'vue'
import { focusInlineConversation } from './geometry.js'
import { INLINE_ARTIFACT_CONTEXT } from './context.js'

// Only this placeholder enters the virtual transcript. Stable owners render frames and controls.
const props = defineProps({
    artifactKey: { type: String, required: true },
    publicationKey: { type: String, required: true },
    title: { type: String, default: '' },
    status: { type: String, default: 'ready' },
    clipEl: { type: Object, default: null },
    suppressed: { type: Boolean, default: false },
})
const provided = inject(INLINE_ARTIFACT_CONTEXT, null)
const runtime = computed(() => unref(provided)?.runtime ?? null)
const entry = computed(() => runtime.value?.entries.get(props.artifactKey))
const current = computed(() => entry.value?.present && entry.value.descriptor.publicationKey === props.publicationKey)
const frameStatus = computed(() => {
    if (props.status === 'not_included') return t('artifacts.inline.notIncluded')
    if (props.status === 'error' || entry.value?.loadState === 'error') return t('artifacts.inline.unavailable')
    return t('artifacts.inline.loading')
})
const placeholder = ref(null)
const intersecting = ref(false)
let detach = null, intersectionObserver = null, resizeObserver = null, clipObserver = null
const refresh = () => {
    runtime.value?.setVisible(props.artifactKey, intersecting.value && !props.suppressed)
    runtime.value?.geometry.schedule()
}
function bind() {
    detach?.()
    detach = null
    // The conversation resizes (composer growth, mobile keyboard) without moving or resizing this placeholder.
    clipObserver?.disconnect()
    clipObserver = null
    if (!runtime.value || !placeholder.value || !current.value) return
    const clip = props.clipEl || placeholder.value.closest('.virtual-scroller')
    if (clip && typeof ResizeObserver !== 'undefined') {
        clipObserver = new ResizeObserver(() => runtime.value?.geometry.schedule())
        clipObserver.observe(clip)
    }
    detach = runtime.value.attach(props.artifactKey, props.publicationKey, {
        placeholderEl: placeholder.value, clipEl: clip,
        isVisible: () => intersecting.value,
        isSuppressed: () => props.suppressed,
        isElevated: () => !!placeholder.value?.closest('.dock-overlay-panel'),
        focusConversation: () => {
            const target = clip?.isConnected ? clip : placeholder.value?.closest('[tabindex]')
            focusInlineConversation(target)
        },
    })
    refresh()
}
watch([runtime, entry, current, () => props.publicationKey, () => props.clipEl], bind, { flush: 'post' })
watch(() => props.suppressed, refresh, { flush: 'sync' })
watch(() => entry.value?.inlineHeight, () => runtime.value?.geometry.schedule(), { flush: 'post' })
onMounted(() => {
    if (typeof IntersectionObserver !== 'undefined') {
        intersectionObserver = new IntersectionObserver(([item]) => {
            intersecting.value = item.isIntersecting
            refresh()
        }, { root: props.clipEl || placeholder.value.closest('.virtual-scroller') })
        intersectionObserver.observe(placeholder.value)
    } else intersecting.value = true
    if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(refresh)
        resizeObserver.observe(placeholder.value)
    }
    bind()
})
onBeforeUnmount(() => {
    intersectionObserver?.disconnect()
    resizeObserver?.disconnect()
    clipObserver?.disconnect()
    detach?.()
})
</script>

<template>
    <div
        ref="placeholder"
        class="inline-artifact-block"
        :data-inline-publication="publicationKey"
        :style="{ height: `${status === 'not_included' || status === 'error' || entry?.loadState === 'error' ? 160 : current ? entry.inlineHeight : 160}px` }"
        :aria-label="title || entry?.descriptor.title"
    >
        <span v-if="!current || status !== 'ready' || entry?.loadState !== 'ready'" class="inline-artifact-state">
            <slot :entry="entry">{{ frameStatus }}</slot>
        </span>
    </div>
</template>

<style scoped>
.inline-artifact-block { position: relative; width: 100%; min-height: 160px; max-height: 900px; }
.inline-artifact-state { display: block; padding: 1rem; color: var(--wa-color-text-quiet); }
</style>
