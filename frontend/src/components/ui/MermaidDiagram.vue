<script setup>
// Renders a standalone Mermaid source to an SVG and makes it pan/zoom-able
// in place — the same direct, in-panel interaction as a rendered image in the
// Files / Artifacts file viewer (NOT the markdown lightbox dialog). Reuses the
// shared mermaid pipeline (lazy-load + per-diagram theme) and usePanZoom.
import { ref, computed, watch } from 'vue'
import { useSettingsStore } from '../../stores/settings'
import { mermaidThemeFor, renderMermaidToSvg } from '../../utils/mermaid'
import { usePanZoom } from '../../composables/usePanZoom'

const props = defineProps({
    code: { type: String, default: '' },
})

const settingsStore = useSettingsStore()
// Same theme as MarkdownContent for the current color scheme.
const theme = computed(() => mermaidThemeFor(settingsStore._effectiveColorScheme))

const svg = ref('')
const error = ref(null)

const diagramRef = ref(null)
const { reset: resetZoom } = usePanZoom(diagramRef)

// Monotonic token: theme/code changes can overlap on their awaits; only the
// latest render may commit its result.
let renderSeq = 0
async function render() {
    const source = props.code
    if (!source || !source.trim()) {
        svg.value = ''
        error.value = null
        return
    }
    const mySeq = ++renderSeq
    try {
        const out = await renderMermaidToSvg(source, theme.value)
        if (mySeq !== renderSeq) return
        svg.value = out
        error.value = null
        resetZoom()
    } catch (e) {
        if (mySeq !== renderSeq) return
        svg.value = ''
        error.value = e?.message || true
    }
}

watch([() => props.code, theme], render, { immediate: true })
</script>

<template>
    <div class="mermaid-pz-container">
        <div v-if="error" class="mermaid-error">{{ error === true ? $t('markdown.media.renderFailed') : error }}</div>
        <!-- v-show (not v-if) so the panzoom target element stays mounted across
             re-renders; usePanZoom binds to it once. -->
        <div
            v-show="!error"
            ref="diagramRef"
            class="mermaid-diagram"
            v-html="svg"
        ></div>
    </div>
</template>

<style scoped>
.mermaid-pz-container {
    position: absolute;
    inset: 0;
    overflow: hidden;
    padding: var(--wa-space-m);
}

/* The panzoom target fills the panel so the diagram scales UP to it. An SVG
   stays crisp at any size (unlike a bitmap), and its viewBox preserves the
   aspect ratio (contain), so it's centered with no distortion. */
.mermaid-diagram {
    width: 100%;
    height: 100%;
    cursor: grab;
}

.mermaid-diagram :deep(svg) {
    display: block;
    width: 100%;
    height: 100%;
    /* mermaid inlines a `max-width: <natural>px` that would cap the diagram far
       below the panel width — override it so it can fill the available area. */
    max-width: 100% !important;
    max-height: 100%;
}

.mermaid-error {
    margin: auto;
    color: var(--wa-color-danger-60, crimson);
    font-size: var(--wa-font-size-s);
    white-space: pre-wrap;
    text-align: center;
}
</style>
