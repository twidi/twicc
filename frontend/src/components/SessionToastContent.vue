<script setup>
/**
 * SessionToastContent - Rich content for session-related toast notifications.
 *
 * Displays the project badge and session title inside a CustomNotification,
 * replacing the plain text "Session: <title>" that was used before.
 *
 * When autoDismiss is true (used for user_turn toasts), the toast auto-closes when:
 * - The user navigates to the session
 * - The session becomes read (e.g. viewed on another device)
 *
 * Usage (via useToast):
 *   toast.session(sessionId, { type: 'success', title: 'Claude Code started' })
 */
import { computed, watch, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDataStore } from '../stores/data'
import { clearUserTurnToast, markSessionReadState } from '../composables/useWebSocket'
import ProjectBadge from './ProjectBadge.vue'

const props = defineProps({
    sessionId: {
        type: String,
        required: true,
    },
    errorMessage: {
        type: String,
        default: null,
    },
    /** When true, auto-dismiss when navigating to session or session becomes read */
    autoDismiss: {
        type: Boolean,
        default: false,
    },
    /** When true, show a "Mark as read" button alongside "Go to session" */
    showMarkRead: {
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

/** Whether we're already viewing this session. */
const isCurrentSession = computed(() => route.params.sessionId === props.sessionId)

/** Whether the session has unread content. */
const isUnread = computed(() => {
    const s = session.value
    if (!s?.last_new_content_at) return false
    return !s.last_viewed_at || s.last_new_content_at > s.last_viewed_at
})

// Auto-dismiss watchers (only active when autoDismiss is true)
if (props.autoDismiss) {
    // Dismiss when user navigates to this session
    watch(isCurrentSession, (current) => {
        if (current) props.item?.clear?.()
    }, { immediate: true })

    // Dismiss when session becomes read (e.g. marked as read on another device,
    // or viewed on current device). immediate: true handles the case where the
    // session is already read when the toast appears (e.g. race with session_updated broadcast).
    watch(isUnread, (unread) => {
        if (!unread) props.item?.clear?.()
    }, { immediate: true })
}

// Clean up tracking when toast is destroyed (by any means: auto-close, manual dismiss, auto-dismiss)
onUnmounted(() => {
    if (props.autoDismiss) {
        clearUserTurnToast(props.sessionId)
    }
})

/** Mark the session as read and dismiss the toast. */
function markRead() {
    markSessionReadState(props.sessionId, false)
    props.item?.clear?.()
}

/** Navigate to the session, switching project if needed, then dismiss the toast. */
function goToSession() {
    if (!projectId.value) return

    const isAllProjectsMode = route.name?.startsWith('projects-')

    if (isAllProjectsMode) {
        router.push({
            name: 'projects-session',
            params: { projectId: projectId.value, sessionId: props.sessionId },
        })
    } else {
        // Single-project mode: router.push will switch project automatically
        // if the session belongs to a different project
        router.push({
            name: 'session',
            params: { projectId: projectId.value, sessionId: props.sessionId },
        })
    }

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
        <span v-if="errorMessage" class="session-toast-error">{{ errorMessage }}</span>
        <div v-if="!isCurrentSession" class="session-toast-actions">
            <wa-button v-if="showMarkRead" size="small" variant="brand" appearance="outlined" @click="markRead">Mark as read</wa-button>
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
    color: var(--wa-color-danger);
    font-weight: bold;
}

.session-toast-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
