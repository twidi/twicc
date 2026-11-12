<script setup>
// TimelineTurnLine.vue - A turn boundary of the Timeline drawn as the end of a rail (instead of an event line):
//   end   (the `turn.ended` row, the top of the turn):  ╭ "Turn ended in 48 s"
//   start (the `turn.started` row, the bottom):         ╰── (a short stub that fades out, no text)
// A running turn has no `turn.ended` row: its end row is a synthetic one (rows.js) that says "Turn running for 2m 30s".
// The rail itself (the vertical line through the icons of the turn) is drawn by TimelineEventRow; this component
// draws the curve where the line turns to the right, and the words.

import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSecondClock } from '../../composables/useSecondClock'
import { formatDuration } from '../../utils/date'

const { t } = useI18n()

const props = defineProps({
    /** `end` or `start`. */
    role: { type: String, required: true },
    /** The `rail` of the row (utils/timeline/rows.js). */
    rail: { type: Object, required: true },
    /** The view model of `describeEvent`: the background-work badge of a turn end. */
    vm: { type: Object, required: true },
})

const running = computed(() => props.role === 'end' && props.rail.open)
const clock = useSecondClock(() => running.value && props.rail.startedAtMs !== null)

const sentence = computed(() => {
    if (running.value) {
        return props.rail.startedAtMs === null ? '' : t('session.timeline.turnRunningFor', { duration: formatDuration(Math.max(0, clock.value - props.rail.startedAtMs) / 1000) })
    }
    if (props.role === 'start') return ''
    return props.rail.durationMs === null
        ? t('session.timeline.turnEnded')
        : t('session.timeline.turnEndedIn', { duration: formatDuration(props.rail.durationMs / 1000) })
})
// A stub with no text still has a name for a screen reader and for a copy.
const label = computed(() => sentence.value || t('session.timeline.turnStarted'))
const showsText = computed(() => sentence.value !== '')
</script>

<template>
    <div class="timeline-turn-line" :class="[`timeline-turn-line--${role}`, { 'timeline-turn-line--text': showsText }]">
        <span class="timeline-turn-corner" aria-hidden="true"></span>
        <span v-if="showsText" class="timeline-turn-text">{{ sentence }}<template v-if="vm.badge">{{ ' ' }}<wa-badge variant="warning" appearance="outlined" class="timeline-turn-badge" :title="vm.badge.title">{{ vm.badge.text }}</wa-badge></template></span>
        <span v-else class="timeline-turn-hidden-label">{{ label }}</span>
    </div>
</template>

<style scoped>
.timeline-turn-line {
    /* The icon box, its gap, the indent under the session title: the text lines up with the event wording. */
    --timeline-turn-text-start: calc(var(--timeline-event-indent, 0rem) + 1.25rem + var(--wa-space-s));
    position: relative;
    display: flex;
    align-items: flex-start;
    min-height: 0.8rem;
    padding-inline-start: var(--timeline-turn-text-start);
    font-size: var(--wa-font-size-xs);
    color: var(--wa-color-text-quiet);
}

/* A fixed line height, so the curve (at the middle of the first line) does not move when the text wraps. */
.timeline-turn-line--text {
    min-height: 1.3rem;
    line-height: 1.3rem;
}

.timeline-turn-text {
    min-width: 0;
    overflow-wrap: anywhere;
}

.timeline-turn-badge {
    vertical-align: baseline;
    margin-inline-start: var(--wa-space-xs);
    padding-block: 0.1em;
}

.timeline-turn-hidden-label {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
}

/* The curve: the vertical line of the rail (x from the row) turns to the right. */
.timeline-turn-corner {
    position: absolute;
    inset-inline-start: var(--timeline-rail-x);
    width: var(--timeline-turn-radius);
    height: var(--timeline-turn-radius);
    box-sizing: border-box;
    border-inline-start: var(--timeline-rail-width) solid var(--timeline-rail-color);
}

/* The end: the curve opens downwards from the middle of the first text line. */
.timeline-turn-line--end .timeline-turn-corner {
    inset-block-start: var(--timeline-turn-end-y);
    /* Longer than the radius: the horizontal part runs on to a short distance of the text, which does not move. */
    width: calc(var(--timeline-turn-text-start) - var(--timeline-rail-x) - 0.4rem);
    border-block-start: var(--timeline-rail-width) solid var(--timeline-rail-color);
    border-start-start-radius: var(--timeline-turn-radius);
}

/* The start: the curve ends on the horizontal line `--timeline-turn-corner-y` under the top of the row. */
.timeline-turn-line--start .timeline-turn-corner {
    inset-block-start: calc(var(--timeline-turn-corner-y) - var(--timeline-turn-radius));
    border-block-end: var(--timeline-rail-width) solid var(--timeline-rail-color);
    border-end-start-radius: var(--timeline-turn-radius);
}

/* The stub that fades out after the curve: it only says "it starts here", it never runs across the line. */
.timeline-turn-line--start:not(.timeline-turn-line--text) .timeline-turn-corner::after {
    content: "";
    position: absolute;
    inset-inline-start: 100%;
    inset-block-end: calc(-1 * var(--timeline-rail-width));
    width: 2.6rem;
    height: var(--timeline-rail-width);
    background: linear-gradient(to right, var(--timeline-rail-color), transparent);
}
</style>
