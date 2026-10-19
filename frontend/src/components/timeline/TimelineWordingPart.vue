<script setup>
// TimelineWordingPart.vue - One part of a timeline wording (utils/timeline/parts.js), rendered as TEXT, never as HTML:
// prose, a value (a session title, set apart from the prose), the arrow between an old and a new value (an icon and
// its word, hidden from the eye), or a session mention.

import TimelineSessionMention from './TimelineSessionMention.vue'

defineProps({
    /** `{type: 'text'|'value', text}`, `{type: 'arrow', label}` or `{type: 'session', mention}`. */
    part: { type: Object, required: true },
})
</script>

<template>
    <span v-if="part.type === 'text'">{{ part.text }}</span>
    <span v-else-if="part.type === 'value'" class="timeline-value">{{ part.text }}</span>
    <!-- Real spaces around the arrow, not margins: a copy or a screen reader gets separate words. A no-break space
         before it, so the arrow stays on the line of the old value and never starts a line alone. The icon has no
         label (Web Awesome then hides it from assistive tech): its word, hidden from the eye, is what a screen reader
         announces, once, and what a copy gets. -->
    <template v-else-if="part.type === 'arrow'">{{ '\u00A0' }}<wa-icon name="arrow-right-long" auto-width class="timeline-arrow"></wa-icon><span class="timeline-arrow-text">{{ part.label }}</span>{{ ' ' }}</template>
    <TimelineSessionMention v-else :mention="part.mention" />
</template>

<style scoped>
/* A value from data (a session title, a setting value), set apart from the prose without quotes: the weight of the
   Orchestration card titles (the semibold token is too close to the prose). It wraps like the prose. */
.timeline-value {
    font-weight: var(--wa-font-weight-bold);
}

/* Between an old and a new value: on the text (lowered like Font Awesome inline icons), long, faint and with a
   little room on each side, so it reads as a separator next to the bold values (real spaces stay in the DOM too). */
.timeline-arrow {
    vertical-align: -0.125em;
    color: var(--wa-color-text-quiet);
    opacity: 0.6;
    margin-inline: 0.2em;
}

/* The word of the arrow ("to"): out of sight, but in the flow. Not the absolute-positioned visually-hidden pattern
   (wa-visually-hidden): a positioned box is blockified, and a copy may then put it on a line of its own. */
.timeline-arrow-text {
    display: inline-block;
    width: 1px;
    height: 1px;
    margin-inline-end: -1px;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
}
</style>
