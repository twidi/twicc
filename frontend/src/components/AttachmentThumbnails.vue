<script setup>
// AttachmentThumbnails.vue - Wrapper around MediaThumbnailGroup for draft attachments.
// Converts DraftMedia[] to normalized MediaItem[] and adds removable behavior.
// Switches to compact mode (button + popover) when:
//   - more than COMPACT_THRESHOLD attachments, OR
//   - not enough horizontal space to display all thumbnails inline
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { draftMediaToMediaItem } from '../utils/fileUtils'
import MediaThumbnailGroup from './MediaThumbnailGroup.vue'

/** Above this count, always switch to compact mode */
const COMPACT_THRESHOLD = 4

/** Thumbnail width (px) + gap between them */
const THUMBNAIL_SIZE = 48
const THUMBNAIL_GAP = 8  // --wa-space-xs

const props = defineProps({
    attachments: {
        type: Array,
        required: true
    }
})

const emit = defineEmits(['remove'])

// Convert DraftMedia objects to normalized MediaItem format
const mediaItems = computed(() => {
    return props.attachments.map(a => draftMediaToMediaItem(a))
})

// Container ref for measuring available width
const containerRef = ref(null)
const containerWidth = ref(Infinity)

// ResizeObserver to track container width
let resizeObserver = null

onMounted(() => {
    if (containerRef.value) {
        resizeObserver = new ResizeObserver((entries) => {
            for (const entry of entries) {
                containerWidth.value = entry.contentRect.width
            }
        })
        resizeObserver.observe(containerRef.value)
    }
})

onUnmounted(() => {
    resizeObserver?.disconnect()
})

/**
 * Width needed to display all thumbnails inline.
 * Each thumbnail is THUMBNAIL_SIZE wide, with THUMBNAIL_GAP between them.
 */
const requiredWidth = computed(() => {
    const count = props.attachments.length
    if (count === 0) return 0
    return count * THUMBNAIL_SIZE + (count - 1) * THUMBNAIL_GAP
})

// Whether to show compact mode: too many items OR not enough space
const isCompact = computed(() => {
    const count = props.attachments.length
    if (count > COMPACT_THRESHOLD) return true
    return requiredWidth.value > containerWidth.value
})

/**
 * Handle remove event from MediaThumbnailGroup.
 * Translates the index back to the DraftMedia id for the parent.
 */
function onRemove(index) {
    const attachment = props.attachments[index]
    if (attachment) {
        emit('remove', attachment.id)
    }
}

/**
 * Label for the compact button.
 */
const compactLabel = computed(() => {
    const count = props.attachments.length
    return `${count} file${count > 1 ? 's' : ''}`
})
</script>

<template>
    <div ref="containerRef" class="attachment-thumbnails-container">
        <!-- Inline thumbnails when they fit -->
        <MediaThumbnailGroup
            v-if="!isCompact"
            :items="mediaItems"
            removable
            @remove="onRemove"
            class="attachment-thumbnails"
        />

        <!-- Compact mode: button + popover with full grid -->
        <template v-else>
            <wa-button
                id="attachments-popover-trigger"
                variant="neutral"
                appearance="outlined"
                size="medium"
            >
                <wa-icon name="images" slot="prefix"></wa-icon>
                {{ compactLabel }}
            </wa-button>
            <wa-popover
                for="attachments-popover-trigger"
                placement="top"
                class="attachments-popover"
            >
                <MediaThumbnailGroup
                    :items="mediaItems"
                    removable
                    @remove="onRemove"
                />
            </wa-popover>
        </template>
    </div>
</template>

<style scoped>
.attachment-thumbnails-container {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    margin-left: calc(-1 * var(--wa-space-2xs));
}

.attachment-thumbnails {
    flex-wrap: nowrap;
}

.attachments-popover {
    --max-width: min(400px, 90vw);
    --arrow-size: 8px;
}
</style>
