<script setup>
// AttachmentThumbnails.vue - Wrapper around MediaThumbnailGroup for draft attachments.
// Converts DraftMedia[] to normalized MediaItem[] and adds removable behavior.
import { computed } from 'vue'
import { draftMediaToMediaItem } from '../utils/fileUtils'
import MediaThumbnailGroup from './MediaThumbnailGroup.vue'

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
</script>

<template>
    <MediaThumbnailGroup
        :items="mediaItems"
        removable
        @remove="onRemove"
        class="attachment-thumbnails"
    />
</template>

<style scoped>
.attachment-thumbnails {
    flex: 1;
    overflow-x: auto;
}
</style>
