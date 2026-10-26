<script setup>
// TimelineEventRow.vue - One event row of the Timeline, in session blocks (display design section 5.1):
//   group head:  [provider icon] session title (one line, ellipsis) ..... [only] project
//                    icon | wording, tags, badge, author label                   | time
//   following:       icon | wording ...                                          | time
//   instance:    icon | wording (semibold)                                       | time
// The header line is the head row's own first line; every event line of a session sits at the same indent,
// under the start of the title. The time is a fixed right column; long text wraps in its own column.
// The current state of a session (archived, hidden, pinned) is not shown: it is not an event.
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

import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { describeEvent } from '../../utils/timeline/describeEvent'
import { splitLastWord } from '../../utils/timeline/parts'
import ProjectBadge from '../project/ProjectBadge.vue'
import TimelineSessionMention from './TimelineSessionMention.vue'
import TimelineWordingPart from './TimelineWordingPart.vue'

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

const vm = computed(() => describeEvent(props.row.event, props.context))
// With tags, the last word of the wording is kept apart: the word and the tags are glued and wrap together.
const wording = computed(() => (vm.value.tags.length ? splitLastWord(vm.value.parts) : { lead: vm.value.parts, last: null }))
const identity = computed(() => props.row.identity)
// A project the store does not know (deleted) is shown as plain text, never as a broken link.
const knownProject = computed(() => (identity.value?.projectId ? !!dataStore.getProject(identity.value.projectId) : false))

// "Only this session" (filters design section 3.4), on the head row of a session block. Pressed when the timeline is
// restricted to this session: pressed again, it shows every session.
const sessionId = computed(() => props.row.event.session_id)
const onlyThisSession = computed(() => props.sessionFilterId !== null && props.sessionFilterId === sessionId.value)
// The name stays "Only this session" in both states (a toggle button's label never changes with its state, WAI-ARIA
// APG): `aria-pressed` carries the state. The tooltip says what a click does.
const onlyTooltip = computed(() => (onlyThisSession.value ? 'Show every session' : 'Only this session'))

// The title of the side table, captured by the pill. Not the header mention's: built with the full id as fallback, it
// would hide the short id the pill shows for an untitled session.
function toggleSession() {
    emit('toggle-session', { id: sessionId.value, title: props.context.sessions?.[sessionId.value]?.title || null })
}

const expanded = ref(false)
// Unique per row, so the toggle can point at the text it shows.
const errorRestId = computed(() => `timeline-error-rest-${props.row.event.id}`)
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
            <TimelineSessionMention :mention="identity.mention" truncate class="timeline-row-title" /><!--
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
        </div><template v-if="identity">{{ ' ' }}</template><div class="timeline-event-line" :class="{ 'timeline-event-line--indented': !row.instance }">
            <wa-icon :name="vm.icon" class="timeline-row-icon" auto-width :title="vm.tooltip"></wa-icon>
            <div class="timeline-row-main">
                <!-- The parts of utils/timeline/parts.js. The tags are inline content of the wording, glued to its last
                     word: they wrap together, a tag is never on a line of its own. -->
                <span class="timeline-wording">
                    <TimelineWordingPart v-for="(part, index) in wording.lead" :key="index" :part="part" />
                    <span v-if="vm.tags.length" class="timeline-tag-glue">
                        <TimelineWordingPart v-if="wording.last" :part="wording.last" /><template v-for="tag in vm.tags" :key="tag">{{ ' ' }}<wa-tag size="small" variant="neutral" class="timeline-tag">{{ tag }}</wa-tag></template>
                    </span>
                </span>
                <template v-if="vm.badge">{{ ' ' }}<wa-badge variant="warning" appearance="outlined" class="timeline-badge" :title="vm.badge.title">{{ vm.badge.text }}</wa-badge></template>
                <template v-if="vm.author.parts.length">{{ ' ' }}<span class="timeline-author">
                    <TimelineWordingPart v-for="(part, index) in vm.author.parts" :key="index" :part="part" />
                </span></template>
                <template v-if="vm.detail">{{ ' ' }}<span class="timeline-error">
                    <code class="timeline-error-line">{{ vm.detail.first }}</code>
                    <template v-if="vm.detail.rest">{{ ' ' }}</template><button
                        v-if="vm.detail.rest"
                        type="button"
                        class="timeline-row__expand"
                        :aria-expanded="expanded ? 'true' : 'false'"
                        :aria-controls="errorRestId"
                        @click="expanded = !expanded"
                    >{{ expanded ? 'Show less' : 'Show more' }}</button>
                    <template v-if="expanded && vm.detail.rest">{{ ' ' }}<pre :id="errorRestId" class="timeline-error-rest">{{ vm.detail.rest }}</pre></template>
                </span></template>
            </div>{{ ' ' }}<span class="timeline-time" :class="{ 'timeline-time--estimated': vm.time.estimated }" :title="vm.tooltip">{{ vm.time.text }}</span>
        </div>
        </div>
    </div>
</template>

