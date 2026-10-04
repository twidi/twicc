<script setup>
import { computed, ref, nextTick, onMounted } from 'vue'
import { useDataStore } from '../../../../stores/data'
import { useDetailsClosing } from '../../../../composables/useDetailsClosing'
import { useAttachmentStripContext } from '../../../../composables/useAttachmentStripContext'
import { queuedAttachmentDisplay } from '../../../../utils/attachmentStrip'
import AttachmentStrip from '../../../media/AttachmentStrip.vue'
import JsonHumanView from '../../../json/JsonHumanView.vue'

const dataStore = useDataStore()

const props = defineProps({
    type: {
        type: String,
        default: 'unknown'
    },
    // Optional sub-discriminator displayed in parentheses after the type
    // (e.g. Codex's payload.type for response_item / event_msg lines).
    subType: {
        type: String,
        default: null
    },
    data: {
        type: Object,
        default: null
    },
    sessionId: {
        type: String,
        required: true
    },
    detailKey: {
        type: String,
        required: true
    }
})

const detailsRef = ref(null)

// A record carrying an extracted attachment manifest (a mid-turn prompt
// recorded as an attachment record, spec 2026-10-03 §10.1) shows its ordered
// strip and cleaned text instead of the raw JSON. `queuedAttachmentDisplay`
// decides; every other record keeps the generic JSON view.
const { share: attachmentShareMode, openArtifact } = useAttachmentStripContext(() => props.sessionId)
const attachmentDisplay = computed(() => queuedAttachmentDisplay(props.data, { share: attachmentShareMode }))

// Lazy rendering: content is only mounted when wa-details is open.
// Initialized from the store to restore state across virtual scroller mount/unmount cycles.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, props.detailKey))

// Skip the open animation when mounting already-open (virtual scroller restoration,
// session switch). Same pattern as CompactSummary.
const instantOpen = ref(isOpen.value)

onMounted(() => {
    if (instantOpen.value) {
        nextTick(() => { instantOpen.value = false })
    }
})

// Keeps the body rendered while the card folds (utils/detailsMotion.js).
const { isClosing, markClosing, clearClosing } = useDetailsClosing()

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
    <wa-details ref="detailsRef" :open="isOpen" :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null" class="item-details unknown-entry" icon-placement="start" @wa-show="onShow" @wa-hide="onHide" @wa-after-hide="onAfterHide">
        <span slot="summary" class="items-details-summary">
            <strong class="items-details-summary-name">Unhandled event</strong>
            <span class="items-details-summary-separator"> — </span>
            <span class="items-details-summary-description">{{ type }}<template v-if="subType"> ({{ subType }})</template></span>
        </span>
        <template v-if="isOpen || isClosing()">
            <div v-if="attachmentDisplay" class="unknown-data attachment-display">
                <AttachmentStrip :items="attachmentDisplay.items" @open-artifact="openArtifact" />
                <div v-if="attachmentDisplay.text" class="attachment-display-text">{{ attachmentDisplay.text }}</div>
            </div>
            <div v-else-if="data" class="unknown-data">
                <JsonHumanView
                    :value="data"
                />
            </div>
            <div v-else class="unknown-no-data">
                No data available
            </div>
        </template>
    </wa-details>
</template>

<style scoped>
.unknown-data {
    /* Side spacing: the open details' (moved from its content part, motion.css). */
    padding: var(--wa-space-xs) var(--spacing, 0);
    overflow-x: auto;
}

.attachment-display-text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}

.unknown-no-data {
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) var(--spacing, 0);
}
</style>
