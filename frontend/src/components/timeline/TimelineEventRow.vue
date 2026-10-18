<script setup>
// TimelineEventRow.vue - One event row of the Timeline, in session blocks (display design section 5.1):
//   group head:  [provider icon] session title (one line, ellipsis) ............ project
//                    icon | wording, tag, badge, author label                    | time
//   following:       icon | wording ...                                          | time
//   instance:    icon | wording (semibold)                                       | time
// The header line is the head row's own first line; every event line of a session sits at the same indent,
// under the start of the title. The time is a fixed right column; long text wraps in its own column.
// The current state of a session (archived, hidden, pinned) is not shown: it is not an event.
//
// A session block is a card with the look of the Orchestration tab's agent cards (the block-card tokens of
// styles/surfaces.css), without their state border. The scroller renders one item per event row, so each
// row draws its part of the card: the head row its top, every row its sides, the last row its bottom and
// the gap to the next block. An instance row is no card: it is a plain line between the blocks.
//
// All the decisions (wording, icon, severity, links, time) are made by the pure modules of
// utils/timeline/; this component only shows them. Every string from data is rendered as TEXT with
// interpolation, never as HTML.

import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { describeEvent } from '../../utils/timeline/describeEvent'
import ProjectBadge from '../project/ProjectBadge.vue'
import TimelineSessionMention from './TimelineSessionMention.vue'

const props = defineProps({
    /** An event row of `buildTimelineRows`. */
    row: { type: Object, required: true },
    /** Facts the wording needs: `{sessions, labelsFor, isSubagent}`. */
    context: { type: Object, required: true },
})

const dataStore = useDataStore()

const vm = computed(() => describeEvent(props.row.event, props.context))
const identity = computed(() => props.row.identity)
// A project the store does not know (deleted) is shown as plain text, never as a broken link.
const knownProject = computed(() => (identity.value?.projectId ? !!dataStore.getProject(identity.value.projectId) : false))

const expanded = ref(false)
// Unique per row, so the toggle can point at the text it shows.
const errorRestId = computed(() => `timeline-error-rest-${props.row.event.id}`)
</script>

<template>
    <div
        class="timeline-row"
        :class="[`timeline-row--${vm.severity}`, {
            'timeline-row--block': !row.instance,
            'timeline-row--indented': row.indented,
            'timeline-row--head': row.head,
            'timeline-row--last': row.last,
            'timeline-row--instance': row.instance,
        }]"
        :data-kind="vm.kind"
    >
        <div class="timeline-row-card">
        <div v-if="identity" class="timeline-row-header">
            <TimelineSessionMention :mention="identity.mention" truncate class="timeline-row-title" />
            <template v-if="identity.showProject && identity.projectId">
                <!-- A plain jump to that project: the router guard keeps the current workspace when the project belongs to it. -->
                <RouterLink
                    v-if="knownProject"
                    :to="{ name: 'project', params: { projectId: identity.projectId } }"
                    class="timeline-project"
                ><ProjectBadge :project-id="identity.projectId" /></RouterLink>
                <span v-else class="timeline-project timeline-project--plain">{{ identity.projectId }}</span>
            </template>
        </div>
        <div class="timeline-event-line" :class="{ 'timeline-event-line--indented': !row.instance }">
            <wa-icon :name="vm.icon" class="timeline-row-icon" auto-width :title="vm.tooltip"></wa-icon>
            <div class="timeline-row-main">
                <span class="timeline-wording">
                    <template v-for="(part, index) in vm.parts" :key="index">
                        <span v-if="part.type === 'text'">{{ part.text }}</span>
                        <TimelineSessionMention v-else :mention="part.mention" />
                    </template>
                </span>
                <span v-if="vm.tag" class="timeline-tag">{{ vm.tag }}</span>
                <wa-badge v-if="vm.badge" variant="warning" appearance="outlined" class="timeline-badge" :title="vm.badge.title">{{ vm.badge.text }}</wa-badge>
                <span v-if="vm.author.parts.length" class="timeline-author">
                    <template v-for="(part, index) in vm.author.parts" :key="index">
                        <span v-if="part.type === 'text'">{{ part.text }}</span>
                        <TimelineSessionMention v-else :mention="part.mention" />
                    </template>
                </span>
                <span v-if="vm.detail" class="timeline-error">
                    <code class="timeline-error-line">{{ vm.detail.first }}</code>
                    <button
                        v-if="vm.detail.rest"
                        type="button"
                        class="timeline-row__expand"
                        :aria-expanded="expanded ? 'true' : 'false'"
                        :aria-controls="errorRestId"
                        @click="expanded = !expanded"
                    >{{ expanded ? 'Show less' : 'Show more' }}</button>
                    <pre v-if="expanded && vm.detail.rest" :id="errorRestId" class="timeline-error-rest">{{ vm.detail.rest }}</pre>
                </span>
            </div>
            <span class="timeline-time" :class="{ 'timeline-time--estimated': vm.time.estimated }" :title="vm.tooltip">{{ vm.time.text }}</span>
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
    /* The block card: its distance to the panel edges, its inner padding, the gap below it. */
    --timeline-card-inset: var(--wa-space-m);
    --timeline-card-padding: var(--wa-space-s);
    --timeline-block-gap: var(--wa-space-s);
    padding-inline: var(--timeline-card-inset);
    font-size: var(--wa-font-size-s);
}

/* A little air above each session block (it also parts a block from an instance line above it). */
.timeline-row--head {
    padding-block-start: var(--wa-space-2xs);
}

/* The gap to what follows the block, outside the card, in the row the scroller measures. */
.timeline-row--last {
    padding-block-end: var(--timeline-block-gap);
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

.timeline-row-main {
    min-width: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    column-gap: var(--wa-space-s);
    row-gap: 0;
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

.timeline-tag,
.timeline-author {
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

/* On the text baseline, like the icon and the time: the badge, the wording and the icon share one visual line. */
.timeline-badge {
    align-self: baseline;
}

.timeline-error {
    display: inline-flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--wa-space-xs);
    flex-basis: 100%;
}

.timeline-row__expand {
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

.timeline-error-rest {
    flex-basis: 100%;
    margin: 0;
    white-space: pre-wrap;
}

/* At least the width of "~00:00", so the text column ends at the same place on every line. */
.timeline-time {
    justify-self: end;
    min-width: 6ch;
    text-align: end;
    white-space: nowrap;
    font-variant-numeric: tabular-nums;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-xs);
}

.timeline-time--estimated {
    opacity: 0.6;
}
</style>
