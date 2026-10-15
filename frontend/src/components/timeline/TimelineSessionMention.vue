<script setup>
// TimelineSessionMention.vue - One mention of a session in the Timeline: provider icon, then the title,
// as a link or as plain text (display design sections 5.3 and 5.4). The same rules serve the group head,
// the "by <session>" author label and the parent of `session.created`.
//
// The `mention` comes from utils/timeline/sessions.js, which already decided link or not. Markers (archived,
// hidden) are shown on group heads only, through `markers`.

import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import ProviderIcon from '../ui/ProviderIcon.vue'

const props = defineProps({
    /** See `sessionMention` in utils/timeline/sessions.js. */
    mention: { type: Object, required: true },
    /** Show the archived / hidden markers (the group head only). */
    markers: { type: Boolean, default: false },
})

const route = useRoute()

const location = computed(() => (props.mention.link
    ? sessionRouteLocation({ id: props.mention.id, project_id: props.mention.projectId }, route)
    : null))
</script>

<template>
    <span class="timeline-session">
        <ProviderIcon v-if="mention.provider" :provider="mention.provider" class="timeline-session-icon" />
        <RouterLink v-if="location" :to="location" class="timeline-session-title">{{ mention.title }}</RouterLink>
        <span v-else class="timeline-session-title timeline-session-title--plain">{{ mention.title }}</span>
        <template v-if="markers">
            <wa-icon v-if="mention.archived" name="box-archive" label="Archived" title="Archived" class="timeline-marker"></wa-icon>
            <wa-icon v-if="mention.hidden" name="eye-slash" label="Hidden" title="Hidden" class="timeline-marker"></wa-icon>
        </template>
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

.timeline-marker {
    flex-shrink: 0;
    align-self: center;
    color: var(--wa-color-text-quiet);
    font-size: var(--wa-font-size-s);
}
</style>
