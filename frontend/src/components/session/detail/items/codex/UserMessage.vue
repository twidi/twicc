<script setup>
import { computed } from 'vue'
import { FILE_TYPES } from '../../../../../utils/fileUtils'
import MediaThumbnailGroup from '../../../../media/MediaThumbnailGroup.vue'
import AttachmentStrip from '../../../../media/AttachmentStrip.vue'
import TextContent from '../TextContent.vue'

const props = defineProps({
    // The joined text of a canonical ``UserMessage`` item's ``text`` entries
    // (``canonical.js``); image attachments come through the sibling
    // ``images`` prop instead.
    text: {
        type: String,
        required: true
    },
    // The ``data:`` URLs of the item's ``image`` content entries (one per
    // attachment). Rendered as a thumbnail strip above the text — matching
    // the visual order of the prompt that was sent (images first, text after).
    images: {
        type: Array,
        default: () => []
    },
    // The ordered attachment strip (utils/attachmentStrip.js) when the
    // message carries an attachment manifest; it replaces ``images``. Null
    // keeps the legacy thumbnail rendering.
    attachments: {
        type: Array,
        default: null
    }
})

const emit = defineEmits(['open-artifact'])

const mediaItems = computed(() =>
    props.images.map(src => ({ type: FILE_TYPES.IMAGE, src }))
)
</script>

<template>
    <AttachmentStrip
        v-if="attachments?.length"
        :items="attachments"
        @open-artifact="request => emit('open-artifact', request)"
    />
    <MediaThumbnailGroup v-else-if="mediaItems.length > 0" :items="mediaItems" />
    <TextContent v-if="text" :text="text" role="user" />
</template>
