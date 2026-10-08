<script setup>
import { computed } from 'vue'
import MarkdownContent from '../../../ui/MarkdownContent.vue'
import { displayInlineText } from '../../../../inline-artifacts/rendering.js'
import { LEADING_SLASH_COMMAND_RE } from '../../../../utils/markdown'

const props = defineProps({
    inlineContext: { type: Object, default: null },
    text: {
        type: String,
        required: true
    },
    // Also rendered as an attribute on the root element (styling hook).
    role: {
        type: String,
        default: null
    }
})

const display = computed(() => displayInlineText(props.text, props.inlineContext))
const displayText = computed(() => display.value.source)

// User messages starting with a slash command (a real command converted by
// commandToText, or free text typed with a leading /word) get the command
// rendered as a tag.
const tagSlashCommand = computed(() =>
    props.role === 'user' && LEADING_SLASH_COMMAND_RE.test(displayText.value)
)
</script>

<template>
    <div class="text-content" :role="role">
        <MarkdownContent :source="displayText" :inline-context="display.inlineContext" :tag-slash-command="tagSlashCommand" />
    </div>
</template>

<style scoped>
.text-content {
    word-break: break-word;
    font-family: var(--wa-font-sans);
}

/* In user messages, code blocks should wrap instead of scrolling horizontally,
   so that the full content is visible (and selectable for text comments). */
.text-content[role="user"] :deep(.markdown-body pre) {
    &, & code {
        white-space: pre-wrap;
        word-wrap: break-word;
    }
}

/* Leading /command of a user message, rendered as a tag (see the
   slash_command_tag rule in utils/markdown.js). The tag look itself lives in
   MarkdownContent.vue, shared with `::` line blocks; only the spacing is
   specific here: the source keeps the space after the command (copy fidelity),
   so a sliver of extra margin next to the chip's padding is enough. */
.text-content :deep(.slash-command-tag) {
    margin-right: 0.1em;
}
</style>
