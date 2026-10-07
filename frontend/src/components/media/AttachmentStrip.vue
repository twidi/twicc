<script setup>
// AttachmentStrip.vue - Every display of attachments: one row of uniform square
// tiles (image thumbnail, else the icon of the kind), the name under each tile,
// the full name in a tooltip.
//
// Read-only (default): the ordered attachments of a user message in the history
// (spec 2026-10-03 §10.2), in the order the user attached them. Items come from
// `buildAttachmentStrip` / `optimisticAttachmentStrip` / `nativeMediaStripItems`
// (utils/attachmentStrip.js). A file chip that can open emits `open-artifact`
// with `{owner, relativePath}`; the parent opens it through the existing
// Artifacts tab navigation. In the share viewer (explicit share mode) no chip
// links, whatever the items say.
//
// Editable (`editable`): the composer's attachments (§9.3), items from
// `composerStripItems` (utils/composerAttachments.js). Each tile gets a Remove
// button (`remove` event, the item id), the upload progress while uploading, the
// error state of a failed or missing upload, and Retry when the upload can be
// retried (`retry` event, the item id). The tooltip adds the size and the state.
// No artifact link.
//
// In both modes, only images preview (the global media preview).
import { computed, inject, nextTick, ref, useId, watch } from 'vue'
import { openMediaPreview } from '../../composables/useMediaPreview'
import {
    ATTACHMENT_SHARE_MODE,
    attachmentKindIcon,
    editableTileState,
    focusCandidatesAfterRemove,
    stripItemArtifactRequest,
} from '../../utils/attachmentStrip'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    items: {
        type: Array,
        required: true
    },
    // The composer's strip: Remove, Retry and the upload state on each tile.
    editable: {
        type: Boolean,
        default: false
    }
})

const emit = defineEmits(['open-artifact', 'remove', 'retry'])

const share = inject(ATTACHMENT_SHARE_MODE, false) === true

// Thumbnails whose image failed to load (e.g. a staged entry released by the
// server meanwhile) fall back to the icon of their kind.
const brokenSources = ref(new Set())
function onThumbnailError(src) {
    if (!src || brokenSources.value.has(src)) return
    brokenSources.value = new Set([...brokenSources.value, src])
}

// Prefix of the anchor ids the tooltips point at. Each item keeps its own number
// for the life of the strip, so a Remove never moves a tooltip onto another tile.
const stripId = useId()
const anchorNumbers = new Map()
function anchorIdOf(itemId) {
    if (!anchorNumbers.has(itemId)) anchorNumbers.set(itemId, anchorNumbers.size)
    return `attachment-${stripId}-${anchorNumbers.get(itemId)}`
}

const entries = computed(() => props.items.map((item) => {
    const thumbnail = item.src && !brokenSources.value.has(item.src) ? item.src : null
    const anchorId = anchorIdOf(item.id)
    return {
        item,
        anchorId,
        removeId: `${anchorId}-remove`,
        retryId: `${anchorId}-retry`,
        thumbnail,
        request: share || props.editable ? null : stripItemArtifactRequest(item),
        icon: attachmentKindIcon(item.kind),
        tile: props.editable ? editableTileState(item) : null,
    }
}))

// Every thumbnail of the strip, for prev/next navigation in the preview.
const thumbnailEntries = computed(() => entries.value.filter(entry => entry.thumbnail))

function openPreview(id) {
    const list = thumbnailEntries.value
    const index = list.findIndex(entry => entry.item.id === id)
    openMediaPreview(
        list.map(entry => ({ type: 'image', src: entry.thumbnail, name: entry.item.name })),
        Math.max(0, index),
    )
}

function openArtifact(request) {
    if (share || !request) return
    emit('open-artifact', request)
}

// Remove buttons by item id, to keep the keyboard focus in the strip once the
// button that had it goes away. The parent updates the items later (asynchronously).
// - After a Remove: the Remove button of the next tile, else of the previous one.
// - After a Retry (the tile no longer offers Retry): the tile's own Remove button.
const removeButtons = new Map()
function setRemoveButton(itemId, el) {
    if (el) removeButtons.set(itemId, el)
    else removeButtons.delete(itemId)
}
let pendingRemove = null
let pendingRetry = null

