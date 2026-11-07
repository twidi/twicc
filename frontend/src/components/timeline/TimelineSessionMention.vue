<script setup>
// TimelineSessionMention.vue - One mention of a session in the Timeline: provider icon, then the title,
// as a link or as plain text (display design sections 5.3 and 5.4). The same rules serve the block header,
// the "by <session>" author label and the parent of `session.created`.
//
// A title that is cut (`truncate`, `ellipsis`) has the full title as tooltip with a mouse and scrolls under the finger
// on a touch screen (ScrollableText, as in the session list and the session header).
//
// The `mention` comes from utils/timeline/sessions.js, which already decided link or not. The block header
// uses `truncate`: the provider icon in a fixed box, the title on one line with an ellipsis and the full
// title as tooltip. Only block headers supply current session state for the status icons: archived, muted and
// pinned before the title, the live process state after it (same rules as the compact session list).

import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import { isSessionUnread } from '../../utils/sessions'
import { processStateTooltip, userTurnBackgroundShellCount } from '../../utils/backgroundWork'
import { getProviderLabel } from '../../providers'
import { useDataStore } from '../../stores/data'
import ProviderIcon from '../ui/ProviderIcon.vue'
import ProcessIndicator from '../ui/ProcessIndicator.vue'
import ScrollableText from '../ui/ScrollableText.vue'

const props = defineProps({
    /** See `sessionMention` in utils/timeline/sessions.js. */
    mention: { type: Object, required: true },
    /** Current session state, supplied only by the block header. Never inferred from an event. */
    sessionState: { type: Object, default: null },
    /** One line with an ellipsis, the full title as tooltip, the icon in a fixed box (the block header). */
    truncate: { type: Boolean, default: false },
    /** One line with an ellipsis and the full title as tooltip, the icon inline (the "by <session>" author line). */
    ellipsis: { type: Boolean, default: false },
})

const route = useRoute()
const store = useDataStore()

const location = computed(() => (props.mention.link
    ? sessionRouteLocation({ id: props.mention.id, project_id: props.mention.projectId }, route)
    : null))
const cut = computed(() => props.truncate || props.ellipsis)
const tooltip = computed(() => (cut.value ? props.mention.title : null))

// The process indicator of the block header. A dead process shows nothing. The unread eye of the session being
// viewed is hidden, as in the session list.
const sessionId = computed(() => (props.sessionState ? props.mention.id : null))
const processState = computed(() => (sessionId.value ? store.getProcessState(sessionId.value) : null))
const hasUnread = computed(() => sessionId.value !== null
    && route.params.sessionId !== sessionId.value
    && isSessionUnread(props.sessionState, processState.value))
const pendingRequest = computed(() => sessionId.value !== null && store.getPendingRequests(sessionId.value).length > 0)
const liveProcess = computed(() => !!processState.value && processState.value.state !== 'dead' && !props.sessionState.ephemeral)
const showProcess = computed(() => liveProcess.value && (hasUnread.value || !pendingRequest.value))
const showPending = computed(() => liveProcess.value && !hasUnread.value && pendingRequest.value)
const showUnreadOnly = computed(() => !processState.value && hasUnread.value)
const processTooltip = computed(() => (processState.value
    ? processStateTooltip(getProviderLabel(props.sessionState.provider), processState.value)
    : ''))
</script>

<template>
    <span class="timeline-session" :class="{ 'timeline-session--truncate': truncate, 'timeline-session--ellipsis': ellipsis }">
        <!-- The box keeps its width with no icon, so the title start (and the event lines under it) never moves. -->
        <span v-if="truncate" class="timeline-session-lead">
            <ProviderIcon v-if="mention.provider" :provider="mention.provider" class="timeline-session-icon" />
        </span>
        <ProviderIcon v-else-if="mention.provider" :provider="mention.provider" class="timeline-session-icon" />
        <span v-if="sessionState && (sessionState.archived || sessionState.mute_on_user_turn || sessionState.pinned)" class="timeline-session-states">
            <wa-icon v-if="sessionState.archived" name="box-archive" :label="$t('common.status.archived')" :title="$t('common.status.archived')" class="session-state-icon session-state-icon--archived"></wa-icon>
            <wa-icon v-if="sessionState.mute_on_user_turn" name="bell-slash" :label="$t('session.notifications.mutedLabel')" :title="$t('session.notifications.mutedLabel')" class="session-state-icon timeline-session-state--muted"></wa-icon>
            <wa-icon v-if="sessionState.pinned" name="thumbtack" :label="$t('timeline.session.pinned')" :title="$t('timeline.session.pinned')" class="session-state-icon timeline-session-state--pinned"></wa-icon>
        </span>
        <RouterLink v-if="location" :to="location" class="timeline-session-title" :title="tooltip"><ScrollableText v-if="cut" :text="mention.title" /><template v-else>{{ mention.title }}</template></RouterLink>
        <span v-else class="timeline-session-title timeline-session-title--plain" :title="tooltip"><ScrollableText v-if="cut" :text="mention.title" /><template v-else>{{ mention.title }}</template></span>
        <ProcessIndicator
            v-if="showProcess"
            :state="processState.state"
            :has-active-crons="processState.active_crons?.length > 0"
            :background-shells="userTurnBackgroundShellCount(processState)"
            :unread="hasUnread"
            size="small"
            :title="hasUnread ? $t('session.list.unreadProcess', { status: processTooltip }) : processTooltip"
            class="timeline-session-process"
        />
        <wa-icon v-else-if="showPending" name="hand" :label="$t('session.status.awaitingResponse')" :title="$t('session.status.awaitingResponse')" class="timeline-session-process timeline-session-pending"></wa-icon>
        <wa-icon v-else-if="showUnreadOnly" name="eye" :label="$t('timeline.session.unread')" :title="$t('timeline.session.unread')" class="timeline-session-process timeline-session-unread"></wa-icon>
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

.timeline-session-states {
    display: inline-flex;
    align-items: center;
    flex: none;
    gap: var(--wa-space-2xs);
    font-size: 0.75em;
}

.timeline-session-state--muted {
    color: var(--wa-color-warning-60);
}

.timeline-session-state--pinned {
    color: var(--wa-color-yellow-80);
    transform: rotate(30deg);
}

.timeline-session-title {
    font-weight: var(--wa-font-weight-semibold);
    overflow-wrap: anywhere;
    color: inherit;
}

.timeline-session-process {
    flex: none;
    align-self: center;
}

.timeline-session-pending,
.timeline-session-unread {
    color: var(--wa-color-warning-60);
    font-size: var(--wa-font-size-xs);
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

/* The cut is drawn by ScrollableText inside the title. */
.timeline-session--ellipsis .timeline-session-title {
    display: block;
    min-width: 0;
    overflow-wrap: normal;
}

.timeline-session--truncate .timeline-session-title {
    display: block;
    min-width: 0;
    overflow-wrap: normal;
}

.timeline-session-lead {
    flex: none;
    display: inline-flex;
    justify-content: center;
    width: var(--timeline-provider-width, 1.25rem);
}
</style>
