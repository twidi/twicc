<script setup>
import { computed, inject, onBeforeUnmount, useId, watch } from 'vue'
import InlineArtifactFrameOwner from './InlineArtifactFrameOwner.vue'
import FloatingPreviewTools from '../components/frames/FloatingPreviewTools.vue'
import { frameClipPath } from '../utils/panelInsets.js'
import { installInlineRuntimeHost } from './runtimeHost.js'

// These owners survive transcript row detachment and cached view deactivation.
// The application FrameHost owns all iframe DOM nodes.
const props = defineProps({
    runtime: { type: Object, required: true },
    pool: { type: Object, required: true },
    fullscreenZIndex: { type: Number, default: 1002 },
})
const expandPreviewHost = inject('expandPreviewHost', null)
const host = installInlineRuntimeHost({ runtime: props.runtime, window, expandPreviewHost })
const errorToolsId = useId()
const errorActions = entry => [{ id: `inline-error-reload-${errorToolsId}-${entry.frameId}`,
    label: 'Reload', icon: 'rotate-right', action: () => props.runtime.reload(entry.artifactKey) }]
const failedEntries = computed(() => [...props.runtime.entries.values()].filter(entry =>
    props.runtime.active.value && (entry.loadState === 'error' || entry.descriptor.status === 'error') && (entry.attachment && entry.geometryVisible || props.runtime.fullscreenArtifactKey.value === entry.artifactKey)))
function errorStyle(entry) {
    const fullscreen = props.runtime.fullscreenArtifactKey.value === entry.artifactKey
    // CSS tracks viewport changes even when the source row is detached.
    if (fullscreen) return { inset: 0, zIndex: props.fullscreenZIndex }
    const rect = entry.geometryRect
    return { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px`,
        clipPath: frameClipPath(rect, entry.geometryClipRect) }
}
watch(() => props.runtime.fullscreenArtifactKey.value, host.syncFullscreen, { immediate: true, flush: 'sync' })
watch(() => props.pool.geometryEpoch, () => props.runtime.geometry.schedule(), { flush: 'post' })
onBeforeUnmount(host.dispose)
</script>

<template>
    <InlineArtifactFrameOwner
        v-for="entry in runtime.loadedEntries"
        :key="entry.artifactKey"
        :entry="entry"
        :runtime="runtime"
        :pool="pool"
    />
    <Teleport to="body">
        <div v-for="entry in failedEntries" :key="entry.artifactKey" class="inline-artifact-error" role="status" :style="errorStyle(entry)">
            <span>Inline artifact unavailable</span>
            <FloatingPreviewTools
                :actions="errorActions(entry)"
                :fullscreen="runtime.fullscreenArtifactKey.value === entry.artifactKey"
                :fullscreen-disabled="true"
                :reset-key="entry.descriptor.publicationKey"
                :frame-rect="runtime.fullscreenArtifactKey.value === entry.artifactKey ? null : entry.geometryRect"
                :visible-bounds="runtime.fullscreenArtifactKey.value === entry.artifactKey ? null : entry.geometryClipRect"
                @toggle-fullscreen="runtime.closeFullscreen()"
                @drag-start="pool.beginDividerDrag"
                @drag-end="pool.endDividerDrag"
            />
        </div>
    </Teleport>
</template>

<style scoped>
.inline-artifact-error {
    position: fixed; z-index: 3; display: flex; align-items: center; justify-content: center; gap: .5rem;
    background: var(--wa-color-surface-default); color: var(--wa-color-text-normal);
}
</style>