function remove(itemId) {
    pendingRetry = null
    pendingRemove = { removedId: itemId, candidates: focusCandidatesAfterRemove(props.items, itemId) }
    emit('remove', itemId)
}

function retry(itemId) {
    pendingRetry = itemId
    emit('retry', itemId)
}

async function focusRemoveButton(candidates) {
    await nextTick()
    for (const id of candidates) {
        const button = removeButtons.get(id)
        if (button) {
            button.focus()
            return
        }
    }
}

watch(() => props.items, (items) => {
    const removed = pendingRemove
    if (removed && !items.some(item => item.id === removed.removedId)) {
        pendingRemove = null
        focusRemoveButton(removed.candidates)
    }
    const retried = pendingRetry
    if (retried && !items.some(item => item.id === retried && item.retryable)) {
        pendingRetry = null
        if (items.some(item => item.id === retried)) focusRemoveButton([retried])
    }
})
</script>

<template>
    <ol v-if="entries.length" class="attachment-strip" :class="{ 'is-editable': editable }">
        <li
            v-for="entry in entries"
            :key="entry.item.id"
            class="attachment-strip-item"
            :class="entry.tile && { 'is-uploading': entry.tile.uploading, 'is-error': entry.tile.error }"
        >
            <!-- Every attachment is the same square tile with its name under it, so a
                 mix of images and files lines up: the image itself when there is one,
                 else the icon of its kind. The name is cut to the tile width: the tooltip
                 gives it whole. The tooltip anchor holds the tile and its name only: the
                 buttons of an editable tile are its siblings, with their own tooltips. -->
            <div :id="entry.anchorId" class="strip-entry">
                <template v-if="entry.thumbnail">
                    <button
                        type="button"
                        class="strip-tile strip-tile-button"
                        :aria-label="`Preview ${entry.item.name}`"
                        @click="openPreview(entry.item.id)"
                    >
                        <img :src="entry.thumbnail" :alt="entry.item.name" @error="onThumbnailError(entry.item.src)" />
                    </button>
                    <span class="strip-name">{{ entry.item.name }}</span>
                </template>
                <button
                    v-else-if="entry.request"
                    type="button"
                    class="strip-file strip-file-button is-link"
                    :aria-label="`Open ${entry.item.name} in the Artifacts tab`"
                    @click="openArtifact(entry.request)"
                >
                    <span class="strip-tile strip-tile-icon">
                        <wa-icon :name="entry.icon"></wa-icon>
                    </span>
                    <span class="strip-name">{{ entry.item.name }}</span>
                </button>
                <span v-else class="strip-file">
                    <span class="strip-tile strip-tile-icon">
                        <wa-icon :name="entry.icon"></wa-icon>
                    </span>
                    <span class="strip-name">{{ entry.item.name }}</span>
                </span>
            </div>
            <AppTooltip :for="entry.anchorId">
                <span class="strip-tooltip-name">{{ entry.item.name }}</span>
                <span v-if="entry.tile?.details" class="strip-tooltip-details">{{ entry.tile.details }}</span>
            </AppTooltip>

            <!-- Editable tile: the upload state on the tile, then its actions. -->
            <template v-if="entry.tile">
                <wa-progress-bar
                    v-if="entry.tile.uploading"
                    class="strip-tile-progress"
                    :value="entry.tile.progress"
                    :label="`Upload of ${entry.item.name}`"
                ></wa-progress-bar>
                <span v-if="entry.tile.error" class="strip-tile-badge">
                    <wa-icon name="exclamation" :label="entry.tile.statusText || 'Upload failed'"></wa-icon>
                </span>
                <template v-if="entry.tile.retryable">
                    <button
                        :id="entry.retryId"
                        type="button"
                        class="strip-tile-action strip-tile-retry"
                        :aria-label="`Retry the upload of ${entry.item.name}`"
                        @click="retry(entry.item.id)"
                    >
                        <wa-icon name="rotate-right"></wa-icon>
                    </button>
                    <AppTooltip :for="entry.retryId">Retry</AppTooltip>
                </template>
                <button
                    :id="entry.removeId"
                    :ref="el => setRemoveButton(entry.item.id, el)"
                    type="button"
                    class="strip-tile-action strip-tile-remove"
                    :aria-label="`Remove ${entry.item.name}`"
                    @click="remove(entry.item.id)"
                >
                    <wa-icon name="xmark"></wa-icon>
                </button>
                <AppTooltip :for="entry.removeId">Remove</AppTooltip>
            </template>
        </li>
    </ol>
