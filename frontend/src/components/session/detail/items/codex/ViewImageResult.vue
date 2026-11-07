<script setup>
import { useI18n } from 'vue-i18n'
const { t } = useI18n()
/**
 * Codex ``view_image`` tool-result renderer.
 *
 * The ``view_image`` tool loads a local image file and feeds it back to
 * the model. Its ``function_call_output`` carries the bytes inline as
 * ``input_image`` part(s) whose ``image_url`` is already a base64
 * ``data:`` URL (the exact image the model saw):
 *
 *   { type: 'function_call_output', call_id, output: [
 *       { type: 'input_image', image_url: 'data:image/png;base64,…' },
 *   ] }
 *
 * We render the image(s) inline inside the Result section instead of the
 * default ``JsonHumanView`` dump (which would print the whole base64
 * blob). Each image is clickable and opens ``MediaPreviewDialog`` for a
 * full-size pan/zoom view — same pattern as :class:`ImageGeneration`.
 */
import { ref, computed } from 'vue'
import MediaPreviewDialog from '../../../../media/MediaPreviewDialog.vue'

const props = defineProps({
    // Base64 ``data:`` URLs pulled from the result's ``input_image``
    // parts by ``CodexToolHelpers.getResultRendering``.
    images: {
        type: Array,
        required: true,
    },
    // Display name for the preview dialog title — the input path
    // basename, falling back to a generic label.
    name: {
        type: String,
        default: null,
    },
})

const displayName = computed(() => props.name || t('session.tools.image.name'))

const previewItems = computed(() =>
    props.images.map((src) => ({ type: 'image', src, name: displayName.value })),
)

const previewDialogRef = ref(null)
function openPreview(index) {
    previewDialogRef.value?.open(index)
}
</script>

<template>
    <div class="view-image-result">
        <div
            v-for="(src, index) in images"
            :key="index"
            class="view-image-wrapper"
        >
            <img
                :src="src"
                :alt="displayName"
                class="view-image-img"
                loading="lazy"
                @click="openPreview(index)"
            />
        </div>
        <MediaPreviewDialog v-if="images.length" ref="previewDialogRef" :items="previewItems" />
    </div>
</template>

<style scoped>
.view-image-result {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-m);
    word-break: break-word;
}

.view-image-wrapper {
    display: flex;
    justify-content: center;
}

.view-image-img {
    max-width: 100%;
    max-height: 75vh;
    object-fit: contain;
    cursor: pointer;
}
</style>
