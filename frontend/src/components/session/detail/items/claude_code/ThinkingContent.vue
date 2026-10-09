<script setup>
import { ref, computed, nextTick, onMounted, inject } from 'vue'
import { STREAMING_BLOCK_CONTEXT } from '../../../../../composables/streamPublicationKeys.js'
import { useStreamingPublication } from '../../../../../composables/useStreamingPublication.js'
import { useDataStore } from '../../../../../stores/data'
import { useDetailsClosing } from '../../../../../composables/useDetailsClosing'
import { isBlankMarkdown } from '../../../../../utils/markdown.js'
import { extractThinkingTitle } from '../../../../../utils/thinkingTitle.js'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'

const dataStore = useDataStore()

const props = defineProps({
    thinking: {
        type: String,
        required: true
    },
    sessionId: {
        type: String,
        required: true
    },
    detailKey: {
        type: String,
        required: true
    },
    streaming: {
        type: Boolean,
        default: false
    }
})

const detailsRef = ref(null)

// Empty thinking (nothing, whitespace, or only HTML comments — which the
// renderer hides) has nothing to show; we surface a placeholder instead of an
// empty expandable body. While streaming we keep rendering the (growing) source
// so the placeholder never flashes before the first tokens land.
const hasContent = computed(() => !isBlankMarkdown(props.thinking))

// Summary description: the first line when it is a heading or a bold span.
// Reads only that line, so it stays cheap while the thinking streams in.
const title = computed(() => extractThinkingTitle(props.thinking))

// Lazy rendering: content is only mounted when wa-details is open.
// Initialized from the store to restore state across virtual scroller mount/unmount cycles.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, props.detailKey))

// Skip open animation when mounting already-open (virtual scroller restoration,
// or state transferred from a streaming block). Same pattern as ToolUseContent.
const instantOpen = ref(isOpen.value)

// Keeps the body rendered while the card folds (utils/detailsMotion.js).
const { isClosing, markClosing, clearClosing } = useDetailsClosing()
const publicationIdentity = inject(STREAMING_BLOCK_CONTEXT, null)
useStreamingPublication({ identity: publicationIdentity, bodyActive: () => isOpen.value || isClosing() })

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

function onShow(event) {
    if (event.target !== event.currentTarget) return
    isOpen.value = true
    clearClosing()
    dataStore.setDetailOpen(props.sessionId, props.detailKey, true)
}

function onHide(event) {
    if (event.target !== event.currentTarget) return
    markClosing()
    isOpen.value = false
    dataStore.setDetailOpen(props.sessionId, props.detailKey, false)
}

function onAfterHide(event) {
    if (event.target !== event.currentTarget) return
    clearClosing()
}
</script>

<template>
    <wa-details ref="detailsRef" :open="isOpen" :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null" class="item-details thinking-content" icon-placement="start" @wa-show="onShow" @wa-hide="onHide" @wa-after-hide="onAfterHide">
        <div slot="summary" class="items-details-summary">
            <div class="items-details-summary-left">
                <strong class="items-details-summary-name">Thinking</strong>
                <template v-if="title">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description">{{ title }}</span>
                </template>
            </div>
            <div v-if="streaming" class="items-details-summary-right">
                <wa-spinner></wa-spinner>
            </div>
        </div>
        <div v-if="isOpen || isClosing()" class="thinking-body">
            <MarkdownContent v-if="streaming || hasContent" :source="thinking" />
            <p v-else class="thinking-placeholder">No thinking content was provided</p>
        </div>
    </wa-details>
</template>

<style scoped>
.thinking-body {
    word-break: break-word;
}

.thinking-placeholder {
    margin: 0;
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
</style>
