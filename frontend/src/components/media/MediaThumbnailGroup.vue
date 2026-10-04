<script setup>
// MediaThumbnailGroup.vue - Display media items as clickable thumbnails with preview dialog.
// Shared between draft attachments (with remove buttons) and conversation messages (read-only).
// Accepts normalized MediaItem[] format.
//
// Composer attachment chips (spec 2026-10-03 §9.3) are items that also carry
// `id`, `name`, `size`, `kind`, `state`, `progress` and `retryable` (built by
// `attachmentChipItem`). They render as an ordered list of rows after the
// legacy thumbnails: thumbnail or kind icon, name, size, upload progress, state
// message, Retry (the `retry` event, keyed by attachment id) and Remove (the
// same index-based `remove` event as the legacy thumbnails).
import { ref, computed, useId } from 'vue'
import MediaPreviewDialog from './MediaPreviewDialog.vue'
import AppTooltip from '../ui/AppTooltip.vue'

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

const emit = defineEmits(['remove', 'retry'])

const previewDialogRef = ref(null)
const idPrefix = useId()

// Items that can be previewed (images and text, not PDF yet)
const previewableTypes = ['image', 'txt']

/** A composer attachment chip (see the header comment). */
function isAttachmentItem(item) {
    return typeof item?.id === 'string' && typeof item.kind === 'string' && typeof item.state === 'string'
}

/**
 * Check if a media item can be previewed in the dialog. Every attachment chip
 * opens it (image, text, or its kind icon).
 */
function canPreview(item) {
    return isAttachmentItem(item) || previewableTypes.includes(item.type)
}

/** Legacy thumbnails and attachment chips, each with its index in `items`. */
const legacyEntries = computed(() =>
    props.items.map((item, index) => ({ item, index })).filter(({ item }) => !isAttachmentItem(item))
)
const chipEntries = computed(() =>
    props.items.map((item, index) => ({ item, index })).filter(({ item }) => isAttachmentItem(item))
)

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
 * Handle remove button click on thumbnail.
 */
function handleRemove(event, index) {
    event.stopPropagation()
    emit('remove', index)
}

/** Chip Retry: keyed by attachment id. */
function handleRetry(event, item) {
    event.stopPropagation()
    emit('retry', item.id)
}

/**
 * Handle remove event from the preview dialog.
 * The dialog emits the index within previewableItems,
 * so we translate it back to the index within the full items array.
 */
function handleDialogRemove(previewIndex) {
    const previewItem = previewableItems.value[previewIndex]
    if (!previewItem) return
    const originalIndex = props.items.indexOf(previewItem)
    if (originalIndex >= 0) {
        emit('remove', originalIndex)
    }
}

/**
 * Get icon name for non-image media types.
 */
function getIconName(item) {
    if (item.icon) return item.icon
    if (item.type === 'pdf') return 'file-pdf'
    if (item.type === 'txt') return 'file-lines'
    return 'file'
}

// Thumbnails whose image failed to load (e.g. a staged entry released by the
// server meanwhile): they fall back to the kind icon.
const brokenSources = ref(new Set())
function onThumbnailError(src) {
    if (!src || brokenSources.value.has(src)) return
    brokenSources.value = new Set([...brokenSources.value, src])
}
function chipThumbnail(item) {
    return item.type === 'image' && item.src && !brokenSources.value.has(item.src) ? item.src : null
}
</script>

