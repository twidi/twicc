<script setup>
// MediaThumbnailGroup.vue - Display media items as clickable thumbnails with preview dialog.
// Shared between draft attachments (with remove buttons) and conversation messages (read-only).
// Accepts normalized MediaItem[] format.
import { ref, computed } from 'vue'
import MediaPreviewDialog from './MediaPreviewDialog.vue'

const props = defineProps({
    items: {
        type: Array,
        required: true
    },
    removable: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['remove'])

const previewDialogRef = ref(null)

// Items that can be previewed (images and text, not PDF yet)
const previewableTypes = ['image', 'txt']

/**
 * Check if a media item can be previewed in the dialog.
 */
function canPreview(item) {
    return previewableTypes.includes(item.type)
}

/**
 * Previewable items for the dialog (filtered subset).
 */
const previewableItems = computed(() => {
    return props.items.filter(item => canPreview(item))
})

/**
 * Open preview dialog for a given item.
 * Finds its index within the previewable list for navigation.
 */
function openPreview(itemIndex) {
    const item = props.items[itemIndex]
    if (!item || !canPreview(item)) return

    // Find this item's position in the previewable subset
    const previewIndex = previewableItems.value.indexOf(item)
    previewDialogRef.value?.open(previewIndex >= 0 ? previewIndex : 0)
}

/**
 * Handle remove button click.
 */
function handleRemove(event, index) {
    event.stopPropagation()
    emit('remove', index)
}

/**
 * Get icon name for non-image media types.
 */
function getIconName(item) {
    if (item.type === 'pdf') return 'file-pdf'
    if (item.type === 'txt') return 'file-lines'
    return 'file'
}
</script>

<template>
    <div class="media-thumbnail-group">
        <div
            v-for="(item, index) in items"
            :key="index"
            class="media-thumbnail"
            :class="{ 'can-preview': canPreview(item) }"
            :title="item.name || ''"
            @click="openPreview(index)"
        >
            <!-- Image thumbnail -->
            <img
                v-if="item.type === 'image'"
                :src="item.src"
                :alt="item.name || 'Image'"
                class="thumbnail-image"
            />

            <!-- File icon for non-images -->
            <div v-else class="thumbnail-icon">
                <wa-icon :name="getIconName(item)"></wa-icon>
            </div>

            <!-- Remove button (only when removable) -->
            <button
                v-if="removable"
                class="thumbnail-remove"
                @click="(e) => handleRemove(e, index)"
                title="Remove"
            >
                <wa-icon name="xmark"></wa-icon>
            </button>

            <!-- File name tooltip on hover (for non-images) -->
            <span v-if="item.type !== 'image' && item.name" class="thumbnail-name">
                {{ item.name }}
            </span>
        </div>
    </div>

    <!-- Preview dialog -->
    <MediaPreviewDialog
        ref="previewDialogRef"
        :items="previewableItems"
    />
</template>

<style scoped>
.media-thumbnail-group {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-xs);
}

.media-thumbnail {
    position: relative;
    width: 48px;
    height: 48px;
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--wa-color-border-neutral-tertiary);
    background: var(--wa-color-surface-secondary);
    flex-shrink: 0;
}

.media-thumbnail .thumbnail-image,
.media-thumbnail .thumbnail-icon {
    border-radius: inherit;
    overflow: hidden;
}

.media-thumbnail.can-preview {
    cursor: pointer;
}

.media-thumbnail.can-preview:hover {
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
    top: -6px;
    right: -6px;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    border: none;
    background: rgba(0, 0, 0, 0.5);
    color: white;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 0.6rem;
    padding: 0;
    box-shadow: none;
}

.thumbnail-remove:hover {
    background: rgba(0, 0, 0, 0.8);
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

.media-thumbnail:hover .thumbnail-name {
    opacity: 1;
}
</style>
