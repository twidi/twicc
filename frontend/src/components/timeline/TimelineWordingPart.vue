<script setup>
// TimelineWordingPart.vue - One part of a timeline wording (utils/timeline/parts.js), rendered as TEXT, never as HTML:
// prose, a value (a session title, set apart from the prose), the arrow between an old and a new value (an icon with
// a text alternative), or a session mention.

import TimelineSessionMention from './TimelineSessionMention.vue'

defineProps({
    /** `{type: 'text'|'value', text}`, `{type: 'arrow', label}` or `{type: 'session', mention}`. */
    part: { type: Object, required: true },
})
</script>

<template>
    <span v-if="part.type === 'text'">{{ part.text }}</span>
    <span v-else-if="part.type === 'value'" class="timeline-value">{{ part.text }}</span>
    <!-- Real spaces around the arrow, not margins: a copy or a screen reader gets separate words. -->
    <template v-else-if="part.type === 'arrow'">{{ ' ' }}<wa-icon name="arrow-right" :label="part.label" auto-width class="timeline-arrow"></wa-icon>{{ ' ' }}</template>
    <TimelineSessionMention v-else :mention="part.mention" />
</template>

<style scoped>
/* A value from data (a session title, a setting value), set apart from the prose without quotes: the weight of the
   Orchestration card titles (the semibold token is too close to the prose). It wraps like the prose. */
.timeline-value {
    font-weight: var(--wa-font-weight-bold);
}

/* Between an old and a new value: on the text (lowered like Font Awesome inline icons), quiet. */
.timeline-arrow {
    vertical-align: -0.125em;
    color: var(--wa-color-text-quiet);
}
</style>
