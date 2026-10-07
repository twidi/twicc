<script setup>
// MediaPreviewDialog.vue - Full-size preview dialog for images, with pan/zoom.
// Supports prev/next navigation via arrow keys and buttons.
// Items: `{ type: 'image', src, name?, link? }` (see composables/useMediaPreview.js).
import { ref, computed, watch, onBeforeUnmount, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { usePanZoom } from '../../composables/usePanZoom'

const props = defineProps({
    items: {
        type: Array,
        default: () => []
    }
})

const emit = defineEmits(['close'])

const dialogRef = ref(null)
const currentIndex = ref(0)
const panzoomRef = ref(null)
const { reset: resetZoom } = usePanZoom(panzoomRef)

watch(currentIndex, () => {
    resetZoom()
})
const prevButtonId = useId()
const nextButtonId = useId()

// Current item based on index
const currentItem = computed(() => {
    if (props.items.length === 0) return null
    return props.items[currentIndex.value] || null
})

// Navigation state
const hasPrev = computed(() => currentIndex.value > 0)
const hasNext = computed(() => currentIndex.value < props.items.length - 1)
const hasNavigation = computed(() => props.items.length > 1)

// Dialog title (filename + position indicator)
const dialogTitle = computed(() => {
    const item = currentItem.value
    if (!item) return 'Preview'

    const name = item.name || (item.type === 'image' ? 'Image' : 'Preview')

    if (hasNavigation.value) {
        return `${name} (${currentIndex.value + 1}/${props.items.length})`
    }
    return name
})

/**
 * Navigate to previous item.
 */
function prev() {
    if (hasPrev.value) {
        currentIndex.value--
    }
}

/**
 * Navigate to next item.
 */
function next() {
    if (hasNext.value) {
        currentIndex.value++
    }
}

/**
 * Handle keyboard navigation.
 */
function onKeyDown(event) {
    if (event.key === 'ArrowLeft') {
        event.preventDefault()
        prev()
    } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        next()
    } else if (event.key === 'Home') {
        event.preventDefault()
        currentIndex.value = 0
    } else if (event.key === 'End') {
        event.preventDefault()
        currentIndex.value = props.items.length - 1
    }
}

/**
 * Open the dialog at a given index.
 */
function open(index = 0) {
    currentIndex.value = index
    resetZoom()
    if (dialogRef.value) {
        dialogRef.value.open = true
    }
    document.addEventListener('keydown', onKeyDown)
}

/**
 * Close the dialog.
 */
function close() {
    if (dialogRef.value) {
        dialogRef.value.open = false
    }
    document.removeEventListener('keydown', onKeyDown)
}

/**
 * Triggered by the underlying wa-dialog's `wa-hide` event — covers every way
 * the dialog can close (programmatic close, Escape, light-dismiss).
 * Cleans up the document-level keydown listener and notifies the parent so
 * external state (e.g. the useMediaPreview singleton) can sync.
 */
function onWaHide(event) {
    // A nested tooltip's own wa-hide bubbles up to here: not a dialog close.
    if (event && event.target !== dialogRef.value) return
    document.removeEventListener('keydown', onKeyDown)
    emit('close')
}

/**
 * Open the current item's link (when set) in a new tab.
 */
function openLink() {
    const link = currentItem.value?.link
    if (!link) return
    window.open(link, '_blank', 'noopener,noreferrer')
}

onBeforeUnmount(() => {
    document.removeEventListener('keydown', onKeyDown)
})

// Expose open/close for parent component
defineExpose({ open, close })
</script>

