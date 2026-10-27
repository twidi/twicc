<script setup>
// TimelineSessionMention.vue - One mention of a session in the Timeline: provider icon, then the title,
// as a link or as plain text (display design sections 5.3 and 5.4). The same rules serve the block header,
// the "by <session>" author label and the parent of `session.created`.
//
// The `mention` comes from utils/timeline/sessions.js, which already decided link or not. The block header
// uses `truncate`: the provider icon in a fixed box, the title on one line with an ellipsis and the full
// title as tooltip. No state marker (archived, hidden) is shown: the current state is not an event.

import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import ProviderIcon from '../ui/ProviderIcon.vue'

const props = defineProps({
    /** See `sessionMention` in utils/timeline/sessions.js. */
    mention: { type: Object, required: true },
    /** One line with an ellipsis, the full title as tooltip, the icon in a fixed box (the block header). */
    truncate: { type: Boolean, default: false },
    /** One line with an ellipsis and the full title as tooltip, the icon inline (the "by <session>" author line). */
    ellipsis: { type: Boolean, default: false },
})

const route = useRoute()

const location = computed(() => (props.mention.link
    ? sessionRouteLocation({ id: props.mention.id, project_id: props.mention.projectId }, route)
    : null))
const tooltip = computed(() => (props.truncate || props.ellipsis ? props.mention.title : null))
</script>

<template>
    <span class="timeline-session" :class="{ 'timeline-session--truncate': truncate, 'timeline-session--ellipsis': ellipsis }">
        <!-- The box keeps its width with no icon, so the title start (and the event lines under it) never moves. -->
        <span v-if="truncate" class="timeline-session-lead">
            <ProviderIcon v-if="mention.provider" :provider="mention.provider" class="timeline-session-icon" />
        </span>
        <ProviderIcon v-else-if="mention.provider" :provider="mention.provider" class="timeline-session-icon" />
        <RouterLink v-if="location" :to="location" class="timeline-session-title" :title="tooltip">{{ mention.title }}</RouterLink>
        <span v-else class="timeline-session-title timeline-session-title--plain" :title="tooltip">{{ mention.title }}</span>
    </span>
</template>

<style scoped>
.timeline-session {
    display: inline-flex;
    align-items: baseline;
    gap: var(--wa-space-2xs);
    min-width: 0;
    max-width: 100%;
}

.timeline-session-icon {
    flex-shrink: 0;
    align-self: center;
}

.timeline-session-title {
    font-weight: var(--wa-font-weight-semibold);
    overflow-wrap: anywhere;
    color: inherit;
}

a.timeline-session-title {
    text-decoration: none;
}

a.timeline-session-title:hover {
    text-decoration: underline;
}

/* The width and the gap come from the row (TimelineEventRow), which indents its event lines by their sum. */
.timeline-session--truncate {
    align-items: center;
    gap: var(--timeline-provider-gap, var(--wa-space-xs));
}

/* The author line: the title shrinks first, the icon and the text around the mention keep their size. */
.timeline-session--ellipsis {
    flex: 0 1 auto;
}

.timeline-session--ellipsis .timeline-session-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow-wrap: normal;
}

.timeline-session-lead {
    flex: none;
    display: inline-flex;
    justify-content: center;
    width: var(--timeline-provider-width, 1.25rem);
}

.timeline-session--truncate .timeline-session-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    overflow-wrap: normal;
}
</style>
