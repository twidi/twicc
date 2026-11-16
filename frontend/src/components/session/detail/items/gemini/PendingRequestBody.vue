<script setup>
/**
 * Body of a pending Gemini tool approval (an ACP ``session/request_permission``).
 *
 * ``pendingRequest.tool_input`` carries the request as the CLI sent it:
 *   { title, kind, locations: [path | { path, line }],
 *     content: [{ type: 'diff', path, oldText, newText }
 *               | { type: 'content', content: { type: 'text', text } }],
 *     options: [{ optionId, name, kind: 'allow_once' | 'allow_always'
 *                                      | 'reject_once' | 'reject_always' }] }
 *
 * One button per offered option; the answer is the chosen ``optionId``
 * (``{ option_id }``, sent by ``providers/gemini/ws.js``).
 */
import { useI18n } from 'vue-i18n'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { canStealFocus } from '../../../../../utils/focusGuard'
import { fileIconFor } from '../../../../../providers/utils/path'
import AppTooltip from '../../../../ui/AppTooltip.vue'
import MarkdownContent from '../../../../ui/MarkdownContent.vue'
import ToolDiffViewer from '../claude_code/ToolDiffViewer.vue'

const { t, te } = useI18n()

const props = defineProps({
    pendingRequest: { type: Object, required: true },
    isResponding: { type: Boolean, default: false },
    sessionId: { type: String, required: true },
})
const emit = defineEmits(['submit'])

const toolInput = computed(() => props.pendingRequest.tool_input || {})

const title = computed(() => (typeof toolInput.value.title === 'string' ? toolInput.value.title : ''))

// ACP tool kind (read, edit, delete, move, search, execute, think, fetch,
// other), from the request input, else the request's tool name.
const kind = computed(() => toolInput.value.kind || props.pendingRequest.tool_name || 'other')
const kindLabel = computed(() => {
    const key = `session.approval.gemini.kinds.${kind.value}`
    return te(key) ? t(key) : kind.value
})

const locations = computed(() => (Array.isArray(toolInput.value.locations) ? toolInput.value.locations : [])
    .map(location => (typeof location === 'string' ? { path: location, line: null } : location))
    .filter(location => location && typeof location.path === 'string' && location.path)
    .map(location => ({ path: location.path, display: location.line ? `${location.path}:${location.line}` : location.path })))

const contentEntries = computed(() => (Array.isArray(toolInput.value.content) ? toolInput.value.content : [])
    .filter(entry => entry && typeof entry === 'object'))
const diffs = computed(() => contentEntries.value.filter(entry => entry.type === 'diff'))
const texts = computed(() => contentEntries.value
    .filter(entry => entry.type === 'content' && entry.content?.type === 'text' && typeof entry.content.text === 'string' && entry.content.text.trim())
    .map(entry => entry.content.text))

// Buttons, in a fixed order whatever the CLI's: allow once (primary),
// allow for the session, then the rejections; unknown kinds last.
const OPTION_ORDER = ['allow_once', 'allow_always', 'reject_once', 'reject_always']
const OPTION_DISPLAY = {
    allow_once: { labelKey: 'session.approval.gemini.allowOnce', hintKey: 'session.approval.gemini.allowOnceHint', variant: 'brand', appearance: 'accent', icon: 'check' },
    allow_always: { labelKey: 'session.approval.gemini.allowAlways', hintKey: 'session.approval.gemini.allowAlwaysHint', variant: 'brand', appearance: 'outlined', icon: 'rotate' },
    reject_once: { labelKey: 'session.approval.gemini.reject', hintKey: 'session.approval.gemini.rejectHint', variant: 'danger', appearance: 'outlined', icon: 'xmark' },
    reject_always: { labelKey: 'session.approval.gemini.rejectAlways', hintKey: 'session.approval.gemini.rejectHint', variant: 'danger', appearance: 'outlined', icon: 'ban' },
}