<template>
    <div v-if="legacyEntries.length" class="media-thumbnail-group">
        <div
            v-for="{ item, index } in legacyEntries"
            :key="index"
            :id="`${idPrefix}-thumb-${index}`"
            class="media-thumbnail"
            :class="{ 'can-preview': canPreview(item) }"
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
                :id="`${idPrefix}-thumb-remove-${index}`"
                class="thumbnail-remove"
                @click="(e) => handleRemove(e, index)"
            >
                <wa-icon name="xmark"></wa-icon>
            </button>
            <AppTooltip v-if="removable" :for="`${idPrefix}-thumb-remove-${index}`">Remove</AppTooltip>

            <!-- File name tooltip on hover (for non-images) -->
            <span v-if="item.type !== 'image' && item.name" class="thumbnail-name">
                {{ item.name }}
            </span>

            <AppTooltip v-if="item.name" :for="`${idPrefix}-thumb-${index}`">{{ item.name }}</AppTooltip>
        </div>
    </div>

    <!-- Composer attachment chips, in add order -->
    <ol v-if="chipEntries.length" class="attachment-chips">
        <li
            v-for="{ item, index } in chipEntries"
            :key="item.id"
            class="attachment-chip"
            :class="`is-${item.state}`"
        >
            <button
                type="button"
                class="chip-preview"
                :aria-label="`Preview ${item.name}`"
                @click="openPreview(index)"
            >
                <img
                    v-if="chipThumbnail(item)"
                    :src="chipThumbnail(item)"
                    :alt="item.name"
                    class="chip-thumbnail"
                    @error="onThumbnailError(item.src)"
                />
                <wa-icon v-else :name="getIconName(item)"></wa-icon>
            </button>
            <div class="chip-body">
                <span class="chip-name" :title="item.name">{{ item.name }}</span>
                <span class="chip-meta">
                    {{ item.sizeLabel }}<template v-if="item.statusText"> · <span class="chip-status">{{ item.statusText }}</span></template>
                </span>
                <wa-progress-bar
                    v-if="item.state === 'uploading'"
                    class="chip-progress"
                    :value="item.progress"
                    :label="`Upload of ${item.name}`"
                ></wa-progress-bar>
            </div>
            <button
                v-if="item.retryable"
                :id="`${idPrefix}-chip-retry-${index}`"
                type="button"
                class="chip-action"
                :aria-label="`Retry the upload of ${item.name}`"
                @click="(e) => handleRetry(e, item)"
            >
                <wa-icon name="rotate-right"></wa-icon>
            </button>
            <AppTooltip v-if="item.retryable" :for="`${idPrefix}-chip-retry-${index}`">Retry</AppTooltip>
            <button
                v-if="removable"
                :id="`${idPrefix}-chip-remove-${index}`"
                type="button"
                class="chip-action"
                :aria-label="`Remove ${item.name}`"
                @click="(e) => handleRemove(e, index)"
            >
                <wa-icon name="xmark"></wa-icon>
            </button>
            <AppTooltip v-if="removable" :for="`${idPrefix}-chip-remove-${index}`">Remove</AppTooltip>
        </li>
    </ol>

    <!-- Preview dialog -->
    <MediaPreviewDialog
        ref="previewDialogRef"
        :items="previewableItems"
        :removable="removable"
        @remove="handleDialogRemove"
    />
</template>

<style scoped>
.media-thumbnail-group {
    display: flex;
    flex-wrap: wrap;
    gap: var(--wa-space-s);
}

.media-thumbnail-group + .attachment-chips {
    margin-top: var(--wa-space-m);
}

.media-thumbnail {
    position: relative;
    width: 96px;
    height: 96px;
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
    top: -10px;
    right: -10px;
    width: 24px;
    height: 24px;
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

/* Composer attachment chips: one row each, so the name, size, progress and
   actions stay readable in the narrow popover on mobile. */
.attachment-chips {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    max-height: min(50dvh, 24rem);
    overflow-y: auto;
}

.attachment-chip {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
    padding: var(--wa-space-2xs);
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--wa-color-border-neutral-tertiary);
    background: var(--wa-color-surface-secondary);
}

.attachment-chip.is-failed,
.attachment-chip.is-missing {
    border-color: var(--wa-color-danger-border-normal);
}

/* Native <button> resets: WA native styles force a height on buttons. */
.chip-preview,
.chip-action {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    height: auto;
    min-height: 0;
    padding: 0;
    border: none;
    box-shadow: none;
    background: none;
    cursor: pointer;
    color: var(--wa-color-text-quiet);
}

.chip-preview {
    width: 2.75rem;
    height: 2.75rem;
    border-radius: var(--wa-border-radius-s);
    overflow: hidden;
    background: var(--wa-color-surface-default);
    font-size: 1.25rem;
}

.chip-thumbnail {
    width: 100%;
    height: 100%;
    object-fit: cover;
}

.chip-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-3xs);
    flex: 1;
    min-width: 0;
}

.chip-name {
    font-size: var(--wa-font-size-s);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

.chip-meta {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

.is-failed .chip-status,
.is-missing .chip-status {
    color: var(--wa-color-danger-on-quiet);
}

.chip-progress {
    --track-height: 0.25rem;
}

.chip-action {
    width: 2rem;
    height: 2rem;
    border-radius: 50%;
    font-size: 0.8rem;
}

.chip-action:hover {
    background: var(--wa-color-surface-default);
    color: var(--wa-color-text-normal);
}
</style>
