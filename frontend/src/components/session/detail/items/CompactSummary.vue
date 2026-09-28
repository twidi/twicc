<script setup>
/**
 * Shared "Session compacted" item, used by both Claude Code and Codex.
 *
 * Claude Code emits a ``summary`` line with the human-readable summary
 * under ``message.content`` — when present, we render it as Markdown.
 * Codex emits a ``compacted`` line whose actual summary is encrypted
 * (``replacement_history[-1].encrypted_content``) and unreadable from
 * disk; in that case ``content`` is empty and we show a placeholder
 * scoped to the provider (``Context summary not provided by Codex``).
 *
 * The wa-details open/close state is persisted per-line via the
 * ``dataStore.detailOpen`` map so the virtual scroller can unmount /
 * remount the row without losing the user's expand state.
 */
import { ref, nextTick, onMounted, computed } from 'vue'
import { useDataStore } from '../../../../stores/data'
import { useDetailsClosing } from '../../../../composables/useDetailsClosing'
import { getProviderLabel } from '../../../../providers'
import MarkdownContent from '../../../ui/MarkdownContent.vue'

const dataStore = useDataStore()

const props = defineProps({
    content: {
        type: String,
        default: '',
    },
    provider: {
        type: String,
        required: true,
    },
    sessionId: {
        type: String,
        required: true,
    },
    detailKey: {
        type: String,
        required: true,
    },
})

const hasContent = computed(() => typeof props.content === 'string' && props.content.trim().length > 0)
const placeholder = computed(() => `Context summary not provided by ${getProviderLabel(props.provider)}`)

const detailsRef = ref(null)

// Lazy rendering: content is only mounted when wa-details is open.
// Initialized from the store to restore state across virtual scroller mount/unmount cycles.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, props.detailKey))

// Skip open animation when mounting already-open (virtual scroller restoration).
// Same pattern as ThinkingContent.
const instantOpen = ref(isOpen.value)

// Keeps the body rendered while the card folds (utils/detailsMotion.js).
const { isClosing, markClosing, clearClosing } = useDetailsClosing()

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

function onShow() {
    clearClosing()
    isOpen.value = true
    dataStore.setDetailOpen(props.sessionId, props.detailKey, true)
}

function onHide() {
    markClosing()
    isOpen.value = false
    dataStore.setDetailOpen(props.sessionId, props.detailKey, false)
}

function onAfterHide() {
    clearClosing()
}
</script>

<template>
    <wa-details ref="detailsRef" :open="isOpen" :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null" class="item-details compact-summary-content" icon-placement="start" @wa-show="onShow" @wa-hide="onHide" @wa-after-hide="onAfterHide">
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Session compacted</strong>
        </span>
        <div v-if="isOpen || isClosing()" class="compact-summary-body">
            <MarkdownContent v-if="hasContent" :source="content" />
            <p v-else class="compact-summary-placeholder">{{ placeholder }}</p>
        </div>
    </wa-details>
</template>

<style scoped>

.compact-summary-body {
    word-break: break-word;
}

.compact-summary-placeholder {
    margin: 0;
    color: var(--wa-color-text-quiet);
    font-style: italic;
}
</style>
