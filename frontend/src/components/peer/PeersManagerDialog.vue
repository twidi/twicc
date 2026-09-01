<script setup>
/**
 * PeersManagerDialog - manage peer instances (cross-instance messaging).
 *
 * Mounted once in App.vue, opened by the `twicc:open-peers-manager`
 * CustomEvent (settings section, toasts, inbox). REST mutations normally reach
 * the store through WS broadcasts. A manual GET resolves unknown results.
 *
 * Rendering rule: handshake-supplied strings (remote_display_name, base_url)
 * come from an unauthenticated endpoint and are attacker-controlled — text
 * interpolation ONLY, never v-html.
 */
import { ref, computed, watch } from 'vue'
import { usePeersStore } from '../../stores/peers'
import { useSettingsStore } from '../../stores/settings'
import { apiFetch } from '../../utils/api'
import { mutatePeer, reloadPeers } from '../../utils/peerManagerRequests'
import PeerHelpLink from './PeerHelpLink.vue'

const props = defineProps({ open: Boolean })
const emit = defineEmits(['close'])

const peersStore = usePeersStore()
const settingsStore = useSettingsStore()
const dialogRef = ref(null)

const peerBaseUrl = computed(() => settingsStore.getUsablePeerBaseUrl)
const peerDisplayName = computed(() => settingsStore.getPeerDisplayName || '')
const pendingReceived = computed(() => peersStore.peers.filter(p => p.state === 'pending_received'))
const pendingSent = computed(() => peersStore.peers.filter(p => p.state === 'pending_sent'))
const reconnectReceived = computed(() => peersStore.peers.filter(p => p.reconnect_direction === 'received'))
const reconnectSent = computed(() => peersStore.peers.filter(p => p.reconnect_direction === 'sent'))
const incomingRequests = computed(() => [...pendingReceived.value, ...reconnectReceived.value])
const sentRequests = computed(() => [...pendingSent.value, ...reconnectSent.value])
const establishedPeers = computed(() => peersStore.peers.filter(p =>
    ['active', 'broken', 'revoked'].includes(p.state) && !p.reconnect_direction
))

// Per-peer transient UI state (inputs, errors, busy flags), keyed by peer id.
const acceptNames = ref({})

// Local-name input for an incoming request: seeded with the locally-chosen
// alias (crossed rows) or the name the requester claims — the user just
// confirms or adjusts it before accepting. `??` keeps a deliberate clear.
function acceptNameValue(peer) {
    return acceptNames.value[peer.id] ?? (peer.name || peer.remote_display_name || '')
}
const verifyCodes = ref({})
const rowErrors = ref({})
const busy = ref({})
const confirmingRemoval = ref(null)
const renaming = ref(null)
const renameValue = ref('')

// Add-peer form state.
const addName = ref('')
const addUrl = ref('')
const addError = ref('')
const addBusy = ref(false)
const unknownResult = ref(false)
const reloadBusy = ref(false)

// Reset transient state on every open. A confirmation armed during a previous
// visit must not survive a dismissal.
watch(() => props.open, (open) => {
    if (!open) return
    confirmingRemoval.value = null
    renaming.value = null
    rowErrors.value = {}
    addError.value = ''
})

function errorText(payload) {
    const errors = payload?.errors
    if (Array.isArray(errors) && errors.length) {
        return errors.map(e => e.message || e.code).join(' — ')
    }
    return payload?.error || payload?.reason || 'Request failed.'
}

async function post(url, body) {
    const result = await mutatePeer(apiFetch, url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    })
    if (result.unknown) unknownResult.value = true
    return result
}

async function reloadPeerList() {
    reloadBusy.value = true
    try {
        const ok = await reloadPeers(apiFetch, peers => peersStore.applyPeers(peers))
        if (ok) unknownResult.value = false
    } finally {
        reloadBusy.value = false
    }
}

async function addPeer() {
    addError.value = ''
    const name = addName.value.trim()
    const url = addUrl.value.trim()
    if (!name || !url) {
        addError.value = 'Name and address are both required.'
        return
    }
    addBusy.value = true
    try {
        const { ok, payload, unknown } = await post('/api/peers/', { name, base_url: url })
        if (!ok && !unknown) {
            addError.value = errorText(payload)
            return
        }
        if (!ok) return
        addName.value = ''
        addUrl.value = ''
    } finally {
        addBusy.value = false
    }
}

