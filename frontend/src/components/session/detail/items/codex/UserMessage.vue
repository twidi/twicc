<script setup>
import { computed } from 'vue'
import { nativeMediaStripItems } from '../../../../../utils/attachmentStrip'
import AttachmentStrip from '../../../../media/AttachmentStrip.vue'
import TextContent from '../TextContent.vue'

const props = defineProps({
    // The joined text of a canonical ``UserMessage`` item's ``text`` entries
    // (``canonical.js``); attachments come through the sibling ``content``
    // or ``attachments`` prop instead.
    text: {
        type: String,
        required: true
    },
    // The item's content entries, for a message without attachment manifest:
    // its ``image`` / ``local_image`` entries are rendered as an attachment
    // strip above the text — matching the visual order of the prompt that
    // was sent (attachments first, text after).
    content: {
        type: Array,
        default: () => []
    },
    // The ordered attachment strip (utils/attachmentStrip.js) when the
    // message carries an attachment manifest; it replaces ``content``. Null
    // keeps the strip built from ``content``.
    attachments: {
        type: Array,
        default: null
    }
})

const emit = defineEmits(['open-artifact'])

// The strip of the message: its manifest strip, else one tile per native
// image entry (file or default names, no artifact link).
const stripItems = computed(() => {
    if (props.attachments?.length) return props.attachments
    return nativeMediaStripItems(props.content)
})
</script>

<template>
    <AttachmentStrip
        v-if="stripItems.length"
        :items="stripItems"
        @open-artifact="request => emit('open-artifact', request)"
    />
    <TextContent v-if="text" :text="text" role="user" />
</template>
