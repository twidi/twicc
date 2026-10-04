<script setup>
// AttachmentStrip.vue - The ordered attachments of a user message in the
// history (spec 2026-10-03 §10.2): image thumbnails and file chips, in the
// order the user attached them. Items come from `buildAttachmentStrip` /
// `optimisticAttachmentStrip` (utils/attachmentStrip.js).
//
// A file chip that can open emits `open-artifact` with `{owner, relativePath}`;
// the parent opens it through the existing Artifacts tab navigation. In the
// share viewer (explicit share mode) no chip links, whatever the items say.
import { computed, inject, ref } from 'vue'
import { openMediaPreview } from '../../composables/useMediaPreview'
import { ATTACHMENT_SHARE_MODE, attachmentKindIcon, stripItemArtifactRequest } from '../../utils/attachmentStrip'

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

const entries = computed(() => props.items.map(item => {
    const thumbnail = item.src && !brokenSources.value.has(item.src) ? item.src : null
    return {
        item,
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
        <li v-for="entry in entries" :key="entry.item.id" class="attachment-strip-item">
            <button
                v-if="entry.thumbnail"
                type="button"
                class="strip-thumbnail"
                :title="entry.item.name"
                :aria-label="`Preview ${entry.item.name}`"
                @click="openPreview(entry.item.id)"
            >
                <img :src="entry.thumbnail" :alt="entry.item.name" @error="onThumbnailError(entry.item.src)" />
            </button>
            <button
                v-else-if="entry.request"
                type="button"
                class="strip-chip is-link"
                :title="`Open ${entry.item.name} in the Artifacts tab`"
                @click="openArtifact(entry.request)"
            >
                <wa-icon :name="entry.icon"></wa-icon>
                <span class="strip-chip-name">{{ entry.item.name }}</span>
            </button>
            <span v-else class="strip-chip" :title="entry.item.name">
                <wa-icon :name="entry.icon"></wa-icon>
                <span class="strip-chip-name">{{ entry.item.name }}</span>
            </span>
        </li>
    </ol>
</template>

<style scoped>
.attachment-strip {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--wa-space-xs);
    min-width: 0;
}

.attachment-strip:not(:last-child) {
    margin-block-end: var(--wa-space-s);
}

.attachment-strip-item {
    display: flex;
    min-width: 0;
    max-width: 100%;
}

/* Native <button> resets: WA native styles force a height on buttons. */
.strip-thumbnail,
.strip-chip {
    box-shadow: none;
    min-height: 0;
    font: inherit;
}

.strip-thumbnail {
    display: block;
    width: 96px;
    height: 96px;
    padding: 0;
    border-radius: var(--wa-border-radius-s);
    border: 1px solid var(--wa-color-border-neutral-tertiary);
    background: var(--wa-color-surface-secondary);
    overflow: hidden;
    cursor: pointer;
}

.strip-thumbnail:hover {
    border-color: var(--wa-color-border-primary);
}

.strip-thumbnail img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
}

.strip-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--wa-space-2xs);
    min-width: 0;
    max-width: 100%;
    height: auto;
    padding: var(--wa-space-3xs) var(--wa-space-xs);
    border-radius: var(--wa-border-radius-pill);
    border: 1px solid var(--wa-color-border-neutral-tertiary);
    background: var(--wa-color-surface-secondary);
    color: var(--wa-color-text-normal);
    font-size: var(--wa-font-size-s);
}

.strip-chip wa-icon {
    flex-shrink: 0;
    color: var(--wa-color-text-quiet);
}

.strip-chip.is-link {
    cursor: pointer;
}

.strip-chip.is-link:hover {
    border-color: var(--wa-color-border-primary);
}

.strip-chip.is-link .strip-chip-name {
    text-decoration: underline;
    text-decoration-color: var(--wa-color-border-neutral-tertiary);
    text-underline-offset: 2px;
}

.strip-chip-name {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
</style>
