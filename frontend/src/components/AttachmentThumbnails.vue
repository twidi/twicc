<script setup>
// AttachmentThumbnails.vue - Display thumbnails for message attachments
import { ref, computed } from 'vue'
import { FILE_TYPES, mediaToDataUrl } from '../utils/fileUtils'
import AttachmentPreviewDialog from './AttachmentPreviewDialog.vue'

const props = defineProps({
    attachments: {
        type: Array,
        required: true
    }
})

const emit = defineEmits(['remove'])

// Preview dialog state
const previewDialogRef = ref(null)

// Previewable attachments (images and text files, not PDFs)
const previewableAttachments = computed(() => {
    return props.attachments.filter(a => canPreview(a))
})

/**
 * Get display icon name for a file type.
 */
function getIconName(attachment) {
    if (attachment.type === FILE_TYPES.PDF) {
        return 'file-pdf'
    }
    if (attachment.type === FILE_TYPES.TXT) {
        return 'file-lines'
    }
    return 'file'
}

/**
 * Get thumbnail src for an image attachment.
 */
function getThumbnailSrc(attachment) {
    if (attachment.type === FILE_TYPES.IMAGE) {
        return mediaToDataUrl(attachment)
    }
    return null
}

/**
 * Check if an attachment can be previewed.
 * Images and text files can be previewed, PDFs cannot.
 */
function canPreview(attachment) {
    return attachment.type === FILE_TYPES.IMAGE || attachment.type === FILE_TYPES.TXT
}

/**
 * Open preview dialog for an attachment.
 * Finds its index within the previewable list for navigation.
 */
function openPreview(attachment) {
    if (!canPreview(attachment)) return
    const index = previewableAttachments.value.findIndex(a => a.id === attachment.id)
    previewDialogRef.value?.open(index >= 0 ? index : 0)
}

/**
 * Handle remove button click.
 */
function handleRemove(event, attachmentId) {
    event.stopPropagation()
    emit('remove', attachmentId)
}
</script>

<template>
    <div class="attachment-thumbnails">
        <div
            v-for="attachment in attachments"
            :key="attachment.id"
            class="attachment-thumbnail"
            :class="{ 'can-preview': canPreview(attachment) }"
            :title="attachment.name"
            @click="openPreview(attachment)"
        >
            <!-- Image thumbnail -->
            <img
                v-if="attachment.type === FILE_TYPES.IMAGE"
                :src="getThumbnailSrc(attachment)"
                :alt="attachment.name"
                class="thumbnail-image"
            />

            <!-- File icon for non-images -->
            <div v-else class="thumbnail-icon">
                <wa-icon :name="getIconName(attachment)"></wa-icon>
            </div>

            <!-- Remove button -->
            <button
                class="thumbnail-remove"
                @click="(e) => handleRemove(e, attachment.id)"
                title="Remove attachment"
            >
                <wa-icon name="xmark"></wa-icon>
            </button>

            <!-- File name tooltip on hover (for non-images) -->
            <span v-if="attachment.type !== FILE_TYPES.IMAGE" class="thumbnail-name">
                {{ attachment.name }}
            </span>
        </div>
    </div>

    <!-- Preview dialog -->
    <AttachmentPreviewDialog
        ref="previewDialogRef"
        :attachments="previewableAttachments"
    />
</template>

<style scoped>
.attachment-thumbnails {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
    flex: 1;
    overflow-x: auto;
}

.attachment-thumbnail {
    position: relative;
    width: 48px;
    height: 48px;
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--wa-color-border-neutral-tertiary);
    background: var(--wa-color-surface-secondary);
    overflow: hidden;
    flex-shrink: 0;
}

.attachment-thumbnail.can-preview {
    cursor: pointer;
}

.attachment-thumbnail.can-preview:hover {
    border-color: var(--wa-color-border-primary);
}

.thumbnail-image {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.thumbnail-icon {
    width: 100%;
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    color: var(--wa-color-text-quiet);
    font-size: 1.5rem;
}

.thumbnail-remove {
    position: absolute;
    top: -4px;
    right: -4px;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: none;
    background: var(--wa-color-surface-danger);
    color: var(--wa-color-text-on-danger);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.65rem;
    opacity: 0;
    transition: opacity 0.15s ease;
}

.attachment-thumbnail:hover .thumbnail-remove {
    opacity: 1;
}

.thumbnail-name {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 2px 4px;
    background: rgba(0, 0, 0, 0.7);
    color: white;
    font-size: 0.6rem;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    opacity: 0;
    transition: opacity 0.15s ease;
}

.attachment-thumbnail:hover .thumbnail-name {
    opacity: 1;
}
</style>
