<script setup>
import { computed } from 'vue'
import { FILE_TYPES } from '../../../../../utils/fileUtils'
import MediaThumbnailGroup from '../../../../media/MediaThumbnailGroup.vue'
import TextContent from '../TextContent.vue'

const props = defineProps({
    // The flat string body of canonical ``UserMessage`` item (Codex doesn't
    // model the input as a content-block array at this layer — image
    // attachments come through the sibling ``images`` prop instead).
    text: {
        type: String,
        required: true
    },
    // Codex CLI persists user-attached images as full ``data:`` URLs in
    // ``payload.images`` (one per attachment). They sit alongside the
    // flat ``message`` text rather than being interleaved with it, so we
    // render them as a thumbnail strip above the text — matching the
    // visual order of the prompt that was sent (images first, text after).
    images: {
        type: Array,
        default: () => []
    }
})

const mediaItems = computed(() =>
    props.images.map(src => ({ type: FILE_TYPES.IMAGE, src }))
)
</script>

<template>
    <MediaThumbnailGroup v-if="mediaItems.length > 0" :items="mediaItems" />
    <TextContent v-if="text" :text="text" role="user" />
</template>
