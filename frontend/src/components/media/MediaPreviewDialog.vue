<script setup>
// MediaPreviewDialog.vue - Full-size preview dialog for media items (images, text, PDF).
// Supports prev/next navigation via arrow keys and buttons.
// Accepts a normalized MediaItem[] format shared by both draft attachments and conversation messages.
// A composer attachment chip (spec 2026-10-03 §9.3) previews an image from
// `src`, a text from `textContent` or `textUrl` (a local object URL or the
// staging content endpoint, read up to TEXT_PREVIEW_LIMIT), and any other
// kind as its icon, name and size.
import { ref, computed, watch, onBeforeUnmount, useId } from 'vue'
import AppTooltip from '../ui/AppTooltip.vue'
import { usePanZoom } from '../../composables/usePanZoom'

/** Bytes of a text attachment shown in its preview. */
const TEXT_PREVIEW_LIMIT = 256 * 1024

const props = defineProps({
    items: {
        type: Array,
        default: () => []
    },
    removable: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['remove', 'close'])

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

// Lazy text of the current item when it only has a `textUrl`.
// { url, text, truncated, error, loading }
const loadedText = ref(null)
let textLoadSeq = 0

async function loadText(url) {
    const seq = ++textLoadSeq
    loadedText.value = { url, text: '', truncated: false, error: false, loading: true }
    try {
        // Lazy: the preview module is only needed once a text preview opens.
        const [{ readTextPreview }, { apiFetch }] = await Promise.all([
            import('../../utils/composerAttachments'),
            import('../../utils/api'),
        ])
        const fetchFn = url.startsWith('blob:') ? fetch : apiFetch
        const { text, truncated } = await readTextPreview(url, { fetch: fetchFn, limit: TEXT_PREVIEW_LIMIT })
        if (seq !== textLoadSeq) return
        loadedText.value = { url, text, truncated, error: false, loading: false }
    } catch {
        if (seq !== textLoadSeq) return
        loadedText.value = { url, text: '', truncated: false, error: true, loading: false }
    }
}

// Only while the dialog is open: a closed dialog never reads a file.
const isOpen = ref(false)

function ensureText() {
    const item = currentItem.value
    if (!isOpen.value || item?.type !== 'txt' || item.textContent || !item.textUrl) return
    if (loadedText.value?.url !== item.textUrl) loadText(item.textUrl)
}

watch(currentItem, ensureText)

const currentText = computed(() => {
    const item = currentItem.value
    if (item?.type !== 'txt') return null
    if (item.textContent) return { text: item.textContent, truncated: false, error: false, loading: false }
    if (item.textUrl && loadedText.value?.url === item.textUrl) return loadedText.value
    return null
})

// Navigation state
const hasPrev = computed(() => currentIndex.value > 0)
const hasNext = computed(() => currentIndex.value < props.items.length - 1)
const hasNavigation = computed(() => props.items.length > 1)

// Dialog title (filename + position indicator)
const dialogTitle = computed(() => {
    const item = currentItem.value
    if (!item) return 'Preview'

    let name = item.name
    if (!name) {
        if (item.type === 'image') name = 'Image'
        else if (item.type === 'pdf') name = 'PDF'
        else if (item.type === 'txt') name = 'Text'
        else name = 'Preview'
    }

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
 * Remove the current item.
 * Emits 'remove' with the current index so the parent can handle deletion.
 * If this was the last item, close the dialog.
 * If we were at the end, move back one position.
 */
function removeCurrent() {
    if (!props.removable || props.items.length === 0) return

    const index = currentIndex.value

    // If this is the last remaining item, close the dialog
    if (props.items.length === 1) {
        emit('remove', index)
        close()
        return
    }

    // If we're at the last position, move back so we don't overshoot
    if (currentIndex.value >= props.items.length - 1) {
        currentIndex.value = props.items.length - 2
    }

    emit('remove', index)
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
    } else if (event.key === 'Delete' || event.key === 'Backspace') {
        if (props.removable) {
            event.preventDefault()
            removeCurrent()
        }
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
    isOpen.value = true
    ensureText()
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
    isOpen.value = false
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

            <!-- Text preview -->
            <div
                v-else-if="currentText && !currentText.loading && !currentText.error"
                class="preview-text-wrap"
            >
                <pre class="preview-text">{{ currentText.text }}</pre>
                <div v-if="currentText.truncated" class="preview-truncated">
                    Preview limited to the first {{ TEXT_PREVIEW_LIMIT / 1024 }} KB.
                </div>
            </div>
            <div
                v-else-if="currentText?.loading"
                class="preview-placeholder"
            >
                <wa-spinner style="font-size: 2rem;"></wa-spinner>
            </div>
            <div
                v-else-if="currentText?.error"
                class="preview-placeholder"
            >
                <wa-icon name="file-lines" style="font-size: 3rem;"></wa-icon>
                <span>Preview not available</span>
            </div>

            <!-- PDF placeholder -->
            <div
                v-else-if="currentItem?.type === 'pdf'"
                class="preview-placeholder"
            >
                <wa-icon name="file-pdf" style="font-size: 3rem;"></wa-icon>
                <span>PDF preview not yet supported</span>
            </div>

            <!-- Any other attachment kind: its icon, name and size -->
            <div
                v-else-if="currentItem?.icon"
                class="preview-placeholder"
            >
                <wa-icon :name="currentItem.icon" style="font-size: 3rem;"></wa-icon>
                <span>{{ currentItem.name }}<template v-if="currentItem.sizeLabel"> · {{ currentItem.sizeLabel }}</template></span>
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

        <!-- Remove button in footer -->
        <wa-button
            v-if="removable"
            slot="footer"
            variant="danger"
            appearance="outlined"
            size="small"
            @click="removeCurrent"
        >
            <wa-icon name="trash" slot="start"></wa-icon>
            Remove
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
/*
 * Dialog sizing strategy:
 * - For text/PDF previews the panel uses fit-content so it wraps tightly
 *   around the content.
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

.preview-text {
    margin: 0;
    padding: var(--wa-space-m);
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-s);
    white-space: pre-wrap;
    word-wrap: break-word;
    background: var(--wa-color-surface-secondary);
    min-width: 300px;
    max-width: calc(90vw - 2rem);
    max-height: calc(90dvh - 100px);
    overflow: auto;
}

.preview-truncated {
    padding: var(--wa-space-2xs) var(--wa-space-m);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
    font-style: italic;
}

.preview-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-m);
    padding: var(--wa-space-xl);
    color: var(--wa-color-text-quiet);
    font-style: italic;
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
