<script setup>
/**
 * SessionToastContent - Rich content for session-related toast notifications.
 *
 * Displays the project badge and session title inside a CustomNotification,
 * replacing the plain text "Session: <title>" that was used before.
 *
 * Two independent auto-close criteria, opted into separately:
 * - dismissOnVisit: the user navigates to the session (any toast that points at
 *   a session the user can open)
 * - dismissOnRead: the session becomes read (e.g. viewed on another device) —
 *   only meaningful for the user_turn toast, whose whole subject is unread content
 *
 * Usage (via useToast):
 *   toast.session(sessionId, { type: 'success', title: 'Claude Code started' })
 */
import { computed, watch, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../../stores/data'
import { clearUserTurnToast, markSessionReadState } from '../../composables/useWebSocket'
import { stopSessionProcess } from '../../composables/useStopSessionProcess'
import { parseProcessError } from '../../utils/errorParsing'
import { sessionRouteLocation } from '../../utils/sessionRoute'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true,
    },
    errorMessage: {
        type: String,
        default: null,
    },
    /** When true, auto-dismiss once the user is viewing this session */
    dismissOnVisit: {
        type: Boolean,
        default: false,
    },
    /** When true, auto-dismiss once the session has no unread content */
    dismissOnRead: {
        type: Boolean,
        default: false,
    },
    /** When true, this toast holds the per-session user_turn slot and releases it on destroy */
    userTurnToast: {
        type: Boolean,
        default: false,
    },
    /** When true, show action buttons (Archive, Mark as read) alongside "Go to session" */
    showActions: {
        type: Boolean,
        default: false,
    },
    /** Notivue item reference — passed by CustomNotification to allow dismissing the toast */
    item: {
        type: Object,
        default: null,
    },
})

const route = useRoute()
const router = useRouter()
const store = useDataStore()

const session = computed(() => store.getSession(props.sessionId))
const processState = computed(() => store.processStates[props.sessionId])

// Use session data when available, fall back to processState (enriched by backend)
const projectId = computed(() => session.value?.project_id || processState.value?.project_id)


const sessionTitle = computed(() => session.value?.title || processState.value?.session_title || 'Unknown')

/** Parsed error info (clean message + optional HTTP status). */
const parsedError = computed(() => {
    if (!props.errorMessage) return null
    return parseProcessError(props.errorMessage)
})

/** Whether we're already viewing this session. */
const isCurrentSession = computed(() => route.params.sessionId === props.sessionId)

/** Whether the session has unread content. */
const isUnread = computed(() => {
    const s = session.value
    if (!s?.last_new_content_at) return false
    return !s.last_viewed_at || s.last_new_content_at > s.last_viewed_at
})

// Dismiss when the user navigates to this session: the toast only exists to
// bring them there, so reaching it — by the toast button or by hand (sidebar,
// search, …) — makes it pointless. immediate: true covers a toast pushed while
// the route already points at the session.
if (props.dismissOnVisit) {
    watch(isCurrentSession, (current) => {
        if (current) props.item?.clear?.()
    }, { immediate: true })
}

// Dismiss when session becomes read (e.g. marked as read on another device,
// or viewed on current device). immediate: true handles the case where the
// session is already read when the toast appears (e.g. race with session_updated broadcast).
// Never opt a pending-request toast into this: a request stays worth showing
// even when the session carries no unread content.
if (props.dismissOnRead) {
    watch(isUnread, (unread) => {
        if (!unread) props.item?.clear?.()
    }, { immediate: true })
}

// Release the per-session user_turn slot when the toast is destroyed (by any
// means: auto-close, manual dismiss, auto-dismiss)
onUnmounted(() => {
    if (props.userTurnToast) {
        clearUserTurnToast(props.sessionId)
    }
})

/**
 * Archive the session and dismiss the toast.
 *
 * Goes through the shared stop flow rather than archiving directly: the
 * session behind a toast is typically still running, so archiving it stops
 * its process — and that must show the active-crons confirmation like every
 * other archive gesture. The dialog is mounted globally in App.vue, so
 * dismissing the toast right away does not cancel it.
 */
function archiveSession() {
    const s = session.value
    if (s) {
        stopSessionProcess(s.id, { archive: true })
    }
    props.item?.clear?.()
}

/** Mark the session as read and dismiss the toast. */
function markRead() {
    markSessionReadState(props.sessionId, false)
    props.item?.clear?.()
}

/** Navigate to the session, preserving the current visual frame, then dismiss the toast. */
function goToSession() {
    if (!projectId.value) return
    // Keep the user's current frame (prefix mode + current project filter +
    // workspace): in single-project mode the session renders cross-filter rather
    // than switching the sidebar to its own project. projectId.value is only used
    // as the path's project in all-projects mode.
    router.push(sessionRouteLocation({ id: props.sessionId, project_id: projectId.value }, route))
    // Dismiss the toast
    props.item?.clear?.()
}
</script>

<template>
    <div class="session-toast-content">
        <span v-if="projectId" class="session-toast-row">
            <span class="session-toast-label">Project:</span>
            <ProjectBadge :project-id="projectId" class="session-toast-project" />
        </span>
        <span class="session-toast-session">
            <span class="session-toast-label">Session:</span>
            <span class="session-toast-title">{{ sessionTitle }}</span>
        </span>
        <span v-if="parsedError" class="session-toast-error" :title="errorMessage">
            {{ parsedError.summary }}<span v-if="parsedError.status" class="error-status"> ({{ parsedError.status }})</span>
        </span>
        <div v-if="!isCurrentSession" class="session-toast-actions">
            <wa-button v-if="showActions" size="small" variant="brand" appearance="outlined" @click="archiveSession">Archive</wa-button>
            <wa-button v-if="showActions" size="small" variant="brand" appearance="outlined" @click="markRead">Mark as read</wa-button>
            <wa-button size="small" variant="brand" appearance="outlined" @click="goToSession">Go to session</wa-button>
        </div>
    </div>
</template>

<style scoped>
.session-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

.session-toast-row {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
}

.session-toast-label {
    opacity: 0.8;
    flex-shrink: 0;
    width: 3rem;
}

.session-toast-project {
    font-weight: 700;
    min-width: 0;
}

.session-toast-session {
    display: flex;
    gap: var(--wa-space-s);
    margin-bottom: var(--wa-space-xs);
}

.session-toast-session .session-toast-label {
    align-self: flex-start;
}

.session-toast-title {
    font-weight: 700;
}

.session-toast-error {
    color: var(--wa-color-danger-on-quiet);
    font-weight: bold;
}

.error-status {
    font-weight: normal;
    opacity: 0.8;
}

.session-toast-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