</template>

<style scoped>
/* A long file name wraps inside the tooltip instead of running past the viewport. */
.strip-tooltip-name,
.strip-tooltip-details {
    display: block;
    max-width: min(24rem, 80vw);
    white-space: normal;
    overflow-wrap: anywhere;
}

/* Editable tiles: size and upload state under the name. */
.strip-tooltip-details {
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

/* One tile size for every attachment. The colours come from custom properties so the
   container decides: a user bubble that is a filled surface (light theme) sets them
   in SessionItem.vue; anywhere else the neutral defaults below apply. */
/* One row, whatever the number of attachments: when they do not all fit, the row scrolls
   sideways (swipe on touch, scrollbar / trackpad / Shift+wheel on desktop) instead of
   wrapping into a grid that grows with each file. The scrollbar look is the app's own
   (styles/scrollbars.css). The block padding leaves room for the focus ring and the hover
   border, which an overflow container would otherwise clip. */
.attachment-strip {
    --strip-tile-size: 96px;
    list-style: none;
    margin: 0;
    padding: 4px 4px 6px;
    display: flex;
    flex-wrap: nowrap;
    align-items: flex-start;
    gap: var(--wa-space-s);
    min-width: 0;
    overflow-x: auto;
    overflow-y: hidden;
    overscroll-behavior-x: contain;
}

/* Smaller tiles when the conversation pane is narrow. It is the pane that counts, not the
   device: the sidebar or the layout can leave a wide screen with a narrow pane, and the
   other way round. The container is `.session-items-list` (SessionItem.vue). */
@container session-items-list (width <= 30rem) {
    .attachment-strip {
        --strip-tile-size: 72px;
    }
}

/* Same in a host outside the conversation pane that declares the `attachment-strip-host`
   container: the composer's attachments popover (MessageInput.vue), narrow on mobile. */
@container attachment-strip-host (width <= 20rem) {
    .attachment-strip {
        --strip-tile-size: 72px;
    }
}

.attachment-strip:not(:last-child) {
    margin-block-end: var(--wa-space-s);
}

/* `margin: 0`: the Web Awesome native styles indent every `li` (1.125em). */
.attachment-strip-item {
    position: relative;
    margin: 0;
    width: var(--strip-tile-size);
    min-width: 0;
    flex: none;
}

.strip-entry {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-3xs);
    width: 100%;
    min-width: 0;
}

/* Native <button> resets: WA native styles force a height on buttons. */
.strip-tile-button,
.strip-file-button {
    box-shadow: none;
    min-height: 0;
    height: auto;
    padding: 0;
    border: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
}

.strip-file {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-3xs);
    width: 100%;
    min-width: 0;
}

.strip-tile {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
    width: var(--strip-tile-size);
    height: var(--strip-tile-size);
    box-sizing: border-box;
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--strip-tile-border, var(--wa-color-surface-border));
    background: var(--strip-tile-bg, var(--wa-color-surface-lowered));
    overflow: hidden;
}

/* Hover border and focus ring: the accent of the container (`--strip-tile-accent`, set on a
   user bubble, where the brand colour would not stand out), else the app's brand and focus colours. */
.strip-tile-button:hover,
.strip-file-button:hover .strip-tile {
    border-color: var(--strip-tile-accent, var(--wa-color-brand-border-loud));
}

.strip-tile-button:focus-visible,
.strip-file-button:focus-visible {
    outline: 2px solid var(--strip-tile-accent, var(--wa-color-focus));
    outline-offset: 2px;
    border-radius: var(--wa-border-radius-s);
}

