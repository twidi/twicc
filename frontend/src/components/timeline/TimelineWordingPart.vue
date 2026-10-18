<script setup>
// TimelineWordingPart.vue - One part of a timeline wording (utils/timeline/parts.js), rendered as TEXT, never as HTML:
// prose, a value (a session title, set apart from the prose), the arrow between an old and a new value (an icon with
// a text alternative), or a session mention.

import TimelineSessionMention from './TimelineSessionMention.vue'

defineProps({
    /** `{type: 'text'|'title', text}`, `{type: 'arrow', label}` or `{type: 'session', mention}`. */
    part: { type: Object, required: true },
})
</script>

<template>
    <span v-if="part.type === 'text'">{{ part.text }}</span>
    <span v-else-if="part.type === 'title'" class="timeline-value">{{ part.text }}</span>
    <wa-icon v-else-if="part.type === 'arrow'" name="arrow-right" :label="part.label" auto-width class="timeline-arrow"></wa-icon>
    <TimelineSessionMention v-else :mention="part.mention" />
</template>

<style scoped>
/* A value from data (a session title), set apart from the prose without quotes: the weight of the Orchestration
   card titles (the semibold token is too close to the prose). It wraps like the prose. */
.timeline-value {
    font-weight: var(--wa-font-weight-bold);
}

/* Between an old and a new value: on the text (lowered like Font Awesome inline icons), quiet. */
.timeline-arrow {
    margin-inline: 0.3em;
    vertical-align: -0.125em;
    color: var(--wa-color-text-quiet);
}
</style>
