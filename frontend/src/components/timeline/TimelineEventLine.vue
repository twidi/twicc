<script setup>
// TimelineEventLine.vue - One event line: icon | wording, tags, badge, author label, error detail | time.
// Shared by the Timeline (TimelineEventRow, inside a session block) and the chat (SessionEventNotice), so an event
// looks the same in both. It decides nothing: the view model comes from `describeEvent` (utils/timeline/). Every
// string from data is rendered as TEXT with interpolation, never as HTML.

import { computed, ref } from 'vue'
import { splitLastWord } from '../../utils/timeline/parts'
import TimelineWordingPart from './TimelineWordingPart.vue'

const props = defineProps({
    /** The view model of `describeEvent`. */
    vm: { type: Object, required: true },
    /** The id of the event, to make the ids of the error toggle unique. */
    eventId: { type: [Number, String], required: true },
    /** Indented under the title of a session block (Timeline). */
    indented: { type: Boolean, default: false },
    /** A plain line between the blocks: its wording is semibold. */
    instance: { type: Boolean, default: false },
})

// With tags, the last word of the wording is kept apart: the word and the tags are glued and wrap together.
const wording = computed(() => (props.vm.tags.length ? splitLastWord(props.vm.parts) : { lead: props.vm.parts, last: null }))
const expanded = ref(false)
// Unique per row, so the toggle can point at the text it shows.
const errorRestId = computed(() => `timeline-error-rest-${props.eventId}`)
</script>

<template>
    <div class="timeline-event-line" :class="[`timeline-event-line--${vm.severity}`, { 'timeline-event-line--indented': indented, 'timeline-event-line--instance': instance }]">
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
</template>

<style scoped>
/* Three columns: event icon | text (wraps under its own start) | time (never wraps, never pushed). */
.timeline-event-line {
    /* The event icon column: icons have their own widths (auto-width), the column keeps the text aligned. */
    --timeline-icon-width: 1.25rem;
    display: grid;
    grid-template-columns: var(--timeline-icon-width) minmax(0, 1fr) auto;
    column-gap: var(--wa-space-s);
    align-items: baseline;
    min-height: 2rem;
    padding-block: var(--wa-space-2xs);
}

.timeline-event-line--indented {
    padding-inline-start: calc(var(--timeline-provider-width, 0rem) + var(--timeline-provider-gap, 0rem));
}

/* Baseline-aligned with the first line of the text (the grid default), so whatever makes that line taller (a badge)
   the icon stays on it. Its baseline is its bottom edge: it is lowered by 0.125em, as Font Awesome does inline. */
.timeline-row-icon {
    justify-self: center;
    position: relative;
    inset-block-start: 0.125em;
    color: var(--wa-color-text-quiet);
}

.timeline-event-line--success .timeline-row-icon { color: var(--wa-color-success-60); }
.timeline-event-line--warning .timeline-row-icon { color: var(--wa-color-warning-60); }
.timeline-event-line--danger .timeline-row-icon { color: var(--wa-color-danger-60); }

/* One inline flow, not flex items: the real spaces between the wording, the badge and the author label are rendered
   (a little margin adds to each), so a copy reads them. The error detail is a block: its own line. */
.timeline-row-main {
    min-width: 0;
    overflow-wrap: anywhere;
}

.timeline-event-line--instance .timeline-wording {
    font-weight: var(--wa-font-weight-semibold);
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