.strip-tile img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}

.strip-tile-icon wa-icon {
    font-size: 2.25rem;
    color: var(--strip-icon-color, var(--wa-color-text-quiet));
}

@container session-items-list (width <= 30rem) {
    .strip-tile-icon wa-icon {
        font-size: 1.75rem;
    }
}

@container attachment-strip-host (width <= 20rem) {
    .strip-tile-icon wa-icon {
        font-size: 1.75rem;
    }
}

/* The name sits under its tile, cut to the tile width (the full name is in the tooltip). */
.strip-name {
    display: block;
    width: 100%;
    min-width: 0;
    box-sizing: border-box;
    text-align: center;
    color: inherit;
    font-size: var(--wa-font-size-s);
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}

/* A file that opens in the Artifacts tab reads as a link, like a link in the message text. */
.is-link .strip-name {
    color: var(--strip-link-color, var(--wa-color-text-link));
    text-decoration: underline;
    text-underline-offset: 2px;
}

/* --- Editable tiles (the composer) -------------------------------------------------
   The overlays sit on the tile, which is the top square of the item: the item is as
   wide as the tile, so its top corners are the tile's. */

/* While uploading, and after an error, the content of the tile fades behind its state. */
.is-uploading .strip-tile > img,
.is-uploading .strip-tile-icon wa-icon,
.is-error .strip-tile > img,
.is-error .strip-tile-icon wa-icon {
    opacity: 0.55;
}

/* Upload progress along the bottom edge of the tile. */
.strip-tile-progress {
    --track-height: 4px;
    position: absolute;
    inset-inline: 6px;
    top: calc(var(--strip-tile-size) - 10px);
    pointer-events: none;
}

/* Failed or missing upload: danger border and badge (the status is in the tooltip). */
.attachment-strip-item.is-error .strip-tile {
    border-color: var(--wa-color-danger-border-loud);
    box-shadow: inset 0 0 0 1px var(--wa-color-danger-border-loud);
}

.attachment-strip-item.is-error .strip-name {
    color: var(--wa-color-danger-on-quiet);
}

.strip-tile-badge {
    position: absolute;
    top: 4px;
    inset-inline-start: 4px;
    display: flex;
    align-items: center;
    justify-content: center;
    width: 1.25rem;
    height: 1.25rem;
    border-radius: 50%;
    background: var(--wa-color-danger-fill-loud);
    color: var(--wa-color-danger-on-loud);
    font-size: 0.7rem;
    pointer-events: none;
}

/* Round buttons on the tile: Remove in the top corner, Retry in the middle. */
.strip-tile-action {
    position: absolute;
    display: flex;
    align-items: center;
    justify-content: center;
    box-sizing: border-box;
    width: 1.5rem;
    height: 1.5rem;
    min-height: 0;
    margin: 0;
    padding: 0;
    border-radius: 50%;
    border: 1px solid var(--wa-color-surface-border);
    background: var(--wa-color-surface-raised);
    box-shadow: none;
    color: var(--wa-color-text-normal);
    font: inherit;
    font-size: 0.75rem;
    cursor: pointer;
}

.strip-tile-action:focus-visible {
    outline: 2px solid var(--strip-tile-accent, var(--wa-color-focus));
    outline-offset: 1px;
}

.strip-tile-remove {
    top: 4px;
    inset-inline-end: 4px;
}

.strip-tile-remove:hover {
    border-color: var(--wa-color-danger-fill-loud);
    background: var(--wa-color-danger-fill-loud);
    color: var(--wa-color-danger-on-loud);
}

.strip-tile-retry {
    top: calc(var(--strip-tile-size) / 2);
    left: 50%;
    transform: translate(-50%, -50%);
    width: 2rem;
    height: 2rem;
    font-size: 0.9rem;
}

.strip-tile-retry:hover {
    border-color: var(--wa-color-brand-fill-loud);
    background: var(--wa-color-brand-fill-loud);
    color: var(--wa-color-brand-on-loud);
}
</style>