<style scoped>
.timeline-row {
    /* The provider icon box of the header and its gap to the title (TimelineSessionMention `truncate` reads
       both): an event line of a session is indented by their sum, so its icon sits under the title start. */
    --timeline-provider-width: 1.25rem;
    --timeline-provider-gap: var(--wa-space-xs);
    /* The event icon column: icons have their own widths (auto-width), the column keeps the text aligned. */
    --timeline-icon-width: 1.25rem;
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

/* Three columns: event icon | text (wraps under its own start) | time (never wraps, never pushed). */
.timeline-event-line {
    display: grid;
    grid-template-columns: var(--timeline-icon-width) minmax(0, 1fr) auto;
    column-gap: var(--wa-space-s);
    align-items: baseline;
    min-height: 2rem;
    padding-block: var(--wa-space-2xs);
}

.timeline-event-line--indented {
    padding-inline-start: calc(var(--timeline-provider-width) + var(--timeline-provider-gap));
}

/* Baseline-aligned with the first line of the text (the grid default), so whatever makes that line taller (a badge)
   the icon stays on it. Its baseline is its bottom edge: it is lowered by 0.125em, as Font Awesome does inline. */
.timeline-row-icon {
    justify-self: center;
    position: relative;
    inset-block-start: 0.125em;
    color: var(--wa-color-text-quiet);
}

.timeline-row--success .timeline-row-icon { color: var(--wa-color-success-60); }
.timeline-row--warning .timeline-row-icon { color: var(--wa-color-warning-60); }
.timeline-row--danger .timeline-row-icon { color: var(--wa-color-danger-60); }

/* One inline flow, not flex items: the real spaces between the wording, the badge and the author label are rendered
   (a little margin adds to each), so a copy reads them. The error detail is a block: its own line. */
.timeline-row-main {
    min-width: 0;
    overflow-wrap: anywhere;
}

.timeline-row--instance .timeline-wording {
    font-weight: var(--wa-font-weight-semibold);
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

/* The last word and the tag move to the next line together. An inline block: a last word longer than the line
   still breaks inside it (the column's overflow-wrap). */
.timeline-tag-glue {
    display: inline-block;
}

/* A fact of the event ("v1.97.0") or its source ("automatic", "from Codex"): a small tag right after the last word
   of the wording (a real space before it, in the template). */
.timeline-tag {
    vertical-align: baseline;
    /* Not the semibold of an instance row's wording. */
    font-weight: var(--wa-font-weight-normal);
    /* Tighter than the form-control height Web Awesome gives a tag: it sits inside a line of text. */
    height: auto;
    line-height: 1.2;
    padding-block: 0.1em;
    padding-inline: 0.5em;
    /* A little more room than the real space before it. */
    margin-inline-start: 0.25em;
    /* Web Awesome makes the tag host unselectable: a copied row would lose its source. The page rule wins over :host. */
    -webkit-user-select: text;
    user-select: text;
}

/* One box that wraps as a whole, like the flex item it was (an inline block is still copied inline). With the real
   space before it, the old 12px gap. */
.timeline-author {
    display: inline-block;
    max-width: 100%;
    margin-inline-start: var(--wa-space-xs);
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

/* On the text baseline, like the icon and the time: the badge, the wording and the icon share one visual line (Web
   Awesome puts it on `middle`). */
.timeline-badge {
    vertical-align: baseline;
    /* With the real space before it, the old 12px gap. */
    margin-inline-start: var(--wa-space-xs);
    /* Tighter than Web Awesome's 0.375em, like the tag. */
    padding-block: 0.1em;
    /* Selectable, like the tag: a copied row keeps its background work. */
    -webkit-user-select: text;
    user-select: text;
}

/* A line of its own under the wording; inside it, the first line, the toggle and the rest flow with real spaces. */
.timeline-error {
    display: block;
}

/* Web Awesome's native.css makes every <button> an inline-flex form control: reset to a text-sized inline box. */
.timeline-row__expand {
    display: inline-block;
    vertical-align: baseline;
    margin-inline-start: var(--wa-space-2xs);
    appearance: none;
    border: none;
    background: none;
    padding: 0;
    height: auto;
    line-height: inherit;
    font: inherit;
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-brand-60);
    cursor: pointer;
}

.timeline-row__expand:hover {
    text-decoration: underline;
}

.timeline-row__expand:focus-visible {
    outline: var(--wa-focus-ring);
    outline-offset: var(--wa-focus-ring-offset);
}

.timeline-error-line,
.timeline-error-rest {
    font-family: var(--wa-font-family-code);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-danger-60);
}

/* The old 8px gap under the first line (the error is a block, it has no flex gap). */
.timeline-error-rest {
    margin: var(--wa-space-xs) 0 0;
    white-space: pre-wrap;
}

.timeline-time {
    justify-self: end;
    text-align: end;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

/* The width of the widest time of the locale on every line ("~23:59", "~11:59 PM"), so the text column ends at the
   same place: an invisible line of no height holding the samples the panel sets (eventTime.js timeColumnSizer).
   After the text, so the time keeps the baseline of its text. Generated content: never copied nor announced. */
.timeline-time::after {
    content: var(--timeline-time-sizer, "");
    display: block;
    height: 0;
    overflow: hidden;
    visibility: hidden;
    white-space: pre;
}

.timeline-time--estimated {
    opacity: 0.6;
}
</style>
