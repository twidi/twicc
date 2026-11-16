<script setup>
/**
 * One finished Gemini tool call, as a collapsible tool card.
 *
 * Unlike Claude and Codex, a Gemini call carries its arguments and its
 * result in the same record (``toolCalls[]`` of an assistant record), so
 * this card renders everything locally — no tool-result fetch:
 *  - header: display name, target summary, error / cancelled icon;
 *  - body: the file diff of an edit (``resultDisplay.fileDiff``), else the
 *    arguments; an error callout; the output in a nested "Result" card.
 */
import { computed, nextTick, onMounted, ref, useId } from 'vue'
import { useDataStore } from '../../../../../stores/data'
import { useDetailsClosing } from '../../../../../composables/useDetailsClosing'
import { geminiToolHelpers } from '../../../../../providers/gemini/toolHelpers'
import { toolCallFileDiff, toolCallOutput } from '../../../../../providers/gemini/records'
import JsonHumanView from '../../../../json/JsonHumanView.vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import ToolDiffViewer from '../claude_code/ToolDiffViewer.vue'

const props = defineProps({
    call: { type: Object, required: true },
    sessionId: { type: String, required: true },
    lineNum: { type: Number, required: true },
    // Position of the call in its record, for the persisted open state of a
    // call without id.
    index: { type: Number, default: 0 },
})

const dataStore = useDataStore()

const detailKey = computed(() => `gemini-tool:${props.call.id || `${props.lineNum}:${props.index}`}`)
const resultKey = computed(() => `result:${detailKey.value}`)

const name = computed(() => props.call.displayName || props.call.name || '')
const args = computed(() => (props.call.args && typeof props.call.args === 'object' ? props.call.args : {}))
const hasArgs = computed(() => Object.keys(args.value).length > 0)

const sessionBaseDir = computed(() => {
    const session = dataStore.getSession(props.sessionId)
    return session?.git_directory || session?.cwd || null
})
const summary = computed(() => geminiToolHelpers.getInlineSummary(props.call.name, args.value, sessionBaseDir.value))

const isError = computed(() => props.call.status === 'error')
const isCancelled = computed(() => props.call.status === 'cancelled')
const output = computed(() => toolCallOutput(props.call))
const diff = computed(() => toolCallFileDiff(props.call))
const diffMode = computed(() => (diff.value?.originalContent ? 'diff' : 'full'))
const statusIconId = useId()

// Lazy rendering + persisted open state (virtual scroller mount/unmount),
// same pattern as ToolUseContent / Reasoning.
const isOpen = ref(dataStore.isDetailOpen(props.sessionId, detailKey.value))
const isResultOpen = ref(dataStore.isDetailOpen(props.sessionId, resultKey.value))
const instantOpen = ref(isOpen.value)
const { isClosing, markClosing, clearClosing } = useDetailsClosing()

onMounted(() => {
    if (instantOpen.value) nextTick(() => { instantOpen.value = false })
})

function onShow() {
    clearClosing()
    isOpen.value = true
    dataStore.setDetailOpen(props.sessionId, detailKey.value, true)
}

function onHide() {
    markClosing()
    isOpen.value = false
    dataStore.setDetailOpen(props.sessionId, detailKey.value, false)
}

function onAfterHide() {
    clearClosing()
}

function onResultShow() {
    isResultOpen.value = true
    dataStore.setDetailOpen(props.sessionId, resultKey.value, true)
}

function onResultHide() {
    isResultOpen.value = false
    dataStore.setDetailOpen(props.sessionId, resultKey.value, false)
}
</script>

<template>
    <wa-details
        :open="isOpen"
        :style="instantOpen ? { '--show-duration': '0ms', '--hide-duration': '0ms' } : null"
        class="item-details tool-use gemini-tool-call"
        icon-placement="start"
        @wa-show.self="onShow"
        @wa-hide.self="onHide"
        @wa-after-hide.self="onAfterHide"
    >
        <div slot="summary" class="items-details-summary">
            <div class="items-details-summary-left">
                <strong class="items-details-summary-name">{{ name }}</strong>
                <template v-if="summary">
                    <span class="items-details-summary-separator"> — </span>
                    <span class="items-details-summary-description">{{ summary }}</span>
                </template>
            </div>
            <div v-if="isError || isCancelled" class="items-details-summary-right">
                <wa-icon
                    :id="statusIconId"
                    :name="isError ? 'xmark' : 'ban'"
                    class="tool-status-icon"
                    :class="{ 'is-error': isError }"
                ></wa-icon>
                <AppTooltip :for="statusIconId">{{ isError ? $t('session.tools.gemini.failed') : $t('session.tools.gemini.cancelled') }}</AppTooltip>
            </div>
        </div>
        <template v-if="isOpen || isClosing()">
            <div v-if="diff" class="tool-diff">
                <ToolDiffViewer
                    :mode="diffMode"
                    :original="diff.originalContent ?? ''"
                    :modified="diff.newContent ?? ''"
                    :file-path="diff.filePath || diff.fileName"
                />
            </div>
            <div v-else-if="hasArgs" class="tool-input">
                <JsonHumanView :value="args" />
            </div>
            <div v-else class="tool-no-input">{{ $t('session.tools.noInput') }}</div>
            <wa-callout v-if="output.error" variant="danger" class="tool-error-message">
                <wa-icon slot="icon" name="circle-exclamation"></wa-icon>
                <pre class="tool-output">{{ output.error }}</pre>
            </wa-callout>
            <wa-details
                v-if="output.output"
                :open="isResultOpen"
                class="tool-result"
                @wa-show.self="onResultShow"
                @wa-hide.self="onResultHide"
            >
                <span slot="summary">{{ $t('session.tools.resultLabel') }}</span>
                <div v-if="isResultOpen" class="tool-result-content">
                    <MarkdownContent v-if="call.renderOutputAsMarkdown" :source="output.output" :show-toolbar="false" />
                    <pre v-else class="tool-output">{{ output.output }}</pre>
                </div>
            </wa-details>
        </template>
    </wa-details>
</template>

<style scoped>
wa-details::part(content) {
    padding-top: 0;
}

.tool-status-icon {
    color: var(--wa-color-text-quiet);
    font-size: 1.2em;

    &.is-error {
        color: var(--wa-color-danger-50);
    }
}

.tool-input {
    /* Side spacing: the open details' (moved from its content part, motion.css). */
    padding: 0 var(--spacing, 0);
    overflow-x: auto;
}

.tool-no-input {
    color: var(--wa-color-text-quiet);
    font-style: italic;
    padding: var(--wa-space-xs) var(--spacing, 0);
}

.tool-diff {
    height: 23rem;
    padding: 0 var(--spacing, 0);
}

.tool-error-message {
    margin-top: var(--wa-space-xs);
}

.tool-result {
    margin-top: calc(var(--card-spacing, var(--wa-space-l)) / 2);
}

.tool-result-content {
    padding: var(--wa-space-xs) var(--spacing, 0) 0;
    /* Same cap as the other tool results: a long output scrolls inside. */
    max-height: 20rem;
    overflow: auto;
}

.tool-output {
    margin: 0;
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-xs);
    white-space: pre-wrap;
    word-break: break-word;
}

/* Like the other tool cards: no markdown raw / copy toolbar inside. */
.tool-use :deep(.markdown-toolbar) {
    display: none;
}
</style>