const options = computed(() => {
    const list = (Array.isArray(toolInput.value.options) ? toolInput.value.options : [])
        .filter(option => option && typeof option.optionId === 'string' && option.optionId)
    const rank = option => {
        const index = OPTION_ORDER.indexOf(option.kind)
        return index === -1 ? OPTION_ORDER.length : index
    }
    return [...list].sort((a, b) => rank(a) - rank(b)).map((option, index) => {
        const display = OPTION_DISPLAY[option.kind] ?? { labelKey: null, hintKey: null, variant: 'neutral', appearance: 'outlined', icon: null }
        return {
            id: option.optionId,
            buttonId: `gemini-option-${props.pendingRequest.request_id}-${index}`,
            label: display.labelKey ? t(display.labelKey) : (option.name || option.optionId),
            hint: display.hintKey ? t(display.hintKey) : null,
            variant: display.variant,
            appearance: display.appearance,
            icon: display.icon,
            primary: option.kind === 'allow_once',
        }
    })
})

function choose(optionId) {
    if (props.isResponding) return
    emit('submit', { option_id: optionId })
}

// Focus the primary button when the request appears (or a queued request
// takes the slot), unless the user is busy elsewhere (canStealFocus).
const rootRef = ref(null)
function focusPrimary() {
    nextTick(() => {
        if (!canStealFocus()) return
        rootRef.value?.querySelector('.auto-focused')?.focus()
    })
}
onMounted(focusPrimary)
watch(() => props.pendingRequest?.request_id, focusPrimary)
</script>

<template>
    <div ref="rootRef" v-scroll-shadow class="gemini-pending-body">
        <div class="gemini-pending-section">
            <div class="gemini-pending-summary">
                <wa-badge variant="neutral">{{ kindLabel }}</wa-badge>
                <span v-if="title" class="gemini-pending-title">{{ title }}</span>
            </div>
            <ul v-if="locations.length" class="gemini-location-list">
                <li v-for="(location, index) in locations" :key="index">
                    <img v-if="fileIconFor(location.path)" :src="fileIconFor(location.path)" alt="" class="gemini-file-icon">
                    <code class="gemini-file-path">{{ location.display }}</code>
                </li>
            </ul>
            <div v-for="(diff, index) in diffs" :key="`diff-${index}`" class="gemini-pending-diff">
                <ToolDiffViewer
                    :mode="diff.oldText ? 'diff' : 'full'"
                    :original="diff.oldText ?? ''"
                    :modified="diff.newText ?? ''"
                    :file-path="diff.path || null"
                />
            </div>
            <div v-for="(text, index) in texts" :key="`text-${index}`" class="gemini-pending-text">
                <MarkdownContent :source="text" :show-toolbar="false" />
            </div>
        </div>
        <div class="gemini-pending-actions">
            <template v-for="option in options" :key="option.id">
                <wa-button
                    :id="option.buttonId"
                    :class="{ 'auto-focused': option.primary }"
                    :variant="option.variant"
                    :appearance="option.appearance"
                    size="small"
                    :disabled="isResponding"
                    @click="choose(option.id)"
                >
                    <wa-icon v-if="option.icon" slot="start" :name="option.icon" variant="classic"></wa-icon>
                    {{ option.label }}
                </wa-button>
                <AppTooltip v-if="option.hint" :for="option.buttonId">{{ option.hint }}</AppTooltip>
            </template>
        </div>
    </div>
</template>

<style scoped>
.gemini-pending-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
    flex: 1;
    min-height: 0;
    overflow-y: auto;
}

.gemini-pending-section {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    background: var(--wa-color-neutral-5);
    border-radius: var(--wa-border-radius-m);
    padding: var(--wa-space-s);
}

.gemini-pending-summary {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    flex-wrap: wrap;
}

.gemini-pending-title {
    font-size: var(--wa-font-size-m);
    word-break: break-word;
}

.gemini-location-list {
    list-style: none;
    padding: 0;
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-2xs);

    li {
        display: flex;
        align-items: center;
        gap: var(--wa-space-xs);
    }
}

.gemini-file-icon {
    width: 1em;
    height: 1em;
    flex-shrink: 0;
}

.gemini-file-path {
    font-family: var(--wa-font-family-mono);
    font-size: var(--wa-font-size-s);
    word-break: break-all;
}

.gemini-pending-diff {
    height: 16rem;
}

.gemini-pending-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-s);
}

/* Always show the focus outline on the primary button (see the Codex body:
   wa-button delegates focus to its shadow DOM, hence :focus-within). */
wa-button.auto-focused:focus-within::part(base) {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}
</style>
