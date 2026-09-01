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
 * sender-written message title, then the text preview.
 */
const props = defineProps({
    /** 'request' | 'message' */
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
        <div class="peer-toast-actions wa-light">
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
    color: var(--wa-color-brand-fill-loud, var(--wa-color-brand-60));
}

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
