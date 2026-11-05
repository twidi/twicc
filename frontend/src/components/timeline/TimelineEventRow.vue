<script setup>
// TimelineEventRow.vue - One event row of the Timeline, in session blocks (display design section 5.1):
//   group head:  [provider icon] session title (one line, ellipsis) ..... [only] project
//                    icon | wording, tags, badge, author label                   | time
//   following:       icon | wording ...                                          | time
//   instance:    icon | wording (semibold)                                       | time
// The header line is the head row's own first line; every event line of a session sits at the same indent,
// under the start of the title. The time is a fixed right column; long text wraps in its own column.
// The header shows current archived, muted and pinned state. Event lines keep their historical meaning.
//
// A session block is a card with the look of the Orchestration tab's agent cards (the block-card tokens of
// styles/surfaces.css), without their state border. The scroller renders one item per event row, so each
// row draws its part of the card: the head row its top, every row its sides, the last row its bottom. The gap
// between two blocks is split, outside the card: half above the head row, half below the last row. An instance
// row is no card: it is a plain line between the blocks.
//
// All the decisions (wording, icon, severity, links, time) are made by the pure modules of
// utils/timeline/; this component only shows them. Every string from data is rendered as TEXT with
// interpolation, never as HTML.

import { useTextToSpeechStore } from '../../stores/textToSpeech'
import { useMessageSummary } from '../../composables/useMessageSummary.js'
import { computed, nextTick, ref, useId, watch } from 'vue'
import { SUMMARY_MIN_CHARS } from '../../constants'
import { SUMMARY_ICON_SRC } from '../../utils/summaryIcon'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { describeEvent } from '../../utils/timeline/describeEvent'
import ProjectBadge from '../project/ProjectBadge.vue'
import TimelineSessionMention from './TimelineSessionMention.vue'
import TimelineEventLine from './TimelineEventLine.vue'
import TimelineMessage from './TimelineMessage.vue'
import TextToSpeechButton from '../ui/TextToSpeechButton.vue'

const props = defineProps({
    /** An event row of `buildTimelineRows`. */
    row: { type: Object, required: true },
    /** Facts the wording needs: `{sessions, labelsFor, isSubagent}`. */
    context: { type: Object, required: true },
    /** The id of the session the timeline is restricted to (filters design section 3.4), else null. */
    sessionFilterId: { type: String, default: null },
})

const emit = defineEmits({
    /** "Only this session" pressed on the head row: `{id, title}`, `title` null for an untitled session. */
    'toggle-session': (payload) => typeof payload?.id === 'string',
})

const dataStore = useDataStore()
const speech = useTextToSpeechStore()

const vm = computed(() => describeEvent(props.row.event, props.context))
const identity = computed(() => props.row.identity)
const isMessage = computed(() => ['user_message.sent', 'assistant_message.sent'].includes(props.row.event.kind))
const hasMessageText = computed(() => isMessage.value && typeof props.row.event.data?.text === 'string' && !!props.row.event.data.text.trim())
const messageExpanded = ref(false)
const summary = useMessageSummary(() => props.row.event.kind === 'assistant_message.sent'
    ? { event_id: props.row.event.id } : null)
const reading = computed(() => (summary.identity && speech.statusForSummary(summary.identity) !== 'idle')
    || speech.statusForText(props.row.event.data?.text) !== 'idle')
const showSummaryButton = computed(() => summary.available
    && (summary.hasSummary || (props.row.event.data?.text ?? '').trim().length >= SUMMARY_MIN_CHARS))
async function toggleSummary() {
    if (summary.view === 'summary') { summary.showOriginal(); return }
    messageExpanded.value = true
    await summary.summarize()
}

const messageHasMore = ref(false)
const messageToggleRef = ref(null)
const messageLabel = computed(() => vm.value.parts.map(part => part.text ?? '').join(''))
const messageId = `timeline-message-${useId()}`
const messageLabelId = `${messageId}-label`
watch(() => props.row.event.id, () => { messageExpanded.value = false }, { flush: 'sync' })

async function focusMessageLabel() {
    await nextTick()
    messageToggleRef.value?.focus?.()
}
// A project the store does not know (deleted) is shown as plain text, never as a broken link.
const knownProject = computed(() => (identity.value?.projectId ? !!dataStore.getProject(identity.value.projectId) : false))

// "Only this session" (filters design section 3.4), on the head row of a session block. Pressed when the timeline is
// restricted to this session: pressed again, it shows every session.
const sessionId = computed(() => props.row.event.session_id)
const currentSessionState = computed(() => dataStore.getSession(sessionId.value)
    ?? props.context.sessions?.[sessionId.value] ?? null)
const onlyThisSession = computed(() => props.sessionFilterId !== null && props.sessionFilterId === sessionId.value)
// The name stays "Only this session" in both states (a toggle button's label never changes with its state, WAI-ARIA
// APG): `aria-pressed` carries the state. The tooltip says what a click does.
const onlyTooltip = computed(() => (onlyThisSession.value ? 'Show every session' : 'Only this session'))

