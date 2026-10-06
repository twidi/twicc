<script setup>
// AttachmentStrip.vue - The ordered attachments of a user message in the
// history (spec 2026-10-03 §10.2): image thumbnails and file chips, in the
// order the user attached them. Items come from `buildAttachmentStrip` /
// `optimisticAttachmentStrip` (utils/attachmentStrip.js).
//
// A file chip that can open emits `open-artifact` with `{owner, relativePath}`;
// the parent opens it through the existing Artifacts tab navigation. In the
// share viewer (explicit share mode) no chip links, whatever the items say.
import { computed, inject, ref, useId } from 'vue'
import { openMediaPreview } from '../../composables/useMediaPreview'
import { ATTACHMENT_SHARE_MODE, attachmentKindIcon, stripItemArtifactRequest } from '../../utils/attachmentStrip'
import AppTooltip from '../ui/AppTooltip.vue'

const props = defineProps({
    items: {
        type: Array,
        required: true
    }
})

const emit = defineEmits(['open-artifact'])

const share = inject(ATTACHMENT_SHARE_MODE, false) === true

// Thumbnails whose image failed to load fall back to a chip.
const brokenSources = ref(new Set())
function onThumbnailError(src) {
    if (!src || brokenSources.value.has(src)) return
    brokenSources.value = new Set([...brokenSources.value, src])
}

// Prefix of the anchor ids the tooltips point at (one per attachment of this strip).
const stripId = useId()

const entries = computed(() => props.items.map((item, index) => {
    const thumbnail = item.src && !brokenSources.value.has(item.src) ? item.src : null
    return {
        item,
        anchorId: `attachment-${stripId}-${index}`,
        thumbnail,
        request: share ? null : stripItemArtifactRequest(item),
        icon: attachmentKindIcon(item.kind),
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
</script>

<template>
    <ol v-if="entries.length" class="attachment-strip">
        <li v-for="entry in entries" :id="entry.anchorId" :key="entry.item.id" class="attachment-strip-item">
            <!-- Every attachment is the same square tile with its name under it, so a
                 mix of images and files lines up: the image itself when there is one,
                 else the icon of its kind. The name is cut to the tile width: the tooltip
                 gives it whole. -->
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
            <AppTooltip :for="entry.anchorId">{{ entry.item.name }}</AppTooltip>
        </li>
    </ol>
</template>

<style scoped>
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

.attachment-strip:not(:last-child) {
    margin-block-end: var(--wa-space-s);
}

.attachment-strip-item {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--wa-space-3xs);
    width: var(--strip-tile-size);
    min-width: 0;
    flex: none;
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
    border: 1px solid var(--strip-tile-border, var(--wa-color-border-neutral-tertiary));
    background: var(--strip-tile-bg, var(--wa-color-surface-secondary));
    overflow: hidden;
}

.strip-tile-button:hover,
.strip-file-button:hover .strip-tile {
    border-color: var(--wa-color-border-primary);
}

.strip-tile-button:focus-visible,
.strip-file-button:focus-visible {
    outline: 2px solid var(--wa-color-border-primary);
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
</style>
