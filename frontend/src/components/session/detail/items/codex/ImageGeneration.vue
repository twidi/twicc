<script setup>
/**
 * Codex ``event_msg.image_generation_end`` renderer.
 *
 * The payload carries everything we need to render the image inline:
 *
 *   - ``revised_prompt``: the actual prompt the image model received
 *     (the user's request after the LLM rewrote it).
 *   - ``result``: the generated image as a base64-encoded PNG string.
 *   - ``saved_path``: where Codex saved the file on disk, typically
 *     ``<codex home>/generated_images/<session>/<call_id>.png``.
 *
 * Codex also emits a ``response_item.image_generation_call`` with the
 * same ``revised_prompt`` and ``result`` (minus the saved_path); the
 * backend bucket it as SYSTEM / DEBUG_ONLY so it doesn't render twice.
 *
 * The inline ``<img>`` is clickable: it opens ``MediaPreviewDialog`` in
 * a full-size pan/zoom view (mirrors the pattern in JsonHumanView for
 * Claude content blocks). The revised prompt is hidden inside a
 * ``wa-details`` with persisted open state (same pattern as
 * :class:`Reasoning` / :class:`CompactSummary`) so the conversation
 * stays compact when the prompt isn't relevant.
 */
import { ref, computed, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import MediaPreviewDialog from '../../../../media/MediaPreviewDialog.vue'

const props = defineProps({
    // Parsed JSONL line:
    // ``{ timestamp, type: 'event_msg', payload: { type: 'image_generation_end',
    //    call_id, status, revised_prompt, result, saved_path } }``
    data: {
        type: Object,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    lineNum: {
        type: Number,
        required: true,
    },
})

const dataStore = useDataStore()

const payload = computed(() => props.data?.payload ?? {})
const revisedPrompt = computed(() => {
    const raw = payload.value.revised_prompt
    return typeof raw === 'string' ? raw : ''
})

// Codex's revised_prompt is a hand-written prose where every newline is
// a paragraph break, but in CommonMark a single ``\n`` is folded into a
// space. We turn each isolated ``\n`` (not next to another ``\n``) into a
// blank-line separator so paragraphs survive the render. Existing
// ``\n\n`` runs are left alone to avoid stacking blank lines further.
const promptMarkdown = computed(() => revisedPrompt.value.replace(/(?<!\n)\n(?!\n)/g, '\n\n'))
const savedPath = computed(() => {
    const raw = payload.value.saved_path
    return typeof raw === 'string' ? raw : ''
})

// Codex always saves generated images as PNG (cf. ``generated_images/``
// folder convention in ``codex-rs/core/src/tools/handlers/image_generation``).
// We hardcode the media type rather than reading it from the payload —
// the JSONL doesn't carry it explicitly and a wrong type silently breaks
// the data: URL.
const imageSrc = computed(() => {
    const raw = payload.value.result
    if (typeof raw !== 'string' || !raw) return ''
    return `data:image/png;base64,${raw}`
})

// Derive a human filename for the preview dialog title — fall back to a
// generic label when ``saved_path`` is missing.
const imageName = computed(() => {
    if (!savedPath.value) return 'Generated image'
    const idx = savedPath.value.lastIndexOf('/')
    return idx >= 0 ? savedPath.value.slice(idx + 1) : savedPath.value
})

const previewItems = computed(() => {
    if (!imageSrc.value) return []
    return [{ type: 'image', src: imageSrc.value, name: imageName.value }]
})

const previewDialogRef = ref(null)
function openPreview() {
    previewDialogRef.value?.open(0)
}

// Persisted prompt open/close state — identical pattern to ``Reasoning``
// and ``CompactSummary``. ``computeVisualItems`` recycles items across
// virtual-scroll mount cycles, so we hydrate from ``dataStore`` and
// write back on every show/hide. ``instantOpen`` short-circuits the
// wa-details show animation when the state was already open at mount
// time (so the row doesn't visibly re-animate after a scroll).
const detailKey = computed(() => `image_prompt:${props.lineNum}`)
const isPromptOpen = ref(dataStore.isDetailOpen(props.sessionId, detailKey.value))
const instantOpen = ref(isPromptOpen.value)

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

function onPromptShow() {
    isPromptOpen.value = true
    dataStore.setDetailOpen(props.sessionId, detailKey.value, true)
}

function onPromptHide() {
    isPromptOpen.value = false
    dataStore.setDetailOpen(props.sessionId, detailKey.value, false)
}
</script>

<template>
    <div class="image-generation-content">
        <div v-if="imageSrc" class="image-generation-image-wrapper">
            <img
                :src="imageSrc"
                :alt="imageName"
                class="image-generation-image"
                loading="lazy"
                @click="openPreview"
            />
        </div>
        <wa-details
            v-if="revisedPrompt"
            :open="isPromptOpen"
            :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null"
            class="image-generation-prompt"
            icon-placement="start"
            @wa-show="onPromptShow"
            @wa-hide="onPromptHide"
        >
            <span slot="summary" class="items-details-summary">
                <strong class="items-details-summary-name">Image prompt</strong>
            </span>
            <div v-if="isPromptOpen" class="image-generation-prompt-body">
                <MarkdownContent :source="promptMarkdown" />
            </div>
        </wa-details>
        <div v-if="savedPath" class="image-generation-path">
            <wa-icon name="image" class="image-generation-path-icon"></wa-icon>
            <span class="image-generation-path-text">{{ savedPath }}</span>
        </div>
        <MediaPreviewDialog v-if="imageSrc" ref="previewDialogRef" :items="previewItems" />
    </div>
</template>

<style scoped>
.image-generation-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    padding-top: var(--wa-space-m);
    word-break: break-word;
}
.virtual-scroller-item > .session-item:not(.is-block-end) .image-generation-content {
    padding-bottom: var(--wa-space-m);
}

.image-generation-image-wrapper {
    display: flex;
    justify-content: center;
}

.image-generation-image {
    max-width: 100%;
    max-height: 75vh;
    object-fit: contain;
    cursor: pointer;
}

wa-details::part(content) {
    padding-top: 0;
}

.image-generation-path {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-family: var(--wa-font-mono);
    font-size: var(--wa-font-size-s);
}

.image-generation-path-icon {
    font-size: 1em;
}

.image-generation-path-text {
    overflow-wrap: anywhere;
}
</style>
