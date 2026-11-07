import { remoteUiMessage } from '../i18n/remoteMessages.js'
import { i18n } from '../i18n/index.js'
import { defineStore, acceptHMRUpdate } from 'pinia'

function browserMessage(message) {
    return { ...message, errorMessage: remoteUiMessage(message, message.error, 'error_ui_message') }
}

/**
 * Peers store — peer instances (cross-instance messaging) and their messages.
 *
 * Hydrated exclusively from the WS connect snapshot (`peers_updated` /
 * `peer_messages_updated`, share precedent) and kept live by the incremental
 * peer_* broadcasts. Messages here are SUMMARIES only (text preview +
 * attachments meta) — the full payload is fetched from REST by the review
 * dialog when needed.
 */
export const usePeersStore = defineStore('peers', {
    state: () => ({
        peers: [],      // serialize_peer rows (never tokens)
        messages: [],   // serialize_peer_message summaries
        loaded: false,
    }),

    getters: {
        pendingInboundMessages: (s) => s.messages.filter(message =>
            message.direction === 'in'
            && message.status === 'pending'
            && s.peers.find(peer => peer.id === message.peer_id)?.state !== 'revoked'
        ),
        pendingRequests: (s) => s.peers.filter(peer =>
            peer.state === 'pending_received' || peer.reconnect_direction === 'received'
        ),
        inboxCount() {
            return this.pendingInboundMessages.length + this.pendingRequests.length
        },
        getPeerById: (s) => (id) => s.peers.find(p => p.id === id) || null,
        /** Display name for a peer: local alias, else the claimed remote name, else its URL. */
        peerLabel() {
            return (peerId) => {
                const peer = this.getPeerById(peerId)
                return peer?.name || peer?.remote_display_name || peer?.base_url || i18n.global.t('session.peers.unknown')
            }
        },
    },

    actions: {
        /** Wholesale replace (WS connect snapshot). */
        applyPeers(peers) {
            this.peers = peers || []
            this.loaded = true
        },
        applyMessages(messages) {
            this.messages = (messages || []).map(browserMessage)
        },
        upsertPeer(peer) {
            if (!peer?.id) return
            const index = this.peers.findIndex(p => p.id === peer.id)
            if (index === -1) this.peers.push(peer)
            else this.peers.splice(index, 1, peer)
        },
        removePeer(peerId) {
            this.peers = this.peers.filter(p => p.id !== peerId)
            // Initial pending rows own no messages. Established rows are revoked, not removed.
            this.messages = this.messages.filter(m => m.peer_id !== peerId)
        },
        upsertMessage(message) {
            if (!message?.id) return
            message = browserMessage(message)
            const index = this.messages.findIndex(m => m.id === message.id)
            if (index === -1) this.messages.unshift(message)
            else this.messages.splice(index, 1, message)
        },
    },
})

if (import.meta.hot) {
    import.meta.hot.accept(acceptHMRUpdate(usePeersStore, import.meta.hot))
}