// The title of the side table, captured by the pill. Not the header mention's: built with the full id as fallback, it
// would hide the short id the pill shows for an untitled session.
function toggleSession() {
    emit('toggle-session', { id: sessionId.value, title: props.context.sessions?.[sessionId.value]?.title || null })
}
</script>

<template>
    <div
        class="timeline-row"
        :class="[`timeline-row--${vm.severity}`, {
            'timeline-row--block': !row.instance,
            'timeline-row--head': row.head,
            'timeline-row--last': row.last,
            'timeline-row--instance': row.instance,
        }]"
        :data-kind="vm.kind"
    >
        <div class="timeline-row-card">
        <div v-if="identity" class="timeline-row-header">
            <!-- Real spaces between the pieces of the row. Inside the main column (an inline flow) they are rendered and
                 a copy gets them. Between flex or grid items (the title and the project, the header and the event line,
                 the main column and the time) they render nothing and a copy puts a line break there anyway: they only
                 keep the text content of the row readable. -->
            <TimelineSessionMention :mention="identity.mention" :session-state="currentSessionState" truncate class="timeline-row-title" /><!--
                No real space around it: a copy skips it (user-select: none), the space before the project is enough.
                --><button
                type="button"
                class="timeline-row-only"
                :aria-pressed="onlyThisSession ? 'true' : 'false'"
                :title="onlyTooltip"
                @click="toggleSession"
            ><!--
                The tooltip is the only visual label of this icon-only control. Unpressed it equals the name: browsers
                drop an accessible description identical to the name, so it is not announced twice.
                --><wa-icon name="filter"></wa-icon><span class="timeline-row-only-text">Only this session</span></button><template v-if="identity.showProject && identity.projectId">{{ ' ' }}<!--
                A plain jump to that project: the router guard keeps the current workspace when the project belongs to it.
                --><RouterLink
                    v-if="knownProject"
                    :to="{ name: 'project', params: { projectId: identity.projectId } }"
                    class="timeline-project"
                ><ProjectBadge :project-id="identity.projectId" /></RouterLink>
                <span v-else class="timeline-project timeline-project--plain">{{ identity.projectId }}</span>
            </template>
        </div><template v-if="identity">{{ ' ' }}</template><TimelineEventLine
            :vm="vm"
            :event-id="row.event.id"
            :indented="!row.instance"
            :instance="row.instance"
            :author-run="row.authorRun ?? null"
        >
            <template v-if="hasMessageText" #wording>
                <button
                    :id="messageLabelId"
                    ref="messageToggleRef"
                    type="button"
                    class="timeline-wording timeline-message-label"
                    :aria-expanded="messageExpanded ? 'true' : 'false'"
                    :aria-controls="messageId"
                    @click="messageExpanded = !messageExpanded"
                >{{ messageLabel }}<wa-icon
                    v-if="messageHasMore"
                    class="timeline-message-chevron"
                    :name="messageExpanded ? 'chevron-up' : 'chevron-down'"
                    aria-hidden="true"
                ></wa-icon></button>
                <TextToSpeechButton
                    v-if="row.event.kind === 'assistant_message.sent'"
                    class="timeline-message-speech"
                    appearance="plain"
                    :text="summary.view === 'summary' ? (summary.entry?.text || '') : row.event.data.text"
                    :original-text="row.event.data.text"
                    :summary-ref="summary.view === 'summary' ? summary.playbackReference : null"
                    :summary-identity="summary.identity"
                    :disabled="summary.view === 'summary' && !summary.playbackReference && !reading"
                    @summary-changed="summary.refreshSaved"
                />
                <wa-button
                    v-if="showSummaryButton"
                    class="timeline-message-summary"
                    size="small"
                    :variant="summary.view === 'summary' ? 'brand' : 'neutral'"
                    appearance="plain"
                    :disabled="summary.view !== 'summary' && !summary.canSummarize"
                    :title="summary.toggleTitle"
                    :aria-label="summary.toggleTitle"
                    :aria-pressed="summary.view === 'summary'"
                    @click="toggleSummary"
                >
                    <wa-icon :src="SUMMARY_ICON_SRC"></wa-icon>
                </wa-button>
            </template>
        </TimelineEventLine>
        <TimelineMessage
            v-if="isMessage"
            v-model:expanded="messageExpanded"
            class="timeline-row-message"
            :event-id="row.event.id"
            :summary="summary"
            :kind="row.event.kind"
            :data="row.event.data"
            :content-id="messageId"
            :labelled-by="messageLabelId"
            @toggle="focusMessageLabel"
            @more-change="messageHasMore = $event"
        />
        </div>
    </div>
</template>

<style scoped>
.timeline-row {
    /* The provider icon box of the header and its gap to the title (TimelineSessionMention `truncate` reads
       both): an event line of a session is indented by their sum, so its icon sits under the title start. */
    --timeline-provider-width: 1.25rem;
    --timeline-provider-gap: var(--wa-space-xs);
    /* The block card: its distance to the panel edges, its inner padding, the gap between two blocks. */
    --timeline-card-inset: var(--wa-space-m);
    --timeline-card-padding: var(--wa-space-s);
    --timeline-block-gap: var(--wa-space-m);
    padding-inline: var(--timeline-card-inset);
    font-size: var(--wa-font-size-s);
}

