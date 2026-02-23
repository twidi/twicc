<script setup>
// MediaPreviewDialog.vue - Full-size preview dialog for media items (images, text, PDF).
// Supports prev/next navigation via arrow keys and buttons.
// Accepts a normalized MediaItem[] format shared by both draft attachments and conversation messages.
import { ref, computed, onBeforeUnmount, useId } from 'vue'
import AppTooltip from './AppTooltip.vue'

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

const emit = defineEmits(['remove'])

const dialogRef = ref(null)
const currentIndex = ref(0)
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
        class="media-preview-dialog"
        light-dismiss
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
            <img
                v-if="currentItem?.type === 'image'"
                :src="currentItem.src"
                :alt="currentItem.name || 'Image'"
                class="preview-image"
            />

            <!-- Text preview -->
            <pre
                v-else-if="currentItem?.type === 'txt' && currentItem.textContent"
                class="preview-text"
            >{{ currentItem.textContent }}</pre>

            <!-- PDF placeholder -->
            <div
                v-else-if="currentItem?.type === 'pdf'"
                class="preview-placeholder"
            >
                <wa-icon name="file-pdf" style="font-size: 3rem;"></wa-icon>
                <span>PDF preview not yet supported</span>
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

        <!-- Remove button in footer -->
        <wa-button
            v-if="removable"
            slot="footer"
            variant="danger"
            appearance="outlined"
            size="small"
            @click="removeCurrent"
        >
            <wa-icon name="trash" slot="prefix"></wa-icon>
            Remove
        </wa-button>
    </wa-dialog>
</template>

<style scoped>
/*
 * Dialog sizing strategy:
 * - The dialog panel uses fit-content so it wraps tightly around the image.
 * - The wa-dialog's native .dialog element already has
 *   max-width: calc(100% - spacing) and max-height: calc(100% - spacing),
 *   so we don't need to set our own viewport constraints on the panel.
 * - The image constrains itself to the available space inside the dialog
 *   using max-width/max-height with 100%.
 * - The body part has no overflow:hidden - images are never truncated.
 */
.media-preview-dialog {
    --width: fit-content;
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
}

.preview-image {
    display: block;
    max-width: 100%;
    max-height: calc(90vh - 100px);
    object-fit: contain;
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
    max-height: calc(90vh - 100px);
    overflow: auto;
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
