<script setup>
/**
 * PeerToastContent - Actionable toast body for peer events.
 *
 * mode 'request': an incoming (or held) peer request — Review opens the
 * peers manager. mode 'message': an incoming peer message — "Open in inbox"
 * opens the inbox on that message, "Keep unread" only dismisses the toast.
 * Both dialogs are mounted once in App.vue and opened through window
 * CustomEvents, so the toast stays decoupled from them.
 *
 * mode 'request' names the peer the way the user does — the local name they
 * chose, and only without one the name the remote instance claims. It always
 * shows the address too: pairing is the moment identity is verified, and one
 * name can designate several instances.
 *
 * mode 'message' reading order (decision of 2026-08-11): WHO speaks is the
 * toast's own title ("Message from <peer>", set by the caller), then the
 * sender-written message title, then the text preview, then where the
 * message counts — session and project, read the inbox row's way
 * (`peerMessageRouting`: the message's own session, else the one its
 * thread names, else a bare project).
 *
 * mode 'status': one of the owner's messages was resolved on the other side
 * (the sentence is the toast's title). Only the routing line, no buttons:
 * five seconds is too short for one to matter.
 */
import { computed } from 'vue'
import { peerMessageRouting, peerRoutingSessionTitle } from '../../utils/peerMessageRouting'
import ProjectBadge from '../project/ProjectBadge.vue'

const props = defineProps({
    /** 'request' | 'message' | 'status' */
    mode: {
        type: String,
        required: true,
    },
    /** serialize_peer row (mode 'request') */
    peer: {
        type: Object,
        default: null,
    },
    /** serialize_peer_message summary (mode 'message') */
    message: {
        type: Object,
        default: null,
    },
    /** Notivue item reference — passed by CustomNotification to allow dismissing the toast */
    item: {
        type: Object,
        default: null,
    },
})

const routing = computed(() => peerMessageRouting(props.message))
const routingTitle = computed(() => peerRoutingSessionTitle(routing.value))

function review() {
    window.dispatchEvent(new CustomEvent('twicc:open-peers-manager'))
    props.item?.clear?.()
}

function read() {
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox', {
        detail: { messageId: props.message?.id },
    }))
    props.item?.clear?.()
}

function later() {
    props.item?.clear?.()
}
</script>

<template>
    <div class="peer-toast-content">
        <template v-if="mode === 'request' && peer">
            <span class="peer-toast-line">
                <wa-icon name="user-plus" class="peer-toast-icon"></wa-icon>
                <strong>{{ peer.name || peer.remote_display_name || 'An instance' }}</strong>
                &nbsp;wants to pair with your instance
            </span>
            <span class="peer-toast-url">{{ peer.base_url }}</span>
            <span class="peer-toast-hint">Review the request to see its verification code.</span>
        </template>
        <template v-else-if="mode === 'message' && message">
            <span v-if="message.title" class="peer-toast-title">{{ message.title }}</span>
            <span v-if="message.text_preview" class="peer-toast-detail">
                {{ message.text_preview }}
            </span>
        </template>
        <!-- Where the message counts: session “…” in ●project, or the project
             alone. Same reading as the inbox row, in one line. -->
        <span v-if="routing" class="peer-toast-route">
            <template v-if="routing.sessionId">
                <span class="peer-toast-route__label">session</span>
                <span class="peer-toast-route__title" :title="routing.sessionTitle">“{{ routingTitle }}”</span>
            </template>
            <template v-if="routing.projectId">
                <span class="peer-toast-route__label">in</span>
                <ProjectBadge :project-id="routing.projectId" class="peer-toast-route__project" />
            </template>
        </span>
        <div v-if="mode !== 'status'" class="peer-toast-actions">
            <template v-if="mode === 'request'">
                <wa-button
                    size="small" variant="brand" appearance="outlined"
                    @click="review"
                >Review</wa-button>
                <wa-button size="small" variant="neutral" appearance="outlined" @click="later">Later</wa-button>
            </template>
            <template v-else>
                <wa-button
                    size="small" variant="brand" appearance="outlined"
                    @click="read"
                >Open in inbox</wa-button>
                <wa-button size="small" variant="neutral" appearance="outlined" @click="later">Keep unread</wa-button>
            </template>
        </div>
    </div>
</template>

<style scoped>
.peer-toast-content {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    margin-top: var(--wa-space-xs);
}

/* The sender-written subject, between the toast title (the peer) and the
   preview. One line — a toast has no room for a wrapping subject. */
.peer-toast-title {
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.peer-toast-detail {
    opacity: 0.9;
    overflow: hidden;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
}

.peer-toast-line {
    display: flex;
    align-items: center;
    font-size: var(--wa-font-size-m);
}

.peer-toast-icon {
    margin-right: var(--wa-space-xs);
    color: var(--wa-color-brand-on-quiet);
}

.peer-toast-route {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--wa-space-2xs);
    font-size: 0.85rem;
    min-width: 0;
}
.peer-toast-route__label { opacity: 0.8; flex-shrink: 0; }
.peer-toast-route__title {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    min-width: 6ch;
}
.peer-toast-route__project { max-width: 20ch; }

.peer-toast-url {
    font-family: var(--wa-font-family-code, monospace);
    font-size: 0.8rem;
    opacity: 0.85;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}

.peer-toast-hint {
    font-size: 0.8rem;
    opacity: 0.75;
}

.peer-toast-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--wa-space-xs);
}
</style>
