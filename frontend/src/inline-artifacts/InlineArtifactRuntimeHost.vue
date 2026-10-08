<script setup>
import { computed, inject, onBeforeUnmount, watch } from 'vue'
import InlineArtifactFrameOwner from './InlineArtifactFrameOwner.vue'
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
const failedEntries = computed(() => [...props.runtime.entries.values()].filter(entry =>
    props.runtime.active.value && (entry.loadState === 'error' || entry.descriptor.status === 'error') && entry.attachment && entry.geometryVisible
    && props.runtime.fullscreenArtifactKey.value !== entry.artifactKey))
function errorStyle(entry) {
    const rect = entry.geometryRect
    return { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px`,
        clipPath: frameClipPath(rect, entry.geometryClipRect) }
}
const fullscreenEntry = computed(() => props.runtime.entries.get(props.runtime.fullscreenArtifactKey.value))
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
        <div v-for="entry in failedEntries" :key="entry.artifactKey" class="inline-artifact-error" :style="errorStyle(entry)">
            <span>Inline artifact unavailable</span>
            <button type="button" @click="runtime.reload(entry.artifactKey)">Reload</button>
        </div>
    </Teleport>
    <div v-if="fullscreenEntry" class="inline-artifact-fullscreen-toolbar" :style="{ zIndex: fullscreenZIndex }">
        <span>{{ fullscreenEntry.descriptor.title }}</span>
        <button type="button" @click="runtime.reload(fullscreenEntry.artifactKey)">Reload</button>
        <button type="button" @click="runtime.closeFullscreen()">Exit full screen</button>
    </div>
</template>

<style scoped>
.inline-artifact-error {
    position: fixed; z-index: 3; display: flex; align-items: center; justify-content: center; gap: .5rem;
    background: var(--wa-color-surface-default); color: var(--wa-color-text-normal);
}
.inline-artifact-fullscreen-toolbar {
    position: fixed; inset: 0 0 auto; height: 44px; box-sizing: border-box;
    display: flex; align-items: center; gap: .5rem; padding: 0 .75rem;
    background: var(--wa-color-surface-default); color: var(--wa-color-text-normal);
}
.inline-artifact-fullscreen-toolbar span { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