async function acceptPeer(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const name = acceptNameValue(peer).trim() || peer.name || peer.remote_display_name
        const { ok, payload, unknown } = await post(`/api/peers/${peer.id}/accept/`, { name })
        if (!ok && !unknown) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function refusePeer(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const { ok, payload, unknown } = await post(`/api/peers/${peer.id}/refuse/`)
        if (!ok && !unknown) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function submitCode(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    const code = (verifyCodes.value[peer.id] || '').trim()
    if (!/^\d{6}$/.test(code)) {
        rowErrors.value = { ...rowErrors.value, [peer.id]: 'Enter the 6-digit code your peer reads to you.' }
        return
    }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const { ok, payload, unknown } = await post(`/api/peers/${peer.id}/verify/`, { code })
        if (!ok && !unknown) {
            rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
        } else if (ok) {
            verifyCodes.value = { ...verifyCodes.value, [peer.id]: '' }
        }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function removePeer(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const { ok, payload, unknown } = await mutatePeer(
            apiFetch, `/api/peers/${peer.id}/`, { method: 'DELETE' },
        )
        if (unknown) unknownResult.value = true
        if (ok) confirmingRemoval.value = null
        else if (!unknown) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

function startRename(peer) {
    renaming.value = peer.id
    renameValue.value = peer.name
}

async function _patch(peer, body) {
    const { ok, payload, unknown } = await mutatePeer(apiFetch, `/api/peers/${peer.id}/`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
    if (unknown) unknownResult.value = true
    else if (!ok) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
}

async function applyRename(peer) {
    const name = renameValue.value.trim()
    renaming.value = null
    if (!name || name === peer.name) return
    await _patch(peer, { name })
}

async function reconnectPeer(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const { ok, payload, unknown } = await post(`/api/peers/${peer.id}/reconnect/`)
        if (!ok && !unknown) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

async function cancelReconnect(peer) {
    rowErrors.value = { ...rowErrors.value, [peer.id]: '' }
    busy.value = { ...busy.value, [peer.id]: true }
    try {
        const { ok, payload, unknown } = await post(`/api/peers/${peer.id}/reconnect/cancel/`)
        if (!ok && !unknown) rowErrors.value = { ...rowErrors.value, [peer.id]: errorText(payload) }
    } finally {
        busy.value = { ...busy.value, [peer.id]: false }
    }
}

function openInbox() {
    emit('close')
    window.dispatchEvent(new CustomEvent('twicc:open-peer-inbox'))
}

function composeTo(peer) {
    // This dialog is being REPLACED, not dismissed: wa-dialog would restore
    // focus to its own trigger on a `setTimeout`, landing after the composer
    // has focused its first field and stealing it (CommandPalette.vue hits the
    // same internal). Clearing the trigger makes that restoration a no-op.
    if (dialogRef.value) dialogRef.value.originalTrigger = null
    emit('close')
    window.dispatchEvent(new CustomEvent('twicc:open-peer-compose', {
        detail: { peerId: peer.id, returnTo: 'manager' },
    }))
}

function brokenReasonText(reason) {
    return {
        remote_credential_rejected: 'Remote credentials rejected',
        local_address_changed: 'Local address was changed',
        local_address_disabled: 'Local address was disabled',
    }[reason] || reason
}

// Scope dialog events to the dialog itself — nested wa-* children bubble the
// same event names (CLAUDE.md "Bubbling custom events").
// No auto-focus on open: the Add-peer form sits at the BOTTOM of the dialog,
// and focusing it would scroll the incoming request's verification code (the
// usual reason to open this dialog) out of view.
function onHide(event) {
    if (event.target !== dialogRef.value) return
    emit('close')
}
</script>

<template>
    <wa-dialog
        ref="dialogRef" :open="open" label="Peers"
        style="--width: min(760px, calc(100vw - 2rem))"
        @wa-hide="onHide"
    >
        <div slot="label" class="peer-dialog-title">
            <span>Peers</span>
            <PeerHelpLink />
        </div>

        <!-- Own address recap -->
        <wa-callout v-if="!peerBaseUrl" variant="warning" size="small" class="pm-block">
            Peer messaging is disabled — set your address in Settings › Peers first.
        </wa-callout>
        <p v-else class="pm-own-address">
            Your address: <code>{{ peerBaseUrl }}</code>
            <template v-if="peerDisplayName"> · shown as <strong>{{ peerDisplayName }}</strong></template>
        </p>

        <wa-callout v-if="unknownResult" variant="warning" size="small" class="pm-block">
            <div class="pm-confirm-body">
                <span>The request result is unknown. Reload peers to read the current server state.</span>
                <wa-button size="small" :loading="reloadBusy" @click="reloadPeerList">Reload peers</wa-button>
            </div>
        </wa-callout>

        <!-- Pending incoming requests -->
        <template v-if="incomingRequests.length">
            <h4 class="pm-section-title">Incoming requests</h4>
            <div v-for="peer in incomingRequests" :key="peer.id" class="pm-request">
                <div class="pm-request__claim">
                    <span class="pm-request__name">{{ peer.remote_display_name || 'unnamed instance' }}</span>
                    <span class="pm-request__url">{{ peer.base_url }}</span>
                    <wa-tag v-if="peer.reconnect_direction" size="small">Reconnect</wa-tag>
                </div>
                <div class="pm-request__code-block">
                    <span class="pm-request__code">{{ peer.verification_code }}</span>
                    <wa-tag v-if="peer.verified_at" variant="success" size="small">Verified ✓</wa-tag>
                </div>
                <p class="pm-hint">
                    Share this code with the requester over a channel you trust — accepting
                    unlocks once they confirm it.
                </p>
                <!-- Crossed handshake: this row also carries OUR outbound request.
                     Verification is symmetric — the peer shows a code too, and it
                     must be enterable HERE or the crossed flow deadlocks. -->
                <template v-if="peer.crossed">
                    <wa-tag v-if="peer.code_confirmed_at" variant="success" size="small">Code confirmed ✓</wa-tag>
                    <template v-else>
                        <div class="pm-request__actions">
                            <wa-input
                                class="pm-code-input"
                                size="small" placeholder="6-digit code" maxlength="6" inputmode="numeric"
                                :value="verifyCodes[peer.id] || ''"
                                @input="verifyCodes = { ...verifyCodes, [peer.id]: $event.target.value }"
                                @keydown.enter="submitCode(peer)"
                            ></wa-input>
                            <wa-button
                                size="small" variant="brand"
                                :disabled="busy[peer.id]"
                                @click="submitCode(peer)"
                            >Verify</wa-button>
                        </div>
                        <p class="pm-hint">You both sent a request — also enter the code your peer reads to you.</p>
                    </template>
                </template>
                <div class="pm-request__actions">
                    <wa-input
                        size="small" placeholder="Local name for this peer"
                        :value="acceptNameValue(peer)"
                        @input="acceptNames = { ...acceptNames, [peer.id]: $event.target.value }"
                    ></wa-input>
                    <wa-button
                        size="small" variant="brand"
                        :disabled="!peer.verified_at || busy[peer.id]"
                        @click="acceptPeer(peer)"
                    >Accept</wa-button>
                    <wa-button
                        size="small" variant="danger" appearance="outlined"
                        :disabled="busy[peer.id]"
                        @click="refusePeer(peer)"
                    >Refuse</wa-button>
                </div>
                <wa-callout v-if="rowErrors[peer.id]" variant="danger" size="small">{{ rowErrors[peer.id] }}</wa-callout>
            </div>
        </template>

        <!-- Pending sent requests -->
        <template v-if="sentRequests.length">
            <h4 class="pm-section-title">Sent requests</h4>
            <p v-if="reconnectSent.length" class="pm-hint">
                If both instances show a sent reconnect, cancel one side and retry the other.
            </p>
            <div v-for="peer in sentRequests" :key="peer.id" class="pm-request">
                <div class="pm-request__claim">
                    <span class="pm-request__name">{{ peer.name }}</span>
                    <span class="pm-request__url">{{ peer.base_url }}</span>
                    <wa-tag v-if="peer.reconnect_direction" size="small">Reconnect</wa-tag>
                </div>
                <wa-callout
                    v-if="peer.remote_accepted_at && !peer.code_confirmed_at"
                    variant="warning" size="small"
                >
                    Accepted remotely, but your code verification hasn't completed —
                    contact your peer before trusting this relationship.
                </wa-callout>
                <template v-if="peer.code_confirmed_at">
                    <wa-tag variant="success" size="small">Code confirmed ✓</wa-tag>
                </template>
                <template v-else>
                    <div class="pm-request__actions">
                        <wa-input
                            class="pm-code-input"
                            size="small" placeholder="6-digit code" maxlength="6" inputmode="numeric"
                            :value="verifyCodes[peer.id] || ''"
                            @input="verifyCodes = { ...verifyCodes, [peer.id]: $event.target.value }"
                            @keydown.enter="submitCode(peer)"
                        ></wa-input>
                        <wa-button
                            size="small" variant="brand"
                            :disabled="busy[peer.id]"
                            @click="submitCode(peer)"
                        >Verify</wa-button>
                    </div>
                    <p class="pm-hint">Enter the code your peer reads to you.</p>
                </template>
                <div class="pm-request__actions">
                    <wa-button
                        v-if="peer.reconnect_direction === 'sent'"
                        size="small" appearance="outlined"
                        :disabled="busy[peer.id]"
                        @click="reconnectPeer(peer)"
                    >Retry</wa-button>
                    <wa-button
                        size="small" variant="danger" appearance="outlined"
                        :disabled="busy[peer.id]"
                        @click="peer.reconnect_direction ? cancelReconnect(peer) : confirmingRemoval = peer.id"
                    >Cancel</wa-button>
                </div>
                <wa-callout v-if="confirmingRemoval === peer.id" variant="warning" size="small">
                    <div class="pm-confirm-body">
                        <span>Remove this pending request?</span>
                        <span class="pm-confirm__actions">
                            <wa-button size="small" variant="danger" @click="removePeer(peer)">Remove</wa-button>
                            <wa-button size="small" appearance="outlined" @click="confirmingRemoval = null">Keep</wa-button>
                        </span>
                    </div>
                </wa-callout>
                <wa-callout v-if="rowErrors[peer.id]" variant="danger" size="small">{{ rowErrors[peer.id] }}</wa-callout>
            </div>
        </template>

        <!-- Established peers -->
        <h4 class="pm-section-title">Peers</h4>
        <p v-if="!establishedPeers.length" class="pm-empty">No peers yet.</p>
        <div v-for="peer in establishedPeers" :key="peer.id" class="pm-peer">
            <div class="pm-peer__main">
                <template v-if="renaming === peer.id">
                    <wa-input
                        size="small" :value="renameValue"
                        @input="renameValue = $event.target.value"
                        @keydown.enter="applyRename(peer)"
                    ></wa-input>
                    <wa-button size="small" @click="applyRename(peer)">OK</wa-button>
                </template>
                <template v-else>
                    <span class="pm-peer__name">{{ peer.name || peer.remote_display_name || 'unnamed' }}</span>
                    <wa-tag :variant="peer.state === 'active' ? 'success' : 'danger'" size="small">
                        {{ peer.state }}
                    </wa-tag>
                    <wa-relative-time
                        v-if="peer.last_contact_at"
                        :date.prop="new Date(peer.last_contact_at)"
                        class="pm-peer__contact"
                    ></wa-relative-time>
                </template>
            </div>
            <div class="pm-peer__url-row">
                <span class="pm-peer__url">{{ peer.base_url }}</span>
                <span v-if="peer.broken_reason" class="pm-peer__reason">
                    {{ brokenReasonText(peer.broken_reason) }}
                </span>
            </div>
            <div class="pm-peer__actions">
                <wa-button
                    v-if="peer.state === 'active'"
                    size="small" variant="brand" appearance="plain"
                    @click="composeTo(peer)"
                >
                    <wa-icon name="paper-plane" slot="start"></wa-icon>
                    Send message
                </wa-button>
                <wa-button size="small" appearance="plain" @click="startRename(peer)">Rename</wa-button>
                <wa-button
                    v-if="peer.state !== 'active'"
                    size="small" variant="brand" appearance="plain"
                    :disabled="busy[peer.id] || !peerBaseUrl"
                    @click="reconnectPeer(peer)"
                >Reconnect</wa-button>
                <wa-button
                    v-if="peer.state !== 'revoked'"
                    size="small" variant="danger" appearance="plain"
                    @click="confirmingRemoval = peer.id"
                >Revoke</wa-button>
            </div>
            <wa-callout v-if="confirmingRemoval === peer.id" variant="warning" size="small">
                <div class="pm-confirm-body">
                    <span>
                        Revokes the relationship silently and preserves its message history.
                        The peer is rejected on its next credential request.
                    </span>
                    <span class="pm-confirm__actions">
                        <wa-button size="small" variant="danger" :disabled="busy[peer.id]" @click="removePeer(peer)">Revoke</wa-button>
                        <wa-button size="small" appearance="outlined" @click="confirmingRemoval = null">Keep</wa-button>
                    </span>
                </div>
            </wa-callout>
            <wa-callout v-if="rowErrors[peer.id]" variant="danger" size="small">{{ rowErrors[peer.id] }}</wa-callout>
        </div>

        <!-- Add peer -->
        <h4 class="pm-section-title">Add a peer</h4>
        <form id="pm-add-form" class="pm-add-form" @submit.prevent="addPeer">
            <wa-input
                size="small" placeholder="Name (e.g. David)"
                :value="addName" @input="addName = $event.target.value"
            ></wa-input>
            <wa-input
                size="small" placeholder="https://their-instance.example.com"
                :value="addUrl" @input="addUrl = $event.target.value"
            ></wa-input>
            <wa-button size="small" variant="brand" type="submit" :disabled="addBusy || !peerBaseUrl">
                <wa-icon name="plus" slot="start"></wa-icon>
                Send request
            </wa-button>
        </form>
        <wa-callout v-if="addError" variant="danger" size="small">{{ addError }}</wa-callout>

        <div slot="footer" class="pm-footer">
            <wa-button appearance="outlined" @click="openInbox">
                <wa-icon name="envelope" slot="start"></wa-icon>
                Inbox
            </wa-button>
            <wa-button @click="emit('close')">Close</wa-button>
        </div>
    </wa-dialog>
</template>

<style scoped>
.peer-dialog-title {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--wa-space-3xs);
}
/* Callouts sitting directly in the dialog body are block-flow siblings, and
   which one renders depends on state — so the separation belongs to the
   callout, never to the block that happens to precede it. Adjacent margins
   collapse, so two stacked callouts still keep a single gap. Row-level
   callouts are nested in a flex row and keep that row's gap instead. */
wa-dialog > wa-callout { margin-block: var(--wa-space-s); }

.pm-block { margin-bottom: var(--wa-space-m); }
.pm-own-address {
    margin: 0 0 var(--wa-space-m);
    color: var(--wa-color-text-quiet);
}
.pm-own-address code { user-select: all; }
.pm-section-title {
    margin: var(--wa-space-l) 0 var(--wa-space-s);
    border-bottom: 2px solid var(--wa-color-surface-border);
    padding-bottom: 0.35rem;
}
.pm-section-title:first-of-type { margin-top: 0; }
.pm-empty { color: var(--wa-color-text-quiet); }

.pm-request, .pm-peer {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-xs);
    padding: var(--wa-space-s) 0;
    border-bottom: 1px solid var(--wa-color-surface-border);
}
.pm-request:last-of-type, .pm-peer:last-of-type { border-bottom: none; }

.pm-request__claim {
    display: flex;
    align-items: baseline;
    gap: var(--wa-space-s);
    min-width: 0;
}
.pm-request__name { font-weight: 600; }
.pm-request__url, .pm-peer__url {
    color: var(--wa-color-text-quiet);
    font-size: 0.85rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.pm-request__code-block {
    display: flex;
    align-items: center;
    gap: var(--wa-space-m);
}
.pm-request__code {
    font-family: var(--wa-font-family-code, monospace);
    font-size: 1.6rem;
    font-weight: 700;
    letter-spacing: 0.35em;
    user-select: all;
}
.pm-hint {
    margin: 0;
    font-size: 0.8rem;
    color: var(--wa-color-text-quiet);
}
.pm-request__actions, .pm-peer__actions {
    display: flex;
    align-items: center;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
.pm-request__actions wa-input { flex: 1; min-width: 10rem; }
.pm-request__actions .pm-code-input {
    flex: 0 1 8rem;
    min-width: 0;
    max-width: 100%;
}

.pm-peer__main {
    display: flex;
    align-items: center;
    gap: var(--wa-space-s);
    min-width: 0;
}
.pm-peer__name { font-weight: 600; }
.pm-peer__contact {
    margin-left: auto;
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
}
.pm-peer__url-row { display: flex; gap: var(--wa-space-xs); min-width: 0; }
.pm-peer__reason {
    color: var(--wa-color-text-quiet);
    font-size: 0.8rem;
}

.pm-add-form {
    display: flex;
    gap: var(--wa-space-xs);
    flex-wrap: wrap;
}
.pm-add-form wa-input { flex: 1; min-width: 12rem; }

.pm-footer {
    display: flex;
    justify-content: flex-end;
    gap: var(--wa-space-s);
    width: 100%;
}

/* Confirmation callouts: breathing room between the text and the buttons. */
.pm-confirm-body {
    display: flex;
    flex-direction: column;
    gap: var(--wa-space-s);
}
.pm-confirm__actions {
    display: flex;
    gap: var(--wa-space-s);
}
</style>
