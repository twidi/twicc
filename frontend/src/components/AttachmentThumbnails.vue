<script setup>
// AttachmentThumbnails.vue - Wrapper around MediaThumbnailGroup for draft attachments.
// Converts DraftMedia[] to normalized MediaItem[] and adds removable behavior.
// Always displays as a button + popover.
import { computed } from 'vue'
import { draftMediaToMediaItem } from '../utils/fileUtils'
import MediaThumbnailGroup from './MediaThumbnailGroup.vue'

const props = defineProps({
    attachments: {
        type: Array,
        required: true
    }
})

const emit = defineEmits(['remove', 'remove-all'])

// Convert DraftMedia objects to normalized MediaItem format
const mediaItems = computed(() => {
    return props.attachments.map(a => draftMediaToMediaItem(a))
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
 * Label for the button.
 */
const buttonLabel = computed(() => {
    const count = props.attachments.length
    return `${count} file${count > 1 ? 's' : ''}`
})
</script>

<template>
    <div class="attachment-thumbnails-container">
        <wa-button
            id="attachments-popover-trigger"
            variant="neutral"
            appearance="outlined"
            size="medium"
        >
            <wa-icon name="images" slot="prefix"></wa-icon>
            {{ buttonLabel }}
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
            <div class="popover-actions">
                <wa-button
                    variant="danger"
                    appearance="outlined"
                    size="small"
                    @click="emit('remove-all')"
                >
                    <wa-icon name="trash" slot="prefix"></wa-icon>
                    Remove all
                </wa-button>
            </div>
        </wa-popover>
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

.attachments-popover {
    --max-width: min(400px, 90vw);
    --arrow-size: 16px;
}

.popover-actions {
    display: flex;
    justify-content: center;
    margin-top: var(--wa-space-l);
}
</style>