/* The gap between two blocks, outside the cards, in the rows the scroller measures: half above the head, half below
   the end. An instance line or a day separator between two blocks then has the same space above and below it. */
.timeline-row--head {
    padding-block-start: calc(var(--timeline-block-gap) / 2);
}

.timeline-row--last {
    padding-block-end: calc(var(--timeline-block-gap) / 2);
}

/* The sides of the card, on every row of the block. The Orchestration cards' shadow (dark mode) is left out: a
   shadow drawn row by row would show a seam at every row boundary. */
.timeline-row--block .timeline-row-card {
    padding-inline: var(--timeline-card-padding);
    background: var(--block-card-bg);
    border-inline: 1px solid var(--block-card-border);
}

/* The top of the card, on the head row (the Orchestration card's top padding). */
.timeline-row--head .timeline-row-card {
    padding-block-start: var(--wa-space-xs);
    border-block-start: 1px solid var(--block-card-border);
    border-start-start-radius: var(--wa-border-radius-m);
    border-start-end-radius: var(--wa-border-radius-m);
}

/* The bottom of the card, on the last row. With the event line's own padding it matches the Orchestration
   card's bottom padding. */
.timeline-row--last .timeline-row-card {
    padding-block-end: var(--wa-space-xs);
    border-block-end: 1px solid var(--block-card-border);
    border-end-start-radius: var(--wa-border-radius-m);
    border-end-end-radius: var(--wa-border-radius-m);
}

/* No card, but the same content edges as the cards (padding and border): the time column stays one column. */
.timeline-row--instance .timeline-row-card {
    padding-inline: calc(var(--timeline-card-padding) + 1px);
}

/* Align the saved message with the event wording. The scroller measures its expanded height normally. */
.timeline-row-message {
    margin-inline-start: calc(var(--timeline-provider-width) + var(--timeline-provider-gap) + 1.25rem + var(--wa-space-s));
    padding-block-end: var(--wa-space-xs);
}

.timeline-message-label {
    appearance: none;
    display: inline;
    border: none;
    background: none;
    padding: 0;
    margin: 0;
    height: auto;
    font: inherit;
    line-height: inherit;
    color: inherit;
    text-align: start;
    cursor: pointer;
}

.timeline-message-label:hover {
    text-decoration: underline;
}

.timeline-message-label:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.timeline-message-chevron {
    margin-inline-start: var(--wa-space-2xs);
    font-size: 0.75em;
    color: var(--wa-color-text-quiet);
}

.timeline-message-speech,
.timeline-message-summary {
    margin-inline-start: var(--wa-space-xs);
    vertical-align: middle;
    --wa-form-control-height: 1.5rem;
}

/* The block header: title truncates first; the project keeps its size up to half the line, then truncates. */
.timeline-row-header {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-height: 1.75rem;
    min-width: 0;
}

.timeline-row-title {
    flex: 1 1 auto;
}

/* "Only this session": an icon button between the title and the project. Web Awesome's native.css makes every
   <button> an inline-flex form control: reset like the error toggle. Its label is never part of a copied row. */
.timeline-row-only {
    flex: 0 0 auto;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    appearance: none;
    border: none;
    background: none;
    padding: var(--wa-space-3xs);
    height: auto;
    border-radius: var(--wa-border-radius-s);
    font: inherit;
    font-size: var(--wa-font-size-xs);
    line-height: 1;
    color: var(--wa-color-text-quiet);
    cursor: pointer;
    transition: opacity var(--wa-transition-fast), color var(--wa-transition-fast);
    -webkit-user-select: none;
    user-select: none;
}

.timeline-row-only:hover {
    color: var(--wa-color-text-normal);
}

.timeline-row-only[aria-pressed="true"] {
    color: var(--wa-color-brand-60);
}

.timeline-row-only:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

/* Its name, out of sight but in the flow (the pattern of the arrow's word, TimelineWordingPart.vue). */
.timeline-row-only-text {
    display: inline-block;
    width: 1px;
    height: 1px;
    margin-inline-end: -1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
}

/* With a hover pointer, the control shows on the header's hover and on keyboard focus (filters design section 3.4),
   and stays while pressed: the block says which session the timeline is restricted to. A touch screen always shows it. */
@media (hover: hover) {
    .timeline-row-only {
        opacity: 0;
    }

    .timeline-row-header:hover .timeline-row-only,
    .timeline-row-only:focus-visible,
    .timeline-row-only[aria-pressed="true"] {
        opacity: 1;
    }
}

.timeline-project {
    flex: 0 0 auto;
    min-width: 0;
    max-width: 50%;
    overflow: hidden;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

/* A flex container, so the badge inside can shrink: its names carry their own ellipsis. */
a.timeline-project {
    display: flex;
    text-decoration: none;
}

.timeline-project--plain {
    white-space: nowrap;
    text-overflow: ellipsis;
}

</style>