<template>
    <wa-dialog
        ref="dialogRef"
        :label="dialogTitle"
        :class="['media-preview-dialog', { 'is-image': currentItem?.type === 'image' }]"
        light-dismiss
        @wa-hide="onWaHide"
    >
        <div class="preview-content">
            <!-- Previous button -->
            <button
                v-if="hasNavigation"
                :id="prevButtonId"
                class="nav-button nav-prev"
                :class="{ 'nav-disabled': !hasPrev }"
                :disabled="!hasPrev"
                @click="prev"
            >
                <wa-icon name="chevron-left"></wa-icon>
            </button>
            <AppTooltip :for="prevButtonId">Previous (Left arrow)</AppTooltip>

            <!-- Image preview -->
            <!--
              Panzoom is attached to this wrapper (not the <img>) so its element
              fills the parent stage at origin (0,0). zoomWithWheel computes its
              focal point assuming the panzoomed element's box coincides with the
              parent's; a small <img> centered directly by flex sits offset inside
              the large stage, which breaks the focal math (zoom stops tracking
              the cursor). The wrapper rides the transform while the <img> stays
              centered, at natural size, inside it.
            -->
            <div
                v-if="currentItem?.type === 'image'"
                ref="panzoomRef"
                class="preview-image-stage"
            >
                <img
                    :src="currentItem.src"
                    :alt="currentItem.name || 'Image'"
                    class="preview-image"
                />
            </div>

            <!-- Next button -->
            <button
                v-if="hasNavigation"
                :id="nextButtonId"
                class="nav-button nav-next"
                :class="{ 'nav-disabled': !hasNext }"
                :disabled="!hasNext"
                @click="next"
            >
                <wa-icon name="chevron-right"></wa-icon>
            </button>
            <AppTooltip :for="nextButtonId">Next (Right arrow)</AppTooltip>
        </div>

        <!-- Open-link button in footer (when the current item carries a link) -->
        <wa-button
            v-if="currentItem?.link"
            slot="footer"
            variant="brand"
            appearance="outlined"
            size="small"
            @click="openLink"
        >
            <wa-icon name="arrow-up-right-from-square" slot="start"></wa-icon>
            Open link
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
/*
 * Dialog sizing strategy:
 * - Without an image (no item yet) the panel uses fit-content.
 * - For images (.is-image) the preview-content becomes a large, fixed "stage"
 *   sized to a generous fraction of the viewport, decoupled from the image's
 *   natural size. This is what makes zoom usable on small images: a tiny image
 *   no longer collapses the dialog to its own size, so the zoomed/panned result
 *   has room to be displayed instead of being clipped to a few pixels.
 * - Inside that stage the image stays at its natural size (pixel-perfect, never
 *   upscaled), only shrinking via max-width/max-height: 100% when larger than
 *   the stage. It is centered, and zoom (@panzoom) grows it into the surrounding
 *   space; overflow:hidden clips whatever spills past the stage edges.
 * - The wa-dialog's native .dialog element already caps itself at
 *   max-width/max-height: calc(100% - spacing), so the stage can never overflow
 *   the viewport even on small screens.
 */
.media-preview-dialog {
    --width: fit-content;
}

.media-preview-dialog.is-image .preview-content {
    width: min(90vw, 1400px);
    height: min(80dvh, 920px);
}

.media-preview-dialog::part(header) {
    overflow: hidden;
}

.media-preview-dialog::part(title) {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.media-preview-dialog::part(body) {
    padding: 0;
}

.preview-content {
    display: flex;
    align-items: center;
    justify-content: center;
    position: relative;
    overflow: hidden;
}

/*
 * Panzoom target: fills the stage so the wheel-zoom focal point stays aligned
 * with the cursor (see the template comment). The <img> is centered inside it
 * and keeps its natural size.
 */
.preview-image-stage {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
}

.preview-image {
    display: block;
    max-width: 100%;
    max-height: 100%;
    object-fit: contain;
    touch-action: none;
}

/* Navigation buttons - overlaid on content edges */
.nav-button {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    z-index: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 2.5rem;
    height: 2.5rem;
    border: none;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.4);
    color: white;
    cursor: pointer;
    font-size: 1.1rem;
    opacity: 0.7;
    transition: opacity 0.2s ease, background 0.2s ease;
}

.nav-button:hover:not(:disabled) {
    background: rgba(0, 0, 0, 0.7);
    opacity: 1;
}

.nav-button.nav-disabled {
    opacity: 0.15;
    cursor: default;
    pointer-events: none;
}

.nav-prev {
    left: var(--wa-space-xs);
}

.nav-next {
    right: var(--wa-space-xs);
}

.media-preview-dialog::part(footer) {
    display: flex;
    justify-content: center;
    padding: var(--wa-space-xs) var(--wa-space-m);
}
</style>
